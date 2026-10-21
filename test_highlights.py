"""Highlight picker (highlights.py), 2026-10-21.

Part 1 uses the real WCT Indianapolis race log of 2026-10-20 when present
(./logs is gitignored, so it is skipped elsewhere): green 268.5 s, full-course
yellow 547.7 s, restart 916.0 s, Mike Zocher wins after 39 laps, Maurice
Becker fastest lap 1:22.762, Michael Endres Driver of the Day — and a false
"moved backwards ~75 % of lap" spin after every pit exit (detector bug fixed
in the dashboard on 2026-10-21, still in this log).
Part 2 is synthetic and always runs.
"""
import os
import sys

import highlights as H

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

fails, passes = [], 0


def check(name, ok, info=""):
    global passes
    if ok:
        passes += 1
    else:
        fails.append(f"{name} {info}")


def covers(s, t):
    return s["t_start"] <= t <= s["t_end"]


INDY = "logs/WCT/indianapolis/20261020-195328_indianapolis_motor_speedway_road_course_race.jsonl"

# ------------------------------------------------------------ part 1: real log
if os.path.exists(INDY):
    ev = H.load_log(INDY)
    start = next(e for e in ev if e["type"] == "session_start")
    # Pro/Am from the CLS cache when present, else none — the picker must work both ways
    classes = H.classes_for(start["drivers"], H.proam_by_name())
    race = H.Race(ev, classes)
    res = H.pick(ev, 240, classes)
    sc = res["scenes"]
    kinds = [s["kind"] for s in sc]
    print(f"Indianapolis: {len(sc)} scenes, {res['total_s']} s, Pro/Am {bool(classes)}")
    for s in sc:
        print(f"  {H.fmt_t(s['t_start'])}-{H.fmt_t(s['t_end'])} {s['kind']:12s} {s['driver']:22s} {s['text']}")

    check("session_num from the log", race.session_num == 3)
    check("green found", abs(race.green_t - 268.48) < 0.01, race.green_t)
    check("one caution, restart at 916", len(race.cautions) == 1
          and abs(race.cautions[0]["t"] - 547.72) < 0.01 and race.cautions[0]["green"] == 916.0,
          race.cautions)
    check("finish = winner's lap 39", abs(race.finish_t - 3881.98) < 0.01, race.finish_t)

    # false pit-exit spins and the pace car are gone, real ones stay
    mb = [e for e in race.incidents if "moved backwards" in (e.get("details") or "")]
    check("no 'moved backwards' pit-exit spins left", not mb, [(e["t_session"], e["driver"]) for e in mb])
    check("pace car dropped", not any("Pace" in e["driver"] for e in race.incidents))
    raw_mb = [e for e in ev if e["type"] == "incident" and "moved backwards" in (e.get("details") or "")]
    check("the log really has them (>= 30)", len(raw_mb) >= 30, len(raw_mb))
    kept = {(round(e["t_session"]), e["driver"]) for e in race.incidents}
    for t, d in ((492, "Manfred Baar"), (634, "Andreas Bastian"), (3930, "Andy Weber")):
        check(f"real incident kept {t} {d}", (t, d) in kept)
    for s in sc:
        if s["kind"] in ("contact", "spin"):
            check(f"scene {s['id']} not a pit-exit spin", "moved backwards" not in (s.get("details") or ""))

    # the reel
    check("total within budget", 180 <= res["total_s"] <= 240, res["total_s"])
    check("opens with the starting grid over the pace lap", kinds[0] == "grid" and sc[0]["overlay"] == "grid"
          and sc[0]["t_end"] <= 268.48 - 7 and sc[0]["t_end"] - sc[0]["t_start"] >= 12, sc[0])
    check("grid: pole sitter Maurice Becker", sc[0]["driver"] == "Maurice Becker", sc[0]["driver"])
    check("then the start, seamlessly", kinds[1] == "start" and covers(sc[1], 268.48)
          and sc[1]["t_start"] == sc[0]["t_end"], sc[:2])
    cau = [s for s in sc if s["kind"] == "caution"]
    check("caution scene covers 547.7 s", len(cau) == 1 and covers(cau[0], 547.72), cau)
    rs = [s for s in sc if s["kind"] == "restart"]
    check("restart scene covers 916 s", len(rs) == 1 and covers(rs[0], 916.0), rs)
    fin = [s for s in sc if s["kind"] == "finish"]
    check("finish scene: Zocher crossing the line", len(fin) == 1 and covers(fin[0], 3881.98)
          and fin[0]["driver"] == "Mike Zocher", fin)
    check("finish scene is the last racing scene", sc.index(fin[0]) >= len(sc) - 3)
    chrono = sorted(sc, key=lambda s: s["t_start"])
    check("chronological apart from the DotD", [s for s in sc if s["kind"] != "dotd"]
          == [s for s in chrono if s["kind"] != "dotd"])
    check("no overlaps", all(a["t_end"] <= b["t_start"] for a, b in zip(chrono, chrono[1:])))
    di = next(i for i, s in enumerate(sc) if s["kind"] == "dotd")
    check("Driver of the Day announced right before the finish", sc[di + 1]["kind"] == "finish",
          [s["kind"] for s in sc])
    check("every scene has a car + session_num", all(s["car_number"] and s["session_num"] == 3 for s in sc))
    check("ids 1..n", [s["id"] for s in sc] == list(range(1, len(sc) + 1)))
    check("passes <= 5, incidents <= 4", kinds.count("pass") <= 5
          and kinds.count("contact") + kinds.count("spin") <= 4)
    check("at least one pass and one contact", "pass" in kinds and "contact" in kinds, kinds)
    check("Driver of the Day scene = Endres", any(s["kind"] == "dotd" and s["car_idx"] == 21 for s in sc))

    # passes: real ones at the front, none under yellow or after the flag
    ps = H.find_passes(race)
    check("passes found", len(ps) > 10, len(ps))
    check("no pass under the caution", not [p for p in ps if 547.72 <= p["t"] <= 916.0])
    check("no pass after the checkered", not [p for p in ps if p["t"] > 3881.98])
    check("late PRO fight Becker > Wonnenberg found", any(
        p["car"] == 11 and p["passed"] == 2 and 3300 < p["t"] < 3340 for p in ps))
    check("never a pass while a car is in the pits", not [p for p in ps
          if race.in_pit_near(p["car"], p["t"], 0, 0) or race.in_pit_near(p["passed"], p["t"], 0, 0)])
    order = race.order_at(2000)
    check("running order mid-race = Zocher, Wonnenberg, Becker", order[:3] == [1, 2, 11],
          [race.name(i) for i in order[:3]])

    fl = H._fastest(race)
    check("fastest lap Maurice Becker 1:22.762", fl and fl[0] == 11 and abs(fl[2] - 82.7622) < 1e-3, fl)

    # budgets
    for b in (120, 300):
        r = H.pick(ev, b, classes)
        must = sum(s["t_end"] - s["t_start"] for s in r["scenes"] if s.get("must"))
        check(f"budget {b}: within", r["total_s"] <= max(b, must) + 0.01, r["total_s"])
    check("bigger budget, more scenes", len(H.pick(ev, 300, classes)["scenes"]) > len(H.pick(ev, 120, classes)["scenes"]))
    if classes:
        check("AM class winner scene", any(s["kind"] == "class_finish" for s in sc))
else:
    print("SKIP part 1: Indianapolis log not present")

# ------------------------------------------------------------ part 2: synthetic
DR = [{"car_idx": i, "car_number": str(10 + i), "name": f"Driver {i}", "car_class": "GT3"} for i in range(4)]


# events from a progress function (laps + pct per car per second)
def build(progress, t0=100.0, n=600):
    ev = [{"type": "session_start", "session_num": 2, "drivers": DR, "track": "Test"},
          {"type": "flag", "t_session": t0 - 2, "flag": "green"}]
    lap_seen = {}
    for k in range(n):
        t = t0 + k
        p = {}
        for i in range(4):
            pr = progress(i, k)
            if pr is None:
                continue
            p[str(i)] = round(pr % 1.0, 4)
            lap = int(pr // 1.0) + 1
            if lap_seen.get(i) != lap:
                lap_seen[i] = lap
                ev.append({"type": "lap", "t_session": t, "car_idx": i, "lap": lap, "position": 0})
        ev.append({"type": "pos", "t": t, "p": p})
    return ev


base = {0: 0.0, 1: -0.010, 2: -0.020, 3: -0.026}


def prog_pass(i, k):
    x = base[i] + 0.0100 * k
    if i == 3 and k > 150:
        x += min(0.012, 0.0004 * (k - 150))     # car 3 closes 0.6 % and gets by car 2
    return x


ev = build(prog_pass)
race = H.Race(ev)
ps = H.find_passes(race)
check("synthetic: car 3 passes car 2 once", [(p["car"], p["passed"]) for p in ps] == [(3, 2)], ps)
check("synthetic: pass for P3", ps and ps[0]["pos"] == 3)


def prog_flicker(i, k):     # side by side: swaps for 2 s, swaps back
    x = base[i] + 0.0100 * k
    if i == 3:
        x += 0.006 + (0.0006 if 200 <= k < 202 else 0.0)
    return x


check("synthetic: a 2 s side-by-side swap is no pass",
      not H.find_passes(H.Race(build(prog_flicker))))

ev = build(prog_pass)
ev.append({"type": "pit", "t_session": 270.0, "car_idx": 2, "duration": 40.0})   # car 2 in the pits 230-270
check("synthetic: passing a car in the pits is no pass", not H.find_passes(H.Race(ev)))

ev = build(prog_pass)
ev.insert(3, {"type": "flag", "t_session": 200.0, "flag": "caution"})
check("synthetic: no pass under yellow", not H.find_passes(H.Race(ev)))

# false pit-exit spin vs a real spin
ev = build(lambda i, k: base[i] + 0.0100 * k)
ev += [{"type": "pit", "t_session": 300.0, "car_idx": 1, "duration": 50.0},
       {"type": "incident", "t_session": 302.0, "car_idx": 1, "driver": "Driver 1",
        "incident_type": "lost_control", "details": "moved backwards 75.7% of lap"},
       {"type": "incident", "t_session": 500.0, "car_idx": 1, "driver": "Driver 1",
        "incident_type": "lost_control", "details": "spin (108 → 31 km/h)"},
       {"type": "incident", "t_session": 420.0, "car_idx": 62, "driver": "Pace Car",
        "incident_type": "lost_control", "details": "moved backwards 75.9% of lap"},
       {"type": "incident", "t_session": 450.0, "car_idx": 0, "driver": "Driver 0",
        "incident_type": "off_track", "details": "four wheels off"}]
race = H.Race(ev)
check("synthetic: only the real spin remains", [e["t_session"] for e in race.incidents] == [500.0],
      [e["t_session"] for e in race.incidents])

# merge: two candidates overlapping, union short -> one scene
c1 = {"kind": "pass", "title": "A", "t": 100, "t_start": 90, "t_end": 108, "prio": 90, "car_idx": 1, "text": "a"}
c2 = {"kind": "contact", "title": "B", "t": 104, "t_start": 96, "t_end": 110, "prio": 80, "car_idx": 2, "text": "b"}
sel = H.select([c1, c2], 240)
check("merge: overlap -> one scene 90-110", len(sel) == 1 and sel[0]["t_start"] == 90 and sel[0]["t_end"] == 110, sel)
check("merge keeps the stronger scene's car", sel and sel[0]["car_idx"] == 1)

# log list: whole races first and preselected; fragments the race logger wrote while a
# REPLAY played (official session_end, but no green flag / few laps) behind them
# (Thomas, 2026-10-21: the newest fragment was preselected -> 0 scenes, Start grey)
import json as _json
import os as _os
import tempfile
with tempfile.TemporaryDirectory() as td:
    def write(name, events, mtime):
        p = _os.path.join(td, name)
        with open(p, "w", encoding="utf-8") as f:
            for e in events:
                f.write(_json.dumps(e) + "\n")
        _os.utime(p, (mtime, mtime))
    head = {"type": "session_start", "track": "Indy", "drivers": DR}
    lapsx = [{"type": "lap", "car_idx": 0, "lap": i} for i in range(60)]
    write("20261020-195328_x_race.jsonl", [head, {"type": "flag", "flag": "green"}] + lapsx
          + [{"type": "session_end", "official": True}], 1000)
    write("20261021-112943_x_race.jsonl", [head, {"type": "session_end", "official": True}], 3000)
    write("20261021-110818_x_race.jsonl", [head] + lapsx, 2000)
    ls = H.list_logs(td)
    check("log list: whole race first, then fragments newest first",
          [d["name"][:15] for d in ls] == ["20261020-195328", "20261021-112943", "20261021-110818"]
          and [d["complete"] for d in ls] == [True, False, False] and ls[0]["track"] == "Indy"
          and ls[0]["laps"] == 60, ls)

print(f"\n{passes} PASS, {len(fails)} FAIL")
for f in fails:
    print("  FAIL", f)
sys.exit(1 if fails else 0)
