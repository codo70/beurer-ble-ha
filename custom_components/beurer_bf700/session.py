"""One BLE sync session with the scale (bleak only, no HA imports)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import time

from bleak import BleakClient

from .const import CHAR_UUID
from .protocol import (
    CMD_GET_SAVED_MEASUREMENTS,
    CMD_MEASUREMENT,
    CMD_SAVED_MEASUREMENT,
    CMD_SCALE_STATUS,
    CMD_USER_INFO,
    CMD_USER_LIST,
    CMD_WEIGHT_MEASUREMENT,
    NIBBLE_INIT,
    Measurement,
    ScaleStatus,
    ScaleUser,
    ack_frame,
    alt_byte,
    cmd_frame,
    init_frame,
    is_scale_ack,
    other_start_byte,
    parse_measurement,
    parse_status,
    parse_user_info,
    set_time_frame,
    uid_bytes,
)

_LOGGER = logging.getLogger(__name__)

ACK_TIMEOUT = 8.0
DATA_TIMEOUT = 15.0


class ScaleDisconnected(Exception):
    """The scale dropped the connection."""


class BF700Session:
    """Download users and measurements from the scale over one connection."""

    def __init__(self, client: BleakClient, start_byte: int) -> None:
        """Initialise."""
        self._client = client
        self._start = start_byte
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.users: dict[int, ScaleUser] = {}
        self.measurements: dict[int, list[Measurement]] = {}
        self.status: ScaleStatus | None = None
        self._saved_uid: int | None = None
        self._saved_buf = bytearray()
        self._saved_last_index = 0
        self._meas_uid: int | None = None
        self._meas_buf = bytearray()

    @property
    def start_byte(self) -> int:
        """Start byte that worked."""
        return self._start

    # --- low level -------------------------------------------------------

    def _on_notify(self, _char: object, data: bytearray) -> None:
        _LOGGER.debug("<< %s", bytes(data).hex(" "))
        self._queue.put_nowait(bytes(data))

    def on_disconnect(self) -> None:
        """Wake up a waiting reader when the scale disconnects."""
        self._queue.put_nowait(None)

    async def _write(self, data: bytes) -> None:
        _LOGGER.debug(">> %s", data.hex(" "))
        await self._client.write_gatt_char(CHAR_UUID, data, response=True)

    async def _next(self, timeout: float) -> bytes:
        data = await asyncio.wait_for(self._queue.get(), timeout)
        if data is None:
            raise ScaleDisconnected
        return data

    async def _wait_for(self, pred: Callable[[bytes], bool], timeout: float) -> bytes:
        """Return the first frame matching pred, handling data frames meanwhile."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            data = await self._next(remaining)
            if pred(data):
                return data
            await self._dispatch(data)

    async def _wait_until(self, cond: Callable[[], bool], timeout: float) -> None:
        """Handle incoming frames until cond() is true."""
        deadline = time.monotonic() + timeout
        while not cond():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            await self._dispatch(await self._next(remaining))

    async def _wait_ack(self, cmd: int, timeout: float = ACK_TIMEOUT) -> bytes:
        return await self._wait_for(lambda d: is_scale_ack(self._start, d, cmd), timeout)

    def _add_measurement(self, uid: int | None, meas: Measurement | None) -> None:
        if uid is None or meas is None:
            return
        lst = self.measurements.setdefault(uid, [])
        if all(m.timestamp != meas.timestamp for m in lst):
            lst.append(meas)

    async def _dispatch(self, data: bytes) -> None:
        """Handle a frame that is not the one currently awaited."""
        if len(data) < 4 or data[0] != self._start:
            return
        cmd = data[1]
        if cmd == CMD_USER_INFO:
            if user := parse_user_info(data):
                self.users[user.uid] = user
            await self._write(ack_frame(self._start, data))
        elif cmd == CMD_SAVED_MEASUREMENT:
            index = data[3]
            if index % 2 == 1:
                self._saved_buf = bytearray(data[4:])
            else:
                self._saved_buf += data[4:]
                self._add_measurement(self._saved_uid, parse_measurement(bytes(self._saved_buf)))
            self._saved_last_index = index
            await self._write(ack_frame(self._start, data))
        elif cmd == CMD_MEASUREMENT:
            count, index = data[2], data[3]
            if index == 1:
                if len(data) >= 13:
                    self._meas_uid = int.from_bytes(data[5:13], "big")
                self._meas_buf = bytearray()
            else:
                self._meas_buf += data[4:]
                if index == count:
                    self._add_measurement(self._meas_uid, parse_measurement(bytes(self._meas_buf)))
            await self._write(ack_frame(self._start, data))
        elif cmd == CMD_WEIGHT_MEASUREMENT:
            _LOGGER.debug("Live weight frame: %s", data.hex(" "))

    # --- sequence --------------------------------------------------------

    async def _init(self) -> None:
        """Send INIT, falling back to the other device family's start byte."""
        for start in (self._start, other_start_byte(self._start)):
            self._start = start
            init_byte = alt_byte(start, NIBBLE_INIT)
            await self._write(init_frame(start))
            try:
                await self._wait_for(lambda d, b=init_byte: len(d) > 0 and d[0] == b, ACK_TIMEOUT)
            except TimeoutError:
                _LOGGER.debug("No INIT response with start byte %#04x", start)
                continue
            return
        raise TimeoutError("Scale did not answer INIT")

    async def _fetch_saved(self) -> None:
        for uid in list(self.users):
            self._saved_uid = uid
            self._saved_last_index = 0
            await self._write(cmd_frame(self._start, CMD_GET_SAVED_MEASUREMENTS, uid_bytes(uid)))
            ack = await self._wait_ack(CMD_GET_SAVED_MEASUREMENTS)
            parts = ack[3] if len(ack) > 3 else 0
            if parts:
                await self._wait_until(lambda p=parts: self._saved_last_index >= p, DATA_TIMEOUT)
        self._saved_uid = None

    async def run(self, listen_time: float) -> None:
        """Run the full sync. Measurements are NOT deleted from the scale."""
        await self._client.start_notify(CHAR_UUID, self._on_notify)
        await self._init()
        await self._write(set_time_frame(self._start, int(time.time())))
        await self._write(cmd_frame(self._start, CMD_SCALE_STATUS, bytes(8)))
        self.status = parse_status(await self._wait_ack(CMD_SCALE_STATUS))

        await self._write(cmd_frame(self._start, CMD_USER_LIST))
        ack = await self._wait_ack(CMD_USER_LIST)
        count = ack[4] if len(ack) > 4 else 0
        if count:
            await self._wait_until(lambda: len(self.users) >= count, DATA_TIMEOUT)
        _LOGGER.debug("Scale users: %s", list(self.users.values()))

        await self._fetch_saved()

        # A weighing may still be running: listen, then download once more.
        if listen_time > 0:
            try:
                await self._wait_until(lambda: False, listen_time)
            except TimeoutError:
                pass
            await self._fetch_saved()
