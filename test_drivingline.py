"""
Offline tests for iracing_drivingline.py — no iRacing needed.

Stubs irsdk, then checks:
  1. Corner detection on real bundled tracks (count plausible, loop
     direction correct, first corner direction matches reality).
  2. Chicane handling at Monza (adjacent L/R kept as separate corners).
  3. compute_cue(): countdown monotonic, in_corner flag, S/F wrap.

Run:  python test_drivingline.py
"""

from __future__ import annotations

import json
import math
import sys
import types
from pathlib import Path

# --- stub irsdk before importing the overlay --------------------------------
stub = types.ModuleType("irsdk")
class _IRSDK:  # noqa: D401
    def __init__(self): pass
    def startup(self): return False
    def shutdown(self): pass
    is_initialized = False
    is_connected = False
stub.IRSDK = _IRSDK
sys.modules.setdefault("irsdk", stub)

import iracing_drivingline as dl  # noqa: E402

HERE = Path(__file__).resolve().parent
PASS = []
FAIL = []


def check(name: str, ok: bool, detail: str = ""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def analyse(track_file: str, official_len: float | None = None):
    raw = json.loads((HERE / "tracks" / f"{track_file}.json").read_text(encoding="utf-8"))
    pts = dl._project_to_meters(raw)
    samples = dl._resample(pts, dl.RESAMPLE_M)
    proj_len = len(samples) * dl.RESAMPLE_M
    scale = (official_len / proj_len) if official_len else 1.0
    corners = dl.detect_corners(samples, dl.RESAMPLE_M, scale)
    k = dl._signed_curvature(samples, dl.RESAMPLE_M)
    total_turn_deg = math.degrees(sum(v * dl.RESAMPLE_M for v in k))
    return samples, proj_len * scale, corners, total_turn_deg


def show(track, corners, length, total_turn):
    print(f"\n--- {track}: {len(corners)} corners, {length:.0f} m, "
          f"net heading {total_turn:+.0f} deg")
    for c in corners:
        print(f"    T{c['num']:>2} {c['dir']}  entry {c['entry_pct']:.3f}  "
              f"r={c['radius_m']:>6.1f} m  {c['turn_deg']:>5.1f} deg  "
              f"{c['severity']:<7} ~{c['est_kmh']} km/h")


# ---------------------------------------------------------------------------
print("=" * 64)
print("1) Okayama Full (real: 13 numbered corners, CLOCKWISE, T1+T2 right)")
samples, length, corners, turn = analyse("okayama_full", 3703.0)
show("okayama_full", corners, length, turn)
check("okayama length ~3703 m", abs(length - 3703) < 40, f"{length:.0f}")
check("okayama clockwise (net +360 deg)", abs(turn - 360) < 30, f"{turn:+.0f}")
check("okayama corner count 8..16", 8 <= len(corners) <= 16, str(len(corners)))
check("okayama first corner is RIGHT", corners and corners[0]["dir"] == "R",
      corners[0]["dir"] if corners else "none")
hairpin = any(c["severity"] == "HAIRPIN" for c in corners)
check("okayama has a hairpin", hairpin)  # real Okayama hairpin (T9)

# ---------------------------------------------------------------------------
print("\n2) Monza (real: 11 corners incl. 2 chicanes, CLOCKWISE)")
samples, length, corners, turn = analyse("monza_full", 5793.0)
show("monza_full", corners, length, turn)
check("monza length ~5793 m", abs(length - 5793) < 60, f"{length:.0f}")
check("monza clockwise (net +360 deg)", abs(turn - 360) < 30, f"{turn:+.0f}")
check("monza corner count 7..14", 7 <= len(corners) <= 14, str(len(corners)))
# Rettifilo chicane: first two corners after S/F should be opposite directions,
# close together (R-L for variante del Rettifilo driven direction)
if len(corners) >= 2:
    c1, c2 = corners[0], corners[1]
    gap_m = ((c2["entry_pct"] - c1["exit_pct"]) % 1.0) * length
    check("monza T1/T2 form a chicane (opposite dirs, <60 m apart)",
          c1["dir"] != c2["dir"] and gap_m < 60,
          f"{c1['dir']}/{c2['dir']}, gap {gap_m:.0f} m")
else:
    check("monza T1/T2 form a chicane", False, "fewer than 2 corners")

# ---------------------------------------------------------------------------
print("\n3) Laguna Seca (real: 11 corners, COUNTER-CLOCKWISE, Corkscrew)")
try:
    samples, length, corners, turn = analyse("lagunaseca", 3602.0)
    show("lagunaseca", corners, length, turn)
    check("laguna counter-clockwise (net -360 deg)", abs(turn + 360) < 30,
          f"{turn:+.0f}")
    check("laguna corner count 7..14", 7 <= len(corners) <= 14, str(len(corners)))
except FileNotFoundError:
    print("    skipped (lagunaseca.json not local)")

# ---------------------------------------------------------------------------
print("\n4) compute_cue() — countdown, wrap, in_corner")
track = {"length_m": 4000.0, "corners": [
    {"num": 1, "entry_pct": 0.10, "apex_pct": 0.12, "exit_pct": 0.14,
     "dir": "R", "radius_m": 50, "turn_deg": 90, "severity": "TIGHT", "est_kmh": 88},
    {"num": 2, "entry_pct": 0.50, "apex_pct": 0.52, "exit_pct": 0.54,
     "dir": "L", "radius_m": 120, "turn_deg": 60, "severity": "MEDIUM", "est_kmh": 137},
    {"num": 3, "entry_pct": 0.90, "apex_pct": 0.92, "exit_pct": 0.94,
     "dir": "R", "radius_m": 30, "turn_deg": 160, "severity": "HAIRPIN", "est_kmh": 68},
]}

cue = dl.compute_cue(track, 0.0)
check("at S/F next corner is T1", cue["next"][0]["num"] == 1)
check("distance to T1 at pct 0 is 400 m", abs(cue["next"][0]["dist_m"] - 400) < 1,
      str(cue["next"][0]["dist_m"]))

# monotonic countdown approaching T1
dists = [dl.compute_cue(track, p)["next"][0]["dist_m"] for p in
         (0.02, 0.04, 0.06, 0.08, 0.095)]
check("countdown monotonic", all(b < a for a, b in zip(dists, dists[1:])),
      str(dists))

cue = dl.compute_cue(track, 0.12)
check("inside T1 -> in_corner set", cue["in_corner"] and cue["in_corner"]["num"] == 1)
check("inside T1 -> next is T2", cue["next"][0]["num"] == 2)

cue = dl.compute_cue(track, 0.95)  # past T3 exit -> wrap to T1
check("after last corner wraps to T1", cue["next"][0]["num"] == 1)
check("wrap distance correct (0.15 lap = 600 m)",
      abs(cue["next"][0]["dist_m"] - 600) < 1, str(cue["next"][0]["dist_m"]))

cue = dl.compute_cue(track, 0.40)
check("second-next preview present", len(cue["next"]) == 2
      and cue["next"][1]["num"] == 3)

# wrap-spanning corner (entry 0.98, exit 0.02)
track2 = {"length_m": 1000.0, "corners": [
    {"num": 1, "entry_pct": 0.98, "apex_pct": 0.99, "exit_pct": 0.02,
     "dir": "L", "radius_m": 40, "turn_deg": 80, "severity": "TIGHT", "est_kmh": 79}]}
cue = dl.compute_cue(track2, 0.99)
check("corner spanning S/F: in_corner at pct 0.99", bool(cue["in_corner"]))
cue = dl.compute_cue(track2, 0.01)
check("corner spanning S/F: in_corner at pct 0.01", bool(cue["in_corner"]))

# ---------------------------------------------------------------------------
print("\n5) cue override merge")
import tempfile, os
ov_dir = dl.CUES_DIR
made = False
try:
    ov_dir.mkdir(exist_ok=True)
    made = True
    (ov_dir / "_test_track.json").write_text(
        json.dumps({"1": {"gear": 2, "name": "Hairpin"}}), encoding="utf-8")
    cs = [{"num": 1, "severity": "TIGHT"}]
    out = dl._apply_cue_overrides("_test_track", cs)
    check("override merged", out[0].get("gear") == 2 and out[0].get("name") == "Hairpin")
finally:
    try:
        os.remove(ov_dir / "_test_track.json")
    except OSError:
        pass

# ---------------------------------------------------------------------------
print("\n" + "=" * 64)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)
print("ALL PASS")
