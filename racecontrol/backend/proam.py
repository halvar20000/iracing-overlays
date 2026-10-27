"""PRO / AM class of each driver (GT3 WCT) for the Auto Steward.

Uses the overlays' shared ``cls_proam.ProAmRoster`` (repo root): the CLS
league-manager's WCT standings, joined by iRacing customer id, cached in
proam_cache.json so it also works offline.  Same "is this a WCT field?"
rule as the tower bars: at least half of the drivers (and 3) on the roster -
otherwise every car stays "" and the PRO-behind hint never fires.
"""

from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)
try:
    from cls_proam import ProAmRoster
    IMPORT_ERROR = ""
except Exception as _e:                 # racecontrol copied on its own
    ProAmRoster = None
    IMPORT_ERROR = f"cls_proam not importable: {_e}"

CHECK_EVERY_S = 5.0


class ProAm:
    def __init__(self, roster=None) -> None:
        self._roster = roster
        self._started = roster is not None
        self._next = 0.0
        self.active = False

    def _get(self):
        if not self._started:
            self._started = True
            if ProAmRoster is None:
                print(f"[proam] PRO/AM hint OFF - {IMPORT_ERROR}")
            else:
                self._roster = ProAmRoster()
                self._roster.start()        # background fetch from CLS
        return self._roster

    def apply(self, state) -> None:
        """Set car.proam on every car (every CHECK_EVERY_S)."""
        now = time.monotonic()
        if now < self._next:
            return
        self._next = now + CHECK_EVERY_S
        cars = [c for c in state.cars.values() if c._seen and c.user_id]
        if not cars and not self._started:
            return          # simulator / no iRacing ids: no CLS fetch at all
        roster = self._get()
        if roster is None:
            self.active = False
        else:
            try:
                self.active = roster.active_for([c.user_id for c in cars])
            except Exception:
                self.active = False
        for c in state.cars.values():
            cls = roster.lookup(c.user_id) if (self.active and c.user_id) else None
            c.proam = cls if cls in ("PRO", "AM") else ""
