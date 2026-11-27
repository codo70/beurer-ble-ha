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

from .bpm_session import BPMSession
from .const import CONF_CONSENT, COOLDOWN, DOMAIN, LISTEN_TIME, SIGNAL_UPDATE
from .devices import FAMILY_SANITAS, FAMILY_STANDARD, DeviceModel
from .gatt import ScaleDisconnected
from .sanitas_protocol import start_byte_for_name
from .sanitas_session import SanitasSession
from .standard_session import StandardSession

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
EVENT_MEASUREMENT = f"{DOMAIN}_measurement"


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

    async def async_start(self) -> None:
        """Load stored state and start listening for advertisements."""
        if data := await self._store.async_load():
            self.persons = data.get("persons", {})
            self.latest = data.get("latest", {})
            self.battery = data.get("battery")
            self._start_byte = data.get("start_byte", self._start_byte)
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
            return SanitasSession(client, self._start_byte)
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
            await self._async_process(*self._from_sanitas(session))
        else:
            await self._async_process(
                session.persons, session.measurements, session.battery, session.connected_ok
            )

    def _from_sanitas(self, session: SanitasSession):
        persons = {str(uid): u.initials for uid, u in session.users.items()}
        measurements = {
            str(uid): [m.as_dict() for m in lst] for uid, lst in session.measurements.items()
        }
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
                    {"address": self.address, "person": pid, "person_name": self.persons[pid], **meas},
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
            await self._store.async_save(
                {
                    "persons": self.persons,
                    "latest": self.latest,
                    "battery": self.battery,
                    "start_byte": self._start_byte,
                }
            )
