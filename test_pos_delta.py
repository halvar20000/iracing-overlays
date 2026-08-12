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

# -------------------------------------------------------------------------
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
