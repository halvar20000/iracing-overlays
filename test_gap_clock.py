"""Tower intervals from passing times (GapClock) + OUT cars' lap-down count.

Björn's WCT The Bend screenshot (2026-10-27): blank intervals mid-tower,
+58 s / +50 s beside them (F2Time order ≠ live progress order after the
stops), and "+17 LAPS" for every OUT car (lap / pct -1). Drives the real
StandingsPoller._build_race_standings() with stubbed telemetry.
"""
import sys
import types

_stub = types.ModuleType("irsdk")
_stub.IRSDK = lambda: None
sys.modules.setdefault("irsdk", _stub)

import iracing_standings as S  # noqa: E402

fails = []
ok = 0


def check(name, cond, info=""):
    global ok
    if cond:
        ok += 1
    else:
        fails.append(f"{name} {info}")


class FakeIR:
    def __init__(self, d):
        self.d = d

    def __getitem__(self, k):
        return self.d.get(k)


N = 4
LAP = 180.0                        # s per lap, constant speed
# car: start offset in seconds behind the leader (0 = leader)
OFFS = {0: 0.0, 1: 7.7, 2: 10.3, 3: 30.0}


def tele(t, f2=None, out=()):
    laps, pct, surf = [], [], []
    for ci in range(N):
        prog = (t - OFFS[ci]) / LAP
        if ci in out:
            laps.append(-1); pct.append(-1.0); surf.append(-1)
        else:
            laps.append(int(prog)); pct.append(prog - int(prog)); surf.append(3)
    return {
        "SessionUniqueID": 7, "SessionNum": 2, "SessionState": 4,
        "SessionTime": t, "EstLapTime": LAP,
        "CarIdxPosition": [ci + 1 for ci in range(N)],
        "CarIdxLap": laps, "CarIdxLapDistPct": pct,
        "CarIdxF2Time": f2 or [0.0] * N,
        "CarIdxLastLapTime": [LAP] * N, "CarIdxBestLapTime": [LAP] * N,
        "CarIdxOnPitRoad": [False] * N, "CarIdxTrackSurface": surf,
        "DriverInfo": {"Drivers": [
            {"CarIdx": i, "UserName": f"D{i}", "CarNumber": str(i),
             "CarClassID": 1, "CarClassShortName": "GT3",
             "CarPath": "", "CarScreenName": ""} for i in range(N)]},
        "SessionInfo": {"Sessions": [
            {"SessionNum": 2, "SessionType": "Race", "ResultsPositions": []}]},
    }


p = S.StandingsPoller()


def run(t, **kw):
    p.ir = FakeIR(tele(t, **kw))
    rows = p._build_race_standings(p._driver_map(), p.ir,
                                   p.ir["SessionInfo"]["Sessions"][0])
    return {r["car_idx"]: r for r in rows}


# F2Time deliberately WRONG (stale order: car 2 "ahead" of car 1, as after
# a stop) — the old code made car 2 blank and car 3 huge.
bad_f2 = [0.0, 20.0, 9.0, 40.0]
for t in range(100, 400):          # 1 Hz polls, five minutes of history
    rows = run(float(t) + 0.37, f2=bad_f2)

for ci, want in ((1, 7.7), (2, 2.6), (3, 19.7)):
    got = rows[ci]["interval"]
    check(f"interval car {ci}", got is not None and abs(got - want) < 0.05,
          f"got {got} want {want}")
check("leader has no interval", rows[0]["interval"] is None)

# Fresh poller, no history yet: F2Time negative diff -> estimate, not blank.
p = S.StandingsPoller()
rows = run(1000.0, f2=bad_f2)
check("no history: car 2 not blank", (rows[2]["interval"] or 0) > 0,
      str(rows[2]["interval"]))

# OUT after lap 2: remembered progress, laps down vs the leader is real.
p = S.StandingsPoller()
for t in range(100, 500):
    run(float(t))
leader_lap = int((499 - 0) / LAP)
rows = None
for t in range(500, 1400):
    rows = run(float(t), out=(3,))
r3 = rows[3]
lead_prog = (1399 - 0) / LAP
car3_prog = (499 - 30) / LAP
check("OUT car laps down from where he stopped",
      r3["laps_behind"] == int(lead_prog - car3_prog),
      f"got {r3['laps_behind']} want {int(lead_prog - car3_prog)}")
check("OUT car has no interval", r3["interval"] is None)
check("OUT car at the bottom", max(r["position"] for r in rows.values()) == r3["position"])

# OUT car never seen running: no "+N LAPS" at all.
p = S.StandingsPoller()
for t in range(100, 300):
    rows = run(float(t), out=(3,))
check("never seen OUT car: no laps down", rows[3]["laps_behind"] == 0,
      str(rows[3]["laps_behind"]))

# Replay jump back resets the clock (no gap from the future).
p.ir = None
p = S.StandingsPoller()
for t in range(1000, 1300):
    run(float(t))
rows = run(200.0)
check("after jump back: interval still sane",
      rows[1]["interval"] is not None and rows[1]["interval"] < 60,
      str(rows[1]["interval"]))

print(f"{ok} passed, {len(fails)} failed")
for f in fails:
    print("FAIL", f)
sys.exit(1 if fails else 0)
