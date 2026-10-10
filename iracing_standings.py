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
  3. Standings list — position, #, driver, interval (gap to car ahead),
                      and in RACE sessions a second column with the
                      positions gained / lost vs the starting grid

Press H to toggle stream mode (transparent background for OBS).

Runs in parallel with the other iracing_*.py scripts. It connects to
iRacing independently.
"""

import threading
from flask import Flask, Response, jsonify, render_template_string, request, send_file, abort

from iracing_sdk_base import SDKPoller, GridBaseline, SESSION_STATE_RACING, setup_utf8_stdout
setup_utf8_stdout()

from car_brands import detect_brand, resolve_logo
from cls_proam import ProAmRoster
import country_flags


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
# In RACE sessions the tower shows TWO data columns side by side:
#   INTERVAL  — gap to the car immediately ahead (within the same class)
#   +/-       — positions gained / lost vs the starting grid
# Both are always visible; there is no view cycling. Qualifying and practice
# keep the single LAP TIME column.


def _fill_class_labels(drivers: dict) -> None:
    """Give every class a readable label when iRacing sends none.

    Single-class hosted sessions often report an EMPTY CarClassShortName
    (Zandvoort GT3, 07.10.: '' for all 22 cars), which made the tower's
    class tab read "Class". Derive one from the cars instead:
      * one car model in the class   -> its short name ("911 GT3 Cup")
      * several models               -> the words ALL of them share, e.g.
        "Porsche 911 GT3 R (992)" / "McLaren 720S GT3 EVO" /
        "Corvette GT3.R" -> "GT3"
      * nothing in common            -> left empty (tab falls back to "Class")
    """
    import re
    by_class: dict = {}
    for d in drivers.values():
        if not d.get("class_name"):
            by_class.setdefault(d.get("class_id", 0), []).append(d)
    for members in by_class.values():
        models = {(m.get("car_screen") or m.get("car_name") or "").strip() for m in members}
        models.discard("")
        label = ""
        if len(models) == 1:
            label = members[0].get("car_name") or next(iter(models))
        elif models:
            token_sets = [re.findall(r"[A-Za-z0-9]+", m) for m in models]
            common = set(token_sets[0]).intersection(*map(set, token_sets[1:]))
            # keep the order of the first name ("GT3 Cup", not "Cup GT3")
            label = " ".join(t for t in token_sets[0] if t in common)
        for m in members:
            m["class_name"] = label
            m["car_class"] = m.get("car_class") or label


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

        # Positions gained/lost feature. The baseline is each car's slot on
        # the STARTING GRID, captured once per race session by GridBaseline
        # (see iracing_sdk_base.py — it also handles the session-change
        # reset and refuses to invent a baseline when the overlay attaches
        # mid-race). pos_delta = grid_slot - current_class_position, i.e. a
        # NET value vs the start that never accumulates. Rendered as a
        # permanent second column next to the interval in race sessions.
        self._grid = GridBaseline()

        # Qualifying memory: every driver's best OFFICIAL time this quali
        # session, keyed by iRacing customer ID. A driver who quits the
        # session drops out of DriverInfo / ResultsPositions, and his time
        # used to vanish with him — so whoever was P2 suddenly read as pole.
        # Times are kept until the session changes (see _quali_memory_for).
        self._quali_key = None
        self._quali_mem: dict = {}   # key -> {"drv": {...}, "best": float}

        # Fastest-lap banner (/fastest): best lap per class this session.
        # Seeded silently on the first tick of a session, so starting the
        # overlay mid-race never pops an old lap; afterwards every
        # improvement becomes an event with a running sequence number.
        self._fl_key = None
        self._fl_best: dict = {}     # class_id -> (time, row snapshot)
        self._fl_seq = 0
        self._fl_event: dict | None = None

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
                "user_id":    d.get("UserID"),
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
                # Flag: the driver's iRacing flair first, CLS registration
                # country as the fallback (see country_flags.py).
                "flair":      (d.get("FlairName") or "").strip(),
                "car_screen": car_screen,
            }
        _fill_class_labels(out)
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

    def _build_race_standings(self, drivers, ir, sess=None) -> list:
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
        #
        # BEFORE THE GREEN this is meaningless (2026-10-08, Zandvoort replay):
        # iRacing freezes every car's lap counter from gridding through the
        # pace lap, and the timing line can run THROUGH the grid — at
        # Zandvoort the front ten sit at 0.00-0.01 of a lap, the back twelve
        # at 0.98-0.999. Sorted by lap+pct the back half of the grid "led"
        # the race and every +/- read ±10..12. So until SessionState reaches
        # Racing the tower shows the STARTING GRID order instead (GridBaseline,
        # then iRacing's own CarIdxPosition), and no intervals / lap-downs.
        # ------------------------------------------------------------------
        state = int(ir["SessionState"] or 0)
        pre_green = 0 < state < SESSION_STATE_RACING
        self._grid.update(
            self.ir,
            class_of={r["car_idx"]: r.get("class_id", 0) for r in rows},
        )
        grid_slot = self._grid.class_grid_pos

        def _order_key(r):
            if pre_green:
                return (
                    0 if r.get("in_world") else 1,
                    grid_slot.get(r["car_idx"], 9999),
                    r.get("iracing_pos") or 9999,
                    -(float(r["lap"]) + float(r["lap_pct"])),
                )
            return (
                0 if r.get("in_world") else 1,      # in-world first
                -(float(r["lap"]) + float(r["lap_pct"])),  # progress desc
            )

        by_class: dict = {}
        for r in rows:
            by_class.setdefault(r.get("class_id", 0), []).append(r)
        for cid, grp in by_class.items():
            grp.sort(key=_order_key)
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

        # ------------------------------------------------------------------
        # Positions gained / lost vs the STARTING GRID.
        #
        #   pos_delta = grid_slot_in_class - current_class_position
        #
        # This is a NET comparison against where the driver started, and
        # nothing about it accumulates. Pole man drops to P2 in turn 1 →
        # -1. He takes the place straight back → 0. Loses and retakes it
        # five more times → still 0. Only where he is NOW versus where he
        # STARTED matters; the churn in between is deliberately invisible
        # here. (If you want "how many passes did he actually make", that
        # is the race logger's cumulative overtakes counter, a different
        # statistic on purpose.)
        #
        # GridBaseline (iracing_sdk_base.py) owns the baseline: source
        # priority qualifying results → race StartingPosition → a green-
        # flag sample, session-change reset, and a hard refusal to invent
        # a baseline when we attached mid-race. When it has nothing for a
        # car, pos_delta stays None and the cell renders empty.
        # ------------------------------------------------------------------
        for r in rows:
            r["pos_delta"] = self._grid.class_delta(
                r["car_idx"], r.get("class_position")
            )
            r["grid_pos"] = self._grid.class_grid_pos.get(r["car_idx"])

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
            if diff >= 1.0 and not pre_green:
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
            if prev is None or pre_green:
                # Class leader — no car ahead within the class. (Before the
                # green there is no gap to show at all.)
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

    def _track_fastest(self, ir, rows, session_type) -> dict:
        key = (ir["SessionUniqueID"], ir["SessionNum"])
        seed = key != self._fl_key
        if seed:
            self._fl_key, self._fl_best, self._fl_event = key, {}, None
        lapnums = ir["CarIdxBestLapNum"] or []
        multi = len({r.get("class_id", 0) for r in rows}) > 1
        best_now: dict = {}
        for r in rows:
            bl = r.get("best_lap") or 0
            cid = r.get("class_id", 0)
            if bl > 0 and r.get("car_idx", -1) >= 0 and (cid not in best_now or bl < best_now[cid][0]):
                best_now[cid] = (bl, r)
        for cid, (bl, r) in best_now.items():
            prev = self._fl_best.get(cid)
            if prev and bl >= prev[0] - 1e-4:
                continue
            self._fl_best[cid] = (bl, {"name": r.get("name"), "car_number": r.get("car_number")})
            if seed:
                continue
            ci = r.get("car_idx", -1)
            lap = lapnums[ci] if 0 <= ci < len(lapnums) else None
            self._fl_seq += 1
            self._fl_event = {
                "seq": self._fl_seq,
                "time": bl,
                "prev_time": prev[0] if prev else None,
                "delta": (bl - prev[0]) if prev else None,
                "prev_name": prev[1]["name"] if prev else None,
                "name": r.get("name"), "car_number": r.get("car_number"),
                "team_name": r.get("team_name"),
                "lap": int(lap) if lap and lap > 0 else None,
                "class_name": r.get("class_name") if multi else "",
                "brand": r.get("brand"), "brand_logo": r.get("brand_logo"),
                "country": r.get("country"), "proam": r.get("proam"),
                "session_type": session_type,
            }
        return {"seq": self._fl_seq, "event": self._fl_event}

    def _quali_memory_for(self, ir) -> dict:
        """The qualifying-time memory for THIS session, cleared whenever
        (SessionUniqueID, SessionNum) changes — i.e. when the quali session
        is over and the next one starts."""
        key = (ir["SessionUniqueID"], ir["SessionNum"])
        if key != self._quali_key:
            self._quali_key = key
            self._quali_mem = {}
        return self._quali_mem

    @staticmethod
    def _driver_key(drv: dict):
        """Stable identity for a driver across a leave / rejoin: customer
        ID, falling back to the name (AI / offline sessions)."""
        uid = drv.get("user_id")
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            uid = None
        if uid and uid > 0:
            return ("uid", uid)
        return ("name", drv.get("name") or "")

    def _build_timed_standings(self, drivers, ir, sess=None,
                               memory: dict | None = None) -> list:
        """
        Qualifying / practice standings.

        Ranking source: the OFFICIAL results block for the current session
        (SessionInfo -> Sessions[cur] -> ResultsPositions), which carries
        Position + FastestTime and matches the in-sim F3 board and the
        Race Results overlay (iracing_results.py). This is authoritative:
        it only counts VALID laps, so a lap iRacing later invalidates
        (off-track, tow, incomplete) does not pollute the order.

        Telemetry CarIdxBestLapTime is used ONLY as a fallback for a driver
        who has no results row yet (just set their first lap; the YAML block
        updates a second or two behind telemetry). Ranking straight off
        CarIdxBestLapTime was the old behaviour and produced positions/times
        that disagreed with F3 during practice — that was the bug.
        """
        best_lap = ir["CarIdxBestLapTime"] or []
        last_lap = ir["CarIdxLastLapTime"] or []
        on_pit   = ir["CarIdxOnPitRoad"] or []
        in_world = ir["CarIdxTrackSurface"] or []

        # Official best time per CarIdx from the current session's results.
        results_best: dict = {}
        if sess:
            for r in sess.get("ResultsPositions") or []:
                try:
                    cidx = int(r.get("CarIdx"))
                except (TypeError, ValueError):
                    continue
                ft = r.get("FastestTime")
                try:
                    ft = float(ft)
                except (TypeError, ValueError):
                    ft = 0.0
                # iRacing uses 0 / negative for "no valid lap".
                results_best[cidx] = ft if ft > 0 else 0.0

        def best_time_for(cidx):
            """Official time if present; else telemetry; else 0 (no time)."""
            bt = results_best.get(cidx, 0.0)
            if bt > 0:
                return bt
            tel = best_lap[cidx] if cidx < len(best_lap) else 0.0
            return tel if tel and tel > 0 else 0.0

        entries = []
        for cidx, drv in drivers.items():
            entries.append((best_time_for(cidx), cidx, drv))

        # Qualifying: remember each driver's best OFFICIAL time and keep it
        # for the rest of the session, also after he leaves. Only official
        # ResultsPositions times are stored — a telemetry-only time can
        # still be invalidated, and must not live on in the memory.
        departed = []
        if memory is not None:
            present = set()
            for i, (bt, cidx, drv) in enumerate(entries):
                key = self._driver_key(drv)
                present.add(key)
                official = results_best.get(cidx, 0.0)
                mem = memory.get(key)
                if official > 0:
                    memory[key] = {"drv": dict(drv), "best": official}
                elif mem:
                    # Still here (or rejoined on a new CarIdx) but the sim no
                    # longer reports his time: the remembered one stands,
                    # unless he has just gone quicker.
                    keep = mem["best"] if not bt > 0 else min(bt, mem["best"])
                    entries[i] = (keep, cidx, drv)
            for key, mem in memory.items():
                if key not in present:
                    departed.append((mem["best"], -1, {**mem["drv"], "car_idx": -1}))
        entries.extend(departed)

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
            gone = cidx < 0   # left the session; time kept from the memory
            rows.append({
                **drv,
                "position":    pos,
                "interval":    (bt - leader_time) if pos > 1 else None,
                "best_lap":    bt,
                "last_lap":    0.0 if gone else (last_lap[cidx] if cidx < len(last_lap) else 0.0),
                "on_pit":      False if gone else (bool(on_pit[cidx]) if cidx < len(on_pit) else False),
                "in_world":    False if gone else ((in_world[cidx] != -1) if cidx < len(in_world) else True),
                "left":        gone,
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
            rows = self._build_race_standings(drivers, ir, sess)
        else:
            memory = (self._quali_memory_for(ir)
                      if session_type == "Qualifying" else None)
            rows = self._build_timed_standings(drivers, ir, sess, memory)

        # WCT GT3 Pro/Am bar (red PRO / green AM in front of the name).
        # Only switched on when the field is a WCT field — see cls_proam.py.
        proam_on = proam.active_for(d.get("user_id") for d in drivers.values())
        for r in rows:
            r["proam"] = proam.lookup(r.get("user_id")) if proam_on else None

        # Flag, camera focus, session-best / personal-best marks and the
        # per-class driver count — all for the tower design.
        focus = ir["CamCarIdx"]
        class_count: dict = {}
        best_by_class: dict = {}
        for r in rows:
            cid = r.get("class_id", 0)
            class_count[cid] = class_count.get(cid, 0) + 1
            bl = r.get("best_lap") or 0
            if bl > 0 and (cid not in best_by_class or bl < best_by_class[cid]):
                best_by_class[cid] = bl
        for r in rows:
            cid = r.get("class_id", 0)
            code = country_flags.code_from_name(r.get("flair"))
            src = "flair" if code else None
            if not code:
                code = country_flags.code_from_iso(proam.country(r.get("user_id")))
                src = "cls" if code else None
            r["country"], r["country_src"] = code, src
            r["focus"] = (focus is not None and r.get("car_idx") == focus)
            r["class_count"] = class_count.get(cid, 0)
            bl, ll = r.get("best_lap") or 0, r.get("last_lap") or 0
            r["session_best"] = bl > 0 and abs(bl - best_by_class.get(cid, -1)) < 1e-4
            r["last_is_pb"] = bl > 0 and ll > 0 and abs(bl - ll) < 1e-4

        fastest_lap = self._track_fastest(ir, rows, session_type)

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
            "proam_active": proam_on,
            # Team event (IEC, NEC, …): each car is a TEAM — the overlays show
            # the team name instead of whoever is driving (2026-10-10).
            "team_event":   int(weekend.get("TeamRacing") or 0) == 1,
            "fastest_lap":  fastest_lap,
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
proam = ProAmRoster()


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
        /* Qualifying / practice: pos | brand | # | driver | lap time */
        grid-template-columns: 72px 48px 84px 1fr 220px;
        align-items: center;
        padding: 6px 18px;
        border-bottom: 1px solid #1d1d27;
        font-variant-numeric: tabular-nums;
        transition: background .15s;
    }
    /* Race sessions add a sixth column for positions gained / lost, so
       INTERVAL and +/- are both permanently visible side by side. */
    .standings.race .row {
        grid-template-columns: 72px 48px 84px 1fr 200px 128px;
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
    /* WCT GT3 Pro/Am marker in front of the name: red PRO, green AM. */
    .driver .pa {
        display: inline-block; width: 7px; height: 0.8em;
        border-radius: 2px; margin-right: 12px; vertical-align: -0.04em;
    }
    .driver .pa.pro { background: #e63946; }
    .driver .pa.am  { background: #2ecc71; }
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

    /* Positions gained / lost column (race sessions only, always visible).
       Green = places gained vs the grid, red = places lost, grey "=" =
       unchanged. Empty when no grid baseline has been captured yet. */
    .delta {
        text-align: right;
        font-size: 26px; font-weight: 800;
        line-height: 1.1;
        letter-spacing: .5px;
    }
    .delta.up   { color: #4ade80; }
    .delta.down { color: #ff5470; }
    .delta.none { color: #7a7a90; font-weight: 700; }

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

    // Qualifying / practice show a single LAP TIME column. Everything else
    // (race, or an unrecognised session type) is treated as a race and gets
    // both data columns: INTERVAL and +/- (positions gained/lost), always
    // visible side by side — no view cycling.
    const isTimed = (st === 'Qualifying' || st === 'Practice');
    const isRace  = !isTimed;

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
        // In RACE mode the first data column is the gap to the car ahead.
        // In QUALIFYING / PRACTICE we show each driver's best lap time
        // instead, since "gap" is less meaningful there than the actual
        // pace each driver has put in.
        let interval = '';
        let intervalCls = 'interval';
        if (isTimed) {
            if (r.best_lap && r.best_lap > 0) {
                interval = fmtLap(r.best_lap);
            } else {
                interval = '<span class="out-flag">no time</span>';
            }
        } else {
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

        // Second data column (race only): NET positions gained / lost vs
        // the starting grid, within class. This is not a running tally —
        // lose a place and take it back and you are on = again.
        //   pos_delta > 0  -> ▲ n  (green)
        //   pos_delta < 0  -> ▼ n  (red)
        //   pos_delta == 0 -> =    (grey, back on / never left his grid slot)
        //   pos_delta null -> blank (no grid slot known for this car:
        //                     late joiner, or we attached mid-race with no
        //                     qualifying results to fall back on)
        let deltaHtml = '';
        if (isRace) {
            const pd = r.pos_delta;
            let dTxt = '', dCls = 'none';
            if (pd == null) {
                dTxt = '';
            } else if (pd === 0) {
                dTxt = '=';
            } else if (pd > 0) {
                dTxt = '▲ ' + pd;
                dCls = 'up';
            } else {
                dTxt = '▼ ' + Math.abs(pd);
                dCls = 'down';
            }
            const dTitle = r.grid_pos ? `started P${r.grid_pos} in class` : '';
            deltaHtml = `<div class="delta ${dCls}" title="${dTitle}">${dTxt}</div>`;
        }

        const pit = r.on_pit ? ' <span class="pit-flag">PIT</span>' : '';
        // 'left' = quit the qualifying session; his time is kept until the session ends.
        const outFlag = (!r.in_world && !r.on_pit) ? ` <span class="out-flag">${r.left ? 'left' : 'out'}</span>` : '';

        const name = r.name || 'Unknown';
        const paBar = r.proam === 'PRO' ? '<span class="pa pro" title="PRO"></span>'
                    : r.proam === 'AM'  ? '<span class="pa am" title="AM"></span>' : '';
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
                <div class="driver">${paBar}${team_esc(abbrevName(name))}${pit}${outFlag}${team}</div>
                <div class="${intervalCls}">${interval}</div>
                ${deltaHtml}
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
            <div class="standings${isRace ? ' race' : ''}">
                <div class="row head">
                    <div>POS</div>
                    <div></div>
                    <div>#</div>
                    <div>DRIVER</div>
                    <div style="text-align:right;">${isTimed ? 'LAP TIME' : 'INTERVAL'}</div>
                    ${isRace ? '<div style="text-align:right;">+/-</div>' : ''}
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


# Compact broadcast tower — the default since 2026-10-08. Served as a plain
# string (no Jinja), so the JS template literals need no escaping.
TOWER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Standings Tower</title>
<style>
    /* Compact broadcast tower (2026-10-08) — modelled on the commercial
       tower Andreas uses: slim info bar, class tab with driver count,
       dense rows with position box, Pro/Am bar, name, flag, brand, times.
       Native size ~440 px wide; scale the whole thing with ?zoom=1.5.
       The previous big-panel design is still at /?style=classic. */
    :root {
        --bar:      rgba(58, 58, 62, 0.92);
        --head:     rgba(84, 84, 90, 0.92);
        --row:      rgba(52, 52, 57, 0.86);
        --row-alt:  rgba(62, 62, 68, 0.86);
        --line:     rgba(0, 0, 0, 0.35);
        --posbox:   rgba(28, 28, 32, 0.95);
        --text:     #f2f2f4;
        --muted:    #b9b9c2;
        --accent:   #2f7ff0;   /* class tab, focused position box */
        --focus:    #ff9a3c;   /* on-camera driver's name / time */
        --sb:       #d86bff;   /* session best */
        --pb:       #45f063;   /* personal best */
        --pro:      #e63946;
        --am:       #2ecc71;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    /* never a scrollbar on stream (48-car NEC field, 2026-10-10): whatever
       does not fit the OBS source is cut off; ?rows=N shortens the list */
    html, body { background: rgba(0,0,0,0); overflow: hidden; }
    body {
        font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
        color: var(--text);
        padding: 8px;
        font-variant-numeric: tabular-nums;
    }
    body.debug-mode { background: #23262b; }
    .tower { width: 440px; }

    /* --- info bar ------------------------------------------------------ */
    .info {
        display: flex; align-items: center; gap: 18px;
        height: 26px; padding: 0 9px;
        background: var(--bar);
        font-size: 15px; font-weight: 600; white-space: nowrap;
    }
    .info .sess { font-weight: 800; letter-spacing: .4px; margin-right: 6px; }
    .info .it { display: inline-flex; align-items: center; gap: 6px; }
    .info svg { width: 17px; height: 17px; flex-shrink: 0; }

    /* --- class tab ----------------------------------------------------- */
    .tabs { display: flex; align-items: flex-end; margin-top: 12px; height: 22px; }
    .tab {
        height: 22px; display: inline-flex; align-items: center; gap: 5px;
        padding: 0 8px; font-size: 14px; font-weight: 800;
        border-radius: 3px 3px 0 0;
    }
    .tab.cls { background: var(--tabc, var(--accent)); color: var(--tabt, #fff); }
    .tab.cnt { background: #e9e9ec; color: #1d1d22; margin-left: 2px; }
    .tab.cnt svg { width: 15px; height: 15px; }
    .class-gap { height: 10px; }

    /* --- table ------------------------------------------------------------ */
    .grid {
        display: grid; align-items: center;
        grid-template-columns: 28px 14px minmax(0, 1fr) 26px 28px 82px 82px;
        column-gap: 0;
    }
    .race .grid { grid-template-columns: 28px 14px minmax(0, 1fr) 26px 28px 76px 76px 40px; }
    /* Team events: no flag column (it would be the CURRENT driver's country,
       meaningless for a team) — the team name gets the room instead. */
    .teams .grid { grid-template-columns: 28px 14px minmax(0, 1fr) 28px 82px 82px; }
    .teams.race .grid { grid-template-columns: 28px 14px minmax(0, 1fr) 28px 76px 76px 40px; }
    .head {
        height: 24px; background: var(--head);
        font-size: 14px; font-weight: 500; color: var(--text);
    }
    .head .c-name { padding-left: 4px; }
    .head .r { text-align: right; padding-right: 7px; }
    .row {
        height: 24.5px; background: var(--row);
        border-top: 1px solid var(--line);
        font-size: 15px; font-weight: 600;
    }
    /* child 1 of each class block is the header, so odd = every 2nd driver */
    .row:nth-child(odd) { background: var(--row-alt); }
    .pos {
        height: 100%; display: flex; align-items: center; justify-content: center;
        background: var(--posbox); font-size: 14px; font-weight: 700;
    }
    .row.focus .pos { background: var(--accent); }
    .row.gapb { margin-top: 6px; border-top: 0; }
    .pa { width: 5px; height: 15px; border-radius: 2px; justify-self: center; }
    .pa.pro { background: var(--pro); }
    .pa.am  { background: var(--am); }
    .name {
        padding-left: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .row.focus .name, .row.focus .best { color: var(--focus); }
    .tag {
        font-size: 10px; font-weight: 800; letter-spacing: .4px;
        padding: 0 4px; border-radius: 2px; margin-left: 5px;
        vertical-align: 2px;
    }
    .tag.pit { background: #b9852a; color: #111; }
    .tag.out { background: rgba(255,255,255,0.14); color: var(--muted); }
    .flag { width: 19px; height: 13px; object-fit: cover; justify-self: center;
            box-shadow: 0 0 0 1px rgba(0,0,0,0.35); }
    .brand { width: 20px; height: 18px; object-fit: contain; justify-self: center;
             filter: drop-shadow(0 0 1px rgba(0,0,0,0.8)); }
    .t { text-align: right; padding-right: 7px; white-space: nowrap; }
    .t.sb { color: var(--sb); }
    .t.pb { color: var(--pb); }
    .t.muted { color: var(--muted); font-weight: 500; }
    .t.leader { color: #ffd166; }
    .t.laps { color: #ff8a3c; }
    .t.battle { color: #ffd84d; }
    .d { text-align: center; font-size: 13px; font-weight: 800; }
    .d.up { color: var(--pb); }
    .d.down { color: #ff5a6a; }
    .d.same { color: var(--muted); }

    .msg { padding: 14px 10px; background: var(--row); color: var(--muted); font-size: 14px; }
</style>
</head>
<body>
<div class="tower" id="tower"><div class="msg">Waiting for iRacing…</div></div>
<script>
const qs = new URLSearchParams(location.search);
if (qs.get('debug') === '1') document.body.classList.add('debug-mode');
const zoom = parseFloat(qs.get('zoom') || '1');
if (zoom > 0 && zoom !== 1) document.body.style.zoom = zoom;
document.addEventListener('keydown', e => {
    if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug-mode');
});

const ICON = {
    timer: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M9 2h6M12 2v3"/><circle cx="12" cy="14" r="8"/><path d="M12 14l3.5-3.5"/></svg>',
    temp:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M10 4a2 2 0 0 1 4 0v10.5a4 4 0 1 1-4 0z"/><path d="M12 9v7"/><path d="M17 6h3M17 10h3"/></svg>',
    track: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M7 21L10 3M17 21L14 3M12 6v2M12 11v2M12 16v2"/></svg>',
    helmet:'<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3C6.5 3 2.5 7.2 2.5 12.4V17a2 2 0 0 0 2 2h9.2l1.6-3H21a.5.5 0 0 0 .5-.5v-2.4C21.5 7.5 17.3 3 12 3zm-1 6h9.3a8.6 8.6 0 0 1 .7 3H11z"/></svg>',
};

function esc(s) {
    return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}
function fmtClock(secs) {
    if (secs == null || secs < 0 || !isFinite(secs) || secs > 86400) return '--:--';
    secs = Math.floor(secs);
    const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
    return h ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
             : `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}
function fmtLap(t) {
    if (!t || t <= 0) return '';
    const m = Math.floor(t / 60), s = t - m * 60;
    return m ? `${m}:${s.toFixed(3).padStart(6,'0')}` : s.toFixed(3);
}
function fmtGap(g) {
    if (g == null || g <= 0) return '';
    if (g < 60) return '+' + g.toFixed(3);
    const m = Math.floor(g / 60), s = g - m * 60;
    return `+${m}:${s.toFixed(2).padStart(5,'0')}`;
}
function abbrev(full) {
    const p = String(full || '').trim().split(/\s+/);
    return p.length < 2 ? (full || '') : `${p[0][0]}. ${p.slice(1).join(' ')}`;
}
function textOn(hex) {   // dark or light text on a class colour
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return '#fff';
    const n = parseInt(m[1], 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
    return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? '#111' : '#fff';
}

function infoBar(d) {
    const st = (d.session_type || 'Session').toUpperCase();
    let clock;
    if (d.laps_remain != null && d.laps_remain >= 0 && d.laps_remain < 9000
            && !(d.remaining > 0 && d.remaining < 86400)) {
        clock = `${d.laps_remain} laps`;
    } else {
        clock = fmtClock(d.remaining);
    }
    const temp = d.air_temp != null ? `${d.air_temp.toFixed(1)}°C` : '—';
    const wx = d.weather?.label || '—';
    return `<div class="info">
        <span class="sess">${esc(st)}</span>
        <span class="it">${ICON.timer}${clock}</span>
        <span class="it">${ICON.temp}${temp}</span>
        <span class="it">${ICON.track}${esc(wx[0].toUpperCase() + wx.slice(1).toLowerCase())}</span>
    </div>`;
}

// Team events show the TEAM (?names=driver keeps the driver).
let TEAM_MODE = false;
function label(r) {
    return (TEAM_MODE && r.team_name) ? r.team_name : abbrev(r.name || 'Unknown');
}

function headRow(isRace) {
    return `<div class="grid head">
        <div></div><div></div><div class="c-name">${TEAM_MODE ? 'team' : 'driver name'}</div>${TEAM_MODE ? '' : '<div></div>'}<div></div>
        ${isRace
            ? '<div class="r">interval</div><div class="r">last</div><div class="r" style="text-align:center;padding:0">+/-</div>'
            : '<div class="r">fastest</div><div class="r">last</div>'}
    </div>`;
}

function rowHtml(r, isRace) {
    const pos = r.class_position || r.position;
    const pa = r.proam === 'PRO' ? '<div class="pa pro"></div>'
             : r.proam === 'AM'  ? '<div class="pa am"></div>' : '<div></div>';
    let tags = '';
    if (r.on_pit) tags += '<span class="tag pit">PIT</span>';
    else if (!r.in_world) tags += `<span class="tag out">${r.left ? 'LEFT' : 'OUT'}</span>`;
    const flag = TEAM_MODE ? ''
        : r.country ? `<img class="flag" src="/flag/${encodeURIComponent(r.country)}.svg" alt="">` : '<div></div>';
    const brand = (r.brand && r.brand_logo) ? `<img class="brand" src="/brand/${encodeURIComponent(r.brand)}" alt="">` : '<div></div>';
    const lastCls = r.last_is_pb ? 'pb' : '';
    const last = `<div class="t ${lastCls}">${fmtLap(r.last_lap)}</div>`;
    let cells;
    if (isRace) {
        let iv = '', ivCls = '';
        if (r.position === 1) { iv = 'LEADER'; ivCls = 'leader'; }
        else if (r.laps_behind > 0) { iv = `+${r.laps_behind} LAP${r.laps_behind > 1 ? 'S' : ''}`; ivCls = 'laps'; }
        else if (r.interval != null) {
            iv = fmtGap(r.interval);
            if (r.interval > 0 && r.interval < 1.0 && (r.lap || 0) >= 2) ivCls = 'battle';
        }
        const pd = r.pos_delta;
        const dHtml = pd == null ? '<div class="d"></div>'
            : pd > 0 ? `<div class="d up">▲${pd}</div>`
            : pd < 0 ? `<div class="d down">▼${-pd}</div>`
            : '<div class="d same">=</div>';
        cells = `<div class="t ${ivCls}">${iv}</div>${last}${dHtml}`;
    } else {
        const best = r.best_lap > 0
            ? `<div class="t best ${r.session_best ? 'sb' : ''}">${fmtLap(r.best_lap)}</div>`
            : '<div class="t muted">no time</div>';
        cells = best + last;
    }
    return `<div class="grid row${r.focus ? ' focus' : ''}${r._gapBefore ? ' gapb' : ''}">
        <div class="pos">${pos}</div>${pa}
        <div class="name">${esc(label(r))}${tags}</div>
        ${flag}${brand}${cells}
    </div>`;
}

function render(d) {
    TEAM_MODE = !!(d && d.team_event) && qs.get('names') !== 'driver';
    const el = document.getElementById('tower');
    if (!d || !d.connected) {
        el.innerHTML = '<div class="msg">Waiting for iRacing…</div>';
        return;
    }
    const isRace = d.session_type === 'Race';
    let rows = d.standings || [];
    // ?rows=N: top N positions only. The car on camera is kept visible — if it
    // is further back it takes the last slot (after a thin gap).
    const ROWS = parseInt(qs.get('rows') || '0', 10);
    if (ROWS > 0 && rows.length > ROWS) {
        const top = rows.slice(0, ROWS);
        const f = rows.find(r => r.focus);
        if (f && !top.includes(f)) { top[ROWS - 1] = { ...f, _gapBefore: true }; }
        rows = top;
    }
    const multi = new Set(rows.map(r => r.class_id)).size > 1;
    let html = infoBar(d);
    let cur = null, open = false;
    for (const r of rows) {
        if (r.class_id !== cur) {
            if (open) html += '</div><div class="class-gap"></div>';
            cur = r.class_id;
            const cc = multi ? (r.class_color || '') : '';
            const style = cc ? ` style="--tabc:${cc};--tabt:${textOn(cc)}"` : '';
            html += `<div class="tabs">
                <span class="tab cls"${style}>${esc(r.class_name || 'Class')}</span>
                <span class="tab cnt">${ICON.helmet}${r.class_count || ''}</span>
            </div><div class="${isRace ? 'race' : ''}${TEAM_MODE ? ' teams' : ''}">${headRow(isRace)}`;
            open = true;
        }
        html += rowHtml(r, isRace);
    }
    if (open) html += '</div>';
    if (!rows.length) html += '<div class="msg">No drivers yet.</div>';
    el.innerHTML = html;
}

async function poll() {
    try {
        const r = await fetch('/standings', { cache: 'no-store' });
        render(await r.json());
    } catch (e) { /* keep last view */ }
    setTimeout(poll, 1000);
}
poll();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    # ?style=classic keeps the previous big-panel design available.
    if request.args.get("style") == "classic":
        return render_template_string(STANDINGS_HTML)
    return Response(TOWER_HTML, mimetype="text/html")


# -----------------------------------------------------------------------------
# Extra broadcast pages on the same server (2026-10-08) — all read /standings,
# so they agree with the tower to the number. Each takes ?zoom=, ?debug=1
# and ?demo=1 (fake data, for styling in OBS without iRacing).
#   /fastest — fastest-lap banner (race only; ?all=1 also quali/practice)
#   /movers  — biggest movers vs the starting grid (race only; ?n=3)
#   /gapbar  — every car as a dot by gap to its class leader (race only)
# -----------------------------------------------------------------------------
FASTEST_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Fastest Lap</title>
<style>
    :root { --bg: rgba(52,52,57,0.92); --bg2: rgba(28,28,32,0.95); --head: rgba(84,84,90,0.92);
            --text: #f2f2f4; --muted: #b9b9c2; --accent: #2f7ff0; --sb: #d86bff;
            --up: #45f063; --down: #ff5a6a; --gold: #ffd166; --pro: #e63946; --am: #2ecc71; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { background: rgba(0,0,0,0); }
    body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; color: var(--text);
           padding: 8px; font-variant-numeric: tabular-nums; }
    body.debug-mode { background: #23262b; }
    .flag { width: 19px; height: 13px; object-fit: cover; box-shadow: 0 0 0 1px rgba(0,0,0,0.35); }
    .brand { width: 20px; height: 18px; object-fit: contain; filter: drop-shadow(0 0 1px rgba(0,0,0,0.8)); }
    .pa { display: inline-block; width: 5px; height: 15px; border-radius: 2px; }
    .pa.pro { background: var(--pro); } .pa.am { background: var(--am); }

    /* Fastest-lap banner: pops for SHOW_S seconds whenever the session's
       (class) fastest lap improves. Race only unless ?all=1. */
    .banner { display: inline-flex; align-items: stretch; height: 44px;
              opacity: 0; transform: translateX(-30px);
              transition: opacity .3s ease, transform .4s cubic-bezier(.2,.9,.3,1.2); }
    .banner.show { opacity: 1; transform: none; }
    .tag { background: var(--sb); color: #fff; font-weight: 800; letter-spacing: 1.5px;
           font-size: 14px; display: flex; align-items: center; padding: 0 12px; }
    .tag svg { width: 18px; height: 18px; margin-right: 7px; }
    .who { background: var(--bg); display: flex; align-items: center; gap: 9px; padding: 0 12px; }
    .num { background: var(--bg2); padding: 1px 7px; border-radius: 3px; font-weight: 700; font-size: 14px; }
    .nm { font-size: 19px; font-weight: 700; white-space: nowrap; }
    .cls { font-size: 12px; color: var(--muted); font-weight: 700; letter-spacing: 1px; }
    .time { background: var(--bg2); display: flex; flex-direction: column; justify-content: center;
            align-items: flex-end; padding: 0 12px; min-width: 120px; }
    .t { font-size: 20px; font-weight: 800; color: var(--sb); line-height: 1.05; }
    .d { font-size: 12px; color: var(--muted); font-weight: 600; white-space: nowrap; }
</style></head>
<body>
<div class="banner" id="b">
    <div class="tag"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M9 2h6M12 2v3"/><circle cx="12" cy="14" r="8"/><path d="M12 14l3.5-3.5"/></svg>FASTEST LAP</div>
    <div class="who" id="who"></div>
    <div class="time"><div class="t" id="t"></div><div class="d" id="d"></div></div>
</div>
<script>
const qs = new URLSearchParams(location.search);
if (qs.get('debug') === '1') document.body.classList.add('debug-mode');
const zoom = parseFloat(qs.get('zoom') || '1');
if (zoom > 0 && zoom !== 1) document.body.style.zoom = zoom;
document.addEventListener('keydown', e => {
    if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug-mode');
});
const DEMO = qs.has('demo');
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function abbrev(full) {
    const p = String(full || '').trim().split(/\s+/);
    return p.length < 2 ? (full || '') : `${p[0][0]}. ${p.slice(1).join(' ')}`;
}
function fmtLap(t) {
    if (!t || t <= 0) return '';
    const m = Math.floor(t / 60), s = t - m * 60;
    return m ? `${m}:${s.toFixed(3).padStart(6,'0')}` : s.toFixed(3);
}
function flagImg(c) { return c ? `<img class="flag" src="/flag/${encodeURIComponent(c)}.svg" alt="">` : ''; }
function brandImg(r) { return (r.brand && r.brand_logo) ? `<img class="brand" src="/brand/${encodeURIComponent(r.brand)}" alt="">` : ''; }
function paBar(p) { return p === 'PRO' ? '<span class="pa pro"></span>' : p === 'AM' ? '<span class="pa am"></span>' : ''; }
async function getStandings() {
    try { const r = await fetch('/standings', { cache: 'no-store' }); return await r.json(); }
    catch (e) { return null; }
}

const SHOW_S = parseFloat(qs.get('secs') || '8');
const ALL = qs.get('all') === '1';
const b = document.getElementById('b');
let lastSeq = null, hideT = null;
function show(e) {
    document.getElementById('who').innerHTML =
        `${paBar(e.proam)}<span class="num">#${esc(e.car_number)}</span>
         <span class="nm">${esc(e.team_mode && e.team_name ? e.team_name : abbrev(e.name))}</span>${e.team_mode && e.team_name ? `<span class="cls">${esc(abbrev(e.name))}</span>` : ''}${flagImg(e.country)}${brandImg(e)}
         ${e.class_name ? `<span class="cls">${esc(e.class_name)}</span>` : ''}`;
    document.getElementById('t').textContent = fmtLap(e.time);
    const bits = [];
    if (e.lap) bits.push(`Lap ${e.lap}`);
    if (e.delta != null) bits.push(`${e.delta.toFixed(3)} s`);
    document.getElementById('d').textContent = bits.join(' · ');
    b.classList.remove('show'); void b.offsetWidth; b.classList.add('show');
    clearTimeout(hideT); hideT = setTimeout(() => b.classList.remove('show'), SHOW_S * 1000);
}
async function tick() {
    if (DEMO) return;
    const d = await getStandings();
    const fl = d && d.fastest_lap;
    if (!fl) return;
    if (lastSeq === null) { lastSeq = fl.seq; return; }   // never replay an old lap on load
    if (fl.seq !== lastSeq && fl.event) {
        lastSeq = fl.seq;
        fl.event.team_mode = !!d.team_event && qs.get('names') !== 'driver';
        if (ALL || fl.event.session_type === 'Race') show(fl.event);
    }
}
if (DEMO) {
    const demo = () => show({ name: 'Maurice Becker', car_number: '49', time: 91.310, delta: -0.214, lap: 12,
        country: 'de', brand: 'porsche', brand_logo: true, proam: 'PRO', class_name: '' });
    demo(); setInterval(demo, 11000);
}
setInterval(tick, 1000); tick();
</script></body></html>
"""

MOVERS_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Biggest Movers</title>
<style>
    :root { --bg: rgba(52,52,57,0.92); --bg2: rgba(28,28,32,0.95); --head: rgba(84,84,90,0.92);
            --text: #f2f2f4; --muted: #b9b9c2; --accent: #2f7ff0; --sb: #d86bff;
            --up: #45f063; --down: #ff5a6a; --gold: #ffd166; --pro: #e63946; --am: #2ecc71; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { background: rgba(0,0,0,0); }
    body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; color: var(--text);
           padding: 8px; font-variant-numeric: tabular-nums; }
    body.debug-mode { background: #23262b; }
    .flag { width: 19px; height: 13px; object-fit: cover; box-shadow: 0 0 0 1px rgba(0,0,0,0.35); }
    .brand { width: 20px; height: 18px; object-fit: contain; filter: drop-shadow(0 0 1px rgba(0,0,0,0.8)); }
    .pa { display: inline-block; width: 5px; height: 15px; border-radius: 2px; }
    .pa.pro { background: var(--pro); } .pa.am { background: var(--am); }

    /* Biggest movers: top N places gained / lost vs the starting grid
       (the tower's +/-), race sessions only. ?n=3 (default). */
    .card { width: 520px; display: none; }
    .card.on { display: block; }
    .top { height: 26px; background: var(--accent); display: flex; align-items: center;
           padding: 0 10px; font-size: 14px; font-weight: 800; letter-spacing: 2px; }
    .top .sub { margin-left: auto; font-size: 13px; font-weight: 600; letter-spacing: .5px; opacity: .9; }
    .cols { display: grid; grid-template-columns: 1fr 1fr; background: var(--bg); }
    .col + .col { border-left: 1px solid rgba(0,0,0,0.35); }
    .h { height: 22px; display: flex; align-items: center; padding: 0 9px; font-size: 13px;
         font-weight: 700; background: var(--head); }
    .h.up { color: var(--up); } .h.down { color: var(--down); }
    .r { height: 26px; display: grid; grid-template-columns: 38px 8px minmax(0,1fr) 24px 58px;
         align-items: center; border-top: 1px solid rgba(0,0,0,0.35); font-size: 15px; font-weight: 600; }
    .dv { text-align: center; font-weight: 800; font-size: 14px; }
    .dv.up { color: var(--up); } .dv.down { color: var(--down); }
    .n { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding-left: 5px; }
    .pp { text-align: right; padding-right: 8px; color: var(--muted); font-size: 13px; white-space: nowrap; }
    .empty { padding: 6px 9px; color: var(--muted); font-size: 13px; border-top: 1px solid rgba(0,0,0,0.35); }
</style></head>
<body>
<div class="card" id="card">
    <div class="top"><span>BIGGEST MOVERS</span><span class="sub" id="sub"></span></div>
    <div class="cols"><div class="col" id="up"></div><div class="col" id="down"></div></div>
</div>
<script>
const qs = new URLSearchParams(location.search);
if (qs.get('debug') === '1') document.body.classList.add('debug-mode');
const zoom = parseFloat(qs.get('zoom') || '1');
if (zoom > 0 && zoom !== 1) document.body.style.zoom = zoom;
document.addEventListener('keydown', e => {
    if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug-mode');
});
const DEMO = qs.has('demo');
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function abbrev(full) {
    const p = String(full || '').trim().split(/\s+/);
    return p.length < 2 ? (full || '') : `${p[0][0]}. ${p.slice(1).join(' ')}`;
}
function fmtLap(t) {
    if (!t || t <= 0) return '';
    const m = Math.floor(t / 60), s = t - m * 60;
    return m ? `${m}:${s.toFixed(3).padStart(6,'0')}` : s.toFixed(3);
}
function flagImg(c) { return c ? `<img class="flag" src="/flag/${encodeURIComponent(c)}.svg" alt="">` : ''; }
function brandImg(r) { return (r.brand && r.brand_logo) ? `<img class="brand" src="/brand/${encodeURIComponent(r.brand)}" alt="">` : ''; }
function paBar(p) { return p === 'PRO' ? '<span class="pa pro"></span>' : p === 'AM' ? '<span class="pa am"></span>' : ''; }
async function getStandings() {
    try { const r = await fetch('/standings', { cache: 'no-store' }); return await r.json(); }
    catch (e) { return null; }
}

const N = parseInt(qs.get('n') || '3', 10);
function line(r, up) {
    const pd = r.pos_delta;
    return `<div class="r"><div class="dv ${up ? 'up' : 'down'}">${up ? '▲' : '▼'}${Math.abs(pd)}</div>
        ${paBar(r.proam) || '<span></span>'}<div class="n">${esc(r._label || abbrev(r.name))}</div>
        <div>${flagImg(r.country)}</div>
        <div class="pp">P${r.grid_pos}→P${r.class_position || r.position}</div></div>`;
}
function render(d) {
    const card = document.getElementById('card');
    if (!d || !d.connected || d.session_type !== 'Race') { card.classList.remove('on'); return; }
    const teamMode = !!d.team_event && qs.get('names') !== 'driver';
    const rows = (d.standings || []).filter(r => r.pos_delta != null && r.in_world)
        .map(r => ({ ...r, _label: teamMode && r.team_name ? r.team_name : abbrev(r.name) }));
    const gain = rows.filter(r => r.pos_delta > 0).sort((a, b) => b.pos_delta - a.pos_delta).slice(0, N);
    const lose = rows.filter(r => r.pos_delta < 0).sort((a, b) => a.pos_delta - b.pos_delta).slice(0, N);
    if (!rows.length) { card.classList.remove('on'); return; }
    card.classList.add('on');
    document.getElementById('up').innerHTML = '<div class="h up">▲ GAINED</div>' +
        (gain.length ? gain.map(r => line(r, true)).join('') : '<div class="empty">—</div>');
    document.getElementById('down').innerHTML = '<div class="h down">▼ LOST</div>' +
        (lose.length ? lose.map(r => line(r, false)).join('') : '<div class="empty">—</div>');
    const lead = (d.standings || []).find(r => r.position === 1);
    document.getElementById('sub').textContent = lead && lead.lap ? `vs starting grid · lap ${lead.lap}` : 'vs starting grid';
}
async function tick() {
    if (DEMO) {
        const mk = (name, g, now, cc) => ({ name, grid_pos: g, class_position: now, pos_delta: g - now, in_world: true, country: cc, proam: g % 2 ? 'PRO' : 'AM' });
        render({ connected: true, session_type: 'Race', standings: [
            mk('Leon Klein', 14, 6, 'de'), mk('Michael Gessner', 19, 13, 'de'), mk('Alex Turek', 21, 16, 'de'),
            mk('Benjamin Warnow', 8, 17, 'ch'), mk('Dennis Richter', 2, 9, 'de'), mk('Florian Roessler', 10, 15, 'de'),
            { name: 'Maurice Becker', grid_pos: 1, class_position: 1, pos_delta: 0, in_world: true, position: 1, lap: 14 }] });
        return;
    }
    render(await getStandings());
}
setInterval(tick, 1000); tick();
</script></body></html>
"""

GAPBAR_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Gap Bar</title>
<style>
    :root { --bg: rgba(52,52,57,0.92); --bg2: rgba(28,28,32,0.95); --head: rgba(84,84,90,0.92);
            --text: #f2f2f4; --muted: #b9b9c2; --accent: #2f7ff0; --sb: #d86bff;
            --up: #45f063; --down: #ff5a6a; --gold: #ffd166; --pro: #e63946; --am: #2ecc71; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { background: rgba(0,0,0,0); }
    body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; color: var(--text);
           padding: 8px; font-variant-numeric: tabular-nums; }
    body.debug-mode { background: #23262b; }
    .flag { width: 19px; height: 13px; object-fit: cover; box-shadow: 0 0 0 1px rgba(0,0,0,0.35); }
    .brand { width: 20px; height: 18px; object-fit: contain; filter: drop-shadow(0 0 1px rgba(0,0,0,0.8)); }
    .pa { display: inline-block; width: 5px; height: 15px; border-radius: 2px; }
    .pa.pro { background: var(--pro); } .pa.am { background: var(--am); }

    /* Gap bar: every car as a dot by its gap to the class leader, so trains
       and battles are visible at a glance. Race sessions only. Gaps are the
       tower's own intervals summed down the class (same numbers as the
       tower, lap-1 fallback included); lapped cars sit in a "+LAP" box at
       the end. Fills the OBS source width (no scrollbars); ?w=900 forces
       a width, ?max=60 fixes the scale (s), ?panel=1 adds a dark strip,
       ?label=1 always shows the class name (default: multiclass only). */
    html, body { overflow: hidden; }
    .wrap { display: none; } .wrap.on { display: block; }
    .cls { margin-bottom: 8px; }
    .lbl { display: inline-flex; align-items: center; gap: 8px; height: 20px; padding: 0 8px;
           background: var(--accent); font-size: 12px; font-weight: 800; letter-spacing: 1px; }
    .lbl .sc { font-weight: 600; opacity: .85; letter-spacing: .3px; }
    .bar { position: relative; height: 112px; }
    .bar.panel { background: var(--bg); }
    .tick, .dot, .flabel { text-shadow: 0 1px 2px rgba(0,0,0,0.9); }
    .dot { box-shadow: 0 1px 4px rgba(0,0,0,0.6); }
    .axis { position: absolute; left: 30px; right: 70px; top: 57px; height: 2px;
            background: rgba(255,255,255,0.45); box-shadow: 0 1px 2px rgba(0,0,0,0.6); }
    .tick { position: absolute; top: 92px; font-size: 12px; font-weight: 700; color: #e6e6ea; transform: translateX(-50%); white-space: nowrap; }
    .tick::before { content: ''; position: absolute; left: 50%; top: -6px; width: 1px; height: 5px; background: rgba(255,255,255,0.25); }
    .dot { position: absolute; width: 24px; height: 24px; margin-left: -12px; border-radius: 50%;
           background: #3b3b42; border: 2px solid #8a8a94; display: flex; align-items: center;
           justify-content: center; font-size: 11px; font-weight: 800; }
    .dot.lead { background: var(--gold); border-color: var(--gold); color: #111; }
    .dot.focus { background: var(--accent); border-color: #fff; z-index: 3; }
    .dot.pit { opacity: .45; }
    .flabel { position: absolute; top: 2px; font-size: 12px; font-weight: 700; color: #fff;
              transform: translateX(-50%); white-space: nowrap; background: var(--accent);
              padding: 0 5px; border-radius: 2px; z-index: 4; }
    .lapped { position: absolute; right: 6px; top: 8px; bottom: 8px; width: 58px; border-left: 1px dashed rgba(255,255,255,0.25);
              display: flex; flex-wrap: wrap; align-content: center; justify-content: center; gap: 2px; }
    .lapped .dot { position: static; margin: 0; width: 18px; height: 18px; font-size: 9px; }
    .lapped .t { width: 100%; text-align: center; font-size: 10px; color: var(--muted); }
</style></head>
<body>
<div class="wrap" id="wrap"></div>
<script>
const qs = new URLSearchParams(location.search);
if (qs.get('debug') === '1') document.body.classList.add('debug-mode');
const zoom = parseFloat(qs.get('zoom') || '1');
if (zoom > 0 && zoom !== 1) document.body.style.zoom = zoom;
document.addEventListener('keydown', e => {
    if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug-mode');
});
const DEMO = qs.has('demo');
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function abbrev(full) {
    const p = String(full || '').trim().split(/\s+/);
    return p.length < 2 ? (full || '') : `${p[0][0]}. ${p.slice(1).join(' ')}`;
}
function fmtLap(t) {
    if (!t || t <= 0) return '';
    const m = Math.floor(t / 60), s = t - m * 60;
    return m ? `${m}:${s.toFixed(3).padStart(6,'0')}` : s.toFixed(3);
}
function flagImg(c) { return c ? `<img class="flag" src="/flag/${encodeURIComponent(c)}.svg" alt="">` : ''; }
function brandImg(r) { return (r.brand && r.brand_logo) ? `<img class="brand" src="/brand/${encodeURIComponent(r.brand)}" alt="">` : ''; }
function paBar(p) { return p === 'PRO' ? '<span class="pa pro"></span>' : p === 'AM' ? '<span class="pa am"></span>' : ''; }
async function getStandings() {
    try { const r = await fetch('/standings', { cache: 'no-store' }); return await r.json(); }
    catch (e) { return null; }
}

const W_FIXED = parseInt(qs.get('w') || '0', 10);
const PANEL = qs.get('panel') === '1';
const LABEL = qs.get('label') === '1';
const FIXED_MAX = parseFloat(qs.get('max') || '0');
function niceMax(g) {
    for (const m of [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300]) if (g <= m) return m;
    return Math.ceil(g / 60) * 60;
}
function stepFor(m) { return m <= 10 ? 1 : m <= 30 ? 5 : m <= 60 ? 10 : m <= 120 ? 20 : 60; }
function render(d) {
    const wrap = document.getElementById('wrap');
    if (!d || !d.connected || d.session_type !== 'Race') { wrap.classList.remove('on'); return; }
    const rows = (d.standings || []).filter(r => r.in_world || r.on_pit);
    const classes = [];
    for (const r of rows) {
        let c = classes.find(x => x.id === r.class_id);
        if (!c) { c = { id: r.class_id, name: r.class_name, cars: [], lapped: [] }; classes.push(c); }
        if (r.laps_behind > 0) { c.lapped.push(r); continue; }
        const gap = c.cars.length ? c.cars[c.cars.length - 1].gap + (r.interval || 0) : 0;
        c.cars.push({ ...r, gap });
    }
    if (!classes.length) { wrap.classList.remove('on'); return; }
    wrap.classList.add('on');
    // Fill the OBS source: page width minus the body padding.
    const W = W_FIXED > 0 ? W_FIXED : Math.max(300, document.documentElement.clientWidth - 16);
    const inner = W - 100;  // axis length in px
    wrap.innerHTML = classes.map(c => {
        const maxGap = Math.max(0, ...c.cars.map(x => x.gap));
        const M = FIXED_MAX > 0 ? FIXED_MAX : niceMax(Math.max(maxGap, 5));
        const xOf = g => 30 + Math.min(g, M) / M * inner;
        let ticks = '';
        const st = stepFor(M);
        for (let t = 0; t <= M + 1e-6; t += st) ticks += `<div class="tick" style="left:${xOf(t)}px">${t ? '+' + t + 's' : ''}</div>`;
        // Three lanes (y = 34 / 58 / 82). A dot takes the first lane where it
        // doesn't touch the previous dot; when all three are busy (a tight
        // train) it is nudged just right of the freest lane, so the train
        // reads as a chain instead of a pile. Leader first, in race order.
        const LANES = [34, 58, 82], D = 26;
        const last = [-1e9, -1e9, -1e9];
        const dots = c.cars.map(r => {
            let x = xOf(r.gap);
            let lane = [1, 0, 2].find(i => x - last[i] >= D);   // middle lane first
            if (lane === undefined) {
                lane = last.indexOf(Math.min(...last));
                x = last[lane] + D;
            }
            last[lane] = x;
            const cls = ['dot'];
            if (r.position === 1 || r.class_position === 1) cls.push('lead');
            if (r.focus) cls.push('focus');
            if (r.on_pit) cls.push('pit');
            const pos = r.class_position || r.position;
            const nm = (d.team_event && qs.get('names') !== 'driver' && r.team_name) ? r.team_name : abbrev(r.name);
            const lab = r.focus ? `<div class="flabel" style="left:${x}px">${esc(nm)}</div>` : '';
            return `${lab}<div class="${cls.join(' ')}" style="left:${x}px;top:${LANES[lane] - 12}px" title="${esc(r.name)}">${pos}</div>`;
        }).join('');
        const lapped = c.lapped.length ? `<div class="lapped"><div class="t">+LAP</div>${c.lapped.map(r =>
            `<div class="dot${r.focus ? ' focus' : ''}">${r.class_position || r.position}</div>`).join('')}</div>` : '';
        return `<div class="cls" style="width:${W}px">
            ${(LABEL || classes.length > 1) ? `<div class="lbl">${esc(c.name || 'Class')}</div>` : ''}
            <div class="bar${PANEL ? ' panel' : ''}"><div class="axis"></div>${ticks}${dots}${lapped}</div></div>`;
    }).join('');
}
async function tick() {
    if (DEMO) {
        const gaps = [0, .4, .9, 1.2, 5.5, 5.8, 6.1, 6.3, 12.0, 14.5, 14.8, 21, 25, 25.4, 33, 38];
        const st = gaps.map((g, i) => ({ name: 'Driver ' + (i + 1), position: i + 1, class_position: i + 1, class_id: 1,
            class_name: 'GT3', in_world: true, interval: i ? g - gaps[i - 1] : null, laps_behind: 0, focus: i === 5, on_pit: i === 12 }));
        st.push({ name: 'Lapped Car', position: 17, class_position: 17, class_id: 1, in_world: true, laps_behind: 1 });
        st[5].name = 'Andreas Bastian';
        render({ connected: true, session_type: 'Race', standings: st });
        return;
    }
    render(await getStandings());
}
setInterval(tick, 1000); tick();
</script></body></html>
"""


@app.route("/fastest")
def fastest_page():
    return Response(FASTEST_HTML, mimetype="text/html")


@app.route("/movers")
def movers_page():
    return Response(MOVERS_HTML, mimetype="text/html")


@app.route("/gapbar")
def gapbar_page():
    return Response(GAPBAR_HTML, mimetype="text/html")


# -----------------------------------------------------------------------------
# YouTube live picture-in-picture (2026-10-08). Lives on this server because
# YouTube refuses embeds on local-file pages ("Error 153") — it must be an
# http://localhost page. OBS points at /youtube once; the video is chosen on
# /youtube/setup and saved in youtube_config.json (gitignored).
# -----------------------------------------------------------------------------
import json as _json
import re as _re
from pathlib import Path as _Path

YOUTUBE_CFG = _Path(__file__).resolve().parent / "youtube_config.json"
_YT_ID = _re.compile(r"(?:v=|youtu\.be/|/live/|/embed/|/shorts/)([\w-]{11})")


def _youtube_id(s: str) -> str:
    s = (s or "").strip()
    if _re.fullmatch(r"[\w-]{11}", s):
        return s
    m = _YT_ID.search(s)
    return m.group(1) if m else ""


def _youtube_cfg() -> dict:
    try:
        return {"video": "", "mute": True, **_json.loads(YOUTUBE_CFG.read_text(encoding="utf-8"))}
    except Exception:
        return {"video": "", "mute": True}


YOUTUBE_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>YouTube Live</title>
<style>
    /* YouTube live picture-in-picture (2026-10-08). Must be served from
       http://localhost — YouTube refuses embeds on local-file pages
       ("Error 153"). Which video: /youtube/setup (saved on the server), or
       ?v=<id or link> on this URL. ?mute=0|1 overrides the saved setting. */
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 100%; height: 100%; background: rgba(0,0,0,0); overflow: hidden; }
    #player, #player iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }
    #msg { position: absolute; inset: 0; display: none; align-items: center; justify-content: center;
           background: #1c1c20; color: #b9b9c2; font: 600 18px 'Segoe UI', Arial, sans-serif; text-align: center; padding: 20px; }
    #msg.on { display: flex; }
</style></head>
<body>
<div id="player"></div>
<div id="msg"></div>
<script>
const qs = new URLSearchParams(location.search);
const msg = document.getElementById('msg');
const say = t => { msg.textContent = t; msg.classList.toggle('on', !!t); };

function videoId(s) {
    s = String(s || '').trim();
    if (/^[\w-]{11}$/.test(s)) return s;
    const m = s.match(/(?:v=|youtu\.be\/|\/live\/|\/embed\/|\/shorts\/)([\w-]{11})/);
    return m ? m[1] : '';
}

let player = null, current = null, retryT = null;
function load(cfg) {
    const id = videoId(qs.get('v') || cfg.video);
    const mute = (qs.get('mute') ?? (cfg.mute ? '1' : '0')) === '1';
    const key = id + '|' + mute;
    if (key === current) return;
    current = key;
    clearTimeout(retryT);
    if (!id) { say('No YouTube video set — open /youtube/setup'); if (player) { player.destroy(); player = null; } return; }
    say('');
    if (player) { player.destroy(); player = null; }
    const div = document.createElement('div'); div.id = 'yt';
    document.getElementById('player').replaceChildren(div);
    player = new YT.Player('yt', {
        videoId: id,
        playerVars: { autoplay: 1, mute: mute ? 1 : 0, controls: 0, rel: 0, modestbranding: 1,
                      playsinline: 1, iv_load_policy: 3, origin: location.origin },
        events: {
            onReady: e => { if (mute) e.target.mute(); else e.target.unMute(); e.target.playVideo(); },
            onError: e => {
                const why = { 2: 'invalid video id', 5: 'player error', 100: 'video not found / private',
                              101: 'the channel does not allow embedding', 150: 'the channel does not allow embedding',
                              153: 'player configuration error' }[e.data] || ('error ' + e.data);
                say(`YouTube: ${why} — retrying in 60 s`);
                retryT = setTimeout(() => { current = null; load(lastCfg); }, 60000);
            },
            // A live stream that hiccups ends up "ended" or stuck buffering: nudge it.
            onStateChange: e => { if (e.data === YT.PlayerState.ENDED) setTimeout(() => player && player.playVideo(), 5000); },
        },
    });
}

let lastCfg = {};
async function poll() {
    try { lastCfg = await (await fetch('/youtube/config', { cache: 'no-store' })).json(); } catch (e) { /* keep */ }
    if (window.YT && YT.Player) load(lastCfg);
}
window.onYouTubeIframeAPIReady = () => { current = null; load(lastCfg); };
const s = document.createElement('script'); s.src = 'https://www.youtube.com/iframe_api'; document.head.appendChild(s);
poll(); setInterval(poll, 5000);
</script></body></html>
"""

YOUTUBE_SETUP_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>YouTube Live — Setup</title>
<style>
    body { font-family: 'Segoe UI', Arial, sans-serif; background: #1c1c20; color: #f2f2f4; padding: 30px; }
    .box { max-width: 640px; background: #343439; padding: 20px 22px; }
    h1 { font-size: 20px; margin-bottom: 14px; }
    input[type=text] { width: 100%; padding: 9px 10px; font-size: 15px; background: #1c1c20; color: #fff; border: 1px solid #54545a; }
    label { display: block; margin: 12px 0 6px; color: #b9b9c2; font-size: 14px; }
    button { margin-top: 16px; padding: 9px 18px; font-size: 15px; font-weight: 700; background: #2f7ff0; color: #fff; border: 0; cursor: pointer; }
    button.sec { background: #54545a; margin-left: 6px; }
    #st { margin-top: 12px; color: #45f063; min-height: 20px; }
    .hint { color: #8a8a94; font-size: 13px; margin-top: 14px; line-height: 1.5; }
    code { background: #1c1c20; padding: 1px 5px; }
</style></head>
<body><div class="box">
    <h1>YouTube live picture-in-picture</h1>
    <label>YouTube link or video ID</label>
    <input type="text" id="video" placeholder="https://www.youtube.com/live/… or https://youtu.be/…">
    <label><input type="checkbox" id="mute"> Muted (recommended — otherwise control the volume in the OBS mixer)</label>
    <button onclick="save()">Show this video</button><button class="sec" onclick="clearV()">Clear</button>
    <div id="st"></div>
    <div class="hint">OBS: Browser source → URL <code>http://localhost:5005/youtube</code>, 1280 × 720,
    tick “Control audio via OBS”. The OBS source picks up a new video here within ~5 s.</div>
</div>
<script>
async function load() {
    const c = await (await fetch('/youtube/config', { cache: 'no-store' })).json();
    document.getElementById('video').value = c.video || '';
    document.getElementById('mute').checked = !!c.mute;
}
async function post(body) {
    const r = await fetch('/youtube/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json();
    document.getElementById('st').textContent = d.ok ? (d.video ? `Showing ${d.video}` : 'Cleared') : ('Error: ' + d.error);
    document.getElementById('st').style.color = d.ok ? '#45f063' : '#ff5a6a';
}
function save() { post({ video: document.getElementById('video').value, mute: document.getElementById('mute').checked }); }
function clearV() { document.getElementById('video').value = ''; post({ video: '', mute: document.getElementById('mute').checked }); }
load();
</script></body></html>
"""


@app.route("/youtube")
def youtube_page():
    return Response(YOUTUBE_HTML, mimetype="text/html")


@app.route("/youtube/setup")
def youtube_setup():
    return Response(YOUTUBE_SETUP_HTML, mimetype="text/html")


@app.route("/youtube/config", methods=["GET", "POST"])
def youtube_config():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        raw = str(body.get("video") or "")
        vid = _youtube_id(raw)
        if raw.strip() and not vid:
            return jsonify({"ok": False, "error": "not a YouTube link or video id"})
        cfg = {"video": vid, "mute": bool(body.get("mute", True))}
        YOUTUBE_CFG.write_text(_json.dumps(cfg), encoding="utf-8")
        return jsonify({"ok": True, **cfg})
    return jsonify(_youtube_cfg())


@app.route("/standings")
def standings():
    return jsonify(poller.get())


# Bump on every change: http://localhost:5005/version shows which code the
# running process actually loaded (the stream PC gets this folder through
# Nextcloud, so a restart can still pick up the previous file).
CODE_VERSION = "2026-10-10 rows"


@app.route("/version")
def version():
    snap = poller.get()
    return jsonify({
        "version": CODE_VERSION,
        "file": __file__,
        "classes": sorted({r.get("class_name") or "" for r in snap.get("standings") or []}),
    })


@app.route("/flag/<code>.svg")
def flag(code: str):
    path = country_flags.flag_path(code)
    if not path:
        abort(404)
    return send_file(str(path), mimetype="image/svg+xml", max_age=86400)


@app.route("/proam")
def proam_status():
    return jsonify(proam.status())


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
    proam.start()
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()
    try:
        print("=" * 60)
        print(f"iRacing Live Standings  (code {CODE_VERSION})")
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