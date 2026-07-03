"""
iRacing Live Standings Overlay
------------------------------
Shows the current running order of a session — race, qualifying or
practice — with live intervals, session clock, weather and track temp.

Requirements:  pip install pyirsdk flask
Run:           python iracing_standings.py
Open:          http://localhost:5005

Layout:
  1. Top info bar   — session type, elapsed/remaining/total time,
                      weather (dry/wet), track temperature
  2. Driver-count   — active drivers on track / total entered
  3. Standings list — position, #, driver, interval (gap to car ahead)

Press H to toggle stream mode (transparent background for OBS).

Runs in parallel with the other iracing_*.py scripts. It connects to
iRacing independently.
"""

import threading
from flask import Flask, jsonify, render_template_string, send_file, abort

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()

from car_brands import detect_brand, resolve_logo


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _fmt_clock(secs) -> str:
    """Format seconds as H:MM:SS or MM:SS."""
    if secs is None or secs < 0 or secs > 1e9:
        return "--:--"
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _fmt_laptime(secs) -> str:
    if secs is None or secs <= 0 or secs > 1e6:
        return "--"
    m = int(secs // 60)
    s = secs - m * 60
    if m:
        return f"{m}:{s:06.3f}"
    return f"{s:.3f}"


def _fmt_gap(secs) -> str:
    """Short gap format, e.g. 0.321, 3.5, 1:02.4."""
    if secs is None or secs <= 0:
        return ""
    if secs < 60:
        return f"+{secs:.3f}" if secs < 10 else f"+{secs:.2f}"
    m = int(secs // 60)
    s = secs - m * 60
    return f"+{m}:{s:05.2f}"


# iRacing TrackWetness values (newer tire model). 0/missing = legacy data.
TRACK_WETNESS = {
    1: ("Dry",                "dry"),
    2: ("Mostly dry",         "dry"),
    3: ("Very lightly wet",   "wet"),
    4: ("Lightly wet",        "wet"),
    5: ("Moderately wet",     "wet"),
    6: ("Very wet",           "wet"),
    7: ("Extremely wet",      "wet"),
}

SKIES_LABELS = {0: "Clear", 1: "Partly cloudy", 2: "Mostly cloudy", 3: "Overcast"}


def _weather(ir) -> dict:
    """Return {'label': 'Dry'|'Wet'|..., 'class': 'dry'|'wet', 'skies': str}."""
    wetness = ir["TrackWetness"]
    label = None
    cls = "dry"
    if wetness and wetness in TRACK_WETNESS:
        label, cls = TRACK_WETNESS[wetness]
    else:
        # Fallback — older data / setup weather
        precip = ir["Precipitation"] or 0.0
        if precip > 0.05:
            label, cls = "Wet", "wet"
        else:
            label, cls = "Dry", "dry"
    skies = SKIES_LABELS.get(ir["Skies"], "")
    return {"label": label, "class": cls, "skies": skies}


# -----------------------------------------------------------------------------
# Standings poller
# -----------------------------------------------------------------------------
class StandingsPoller(SDKPoller):
    tag = "standings"
    poll_interval = 1.0

    def __init__(self):
        super().__init__()
        # Per-car pit-stop tracking. iRacing doesn't expose "last pit lap"
        # or "pit-lane time" directly, so we derive them from
        # CarIdxOnPitRoad transitions. Dicts are keyed by CarIdx.
        self._prev_on_pit: dict[int, bool] = {}   # previous tick state
        self._pit_entry_lap: dict[int, int] = {}  # lap when pit lane entered (in progress)
        self._pit_entry_t:   dict[int, float] = {}  # session time when entered
        self._last_pit_lap:  dict[int, int] = {}  # lap of most recent completed pit
        self._last_pit_time: dict[int, float] = {}  # seconds spent in pit lane on last stop

    def _driver_map(self) -> dict:
        info = self.ir["DriverInfo"] or {}
        out = {}
        for d in info.get("Drivers", []) or []:
            cidx = d.get("CarIdx")
            if cidx is None:
                continue
            if d.get("CarIsPaceCar") == 1:
                continue
            if d.get("IsSpectator") == 1:
                continue
            car_path   = d.get("CarPath", "") or ""
            car_screen = d.get("CarScreenName", "") or ""
            brand = detect_brand(car_path, car_screen)
            # iRacing exposes class metadata per driver in DriverInfo.
            # CarClassID groups drivers; CarClassShortName is a short label
            # ("GT3", "LMP2"); CarClassColor is an int whose low 24 bits are
            # an RGB colour. Default to neutral when single-class / missing.
            class_id    = int(d.get("CarClassID") or 0)
            class_name  = (d.get("CarClassShortName") or "").strip()
            class_color_raw = d.get("CarClassColor")
            try:
                cc = int(class_color_raw) if class_color_raw is not None else 0
                class_color = f"#{cc & 0xFFFFFF:06x}" if cc else "#4ade80"
            except (TypeError, ValueError):
                class_color = "#4ade80"

            out[cidx] = {
                "car_idx":    cidx,
                "name":       d.get("UserName", "") or "",
                "abbrev":     d.get("AbbrevName", "") or "",
                "car_number": d.get("CarNumber", "") or "",
                "car_path":   car_path,
                "car_name":   d.get("CarScreenNameShort") or car_screen,
                "car_class":  class_name or "",
                "class_id":   class_id,
                "class_name": class_name,
                "class_color": class_color,
                "brand":      brand,               # slug, e.g. "porsche"
                "brand_logo": bool(resolve_logo(brand)) if brand else False,
                "irating":    d.get("IRating", 0) or 0,
                "license":    d.get("LicString", "") or "",
                "team_name":  d.get("TeamName", "") or "",
            }
        return out

    def _update_pit_tracking(self, ir):
        """Detect OnPitRoad transitions and record last-pit lap + pit-lane time
        per driver. Called once per poll tick."""
        on_pit = ir["CarIdxOnPitRoad"] or []
        laps   = ir["CarIdxLap"] or []
        t_now  = ir["SessionTime"] or 0.0
        for cidx in range(len(on_pit)):
            now_on = bool(on_pit[cidx])
            was_on = self._prev_on_pit.get(cidx, False)
            if now_on and not was_on:
                # Just entered pit lane
                self._pit_entry_lap[cidx] = int(laps[cidx]) if cidx < len(laps) else 0
                self._pit_entry_t[cidx]   = t_now
            elif was_on and not now_on:
                # Just exited pit lane — compute duration
                entry_t = self._pit_entry_t.pop(cidx, None)
                entry_l = self._pit_entry_lap.pop(cidx, None)
                if entry_t is not None and entry_l is not None:
                    duration = max(0.0, t_now - entry_t)
                    self._last_pit_time[cidx] = duration
                    self._last_pit_lap[cidx]  = entry_l
            self._prev_on_pit[cidx] = now_on

    def _current_session(self, sessions, session_num):
        for s in sessions:
            if s.get("SessionNum") == session_num:
                return s
        return None

    def _session_duration(self, sess: dict) -> float:
        """Parse SessionTime string like '3600.0 sec' → seconds. Returns 0 for unlimited."""
        if not sess:
            return 0.0
        raw = sess.get("SessionTime")
        if not raw or raw == "unlimited":
            return 0.0
        try:
            s = str(raw).lower().replace("sec", "").strip()
            return float(s)
        except Exception:
            return 0.0

    def _build_race_standings(self, drivers, ir) -> list:
        """
        Live race running order.

        Position is derived from live track progress (CarIdxLap +
        CarIdxLapDistPct), NOT from CarIdxPosition. iRacing only updates
        CarIdxPosition at the start/finish line, which means an overtake
        mid-lap doesn't show up in the standings until the leader next
        crosses S/F — sometimes more than a full lap of lag. By sorting
        on track progress instead, overtakes are reflected the instant
        they happen. This is how iOverlay / RaceControl / other broadcast
        tools derive their "live" position column.

        Interval column = GAP TO CAR AHEAD (in seconds, within the same
        class). Computed by taking the difference between consecutive
        drivers' CarIdxF2Time values after sort — CarIdxF2Time itself is
        "race time behind the class leader", NOT gap to the car in front.
        CarIdxF2Time is a race-time measurement and does NOT have the
        S/F update-lag problem, so it pairs cleanly with the live
        track-progress sort.

        Lapped cars show '+N LAP' instead of a seconds interval.
        """
        positions  = ir["CarIdxPosition"] or []
        f2times    = ir["CarIdxF2Time"] or []
        laps       = ir["CarIdxLap"] or []
        lap_pct    = ir["CarIdxLapDistPct"] or []
        last_lap   = ir["CarIdxLastLapTime"] or []
        best_lap   = ir["CarIdxBestLapTime"] or []
        on_pit     = ir["CarIdxOnPitRoad"] or []
        in_world   = ir["CarIdxTrackSurface"] or []  # -1 = NotInWorld

        rows = []
        for cidx, drv in drivers.items():
            # iRacing's CarIdxPosition is read but NOT used for ordering.
            # Kept for diagnostic purposes only — position is re-assigned
            # below from live track progress.
            pos_raw = positions[cidx] if cidx < len(positions) else 0
            iracing_pos = int(pos_raw) if pos_raw and pos_raw > 0 else 0

            # CarIdxF2Time is the TOTAL race time behind the class leader
            # ("gap to leader"), not gap to the car ahead. We keep it as
            # _gap_to_leader and compute the real per-car interval below.
            # CRITICAL: accept raw == 0.0 as valid. The class leader's F2
            # is exactly 0 (they're behind themselves by nothing); treating
            # that as None used to break the diff chain and leave P2's
            # interval blank (and cascade small values down the field).
            raw = f2times[cidx] if cidx < len(f2times) else None
            if raw is None or raw < 0 or raw >= 3600:
                gap_to_leader = None
            else:
                gap_to_leader = float(raw)  # 0.0 allowed (class leader).

            row = {
                **drv,
                "position":      0,      # assigned below from live track progress
                "iracing_pos":   iracing_pos,  # raw CarIdxPosition, diagnostic only
                "interval":      None,   # filled in after sort — see below
                "_gap_to_leader": gap_to_leader,
                "lap":           int(laps[cidx]) if cidx < len(laps) else 0,
                "lap_pct":       float(lap_pct[cidx]) if cidx < len(lap_pct) else 0.0,
                "last_lap":      last_lap[cidx] if cidx < len(last_lap) else 0.0,
                "best_lap":      best_lap[cidx] if cidx < len(best_lap) else 0.0,
                "on_pit":        bool(on_pit[cidx]) if cidx < len(on_pit) else False,
                "in_world":      (in_world[cidx] != -1) if cidx < len(in_world) else True,
                "laps_behind":   0,
                # Pit tracking — empty strings when no stop has been made yet.
                "last_pit_lap":  self._last_pit_lap.get(cidx),
                "pit_lane_time": self._last_pit_time.get(cidx),
            }
            rows.append(row)

        # ------------------------------------------------------------------
        # Live running order via track progress.
        #
        # iRacing only updates CarIdxPosition at the start/finish line, so
        # an overtake mid-lap doesn't show up in CarIdxPosition until the
        # leader next crosses S/F — sometimes a full lap of lag. Sort by
        # live track progress (CarIdxLap + CarIdxLapDistPct) instead, so
        # overtakes show up instantly.
        #
        # Within each class we sort in-world cars first, then out-of-world
        # cars (DNF / disconnected / retired to garage) at the bottom,
        # both groups by descending progress.
        # ------------------------------------------------------------------
        by_class: dict = {}
        for r in rows:
            by_class.setdefault(r.get("class_id", 0), []).append(r)
        for cid, grp in by_class.items():
            grp.sort(
                key=lambda r: (
                    0 if r.get("in_world") else 1,      # in-world first
                    -(float(r["lap"]) + float(r["lap_pct"])),  # progress desc
                ),
            )
            for i, r in enumerate(grp, start=1):
                r["position"] = i

        # Sort by (class_id, in_world, position) so rows in the same class
        # sit together for class-separator rendering, and any driver who has
        # left the simulation (DNF / disconnected / retired to garage, i.e.
        # CarIdxTrackSurface == -1) drops to the bottom of their class —
        # otherwise iRacing's last-known CarIdxPosition keeps them stuck
        # mid-table while the rest of the field laps them.
        # Any stray position == 0 rows (e.g. disconnect without a stored
        # position) also go to the bottom.
        rows.sort(key=lambda r: (
            r.get("class_id", 0),
            0 if r.get("in_world") else 1,
            r["position"] if r["position"] > 0 else 99999,
        ))

        # Compute per-class position (1-based within each class group).
        _cls_counter: dict = {}
        for r in rows:
            cid = r.get("class_id", 0)
            _cls_counter[cid] = _cls_counter.get(cid, 0) + 1
            r["class_position"] = _cls_counter[cid]

        # Compute lapped cars vs class leader — compare TOTAL TRACK PROGRESS
        # (lap + lap_dist_pct), NOT the raw integer lap count. The raw
        # count would flicker to "+1 LAP" for a full lap of everyone in
        # the field every time the leader crosses the line (because
        # CarIdxLap bumps a heartbeat earlier for the leader than it does
        # for the car 0.5s behind). Using lap+pct, a car is only lapped
        # when the leader is genuinely a full track-length ahead.
        per_class_leader_progress: dict = {}
        for r in rows:
            cid = r.get("class_id", 0)
            progress = float(r.get("lap", 0) or 0) + float(r.get("lap_pct", 0.0) or 0.0)
            if cid not in per_class_leader_progress:
                per_class_leader_progress[cid] = progress
        for r in rows:
            cid = r.get("class_id", 0)
            leader_progress = per_class_leader_progress.get(cid)
            if leader_progress is None:
                continue
            my_progress = float(r.get("lap", 0) or 0) + float(r.get("lap_pct", 0.0) or 0.0)
            diff = leader_progress - my_progress
            if diff >= 1.0:
                r["laps_behind"] = int(diff)  # 1.x -> 1, 2.x -> 2, etc.

        # Estimated lap time for the track+car combination. Used as a
        # fallback during lap 1 when CarIdxF2Time isn't populated yet.
        # Falls back to 100 s if iRacing doesn't supply a value.
        est_lap = ir["EstLapTime"]
        if not est_lap or est_lap <= 0:
            est_lap = 100.0

        # Now compute real per-car interval (gap to car immediately ahead,
        # within the same class). Primary method: diff of consecutive
        # CarIdxF2Time values (cumulative "behind class leader" — two rows'
        # values differ by the gap between them). Fallback: multiply lap
        # distance percentage difference by EstLapTime, used during lap 1
        # before iRacing has computed F2Time for the field.
        prev_by_class: dict = {}
        for r in rows:
            cid = r.get("class_id", 0)
            my_total = r.get("_gap_to_leader")
            prev = prev_by_class.get(cid)
            if prev is None:
                # Class leader — no car ahead within the class.
                r["interval"] = None
            elif r.get("laps_behind", 0) >= 1:
                # Lapped: the "+N LAP" label replaces the interval.
                r["interval"] = None
            else:
                prev_total = prev.get("_gap_to_leader")
                # Prefer F2Time-based gap when either car has a populated
                # value ( > 0 ). During lap 1 both are often 0, so fall
                # through to the lap_pct-based estimate below.
                if (my_total is not None and prev_total is not None
                        and (my_total > 0 or prev_total > 0)):
                    delta = my_total - prev_total
                    # Negative delta can happen mid-frame during overtakes;
                    # clamp to 0 so the display doesn't flash "−0.4".
                    r["interval"] = max(0.0, delta)
                else:
                    # Lap-1 fallback: track-position-based estimate.
                    my_pct = float(r.get("lap_pct", 0.0) or 0.0)
                    prev_pct = float(prev.get("lap_pct", 0.0) or 0.0)
                    pct_diff = prev_pct - my_pct
                    if pct_diff < 0:
                        # Leader wrapped the S/F line but this car hasn't.
                        pct_diff += 1.0
                    r["interval"] = pct_diff * est_lap
            prev_by_class[cid] = r

        return rows

    def _build_timed_standings(self, drivers, ir) -> list:
        """
        Qualifying / practice standings: sort by best lap time ascending.
        Interval = gap to P1's best lap.
        """
        best_lap = ir["CarIdxBestLapTime"] or []
        last_lap = ir["CarIdxLastLapTime"] or []
        on_pit   = ir["CarIdxOnPitRoad"] or []
        in_world = ir["CarIdxTrackSurface"] or []

        entries = []
        for cidx, drv in drivers.items():
            bt = best_lap[cidx] if cidx < len(best_lap) else 0.0
            entries.append((bt, cidx, drv))

        # Rank: valid times first (ascending), no-time drivers after (stable)
        with_time = sorted((e for e in entries if e[0] > 0), key=lambda e: e[0])
        no_time   = [e for e in entries if not (e[0] > 0)]

        # Per-class leader times (for gap-to-class-leader in multi-class sessions).
        class_leader: dict = {}
        for bt, cidx, drv in with_time:
            cid = drv.get("class_id", 0)
            if cid not in class_leader:
                class_leader[cid] = bt

        leader_time = with_time[0][0] if with_time else 0.0

        rows = []
        pos = 0
        for bt, cidx, drv in with_time:
            pos += 1
            rows.append({
                **drv,
                "position":    pos,
                "interval":    (bt - leader_time) if pos > 1 else None,
                "best_lap":    bt,
                "last_lap":    last_lap[cidx] if cidx < len(last_lap) else 0.0,
                "on_pit":      bool(on_pit[cidx]) if cidx < len(on_pit) else False,
                "in_world":    (in_world[cidx] != -1) if cidx < len(in_world) else True,
                "lap":         0,
                "laps_behind": 0,
                "last_pit_lap":  self._last_pit_lap.get(cidx),
                "pit_lane_time": self._last_pit_time.get(cidx),
            })
        for _, cidx, drv in no_time:
            pos += 1
            rows.append({
                **drv,
                "position":    pos,
                "interval":    None,
                "best_lap":    0.0,
                "last_lap":    last_lap[cidx] if cidx < len(last_lap) else 0.0,
                "on_pit":      bool(on_pit[cidx]) if cidx < len(on_pit) else False,
                "in_world":    (in_world[cidx] != -1) if cidx < len(in_world) else True,
                "lap":         0,
                "laps_behind": 0,
                "no_time":     True,
                "last_pit_lap":  self._last_pit_lap.get(cidx),
                "pit_lane_time": self._last_pit_time.get(cidx),
            })

        # Group rows by class and assign class_position (1-based within class).
        rows.sort(key=lambda r: (r.get("class_id", 0), r["position"]))
        _cls_counter: dict = {}
        for r in rows:
            cid = r.get("class_id", 0)
            _cls_counter[cid] = _cls_counter.get(cid, 0) + 1
            r["class_position"] = _cls_counter[cid]

        return rows

    def _read_snapshot(self) -> dict:
        ir = self.ir
        self._update_pit_tracking(ir)
        info     = ir["SessionInfo"] or {}
        sessions = info.get("Sessions", []) or []
        weekend  = ir["WeekendInfo"] or {}

        session_num = ir["SessionNum"] or 0
        sess = self._current_session(sessions, session_num)
        stype_raw = (sess.get("SessionType") or "") if sess else ""
        sname = (sess.get("SessionName") or "").upper() if sess else ""

        # Normalize session type
        stype_lower = stype_raw.lower()
        if "race" in stype_lower:
            session_type = "Race"
        elif "qualif" in stype_lower:
            session_type = "Qualifying"
        elif "practice" in stype_lower or "practi" in stype_lower or "warmup" in stype_lower:
            session_type = "Practice"
        else:
            session_type = stype_raw or sname or "Session"

        drivers = self._driver_map()
        if session_type == "Race":
            rows = self._build_race_standings(drivers, ir)
        else:
            rows = self._build_timed_standings(drivers, ir)

        # Driver counts
        num_entered = len(drivers)
        num_on_track = sum(1 for r in rows if r.get("in_world"))

        # Session clock
        elapsed   = ir["SessionTime"] or 0.0
        remaining = ir["SessionTimeRemain"] or 0.0
        total     = self._session_duration(sess)
        if total <= 0 and remaining > 0:
            total = elapsed + remaining

        laps_total = sess.get("SessionLaps") if sess else None
        if isinstance(laps_total, str) and not laps_total.isdigit():
            laps_total = None
        laps_remain = ir["SessionLapsRemain"]
        if laps_remain is not None and laps_remain > 99999:
            laps_remain = None

        weather = _weather(ir)
        track_temp = ir["TrackTempCrew"]
        air_temp   = ir["AirTemp"]

        return {
            "connected":    True,
            "session_type": session_type,
            "session_name": sess.get("SessionName", "") if sess else "",
            "track":        weekend.get("TrackDisplayName", ""),
            "track_config": weekend.get("TrackConfigName", ""),
            "elapsed":      elapsed,
            "remaining":    remaining,
            "total":        total,
            "laps_total":   laps_total,
            "laps_remain":  laps_remain,
            "weather":      weather,
            "track_temp":   track_temp,
            "air_temp":     air_temp,
            "num_entered":  num_entered,
            "num_on_track": num_on_track,
            "standings":    rows,
        }



# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)


@app.after_request
def _no_cache(resp):
    # Prevent browsers / OBS from caching overlay HTML + JSON. Individual
    # routes that explicitly want caching (static assets) set their own
    # Cache-Control header before returning — we only stamp this default
    # when nothing else was set.
    if "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp
poller = StandingsPoller()


STANDINGS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Live Standings</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    /* Transparent by default — this overlay is designed to be loaded as
       an OBS Browser Source which composites it over iRacing video.
       OBS's Chromium needs both html AND body explicitly transparent. */
    html, body { background-color: rgba(0,0,0,0); }
    body {
        font-family: 'Segoe UI', system-ui, sans-serif;
        color: #e8e8ea;
        min-height: 100vh;
        padding: 12px;
        transition: background 0.2s;
    }
    /* Debug background — toggle on with H when previewing in a
       browser tab, so the overlay isn't composited over a white page. */
    body.debug-mode { background: #0a0a0f; padding: 20px; }

    /* Default panel translucency: 0.65 alpha = 65% opaque
       (35% see-through). Lets iRacing video show through behind the
       standings while text stays readable. */
    .panel { background: rgba(20,20,28,0.65); }
    /* In debug-mode, panels go fully solid so the layout is easy to
       inspect against a dark page background. */
    body.debug-mode .panel { background: #14141c; }

    .stream-toggle {
        position: fixed; top: 10px; right: 10px; z-index: 1000;
        background: rgba(20, 20, 28, 0.9);
        border: 1px solid #333; color: #bbb;
        padding: 5px 10px; font-size: 11px; border-radius: 4px;
        cursor: pointer; font-family: inherit;
        /* Hidden by default — only shows when the overlay is being
           previewed in a browser (debug-mode). OBS users never see it. */
        display: none;
    }
    body.debug-mode .stream-toggle { display: block; }

    .wrap { max-width: 1120px; margin: 0 auto; }

    .panel {
        /* Background colour set above (translucent default + opaque
           debug-mode override) — don't repeat it here or it overrides
           the debug variant. */
        border: 1px solid #26262f;
        border-radius: 8px;
        overflow: hidden;
        margin-bottom: 12px;
    }

    /* --- Top info bar (iOverlay-style: icons + values, no labels) ----- */
    .infobar-panel {
        /* No rounded corners — lets it tuck tight against the top edge of
           the standings panel below for a clean "one ribbon" look. */
        margin-bottom: 0;
        border-radius: 8px 8px 0 0;
    }
    .info-bar.compact {
        display: flex;
        align-items: center;
        gap: 34px;
        padding: 18px 26px;
        flex-wrap: wrap;
        color: #e8e8ee;
        font-variant-numeric: tabular-nums;
        font-size: 26px;
        font-weight: 600;
    }
    .info-item {
        display: inline-flex; align-items: center; gap: 12px;
        white-space: nowrap;
    }
    .info-item-track { max-width: 440px; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
    .info-item-track span { overflow: hidden; text-overflow: ellipsis; }
    .info-item .muted { color: #8a8aa0; font-weight: 500; }
    /* Inline icon. Use currentColor so it inherits text colour. */
    .ibi { width: 28px; height: 28px; flex-shrink: 0; color: #9a9aad; }

    /* Session-type pill: RACE / QUAL / PRAC with a coloured background. */
    .info-pill.session {
        display: inline-block;
        padding: 6px 18px;
        border-radius: 6px;
        font-size: 22px; font-weight: 800; letter-spacing: 2px;
        color: #0a0a0f;
        background: #e8e8ee;
    }
    .info-pill.session.session-race { background: #ff6b35; color: #0a0a0f; }
    .info-pill.session.session-qual { background: #4ade80; color: #0a0a0f; }
    .info-pill.session.session-prac { background: #22c9e0; color: #0a0a0f; }

    .weather-pill {
        display: inline-block; padding: 5px 18px; border-radius: 16px;
        font-size: 22px; font-weight: 700; letter-spacing: .5px;
    }
    .weather-pill.dry { background: #2d1f11; color: #ff9f5a; border: 1px solid #5a3a1f; }
    .weather-pill.wet { background: #0f2036; color: #61b4ff; border: 1px solid #254a73; }

    /* --- Driver count bar ------------------------------------------- */
    .count-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 10px 18px;
        background: #1b1b26;
        border-bottom: 1px solid #26262f;
    }
    .count-bar .label {
        font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
        color: #9a9aad; font-weight: 600;
    }
    .count-bar .numbers {
        font-family: 'Rajdhani', 'Segoe UI', sans-serif;
        font-size: 18px; font-weight: 700; color: #fff;
        font-variant-numeric: tabular-nums;
    }
    .count-bar .numbers .muted { color: #8a8aa0; font-weight: 500; }
    .count-bar .bar-back {
        flex: 1; max-width: 360px;
        height: 6px; background: #2a2a38; border-radius: 3px;
        margin: 0 16px; overflow: hidden;
    }
    .count-bar .bar-fill {
        height: 100%;
        background: linear-gradient(90deg, #e63946, #ff6b35);
        transition: width .3s ease;
    }

    /* --- Standings list --------------------------------------------- */
    .standings { padding: 0; }
    .row {
        display: grid;
        /* pos | brand | # | driver | interval (or laptime in quali) */
        grid-template-columns: 72px 48px 84px 1fr 220px;
        align-items: center;
        padding: 6px 18px;
        border-bottom: 1px solid #1d1d27;
        font-variant-numeric: tabular-nums;
        transition: background .15s;
    }

    .brand-cell {
        display: flex; align-items: center; justify-content: center;
        height: 28px;
    }
    .brand-cell img {
        max-width: 28px; max-height: 26px;
        object-fit: contain;
        filter: drop-shadow(0 0 1px rgba(0,0,0,0.7));
    }
    .brand-missing {
        width: 8px; height: 8px; border-radius: 50%;
        background: #2a2a38;
    }
    .row:last-child { border-bottom: none; }

    /* Zebra striping on the data rows so each driver's line reads clearly.
       The header row is :nth-child(1); the first driver is :nth-child(2),
       which we paint as the "lighter" stripe, then alternate. :not(.head)
       keeps the header untouched. */
    .standings .row:not(.head):nth-child(odd) {
        background: rgba(0, 0, 0, 0.22);
    }
    .standings .row:not(.head):nth-child(even) {
        background: rgba(255, 255, 255, 0.04);
    }
    /* Hover rule comes AFTER the zebra rules so it wins (equal specificity,
       last one listed applies). */
    .standings .row:not(.head):hover {
        background: rgba(255, 255, 255, 0.08);
    }

    .row.head {
        background: #1b1b26;
        font-size: 13px; text-transform: uppercase; letter-spacing: 1px;
        color: #7a7a90; font-weight: 700;
        padding: 10px 18px;
    }
    .row.head:hover { background: #1b1b26; }

    /* Class separator row (rendered above each group of same-class drivers).
       The --class-color CSS var is set inline from iRacing's CarClassColor
       so it matches the HUD colour iRacing assigns to each class. */
    .class-header {
        display: flex; align-items: center; gap: 10px;
        padding: 8px 18px 6px;
        background: transparent;
        border-bottom: 1px solid rgba(255,255,255,0.06);
    }
    .class-chip {
        display: inline-block;
        width: 6px; height: 18px; border-radius: 2px;
        background: var(--class-color, #4ade80);
    }
    .class-name {
        font-size: 14px; font-weight: 800; letter-spacing: 1px;
        color: var(--class-color, #4ade80);
        text-transform: uppercase;
    }

    .pos {
        font-size: 28px; font-weight: 800; color: #fff;
        text-align: center;
    }
    .pos.p1 { color: #ffd166; }
    .pos.p2 { color: #c0c0d0; }
    .pos.p3 { color: #cd7f32; }

    .num {
        display: inline-block;
        background: #1f1f2b;
        border: 1px solid #2e2e3d;
        color: #d0d0e0;
        padding: 5px 10px;
        border-radius: 4px;
        font-size: 17px; font-weight: 700;
        min-width: 56px; text-align: center;
    }

    .driver {
        font-size: 38px; font-weight: 600; color: #fff;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        padding-right: 4px;
        line-height: 1.1;
    }
    .driver .team { font-size: 16px; color: #7a7a90; font-weight: 500; display: block; margin-top: 3px; }

    .interval {
        text-align: right; color: #e8e8ee;
        font-size: 28px; font-weight: 600;
        line-height: 1.1;
    }
    .interval.leader { color: #ffd166; font-weight: 700; }
    .interval.laps   { color: #ff6b35; }
    /* "Battle" highlight — car is within 1.0s of the car ahead.
       Matches the dashboard's .gap.battle amber style so the two
       overlays are visually consistent. Only applied from lap 2 to
       skip the pack-is-close-but-it-doesn't-matter-yet start phase. */
    .interval.battle { color: #facc15; font-weight: 700; }

    /* Pit columns — amber, matching iOverlay's colour for mid-race pit data. */
    .pitlap {
        text-align: right; color: #ff9f5a;
        font-size: 18px; font-weight: 600;
    }
    .pitlane {
        text-align: right; color: #ff9f5a;
        font-size: 18px; font-weight: 600;
    }
    .pitlane.red, .pitlap.red { color: #f87171; }

    .pit-flag {
        display: inline-block;
        background: #3a1a1a;
        border: 1px solid #5c2a2a;
        color: #ff8888;
        padding: 1px 6px;
        border-radius: 3px;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.5px;
        margin-left: 6px;
    }
    .out-flag {
        color: #666;
        font-style: italic;
    }

    .waiting, .error {
        text-align: center; padding: 60px 20px;
        color: #7a7a90;
    }
    .waiting h2 { color: #e63946; margin-bottom: 10px; font-size: 22px; letter-spacing: 1px; }

    .track-line {
        padding: 10px 18px;
        font-size: 12px; color: #8a8aa0;
        border-top: 1px solid #1d1d27;
        letter-spacing: .3px;
    }
    .track-line b { color: #c8c8d8; font-weight: 600; }
</style>
</head>
<body>

<button class="stream-toggle" onclick="toggleStreamMode()">Debug background (H)</button>

<div class="wrap" id="root">
    <div class="panel waiting"><h2>WAITING FOR IRACING…</h2><div>Load into a session to see live standings.</div></div>
</div>

<script>
function toggleStreamMode() { document.body.classList.toggle('debug-mode'); }
document.addEventListener('keydown', e => {
    if (e.key === 'h' || e.key === 'H') toggleStreamMode();
});

function fmtClock(secs) {
    if (secs == null || secs < 0 || !isFinite(secs)) return '--:--';
    secs = Math.floor(secs);
    const h = Math.floor(secs/3600);
    const m = Math.floor((secs%3600)/60);
    const s = secs%60;
    return h ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
             : `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}

function fmtGap(g) {
    if (g == null || g <= 0) return '';
    if (g < 10) return '+' + g.toFixed(3);
    if (g < 60) return '+' + g.toFixed(2);
    const m = Math.floor(g/60); const s = g - m*60;
    return `+${m}:${s.toFixed(2).padStart(5,'0')}`;
}

function fmtLap(t) {
    if (!t || t <= 0) return '—';
    const m = Math.floor(t/60); const s = t - m*60;
    return m ? `${m}:${s.toFixed(3).padStart(6,'0')}` : s.toFixed(3);
}

function render(d) {
    const root = document.getElementById('root');

    if (!d.connected) {
        root.innerHTML = `
            <div class="panel waiting">
                <h2>WAITING FOR IRACING…</h2>
                <div>Load into a session to see live standings.</div>
            </div>`;
        return;
    }

    const st = d.session_type || 'Session';
    const stClass = st === 'Race' ? 'session-race'
                  : st === 'Qualifying' ? 'session-qual'
                  : 'session-prac';

    // Decide whether this race is lap-based or time-based.
    // Preference: if iRacing reports a lap target at all, treat it as a
    // lap race. Many lap-based formats (Porsche Cup, fixed-setup race
    // series) also publish a safety time cap, so the earlier check that
    // required d.total == 0 wrongly classified them as timed races.
    const isLapRace = d.laps_total != null && Number(d.laps_total) > 0;

    // "Remaining / total" text shown in the timer pill.
    // Lap races: "<remaining> / <total> LAPS"
    // Timed   : "<time remaining> / <total time>"
    let remainText, totalText;
    if (isLapRace) {
        const rem = (d.laps_remain != null) ? d.laps_remain : '—';
        remainText = `${rem}`;
        totalText  = `${d.laps_total} LAPS`;
    } else {
        remainText = fmtClock(d.remaining);
        totalText  = d.total > 0 ? fmtClock(d.total) : '—';
    }

    const trackC = d.track_temp != null ? `${d.track_temp.toFixed(1)}°C` : '—';
    const weatherCls = d.weather?.class || 'dry';
    const weatherLbl = d.weather?.label || '—';

    const pct = d.num_entered > 0 ? (d.num_on_track / d.num_entered * 100) : 0;

    let rowsHtml = '';
    let currentClass = null;
    for (const r of (d.standings || [])) {
        // Emit a class separator header row whenever the class changes.
        // Works for both single-class (one header) and multi-class sessions.
        if (r.class_id !== currentClass) {
            currentClass = r.class_id;
            const cn = (r.class_name || '').trim();
            const cc = r.class_color || '#4ade80';
            rowsHtml += `
                <div class="class-header" style="--class-color: ${cc};">
                    <span class="class-chip"></span>
                    <span class="class-name">${team_esc(cn || 'Class')}</span>
                </div>`;
        }
        // Use class_position (1-based within class) for the POS column so
        // each class starts at 1 in multi-class races. Falls back to the
        // overall position when class_position isn't set.
        const displayPos = r.class_position || r.position;
        const posCls = displayPos === 1 ? 'p1'
                     : displayPos === 2 ? 'p2'
                     : displayPos === 3 ? 'p3' : '';
        // In RACE mode the right-hand column is the gap to the car ahead.
        // In QUALIFYING / PRACTICE we show each driver's best lap time
        // instead, since "gap" is less meaningful there than the actual
        // pace each driver has put in.
        let interval = '';
        let intervalCls = 'interval';
        if (st === 'Qualifying' || st === 'Practice') {
            if (r.best_lap && r.best_lap > 0) {
                interval = fmtLap(r.best_lap);
            } else {
                interval = '<span class="out-flag">no time</span>';
            }
        } else {
            // Race (or unknown): keep the original gap-to-car-ahead logic.
            if (r.position === 1) {
                interval = 'LEADER';
                intervalCls += ' leader';
            } else if (r.laps_behind > 0) {
                interval = `+${r.laps_behind} LAP${r.laps_behind > 1 ? 'S' : ''}`;
                intervalCls += ' laps';
            } else if (r.interval != null) {
                interval = fmtGap(r.interval);
                // Battle highlight: within 1.0 s of the car ahead, from
                // lap 2 onward (lap 1 doesn't count — everyone's packed
                // together off the rolling start and it's visually noisy).
                if (r.interval > 0 && r.interval < 1.0 && (r.lap || 0) >= 2) {
                    intervalCls += ' battle';
                }
            } else {
                interval = '';
            }
        }

        const pit = r.on_pit ? ' <span class="pit-flag">PIT</span>' : '';
        const outFlag = (!r.in_world && !r.on_pit) ? ' <span class="out-flag">out</span>' : '';

        const name = r.name || 'Unknown';
        const team = r.team_name && r.team_name !== name ? `<span class="team">${team_esc(r.team_name)}</span>` : '';

        // Brand logo — if iRacing gave us a CarPath we can resolve to a
        // known brand AND a logo file exists, show it. Otherwise a small
        // placeholder dot keeps the column width stable.
        let brandHtml;
        if (r.brand && r.brand_logo) {
            const carTitle = r.car_name ? team_esc(r.car_name) : team_esc(r.brand);
            brandHtml = `<img src="/brand/${encodeURIComponent(r.brand)}" alt="${team_esc(r.brand)}" title="${carTitle}">`;
        } else {
            brandHtml = '<div class="brand-missing" title="unknown brand"></div>';
        }

        rowsHtml += `
            <div class="row">
                <div class="pos ${posCls}">${displayPos}</div>
                <div class="brand-cell">${brandHtml}</div>
                <div><span class="num">#${r.car_number || '—'}</span></div>
                <div class="driver">${team_esc(abbrevName(name))}${pit}${outFlag}${team}</div>
                <div class="${intervalCls}">${interval}</div>
            </div>`;
    }

    if (!rowsHtml) {
        rowsHtml = '<div class="row"><div style="grid-column: 1/-1; text-align:center; color:#7a7a90; padding:20px;">No drivers classified yet.</div></div>';
    }

    const trackLabel = [d.track, d.track_config].filter(x => x).join(' — ');

    // Minimalist top info bar: a session-type pill + icons with values,
    // no labels, matching iOverlay's compact style.
    const ICON_CLOCK =
        '<svg class="ibi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
    const ICON_TIMER =
        '<svg class="ibi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M10 3h4M12 3v4"/><circle cx="12" cy="13" r="8"/><path d="M12 13l3-3"/></svg>';
    const ICON_TRACK =
        '<svg class="ibi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M4 12c0-3 2-5 5-5h6c3 0 5 2 5 5s-2 5-5 5H9c-3 0-5-2-5-5z"/></svg>';
    const ICON_THERMO =
        '<svg class="ibi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M10 13V4a2 2 0 114 0v9a4 4 0 11-4 0z"/></svg>';

    root.innerHTML = `
        <div class="panel infobar-panel">
            <div class="info-bar compact">
                <div class="info-pill session ${stClass}">${st.toUpperCase()}</div>
                <div class="info-item">${ICON_CLOCK}<span>${fmtClock(d.elapsed)}</span></div>
                <div class="info-item">${ICON_TIMER}<span>${remainText}<span class="muted"> / ${totalText}</span></span></div>
                <div class="info-item info-item-track">${ICON_TRACK}<span>${team_esc(d.track || '')}${d.track_config ? ' · ' + team_esc(d.track_config) : ''}</span></div>
                <div class="info-item"><span class="weather-pill ${weatherCls}">${weatherLbl}</span></div>
                <div class="info-item">${ICON_THERMO}<span>${trackC}</span></div>
            </div>
        </div>

        <div class="panel">
            <div class="standings">
                <div class="row head">
                    <div>POS</div>
                    <div></div>
                    <div>#</div>
                    <div>DRIVER</div>
                    <div style="text-align:right;" id="col-right-label">${(st === 'Qualifying' || st === 'Practice') ? 'LAP TIME' : 'INTERVAL'}</div>
                </div>
                ${rowsHtml}
            </div>
            ${trackLabel ? `<div class="track-line"><b>${team_esc(trackLabel)}</b></div>` : ''}
        </div>
    `;
}

function team_esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
}

// "Joseph Johnson" -> "J. Johnson"
//   • Keeps single-word names whole ("Flako", "Madonna")
//   • Uses the LAST word as the surname so middle names are dropped
//     ("Nathan N Williams" -> "N. Williams", "Tim C. Huber" -> "T. Huber")
//   • Ignores non-alphanumeric tokens like trailing dots ("Flako .")
function abbrevName(full) {
    if (!full) return '';
    const parts = String(full).trim().split(/\s+/).filter(p => p && /[a-zA-Z0-9]/.test(p));
    if (parts.length === 0) return String(full);
    if (parts.length === 1) return parts[0];
    const firstInitial = parts[0].charAt(0).toUpperCase();
    const lastName = parts[parts.length - 1];
    return firstInitial + '. ' + lastName;
}

async function poll() {
    try {
        const r = await fetch('/standings');
        const d = await r.json();
        render(d);
    } catch (e) {
        // keep last view
    }
    setTimeout(poll, 1000);
}
poll();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(STANDINGS_HTML)


@app.route("/standings")
def standings():
    return jsonify(poller.get())


@app.route("/brand/<slug>")
def brand_logo(slug: str):
    path = resolve_logo(slug)
    if not path or not path.is_file():
        abort(404)
    # Let the browser cache aggressively — logos don't change mid-race.
    return send_file(str(path), max_age=3600)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main():
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()
    try:
        print("=" * 60)
        print("iRacing Live Standings")
        print("Open: http://localhost:5005")
        print("Press Ctrl+C to stop")
        print("=" * 60)
        app.run(host="0.0.0.0", port=5005, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()


if __name__ == "__main__":
    main()
