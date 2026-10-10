"""
CLS league detection — WHICH series is on the stream right now?
---------------------------------------------------------------
The championship overlay (5010) and everything that shares its state
(/duel, /lastrace, /table, /rsvp, /stats, /lastdotd) need one answer
before they can show a number: which league and which SEASON is this
session. Getting it wrong is worse than showing nothing — a PCCD
championship over a WCT GT3 race looks authoritative and is wrong.

It used to be a dropdown on the config page. Now it is read off the
grid: every CLS standings row carries the linked `iracingMemberId`,
which joins to `DriverInfo.Drivers[].UserID`, so the season whose
roster matches the most drivers in the session IS the session's
season. Same identity bridge `cls_proam.py` already uses for the
Pro/Am bars, same thresholds, one league further.

Why (league, SEASON) and not just the league
--------------------------------------------
Two leagues currently have TWO seasons open at once — cas-pccd (5th
and 6th) and cas-sfl-cup (8th and 9th) — and the empty new one is the
one the API returns when no `season` is given. That is the gotcha
`championship_config.json` worked around by pinning a season id by
hand, which then has to be repinned every time a season rolls over.
Matching per season picks the one the drivers are actually IN:

    cas-pccd  5th season  27 linked ids      <- the raced one
    cas-pccd  6th season   1 linked id
    cas-sfl   8th Season  19 linked ids      <- the raced one
    cas-sfl   9th Season   0 linked ids

Thresholds, and why a wrong guess is impossible-ish
---------------------------------------------------
Rosters DO overlap (GT3 WCT and IEC share 14 drivers) but only the
drivers present in the session are counted, so the series being raced
wins on count. Below MIN_MATCHES / MIN_MATCH_SHARE the detector
answers None and the caller keeps its configured league — it never
guesses. A season with no linked ids at all (SFL 9th today) can never
be detected; that is what the manual override stays for.

Offline start: the last good roster map is cached to
cls_league_cache.json, so detection still works when the stream PC has
no internet at race start. urllib only — no new dependency.
"""

import json
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR  = Path(__file__).resolve().parent
CACHE_PATH  = SCRIPT_DIR / "cls_league_cache.json"

DEFAULT_API = "https://league.simracing-hub.com"

# Same numbers as cls_proam.py on purpose: both answer "is this session
# that league's session", and two different answers on one stream would
# be a bug nobody could read off the screen.
MIN_MATCHES     = 3
MIN_MATCH_SHARE = 0.5
REFRESH_SECONDS = 300    # rosters change between rounds, not during one
RETRY_SECONDS   = 30


class LeagueDetector:
    """Background roster map of every CLS league's runnable seasons.

    `detect(user_ids)` returns the best (league, season) match or None.
    Start it once per process; `ProAmRoster` can share the same instance
    rather than fetching the whole API a second time.
    """

    def __init__(self, api_base: str = DEFAULT_API):
        self._api_base = (api_base or DEFAULT_API).rstrip("/")
        self._lock = threading.Lock()
        # [{slug, league_name, season_id, season_name, completed_rounds,
        #   total_rounds, ids:set[int]}]
        self._seasons: list[dict] = []
        self._last_ok: float = 0.0
        self._error: str | None = None
        self._thread: threading.Thread | None = None
        self._load_cache()

    # -- public ----------------------------------------------------------
    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def detect(self, user_ids) -> dict | None:
        """Best (league, season) for a session's driver customer IDs.

        Returns None when the field does not identify a season well
        enough — the caller must then keep whatever it is configured
        with. Never raises.
        """
        ids = set()
        for u in user_ids or ():
            try:
                n = int(u)
            except (TypeError, ValueError):
                continue
            if n > 0:              # 0 / -1 are the pace car and empty slots
                ids.add(n)
        if not ids:
            return None

        with self._lock:
            seasons = list(self._seasons)
        if not seasons:
            return None

        scored = []
        for s in seasons:
            hits = len(ids & s["ids"])
            if hits:
                scored.append((hits, s))
        if not scored:
            return None

        # Most drivers wins. Ties break on the season that is further
        # along — a brand-new season sharing a league's roster must never
        # outrank the one actually being raced (PCCD 6th vs 5th).
        scored.sort(key=lambda t: (t[0], t[1].get("completed_rounds") or 0),
                    reverse=True)
        hits, best = scored[0]
        if hits < MIN_MATCHES or hits < MIN_MATCH_SHARE * len(ids):
            return None

        runner_up = scored[1][0] if len(scored) > 1 else 0
        return {
            "league_slug":   best["slug"],
            "league_name":   best.get("league_name"),
            "season_id":     best["season_id"],
            "season_name":   best.get("season_name"),
            "matched":       hits,
            "drivers":       len(ids),
            "runner_up":     runner_up,
            # A field that matches two seasons almost equally is worth
            # showing on the config page rather than hiding.
            "ambiguous":     runner_up >= hits,
        }

    def status(self) -> dict:
        with self._lock:
            return {
                "seasons": [{
                    "league_slug": s["slug"],
                    "season_id":   s["season_id"],
                    "season_name": s.get("season_name"),
                    "drivers":     len(s["ids"]),
                    "completed_rounds": s.get("completed_rounds"),
                } for s in self._seasons],
                "last_ok": self._last_ok,
                "error":   self._error,
            }

    def rows_for(self, slug: str, season_id: str | None) -> set:
        """The linked customer IDs of one season (used by tests)."""
        with self._lock:
            for s in self._seasons:
                if s["slug"] == slug and (season_id is None
                                          or s["season_id"] == season_id):
                    return set(s["ids"])
        return set()

    # -- internals -------------------------------------------------------
    def _run(self) -> None:
        while True:
            try:
                self._fetch()
                wait = REFRESH_SECONDS
            except Exception as e:
                with self._lock:
                    self._error = f"{type(e).__name__}: {e}"
                print(f"[league-detect] Fetch failed: {self._error}")
                wait = RETRY_SECONDS
            time.sleep(wait)

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._api_base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "iracing-overlays"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def _fetch(self) -> None:
        leagues = self._get_json("/api/overlay/leagues").get("leagues") or []
        seasons: list[dict] = []
        for lg in leagues:
            slug = lg.get("slug")
            if not slug:
                continue
            for season in lg.get("seasons") or []:
                sid = season.get("id")
                if not sid:
                    continue
                body = self._get_json("/api/overlay/standings",
                                      {"league": slug, "season": sid})
                ids = set()
                for row in body.get("standings") or []:
                    try:
                        ids.add(int(row.get("iracingMemberId")))
                    except (TypeError, ValueError):
                        continue
                meta = body.get("season") or season
                seasons.append({
                    "slug":             slug,
                    "league_name":      (body.get("league") or lg).get("name"),
                    "season_id":        sid,
                    "season_name":      meta.get("name") or season.get("name"),
                    "completed_rounds": meta.get("completedRounds"),
                    "total_rounds":     meta.get("totalRounds"),
                    "ids":              ids,
                })
        if not seasons:
            return
        with self._lock:
            self._seasons = seasons
            self._last_ok = time.time()
            self._error = None
        total = sum(len(s["ids"]) for s in seasons)
        print(f"[league-detect] {len(seasons)} season(s), {total} linked drivers")
        self._save_cache(seasons)

    def _save_cache(self, seasons: list[dict]) -> None:
        try:
            CACHE_PATH.write_text(json.dumps([{
                **{k: v for k, v in s.items() if k != "ids"},
                "ids": sorted(s["ids"]),
            } for s in seasons]), encoding="utf-8")
        except Exception as e:
            print(f"[league-detect] Could not write {CACHE_PATH.name}: {e}")

    def _load_cache(self) -> None:
        if not CACHE_PATH.exists():
            return
        try:
            raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            self._seasons = [{**s, "ids": set(s.get("ids") or ())} for s in raw]
            print(f"[league-detect] Loaded {len(self._seasons)} season(s) "
                  f"from {CACHE_PATH.name}")
        except Exception as e:
            print(f"[league-detect] Could not read {CACHE_PATH.name}: {e}")
