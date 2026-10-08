"""Championship overlay: two-race rounds (PCCD format), offline.

Drives the real RacePoller._read_snapshot / RoundMemory / build_projection
with stubbed telemetry against a canned CLS payload:
  practice adds nothing · race 1 live · race 1 classification after the
  checkered · warmup shows the provisional table · race 2 in a SEPARATE
  hosted session still counts race 1 · 50 % distance rule · CLS publishing
  the round retires the memory.
"""
import sys
import tempfile
import types
from pathlib import Path

_stub = types.ModuleType("irsdk")
_stub.IRSDK = lambda: None
sys.modules.setdefault("irsdk", _stub)

import iracing_championship as C  # noqa: E402

fails, passes = [], 0


def check(name, got, want):
    global passes
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")
    else:
        passes += 1


TABLE = {"1": 41, "2": 35, "3": 30, "4": 26, "5": 23}
# cust_id, name, CLS points
CHAMP = [(1, "Remo", 326), (2, "Maurice", 324), (3, "Andre", 208), (4, "Alex", 205)]


def champ(completed=6):
    return {"ok": True, "league": {"slug": "cas-pccd"},
            "season": {"id": "S5", "proAmEnabled": False, "completedRounds": completed,
                       "totalRounds": 8},
            "scoring": {"pointsTable": TABLE},
            "standings": [{"rank": i + 1, "iracingMemberId": str(c), "name": n, "points": p}
                          for i, (c, n, p) in enumerate(CHAMP)]}


class FakeIR:
    def __init__(self, d):
        self.d = d

    def __getitem__(self, k):
        return self.d.get(k)


counter = [1]


def ir(uid, num, sessions, state, order, laps_done=None, surface=None):
    """order = cust ids in track order (leader first); car_idx = cust - 1."""
    n = len(CHAMP)
    pct = [0.0] * n
    for rank, cust in enumerate(order):
        pct[cust - 1] = 0.9 - rank * 0.1
    return FakeIR({
        "SessionUniqueID": counter[0], "SessionNum": num, "SessionState": state,
        # the hosted session's stable id; SessionUniqueID below is iRacing's
        # per-session-part COUNTER and must not be used as a key
        "WeekendInfo": {"TrackDisplayName": "Algarve", "SessionID": uid, "SubSessionID": uid * 10},
        "SessionInfo": {"Sessions": sessions},
        "DriverInfo": {"Drivers": [
            {"CarIdx": c - 1, "UserID": c, "UserName": nm, "CarNumber": str(c)}
            for c, nm, _p in CHAMP]},
        "CarIdxPosition": [0] * n, "CarIdxLap": [5] * n,
        "CarIdxLapCompleted": laps_done or [4] * n,
        "CarIdxLapDistPct": pct, "CarIdxOnPitRoad": [False] * n,
        "CarIdxF2Time": [0.0] * n, "CarIdxTrackSurface": surface or [3] * n,
    })


def results(order, laps=None):
    return [{"CarIdx": c - 1, "Position": i + 1,
             "LapsComplete": (laps or {}).get(c, 20), "ReasonOutId": 0}
            for i, c in enumerate(order)]


tmp = Path(tempfile.mkdtemp()) / "round_cache.json"
mem = C.RoundMemory(tmp)
CFG = {"race_points_min_distance_pct": 50}


def project(poller, fake, ch):
    poller.ir = fake
    snap = poller._read_snapshot()
    snap["connected"] = True
    mem.update(snap, ch)
    earlier = mem.earlier_races(ch, snap["session_key"])
    return C.build_projection(snap, ch, earlier, CFG)


def pts(proj):
    return {r["name"]: r["proj_points"] for r in proj["champ_rows"]}


p = C.RacePoller()
PRACTICE = {"SessionNum": 0, "SessionType": "Practice", "SessionName": "PRACTICE"}
RACE1 = {"SessionNum": 2, "SessionType": "Race", "SessionName": "RACE 1"}

# 1. Practice: no live points even though cars are on track.
proj = project(p, ir(100, 0, [PRACTICE, RACE1], 4, [4, 3, 2, 1]), champ())
check("1 practice adds nothing", pts(proj), {"Remo": 326, "Maurice": 324, "Andre": 208, "Alex": 205})

# 2. Race 1 live: Alex leads.
proj = project(p, ir(100, 2, [PRACTICE, RACE1], 4, [4, 3, 2, 1]), champ())
check("2 race 1 live", pts(proj), {"Alex": 246, "Andre": 243, "Maurice": 354, "Remo": 352})

# 3. Checkered: order comes from ResultsPositions, not from the cars
#    trundling back to the pits (track order is now reversed).
r1 = dict(RACE1, ResultsPositions=results([1, 2, 3, 4]))
proj = project(p, ir(100, 2, [PRACTICE, r1], 5, [4, 3, 2, 1],
                     surface=[-1, 3, 3, 3]), champ())
check("3 classification after the checkered",
      pts(proj), {"Remo": 367, "Maurice": 359, "Andre": 238, "Alex": 231})
check("3 marked finished", proj["race_finished"], True)

# 4. Warmup in the same hosted session: provisional = CLS + race 1.
WARMUP = {"SessionNum": 3, "SessionType": "Practice", "SessionName": "WARMUP"}
proj = project(p, ir(100, 3, [PRACTICE, r1, WARMUP], 4, [2, 1, 3, 4]), champ())
check("4 warmup shows provisional after race 1",
      pts(proj), {"Remo": 367, "Maurice": 359, "Andre": 238, "Alex": 231})
check("4 race 1 listed as earlier race", proj["earlier_races"], ["RACE 1"])

# 5. Race 2 as a SEPARATE hosted session (race 1 no longer in SessionInfo),
#    with a fresh overlay process -> comes back from the cache file.
mem = C.RoundMemory(tmp)
p = C.RacePoller()
RACE2 = {"SessionNum": 0, "SessionType": "Race", "SessionName": "RACE 2"}
proj = project(p, ir(200, 0, [RACE2], 4, [4, 3, 2, 1]), champ())
check("5 race 2 live = CLS + race 1 + race 2",
      pts(proj), {"Remo": 367 + 26, "Maurice": 359 + 30, "Andre": 238 + 35, "Alex": 231 + 41})
row = {r["name"]: r for r in proj["champ_rows"]}["Remo"]
check("5 per-race breakdown", (row["earlier"], row["race_pos"], row["race_pts"]),
      ([{"pos": 1, "pts": 41}], 4, 26))

# 6. 50 % rule: Alex retired after 9 of 20 laps -> no points.
r2 = dict(RACE2, ResultsPositions=results([1, 2, 3, 4], laps={4: 9}))
proj = project(p, ir(200, 0, [r2], 5, [1, 2, 3, 4]), champ())
check("6 below 50 % distance scores nothing", pts(proj)["Alex"], 231)
check("6 exactly 50 % still scores", C._distance_ok(10, 20, 50), True)

# 7. CLS publishes round 7 -> memory no longer added (no double count).
proj = project(p, ir(300, 0, [PRACTICE], 4, [1, 2, 3, 4]), champ(completed=7))
check("7 round in CLS -> memory retired", proj["earlier_races"], [])

# 8. Race-2 table override is used for the second race.
CFG["points_table_race2"] = {"1": 50, "2": 40, "3": 30, "4": 20}
mem2 = C.RoundMemory(Path(tempfile.mkdtemp()) / "c.json")
mem, keep = mem2, mem
p = C.RacePoller()
project(p, ir(100, 2, [r1], 5, [1, 2, 3, 4]), champ())
proj = project(p, ir(100, 4, [r1, dict(RACE2, SessionNum=4)], 4, [1, 2, 3, 4]), champ())
check("8 race 2 table override", pts(proj)["Remo"], 326 + 41 + 50)

# 9. Pace lap: timing line splits the grid (front past it, back before it)
#    -> projection follows the GRID, not lap+pct.
QUALI = {"SessionNum": 1, "SessionType": "Lone Qualify", "ResultsPositions": [
    {"CarIdx": c - 1, "Position": i + 1} for i, c in enumerate([1, 2, 3, 4])]}
mem = C.RoundMemory(Path(tempfile.mkdtemp()) / "g.json")
CFG["points_table_race2"] = None
p = C.RacePoller()
fake = ir(400, 2, [QUALI, RACE1], 3, [1, 2, 3, 4])
fake.d["CarIdxLapDistPct"] = [0.011, 0.009, 0.999, 0.997]   # 3 & 4 behind the line
fake.d["CarIdxLap"] = [-1] * 4
proj = project(p, fake, champ())
check("9 pace lap follows the grid", [r["name"] for r in proj["race_rows"]],
      ["Remo", "Maurice", "Andre", "Alex"])

# 10. Algarve 2026-10-08 regression: iRacing's SessionUniqueID counter went
#     up during the event (warmup, race 2). Race 1 must still be ONE entry
#     and count ONCE during race 2.
mem = C.RoundMemory(Path(tempfile.mkdtemp()) / "a.json")
p = C.RacePoller()
r1 = dict(RACE1, ResultsPositions=results([1, 2, 3, 4]))
counter[0] = 3
project(p, ir(500, 2, [r1], 5, [1, 2, 3, 4]), champ())                   # race 1 finished
counter[0] = 4
project(p, ir(500, 3, [r1, WARMUP], 4, [1, 2, 3, 4]), champ())           # warmup
counter[0] = 5
RACE2b = {"SessionNum": 4, "SessionType": "Race", "SessionName": "RACE 2"}
proj = project(p, ir(500, 4, [r1, WARMUP, RACE2b], 4, [2, 1, 3, 4]), champ())
check("10 race 1 stored once despite the counter changing", len(mem._entries), 1)
check("10 race 1 counted once in race 2", proj["earlier_races"], ["RACE 1"])
check("10 race 2 totals", pts(proj)["Remo"], 326 + 41 + 35)

# 11. Old caches that DO hold the same race several times (one copy saved
#     while the last car was still on his final lap) count it once.
mem = C.RoundMemory(Path(tempfile.mkdtemp()) / "b.json")
season = champ()["season"]
base = {"season_id": season["id"], "completed_rounds": season["completedRounds"],
        "track": "Algarve", "name": "HEAT 1"}
res15 = [{"cust_id": c, "pos": i + 1, "laps": 15, "out": 0} for i, c in enumerate([1, 2, 3, 4])]
res14 = [dict(r, laps=14 if r["cust_id"] == 4 else 15) for r in res15]
now = __import__("time").time()
mem._entries = {"3:2": {**base, "saved_at": now - 900, "results": res14},
                "4:2": {**base, "saved_at": now - 600, "results": res15},
                "5:2": {**base, "saved_at": now - 300, "results": res15}}
e = mem.earlier_races(champ(), "5:4")
check("11 legacy duplicates count once", len(e), 1)
check("11 most complete copy kept", sum(r["laps"] for r in e[0]["results"]), 60)

print(f"{passes} passed, {len(fails)} failed")
for f in fails:
    print("  FAIL:", f)
sys.exit(1 if fails else 0)
