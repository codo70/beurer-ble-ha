"""Identify supported Beurer / Sanitas devices from their advertisement."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .gatt import uuid16

FAMILY_SANITAS = "sanitas"  # BF700/BF710/BF800 proprietary protocol
FAMILY_STANDARD = "standard"  # Weight Scale + Body Composition + User Data profiles
FAMILY_BPM = "bpm"  # Blood Pressure profile

SVC_BLOOD_PRESSURE = uuid16(0x1810)


@dataclass(frozen=True)
class DeviceModel:
    """What we know about a device type."""

    family: str
    model: str
    # Standard scales: Beurer user list (service, characteristic) or None.
    user_list: tuple[str, str] | None = None


_BF105_LIST = (uuid16(0xFFFF), uuid16(0x0001))
_BF500_LIST = (uuid16(0xFFFF), uuid16(0xFFF1))
_BF600_LIST = (uuid16(0xFFF0), uuid16(0xFFF2))

# Order matters: more specific names first. Based on openScale's handlers.
_STANDARD = (
    ("bf1000", DeviceModel(FAMILY_STANDARD, "BF1000", _BF105_LIST)),
    ("bf105", DeviceModel(FAMILY_STANDARD, "BF105", _BF105_LIST)),
    ("bf720", DeviceModel(FAMILY_STANDARD, "BF720", _BF105_LIST)),
    ("bf915", DeviceModel(FAMILY_STANDARD, "BF915", _BF105_LIST)),
    ("sbf72", DeviceModel(FAMILY_STANDARD, "Sanitas SBF72", _BF105_LIST)),
    ("sbf73", DeviceModel(FAMILY_STANDARD, "Sanitas SBF73", _BF105_LIST)),
    ("bf950", DeviceModel(FAMILY_STANDARD, "BF950", _BF105_LIST)),
    ("sbf76", DeviceModel(FAMILY_STANDARD, "Sanitas SBF76", _BF105_LIST)),
    ("sbf77", DeviceModel(FAMILY_STANDARD, "Sanitas SBF77", _BF105_LIST)),
    ("bf500", DeviceModel(FAMILY_STANDARD, "BF500", _BF500_LIST)),
    ("bf600", DeviceModel(FAMILY_STANDARD, "BF600", _BF600_LIST)),
    ("bf850", DeviceModel(FAMILY_STANDARD, "BF850", _BF600_LIST)),
)

_SANITAS = (
    ("bf-700", "BF700"),
    ("beurer bf700", "BF700"),
    ("bf-800", "BF800"),
    ("bf800", "BF800"),
    ("libra-b", "Runtastic Libra"),
    ("libra-w", "Runtastic Libra"),
    ("beurer bf710", "BF710"),
    ("bf710", "BF710"),
    ("bf700", "BF700"),
    ("sbf70", "Sanitas SBF70"),
    ("sbf75", "SilverCrest SBF75"),
    ("aicdscale1", "Crane / AICDSCALE1"),
)

_BPM_NAME = re.compile(r"^(beurer\s*)?(bm|bc)\s?-?\d{2}", re.IGNORECASE)


def identify(name: str | None, service_uuids: list[str] | None = None) -> DeviceModel | None:
    """Return the device model for an advertisement, or None if unsupported."""
    lname = (name or "").lower()
    for key, model in _STANDARD:
        if key in lname:
            return model
    for key, label in _SANITAS:
        if key in lname:
            return DeviceModel(FAMILY_SANITAS, label)
    uuids = [u.lower() for u in service_uuids or []]
    if _BPM_NAME.match(lname.strip()):
        return DeviceModel(FAMILY_BPM, (name or "").strip().upper())
    if SVC_BLOOD_PRESSURE in uuids:
        return DeviceModel(FAMILY_BPM, (name or "Blood pressure monitor").strip())
    return None
