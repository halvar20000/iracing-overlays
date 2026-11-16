"""Incidents of the LOCAL car (the driver running the logger), told apart by
what the car did - not guessed from incident points.

Why (Johann Solowej, 2026-11-16): the race logger named every +2 a spin, but
iRacing gives 0x / 2x / 4x for contacts depending on the impact, so a hard
wall hit read as a spin and a 0x touch was not logged at all. iRacing
publishes the local car's motion in detail, so here:

  contact   = the car's velocity changes faster than any car can brake or
              corner (CONTACT_G, from the change of the world velocity
              between two samples, or the accelerometer sample itself),
              with another car within CAR_NEAR_M -> "collision" (+ which
              car), else "wall_contact";
  spin      = the car moves at > SPIN_SLIP_DEG to its heading for
              SPIN_MIN_S (sideways / backwards) -> "lost_control";
  off track = PlayerTrackSurface off track for OFF_MIN_S at speed ->
              "off_track".

Detections close together (GROUP_S) are ONE incident (a wall hit that spins
the car = wall_contact with spun = true). An incident waits HOLD_S before it
is handed out, so the incident points iRacing adds a moment later
(PlayerCarMyIncidentCount) go onto it ("+2"). Points with no detection near
them are handed out as their own incident ("points_only").

LocalIncidentDetector is pure (fed one sample at a time, tests drive it);
LocalCarWatch is the thread that samples iRacing at SAMPLE_HZ with its own
IRSDK handle (the logger's own loop runs at 2 Hz, far too slow for a hit).

The thresholds are first guesses, not fitted to real data yet: every event
carries the raw numbers (peak_g, slip_deg, speed_kmh) so they can be tuned
from the first logs.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque

SAMPLE_HZ = 20
G = 9.81

CONTACT_G = 5.0          # a car brakes / corners at <= ~3.5 g
CONTACT_MIN_MPS = 4.0    # ignore bumps while (almost) standing
CAR_NEAR_M = 12.0        # another car this close at the hit = car contact
SPIN_SLIP_DEG = 70.0
SPIN_MIN_MPS = 6.0
SPIN_MIN_S = 0.25
OFF_MIN_S = 0.4
OFF_MIN_MPS = 8.0
GROUP_S = 3.0            # detections this close = one incident
HOLD_S = 4.0             # wait for iRacing's points before handing it out
POINTS_BEFORE_S = 2.0    # points may (rarely) come a moment before the motion
MAX_DT = 0.25            # longer gap between samples = no velocity check

SURF_OFF = 0             # irsdk TrkLoc: -1 not in world, 0 off track,
SURF_ON = 3              # 1 pit stall, 2 approaching pits, 3 on track


def _world_v(vx: float, vy: float, yaw: float) -> tuple[float, float]:
    c, s = math.cos(yaw), math.sin(yaw)
    return vx * c - vy * s, vx * s + vy * c


class LocalIncidentDetector:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._prev = None
        self._inc = None            # last PlayerCarMyIncidentCount
        self._open = None           # incident being gathered
        self._slip_since = None
        self._off_since = None
        self._spun_flag = False     # spin already counted for this slide
        self._off_flag = False
        self.ready: deque = deque()

    # ---- helpers -------------------------------------------------------
    def _note(self, t: float, kind: str, **info) -> None:
        o = self._open
        if o is None or t - o["t_last"] > GROUP_S:
            if o is not None:
                self._close()
            o = self._open = {"t": t, "t_last": t, "kinds": [], "points": 0,
                              "peak_g": 0.0, "slip_deg": 0.0, "with": None,
                              "speed_kmh": info.get("speed_kmh")}
        o["t_last"] = t
        if kind not in o["kinds"]:
            o["kinds"].append(kind)
        if "peak_g" in info:
            o["peak_g"] = max(o["peak_g"], info["peak_g"])
        if "slip_deg" in info:
            o["slip_deg"] = max(o["slip_deg"], info["slip_deg"])
        if info.get("with") is not None and o["with"] is None:
            o["with"] = info["with"]

    def _close(self) -> None:
        o, self._open = self._open, None
        if o is None:
            return
        k = o["kinds"]
        if "contact" in k:
            kind = "collision" if o["with"] is not None else "wall_contact"
        elif "spin" in k:
            kind = "lost_control"
        elif "off" in k:
            kind = "off_track"
        else:
            kind = "points_only"
        ev = {"t_session": round(o["t"], 2), "incident_type": kind,
              "points": o["points"], "detected": k,
              "spun": "spin" in k, "off_track": "off" in k}
        if o["peak_g"]:
            ev["peak_g"] = round(o["peak_g"], 1)
        if o["slip_deg"]:
            ev["slip_deg"] = round(o["slip_deg"])
        if o["speed_kmh"] is not None:
            ev["speed_kmh"] = o["speed_kmh"]
        if o["with"] is not None:
            ev["with_car_idx"] = o["with"]
        self.ready.append(ev)

    # ---- one sample ----------------------------------------------------
    def update(self, s: dict) -> None:
        """s: t, speed, vx, vy, yaw, long_acc, lat_acc, surface, on_pit,
        inc, pct, track_m, others = {car_idx: pct}, active (driving, live)."""
        t = s["t"]
        prev = self._prev
        if prev is not None and t < prev["t"] - 1.0:      # session / replay jump
            self.reset()
            prev = None
        if not s.get("active"):
            self._prev = None
            self._slip_since = self._off_since = None
            self._inc = s.get("inc", self._inc)
            self._flush(t)
            return

        speed = float(s.get("speed") or 0.0)
        kmh = round(speed * 3.6)
        on_pit = bool(s.get("on_pit"))

        # --- contact: velocity change no car can make by itself
        g_acc = math.hypot(s.get("long_acc") or 0.0, s.get("lat_acc") or 0.0) / G
        g_dv = 0.0
        if prev is not None and 0 < t - prev["t"] <= MAX_DT:
            a = _world_v(prev["vx"], prev["vy"], prev["yaw"])
            b = _world_v(s["vx"], s["vy"], s["yaw"])
            g_dv = math.hypot(b[0] - a[0], b[1] - a[1]) / (t - prev["t"]) / G
        peak = max(g_acc, g_dv)
        fast = max(speed, prev["speed"] if prev else 0.0)
        if peak >= CONTACT_G and fast >= CONTACT_MIN_MPS and not on_pit:
            self._note(t, "contact", peak_g=peak, speed_kmh=round(fast * 3.6),
                       **{"with": self._near_car(s)})

        # --- spin: moving sideways / backwards
        slip = 0.0
        if speed >= SPIN_MIN_MPS:
            slip = math.degrees(math.atan2(abs(s["vy"]), s["vx"]))
        if slip >= SPIN_SLIP_DEG and not on_pit:
            if self._slip_since is None:
                self._slip_since = t
            if t - self._slip_since >= SPIN_MIN_S and not self._spun_flag:
                self._spun_flag = True
                self._note(t, "spin", slip_deg=slip, speed_kmh=kmh)
            elif self._spun_flag and self._open is not None:
                self._open["slip_deg"] = max(self._open["slip_deg"], slip)
        elif slip < SPIN_SLIP_DEG / 2 or speed < 1.0:
            self._slip_since = None
            self._spun_flag = False

        # --- off track
        if s.get("surface") == SURF_OFF and speed >= OFF_MIN_MPS:
            if self._off_since is None:
                self._off_since = t
            if t - self._off_since >= OFF_MIN_S and not self._off_flag:
                self._off_flag = True
                self._note(t, "off", speed_kmh=kmh)
        elif s.get("surface") in (SURF_ON, 1, 2):
            self._off_since = None
            self._off_flag = False

        # --- iRacing's points go onto the incident they belong to
        inc = s.get("inc")
        if inc is not None and inc >= 0:
            if self._inc is not None and inc > self._inc:
                self._add_points(t, inc - self._inc)
            self._inc = inc

        self._prev = {"t": t, "vx": s["vx"], "vy": s["vy"], "yaw": s["yaw"],
                      "speed": speed}
        self._flush(t)

    def _add_points(self, t: float, n: int) -> None:
        o = self._open
        if o is not None and t - o["t"] <= HOLD_S + GROUP_S:
            o["points"] += n
            return
        # handed out within the last moment? (points came late)
        for ev in reversed(self.ready):
            if t - ev["t_session"] <= HOLD_S + GROUP_S:
                ev["points"] += n
                return
            break
        self._note(t, "points")
        self._open["points"] += n

    def _flush(self, t: float) -> None:
        o = self._open
        if o is not None and t - o["t_last"] >= HOLD_S:
            self._close()

    @staticmethod
    def _near_car(s: dict):
        others = s.get("others") or {}
        L = s.get("track_m") or 0.0
        pct = s.get("pct")
        if not others or not L or pct is None or pct < 0:
            return None
        best, best_d = None, CAR_NEAR_M
        for idx, p in others.items():
            if p is None or p < 0:
                continue
            d = abs(p - pct)
            d = min(d, 1.0 - d) * L
            if d <= best_d:
                best, best_d = idx, d
        return best

    def drain(self) -> list[dict]:
        out = list(self.ready)
        self.ready.clear()
        return out


def _track_m(ir) -> float:
    try:
        raw = str((ir["WeekendInfo"] or {}).get("TrackLength", "")).split()[0]
        km = float(raw)
        return km * 1000.0
    except Exception:
        return 0.0


class LocalCarWatch(threading.Thread):
    """Samples the local car at SAMPLE_HZ with its own SDK handle; the
    logger drains the finished incidents in its own loop."""

    def __init__(self, irsdk_module):
        super().__init__(daemon=True, name="local-incidents")
        self._irsdk = irsdk_module
        self.det = LocalIncidentDetector()
        self._lock = threading.Lock()
        self._running = True
        self.active = False         # a driving local car seen recently
        self.error = None

    def stop(self) -> None:
        self._running = False

    def drain(self) -> list[dict]:
        with self._lock:
            return self.det.drain()

    def run(self) -> None:
        ir = self._irsdk.IRSDK()
        connected = False
        track_m, track_t = 0.0, 0.0
        while self._running:
            try:
                if not (connected and ir.is_initialized and ir.is_connected):
                    if connected:
                        try:
                            ir.shutdown()
                        except Exception:
                            pass
                    connected = bool(ir.startup()) and ir.is_initialized and ir.is_connected
                    if not connected:
                        self.active = False
                        time.sleep(2.0)
                        continue
                ir.freeze_var_buffer_latest()
                now = time.monotonic()
                if now - track_t > 30:
                    track_m, track_t = _track_m(ir), now
                me = ir["PlayerCarIdx"]
                pct_arr = ir["CarIdxLapDistPct"] or []
                driving = bool(ir["IsOnTrack"]) and not bool(ir["IsReplayPlaying"])
                others = {}
                if driving and me is not None:
                    for i, p in enumerate(pct_arr):
                        if i != me and p is not None and p >= 0:
                            others[i] = p
                s = {
                    "t": float(ir["SessionTime"] or 0.0),
                    "active": driving,
                    "speed": ir["Speed"] or 0.0,
                    "vx": ir["VelocityX"] or 0.0,
                    "vy": ir["VelocityY"] or 0.0,
                    "yaw": ir["Yaw"] or 0.0,
                    "long_acc": ir["LongAccel"] or 0.0,
                    "lat_acc": ir["LatAccel"] or 0.0,
                    "surface": ir["PlayerTrackSurface"],
                    "on_pit": ir["OnPitRoad"],
                    "inc": ir["PlayerCarMyIncidentCount"],
                    "pct": pct_arr[me] if me is not None and me < len(pct_arr) else None,
                    "track_m": track_m,
                    "others": others,
                }
                self.active = driving
                with self._lock:
                    self.det.update(s)
                self.error = None
            except Exception as e:          # never take the logger down
                self.error = repr(e)
                time.sleep(1.0)
            time.sleep(1.0 / SAMPLE_HZ)
        try:
            ir.shutdown()
        except Exception:
            pass
