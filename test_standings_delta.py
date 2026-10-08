"""End-to-end check of the standings tower's +/- column, offline.

Drives the real StandingsPoller._build_race_standings() with stubbed
telemetry and asserts the pos_delta each row gets.
"""
import sys
import types

_stub = types.ModuleType("irsdk")
_stub.IRSDK = lambda: None
sys.modules.setdefault("irsdk", _stub)

import iracing_standings as S  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")


class FakeIR:
    def __init__(self, d):
        self.d = d

    def __getitem__(self, k):
        return self.d.get(k)

    def set(self, **kw):
        self.d.update(kw)
        return self


N = 5


def telemetry(order, lap=1, state=4):
    """order = list of car_idx in current running order, leader first."""
    pos = [0] * N
    pct = [0.0] * N
    for rank, ci in enumerate(order):
        pos[ci] = rank + 1
        # track progress descends with rank so the live sort matches `order`
        pct[ci] = 0.9 - rank * 0.05
    return {
        "SessionUniqueID": 5, "SessionNum": 1, "SessionState": state,
        "CarIdxPosition": pos, "CarIdxLap": [lap] * N,
        "CarIdxLapDistPct": pct,
        "CarIdxF2Time": [0.0] * N, "CarIdxLastLapTime": [90.0] * N,
        "CarIdxBestLapTime": [90.0] * N, "CarIdxOnPitRoad": [False] * N,
        "CarIdxTrackSurface": [3] * N, "CarIdxEstTime": [0.0] * N,
        "SessionTime": 100.0, "DriverInfo": {"Drivers": [
            {"CarIdx": i, "UserName": f"D{i}", "CarNumber": str(i),
             "CarClassID": 1, "CarClassShortName": "GT3",
             "CarClassColor": 0xFFFFFF, "CarPath": "", "CarScreenName": ""}
            for i in range(N)
        ]},
        "SessionInfo": {"Sessions": [
            {"SessionNum": 0, "SessionType": "Lone Qualify",
             "ResultsPositions": [{"CarIdx": ci, "Position": i}
                                  for i, ci in enumerate(range(N), start=1)]},
            {"SessionNum": 1, "SessionType": "Race", "ResultsPositions": []},
        ]},
    }


p = S.StandingsPoller()
p.ir = FakeIR(telemetry([0, 1, 2, 3, 4]))


def run(order, lap=1):
    p.ir.d.update(telemetry(order, lap=lap))
    rows = p._build_race_standings(p._driver_map(), p.ir,
                                   p.ir["SessionInfo"]["Sessions"][1])
    return {r["car_idx"]: r["pos_delta"] for r in rows}


# Grid = 0,1,2,3,4. Everyone on their slot.
check("start of race all zero", run([0, 1, 2, 3, 4]),
      {0: 0, 1: 0, 2: 0, 3: 0, 4: 0})

# Turn 1: car 1 gets past the pole man.
d = run([1, 0, 2, 3, 4])
check("pole man -1 after T1", d[0], -1)
check("passer +1 after T1", d[1], 1)

# He takes it back — and they swap five more times.
seen = []
for lap in range(2, 8):
    seen.append(run([0, 1, 2, 3, 4], lap=lap)[0])
    seen.append(run([1, 0, 2, 3, 4], lap=lap)[0])
check("repeated swaps only ever -1/0", sorted(set(seen)), [-1, 0])
check("back on his slot reads 0", run([0, 1, 2, 3, 4], lap=8)[0], 0)

# A real recovery drive: car 4 (P5 on the grid) up to P2.
d = run([0, 4, 1, 2, 3], lap=9)
check("real gain of 3", d[4], 3)
check("pole man still 0", d[0], 0)
check("car 1 down to P3", d[1], -1)

print("standings pos_delta:", run([0, 4, 1, 2, 3], lap=9))


# =========================================================================
# TWO-RACE ROUND — race 2 must use RACE 2's grid, not qualifying (which
# is race 1's grid and stays in SessionInfo all weekend).
#
# Weekend: 0 Practice / 1 Qualify (0,1,2,3,4) / 2 Race 1 (same grid) /
#          3 Race 2 (reverse grid: 4,3,2,1,0).
# =========================================================================
def weekend_sessions():
    return [
        {"SessionNum": 0, "SessionType": "Practice", "ResultsPositions": []},
        {"SessionNum": 1, "SessionType": "Lone Qualify",
         "ResultsPositions": [{"CarIdx": ci, "Position": i}
                              for i, ci in enumerate(range(N), start=1)]},
        {"SessionNum": 2, "SessionType": "Race",
         "ResultsPositions": [{"CarIdx": ci, "StartingPosition": ci}
                              for ci in range(N)]},
        {"SessionNum": 3, "SessionType": "Race",
         "ResultsPositions": [{"CarIdx": ci, "StartingPosition": sp}
                              for sp, ci in enumerate(reversed(range(N)))]},
    ]


def race2_telemetry(order, lap=1):
    t = telemetry(order, lap=lap)
    t["SessionNum"] = 3
    t["SessionInfo"] = {"Sessions": weekend_sessions()}
    return t


p2 = S.StandingsPoller()          # fresh poller = fresh grid baseline
p2.ir = FakeIR(race2_telemetry([4, 3, 2, 1, 0]))


def run2(order, lap=1):
    p2.ir.d.update(race2_telemetry(order, lap=lap))
    rows = p2._build_race_standings(p2._driver_map(), p2.ir,
                                    p2.ir["SessionInfo"]["Sessions"][3])
    return {r["car_idx"]: r["pos_delta"] for r in rows}


# Lap 1 of race 2, everyone still on their (reversed) grid slot.
# Before the fix this read {0: -4, 1: -2, 2: 0, 3: 2, 4: 4}.
check("race 2 lap 1 all zero", run2([4, 3, 2, 1, 0]),
      {0: 0, 1: 0, 2: 0, 3: 0, 4: 0})
check("race 2 baseline source", p2._grid.source, "race_results")

# Car 0 (starts last in race 2) charges to the lead: +4.
d2 = run2([0, 4, 3, 2, 1], lap=6)
check("race 2 real gain from last to first", d2[0], 4)
check("race 2 reverse-grid pole man loses one", d2[4], -1)

print("race 2 pos_delta:", run2([0, 4, 3, 2, 1], lap=6))

# =========================================================================
# BEFORE THE GREEN — the timing line runs through the grid (Zandvoort
# replay, 2026-10-08). Lap counters are frozen for everyone; the front of
# the grid sits just PAST the line (pct 0.00-0.01), the back just BEFORE it
# (0.98-0.999). Sorting by lap+pct put the back of the grid in the lead and
# showed +/-10 on every car. Pre-green the tower must show the grid order.
# =========================================================================
def gridding(pcts, state, lap=-1):
    t = telemetry([0, 1, 2, 3, 4])
    t.update(SessionState=state, CarIdxLap=[lap] * N, CarIdxPosition=[0] * N,
             CarIdxLapDistPct=pcts)
    return t


SPLIT = [0.011, 0.009, 0.0065, 0.999, 0.997]   # grid 1-3 past the line, 4-5 before

p3 = S.StandingsPoller()
for state, label in ((1, "get in car"), (2, "warmup"), (3, "pace lap")):
    p3.ir = FakeIR(gridding(SPLIT, state))
    rows = p3._build_race_standings(p3._driver_map(), p3.ir,
                                    p3.ir["SessionInfo"]["Sessions"][1])
    check(f"pre-green ({label}): grid order",
          [r["car_idx"] for r in rows], [0, 1, 2, 3, 4])
    check(f"pre-green ({label}): every +/- is 0",
          {r["car_idx"]: r["pos_delta"] for r in rows},
          {0: 0, 1: 0, 2: 0, 3: 0, 4: 0})
    check(f"pre-green ({label}): no lap-downs / intervals",
          [(r["laps_behind"], r["interval"]) for r in rows], [(0, None)] * N)

# Green flag, field across the line and racing: live order takes over again.
p3.ir = FakeIR(gridding([0.05, 0.06, 0.04, 0.03, 0.02], 4, lap=1))
rows = p3._build_race_standings(p3._driver_map(), p3.ir,
                                p3.ir["SessionInfo"]["Sessions"][1])
check("after green: live order", [r["car_idx"] for r in rows], [1, 0, 2, 3, 4])
check("after green: real +/-", {r["car_idx"]: r["pos_delta"] for r in rows}[1], 1)

if fails:
    print("\nFAILURES:")
    for f in fails:
        print("  ", f)
    sys.exit(1)
print("all checks passed")
