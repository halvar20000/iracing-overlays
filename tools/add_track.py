"""
add_track.py — ingest a user-supplied GPX file into the bundled track
library used by iracing_trackmap.py.

Usage:
    python tools/add_track.py <trackname> <track_outline.gpx> [pit_lane.gpx]

Examples:
    python tools/add_track.py watkinsglen_2021_fullcourse my_lap.gpx
    python tools/add_track.py cota_gp cota_full.gpx cota_pit.gpx

The <trackname> must match what iRacing reports as WeekendInfo.TrackName
(lowercase, spaces replaced with underscores). Look it up by loading the
session at that track and checking /state from the trackmap overlay.

Supported GPX input formats:
  * <rtept> route points  (SIMRacingApps style)
  * <trkpt> track points  (most GPS loggers, Marlin Track Mapper, Strava)
  * <wpt>   waypoints     (rare, fallback)

Output goes to ./tracks/<trackname>.json in the same format the runtime
uses, so nothing else needs to change.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path


# Regex accepts any of the three GPX point element names, with lat/lon
# attributes in either order.
_PT_RE = re.compile(
    r'<(?:trkpt|rtept|wpt)\s+[^>]*?'
    r'(?:lat="([^"]+)"[^>]*?lon="([^"]+)"|lon="([^"]+)"[^>]*?lat="([^"]+)")',
    re.I,
)


def parse_gpx(path: Path) -> list[list[float]]:
    """Return a list of [lat, lon] pairs from a GPX file."""
    if not path.is_file():
        raise SystemExit(f"GPX file not found: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    pts: list[list[float]] = []
    for m in _PT_RE.finditer(text):
        # Two capture-group orderings (lat-first vs lon-first).
        if m.group(1) is not None:
            lat, lon = m.group(1), m.group(2)
        else:
            lat, lon = m.group(4), m.group(3)
        try:
            pts.append([float(lat), float(lon)])
        except ValueError:
            continue
    return pts


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    trackname = sys.argv[1].strip().lower().replace(" ", "_")
    ontrack_gpx = Path(sys.argv[2])
    onpitroad_gpx = Path(sys.argv[3]) if len(sys.argv) >= 4 else None

    ontrack = parse_gpx(ontrack_gpx)
    if len(ontrack) < 10:
        print(f"ERROR: {ontrack_gpx} has only {len(ontrack)} points. "
              f"Expected a full-lap outline (usually 200+).")
        return 1

    onpitroad = parse_gpx(onpitroad_gpx) if onpitroad_gpx else []

    # Track center — mean of all outline points. Good enough for the
    # equirectangular projection the runtime uses around a small area.
    center_lat = sum(p[0] for p in ontrack) / len(ontrack)
    center_lon = sum(p[1] for p in ontrack) / len(ontrack)

    out = {
        "trackname":    trackname,
        "latitude":     center_lat,
        "longitude":    center_lon,
        "north":        270.0,   # SRA convention: 270 = north points up
        "resolution":   1.0,
        "merge_point":  0.0,
        "finish_line":  270.0,
        "ontrack":      ontrack,
        "onpitroad":    onpitroad,
        "_source":      f"imported via add_track.py from {ontrack_gpx.name}",
    }

    out_dir = Path(__file__).resolve().parent.parent / "tracks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{trackname}.json"
    out_path.write_text(json.dumps(out, separators=(",", ":")))

    print(f"OK  wrote {out_path}")
    print(f"    {len(ontrack)} outline points, {len(onpitroad)} pit-lane points")
    print(f"    center  lat={center_lat:.6f}  lon={center_lon:.6f}")
    print(f"    lat     {min(p[0] for p in ontrack):.4f} to {max(p[0] for p in ontrack):.4f}")
    print(f"    lon     {min(p[1] for p in ontrack):.4f} to {max(p[1] for p in ontrack):.4f}")
    print()
    print("Restart iracing_trackmap.py (or launch_all.py) for the new track to be picked up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
