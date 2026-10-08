"""Offline tests for the +/- (positions gained/lost) fix.

Runs without iRacing: a stub `irsdk` module is injected before importing
iracing_sdk_base, and a FakeIR object plays back scripted telemetry.

The property under test, in Thomas's words:
    "If the driver on pole lost one position in the first corner and
     regains his place later, he is at 0. If this happens again several
     times he still is at 0. It is not cumulating."
"""
import sys
import types

# --- stub out pyirsdk so iracing_sdk_base imports cleanly -----------------
_stub = types.ModuleType("irsdk")
_stub.IRSDK = lambda: None
sys.modules.setdefault("irsdk", _stub)

from iracing_sdk_base import GridBaseline  # noqa: E402

PASS, FAIL = [], []


def check(name, got, want):
    if got == want:
        PASS.append(name)
    else:
        FAIL.append(f"{name}: got {got!r}, want {want!r}")


class FakeIR:
    """Minimal stand-in for irsdk.IRSDK's __getitem__ telemetry access."""

    def __init__(self, uid=1, sess_num=0, state=4, positions=None,
                 laps=None, sessions=None):
        self.d = {
            "SessionUniqueID": uid,
            "SessionNum": sess_num,
            "SessionState": state,
            "CarIdxPosition": positions or [],
            "CarIdxLap": laps or [],
            "SessionInfo": {"Sessions": sessions or []},
        }

    def __getitem__(self, k):
        return self.d.get(k)

    def set(self, **kw):
        self.d.update(kw)
        return self


def quali_session(order):
    """order = list of car_idx, pole first. ResultsPositions is 1-based."""
    return {
        "SessionNum": 0,
        "SessionType": "Lone Qualify",
        "ResultsPositions": [
            {"CarIdx": ci, "Position": i} for i, ci in enumerate(order, start=1)
        ],
    }


def race_session(sess_num=1):
    return {"SessionNum": sess_num, "SessionType": "Race", "ResultsPositions": []}


# =========================================================================
# 1. THE CORE CASE — pole man loses a place in turn 1 and takes it back,
#    over and over. Must read -1 / 0 / -1 / 0 ... and never accumulate.
# =========================================================================
grid = GridBaseline()
sessions = [quali_session([0, 1, 2, 3, 4]), race_session()]
ir = FakeIR(uid=7, sess_num=1, state=3, sessions=sessions, laps=[0] * 5,
            positions=[1, 2, 3, 4, 5])
grid.update(ir)
check("1a baseline source", grid.source, "qualifying")
check("1b pole man grid slot", grid.grid_pos.get(0), 1)

# Green. Everyone still on their grid slot.
ir.set(SessionState=4)
grid.update(ir)
check("1c pole at start", grid.delta(0, 1), 0)

# Turn 1: car 0 shuffled to P2, car 1 leads.
seq = []
for lap in range(1, 8):
    ir.set(CarIdxLap=[lap] * 5, CarIdxPosition=[2, 1, 3, 4, 5])
    grid.update(ir)
    seq.append(grid.delta(0, 2))          # car 0 currently P2
    ir.set(CarIdxPosition=[1, 2, 3, 4, 5])
    grid.update(ir)
    seq.append(grid.delta(0, 1))          # car 0 back to P1

check("1d lose/regain x7 alternates -1/0 only", set(seq), {-1, 0})
check("1e final value after 7 swaps is 0", seq[-1], 0)
check("1f never accumulates (min)", min(seq), -1)
check("1g the man he swapped with is also 0", grid.delta(1, 2), 0)

# A genuine gain still shows: car 4 (started P5) is now P2.
check("1h real gain of 3", grid.delta(4, 2), 3)
check("1i real loss of 3", grid.delta(1, 5), -3)

# =========================================================================
# 2. No qualifying results, but we were watching before the green:
#    a green-flag sample is allowed.
# =========================================================================
grid2 = GridBaseline()
ir2 = FakeIR(uid=9, sess_num=0, state=3, sessions=[race_session(0)],
             laps=[0] * 4, positions=[1, 2, 3, 4])
grid2.update(ir2)                                   # pre-green: nothing yet
check("2a nothing captured before green", grid2.captured, False)
ir2.set(SessionState=4)
grid2.update(ir2)
check("2b captured at green", grid2.source, "green_flag")
check("2c P1 at green", grid2.grid_pos.get(0), 1)
ir2.set(CarIdxLap=[5, 5, 5, 5], CarIdxPosition=[3, 1, 2, 4])
grid2.update(ir2)
check("2d delta after green sample", grid2.delta(0, 3), -2)

# =========================================================================
# 3. Attached MID-RACE with no qualifying results — must refuse to invent
#    a baseline. Blank beats a wrong number.
# =========================================================================
grid3 = GridBaseline()
ir3 = FakeIR(uid=11, sess_num=0, state=4, sessions=[race_session(0)],
             laps=[14, 14, 13, 13], positions=[1, 2, 3, 4])
for _ in range(5):
    grid3.update(ir3)
check("3a no baseline invented mid-race", grid3.captured, False)
check("3b delta is None -> blank cell", grid3.delta(0, 1), None)

# 3c: same mid-race attach, but qualifying results ARE available — then we
# can still be exactly right, which is the whole point of preferring them.
grid3b = GridBaseline()
ir3b = FakeIR(uid=12, sess_num=1, state=4,
              sessions=[quali_session([3, 2, 1, 0]), race_session()],
              laps=[14, 14, 13, 13], positions=[1, 2, 3, 4])
grid3b.update(ir3b)
check("3c mid-race attach still exact via quali", grid3b.source, "qualifying")
check("3d car 0 started last, now P1 -> +3", grid3b.delta(0, 1), 3)

# =========================================================================
# 4. Session change resets the baseline.
# =========================================================================
ir.set(SessionNum=2, SessionInfo={"Sessions": [quali_session([4, 3, 2, 1, 0]),
                                               race_session(2)]},
       SessionState=3, CarIdxLap=[0] * 5)
grid.update(ir)
check("4a re-captured for the new session", grid.grid_pos.get(4), 1)
check("4b old pole man is now P5 on the grid", grid.grid_pos.get(0), 5)

# =========================================================================
# 5. Multi-class: deltas are per class.
# =========================================================================
grid5 = GridBaseline()
ir5 = FakeIR(uid=21, sess_num=1, state=3,
             sessions=[quali_session([0, 1, 2, 3]), race_session()],
             laps=[0] * 4, positions=[1, 2, 3, 4])
# cars 0,2 = GT3 (class 10); cars 1,3 = LMP2 (class 20)
grid5.update(ir5, class_of={0: 10, 1: 20, 2: 10, 3: 20})
check("5a GT3 pole", grid5.class_grid_pos.get(0), 1)
check("5b GT3 second", grid5.class_grid_pos.get(2), 2)
check("5c LMP2 pole", grid5.class_grid_pos.get(1), 1)
check("5d GT3 swap = -1 in class", grid5.class_delta(0, 2), -1)
check("5e GT3 swap back = 0 in class", grid5.class_delta(0, 1), 0)

# =========================================================================
# 6. Late joiner has no grid slot -> None -> blank.
# =========================================================================
check("6a late joiner blank", grid5.class_delta(99, 3), None)
check("6b unclassified current pos blank", grid5.class_delta(0, 0), None)

# =========================================================================
# 7. Source is base-agnostic: a 0-based StartingPosition block ranks the
#    same as a 1-based qualifying block.
# =========================================================================
grid7 = GridBaseline()
race = {"SessionNum": 0, "SessionType": "Race", "ResultsPositions": [
    {"CarIdx": 5, "StartingPosition": 0},
    {"CarIdx": 6, "StartingPosition": 1},
    {"CarIdx": 7, "StartingPosition": 2},
]}
ir7 = FakeIR(uid=31, sess_num=0, state=4, sessions=[race], laps=[0] * 8,
             positions=[0, 0, 0, 0, 0, 1, 2, 3])
grid7.update(ir7)
check("7a 0-based source re-ranked to 1", grid7.grid_pos.get(5), 1)
check("7b source label", grid7.source, "race_results")
check("7c delta from 0-based source", grid7.delta(7, 1), 2)

# =========================================================================
# 8. TWO-RACE ROUND — the regression that started this.
#
#    SessionInfo carries the whole weekend, so the qualifying block is
#    still present during race 2. Qualifying only describes race 1's
#    grid; race 2 is gridded from race 1's result (here: reversed). The
#    baseline must come from race 2's OWN StartingPosition, not from
#    qualifying — otherwise every car shows a bogus ±N from lap 1.
# =========================================================================
def race_with_grid(sess_num, order):
    """order = list of car_idx, pole first. StartingPosition is 0-based."""
    return {
        "SessionNum": sess_num,
        "SessionType": "Race",
        "ResultsPositions": [
            {"CarIdx": ci, "StartingPosition": i}
            for i, ci in enumerate(order, start=0)
        ],
    }


PRACTICE = {"SessionNum": 0, "SessionType": "Practice", "ResultsPositions": []}
QUALI    = dict(quali_session([0, 1, 2, 3, 4]), SessionNum=1)
RACE1    = race_with_grid(2, [0, 1, 2, 3, 4])          # grid = quali order
RACE2    = race_with_grid(3, [4, 3, 2, 1, 0])          # reverse grid
WEEKEND  = [PRACTICE, QUALI, RACE1, RACE2]

# -- race 1 -------------------------------------------------------------
grid8 = GridBaseline()
ir8 = FakeIR(uid=41, sess_num=2, state=3, sessions=WEEKEND,
             laps=[0] * 5, positions=[1, 2, 3, 4, 5])
grid8.update(ir8)
check("8a race 1 pole is car 0", grid8.grid_pos.get(0), 1)
check("8b race 1 last is car 4", grid8.grid_pos.get(4), 5)

# -- race 2, same weekend, reversed grid --------------------------------
grid8b = GridBaseline()
ir8b = FakeIR(uid=41, sess_num=3, state=3, sessions=WEEKEND,
              laps=[0] * 5, positions=[5, 4, 3, 2, 1])
grid8b.update(ir8b)
check("8c race 2 uses its own grid", grid8b.source, "race_results")
check("8d race 2 pole is car 4", grid8b.grid_pos.get(4), 1)
check("8e race 2 last is car 0", grid8b.grid_pos.get(0), 5)
# Car 0 starts race 2 from P5 and is still P5 -> 0, NOT -4.
check("8f no phantom loss for the race-1 polesitter", grid8b.delta(0, 5), 0)
# Car 4 starts race 2 from pole and leads -> 0, NOT +4.
check("8g no phantom gain for the reverse-grid polesitter", grid8b.delta(4, 1), 0)
# A real move in race 2 still reads correctly: car 0 climbs P5 -> P1.
check("8h real race-2 gain", grid8b.delta(0, 1), 4)

# -- the same session sequence walked through by ONE baseline object,
#    which is what actually happens with a poller left running.
grid8c = GridBaseline()
ir8c = FakeIR(uid=41, sess_num=2, state=3, sessions=WEEKEND,
              laps=[0] * 5, positions=[1, 2, 3, 4, 5])
grid8c.update(ir8c)
check("8i live poller: race 1 pole", grid8c.grid_pos.get(0), 1)
ir8c.set(SessionNum=3, SessionState=3, CarIdxLap=[0] * 5,
         CarIdxPosition=[5, 4, 3, 2, 1])
grid8c.update(ir8c)
check("8j live poller: re-captured for race 2", grid8c.grid_pos.get(4), 1)
check("8k live poller: race-1 pole man now starts last", grid8c.grid_pos.get(0), 5)

# -- race 2 before the sim publishes its grid: blank, never qualifying --
grid8d = GridBaseline()
weekend_no_r2_grid = [PRACTICE, QUALI, RACE1,
                      {"SessionNum": 3, "SessionType": "Race",
                       "ResultsPositions": []}]
ir8d = FakeIR(uid=42, sess_num=3, state=4, sessions=weekend_no_r2_grid,
              laps=[9, 9, 8, 8, 8], positions=[1, 2, 3, 4, 5])
for _ in range(3):
    grid8d.update(ir8d)
check("8l race 2 without a published grid stays blank", grid8d.captured, False)
check("8m ... and the cell is empty, not wrong", grid8d.delta(0, 1), None)

# -- single-race weekend still falls back to qualifying -----------------
grid8e = GridBaseline()
ir8e = FakeIR(uid=43, sess_num=2, state=3,
              sessions=[PRACTICE, QUALI,
                        {"SessionNum": 2, "SessionType": "Race",
                         "ResultsPositions": []}],
              laps=[0] * 5, positions=[1, 2, 3, 4, 5])
grid8e.update(ir8e)
check("8n single race still uses qualifying", grid8e.source, "qualifying")
check("8o ... with the right pole man", grid8e.grid_pos.get(0), 1)

# =========================================================================
# 9. The GRID overlay (iracing_grid.py) had the same weekend-wide
#    qualifying assumption. Guarded — skipped if flask isn't installed.
# =========================================================================
try:
    from iracing_grid import GridPoller  # noqa: E402
except Exception as _e:                                    # pragma: no cover
    PASS.append(f"9 skipped (iracing_grid import failed: {_e})")
else:
    def _drivers(n):
        return {i: {"car_idx": i, "name": f"D{i}", "car_number": str(i)}
                for i in range(n)}

    gp = GridPoller()

    # During race 1 the board shows race 1's grid...
    gp.ir = FakeIR(uid=51, sess_num=2, state=4, sessions=WEEKEND)
    r1 = gp._rows_from_race_grid(gp._find_race_session(WEEKEND), _drivers(5))
    check("9a grid overlay: race 1 pole", r1[0]["car_idx"], 0)

    # ...and during race 2 it shows race 2's, not qualifying's.
    gp.ir = FakeIR(uid=51, sess_num=3, state=4, sessions=WEEKEND)
    target = gp._find_race_session(WEEKEND)
    check("9b grid overlay: targets race 2", target.get("SessionNum"), 3)
    check("9c grid overlay: quali is off limits for race 2",
          gp._is_first_race(WEEKEND, target), False)
    r2 = gp._rows_from_race_grid(target, _drivers(5))
    check("9d grid overlay: race 2 pole is car 4", r2[0]["car_idx"], 4)
    check("9e grid overlay: race 2 last is car 0", r2[-1]["car_idx"], 0)

    # Sitting in qualifying, the board looks ahead to race 1.
    gp.ir = FakeIR(uid=51, sess_num=1, state=4, sessions=WEEKEND)
    check("9f grid overlay: quali looks ahead to race 1",
          gp._find_race_session(WEEKEND).get("SessionNum"), 2)

# -------------------------------------------------------------------------
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
