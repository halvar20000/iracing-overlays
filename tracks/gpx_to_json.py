"""
gpx_to_json.py — convert a gpx.studio track into a trackmap JSON file.
----------------------------------------------------------------------
Use this when iRacing runs a race at a track that's not bundled in the
SIMRacingApps library. Draw the racing line around the circuit in
https://gpx.studio/ (follow the tarmac as precisely as the zoom lets you),
export as a .gpx file, then run:

    python gpx_to_json.py <gpx_file> <iRacing_track_name>

Example:
    python gpx_to_json.py ~/Downloads/Monza.gpx monza_full

The iRacing track name is the value iRacing reports in
WeekendInfo.TrackName for that layout (lowercased, spaces → underscores).
You can see it in the iracing_trackmap.py console output when you load
into a session — the overlay logs the filename it's looking for.

Saves <iRacing_track_name>.json into the same folder as this script.
"""

from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path


def parse_trackpoints(gpx_text: str) -> list[tuple[float, float]]:
    """Extract every <trkpt lat=".." lon="..">. Consecutive duplicates
    are dropped — gpx.studio sometimes repeats a point at a vertex when
    you click-drag in place, which produces a degenerate zero-length
    segment that the arc-length math would then treat as a discontinuity.
    """
    pts = re.findall(r'<trkpt\s+lat="([^"]+)"\s+lon="([^"]+)"', gpx_text)
    out: list[tuple[float, float]] = []
    for lat, lon in pts:
        p = (float(lat), float(lon))
        if not out or out[-1] != p:
            out.append(p)
    return out


def build_track_json(points: list[tuple[float, float]], trackname: str) -> dict:
    """Turn the raw point list into the JSON shape iracing_trackmap.py expects."""
    if len(points) < 20:
        raise ValueError(
            f"only {len(points)} points — that's not enough for a useful "
            f"track outline. Try drawing more detail in gpx.studio."
        )

    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    center_lat = (min(lats) + max(lats)) / 2
    center_lon = (min(lons) + max(lons)) / 2

    # Close the loop if the user didn't quite meet the start point.
    # 20 m threshold is forgiving — most tracks are closed more tightly
    # but manually-drawn ones often stop a little short.
    first, last = points[0], points[-1]
    gap_deg = ((first[0] - last[0]) ** 2 + (first[1] - last[1]) ** 2) ** 0.5
    gap_m = gap_deg * 111_320
    if gap_m > 20:
        points = points + [first]
        print(f"  start→end gap was {gap_m:.0f} m — closed the loop")

    return {
        "trackname":   trackname,
        "latitude":    round(center_lat, 6),
        "longitude":   round(center_lon, 6),
        "north":       270.0,   # 270 = no rotation, map-north up in SVG
        "resolution":  5.0,     # metadata, not used by the overlay
        "merge_point": 0.0,
        "finish_line": 0.0,
        "ontrack":     [[lat, lon] for lat, lon in points],
        "onpitroad":   [],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gpx_file", help="input GPX file from gpx.studio")
    parser.add_argument(
        "track_name",
        help="iRacing track name (e.g. 'monza_full', 'silverstone_gp'). "
             "This is also the output JSON filename (without .json).",
    )
    args = parser.parse_args()

    gpx_path = Path(args.gpx_file)
    if not gpx_path.is_file():
        print(f"ERROR: {gpx_path} not found")
        sys.exit(1)

    text = gpx_path.read_text(encoding="utf-8")
    points = parse_trackpoints(text)
    print(f"  parsed {len(points)} trackpoints from {gpx_path.name}")

    track = build_track_json(points, args.track_name)

    out_path = Path(__file__).resolve().parent / f"{args.track_name}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(track, f, separators=(",", ":"))

    print(f"  wrote {out_path}  ({out_path.stat().st_size:,} bytes)")
    print(f"  center: {track['latitude']}, {track['longitude']}")
    print()
    print("Next: restart iracing_trackmap.py and load into a session at "
          "that track. The overlay should pick the file up automatically.")


if __name__ == "__main__":
    main()
