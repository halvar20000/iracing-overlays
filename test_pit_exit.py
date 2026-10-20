"""Leaving the pits must not read as "moved backwards" (WCT Indianapolis
2026-10-20: the pit lane crosses the S/F line, entry ~0.80, exit ~0.05 —
every pit exit was a lost_control + replay). A real backwards slide still is.

Run:  python test_pit_exit.py [path to the project folder]
"""
import sys, types
from pathlib import Path

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
irsdk_stub = types.ModuleType("irsdk")
class _FakeIRSDK:
    def __init__(self): self.fields = {}
    def freeze_var_buffer_latest(self): pass
    def __getitem__(self, k): return self.fields.get(k)
irsdk_stub.IRSDK = _FakeIRSDK
sys.modules["irsdk"] = irsdk_stub
flask_stub = types.ModuleType("flask")
class _App:
    def __init__(self, *a, **k): pass
    def after_request(self, f): return f
    def route(self, *a, **k): return lambda f: f
flask_stub.Flask = _App; flask_stub.Response = object
flask_stub.render_template_string = lambda s, **k: s
flask_stub.jsonify = lambda *a, **k: None
flask_stub.request = types.SimpleNamespace(json=None, args={})
sys.modules["flask"] = flask_stub
sys.path.insert(0, str(root))
import iracing_dashboard as dash

L, DT, N = 4000.0, 0.1, 4

def fields(t, pcts, surfs, pits):
    return {"SessionTime": t, "SessionNum": 4, "SessionState": 4,
            "WeekendInfo": {"TrackLength": "4.00 km"},
            "DriverInfo": {"Drivers": [{"CarIdx": i, "CarNumber": str(10 + i), "UserName": f"D{i}",
                                        "CurDriverIncidentCount": -1} for i in range(N)]},
            "CarIdxTrackSurface": list(surfs), "CarIdxSessionFlags": [0] * N,
            "CarIdxLapDistPct": [p % 1.0 for p in pcts], "CarIdxClassPosition": list(range(1, N + 1)),
            "CarIdxOnPitRoad": list(pits), "CarIdxLap": [int(p) + 1 for p in pcts],
            "SessionInfo": {"Sessions": [{"SessionNum": 4, "SessionType": "Race", "SessionLaps": "unlimited"}]}}

def run(step, seconds, start=(2.70, 2.50, 2.30, 2.10)):
    """step(t, i, pct) -> (new_pct, surface, on_pit)"""
    p = dash.TelemetryPoller(poll_hz=10)
    pcts, t = list(start), 0.0
    for _ in range(int(seconds / DT)):
        t += DT
        out = [step(t, i, pcts[i]) for i in range(N)]
        pcts = [o[0] for o in out]
        p.ir.fields = fields(t, pcts, [o[1] for o in out], [o[2] for o in out])
        p._update_incidents()
    return [(x["car_idx"], x["type"], x["details"]) for x in p._incidents]

failures = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)

# P1: car 0 dives into the pit lane at 0.80, rolls through it past the S/F
# line at pit speed (~22 m/s; pit surface 1 = pit stall / road), stops 20 s,
# comes out at 0.05 of the next lap and drives on. Nothing may be reported.
def p1(t, i, pct):
    if i != 0:
        return pct + 45.0 * DT / L, 3, False
    frac = pct % 1.0
    in_pit = 0.80 <= frac or frac < 0.05 if t > 1.0 else False
    if 8.0 <= t < 28.0:                      # standing in the box
        return pct, 1, True
    if in_pit and t > 1.0:
        return pct + 22.0 * DT / L, 2, True  # pit lane
    return pct + 45.0 * DT / L, 3, False
exited = []
def p1_track(t, i, pct):
    r = p1(t, i, pct)
    if i == 0 and not r[2] and t > 30 and not exited:
        exited.append((round(t, 1), round(r[0] % 1.0, 3)))
    return r
inc = run(p1_track, 120.0, start=(2.78, 2.50, 2.30, 2.10))
check("car really left the pits after the S/F line", bool(exited) and exited[0][1] < 0.1, str(exited))
check("pit stop through the S/F line: no incident", not inc, str(inc))

# P2: a real backwards roll ON TRACK at low speed (no speed collapse can fire:
# never above 90 km/h) is still reported by the backwards detector.
def p2(t, i, pct):
    if i == 0:
        if 5.0 <= t < 5.1:
            return pct - 20.0 / L, 3, False      # thrown back 20 m in one poll (the detector's 12 m step)
        return pct + 15.0 * DT / L, 3, False     # 54 km/h
    return pct + 45.0 * DT / L, 3, False
inc = run(p2, 15.0)
check("real backwards roll on track: still reported",
      any(c == 0 and "backwards" in d for c, _, d in inc), str(inc))

print()
print("ALL PASS" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
