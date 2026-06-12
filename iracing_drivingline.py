"""
iracing_drivingline.py
----------------------
Corner-cue overlay — a "driving line substitute" for sessions where
iRacing disables the racing-line aid (D class and above).

Port 5012. OBS browser source / second monitor:  http://localhost:5012
For an on-top-of-the-sim window use driving_line_window.py (reads
/data from this server and renders a transparent click-through window).

How it works (geometry-only, no recorded laps needed):
  1. Reads WeekendInfo.TrackName and loads ./tracks/<name>.json
     (same bundled geometry the trackmap overlay uses).
  2. Projects the lat/lon loop to meters, resamples it at uniform
     spacing and computes signed curvature along the loop.
  3. Contiguous high-curvature regions become numbered corners with
     entry/apex/exit position, direction (L/R), min radius, total
     heading change, a severity class and a rough estimated apex
     speed (v = sqrt(a_lat * r), a_lat ~ GT3 grip — an ESTIMATE).
  4. At 10 Hz the poller maps the player's LapDistPct onto the loop
     and serves the next two corners with live distance countdown.

NOTE: braking points and gears are car-specific and can NOT come from
track geometry. The /data payload and corner model leave room for a
future "recorded reference lap" enrichment (see CUES_DIR below): drop
a cues/<track_file>.json with per-corner overrides ("brake_m", "gear",
"speed_kmh") and they are merged onto the geometry corners.
"""

from __future__ import annotations

import json
import math
import sys
import threading
from pathlib import Path

from iracing_sdk_base import SDKPoller, setup_utf8_stdout

setup_utf8_stdout()

try:
    from flask import Flask, jsonify, render_template_string
except ImportError:
    print("ERROR: flask not installed. Run:  pip install flask")
    raise SystemExit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
TRACKS_DIR = HERE / "tracks"
CUES_DIR = HERE / "cues"          # optional per-track overrides (future)

PORT = 5012
POLL_INTERVAL = 0.1               # 10 Hz — smooth countdown

# --- corner-detection tuning ------------------------------------------------
RESAMPLE_M = 4.0                  # uniform resample spacing (meters)
SMOOTH_HALF_WIN = 3               # curvature moving-average half window
CURV_THRESH = 1.0 / 200.0         # |k| above this counts as "turning" (r<200 m)
MERGE_GAP_M = 25.0                # merge same-direction runs closer than this
MIN_TURN_DEG = 12.0               # discard bends with less total heading change

# severity by min radius (m)
SEVERITY_BANDS = [
    (35.0,  "HAIRPIN"),
    (80.0,  "TIGHT"),
    (150.0, "MEDIUM"),
    (280.0, "FAST"),
]

A_LAT = 12.0                      # m/s^2 lateral grip for the speed ESTIMATE
EST_SPEED_CAP_KMH = 270.0

SURFACE_NOT_IN_WORLD = -1


# ---------------------------------------------------------------------------
# Geometry: load track, resample, curvature, corners
# ---------------------------------------------------------------------------
def _project_to_meters(raw: dict) -> list[tuple[float, float]]:
    """Equirectangular projection of the ontrack loop around its center.
    x grows east, y grows SOUTH (screen convention) — in this system a
    positive heading change is a RIGHT turn."""
    ontrack = raw.get("ontrack") or []
    center_lat = float(raw.get("latitude") or 0.0)
    center_lon = float(raw.get("longitude") or 0.0)
    cos_c = math.cos(math.radians(center_lat))
    mpd = 111320.0
    return [((lon - center_lon) * mpd * cos_c, (center_lat - lat) * mpd)
            for lat, lon in ontrack]


def _resample(pts: list[tuple[float, float]], step: float) -> list[tuple[float, float]]:
    """Resample a closed polyline at uniform arc-length spacing."""
    if len(pts) < 3:
        return list(pts)
    # ensure closed
    if pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    arc = [0.0]
    for i in range(1, len(pts)):
        arc.append(arc[-1] + math.hypot(pts[i][0] - pts[i - 1][0],
                                        pts[i][1] - pts[i - 1][1]))
    total = arc[-1]
    if total <= 0:
        return list(pts)
    n = max(8, int(total / step))
    out = []
    j = 0
    for k in range(n):
        target = total * k / n
        while j < len(arc) - 2 and arc[j + 1] < target:
            j += 1
        span = arc[j + 1] - arc[j] or 1e-9
        t = (target - arc[j]) / span
        out.append((pts[j][0] + (pts[j + 1][0] - pts[j][0]) * t,
                    pts[j][1] + (pts[j + 1][1] - pts[j][1]) * t))
    return out


def _signed_curvature(samples: list[tuple[float, float]], step: float) -> list[float]:
    """Signed curvature (1/m) per sample of a closed uniform polyline.
    Positive = right turn (y-south coordinate system)."""
    n = len(samples)
    headings = []
    for i in range(n):
        x0, y0 = samples[i]
        x1, y1 = samples[(i + 1) % n]
        headings.append(math.atan2(y1 - y0, x1 - x0))
    raw = []
    for i in range(n):
        d = headings[(i + 1) % n] - headings[i]
        while d > math.pi:
            d -= 2 * math.pi
        while d < -math.pi:
            d += 2 * math.pi
        raw.append(d / step)
    # moving average smoothing (circular)
    w = SMOOTH_HALF_WIN
    if w <= 0:
        return raw
    out = []
    for i in range(n):
        acc = 0.0
        for o in range(-w, w + 1):
            acc += raw[(i + o) % n]
        out.append(acc / (2 * w + 1))
    return out


def detect_corners(samples: list[tuple[float, float]], step: float,
                   scale: float = 1.0) -> list[dict]:
    """Find corners on a closed resampled loop.

    `scale` converts projected meters to official meters (official
    track length / projected loop length) so distances match the sim.
    Returns corners ordered by entry position from the S/F point
    (sample 0), each with entry/apex/exit as 0..1 lap fractions.
    """
    n = len(samples)
    if n < 16:
        return []
    k = _signed_curvature(samples, step)
    total_m = n * step * scale

    # 1) contiguous runs over threshold
    runs = []  # (start_idx, end_idx_inclusive, sign) — may wrap
    i = 0
    flags = [abs(v) >= CURV_THRESH for v in k]
    # find a sample that is NOT turning to anchor the scan (avoids a
    # run that wraps being split in two)
    anchor = next((idx for idx, f in enumerate(flags) if not f), 0)
    idx = anchor
    count = 0
    while count < n:
        if flags[idx]:
            start = idx
            sign = 1 if k[idx] >= 0 else -1
            length = 0
            while count < n and flags[idx]:
                # sign flips inside a run split it (chicanes)
                cur_sign = 1 if k[idx] >= 0 else -1
                if cur_sign != sign:
                    runs.append((start, (idx - 1) % n, sign))
                    start = idx
                    sign = cur_sign
                idx = (idx + 1) % n
                count += 1
                length += 1
            runs.append((start, (idx - 1) % n, sign))
        else:
            idx = (idx + 1) % n
            count += 1

    # 2) merge same-sign runs separated by a small gap
    def run_len(a, b):
        return ((b - a) % n) + 1

    merged = []
    for r in runs:
        if merged:
            pa, pb, ps = merged[-1]
            gap = ((r[0] - pb) % n) - 1
            if ps == r[2] and 0 <= gap * step <= MERGE_GAP_M:
                merged[-1] = (pa, r[1], ps)
                continue
        merged.append(r)

    # 3) build corner dicts, filter slight bends
    corners = []
    for a, b, sign in merged:
        idxs = [(a + o) % n for o in range(run_len(a, b))]
        turn_rad = sum(abs(k[i]) for i in idxs) * step
        turn_deg = math.degrees(turn_rad)
        if turn_deg < MIN_TURN_DEG:
            continue
        apex_i = max(idxs, key=lambda i: abs(k[i]))
        k_apex = abs(k[apex_i]) or 1e-9
        radius = (1.0 / k_apex) * scale
        sev = None
        for lim, name in SEVERITY_BANDS:
            if radius < lim:
                sev = name
                break
        if sev is None:
            continue  # too gentle to cue
        est_v = min(EST_SPEED_CAP_KMH, 3.6 * math.sqrt(A_LAT * radius))
        corners.append({
            "entry_pct": a / n,
            "apex_pct":  apex_i / n,
            "exit_pct":  ((b + 1) % n) / n,
            "dir":       "R" if sign > 0 else "L",
            "radius_m":  round(radius, 1),
            "turn_deg":  round(turn_deg, 1),
            "severity":  sev,
            "est_kmh":   int(round(est_v)),
        })

    corners.sort(key=lambda c: c["entry_pct"])
    for num, c in enumerate(corners, 1):
        c["num"] = num
    return corners


def _parse_track_length_m(weekend: dict) -> float | None:
    """WeekendInfo.TrackLength is like '3.70 km'."""
    s = str(weekend.get("TrackLength") or "").strip().lower()
    try:
        val = float(s.split()[0])
        if "km" in s:
            return val * 1000.0
        if "mi" in s:
            return val * 1609.344
        return val
    except Exception:
        return None


_track_cache: dict[str, dict | None] = {}
_track_cache_lock = threading.Lock()


def load_track_corners(track_file: str, official_len_m: float | None) -> dict | None:
    """Load + analyse a track. Cached per (file, rounded official length)."""
    if not track_file:
        return None
    key = f"{track_file}|{int(official_len_m or 0)}"
    with _track_cache_lock:
        if key in _track_cache:
            return _track_cache[key]
    path = TRACKS_DIR / f"{track_file}.json"
    result = None
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            pts = _project_to_meters(raw)
            samples = _resample(pts, RESAMPLE_M)
            proj_len = len(samples) * RESAMPLE_M
            scale = (official_len_m / proj_len) if official_len_m else 1.0
            corners = detect_corners(samples, RESAMPLE_M, scale)
            corners = _apply_cue_overrides(track_file, corners)
            result = {
                "track_file": track_file,
                "length_m":   round(proj_len * scale, 1),
                "corners":    corners,
            }
            print(f"[line] {track_file}: {len(corners)} corners, "
                  f"length {result['length_m']:.0f} m (scale {scale:.3f})")
        except Exception as e:
            print(f"[line] failed to analyse {path.name}: {e!r}")
    # Only cache successful loads — a missing JSON may be added while
    # we're running (the is_file() retry at 10 Hz is cheap).
    if result is not None:
        with _track_cache_lock:
            _track_cache[key] = result
    return result


def _apply_cue_overrides(track_file: str, corners: list[dict]) -> list[dict]:
    """Optional future enrichment: cues/<track_file>.json may carry
    per-corner overrides keyed by corner number, e.g.
      {"5": {"gear": 2, "brake_m": 120, "speed_kmh": 78, "name": "Hairpin"}}
    Recorded-reference-lap tooling can generate this later."""
    path = CUES_DIR / f"{track_file}.json"
    if not path.is_file():
        return corners
    try:
        overrides = json.loads(path.read_text(encoding="utf-8"))
        for c in corners:
            o = overrides.get(str(c["num"]))
            if isinstance(o, dict):
                c.update(o)
        print(f"[line] applied cue overrides from cues/{track_file}.json")
    except Exception as e:
        print(f"[line] bad cue override file {path.name}: {e!r}")
    return corners


# ---------------------------------------------------------------------------
# Cue computation
# ---------------------------------------------------------------------------
def compute_cue(track: dict, pct: float) -> dict:
    """Given the player's lap fraction, return current/next corner info."""
    corners = track.get("corners") or []
    length = track.get("length_m") or 1.0
    if not corners:
        return {"in_corner": None, "next": [], "track_len_m": length}

    pct %= 1.0
    in_corner = None
    for c in corners:
        e, x = c["entry_pct"], c["exit_pct"]
        inside = (e <= pct < x) if e <= x else (pct >= e or pct < x)
        if inside:
            in_corner = c
            break

    upcoming = []
    # corners sorted by entry; find the first entry after pct (wrap)
    start_i = 0
    for i, c in enumerate(corners):
        if c["entry_pct"] > pct:
            start_i = i
            break
    else:
        start_i = 0  # wrapped — next corner is the first one
    for o in range(2):
        c = corners[(start_i + o) % len(corners)]
        dist = (c["entry_pct"] - pct) % 1.0 * length
        d = dict(c)
        d["dist_m"] = round(dist, 0)
        upcoming.append(d)

    return {"in_corner": in_corner, "next": upcoming, "track_len_m": length}


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------
class LinePoller(SDKPoller):
    tag = "line"
    poll_interval = POLL_INTERVAL

    def __init__(self):
        super().__init__()
        self._last_track_file = ""

    def _read_snapshot(self) -> dict:
        ir = self.ir
        weekend = ir["WeekendInfo"] or {}
        track_name_raw = (weekend.get("TrackName") or "").strip()
        track_file = track_name_raw.replace(" ", "_").lower()
        official_len = _parse_track_length_m(weekend)

        if track_file and track_file != self._last_track_file:
            self._last_track_file = track_file
            print(f"[line] track: {track_file!r}")

        track = load_track_corners(track_file, official_len)

        # player's car — fall back to camera car when spectating
        info = ir["DriverInfo"] or {}
        try:
            my_idx = int(info.get("DriverCarIdx"))
        except (TypeError, ValueError):
            my_idx = -1
        lap_pct_arr = ir["CarIdxLapDistPct"] or []
        surface = ir["CarIdxTrackSurface"] or []
        pct = None
        used_idx = my_idx
        if (0 <= my_idx < len(surface)
                and int(surface[my_idx]) != SURFACE_NOT_IN_WORLD):
            pct = float(lap_pct_arr[my_idx] or 0.0)
        else:
            cam_idx = ir["CamCarIdx"]
            if (isinstance(cam_idx, int) and 0 <= cam_idx < len(lap_pct_arr)
                    and (cam_idx >= len(surface)
                         or int(surface[cam_idx]) != SURFACE_NOT_IN_WORLD)):
                pct = float(lap_pct_arr[cam_idx] or 0.0)
                used_idx = cam_idx

        speed = ir["Speed"]
        speed_kmh = round(float(speed) * 3.6, 0) if isinstance(speed, (int, float)) else None

        snap = {
            "connected":       True,
            "track_file":      track_file,
            "track_display":   weekend.get("TrackDisplayName", "") or track_name_raw,
            "track_available": bool(track),
            "car_idx":         used_idx,
            "pct":             pct,
            "speed_kmh":       speed_kmh,
        }
        if track:
            snap["n_corners"] = len(track["corners"])
            if pct is not None:
                snap["cue"] = compute_cue(track, pct)
        return snap


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)
poller = LinePoller()


@app.after_request
def _no_cache(resp):
    if "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.route("/data")
def data():
    return jsonify(poller.get())


@app.route("/corners")
def corners_debug():
    """Plain JSON dump of the analysed corners for the current track —
    open in a browser to inspect / tune detection."""
    snap = poller.get()
    track = load_track_corners(snap.get("track_file") or "", None)
    return jsonify(track or {"error": "no track loaded"})


PAGE = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Corner Cues</title>
<style>
  html, body { background-color: rgba(0,0,0,0); margin: 0; padding: 0;
               font-family: 'Segoe UI', Arial, sans-serif; overflow: hidden;
               -webkit-user-select: none; user-select: none; }
  body.debug { background-color: #14141c; }
  #wrap { display: flex; flex-direction: column; align-items: center;
          padding-top: 8px; gap: 6px; }
  #card { display: none; align-items: center; gap: 18px;
          background: rgba(10,10,16,0.82); border-radius: 12px;
          padding: 10px 26px; border: 1px solid rgba(255,255,255,0.08); }
  #arrow { font-size: 64px; font-weight: 800; line-height: 1;
           width: 80px; text-align: center; }
  .L { color: #4da3ff; } .R { color: #ffb14d; }
  #mid { text-align: center; }
  #cname  { font-size: 26px; font-weight: 700; color: #fff; }
  #csev   { font-size: 15px; font-weight: 600; letter-spacing: 2px;
            color: #9aa0ae; }
  #dist   { font-size: 52px; font-weight: 800; color: #fff; width: 170px;
            text-align: right; font-variant-numeric: tabular-nums; }
  #dist.warn { color: #ffd24d; } #dist.now { color: #ff5050; }
  #bar { width: 420px; height: 8px; border-radius: 4px;
         background: rgba(255,255,255,0.12); overflow: hidden; display:none; }
  #fill { height: 100%; width: 0%; background: #4dd06a; transition: width .1s linear; }
  #next2 { display: none; font-size: 16px; color: #c8ccd6;
           background: rgba(10,10,16,0.6); padding: 4px 14px;
           border-radius: 8px; }
  #status { color: #667; font-size: 14px; padding: 6px 12px; display: none; }
  body.debug #status { display: block; }
</style>
</head>
<body>
<div id="wrap">
  <div id="card">
    <div id="arrow"></div>
    <div id="mid">
      <div id="cname"></div>
      <div id="csev"></div>
    </div>
    <div id="dist"></div>
  </div>
  <div id="bar"><div id="fill"></div></div>
  <div id="next2"></div>
  <div id="status"></div>
</div>
<script>
const SHOW_FROM_M = 500;   // cue appears this far before the corner
document.addEventListener('keydown', e => {
  if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug');
});
// http://localhost:5012/?debug=1 -> debug background without needing focus
if (/debug=1/.test(location.search)) document.body.classList.add('debug');
function arrowFor(dir){ return dir === 'L' ? '⬅' : '➡'; }
async function tick(){
  let d = null;
  try { d = await (await fetch('/data')).json(); } catch(e) {}
  const card = document.getElementById('card');
  const bar  = document.getElementById('bar');
  const nx2  = document.getElementById('next2');
  const st   = document.getElementById('status');
  if (!d || !d.connected){ card.style.display='none'; bar.style.display='none';
    nx2.style.display='none'; st.textContent='waiting for iRacing…'; return; }
  if (!d.track_available){ card.style.display='none'; bar.style.display='none';
    nx2.style.display='none'; st.textContent='track not bundled: '+(d.track_file||'?'); return; }
  const cue = d.cue;
  if (!cue || !cue.next || !cue.next.length){ card.style.display='none';
    bar.style.display='none'; nx2.style.display='none';
    st.textContent='no position'; return; }
  st.textContent = d.track_display + ' — ' + d.n_corners + ' corners' +
                   (d.speed_kmh!=null ? ' — '+d.speed_kmh+' km/h' : '');
  const inC = cue.in_corner;
  const c   = inC || cue.next[0];
  const dist = inC ? 0 : cue.next[0].dist_m;
  if (!inC && dist > SHOW_FROM_M){
    card.style.display='none'; bar.style.display='none'; nx2.style.display='none';
    st.textContent += ' — next T' + cue.next[0].num + ' in ' + Math.round(dist) + ' m';
    return;
  }
  card.style.display='flex';
  const ar = document.getElementById('arrow');
  ar.textContent = arrowFor(c.dir);
  ar.className = c.dir;
  document.getElementById('cname').textContent =
    'T' + c.num + (c.name ? ' · ' + c.name : '');
  let sev = c.severity + ' · ~' + c.est_kmh + ' km/h';
  if (c.gear) sev += ' · gear ' + c.gear;
  document.getElementById('csev').textContent = sev;
  const de = document.getElementById('dist');
  if (inC){ de.textContent = 'APEX'; de.className = 'now'; }
  else {
    de.textContent = Math.round(dist) + ' m';
    de.className = dist < 80 ? 'now' : (dist < 200 ? 'warn' : '');
  }
  bar.style.display='block';
  const f = document.getElementById('fill');
  const frac = inC ? 1 : Math.max(0, Math.min(1, 1 - dist / SHOW_FROM_M));
  f.style.width = (frac*100).toFixed(1) + '%';
  f.style.background = frac > 0.84 ? '#ff5050' : (frac > 0.6 ? '#ffd24d' : '#4dd06a');
  if (cue.next.length > 1 && !inC){
    const n = inC ? cue.next[0] : cue.next[1];
    nx2.style.display='block';
    nx2.textContent = 'then T' + n.num + ' ' + (n.dir==='L'?'⬅':'➡') +
                      ' ' + n.severity + ' in ' + Math.round(n.dist_m) + ' m';
  } else { nx2.style.display='none'; }
}
setInterval(tick, 100); tick();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


def main():
    print("=" * 60)
    print("  iRacing Corner-Cue Overlay (driving line substitute)")
    print(f"  Overlay:      http://localhost:{PORT}")
    print(f"  Data API:     http://localhost:{PORT}/data")
    print(f"  Corner dump:  http://localhost:{PORT}/corners")
    print(f"  Track data:   {TRACKS_DIR}")
    print("  On-top window: python driving_line_window.py")
    print("  Press H on the page to toggle debug background")
    print("=" * 60)
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()
    try:
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    finally:
        poller.stop()


if __name__ == "__main__":
    main()
