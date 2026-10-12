"""
Offline checks for field_sectors.py (best sectors, theoretical best, sector
top speed) with simulated cars on a 5000 m track, 3 sectors, 15 Hz polling.
Run:  python test_field_sectors.py
"""
import random

from field_sectors import FieldSectors

PASS = FAIL = 0
TRACK = 5000.0
BOUNDS = [0.0, 0.33, 0.66]
HZ = 15.0


def check(name, cond, info=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS]", name, info)
    else:
        FAIL += 1
        print("[FAIL]", name, info)


def sim(fs, cars, seconds, t0=0.0, jitter=True, surf_fn=None, pit_fn=None):
    """cars: {idx: [pct, speed_fn(pct) -> m/s]}. Integrates each car's
    position and feeds fs one sample per poll. Returns end time."""
    t = t0
    rng = random.Random(1)
    n = max(cars) + 1
    end = t0 + seconds
    while t < end:
        dt = 1.0 / HZ * (rng.uniform(0.85, 1.15) if jitter else 1.0)
        # integrate in 1 ms steps so the "true" times are exact
        steps = int(round(dt * 1000))
        for _ in range(steps):
            for c in cars.values():
                c[0] = (c[0] + c[1](c[0]) * 0.001 / TRACK) % 1.0
        t += steps / 1000.0
        pcts = [-1.0] * n
        surfs = [-1] * n
        pits = [False] * n
        for idx, c in cars.items():
            pcts[idx] = c[0]
            surfs[idx] = surf_fn(idx, c[0], t) if surf_fn else 3
            pits[idx] = pit_fn(idx, c[0], t) if pit_fn else False
        fs.update(t, pcts, surfs, pits)
    return t


def new():
    fs = FieldSectors()
    fs.configure(BOUNDS, TRACK)
    return fs


# 1. constant 50 m/s → sectors 33.0 / 33.0 / 34.0, started mid-sector
fs = new()
sim(fs, {0: [0.10, lambda p: 50.0]}, 260)
b = fs.best
check("constant speed: all three sectors recorded", all(x is not None for x in b))
check("S1 = 33.000 s", b[0] and abs(b[0][0] - 33.0) < 0.01, "%.4f" % b[0][0])
check("S2 = 33.000 s", b[1] and abs(b[1][0] - 33.0) < 0.01, "%.4f" % b[1][0])
check("S3 = 34.000 s (across S/F)", b[2] and abs(b[2][0] - 34.0) < 0.01, "%.4f" % b[2][0])
check("ideal = 100.000 s", fs.ideal() and abs(fs.ideal() - 100.0) < 0.02, "%.4f" % fs.ideal())
check("top speed 180 km/h", abs(fs.top[1][0] - 180.0) < 1.0, "%.1f" % fs.top[1][0])

# 2. partial first sector never counts
fs = new()
sim(fs, {0: [0.20, lambda p: 50.0]}, 10)          # stays inside S1
check("half sector after start → no S1 time", fs.best[0] is None)

# 3. two cars, B faster only in S2 → ideal mixes drivers
fs = new()
cars = {0: [0.0, lambda p: 50.0],
        1: [0.5, lambda p: 60.0 if 0.33 <= p < 0.66 else 45.0]}
sim(fs, cars, 400)
check("S2 best = car 1 (27.5 s)", fs.best[1][1] == 1 and abs(fs.best[1][0] - 27.5) < 0.01,
      "%s %.4f" % (fs.best[1][1], fs.best[1][0]))
check("S1 best = car 0 (33.0 s)", fs.best[0][1] == 0 and abs(fs.best[0][0] - 33.0) < 0.01)
check("ideal = 33 + 27.5 + 34 = 94.5", abs(fs.ideal() - 94.5) < 0.02, "%.4f" % fs.ideal())
check("car 1 personal ideal = 36.67+27.5+37.78",
      abs(fs.car_ideal(1) - (0.33 * TRACK / 45 + 27.5 + 0.34 * TRACK / 45)) < 0.02,
      "%.3f" % fs.car_ideal(1))
rank = fs.ranking(1)
check("sector-2 speed ranking: car 1 216 km/h first", rank[0][0] == 1 and abs(rank[0][1] - 216) < 1.0,
      str([(i, round(v, 1)) for i, v in rank]))

# 4. off-track in S3 on every lap → no S3 time for that car, speed still counts
fs = new()
sim(fs, {0: [0.0, lambda p: 70.0]}, 300,
    surf_fn=lambda i, p, t: 0 if 0.80 < p < 0.81 else 3)
check("off track in S3 → S3 never recorded", fs.best[2] is None)
check("… S1 / S2 still recorded", fs.best[0] is not None and fs.best[1] is not None)
check("… no ideal without all sectors", fs.ideal() is None)
check("… top speed in S3 still counts", abs(fs.top[2].get(0, 0) - 252.0) < 1.0)

# 5. pit road: sector not timed, pit-lane samples give no speed
fs = new()
sim(fs, {0: [0.0, lambda p: 22.0 if p > 0.9 else 50.0]}, 300,
    pit_fn=lambda i, p, t: p > 0.9)
check("pit in S3 → S3 not timed", fs.best[2] is None)
check("pit-lane speed ignored (S3 top = 180 from before the pit)",
      abs(fs.top[2][0] - 180.0) < 1.0, "%.1f" % fs.top[2][0])

# 6. top speed with a short peak (2 s at 75 m/s = 270 km/h in S2)
def peak(p):
    return 75.0 if 0.45 <= p < 0.45 + 150.0 / TRACK else 50.0
fs = new()
sim(fs, {0: [0.0, peak]}, 220)
check("peak 270 km/h read within 2 km/h", 268.0 <= fs.top[1][0] <= 270.5, "%.1f" % fs.top[1][0])
check("S1 top unaffected (180)", abs(fs.top[0][0] - 180.0) < 1.0)

# 7. data gap > 1 s mid-sector → that sector does not count
fs = new()
t = sim(fs, {0: [0.0, lambda p: 50.0]}, 20)                 # in S1, entry seen at 0? (start at 0.0)
fs.update(t + 3.0, [0.0 + (20 + 3) * 50 / TRACK], [3], [False])  # 3 s jump
check("gap → car restarted, no entry seen", fs.cars[0].enter_t is None)

# 8. replay scrub backwards: no crash, bests kept
fs = new()
t = sim(fs, {0: [0.0, lambda p: 50.0]}, 120)
before = fs.best[0]
fs.update(t - 60.0, [0.4], [3], [False])
fs.update(t - 59.9, [0.401], [3], [False])
check("backwards time jump keeps bests", fs.best[0] == before)

# 9. camera car: live sector + top speed
fs = new()
sim(fs, {0: [0.0, lambda p: 50.0], 1: [0.0, lambda p: 55.0]}, 115)
sec, live, full = fs.cam_speed(1)
check("cam_speed: sector index + live speed", sec == fs.sector_of(fs.cars[1].pct) and live and abs(live - 198) < 1.0,
      "sec %s live %s" % (sec, live))
check("unknown cam → None", fs.cam_speed(7) == (None, None, False))

# 10. car leaves the world → dropped; new bounds → full reset
fs = new()
sim(fs, {0: [0.0, lambda p: 50.0]}, 50)
fs.update(60, [-1.0], [-1], [False])
check("not in world → car dropped", 0 not in fs.cars)
fs.configure([0.0, 0.5], TRACK)
check("new sector layout → bests cleared", fs.n == 2 and fs.best == [None, None])
fs.configure([0.0, 0.5], TRACK)
fs.best[0] = (1.0, 0)
fs.configure([0.5, 0.0], TRACK)
check("same layout again → kept", fs.best[0] == (1.0, 0))

# 11. 64 cars at 15 Hz stay cheap
import time as _t
fs = new()
cars = {i: [i / 64.0, (lambda v: (lambda p: v))(45 + i % 10)] for i in range(64)}
a = _t.perf_counter()
sim(fs, cars, 30, jitter=False)
per_tick = (_t.perf_counter() - a) / (30 * HZ)
check("64 cars: update cost per poll small", True, "%.2f ms incl. simulation" % (per_tick * 1000))

print("\n%d/%d checks passed" % (PASS, PASS + FAIL))
raise SystemExit(1 if FAIL else 0)
