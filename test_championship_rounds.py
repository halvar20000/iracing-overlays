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

# 12. WCT GT3 scoring: participation points and the drop-week guard.
#     PCCD awards 0 participation, so none of this touches the cases above.
def wct(completed=3, total=12, drop=3, part=5):
    return {"ok": True, "league": {"slug": "cas-gt3-wct"},
            "season": {"id": "W14", "proAmEnabled": True, "totalRounds": total,
                       "completedRounds": completed},
            "scoring": {"pointsTable": TABLE, "classPointsTable": TABLE,
                        "participationPoints": part, "dropWorstNRounds": drop},
            "standings": [
                {"rank": 1, "name": "Pro One", "firstName": "Pro", "lastName": "One",
                 "points": 100, "iracingMemberId": "1", "proAmClass": "PRO"},
                {"rank": 2, "name": "Am One", "firstName": "Am", "lastName": "One",
                 "points": 90, "iracingMemberId": "2", "proAmClass": "AM"},
                {"rank": 3, "name": "Pro Two", "firstName": "Pro", "lastName": "Two",
                 "points": 80, "iracingMemberId": "3", "proAmClass": "PRO"},
                {"rank": 4, "name": "Am Two", "firstName": "Am", "lastName": "Two",
                 "points": 70, "iracingMemberId": "4", "proAmClass": "AM"},
            ]}


def wct_race(laps=None):
    laps = laps or {1: 20, 2: 20, 3: 20, 4: 20}
    return {"connected": True, "is_race": True, "track_name": "Spa",
            "session_type": "RACE", "session_name": "RACE",
            "race_finished": False, "done_races": [], "session_key": "w",
            "rows": [{"cust_id": c, "race_pos": i + 1, "laps_done": laps[c],
                      "name": f"d{c}", "abbrev": "", "car_number": "",
                      "team_name": "", "class_name": "", "in_pit": 0,
                      "in_world": 1} for i, c in enumerate([1, 2, 3, 4])]}


cfg = dict(C.DEFAULT_CONFIG)
pw = C.build_projection(wct_race(), wct(), [], cfg)
rw = {r["name"]: r for r in pw["champ_rows"]}
# Pro One is overall P1 and PRO P1; Am One is overall P2 but AM P1.
check("12 participation read from the API", pw["scoring_info"]["participation_points"], 5)
check("12 Combined column excludes participation", rw["Pro One"]["race_pts"], TABLE["1"])
check("12 Pro/Am column includes it", rw["Pro One"]["class_race_pts"], TABLE["1"] + 5)
check("12 class position, not overall (Am One is AM P1)",
      rw["Am One"]["class_race_pts"], TABLE["1"] + 5)
check("12 projection uses the class total", rw["Am One"]["proj_points"], 90 + TABLE["1"] + 5)

# Under the race-points threshold: no position points AND no participation.
pw = C.build_projection(wct_race({1: 20, 2: 20, 3: 20, 4: 9}), wct(), [], cfg)
rw = {r["name"]: r for r in pw["champ_rows"]}
check("12 45 % distance scores nothing", rw["Am Two"]["proj_points"], 70)
# Between the two thresholds (50 % race points, 75 % participation): points
# but no participation point.
pw = C.build_projection(wct_race({1: 20, 2: 20, 3: 20, 4: 13}), wct(), [], cfg)
rw = {r["name"]: r for r in pw["champ_rows"]}
check("12 65 % distance: points but no participation",
      rw["Am Two"]["class_race_pts"], TABLE["2"])

# Drop weeks: 12 rounds minus 3 = best 9 count, so the projection is only
# approximate from round 10 on. It must say so rather than overstate.
check("12 counting rounds", pw["scoring_info"]["counting_rounds"], 9)
check("12 drops cannot bite at round 4", pw["scoring_info"]["drop_weeks_active"], False)
p10 = C.build_projection(wct_race(), wct(completed=9), [], cfg)
check("12 drops bite at round 10", p10["scoring_info"]["drop_weeks_active"], True)
p_pccd = C.build_projection(wct_race(), champ(), [], cfg)
check("12 a league without drops is never flagged",
      p_pccd["scoring_info"]["drop_weeks_active"], False)
check("12 a league without participation is unaffected",
      p_pccd["scoring_info"]["participation_points"], 0)

# 13. Auto-detect wiring: the series on the grid repoints the fetcher, and
#     manual mode pins it. Synthetic rosters — no network.
import cls_league_detect as LD

_wct_ids = set(range(500, 530))
_pccd5 = set(range(600, 627))
_pccd6 = {600}
_det = LD.LeagueDetector.__new__(LD.LeagueDetector)
_det._api_base = "http://test.invalid"
_det._lock = __import__("threading").Lock()
_det._thread = None
_det._last_ok = 0.0
_det._error = None
_det._seasons = [
    {"slug": "cas-gt3-wct", "season_id": "W14", "season_name": "GT3 WCT 14th",
     "league_name": "CAS GT3 WCT", "completed_rounds": 3, "ids": _wct_ids},
    {"slug": "cas-pccd", "season_id": "P5", "season_name": "5th season",
     "league_name": "CAS PCCD", "completed_rounds": 7, "ids": _pccd5},
    {"slug": "cas-pccd", "season_id": "P6", "season_name": "6th season",
     "league_name": "CAS PCCD", "completed_rounds": 0, "ids": _pccd6},
]
C.detector = _det
C.detected_state = {"result": None, "applied": None, "at": 0.0}


def grid(ids):
    return {"connected": True, "is_race": True,
            "rows": [{"cust_id": u, "race_pos": i + 1, "laps_done": 10}
                     for i, u in enumerate(sorted(ids))]}


C.fetcher.update_config({**C.DEFAULT_CONFIG, "league_mode": "auto",
                         "league_slug": "cas-pccd", "season_id": "P5"})
hit = C._apply_detected_league(grid(sorted(_wct_ids)[:20]))
check("13 WCT grid detected", hit and hit["league_slug"], "cas-gt3-wct")
check("13 fetcher repointed to WCT",
      C.fetcher.get()["config"]["league_slug"], "cas-gt3-wct")
check("13 and to its season", C.fetcher.get()["config"]["season_id"], "W14")

hit = C._apply_detected_league(grid(sorted(_pccd5)[:18]))
check("13 PCCD grid detected", hit and hit["league_slug"], "cas-pccd")
check("13 picks the populated season, not the empty one",
      C.fetcher.get()["config"]["season_id"], "P5")

# A field nobody recognises must leave the league alone — never blank it.
C._apply_detected_league(grid(range(70000, 70020)))
check("13 unknown field keeps the last league",
      C.fetcher.get()["config"]["league_slug"], "cas-pccd")

# Manual mode ignores the grid entirely.
C.fetcher.update_config({**C.DEFAULT_CONFIG, "league_mode": "manual",
                         "league_slug": "cas-pccd", "season_id": "P5"})
check("13 manual mode returns nothing",
      C._apply_detected_league(grid(sorted(_wct_ids)[:20])), None)
check("13 manual mode keeps the pinned league",
      C.fetcher.get()["config"]["league_slug"], "cas-pccd")

# No detector at all (import failed) must not raise.
C.detector = None
check("13 missing detector is harmless",
      C._apply_detected_league(grid(sorted(_wct_ids)[:20])), None)

print(f"{passes} passed, {len(fails)} failed")
for f in fails:
    print("  FAIL:", f)
sys.exit(1 if fails else 0)
