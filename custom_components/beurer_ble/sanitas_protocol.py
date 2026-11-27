"""Beurer BF700/BF710/BF800 / Sanitas SBF70 BLE protocol.

Pure functions only (no Home Assistant imports) so they can be tested alone.
Protocol as implemented by openScale (BeurerSanitasHandler.kt, GPL-3.0-or-later,
https://github.com/oliexdev/openScale).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import struct

START_BF700 = 0xF7  # BF700 / BF800 / Runtastic Libra
START_BF710 = 0xE7  # BF710 / Sanitas SBF70 / SBF75

NIBBLE_INIT = 0x6
NIBBLE_CMD = 0x7
NIBBLE_SET_TIME = 0x9
NIBBLE_DISCONNECT = 0xA

CMD_USER_ADD = 0x31
CMD_USER_LIST = 0x33
CMD_USER_INFO = 0x34
CMD_USER_DETAILS = 0x36
CMD_DO_MEASUREMENT = 0x40
CMD_GET_SAVED_MEASUREMENTS = 0x41
CMD_SAVED_MEASUREMENT = 0x42
CMD_DELETE_SAVED_MEASUREMENTS = 0x43
CMD_SET_UNIT = 0x4D
CMD_SCALE_STATUS = 0x4F
CMD_WEIGHT_MEASUREMENT = 0x58
CMD_MEASUREMENT = 0x59
CMD_SCALE_ACK = 0xF0
CMD_APP_ACK = 0xF1

_BF700_NAMES = (
    "bf-700",
    "beurer bf700",
    "bf-800",
    "beurer bf800",
    "bf800",
    "rt-libra-b",
    "rt-libra-w",
    "libra-b",
    "libra-w",
)
_BF710_NAMES = ("bf700", "beurer bf710", "bf710", "sanitas sbf70", "sbf75", "aicdscale1")


def start_byte_for_name(name: str | None) -> int:
    """Return the protocol start byte for an advertised device name."""
    lname = (name or "").lower()
    if any(n in lname for n in _BF700_NAMES):
        return START_BF700
    if any(n in lname for n in _BF710_NAMES):
        return START_BF710
    return START_BF700


def other_start_byte(start: int) -> int:
    """Return the start byte of the other device family (fallback)."""
    return START_BF710 if start == START_BF700 else START_BF700


def is_supported_name(name: str | None) -> bool:
    """Return True if the name looks like a supported scale."""
    lname = (name or "").lower()
    return any(n in lname for n in _BF700_NAMES + _BF710_NAMES)


def alt_byte(start: int, nibble: int) -> int:
    """High nibble of the start byte combined with a fixed low nibble."""
    return (start & 0xF0) | nibble


def cmd_frame(start: int, cmd: int, payload: bytes = b"") -> bytes:
    """Build a command frame."""
    return bytes([start, cmd]) + payload


def init_frame(start: int) -> bytes:
    """Build the init frame."""
    return bytes([alt_byte(start, NIBBLE_INIT), 0x01])


def set_time_frame(start: int, unix_ts: int) -> bytes:
    """Build the set-time frame (uint32 big endian unix seconds)."""
    return bytes([alt_byte(start, NIBBLE_SET_TIME)]) + struct.pack(">I", unix_ts & 0xFFFFFFFF)


def ack_frame(start: int, data: bytes) -> bytes:
    """Build the app ACK for an incoming multi-part frame."""
    return bytes([start, CMD_APP_ACK, data[1], data[2], data[3]])


def uid_bytes(uid: int) -> bytes:
    """Encode a scale user id (uint64 big endian)."""
    return struct.pack(">Q", uid)


def is_scale_ack(start: int, data: bytes, cmd: int) -> bool:
    """Return True if data is the scale ACK for cmd."""
    return len(data) >= 3 and data[0] == start and data[1] == CMD_SCALE_ACK and data[2] == cmd


@dataclass
class ScaleUser:
    """A user profile stored on the scale."""

    uid: int
    initials: str
    birth_year: int


@dataclass
class Measurement:
    """One body composition measurement."""

    timestamp: int
    weight_kg: float
    impedance: int | None
    fat_pct: float | None
    water_pct: float | None
    muscle_pct: float | None
    bone_kg: float | None

    def as_dict(self) -> dict:
        """Serialise for storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Measurement:
        """Deserialise from storage."""
        return cls(**data)


@dataclass
class ScaleStatus:
    """Scale status from the CMD_SCALE_STATUS ACK."""

    battery: int | None
    unit: int | None
    version: int | None


def parse_user_info(data: bytes) -> ScaleUser | None:
    """Parse a CMD_USER_INFO (0x34) frame."""
    if len(data) < 16:
        return None
    uid = struct.unpack_from(">Q", data, 4)[0]
    raw_name = data[12:15].split(b"\x00", 1)[0]
    initials = raw_name.decode("ascii", errors="replace").strip()
    return ScaleUser(uid=uid, initials=initials or str(uid), birth_year=1900 + data[15])


def parse_status(data: bytes) -> ScaleStatus:
    """Parse the ACK of CMD_SCALE_STATUS."""
    return ScaleStatus(
        battery=data[4] if len(data) > 4 else None,
        unit=data[7] if len(data) > 7 else None,
        version=data[11] if len(data) > 11 else None,
    )


def _u16(buf: bytes, off: int) -> int:
    return struct.unpack_from(">H", buf, off)[0]


def parse_measurement(buf: bytes) -> Measurement | None:
    """Parse a reassembled measurement payload (16 bytes or more)."""
    if len(buf) < 16:
        return None
    timestamp = struct.unpack_from(">I", buf, 0)[0] & 0x7FFFFFFF
    weight = _u16(buf, 4) * 50 / 1000
    if weight <= 0:
        return None
    impedance = _u16(buf, 6)
    fat = _u16(buf, 8) / 10
    water = _u16(buf, 10) / 10
    muscle = _u16(buf, 12) / 10
    bone = _u16(buf, 14) * 50 / 1000
    return Measurement(
        timestamp=timestamp,
        weight_kg=round(weight, 2),
        impedance=impedance or None,
        fat_pct=fat or None,
        water_pct=water or None,
        muscle_pct=muscle or None,
        bone_kg=round(bone, 2) or None,
    )
