"""Offline scenario tests for the dashboard's incident detection
(speed-collapse + quiet off-track + yellow-zone dedup), 2026-06-04.
"""
import sys, types

# ── stubs before importing the dashboard ────────────────────────────────────
irsdk_stub = types.ModuleType("irsdk")
class _FakeIRSDK:
    def __init__(self): self.fields = {}
    def freeze_var_buffer_latest(self): pass
    def __getitem__(self, k): return self.fields.get(k)
    def startup(self): return True
    def shutdown(self): pass
    is_initialized = True
    is_connected = True
irsdk_stub.IRSDK = _FakeIRSDK
sys.modules["irsdk"] = irsdk_stub

flask_stub = types.ModuleType("flask")
class _App:
    def __init__(self, *a, **k): pass
    def after_request(self, f): return f
    def route(self, *a, **k): return lambda f: f
    def run(self, *a, **k): pass
flask_stub.Flask = _App
flask_stub.Response = object
flask_stub.render_template_string = lambda s, **k: s
flask_stub.jsonify = lambda *a, **k: None
flask_stub.request = types.SimpleNamespace(json=None, args={})
sys.modules["flask"] = flask_stub

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import iracing_dashboard as dash

L = 4000.0
DT = 0.1

def make_poller():
    p = dash.TelemetryPoller(poll_hz=10)
    return p

def base_fields(t, pcts, surfs, pits, flags=None, sess_state=4):
    n = len(pcts)
    return {
        "SessionTime": t,
        "SessionNum": 4,
        "SessionState": sess_state,
        "WeekendInfo": {"TrackLength": "4.00 km"},
        "DriverInfo": {"Drivers": [
            {"CarIdx": i, "CarNumber": str(10 + i),
             "UserName": f"Driver {i}", "CurDriverIncidentCount": -1}
            for i in range(n)]},
        "CarIdxTrackSurface": list(surfs),
        "CarIdxSessionFlags": list(flags) if flags else [0] * n,
        "CarIdxLapDistPct": [p % 1.0 for p in pcts],
        "CarIdxClassPosition": list(range(1, n + 1)),
        "CarIdxOnPitRoad": list(pits),
        "CarIdxLap": [int(p) + 1 for p in pcts],
    }

class Sim:
    """4 cars; speeds in m/s controlled per scenario via v(t, idx)."""
    def __init__(self, p, v_fn, surf_fn=None, pit_fn=None, flag_fn=None):
        self.p, self.v_fn = p, v_fn
        self.surf_fn = surf_fn or (lambda t, i: 3)
        self.pit_fn = pit_fn or (lambda t, i: False)
        self.flag_fn = flag_fn or (lambda t, i: 0)
        self.pcts = [0.10, 0.40, 0.403, 0.70]   # cars 1+2 close together
        self.t = 0.0

    def run(self, seconds):
        steps = int(seconds / DT)
        for _ in range(steps):
            self.t += DT
            for i in range(4):
                self.pcts[i] += self.v_fn(self.t, i) * DT / L
            self.p.ir.fields = base_fields(
                self.t, self.pcts,
                [self.surf_fn(self.t, i) for i in range(4)],
                [self.pit_fn(self.t, i) for i in range(4)],
                [self.flag_fn(self.t, i) for i in range(4)])
            self.p._update_incidents()

failures = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond: failures.append(name)

def incidents(p):
    return [(i["car_idx"], i["type"], i["details"]) for i in p.incidents_list()]

# expose deque
dash.TelemetryPoller.incidents_list = lambda self: list(self._incidents)

# ── S1: solo recovered spin (45 m/s → 2 m/s → back), no one nearby ─────────
p = make_poller()
def v1(t, i):
    if i != 1:
        return 50.0
    if t < 4.0:      return 45.0
    if t < 5.2:      return max(2.0, 45.0 - (t - 4.0) * 36.0)
    if t < 6.0:      return 2.0
    return min(40.0, 2.0 + (t - 6.0) * 13.0)
sim = Sim(p, v1)
sim.pcts = [0.10, 0.40, 0.70, 0.90]      # car 1 isolated for this one
sim.run(10.0)
inc = incidents(p)
check("S1 exactly one incident", len(inc) == 1, str(inc))
check("S1 is a spin (lost_control)",
      len(inc) == 1 and inc[0][0] == 1 and inc[0][1] == "lost_control", str(inc))

# ── S2: two cars collapse together → collision ──────────────────────────────
p = make_poller()
def v2(t, i):
    if i not in (1, 2):
        return 50.0
    if t < 4.0:  return 45.0
    if t < 5.0:  return max(1.0, 45.0 - (t - 4.0) * 44.0)
    return 1.0
sim = Sim(p, v2)
sim.run(7.0)
inc = incidents(p)
types_ = {c: ty for c, ty, _ in inc}
check("S2 both cars reported", set(types_) == {1, 2}, str(inc))
check("S2 classified as collision",
      all(ty == "collision" for ty in types_.values()), str(inc))

# ── S3: hard braking into a 47 km/h hairpin → NO incident ───────────────────
p = make_poller()
def v3(t, i):
    if i != 1:
        return 50.0
    if t < 4.0:  return 70.0
    if t < 7.5:  return max(13.0, 70.0 - (t - 4.0) * 16.3)
    return min(60.0, 13.0 + (t - 7.5) * 12.0)
sim = Sim(p, v3)
sim.pcts = [0.10, 0.40, 0.70, 0.90]
sim.run(12.0)
check("S3 hairpin braking: no incidents", len(incidents(p)) == 0,
      str(incidents(p)))

# ── S4: pit entry (slow on approach surface) → NO incident ──────────────────
p = make_poller()
def v4(t, i):
    if i != 1:
        return 50.0
    if t < 4.0:  return 45.0
    if t < 6.0:  return max(22.0, 45.0 - (t - 4.0) * 12.0)
    return max(0.0, 22.0 - (t - 6.0) * 8.0)
def surf4(t, i):
    if i == 1 and t >= 6.0:
        return 2   # approaching pits
    return 3
sim = Sim(p, v4, surf_fn=surf4)
sim.pcts = [0.10, 0.40, 0.70, 0.90]
sim.run(10.0)
check("S4 pit entry: no incidents", len(incidents(p)) == 0, str(incidents(p)))

# ── S5: kerb hop (0.2 s off-track at speed) → NO entry ──────────────────────
p = make_poller()
def surf5(t, i):
    if i == 1 and 4.0 <= t < 4.2:
        return 0
    return 3
sim = Sim(p, lambda t, i: 45.0, surf_fn=surf5)
sim.pcts = [0.10, 0.40, 0.70, 0.90]
sim.run(6.0)
check("S5 kerb hop: no incidents", len(incidents(p)) == 0, str(incidents(p)))

# ── S6: sustained off-track at speed (1 s) → ONE quiet off_track ────────────
p = make_poller()
def surf6(t, i):
    if i == 1 and 4.0 <= t < 5.0:
        return 0
    return 3
sim = Sim(p, lambda t, i: 45.0, surf_fn=surf6)
sim.pcts = [0.10, 0.40, 0.70, 0.90]
sim.run(7.0)
inc = incidents(p)
check("S6 one off_track entry",
      len(inc) == 1 and inc[0][:2] == (1, "off_track"), str(inc))

# ── S7: collapse first, local yellow 2 s later → no duplicate, right car ────
p = make_poller()
collapse_t = 4.0
def v7(t, i):
    if i != 1:
        return 50.0
    if t < collapse_t:    return 45.0
    if t < collapse_t + 1.2: return max(2.0, 45.0 - (t - collapse_t) * 36.0)
    return 2.0
def flags7(t, i):
    # passing cars carry the local yellow near car 1's zone after t=6
    if t >= 6.0 and i == 0:
        return 0x0008
    return 0
sim = Sim(p, v7, flag_fn=flags7)
# put car 0 close enough to be "in the zone" but not touching (>0.0045)
sim.pcts = [0.394, 0.40, 0.70, 0.90]
sim.run(9.0)
inc = incidents(p)
check("S7 single report despite yellow", len(inc) == 1, str(inc))
check("S7 culprit is the spun car",
      len(inc) == 1 and inc[0][0] == 1, str(inc))

# ── S8: Miami-style slow corner — every car crawls at 40 km/h → NO reports ──
p = make_poller()
def v8(t, i):
    t0 = 4.0 + i * 5.0
    if t0 <= t < t0 + 4.0:
        return 11.0          # ~40 km/h hairpin crawl
    return 45.0
sim = Sim(p, v8)
sim.pcts = [0.10, 0.40, 0.60, 0.90]
sim.run(26.0)
check("S8 slow corner: no incidents", len(incidents(p)) == 0,
      str(incidents(p)))

# ── S9: genuinely stopped car (stalled, 3.5 s at ~2 km/h) → ONE report ──────
p = make_poller()
def v9(t, i):
    if i != 1:
        return 45.0
    if t < 4.0:   return 30.0
    # gentle coast-down (3 m/s^2) — never a collapse-shaped drop
    if t < 14.0:  return max(0.5, 30.0 - (t - 4.0) * 3.0)
    return 0.5
sim = Sim(p, v9)
sim.pcts = [0.10, 0.40, 0.70, 0.90]
sim.run(22.0)
inc = incidents(p)
check("S9 stalled car: exactly one report",
      len(inc) == 1 and inc[0][0] == 1 and inc[0][1] == "lost_control"
      and "stopped" in inc[0][2], str(inc))

print()
print("ALL PASS" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
