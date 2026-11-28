# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 codo70. Scale protocols based on openScale (C) olie.xdev, GPL-3.0-or-later.
"""Connection handling and state for one Beurer device."""

from __future__ import annotations

import asyncio
import logging
import time

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .assign import pick_by_weight
from .bpm_session import BPMSession
from .const import (
    CONF_AUTO_ASSIGN,
    CONF_CONSENT,
    CONF_PERSONS,
    COOLDOWN,
    DOMAIN,
    LISTEN_TIME,
    SIGNAL_UPDATE,
)
from .devices import FAMILY_SANITAS, FAMILY_STANDARD, DeviceModel
from .gatt import ScaleDisconnected
from .sanitas_protocol import NewUser, start_byte_for_name
from .sanitas_session import SanitasSession
from .standard_session import StandardSession

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
EVENT_MEASUREMENT = f"{DOMAIN}_measurement"


GUEST_PIDS = ("guest",)


def parse_consents(text: str | None) -> dict[int, int]:
    """Parse "1:1234, 2:0815" into {slot: code}."""
    result: dict[int, int] = {}
    for part in (text or "").replace(";", ",").split(","):
        if ":" not in part:
            continue
        slot, code = part.split(":", 1)
        try:
            result[int(slot.strip())] = int(code.strip())
        except ValueError:
            continue
    return result


class BeurerCoordinator:
    """Connect when the device advertises and keep the latest values."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, address: str, name: str, model: DeviceModel
    ) -> None:
        """Initialise."""
        self.hass = hass
        self.entry = entry
        self.address = address
        self.name = name
        self.model = model
        self.persons: dict[str, str] = {}
        self.latest: dict[str, dict] = {}
        self.battery: int | None = None
        self.last_sync: float | None = None
        self._start_byte = start_byte_for_name(name)
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._task: asyncio.Task | None = None
        self._last_run = 0.0
        self._unsub: CALLBACK_TYPE | None = None
        self.person_map: dict[str, str] = dict(entry.options.get(CONF_PERSONS, {}))
        # User to create on the scale at the next connection, plus the HA person for it.
        self.pending_user: NewUser | None = None
        self.pending_person: str | None = None
        # Scale users to delete at the next connection (uid or initials).
        self.pending_delete: set[str] = set()
        # Scale users that still need their reference measurement.
        self.needs_reference: set[int] = set()
        # Blood pressure monitors resend all stored readings every time and their
        # clock may be wrong: remember readings already seen (by content).
        self._seen: dict[str, None] = {}
        # Scale users that already had a body analysis (no reference measurement needed).
        self._analysed: set[str] = set()
        self.auto_assign: bool = entry.options.get(CONF_AUTO_ASSIGN, True)

    def ha_person(self, pid: str) -> str | None:
        """HA person entity a device person is mapped to."""
        return self.person_map.get(pid)

    def ha_person_name(self, person: str) -> str:
        """Friendly name of an HA person entity."""
        if (state := self.hass.states.get(person)) is not None:
            return state.name
        return person.split(".", 1)[-1].replace("_", " ").title()

    def effective_pid(self, pid: str) -> str:
        """Device persons without an HA person are shown together as the guest."""
        return pid if pid == "guest" or self.ha_person(pid) else "guest"

    def _assign_guests(self, measurements: dict[str, list[dict]]) -> dict[str, list[dict]]:
        """Unmapped persons become the guest; guest weighings may go to a person by weight."""
        merged: dict[str, list[dict]] = {}
        for pid, lst in measurements.items():
            merged.setdefault(self.effective_pid(pid), []).extend(lst)
        measurements = merged
        if "guest" in measurements:
            self.persons.setdefault("guest", "Gast")
        if not self.auto_assign:
            return measurements
        candidates = {
            pid: m["weight_kg"]
            for pid, m in self.latest.items()
            if pid not in GUEST_PIDS and m.get("weight_kg")
        }
        result: dict[str, list[dict]] = {}
        for pid, lst in measurements.items():
            for meas in lst:
                target = pid
                if pid in GUEST_PIDS and meas.get("weight_kg"):
                    target = pick_by_weight(meas["weight_kg"], candidates) or pid
                    if target != pid:
                        _LOGGER.debug("Guest weighing %.1f kg assigned to %s", meas["weight_kg"], target)
                result.setdefault(target, []).append(meas)
        return result

    async def async_start(self) -> None:
        """Load stored state and start listening for advertisements."""
        if data := await self._store.async_load():
            self.persons = data.get("persons", {})
            self.latest = data.get("latest", {})
            self.battery = data.get("battery")
            self._start_byte = data.get("start_byte", self._start_byte)
            self.needs_reference = set(data.get("needs_reference", []))
            self._seen = dict.fromkeys(data.get("seen", []))
            self._analysed = set(data.get("analysed", [])) | {
                pid for pid, m in self.latest.items() if m.get("fat_pct")
            }
            # Values of persons that are not mapped (any more) belong to the guest.
            for pid in [p for p in self.latest if self.effective_pid(p) != p]:
                meas = self.latest.pop(pid)
                guest = self.latest.get("guest")
                if guest is None or meas.get("timestamp", 0) > guest.get("timestamp", 0):
                    self.latest["guest"] = meas
            if "guest" in self.latest:
                self.persons.setdefault("guest", "Gast")
        self._unsub = bluetooth.async_register_callback(
            self.hass,
            self._async_on_advertisement,
            bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )

    async def async_stop(self) -> None:
        """Stop listening and cancel a running sync."""
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._task and not self._task.done():
            self._task.cancel()

    @callback
    def _async_on_advertisement(
        self, service_info: bluetooth.BluetoothServiceInfoBleak, change: bluetooth.BluetoothChange
    ) -> None:
        if self._task and not self._task.done():
            return
        if time.monotonic() - self._last_run < COOLDOWN:
            return
        self._last_run = time.monotonic()
        self._task = self.entry.async_create_background_task(
            self.hass, self._async_sync(), f"{DOMAIN}_sync_{self.address}"
        )

    def _make_session(self, client):
        tz = dt_util.get_default_time_zone()
        if self.model.family == FAMILY_SANITAS:
            # Users the scale has never measured with body analysis need a reference
            # measurement first, otherwise the scale does not recognise them.
            # Only for users that belong to an HA person: a stray user on the scale
            # would otherwise be requested at every weighing and shown instead.
            refs = set(self.needs_reference) | {
                int(pid)
                for pid in self.persons
                if pid.isdigit()
                and int(pid) > 0
                and pid not in self._analysed
                and self.ha_person(pid)
            }
            return SanitasSession(
                client, self._start_byte, self.pending_user, refs, set(self.pending_delete)
            )
        if self.model.family == FAMILY_STANDARD:
            consents = parse_consents(self.entry.options.get(CONF_CONSENT))
            return StandardSession(client, tz, self.model, consents)
        return BPMSession(client, tz)

    async def _async_sync(self) -> None:
        ble_device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if ble_device is None:
            _LOGGER.debug("%s: no connectable path to the device", self.address)
            return
        session = None
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.name,
                disconnected_callback=lambda _c: session and session.on_disconnect(),
                max_attempts=3,
            )
        except (BleakError, TimeoutError) as err:
            _LOGGER.debug("%s: connect failed: %s", self.address, err)
            return
        session = self._make_session(client)
        # Show each measurement right away instead of after the whole sync.
        if isinstance(session, SanitasSession):
            session.on_data = lambda: self.hass.async_create_task(
                self._async_process(*self._from_sanitas(session))
            )
        # Blood pressure monitors resend every stored reading: process them once at the
        # end, so each sensor changes only to the newest reading (one point in history).
        try:
            await session.run(LISTEN_TIME)
        except (BleakError, TimeoutError, ScaleDisconnected) as err:
            _LOGGER.debug("%s: sync ended early: %r", self.address, err)
        finally:
            try:
                await client.disconnect()
            except BleakError:
                pass
            self._last_run = time.monotonic()
        if isinstance(session, SanitasSession):
            if session.created_uid is not None:
                self.needs_reference.add(session.created_uid)
            # A full body analysis for the user means the scale knows them now.
            done = {
                uid
                for uid, lst in session.measurements.items()
                if any(m.fat_pct for m in lst)
            }
            self.needs_reference -= done
            self._analysed |= {str(uid) for uid in done}
            self._finish_user_deletion(session)
            await self._async_process(*self._from_sanitas(session))
            await self._async_finish_user_creation(session)
            await self._async_save()
        elif isinstance(session, BPMSession):
            await self._async_process_bpm(session)
        else:
            await self._async_process(
                session.persons, session.measurements, session.battery, session.connected_ok
            )

    def _finish_user_deletion(self, session: SanitasSession) -> None:
        for key, err in session.delete_errors.items():
            _LOGGER.warning("Deleting user %s from the scale failed: %s", key, err)
            self.pending_delete.discard(key)
        for uid in session.deleted_uids:
            pid = str(uid)
            initials = self.persons.pop(pid, None)
            self.pending_delete -= {pid, (initials or "").upper()}
            self.latest.pop(pid, None)
            self.needs_reference.discard(uid)
            self._analysed.discard(pid)
        if session.deleted_uids and any(
            str(uid) in self.entry.options.get(CONF_PERSONS, {}) for uid in session.deleted_uids
        ):
            persons = {
                k: v
                for k, v in self.entry.options.get(CONF_PERSONS, {}).items()
                if k not in {str(uid) for uid in session.deleted_uids}
            }
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, CONF_PERSONS: persons}
            )

    async def _async_finish_user_creation(self, session: SanitasSession) -> None:
        if self.pending_user is None:
            return
        if session.created_uid is None:
            _LOGGER.warning(
                "Creating user %s on the scale did not work yet (%s); will retry at the next connection",
                self.pending_user.initials,
                session.create_error or "not connected long enough",
            )
            if session.create_error == "scale_full":
                self.pending_user = None
            return
        pid = str(session.created_uid)
        person = self.pending_person
        self.pending_user = None
        self.pending_person = None
        if person:
            persons = {**self.entry.options.get(CONF_PERSONS, {}), pid: person}
            # Reloads the entry, so the new person gets its own device.
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, CONF_PERSONS: persons}
            )

    def _from_sanitas(self, session: SanitasSession):
        persons = {str(uid): u.initials for uid, u in session.users.items()}
        measurements: dict[str, list[dict]] = {}
        for uid, lst in session.measurements.items():
            pid = str(uid) if uid in session.users or str(uid) in self.persons else "guest"
            measurements.setdefault(pid, []).extend(m.as_dict() for m in lst)
        if session.live_weight and not any(measurements.values()):
            # Only a weight frame (e.g. no user on the scale): keep it as a guest weighing.
            measurements["guest"] = [{"timestamp": int(time.time()), "weight_kg": session.live_weight}]
        if "guest" in measurements:
            persons["guest"] = "Gast"
        battery = session.status.battery if session.status else None
        if session.status is not None:
            self._start_byte = session.start_byte
        return persons, measurements, battery, session.status is not None

    async def _async_process(
        self,
        persons: dict[str, str],
        measurements: dict[str, list[dict]],
        battery: int | None,
        ok: bool,
    ) -> None:
        changed = False
        measurements = self._assign_guests(measurements)
        for pid, label in persons.items():
            generic = label in (f"P{pid}", f"Benutzer {pid}", "Gast")
            if self.persons.get(pid) != label and not (generic and pid in self.persons):
                self.persons[pid] = label
                changed = True
        for pid, lst in measurements.items():
            old_ts = self.latest.get(pid, {}).get("timestamp", 0)
            new = sorted((m for m in lst if m["timestamp"] > old_ts), key=lambda m: m["timestamp"])
            if not new:
                continue
            self.persons.setdefault(pid, pid)
            for meas in new:
                self.hass.bus.async_fire(
                    EVENT_MEASUREMENT,
                    {
                        "address": self.address,
                        "person": pid,
                        "person_name": self.persons[pid],
                        "ha_person": self.ha_person(pid),
                        **meas,
                    },
                )
            self.latest[pid] = new[-1]
            changed = True
        if battery is not None and battery != self.battery:
            self.battery = battery
            changed = True
        if ok:
            self.last_sync = time.time()
        if changed or ok:
            async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))
        if changed:
            await self._async_save()

    async def _async_process_bpm(self, session: BPMSession) -> None:
        """New = never seen before; latest = last one the monitor sent (its memory order)."""
        now = time.time()
        changed = False
        for pid, label in session.persons.items():
            if pid not in self.persons:
                self.persons[pid] = label
                changed = True
        for dev_pid, lst in session.measurements.items():
            pid = self.effective_pid(dev_pid)
            if pid == "guest":
                self.persons.setdefault("guest", "Gast")
            newest = None
            for raw in lst:
                key = "|".join(
                    str(raw.get(k)) for k in ("device_ts", "systolic", "diastolic", "pulse")
                )
                key = f"{dev_pid}|{key}"
                if key in self._seen:
                    continue
                self._seen[key] = None
                ts = raw.get("device_ts")
                plausible = ts is not None and 1262304000 <= ts <= now + 86400
                meas = {k: v for k, v in raw.items() if k not in ("device_ts", "seq")}
                meas["timestamp"] = ts if plausible else int(now)
                meas["device_time_ok"] = plausible
                self.hass.bus.async_fire(
                    EVENT_MEASUREMENT,
                    {
                        "address": self.address,
                        "person": dev_pid,
                        "person_name": self.persons.get(dev_pid, dev_pid),
                        "ha_person": self.ha_person(dev_pid),
                        **meas,
                    },
                )
                newest = meas
            if newest is not None:
                self.latest[pid] = newest
                changed = True
        if session.battery is not None and session.battery != self.battery:
            self.battery = session.battery
            changed = True
        if len(self._seen) > 5000:
            self._seen = dict.fromkeys(list(self._seen)[-5000:])
        if changed or session.connected_ok:
            self.last_sync = now
            async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))
        if changed:
            await self._async_save()

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "persons": self.persons,
                "latest": self.latest,
                "battery": self.battery,
                "start_byte": self._start_byte,
                "needs_reference": sorted(self.needs_reference),
                "seen": list(self._seen),
                "analysed": sorted(self._analysed),
            }
        )
