"""Offline checks for race_director.py — no iRacing, no Flask.

Simulates a race tick by tick (0.5 s), applies every shot the director
asks for (camera = that car) and checks what ends up on screen.
Run: python test_race_director.py
"""
import random

import race_director as rd

GROUPS = [{"id": i, "name": n} for i, n in enumerate(
    ["Nose", "Gearbox", "Roll Bar", "Cockpit", "Scenic", "TV1", "TV2", "TV3",
     "Chase", "Far Chase", "Pit Lane", "Blimp"])]
EST = 100.0  # lap time, s

fails = 0


def check(name, ok, extra=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
    if not ok:
        fails += 1


def car(idx, gap_to_leader_s, cls=1, cls_name="GT3", pit=False, world=True):
    """A car `gap_to_leader_s` behind P1 (same lap)."""
    return {"idx": idx, "number": str(10 + idx), "name": f"Driver {idx}",
            "class_id": cls, "class_name": cls_name, "progress": 5.0 - gap_to_leader_s / EST,
            "on_pit": pit, "in_world": world, "est_lap": EST}


class Sim:
    def __init__(self, seed=1):
        self.d = rd.RaceDirector(random.Random(seed))
        self.t = 1000.0
        self.cam = None
        self.group = None
        self.cuts = []   # (t, kind, idx, group)

    def run(self, seconds, cars_fn, events_at=None, racing=True):
        events_at = dict(events_at or {})
        end = self.t + seconds
        while self.t < end:
            ev = events_at.pop(round(self.t, 1), [])
            shot = self.d.tick(self.t, cars_fn(self.t), GROUPS, self.cam, ev, racing)
            if shot:
                self.cuts.append((self.t, shot.kind, shot.car["idx"], shot.group["name"]))
                if shot.kind != "replay":   # a replay returns to the car on air
                    self.cam, self.group = shot.car["idx"], shot.group["name"]
            self.t = round(self.t + 0.5, 1)
        return self


# 1) quiet race: leader first, never stuck on the leader, never Scenic
spread = lambda t: [car(i, i * 4.0) for i in range(8)]       # 4 s between everyone
s = Sim().run(120, spread)
check("quiet race opens on the leader", s.cuts[0][1] == "leader" and s.cuts[0][2] == 0, s.cuts[:2])
kinds = {k for _, k, _, _ in s.cuts}
check("quiet race rotates leader / field", {"leader", "field"} <= kinds, kinds)
longest = max((b[0] - a[0]) for a, b in zip(s.cuts, s.cuts[1:]))
check("no shot longer than ~20 s on a quiet race", longest <= 20, longest)
check("Scenic is never used", all(g != "Scenic" for *_, g in s.cuts))
shortest = min((b[0] - a[0]) for a, b in zip(s.cuts, s.cuts[1:]) if b[2] != a[2])
check("no cut to another car before MIN_SHOT", shortest >= rd.MIN_SHOT, shortest)
field_cars = {i for _, k, i, _ in s.cuts if k == "field"}
check("field shots spread over several cars", len(field_cars) >= 4, field_cars)


# 2) a battle for P3 appears -> director goes there and stays, varying the camera
def battle(t):
    cs = [car(i, i * 4.0) for i in range(8)]
    cs[3] = car(3, 2 * 4.0 + 0.5)          # P4 0.5 s behind P3
    return cs
s = Sim().run(20, spread).run(50, battle)
bt = [c for c in s.cuts if c[1] == "battle"]
check("battle for P3 is picked up", bt and bt[0][2] == 3, s.cuts)
groups_used = {g for _, k, _, g in s.cuts if k == "battle"}
check("long battle shot changes camera", len(groups_used) >= 2, groups_used)
check("battle held until MAX_SHOT, then something else",
      s.cuts[-1][1] != "battle" or s.t - bt[0][0] <= rd.MAX_SHOT["battle"] + 1, s.cuts[-3:])

# 3) contact during the battle, car NOT on screen -> replay at once, then
#    back to the battle (which gets its full length again)
inc = {"id": 1, "type": "collision", "car_idx": 6, "details": "contact"}
s = Sim().run(20, battle)
t_inc = s.t + 3.0
s.run(30, battle, {round(t_inc, 1): [inc]})
after = [c for c in s.cuts if c[0] >= t_inc]
check("unseen contact -> replay immediately", after and after[0][1] == "replay" and after[0][0] == t_inc, after[:2])
check("replay uses a TV camera", after and after[0][3].startswith("TV"), after[:1])
check("after the replay the battle continues (no cut to another car for 16 s)",
      all(c[2] == 3 for c in after[1:] if c[0] < t_inc + rd.REPLAY_HOLD_S), after[:4])
check("an already-seen incident id is not shown twice",
      sum(1 for c in s.cuts if c[1] == "replay") == 1)

# 3b) contact of a car that WAS on screen -> no replay
s = Sim().run(20, battle)                       # camera on #3, battle with #2
s.run(10, battle, {round(s.t + 2, 1): [{"id": 7, "type": "collision", "car_idx": 2}]})
check("contact seen live (car in the battle on air) -> no replay",
      not any(c[1] == "replay" for c in s.cuts), s.cuts[-3:])
check("...and the log says why", any(e["kind"] == "seen" for e in s.d.log))

# 4) overtakes: ignored in the first 2 laps after the green, then replayed
#    when they were not on screen
def moving(t):          # whole field moves, 4 s apart, lap 107 s... EST 100
    return [dict(car(i, i * 4.0), progress=5.0 - i * 0.04 + (t - 1000) / EST) for i in range(8)]
ot = lambda k, t: {"id": 100 + k, "type": "overtake", "car_idx": 6, "passed_idx": 5,
                   "passed_name": "Driver 5", "passed_number": "15", "position": 6}
s = Sim().run(150, moving, {1100.0: [ot(1, 1100)]})          # lap 1-2 after the green
check("overtake in the first 2 laps is ignored", not any(c[1] == "replay" for c in s.cuts), s.cuts)
s.run(100, moving, {1210.0: [ot(2, 1210)]})                    # lap 3
rp = [c for c in s.cuts if c[1] == "replay"]
check("overtake after lap 2, not on screen -> replay", rp and rp[0][2] == 6, s.cuts[-4:])
check("replay reason names both cars", "passes #15" in s.d.log[-1]["reason"] or
      any("replay overtake" in e["reason"] and "#15" in e["reason"] for e in s.d.log))
s.run(60, moving, {1240.0: [ot(3, 1240)]})                     # within REPLAY_GAP_S
check("no second overtake replay within REPLAY_GAP_S",
      sum(1 for c in s.cuts if c[1] == "replay") == 1, [c for c in s.cuts if c[1] == "replay"])

# 5) multiclass: a car of another class 0.3 s behind is NOT a battle
def mixed(t):
    return [car(0, 0), car(1, 0.3, cls=2, cls_name="GT4"), car(2, 8), car(3, 12, cls=2, cls_name="GT4")]
s = Sim().run(40, mixed)
check("no battle across classes", all(k != "battle" for _, k, _, _ in s.cuts), s.cuts)
check("both class leaders get screen time", {0, 1} <= {i for _, k, i, _ in s.cuts if k == "leader"}, s.cuts)

# 6) operator clicks: hands off for MANUAL_PAUSE_S
s = Sim().run(10, battle)
n = len(s.cuts)
s.d.manual_override(s.t)
s.run(rd.MANUAL_PAUSE_S - 1, battle)
check("no cuts while paused after a manual click", len(s.cuts) == n, s.cuts[n:])
s.run(3, battle)
check("director resumes after the pause", len(s.cuts) > n)

# 7) camera car leaves the world -> immediate re-pick
gone = {"t": None}
def vanish(t):
    cs = battle(t)
    if gone["t"] and t >= gone["t"]:
        cs[3]["in_world"] = False
    return cs
s = Sim().run(15, vanish)
gone["t"] = s.t
s.run(3, vanish)
check("car leaving the world is replaced at once", s.cam != 3 and s.cuts[-1][0] <= gone["t"] + 0.5, s.cuts[-2:])

# 8) not racing (pre-green / checkered) -> director does nothing
s = Sim().run(20, spread, racing=False)
check("idle when not racing", not s.cuts)

# 9) groups missing from the plan -> any non-Scenic group
d = rd.RaceDirector()
g = d._pick_group("battle", [{"id": 0, "name": "Scenic"}, {"id": 1, "name": "Blimp"}], 0)
check("fallback group when TV/Chase are missing", g and g["name"] == "Blimp", g)
check("only Scenic -> no shot", d._pick_group("leader", [{"id": 0, "name": "Scenic"}], 0) is None)

# 10) camera drifted away (iRacing / someone else) -> director re-asserts
s = Sim().run(10, spread)
s.cam = 99
s.run(3, spread)
check("camera re-asserted after drifting", s.cam != 99)

# 11) status carries the reason for the commentary step
st = Sim().run(70, battle).d.status(1070.0)
check("status has current shot + reason", st["current"] and st["current"]["reason"], st["current"])
check("log keeps the battle reason", any("battle for P3" in e["reason"] for e in st["log"]), st["log"][:2])
check("decision log is kept", len(st["log"]) >= 2)

# 12) green flag, whole field crossing the line: a battle pair that drops a
#     lap for a moment (S/F counter lag) must not trigger cuts every 0.3 s
#     (Motegi replay 2026-10-14)
def sf_lag(t):
    cs = []
    for i in range(20):
        p = 1.97 + (t - 1000) / 107.0 - i * 0.0026
        lap, pct = int(p), p - int(p)
        if pct < 0.004:
            lap -= 1
        cs.append(dict(car(i, 0), progress=lap + pct, est_lap=1.0))
    return cs
s = Sim().run(30, sf_lag)
early = [b[0] - a[0] for a, b in zip(s.cuts, s.cuts[1:]) if b[2] != a[2]]
check("no early cuts when cars cross the line at the green", not early or min(early) >= rd.MIN_SHOT, s.cuts)
check("est_lap 1.0 is ignored (no 0.0 s battles)", all("0.0s" not in e["reason"] for e in s.d.log), s.d.log[0]["reason"])

# 13) tight pack, battles everywhere with jittering gaps: calm shots (first
#     real test cut battle -> battle every 8 s), the leader still comes up
R = random.Random(9)
gaps = [R.uniform(0.1, 1.3) for _ in range(19)]
def pack(t):
    for i in range(19):
        gaps[i] = min(3, max(0.05, gaps[i] + R.gauss(0, 0.12)))
    cs, cum = [], 0.0
    for i in range(20):
        cum += gaps[i - 1] if i else 0.0
        cs.append(car(i, cum))
    return cs
s = Sim().run(600, pack)
cars_cuts = s.cuts[:1] + [c for a, c in zip(s.cuts, s.cuts[1:]) if c[2] != a[2] or c[1] != a[1]]
durs = sorted(b[0] - a[0] for a, b in zip(cars_cuts, cars_cuts[1:]))
check("tight pack: median shot >= 18 s", durs[len(durs) // 2] >= 18, durs)
check("tight pack: battles are not cut before 15 s (except by events)",
      all(b[0] - a[0] >= getattr(rd, "MIN_BY_KIND", {}).get("battle", 15) for a, b in zip(cars_cuts, cars_cuts[1:])
          if a[1] == "battle" and b[1] in ("battle", "leader", "field")), cars_cuts)
# the leader is on screen in a leader shot or in the battle for P1
lead = [c[0] for c in s.cuts if c[1] == "leader" or (c[1] == "battle" and c[2] == 1)]
check("tight pack: leader shown at least every ~2.5 min",
      lead and all(b - a <= 150 for a, b in zip([1000.0] + lead, lead + [s.t])), lead)

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
raise SystemExit(1 if fails else 0)
