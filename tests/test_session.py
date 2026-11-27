"""Offline tests with fake devices. Run: python3 tests/test_session.py custom_components/beurer_ble"""
import asyncio, struct, sys, types, importlib.util, pathlib
pkg_dir = pathlib.Path(sys.argv[1])
# stub bleak (no Home Assistant / bleak needed)
bleak = types.ModuleType("bleak"); bleak.BleakClient = object; sys.modules["bleak"] = bleak
exc = types.ModuleType("bleak.exc")
class BleakError(Exception): pass
exc.BleakError = BleakError; sys.modules["bleak.exc"] = exc; bleak.exc = exc
pkg = types.ModuleType("bf"); pkg.__path__ = [str(pkg_dir)]; sys.modules["bf"] = pkg
def load(name):
    spec = importlib.util.spec_from_file_location(f"bf.{name}", pkg_dir / f"{name}.py")
    m = importlib.util.module_from_spec(spec); sys.modules[f"bf.{name}"] = m; spec.loader.exec_module(m); return m
gatt = load("gatt"); devices = load("devices"); proto = load("sanitas_protocol"); sess = load("sanitas_session")
std = load("standard_session"); bpm = load("bpm_session")

S = 0xF7
UID = 0x65
def meas_payload(ts, kg, imp, fat, water, muscle, bone):
    return struct.pack(">IHHHHHH", ts, int(kg*1000/50), imp, int(fat*10), int(water*10), int(muscle*10), int(bone*1000/50))

class FakeScale:
    """Answers like a BF700. Only responds to start byte 0xF7."""
    def __init__(self): self.cb=None; self.acks=[]; self.writes=[]
    async def start_notify(self, uuid, cb): self.cb=cb
    def send(self, *frames):
        for f in frames: asyncio.get_running_loop().call_soon(self.cb, None, bytearray(f))
    async def write_gatt_char(self, uuid, data, response=True):
        self.writes.append(bytes(data))
        d=bytes(data)
        if d[0]==0xE6: return  # wrong family: no answer
        if d[0]==0xF6: self.send(bytes([0xF6,0x00,0x20])); return
        if d[0]==0xF9: return
        if d[1]==0xF1: self.acks.append(d); return
        cmd=d[1]
        if cmd==0x4F: self.send(bytes([S,0xF0,0x4F,0x00,87,0,0,1,0,0,0,3]))
        elif cmd==0x33:
            self.send(bytes([S,0xF0,0x33,0x00,1,8]))
            name=b"COD"
            self.send(bytes([S,0x34,1,1])+struct.pack(">Q",UID)+name+bytes([85]))
        elif cmd==0x41:
            uid=struct.unpack(">Q",d[2:10])[0]; assert uid==UID
            r1=meas_payload(1764200000,80.0,500,22.3,55.1,38.4,3.2)
            r2=meas_payload(1764300000|0x80000000,79.5,510,22.0,55.4,38.6,3.2)
            self.send(bytes([S,0xF0,0x41,4]))
            for i,r in enumerate([r1,r2]):
                self.send(bytes([S,0x42,4,2*i+1])+r[:8], bytes([S,0x42,4,2*i+2])+r[8:])

async def main():
    fake=FakeScale()
    s=sess.SanitasSession(fake, 0xE7)   # wrong guess on purpose -> fallback
    sess.ACK_TIMEOUT=0.3
    await s.run(listen_time=0.2)
    assert s.start_byte==0xF7, hex(s.start_byte)
    assert s.status.battery==87
    u=s.users[UID]; assert u.initials=="COD" and u.birth_year==1985, u
    ms=sorted(s.measurements[UID], key=lambda m:m.timestamp)
    assert len(ms)==2, ms   # refetch must not duplicate
    assert ms[1].timestamp==1764300000 and ms[1].weight_kg==79.5 and ms[1].fat_pct==22.0 and ms[1].bone_kg==3.2, ms[1]
    assert not any(w[1]==0x43 for w in fake.writes if len(w)>1), "must not delete"
    assert len(fake.acks)==1+4*2, len(fake.acks)
    print("OK", ms[1])
asyncio.run(main())
assert proto.start_byte_for_name("BEURER BF700")==0xF7
assert proto.start_byte_for_name("BF700")==0xE7
assert proto.is_supported_name("Beurer BF710") and not proto.is_supported_name("Shelly")
print("sanitas name tests OK")

# ---------------------------------------------------------------- device detection
I = devices.identify
assert I("BEURER BF700").family == "sanitas"
assert I("BF710").family == "sanitas"
assert I("BF720").family == "standard" and I("BF720").user_list[1].startswith("00000001")
assert I("Beurer BF600").user_list[0].startswith("0000fff0")
assert I("SBF72").family == "standard" and I("SBF70").family == "sanitas"
assert I("BM85").family == "bpm" and I("Beurer BM 54").family == "bpm"
assert I("XYZ", ["00001810-0000-1000-8000-00805f9b34fb"]).family == "bpm"
assert I("BF450") is None and I("Shelly") is None and I("BMW-Key") is None
print("detection OK")

# ---------------------------------------------------------------- SFLOAT / BP parsing
assert gatt.sfloat(0x0078) == 120 and gatt.sfloat(0xF4D2) == 123.4
def bp_packet(sys_, dia, mean, dt, pulse, user, status, swap_pulse=False):
    flags = 0x02 | 0x04 | 0x08 | 0x10
    p = struct.pack("<BHHH", flags, sys_, dia, mean) + struct.pack("<HBBBBB", *dt)
    p += struct.pack(">H" if swap_pulse else "<H", pulse) + bytes([user]) + struct.pack("<H", status)
    return p
from datetime import timezone
u, ts, v = bpm.parse_bp_measurement(bp_packet(128, 84, 99, (2026, 11, 27, 8, 30, 0), 66, 2, 0x0004, swap_pulse=True), timezone.utc)
assert u == 2 and v["systolic"] == 128 and v["diastolic"] == 84 and v["pulse"] == 66 and v["irregular_pulse"], v
assert ts == 1795768200, ts
print("bp parse OK", v)

# ---------------------------------------------------------------- fake GATT client
class Char:
    def __init__(self, uuid): self.uuid = uuid
class Svc:
    def __init__(self, uuid, chars): self.uuid = uuid; self.chars = {c: Char(c) for c in chars}
    def get_characteristic(self, u): return self.chars.get(u)
class Services:
    def __init__(self, svcs): self.s = {x.uuid: x for x in svcs}
    def get_service(self, u): return self.s.get(u)
class FakeGatt:
    def __init__(self, svcs): self.services = Services(svcs); self.cbs = {}; self.writes = []; self.paired = False
    def send(self, uuid, data):
        asyncio.get_running_loop().call_soon(self.cbs[uuid], None, bytearray(data))
    async def start_notify(self, char, cb): self.cbs[char.uuid] = cb
    async def read_gatt_char(self, char): return bytearray([64])
    async def pair(self): self.paired = True
    async def write_gatt_char(self, char, data, response=True):
        self.writes.append((char.uuid, bytes(data))); self.on_write(char.uuid, bytes(data))
    def on_write(self, uuid, data): pass

U = gatt.uuid16
class FakeBM85(FakeGatt):
    def __init__(self):
        super().__init__([Svc(U(0x1810), [U(0x2A35)]), Svc(U(0x180F), [U(0x2A19)])])
    async def start_notify(self, char, cb):
        await super().start_notify(char, cb)
        self.send(U(0x2A35), bp_packet(120, 80, 93, (2026, 11, 26, 7, 0, 0), 70, 1, 0, swap_pulse=True))
        self.send(U(0x2A35), bp_packet(131, 86, 101, (2026, 11, 27, 7, 5, 0), 72, 1, 0, swap_pulse=True))

async def test_bpm():
    s = bpm.BPMSession(FakeBM85(), timezone.utc)
    task = asyncio.create_task(s.run(0))
    await asyncio.sleep(0.05); s.on_disconnect()   # monitor hangs up after sending
    await task
    ms = s.measurements["1"]
    assert len(ms) == 2 and ms[1]["systolic"] == 131 and ms[1]["pulse"] == 72, ms
    assert s.battery == 64 and s.persons == {"1": "Benutzer 1"}
    print("bpm session OK")
asyncio.run(test_bpm())

# ---------------------------------------------------------------- standard scale (BF720)
def weight_packet(kg, dt, user):
    return struct.pack("<BH", 0x02 | 0x04, round(kg / 0.005)) + struct.pack("<HBBBBB", *dt) + bytes([user])
def bodycomp_packet(fat, dt, user, muscle, soft_lean, water, imp):
    flags = 0x0002 | 0x0004 | 0x0010 | 0x0080 | 0x0100 | 0x0200
    return (struct.pack("<HH", flags, round(fat * 10)) + struct.pack("<HBBBBB", *dt) + bytes([user])
            + struct.pack("<HHHH", round(muscle * 10), round(soft_lean / 0.005), round(water / 0.005), round(imp * 10)))

class FakeBF720(FakeGatt):
    CODE = 4711
    def __init__(self):
        super().__init__([Svc(U(0x181D), [U(0x2A9D)]), Svc(U(0x181B), [U(0x2A9C)]), Svc(U(0x181C), [U(0x2A9F)]),
                          Svc(U(0xFFFF), [U(0x0001)]), Svc(U(0x180F), [U(0x2A19)]), Svc(U(0x1805), [U(0x2A2B)])])
    def on_write(self, uuid, d):
        if uuid == U(0x0001):
            self.send(uuid, bytes([0, 1]) + b"COD" + struct.pack("<HBBBBB", 1985, 3, 1, 180, 0, 3))
            self.send(uuid, bytes([0, 2]) + b"ANN" + struct.pack("<HBBBBB", 1988, 5, 2, 168, 1, 3))
            self.send(uuid, bytes([1]))
        elif uuid == U(0x2A9F) and d[0] == 0x02:
            slot, code = d[1], d[2] | d[3] << 8
            ok = code == self.CODE and slot == 1
            self.send(uuid, bytes([0x20, 0x02, 0x01 if ok else 0x05]))
            if ok:
                dt = (2026, 11, 27, 7, 15, 0)
                self.send(U(0x2A9D), weight_packet(81.2, dt, 1))
                self.send(U(0x2A9C), bodycomp_packet(21.5, dt, 1, 40.1, 60.5, 47.0, 512.3))

async def test_std():
    dev = FakeBF720()
    s = std.StandardSession(dev, timezone.utc, devices.identify("BF720"), {1: 4711, 2: 1111})
    await s.run(0.1)
    assert s.persons["1"] == "COD" and s.persons["2"] == "ANN", s.persons
    assert s.consent_failed == [2], s.consent_failed
    [m] = s.measurements["1"]
    assert m["weight_kg"] == 81.2 and m["fat_pct"] == 21.5 and m["muscle_pct"] == 40.1, m
    assert m["water_pct"] == round(47.0 / 81.2 * 100, 1) and m["impedance"] == 512.3, m
    assert abs(m["bone_kg"] - (81.2 * (1 - 0.215) - 60.5)) < 0.02, m
    assert "water_mass_kg" not in m and s.battery == 64
    assert any(u == U(0x2A2B) for u, _ in dev.writes), "clock not set"
    print("standard session OK", m)
asyncio.run(test_std())
print("ALL OK")

# ---------------------------------------------------------------- guest assignment by weight
const = load("const"); assign = load("assign")
pick = assign.pick_by_weight
assert pick(81.0, {"1": 80.2, "2": 62.0}) == "1"
assert pick(70.0, {"1": 80.2, "2": 62.0}) is None          # nobody close enough
assert pick(80.0, {"1": 79.5, "2": 81.0}) is None          # ambiguous
assert pick(62.5, {"2": 62.0}) == "2" and pick(62.5, {}) is None
print("guest assignment OK")

# ---------------------------------------------------------------- BF700: empty scale, create user, live weight
class EmptyScale(FakeScale):
    """Reset scale: no users. Answers status late (busy weighing)."""
    async def write_gatt_char(self, uuid, data, response=True):
        d = bytes(data); self.writes.append(d)
        if d[0] == 0xF6: self.send(bytes([0xF6, 0x00, 0x09])); return
        if d[0] == 0xF9 or d[1] == 0xF1: return
        cmd = d[1]
        if cmd == 0x4F:
            self.send(bytes([S, 0x58, 0x01, 0x02, 0x2A]), bytes([S, 0x58, 0x00, 0x06, 0x36]))  # 27.7 settling, 79.5 stable
            asyncio.get_running_loop().call_later(0.2, self.cb, None, bytearray([S, 0xF0, 0x4F, 0, 75, 0, 0, 1, 1, 1, 1, 3]))
        elif cmd == 0x33: self.send(bytes([S, 0xF0, 0x33, 0x00, 0, 8]))
        elif cmd == 0x31:
            self.added = d
            self.send(bytes([S, 0xF0, 0x31, 0x00]))
        elif cmd == 0x40:
            uid = d[2:10]
            self.send(bytes([S, 0xF0, 0x40, 0x00]))
            r = meas_payload(1764400000, 80.3, 505, 21.9, 55.0, 38.9, 3.2)
            self.send(bytes([S, 0x59, 3, 1, 0]) + uid, bytes([S, 0x59, 3, 2]) + r[:10], bytes([S, 0x59, 3, 3]) + r[10:])
        elif cmd == 0x41: self.send(bytes([S, 0xF0, 0x41, 0]))

async def test_create():
    fake = EmptyScale()
    new = proto.NewUser("cod", 1985, 3, 1, 180, True, 3)
    s = sess.SanitasSession(fake, 0xF7, new)
    await s.run(listen_time=0.1)
    assert s.status.battery == 75 and s.live_weight == 79.5, (s.status, s.live_weight)
    assert s.created_uid == 101 and s.users[101].initials == "COD"
    assert fake.added[10:13] == b"COD" and fake.added[13:] == bytes([85, 2, 1, 180, 0x83]), fake.added.hex()
    [m] = s.measurements[101]
    assert m.weight_kg == 80.3 and m.fat_pct == 21.9, m
    print("bf700 create user OK", m)
asyncio.run(test_create())

# ---------------------------------------------------------------- real BF700 frame (2026-11-27, user COD)
buf = bytes.fromhex("6b098274077c01bc0111 01cc016e00f2078f0a950115".replace(" ", ""))
m = proto.parse_measurement(buf)
assert (m.weight_kg, m.impedance, m.fat_pct, m.water_pct, m.muscle_pct, m.bone_kg) == (95.8, 444, 27.3, 46.0, 36.6, 12.1), m
assert (m.bmr_kcal, m.amr_kcal, m.bmi) == (1935, 2709, 27.7), m
assert proto.Measurement.from_dict({k: v for k, v in m.as_dict().items() if k not in ("bmr_kcal", "amr_kcal", "bmi")}).bmi is None
print("real BF700 frame OK")
