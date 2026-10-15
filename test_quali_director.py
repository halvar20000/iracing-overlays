"""Offline checks for the qualifying REGIE (quali_director.py via RaceDirector
mode="quali") — a simulated quali session, no SDK.
Run: python test_quali_director.py
"""
import math
import random

import race_director as rd
import quali_director as qd

fails = 0


def check(name, ok, extra=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
    if not ok:
        fails += 1


GROUPS = [{"id": i + 1, "name": n} for i, n in enumerate(["TV1", "TV2", "TV3", "Chase", "Cockpit", "Scenic"])]


def shape(x):
    """Lap fraction covered after time fraction x — not linear (slow and fast parts)."""
    return x + 0.06 * math.sin(2 * math.pi * x) / (2 * math.pi)


def inv_shape(p):
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if shape(mid) < p:
            lo = mid
        else:
            hi = mid
    return lo


class Car:
    def __init__(self, idx, laps, leave_at):
        self.idx, self.laps, self.leave_at = idx, laps, leave_at   # laps: flying lap times
        self.name = f"Driver {idx}"
        self.best = 0.0
        self.pending = None
        self.events = []          # (t_cross, laptime)

    def state(self, t):
        """-> progress, on_pit, in_world  (out-lap 60 s, then flying laps, then pit)."""
        if t < self.leave_at:
            return 0.0 + 0.5, True, True                     # in the pit lane, not moving
        x = t - self.leave_at
        if x < 60.0:                                        # out-lap: pct 0.5 -> 1.0
            return 0.5 + 0.5 * (x / 60.0), x < 5.0, True
        x -= 60.0
        lap = 1
        for L in self.laps:
            if x < L:
                return lap + shape(x / L), False, True
            x -= L
            lap += 1
        return lap + min(0.3, x / 100.0), x > 3.0, x < 30.0   # in-lap to the pit, then gone

    def cross_times(self):
        t = self.leave_at + 60.0
        out = []
        for L in self.laps:
            t += L
            out.append((t, L))
        return out


def run(cars, until, mode="quali", start=0.0, step=0.25, director=None, on_tick=None):
    d = director or rd.RaceDirector(random.Random(3))
    cam = 0
    t = start
    while t < until:
        rows = []
        for c in cars:
            prog, pit, world = c.state(t)
            for tc, L in c.cross_times():                    # official best 1 s after the line
                if tc + 1.0 <= t and (c.best == 0 or L < c.best - 1e-6) and (tc, L) not in c.events:
                    c.events.append((tc, L))
                    c.best = L if c.best == 0 else min(c.best, L)
            rows.append({"idx": c.idx, "number": str(10 + c.idx), "name": c.name, "class_id": 1,
                         "class_name": "GT3", "progress": prog, "on_pit": pit, "in_world": world,
                         "est_lap": 100.0, "best": c.best})
        shot = d.tick(t, rows, GROUPS, cam, [], racing=True, mode=mode, t_session=t)
        if shot is not None:
            cam = shot.car["idx"]
        if on_tick:
            on_tick(t, d, cam)
        t += step
    return d


# --- scenario 1: A sets pole first, B later goes faster, C is slow -------------
A = Car(0, [101.0, 100.0, 118.0, 118.0], leave_at=0.0)   # pole 100.0 on its 2nd lap, then slow laps
B = Car(1, [100.6, 99.5], leave_at=150.0)      # beats it later: 99.5
C = Car(2, [106.0, 105.5], leave_at=10.0)      # never close
D = Car(3, [103.0], leave_at=400.0)            # one lap, P? but slower than its… first lap
cars = [A, B, C, D]
on_air = []                                     # (t, kind, car)
d = run(cars, 700.0, on_tick=lambda t, d, cam: on_air.append(
    (t, d.current.kind if d.current else None, d.current.car["idx"] if d.current else None)))

log = list(reversed(d.log))
qlaps = [e for e in log if e["kind"] == "quali_lap"]
check("first pole logged", any(e["car_idx"] == 0 and e["info"]["new_pole"] for e in qlaps),
      [(e["name"], e["info"]["lap_time_str"], e["info"]["position"]) for e in qlaps])
newpole_B = [e for e in qlaps if e["car_idx"] == 1 and e["info"]["new_pole"]]
check("B's new pole logged with the margin", newpole_B and abs(newpole_B[0]["info"]["margin"] - 0.5) < 0.01,
      newpole_B)
check("lap time formatted", newpole_B and newpole_B[0]["info"]["lap_time_str"] == "1:39.500", newpole_B[:1])
kept = [e for e in qlaps if e["car_idx"] == 0 and e["info"]["lap_time"] == 100.0]
check("A improving its own pole is 'kept_pole'", kept and kept[0]["info"]["kept_pole"], kept)
check("A's first pole is a new pole, not kept", any(e["car_idx"] == 0 and e["info"]["lap_time"] == 101.0
                                                   and not e["info"]["kept_pole"] for e in qlaps))
check("C logged with its position and gap", any(e["car_idx"] == 2 and e["info"]["position"] >= 2
                                                and e["info"]["gap_to_pole"] for e in qlaps))

# B's pole lap: from 50 s into the lap to its end the camera is on B as an attack
b_cross = B.cross_times()[1][0]                 # end of B's 99.5 lap
b_start = b_cross - 99.5
seg = [x for x in on_air if b_start + 50 <= x[0] <= b_cross]
share = sum(1 for x in seg if x[1] == "attack" and x[2] == 1) / max(1, len(seg))
check("B's pole lap shown live (attack, second half)", share > 0.9, f"{share:.2f}")
hold = [x for x in on_air if b_cross <= x[0] <= b_cross + 4.0]
check("stays on B a few seconds past the line", all(x[2] == 1 for x in hold), hold[:3])
c_attack = [x for x in on_air if x[1] == "attack" and x[2] == 2]
check("slow car C never an 'attack'", not c_attack)

# curve: once A's pole lap was recorded, B's delta is NOT approximate
att_B = [e for e in log if e["kind"] == "attack" and e["car_idx"] == 1]
check("attack shots logged for B", att_B)
check("delta from the recorded pole curve (not approx)", att_B and not att_B[-1]["info"]["approx"], att_B[-1:])
check("curve stored for A's pole lap", 0 in d.quali.curve and abs(d.quali.curve[0][0] - 100.0) < 0.3,
      d.quali.curve.get(0, (None,))[0])

# the delta the director computes at mid-lap is right (B 0.5 s faster than A at the end, linear in x)
dq = d.quali
probe = {}
def probe_tick(t, d, cam):
    if abs(t - (b_start + 60.0)) < 0.13:
        car = {"idx": 1, "progress": 2 + shape(60.0 / 99.5)}
        probe["d"] = d.quali.delta(car, t, 0, 100.0)
run([Car(0, [101.0, 100.0], 0.0), Car(1, [100.6, 99.5], 150.0)], b_start + 61.0, on_tick=probe_tick)
exp = 60.0 - inv_shape(shape(60.0 / 99.5)) * 100.0     # elapsed - pole time at the same pct
check("live delta matches the true gap", probe.get("d") and abs(probe["d"][0] - exp) < 0.15
      and probe["d"][1] is False, (probe.get("d"), exp))

# pole sitter on a slow lap, the only other car far off the pace: show the pole
pole_air = []
dp = run([Car(0, [100.0, 118.0, 118.0], 0.0), Car(1, [110.0, 110.0], 50.0)], 380.0,
         on_tick=lambda t, d, cam: pole_air.append((t, d.current.kind if d.current else None,
                                                     d.current.car["idx"] if d.current else None)))
after = [x for x in pole_air if x[0] > 200]
check("pole sitter gets screen time", any(x[1] == "pole" and x[2] == 0 for x in after),
      sorted({(x[1], x[2]) for x in after}))
check("pole shot names the time", any(e["kind"] == "pole" and "1:40.000" in e["reason"] for e in dp.log))

# --- challenger under pole pace beats the pole sitter's own lap on air ----------
P = Car(0, [100.0, 100.05, 100.05], 0.0)        # pole sitter keeps lapping at pole pace
Q = Car(1, [101.0, 99.4], 40.0)                 # second lap under pole pace
qa = []
run([P, Q], 400.0, on_tick=lambda t, d, cam: qa.append((t, d.current.car["idx"] if d.current else None)))
q_end = Q.cross_times()[1][0]
q_seg = [x for x in qa if q_end - 30 <= x[0] <= q_end]
check("challenger under pole pace shown over the pole sitter's lap",
      sum(1 for x in q_seg if x[1] == 1) / max(1, len(q_seg)) > 0.9)

# --- scenario 2: lap spoiled by the pit lane is never an attack ------------------
class PitCar(Car):
    def state(self, t):
        prog, pit, world = Car.state(self, t)
        if 1.4 < prog < 1.45:
            pit = True                                # drives through the pit lane mid-lap
        return prog, pit, world
E = PitCar(5, [95.0], leave_at=0.0)                 # would be "pole" pace on a clean lap
F = Car(6, [100.0], leave_at=0.0)
log_e = []
d2 = run([E, F], 300.0, on_tick=lambda t, d, cam: log_e.append(d.current.car["idx"] if d.current and d.current.kind == "attack" else None))
check("spoiled lap not shown as an attack after the pit", 5 not in log_e[int((60 + 0.45 * 95) / 0.25):int(255 / 0.25)])

# --- scenario 3: switching to the race resets the quali state --------------------
d3 = run([Car(0, [100.0], 0.0)], 200.0)
check("quali state filled", d3.quali.best)
d3.tick(500.0, [{"idx": 0, "number": "10", "name": "x", "class_id": 1, "progress": 3.0, "on_pit": False,
                 "in_world": True, "est_lap": 100.0}], GROUPS, 0, [], racing=True, mode="race")
check("race mode: quali state cleared", not d3.quali.best and d3.mode == "race")

# --- scenario 4: first sight of an existing best is not news ---------------------
d4 = rd.RaceDirector(random.Random(1))
row = {"idx": 0, "number": "10", "name": "X", "class_id": 1, "progress": 2.5, "on_pit": False,
       "in_world": True, "est_lap": 100.0, "best": 98.7}
d4.tick(1.0, [row], GROUPS, 0, [], mode="quali", t_session=1.0)
check("existing bests at switch-on are not logged", not [e for e in d4.log if e["kind"] == "quali_lap"])

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
raise SystemExit(1 if fails else 0)
