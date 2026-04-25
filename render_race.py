"""
render_race.py — turn a logged race into a 2D animated MP4.
-----------------------------------------------------------
Reads a JSONL file produced by iracing_race_logger.py, looks up the
matching track outline in ./tracks/, and renders a top-down animation
of the entire race: cars as numbered dots moving around the circuit,
with a leaderboard, lap counter, and incident flashes.

Usage:
    python render_race.py logs/20260426-193015_monza_full_race.jsonl
    python render_race.py logs/...jsonl --out my_video.mp4 --fps 30

Output: <input>.mp4 next to the JSONL by default.

Requirements:
    pip install pillow
    Plus ffmpeg on the PATH. Easiest install on Windows:
        pip install imageio-ffmpeg
    On Mac:  brew install ffmpeg

Limitations / honest notes:
  - Animation fidelity is ~1 Hz (the rate the logger writes position
    ticks); we interpolate linearly between ticks for a smooth-looking
    30 fps render. You'll see fluid motion but it can't reflect motion
    at finer than 1-second resolution. For higher fidelity you'd
    record iRacing's own IBT telemetry instead.
  - Only renders races logged AFTER the position-tick feature was
    added. Older logs (lap events only) won't have enough data.
  - Track outline must exist in ./tracks/<TrackName>.json. Missing
    tracks: see tracks/gpx_to_json.py to draw your own from a GPX
    you trace in https://gpx.studio/.
"""

from __future__ import annotations
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("ERROR: Pillow not installed. Run:  pip install pillow")
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
TRACKS_DIR = HERE / "tracks"

# Default render settings — tweak with CLI flags.
DEFAULT_FPS         = 30
DEFAULT_WIDTH       = 1280
DEFAULT_HEIGHT      = 720
TRACK_PADDING_PX    = 60   # pixels of margin around the track in the canvas
LEADERBOARD_W_PX    = 320  # right-side panel width
INCIDENT_FLASH_SECS = 2.5  # how long an incident marker pulses

# Theme — matches the rest of the overlay suite
COLOR_BG          = (10, 10, 15)
COLOR_PANEL       = (20, 20, 28)
COLOR_PANEL_LINE  = (38, 38, 47)
COLOR_TEXT        = (232, 232, 234)
COLOR_TEXT_DIM    = (138, 138, 160)
COLOR_TRACK       = (138, 138, 160)
COLOR_TRACK_FILL  = (26, 26, 36)
COLOR_ACCENT      = (255, 107, 53)   # CAS orange
COLOR_GOLD        = (255, 209, 102)
COLOR_INCIDENT    = (255, 90, 90)
COLOR_PIT_DIM     = (110, 110, 130)

# A tasteful palette for car dots. Not based on real liveries — that
# would need design strings or paint files we don't have post-hoc.
CAR_PALETTE = [
    (255, 107, 53), (97, 180, 255), (255, 209, 102), (74, 222, 128),
    (244, 114, 182), (163, 113, 247), (34, 201, 224), (255, 137, 137),
    (132, 204, 22), (251, 146, 60), (192, 132, 252), (45, 212, 191),
    (250, 204, 21), (236, 72, 153), (96, 165, 250), (245, 158, 11),
    (52, 211, 153), (167, 139, 250), (248, 113, 113), (52, 199, 89),
    (255, 184, 108), (139, 233, 253), (255, 121, 198), (189, 147, 249),
]


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------
def parse_log(path: Path) -> dict:
    """Read a JSONL race log into a structured dict.

    Returns:
      {
        "session": <session_start payload>,
        "drivers": <list of driver dicts>,
        "laps":    <list of lap events sorted by t_session>,
        "incidents": <list of incident events sorted by t_session>,
        "positions": <list of (t_session, {car_idx: pct}) sorted by t>,
        "final":   <session_end payload or None>,
      }
    """
    session = None
    drivers = []
    laps: list[dict] = []
    incidents: list[dict] = []
    positions: list[tuple[float, dict[int, float]]] = []
    final = None

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "session_start":
                session = ev
                drivers = ev.get("drivers", []) or []
            elif t == "lap":
                laps.append(ev)
            elif t == "incident":
                incidents.append(ev)
            elif t == "pos":
                pos_map = {int(k): float(v) for k, v in (ev.get("p") or {}).items()}
                positions.append((float(ev.get("t", 0)), pos_map))
            elif t == "session_end":
                final = ev

    laps.sort(key=lambda e: e.get("t_session", 0))
    incidents.sort(key=lambda e: e.get("t_session", 0))
    positions.sort(key=lambda p: p[0])

    return {
        "session":   session or {},
        "drivers":   drivers,
        "laps":      laps,
        "incidents": incidents,
        "positions": positions,
        "final":     final,
    }


# ---------------------------------------------------------------------------
# Track loading + projection (copied from iracing_trackmap.py — kept self-
# contained so render_race.py runs without importing the Flask overlay)
# ---------------------------------------------------------------------------
def load_track(track_name: str, canvas_w: int, canvas_h: int,
               padding: int) -> dict | None:
    """Load tracks/<name>.json and project lat/lon points into the
    canvas's pixel coordinate system. Returns None if the track JSON
    isn't bundled.
    """
    if not track_name:
        return None
    candidates = [track_name, track_name.lower(), track_name.replace(" ", "_").lower()]
    path = None
    for name in candidates:
        p = TRACKS_DIR / f"{name}.json"
        if p.is_file():
            path = p
            break
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[render] failed to read {path.name}: {e}")
        return None

    ontrack   = raw.get("ontrack") or []
    onpitroad = raw.get("onpitroad") or []
    if not ontrack:
        return None

    center_lat = float(raw.get("latitude") or 0.0)
    center_lon = float(raw.get("longitude") or 0.0)
    cos_c = math.cos(math.radians(center_lat))
    METRES_PER_DEG = 111320.0

    def latlon_to_xy(lat, lon):
        x = (lon - center_lon) * METRES_PER_DEG * cos_c
        y = (center_lat - lat) * METRES_PER_DEG
        return x, y

    north_deg = float(raw.get("north") or 270.0)
    rot_rad = math.radians(north_deg - 270.0)
    cos_r, sin_r = math.cos(rot_rad), math.sin(rot_rad)

    def rot(pt):
        x, y = pt
        return (x * cos_r - y * sin_r, x * sin_r + y * cos_r)

    ontrack_m   = [rot(latlon_to_xy(lat, lon)) for lat, lon in ontrack]
    onpitroad_m = [rot(latlon_to_xy(lat, lon)) for lat, lon in onpitroad]

    xs = [p[0] for p in ontrack_m] + [p[0] for p in onpitroad_m]
    ys = [p[1] for p in ontrack_m] + [p[1] for p in onpitroad_m]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    w_m = max(1.0, max_x - min_x)
    h_m = max(1.0, max_y - min_y)

    avail_w = canvas_w - 2 * padding
    avail_h = canvas_h - 2 * padding
    scale = min(avail_w / w_m, avail_h / h_m)

    def to_px(pt):
        x, y = pt
        sx = (x - min_x) * scale + (canvas_w - w_m * scale) / 2.0
        sy = (y - min_y) * scale + (canvas_h - h_m * scale) / 2.0
        return (sx, sy)

    ontrack_xy   = [to_px(p) for p in ontrack_m]
    onpitroad_xy = [to_px(p) for p in onpitroad_m]

    arc = [0.0]
    for i in range(1, len(ontrack_xy)):
        x0, y0 = ontrack_xy[i - 1]
        x1, y1 = ontrack_xy[i]
        arc.append(arc[-1] + math.hypot(x1 - x0, y1 - y0))
    total = arc[-1] or 1.0
    arc_norm = [a / total for a in arc]

    return {
        "trackname":   raw.get("trackname", track_name),
        "ontrack_xy":  ontrack_xy,
        "onpitroad_xy": onpitroad_xy,
        "arc_norm":    arc_norm,
    }


def pct_to_xy(track: dict, pct: float) -> tuple[float, float] | None:
    if not track or not track.get("ontrack_xy"):
        return None
    pct = pct % 1.0 if pct >= 0 else 0.0
    pts = track["ontrack_xy"]
    arc = track["arc_norm"]
    lo, hi = 0, len(arc) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if arc[mid] < pct: lo = mid + 1
        else:              hi = mid
    i = lo
    if i == 0:
        return pts[0]
    span = arc[i] - arc[i - 1] or 1e-9
    t = (pct - arc[i - 1]) / span
    x0, y0 = pts[i - 1]
    x1, y1 = pts[i]
    return (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)


# ---------------------------------------------------------------------------
# Position interpolation
# ---------------------------------------------------------------------------
class PositionLookup:
    """For each car, holds an ordered list of (t, pct) samples and
    returns the interpolated position at any given time. Handles the
    S/F wrap (0.99 → 0.01) cleanly so cars don't appear to teleport
    backwards across the line.
    """

    def __init__(self, positions: list[tuple[float, dict[int, float]]]):
        # Build per-car timeline
        per_car: dict[int, list[tuple[float, float]]] = {}
        for t, pos_map in positions:
            for cidx, pct in pos_map.items():
                per_car.setdefault(cidx, []).append((t, pct))
        # Sort each
        for cidx in per_car:
            per_car[cidx].sort(key=lambda e: e[0])
        self._per_car = per_car

    def cars(self) -> Iterable[int]:
        return self._per_car.keys()

    def at(self, car_idx: int, t: float) -> float | None:
        """Return interpolated lap_dist_pct for car at time t, or None."""
        timeline = self._per_car.get(car_idx)
        if not timeline:
            return None
        if t <= timeline[0][0]:
            return timeline[0][1]
        if t >= timeline[-1][0]:
            return timeline[-1][1]
        # Binary search for the surrounding pair
        lo, hi = 0, len(timeline) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if timeline[mid][0] <= t: lo = mid
            else:                      hi = mid
        t0, p0 = timeline[lo]
        t1, p1 = timeline[hi]
        # Handle S/F wrap: if pct dropped sharply between samples, the
        # car crossed the line. Unwrap by adding 1.0 to the later one.
        if p1 + 0.5 < p0:
            p1 += 1.0
        span = t1 - t0 or 1e-9
        frac = (t - t0) / span
        v = p0 + (p1 - p0) * frac
        return v % 1.0


# ---------------------------------------------------------------------------
# Standings at time t
# ---------------------------------------------------------------------------
def standings_at(t: float, drivers: list[dict],
                 laps_by_car: dict[int, list[dict]],
                 pos_lookup: PositionLookup) -> list[dict]:
    """Return drivers sorted by current track progress at time t."""
    rows = []
    for d in drivers:
        idx = d["car_idx"]
        # Most recently completed lap before t
        completed = 0
        last_lap_time = None
        for lap in laps_by_car.get(idx, []):
            if lap.get("t_session", 0) <= t:
                completed = lap.get("lap", completed)
                last_lap_time = lap.get("lap_time", last_lap_time)
            else:
                break
        pct = pos_lookup.at(idx, t)
        progress = float(completed) + (pct if pct is not None else 0.0)
        rows.append({
            "driver":    d,
            "completed": completed,
            "last_lap":  last_lap_time,
            "pct":       pct,
            "progress":  progress,
        })
    rows.sort(key=lambda r: -r["progress"])
    for i, r in enumerate(rows, start=1):
        r["pos"] = i
    return rows


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
def _font(size: int):
    """Try a few common system fonts; fall back to Pillow's bitmap."""
    candidates = [
        "C:/Windows/Fonts/segoeui.ttf",     # Windows
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",   # macOS
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def car_color(driver: dict, idx_in_field: int) -> tuple[int, int, int]:
    """Pick a color for a car. Stable per-driver via (car_number, idx)."""
    return CAR_PALETTE[idx_in_field % len(CAR_PALETTE)]


def fmt_lap(t: float | None) -> str:
    if not t or t <= 0:
        return "—"
    m = int(t // 60)
    s = t - m * 60
    if m: return f"{m}:{s:06.3f}"
    return f"{s:.3f}"


def fmt_clock(s: float) -> str:
    s = max(0, int(s))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h: return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


# ---------------------------------------------------------------------------
# Main render loop
# ---------------------------------------------------------------------------
def render(jsonl_path: Path, out_path: Path,
           fps: int = DEFAULT_FPS,
           width: int = DEFAULT_WIDTH,
           height: int = DEFAULT_HEIGHT,
           start_t: float | None = None,
           end_t:   float | None = None) -> None:

    print(f"[render] Loading {jsonl_path.name}…")
    log = parse_log(jsonl_path)
    session = log["session"]
    drivers = log["drivers"]
    laps    = log["laps"]
    incidents = log["incidents"]
    positions = log["positions"]

    if not session:
        raise SystemExit("ERROR: no session_start event found in log")
    if not drivers:
        raise SystemExit("ERROR: no drivers in session_start")
    if not positions:
        raise SystemExit(
            "ERROR: log has no position-tick events. This race was logged "
            "with an older version of iracing_race_logger.py — only races "
            "logged after the position-tick feature was added can be "
            "rendered."
        )

    track_name = (session.get("track_name") or "").strip()
    track_canvas_w = width - LEADERBOARD_W_PX - 24  # leave room for the panel
    track_canvas_h = height - 80  # leave room for header strip

    track = load_track(track_name, track_canvas_w, track_canvas_h, TRACK_PADDING_PX)
    if track is None:
        raise SystemExit(
            f"ERROR: no track outline for '{track_name}' in ./tracks/. "
            "Either add it via tracks/gpx_to_json.py or copy it in by hand."
        )

    pos_lookup = PositionLookup(positions)

    # Index laps by car for fast standings calc
    laps_by_car: dict[int, list[dict]] = {}
    for lap in laps:
        laps_by_car.setdefault(lap["car_idx"], []).append(lap)

    # Determine render time range
    t_min = positions[0][0]
    t_max = positions[-1][0]
    if start_t is not None: t_min = max(t_min, start_t)
    if end_t   is not None: t_max = min(t_max, end_t)
    if t_max <= t_min:
        raise SystemExit("ERROR: empty render range")

    duration = t_max - t_min
    n_frames = int(duration * fps)
    print(f"[render] Track: {session.get('track', '?')} ({track_name})")
    print(f"[render] Drivers: {len(drivers)}  ·  Laps: {len(laps)}  ·  Incidents: {len(incidents)}")
    print(f"[render] Duration: {duration:.0f}s  ·  Frames: {n_frames}  @ {fps}fps  ({width}×{height})")

    # Color assignment: stable per car_idx, ordered by their starting
    # position so leaders get the first palette colors.
    drivers_sorted = sorted(drivers, key=lambda d: d.get("car_number", "ZZZ"))
    color_for_idx: dict[int, tuple[int, int, int]] = {}
    for i, d in enumerate(drivers_sorted):
        color_for_idx[d["car_idx"]] = CAR_PALETTE[i % len(CAR_PALETTE)]

    # Pre-render the static track layer once — saves repainting it
    # 18,000 times in a 10-minute render.
    track_layer = Image.new("RGB", (width, height), COLOR_BG)
    tdraw = ImageDraw.Draw(track_layer)
    # Header strip
    tdraw.rectangle([0, 0, width, 60], fill=COLOR_PANEL)
    tdraw.line([0, 60, width, 60], fill=COLOR_PANEL_LINE, width=1)
    # Leaderboard panel background
    panel_x = width - LEADERBOARD_W_PX
    tdraw.rectangle([panel_x, 60, width, height], fill=COLOR_PANEL)
    tdraw.line([panel_x, 60, panel_x, height], fill=COLOR_PANEL_LINE, width=1)
    # Track outline
    if len(track["ontrack_xy"]) >= 2:
        # Translate ontrack_xy upward by 60px (header strip) so it doesn't
        # bleed into the header. Pre-compute shifted coords.
        track_pts = [(x, y + 60) for (x, y) in track["ontrack_xy"]]
        # Slightly thicker dark fill to suggest the track surface
        for w, c in [(14, COLOR_TRACK_FILL), (3, COLOR_TRACK)]:
            tdraw.line(track_pts + [track_pts[0]], fill=c, width=w, joint="curve")
    if len(track["onpitroad_xy"]) >= 2:
        pit_pts = [(x, y + 60) for (x, y) in track["onpitroad_xy"]]
        tdraw.line(pit_pts, fill=COLOR_PIT_DIM, width=2)

    # Track-projection helper that accounts for the 60-px header offset
    def to_screen(pct: float) -> tuple[float, float] | None:
        xy = pct_to_xy(track, pct)
        if xy is None:
            return None
        return (xy[0], xy[1] + 60)

    # Header text
    track_label = session.get("track", track_name)
    if session.get("track_config"):
        track_label += f" — {session['track_config']}"

    # Fonts
    f_header     = _font(20)
    f_clock      = _font(14)
    f_lap_count  = _font(14)
    f_panel_head = _font(11)
    f_drv        = _font(13)
    f_drv_small  = _font(11)
    f_dot        = _font(11)
    f_incident   = _font(13)

    # Frame output dir
    tmpdir = Path(tempfile.mkdtemp(prefix="render_race_"))
    frame_paths: list[Path] = []

    try:
        for i in range(n_frames):
            t = t_min + i / fps
            img = track_layer.copy()
            d = ImageDraw.Draw(img)

            # --- Header ---
            d.text((20, 18), track_label, font=f_header, fill=COLOR_TEXT)
            elapsed = t - t_min
            time_str = fmt_clock(elapsed)
            d.text((width - LEADERBOARD_W_PX - 140, 24),
                   f"  {time_str}", font=f_clock, fill=COLOR_TEXT_DIM)

            # --- Active incidents (last INCIDENT_FLASH_SECS seconds) ---
            active_inc = [inc for inc in incidents
                          if 0 <= t - inc.get("t_session", 0) < INCIDENT_FLASH_SECS]
            for inc in active_inc:
                cidx = inc.get("car_idx")
                pct = pos_lookup.at(cidx, inc.get("t_session", t))
                if pct is None:
                    continue
                xy = to_screen(pct)
                if not xy: continue
                age = t - inc["t_session"]
                # Pulsing ring radius
                radius = int(14 + age * 18)
                alpha_ring = int(220 * (1.0 - age / INCIDENT_FLASH_SECS))
                # PIL doesn't support alpha on RGB drawing easily — fake
                # it with a few concentric outlines fading toward bg.
                for r_off in range(0, 4):
                    if radius - r_off < 1: break
                    fade = max(0, alpha_ring - r_off * 50)
                    if fade <= 0: continue
                    blend = int(fade * 255 / 220)
                    col = (
                        min(255, COLOR_BG[0] + (COLOR_INCIDENT[0] - COLOR_BG[0]) * blend // 255),
                        min(255, COLOR_BG[1] + (COLOR_INCIDENT[1] - COLOR_BG[1]) * blend // 255),
                        min(255, COLOR_BG[2] + (COLOR_INCIDENT[2] - COLOR_BG[2]) * blend // 255),
                    )
                    d.ellipse(
                        [xy[0]-radius+r_off, xy[1]-radius+r_off,
                         xy[0]+radius-r_off, xy[1]+radius-r_off],
                        outline=col, width=2,
                    )

            # --- Cars ---
            current_standings = standings_at(t, drivers, laps_by_car, pos_lookup)
            for entry in reversed(current_standings):
                # Render trailing positions first so leaders draw on top
                drv = entry["driver"]
                pct = entry["pct"]
                if pct is None:
                    continue
                xy = to_screen(pct)
                if not xy: continue
                col = color_for_idx.get(drv["car_idx"], COLOR_ACCENT)
                # Bigger ring for leader, smaller for backmarkers
                pos = entry["pos"]
                r = 11 if pos == 1 else 9
                # White outline for visibility
                d.ellipse([xy[0]-r-2, xy[1]-r-2, xy[0]+r+2, xy[1]+r+2],
                          fill=COLOR_BG)
                d.ellipse([xy[0]-r, xy[1]-r, xy[0]+r, xy[1]+r], fill=col)
                # Car number, centered. Pillow text-anchor handling is
                # version-dependent; bbox-based centering is portable.
                num = str(drv.get("car_number", ""))[:3]
                bbox = d.textbbox((0, 0), num, font=f_dot)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                # Choose label color for legibility
                lum = (col[0]*299 + col[1]*587 + col[2]*114) / 1000
                label_col = (10, 10, 15) if lum > 140 else (255, 255, 255)
                d.text((xy[0] - tw/2 - bbox[0], xy[1] - th/2 - bbox[1]),
                       num, font=f_dot, fill=label_col)

            # --- Leaderboard panel ---
            panel_x0 = width - LEADERBOARD_W_PX
            d.text((panel_x0 + 14, 75), "LEADERBOARD",
                   font=f_panel_head, fill=COLOR_TEXT_DIM)
            row_y = 100
            row_h = 22
            visible_rows = (height - row_y - 20) // row_h
            for entry in current_standings[:visible_rows]:
                drv = entry["driver"]
                col = color_for_idx.get(drv["car_idx"], COLOR_ACCENT)
                pos = entry["pos"]
                # Position color
                pos_col = (COLOR_GOLD if pos == 1 else
                           (200, 200, 208) if pos == 2 else
                           (205, 127, 50)  if pos == 3 else
                           COLOR_TEXT_DIM)
                d.text((panel_x0 + 14, row_y), f"{pos:>2}",
                       font=f_drv, fill=pos_col)
                # Color swatch
                d.rectangle([panel_x0 + 40, row_y + 5,
                             panel_x0 + 50, row_y + 15], fill=col)
                # Number + name (truncate name if too long)
                num = str(drv.get("car_number", ""))[:3]
                name = drv.get("name", "")[:18]
                d.text((panel_x0 + 58, row_y), f"#{num}  {name}",
                       font=f_drv_small, fill=COLOR_TEXT)
                row_y += row_h

            # --- Lap counter ---
            leader_lap = current_standings[0]["completed"] if current_standings else 0
            total_laps = ""
            if log["final"]:
                # Count from final's first finisher
                final_finishers = log["final"].get("final", [])
                if final_finishers:
                    total_laps = f" / {final_finishers[0].get('laps_completed', '?')}"
            d.text((width - LEADERBOARD_W_PX - 220, 24),
                   f"Lap  {leader_lap}{total_laps}",
                   font=f_lap_count, fill=COLOR_TEXT)

            # Incident text under the header
            if active_inc:
                latest = active_inc[-1]
                txt = f"⚠ INCIDENT  #{latest.get('car_number','?')} {latest.get('driver','')}"
                d.text((20, 38), txt[:60], font=f_incident, fill=COLOR_INCIDENT)

            # Save frame
            fp = tmpdir / f"f_{i:06d}.png"
            img.save(fp)
            frame_paths.append(fp)
            if (i + 1) % max(1, n_frames // 20) == 0 or i == n_frames - 1:
                pct_done = (i + 1) / n_frames * 100
                print(f"[render]   frame {i+1}/{n_frames} ({pct_done:.0f}%)")

        # --- Assemble MP4 ---
        print(f"[render] Assembling MP4 → {out_path}")
        ffmpeg = _find_ffmpeg()
        if ffmpeg is None:
            print("\nERROR: ffmpeg not found.")
            print("  Install on Mac:     brew install ffmpeg")
            print("  Install via Python: pip install imageio-ffmpeg")
            print(f"\nFrames are still in: {tmpdir}")
            print("(You can run ffmpeg manually on them once installed.)")
            return

        cmd = [
            ffmpeg, "-y", "-framerate", str(fps),
            "-i", str(tmpdir / "f_%06d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", "20", "-movflags", "+faststart",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print("ffmpeg stderr:")
            print(result.stderr[-2000:])
            raise SystemExit(f"ffmpeg failed (code {result.returncode})")

    finally:
        # Clean up frames
        if frame_paths:
            try:
                shutil.rmtree(tmpdir)
            except Exception:
                pass

    print(f"[render] Done → {out_path}  ({out_path.stat().st_size // 1024} KB)")


def _find_ffmpeg() -> str | None:
    """Prefer an `imageio-ffmpeg` bundled binary, else system PATH."""
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    return shutil.which("ffmpeg")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="path to the JSONL log file")
    ap.add_argument("--out", help="MP4 output path (default: alongside the input)")
    ap.add_argument("--fps",   type=int, default=DEFAULT_FPS)
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    ap.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    ap.add_argument("--start", type=float, default=None,
                    help="render from this session_time (seconds)")
    ap.add_argument("--end",   type=float, default=None,
                    help="render up to this session_time (seconds)")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_file():
        raise SystemExit(f"ERROR: {inp} not found")
    out = Path(args.out) if args.out else inp.with_suffix(".mp4")

    render(inp, out,
           fps=args.fps, width=args.width, height=args.height,
           start_t=args.start, end_t=args.end)


if __name__ == "__main__":
    main()
