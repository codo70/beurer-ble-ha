"""Sync session for Beurer / Sanitas scales that use the standard BLE profiles.

Weight Scale (0x181D), Body Composition (0x181B) and User Data (0x181C) services,
plus Beurer's vendor user list. Follows openScale's StandardWeightProfileHandler
and StandardBeurerSanitasHandler (GPL-3.0-or-later).

Stored measurements are only released per scale user after a UDS consent with
that user's slot index and 4 digit consent code.
"""

from __future__ import annotations

from datetime import tzinfo
import logging
import time

from bleak import BleakClient
from bleak.exc import BleakError

from .devices import DeviceModel
from .gatt import (
    LB_TO_KG,
    BaseSession,
    parse_date_time,
    to_epoch,
    u16le,
    uuid16,
)

_LOGGER = logging.getLogger(__name__)

SVC_WEIGHT_SCALE = uuid16(0x181D)
CHR_WEIGHT_MEASUREMENT = uuid16(0x2A9D)
SVC_BODY_COMPOSITION = uuid16(0x181B)
CHR_BODY_COMPOSITION = uuid16(0x2A9C)
SVC_USER_DATA = uuid16(0x181C)
CHR_USER_CONTROL_POINT = uuid16(0x2A9F)

UCP_CONSENT = 0x02
UCP_RESPONSE = 0x20
UCP_SUCCESS = 0x01
UCP_NOT_AUTHORIZED = 0x05

GUEST_INDEX = 0xFF
SAME_PACKET_WINDOW = 5


def _mass(raw: int, is_kg: bool) -> float:
    return raw * 0.005 if is_kg else raw * 0.01 * LB_TO_KG


def parse_weight(data: bytes, tz: tzinfo | None) -> tuple[int | None, int | None, dict] | None:
    """Parse Weight Measurement (0x2A9D). Returns (user index, timestamp, values)."""
    if len(data) < 3:
        return None
    flags = data[0]
    is_kg = not flags & 0x01
    off = 1
    weight = _mass(u16le(data, off), is_kg)
    off += 2
    ts = None
    if flags & 0x02:
        ts = to_epoch(parse_date_time(data, off), tz)
        off += 7
    user = None
    if flags & 0x04 and len(data) > off:
        user = data[off]
        off += 1
    values: dict = {"weight_kg": round(weight, 2)}
    if flags & 0x08 and len(data) >= off + 2:
        values["bmi"] = round(u16le(data, off) * 0.1, 1)
    return user, ts, values


def parse_body_composition(
    data: bytes, tz: tzinfo | None
) -> tuple[int | None, int | None, dict] | None:
    """Parse Body Composition Measurement (0x2A9C)."""
    if len(data) < 4:
        return None
    flags = u16le(data, 0)
    is_kg = not flags & 0x0001
    off = 2
    values: dict = {}
    fat = u16le(data, off) * 0.1
    off += 2
    if fat:
        values["fat_pct"] = round(fat, 1)
    ts = None
    user = None

    def take() -> int | None:
        nonlocal off
        if len(data) < off + 2:
            return None
        val = u16le(data, off)
        off += 2
        return val

    if flags & 0x0002:
        ts = to_epoch(parse_date_time(data, off), tz)
        off += 7
    if flags & 0x0004 and len(data) > off:
        user = data[off]
        off += 1
    if flags & 0x0008 and (raw := take()) is not None:
        values["bmr_kcal"] = round(raw / 4.1868)  # kJ -> kcal
    if flags & 0x0010 and (raw := take()) is not None:
        values["muscle_pct"] = round(raw * 0.1, 1)
    if flags & 0x0020 and (raw := take()) is not None:
        values["muscle_mass_kg"] = round(_mass(raw, is_kg), 2)
    if flags & 0x0040 and (raw := take()) is not None:
        values["fat_free_mass_kg"] = round(_mass(raw, is_kg), 2)
    if flags & 0x0080 and (raw := take()) is not None:
        values["soft_lean_kg"] = _mass(raw, is_kg)
    if flags & 0x0100 and (raw := take()) is not None:
        values["water_mass_kg"] = _mass(raw, is_kg)
    if flags & 0x0200 and (raw := take()) is not None and raw:
        values["impedance"] = round(raw * 0.1, 1)
    if flags & 0x0400 and (raw := take()) is not None:
        values["weight_kg"] = round(_mass(raw, is_kg), 2)
    return user, ts, {k: v for k, v in values.items() if v}


def finish_values(values: dict) -> dict:
    """Derive water % and bone mass, drop helper fields."""
    weight = values.get("weight_kg")
    water = values.pop("water_mass_kg", None)
    soft_lean = values.pop("soft_lean_kg", None)
    if weight and water:
        values["water_pct"] = round(water / weight * 100, 1)
    fat = values.get("fat_pct")
    if weight and fat and soft_lean:
        lean = weight - weight * fat / 100
        bone = lean - soft_lean
        if 0 < bone < 10:
            values["bone_kg"] = round(bone, 2)
    return values


def parse_user_list_entry(data: bytes) -> tuple[int, str] | None:
    """Parse one Beurer user list record: (slot index, initials)."""
    if len(data) < 5:
        return None
    index = data[1] or data[0]
    raw = data[2:5]
    if raw == b"\xff\xff\xff":
        initials = ""
    else:
        initials = "".join(c for c in raw.decode("ascii", errors="ignore") if c.isalnum())
    return index, initials


class StandardSession(BaseSession):
    """Download measurements from a standard-profile Beurer scale."""

    def __init__(
        self,
        client: BleakClient,
        tz: tzinfo | None,
        model: DeviceModel,
        consents: dict[int, int],
    ) -> None:
        """Initialise."""
        super().__init__(client, tz)
        self._model = model
        self._consents = consents
        self._slot: int | None = None
        self._ucp_result: int | None = None
        self._users_done = False
        self._last_ts: dict[str, tuple[float, int]] = {}
        self.consent_failed: list[int] = []

    def _pid(self, user: int | None) -> str:
        if user is None:
            user = self._slot
        if user is None or user == GUEST_INDEX:
            self.persons.setdefault("guest", "Gast")
            return "guest"
        pid = str(user)
        self.persons.setdefault(pid, f"P{user}")
        return pid

    def _timestamp(self, pid: str, ts: int | None) -> int:
        now = time.monotonic()
        if ts is not None:
            self._last_ts[pid] = (now, ts)
            return ts
        last = self._last_ts.get(pid)
        if last and now - last[0] < SAME_PACKET_WINDOW:
            return last[1]
        ts = int(time.time())
        self._last_ts[pid] = (now, ts)
        return ts

    async def _handle(self, tag: str, data: bytes) -> None:
        if tag in ("weight", "bodycomp"):
            parser = parse_weight if tag == "weight" else parse_body_composition
            if (parsed := parser(data, self._tz)) is None:
                return
            user, ts, values = parsed
            pid = self._pid(user)
            self.add_measurement(pid, {"timestamp": self._timestamp(pid, ts), **values})
        elif tag == "ucp":
            if len(data) >= 3 and data[0] == UCP_RESPONSE and data[1] == UCP_CONSENT:
                self._ucp_result = data[2]
        elif tag == "users":
            if len(data) == 1 or (len(data) >= 1 and data[0] in (1, 2) and len(data) < 5):
                self._users_done = True
            elif entry := parse_user_list_entry(data):
                index, initials = entry
                self.persons[str(index)] = initials or f"P{index}"

    async def _read_user_list(self) -> None:
        if not self._model.user_list:
            return
        char = self._find_char(*self._model.user_list)
        if char is None:
            return
        try:
            await self._client.start_notify(char, self._notify_cb("users"))
            await self._client.write_gatt_char(char, b"\x00", response=True)
        except BleakError as err:
            _LOGGER.debug("User list not available: %s", err)
            return
        deadline = time.monotonic() + 6
        while not self._users_done and (remaining := deadline - time.monotonic()) > 0:
            try:
                await self._handle(*await self._next(remaining))
            except TimeoutError:
                break
        _LOGGER.debug("Scale users: %s", self.persons)

    async def _consent(self, ucp, slot: int, code: int) -> bool:
        self._ucp_result = None
        self._slot = slot
        await self._client.write_gatt_char(
            ucp, bytes([UCP_CONSENT, slot, code & 0xFF, (code >> 8) & 0xFF]), response=True
        )
        deadline = time.monotonic() + 6
        while self._ucp_result is None and (remaining := deadline - time.monotonic()) > 0:
            try:
                await self._handle(*await self._next(remaining))
            except TimeoutError:
                break
        if self._ucp_result != UCP_SUCCESS:
            _LOGGER.warning(
                "Scale refused consent for user slot %s (result %s). Check the consent code",
                slot,
                self._ucp_result,
            )
            self.consent_failed.append(slot)
            return False
        return True

    async def run(self, listen_time: float) -> None:
        """Run the sync. Nothing is deleted on the scale."""
        weight = self._find_char(SVC_WEIGHT_SCALE, CHR_WEIGHT_MEASUREMENT)
        if weight is None:
            raise BleakError("Weight Scale service not found")
        await self._notify_with_pairing(weight, "weight")
        if body := self._find_char(SVC_BODY_COMPOSITION, CHR_BODY_COMPOSITION):
            await self._client.start_notify(body, self._notify_cb("bodycomp"))
        ucp = self._find_char(SVC_USER_DATA, CHR_USER_CONTROL_POINT)
        if ucp is not None:
            await self._client.start_notify(ucp, self._notify_cb("ucp"))
        self.connected_ok = True
        self._log_services()
        await self._read_battery()
        await self._write_time()
        await self._read_user_list()

        if ucp is not None:
            for slot, code in self._consents.items():
                if await self._consent(ucp, slot, code):
                    # Stored measurements of this user follow as indications.
                    await self._drain(self._handle, idle=4, total=30)

        if listen_time > 0:
            await self._drain(self._handle, idle=listen_time, total=listen_time)

        for pid, lst in self.measurements.items():
            self.measurements[pid] = [finish_values(m) for m in lst if m.get("weight_kg")]
