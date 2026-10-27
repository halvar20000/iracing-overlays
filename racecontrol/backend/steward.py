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
* QUALI AM / PRO (GT3 WCT, ``quali_split``) - at the start of a qualifying
  ``quali_am_text`` to everybody, after ``quali_split_min`` minutes
  ``quali_pro_text``.
* BLUE FLAG - a car about to be lapped with the faster car <= ``blue_gap_s``
  behind it gets ``/all #14 BLUE FLAG - let #7 ... pass`` (again after
  ``blue_repeat_s`` if it still holds him up).

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
    "fcy_enabled": True, "fcy_mode": "auto", "fcy_incs": 10,
    "fcy_window_s": 10, "fcy_cooldown_s": 180, "fcy_laps": 0,
    "quali_split": False, "quali_split_min": 15,
    "quali_am_text": "QUALIFYING: THE FIRST 15 MINUTES ARE RESERVED FOR "
                     "AM DRIVERS",
    "quali_pro_text": "AM QUALIFYING FINISHED - PRO QUALIFYING STARTED",
    "blue_enabled": True, "blue_gap_s": 1.0, "blue_repeat_s": 20,
}
_BOOL = ("enabled", "fcy_enabled", "quali_split", "blue_enabled")
_INT = ("dt_first", "dt_every", "fcy_incs", "fcy_window_s",
        "fcy_cooldown_s", "fcy_laps", "quali_split_min", "blue_repeat_s")
_FLOAT = ("blue_gap_s",)
_TEXT = ("quali_am_text", "quali_pro_text")


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


def clean(cfg: dict) -> dict:
    out = dict(DEFAULT_CONFIG)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG})
    for k in _BOOL:
        out[k] = bool(out[k])
    for k, conv in [(k, int) for k in _INT] + [(k, float) for k in _FLOAT]:
        try:
            out[k] = max(0, conv(float(out[k])))
        except (TypeError, ValueError):
            out[k] = DEFAULT_CONFIG[k]
    for k in _TEXT:
        out[k] = " ".join(str(out[k] or "").split())[:200]
    if out["fcy_mode"] not in ("ask", "auto"):
        out["fcy_mode"] = "auto"
    out["fcy_incs"] = max(1, out["fcy_incs"])
    out["fcy_window_s"] = max(1, out["fcy_window_s"])
    out["fcy_laps"] = min(20, max(0, out["fcy_laps"]))
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
        self._quali_sent: set[str] = set()
        self._blue: dict[tuple[int, int], float] = {}   # (slow, fast) -> t

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
        if rules.get("quali_split"):
            out += self._quali(state, rules)
        if rules.get("blue_enabled"):
            out += self._blue_flags(state, rules)
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

    # GT3 WCT qualifying: AM first, then PRO ----------------------------------

    def _quali(self, state, rules: dict) -> list[dict]:
        if not _is_quali(state):
            return []
        elapsed = state.session_time
        split = float(rules.get("quali_split_min") or 15) * 60
        out = []
        tag = f"{state.session_key}:{state.session_num}"
        # the start message only at the start (a restart of iCAS mid-quali
        # must not announce it again)
        if elapsed < 180 and elapsed < split and tag + ":am" not in self._quali_sent:
            self._quali_sent.add(tag + ":am")
            out.append({"type": "quali", "phase": "am",
                        "text": rules.get("quali_am_text", "")})
        if split <= elapsed < split + 120 and tag + ":pro" not in self._quali_sent:
            self._quali_sent.add(tag + ":pro")
            out.append({"type": "quali", "phase": "pro",
                        "text": rules.get("quali_pro_text", "")})
        return [a for a in out if a["text"]]

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
