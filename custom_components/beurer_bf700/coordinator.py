"""Connection handling and state for one Beurer BF700 scale."""

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

from .const import COOLDOWN, DOMAIN, LISTEN_TIME, SIGNAL_NEW_USER, SIGNAL_UPDATE
from .protocol import Measurement, ScaleUser, start_byte_for_name
from .session import BF700Session, ScaleDisconnected

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1


class BF700Coordinator:
    """Connect when the scale advertises and keep the latest values."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str, name: str) -> None:
        """Initialise."""
        self.hass = hass
        self.entry = entry
        self.address = address
        self.name = name
        self.users: dict[int, ScaleUser] = {}
        self.latest: dict[int, Measurement] = {}
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
            for uid, u in data.get("users", {}).items():
                self.users[int(uid)] = ScaleUser(int(uid), u["initials"], u["birth_year"])
            for uid, m in data.get("latest", {}).items():
                self.latest[int(uid)] = Measurement.from_dict(m)
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

    async def _async_sync(self) -> None:
        ble_device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if ble_device is None:
            _LOGGER.debug("%s: no connectable path to the scale", self.address)
            return
        session: BF700Session | None = None
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
        session = BF700Session(client, self._start_byte)
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
        await self._async_process(session)

    async def _async_process(self, session: BF700Session) -> None:
        new_users = [uid for uid in session.users if uid not in self.users]
        self.users.update(session.users)
        changed = bool(new_users)
        for uid, measurements in session.measurements.items():
            if not measurements:
                continue
            newest = max(measurements, key=lambda m: m.timestamp)
            old = self.latest.get(uid)
            if old is None or newest.timestamp > old.timestamp:
                self.latest[uid] = newest
                changed = True
                if uid not in self.users:
                    self.users[uid] = ScaleUser(uid, str(uid), 0)
                    new_users.append(uid)
        if session.status and session.status.battery is not None:
            if session.status.battery != self.battery:
                self.battery = session.status.battery
                changed = True
        if session.status is not None:
            self.last_sync = time.time()
            self._start_byte = session.start_byte
        for uid in new_users:
            async_dispatcher_send(self.hass, SIGNAL_NEW_USER.format(self.entry.entry_id), uid)
        if changed or session.status is not None:
            async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))
        if changed:
            await self._store.async_save(
                {
                    "users": {
                        str(u.uid): {"initials": u.initials, "birth_year": u.birth_year}
                        for u in self.users.values()
                    },
                    "latest": {str(uid): m.as_dict() for uid, m in self.latest.items()},
                    "battery": self.battery,
                    "start_byte": self._start_byte,
                }
            )
