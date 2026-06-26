"""
driver_of_the_day.py  —  "Driver of the Day" analyzer for iRacing race logs.

Reads a race JSONL log produced by iracing_race_logger.py and nominates a
Driver of the Day (DotD) from a weighted blend of four merits:

    * positions gained   (start -> finish)         — heaviest weight
    * recovery           (climb back from the worst point reached)
    * overtakes          (on-track passes, from the logger's counter)
    * clean racing       (fewer incidents = better)

The winner is deliberately NOT the race winner by default: a clean pole-to-
flag victory scores near zero on gained / recovery / overtakes, so the driver
who actually carved through the field wins instead. The race winner stays
*eligible* and will take it only when they genuinely earned it (e.g. won from
deep in the grid) — "eligible but disadvantaged".

Usage (standalone):
    python driver_of_the_day.py logs/20260611-152522_watkins_glen_cup_race.jsonl
    python driver_of_the_day.py <log> --json          # machine-readable
    python driver_of_the_day.py <log> --emit           # append driver_of_day event to the log
    python driver_of_the_day.py <log> --weights pos=0.4,rec=0.2,ot=0.25,clean=0.15
    python driver_of_the_day.py            # no arg -> newest logs/*_race.jsonl

It is also a library: iracing_race_logger.py and the DotD overlay both call
`analyze(events)` / `analyze_file(path)`.

No third-party dependencies — stdlib only.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

# ---------------------------------------------------------------------------
# Weight profiles. "balanced" and the others mirror the design choices; the
# default here is the "positions gained heaviest" profile Thomas picked.
# Each profile must sum to 1.0 across the four keys.
# ---------------------------------------------------------------------------
WEIGHT_PROFILES = {
    "positions": {"pos": 0.40, "rec": 0.20, "ot": 0.25, "clean": 0.15},
    "balanced":  {"pos": 0.30, "rec": 0.25, "ot": 0.25, "clean": 0.20},
    "recovery":  {"pos": 0.25, "rec": 0.40, "ot": 0.20, "clean": 0.15},
    "clean":     {"pos": 0.20, "rec": 0.15, "ot": 0.35, "clean": 0.30},
}
DEFAULT_PROFILE = "positions"

# Eligibility defaults
MIN_LAPS_FRACTION = 0.5        # must complete >= this share of the leader's laps
FINISHED_REASONS = {"running"} # reason_out values that count as "finished the race"


def setup_utf8_stdout():
    """Survive Windows cp1252 code pages (same trick the overlays use)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_events(path):
    """Read a JSONL log into a list of event dicts (bad lines skipped)."""
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue
    return events


def newest_race_log(logs_dir="logs"):
    """Return the path of the most recent *_race.jsonl, or None."""
    cands = glob.glob(os.path.join(logs_dir, "*_race.jsonl"))
    if not cands:
        cands = glob.glob(os.path.join(logs_dir, "*.jsonl"))
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------
def _minmax_norm(values):
    """Min-max normalise a list to 0..1. Equal values -> all 0.5."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def analyze(events, profile=DEFAULT_PROFILE, weights=None,
            min_laps_fraction=MIN_LAPS_FRACTION,
            dnf_can_win=False, exclude_names=None):
    """
    Compute Driver of the Day from a list of log events.

    `exclude_names` is an optional iterable of driver display names that are
    ranked normally but cannot be CROWNED — used for the "no back-to-back
    winner in the same season" rule (the previous round's winner is passed
    in here, so the title falls to the next eligible driver).

    Returns a dict:
        {
          "ok": bool, "error": str|None,
          "track": str, "session": str,
          "weights": {...},
          "winner": {row} | None,
          "drivers": [rows sorted by score desc],
          "meta": {...}
        }
    Each driver row carries raw metrics, normalised components, weighted
    contributions, the final score, an `eligible` flag and a `why` summary.
    """
    if weights is None:
        weights = dict(WEIGHT_PROFILES.get(profile, WEIGHT_PROFILES[DEFAULT_PROFILE]))
    # normalise weights so they always sum to 1
    wsum = sum(weights.values()) or 1.0
    weights = {k: v / wsum for k, v in weights.items()}

    # names blocked from winning (case-insensitive, trimmed)
    blocked = {(n or "").strip().lower() for n in (exclude_names or []) if n}

    start = None
    end = None
    laps = {}   # car_idx -> list of lap events in order
    for e in events:
        t = e.get("type")
        if t == "session_start" and start is None:
            start = e
        elif t == "session_end":
            end = e          # keep the last one (most final)
        elif t == "lap":
            laps.setdefault(e["car_idx"], []).append(e)

    if start is None:
        return {"ok": False, "error": "no session_start in log", "drivers": []}
    if end is None or not end.get("final"):
        return {"ok": False, "error": "no final classification (session_end) in log — "
                                      "race may not have finished", "drivers": []}

    final_by_idx = {f["car_idx"]: f for f in end["final"]}
    leader_laps = max((f.get("laps_completed", 0) for f in end["final"]), default=0)
    min_laps = leader_laps * min_laps_fraction

    drivers = []
    for d in start.get("drivers", []):
        ci = d["car_idx"]
        fin = final_by_idx.get(ci)
        if fin is None:
            continue  # never classified (DNS / spectator / pace car)

        # --- positions over the race from lap events ---
        valid_positions = [l["position"] for l in laps.get(ci, [])
                           if isinstance(l.get("position"), int) and l["position"] > 0]
        start_pos = valid_positions[0] if valid_positions else fin.get("position")
        worst_pos = max(valid_positions) if valid_positions else fin.get("position")
        finish_pos = fin.get("position")

        positions_gained = (start_pos - finish_pos) if (start_pos and finish_pos) else 0
        # recovery: how far they climbed back from their lowest point of the race
        recovery = max(0, (worst_pos - finish_pos)) if (worst_pos and finish_pos) else 0

        # --- overtakes / overtaken: cumulative counters, take the max seen ---
        ots = [l.get("overtakes", 0) for l in laps.get(ci, [])]
        otns = [l.get("overtaken", 0) for l in laps.get(ci, [])]
        overtakes = max(ots) if ots else 0
        overtaken = max(otns) if otns else 0

        incidents = fin.get("incidents", 0) or 0
        laps_completed = fin.get("laps_completed", 0) or 0
        reason_out = (fin.get("reason_out") or "").strip()
        finished = reason_out.lower() in FINISHED_REASONS

        # --- eligibility to be crowned ---
        elig_reasons = []
        eligible = True
        blocked_repeat = False
        if laps_completed < min_laps:
            eligible = False
            elig_reasons.append("did not complete %d%% of leader's distance"
                                % int(min_laps_fraction * 100))
        if not finished and not dnf_can_win:
            eligible = False
            elig_reasons.append("did not finish (%s)" % (reason_out or "DNF"))
        if (d.get("name") or "").strip().lower() in blocked:
            eligible = False
            blocked_repeat = True
            elig_reasons.append("won the previous round (no back-to-back)")

        drivers.append({
            "car_idx": ci,
            "car_number": d.get("car_number"),
            "name": d.get("name"),
            "car": d.get("car"),
            "car_class": d.get("car_class"),
            "irating": d.get("irating"),
            "start_pos": start_pos,
            "worst_pos": worst_pos,
            "finish_pos": finish_pos,
            "positions_gained": positions_gained,
            "recovery": recovery,
            "overtakes": overtakes,
            "overtaken": overtaken,
            "net_passes": overtakes - overtaken,
            "incidents": incidents,
            "laps_completed": laps_completed,
            "reason_out": reason_out,
            "finished": finished,
            "eligible": eligible,
            "blocked_repeat": blocked_repeat,
            "ineligible_reasons": elig_reasons,
        })

    if not drivers:
        return {"ok": False, "error": "no classified drivers found", "drivers": []}

    # --- normalise across the ELIGIBLE field only (so a parked car can't
    #     stretch the scale), then score everyone on that scale ---
    pool = [d for d in drivers if d["eligible"]] or drivers

    def norm_map(key, invert=False):
        vals = [d[key] for d in pool]
        normed = _minmax_norm(vals)
        m = {id(d): n for d, n in zip(pool, normed)}
        out = {}
        lo, hi = (min(vals), max(vals)) if vals else (0, 0)
        for d in drivers:
            if id(d) in m:
                n = m[id(d)]
            else:  # ineligible driver scored on the same scale, clamped
                if hi - lo < 1e-9:
                    n = 0.5
                else:
                    n = (d[key] - lo) / (hi - lo)
                    n = max(0.0, min(1.0, n))
            out[id(d)] = (1.0 - n) if invert else n
        return out

    n_pos = norm_map("positions_gained")
    n_rec = norm_map("recovery")
    n_ot = norm_map("overtakes")
    n_clean = norm_map("incidents", invert=True)  # fewer incidents -> higher

    for d in drivers:
        c_pos = weights["pos"] * n_pos[id(d)]
        c_rec = weights["rec"] * n_rec[id(d)]
        c_ot = weights["ot"] * n_ot[id(d)]
        c_clean = weights["clean"] * n_clean[id(d)]
        d["components"] = {
            "positions_gained": round(c_pos, 4),
            "recovery": round(c_rec, 4),
            "overtakes": round(c_ot, 4),
            "clean": round(c_clean, 4),
        }
        d["norm"] = {
            "positions_gained": round(n_pos[id(d)], 3),
            "recovery": round(n_rec[id(d)], 3),
            "overtakes": round(n_ot[id(d)], 3),
            "clean": round(n_clean[id(d)], 3),
        }
        d["score"] = round(c_pos + c_rec + c_ot + c_clean, 4)
        d["why"] = _why(d)

    # Rank purely by score so a blocked/ineligible driver still appears at the
    # rank their drive earned (marked), while the crown goes to the best
    # *eligible* driver below them.
    drivers.sort(key=lambda d: d["score"], reverse=True)

    winner = next((d for d in drivers if d["eligible"]), None)

    return {
        "ok": True,
        "error": None,
        "track": start.get("track"),
        "track_config": start.get("track_config"),
        "session": start.get("session_name") or start.get("session_type"),
        "weights": weights,
        "profile": profile,
        "winner": winner,
        "drivers": drivers,
        "excluded_names": sorted({d["name"] for d in drivers if d["blocked_repeat"]}),
        "meta": {
            "n_drivers": len(drivers),
            "leader_laps": leader_laps,
            "official": end.get("official"),
        },
    }


def _why(d):
    """Short human explanation of a driver's standout merits."""
    bits = []
    if d["positions_gained"] > 0:
        bits.append("gained %d position%s (P%s→P%s)" %
                    (d["positions_gained"], "" if d["positions_gained"] == 1 else "s",
                     d["start_pos"], d["finish_pos"]))
    elif d["positions_gained"] < 0:
        bits.append("lost %d (P%s→P%s)" %
                    (-d["positions_gained"], d["start_pos"], d["finish_pos"]))
    if d["recovery"] > 0:
        bits.append("recovered %d from P%s low" % (d["recovery"], d["worst_pos"]))
    if d["overtakes"]:
        bits.append("%d overtake%s" % (d["overtakes"], "" if d["overtakes"] == 1 else "s"))
    bits.append("%d incident%s" % (d["incidents"], "" if d["incidents"] == 1 else "s"))
    return ", ".join(bits)


def analyze_file(path, **kw):
    return analyze(load_events(path), **kw)


# ---------------------------------------------------------------------------
# Pretty console output
# ---------------------------------------------------------------------------
def format_report(result, top=10, color=True):
    if not result.get("ok"):
        return "Driver of the Day: cannot compute — %s" % result.get("error")

    def c(code, s):
        return ("\033[%sm%s\033[0m" % (code, s)) if color else s

    w = result["winner"]
    lines = []
    title = "  DRIVER OF THE DAY  "
    track = result.get("track") or "?"
    cfg = result.get("track_config")
    track_s = "%s %s" % (track, cfg) if cfg else track
    lines.append(c("1;33", "=" * 60))
    lines.append(c("1;33", title.center(60, " ")))
    lines.append(c("0;90", ("%s  •  %s" % (track_s, result.get("session") or "Race")).center(60)))
    season = result.get("season") or {}
    if season.get("name"):
        lines.append(c("0;90", ("%s" % season["name"]).center(60)))
    lines.append(c("1;33", "=" * 60))
    if result.get("previous_winner"):
        lines.append(c("0;90", "  no back-to-back: %s won the previous round and is "
                       "blocked here" % result["previous_winner"]))
    if w:
        lines.append("")
        lines.append("  " + c("1;32", "#%s  %s" % (w["car_number"], w["name"])))
        lines.append("  " + c("0;37", w.get("car") or ""))
        lines.append("  " + c("1;37", w["why"]))
        lines.append("  " + c("0;90", "score %.3f" % w["score"]))
        lines.append("")
        # contribution bar
        comp = w["components"]
        for label, key in (("positions", "positions_gained"), ("recovery", "recovery"),
                           ("overtakes", "overtakes"), ("clean   ", "clean")):
            val = comp[key]
            bar = "█" * int(round(val / max(result["weights"].get(
                {"positions_gained": "pos", "recovery": "rec",
                 "overtakes": "ot", "clean": "clean"}[key], "pos"), 1e-9) * 18))
            lines.append("    %s %s %.3f" % (label, c("0;36", bar.ljust(18)), val))
    lines.append("")
    lines.append(c("1;37", "  Full ranking:"))
    hdr = "  %-3s %-20s %5s %4s %4s %4s %6s" % ("Pos", "Driver", "Gain", "Rec", "OT", "Inc", "Score")
    lines.append(c("0;90", hdr))
    for i, d in enumerate(result["drivers"][:top], 1):
        tag = " (prev)" if d.get("blocked_repeat") else ("" if d["eligible"] else " (x)")
        name = (d["name"] or "")[:18] + tag
        row = "  %-3d %-22s %+5d %4d %4d %4d %6.3f" % (
            i, name[:22], d["positions_gained"], d["recovery"],
            d["overtakes"], d["incidents"], d["score"])
        lines.append(c("1;32", row) if (w and d["car_idx"] == w["car_idx"]) else row)
    if any(not d["eligible"] for d in result["drivers"][:top]):
        lines.append(c("0;90", "  (x) = ineligible (DNF or under distance)   "
                              "(prev) = won last round, blocked from back-to-back"))
    return "\n".join(lines)


def to_log_event(result):
    """Build a compact 'driver_of_day' event suitable for appending to the log."""
    w = result.get("winner")
    season = result.get("season") or {}
    return {
        "type": "driver_of_day",
        "ok": result.get("ok"),
        "winner": None if not w else {
            "car_idx": w["car_idx"], "car_number": w["car_number"],
            "name": w["name"], "car": w.get("car"),
            "score": w["score"], "why": w["why"],
            "positions_gained": w["positions_gained"], "recovery": w["recovery"],
            "overtakes": w["overtakes"], "incidents": w["incidents"],
            "start_pos": w["start_pos"], "finish_pos": w["finish_pos"],
        },
        "profile": result.get("profile"),
        "weights": result.get("weights"),
        # no-back-to-back rule context (present when the streak rule ran)
        "season": season.get("name"),
        "season_key": season.get("key"),
        "previous_winner": result.get("previous_winner"),
        "ranking": [
            {"car_number": d["car_number"], "name": d["name"], "score": d["score"]}
            for d in result.get("drivers", [])[:5]
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_weights(s):
    out = {}
    for part in s.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = float(v)
    return out or None


def main(argv=None):
    setup_utf8_stdout()
    ap = argparse.ArgumentParser(description="Nominate the Driver of the Day from a race log.")
    ap.add_argument("log", nargs="?", help="path to a *_race.jsonl log (default: newest in ./logs)")
    ap.add_argument("--profile", default=DEFAULT_PROFILE, choices=list(WEIGHT_PROFILES),
                    help="weighting profile (default: %(default)s)")
    ap.add_argument("--weights", type=_parse_weights,
                    help="custom weights, e.g. pos=0.4,rec=0.2,ot=0.25,clean=0.15")
    ap.add_argument("--top", type=int, default=10, help="rows to show in the ranking")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--emit", action="store_true",
                    help="append a driver_of_day event to the log file")
    ap.add_argument("--dnf-can-win", action="store_true",
                    help="allow a DNF driver to be crowned")
    ap.add_argument("--allow-repeat", action="store_true",
                    help="disable the no-back-to-back rule (allow the previous "
                         "round's winner to win again)")
    ap.add_argument("--record", action="store_true",
                    help="record this winner into the season history "
                         "(dotd_history.json) so the streak rule sees it")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)

    path = args.log or newest_race_log()
    if not path:
        print("No log file given and none found in ./logs", file=sys.stderr)
        return 2
    if not os.path.exists(path):
        print("Log not found: %s" % path, file=sys.stderr)
        return 2

    no_repeat = not args.allow_repeat
    if no_repeat or args.record:
        # streak-aware path (resolves season from the league-manager + history)
        import dotd_streak
        result = dotd_streak.pick(path, profile=args.profile, weights=args.weights,
                                  dnf_can_win=args.dnf_can_win,
                                  no_repeat=no_repeat, record=args.record)
    else:
        result = analyze_file(path, profile=args.profile, weights=args.weights,
                              dnf_can_win=args.dnf_can_win)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(format_report(result, top=args.top, color=not args.no_color))

    if args.emit and result.get("ok"):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(to_log_event(result), ensure_ascii=False) + "\n")
        if not args.json:
            print("\n  driver_of_day event appended to %s" % os.path.basename(path))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
