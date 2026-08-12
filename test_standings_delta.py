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
if fails:
    print("\nFAILURES:")
    for f in fails:
        print("  ", f)
    sys.exit(1)
print("all checks passed")
