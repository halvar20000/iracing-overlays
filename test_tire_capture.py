"""
Offline tests for the in-box tire / setup capture in iracing_race_logger.

Run with:  python test_tire_capture.py

There is no iRacing and no pyirsdk here, so irsdk/flask/requests are
stubbed and the RaceLogger is driven through a scripted fake telemetry
source. What matters is the thing nobody can check from a replay
afterwards: that a stop where four new tires went on is reported as
FOUR CHANGED CORNERS AND FRESH, a stop that remounted a scuffed set is
reported as changed-but-not-fresh, a stop with no tire change does not
invent one, and a spectated car's pit event keeps exactly its old shape.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# --- stub the three modules the logger imports but we don't need here ---
sys.modules.setdefault("irsdk", types.SimpleNamespace(IRSDK=lambda: None))
class _FakeApp:
    """Swallows @app.route(...) / @app.before_request and friends."""

    def __getattr__(self, name):
        def deco(*a, **k):
            if len(a) == 1 and callable(a[0]) and not k:
                return a[0]              # @app.before_request
            return lambda f: f           # @app.route("/x")
        return deco


_flask = types.ModuleType("flask")
_flask.Flask = lambda *a, **k: _FakeApp()
for _n in ("jsonify", "render_template_string", "send_file", "abort"):
    setattr(_flask, _n, lambda *a, **k: None)
_flask.request = types.SimpleNamespace(args={})
sys.modules.setdefault("flask", _flask)
sys.modules.setdefault("requests", types.ModuleType("requests"))

import iracing_race_logger as L  # noqa: E402

PASS = FAIL = 0


def check(label, got, want=None, predicate=None):
    global PASS, FAIL
    ok = predicate(got) if predicate else (got == want)
    if ok:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}  (got {got!r}"
              + ("" if predicate else f", want {want!r}") + ")")


class FakeIR:
    """Minimal stand-in for irsdk.IRSDK: ir["VarName"] lookups."""

    def __init__(self, **vars):
        self.vars = vars

    def __getitem__(self, key):
        return self.vars.get(key)

    def set(self, **kw):
        self.vars.update(kw)


def logger(ir, drivers=None):
    """A RaceLogger wired to a fake SDK, collecting emitted events."""
    lg = L.RaceLogger.__new__(L.RaceLogger)
    lg.ir = ir
    lg._log_fp = object()          # truthy: "a log is open"
    lg.events = []
    lg._emit = lg.events.append    # bypass the file writer
    lg._log_session_meta = {"drivers": drivers or [
        {"car_idx": 0, "car_number": "7", "name": "Johann Solowej", "team": ""},
    ]}
    lg._wear_samples = L.deque(maxlen=L.WEAR_SAMPLES_MAX)
    lg._wear_latest = None
    lg._pit_sv_seen = 0
    lg._pit_sv_compound = None
    lg._pit_sv_fuel = None
    lg._local_on_pit = False
    lg._setup_sig = None
    lg._pit_in_pit = {}
    lg._pit_entry_t = {}
    lg._pit_entry_lap = {}
    lg._pit_count = {}
    lg._last_pit_duration = {}
    return lg


def wear_vars(lf, rf, lr, rr):
    """iRacing's 12 wear variables from four per-corner fractions."""
    out = {}
    for prefix, val in zip(("LF", "RF", "LR", "RR"), (lf, rf, lr, rr)):
        for suffix, v in zip(("wearL", "wearM", "wearR"), val):
            out[prefix + suffix] = v
    return out


def tick(lg, t, on_pit, **extra):
    """One poll: advance the clock, set pit road, sample, emit pit events."""
    lg.ir.set(SessionTime=t, CarIdxOnPitRoad=[on_pit], CarIdxLap=[12], **extra)
    lg._sample_local_pit_state()
    lg._maybe_emit_pit_events()


DRIVER_INFO = {"DriverCarIdx": 0, "DriverSetupName": "spa_race_v4.sto",
               "DriverSetupIsModified": 0, "DriverSetupLoadTypeName": "user",
               "DriverSetupPassedTech": 1}

FRESH = (1.0, 1.0, 1.0)
WORN  = (0.44, 0.41, 0.38)     # right-front style wear spread
SCUFF = (0.70, 0.69, 0.68)


print("\n[a stop on four new tires]")
ir = FakeIR(DriverInfo=DRIVER_INFO, **wear_vars(FRESH, FRESH, FRESH, FRESH))
lg = logger(ir)
tick(lg, 100.0, False)                                   # on track
ir.set(**wear_vars(WORN, WORN, WORN, WORN))
tick(lg, 110.0, True, PitSvFlags=0x1F, PitSvFuel=48.0,   # in the box, worn
     PitSvTireCompound=1)
ir.set(**wear_vars(FRESH, FRESH, FRESH, FRESH))
tick(lg, 128.0, True, PitSvFlags=0)                      # service done
tick(lg, 140.0, False)                                   # back on track

pits = [e for e in lg.events if e["type"] == "pit"]
check("one pit event", len(pits), 1)
ev = pits[0]
check("  all four corners changed", sorted(ev.get("tires_changed", [])),
      ["lf", "lr", "rf", "rr"])
check("  left on fresh rubber", ev.get("left_on_fresh"), True)
check("  wear_in is what came off", ev["tire_wear_in"]["rf"], [44.0, 41.0, 38.0])
check("  wear_out is what went on", ev["tire_wear_out"]["rf"], [100.0, 100.0, 100.0])
check("  per-corner fresh flag", ev["tires"]["lf"]["fresh"], True)
check("  service was recorded", sorted(ev["service"]["requested"]),
      ["fuel", "lf_tire", "lr_tire", "rf_tire", "rr_tire"])
check("  fuel added", ev["service"]["fuel_l"], 48.0)
check("  compound", ev["service"]["compound"], 1)
check("  raw samples kept", len(ev["tire_wear_samples"]), 2)
check("  old keys untouched", (ev["entry_lap"], ev["stop_count"]), (12, 1))

print("\n[a scuffed set remounted — changed, but NOT fresh]")
ir = FakeIR(DriverInfo=DRIVER_INFO, **wear_vars(WORN, WORN, WORN, WORN))
lg = logger(ir)
tick(lg, 10.0, True, PitSvFlags=0x0F)
ir.set(**wear_vars(SCUFF, SCUFF, SCUFF, SCUFF))
tick(lg, 30.0, True)
tick(lg, 45.0, False)
ev = [e for e in lg.events if e["type"] == "pit"][0]
check("four corners changed", len(ev["tires_changed"]), 4)
check("  but not fresh", ev["left_on_fresh"], False)
check("  tread it left on", ev["tires"]["rf"]["wear_out"], [70.0, 69.0, 68.0])

print("\n[fuel-only stop — no tire change is invented]")
ir = FakeIR(DriverInfo=DRIVER_INFO, **wear_vars(WORN, WORN, WORN, WORN))
lg = logger(ir)
tick(lg, 10.0, True, PitSvFlags=0x10, PitSvFuel=30.0)
tick(lg, 22.0, True)
tick(lg, 35.0, False)
ev = [e for e in lg.events if e["type"] == "pit"][0]
check("no tires_changed key", "tires_changed" in ev, False)
check("no left_on_fresh claim", "left_on_fresh" in ev, False)
check("single reading -> changed unknown", ev["tires"]["rf"]["changed"], None)
check("  still reports the condition", ev["tire_wear_out"]["rf"], [44.0, 41.0, 38.0])
check("  and that only fuel was asked for", ev["service"]["requested"], ["fuel"])

print("\n[a worn-out set that stayed on is not 'fresh']")
ir = FakeIR(DriverInfo=DRIVER_INFO, **wear_vars(WORN, WORN, WORN, WORN))
lg = logger(ir)
tick(lg, 10.0, True)
tick(lg, 30.0, False)
ev = [e for e in lg.events if e["type"] == "pit"][0]
check("fresh flag false", ev["tires"]["lf"]["fresh"], False)

print("\n[spectated car: pit event keeps its old shape]")
ir = FakeIR(DriverInfo={"DriverCarIdx": None})   # no local car at all
lg = logger(ir)
tick(lg, 10.0, True)
tick(lg, 30.0, False)
ev = [e for e in lg.events if e["type"] == "pit"][0]
check("no tire keys", [k for k in ev if k.startswith("tire") or k == "tires"], [])
check("no service key", "service" in ev, False)
check("duration still there", ev["duration"], 20.0)

print("\n[pit-lane noise is still discarded]")
ir = FakeIR(DriverInfo=DRIVER_INFO, **wear_vars(WORN, WORN, WORN, WORN))
lg = logger(ir)
tick(lg, 10.0, True)
tick(lg, 11.0, False)          # 1 s < PIT_MIN_DURATION
check("no pit event", [e for e in lg.events if e["type"] == "pit"], [])

print("\n[two stops in one race don't bleed into each other]")
ir = FakeIR(DriverInfo=DRIVER_INFO, **wear_vars(WORN, WORN, WORN, WORN))
lg = logger(ir)
tick(lg, 10.0, True, PitSvFlags=0x0F)
ir.set(**wear_vars(FRESH, FRESH, FRESH, FRESH))
tick(lg, 30.0, True)
tick(lg, 45.0, False)
ir.set(**wear_vars(SCUFF, SCUFF, SCUFF, SCUFF))
tick(lg, 600.0, True, PitSvFlags=0x10, PitSvFuel=20.0)   # fuel only now
tick(lg, 615.0, True)
tick(lg, 630.0, False)
pits = [e for e in lg.events if e["type"] == "pit"]
check("two stops", len(pits), 2)
check("  second stop changed nothing", "tires_changed" in pits[1], False)
check("  second stop's service is its own", pits[1]["service"]["requested"], ["fuel"])
check("  second stop's tread is current", pits[1]["tire_wear_out"]["lf"],
      [70.0, 69.0, 68.0])
check("  first stop still reads fresh", pits[0]["left_on_fresh"], True)

print("\n[setup events]")
ir = FakeIR(DriverInfo=dict(DRIVER_INFO), SessionTime=5.0)
lg = logger(ir)
lg._maybe_emit_setup()
lg._maybe_emit_setup()                     # unchanged -> no second event
setups = [e for e in lg.events if e["type"] == "setup"]
check("one event for one setup", len(setups), 1)
check("  name", setups[0]["name"], "spa_race_v4.sto")
check("  flagged as the first", setups[0]["first"], True)
check("  not modified", setups[0]["modified"], False)
check("  load type", setups[0]["load_type"], "user")
ir.vars["DriverInfo"]["DriverSetupIsModified"] = 1      # a click in the garage
lg._maybe_emit_setup()
setups = [e for e in lg.events if e["type"] == "setup"]
check("garage edit emits again", len(setups), 2)
check("  and says so", setups[1]["modified"], True)
check("  no longer the first", setups[1]["first"], False)
ir.vars["DriverInfo"]["DriverSetupName"] = "spa_race_v5.sto"
lg._maybe_emit_setup()
check("a different file emits again",
      len([e for e in lg.events if e["type"] == "setup"]), 3)

print("\n[setup missing from the session string -> no event, no crash]")
lg2 = logger(FakeIR(DriverInfo={"DriverCarIdx": 0}, SessionTime=1.0))
lg2._maybe_emit_setup()
check("no setup event", lg2.events, [])

print("\n[percent conversion]")
check("fraction 0.94 -> 94 %", L._pct(0.94), 94.0)
check("new tire 1.0 -> 100 %", L._pct(1.0), 100.0)
check("already a percent (82) stays", L._pct(82.0), 82.0)
check("mean of a triple", L._mean([44.0, 41.0, 38.0]), 41.0)
check("empty wear sample never wins 'most worn'",
      L._wear_mean({"t": 1.0}) > 1e8, True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
