"""
iRacing Track Map Overlay (offline, no auth required)
------------------------------------------------------
Shows the current track's outline and live car positions as dots, using
pre-built track geometry from SIMRacingApps' open-source track library
(Apache 2.0). See ./tracks/NOTICE.txt for attribution.

No iRacing members-ng login is required — all track geometry lives on
disk in ./tracks/<WeekendInfo.TrackName>.json. Live car positions come
from the local iRacing SDK via pyirsdk.

Requirements:  pip install pyirsdk flask
Run:           python iracing_trackmap.py
Open:          http://localhost:5007

Background on the data:
  - SIMRacingApps hand-built a library of GPX routes for ~200 iRacing
    tracks. Each route is a list of (lat, lon) waypoints forming the
    track outline (ONTRACK) and pit lane (ONPITROAD).
  - We converted those GPX files into a single per-track JSON at bundle
    time (see tracks/ folder).
  - At runtime we (a) look up the file by WeekendInfo.TrackName,
    (b) project lat/lon to 2D via equirectangular projection around the
    track center, (c) build an SVG polyline, (d) project CarIdxLapDistPct
    onto the polyline to place car dots.
"""

from __future__ import annotations
import html
import json
import math
import re
import threading
import time
from pathlib import Path
from flask import Flask, jsonify, render_template_string, Response, abort, request

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
from cls_proam import ProAmRoster
from class_colors import class_colors, text_on
setup_utf8_stdout()


# ---------------------------------------------------------------------------
# Config / paths
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
TRACKS_DIR = HERE / "tracks"

POLL_INTERVAL = 0.25  # seconds

# Size of the SVG viewBox. Tracks are projected into this box with margin.
SVG_VIEW_W = 1000
SVG_VIEW_H = 600
SVG_MARGIN = 40
TURN_LABEL_OFFSET = 36   # turn number: apex -> label centre (road 28 wide, dots r 17)

# An ontrack route whose open end is within this fraction of its own length is
# treated as a closed circuit and gets its final segment drawn in.
LOOP_CLOSE_MAX_FRAC = 0.20

# Pit-road trimming. In most SimRacingApps-derived files (219 of 314) the
# ONPITROAD route is a whole LAP that starts at pit exit and ends at pit
# entry, so the orange pit line was drawn around the entire circuit. When
# the pit route is longer than PIT_TRIM_MIN_FRAC of a lap, only its longest
# stretch running more than PIT_OFF_TRACK_M away from the racing line is
# kept — that stretch is the actual pit lane.
PIT_TRIM_MIN_FRAC = 0.5
PIT_OFF_TRACK_M = 3.0

# iRacing "track surface" enum values for in-world detection.
SURFACE_NOT_IN_WORLD = -1
SURFACE_OFF_TRACK    = 0
SURFACE_PIT_STALL    = 1
SURFACE_APPROACH_PIT = 2
SURFACE_ON_TRACK     = 3


# ---------------------------------------------------------------------------
# Track data loading + projection
# ---------------------------------------------------------------------------
_track_cache: dict[str, dict] = {}


def _dist_to_polyline(p, line) -> float:
    """Metres from point p to the closed polyline `line` (local x/y)."""
    best = float("inf")
    px, py = p
    n = len(line)
    for i in range(n):
        ax, ay = line[i]
        bx, by = line[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
        d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        if d < best:
            best = d
    return best


def _trim_pit_lap(pit, track):
    """Cut a whole-lap pit route down to the real pit lane (see
    PIT_TRIM_MIN_FRAC). Pit data that is already just the pit lane is
    returned unchanged; a route that never leaves the racing line is
    dropped (nothing worth drawing)."""
    if len(pit) < 3 or len(track) < 3:
        return pit

    def length(pts):
        return sum(math.dist(pts[i - 1], pts[i]) for i in range(1, len(pts)))

    if length(pit) <= PIT_TRIM_MIN_FRAC * length(track):
        return pit
    off = [_dist_to_polyline(p, track) > PIT_OFF_TRACK_M for p in pit]
    runs, start = [], None
    for i, o in enumerate(off + [False]):
        if o and start is None:
            start = i
        elif not o and start is not None:
            runs.append((start, i - 1))
            start = None
    if not runs:
        return []
    n = len(pit)

    def with_ends(a, b):
        return pit[max(0, a - 1):min(n, b + 2)]

    segs = [with_ends(a, b) for a, b in runs]
    # pit entry is recorded at the END of the route, pit exit at the START:
    # when both ends are off the racing line they are one pit lane.
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == n - 1:
        segs.append(with_ends(*runs[-1]) + with_ends(*runs[0]))
    return max(segs, key=length)


_resolve_logged: set = set()


def _resolve_track_file(track_file: str, display: str, config: str) -> str:
    """Exact file first. Otherwise a bundled map of the same circuit + layout.

    Many maps were built by hand (OSM bulk build, June 2026) under GUESSED
    slugs because iRacing's TrackName wasn't known yet — e.g. Motegi exists
    as motegi_gp / motegi_grandprix / twinring_gp. Instead of renaming files
    after every first visit, look for <word>_<layout> where <word> is a word
    of TrackName / TrackDisplayName ("motegi") and <layout> is the
    TrackConfigName squeezed ("grandprix") or its initials ("gp"). Anything
    less certain stays "not bundled" — a wrong map is worse than none.
    """
    if not track_file or (TRACKS_DIR / f"{track_file}.json").is_file():
        return track_file
    sq = lambda t: re.sub(r"[^a-z0-9]", "", (t or "").lower())
    words = re.findall(r"[a-z0-9]+", f"{track_file.replace('_', ' ')} {display}".lower())
    bases = [w for w in dict.fromkeys(words) if len(w) >= 4]
    cfg_words = re.findall(r"[a-z0-9]+", (config or "").lower())
    layouts = {sq(config)} | ({"".join(w[0] for w in cfg_words)} if len(cfg_words) > 1 else set())
    layouts.discard("")
    stems = sorted(f.stem for f in TRACKS_DIR.glob("*.json")) if TRACKS_DIR.is_dir() else []
    pick = None
    for b in bases:
        same = [st for st in stems if st == b or st.startswith(b + "_")]
        hit = [st for st in same if sq(st[len(b):]) in layouts]
        if hit:
            pick = hit[0]
            break
        if len(same) == 1 and not layouts:
            pick = same[0]
            break
    if pick and track_file not in _resolve_logged:
        _resolve_logged.add(track_file)
        print(f"[trackmap] no {track_file}.json - using {pick}.json "
              f"(same circuit, layout {config!r}); rename it to {track_file}.json to make it exact")
    return pick or track_file


def _load_track(track_name: str) -> dict | None:
    """Load and prepare a track definition from ./tracks/<name>.json.

    Returns a dict with 'ontrack_xy', 'onpitroad_xy', 'view_box', etc.
    The SVG-projected XY coordinates are cached so we only do the math
    once per track per process lifetime.
    """
    if not track_name:
        return None
    if track_name in _track_cache:
        return _track_cache[track_name]

    path = TRACKS_DIR / f"{track_name}.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[trackmap] failed to parse {path.name}: {e}")
        return None

    ontrack   = raw.get("ontrack") or []
    onpitroad = raw.get("onpitroad") or []
    if not ontrack:
        return None

    # --- equirectangular projection around the track center -----------------
    # "Latitude"/"Longitude" in the raw file is the center point of the map.
    # For a circuit-sized area (<few km) equirectangular is accurate enough.
    center_lat = float(raw.get("latitude") or 0.0)
    center_lon = float(raw.get("longitude") or 0.0)
    cos_c = math.cos(math.radians(center_lat))
    # Rough metres-per-degree at this latitude:
    METRES_PER_DEG = 111320.0

    def latlon_to_xy(lat: float, lon: float) -> tuple[float, float]:
        # x grows east, y grows south (screen conv); flip lat so north is up.
        x = (lon - center_lon) * METRES_PER_DEG * cos_c
        y = (center_lat - lat) * METRES_PER_DEG
        return x, y

    # SRA's "North" field is a bearing in degrees using their convention:
    # 270 = map's north points straight up. Rotate so that works in SVG.
    north_deg = float(raw.get("north") or 270.0)
    rot_deg   = north_deg - 270.0
    rot_rad   = math.radians(rot_deg)
    cos_r, sin_r = math.cos(rot_rad), math.sin(rot_rad)

    def rotate(pt):
        x, y = pt
        return (x * cos_r - y * sin_r, x * sin_r + y * cos_r)

    ontrack_m   = [rotate(latlon_to_xy(lat, lon)) for lat, lon in ontrack]
    onpitroad_m = [rotate(latlon_to_xy(lat, lon)) for lat, lon in onpitroad]

    # --- close the lap ------------------------------------------------------
    # Several bundled GPX routes stop short of the start/finish line, leaving
    # the S/F straight undrawn (Donington was missing its whole pit straight,
    # so only the pit lane showed there). Anything whose open end sits within
    # LOOP_CLOSE_MAX_FRAC of the route length is a circuit, so we append the
    # first point to close it. That also makes the arc length below match a
    # real lap, otherwise every car dot drifts by the size of the gap.
    # Point-to-point routes (the Mt Washington hillclimbs) are far above the
    # threshold and stay open.
    if len(ontrack_m) > 3:
        run = sum(math.dist(ontrack_m[i - 1], ontrack_m[i])
                  for i in range(1, len(ontrack_m)))
        gap = math.dist(ontrack_m[-1], ontrack_m[0])
        if run > 0 and gap / run <= LOOP_CLOSE_MAX_FRAC and gap > 1e-6:
            ontrack_m.append(ontrack_m[0])

    # --- pit lane only --------------------------------------------------------
    onpitroad_m = _trim_pit_lap(onpitroad_m, ontrack_m)

    # Compute bounding box across both layers so nothing gets clipped.
    xs = [p[0] for p in ontrack_m] + [p[0] for p in onpitroad_m]
    ys = [p[1] for p in ontrack_m] + [p[1] for p in onpitroad_m]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    w = max(1.0, max_x - min_x)
    h = max(1.0, max_y - min_y)

    # Scale to fit the viewBox with margin, preserving aspect ratio.
    avail_w = SVG_VIEW_W - 2 * SVG_MARGIN
    avail_h = SVG_VIEW_H - 2 * SVG_MARGIN
    scale = min(avail_w / w, avail_h / h)

    def to_svg(pt):
        x, y = pt
        sx = (x - min_x) * scale + (SVG_VIEW_W - w * scale) / 2.0
        sy = (y - min_y) * scale + (SVG_VIEW_H - h * scale) / 2.0
        return (sx, sy)

    ontrack_xy   = [to_svg(p) for p in ontrack_m]
    onpitroad_xy = [to_svg(p) for p in onpitroad_m]

    # Cumulative arc length along ONTRACK — used to project car % onto
    # the polyline. Total length normalised to 1.0.
    arc = [0.0]
    for i in range(1, len(ontrack_xy)):
        x0, y0 = ontrack_xy[i - 1]
        x1, y1 = ontrack_xy[i]
        arc.append(arc[-1] + math.hypot(x1 - x0, y1 - y0))
    total = arc[-1] or 1.0
    arc_norm = [a / total for a in arc]

    out = {
        "trackname":    track_name,
        "ontrack_xy":   ontrack_xy,
        "onpitroad_xy": onpitroad_xy,
        "arc_norm":     arc_norm,  # 0..1, same length as ontrack_xy
        "view_w":       SVG_VIEW_W,
        "view_h":       SVG_VIEW_H,
        "merge_point":  float(raw.get("merge_point") or 0.0),
    }
    _track_cache[track_name] = out
    return out


def pct_to_xy(track: dict, pct: float) -> tuple[float, float] | None:
    """Map 0..1 lap-distance percentage to a point along the ONTRACK polyline."""
    if not track or not track.get("ontrack_xy"):
        return None
    pct = pct % 1.0 if pct >= 0 else 0.0
    pts = track["ontrack_xy"]
    arc = track["arc_norm"]
    # Binary-search the first arc position >= pct.
    lo, hi = 0, len(arc) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if arc[mid] < pct:
            lo = mid + 1
        else:
            hi = mid
    i = lo
    if i == 0:
        return pts[0]
    # Linear interpolate between pts[i-1] and pts[i].
    span = arc[i] - arc[i - 1] or 1e-9
    t = (pct - arc[i - 1]) / span
    x0, y0 = pts[i - 1]
    x1, y1 = pts[i]
    return (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------
class TrackmapPoller(SDKPoller):
    tag = "trackmap"
    poll_interval = POLL_INTERVAL

    def __init__(self):
        super().__init__()
        # Session/track change detection — pyirsdk aggressively caches
        # WeekendInfo YAML and sometimes misses session transitions
        # (especially when switching between sessions within the same
        # iRacing process). We track SessionUniqueID + TrackID and
        # force a full SDK reconnect if either changes.
        self._last_session_uid: int | None = None
        self._last_track_id:    int | None = None
        self._last_track_file:  str = ""

    def _force_reconnect(self, reason: str) -> None:
        """Shutdown + restart the SDK to force pyirsdk to re-parse its
        cached session-info YAML. Used when we suspect a stale cache."""
        print(f"[trackmap] Force-reconnect: {reason}")
        try:
            self.ir.shutdown()
        except Exception as e:
            print(f"[trackmap] shutdown error: {e!r}")
        self.connected = False
        # Brief sleep so Windows shared-mem handle fully closes
        time.sleep(0.3)
        try:
            if self.ir.startup() and self.ir.is_initialized and self.ir.is_connected:
                self.connected = True
                print("[trackmap] Reconnected to iRacing")
        except Exception as e:
            print(f"[trackmap] startup error: {e!r}")
        # Wipe local caches so we re-project next poll
        self._last_session_uid = None
        self._last_track_id    = None
        self._last_track_file  = ""

    def _read_snapshot(self) -> dict:
        ir = self.ir
        weekend = ir["WeekendInfo"] or {}
        track_name_raw = (weekend.get("TrackName") or "").strip()
        # Filename convention: lowercase, spaces -> underscores.
        track_file = track_name_raw.replace(" ", "_").lower()
        track_display = weekend.get("TrackDisplayName", "") or ""
        track_config  = weekend.get("TrackConfigName", "") or ""
        track_id      = int(weekend.get("TrackID") or 0)

        # --- session-change detection ------------------------------------
        # pyirsdk caches the YAML string by session_info_update tick. When
        # iRacing transitions between sessions (e.g. qualifying -> race,
        # or one test session to another), the tick doesn't always bump
        # and we keep seeing the old TrackName. Track SessionUniqueID
        # + TrackID and force a reconnect when either changes — this
        # throws out pyirsdk's cache entirely.
        try:
            sess_uid = int(weekend.get("SessionID") or 0)
        except Exception:
            sess_uid = 0
        if self._last_track_id is not None and track_id != self._last_track_id:
            print(f"[trackmap] TrackID changed: {self._last_track_id} -> {track_id} "
                  f"({self._last_track_file} -> {track_file})")
        if self._last_track_file and self._last_track_file != track_file:
            # Track change detected while poller was alive — force a clean
            # re-read so we're not serving stale data for the *new* session
            # that pyirsdk might still be caching partially.
            self._force_reconnect(
                f"track change {self._last_track_file!r} -> {track_file!r}"
            )
            # Re-read after reconnect so the rest of the function sees fresh data.
            weekend = ir["WeekendInfo"] or {}
            track_name_raw = (weekend.get("TrackName") or "").strip()
            track_file = track_name_raw.replace(" ", "_").lower()
            track_display = weekend.get("TrackDisplayName", "") or ""
            track_config  = weekend.get("TrackConfigName", "") or ""
            track_id      = int(weekend.get("TrackID") or 0)
        self._last_session_uid = sess_uid
        self._last_track_id    = track_id
        self._last_track_file  = track_file

        track_file = _resolve_track_file(track_file, track_display, track_config)
        track = _load_track(track_file)
        cam_idx = ir["CamCarIdx"]

        info = ir["DriverInfo"] or {}
        drivers = info.get("Drivers", []) or []
        lap_pct   = ir["CarIdxLapDistPct"] or []
        on_pit    = ir["CarIdxOnPitRoad"] or []
        surface   = ir["CarIdxTrackSurface"] or []
        positions = ir["CarIdxPosition"] or []

        # WCT GT3 (2026-10-13): dots in the Pro/Am colours — red PRO, green
        # AM — only when the field IS a WCT field (cls_proam auto mode, same
        # rule as the tower's bars). The camera car keeps its own colour.
        proam_on = proam.active_for(
            d.get("UserID") for d in drivers
            if d.get("CarIsPaceCar") != 1 and d.get("IsSpectator") != 1)

        # Multiclass (IEC, 2026-10-25): every dot in its CLASS colour — the
        # same colours as the tower (class_colors). Pro/Am wins in a WCT
        # field; single class keeps the old orange.
        ccol = class_colors(drivers)
        multiclass = len(ccol) > 1
        legend: dict = {}       # colour -> class name, for the map's key

        cars = []
        for d in drivers:
            idx = d.get("CarIdx")
            if idx is None:
                continue
            if d.get("CarIsPaceCar") == 1:
                continue
            if d.get("IsSpectator") == 1:
                continue
            if idx >= len(lap_pct):
                continue
            pct = float(lap_pct[idx] or 0.0)
            in_world = (idx < len(surface)
                        and int(surface[idx]) != SURFACE_NOT_IN_WORLD)
            if not in_world:
                continue
            cars.append({
                "idx":        idx,
                "num":        str(d.get("CarNumber", "") or ""),
                "name":       d.get("UserName", "") or "",
                "pct":        pct,
                "on_pit":     bool(on_pit[idx]) if idx < len(on_pit) else False,
                "position":   int(positions[idx] or 0) if idx < len(positions) else 0,
                "cam":        (idx == cam_idx),
                "proam":      proam.lookup_field(d.get("UserID"), proam_on),
            })
            col = ccol.get(int(d.get("CarClassID") or 0)) if multiclass else None
            cars[-1]["color"] = col
            cars[-1]["text"] = text_on(col) if col else None
            if col:
                legend.setdefault(col, (d.get("CarClassShortName") or "").strip()
                                  or (d.get("CarScreenNameShort") or "").strip())

        return {
            "connected":      True,
            "track_name":     track_display or track_name_raw,
            "track_config":   track_config,
            "track_id":       track_id,
            "track_file":     track_file,
            "track_available": bool(track),
            "view_w":         track["view_w"] if track else SVG_VIEW_W,
            "view_h":         track["view_h"] if track else SVG_VIEW_H,
            "cars":           cars,
            "classes":        [{"color": c, "name": n} for c, n in legend.items()],
            "location":       _track_location(weekend, track_file),
            "track_length_m": _track_length_m(weekend),
        }



def _deg(v):
    """WeekendInfo 'TrackLatitude: 36.532902 m' -> 36.532902."""
    try:
        return float(str(v).split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def _track_location(weekend: dict, track_file: str) -> dict:
    """Where the track is (for /location): iRacing's own coordinates, the
    bundled track JSON's centre as a fallback."""
    lat, lon = _deg(weekend.get("TrackLatitude")), _deg(weekend.get("TrackLongitude"))
    if lat is None or lon is None or (lat == 0 and lon == 0):
        try:
            raw = json.loads((TRACKS_DIR / f"{track_file}.json").read_text(encoding="utf-8"))
            lat, lon = float(raw["latitude"]), float(raw["longitude"])
        except Exception:
            lat = lon = None
    return {"lat": lat, "lon": lon, "city": weekend.get("TrackCity") or "",
            "country": weekend.get("TrackCountry") or "",
            "name": weekend.get("TrackDisplayName") or weekend.get("TrackName") or "",
            "config": weekend.get("TrackConfigName") or ""}


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)
poller = TrackmapPoller()
proam = ProAmRoster()


@app.after_request
def _no_cache(resp):
    # Prevent browsers / OBS from caching overlay HTML + JSON. Individual
    # routes that explicitly want caching (the track SVG, which sets its
    # own Cache-Control: public, max-age=86400) bypass this check — we
    # only stamp the default when no caching directive was set.
    if "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.route("/")
def index():
    return render_template_string(TRACKMAP_HTML)


@app.route("/state")
def state():
    return jsonify(poller.get())


LOCATION_HTML = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"><title>Streckenort</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
    /* WHERE IS THE TRACK (2026-10-15, for the AI moderation): satellite map,
       starts on the country with a pin, then flies down onto the circuit.
       Coordinates from iRacing (WeekendInfo.TrackLatitude/-Longitude).
       Replays the flight whenever the OBS source is shown or the track changes.
       ?start=5 (zoom of the overview) ?zoom=15 (close-up) ?delay=2.5 ?fly=7 (s)
       ?label=0 hides the name card, ?demo=1 Motegi. Tiles: Esri World Imagery. */
    html, body { margin: 0; height: 100%; background: #0b0d12; overflow: hidden; }
    #m { position: absolute; inset: 0; background: #0b0d12; }
    .card { position: absolute; left: 32px; bottom: 36px; z-index: 1000; background: rgba(12,14,20,.92);
            border-left: 6px solid #2f7ff0; padding: 14px 22px 16px; color: #f2f2f4;
            font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; border-radius: 4px;
            box-shadow: 0 6px 24px rgba(0,0,0,.45); max-width: 70%; transition: opacity .6s; }
    .card .tag { font-size: 15px; letter-spacing: 3px; font-weight: 800; color: #2f7ff0; }
    .card .nm { font-size: 40px; font-weight: 800; line-height: 1.15; margin-top: 2px; }
    .card .cf { font-size: 22px; color: #c9c9d2; margin-top: 2px; }
    .card .loc { font-size: 24px; margin-top: 8px; font-weight: 600; }
    .msg { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
           color: #9aa0ab; font: 22px 'Segoe UI', Arial, sans-serif; z-index: 1001; }
    .leaflet-control-attribution { font-size: 10px; opacity: .7; }
</style></head>
<body><div id="m"></div>
<div class="card" id="card" style="opacity:0"><div class="tag">STRECKE</div><div class="nm" id="nm"></div>
  <div class="cf" id="cf"></div><div class="loc" id="loc"></div></div>
<div class="msg" id="msg">Warte auf iRacing …</div>
<script>
const qs = new URLSearchParams(location.search);
const START = parseFloat(qs.get('start') || '5'), ZOOM = parseFloat(qs.get('zoom') || '15');
const DELAY = parseFloat(qs.get('delay') || '2.5'), FLY = parseFloat(qs.get('fly') || '7');
if (qs.get('label') === '0') document.getElementById('card').style.display = 'none';
const map = L.map('m', {zoomControl: false, attributionControl: true, zoomSnap: 0.25, fadeAnimation: true});
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            {maxZoom: 18, attribution: 'Esri, Maxar, Earthstar Geographics'}).addTo(map);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
            {maxZoom: 18}).addTo(map);
map.setView([30, 10], 2);
let loc = null, key = '', marker = null, timer = null;
function esc(t) { return String(t || '').replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c])); }
function play() {
    if (!loc) return;
    clearTimeout(timer);
    const p = [loc.lat, loc.lon];
    map.stop(); map.setView(p, START, {animate: false});
    if (marker) marker.remove();
    marker = L.marker(p).addTo(map);
    document.getElementById('nm').textContent = loc.name || '';
    document.getElementById('cf').textContent = loc.config || '';
    document.getElementById('loc').innerHTML = esc([loc.city, loc.country].filter(Boolean).join(', '));
    document.getElementById('card').style.opacity = 1;
    timer = setTimeout(() => map.flyTo(p, ZOOM, {duration: FLY}), DELAY * 1000);
}
async function load() {
    let d;
    if (qs.get('demo') === '1') d = {location: {lat: 36.5329, lon: 140.2272, name: 'Mobility Resort Motegi',
                                                 config: 'Grand Prix', city: 'Motegi', country: 'Japan'}};
    else { try { d = await (await fetch('/state', {cache: 'no-store'})).json(); } catch (e) { return; } }
    const l = d && d.location;
    const msg = document.getElementById('msg');
    if (!l || l.lat == null || l.lon == null) { msg.style.display = 'flex';
        msg.textContent = d && d.connected ? 'Für diese Strecke sind keine Koordinaten bekannt.' : 'Warte auf iRacing …'; return; }
    msg.style.display = 'none';
    const k = [l.lat, l.lon, l.name, l.config].join('|');
    loc = l;
    if (k !== key) { key = k; play(); }
}
// fly again every time the source is shown (OBS keeps hidden sources running)
window.addEventListener('obsSourceVisibleChanged', e => { if (e.detail && e.detail.visible) play(); });
window.addEventListener('obsSourceActiveChanged', e => { if (e.detail && e.detail.active) play(); });
load(); setInterval(load, 5000);
</script></body></html>
"""


@app.route("/location")
def location_page():
    from flask import Response
    return Response(LOCATION_HTML, mimetype="text/html")


@app.route("/refresh")
def refresh():
    """Force-reconnect the SDK — use when iRacing swapped tracks and the
    overlay is still showing the previous track's map."""
    poller._force_reconnect("manual /refresh")
    return jsonify({"ok": True, "message": "SDK reconnect triggered"})


@app.route("/debug")
def debug():
    """Raw WeekendInfo dump — to see what the SDK is actually reporting."""
    try:
        weekend = poller.ir["WeekendInfo"] or {}
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e!r}"}), 500
    return jsonify({
        "connected":      poller.connected,
        "is_initialized": bool(poller.ir.is_initialized),
        "is_connected":   bool(poller.ir.is_connected),
        "TrackName":      weekend.get("TrackName"),
        "TrackID":        weekend.get("TrackID"),
        "TrackDisplayName":   weekend.get("TrackDisplayName"),
        "TrackConfigName":    weekend.get("TrackConfigName"),
        "SessionID":      weekend.get("SessionID"),
        "SubSessionID":   weekend.get("SubSessionID"),
        "last_track_file": poller._last_track_file,
        "last_track_id":   poller._last_track_id,
    })


def _track_length_m(weekend: dict) -> float | None:
    """WeekendInfo.TrackLength ('3.70 km') in metres, or None."""
    try:
        v = float(str(weekend.get("TrackLength") or "").split()[0])
    except (ValueError, IndexError):
        return None
    return v * 1000.0 if v > 0 else None


def _turn_labels(track_file: str, track: dict) -> list[dict]:
    """Turn numbers for the map (2026-10-28): the SAME corners and numbers
    as the corner-cue overlay 5012 / its /turn label (geometry analysis +
    cues/<track>.json "turn" / "hide"), placed next to the apex on the
    OUTSIDE of the corner so the label never sits on the road."""
    try:
        from iracing_drivingline import load_track_corners
    except Exception as e:
        print(f"[trackmap] turn numbers unavailable: {e!r}")
        return []
    an = load_track_corners(track_file, (poller.get() or {}).get("track_length_m"))
    out = []
    for c in (an or {}).get("corners") or []:
        if c.get("hide"):
            continue
        p = c["apex_pct"]
        a, b = pct_to_xy(track, p - 0.003), pct_to_xy(track, p + 0.003)
        xy = pct_to_xy(track, p)
        if not (a and b and xy):
            continue
        tx, ty = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(tx, ty) or 1.0
        tx, ty = tx / ln, ty / ln
        # y grows south: right of travel = (-ty, tx); outside of a right
        # turn is the left side, and vice versa.
        nx, ny = (ty, -tx) if c["dir"] == "R" else (-ty, tx)
        x = min(max(xy[0] + nx * TURN_LABEL_OFFSET, 14), track["view_w"] - 14)
        y = min(max(xy[1] + ny * TURN_LABEL_OFFSET, 14), track["view_h"] - 14)
        out.append({"turn": c.get("turn", c["num"]), "x": x, "y": y})
    return out


@app.route("/track/<track_file>.svg")
def track_svg(track_file: str):
    """Return an SVG containing the track outline + pit lane.
    The car dots are drawn client-side on top via <circle> elements."""
    # Basic sanitisation — no path-traversal, no weird chars.
    if not track_file or any(c not in
            "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in track_file):
        abort(400)
    track = _load_track(track_file)
    if not track:
        abort(404)

    def polyline(points, cls):
        if not points:
            return ""
        d = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        return f'<polyline class="{cls}" points="{d}"/>'

    # ?turns=0 = no turn numbers
    turns = ""
    if request.args.get("turns", "1") != "0":
        turns = "".join(
            f'<g class="turn"><circle cx="{t["x"]:.1f}" cy="{t["y"]:.1f}" r="13"/>'
            f'<text x="{t["x"]:.1f}" y="{t["y"]:.1f}">{html.escape(str(t["turn"]))}</text></g>'
            for t in _turn_labels(track_file, track))

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {track["view_w"]} {track["view_h"]}" '
        f'preserveAspectRatio="xMidYMid meet">'
        f'<style>'
        f'.pit {{ fill:none; stroke:#6a6a7a; stroke-width:5; stroke-linecap:round; stroke-linejoin:round; }}'
        f'.road {{ fill:none; stroke:#e8e8f0; stroke-width:28; stroke-linecap:round; stroke-linejoin:round; }}'
        f'.inner {{ fill:none; stroke:#4ade80; stroke-width:2; stroke-dasharray:5 8; stroke-linecap:round; }}'
        f'.turn circle {{ fill:rgba(14,14,20,0.88); stroke:#9a9aad; stroke-width:1.5; }}'
        f'.turn text {{ fill:#ffffff; font:800 15px "Segoe UI",sans-serif; text-anchor:middle; dominant-baseline:central; }}'
        f'</style>'
        + polyline(track["onpitroad_xy"], "pit")
        + polyline(track["ontrack_xy"], "road")
        + polyline(track["ontrack_xy"], "inner")
        + turns
        + '</svg>'
    )
    resp = Response(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.route("/track/<track_file>/position")
def track_position(track_file: str):
    """Helper for clients that prefer server-computed XY instead of doing
    the polyline-interpolation client-side. Returns the XY for a given
    percentage. Optional; /state + the SVG are enough for the overlay."""
    try:
        pct = float(request.args.get("pct", "0"))
    except Exception:
        abort(400)
    track = _load_track(track_file)
    if not track:
        abort(404)
    xy = pct_to_xy(track, pct)
    if xy is None:
        abort(404)
    return jsonify({"x": xy[0], "y": xy[1]})


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
TRACKMAP_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Track Map</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { background-color: rgba(0,0,0,0); }
    body {
        font-family: 'Segoe UI', system-ui, sans-serif;
        color: #e8e8ea;
        min-height: 100vh;
        padding: 12px;
    }
    body.debug-mode { background-color: #0a0a0f; }
    body.debug-mode .wrap { background: rgba(20,20,28,0.88); border: 1px solid #26262f; border-radius: 10px; }

    .wrap {
        max-width: 1080px;
        margin: 0 auto;
        padding: 16px;
        background: transparent;
    }
    .header {
        display: flex; align-items: center; justify-content: space-between;
        margin-bottom: 10px;
        text-shadow: 0 1px 2px rgba(0,0,0,0.85);
    }
    .header .title { font-size: 14px; color: #b0b0c0; }
    .header .title b { color: #fff; font-size: 16px; }
    .header .status { font-size: 11px; color: #8a8aa0; text-transform: uppercase; letter-spacing: 1px; }
    .header .status.ok  { color: #4ade80; }
    .header .status.err { color: #f87171; }

    .svg-host {
        position: relative;
        width: 100%;
        aspect-ratio: 5 / 3;
    }
    .svg-host > svg { width: 100%; height: 100%; display: block; }
    .legend { position: absolute; left: 6px; top: 6px; display: flex; gap: 6px; flex-wrap: wrap; }
    .legend span { display: inline-flex; align-items: center; gap: 6px; padding: 3px 9px 3px 6px;
        background: rgba(14,14,20,0.88); border-radius: 4px; color: #f2f2f4;
        font: 800 15px 'Segoe UI', sans-serif; }
    .legend i { width: 14px; height: 14px; border-radius: 50%; border: 2px solid #1a1a24; }

    /* Car dots are drawn on top of the track SVG via a separate overlay SVG. */
    .cars-overlay {
        position: absolute; inset: 0;
        width: 100%; height: 100%;
    }
    .car {
        fill: #ff6b35;
        stroke: #1a1a24;
        stroke-width: 2;
    }
    .car.cam {
        fill: #facc15;
        stroke: #0a0a0f;
        stroke-width: 2.5;
        filter: drop-shadow(0 0 6px #facc15);
    }
    .car.pit { fill: #94a3b8; }
    /* WCT GT3: Pro/Am colours (same as the tower's bars); in the pit the
       dot keeps its class colour, just faded. Camera car stays yellow. */
    .car.pro { fill: #e63946; }
    .car.am  { fill: #2ecc71; }
    .car.nr  { fill: #000000; stroke: #ffffff; stroke-width: 2; }   /* not rated yet */
    .car.pro.pit, .car.am.pit, .car.nr.pit { fill-opacity: 0.45; }
    .car.cam.pro, .car.cam.am, .car.cam.nr { fill: #facc15; fill-opacity: 1; }
    /* Multiclass: class colour from the server (inline fill). The camera car
       KEEPS its class colour and gets a white ring + glow instead of yellow
       (a yellow GT3 class would look like the camera car). */
    .car.klass.pit { fill-opacity: 0.45; }
    .car.klass.cam { stroke: #ffffff; stroke-width: 5; filter: drop-shadow(0 0 8px #ffffff); }
    .car-label {
        font-family: 'Segoe UI', sans-serif;
        font-size: 19px;
        font-weight: 800;
        fill: #0a0a0f;
        text-anchor: middle;
        dominant-baseline: central;
    }

    .waiting {
        text-align: center;
        padding: 60px 20px;
        color: #b0b0c0;
        text-shadow: 0 1px 3px rgba(0,0,0,0.9);
    }
    .waiting h2 { color: #e63946; margin-bottom: 8px; font-size: 20px; letter-spacing: 1px; }
    .waiting p { font-size: 12px; color: #9a9aad; }

    .stream-toggle {
        position: fixed; top: 10px; right: 10px;
        background: rgba(20,20,28,0.9); border: 1px solid #333; color: #bbb;
        padding: 5px 10px; font-size: 11px; border-radius: 4px;
        cursor: pointer; font-family: inherit;
        display: none;
    }
    body.debug-mode .stream-toggle { display: block; }
</style>
</head>
<body>

<button class="stream-toggle" onclick="toggleDebugBg()">Debug background (H)</button>

<div class="wrap" id="root">
    <div class="waiting">
        <h2>WAITING FOR IRACING…</h2>
        <p>Load into a session and the track map will appear.</p>
    </div>
</div>

<script>
function toggleDebugBg() { document.body.classList.toggle('debug-mode'); }
document.addEventListener('keydown', e => {
    if (e.key === 'h' || e.key === 'H') toggleDebugBg();
});

let currentTrackFile = '';
let trackPoints      = null;  // cached ontrack SVG points for interpolation
let trackArc         = null;  // cached cumulative-arc-length normalisation

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
        '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
}

/**
 * Pull the polyline points out of the embedded SVG so we can interpolate
 * car positions on the client side without an extra round-trip per car.
 */
function extractPointsAndArc(svgEl) {
    const polys = svgEl.querySelectorAll('polyline.road');
    if (!polys.length) return { pts: null, arc: null };
    const raw = polys[0].getAttribute('points') || '';
    const pts = raw.trim().split(/\s+/).map(tok => {
        const [x, y] = tok.split(',').map(Number);
        return [x, y];
    });
    const arc = [0];
    for (let i = 1; i < pts.length; i++) {
        const dx = pts[i][0] - pts[i-1][0];
        const dy = pts[i][1] - pts[i-1][1];
        arc.push(arc[arc.length-1] + Math.hypot(dx, dy));
    }
    const total = arc[arc.length-1] || 1;
    return { pts, arc: arc.map(a => a / total) };
}

function pctToXY(pct) {
    if (!trackPoints || !trackArc || !trackPoints.length) return null;
    pct = pct - Math.floor(pct);
    if (pct < 0) pct += 1;
    // Binary search for first arc >= pct.
    let lo = 0, hi = trackArc.length - 1;
    while (lo < hi) {
        const mid = (lo + hi) >>> 1;
        if (trackArc[mid] < pct) lo = mid + 1;
        else hi = mid;
    }
    if (lo === 0) return trackPoints[0];
    const a0 = trackArc[lo - 1];
    const a1 = trackArc[lo];
    const t  = (pct - a0) / ((a1 - a0) || 1e-9);
    const p0 = trackPoints[lo - 1];
    const p1 = trackPoints[lo];
    return [p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t];
}

async function loadTrack(trackFile, viewW, viewH) {
    currentTrackFile = trackFile;
    trackPoints = null;
    trackArc = null;

    const root = document.getElementById('root');
    root.innerHTML = `
        <div class="svg-host">
            <div id="track-svg-host"></div>
            <svg class="cars-overlay" id="cars-overlay"
                 viewBox="0 0 ${viewW} ${viewH}"
                 preserveAspectRatio="xMidYMid meet"></svg>
            <div class="legend" id="legend"></div>
        </div>`;

    try {
        const tq = new URLSearchParams(location.search).get('turns') === '0' ? '&turns=0' : '';
        const r = await fetch(`/track/${trackFile}.svg?v=${Date.now()}${tq}`, { cache: 'no-store' });
        if (!r.ok) throw new Error('SVG fetch failed: ' + r.status);
        const svgText = await r.text();
        document.getElementById('track-svg-host').innerHTML = svgText;
        const svgEl = document.querySelector('#track-svg-host svg');
        if (svgEl) {
            const extracted = extractPointsAndArc(svgEl);
            trackPoints = extracted.pts;
            trackArc    = extracted.arc;
        }
    } catch (e) {
        console.error(e);
    }
}

function renderWaiting(title, subtitle) {
    document.getElementById('root').innerHTML = `
        <div class="waiting">
            <h2>${esc(title)}</h2>
            <p>${esc(subtitle)}</p>
        </div>`;
    currentTrackFile = '';
    trackPoints = null;
    trackArc    = null;
}

function renderCars(d) {
    // Update header text
    const title  = document.getElementById('track-title');
    const status = document.getElementById('track-status');
    if (title)  title.innerHTML = `<b>${esc(d.track_name || '')}</b>${d.track_config ? ' · ' + esc(d.track_config) : ''}`;
    if (status) status.textContent = `${(d.cars || []).length} cars`;

    const overlay = document.getElementById('cars-overlay');
    if (!overlay || !trackPoints) return;

    const cars = (d.cars || []);
    // Put the camera-followed car LAST so it renders on top.
    cars.sort((a, b) => (a.cam ? 1 : 0) - (b.cam ? 1 : 0));

    // Build the new children list as a single innerHTML string for speed.
    const parts = [];
    for (const c of cars) {
        const xy = pctToXY(c.pct);
        if (!xy) continue;
        const pa  = c.proam === 'PRO' ? ' pro' : c.proam === 'AM' ? ' am' : c.proam === 'UNRATED' ? ' nr' : '';
        const kc  = (!pa && c.color) ? c.color : '';
        const cls = 'car' + pa + (kc ? ' klass' : '') + (c.cam ? ' cam' : '') + (c.on_pit ? ' pit' : '');
        const r   = c.cam ? 22 : 17;
        const fill = kc ? ` style="fill:${esc(kc)}"` : '';
        const tfill = kc && c.text ? ` style="fill:${esc(c.text)}"`
            : (pa === ' nr' && !c.cam) ? ' style="fill:#ffffff"' : '';
        parts.push(`<circle class="${cls}" cx="${xy[0].toFixed(1)}" cy="${xy[1].toFixed(1)}" r="${r}"${fill}></circle>`);
        parts.push(`<text class="car-label" x="${xy[0].toFixed(1)}" y="${xy[1].toFixed(1)}"${tfill}>${esc(c.num)}</text>`);
    }
    overlay.innerHTML = parts.join('');
    // multiclass key: off by default (the tower shows the class colours); ?legend=1 shows it
    const lg = document.getElementById('legend');
    if (lg) {
        const cl = new URLSearchParams(location.search).get('legend') === '1' ? (d.classes || []) : [];
        const h = cl.map(k => `<span><i style="background:${esc(k.color)}"></i>${esc(k.name || '')}</span>`).join('');
        if (lg.dataset.h !== h) { lg.innerHTML = h; lg.dataset.h = h; }
        lg.style.display = h ? '' : 'none';
    }
}

async function poll() {
    try {
        const r = await fetch('/state');
        const d = await r.json();

        if (!d.connected) {
            renderWaiting('WAITING FOR IRACING…', 'Load into a session and the map will appear.');
        } else if (!d.track_file) {
            renderWaiting('NO TRACK IN SESSION', 'Waiting for iRacing to report the current track.');
        } else if (!d.track_available) {
            renderWaiting(
                'TRACK MAP NOT BUNDLED',
                `No offline map found for "${d.track_file}". ` +
                `Bundled maps live in ./tracks/. You can add one or wait ` +
                `for the upstream SIMRacingApps project to add coverage.`
            );
        } else {
            if (d.track_file !== currentTrackFile) {
                await loadTrack(d.track_file, d.view_w, d.view_h);
            }
            renderCars(d);
        }
    } catch (e) {
        // keep last view
    }
    setTimeout(poll, 250);
}
poll();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("iRacing Track Map Overlay (offline)")
    print(f"Track data:   {TRACKS_DIR}")
    available = len(list(TRACKS_DIR.glob("*.json"))) if TRACKS_DIR.is_dir() else 0
    print(f"Bundled maps: {available} tracks")
    print("Open:         http://localhost:5007")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    proam.start()
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()
    try:
        app.run(host="0.0.0.0", port=5007, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()


if __name__ == "__main__":
    main()
