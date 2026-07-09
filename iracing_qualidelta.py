"""
iRacing Qualifying Delta Overlay
--------------------------------
A standalone OBS overlay that shows a LIVE qualifying delta time, in two
modes that it switches between automatically:

  DRIVING (you're in the car)
      Uses iRacing's own predictive delta for your car
      (LapDeltaToSessionBestLap) — the smoothest, most accurate delta.

  SPECTATOR / BROADCAST (you're watching)
      iRacing does NOT broadcast a ready-made delta for other cars, so we
      COMPUTE one for the on-camera driver: as cars run we sample each
      car's track position (CarIdxLapDistPct) against SessionTime to build
      a "time-at-each-point-of-the-lap" reference curve for the session
      best (pole) lap, then compare the on-camera car's live elapsed time
      at its current track position to that curve. Works for ANY driver —
      leader or not — and follows the iRacing camera automatically.
      (Sampled at ~15 Hz vs iRacing's internal 60 Hz, so it's broadcast-
      usable but a touch less precise than the driving-yourself delta.)

What it shows either way:
  * Big centre-zero delta (green = ahead, red/amber = behind) with a bar
    that fills toward the side you're gaining.
  * Per-sector split chips (green faster / red slower vs the reference
    lap's sectors; purple = new personal-best sector in driving mode).

TWO REFERENCE MODES (toggle with the M key, or use the /own path):
  * "vs POLE"      — delta to the SESSION best lap (the fastest car).
                     This is the default.
  * "vs OWN BEST"  — delta to the driver's OWN fastest lap this session,
                     so each driver runs against their own time. In
                     driving mode this uses iRacing's LapDeltaToBestLap;
                     while spectating it uses the on-camera car's own
                     best-lap reference curve.
  Both modes are computed at once, so you can run TWO OBS browser sources
  — one at http://localhost:5014 (pole) and one at
  http://localhost:5014/own (own best) — side by side. (The legacy
  ?ref=own query string still works, but /own is preferred for OBS
  browser sources, which handle query-string-free iframe URLs better.)

Requirements:  pip install pyirsdk flask
Run:           python iracing_qualidelta.py
Open:          http://localhost:5014        (vs pole)
               http://localhost:5014/own    (vs own best)
Stream:        transparent background by default (OBS browser source).
               Press H (or add ?debug=1) for a debug background.
               Press M to toggle pole / own-best reference.
"""

import bisect
import threading
from flask import Flask, jsonify, render_template_string

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()

PORT = 5014   # 5013 is the Driver-of-the-Day overlay; Quali Delta moved to 5014

# CarIdxTrackSurface values we care about
SURF_NOT_IN_WORLD = -1
SURF_OFF_TRACK = 0     # off-track surface — invalidates a reference lap


# -----------------------------------------------------------------------------
# Poller
# -----------------------------------------------------------------------------
class QualiDeltaPoller(SDKPoller):
    tag = "delta"

    def __init__(self, poll_hz: int = 15):
        super().__init__(poll_interval=1.0 / poll_hz)
        self._last_session_key = None
        self._reset_session()

    # --- per-session state ------------------------------------------------
    def _reset_session(self):
        self._sector_pcts = None          # [SectorStartPct, ...]
        # --- driving-mode (own car) sector state ---
        self._cur_lap = None
        self._lap_start_t = None
        self._sector_idx = 0
        self._sector_enter_t = None
        self._cur_lap_sectors = []
        self._best_lap_sectors = []
        self._best_lap_time = None
        self._optimal_sectors = []
        self._display_sectors = []
        self._lap_valid = False
        self._armed = False
        # --- spectator-mode state ---
        self._spec_reset()

    def _spec_reset(self):
        # per-car lap tracking for reference building
        self._car_lap = {}            # idx -> last lap number
        self._car_lap_start = {}      # idx -> SessionTime at lap start
        self._car_buf = {}            # idx -> [(pct, elapsed), ...] current lap
        self._car_valid = {}          # idx -> clean-lap flag
        self._car_last_buf = {}       # idx -> last completed CLEAN full-lap buffer
        self._car_last_meas = {}      # idx -> sampled time of that buffer
        # per-car own-best reference curves (idx -> dict). Every car that has
        # set a valid lap gets a scaled (pct -> elapsed) curve of its OWN best
        # lap; the pole reference below is simply the fastest car's curve.
        self._car_ref = {}
        # session-best (pole) reference curve
        self._ref_pcts = []           # sorted pct samples
        self._ref_times = []          # elapsed time at each pct (scaled to pole)
        self._ref_laptime = None
        self._ref_sectors = []        # pole lap sector times
        self._ref_src = None          # (car_idx, pole_time) the ref curve came from
        self._session_best = None     # official pole time (min CarIdxBestLapTime)
        self._ref_driver_idx = None   # car idx that holds the pole/reference lap
        # on-camera car sector tracking (raw times; colorized per reference)
        self._cam_idx = None
        self._cam_lap = None
        self._cam_sec_idx = 0
        self._cam_sec_enter_t = None
        self._cam_sec_times = []      # raw completed sector times (None = pending)

    def _load_sectors(self):
        """Sector boundaries from SplitTimeInfo; fall back to 3 equal
        sectors when iRacing doesn't expose them for this track."""
        pcts = []
        try:
            info = self.ir["SplitTimeInfo"] or {}
            for s in (info.get("Sectors") or []):
                p = s.get("SectorStartPct")
                if p is not None:
                    pcts.append(float(p))
        except Exception:
            pcts = []
        if not pcts:
            pcts = [0.0, 1.0 / 3.0, 2.0 / 3.0]
        pcts = sorted(set(pcts))
        if pcts[0] > 0.0001:
            pcts = [0.0] + pcts
        self._sector_pcts = pcts

    @staticmethod
    def _interp(pcts, times, pct):
        """Linear-interpolate elapsed time at a given track pct."""
        if not pcts:
            return None
        if pct <= pcts[0]:
            return times[0]
        if pct >= pcts[-1]:
            return times[-1]
        i = bisect.bisect_left(pcts, pct)
        p0, p1 = pcts[i - 1], pcts[i]
        t0, t1 = times[i - 1], times[i]
        return t0 if p1 == p0 else t0 + (t1 - t0) * (pct - p0) / (p1 - p0)

    # =====================================================================
    # DRIVING MODE (own car) — uses iRacing's predictive delta + own sectors
    # =====================================================================
    def _close_sector(self, i: int, t: float):
        sec_time = t - self._sector_enter_t
        self._cur_lap_sectors.append(sec_time)
        ref = self._best_lap_sectors[i] if i < len(self._best_lap_sectors) else None
        opt = self._optimal_sectors[i] if i < len(self._optimal_sectors) else None
        delta = (sec_time - ref) if ref is not None else None
        if opt is not None and sec_time < opt - 1e-4:
            state = "best"
        elif delta is not None and delta < 0:
            state = "faster"
        elif delta is not None:
            state = "slower"
        else:
            state = "neutral"
        if i < len(self._display_sectors):
            self._display_sectors[i] = {
                "idx": i + 1, "time": sec_time, "delta": delta, "state": state,
            }

    def _finalize_lap(self):
        n = len(self._sector_pcts)
        secs = self._cur_lap_sectors
        if len(secs) != n:
            return
        lap_time = self.ir["LapLastLapTime"]
        if not lap_time or lap_time <= 0:
            lap_time = sum(secs)
        if not self._optimal_sectors:
            self._optimal_sectors = list(secs)
        else:
            for i in range(n):
                if secs[i] < self._optimal_sectors[i]:
                    self._optimal_sectors[i] = secs[i]
        if self._lap_valid and lap_time > 0:
            if self._best_lap_time is None or lap_time < self._best_lap_time:
                self._best_lap_time = lap_time
                self._best_lap_sectors = list(secs)

    def _update_sectors(self, pct, lap, t, on_pit, on_track):
        if self._sector_pcts is None:
            self._load_sectors()
        n = len(self._sector_pcts)
        if self._cur_lap is None:
            self._cur_lap = lap
            return
        if lap > self._cur_lap or (lap != self._cur_lap and pct is not None and pct < 0.2):
            if self._armed and len(self._cur_lap_sectors) == n - 1:
                self._close_sector(n - 1, t)
                self._finalize_lap()
            self._cur_lap = lap
            self._lap_start_t = t
            self._sector_idx = 0
            self._sector_enter_t = t
            self._cur_lap_sectors = []
            self._lap_valid = on_track and not on_pit
            self._armed = True
            self._display_sectors = [
                {"idx": i + 1, "time": None, "delta": None, "state": "pending"}
                for i in range(n)
            ]
            return
        if not self._armed:
            return
        if not on_track or on_pit:
            self._lap_valid = False
        nxt = self._sector_idx + 1
        if nxt < n and pct is not None and pct >= self._sector_pcts[nxt]:
            self._close_sector(self._sector_idx, t)
            self._sector_idx = nxt
            self._sector_enter_t = t

    # =====================================================================
    # SPECTATOR MODE — homemade delta for the on-camera car vs pole
    # =====================================================================
    def _spec_update_refs(self, pcts, laps, on_pits, surfaces, bestlaps, t):
        """Sample every car's lap into a (pct -> elapsed) buffer and stash the
        last CLEAN full lap per car. The pole reference curve is then chosen
        by `_update_pole_reference` from iRacing's official session best."""
        for idx in range(len(pcts)):
            surf = surfaces[idx] if idx < len(surfaces) else SURF_NOT_IN_WORLD
            if surf == SURF_NOT_IN_WORLD:
                continue
            pct = pcts[idx]
            lap = laps[idx] if idx < len(laps) else None
            if pct is None or lap is None:
                continue
            on_pit = bool(on_pits[idx]) if idx < len(on_pits) else False

            if idx not in self._car_lap:
                self._car_lap[idx] = lap
                self._car_lap_start[idx] = t
                self._car_buf[idx] = []
                self._car_valid[idx] = (surf > SURF_OFF_TRACK) and not on_pit
                continue

            if lap > self._car_lap[idx]:
                buf = self._car_buf[idx]
                # Keep this lap's curve only if it was clean and covered a full
                # lap; whether it counts as POLE is decided later from the
                # official CarIdxBestLapTime (so deleted laps never win).
                if self._car_valid[idx] and len(buf) >= 10 \
                        and buf[0][0] <= 0.05 and buf[-1][0] >= 0.95:
                    self._car_last_buf[idx] = buf
                    self._car_last_meas[idx] = t - self._car_lap_start[idx]
                self._car_lap[idx] = lap
                self._car_lap_start[idx] = t
                self._car_buf[idx] = []
                self._car_valid[idx] = (surf > SURF_OFF_TRACK) and not on_pit
                continue

            # same lap → sample
            if surf == SURF_OFF_TRACK or on_pit:
                self._car_valid[idx] = False
            elapsed = t - self._car_lap_start[idx]
            buf = self._car_buf[idx]
            if not buf or pct > buf[-1][0]:
                buf.append((pct, elapsed))
            elif pct < buf[-1][0] - 0.5:
                self._car_valid[idx] = False   # teleport / reset glitch

        self._update_pole_reference(bestlaps)

    def _store_car_ref(self, idx, best):
        """Build/refresh car `idx`'s OWN best-lap reference curve from its most
        recent clean lap, but only when that lap IS its official best (so a
        later in/out lap can't overwrite the reference). `CarIdxBestLapTime`
        only holds VALID laps, so a fast-but-deleted (track-limits) lap never
        becomes the reference. The curve is scaled so its total equals the
        official best time exactly."""
        buf = self._car_last_buf.get(idx)
        meas = self._car_last_meas.get(idx)
        if not buf or meas is None or meas <= 0:
            return
        # The car's most recent clean lap must BE its best lap (not a later
        # in/out lap) — its sampled time should match the official best.
        if abs(meas - best) > 0.4:
            return
        src = (idx, round(best, 3))
        existing = self._car_ref.get(idx)
        if existing and existing.get("src") == src:
            return   # already using this exact best lap
        raw_times = [e for _, e in buf]
        total = raw_times[-1] if raw_times else 0.0
        factor = (best / total) if total > 0 else 1.0
        pcts = [p for p, _ in buf]
        times = [e * factor for e in raw_times]   # scale total -> official best
        self._car_ref[idx] = {
            "pcts": pcts,
            "times": times,
            "laptime": best,
            "sectors": self._compute_ref_sectors(pcts, times),
            "src": src,
        }

    def _update_pole_reference(self, bestlaps):
        """Refresh every car's own-best reference curve, then set the POLE
        reference to the fastest car's curve (min CarIdxBestLapTime)."""
        best = None
        pole_idx = None
        for idx in range(len(bestlaps)):
            b = bestlaps[idx]
            if b and b > 0:
                self._store_car_ref(idx, b)
                if best is None or b < best:
                    best = b
                    pole_idx = idx
        self._session_best = best
        if best is None or pole_idx is None:
            return
        self._ref_driver_idx = pole_idx   # who's on pole (for the header)
        r = self._car_ref.get(pole_idx)
        if not r:
            return
        self._ref_pcts = r["pcts"]
        self._ref_times = r["times"]
        self._ref_laptime = r["laptime"]
        self._ref_sectors = r["sectors"]
        self._ref_src = r["src"]

    def _compute_ref_sectors(self, pcts, times):
        if self._sector_pcts is None:
            self._load_sectors()
        bounds = list(self._sector_pcts) + [1.0]
        secs = []
        for i in range(len(self._sector_pcts)):
            ta = self._interp(pcts, times, bounds[i])
            tb = self._interp(pcts, times, bounds[i + 1])
            if ta is None or tb is None:
                return []
            secs.append(tb - ta)
        return secs

    def _spec_cam_delta(self, cam, ref_pcts, ref_times, pcts, on_pits, surfaces, t):
        """Live delta of the on-camera car against a given reference curve
        (`ref_pcts`/`ref_times`). Used for both the pole curve and the car's
        own best-lap curve."""
        if cam is None or cam < 0 or not ref_pcts:
            return None, False
        if cam not in self._car_lap_start:
            return None, False
        surf = surfaces[cam] if cam < len(surfaces) else SURF_NOT_IN_WORLD
        on_pit = bool(on_pits[cam]) if cam < len(on_pits) else False
        pct = pcts[cam] if cam < len(pcts) else None
        if pct is None or surf <= SURF_OFF_TRACK or on_pit:
            return None, False
        elapsed = t - self._car_lap_start[cam]
        ref = self._interp(ref_pcts, ref_times, pct)
        if ref is None:
            return None, False
        return (elapsed - ref), True

    def _spec_cam_sector_times(self, cam, pcts, laps, t):
        """Track the on-camera car's raw sector times (independent of any
        reference). Returns a list of completed sector times (None = pending).
        Colorize it against a reference with `_colorize_sectors`."""
        if self._sector_pcts is None:
            self._load_sectors()
        n = len(self._sector_pcts)
        if cam is None or cam < 0:
            self._cam_idx = None
            return []
        pct = pcts[cam] if cam < len(pcts) else None
        lap = laps[cam] if cam < len(laps) else None
        if pct is None or lap is None:
            return self._cam_sec_times
        # camera switched OR the watched car started a new lap → reset times
        if cam != self._cam_idx or lap != self._cam_lap:
            self._cam_idx = cam
            self._cam_lap = lap
            self._cam_sec_idx = 0
            self._cam_sec_enter_t = self._car_lap_start.get(cam, t)
            self._cam_sec_times = [None] * n
            return self._cam_sec_times
        nxt = self._cam_sec_idx + 1
        if nxt < n and pct >= self._sector_pcts[nxt]:
            sec_time = t - self._cam_sec_enter_t
            if self._cam_sec_idx < len(self._cam_sec_times):
                self._cam_sec_times[self._cam_sec_idx] = sec_time
            self._cam_sec_idx = nxt
            self._cam_sec_enter_t = t
        return self._cam_sec_times

    @staticmethod
    def _colorize_sectors(times, ref_sectors):
        """Turn raw sector times + a reference sector list into display chips
        (faster / slower / neutral / pending)."""
        out = []
        for i, st in enumerate(times or []):
            if st is None:
                out.append({"idx": i + 1, "time": None, "delta": None,
                            "state": "pending"})
                continue
            ref = ref_sectors[i] if ref_sectors and i < len(ref_sectors) else None
            delta = (st - ref) if ref is not None else None
            if delta is not None and delta < 0:
                state = "faster"
            elif delta is not None:
                state = "slower"
            else:
                state = "neutral"
            out.append({"idx": i + 1, "time": st, "delta": delta, "state": state})
        return out

    def _cam_driver(self, cam):
        if cam is None or cam < 0:
            return ""
        try:
            drivers = self.ir["DriverInfo"]["Drivers"] if self.ir["DriverInfo"] else []
            for d in drivers:
                if d.get("CarIdx") == cam:
                    num = d.get("CarNumber", "")
                    name = d.get("UserName", "") or ""
                    parts = name.split()
                    if len(parts) >= 2:
                        name = parts[0][0] + ". " + parts[-1]
                    prefix = ("#" + str(num) + " ") if num != "" else ""
                    return prefix + name
        except Exception:
            pass
        return ""

    @staticmethod
    def _lic_hex(col):
        """iRacing's LicColor comes as an int or a '0x..'/'#..' string.
        Return a '#rrggbb' hex, or None if it can't be parsed."""
        if col is None:
            return None
        try:
            if isinstance(col, str):
                s = col.strip()
                if s.startswith("#"):
                    return s
                v = int(s, 16) if s.lower().startswith("0x") else int(s)
            else:
                v = int(col)
            return "#%06x" % (v & 0xFFFFFF)
        except Exception:
            return None

    def _driver_info(self, idx):
        """Broadcast header fields for a car index: number, SURNAME (caps),
        license letter + color. Safe defaults when the car isn't found."""
        out = {"number": "", "surname": "", "lic": "", "lic_color": "#27d367"}
        if idx is None or idx < 0:
            return out
        try:
            drivers = self.ir["DriverInfo"]["Drivers"] if self.ir["DriverInfo"] else []
            for d in drivers:
                if d.get("CarIdx") == idx:
                    out["number"] = str(d.get("CarNumber", ""))
                    name = (d.get("UserName") or "").strip()
                    if name:
                        out["surname"] = name.split()[-1].upper()
                    lic = (d.get("LicString") or "").strip()
                    if lic:
                        out["lic"] = lic[0].upper()
                    hexcol = self._lic_hex(d.get("LicColor"))
                    if hexcol:
                        out["lic_color"] = hexcol
                    break
        except Exception:
            pass
        return out

    # --- session label ----------------------------------------------------
    def _session_label(self):
        try:
            info = self.ir["SessionInfo"] or {}
            num = self.ir["SessionNum"]
            for s in info.get("Sessions", []) or []:
                if s.get("SessionNum") == num:
                    return (s.get("SessionType") or "").upper()
        except Exception:
            pass
        return ""

    # --- snapshot ---------------------------------------------------------
    def _read_snapshot(self) -> dict:
        ir = self.ir
        ir.freeze_var_buffer_latest()

        key = (ir["SessionUniqueID"], ir["SessionNum"])
        if key != self._last_session_key:
            self._reset_session()
            self._last_session_key = key

        label = self._session_label()
        on_track = bool(ir["IsOnTrack"])

        # ── DRIVING MODE ────────────────────────────────────────────────
        if on_track:
            pct = ir["LapDistPct"]
            lap = ir["Lap"]
            t = ir["SessionTime"]
            on_pit = bool(ir["OnPitRoad"])
            if pct is not None and lap is not None and t is not None:
                self._update_sectors(pct, lap, t, on_pit, on_track)
            # vs POLE (session best) — iRacing's predictive session-best delta
            s_delta = ir["LapDeltaToSessionBestLap"]
            s_ok = bool(ir["LapDeltaToSessionBestLap_OK"])
            # vs OWN BEST — iRacing's predictive own-best delta
            o_delta = ir["LapDeltaToBestLap"]
            o_ok = bool(ir["LapDeltaToBestLap_OK"])
            own_best = ir["LapBestLapTime"] or 0.0
            try:
                dci = self.ir["DriverInfo"]["DriverCarIdx"] if self.ir["DriverInfo"] else None
            except Exception:
                dci = None
            info = self._driver_info(dci)
            # Sector chips in driving mode are already computed vs the driver's
            # own best lap, so both reference views share them.
            return {
                "connected": True,
                "mode": "driving",
                "watching": "YOU",
                "session_label": label,
                "last_lap": ir["LapLastLapTime"] or 0.0,
                "pos": int(ir["PlayerCarPosition"] or 0),
                "number": info["number"],
                "surname": info["surname"] or "YOU",
                "lic": info["lic"],
                "lic_color": info["lic_color"],
                "refs": {
                    "session": {
                        "delta": float(s_delta) if s_delta is not None else None,
                        "delta_ok": s_ok,
                        "sectors": self._display_sectors,
                        "have_reference": True,
                        "ref_label": "Pole",
                        "ref_lap": own_best,
                        "ref_driver": "",
                    },
                    "own": {
                        "delta": float(o_delta) if o_delta is not None else None,
                        "delta_ok": o_ok,
                        "sectors": self._display_sectors,
                        "have_reference": own_best > 0,
                        "ref_label": "Own Best",
                        "ref_lap": own_best,
                        "ref_driver": "",
                    },
                },
            }

        # ── SPECTATOR MODE ──────────────────────────────────────────────
        pcts = ir["CarIdxLapDistPct"] or []
        laps = ir["CarIdxLap"] or []
        on_pits = ir["CarIdxOnPitRoad"] or []
        surfaces = ir["CarIdxTrackSurface"] or []
        lastlaps = ir["CarIdxLastLapTime"] or []
        bestlaps = ir["CarIdxBestLapTime"] or []
        positions = ir["CarIdxPosition"] or []
        t = ir["SessionTime"] or 0.0

        self._spec_update_refs(pcts, laps, on_pits, surfaces, bestlaps, t)
        cam = ir["CamCarIdx"]

        # vs POLE — the session-best (fastest car) reference curve
        s_delta, s_ok = self._spec_cam_delta(
            cam, self._ref_pcts, self._ref_times, pcts, on_pits, surfaces, t)
        # vs OWN BEST — the on-camera car's own best-lap reference curve
        own_ref = self._car_ref.get(cam) if cam is not None else None
        if own_ref:
            o_delta, o_ok = self._spec_cam_delta(
                cam, own_ref["pcts"], own_ref["times"], pcts, on_pits, surfaces, t)
        else:
            o_delta, o_ok = None, False

        # raw sector times are reference-independent → colorize per reference
        sec_times = self._spec_cam_sector_times(cam, pcts, laps, t)
        s_sectors = self._colorize_sectors(sec_times, self._ref_sectors)
        o_sectors = self._colorize_sectors(
            sec_times, own_ref["sectors"] if own_ref else [])

        cam_last = 0.0
        if cam is not None and 0 <= cam < len(lastlaps):
            cam_last = lastlaps[cam] if lastlaps[cam] and lastlaps[cam] > 0 else 0.0
        cam_best = 0.0
        if cam is not None and 0 <= cam < len(bestlaps):
            cam_best = bestlaps[cam] if bestlaps[cam] and bestlaps[cam] > 0 else 0.0

        info = self._driver_info(cam)
        ref_info = self._driver_info(self._ref_driver_idx)
        pos = 0
        if cam is not None and 0 <= cam < len(positions) and positions[cam]:
            pos = positions[cam]

        return {
            "connected": True,
            "mode": "spectator",
            "watching": self._cam_driver(cam),
            "session_label": label,
            "last_lap": cam_last,
            "pos": int(pos),
            "number": info["number"],
            "surname": info["surname"],
            "lic": info["lic"],
            "lic_color": info["lic_color"],
            "refs": {
                "session": {
                    "delta": float(s_delta) if s_delta is not None else None,
                    "delta_ok": s_ok,
                    "sectors": s_sectors,
                    "have_reference": bool(self._ref_pcts),
                    "ref_label": "Pole",
                    "ref_lap": self._session_best or 0.0,
                    "ref_driver": ref_info["surname"],
                },
                "own": {
                    "delta": float(o_delta) if o_delta is not None else None,
                    "delta_ok": o_ok,
                    "sectors": o_sectors,
                    "have_reference": bool(own_ref),
                    "ref_label": "Own Best",
                    "ref_lap": cam_best,
                    "ref_driver": "",
                },
            },
        }


# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
poller = QualiDeltaPoller(poll_hz=15)


@app.after_request
def _no_cache(resp):
    if "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Quali Delta</title>
<style>
    :root {
        --green:  #27d367;
        --red:    #ff4d4d;
        --purple: #b06bff;
        --amber:  #ffcf33;
        --panel:  rgba(10, 10, 14, 0.90);   /* dark broadcast bar */
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
        width: 100%; height: 100%;
        background: transparent;
        font-family: 'Segoe UI', system-ui, sans-serif;
        color: #fff; overflow: hidden;
        font-variant-numeric: tabular-nums;
    }
    body { display: flex; align-items: center; justify-content: center; padding: 16px; }
    body.debug { background: #15151c; }

    .card {
        display: inline-block; min-width: 430px; user-select: none;
        border-radius: 6px; overflow: hidden;
        box-shadow: 0 6px 26px rgba(0, 0, 0, 0.6);
    }
    body.debug .card { box-shadow: 0 0 0 1px rgba(255,255,255,0.12), 0 6px 26px rgba(0,0,0,0.6); }

    /* ---- main body: left (driver + delta) | right (reference) ---- */
    .body { display: flex; background: var(--panel); }
    .left { flex: 1 1 auto; padding: 11px 16px 13px; min-width: 0; }
    .right {
        flex: 0 0 auto; min-width: 124px;
        display: flex; flex-direction: column; justify-content: center;
        padding: 11px 18px; text-align: right;
        border-left: 2px solid rgba(255, 255, 255, 0.10);
    }

    .top { display: flex; align-items: center; gap: 11px; }
    .pos {
        flex: 0 0 auto; min-width: 30px; height: 30px; padding: 0 7px;
        display: flex; align-items: center; justify-content: center;
        background: var(--green); color: #06210f;
        font-weight: 900; font-size: 20px; border-radius: 5px;
    }
    .name {
        font-size: 27px; font-weight: 800; letter-spacing: 1px; color: #fff;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        text-shadow: 0 2px 8px rgba(0,0,0,0.5);
    }
    .lic {
        flex: 0 0 auto; width: 24px; height: 24px; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-size: 13px; font-weight: 900; color: #fff;
        background: var(--green);
        box-shadow: inset 0 0 0 2px rgba(255, 255, 255, 0.22);
    }

    .delta-big {
        margin-top: 8px; font-size: 52px; line-height: 1; font-weight: 900;
        letter-spacing: 1px; color: var(--amber);
        text-shadow: 0 3px 12px rgba(0, 0, 0, 0.55);
    }
    .delta-big.ahead  { color: var(--green); }
    .delta-big.behind { color: var(--amber); }

    .ref-name {
        font-size: 14px; font-weight: 700; letter-spacing: 1px; color: #9aa0b4;
        text-transform: uppercase; white-space: nowrap;
    }
    .ref-time { font-size: 26px; font-weight: 800; color: #fff; margin-top: 3px; }

    /* ---- bottom sector strip ---- */
    .sectors { display: flex; gap: 2px; background: var(--panel); padding: 0 2px 2px; }
    .sector {
        flex: 1 1 0; min-width: 0; height: 26px;
        display: flex; align-items: center; justify-content: center;
        font-size: 12px; font-weight: 800; letter-spacing: 1px;
        text-transform: uppercase; color: #cfd3e0;
        background: rgba(255, 255, 255, 0.07);
        border-bottom: 3px solid rgba(255, 255, 255, 0.14);
    }
    .sector.faster  { background: rgba(39, 211, 103, 0.24); border-color: var(--green);  color: #e3fcec; }
    .sector.slower  { background: rgba(255, 77, 77, 0.18);  border-color: var(--red);    color: #ffe6e6; }
    .sector.best    { background: rgba(176, 107, 255, 0.24); border-color: var(--purple); color: #f0e6ff; }
    .sector.current { background: rgba(255, 207, 51, 0.16); border-color: var(--amber);  color: #fff; }
    .sector.pending { opacity: 0.5; }

    .note {
        background: var(--panel); color: #6f93c9;
        font-size: 12px; font-weight: 700; text-align: center; padding: 4px 0 8px;
    }
    .idle {
        background: var(--panel); text-align: center;
        font-size: 20px; font-weight: 700; color: #5a6072;
        letter-spacing: 1px; padding: 22px 28px;
    }
    .hidden { display: none !important; }
</style>
</head>
<body>

<div class="card" id="card">
    <div id="live">
        <div class="body">
            <div class="left">
                <div class="top">
                    <div class="pos" id="pos">—</div>
                    <div class="name" id="surname">—</div>
                    <div class="lic" id="lic">—</div>
                </div>
                <div class="delta-big" id="delta">—</div>
            </div>
            <div class="right">
                <div class="ref-name" id="ref-driver">—</div>
                <div class="ref-time" id="ref-time">—</div>
            </div>
        </div>
        <div class="sectors" id="sectors"></div>
        <div class="note hidden" id="note"></div>
    </div>

    <div class="idle hidden" id="idle">Waiting for iRacing…</div>
</div>

<script>
function fmtLap(sec) {
    if (!sec || sec <= 0) return "—";
    const m = Math.floor(sec / 60);
    const s = (sec - m * 60).toFixed(3).padStart(6, "0");
    return m + ":" + s;
}

function renderHeader(d) {
    const posEl = document.getElementById("pos");
    if (d.pos && d.pos > 0) { posEl.textContent = d.pos; posEl.classList.remove("hidden"); }
    else posEl.classList.add("hidden");

    document.getElementById("surname").textContent =
        d.surname || d.watching || "—";

    const licEl = document.getElementById("lic");
    if (d.lic) {
        licEl.textContent = d.lic;
        licEl.style.background = d.lic_color || "var(--green)";
        licEl.classList.remove("hidden");
    } else licEl.classList.add("hidden");
}

function renderDelta(d, ok) {
    const el = document.getElementById("delta");
    el.classList.remove("ahead", "behind");
    if (!ok || d == null) { el.textContent = "—"; return; }
    el.textContent = (d >= 0 ? "+" : "") + d.toFixed(3);
    if (d < -0.005) el.classList.add("ahead");        // ahead of pole → green
    else if (d > 0.005) el.classList.add("behind");   // behind → amber (broadcast look)
}

function renderRef(d) {
    // Right block: who set the reference + their lap. In driving mode there's
    // no named holder, so we show the label ("Best") instead of a name.
    document.getElementById("ref-driver").textContent =
        d.ref_driver || d.ref_label || "BEST";
    document.getElementById("ref-time").textContent = fmtLap(d.ref_lap);
}

function renderSectors(secs) {
    const wrap = document.getElementById("sectors");
    secs = secs || [];
    if (wrap.children.length !== secs.length) {
        wrap.innerHTML = "";
        secs.forEach(() => {
            const cell = document.createElement("div");
            cell.className = "sector";
            wrap.appendChild(cell);
        });
    }
    // The active sector = first one not yet completed.
    let curIdx = -1;
    for (let i = 0; i < secs.length; i++) {
        if (secs[i].state === "pending" || secs[i].time == null) { curIdx = i; break; }
    }
    secs.forEach((s, i) => {
        const cell = wrap.children[i];
        if (i === curIdx) {
            cell.className = "sector current";
            cell.textContent = "SECTOR " + s.idx;     // in-progress shows full word
        } else if (s.state === "pending" || s.time == null) {
            cell.className = "sector pending";
            cell.textContent = "S" + s.idx;
        } else {
            cell.className = "sector " + (s.state || "neutral");
            cell.textContent = "S" + s.idx;           // colour encodes faster/slower/best
        }
    });
}

// Keep the last good frame on screen through brief blips (a dropped request
// or a single not-connected poll) instead of flashing the "Waiting…" state.
let badPolls = 0;
const BAD_LIMIT = 8;   // ~0.8s of trouble before we show the idle state

function showIdle(msg) {
    document.getElementById("live").classList.add("hidden");
    const idle = document.getElementById("idle");
    idle.classList.remove("hidden");
    idle.textContent = msg;
}

async function tick() {
    let d = null;
    try {
        const r = await fetch("/status");
        d = await r.json();
    } catch (e) {
        d = null;
    }

    if (!d || !d.connected) {
        badPolls++;
        if (badPolls < BAD_LIMIT) return;          // transient — hold last frame
        showIdle(!d ? "Waiting for server…" : "Waiting for iRacing…");
        return;
    }
    badPolls = 0;

    document.getElementById("live").classList.remove("hidden");
    document.getElementById("idle").classList.add("hidden");

    // Pick the active reference view (vs pole / vs own best) and merge it over
    // the shared header fields. Fall back to a flat payload for safety.
    const ref = (d.refs && (d.refs[refMode] || d.refs.session)) || d;
    const view = Object.assign({}, d, ref);

    renderHeader(view);
    renderDelta(view.delta, view.delta_ok);
    renderRef(view);
    renderSectors(view.sectors);

    // Status note while spectating before a reference lap exists
    const note = document.getElementById("note");
    if (view.mode === "spectator" && !view.have_reference) {
        note.classList.remove("hidden");
        note.textContent = (refMode === "own")
            ? "Building this driver's best lap…"
            : "Building reference lap…";
    } else if (view.mode === "spectator" && !view.delta_ok) {
        note.classList.remove("hidden");
        note.textContent = "Waiting for a flying lap…";
    } else {
        note.classList.add("hidden");
    }
}

// Reference mode: "session" (vs pole, default) or "own" (vs own best lap).
// Set with the /own path OR ?ref=own in the URL, or toggle live with the M key.
// The /own path is preferred for OBS loaders (no query string needed, which
// OBS browser sources handle more reliably inside an iframe).
let refMode = (location.pathname.replace(/\/+$/, "") === "/own"
    || new URLSearchParams(location.search).get("ref") === "own")
    ? "own" : "session";

// Stream mode: transparent by default; H (or ?debug=1) shows a debug bg.
function applyDebug(on) { document.body.classList.toggle("debug", on); }
document.addEventListener("keydown", e => {
    if (e.key === "h" || e.key === "H") applyDebug(!document.body.classList.contains("debug"));
    if (e.key === "m" || e.key === "M") {
        refMode = (refMode === "own") ? "session" : "own";
        tick();
    }
});
if (new URLSearchParams(location.search).get("debug") === "1") applyDebug(true);

setInterval(tick, 100);
tick();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE_HTML)


@app.route("/own")
def index_own():
    # Same page, but the front-end reads the /own path and defaults to the
    # OWN-BEST reference. Lets OBS loaders use a clean query-string-free URL.
    return render_template_string(PAGE_HTML)


@app.route("/status")
def status():
    return jsonify(poller.get())


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()

    print("\n" + "=" * 60)
    print("  iRacing Qualifying Delta Overlay")
    print(f"  vs POLE:      http://localhost:{PORT}")
    print(f"  vs OWN BEST:  http://localhost:{PORT}/own")
    print("  Transparent background — add as an OBS browser source.")
    print("  DRIVING: iRacing predictive delta for your own car.")
    print("  SPECTATING: computed delta for the on-camera car.")
    print("  Reference: POLE (session best) or OWN BEST — press M to toggle.")
    print("  Press H (or ?debug=1) for a debug background.")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        # threaded=True so the 10 Hz browser polling and the SDK poller thread
        # don't contend — a single-threaded dev server can briefly stall under
        # the fast polling, which shows up as the overlay blinking in OBS.
        app.run(host="0.0.0.0", port=PORT, debug=False,
                use_reloader=False, threaded=True)
    finally:
        poller.stop()
