"""Sync session for Beurer blood pressure monitors (BM 85 and others).

They use the standard Blood Pressure profile (service 0x1810): after connecting
and enabling indications on Blood Pressure Measurement (0x2A35) the monitor sends
its stored readings and usually disconnects by itself.
"""

from __future__ import annotations

from datetime import tzinfo
import logging

from bleak import BleakClient

from .gatt import (
    KPA_TO_MMHG,
    BaseSession,
    ScaleDisconnected,
    parse_date_time,
    sfloat,
    to_epoch,
    u16le,
    uuid16,
)

_LOGGER = logging.getLogger(__name__)

SVC_BLOOD_PRESSURE = uuid16(0x1810)
CHR_BP_MEASUREMENT = uuid16(0x2A35)

PULSE_RANGE = (25, 250)


def _pulse(data: bytes, off: int) -> float | None:
    """Pulse rate. The BM 85 sends it with swapped bytes, so accept either order."""
    little = sfloat(u16le(data, off))
    if little is not None and PULSE_RANGE[0] <= little <= PULSE_RANGE[1]:
        return little
    big = sfloat(int.from_bytes(data[off : off + 2], "big"))
    if big is not None and PULSE_RANGE[0] <= big <= PULSE_RANGE[1]:
        return big
    return little


def parse_bp_measurement(data: bytes, tz: tzinfo | None) -> tuple[int | None, int | None, dict] | None:
    """Parse Blood Pressure Measurement (0x2A35). Returns (user, timestamp, values)."""
    if len(data) < 7:
        return None
    flags = data[0]
    factor = KPA_TO_MMHG if flags & 0x01 else 1.0
    systolic, diastolic, mean = (sfloat(u16le(data, o)) for o in (1, 3, 5))
    off = 7
    ts = None
    if flags & 0x02:
        ts = to_epoch(parse_date_time(data, off), tz)
        off += 7
    pulse = None
    if flags & 0x04 and len(data) >= off + 2:
        pulse = _pulse(data, off)
        off += 2
    user = None
    if flags & 0x08 and len(data) > off:
        user = data[off]
        off += 1
    elif flags & 0x40 and len(data) > off:
        # Beurer extension (seen on the BM 85): flag bit 6 = one byte user index,
        # 0-based (0 = user 1, 1 = user 2), where the standard user id would be.
        user = data[off] + 1
        off += 1
    status = None
    if flags & 0x10 and len(data) >= off + 2:
        # The BM 85 sends 16-bit values after the timestamp big-endian (pulse too).
        status = int.from_bytes(data[off : off + 2], "big" if flags & 0x40 else "little")

    def mmhg(v: float | None) -> int | None:
        return round(v * factor) if v else None

    values = {
        "systolic": mmhg(systolic),
        "diastolic": mmhg(diastolic),
        "mean_arterial": mmhg(mean),
        "pulse": round(pulse) if pulse else None,
    }
    if status is not None:
        values["irregular_pulse"] = bool(status & 0x0004)
        values["body_movement"] = bool(status & 0x0001)
        values["cuff_fit_ok"] = not status & 0x0002
        values["status_raw"] = status
    if not values["systolic"]:
        return None
    return user, ts, values


class BPMSession(BaseSession):
    """Download stored readings from a blood pressure monitor."""

    def __init__(self, client: BleakClient, tz: tzinfo | None) -> None:
        """Initialise."""
        super().__init__(client, tz)
        self._seq = 0

    async def _handle(self, tag: str, data: bytes) -> None:
        if tag != "bp" or (parsed := parse_bp_measurement(data, self._tz)) is None:
            return
        user, ts, values = parsed
        pid = str(user) if user not in (None, 0, 0xFF) else "1"
        self.persons.setdefault(pid, f"Benutzer {pid}")
        # Keep the device timestamp as given (it may be wrong); the coordinator
        # decides what is new by content and order, not by this clock.
        self._seq += 1
        self.measurements.setdefault(pid, []).append(
            {"device_ts": ts, "seq": self._seq, **values}
        )
        if self.on_data is not None:
            self.on_data()

    async def run(self, listen_time: float) -> None:
        """Run the sync."""
        char = self._find_char(SVC_BLOOD_PRESSURE, CHR_BP_MEASUREMENT)
        if char is None:
            raise ScaleDisconnected("Blood Pressure service not found")
        self._log_services()
        # Set the clock first: some monitors only accept it before sending readings.
        await self._write_time()
        await self._notify_with_pairing(char, "bp")
        self.connected_ok = True
        await self._read_battery()
        try:
            # First reading can take a moment; then stop after a short pause.
            await self._drain(self._handle, idle=8, total=60)
        except ScaleDisconnected:
            _LOGGER.debug("Monitor disconnected after sending its readings")
