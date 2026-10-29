"""Auto Steward (step 3c of iCASControl).

Four rules, all switched by ``enabled`` (header AUTO STEWARD) and set in the
Rules dialog:

* INCIDENT LIMIT - the first drive-through is due at ``dt_first`` points
  (15x), then one more at every further ``dt_every`` (21x, 27x ...).  Only a
  highlighted log line: the league's iRacing settings give the penalty
  itself.  (RaceState._auto_steward, the level is autosaved per car.)
* FULL COURSE CAUTION - ``fcy_incs`` incident points or more (all cars
  together, e.g. 10x) within ``fcy_window_s`` seconds (e.g. 10) in a race
  under green.  ``fcy_mode`` "auto" (default) = !yellow goes out at once,
  "ask" = a log line with a DEPLOY button.  ``fcy_laps`` > 0: once the
  caution is out, ``!pacelaps N`` sets its length (0 = iRacing decides).
* SHORT CAUTION (``fcy_short``, 0.1.18 - Le Mans): for EVERY caution (auto,
  button, iRacing's own) - the lap it comes out = lap 0; after
  ``fcy_pit_open_s`` s ``!pitopen``; when the leader starts lap 1
  ``!pacelaps fcy_short_laps`` (1 = pace car in at the end of that lap) and
  ``!waveby`` for every car a full lap behind its class leader
  (``fcy_wave`` "class") or the overall leader ("overall"), "off" = none.
  ``fcy_lineup_adj`` (-1, 0 = off; 0.1.26): when the leader has caught the
  pace car (within LINEUP_M for LINEUP_HOLD_S) in that lap 1, after the
  !pacelaps, ``!pacelaps -1`` once more - at Le Mans (29.10.) the pace car
  stayed out 2 laps after !pacelaps 1 (iRacing seems to count from the
  line-up).  Caught already in lap 0 / later = only a log line.
  While on, ``fcy_laps`` is not used.
* SEQUENCER (``seq_enabled`` + ``sequence``, like iRaceControl's) - RC
  messages to everybody at a session time, e.g. GT3 WCT quali 00:00:01
  "Start Qualifying AM Klasse", 00:15:00 "Ende Qualifying AM Klasse /
  Start Qualifying PRO Klasse".  A step is sent once, and only within 2 min
  of its time (iCAS started later does not announce old steps).
* BLUE FLAG RULE (``cb_enabled``, 0.1.17, IEC - multiclass + lapping): a
  car gets a private ``cb_text`` when a car of a FASTER CLASS (iRacing's
  CarClassEstLapTime, else the class's best laps) or a car LAPPING him is
  within ``cb_gap_s`` behind him on track; again every ``cb_every_s`` (30)
  while he has not let it past.  Not let past after one of HIS laps (at most
  ``cb_dt_max_s`` 180 s - Le Mans) -> DRIVE THROUGH + ``cb_dt_text`` to all.
  Exception: a car that UNLAPS itself (passes a car that is a lap or more
  ahead of it in the race) is not blue-flagged by that car for
  ``cb_grace_s`` (120) s - its time to pull away by ``cb_gap_s``.  Replaces
  iRacing's-blue-flag rule and the own hint below while it is on.
* BLUE FLAG (own gap check, off by default since 0.1.11) - a car about to be
  lapped with the faster car <= ``blue_gap_s`` behind it gets a private
  ``BLUE FLAG - let #7 ... pass`` (again after ``blue_repeat_s``).

GT3 WCT's iRaceControl ruleset (Thomas's screenshot, 2026-10-27), race only:

* iRacing's BLUE flag shown to a car for ``bf_warn_s`` (20) s -> private
  ``bf_warn_text``; for ``bf_dt_s`` (30) s -> DRIVE THROUGH (!black #N D) +
  ``bf_dt_text`` to everybody.  Once per blue-flag period.
* Fast repairs used >= ``fr_used`` (1) -> private ``fr_text``, once per car.
* MEATBALL (repair flag) -> ``meatball_text`` to everybody.
* Full course caution: not in the last ``fcy_end_protect_min`` (5) minutes.

* PRO BEHIND (GT3 WCT, from CLS Pro/Am): an AM driver gets ONE private
  ``pro_text`` when a PRO car is within ``pro_gap_s`` behind him on track
  (same race, not lapping him - that is the blue flag), from lap
  ``pro_from_lap`` on, not under caution.  Once per AM / PRO pair and race:
  the AM may well be the faster one, so it is never repeated.  Only in a
  WCT field (backend/proam.py); every other league has no PRO / AM.

Texts take %cnr% (car number) and %dname% (driver), like iRaceControl;
the PRO-behind text also %pcnr% / %pdname% (the PRO car).

The chat lines only go out while the header's iRACING CHAT is ON.

Config: racecontrol/data/steward_config.json (gitignored, updater-protected).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_PATH = DATA_DIR / "steward_config.json"
DEFAULT_CONFIG = {
    "enabled": True,
    "dt_first": 15, "dt_every": 6,
    "fcy_enabled": True, "fcy_mode": "auto", "fcy_incs": 40,
    "fcy_window_s": 15, "fcy_cooldown_s": 180, "fcy_laps": 0,
    "fcy_end_protect_min": 5,
    # short caution: pits open, 1 lap behind the pace car, lapped cars waved
    # !pitopen OFF by default (2026-10-29: iRacing answered "Cannot re-open
    # the pit lanes when already under caution" - it opens them itself)
    "fcy_short": False, "fcy_short_laps": 1, "fcy_pitopen": False,
    "fcy_pit_open_s": 5,
    "fcy_wave": "class", "fcy_lineup_adj": -1,
    # sequencer (like iRaceControl's): an RC message to everybody at a
    # session time; default = the GT3 WCT quali AM / PRO split
    "seq_enabled": False,
    "sequence": [
        {"session": "quali", "at": 1, "text": "Start Qualifying AM Klasse"},
        {"session": "quali", "at": 900,
         "text": "Ende Qualifying AM Klasse / Start Qualifying PRO Klasse"},
    ],
    "blue_enabled": False, "blue_gap_s": 1.0, "blue_repeat_s": 20,
    "bf_warn_s": 20, "bf_warn_text": "BLUE FLAG - LET PASS FASTER CAR",
    "bf_dt_s": 30, "bf_dt_text": "#%cnr% (%dname%) - DRIVE THROUGH FOR BLUE FLAG",
    "fr_used": 1, "fr_text": "YOU'VE USED UP ALL YOUR FASTREPAIR",
    "meatball_enabled": True, "meatball_text": "#%cnr% (%dname%) - HAS A MEATBALL FLAG",
    "pro_enabled": True, "pro_gap_s": 1.0, "pro_from_lap": 3,
    "pro_text": "PRO CAR BEHIND YOU - #%pcnr% %pdname%",
    # blue flag rule (multiclass + lapping, IEC)
    "cb_enabled": False, "cb_gap_s": 1.0, "cb_every_s": 30, "cb_from_lap": 2,
    "cb_dt": True, "cb_dt_max_s": 180, "cb_grace_s": 120,
    "cb_text": "BLUE FLAG - LET #%fcnr% %fdname% PASS",
    "cb_dt_text": "#%cnr% (%dname%) - DRIVE THROUGH FOR IGNORING BLUE FLAGS",
}
_BOOL = ("enabled", "fcy_enabled", "seq_enabled", "blue_enabled",
         "meatball_enabled", "pro_enabled", "cb_enabled", "cb_dt", "fcy_short",
         "fcy_pitopen")
_INT = ("dt_first", "dt_every", "fcy_incs", "fcy_window_s",
        "fcy_cooldown_s", "fcy_laps", "fcy_end_protect_min",
        "blue_repeat_s", "bf_warn_s", "bf_dt_s", "fr_used", "pro_from_lap",
        "cb_every_s", "cb_from_lap", "cb_dt_max_s", "cb_grace_s",
        "fcy_short_laps", "fcy_pit_open_s", "fcy_lineup_adj")
_SIGNED = ("fcy_lineup_adj",)
_FLOAT = ("blue_gap_s", "pro_gap_s", "cb_gap_s")
_TEXT = ("bf_warn_text", "bf_dt_text",
         "fr_text", "meatball_text", "pro_text", "cb_text", "cb_dt_text")
LINEUP_M = 250.0          # leader this close behind the pace car = caught it
LINEUP_HOLD_S = 5.0
SEQ_SESSIONS = ("quali", "race", "practice", "warmup", "any")
SEQ_MAX = 30
SEQ_LATE_S = 120        # a step is still sent this long after its time
FLAG_BLUE = 0x00000020          # CarIdxSessionFlags
FLAG_REPAIR = 0x00100000        # the meatball
# blue flag rule
CB_CLASS_MARGIN = 0.01   # a class >= 1 % quicker (estimated lap) is "faster"
CB_RELEASE = 3.0         # x cb_gap_s further back = not held up (this frame)
CB_RELEASE_S = 10.0      # ... that long, or a pass, ends the blue period
CB_DT_GAP = 2.0          # x cb_gap_s: the DT needs him still this close
CB_PASS_PCT = 0.02       # pairs this close on track are watched for passes


def fill(text: str, car) -> str:
    """iRaceControl placeholders: %cnr% = car number, %dname% = driver,
    %tname% = team (team events; else the driver)."""
    return (str(text or "").replace("%cnr%", str(car.number))
            .replace("%dname%", str(car.driver))
            .replace("%tname%", str(getattr(car, "team", "") or car.driver)))


def fill_pro(text: str, am, pro) -> str:
    return (fill(text, am).replace("%pcnr%", str(pro.number))
            .replace("%pdname%", str(pro.driver)))


def fill_fast(text: str, car, fast) -> str:
    """Blue flag texts: %cnr% / %dname% = the car to let past the faster
    one, %fcnr% / %fdname% = the faster car."""
    return fill(str(text or "").replace("%fcnr%", str(fast.number))
                .replace("%fdname%", str(fast.driver))
                .replace("%ftname%", str(getattr(fast, "team", "") or fast.driver)), car)


def class_key(car):
    return getattr(car, "class_id", 0) or car.car_class


def class_pace(cars) -> dict:
    """Class -> lap time to compare classes: iRacing's CarClassEstLapTime,
    else the median best lap of the class's cars."""
    est: dict = {}
    bests: dict = {}
    for c in cars:
        k = class_key(c)
        e = float(getattr(c, "class_est_lap", 0) or 0)
        if e > 20:
            est[k] = e
        if c.best_lap > 0:
            bests.setdefault(k, []).append(c.best_lap)
    out = {}
    for k, laps in bests.items():
        laps.sort()
        out[k] = laps[len(laps) // 2]
    out.update(est)
    return out


def faster_class(fast, slow, pace: dict) -> bool:
    a, b = pace.get(class_key(fast)), pace.get(class_key(slow))
    return bool(class_key(fast) != class_key(slow) and a and b
                and a < b * (1 - CB_CLASS_MARGIN))


def level(total: int, rules: dict) -> int:
    """How many drive-throughs ``total`` incident points have earned."""
    first = int(rules.get("dt_first") or 0)
    every = int(rules.get("dt_every") or 0)
    if first <= 0 or total < first:
        return 0
    if every <= 0:
        return 1
    return 1 + (total - first) // every


def threshold(n: int, rules: dict) -> int:
    """The incident total at which drive-through number ``n`` is due."""
    return int(rules["dt_first"]) + (n - 1) * int(rules.get("dt_every") or 0)


def describe(rules: dict) -> str:
    first, every = int(rules.get("dt_first") or 0), int(rules.get("dt_every") or 0)
    return f"limit {first}x, then every {every}x" if every > 0 \
        else f"limit {first}x"


def parse_time(v) -> Optional[int]:
    """Seconds from 90 / "90" / "1:30" / "00:15:00"; None if unusable."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v) if v >= 0 else None
    parts = str(v or "").strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if not 1 <= len(nums) <= 3 or any(n < 0 for n in nums):
        return None
    secs = 0.0
    for n in nums:
        secs = secs * 60 + n
    return int(secs)


def fmt_time(secs: int) -> str:
    return f"{secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}"


def clean_sequence(steps) -> list[dict]:
    out = []
    for st in steps if isinstance(steps, list) else []:
        if not isinstance(st, dict):
            continue
        at = parse_time(st.get("at"))
        text = " ".join(str(st.get("text") or "").split())[:200]
        session = str(st.get("session") or "quali").lower()
        if at is None or not text:
            continue
        out.append({"session": session if session in SEQ_SESSIONS else "quali",
                    "at": at, "text": text})
    out.sort(key=lambda st: (SEQ_SESSIONS.index(st["session"]), st["at"]))
    return out[:SEQ_MAX]


def _migrate(cfg: dict) -> dict:
    """0.1.5 - 0.1.11 had a fixed quali AM / PRO switch -> two steps."""
    if "sequence" in cfg or not any(k.startswith("quali_") for k in cfg):
        return cfg
    cfg = dict(cfg)
    old = {"quali_am_text": "QUALIFYING: THE FIRST 15 MINUTES ARE RESERVED "
                            "FOR AM DRIVERS",
           "quali_pro_text": "AM QUALIFYING FINISHED - PRO QUALIFYING STARTED"}
    if all(cfg.get(k, v) == v for k, v in old.items()) \
            and (parse_time(cfg.get("quali_split_min")) or 15) == 15:
        cfg["seq_enabled"] = bool(cfg.get("quali_split"))
        return cfg               # untouched old defaults -> the new defaults
    split = parse_time(cfg.get("quali_split_min")) or 15
    steps = [{"session": "quali", "at": 1, "text": cfg.get("quali_am_text")},
             {"session": "quali", "at": split * 60,
              "text": cfg.get("quali_pro_text")}]
    steps = [st for st in steps if st["text"]]
    if steps:
        cfg["sequence"] = steps
    cfg.setdefault("seq_enabled", bool(cfg.get("quali_split")))
    return cfg


def clean(cfg: dict) -> dict:
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in _migrate(cfg or {}).items()
                if k in DEFAULT_CONFIG})
    for k in _BOOL:
        out[k] = bool(out[k])
    for k, conv in [(k, int) for k in _INT] + [(k, float) for k in _FLOAT]:
        try:
            out[k] = conv(float(out[k])) if k in _SIGNED else max(0, conv(float(out[k])))
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    for k in _TEXT:
        out[k] = " ".join(str(out[k] or "").split())[:200]
    out["sequence"] = clean_sequence(out["sequence"])
    if out["fcy_mode"] not in ("ask", "auto"):
        out["fcy_mode"] = "auto"
    out["fcy_incs"] = max(1, out["fcy_incs"])
    out["fcy_window_s"] = max(1, out["fcy_window_s"])
    out["fcy_laps"] = min(20, max(0, out["fcy_laps"]))
    out["fcy_short_laps"] = min(20, max(1, out["fcy_short_laps"]))
    out["fcy_lineup_adj"] = min(5, max(-5, out["fcy_lineup_adj"]))
    if out["fcy_wave"] not in ("class", "overall", "off"):
        out["fcy_wave"] = "class"
    return out


def load_config(path: Optional[Path] = None) -> dict:
    path = path or CONFIG_PATH
    try:
        return clean(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[steward] {path.name} unreadable: {exc}", file=sys.stderr)
    return clean({})


def save_config(cfg: dict, path: Optional[Path] = None) -> None:
    path = path or CONFIG_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(clean(cfg), indent=1), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        print(f"[steward] could not save {path.name}: {exc}", file=sys.stderr)


# -- the rules that act during a session ---------------------------------------

def _is_race(state) -> bool:
    return "RACE" in str(state.session_type).upper()


def _is_quali(state) -> bool:
    return "QUALIF" in str(state.session_type).upper()


def session_kind(state) -> str:
    t = str(state.session_type).upper()
    for word, kind in (("QUALIF", "quali"), ("RACE", "race"),
                       ("WARM", "warmup"), ("PRACT", "practice")):
        if word in t:
            return kind
    return ""


def _caution_out(state) -> bool:
    """Full course caution (incl. its one-to-green lap).  A LOCAL yellow is
    not one - counting it kept iCAS on caution after the restart."""
    return any(f in ("caution", "one_to_green") for f in (state.flags or []))


def _racing_car(car) -> bool:
    return (car._seen and not car.on_pit_road and not car.finished
            and car.surface not in ("NotInWorld", "InPitStall"))


class AutoSteward:
    """Looks at the RaceState after every frame and returns what should be
    done - the engine logs it and sends the chat lines.  Kept by the engine
    (the RaceState is replaced on a source switch); ``reset`` on a new
    session."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._session = None
        self._fcy_block_until = -1.0      # session time
        self._seq_sent: set[int] = set()
        self._blue: dict[tuple[int, int], float] = {}   # (slow, fast) -> t
        self._bf_since: dict[int, float] = {}   # car -> blue flag shown since
        self._bf_done: dict[int, set] = {}      # car -> {"warn", "dt"} this period
        self._fr_seen: dict[int, int] = {}      # car -> fast repairs used
        self._fr_sent: set[int] = set()
        self._meatball: dict[int, bool] = {}
        self._pro_sent: set[tuple[int, int]] = set()    # (AM, PRO) told
        # blue flag rule: (slow, fast) -> blue period
        self._cb: dict[tuple[int, int], dict] = {}
        self._cb_grace: dict[tuple[int, int], float] = {}  # (unlapper, car) -> until
        self._cb_order: dict[tuple[int, int], int] = {}    # close pairs: who is ahead
        self._cb_dt_at: dict[int, float] = {}              # car -> last blue DT
        self._sc: Optional[dict] = None     # short caution in progress
        self._near_t: Optional[float] = None  # leader near the pace car since

    def check(self, state, rules: dict) -> list[dict]:
        key = (state.session_key, state.session_num)
        if key != self._session:
            self.reset()
            self._session = key
        if not rules.get("enabled") or not state.connected or state.in_replay:
            return []
        out: list[dict] = []
        if rules.get("fcy_enabled"):
            out += self._fcy(state, rules)
        if rules.get("fcy_short"):
            out += self._short_caution(state, rules)
        if rules.get("seq_enabled"):
            out += self._sequence(state, rules)
        if rules.get("cb_enabled"):
            out += self._blue_rule(state, rules)     # replaces the two below
        else:
            if rules.get("blue_enabled"):
                out += self._blue_flags(state, rules)
            out += self._iracing_blue(state, rules)
        out += self._fast_repairs(state, rules)
        out += self._meatballs(state, rules)
        if rules.get("pro_enabled"):
            out += self._pro_behind(state, rules)
        return out

    # GT3 WCT ruleset: iRacing's own blue flag, fast repairs, meatball ----------

    def _live_cars(self, state) -> list:
        live = getattr(state, "frame_cars", None)
        return [c for c in state.cars.values() if c._seen
                and (live is None or c.car_idx in live)]

    def _iracing_blue(self, state, rules: dict) -> list[dict]:
        warn_s, dt_s = int(rules.get("bf_warn_s") or 0), int(rules.get("bf_dt_s") or 0)
        racing = (_is_race(state) and state.session_state == "RACING"
                  and not _caution_out(state))
        now = state.session_time
        out = []
        for car in self._live_cars(state):
            blue = bool(car.car_flags & FLAG_BLUE) and racing \
                and not car.on_pit_road
            if not blue:
                self._bf_since.pop(car.car_idx, None)
                self._bf_done.pop(car.car_idx, None)
                continue
            since = self._bf_since.setdefault(car.car_idx, now)
            done = self._bf_done.setdefault(car.car_idx, set())
            held = now - since
            if dt_s and held >= dt_s and "dt" not in done:
                done.update(("dt", "warn"))
                out.append({"type": "blue_dt", "car": car, "held": held,
                            "text": fill(rules.get("bf_dt_text"), car)})
            elif warn_s and held >= warn_s and "warn" not in done:
                done.add("warn")
                out.append({"type": "blue_warn", "car": car, "held": held,
                            "text": fill(rules.get("bf_warn_text"), car)})
        return out

    def _fast_repairs(self, state, rules: dict) -> list[dict]:
        limit = int(rules.get("fr_used") or 0)
        out = []
        for car in self._live_cars(state):
            used = int(car.fast_repairs_used)
            if used < 0:
                continue
            before = self._fr_seen.get(car.car_idx)
            self._fr_seen[car.car_idx] = used
            # first sighting = baseline (iCAS started mid-race): silent
            if (limit and before is not None and before < limit <= used
                    and car.car_idx not in self._fr_sent
                    and _is_race(state)):
                self._fr_sent.add(car.car_idx)
                out.append({"type": "fast_repair", "car": car, "used": used,
                            "text": fill(rules.get("fr_text"), car)})
        return out

    def _meatballs(self, state, rules: dict) -> list[dict]:
        out = []
        for car in self._live_cars(state):
            on = bool(car.car_flags & FLAG_REPAIR)
            before = self._meatball.get(car.car_idx)
            self._meatball[car.car_idx] = on
            if (on and before is False and rules.get("meatball_enabled")
                    and _is_race(state)):
                out.append({"type": "meatball", "car": car,
                            "text": fill(rules.get("meatball_text"), car)})
        return out

    def _pro_behind(self, state, rules: dict) -> list[dict]:
        if not _is_race(state) or state.session_state != "RACING" \
                or _caution_out(state):
            return []
        gap_max = float(rules.get("pro_gap_s") or 1.0)
        from_lap = int(rules.get("pro_from_lap") or 0)
        length_m = float(state.session_length_km or 0) * 1000
        cars = [c for c in self._live_cars(state) if _racing_car(c)]
        pros = [c for c in cars if getattr(c, "proam", "") == "PRO"]
        out = []
        for am in cars:
            # the opening laps: the field is packed, everybody is "behind"
            if getattr(am, "proam", "") != "AM" or am.laps_completed + 1 < from_lap:
                continue
            for pro in pros:
                if (am.car_idx, pro.car_idx) in self._pro_sent:
                    continue
                # a PRO a lap (or more) ahead is lapping him - blue flag rule
                if abs((pro.lap + pro.lap_dist_pct)
                       - (am.lap + am.lap_dist_pct)) >= 0.5:
                    continue
                d = (am.lap_dist_pct - pro.lap_dist_pct) % 1.0
                if d >= 0.5:
                    continue                    # the PRO is ahead
                lap_t = pro.best_lap or pro.last_lap
                if not lap_t and pro.speed_ms > 10 and length_m:
                    lap_t = length_m / pro.speed_ms
                if not lap_t or d * lap_t > gap_max:
                    continue
                self._pro_sent.add((am.car_idx, pro.car_idx))
                out.append({"type": "pro_behind", "car": am, "pro": pro,
                            "gap": d * lap_t,
                            "text": fill_pro(rules.get("pro_text"), am, pro)})
                break                           # one line per frame and AM
        return out

    # full course caution ----------------------------------------------------

    def _fcy(self, state, rules: dict) -> list[dict]:
        now = state.session_time
        window = float(rules.get("fcy_window_s") or 20)
        if not _is_race(state) or state.session_state != "RACING":
            return []
        if _caution_out(state):
            # what happened before / under this caution must not call the
            # next one right after the restart
            self._fcy_block_until = now + window
            return []
        if now < self._fcy_block_until:
            return []
        protect = float(rules.get("fcy_end_protect_min") or 0) * 60
        remain = float(getattr(state, "session_time_remain", 0) or 0)
        if protect and 0 < remain <= protect:
            return []                 # the last minutes: no caution any more
        # official incident points (a detection gets its points onto the
        # same line when they arrive)
        recent = [e for e in state.events
                  if e.is_incident and e.car_idx >= 0 and e.inc_points > 0
                  and e.session_num == state.session_num
                  and 0 <= now - e.sim_time <= window]
        points = sum(e.inc_points for e in recent)
        if points < int(rules.get("fcy_incs") or 10):
            return []
        # what triggered it must not count again
        self._fcy_block_until = now + max(
            window, float(rules.get("fcy_cooldown_s") or 0))
        cars = {e.car_idx for e in recent}
        nums = sorted({state.cars[i].number for i in cars if i in state.cars},
                      key=lambda n: (len(n), n))
        return [{"type": "caution", "points": points, "cars": len(cars),
                 "window": window, "numbers": nums,
                 "mode": rules.get("fcy_mode", "auto")}]

    # short caution: pits open, one lap behind the pace car, wave the lapped --

    def _short_caution(self, state, rules: dict) -> list[dict]:
        if not _is_race(state) or state.session_state != "RACING" \
                or not _caution_out(state):
            self._sc = None
            if getattr(state, "short_caution", None) is not None:
                state.short_caution = None
            return []
        if self._sc is None:
            # iCAS restarted mid-caution (2026-10-29 Le Mans: twice, and each
            # time !pitopen / !pacelaps / !waveby went out again with a new
            # lap 0) -> go on with the saved one of this session
            saved = getattr(state, "short_caution", None)
            if (isinstance(saved, dict) and saved.get("session_num") == state.session_num
                    and saved.get("session_key") == state.session_key):
                self._sc = {k: saved[k] for k in ("t", "lap0", "pits", "lap1")
                            if k in saved}
                if len(self._sc) < 4:
                    self._sc = None
                else:
                    self._sc["lineup"] = bool(saved.get("lineup"))
        out = self._short_caution_step(state, rules)
        if self._sc is not None and hasattr(state, "short_caution"):
            state.short_caution = dict(self._sc, session_num=state.session_num,
                                       session_key=state.session_key)
        return out

    def _short_caution_step(self, state, rules: dict) -> list[dict]:
        now = state.session_time
        cars = [c for c in self._live_cars(state) if not c.finished
                and c.surface != "NotInWorld"]
        if not cars:
            return []
        prog = {c.car_idx: c.lap + c.lap_dist_pct for c in cars}
        leader = max(cars, key=lambda c: prog[c.car_idx])
        out = []
        if self._sc is None:
            # the lap the caution came out = lap 0
            self._sc = {"t": now, "lap0": leader.lap, "pits": False,
                        "lap1": False, "lineup": False}
            out.append({"type": "caution_short", "lap0": leader.lap,
                        "laps": int(rules.get("fcy_short_laps") or 1)})
        sc = self._sc
        if not sc["pits"] and now - sc["t"] >= float(rules.get("fcy_pit_open_s") or 0):
            sc["pits"] = True
            if rules.get("fcy_pitopen"):
                out.append({"type": "caution_pits"})
        if not sc["lap1"] and leader.lap > sc["lap0"]:
            sc["lap1"] = True
            out.append({"type": "caution_laps",
                        "n": int(rules.get("fcy_short_laps") or 1),
                        "leader": leader})
            mode = rules.get("fcy_wave", "class")
            if mode != "off":
                best: dict = {}
                for c in cars:
                    k = class_key(c) if mode == "class" else 0
                    if k not in best or prog[c.car_idx] > prog[best[k].car_idx]:
                        best[k] = c
                wave = [c for c in cars if not c.on_pit_road
                        and prog[best[class_key(c) if mode == "class" else 0].car_idx]
                        - prog[c.car_idx] >= 1.0]
                wave.sort(key=lambda c: -prog[c.car_idx])
                if wave:
                    out.append({"type": "caution_wave", "cars": wave,
                                "mode": mode})
        out += self._lineup(state, rules, leader)
        return out

    def _lineup(self, state, rules: dict, leader) -> list[dict]:
        """The leader has caught the pace car: ``fcy_lineup_adj`` once."""
        sc = self._sc
        adj = int(rules.get("fcy_lineup_adj") or 0)
        pace = float(getattr(state, "pace_car_prog", -1.0))
        length_m = float(state.session_length_km or 0) * 1000
        if not adj or sc.get("lineup") or pace < 0 or not length_m \
                or leader.on_pit_road:
            self._near_t = None
            return []
        gap = ((pace - leader.lap_dist_pct) % 1.0) * length_m
        if gap > LINEUP_M:
            self._near_t = None
            return []
        if self._near_t is None:
            self._near_t = state.session_time
        if state.session_time - self._near_t < LINEUP_HOLD_S:
            return []
        sc["lineup"] = True
        send = sc["lap1"] and leader.lap == sc["lap0"] + 1
        return [{"type": "caution_lineup", "n": adj, "send": send,
                 "gap": gap, "leader": leader,
                 "lap": leader.lap - sc["lap0"]}]

    # sequencer: timed RC messages -------------------------------------------

    def _sequence(self, state, rules: dict) -> list[dict]:
        kind = session_kind(state)
        if not kind:
            return []
        elapsed = state.session_time
        out = []
        for n, st in enumerate(rules.get("sequence") or []):
            if st["session"] not in (kind, "any") or n in self._seq_sent:
                continue
            # only close to its time: iCAS started (or restarted) later in
            # the session must not announce old steps
            if st["at"] <= elapsed < st["at"] + SEQ_LATE_S:
                self._seq_sent.add(n)
                out.append({"type": "sequence", "at": st["at"],
                            "session": kind, "text": st["text"]})
        return out

    # blue flag rule (multiclass + lapping) ----------------------------------

    def _blue_rule(self, state, rules: dict) -> list[dict]:
        if not _is_race(state) or state.session_state != "RACING":
            return []
        if _caution_out(state):
            # order frozen behind the pace car: nobody is held up
            self._cb.clear()
            self._cb_order.clear()
            return []
        now = state.session_time
        gap_max = float(rules.get("cb_gap_s") or 1.0)
        every = float(rules.get("cb_every_s") or 0)
        from_lap = int(rules.get("cb_from_lap") or 0)
        grace_s = float(rules.get("cb_grace_s") or 0)
        dt_max = float(rules.get("cb_dt_max_s") or 180)
        length_m = float(state.session_length_km or 0) * 1000
        cars = [c for c in self._live_cars(state) if _racing_car(c)]
        idx = {c.car_idx for c in cars}
        pace = class_pace(cars)
        prog = {c.car_idx: c.lap + c.lap_dist_pct for c in cars}

        def lap_time(c):
            t = c.best_lap or c.last_lap or float(getattr(c, "class_est_lap", 0) or 0)
            if not t and c.speed_ms > 10 and length_m:
                t = length_m / c.speed_ms
            return t

        out: list[dict] = []
        # 1. passes: a car passing one that is a lap (or more) ahead of it in
        #    the race UNLAPS itself -> time to pull away before any blue flag
        for a in cars:
            for b in cars:
                if a.car_idx >= b.car_idx:
                    continue
                pair = (a.car_idx, b.car_idx)
                rel = ((a.lap_dist_pct - b.lap_dist_pct + 0.5) % 1.0) - 0.5
                if abs(rel) > CB_PASS_PCT:
                    self._cb_order.pop(pair, None)
                    continue
                sign = 1 if rel > 0 else -1          # +1: a ahead of b
                before = self._cb_order.get(pair)
                self._cb_order[pair] = sign
                if before is None or before == sign:
                    continue
                passer, passed = (a, b) if sign > 0 else (b, a)
                if prog[passed.car_idx] - prog[passer.car_idx] >= 0.5 and grace_s:
                    key = (passer.car_idx, passed.car_idx)
                    self._cb_grace[key] = now + grace_s
                    self._cb.pop(key, None)
                    out.append({"type": "blue_unlap", "car": passer,
                                "fast": passed, "grace": grace_s})
        for key in [k for k, until in self._cb_grace.items() if until <= now]:
            del self._cb_grace[key]

        # 2. who is held up by whom
        active: set[tuple[int, int]] = set()
        for slow in cars:
            if slow.lap < from_lap:
                continue
            for fast in cars:
                if fast is slow:
                    continue
                d = (slow.lap_dist_pct - fast.lap_dist_pct) % 1.0
                if d >= 0.5:
                    continue                      # fast is ahead on track
                lapping = prog[fast.car_idx] - prog[slow.car_idx] >= 0.5
                if lapping:
                    why = "lapping"
                elif faster_class(fast, slow, pace):
                    why = "faster class"
                else:
                    continue
                lt = lap_time(fast)
                if not lt:
                    continue
                gap = d * lt
                if gap > gap_max * CB_RELEASE:
                    continue
                pair = (slow.car_idx, fast.car_idx)
                per = self._cb.get(pair)
                if per is None:
                    if gap > gap_max or now < self._cb_grace.get(pair, -1.0):
                        continue
                    lt_slow = lap_time(slow)
                    per = self._cb[pair] = {
                        "start": now, "last": None, "n": 0, "dt": False,
                        "far": None, "why": why,
                        "deadline": min(dt_max, lt_slow) if lt_slow else dt_max}
                active.add(pair)
                per["far"] = None
                if per["dt"]:
                    continue
                held = now - per["start"]
                if (rules.get("cb_dt") and held >= per["deadline"]
                        and gap <= gap_max * CB_DT_GAP):
                    per["dt"] = True
                    last_dt = self._cb_dt_at.get(slow.car_idx)
                    if last_dt is not None and now - last_dt < per["deadline"]:
                        continue                  # one DT for one train of cars
                    self._cb_dt_at[slow.car_idx] = now
                    out.append({"type": "blue_dt", "car": slow, "fast": fast,
                                "held": held, "why": per["why"],
                                "text": fill_fast(rules.get("cb_dt_text"), slow, fast)})
                    continue
                if per["last"] is None or (every and now - per["last"] >= every):
                    per["last"] = now
                    per["n"] += 1
                    out.append({"type": "blue", "car": slow, "fast": fast,
                                "gap": gap, "again": per["n"] > 1,
                                "n": per["n"], "why": per["why"],
                                "deadline": per["deadline"] if rules.get("cb_dt") else 0,
                                "text": fill_fast(rules.get("cb_text"), slow, fast)})
        # a pass (fast car ahead), a car leaving the track / pits, or the
        # fast car dropping back for CB_RELEASE_S ends the period
        for pair, per in list(self._cb.items()):
            if pair in active:
                continue
            s, f = pair
            passed = False
            if s in idx and f in idx:
                sc = next(c for c in cars if c.car_idx == s)
                fc = next(c for c in cars if c.car_idx == f)
                passed = (fc.lap_dist_pct - sc.lap_dist_pct) % 1.0 < 0.1
            if passed or s not in idx or f not in idx:
                del self._cb[pair]
                continue
            per["far"] = per["far"] if per["far"] is not None else now
            if now - per["far"] >= CB_RELEASE_S:
                del self._cb[pair]
        return out

    # blue flags ---------------------------------------------------------------

    def _blue_flags(self, state, rules: dict) -> list[dict]:
        if not _is_race(state) or state.session_state != "RACING" \
                or _caution_out(state):
            return []
        now = state.session_time
        gap_max = float(rules.get("blue_gap_s") or 1.0)
        repeat = float(rules.get("blue_repeat_s") or 0)
        length_m = float(state.session_length_km or 0) * 1000
        live = getattr(state, "frame_cars", None)
        cars = [c for c in state.cars.values() if _racing_car(c)
                and (live is None or c.car_idx in live)]
        out = []
        holding: set[tuple[int, int]] = set()
        for slow in cars:
            best = None
            for fast in cars:
                if fast is slow:
                    continue
                # slow is ahead on the track ...
                d = (slow.lap_dist_pct - fast.lap_dist_pct) % 1.0
                # ... but a lap (or more) behind in the race
                if (fast.lap + fast.lap_dist_pct) - \
                        (slow.lap + slow.lap_dist_pct) < 0.5:
                    continue
                lap_t = fast.best_lap or fast.last_lap
                if not lap_t and fast.speed_ms > 10 and length_m:
                    lap_t = length_m / fast.speed_ms
                if not lap_t:
                    continue
                gap = d * lap_t
                if gap <= 3 * gap_max:
                    holding.add((slow.car_idx, fast.car_idx))
                if gap <= gap_max and (best is None or gap < best[0]):
                    best = (gap, fast)
            if best is None:
                continue
            gap, fast = best
            pair = (slow.car_idx, fast.car_idx)
            last = self._blue.get(pair)
            if last is not None and (not repeat or now - last < repeat):
                continue
            self._blue[pair] = now
            out.append({"type": "blue", "car": slow, "fast": fast,
                        "gap": gap, "again": last is not None})
        # a pass done (or the gap opened again) re-arms the pair
        for pair in list(self._blue):
            if pair not in holding:
                del self._blue[pair]
        return out
