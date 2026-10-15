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
  mode       "race" (default) or "quali" — qualifying logic in quali_director.py
             (fastest driver + flying laps that threaten the front, lap results)
  t_session  session clock (s) — quali lap timing; falls back to `now`
"""

from __future__ import annotations

import random
import time
from collections import deque

from quali_director import QualiState

# --- pacing (seconds) ---------------------------------------------------------
MIN_SHOT = 8.0           # a shot is never cut earlier — except for a higher-priority event
MIN_BY_KIND = {          # ...longer for the subjects that need time to read
    "battle": 15.0, "leader": 10.0,
    "attack": 12.0, "pole": 10.0,     # qualifying
}
SWITCH_MARGIN = 10       # after MIN: another subject must be clearly better, not just
                         # "a battle one place further up" (first test: cut every 8 s,
                         # viewers lost the overview — Thomas, 2026-10-14)
GONE_GRACE_S = 3.0       # a battle that breaks up for a moment is not over yet
LEADER_EVERY_S = 120.0   # the leader gets a shot at least this often
CAM_VARY_EVERY = 14.0    # long shots change camera group this often
MAX_SHOT = {             # after this a shot loses its claim, something else gets a turn
    "incident": 10.0, "overtake": 9.0, "battle": 40.0, "pit": 12.0,
    "leader": 16.0, "field": 12.0,
    "attack": 200.0, "pole": 16.0,    # an attack runs to the end of its lap
}
REVISIT_PENALTY_S = 45.0  # a car shown within this window scores lower
MANUAL_PAUSE_S = 20.0     # after an operator click the director keeps its hands off

# --- thresholds ---------------------------------------------------------------
BATTLE_ENTER_S = 1.0     # gap to the same-class car ahead that makes a battle
BATTLE_KEEP_S = 1.5      # ...and keeps the current battle alive (hysteresis)
EVENT_FRESH_S = 4.0      # incidents / overtakes older than this are not cut to

# --- replays (Thomas, 2026-10-14) ---------------------------------------------
# Incidents (contact / spin) are ALWAYS shown, as a REPLAY — unless they were in
# the live picture already. Overtakes: ignored in the first OVERTAKE_SKIP_LAPS
# lap(s) after the green (start chaos), afterwards replayed the same way.
OVERTAKE_SKIP_LAPS = 1.0   # = from lap 2 on (Thomas: "ab Runde 2")
VISIBLE_S = 0.8          # cars this close (s) to the camera car are in the picture
                         # (1.5 counted a pass 1-2 s ahead of a P5 battle on the
                         # Chase cam as "seen" — Thomas wasn't sure he saw it)
SEEN_WINDOW_S = {        # look back this far for "was it on screen" (detection delay:
    "overtake": 10.0,    # an overtake is confirmed ~3 s after the move, a contact
    "collision": 15.0,   # can be reported when the car stops / is towed)
    "lost_control": 10.0,
}
REPLAY_GAP_S = {"incident": 15.0, "overtake": 45.0}   # min. time since the last replay
EVENT_MAX_AGE_S = 20.0   # events older than this (wall clock) are history
SAME_EVENT_S = 90.0      # same pass (same two cars) / same car's contact again within
                         # this = the detector re-reporting it (e.g. after a seek)
REPLAY_HOLD_S = 16.0     # after a replay the shot on air gets a fresh MIN_SHOT
PIT_TOP_N = 5            # pit stops worth showing: top N of a class

# --- base priorities ----------------------------------------------------------
PRIO = {"incident": 100, "overtake": 70, "battle": 60, "pit": 45,
        "leader": 35, "field": 20}
LEADER_DUE_PRIO = 75     # ...by outranking battles (+ SWITCH_MARGIN) once due
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
    "attack":   ["TV1", "Chase", "Cockpit", "TV2", "TV3"],
    "pole":     ["TV1", "Chase", "TV2", "TV3"],
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
        self.missing_since: float | None = None   # current subject not a candidate since
        self.vis_hist: deque = deque()            # (t, cars in the picture)
        self.green_lead: float | None = None      # leader progress at the green
        self.last_replay_at = -1e9
        self.event_keys: dict = {}                # (type, car, passed) -> last time handled
        self.started_at = None                    # first racing tick (leader-due clock)
        self.log: deque = deque(maxlen=60)
        self.kind_cuts: dict[str, int] = {}      # cuts per kind -> camera rotation
        self.mode = "race"
        self.quali = QualiState()

    # -- public ---------------------------------------------------------------
    def reset(self):
        self.current = None
        self.last_shown.clear()
        self.seen_events.clear()
        self.paused_until = 0.0
        self.missing_since = None
        self.started_at = None
        self.vis_hist.clear()
        self.green_lead = None
        self.last_replay_at = -1e9
        self.event_keys.clear()
        self.quali.reset()

    def after_replay(self, resume_at: float):
        """The dashboard is back from a replay; the shot on air (where it
        returned to) counts as starting at `resume_at` — a full MIN_SHOT before
        any cut, CAM_VARY_EVERY before a camera change."""
        if self.current is not None:
            self.current.started = self.current.cam_changed = resume_at
        self.last_replay_at = max(self.last_replay_at, resume_at)

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

    def tick(self, now, cars, groups, cam_idx, events, racing=True, mode="race", t_session=None):
        """Returns a Shot to cut to (with .group set), or None."""
        if mode != self.mode:                    # quali -> race: a fresh start
            self.reset()
            self.current = None
            self.mode = mode
        fresh = self._new_events(events)
        if not racing:
            self.green_lead = None               # next green restarts the lap count
            return None
        live = [c for c in cars if c.get("in_world")]
        if not live:
            return None
        by_idx = {c["idx"]: c for c in live}
        lead = max(c["progress"] for c in live)
        if self.green_lead is None:
            self.green_lead = lead
        self._note_visible(now, cam_idx, by_idx)
        t = now if t_session is None else t_session
        if mode == "quali":
            self.quali.update(now, t, cars, self)    # lap tracking runs even while paused
        if now < self.paused_until:
            return None
        if mode == "quali":                      # no replays in quali
            cands = self.quali.candidates(self, now, t, live, by_idx, fresh, cars)
        else:
            replay, live_events = self._event_replay(now, fresh, by_idx, groups, lead)
            if replay is not None:
                return replay
            cands = self._candidates(now, live, by_idx, live_events)
        cur = self.current
        if cur is not None and cur.car["idx"] not in by_idx:
            cur = self.current = None            # car left the world: re-pick now
        if cur is not None:
            cur.car = by_idx[cur.car["idx"]]
            # both cars of a battle are on screen
            for i in (cur.info.get("pair") or (cur.car["idx"],)):
                self.last_shown[i] = now
        if cur is None:
            best = max(cands, key=lambda s: s.prio) if cands else None
            return self._cut(now, best, groups) if best else None

        age = now - cur.started
        still = next((s for s in cands if s.key() == cur.key()), None)
        if still is None and cur.kind == "battle":
            # gap jitter: keep the battle for GONE_GRACE_S before calling it over
            if self.missing_since is None:
                self.missing_since = now
            if now - self.missing_since < GONE_GRACE_S:
                still = Shot(cur.kind, cur.car, cur.prio, cur.reason, cur.info)
        else:
            self.missing_since = None
        others = [s for s in cands if s.key() != cur.key()]
        other = max(others, key=lambda s: s.prio) if others else None
        if age < MIN_BY_KIND.get(cur.kind, MIN_SHOT):
            # measured against the priority the shot was CUT with: a subject
            # that looks weaker for a moment (S/F line glitch) is no excuse
            # to cut after 3 s
            # only EVENTS interrupt (a spin, a pass) — a due leader or a new
            # battle waits for the shot to run its course
            if (other and other.kind in ("incident", "overtake")
                    and other.prio >= max(cur.prio, cur.cut_prio) + INTERRUPT_MARGIN):
                return self._cut(now, other, groups)
        elif still is None or age >= MAX_SHOT.get(cur.kind, 15.0):
            # subject over (battle broke up, event shown) or shown long
            # enough: give the screen to the best OTHER subject
            if other:
                return self._cut(now, other, groups)
            if still is None:
                self.current = None
                return None
        elif other and other.prio >= still.prio + SWITCH_MARGIN:
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

    # -- replays ---------------------------------------------------------------
    def _note_visible(self, now, cam_idx, by_idx):
        """Remember which cars were in the picture: the camera car, both cars
        of the battle on air and anything within VISIBLE_S of the camera car."""
        vis = set()
        cam = by_idx.get(cam_idx)
        if cam is not None:
            est = cam.get("est_lap") if (cam.get("est_lap") or 0) >= 20 else 100.0
            vis = {i for i, c in by_idx.items()
                   if abs(c["progress"] - cam["progress"]) * est <= VISIBLE_S}
            vis.add(cam_idx)
        if self.current is not None:
            vis.update(self.current.info.get("pair") or ())
        self.vis_hist.append((now, frozenset(vis)))
        while self.vis_hist and now - self.vis_hist[0][0] > max(SEEN_WINDOW_S.values()):
            self.vis_hist.popleft()

    def _was_visible(self, now, idxs, window):
        """True if ALL given cars were in the picture together at some point
        of the window — for an overtake both cars, not just one of them."""
        idxs = {i for i in idxs if i is not None}
        return bool(idxs) and any(now - t <= window and idxs <= vis for t, vis in self.vis_hist)

    def _event_replay(self, now, fresh, by_idx, groups, lead):
        """-> (replay Shot or None, events still to be shown LIVE)."""
        live_events = []
        wall = time.time()
        pick = None
        for e in fresh:
            typ = e.get("type")
            if typ not in ("collision", "lost_control", "overtake"):
                continue
            if e.get("wall_clock") and wall - e["wall_clock"] > EVENT_MAX_AGE_S:
                continue                                  # came in while we were paused
            c = by_idx.get(e.get("car_idx"))
            if c is None:
                continue
            ekey = (typ, e.get("car_idx"), e.get("passed_idx"))
            if now - self.event_keys.get(ekey, -1e9) < SAME_EVENT_S:
                continue                                  # already handled
            self.event_keys[ekey] = now
            kind = "overtake" if typ == "overtake" else "incident"
            if kind == "overtake" and lead - self.green_lead < OVERTAKE_SKIP_LAPS:
                continue                                  # start chaos: ignored
            if self._was_visible(now, (e.get("car_idx"), e.get("passed_idx")),
                                 SEEN_WINDOW_S.get(typ, 10.0)):
                self.log.appendleft({"t": round(now, 1), "kind": "seen", "car_idx": c["idx"],
                                     "car_number": c["number"], "name": c["name"], "group": None,
                                     "reason": f"{typ} #{c['number']} {c['name']} was on screen live",
                                     "info": {"event": typ, "passed": e.get("passed_name"),
                                              "passed_number": e.get("passed_number"),
                                              "position": e.get("position")}})
                continue
            if now - self.last_replay_at < REPLAY_GAP_S[kind]:
                if kind == "incident":
                    live_events.append(e)                 # no replay now: show it live
                continue
            # incidents beat overtakes; otherwise the first one
            if pick is None or (kind == "incident" and pick[0] == "overtake"):
                pick = (kind, e, c)
        if pick is None:
            return None, live_events
        kind, e, c = pick
        if e.get("type") == "overtake":
            why = f"replay overtake: #{c['number']} {c['name']} passes #{e.get('passed_number')} {e.get('passed_name')}"
            if e.get("position"):
                why += f" for P{e['position']}"
        else:
            why = f"replay {'contact' if e['type'] == 'collision' else 'spin'}: #{c['number']} {c['name']}"
        shot = Shot("replay", c, 999, why,
                    {"event": e.get("type"), "event_id": e.get("id"),
                     "t_session": e.get("t_session"), "session_num": e.get("session_num"),
                     "passed": e.get("passed_name"), "position": e.get("position")})
        shot.group = self._pick_group("incident", groups, 0)
        if shot.group is None:
            return None, live_events
        shot.started = shot.cam_changed = now
        self.last_replay_at = now
        self.last_shown[c["idx"]] = now
        if self.current is not None:
            # the shot we come back to gets its full length again
            self.current.started = now + REPLAY_HOLD_S
            self.current.cam_changed = now + REPLAY_HOLD_S
        self.log.appendleft({"t": round(now, 1), **shot.as_dict()})
        return shot, live_events

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
        self.missing_since = None
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

        # 2) overtakes: shown as REPLAYS (_event_replay), not cut to live
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

        # 5) class leaders — due again after LEADER_EVERY_S off screen
        if self.started_at is None:
            self.started_at = now
        for cl in classes.values():
            c = cl[0]
            if c.get("on_pit"):
                continue
            seen = self.last_shown.get(c["idx"], self.started_at)
            if cur_key != ("leader", c["idx"]) and now - seen >= LEADER_EVERY_S:
                prio = LEADER_DUE_PRIO
            else:
                prio = PRIO["leader"] - (0 if cur_key == ("leader", c["idx"])
                                         else self._recent_penalty(now, c["idx"]))
            out.append(Shot("leader", c, prio,
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
