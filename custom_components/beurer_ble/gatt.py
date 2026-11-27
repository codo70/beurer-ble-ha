# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 codo70. Scale protocols based on openScale (C) olie.xdev, GPL-3.0-or-later.
"""Shared helpers for the standard Bluetooth SIG profiles (no HA imports)."""

from __future__ import annotations

import asyncio
from datetime import datetime, tzinfo
import logging
import struct
import time

from bleak import BleakClient
from bleak.exc import BleakError

_LOGGER = logging.getLogger(__name__)

LB_TO_KG = 0.45359237
KPA_TO_MMHG = 7.50062


def uuid16(short: int) -> str:
    """Expand a 16 bit UUID to the full Bluetooth base UUID."""
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


SVC_BATTERY = uuid16(0x180F)
CHR_BATTERY_LEVEL = uuid16(0x2A19)
SVC_CURRENT_TIME = uuid16(0x1805)
CHR_CURRENT_TIME = uuid16(0x2A2B)
CHR_DATE_TIME = uuid16(0x2A08)


class ScaleDisconnected(Exception):
    """The device dropped the connection."""


def u16le(data: bytes, off: int) -> int:
    """Read an unsigned 16 bit little endian value."""
    return struct.unpack_from("<H", data, off)[0]


def sfloat(raw: int) -> float | None:
    """Decode an IEEE-11073 16 bit SFLOAT. Returns None for special values."""
    if raw in (0x07FF, 0x0800, 0x07FE, 0x0802, 0x0801):
        return None
    mantissa = raw & 0x0FFF
    if mantissa >= 0x0800:
        mantissa -= 0x1000
    exponent = raw >> 12
    if exponent >= 0x8:
        exponent -= 0x10
    return mantissa * (10**exponent)


def parse_date_time(data: bytes, off: int) -> tuple[int, int, int, int, int, int] | None:
    """Parse a 7 byte Date Time field. Returns None if the date is unset."""
    if len(data) < off + 7:
        return None
    year = u16le(data, off)
    month, day, hour, minute, second = data[off + 2 : off + 7]
    if year == 0 or month == 0 or day == 0:
        return None
    return year, month, day, hour, minute, second


def to_epoch(dt: tuple[int, int, int, int, int, int] | None, tz: tzinfo | None) -> int | None:
    """Convert a device local Date Time to unix seconds."""
    if dt is None:
        return None
    try:
        return int(datetime(*dt, tzinfo=tz).timestamp())
    except ValueError:
        return None


def current_time_payload(now: datetime) -> bytes:
    """Build a Current Time (0x2A2B) payload from a local datetime."""
    return struct.pack(
        "<HBBBBBBBB",
        now.year,
        now.month,
        now.day,
        now.hour,
        now.minute,
        now.second,
        now.isoweekday(),
        0,
        0,
    )


class BaseSession:
    """Collects persons, measurements and battery during one connection.

    persons:      person id -> display label (e.g. scale initials)
    measurements: person id -> list of dicts with "timestamp" and value keys
    """

    def __init__(self, client: BleakClient, tz: tzinfo | None) -> None:
        """Initialise."""
        self._client = client
        self._tz = tz
        self._queue: asyncio.Queue[tuple[str, bytes] | None] = asyncio.Queue()
        self.persons: dict[str, str] = {}
        self.measurements: dict[str, list[dict]] = {}
        self.battery: int | None = None
        self.connected_ok = False
        # Called (without arguments) whenever a finished measurement was received.
        self.on_data = None

    def on_disconnect(self) -> None:
        """Wake up a waiting reader when the device disconnects."""
        self._queue.put_nowait(None)

    def _notify_cb(self, tag: str):
        def _cb(_char: object, data: bytearray) -> None:
            _LOGGER.debug("<< %s %s", tag, bytes(data).hex(" "))
            self._queue.put_nowait((tag, bytes(data)))

        return _cb

    async def _next(self, timeout: float) -> tuple[str, bytes]:
        item = await asyncio.wait_for(self._queue.get(), timeout)
        if item is None:
            raise ScaleDisconnected
        return item

    def add_measurement(self, pid: str, values: dict) -> None:
        """Store a measurement, merging values with the same timestamp."""
        lst = self.measurements.setdefault(pid, [])
        for existing in lst:
            if existing["timestamp"] == values["timestamp"]:
                existing.update({k: v for k, v in values.items() if v is not None})
                return
        lst.append(values)

    def _find_char(self, service: str, char: str):
        svc = self._client.services.get_service(service)
        if svc is None:
            return None
        return svc.get_characteristic(char)

    async def _read_battery(self) -> None:
        if (char := self._find_char(SVC_BATTERY, CHR_BATTERY_LEVEL)) is None:
            return
        try:
            data = await self._client.read_gatt_char(char)
        except BleakError as err:
            _LOGGER.debug("Battery read failed: %s", err)
            return
        if data:
            self.battery = data[0]

    def _log_services(self) -> None:
        """Debug: list every service and characteristic of the device."""
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        for svc in self._client.services:
            for char in svc.characteristics:
                _LOGGER.debug("GATT %s / %s %s", svc.uuid, char.uuid, ",".join(char.properties))

    async def _write_time(self) -> None:
        """Set the device clock: Current Time (0x2A2B) or a writable Date Time (0x2A08)."""
        now = datetime.now(self._tz)
        targets = []
        if (char := self._find_char(SVC_CURRENT_TIME, CHR_CURRENT_TIME)) is not None:
            targets.append((char, current_time_payload(now)))
        else:
            for svc in self._client.services:
                for char in svc.characteristics:
                    if char.uuid == CHR_DATE_TIME and (
                        "write" in char.properties or "write-without-response" in char.properties
                    ):
                        targets.append((char, current_time_payload(now)[:7]))
        for char, payload in targets:
            try:
                await self._client.write_gatt_char(
                    char, payload, response="write" in char.properties
                )
                _LOGGER.debug("Device clock set via %s", char.uuid)
                return
            except BleakError as err:
                _LOGGER.debug("Setting the device clock via %s failed: %s", char.uuid, err)
        if not targets:
            _LOGGER.debug("Device offers no writable clock characteristic")

    async def _notify_with_pairing(self, char, tag: str) -> None:
        """Subscribe; if the device demands encryption, pair once and retry."""
        try:
            await self._client.start_notify(char, self._notify_cb(tag))
        except BleakError as err:
            _LOGGER.debug("Subscribe to %s failed (%s), trying to pair", tag, err)
            await self._client.pair()
            await self._client.start_notify(char, self._notify_cb(tag))

    async def _drain(self, handler, idle: float, total: float) -> None:
        """Feed incoming notifications to handler until idle or total time passes."""
        deadline = time.monotonic() + total
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                tag, data = await self._next(min(idle, remaining))
            except TimeoutError:
                return
            await handler(tag, data)
