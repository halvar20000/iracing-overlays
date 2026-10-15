"""
Qualifying for the race director (REGIE) — what a director shows in quali:
the FASTEST driver and whoever is on a lap that could beat him or reach the
front, live, to the end of that lap; when a lap ends, its result.

Used by race_director.RaceDirector when the dashboard ticks it with
mode="quali" (same pacing, same log, same camera handling; no replays).

Per car it follows the lap in progress (from the director's smoothed lap +
pct progress and the session clock):
  • a FLYING lap starts at an S/F crossing on track (not from the pit lane);
    pit road, leaving the world or (if reported) an off-track sample spoils it;
  • a live DELTA to the class pole: the car's elapsed time at its current
    pct against the pole lap's own (pct -> time) curve, recorded while that
    lap was driven and scaled to the official best — the same idea as the
    Quali Delta overlay. Without a recorded curve (pole set before we
    watched) a straight-line estimate is used (pole time x pct), marked
    approximate;
  • its BEST lap (CarIdxBestLapTime): an improvement is logged as a
    "quali_lap" entry (time, new position, gap to pole, new pole?) for the
    commentary — for the top 10 or the car on air.

Candidates:
  attack  a clean flying lap predicted in the top ATTACK_TOP_N (or the pole
          sitter's own lap): stronger the closer to (or under) pole pace and
          the further into the lap; held a few seconds past the line so the
          result is seen
  pole    the class pole sitter on track (due every LEADER_EVERY_S)
  field   other cars on track, flying laps first, least screen time first
  incident  a spin / contact of a car on a promising flying lap — cut live
"""

from __future__ import annotations

import math

ATTACK_TOP_N = 10        # flying laps predicted this far up are worth showing
ATTACK_MIN_PCT = 0.15    # before this a lap's delta means little
ATTACK_PRIO = 80         # on pole pace; minus ATTACK_PER_S per second slower
ATTACK_PER_S = 40
ATTACK_MAX_DELTA = 1.5   # slower than this vs pole: not an attack, whatever the position
POLE_SITTER_ADJ = -5     # the pole sitter's own lap ranks below...
CHALLENGER_BONUS = 12    # ...anyone else under pole pace (more than SWITCH_MARGIN apart)
FIRST_LAP_PRIO = 55      # no time set yet in the class: a first flying lap is the story
FIRST_LAP_MIN_PCT = 0.3
NEXT_LAP_PRIO = 70       # the car on air goes straight into its next flying lap: stay
FINISH_HOLD_S = 5.0      # an attack shot stays this long past the line (the result)
POLE_PRIO = 35
POLE_DUE_PRIO = 65
FIELD_PRIO = 20
INCIDENT_PRIO = 100
LOG_TOP_N = 10           # best-lap improvements logged for this far up (+ the car on air)
CURVE_STEP = 0.005       # store a (pct, elapsed) sample every 0.5 % of a lap
CURVE_MATCH_S = 0.35     # a finished lap is THE best lap if within this of the official best


def fmt_lap(t: float | None) -> str | None:
    if not t or t <= 0:
        return None
    m, s = divmod(t, 60.0)
    return f"{int(m)}:{s:06.3f}" if m else f"{s:.3f}"


class QualiState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.lap: dict = {}          # idx -> current lap tracking
        self.curve: dict = {}        # idx -> (total, [(pct, t)]) of its best lap
        self.last_lap: dict = {}     # idx -> (total, samples) of its last finished clean lap
        self.best: dict = {}         # idx -> best lap seen
        self.finished: dict = {}     # idx -> wall time its flying lap ended (attack hold)
        self.primed = False          # first tick only records the bests (no "news")

    # -- tracking -------------------------------------------------------------
    def update(self, now, t, cars, director):
        """Follow every car's lap; log best-lap improvements."""
        for c in cars:
            idx = c["idx"]
            prog = c.get("progress") or 0.0
            lapn = math.floor(prog)
            pct = prog - lapn
            st = self.lap.get(idx)
            spoil = c.get("on_pit") or not c.get("in_world") or c.get("surface") == 0
            if st is None or lapn < st["lap"] or lapn > st["lap"] + 1:
                # first sight or a replay seek: wait for the next crossing
                self.lap[idx] = {"lap": lapn, "start": None, "flying": False, "clean": False,
                                 "samples": []}
                continue
            if lapn == st["lap"] + 1:
                if st["flying"] and st["clean"] and st["start"] is not None:
                    total = t - pct * (c.get("est_lap") or 100.0) - st["start"]
                    self.last_lap[idx] = (total, st["samples"] + [(1.0, total)])
                    self.finished[idx] = now
                est = c.get("est_lap") or 100.0
                self.lap[idx] = st = {"lap": lapn, "start": t - pct * est,
                                      "flying": not spoil, "clean": not spoil, "samples": []}
            if spoil:
                st["clean"] = False
                if c.get("on_pit") or not c.get("in_world"):
                    st["flying"] = False
            if st["start"] is not None and st["clean"]:
                el = t - st["start"]
                if not st["samples"] or pct - st["samples"][-1][0] >= CURVE_STEP:
                    st["samples"].append((pct, el))
        self._bests(now, cars, director)
        self.primed = True

    def _bests(self, now, cars, director):
        by_class: dict = {}
        for c in cars:
            if (c.get("best") or 0) > 0:
                by_class.setdefault(c.get("class_id"), []).append(c)
        for c in cars:
            b = c.get("best") or 0.0
            idx = c["idx"]
            old = self.best.get(idx)
            if b <= 0 or (old is not None and b >= old - 1e-4):
                continue
            self.best[idx] = b
            ll = self.last_lap.get(idx)
            if ll and abs(ll[0] - b) <= CURVE_MATCH_S:
                self.curve[idx] = ll
            if not self.primed:
                continue
            field = sorted(x.get("best") for x in by_class.get(c.get("class_id"), []))
            pos = 1 + sum(1 for x in field if x < b - 1e-4)
            others = [x.get("best") for x in by_class.get(c.get("class_id"), []) if x["idx"] != idx]
            prev_pole = min(others) if others else None
            on_air = director.current is not None and director.current.car["idx"] == idx
            if pos > LOG_TOP_N and not on_air:
                continue
            gap = None if pos == 1 else round(b - field[0], 3)
            info = {"lap_time": round(b, 3), "lap_time_str": fmt_lap(b), "position": pos,
                    "gap_to_pole": gap, "new_pole": pos == 1,
                    "margin": round(prev_pole - b, 3) if pos == 1 and prev_pole else None,
                    "improved_from": round(old, 3) if old else None,
                    # was already on pole and only went faster
                    "kept_pole": bool(pos == 1 and old and (prev_pole is None or old <= prev_pole))}
            why = (f"pole improved: #{c['number']} {c['name']} {fmt_lap(b)}" if info["kept_pole"] else
                   f"new pole: #{c['number']} {c['name']} {fmt_lap(b)}" if pos == 1 else
                   f"best lap: #{c['number']} {c['name']} {fmt_lap(b)} -> P{pos} (+{gap:.3f})")
            director.log.appendleft({"t": round(now, 1), "kind": "quali_lap", "car_idx": idx,
                                     "car_number": c["number"], "name": c["name"], "group": None,
                                     "reason": why, "info": info})

    # -- live delta -----------------------------------------------------------
    def delta(self, c, t, pole_idx, pole_best):
        """-> (delta s vs pole, approximate?) for a car on a clean flying lap."""
        st = self.lap.get(c["idx"])
        if not st or not st["flying"] or not st["clean"] or st["start"] is None or not pole_best:
            return None, None
        pct = (c.get("progress") or 0.0) % 1.0
        if pct < ATTACK_MIN_PCT:
            return None, None
        el = t - st["start"]
        cur = self.curve.get(pole_idx)
        if cur:
            total, pts = cur
            ref = _interp(pts, pct) * (pole_best / total)
            return el - ref, False
        return el - pole_best * pct, True

    # -- candidates -----------------------------------------------------------
    def candidates(self, director, now, t, live, by_idx, fresh, cars=None):
        from race_director import Shot
        cur = director.current
        cur_key = cur.key() if cur else None
        out = []
        classes: dict = {}
        for c in live:
            classes.setdefault(c.get("class_id"), []).append(c)
        multiclass = len(classes) > 1
        # pole / positions from EVERY car with a time — a pole sitter back in the
        # garage is still on pole
        timed_by_class: dict = {}
        for c in cars if cars is not None else live:
            if (c.get("best") or 0) > 0:
                timed_by_class.setdefault(c.get("class_id"), []).append(c)

        def cls(c):
            return f" {c.get('class_name')}" if multiclass and c.get("class_name") else ""

        promising = set()
        for cid, cl in classes.items():
            timed = sorted(timed_by_class.get(cid, []), key=lambda c: c["best"])
            pole = timed[0] if timed else None
            pole_best = pole["best"] if pole else None
            bests = [c["best"] for c in timed]
            for c in cl:
                st = self.lap.get(c["idx"]) or {}
                pct = (c.get("progress") or 0.0) % 1.0
                clean_lap = st.get("flying") and st.get("clean") and st.get("start") is not None
                if pole is None:
                    # nobody has a time yet: the first flying laps are the story
                    if clean_lap and pct >= FIRST_LAP_MIN_PCT:
                        promising.add(c["idx"])
                        out.append(Shot("attack", c, int(FIRST_LAP_PRIO + 10 * pct),
                                        f"first flying lap: #{c['number']} {c['name']}{cls(c)}",
                                        {"first_lap": True, "pct": round(pct, 2)}))
                    continue
                if (cur_key == ("attack", c["idx"]) and clean_lap and pct < ATTACK_MIN_PCT
                        and self.finished.get(c["idx"], -1e9) >= cur.started):
                    # straight into the next flying lap: no cut away and back
                    promising.add(c["idx"])
                    hold = now - self.finished[c["idx"]] < FINISH_HOLD_S
                    out.append(Shot("attack", c, 90 if hold else NEXT_LAP_PRIO,
                                    f"next flying lap: #{c['number']} {c['name']}{cls(c)}",
                                    dict(cur.info, next_lap=True)))
                    continue
                d, approx = self.delta(c, t, pole["idx"], pole_best)
                if d is None or d > ATTACK_MAX_DELTA:
                    continue
                pred = pole_best + d
                pos = 1 + sum(1 for b, x in zip(bests, timed) if b < pred and x["idx"] != c["idx"])
                own_best = c.get("best") or 0
                if own_best and pred >= own_best and not (pole and c["idx"] == pole["idx"]):
                    continue                    # no improvement coming -> nothing to show
                if pos > ATTACK_TOP_N:
                    continue
                promising.add(c["idx"])
                is_pole = pole is not None and c["idx"] == pole["idx"]
                prio = ATTACK_PRIO - max(d, 0.0) * ATTACK_PER_S + 10 * pct - 2 * min(pos, 10)
                if is_pole:
                    prio += POLE_SITTER_ADJ       # only improving his own time
                elif d < 0:
                    prio += CHALLENGER_BONUS      # under pole pace: THE story of the moment
                sign = "-" if d < 0 else "+"
                out.append(Shot("attack", c, int(prio),
                                f"flying lap: #{c['number']} {c['name']}{cls(c)} "
                                f"{sign}{abs(d):.2f}s vs pole{' (approx)' if approx else ''}, "
                                f"heading for P{pos}",
                                {"delta": round(d, 2), "approx": approx, "predicted_position": pos,
                                 "pct": round(pct, 2), "pole_sitter": is_pole,
                                 "pole": None if is_pole or not pole else pole["name"],
                                 "pole_time": fmt_lap(pole_best)}))
            # the lap on air just ended: stay for the result
            if cur and cur.kind == "attack" and cur.car["idx"] in by_idx:
                fin = self.finished.get(cur.car["idx"])
                if fin is not None and fin >= cur.started and now - fin < FINISH_HOLD_S \
                        and not any(s.key() == cur_key for s in out):
                    out.append(Shot("attack", by_idx[cur.car["idx"]], 90, cur.reason,
                                    dict(cur.info, finished=True)))
            # the pole sitter
            if pole is not None and pole["idx"] in by_idx and not pole.get("on_pit") \
                    and pole["idx"] not in promising:
                if director.started_at is None:
                    director.started_at = now
                seen = director.last_shown.get(pole["idx"], director.started_at)
                if cur_key != ("pole", pole["idx"]) and now - seen >= 120.0:
                    prio = POLE_DUE_PRIO
                else:
                    prio = POLE_PRIO - (0 if cur_key == ("pole", pole["idx"])
                                        else director._recent_penalty(now, pole["idx"]))
                out.append(Shot("pole", pole, prio,
                                f"pole: #{pole['number']} {pole['name']}{cls(pole)} {fmt_lap(pole_best)}",
                                {"position": 1, "lap_time_str": fmt_lap(pole_best)}))

        # incidents on a promising lap: live
        for e in fresh:
            if e.get("type") in ("collision", "lost_control") and e.get("car_idx") in promising:
                c = by_idx[e["car_idx"]]
                out.append(Shot("incident", c, INCIDENT_PRIO,
                                f"{'contact' if e['type'] == 'collision' else 'spin'} on a flying lap: "
                                f"#{c['number']} {c['name']}",
                                {"event": e.get("type"), "flying_lap": True}))
        if cur and cur.kind == "incident" and now - cur.started < 10.0 and cur.car["idx"] in by_idx:
            out.append(Shot("incident", by_idx[cur.car["idx"]], cur.prio, cur.reason, cur.info))

        # the field: on track, flying laps first, least screen time first
        rest = [c for c in live if not c.get("on_pit") and c["idx"] not in promising]
        picks = []
        if cur and cur.kind == "field" and cur.car["idx"] in by_idx:
            picks.append(by_idx[cur.car["idx"]])
        pool = [c for c in rest if not picks or c["idx"] != picks[0]["idx"]]
        flying = [c for c in pool if (self.lap.get(c["idx"]) or {}).get("flying")]
        pool = flying or pool
        if pool:
            oldest = min(director.last_shown.get(c["idx"], -1e9) for c in pool)
            picks.append(director.rng.choice([c for c in pool
                                              if director.last_shown.get(c["idx"], -1e9) == oldest]))
        all_cars = cars or live

        def class_bests(c):          # the car's class, fastest first (quali position)
            return sorted((x.get("best") or 0) for x in all_cars
                          if (x.get("best") or 0) > 0 and x.get("class_id") == c.get("class_id"))
        for c in picks:
            fl = (self.lap.get(c["idx"]) or {}).get("flying")
            best = c.get("best") or 0
            best = best if best > 0 else 0      # iRacing reports -1 for "no time yet"
            # the commentary needs something to say about a car without a time
            # too (2026-10-15: "noch ohne gemessene Zeit" over and over)
            out.append(Shot("field", c, FIELD_PRIO + (5 if fl else 0),
                            f"on track: #{c['number']} {c['name']}"
                            + (f" (best {fmt_lap(best)})" if best else "")
                            + (" — flying lap" if fl else ""),
                            {"lap_time_str": fmt_lap(best) if best else None, "flying": bool(fl),
                             "quali": True, "car": c.get("car") or None, "team": c.get("team") or None,
                             "position": (class_bests(c).index(best) + 1)
                                         if best and best in class_bests(c) else None,
                             "times_set": len(class_bests(c))}))
        return out


def _interp(pts, pct):
    """Elapsed time at `pct` on a recorded lap [(pct, t)…] (sorted by pct)."""
    if pct <= pts[0][0]:
        p0, t0 = pts[0]
        return t0 * (pct / p0) if p0 > 0 else t0
    for (p0, t0), (p1, t1) in zip(pts, pts[1:]):
        if p0 <= pct <= p1:
            return t0 + (t1 - t0) * ((pct - p0) / (p1 - p0) if p1 > p0 else 0.0)
    return pts[-1][1]
