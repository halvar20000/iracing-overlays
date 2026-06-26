"""Track-geometry loader.

Loads a circuit definition from ``assets/tracks/<name>.json`` and projects its
GPS polylines into a normalised, aspect-correct 2D path the dashboard can draw.

The track files are derived from the SIMRacingApps project by Jeffrey Gilliam
(Apache-2.0) — see ``assets/tracks/NOTICE.txt`` for the full attribution.
Each file holds an ``ontrack`` outline and an ``onpitroad`` pit lane as
(latitude, longitude) waypoints, plus a ``north`` bearing.

Projection: equirectangular around the track centre (accurate to a few metres
over a circuit-sized area), rotated by the file's ``north`` field, then scaled
into the unit square with the aspect ratio preserved.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

# Track geometry is shared with the parent iRacing-overlays project when this
# component lives inside it (the overlays' tracks/ folder is a same-format
# superset); fall back to the bundled assets/tracks when run standalone.
_SHARED_TRACKS = Path(__file__).resolve().parent.parent.parent / "tracks"
_BUNDLED_TRACKS = Path(__file__).resolve().parent.parent / "assets" / "tracks"
TRACKS_DIR = _SHARED_TRACKS if _SHARED_TRACKS.is_dir() else _BUNDLED_TRACKS
_METRES_PER_DEG = 111320.0
_SAMPLES = 240                       # points in the resampled outline

_cache: dict[str, Optional[dict]] = {}


def _resample_loop(points: list[tuple[float, float]], n: int) -> list[list[float]]:
    """Resample a closed polyline to ``n`` points of equal arc-length spacing,
    so a car placed by lap-distance % moves at a steady pace."""
    if len(points) < 3:
        return [[p[0], p[1]] for p in points]
    pts = list(points) + [points[0]]
    cum = [0.0]
    for i in range(1, len(pts)):
        a, b = pts[i], pts[i - 1]
        cum.append(cum[-1] + math.hypot(a[0] - b[0], a[1] - b[1]))
    total = cum[-1] or 1e-9
    out: list[list[float]] = []
    j = 0
    for s in range(n):
        target = total * s / n
        while j < len(cum) - 1 and cum[j + 1] < target:
            j += 1
        seg = cum[j + 1] - cum[j] or 1e-9
        f = (target - cum[j]) / seg
        a, b = pts[j], pts[j + 1]
        out.append([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f])
    return out


def _normalise_name(track_name: str) -> str:
    return track_name.strip().lower().replace(" ", "_")


def _find_file(track_name: str) -> Optional[Path]:
    """Resolve an iRacing track-name slug to a bundled track file."""
    if not track_name:
        return None
    base = _normalise_name(track_name)
    for cand in (base, base.replace("-", "_"), base.replace("_", "")):
        p = TRACKS_DIR / f"{cand}.json"
        if p.is_file():
            return p
    return None


def available_tracks() -> list[str]:
    if not TRACKS_DIR.is_dir():
        return []
    return sorted(p.stem for p in TRACKS_DIR.glob("*.json"))


def load_track(track_name: str) -> Optional[dict]:
    """Return ``{name, path, pit, length_km}`` for a circuit, or ``None``.

    ``path`` and ``pit`` are lists of ``[x, y]`` in the unit square (aspect
    ratio preserved, centred). Results are cached per process.
    """
    if not track_name:
        return None
    key = _normalise_name(track_name)
    if key in _cache:
        return _cache[key]

    track_file = _find_file(track_name)
    if track_file is None:
        _cache[key] = None
        return None
    try:
        raw = json.loads(track_file.read_text(encoding="utf-8"))
    except Exception:
        _cache[key] = None
        return None

    ontrack = raw.get("ontrack") or []
    onpit = raw.get("onpitroad") or []
    if len(ontrack) < 3:
        _cache[key] = None
        return None

    # --- equirectangular projection around the track centre ---------------
    center_lat = float(raw.get("latitude") or 0.0)
    center_lon = float(raw.get("longitude") or 0.0)
    cos_c = math.cos(math.radians(center_lat))

    def to_xy(lat: float, lon: float) -> tuple[float, float]:
        x = (lon - center_lon) * _METRES_PER_DEG * cos_c
        y = (center_lat - lat) * _METRES_PER_DEG       # north up
        return (x, y)

    # SRA's "north" convention: 270 == map north points straight up.
    rot = math.radians(float(raw.get("north") or 270.0) - 270.0)
    cr, sr = math.cos(rot), math.sin(rot)

    def rotate(p: tuple[float, float]) -> tuple[float, float]:
        return (p[0] * cr - p[1] * sr, p[0] * sr + p[1] * cr)

    ot = [rotate(to_xy(la, lo)) for la, lo in ontrack]
    op = [rotate(to_xy(la, lo)) for la, lo in onpit]

    # Real track length, summed along the (closed) outline in metres.
    length_m = sum(
        math.hypot(ot[i][0] - ot[(i + 1) % len(ot)][0],
                   ot[i][1] - ot[(i + 1) % len(ot)][1])
        for i in range(len(ot)))

    # --- normalise to the unit square, aspect preserved, centred ----------
    allp = ot + op
    xs = [p[0] for p in allp]
    ys = [p[1] for p in allp]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    w = max(1e-6, max_x - min_x)
    h = max(1e-6, max_y - min_y)
    scale = 1.0 / max(w, h)

    def norm(p: tuple[float, float]) -> list[float]:
        return [round((p[0] - min_x) * scale + (1 - w * scale) / 2, 5),
                round((p[1] - min_y) * scale + (1 - h * scale) / 2, 5)]

    out = {
        "name": raw.get("trackname", key),
        "path": [norm(p) for p in _resample_loop(ot, _SAMPLES)],
        "pit": [norm(p) for p in op] if len(op) >= 2 else None,
        "length_km": round(length_m / 1000.0, 3),
    }
    _cache[key] = out
    return out
