"""Which CLS league is racing (0.1.39) - for the Auto Steward profiles.

Uses the overlays' shared ``cls_league_detect.LeagueDetector`` (repo root):
every CLS league's current rosters, joined by iRacing customer id, cached in
cls_league_cache.json.  Same rule as the championship overlay: the season
whose roster holds the most drivers of the field, at least 3 and half of
them - otherwise None (nothing is switched).  Started only once a car with an
iRacing UserID is seen, so the simulator / tests fetch nothing.
"""

from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)
try:
    from cls_league_detect import LeagueDetector
    IMPORT_ERROR = ""
except Exception as _e:                 # racecontrol copied on its own
    LeagueDetector = None
    IMPORT_ERROR = f"cls_league_detect not importable: {_e}"

CHECK_EVERY_S = 10.0
# shown in the Rules dialog even before CLS answered
KNOWN = {"cas-gt3-wct": "CAS GT3 WCT", "cas-iec": "CAS IEC",
         "cas-pccd": "CAS PCC", "cas-sfl-cup": "CAS SFL Cup",
         "cas-combined-cup": "CAS Combined Cup", "cas-fun-league": "CAS Fun League"}


class League:
    def __init__(self, detector=None) -> None:
        self._det = detector
        self._started = detector is not None
        self._next = 0.0
        self.current: dict | None = None    # {slug, name, matched, drivers}

    def _get(self):
        if not self._started:
            self._started = True
            if LeagueDetector is None:
                print(f"[league] auto profile OFF - {IMPORT_ERROR}")
            else:
                self._det = LeagueDetector()
                self._det.start()
        return self._det

    def update(self, state) -> dict | None:
        """-> the league of the field now (checked every CHECK_EVERY_S)."""
        now = time.monotonic()
        if now < self._next:
            return self.current
        self._next = now + CHECK_EVERY_S
        ids = [c.user_id for c in state.cars.values() if c._seen and c.user_id]
        if not ids:
            return self.current          # keep the last answer (e.g. loading)
        det = self._get()
        found = None
        if det is not None:
            try:
                found = det.detect(ids)
            except Exception:
                found = None
        self.current = None if not found else {
            "slug": found["league_slug"],
            "name": found.get("league_name") or found["league_slug"],
            "matched": found.get("matched"), "drivers": found.get("drivers")}
        return self.current

    def choices(self) -> list[dict]:
        out = dict(KNOWN)
        det = self._det
        try:
            for s in (det.status()["seasons"] if det else []):
                out.setdefault(s["league_slug"], s["league_slug"])
        except Exception:
            pass
        return [{"slug": k, "name": v} for k, v in sorted(out.items(), key=lambda kv: kv[1].lower())]
