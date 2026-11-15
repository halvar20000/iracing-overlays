#!/bin/bash
# Renders the zoom videos listed in zoom_list.tsv (prio A/B/C, file, title,
# subtitle). Usage: ./make_track_zooms.sh [prios, default "AB"] - existing
# videos in zoom_videos/ are skipped (delete one to render it again).
cd "$(dirname "$0")"
PRIOS="${1:-AB}"
grep -v '^#' zoom_list.tsv | while IFS=$'\t' read -r prio file title sub; do
  [[ "$PRIOS" == *"$prio"* ]] || continue
  [ -f "zoom_videos/$file.mp4" ] && { echo "skip $file"; continue; }
  echo "=== $file"
  python3 make_track_zoom.py "tracks/$file.json" "$title" "$sub" | tail -1
done
