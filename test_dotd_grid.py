"""Driver-of-the-Day regression check for the starting-grid change.

Two synthetic logs of the same race:
  OLD  — lap events without `grid_pos` (any log recorded before the fix)
  NEW  — same race, lap events carrying `grid_pos`

The OLD log must score exactly as it did before the change; the NEW log
must measure positions_gained from the real grid instead of from the
position at the end of lap 1.
"""
import sys
from driver_of_the_day import analyze

DRIVERS = [
    {"car_idx": 0, "name": "Pole Man",  "car_number": "1"},
    {"car_idx": 1, "name": "Charger",   "car_number": "2"},
    {"car_idx": 2, "name": "Backmarker", "car_number": "3"},
]

# Grid: 0 -> P1, 1 -> P10, 2 -> P3.
GRID = {0: 1, 1: 10, 2: 3}
# End of lap 1 the charger has already gained 5 places (P10 -> P5).
LAP_POS = {0: [1, 2, 1, 1], 1: [5, 4, 3, 2], 2: [3, 3, 4, 3]}


def build(with_grid):
    ev = [{"type": "session_start", "drivers": DRIVERS,
           "track": "Monza", "session_name": "Race"}]
    for ci, poss in LAP_POS.items():
        for lap, p in enumerate(poss, start=1):
            e = {"type": "lap", "car_idx": ci, "lap": lap, "position": p,
                 "lap_time": 90.0, "overtakes": 0, "overtaken": 0}
            if with_grid:
                e["grid_pos"] = GRID[ci]
            ev.append(e)
    ev.append({"type": "session_end", "final": [
        {"car_idx": 0, "position": 1, "laps_completed": 4, "incidents": 0,
         "reason_out": "Running"},
        {"car_idx": 1, "position": 2, "laps_completed": 4, "incidents": 0,
         "reason_out": "Running"},
        {"car_idx": 2, "position": 3, "laps_completed": 4, "incidents": 0,
         "reason_out": "Running"},
    ]})
    return ev


def gains(res):
    return {d["name"]: d["positions_gained"] for d in res["drivers"]}


def recov(res):
    return {d["name"]: d["recovery"] for d in res["drivers"]}


old = analyze(build(False))
new = analyze(build(True))

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")


check("old log ok", old["ok"], True)
check("new log ok", new["ok"], True)

# OLD: start = position at end of lap 1 -> charger only credited 3 (P5->P2)
check("OLD gains unchanged", gains(old),
      {"Pole Man": 0, "Charger": 3, "Backmarker": 0})
# NEW: start = real grid slot -> charger credited the full 8 (P10->P2)
check("NEW gains use the grid", gains(new),
      {"Pole Man": 0, "Charger": 8, "Backmarker": 0})

# Recovery must be untouched by the change: it is still measured from the
# lowest point recorded DURING the race, not from the grid.
check("recovery unchanged by the fix", recov(old), recov(new))

print(f"OLD gains: {gains(old)}   recovery: {recov(old)}")
print(f"NEW gains: {gains(new)}   recovery: {recov(new)}")
print(f"OLD winner: {old['winner']['name']}   NEW winner: {new['winner']['name']}")

if fails:
    print("\nFAILURES:")
    for f in fails:
        print("  ", f)
    sys.exit(1)
print("\nall checks passed")
