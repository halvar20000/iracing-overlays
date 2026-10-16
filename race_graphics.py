"""
GRAFIK — which overlay to bring up on the iRacing scene while REGIE runs
(2026-10-15, Thomas: "make it varied — the battle graphic, the provisional
Driver of the Day in the last third, the weather at the start").

Pure logic, no OBS / network here: `GraphicsDirector.tick(now, state)` returns
what to do; the dashboard shows / hides the OBS sources and speaks the line.

Rules
  • one graphic at a time, held `secs`, then hidden; at least GAP_S between two
  • never while the dashboard plays its own replay (state["replay"])
  • weather   — race: once, WEATHER_AFTER_S after the green
  • battle    — race: when REGIE's shot on air is a battle and has been for
                BATTLE_AFTER_S; not again within `every_s` (the catch-up
                graphic follows the camera car = the chaser of that battle)
  • dotd      — race: once per mark in `at` (share of the race done, 0.67 =
                the last third); needs a provisional winner from 5013
  • a source OBS does not have is switched off for the session (no retries)

Config: show_config.json "graphics": {"scene": ..., "weather": {"source":
"Wetter", ...}, ...} — merged over DEFAULTS; "enabled": false switches it off.
"""
from __future__ import annotations

import copy

DEFAULTS = {
    "enabled": True,
    "scene": "iRacing",
    "gap_s": 45.0,
    "weather": {"source": "Wetter", "secs": 12.0, "after_s": 40.0},
    # the catch-up graphic needs two clean laps per car ("GATHERING LAP DATA"
    # in lap 1 — video 2026-10-16): not before after_green_s
    "battle": {"source": "Duell", "secs": 10.0, "after_s": 6.0, "every_s": 180.0, "after_green_s": 300.0},
    "dotd": {"source": "Fahrer des Tages", "secs": 14.0, "at": [0.67]},
}


def merge_config(user: dict | None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    for k, v in (user or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


class GraphicsDirector:
    def __init__(self, cfg: dict | None = None):
        self.cfg = merge_config(cfg)
        self.reset()

    def reset(self):
        self.on = None              # {"key", "until"} — the graphic on screen
        self.last_end = -1e9        # when the last graphic went down
        self.green_at = None        # first racing tick of this race
        self.done: set = set()      # once-only things already shown ("weather", "dotd@0.67")
        self.battle_at = -1e9       # last battle graphic
        self.missing: set = set()   # sources OBS does not have
        self.log: list = []

    def source(self, key) -> str | None:
        return (self.cfg.get(key) or {}).get("source") or None

    def mark_missing(self, key):
        self.missing.add(key)

    def tick(self, now: float, state: dict) -> list:
        """state: session ("race" / "quali" / other), racing (bool, green),
        replay (bool), shot (kind of REGIE's shot on air or None),
        shot_since (when that shot started), progress (0..1 of the race or
        None), dotd_ready (bool). Returns [("hide", key)] / [("show", key)]."""
        if not self.cfg.get("enabled", True):
            return []
        out = []
        if self.on and (now >= self.on["until"] or state.get("replay")):
            out.append(("hide", self.on["key"]))
            self.last_end = now
            self.on = None
        if state.get("session") != "race":
            self.green_at = None
            return out
        if state.get("racing") and self.green_at is None:
            self.green_at = now
        if self.on or state.get("replay") or now - self.last_end < float(self.cfg.get("gap_s", 45)):
            return out
        key = self._pick(now, state)
        if key:
            self.on = {"key": key, "until": now + float(self.cfg[key].get("secs", 10))}
            self.log.append({"t": round(now, 1), "key": key})
            out.append(("show", key))
        return out

    def _ok(self, key) -> bool:
        return bool(self.source(key)) and key not in self.missing

    def _pick(self, now, state):
        c = self.cfg
        # weather: once, shortly after the green
        if (self._ok("weather") and "weather" not in self.done and self.green_at is not None
                and now - self.green_at >= float(c["weather"].get("after_s", 40))):
            self.done.add("weather")
            return "weather"
        # provisional Driver of the Day: once per mark (last third …)
        prog = state.get("progress")
        if self._ok("dotd") and prog is not None and state.get("dotd_ready"):
            for mark in sorted(c["dotd"].get("at") or []):
                tag = f"dotd@{mark}"
                if prog >= mark and tag not in self.done:
                    self.done.add(tag)
                    return "dotd"
        # battle: REGIE shows one and has done for a few seconds
        if (self._ok("battle") and state.get("shot") == "battle"
                and self.green_at is not None
                and now - self.green_at >= float(c["battle"].get("after_green_s", 0))
                and state.get("shot_since") is not None
                and now - state["shot_since"] >= float(c["battle"].get("after_s", 6))
                and now - self.battle_at >= float(c["battle"].get("every_s", 180))):
            self.battle_at = now
            return "battle"
        return None


def weather_line(w: dict) -> str | None:
    """Spoken line from the weather overlay's /status."""
    if not w or not w.get("connected"):
        return None
    parts = []
    if w.get("air_temp") is not None:
        parts.append(f"{round(w['air_temp'])} Grad Luft")
    if w.get("track_temp") is not None:
        parts.append(f"{round(w['track_temp'])} Grad auf dem Asphalt")
    if not parts:
        return None
    s = "Ein Blick aufs Wetter: " + " und ".join(parts)
    wet = (w.get("wetness") or "").lower()
    if w.get("declared_wet") or (w.get("precip") or 0) >= 1:
        s += ", die Strecke ist nass."
    elif wet and "dry" not in wet:
        s += ", die Strecke ist feucht."
    else:
        s += ", trocken."
    return s


def dotd_line(d: dict) -> str | None:
    """Spoken line from the DotD overlay's /data (provisional)."""
    w = (d or {}).get("winner") if (d or {}).get("ok") else None
    if not w or not w.get("name"):
        return None
    return (f"Zwischenstand bei der Wahl zum Fahrer des Tages: Im Moment liegt {w['name']} vorne."
            if d.get("provisional") else f"Fahrer des Tages ist {w['name']}.")
