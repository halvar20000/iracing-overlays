"""Fastest-lap banner tracker (StandingsPoller._track_fastest), offline."""
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


class IR(dict):
    def __getitem__(self, k):
        return self.get(k)


def rows(bests, cls=None):
    return [{"car_idx": i, "name": f"D{i}", "car_number": str(i), "best_lap": b,
             "class_id": (cls or {}).get(i, 1), "class_name": "GT3"} for i, b in enumerate(bests)]


p = S.StandingsPoller()
ir = IR(SessionUniqueID=1, SessionNum=3, CarIdxBestLapNum=[4, 5, 6, 7])

# Overlay starts mid-race: the existing fastest lap is NOT announced.
st = p._track_fastest(ir, rows([92.0, 91.5, 93.0, 0]), "Race")
check("seed: no event", (st["seq"], st["event"]), (0, None))

# Nothing changes -> still nothing.
st = p._track_fastest(ir, rows([92.0, 91.5, 93.0, 0]), "Race")
check("steady: no event", st["seq"], 0)

# Car 2 goes quickest.
st = p._track_fastest(ir, rows([92.0, 91.5, 91.2, 0]), "Race")
e = st["event"]
check("new fastest -> event", (st["seq"], e["name"], round(e["time"], 3)), (1, "D2", 91.2))
check("delta vs previous", round(e["delta"], 3), -0.3)
check("previous holder", e["prev_name"], "D1")
check("lap number", e["lap"], 6)

# A slower personal best elsewhere does not fire.
st = p._track_fastest(ir, rows([91.9, 91.5, 91.2, 0]), "Race")
check("slower PB: no event", st["seq"], 1)

# Multiclass: each class has its own fastest lap; the class name is sent.
cls = {0: 1, 1: 1, 2: 1, 3: 2}
p._track_fastest(ir, rows([91.9, 91.5, 91.2, 99.0], cls), "Race")   # class 2 first lap
st = p._track_fastest(ir, rows([91.9, 91.5, 91.2, 98.5], cls), "Race")
check("class 2 improvement fires", (st["event"]["name"], st["event"]["class_name"]), ("D3", "GT3"))

# Departed quali rows (car_idx -1) are ignored.
r = rows([91.9, 91.5, 91.2, 98.5], cls) + [{"car_idx": -1, "name": "Gone", "best_lap": 80.0, "class_id": 1}]
seq_before = st["seq"]
st = p._track_fastest(ir, r, "Race")
check("departed driver ignored", st["seq"], seq_before)

# New session: reset + silent seed again.
ir2 = IR(SessionUniqueID=1, SessionNum=4, CarIdxBestLapNum=[0, 0, 0, 0])
st = p._track_fastest(ir2, rows([95.0, 94.0, 0, 0]), "Race")
check("session change: silent seed", st["event"], None)

print(f"{passes} passed, {len(fails)} failed")
for f in fails:
    print("  FAIL:", f)
sys.exit(1 if fails else 0)
