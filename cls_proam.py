"""
CLS Pro/Am roster — which driver of the running WCT GT3 season is PRO / AM
--------------------------------------------------------------------------
Shared by the standings tower (5005) and the driver card (5017). Both show
a small coloured bar in front of the driver name: red = PRO, green = AM.

Source: the league-manager's public overlay API (the same one the
championship overlay uses):

    GET https://league.simracing-hub.com/api/overlay/standings?league=cas-gt3-wct

Every standings row carries `proAmClass` ("PRO" / "AM") and the linked
`iracingMemberId`, which joins to DriverInfo.Drivers[].UserID. Matching is
by customer ID only — names are never used. Without a `season` param the
API returns the league's current season, so a new season is picked up
automatically.

"Only for WCT GT3": the roster is always fetched, but the bars are only
shown when the session actually IS a WCT session. Mode "auto" (default)
decides that from the field: at least half of the drivers in the session
(and at least MIN_MATCHES of them) must be on the WCT roster. A WCT driver
turning up in a PCCD or IEC race therefore gets no bar. Override with
`"mode": "on"` / `"off"` in proam_config.json.

The last good roster is cached to proam_cache.json, so the bars still work
if the stream PC has no internet at race start. Fetch uses urllib (stdlib)
so neither overlay gains a dependency.
"""

import json
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR  = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "proam_config.json"
CACHE_PATH  = SCRIPT_DIR / "proam_cache.json"

DEFAULT_CONFIG = {
    "api_base":        "https://league.simracing-hub.com",
    "league_slug":     "cas-gt3-wct",
    "season_id":       None,    # None -> the API picks the league's current season
    "mode":            "auto",  # auto | on | off
    "refresh_seconds": 300,     # roster changes rarely; 5 min is plenty
}

MIN_MATCHES     = 3     # auto mode: never switch on for one or two stray drivers
MIN_MATCH_SHARE = 0.5   # auto mode: share of the field that must be on the roster
RETRY_SECONDS   = 30    # after a failed fetch


def _load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[proam] Could not read {CONFIG_PATH.name}: {e}")
    return cfg


class ProAmRoster:
    """Background-refreshed map iRacing customer ID -> "PRO" / "AM"."""

    def __init__(self):
        self._cfg = _load_config()
        self._lock = threading.Lock()
        self._classes: dict[int, str] = {}
        self._season = ""
        self._last_ok = 0.0
        self._error: str | None = None
        self._load_cache()

    # -- public -----------------------------------------------------------
    def start(self) -> None:
        if (self._cfg.get("mode") or "auto").lower() == "off":
            print("[proam] mode=off — Pro/Am bars disabled")
            return
        threading.Thread(target=self._run, daemon=True).start()

    def lookup(self, user_id) -> "str | None":
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None
        with self._lock:
            return self._classes.get(uid)

    def active_for(self, user_ids) -> bool:
        """Should the bars be shown for a session with these drivers?"""
        mode = (self._cfg.get("mode") or "auto").lower()
        if mode == "off":
            return False
        if mode == "on":
            return True
        ids = []
        for u in user_ids:
            try:
                ids.append(int(u))
            except (TypeError, ValueError):
                pass
        if not ids:
            return False
        with self._lock:
            hits = sum(1 for u in ids if u in self._classes)
        return hits >= MIN_MATCHES and hits >= MIN_MATCH_SHARE * len(ids)

    def status(self) -> dict:
        with self._lock:
            return {
                "league":  self._cfg.get("league_slug"),
                "season":  self._season,
                "mode":    self._cfg.get("mode"),
                "drivers": len(self._classes),
                "pro":     sum(1 for c in self._classes.values() if c == "PRO"),
                "am":      sum(1 for c in self._classes.values() if c == "AM"),
                "last_ok": self._last_ok,
                "error":   self._error,
            }

    # -- internals --------------------------------------------------------
    def _run(self) -> None:
        while True:
            try:
                self._fetch()
                wait = float(self._cfg.get("refresh_seconds") or 300)
            except Exception as e:
                with self._lock:
                    self._error = f"{type(e).__name__}: {e}"
                print(f"[proam] Fetch failed: {self._error}")
                wait = RETRY_SECONDS
            time.sleep(wait)

    def _fetch(self) -> None:
        cfg = self._cfg
        params = {"league": cfg.get("league_slug") or "cas-gt3-wct"}
        if cfg.get("season_id"):
            params["season"] = cfg["season_id"]
        url = (f"{(cfg.get('api_base') or DEFAULT_CONFIG['api_base']).rstrip('/')}"
               f"/api/overlay/standings?{urllib.parse.urlencode(params)}")
        req = urllib.request.Request(url, headers={"User-Agent": "iracing-overlays"})
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(body.get("error") or "API returned ok=false")
        classes = self._parse(body)
        season = (body.get("season") or {}).get("name") or ""
        changed = False
        with self._lock:
            changed = classes != self._classes or season != self._season
            self._classes = classes
            self._season = season
            self._last_ok = time.time()
            self._error = None
        if changed:
            pro = sum(1 for c in classes.values() if c == "PRO")
            print(f"[proam] {season}: {pro} PRO / {len(classes) - pro} AM")
            try:
                CACHE_PATH.write_text(json.dumps(body), encoding="utf-8")
            except Exception as e:
                print(f"[proam] Could not write {CACHE_PATH.name}: {e}")

    @staticmethod
    def _parse(body: dict) -> dict:
        out = {}
        for s in body.get("standings") or []:
            cls = (s.get("proAmClass") or "").upper()
            if cls not in ("PRO", "AM"):
                continue
            try:
                out[int(s.get("iracingMemberId"))] = cls
            except (TypeError, ValueError):
                continue
        return out

    def _load_cache(self) -> None:
        if not CACHE_PATH.exists():
            return
        try:
            body = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            league = (body.get("league") or {}).get("slug")
            if league and league != self._cfg.get("league_slug"):
                return   # cache from a different league config — ignore
            self._classes = self._parse(body)
            self._season = (body.get("season") or {}).get("name") or ""
            print(f"[proam] Cached roster: {self._season} "
                  f"({len(self._classes)} drivers)")
        except Exception as e:
            print(f"[proam] Could not read {CACHE_PATH.name}: {e}")
