"""Offline test for the official-incident-points tracker in
iracing_race_logger.py (`inc` / `inc_snapshot` events).

Stubs irsdk + flask so the module imports without iRacing, then drives
_maybe_emit_incident_points() through a fake ResultsPositions timeline with
a fake clock. Run:  python3 test_inc_points.py
"""
import sys, types, json, importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---- stub out the modules the logger imports -------------------------------
for name in ("irsdk", "flask", "requests"):
    if name not in sys.modules:
        m = types.ModuleType(name)
        if name == "irsdk":
            class IRSDK:
                def __init__(self, *a, **k): pass
                def startup(self, *a, **k): return False
                def shutdown(self): pass
                def __getitem__(self, k): return None
                def freeze_var_buffer_latest(self): pass
                def unfreeze_var_buffer_latest(self): pass
            m.IRSDK = IRSDK
        if name == "flask":
            class Flask:
                def __init__(self, *a, **k): pass
                def route(self, *a, **k):
                    def deco(f): return f
                    return deco
                def before_request(self, f): return f
                def after_request(self, f): return f
                def errorhandler(self, *a, **k):
                    def deco(f): return f
                    return deco
                def run(self, *a, **k): pass
            m.Flask = Flask
            m.jsonify = lambda *a, **k: None
            m.render_template_string = lambda *a, **k: ""
            m.request = types.SimpleNamespace(args={})
            m.Response = object
            m.send_file = lambda *a, **k: None
            m.abort = lambda *a, **k: None
        sys.modules[name] = m


def load(path, modname):
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


RL = load(HERE / "iracing_race_logger.py", "rl_under_test")


class FakeIR:
    """Minimal ir[...] shim: SessionInfo / SessionNum / SessionTime / CarIdxLap."""
    def __init__(self):
        self.incidents = {}      # car_idx -> official incident points
        self.session_time = 0.0
        self.laps = {}           # car_idx -> lap
        self.session_num = 3

    def __getitem__(self, key):
        if key == "SessionInfo":
            return {"Sessions": [{
                "SessionNum": self.session_num,
                "ResultsPositions": [
                    {"CarIdx": idx, "Incidents": inc}
                    for idx, inc in sorted(self.incidents.items())
                ],
            }]}
        if key == "SessionNum":
            return self.session_num
        if key == "SessionTime":
            return self.session_time
        if key == "CarIdxLap":
            return [self.laps.get(i, 0) for i in range(64)]
        return None


class Harness(RL.RaceLogger):
    """RaceLogger with the SDK, the log file and the clock replaced."""
    def __init__(self):
        self.ir = FakeIR()
        self.events = []
        self._log_fp = object()          # truthy: "a log is open"
        self._log_session_meta = {"drivers": [
            {"car_idx": 3, "car_number": "13", "name": "Patrick Auer", "team": "KEK Racing"},
            {"car_idx": 7, "car_number": "067", "name": "Thomas Herbrig", "team": "CAS-Tech"},
        ]}
        self._inc_points = {}
        self._inc_last_poll = -1e9
        self._inc_last_snapshot = -1e9
        self.now = 1000.0

    def _emit(self, event):
        event.setdefault("t_wall", "STUB")
        self.events.append(event)

    def tick(self, advance=RL.INC_POLL_INTERVAL):
        """Advance the fake clock and run one incident poll."""
        self.now += advance
        real = RL.time.monotonic
        RL.time.monotonic = lambda: self.now
        try:
            self._maybe_emit_incident_points()
        finally:
            RL.time.monotonic = real


results = []
def check(label, cond, detail=""):
    results.append((label, cond, detail))
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"   {detail}" if detail and not cond else ""))


def of(h, t):
    return [e for e in h.events if e["type"] == t]


print("\n--- 1. baseline is adopted silently, no phantom incidents ---")
h = Harness()
h.ir.incidents = {3: 4, 7: 0}          # logger attaches mid-race
h.ir.session_time = 500.0
h.tick()
check("no inc event on first sighting", len(of(h, "inc")) == 0, of(h, "inc"))
check("baseline stored", h._inc_points == {3: 4, 7: 0}, h._inc_points)
check("snapshot written immediately", len(of(h, "inc_snapshot")) == 1)
check("snapshot carries both cars", of(h, "inc_snapshot")[0]["totals"] == {"3": 4, "7": 0})

print("\n--- 2. a 2x incident emits one event with delta and total ---")
h.ir.session_time = 512.0
h.ir.laps = {3: 9}
h.ir.incidents[3] = 6
h.tick()
inc = of(h, "inc")
check("exactly one inc event", len(inc) == 1, inc)
check("delta = 2", inc and inc[0]["delta"] == 2)
check("total = 6", inc and inc[0]["total"] == 6)
check("car identified", inc and inc[0]["car_number"] == "13" and inc[0]["driver"] == "Patrick Auer")
check("team carried", inc and inc[0]["team"] == "KEK Racing")
check("lap carried", inc and inc[0]["lap"] == 9)
check("race time carried", inc and inc[0]["t_session"] == 512.0)

print("\n--- 3. unchanged counts stay silent ---")
before = len(of(h, "inc"))
h.tick(); h.tick(); h.tick()
check("no events without a change", len(of(h, "inc")) == before)

print("\n--- 4. polling is throttled to INC_POLL_INTERVAL ---")
h2 = Harness()
h2.ir.incidents = {3: 0}
h2.tick()                                  # baseline
h2.ir.incidents[3] = 1
h2.tick(advance=0.4)                       # too soon
check("read skipped inside the interval", len(of(h2, "inc")) == 0)
h2.tick(advance=RL.INC_POLL_INTERVAL)      # now due
check("read happens once due", len(of(h2, "inc")) == 1)

print("\n--- 5. several cars in one poll ---")
h3 = Harness()
h3.ir.incidents = {3: 0, 7: 0}
h3.tick()
h3.ir.incidents = {3: 1, 7: 4}
h3.tick()
inc = of(h3, "inc")
check("one event per car", len(inc) == 2, inc)
check("deltas correct", sorted(e["delta"] for e in inc) == [1, 4])

print("\n--- 6. a car appearing later gets a baseline, not an event ---")
h3.ir.incidents[11] = 8                    # late joiner already carrying points
h3.tick()
check("late joiner emits nothing", len([e for e in of(h3, "inc") if e["car_idx"] == 11]) == 0)
check("late joiner is tracked", h3._inc_points[11] == 8)
h3.ir.incidents[11] = 10
h3.tick()
late = [e for e in of(h3, "inc") if e["car_idx"] == 11]
check("its next change does emit", len(late) == 1 and late[0]["delta"] == 2)
check("unknown car still logged", late and late[0]["car_number"] == "" and late[0]["driver"] == "")

print("\n--- 7. a backwards jump (session reset) is adopted silently ---")
h4 = Harness()
h4.ir.incidents = {3: 12}
h4.tick()
h4.ir.incidents[3] = 0
h4.tick()
check("no event on reset", len(of(h4, "inc")) == 0)
check("value adopted", h4._inc_points[3] == 0)
h4.ir.incidents[3] = 1
h4.tick()
check("counting resumes from the new base", len(of(h4, "inc")) == 1 and of(h4, "inc")[0]["delta"] == 1)

print("\n--- 8. snapshot cadence ---")
h5 = Harness()
h5.ir.incidents = {3: 0}
h5.tick()
check("first snapshot at once", len(of(h5, "inc_snapshot")) == 1)
for _ in range(int(RL.INC_SNAPSHOT_INTERVAL // RL.INC_POLL_INTERVAL) - 1):
    h5.tick()
check("none before the interval is up", len(of(h5, "inc_snapshot")) == 1, len(of(h5, "inc_snapshot")))
h5.tick()
check("second snapshot after the interval", len(of(h5, "inc_snapshot")) == 2)

print("\n--- 9. broken SessionInfo must not raise ---")
h6 = Harness()
h6.ir.incidents = {3: 1}
h6.tick()
class Broken(FakeIR):
    def __getitem__(self, key):
        if key == "SessionInfo":
            raise RuntimeError("half-written YAML")
        return super().__getitem__(key)
b = Broken(); b.incidents = h6.ir.incidents; h6.ir = b
try:
    h6.tick()
    check("survives a SessionInfo read error", True)
except Exception as e:
    check("survives a SessionInfo read error", False, repr(e))

print("\n--- 10. nothing is written when no log is open ---")
h7 = Harness()
h7._log_fp = None
h7.ir.incidents = {3: 5}
h7.tick()
check("no events, no state", h7.events == [] and h7._inc_points == {})

print("\n--- 11. events are valid JSON lines ---")
ok = True
for e in h.events + h3.events:
    try:
        json.loads(json.dumps(e, separators=(",", ":")))
    except Exception:
        ok = False
check("every event serialises", ok)

passed = sum(1 for _, c, _ in results if c)
print(f"\n{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
