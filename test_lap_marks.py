"""Yellow position in the tower: LapMarks (off track = this lap spoiled) and
its way through the poller's snapshot. Run: python test_lap_marks.py"""
import sys
import types

_stub = types.ModuleType("irsdk")
_stub.IRSDK = lambda: None
sys.modules.setdefault("irsdk", _stub)

import iracing_standings as S  # noqa: E402

fails, ok = [], 0


def check(name, cond, info=""):
    global ok
    if cond:
        ok += 1
    else:
        fails.append(f"{name} {info}")


KEY = (7, 1)
ON, OFF, STALL, GONE = 3, 0, 1, -1

# one reading is a flicker, two in a row are an off-track
m = S.LapMarks()
m.update(KEY, 10.0, [3], [OFF])
check("single reading not marked", not m.marked(0, 10.0))
m.update(KEY, 10.2, [3], [ON])
m.update(KEY, 10.4, [3], [OFF])
check("interrupted readings not marked", not m.marked(0, 10.4))
m.update(KEY, 10.6, [3], [OFF])
check("two readings = marked", m.marked(0, 10.6))

# stays on for the rest of the lap, long after the car is back on track
for i in range(300):
    m.update(KEY, 11.0 + i * 0.2, [3], [ON])
check("still marked later in the lap", m.marked(0, 70.0))

# new lap: held a moment for the finished invalid lap, then clean
m.update(KEY, 71.0, [4], [ON])
check("held just after the line", m.marked(0, 71.0))
m.update(KEY, 71.0 + S.LapMarks.HOLD_S + 0.1, [4], [ON])
check("clean after the hold", not m.marked(0, 71.0 + S.LapMarks.HOLD_S + 0.1))

# other cars untouched
m = S.LapMarks()
m.update(KEY, 1.0, [1, 1], [OFF, ON])
m.update(KEY, 1.2, [1, 1], [OFF, ON])
check("only the car that went off", m.marked(0, 1.2) and not m.marked(1, 1.2))
check("left driver (car_idx -1) never marked", not m.marked(-1, 1.2))

# going to the stall or out of the world ends it
for surf, label in ((STALL, "stall"), (GONE, "out of world")):
    m = S.LapMarks()
    m.update(KEY, 1.0, [1], [OFF]); m.update(KEY, 1.2, [1], [OFF])
    m.update(KEY, 1.4, [1], [surf])
    check(f"{label} clears", not m.marked(0, 1.4))

# not counted while not racing (grid / pace lap / race cool-down)
m = S.LapMarks()
m.update(KEY, 1.0, [1], [OFF], count=False); m.update(KEY, 1.2, [1], [OFF], count=False)
check("count=False marks nothing", not m.marked(0, 1.2))

# new session and replay jump back forget everything
m = S.LapMarks()
m.update(KEY, 100.0, [1], [OFF]); m.update(KEY, 100.2, [1], [OFF])
m.update((7, 2), 100.4, [1], [ON])
check("session change clears", not m.marked(0, 100.4))
m = S.LapMarks()
m.update(KEY, 100.0, [1], [OFF]); m.update(KEY, 100.2, [1], [OFF])
m.update(KEY, 50.0, [1], [ON])
check("replay jump back clears", not m.marked(0, 50.0))


# --- through the real poller -------------------------------------------------
class FakeIR:
    def __init__(self, d):
        self.d = d

    def __getitem__(self, k):
        return self.d.get(k)


def tele(t, surf, stype="Lone Qualify", state=4):
    n = len(surf)
    return {
        "SessionUniqueID": 7, "SessionNum": 1, "SessionState": state, "SessionTime": t,
        "CarIdxLap": [2] * n, "CarIdxLapDistPct": [0.5] * n, "CarIdxPosition": [1, 2],
        "CarIdxF2Time": [0.0] * n, "CarIdxLastLapTime": [90.0] * n,
        "CarIdxBestLapTime": [90.0, 91.0], "CarIdxOnPitRoad": [False] * n,
        "CarIdxTrackSurface": surf, "CamCarIdx": 0,
        "DriverInfo": {"Drivers": [
            {"CarIdx": i, "UserName": f"D{i}", "CarNumber": str(i), "CarClassID": 1,
             "CarClassShortName": "GT3", "CarPath": "", "CarScreenName": ""} for i in range(n)]},
        "SessionInfo": {"Sessions": [{"SessionNum": 1, "SessionType": stype, "ResultsPositions": []}]},
        "WeekendInfo": {},
    }


p = S.StandingsPoller()
p.proam = None
p.ir = FakeIR(tele(5.0, [ON, ON]))
first = p._read_snapshot()
check("no marks to begin with", not any(r["lap_invalid"] for r in first["standings"]))

# the 5 Hz samples between two full snapshots still catch a short excursion
builds = []
orig = p._read_full
p._read_full = lambda: builds.append(1) or orig()
for t, surf in ((5.2, OFF), (5.4, OFF), (5.6, ON)):
    p.ir = FakeIR(tele(t, [surf, ON]))
    check("sample between snapshots keeps the old one", p._read_snapshot() is first)
check("no rebuild between snapshots", not builds)
p._snap_at -= S.StandingsPoller.SNAPSHOT_EVERY_S
p.ir = FakeIR(tele(5.8, [ON, ON]))
rows = {r["car_idx"]: r for r in p._read_snapshot()["standings"]}
check("rebuilt once a second", len(builds) == 1)
check("short excursion reaches the row", rows[0]["lap_invalid"] is True)
check("other car stays clean", rows[1]["lap_invalid"] is False)

# the race's cool-down after the flag does not count, a quali's last lap does
for stype, expect in (("Race", False), ("Lone Qualify", True)):
    p = S.StandingsPoller()
    p.ir = FakeIR(tele(5.0, [ON, ON], stype, 5))
    p._read_snapshot()
    for t in (5.2, 5.4):
        p.ir = FakeIR(tele(t, [OFF, ON], stype, 5))
        p._sample_lap_marks(p.ir)
    check(f"after the flag, {stype}: marked = {expect}", p._lap_marks.marked(0, 5.4) is expect)

# warning flag: only the car with iRacing's furled black bit, no other flag
p = S.StandingsPoller()
d = tele(5.0, [ON, ON])
d["CarIdxSessionFlags"] = [S.FLAG_FURLED | 0x20, 0x10000]     # warning + blue / black
p.ir = FakeIR(d)
rows = {r["car_idx"]: r for r in p._read_snapshot()["standings"]}
check("warning flag on the warned car", rows[0]["warn_flag"] is True)
check("black flag alone is no warning flag", rows[1]["warn_flag"] is False)
p = S.StandingsPoller()
p.ir = FakeIR(tele(5.0, [ON, ON]))
check("no flag data = no warning flag",
      not any(r["warn_flag"] for r in p._read_snapshot()["standings"]))

print(f"{ok} passed, {len(fails)} failed")
for f in fails:
    print("FAIL", f)
sys.exit(1 if fails else 0)
