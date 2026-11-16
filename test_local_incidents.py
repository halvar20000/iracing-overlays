"""
Offline tests for local_incidents.py (the driver's own car told apart by its
motion) and the race logger's use of it, plus the slow-down (furled black)
penalty and the "+2 = spin_or_contact" rule for cars seen only by points.

Run with:  python test_local_incidents.py

Johann Solowej (2026-11-16): a +2 was always called a spin, but iRacing gives
0x / 2x / 4x for contacts, a 0x wall touch was not logged at all, and the
furled black flag (slow down) was missing.
"""
import math
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_incidents as LI  # noqa: E402

PASS = FAIL = 0


def check(label, got, want=None, predicate=None):
    global PASS, FAIL
    ok = predicate(got) if predicate else (got == want)
    PASS += ok
    FAIL += not ok
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"  (got {got!r}, want {want!r})"))


DT = 1.0 / LI.SAMPLE_HZ


class Car:
    """World-frame car: position along the lap, world velocity, heading."""

    def __init__(self, speed=50.0):
        self.t = 100.0
        self.wv = [speed, 0.0]       # world velocity
        self.yaw = 0.0
        self.surface = 3
        self.on_pit = False
        self.inc = 0
        self.pct = 0.30
        self.others = {}
        self.active = True
        self.acc = (0.0, 0.0)

    def sample(self):
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        wx, wy = self.wv
        return {"t": self.t, "active": self.active, "speed": math.hypot(wx, wy),
                "vx": wx * c + wy * s, "vy": -wx * s + wy * c, "yaw": self.yaw,
                "long_acc": self.acc[0], "lat_acc": self.acc[1],
                "surface": self.surface, "on_pit": self.on_pit, "inc": self.inc,
                "pct": self.pct, "track_m": 4000.0, "others": dict(self.others)}


def run(det, car, seconds, step=None):
    n = int(round(seconds / DT))
    for _ in range(n):
        if step:
            step(car)
        car.t += DT
        car.pct = (car.pct + math.hypot(*car.wv) * DT / 4000.0) % 1.0
        det.update(car.sample())


def new():
    d, c = LI.LocalIncidentDetector(), Car()
    run(d, c, 1.0)
    return d, c


# 1 clean driving: corners at 3 g, brakes at 3.5 g -> nothing
d, c = new()
def corner(car):                      # heading + velocity turn together, 3 g lateral
    w = 3 * 9.81 / 50.0
    car.yaw += w * DT
    sp = math.hypot(*car.wv)
    car.wv = [sp * math.cos(car.yaw), sp * math.sin(car.yaw)]
    car.acc = (0.0, 3 * 9.81)
run(d, c, 3.0, corner)
def brake(car):
    sp = max(10.0, math.hypot(*car.wv) - 3.5 * 9.81 * DT)
    car.wv = [sp * math.cos(car.yaw), sp * math.sin(car.yaw)]
    car.acc = (-3.5 * 9.81, 0.0)
run(d, c, 1.0, brake)
c.acc = (0.0, 0.0)
run(d, c, 6.0)
check("clean cornering + hard braking = nothing", d.drain(), [])

# 2 wall hit 50 -> 20 m/s, +2 a second later = wall_contact with 2 points
d, c = new()
c.wv = [20.0, 0.0]
run(d, c, 1.0)
c.inc = 2
run(d, c, 6.0)
ev = d.drain()
check("wall hit -> one wall_contact", [e["incident_type"] for e in ev], ["wall_contact"])
check("wall hit carries the +2", ev[0]["points"] if ev else None, 2)
check("wall hit peak_g recorded", ev[0].get("peak_g", 0) if ev else 0, predicate=lambda g: g > 20)
check("not called a spin", ev[0]["spun"] if ev else None, False)

# 3 light touch (0x): 3 m/s in one sample = 6 g
d, c = new()
c.wv = [47.0, 0.0]
run(d, c, 6.0)
ev = d.drain()
check("0x touch logged as wall_contact with 0 points",
      [(e["incident_type"], e["points"]) for e in ev], [("wall_contact", 0)])

# 4 car contact: another car 5 m away at the hit
d, c = new()
c.others = {12: c.pct + 5 / 4000.0, 3: 0.9}
c.wv = [40.0, 6.0]
run(d, c, 1.0)
c.inc = 4
run(d, c, 6.0)
ev = d.drain()
check("car contact -> collision with car 12, +4",
      [(e["incident_type"], e.get("with_car_idx"), e["points"]) for e in ev], [("collision", 12, 4)])

# 5 spin: heading turns round, world velocity stays (car slides) -> lost_control +2
d, c = new()
def spin(car):
    car.yaw += math.pi / 1.5 * DT       # 180 deg in 1.5 s
    sp = max(0.0, math.hypot(*car.wv) - 6 * DT)
    car.wv = [sp, 0.0]
run(d, c, 1.5, spin)
c.inc = 2
run(d, c, 6.0)
ev = d.drain()
check("spin -> one lost_control", [e["incident_type"] for e in ev], ["lost_control"])
check("spin carries +2 and slip angle",
      (ev[0]["points"], ev[0].get("slip_deg", 0) >= 70) if ev else None, (2, True))

# 6 wall hit that spins the car = ONE incident: wall_contact, spun
d, c = new()
c.wv = [25.0, 0.0]
run(d, c, 0.2)
run(d, c, 1.5, spin)
c.inc = 2
run(d, c, 6.0)
ev = d.drain()
check("hit + spin = one wall_contact, spun", [(e["incident_type"], e["spun"]) for e in ev],
      [("wall_contact", True)])

# 7 off track 1 s at speed, +1 -> off_track; kerb hop 0.2 s -> nothing
d, c = new()
c.surface = 0
run(d, c, 1.0)
c.surface = 3
c.inc = 1
run(d, c, 6.0)
c.surface = 0
run(d, c, 0.2)
c.surface = 3
run(d, c, 6.0)
ev = d.drain()
check("off track 1 s -> off_track +1, kerb hop ignored",
      [(e["incident_type"], e["points"]) for e in ev], [("off_track", 1)])

# 8 points with no motion -> points_only
d, c = new()
c.inc = 4
run(d, c, 6.0)
ev = d.drain()
check("points without a detection -> points_only +4",
      [(e["incident_type"], e["points"]) for e in ev], [("points_only", 4)])

# 9 pit road: stopping in the box is no contact; spectating = nothing
d, c = new()
c.on_pit = True
c.wv = [0.0, 0.0]
run(d, c, 6.0)
c.on_pit = False
c.active = False
c.wv = [50.0, 0.0]
run(d, c, 0.5)
c.wv = [5.0, 0.0]
run(d, c, 6.0)
check("pit stop / not driving -> nothing", d.drain(), [])

# 10 session jump back resets (no fake contact from the jump)
d, c = new()
c.t = 10.0
c.wv = [0.0, 0.0]
run(d, c, 6.0)
check("session time jump back -> nothing", d.drain(), [])

# 11 hold: nothing handed out before HOLD_S
d, c = new()
c.wv = [20.0, 0.0]
run(d, c, 1.0)
check("incident waits for its points (not out after 1 s)", d.drain(), [])

# ---- logger side ------------------------------------------------------
sys.modules.setdefault("irsdk", types.SimpleNamespace(IRSDK=lambda: None))
class _FakeApp:
    def __getattr__(self, name):
        def deco(*a, **k):
            if len(a) == 1 and callable(a[0]) and not k:
                return a[0]
            return lambda f: f
        return deco
_flask = types.ModuleType("flask")
_flask.Flask = lambda *a, **k: _FakeApp()
for _n in ("jsonify", "render_template_string", "send_file", "abort"):
    setattr(_flask, _n, lambda *a, **k: None)
_flask.request = types.SimpleNamespace(args={})
sys.modules.setdefault("flask", _flask)
sys.modules.setdefault("requests", types.ModuleType("requests"))
import iracing_race_logger as L  # noqa: E402


class FakeIR:
    def __init__(self, **v):
        self.v = v
    def __getitem__(self, k):
        return self.v.get(k)
    def set(self, **kw):
        self.v.update(kw)


DRIVERS = [{"car_idx": 0, "car_number": "7", "name": "Johann Solowej", "team": ""},
           {"car_idx": 12, "car_number": "23", "name": "Anna Gegner", "team": ""}]


def logger(ir):
    lg = L.RaceLogger.__new__(L.RaceLogger)
    lg.ir = ir
    lg._log_fp = object()
    lg.events = []
    lg._emit = lg.events.append
    lg._log_session_meta = {"drivers": DRIVERS}
    lg._prev_car_session_flags = {}
    lg._driver_incident_count = {}
    lg._incidents_logged = 0
    return lg


class FakeWatch:
    def __init__(self, evs, active=True):
        self.evs, self.active = evs, active
    def drain(self):
        out, self.evs = self.evs, []
        return out


di = lambda a, b: {"DriverCarIdx": 0, "Drivers": [
    {"CarIdx": 0, "CurDriverIncidentCount": a}, {"CarIdx": 12, "CurDriverIncidentCount": b}]}
lg = logger(FakeIR(SessionTime=10.0, DriverInfo=di(0, 0)))
lg._local_watch = FakeWatch([{"t_session": 9.0, "incident_type": "collision", "points": 4,
                              "detected": ["contact", "spin"], "spun": True, "off_track": False,
                              "peak_g": 14.2, "with_car_idx": 12}])
lg._dashboard_incidents_live = False
lg._incident_source_written = None
lg._last_sdk_inc_t = -1e9
lg._sdk_inc_prev = {}
lg._maybe_emit_sdk_incidents()
lg._maybe_emit_local_incidents()
lg.ir.set(SessionTime=13.0, DriverInfo=di(4, 2))
lg._maybe_emit_sdk_incidents()
inc = [e for e in lg.events if e["type"] == "incident"]
check("local car: one event from its motion, not from points",
      [(e["source"], e["car_number"], e["incident_type"]) for e in inc if e["car_idx"] == 0],
      [("local", "7", "collision")])
loc = [e for e in inc if e.get("source") == "local"]
check("local event names the other car + points",
      (loc[0]["with_car_number"], loc[0]["with_driver"], loc[0]["details"]) if loc else None,
      ("23", "Anna Gegner", "contact with a car #23 Anna Gegner, spun, +4x"))
check("other car +2 -> spin_or_contact (not a spin)",
      [e["incident_type"] for e in inc if e["car_idx"] == 12], ["spin_or_contact"])

# watch inactive (spectating): the local car's points are used again
lg2 = logger(FakeIR(SessionTime=10.0, DriverInfo=di(0, 0)))
lg2._local_watch = FakeWatch([], active=False)
lg2._dashboard_incidents_live = False
lg2._incident_source_written = None
lg2._last_sdk_inc_t = -1e9
lg2._sdk_inc_prev = {}
lg2._maybe_emit_sdk_incidents()
lg2.ir.set(SessionTime=13.0, DriverInfo=di(2, 0))
lg2._maybe_emit_sdk_incidents()
check("not driving -> points path for car 0 again",
      [e["incident_type"] for e in lg2.events if e["type"] == "incident"], ["spin_or_contact"])

# slow down (furled black 0x80000) + its duration
lg3 = logger(FakeIR(SessionTime=50.0, CarIdxSessionFlags=[0, 0]))
lg3._maybe_emit_penalty_events()
lg3.ir.set(SessionTime=51.0, CarIdxSessionFlags=[0x80000, 0])
lg3._maybe_emit_penalty_events()
lg3.ir.set(SessionTime=59.5, CarIdxSessionFlags=[0, 0])
lg3._maybe_emit_penalty_events()
check("slowdown penalty logged", [(e["type"], e["penalty_type"], e["car_number"]) for e in lg3.events],
      [("penalty", "slowdown", "7"), ("penalty_end", "slowdown", "7")])
check("slowdown duration", lg3.events[-1].get("duration_s") if lg3.events else None, 8.5)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
