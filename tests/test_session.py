import asyncio, struct, sys, types, importlib.util, pathlib
pkg_dir = pathlib.Path(sys.argv[1])
# stub bleak
bleak = types.ModuleType("bleak"); bleak.BleakClient = object; sys.modules["bleak"] = bleak
# load package modules without __init__ (needs HA)
pkg = types.ModuleType("bf"); pkg.__path__ = [str(pkg_dir)]; sys.modules["bf"] = pkg
def load(name):
    spec = importlib.util.spec_from_file_location(f"bf.{name}", pkg_dir / f"{name}.py")
    m = importlib.util.module_from_spec(spec); sys.modules[f"bf.{name}"] = m; spec.loader.exec_module(m); return m
const = load("const"); proto = load("protocol"); sess = load("session")

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
    s=sess.BF700Session(fake, 0xE7)   # wrong guess on purpose -> fallback
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
print("name tests OK")
