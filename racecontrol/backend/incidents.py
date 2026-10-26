"""Live incident detection for iRacing mode (no SDK import - pure logic).

Two independent sources, merged by the race-state engine:

* :class:`OfficialIncidents` - iRacing's own incident points per car, from
  ``SessionInfo.Sessions[n].ResultsPositions[].Incidents``.  In a team event
  that is the CAR's count (what a drive-through limit is judged on).
  CAVEAT, seen in every race log of the companion race logger: for a
  SPECTATOR who is not admin of the session, iRacing publishes these only at
  the finish (all counts 0 for the whole race, then the final tally in one
  go).  Live counts need the controller to run as a session ADMIN - the same
  as iRaceControl.  So we never rely on them alone:

* :class:`BehaviourDetector` - spins and contacts inferred from car motion,
  the speed-collapse method of the dashboard (``iracing_dashboard.py``,
  2026-06-04): racing speed to near-stop within 2.5 s.  Normal braking
  cannot fire it (no corner apex is under 32 km/h), pit lane is excluded,
  a connection blink (frozen telemetry, out of world and back) is ignored,
  and a second car collapsing nearby or sitting alongside makes it a
  contact.  Plus "stopped on track" (under 5 km/h for 3 s).

A detection carries no points; when the official points arrive for the same
car shortly after, the engine attaches them to that detection.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

# Speed-collapse tuning - the same values as the dashboard.
SC_PEAK_MIN_MPS = 25.0     # must have been >= 90 km/h within the window
SC_FINAL_MAX_MPS = 9.0     # and now be <= 32 km/h
SC_DROP_MIN_MPS = 18.0     # and have lost >= 65 km/h
SC_WINDOW_S = 2.5          # lookback for the recent peak
SC_COOLDOWN_S = 8.0        # per-car re-arm time
SC_PAIR_PCT = 0.015        # another collapse this close = contact
SC_PAIR_WINDOW_S = 3.0
SC_TOUCH_PCT = 0.0045      # a car this close right now = contact
STOP_MAX_MPS = 1.4         # ~5 km/h
STOP_MIN_S = 3.0
STOP_COOLDOWN_S = 30.0
UNSTABLE_WINDOW_S = 90.0   # a car back from out-of-world is ignored this long

# iRacing CarIdxTrackSurface values.
SURF_NOT_IN_WORLD = -1
SURF_OFF_TRACK = 0
SURF_PIT_STALL = 1
SURF_APPROACHING_PITS = 2
SURF_ON_TRACK = 3


@dataclass
class Detection:
    car_idx: int
    kind: str                       # 'spin' | 'contact' | 'stopped'
    text: str
    t: float                        # session time
    other_car_idx: Optional[int] = None


class BehaviourDetector:
    """Feed it every poll; returns new detections."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._prog: dict[int, deque] = {}       # idx -> (t, unwrapped progress)
        self._speed: dict[int, deque] = {}      # idx -> (t, m/s)
        self._collapse_at: dict[int, float] = {}
        self._stop_since: dict[int, float] = {}
        self._stop_said: dict[int, float] = {}
        self._unstable_until: dict[int, float] = {}
        self._was_out: dict[int, bool] = {}
        self._last_t: Optional[float] = None
        self._session: Optional[int] = None

    # -- kinematics ---------------------------------------------------------

    def _update_speed(self, idx: int, t: float, pct: float,
                      track_m: float) -> Optional[float]:
        hist = self._prog.setdefault(idx, deque(maxlen=45))
        if hist:
            t_prev, cum_prev = hist[-1]
            delta = pct - (cum_prev % 1.0)
            if delta < -0.5:
                delta += 1.0
            elif delta > 0.5:
                delta -= 1.0
            if t - t_prev > 2.0 or abs(delta) > 0.2 or t <= t_prev:
                hist.clear()                    # data gap / teleport / tow
                self._speed.get(idx, deque()).clear()
                cum = pct
            else:
                cum = cum_prev + delta
        else:
            cum = pct
        hist.append((t, cum))
        v = None
        for t_old, cum_old in hist:
            if t - t_old <= 0.65:
                if t - t_old >= 0.35:
                    v = (cum - cum_old) * track_m / (t - t_old)
                break
        if v is None and len(hist) >= 2:
            t_old, cum_old = hist[0]
            if 0.05 < t - t_old <= 0.65:
                v = (cum - cum_old) * track_m / (t - t_old)
        if v is not None:
            self._speed.setdefault(idx, deque(maxlen=45)).append((t, v))
        return v

    def _peak(self, idx: int, t: float) -> Optional[float]:
        vals = [v for (ts, v) in self._speed.get(idx, ()) if t - ts <= SC_WINDOW_S]
        return max(vals) if vals else None

    def _frozen(self, idx: int) -> bool:
        h = self._prog.get(idx)
        return bool(h and len(h) >= 3 and h[-1][1] == h[-2][1] == h[-3][1])

    def _contact_with(self, idx: int, t: float,
                      pcts: dict[int, float]) -> Optional[int]:
        me = pcts.get(idx)
        if me is None:
            return None
        best = None
        for j, p in pcts.items():
            if j == idx:
                continue
            d = abs(p - me)
            d = min(d, 1.0 - d)
            if d < SC_TOUCH_PCT:
                return j
            if d < SC_PAIR_PCT and t - self._collapse_at.get(j, -1e9) < SC_PAIR_WINDOW_S:
                best = j
        return best

    # -- main entry ---------------------------------------------------------

    def update(self, t: float, session_num: int, racing: bool,
               cars: list[tuple[int, float, int, bool]],
               track_m: float) -> list[Detection]:
        """``cars`` = (car_idx, lap_dist_pct, track_surface, on_pit_road).

        ``racing`` false (grid, pace lap, after the flag ...) only tracks.
        """
        if self._session != session_num or (
                self._last_t is not None and t < self._last_t - 5.0):
            self.reset()
            self._session = session_num
        self._last_t = t
        out: list[Detection] = []
        if track_m <= 0:
            return out

        pcts = {idx: p for idx, p, surf, _pit in cars
                if surf != SURF_NOT_IN_WORLD and p is not None and p >= 0}
        for idx, pct, surf, on_pit in cars:
            if surf == SURF_NOT_IN_WORLD or pct is None or pct < 0:
                self._was_out[idx] = True
                self._prog.pop(idx, None)
                self._speed.pop(idx, None)
                self._stop_since.pop(idx, None)
                continue
            if self._was_out.pop(idx, False):
                # back in the world: a tow / reset - or a connection blink
                self._unstable_until[idx] = t + UNSTABLE_WINDOW_S
            v = self._update_speed(idx, t, pct, track_m)
            if v is None or not racing:
                continue
            if t < self._unstable_until.get(idx, -1e9):
                continue
            on_track = surf in (SURF_ON_TRACK, SURF_OFF_TRACK) and not on_pit
            if not on_track:
                self._stop_since.pop(idx, None)
                continue

            # spin / contact
            if v < SC_FINAL_MAX_MPS:
                peak = self._peak(idx, t)
                if (peak is not None and peak >= SC_PEAK_MIN_MPS
                        and peak - v >= SC_DROP_MIN_MPS
                        and t - self._collapse_at.get(idx, -1e9) > SC_COOLDOWN_S
                        and not self._frozen(idx)):
                    self._collapse_at[idx] = t
                    other = self._contact_with(idx, t, pcts)
                    kind = "contact" if other is not None else "spin"
                    out.append(Detection(
                        idx, kind,
                        f"{kind} ({peak * 3.6:.0f} -> {max(v, 0) * 3.6:.0f} km/h)",
                        t, other))

            # stopped on track
            if v < STOP_MAX_MPS:
                since = self._stop_since.setdefault(idx, t)
                if (t - since >= STOP_MIN_S
                        and t - self._stop_said.get(idx, -1e9) > STOP_COOLDOWN_S
                        and t - self._collapse_at.get(idx, -1e9) > STOP_MIN_S + 2):
                    self._stop_said[idx] = t
                    out.append(Detection(idx, "stopped", "stopped on track", t))
            else:
                self._stop_since.pop(idx, None)
        return out


class OfficialIncidents:
    """Turns iRacing's per-car incident totals into deltas.

    The first look at a session (attaching mid-session) only records the
    totals as the baseline - otherwise everyone's running total would read as
    one huge incident.  A session we saw START (the session number changed
    while connected) begins at zero, so its first points are news.  A saved
    baseline (crash recovery) makes the points scored while we were down show
    up as deltas too.
    """

    def __init__(self) -> None:
        self.session: Optional[int] = None
        self.totals: dict[int, int] = {}

    def seed(self, session_num: int, totals: dict[int, int]) -> None:
        self.session = session_num
        self.totals = {int(k): int(v) for k, v in totals.items()}

    def update(self, session_num: int,
               totals: dict[int, int]) -> list[tuple[int, int, int]]:
        """Returns [(car_idx, delta, total)] for every car whose count rose."""
        if self.session is None:
            self.session = session_num
            self.totals = dict(totals)
            return []
        if session_num != self.session:
            self.session = session_num
            self.totals = {}
        out = []
        for idx, total in totals.items():
            prev = self.totals.get(idx)
            self.totals[idx] = total
            if prev is None:
                prev = 0
            if total > prev:
                out.append((idx, total - prev, total))
            # a drop (session reset by the admin) is adopted silently
        return out
