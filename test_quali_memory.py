"""Qualifying: a driver who quits keeps his time until the session ends.

Drives the real StandingsPoller._build_timed_standings() + the session
memory with stubbed telemetry. Regression for 2026-10-08: when the pole
sitter left qualifying, his time vanished and P2 read as pole.
"""
import sys
import types

_stub = types.ModuleType("irsdk")
_stub.IRSDK = lambda: None
sys.modules.setdefault("irsdk", _stub)

import iracing_standings as S  # noqa: E402

fails, passes = [], 0


def check(name, got, want):
    global passes
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")
    else:
        passes += 1


class FakeIR:
    def __init__(self, d):
        self.d = d

    def __getitem__(self, k):
        return self.d.get(k)


# name, user id, official best
FIELD = [("Pole Man", 1001, 91.310), ("Second", 1002, 91.338),
         ("Third", 1003, 91.544), ("NoTime", 1004, 0.0)]


def make(field, uid=7, num=1, tel=None):
    n = len(field)
    tel = tel or [0.0] * n
    return FakeIR({
        "SessionUniqueID": uid, "SessionNum": num,
        "CarIdxBestLapTime": tel, "CarIdxLastLapTime": [0.0] * n,
        "CarIdxOnPitRoad": [False] * n, "CarIdxTrackSurface": [3] * n,
        "DriverInfo": {"Drivers": [
            {"CarIdx": i, "UserName": nm, "UserID": uid_, "CarNumber": str(i),
             "CarClassID": 1, "CarClassShortName": "GT3",
             "CarPath": "", "CarScreenName": ""}
            for i, (nm, uid_, _b) in enumerate(field)]},
    }), {"SessionNum": num, "SessionType": "Lone Qualify", "ResultsPositions": [
        {"CarIdx": i, "FastestTime": b if b > 0 else -1}
        for i, (_n, _u, b) in enumerate(field)]}


def standings(p, ir, sess, quali=True):
    p.ir = ir
    drivers = p._driver_map()
    mem = p._quali_memory_for(ir) if quali else None
    return p._build_timed_standings(drivers, ir, sess, mem)


def names(rows):
    return [(r["name"], round(r["best_lap"], 3), r.get("left", False)) for r in rows]


p = S.StandingsPoller()

# 1. Everyone present.
rows = standings(p, *make(FIELD))
check("1 normal order", [r["name"] for r in rows],
      ["Pole Man", "Second", "Third", "NoTime"])

# 2. Pole man quits: gone from DriverInfo AND ResultsPositions.
rows = standings(p, *make(FIELD[1:]))
check("2 pole man still P1 after leaving", names(rows)[0], ("Pole Man", 91.31, True))
check("2 second still P2", rows[1]["name"], "Second")
check("2 gap of P2 still measured to the departed pole",
      round(rows[1]["interval"], 3), 0.028)
check("2 departed row is out of world", rows[0]["in_world"], False)

# 3. Pole man still listed but the sim no longer reports his time.
field = [("Pole Man", 1001, 0.0)] + FIELD[1:]
rows = standings(p, *make(field))
check("3 time kept while listed without a time", names(rows)[0], ("Pole Man", 91.31, False))
check("3 no duplicate row", [r["name"] for r in rows].count("Pole Man"), 1)

# 4. He rejoins on a NEW CarIdx and sets a slower telemetry lap: best stays.
field = FIELD[1:] + [("Pole Man", 1001, 0.0)]
tel = [0.0, 0.0, 0.0, 92.5]
rows = standings(p, *make(field, tel=tel))
check("4 rejoin slower lap keeps the old best", names(rows)[0], ("Pole Man", 91.31, False))

# 5. ...and an official quicker lap replaces it.
field = FIELD[1:] + [("Pole Man", 1001, 91.100)]
rows = standings(p, *make(field))
check("5 quicker official lap wins", names(rows)[0], ("Pole Man", 91.1, False))

# 6. Session over (new SessionNum): memory cleared.
rows = standings(p, *make(FIELD[1:], num=2))
check("6 new session forgets departed drivers",
      [r["name"] for r in rows], ["Second", "Third", "NoTime"])

# 7. Practice is not affected (no memory passed).
p2 = S.StandingsPoller()
standings(p2, *make(FIELD), quali=False)
rows = standings(p2, *make(FIELD[1:]), quali=False)
check("7 practice: no memory", rows[0]["name"], "Second")

# 8. Driver who never set a time and leaves does not linger.
p3 = S.StandingsPoller()
standings(p3, *make(FIELD))
rows = standings(p3, *make(FIELD[:3]))
check("8 no-time driver who leaves is not kept", len(rows), 3)

print(f"{passes} passed, {len(fails)} failed")
for f in fails:
    print("  FAIL:", f)
sys.exit(1 if fails else 0)
