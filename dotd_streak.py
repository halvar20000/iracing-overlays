"""
dotd_streak.py  —  "no back-to-back Driver of the Day" support.

Adds the season-aware streak rule on top of driver_of_the_day.analyze():
a driver cannot win Driver of the Day in two consecutive rounds of the
SAME season. The previous round's winner is passed to analyze() as an
excluded name, so the title falls to the next eligible driver.

Where the "season" comes from
-----------------------------
The season identity is taken from the CLS league-manager — the same source
the championship overlay uses (championship_config.json -> league_slug /
season_id -> https://league.simracing-hub.com/api/overlay/standings). The
league-manager's season.id is globally unique and changes when the season
rolls over, so the streak resets automatically at a new season with no
manual step. If the league-manager can't be reached, we fall back to a key
derived from the config (league_slug + season_id) so the rule still works
offline.

Winner history is persisted in dotd_history.json next to the scripts, keyed
by season id. Matching is by driver display name (the only stable identifier
in the logs), case-insensitive.

Stdlib only (urllib/json/pathlib) — no third-party dependencies, so the
analyzer and overlay stay dependency-free.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import driver_of_the_day as dotd

HERE = Path(__file__).resolve().parent
CHAMP_CONFIG = HERE / "championship_config.json"
HISTORY_PATH = HERE / "dotd_history.json"
DEFAULT_API = "https://league.simracing-hub.com"
DEFAULT_LEAGUE_SLUG = "cas-gt3-wct"

# cache the resolved season so the overlay doesn't hammer the API every tick
_season_cache = {"t": 0.0, "ttl": 180.0, "value": None}


# ---------------------------------------------------------------------------
# Season resolution (links to the league-manager)
# ---------------------------------------------------------------------------
def _read_champ_config():
    try:
        with open(CHAMP_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def resolve_season(timeout=10.0, use_cache=True):
    """
    Return {"key", "name", "league", "source"} for the current season.

    `key` is the stable season identifier used to bucket the winner history.
    `source` is "league-manager" when the live API answered, else "config"
    (offline fallback) or "none" when nothing is configured.
    """
    now = time.time()
    if use_cache and _season_cache["value"] and (now - _season_cache["t"]) < _season_cache["ttl"]:
        return _season_cache["value"]

    cfg = _read_champ_config()
    base = (cfg.get("api_base") or DEFAULT_API).rstrip("/")
    slug = cfg.get("league_slug") or DEFAULT_LEAGUE_SLUG
    season_id = cfg.get("season_id")  # may be None -> API picks ACTIVE

    result = None
    # 1) Try the live league-manager — authoritative + auto season rollover.
    try:
        params = {"league": slug}
        if season_id:
            params["season"] = season_id
        url = base + "/api/overlay/standings?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "dotd-streak/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.load(r)
        season = body.get("season") or {}
        league = body.get("league") or {}
        if season.get("id"):
            result = {
                "key": "lm:" + str(season["id"]),
                "name": season.get("name") or "Season",
                "league": league.get("name") or slug,
                "source": "league-manager",
            }
    except Exception:
        result = None

    # 2) Offline fallback — key from config so the streak still applies.
    if result is None:
        key = "cfg:%s:%s" % (slug, season_id or "active")
        result = {"key": key, "name": "(season offline)", "league": slug, "source": "config"}

    _season_cache.update(t=now, value=result)
    return result


# ---------------------------------------------------------------------------
# Winner history
# ---------------------------------------------------------------------------
def load_history(path=HISTORY_PATH):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seasons": {}}


def save_history(history, path=HISTORY_PATH):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def previous_winner_name(history, season_key, current_log=None):
    """Name of the most recent recorded winner in this season whose log is
    NOT current_log (so re-running on the same race stays stable)."""
    season = (history.get("seasons") or {}).get(season_key)
    if not season:
        return None
    for entry in reversed(season.get("winners", [])):
        if current_log and entry.get("log") == current_log:
            continue
        return entry.get("name")
    return None


def record_winner(history, season_key, season_name, league, winner, log_name, track):
    """Idempotent per (season_key, log_name): updates the entry for this log
    if present, else appends. Returns the mutated history."""
    seasons = history.setdefault("seasons", {})
    season = seasons.setdefault(season_key, {"name": season_name, "league": league, "winners": []})
    season["name"] = season_name
    season["league"] = league
    entry = {
        "log": log_name,
        "name": winner.get("name"),
        "car_number": winner.get("car_number"),
        "track": track,
        "score": winner.get("score"),
        "t": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    winners = season["winners"]
    for i, w in enumerate(winners):
        if w.get("log") == log_name:
            winners[i] = entry
            break
    else:
        winners.append(entry)
    return history


# ---------------------------------------------------------------------------
# High-level convenience used by the CLI / overlay / logger
# ---------------------------------------------------------------------------
def pick(log_path, profile=dotd.DEFAULT_PROFILE, weights=None, dnf_can_win=False,
         no_repeat=True, record=False, history_path=HISTORY_PATH, timeout=10.0):
    """
    Compute DotD for one race with the no-back-to-back rule applied.

    Returns the standard analyze() result, enriched with a "season" block and
    "previous_winner". When `record` is True and a winner is found, appends it
    to the season history (the race logger does this at session_end).
    """
    log_name = os.path.basename(str(log_path))
    season = resolve_season(timeout=timeout) if no_repeat else {
        "key": None, "name": None, "league": None, "source": "disabled"}

    history = load_history(history_path)
    prev = previous_winner_name(history, season["key"], current_log=log_name) if no_repeat else None
    exclude = [prev] if prev else []

    result = dotd.analyze_file(log_path, profile=profile, weights=weights,
                               dnf_can_win=dnf_can_win, exclude_names=exclude)
    result["season"] = season
    result["previous_winner"] = prev

    if record and result.get("ok") and result.get("winner") and season["key"]:
        record_winner(history, season["key"], season["name"], season["league"],
                      result["winner"], log_name, result.get("track"))
        save_history(history, history_path)
        result["recorded"] = True

    return result
