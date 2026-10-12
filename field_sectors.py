"""
field_sectors.py — best sector times and sector top speeds for the WHOLE
field, from the per-car arrays iRacing broadcasts (no SDK import here, so it
can be tested on its own). Used by the Quali Delta server (5014) for its
/sectors and /speed pages.

Feed it one sample per poll with `update()`:
  t         SessionTime
  pcts      CarIdxLapDistPct
  surfaces  CarIdxTrackSurface (-1 not in world, 0 off track, 1 pit stall,
            2 approaching pits, 3 on track)
  on_pits   CarIdxOnPitRoad

Sector times
  A sector time only counts when the car was seen ENTERING the sector (so an
  overlay started mid-lap never records a half sector), stayed on track and
  off pit road the whole way, and was sampled without gaps. Boundary
  crossings are interpolated between two samples, so a 15 Hz poll still
  gives times to a few milliseconds. Session best per sector + who set it;
  the sum of the bests is the theoretical best lap ("ideal lap").
  NOTE: iRacing does not publish per-sector validity. A lap deleted for
  track limits without the car leaving the surface (rare) can still set a
  sector best here.

Top speed
  iRacing broadcasts no per-car speed, so it is derived from the distance
  covered along the lap (pct × track length) over a ~0.3-0.6 s window —
  the same idea the dashboard's incident detector uses. The window averages
  away sample noise and reads a km/h or two under a true peak. Highest speed
  per car per sector, pit road excluded.

Session change: call `reset()`. A backwards SessionTime jump (replay scrub),
a gap > 1 s or a teleport only restarts that car's tracking — the bests
already recorded stay.
"""

import bisect
from collections import deque

GAP_MAX_S = 1.0            # longer between two samples = start over for that car
TELEPORT_MAX_PCT = 0.2     # more lap distance than this in one sample = jump
SPEED_WIN_MIN_S = 0.3      # speed measured over at least this long …
SPEED_WIN_MAX_S = 0.6      # … and at most this long
SPEED_MAX_KMH = 480.0      # anything above is a glitch
SECTOR_MIN_S = 1.0
SECTOR_MAX_S = 600.0

SURF_NOT_IN_WORLD = -1
SURF_OFF_TRACK = 0
SURF_PIT_STALL = 1
SURF_APPROACHING_PITS = 2


class _Car:
    __slots__ = ("t", "pct", "sec", "enter_t", "clean", "dist", "hist",
                 "cur_max", "full")

    def __init__(self, t, pct, sec):
        self.t = t
        self.pct = pct
        self.sec = sec
        self.enter_t = None      # None = entry not seen → sector won't count
        self.clean = True
        self.dist = 0.0          # metres driven since tracking (unwrapped)
        self.hist = deque()      # (t, dist)
        self.cur_max = 0.0       # top speed in the current sector so far (km/h)
        self.full = False        # entry of the current sector was seen


class FieldSectors:
    def __init__(self):
        self.reset()

    def reset(self):
        self.bounds = None       # sorted sector start pcts, first = 0.0
        self.track_m = None
        self.cars = {}
        self.best = []           # per sector: (time, car_idx) or None
        self.car_best = {}       # car_idx -> [time or None] per sector
        self.top = []            # per sector: {car_idx: best km/h}

    def configure(self, bounds, track_m):
        """Sector start pcts (any order, 0.0 added) and track length in m.
        A change of either restarts everything."""
        b = sorted(set(float(x) for x in bounds if x is not None and 0.0 <= float(x) < 1.0))
        if not b or b[0] > 0.0001:
            b = [0.0] + b
        if b == self.bounds and track_m == self.track_m:
            return
        self.reset()
        self.bounds = b
        self.track_m = track_m
        self.best = [None] * len(b)
        self.top = [dict() for _ in b]

    @property
    def n(self):
        return len(self.bounds) if self.bounds else 0

    def sector_of(self, pct):
        return max(0, bisect.bisect_right(self.bounds, pct % 1.0) - 1)

    # ------------------------------------------------------------------
    def update(self, t, pcts, surfaces, on_pits):
        if not self.bounds or t is None:
            return
        n = len(self.bounds)
        cands = self.bounds + [b + 1.0 for b in self.bounds]
        for idx, pct in enumerate(pcts or []):
            surf = surfaces[idx] if idx < len(surfaces or []) else SURF_NOT_IN_WORLD
            if surf is None or surf == SURF_NOT_IN_WORLD or pct is None or pct < 0:
                self.cars.pop(idx, None)
                continue
            pit = bool(on_pits[idx]) if idx < len(on_pits or []) else False
            pit = pit or surf in (SURF_PIT_STALL, SURF_APPROACHING_PITS)
            car = self.cars.get(idx)
            if car is None:
                self.cars[idx] = _Car(t, pct, self.sector_of(pct))
                continue
            dt = t - car.t
            if dt <= 0:
                if dt < -0.5:                       # replay went back
                    self.cars[idx] = _Car(t, pct, self.sector_of(pct))
                continue                            # paused / same tick
            dp = pct - car.pct
            if dp < -0.5:
                dp += 1.0                           # crossed S/F
            if dt > GAP_MAX_S or dp > TELEPORT_MAX_PCT or dp < -0.01:
                self.cars[idx] = _Car(t, pct, self.sector_of(pct))
                continue
            dp = max(dp, 0.0)

            # sector boundaries crossed between the two samples
            if dp > 0:
                p0, p1 = car.pct, car.pct + dp
                for ci, c in enumerate(cands):
                    if p0 < c <= p1:
                        tc = car.t + (c - p0) / dp * dt
                        self._close(idx, car, tc)
                        car.sec = ci % n
                        car.enter_t = tc
                        car.clean = True
                        car.full = True
                        car.cur_max = 0.0

            # this sample belongs to the (possibly new) current sector
            if pit or surf == SURF_OFF_TRACK:
                car.clean = False

            # speed over a short window
            car.dist += dp * (self.track_m or 0.0)
            car.hist.append((t, car.dist))
            while len(car.hist) > 2 and t - car.hist[1][0] >= SPEED_WIN_MAX_S:
                car.hist.popleft()
            t0, d0 = car.hist[0]
            if self.track_m and t - t0 >= SPEED_WIN_MIN_S and not pit:
                kmh = (car.dist - d0) / (t - t0) * 3.6
                if 0 < kmh < SPEED_MAX_KMH and kmh > car.cur_max:
                    car.cur_max = kmh

            car.t = t
            car.pct = pct

    def _close(self, idx, car, tc):
        """The car just left sector car.sec at time tc."""
        s = car.sec
        if not car.full or car.enter_t is None:
            return
        if car.cur_max > 0:
            top = self.top[s]
            if car.cur_max > top.get(idx, 0.0):
                top[idx] = car.cur_max
        if not car.clean:
            return
        st = tc - car.enter_t
        if not (SECTOR_MIN_S <= st <= SECTOR_MAX_S):
            return
        mine = self.car_best.setdefault(idx, [None] * len(self.bounds))
        if mine[s] is None or st < mine[s]:
            mine[s] = st
        if self.best[s] is None or st < self.best[s][0]:
            self.best[s] = (st, idx)

    # ------------------------------------------------------------------
    def ideal(self):
        if not self.best or any(b is None for b in self.best):
            return None
        return sum(b[0] for b in self.best)

    def car_ideal(self, idx):
        mine = self.car_best.get(idx)
        if not mine or any(x is None for x in mine):
            return None
        return sum(mine)

    def cam_speed(self, cam):
        """(sector index, live top speed in this sector, sector entry seen)
        for the camera car, or (None, None, False)."""
        car = self.cars.get(cam) if cam is not None else None
        if car is None:
            return None, None, False
        return car.sec, (car.cur_max or None), car.full

    def ranking(self, s):
        """[(car_idx, km/h)] fastest first for sector s."""
        return sorted(self.top[s].items(), key=lambda kv: -kv[1]) if 0 <= s < len(self.top) else []
