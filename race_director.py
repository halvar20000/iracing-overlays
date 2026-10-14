"""
Race director ("Regie") — picks which car and which camera the broadcast
shows, the way a human director would: incidents first, then overtakes and
close battles (front of the field weighs more), class leaders and the rest
of the field in between, with a minimum and maximum shot length and some
camera variety inside a long shot.

Pure logic, no SDK: the dashboard feeds it a snapshot every tick and gets
back a Shot to cut to (or None = stay). Every decision is logged with WHY
it was taken (`log`), which is exactly the input a later commentary step
needs ("battle for P3, 0.4 s").

Step 1 of the AI broadcast idea (2026-10-14). Tested by test_race_director.py.

Inputs per tick (`tick()`):
  now        wall clock (s)
  cars       [{idx, number, name, class_id, class_name, progress, on_pit,
               in_world, est_lap}]  — progress = lap + lap pct
  groups     [{id, name}]           — camera groups of the session
  cam_idx    car the camera is on now
  events     new incidents / overtakes (dashboard feed dicts, any order;
             already-seen ids are ignored)
  racing     True while the race is green (SessionState 4)
"""

from __future__ import annotations

import random
import time
from collections import deque

# --- pacing (seconds) ---------------------------------------------------------
MIN_SHOT = 8.0           # a shot is never cut earlier — except for a higher-priority event
CAM_VARY_EVERY = 14.0    # long shots change camera group this often
MAX_SHOT = {             # after this a shot loses its claim, something else gets a turn
    "incident": 10.0, "overtake": 9.0, "battle": 40.0, "pit": 12.0,
    "leader": 16.0, "field": 12.0,
}
REVISIT_PENALTY_S = 45.0  # a car shown within this window scores lower
MANUAL_PAUSE_S = 20.0     # after an operator click the director keeps its hands off

# --- thresholds ---------------------------------------------------------------
BATTLE_ENTER_S = 1.0     # gap to the same-class car ahead that makes a battle
BATTLE_KEEP_S = 1.5      # ...and keeps the current battle alive (hysteresis)
EVENT_FRESH_S = 4.0      # incidents / overtakes older than this are not cut to
PIT_TOP_N = 5            # pit stops worth showing: top N of a class

# --- base priorities ----------------------------------------------------------
PRIO = {"incident": 100, "overtake": 70, "battle": 60, "pit": 45,
        "leader": 35, "field": 20}
INTERRUPT_MARGIN = 25    # a candidate this much stronger may cut a shot before MIN_SHOT

# Camera groups per shot kind, by name (exact, case/space-insensitive),
# tried in order and rotated inside long shots. Missing names are skipped;
# Scenic is never used. Track cameras (TV) show incidents and battles best.
CAMS = {
    "incident": ["TV1", "TV2", "TV3"],
    "overtake": ["TV1", "TV2", "TV3"],
    "battle":   ["TV1", "Chase", "TV2", "Far Chase", "TV3"],
    "pit":      ["Pit Lane", "TV1", "TV2"],
    "leader":   ["TV1", "Chase", "TV2", "Cockpit", "TV3"],
    "field":    ["TV2", "TV1", "Chase", "TV3"],
}


def _norm(s: str) -> str:
    return (s or "").upper().replace(" ", "")


class Shot:
    def __init__(self, kind, car, prio, reason, info=None):
        self.kind = kind
        self.car = car            # the car dict the camera follows
        self.prio = prio
        self.reason = reason      # human-readable (English)
        self.info = info or {}    # structured, for the commentary step
        self.group = None         # {id, name} chosen when applied
        self.started = 0.0
        self.cam_changed = 0.0
        self.cam_i = 0
        self.cut_prio = prio      # priority when it went on air

    def key(self):
        """Same subject = same kind + same car (or the same battle pair)."""
        return (self.kind, self.info.get("pair") or self.car["idx"])

    def as_dict(self):
        return {"kind": self.kind, "car_idx": self.car["idx"],
                "car_number": self.car["number"], "name": self.car["name"],
                "group": self.group["name"] if self.group else None,
                "reason": self.reason, "info": self.info}


class RaceDirector:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.current: Shot | None = None
        self.last_shown: dict[int, float] = {}   # car idx -> last time on screen
        self.seen_events: set = set()
        self.paused_until = 0.0
        self.log: deque = deque(maxlen=60)
        self.kind_cuts: dict[str, int] = {}      # cuts per kind -> camera rotation

    # -- public ---------------------------------------------------------------
    def reset(self):
        self.current = None
        self.last_shown.clear()
        self.seen_events.clear()
        self.paused_until = 0.0

    def manual_override(self, now: float | None = None):
        """Operator picked a car / camera by hand: hands off for a while."""
        self.paused_until = (now if now is not None else time.time()) + MANUAL_PAUSE_S
        self.current = None

    def status(self, now: float | None = None) -> dict:
        now = now if now is not None else time.time()
        cur = self.current
        return {
            "current": dict(cur.as_dict(), since=round(now - cur.started, 1)) if cur else None,
            "paused_for": max(0.0, round(self.paused_until - now, 1)),
            "log": list(self.log),
        }

    def tick(self, now, cars, groups, cam_idx, events, racing=True):
        """Returns a Shot to cut to (with .group set), or None."""
        fresh = self._new_events(events)
        if now < self.paused_until or not racing:
            return None
        live = [c for c in cars if c.get("in_world")]
        if not live:
            return None
        by_idx = {c["idx"]: c for c in live}
        cands = self._candidates(now, live, by_idx, fresh)
        cur = self.current
        if cur is not None and cur.car["idx"] not in by_idx:
            cur = self.current = None            # car left the world: re-pick now
        if cur is not None:
            cur.car = by_idx[cur.car["idx"]]
            self.last_shown[cur.car["idx"]] = now
        if cur is None:
            best = max(cands, key=lambda s: s.prio) if cands else None
            return self._cut(now, best, groups) if best else None

        age = now - cur.started
        still = next((s for s in cands if s.key() == cur.key()), None)
        others = [s for s in cands if s.key() != cur.key()]
        other = max(others, key=lambda s: s.prio) if others else None
        if age < MIN_SHOT:
            # measured against the priority the shot was CUT with: a subject
            # that looks weaker for a moment (S/F line glitch) is no excuse
            # to cut after 3 s
            if other and other.prio >= max(cur.prio, cur.cut_prio) + INTERRUPT_MARGIN:
                return self._cut(now, other, groups)
        elif still is None or age >= MAX_SHOT.get(cur.kind, 15.0):
            # subject over (battle broke up, event shown) or shown long
            # enough: give the screen to the best OTHER subject
            if other:
                return self._cut(now, other, groups)
            if still is None:
                self.current = None
                return None
        elif other and other.prio > still.prio:
            return self._cut(now, other, groups)
        # same subject continues — vary the camera on long shots
        if still is not None:
            cur.prio, cur.reason, cur.info = still.prio, still.reason, still.info
        # (not when the shot ends soon anyway — a 2 s camera before a cut looks nervous)
        if (now - cur.cam_changed >= CAM_VARY_EVERY and cur.car["idx"] == cam_idx
                and age + MIN_SHOT <= MAX_SHOT.get(cur.kind, 15.0)):
            g = self._pick_group(cur.kind, groups, cur.cam_i + 1, avoid=cur.group)
            if g:
                cur.cam_i += 1
                cur.group, cur.cam_changed = g, now
                return cur
        if cur.car["idx"] != cam_idx and age >= 2.0:
            return cur                            # camera drifted (e.g. iRacing) — re-assert
        return None

    # -- internals ------------------------------------------------------------
    def _new_events(self, events):
        out = []
        for e in events or []:
            eid = e.get("id")
            if eid is None or eid in self.seen_events:
                continue
            self.seen_events.add(eid)
            out.append(e)
        return out

    def _cut(self, now, shot, groups):
        # each new shot of a kind starts on the next camera of its list, so
        # five field shots in a row are not all TV2
        n = self.kind_cuts.get(shot.kind, 0)
        shot.cam_i = 0 if shot.kind in ("incident", "overtake") else n
        shot.group = self._pick_group(shot.kind, groups, shot.cam_i)
        if shot.group is None:
            return None
        shot.started = shot.cam_changed = now
        self.kind_cuts[shot.kind] = n + 1
        self.current = shot
        self.last_shown[shot.car["idx"]] = now
        self.log.appendleft({"t": round(now, 1), **shot.as_dict()})
        return shot

    def _pick_group(self, kind, groups, i, avoid=None):
        by_name = {}
        for g in groups or []:
            n = _norm(g.get("name"))
            if n and "SCENIC" not in n:
                by_name.setdefault(n, g)
        names = [n for n in CAMS.get(kind, ["TV1"]) if _norm(n) in by_name]
        if not names:   # nothing from the plan exists: any non-Scenic group
            return next(iter(by_name.values()), None)
        for k in range(len(names)):
            g = by_name[_norm(names[(i + k) % len(names)])]
            if avoid is None or g.get("id") != avoid.get("id") or len(names) == 1:
                return g
        return None

    def _recent_penalty(self, now, idx):
        t = self.last_shown.get(idx)
        if t is None:
            return 0
        return 25 if now - t < REVISIT_PENALTY_S else 0

    def _candidates(self, now, live, by_idx, fresh):
        cur = self.current
        cur_key = cur.key() if cur else None
        out = []

        # class order by live progress
        classes: dict = {}
        for c in live:
            classes.setdefault(c.get("class_id"), []).append(c)
        pos = {}
        for cl in classes.values():
            cl.sort(key=lambda c: -c["progress"])
            for i, c in enumerate(cl):
                pos[c["idx"]] = i + 1
        multiclass = len(classes) > 1

        def label(c):
            p = f"P{pos.get(c['idx'], '?')}"
            return f"{p} {c.get('class_name')}" if multiclass and c.get("class_name") else p

        # 1) incidents — the freshest one that is still in the world
        for e in fresh:
            if e.get("type") in ("collision", "lost_control") and e.get("car_idx") in by_idx:
                c = by_idx[e["car_idx"]]
                what = "contact" if e["type"] == "collision" else "spin"
                out.append(Shot("incident", c, PRIO["incident"],
                                f"{what}: #{c['number']} {c['name']} ({label(c)})",
                                {"event": e.get("type"), "position": pos.get(c["idx"]),
                                 "details": e.get("details", "")}))
        # keep showing an incident for its shot length even after it is "seen"
        if cur and cur.kind in ("incident", "overtake") and now - cur.started < MAX_SHOT[cur.kind]:
            if cur.car["idx"] in by_idx:
                out.append(Shot(cur.kind, by_idx[cur.car["idx"]], cur.prio, cur.reason, cur.info))

        # 2) overtakes — front of the field weighs more
        for e in fresh:
            if e.get("type") == "overtake" and e.get("car_idx") in by_idx:
                c = by_idx[e["car_idx"]]
                p = pos.get(c["idx"], 99)
                out.append(Shot("overtake", c, PRIO["overtake"] - min(p, 20),
                                f"overtake: #{c['number']} {c['name']} passes "
                                f"#{e.get('passed_number', '?')} {e.get('passed_name', '')} for {label(c)}",
                                {"position": p, "passed": e.get("passed_name"),
                                 "passed_number": e.get("passed_number")}))

        # 3) battles inside a class: chaser within BATTLE_ENTER_S of the car ahead
        for cl in classes.values():
            for i in range(1, len(cl)):
                a, b = cl[i - 1], cl[i]                # a ahead, b chasing
                if a.get("on_pit") or b.get("on_pit"):
                    continue
                est = next((e for e in (b.get("est_lap"), a.get("est_lap"))
                            if e and e >= 20), 100.0)   # 1.0 seen in a replay
                gap = (a["progress"] - b["progress"]) * est
                pair = (a["idx"], b["idx"])
                limit = BATTLE_KEEP_S if cur_key == ("battle", pair) else BATTLE_ENTER_S
                if gap <= 0 or gap > limit:
                    continue
                p = pos[a["idx"]]
                prio = PRIO["battle"] - 2 * min(p, 15) - int(gap * 10)
                prio -= self._recent_penalty(now, b["idx"]) if cur_key != ("battle", pair) else 0
                out.append(Shot("battle", b, prio,
                                f"battle for {label(a)}: #{b['number']} {b['name']} "
                                f"{gap:.1f}s behind #{a['number']} {a['name']}",
                                {"pair": pair, "position": p, "gap": round(gap, 2),
                                 "ahead": a["name"], "ahead_number": a["number"]}))

        # 4) pit stops of the front runners
        for c in live:
            if c.get("on_pit") and pos.get(c["idx"], 99) <= PIT_TOP_N:
                out.append(Shot("pit", c, PRIO["pit"] - pos[c["idx"]] - self._recent_penalty(now, c["idx"])
                                if cur_key != ("pit", c["idx"]) else PRIO["pit"] - pos[c["idx"]],
                                f"pit stop: #{c['number']} {c['name']} ({label(c)})",
                                {"position": pos[c["idx"]]}))

        # 5) class leaders
        for cl in classes.values():
            c = cl[0]
            if c.get("on_pit"):
                continue
            pen = 0 if cur_key == ("leader", c["idx"]) else self._recent_penalty(now, c["idx"])
            out.append(Shot("leader", c, PRIO["leader"] - pen,
                            f"leader: #{c['number']} {c['name']} ({label(c)})",
                            {"position": 1}))

        # 6) the rest of the field — the current field car (if any) plus the
        #    one with the least screen time, so field shots can follow each
        #    other instead of bouncing back to the leader every time
        rest = [c for c in live if not c.get("on_pit") and pos.get(c["idx"], 1) > 1]
        picks = []
        if cur and cur.kind == "field" and cur.car["idx"] in by_idx:
            picks.append(by_idx[cur.car["idx"]])
        pool = [c for c in rest if not picks or c["idx"] != picks[0]["idx"]]
        if pool:
            oldest = min(self.last_shown.get(c["idx"], -1e9) for c in pool)
            picks.append(self.rng.choice([c for c in pool if self.last_shown.get(c["idx"], -1e9) == oldest]))
        for c in picks:
            out.append(Shot("field", c, PRIO["field"],
                            f"on track: #{c['number']} {c['name']} ({label(c)})",
                            {"position": pos.get(c["idx"])}))
        return out
