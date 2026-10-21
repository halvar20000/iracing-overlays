"""
highlights.py — picks the scenes of a highlight video from a race log.

Pure logic, no SDK / OBS / Flask: give it the events of a race log written
by iracing_race_logger.py, get back a short list of scenes (3-5 min in
total) with SessionTime start / end, the car the camera follows and why.
highlight_runner.py then plays them from the iRacing replay and records
them with OBS.

Scene kinds (German titles for the title card / commentary):
  grid       the starting grid graphic (OBS source, iracing_grid.py = quali
             result) over the last seconds of the pace lap, running straight
             into the start                                       (always)
  start      the green flag, following the leader             (always)
  caution    the full-course yellow coming out                 (always)
  restart    the green after a caution                         (always)
  finish     the winner's last metres and the line             (always)
  class_finish  the AM class winner crossing the line (Pro/Am fields)
  pass       a position change at the front: lead, podium, class lead —
             derived from the per-second `pos` events (lap + CarIdxLapDistPct
             of every car), not from the lap events (those only know the
             order at the S/F line)
  contact / spin   from `incident` events — without the false "moved
             backwards ~75 % of lap" spins every pit exit produced before
             the dashboard fix of 2026-10-21 (an incident of a car right
             after its own `pit` exit is dropped), and without the pace car
  fastest    the last stretch of the session's fastest lap
  dotd       Driver of the Day — his best overtake, else a few seconds of him

Selection: the four "always" scenes first, then the rest by priority while
the total stays under the budget (default 240 s), at most MAX_PASSES passes
and MAX_INCIDENTS incidents, no overlaps (an overlapping scene is merged
into its neighbour when the result stays short, else dropped). Output is
chronological — except the Driver of the Day, announced right before the finish.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_BUDGET_S = 240.0
MIN_BUDGET_S, MAX_BUDGET_S = 60.0, 600.0
MAX_PASSES = 5
MAX_INCIDENTS = 4

# scene windows: seconds before / after the moment
WIN = {
    "grid":    (8.0 + 20.0, -8.0),    # 20 s of pace lap, ends where "start" begins: the grid
                                       # page holds the first places 10 s, then scrolls
    "start":   (8.0, 30.0),
    "caution": (4.0, 10.0),
    "restart": (12.0, 18.0),
    "finish":  (25.0, 5.0),
    "class_finish": (12.0, 4.0),
    "pass":    (12.0, 6.0),
    "contact": (8.0, 6.0),
    "spin":    (8.0, 6.0),
    "fastest": (22.0, 2.0),
    "dotd":    (12.0, 6.0),
}
CROWD_S, CROWD_PENALTY = 180.0, 12   # spread the reel over the race
MERGE_MAX_S = 36.0          # an overlap is merged only if the union stays this short

PIT_EXIT_FALSE_SPIN_S = 20.0   # "moved backwards" this soon after a pit exit = pit-lane artefact
PASS_HOLD_S = 5.0           # the new order must hold this long
PASS_MAX_GAP_LAPS = 0.03    # cars this close (in laps) at the swap (~120 m at Indy)
PASS_CHURN_S = 15.0         # A passes B, B re-passes A within this -> neither counts
PASS_PIT_GUARD_S = 30.0     # no pass while either car was in the pits around it
INCIDENT_GROUP_S = 4.0      # incidents this close = one scene (multi-car contact)

KIND_TITLE = {
    "grid": "Startaufstellung",
    "start": "Der Start",
    "caution": "Gelbphase",
    "restart": "Restart",
    "finish": "Zieleinlauf",
    "class_finish": "Klassensieg",
    "pass": "Überholmanöver",
    "contact": "Kontakt",
    "spin": "Dreher",
    "fastest": "Schnellste Runde",
    "dotd": "Fahrer des Tages",
}


# ---------------------------------------------------------------- loading
def load_log(path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def proam_by_name(cache_path="proam_cache.json") -> dict:
    """{name casefold: 'PRO'/'AM'} from cls_proam's cache (CLS WCT standings)."""
    try:
        body = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for s in body.get("standings") or []:
        cls = (s.get("proAmClass") or "").upper()
        if cls in ("PRO", "AM") and s.get("name"):
            out[s["name"].casefold()] = cls
    return out


def classes_for(drivers: list[dict], by_name: dict) -> dict:
    """{car_idx: 'PRO'/'AM'} — only when the field IS that league's field
    (same thresholds as cls_proam: >= 3 drivers and >= 50 % on the roster)."""
    hits = {}
    for d in drivers:
        c = by_name.get((d.get("name") or "").casefold())
        if c:
            hits[d["car_idx"]] = c
    if len(hits) >= 3 and len(hits) >= 0.5 * max(1, len(drivers)):
        return hits
    return {}


def _car_family(car: str) -> str:
    """Class guess from the car name for logs without car_class (older logger)."""
    c = (car or "").upper()
    for key, fam in (("CUP", "Cup"), ("LMP", "LMP"), ("P217", "LMP"), ("GTP", "GTP"),
                     ("GT4", "GT4"), ("GTE", "GTE"), ("GT3", "GT3")):
        if key in c:
            return fam
    return car or ""


def fmt_t(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def _short(name: str) -> str:
    parts = (name or "").split()
    return parts[-1] if parts else (name or "")


# ---------------------------------------------------------------- race model
class Race:
    """Everything the picker needs, derived once from the events."""

    def __init__(self, events: list[dict], classes: dict | None = None):
        self.events = events
        self.start = next((e for e in events if e.get("type") == "session_start"), {}) or {}
        self.session_num = self.start.get("session_num")
        self.drivers = {d["car_idx"]: d for d in self.start.get("drivers") or []}
        self.classes = {int(k): v for k, v in (classes or {}).items()}
        # iRacing car classes: in a multiclass field passes count inside a class only
        self.car_class = {i: d.get("car_class") or _car_family(d.get("car")) for i, d in self.drivers.items()}
        self.multiclass = len(set(self.car_class.values())) > 1
        self.flags = [e for e in events if e.get("type") == "flag"]
        self.end = next((e for e in events if e.get("type") == "session_end"), None)
        self.dotd = next((e for e in events if e.get("type") == "driver_of_day" and e.get("ok")), None)
        self.laps = [e for e in events if e.get("type") == "lap"]
        self.green_t = self._first_green()
        self.cautions = self._cautions()
        self.pits = self._pits()
        self.incidents = self._incidents()
        self.samples = self._progress()        # [(t, {idx: progress})]
        self.finish_t = self._finish_t()

    # -- names
    def name(self, idx) -> str:
        d = self.drivers.get(idx)
        return d.get("name") if d else f"#{idx}"

    def number(self, idx) -> str:
        d = self.drivers.get(idx)
        return str(d.get("car_number")) if d else ""

    # -- flags
    def _first_green(self):
        g = next((f["t_session"] for f in self.flags if f.get("flag") == "green"), None)
        if g is None:   # no green flag logged: first lap event with a position
            g = next((e["t_session"] for e in self.laps if e.get("position")), None)
        return g

    def _cautions(self) -> list[dict]:
        """[{t: caution out, one_to_green, green}] — green None if the race ended under it."""
        out, cur = [], None
        for f in self.flags:
            k, t = f.get("flag"), f.get("t_session")
            if k == "caution":
                if cur is None:
                    cur = {"t": t, "one_to_green": None, "green": None}
                    out.append(cur)
            elif k == "one_to_green" and cur is not None:
                cur["one_to_green"] = t
            elif k == "green" and cur is not None:
                cur["green"] = t
                cur = None
        return out

    def under_caution(self, t) -> bool:
        for c in self.cautions:
            if c["t"] <= t <= (c["green"] if c["green"] is not None else 1e12):
                return True
        return False

    # -- pits: [entry, exit] per car (the pit event is logged at the EXIT)
    def _pits(self) -> dict:
        out = {}
        for e in self.events:
            if e.get("type") == "pit":
                ex = float(e["t_session"])
                out.setdefault(e["car_idx"], []).append((ex - float(e.get("duration") or 0), ex))
        return out

    def in_pit_near(self, idx, t, before=PASS_PIT_GUARD_S, after=10.0) -> bool:
        for a, b in self.pits.get(idx, []):
            if a <= t + after and b >= t - before:
                return True
        return False

    def false_pit_spin(self, e) -> bool:
        """The pre-2026-10-21 detector reported every pit exit as 'moved backwards
        ~75 % of lap' (the pit lane crosses the S/F line). Drop those."""
        if "moved backwards" not in (e.get("details") or ""):
            return False
        t = float(e["t_session"])
        for a, b in self.pits.get(e["car_idx"], []):
            if a - 2.0 <= t <= b + PIT_EXIT_FALSE_SPIN_S:
                return True
        return False

    def _incidents(self) -> list[dict]:
        out = []
        for e in self.events:
            if e.get("type") != "incident":
                continue
            if e.get("incident_type") not in ("collision", "lost_control"):
                continue
            idx = e.get("car_idx")
            if idx not in self.drivers or "pace car" in (e.get("driver") or "").lower():
                continue
            if self.false_pit_spin(e):
                continue
            out.append(e)
        out.sort(key=lambda e: e["t_session"])
        return out

    # -- per-second running order from `pos` (+ lap events to resync)
    def _progress(self) -> list:
        """[(t, {idx: laps + pct})] from the green on.

        Laps are counted from the pct wrapping at the S/F line, NOT taken from
        the lap events: iRacing's lap counter does not count the start
        crossing (every car logs "lap 0" both on the pace lap and right after
        the green) and flips a tick before / after the pct near the line. At
        the green a car still behind the line (pct > 0.5) starts at lap -1.
        A car that skips the line (towed to the pits) is put back in step
        with its lap counter mid-lap, using the offset counter -> own count
        most cars show shortly after the start."""
        green = self.green_t
        if green is None:
            return []
        lap_events = {}
        for e in self.laps:
            if e.get("lap") is not None:
                lap_events.setdefault(e["car_idx"], []).append((e["t_session"], e["lap"]))
        ptr = {k: 0 for k in lap_events}
        cur_lap, laps, last = {}, {}, {}
        offset = None
        offset_votes = {}
        samples = []
        for e in self.events:
            if e.get("type") != "pos":
                continue
            t = float(e["t"])
            for idx, lst in lap_events.items():
                i = ptr[idx]
                while i < len(lst) and lst[i][0] <= t:
                    cur_lap[idx] = lst[i][1]
                    i += 1
                ptr[idx] = i
            if t < green:
                continue
            snap = {}
            for k, pct in (e.get("p") or {}).items():
                idx = int(k)
                if idx not in self.drivers or pct is None or pct < 0:
                    continue
                pct = float(pct)
                if idx not in laps:
                    laps[idx] = -1 if pct > 0.5 else 0
                else:
                    lp = last[idx]
                    if lp > 0.75 and pct < 0.25:
                        laps[idx] += 1
                    elif lp < 0.25 and pct > 0.75:
                        laps[idx] -= 1
                last[idx] = pct
                if 0.2 < pct < 0.8 and idx in cur_lap and cur_lap[idx] >= 0:
                    d = laps[idx] - cur_lap[idx]
                    if offset is None:
                        if green + 60 < t < green + 400:
                            offset_votes[d] = offset_votes.get(d, 0) + 1
                        elif t >= green + 400 and offset_votes:
                            offset = max(offset_votes, key=offset_votes.get)
                    elif d != offset:
                        laps[idx] = cur_lap[idx] + offset
                snap[idx] = laps[idx] + pct
            if snap and t >= green + 2.0:
                samples.append((t, snap))
        return samples

    def order_at(self, t) -> list:
        """Car idxs by progress at the sample nearest before t."""
        snap = None
        for st, s in self.samples:
            if st > t:
                break
            snap = s
        if snap is None:
            return []
        return sorted(snap, key=lambda i: -snap[i])

    def leader_at(self, t):
        o = self.order_at(t)
        return o[0] if o else None

    def class_rank(self, order: list, idx):
        c = self.classes.get(idx)
        if not c:
            return None
        r = 0
        for i in order:
            if self.classes.get(i) == c:
                r += 1
                if i == idx:
                    return r
        return None

    # -- finish
    def _finish_t(self):
        if not self.end or not self.end.get("final"):
            return None
        win = next((r for r in self.end["final"] if r.get("position") == 1), self.end["final"][0])
        return self.lap_end_time(win["car_idx"], win.get("laps_completed"))

    def lap_end_time(self, idx, lap_no):
        mine = [e for e in self.laps if e["car_idx"] == idx]
        hit = [e for e in mine if e.get("lap") == lap_no]
        if hit:
            return hit[0]["t_session"]
        racing = [e for e in mine if not e.get("on_pit")]
        return racing[-1]["t_session"] if racing else None

    def winners(self) -> list:
        """[(car_idx, class or None)] — overall winner, then the other class winner."""
        if not self.end:
            return []
        final = sorted(self.end.get("final") or [], key=lambda r: r.get("position") or 999)
        if not final:
            return []
        out = [(final[0]["car_idx"], self.classes.get(final[0]["car_idx"]))]
        seen = {out[0][1]}
        for r in final[1:]:
            c = self.classes.get(r["car_idx"])
            if c and c not in seen and (r.get("reason_out") or "Running") == "Running":
                out.append((r["car_idx"], c))
                seen.add(c)
        return out

    def last_t(self):
        return self.samples[-1][0] if self.samples else None


# ---------------------------------------------------------------- detectors
def find_passes(race: Race) -> list[dict]:
    """Position swaps that held, between cars close together on track,
    neither in the pits, not under the caution."""
    s = race.samples
    found = []
    for k in range(1, len(s)):
        t, snap = s[k]
        _, prev = s[k - 1]
        common = [i for i in snap if i in prev]
        if len(common) < 2:
            continue
        o_prev = sorted(common, key=lambda i: -prev[i])
        o_now = sorted(common, key=lambda i: -snap[i])
        r_prev = {i: n for n, i in enumerate(o_prev)}
        r_now = {i: n for n, i in enumerate(o_now)}
        for a in common:
            if r_now[a] >= r_prev[a]:
                continue
            for b in common:
                if b == a or not (r_prev[b] < r_prev[a] and r_now[b] > r_now[a]):
                    continue
                if abs(snap[a] - snap[b]) > PASS_MAX_GAP_LAPS:
                    continue
                if race.multiclass and race.car_class.get(a) != race.car_class.get(b):
                    continue      # a prototype lapping a GT3 is traffic, not a pass
                if race.finish_t is not None and t > race.finish_t:
                    continue      # after the checkered: finishing order / cool-down, not racing
                if race.under_caution(t) or race.in_pit_near(a, t) or race.in_pit_near(b, t):
                    continue
                if any(abs(i["t_session"] - t) < 12 and i["car_idx"] in (a, b) for i in race.incidents):
                    continue      # a spun / hit car being passed is the incident, not a pass
                # hold: a stays ahead of b for PASS_HOLD_S, both still there
                ok, j = True, k + 1
                while j < len(s) and s[j][0] <= t + PASS_HOLD_S:
                    sj = s[j][1]
                    if a not in sj or b not in sj or sj[a] <= sj[b]:
                        ok = False
                        break
                    j += 1
                if not ok:
                    continue
                full = sorted(snap, key=lambda i: -snap[i])
                if race.multiclass:
                    full = [i for i in full if race.car_class.get(i) == race.car_class.get(a)]
                pos = full.index(a) + 1
                found.append({"t": t, "car": a, "passed": b, "pos": pos,
                              "class": race.classes.get(a),
                              "class_pos": race.class_rank(full, a)})
    # churn: b re-passes a soon after -> neither counts
    drop = set()
    for i, p in enumerate(found):
        for j in range(i + 1, len(found)):
            q = found[j]
            if q["t"] - p["t"] > PASS_CHURN_S:
                break
            if q["car"] == p["passed"] and q["passed"] == p["car"]:
                drop.update((i, j))
    return [p for i, p in enumerate(found) if i not in drop]


def pass_prio(race: Race, p: dict) -> float:
    pos, cp = p["pos"], p.get("class_pos")
    if pos == 1:
        pr = 90
    elif cp == 1:
        pr = 82
    elif pos <= 3:
        pr = 72
    elif cp is not None and cp <= 3:
        pr = 62
    elif pos <= 10:
        pr = 40
    else:
        pr = 22
    if race.finish_t and race.green_t:
        frac = (p["t"] - race.green_t) / max(1.0, race.finish_t - race.green_t)
        if frac > 0.75:
            pr += 8           # late in the race weighs more
    return pr


def group_incidents(race: Race) -> list[list[dict]]:
    groups = []
    for e in race.incidents:
        if groups and e["t_session"] - groups[-1][-1]["t_session"] <= INCIDENT_GROUP_S:
            groups[-1].append(e)
        else:
            groups.append([e])
    return groups


# ---------------------------------------------------------------- scenes
def _scene(kind, t, car, race, prio, text="", t0=None, t1=None, **extra) -> dict:
    pre, post = WIN[kind]
    s = {
        "kind": kind,
        "title": KIND_TITLE[kind],
        "t": round(float(t), 2),
        "t_start": round(float(t0 if t0 is not None else t - pre), 2),
        "t_end": round(float(t1 if t1 is not None else t + post), 2),
        "car_idx": car,
        "car_number": race.number(car) if car is not None else "",
        "driver": race.name(car) if car is not None else "",
        "prio": prio,
        "text": text,
        "session_num": race.session_num,
    }
    s.update(extra)
    return s


def candidates(race: Race) -> list[dict]:
    out = []
    # starting grid (graphic over the pace lap), then the start
    if race.green_t is not None:
        pole = _grid_pole(race)
        out.append(_scene("grid", race.green_t, pole, race, 1000, must=True, overlay="grid",
                          text=f"Die Startaufstellung — {race.name(pole)} auf der Pole."))
        lead = race.leader_at(race.green_t + 3) or pole
        out.append(_scene("start", race.green_t, lead, race, 1000, must=True,
                          text=f"Grüne Flagge — {race.name(lead)} führt das Feld in die erste Kurve."))
    # caution + restart
    for c in race.cautions:
        lead = race.leader_at(c["t"])
        cause = _caution_cause(race, c["t"])
        txt = "Gelbphase! Das Safety Car kommt raus."
        if cause:
            txt = f"Gelbphase nach dem Zwischenfall um {race.name(cause[0]['car_idx'])}."
        out.append(_scene("caution", c["t"], lead, race, 900, must=True, text=txt, flag_t=c["t"]))
        if c["green"] is not None:
            lead = race.leader_at(c["green"])
            out.append(_scene("restart", c["green"], lead, race, 900, must=True, flag_t=c["green"],
                              text=f"Restart — {race.name(lead)} gibt das Tempo vor."))
    # finish + class winner
    wins = race.winners()
    if race.finish_t is not None and wins:
        w = wins[0][0]
        out.append(_scene("finish", race.finish_t, w, race, 1000, must=True,
                          text=f"{race.name(w)} gewinnt das Rennen!"))
        for idx, cls in wins[1:]:
            t = race.lap_end_time(idx, _laps_of(race, idx))
            if t is not None:
                out.append(_scene("class_finish", t, idx, race, 85, cls=cls,
                                  text=f"{race.name(idx)} holt den Sieg in der {cls}-Klasse."))
    # passes
    dotd_idx = (race.dotd or {}).get("winner", {}).get("car_idx")
    dotd_pass = None
    for p in find_passes(race):
        pr = pass_prio(race, p)
        a, b = p["car"], p["passed"]
        if (a == dotd_idx and p["t"] > (race.green_t or 0) + WIN["start"][1] + 10
                and (dotd_pass is None or pr >= dotd_pass["prio_raw"])):
            dotd_pass = {**p, "prio_raw": pr}
        where = f"die Führung" if p["pos"] == 1 else f"Platz {p['pos']}"
        if p.get("class_pos") == 1 and p["pos"] != 1:
            where = f"Platz {p['pos']} und die {p['class']}-Führung"
        if race.multiclass:
            where += f" ({race.car_class.get(a)})"
        out.append(_scene("pass", p["t"], a, race, pr, passed_idx=b, passed=race.name(b), pos=p["pos"],
                          class_pos=p.get("class_pos"), cls=p.get("class"),
                          text=f"{race.name(a)} geht an {_short(race.name(b))} vorbei — {where}!"))
    # incidents
    caution_causes = {id(e) for c in race.cautions for e in (_caution_cause(race, c["t"]) or [])}
    for g in group_incidents(race):
        coll = [e for e in g if e["incident_type"] == "collision"]
        kind = "contact" if coll else "spin"
        main = (coll or g)[0]
        t = g[0]["t_session"]
        order = race.order_at(t)
        top5 = set(order[:5])
        cars = []
        for e in g:
            if e["car_idx"] not in cars:
                cars.append(e["car_idx"])
        pr = 70 if kind == "contact" else (45 if "stopped" in (main.get("details") or "") else 60)
        if top5 & set(cars):
            pr += 15
        if len(cars) > 1:
            pr += 5
        if any(id(e) in caution_causes for e in g):
            pr += 30
        names = " und ".join(_short(race.name(c)) for c in cars[:3])
        txt = (f"Kontakt zwischen {names}!" if kind == "contact" and len(cars) > 1
               else f"Kontakt für {race.name(main['car_idx'])}!" if kind == "contact"
               else f"{race.name(main['car_idx'])} dreht sich!")
        out.append(_scene(kind, t, main["car_idx"], race, pr, cars=cars, text=txt,
                          details=main.get("details")))
    # fastest lap
    fl = _fastest(race)
    if fl:
        idx, t_end, lap_t = fl
        out.append(_scene("fastest", t_end, idx, race, 50, lap_time=lap_t,
                          text=f"Schnellste Runde für {race.name(idx)}: {_laptime(lap_t)}."))
    # Driver of the Day
    if dotd_idx is not None and dotd_idx in race.drivers:
        why = (race.dotd.get("winner") or {}).get("why") or ""
        if dotd_pass:
            t = dotd_pass["t"]
        else:
            t = _dotd_fallback_t(race)
        if t is not None:
            out.append(_scene("dotd", t, dotd_idx, race, 80,
                              text=f"Fahrer des Tages: {race.name(dotd_idx)}" + (f" — {_why_de(why)}." if why else ".")))
    return out


def _grid_pole(race):
    for e in race.laps:
        if e.get("grid_pos") == 1:
            return e["car_idx"]
    return next(iter(race.drivers), None)


def _caution_cause(race: Race, t_c):
    """The incident group closest before the caution (within 60 s), collisions first."""
    best = None
    for g in group_incidents(race):
        t = g[0]["t_session"]
        if t_c - 60 <= t <= t_c + 1:
            score = (1 if any(e["incident_type"] == "collision" for e in g) else 0, t)
            if best is None or score > best[0]:
                best = (score, g)
    return best[1] if best else None


def _laps_of(race, idx):
    for r in (race.end or {}).get("final") or []:
        if r["car_idx"] == idx:
            return r.get("laps_completed")
    return None


def _fastest(race: Race):
    """(car_idx, SessionTime the lap ended, lap time) of the session's fastest lap.
    The lap END is found as the pair of consecutive S/F crossings whose
    difference matches the best lap (the lap events' own lap_time can lag)."""
    best = None
    for r in (race.end or {}).get("final") or []:
        bl = r.get("best_lap")
        if bl and bl > 0 and (best is None or bl < best[1]):
            best = (r["car_idx"], bl, r.get("best_lap_num"))
    if not best:
        return None
    idx, bl, num = best
    mine = [e for e in race.laps if e["car_idx"] == idx]
    cand = None
    for a, b in zip(mine, mine[1:]):
        d = b["t_session"] - a["t_session"]
        if abs(d - bl) < 0.7 and (cand is None or abs(d - bl) < cand[0]):
            cand = (abs(d - bl), b["t_session"])
    t_end = cand[1] if cand else race.lap_end_time(idx, num)
    return (idx, t_end, bl) if t_end is not None else None


def _laptime(s):
    m = int(s // 60)
    return f"{m}:{s - 60 * m:06.3f}".replace(".", ",")


def _why_de(why: str) -> str:
    w = why
    w = re.sub(r"gained (\d+) positions? \(P(\d+)\D+P(\d+)\)", r"\1 Plätze gutgemacht (P\2 auf P\3)", w)
    w = re.sub(r"(\d+) overtakes?", r"\1 Überholmanöver", w)
    w = re.sub(r"(\d+) incidents?", r"\1 Incident(s)", w)
    w = w.replace("recovered", "erholt")
    return w


def _dotd_fallback_t(race: Race):
    if race.green_t is None or race.finish_t is None:
        return None
    return race.green_t + 0.6 * (race.finish_t - race.green_t)


# ---------------------------------------------------------------- selection
def _overlap(a, b) -> bool:
    return a["t_start"] < b["t_end"] and b["t_start"] < a["t_end"]


def _dur(s) -> float:
    return s["t_end"] - s["t_start"]


def select(cands: list[dict], budget_s=DEFAULT_BUDGET_S) -> list[dict]:
    budget_s = max(MIN_BUDGET_S, min(MAX_BUDGET_S, float(budget_s)))
    chosen: list[dict] = []
    total = 0.0
    n = {"pass": 0, "inc": 0}

    def try_add(s) -> bool:
        nonlocal total
        clash = [c for c in chosen if _overlap(c, s)]
        if clash:
            # merge into the clashing scene if the union stays short; the
            # stronger scene keeps its car / title
            c = clash[0]
            if len(clash) > 1:
                return False
            t0, t1 = min(c["t_start"], s["t_start"]), max(c["t_end"], s["t_end"])
            extra = (t1 - t0) - _dur(c)
            if t1 - t0 > MERGE_MAX_S and not (c.get("must") and extra <= 0):
                return False
            if total + extra > budget_s and not s.get("must"):
                return False
            c["t_start"], c["t_end"] = round(t0, 2), round(t1, 2)
            c.setdefault("also", []).append(s["title"])
            if s.get("text") and s["text"] not in c.get("text", ""):
                c["text_also"] = (c.get("text_also", "") + " " + s["text"]).strip()
            total += extra
            return True
        if total + _dur(s) > budget_s and not s.get("must"):
            return False
        chosen.append(dict(s))
        total += _dur(s)
        return True

    musts = [c for c in cands if c.get("must")]
    for s in sorted(musts, key=lambda c: c["t"]):
        try_add(s)
    # the rest greedily by priority, re-scored after every pick so the reel
    # spreads over the race: a scene close to an already chosen one (and a
    # car already shown twice) counts less
    pool = [c for c in cands if not c.get("must")]
    per_car: dict = {}

    def score(s):
        sc = s["prio"]
        for c in chosen:
            if not c.get("must") and abs(c["t"] - s["t"]) < CROWD_S:
                sc -= CROWD_PENALTY
        if per_car.get(s["car_idx"], 0) >= 2:
            sc -= 25
        return sc

    while pool:
        pool.sort(key=lambda c: (-score(c), c["t"]))
        s = pool.pop(0)
        if s["kind"] == "pass" and n["pass"] >= MAX_PASSES:
            continue
        if s["kind"] in ("contact", "spin") and n["inc"] >= MAX_INCIDENTS:
            continue
        if try_add(s):
            per_car[s["car_idx"]] = per_car.get(s["car_idx"], 0) + 1
            if s["kind"] == "pass":
                n["pass"] += 1
            elif s["kind"] in ("contact", "spin"):
                n["inc"] += 1
    chosen.sort(key=lambda c: c["t_start"])
    # Driver of the Day is ANNOUNCED right before the finish (Thomas,
    # 2026-10-21), whenever his scene happened in the race
    dotd = [c for c in chosen if c["kind"] == "dotd"]
    if dotd and any(c["kind"] == "finish" for c in chosen):
        chosen.remove(dotd[0])
        fin = next(i for i, c in enumerate(chosen) if c["kind"] == "finish")
        chosen.insert(fin, dotd[0])
    for i, c in enumerate(chosen):
        c["id"] = i + 1
        c["t_start"] = max(0.0, c["t_start"])
    return chosen


def pick(events: list[dict], budget_s=DEFAULT_BUDGET_S, classes: dict | None = None) -> dict:
    """events -> {scenes, total_s, info}. `classes` = {car_idx: 'PRO'/'AM'}."""
    race = Race(events, classes)
    cands = candidates(race)
    scenes = select(cands, budget_s)
    return {
        "scenes": scenes,
        "total_s": round(sum(_dur(s) for s in scenes), 1),
        "candidates": len(cands),
        "info": {
            "track": race.start.get("track"),
            "config": race.start.get("track_config"),
            "session_num": race.session_num,
            "subsession_id": race.start.get("subsession_id"),
            "drivers": len(race.drivers),
            "green_t": race.green_t,
            "finish_t": race.finish_t,
            "cautions": race.cautions,
            "proam": bool(race.classes),
            "winner": race.name(race.winners()[0][0]) if race.winners() else None,
        },
    }


def pick_file(path, budget_s=DEFAULT_BUDGET_S, proam_cache="proam_cache.json") -> dict:
    events = load_log(path)
    start = next((e for e in events if e.get("type") == "session_start"), {}) or {}
    classes = classes_for(start.get("drivers") or [], proam_by_name(proam_cache))
    out = pick(events, budget_s, classes)
    out["log"] = str(path)
    return out


COMPLETE_MIN_LAPS = 50       # lap events of all cars together


def _log_summary(p: Path) -> dict:
    """Track, laps logged and whether this is a WHOLE race: green flag, an
    end, and at least COMPLETE_MIN_LAPS lap events. The race logger also
    writes short logs while a REPLAY of a race plays (seeks start new files) —
    those even carry an "official" session_end, but no start and few laps."""
    track, laps, complete = "", 0, False
    try:
        data = p.read_bytes()
        try:
            track = json.loads(data.split(b"\n", 1)[0].decode("utf-8")).get("track") or ""
        except ValueError:
            pass
        laps = data.count(b'"type":"lap"') + data.count(b'"type": "lap"')
        has = lambda k, v: (b'"%s":"%s"' % (k, v)) in data or (b'"%s": "%s"' % (k, v)) in data
        complete = has(b"type", b"session_end") and has(b"flag", b"green") and laps >= COMPLETE_MIN_LAPS
    except OSError:
        pass
    return {"track": track, "laps": laps, "complete": complete}


def list_logs(root="logs") -> list[dict]:
    """Race logs: finished races first (newest first), then fragments.
    {path, name, size, mtime, track, complete}."""
    out = []
    for p in Path(root).rglob("*_race.jsonl"):
        try:
            st = p.stat()
        except OSError:
            continue
        out.append({"path": str(p.as_posix()), "name": p.name, "size": st.st_size, "mtime": st.st_mtime,
                    **_log_summary(p)})
    out.sort(key=lambda d: (not d["complete"], -d["mtime"]))
    return out


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    res = pick_file(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_BUDGET_S)
    print(f"{res['info']['track']} — {len(res['scenes'])} Szenen, {res['total_s']} s "
          f"(aus {res['candidates']} Kandidaten)")
    for s in res["scenes"]:
        print(f"{s['id']:2d} {fmt_t(s['t_start'])}-{fmt_t(s['t_end'])} {s['t_end'] - s['t_start']:5.1f}s "
              f"{s['kind']:12s} #{s['car_number']:4s} {s['driver']:22s} p{s['prio']:<4} {s['text']}")
