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
  * Big centre-zero delta to the SESSION best lap (green = ahead of pole,
    red = behind) with a bar that fills toward the side you're gaining.
  * Per-sector split chips (green faster / red slower vs the pole lap's
    sectors; purple = new personal-best sector in driving mode).

Requirements:  pip install pyirsdk flask
Run:           python iracing_qualidelta.py
Open:          http://localhost:5013
Stream:        transparent background by default (OBS browser source).
               Press H (or add ?debug=1) for a debug background.
"""

import bisect
import threading
from flask import Flask, jsonify, render_template_string

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()

PORT = 5013

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
        # session-best (pole) reference curve
        self._ref_pcts = []           # sorted pct samples
        self._ref_times = []          # elapsed time at each pct (scaled to pole)
        self._ref_laptime = None
        self._ref_sectors = []        # pole lap sector times
        self._ref_src = None          # (car_idx, pole_time) the ref curve came from
        self._session_best = None     # official pole time (min CarIdxBestLapTime)
        # on-camera car sector tracking
        self._cam_idx = None
        self._cam_lap = None
        self._cam_sec_idx = 0
        self._cam_sec_enter_t = None
        self._cam_display = []

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

    def _update_pole_reference(self, bestlaps):
        """Pick the pole reference from iRacing's OFFICIAL session best.
        `CarIdxBestLapTime` only holds VALID laps, so a fast-but-deleted
        (track-limits) lap never becomes pole. The displayed pole time is
        this official value; the reference curve is the pole car's matching
        clean lap, scaled so its total equals the official time exactly."""
        best = None
        pole_idx = None
        for idx in range(len(bestlaps)):
            b = bestlaps[idx]
            if b and b > 0 and (best is None or b < best):
                best = b
                pole_idx = idx
        self._session_best = best
        if best is None or pole_idx is None:
            return
        buf = self._car_last_buf.get(pole_idx)
        meas = self._car_last_meas.get(pole_idx)
        if not buf or meas is None or meas <= 0:
            return
        # The pole car's most recent clean lap must BE the pole lap (not a
        # later in/out lap) — its sampled time should match the official best.
        if abs(meas - best) > 0.4:
            return
        src = (pole_idx, round(best, 3))
        if src == self._ref_src:
            return   # already using this exact pole lap
        raw_times = [e for _, e in buf]
        total = raw_times[-1] if raw_times else 0.0
        factor = (best / total) if total > 0 else 1.0
        self._ref_pcts = [p for p, _ in buf]
        self._ref_times = [e * factor for e in raw_times]   # scale total -> official pole
        self._ref_laptime = best
        self._ref_sectors = self._compute_ref_sectors(self._ref_pcts, self._ref_times)
        self._ref_src = src

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

    def _spec_cam_delta(self, cam, pcts, on_pits, surfaces, t):
        if cam is None or cam < 0 or not self._ref_pcts:
            return None, False
        if cam not in self._car_lap_start:
            return None, False
        surf = surfaces[cam] if cam < len(surfaces) else SURF_NOT_IN_WORLD
        on_pit = bool(on_pits[cam]) if cam < len(on_pits) else False
        pct = pcts[cam] if cam < len(pcts) else None
        if pct is None or surf <= SURF_OFF_TRACK or on_pit:
            return None, False
        elapsed = t - self._car_lap_start[cam]
        ref = self._interp(self._ref_pcts, self._ref_times, pct)
        if ref is None:
            return None, False
        return (elapsed - ref), True

    def _spec_cam_sectors(self, cam, pcts, laps, t):
        if self._sector_pcts is None:
            self._load_sectors()
        n = len(self._sector_pcts)
        if cam is None or cam < 0:
            self._cam_idx = None
            return []
        pct = pcts[cam] if cam < len(pcts) else None
        lap = laps[cam] if cam < len(laps) else None
        if pct is None or lap is None:
            return self._cam_display
        # camera switched OR the watched car started a new lap → reset chips
        if cam != self._cam_idx or lap != self._cam_lap:
            self._cam_idx = cam
            self._cam_lap = lap
            self._cam_sec_idx = 0
            self._cam_sec_enter_t = self._car_lap_start.get(cam, t)
            self._cam_display = [
                {"idx": i + 1, "time": None, "delta": None, "state": "pending"}
                for i in range(n)
            ]
            return self._cam_display
        nxt = self._cam_sec_idx + 1
        if nxt < n and pct >= self._sector_pcts[nxt]:
            sec_time = t - self._cam_sec_enter_t
            ref = self._ref_sectors[self._cam_sec_idx] if self._cam_sec_idx < len(self._ref_sectors) else None
            delta = (sec_time - ref) if ref is not None else None
            if delta is not None and delta < 0:
                state = "faster"
            elif delta is not None:
                state = "slower"
            else:
                state = "neutral"
            if self._cam_sec_idx < len(self._cam_display):
                self._cam_display[self._cam_sec_idx] = {
                    "idx": self._cam_sec_idx + 1, "time": sec_time,
                    "delta": delta, "state": state,
                }
            self._cam_sec_idx = nxt
            self._cam_sec_enter_t = t
        return self._cam_display

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
            delta = ir["LapDeltaToSessionBestLap"]
            delta_ok = bool(ir["LapDeltaToSessionBestLap_OK"])
            return {
                "connected": True,
                "mode": "driving",
                "watching": "YOU",
                "session_label": label,
                "delta": float(delta) if delta is not None else None,
                "delta_ok": delta_ok,
                "sectors": self._display_sectors,
                "have_reference": True,
                "ref_label": "Best",
                "ref_lap": ir["LapBestLapTime"] or 0.0,
                "last_lap": ir["LapLastLapTime"] or 0.0,
            }

        # ── SPECTATOR MODE ──────────────────────────────────────────────
        pcts = ir["CarIdxLapDistPct"] or []
        laps = ir["CarIdxLap"] or []
        on_pits = ir["CarIdxOnPitRoad"] or []
        surfaces = ir["CarIdxTrackSurface"] or []
        lastlaps = ir["CarIdxLastLapTime"] or []
        bestlaps = ir["CarIdxBestLapTime"] or []
        t = ir["SessionTime"] or 0.0

        self._spec_update_refs(pcts, laps, on_pits, surfaces, bestlaps, t)
        cam = ir["CamCarIdx"]
        delta, ok = self._spec_cam_delta(cam, pcts, on_pits, surfaces, t)
        sectors = self._spec_cam_sectors(cam, pcts, laps, t)
        cam_last = 0.0
        if cam is not None and 0 <= cam < len(lastlaps):
            cam_last = lastlaps[cam] if lastlaps[cam] and lastlaps[cam] > 0 else 0.0

        return {
            "connected": True,
            "mode": "spectator",
            "watching": self._cam_driver(cam),
            "session_label": label,
            "delta": float(delta) if delta is not None else None,
            "delta_ok": ok,
            "sectors": sectors,
            "have_reference": bool(self._ref_pcts),
            "ref_label": "Pole",
            "ref_lap": self._session_best or 0.0,
            "last_lap": cam_last,
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
        --green: #19d36b;
        --red:   #ff4d4d;
        --purple:#b06bff;
        --amber: #ffd166;
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
        display: inline-flex; flex-direction: column; gap: 8px;
        padding: 16px 22px; border-radius: 14px;
        /* Semi-opaque panel so the text stays readable over gameplay.
           Raise/lower the last value (0=clear, 1=solid) to taste. */
        background: rgba(14, 14, 20, 0.80);
        border: 2px solid rgba(255,255,255,0.12);
        box-shadow: 0 4px 24px rgba(0,0,0,0.55);
        min-width: 380px; user-select: none;
    }
    body.debug .card {
        background: rgba(14, 14, 20, 0.94);
        border-color: rgba(255,255,255,0.18);
    }

    .head {
        display: flex; align-items: flex-start; justify-content: space-between;
        gap: 16px;
    }
    .head .title {
        font-size: 13px; font-weight: 700; letter-spacing: 2px;
        color: #9aa0b4; text-transform: uppercase; display: block;
    }
    .head .watching {
        font-size: 15px; font-weight: 800; letter-spacing: .5px;
        color: #e8ebf2; display: block; margin-top: 2px;
    }
    .head .sess {
        font-size: 13px; font-weight: 800; letter-spacing: 1.5px;
        color: var(--amber); text-transform: uppercase; white-space: nowrap;
    }

    .delta-big {
        font-size: 64px; font-weight: 900; line-height: 1;
        text-align: center; letter-spacing: 1px;
        text-shadow: 0 3px 14px rgba(0,0,0,0.6);
        color: #d6dae6;
    }
    .delta-big.ahead  { color: var(--green); }
    .delta-big.behind { color: var(--red); }

    .bar {
        position: relative; height: 16px; border-radius: 8px;
        background: rgba(255,255,255,0.10); overflow: hidden;
    }
    .bar .center {
        position: absolute; left: 50%; top: -2px; bottom: -2px;
        width: 2px; background: rgba(255,255,255,0.55);
        transform: translateX(-1px); z-index: 2;
    }
    .bar .fill {
        position: absolute; top: 0; bottom: 0; width: 0%;
        background: var(--green); transition: width .07s linear;
    }

    .sectors { display: flex; gap: 6px; margin-top: 2px; }
    .sector {
        flex: 1; min-width: 0;
        display: flex; flex-direction: column; align-items: center; gap: 2px;
        padding: 6px 4px; border-radius: 8px;
        background: rgba(255,255,255,0.06);
        border: 1px solid rgba(255,255,255,0.08);
    }
    .sector .s-name {
        font-size: 11px; font-weight: 700; letter-spacing: 1px;
        color: #8b91a6; text-transform: uppercase;
    }
    .sector .s-delta { font-size: 17px; font-weight: 800; color: #c7ccdb; }
    .sector.faster { background: rgba(25,211,107,0.16); border-color: rgba(25,211,107,0.5); }
    .sector.faster .s-delta { color: var(--green); }
    .sector.slower { background: rgba(255,77,77,0.14);  border-color: rgba(255,77,77,0.5); }
    .sector.slower .s-delta { color: var(--red); }
    .sector.best   { background: rgba(176,107,255,0.18); border-color: rgba(176,107,255,0.6); }
    .sector.best   .s-delta { color: var(--purple); }
    .sector.pending { opacity: 0.45; }

    .foot {
        display: flex; justify-content: space-between; gap: 16px;
        font-size: 12px; color: #8b91a6; font-weight: 600;
    }
    .foot .v { color: #c7ccdb; font-weight: 800; }
    .note { font-size: 12px; color: #6f93c9; font-weight: 700; text-align: center; }

    .idle { text-align: center; font-size: 22px; font-weight: 700;
            color: #5a6072; letter-spacing: 1px; padding: 18px 0; }
    .hidden { display: none !important; }
</style>
</head>
<body>

<div class="card" id="card">
    <div class="head">
        <div>
            <span class="title">Δ to session best</span>
            <span class="watching" id="watching"></span>
        </div>
        <span class="sess" id="sess">—</span>
    </div>

    <div id="live">
        <div class="delta-big" id="delta">—</div>
        <div class="bar">
            <div class="fill" id="bar-fill"></div>
            <div class="center"></div>
        </div>
        <div class="sectors" id="sectors"></div>
        <div class="note hidden" id="note"></div>
        <div class="foot">
            <span><span id="ref-label">Best</span> <span class="v" id="ref">—</span></span>
            <span>Last <span class="v" id="last">—</span></span>
        </div>
    </div>

    <div class="idle hidden" id="idle">Waiting for iRacing…</div>
</div>

<script>
const SCALE = 1.5;   // seconds = full half-bar

function fmtLap(sec) {
    if (!sec || sec <= 0) return "—";
    const m = Math.floor(sec / 60);
    const s = (sec - m * 60).toFixed(3).padStart(6, "0");
    return m + ":" + s;
}
function renderDelta(d, ok) {
    const el = document.getElementById("delta");
    el.classList.remove("ahead", "behind");
    if (!ok || d == null) { el.textContent = "—"; return; }
    el.textContent = (d >= 0 ? "+" : "") + d.toFixed(3);
    if (d < -0.005) el.classList.add("ahead");
    else if (d > 0.005) el.classList.add("behind");
}
function renderBar(d, ok) {
    const fill = document.getElementById("bar-fill");
    if (!ok || d == null) { fill.style.width = "0%"; return; }
    const w = Math.min(Math.abs(d) / SCALE, 1) * 50;   // each half = 50%
    fill.style.width = w + "%";
    if (d < 0) { fill.style.right = "50%"; fill.style.left = "auto";  fill.style.background = "var(--green)"; }
    else       { fill.style.left = "50%";  fill.style.right = "auto"; fill.style.background = "var(--red)"; }
}
function renderSectors(secs) {
    const wrap = document.getElementById("sectors");
    secs = secs || [];
    if (wrap.children.length !== secs.length) {
        wrap.innerHTML = "";
        secs.forEach(s => {
            const cell = document.createElement("div");
            cell.className = "sector";
            cell.innerHTML = '<span class="s-name">S' + s.idx + '</span><span class="s-delta"></span>';
            wrap.appendChild(cell);
        });
    }
    secs.forEach((s, i) => {
        const cell = wrap.children[i];
        cell.className = "sector " + (s.state || "neutral");
        const d = cell.querySelector(".s-delta");
        if (s.state === "pending" || s.time == null) d.textContent = "–";
        else if (s.delta == null) d.textContent = s.time.toFixed(2);
        else d.textContent = (s.delta >= 0 ? "+" : "") + s.delta.toFixed(2);
    });
}

async function tick() {
    try {
        const r = await fetch("/status");
        const d = await r.json();
        const live = document.getElementById("live");
        const idle = document.getElementById("idle");

        if (!d.connected) {
            live.classList.add("hidden");
            idle.classList.remove("hidden");
            idle.textContent = "Waiting for iRacing…";
            return;
        }
        live.classList.remove("hidden");
        idle.classList.add("hidden");

        document.getElementById("sess").textContent = d.session_label || "—";
        document.getElementById("watching").textContent =
            (d.mode === "driving") ? "YOU" : (d.watching || "—");

        renderDelta(d.delta, d.delta_ok);
        renderBar(d.delta, d.delta_ok);
        renderSectors(d.sectors);

        document.getElementById("ref-label").textContent = d.ref_label || "Best";
        document.getElementById("ref").textContent  = fmtLap(d.ref_lap);
        document.getElementById("last").textContent = fmtLap(d.last_lap);

        // "Building reference…" note while spectating before a pole lap exists
        const note = document.getElementById("note");
        if (d.mode === "spectator" && !d.have_reference) {
            note.classList.remove("hidden");
            note.textContent = "Building reference lap…";
        } else if (d.mode === "spectator" && !d.delta_ok) {
            note.classList.remove("hidden");
            note.textContent = "Waiting for a flying lap…";
        } else {
            note.classList.add("hidden");
        }
    } catch (e) {
        document.getElementById("live").classList.add("hidden");
        const idle = document.getElementById("idle");
        idle.classList.remove("hidden");
        idle.textContent = "Waiting for server…";
    }
}

// Stream mode: transparent by default; H (or ?debug=1) shows a debug bg.
function applyDebug(on) { document.body.classList.toggle("debug", on); }
document.addEventListener("keydown", e => {
    if (e.key === "h" || e.key === "H") applyDebug(!document.body.classList.contains("debug"));
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
    print(f"  Open in browser:  http://localhost:{PORT}")
    print("  Transparent background — add as an OBS browser source.")
    print("  DRIVING: iRacing predictive delta for your own car.")
    print("  SPECTATING: computed delta for the on-camera car vs pole.")
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
