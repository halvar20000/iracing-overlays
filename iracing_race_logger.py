"""
iRacing Race Logger
-------------------
Standalone background script that records an entire race session as a
machine-readable log file. One event per line (JSONL — newline-
delimited JSON), so the file can be appended to live and parsed later
in Excel / jq / Python without reading the whole thing.

Requirements:  pip install pyirsdk flask requests
Run:           python iracing_race_logger.py
Open:          http://localhost:5009    (status page + download link)

What's captured per race:
  - session_start  — track, session type, drivers list (iRating /
                     license / team / car), weather at the start
  - lap            — every lap completion by every driver: lap
                     number, lap time, position at S/F, gap to
                     leader, pit flag, best-lap-so-far
  - incident       — fetched from the dashboard's /incidents feed
                     (port 5000); deduped, so the same incident is
                     never written twice
  - session_end    — final classification when iRacing flips the
                     checkered (positions, laps completed, best lap,
                     incident count, finished / DNF / DQ)

Output: logs/<YYYYMMDD-HHMMSS>_<track>_race.jsonl

Practice / qualifying sessions are intentionally NOT logged. Add a
toggle here later if that ever changes.
"""

from __future__ import annotations
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, render_template_string, send_file, abort, request

try:
    import requests  # for /incidents fetch from the dashboard
except ImportError:
    requests = None  # type: ignore
    print("[logger] WARNING: 'requests' not installed — incidents won't be "
          "logged. Run 'pip install requests' to enable.")

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
LOGS_DIR = HERE / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Where to fetch the incident feed from. The dashboard publishes JSON at
# /incidents on port 5000. If the dashboard isn't running we silently
# skip incident logging — laps + classification still get recorded.
DASHBOARD_INCIDENTS_URL = "http://127.0.0.1:5000/incidents"
INCIDENT_POLL_INTERVAL  = 5.0   # seconds between fetches (laps poll faster)

# --- Pit detection ---
# Below this duration in seconds we treat the pit-road event as edge-of-
# pit-lane noise rather than a real stop or drive-through.
PIT_MIN_DURATION = 2.0

# --- Flag tracking (session-wide SessionFlags bitmask) ---
# Whitelisted flag bits we care about for the race log. Skips internal
# start-state bits (StartHidden/Ready/Set/Go), test signals (Random),
# and per-car bits handled separately (Furled/Repair/Black/DQ).
SESSION_FLAG_BITS = {
    0x0001: "checkered",
    0x0002: "white",
    0x0004: "green",
    0x0008: "yellow",
    0x0010: "red",
    0x0100: "yellow_waving",
    0x0200: "one_to_green",
    0x4000: "caution",
}

# --- Penalty detection (per-car CarIdxSessionFlags bitmask) ---
# These are iRacing's authoritative signals that a penalty has been
# assessed on a specific driver. Newly-set bits emit a penalty event.
CAR_PENALTY_BITS = {
    0x0020:    "blue_flag",        # let faster car by
    0x10000:   "black_flag",       # drive-through / stop-go
    0x20000:   "disqualify",       # DQ
    0x100000:  "repair",           # mandatory repair (meatball)
}

# --- Slow-lap detection ---
# A lap > average × this is flagged as anomalous (rolling 5-lap window).
SLOW_LAP_THRESHOLD = 1.10
SLOW_LAP_HISTORY   = 5

# --- Live charts ---
# Stable per-driver palette used by the chart-render endpoint. Indexed
# by the driver's position in the field when sorted by car_number, so a
# given driver gets the same color all race long. Distinct, vivid hues
# that read well on the dark theme.
CHART_PALETTE = [
    "#ff6b35", "#61b4ff", "#ffd166", "#4ade80", "#a371f7",
    "#22c9e0", "#ff8888", "#84cc16", "#f97316", "#9333ea",
    "#fb923c", "#34d399", "#fbbf24", "#f472b6", "#60a5fa",
    "#fcd34d", "#a78bfa", "#f87171", "#22d3ee", "#fde047",
]
# Cap how many drivers the operator can pin to the chart at once.
# More than this gets visually crowded and the legend overflows.
CHART_MAX_SELECTED = 5


def _safe_filename(s: str) -> str:
    """Trim a string into something sensible for a filename."""
    out = re.sub(r"[^A-Za-z0-9_\-]+", "_", s.strip().lower())
    return out.strip("_") or "session"


# ---------------------------------------------------------------------------
# Logger poller
# ---------------------------------------------------------------------------
class RaceLogger(SDKPoller):
    tag = "logger"
    poll_interval = 0.5  # 2 Hz — laps complete on the order of minutes

    def __init__(self):
        super().__init__()
        # Active log state. None means "not currently logging".
        self._log_path: Path | None = None
        self._log_fp = None              # file handle, line-buffered append
        self._log_session_key: tuple | None = None  # (uid, session_num)
        self._log_started_at: float = 0.0     # wall clock when log opened
        self._log_session_meta: dict = {}     # cached session-start payload

        # Per-car lap tracking.  We watch CarIdxLap; when it increments,
        # the car just crossed S/F and CarIdxLastLapTime now holds the
        # time of the lap they just completed.
        self._last_lap_seen: dict[int, int] = {}

        # Counters surfaced via /status for the live page
        self._laps_logged = 0
        self._incidents_logged = 0
        self._final_written = False
        # Per-driver running incident count. Keyed by car_idx (numeric,
        # always present in the dashboard's /incidents payload). Used to
        # be keyed by car_number, but car_number can be empty in some
        # spectator scenarios — the live monitor's "INC" column then
        # quietly stays at 0 for everyone, which is the bug we're
        # fixing here.
        self._driver_incident_count: dict[int, int] = {}   # car_idx -> count

        # Recent events for the live monitor timeline (newest first).
        # Captures lap-completion + incident events while the logger is
        # active. Bounded so it can never grow unbounded over a long race.
        self._recent_events: deque[dict] = deque(maxlen=80)

        # Overtake bookkeeping (race-scoped; reset each new race).
        # iRacing's CarIdxPosition only updates at S/F crossings, so a
        # delta vs. last-seen value tells us when a driver gained or
        # lost positions. Indirect movements (leader retires, everyone
        # behind moves up by 1) are counted too — that's iRacing's own
        # "positions gained / lost" definition.
        self._prev_position_seen: dict[int, int] = {}   # car_idx -> CarIdxPosition
        self._overtakes_made:     dict[int, int] = {}   # car_idx -> positions gained
        self._overtakes_against:  dict[int, int] = {}   # car_idx -> positions lost

        # Position-tick emission. Once per second we write a "pos" event
        # capturing every car's CarIdxLapDistPct. This is the granular
        # data render_race.py needs to animate the race afterwards —
        # without it, the lap-completion events alone are far too sparse
        # for a smooth replay. ~360 KB per 30-min race; trivial.
        self._last_pos_emit_t: float = -1e9   # session_time of last emit

        # Pit-stop tracking — entry/exit, duration, running count per car.
        # Detected via CarIdxOnPitRoad transitions (broader than the
        # InPitStall-only signal, so drive-throughs are caught too).
        self._pit_in_pit:    dict[int, bool]  = {}    # car_idx -> currently on pit road
        self._pit_entry_t:   dict[int, float] = {}    # car_idx -> session_time entered
        self._pit_entry_lap: dict[int, int]   = {}    # car_idx -> lap when entered
        self._pit_count:     dict[int, int]   = {}    # car_idx -> total stops completed
        # Duration of the most recent completed pit stop per car (seconds).
        # Used by the live drivers table and the public /share/standings
        # page to show "last pit time" without parsing the JSONL log.
        self._last_pit_duration: dict[int, float] = {}
        # First session-rank we ever observed for each driver (rank by
        # best-lap time, set the first tick they have a valid lap). Used
        # in practice/qualifying to show "improved by N positions
        # since you joined / since your first hot lap" without needing
        # iRacing's race-style CarIdxPosition (which is meaningless in
        # a hot-lap session).
        self._first_session_rank: dict[int, int] = {}
        # Chart-history tracker (separate from the race-only
        # _last_lap_seen / _maybe_emit_laps pipeline). This one runs
        # every poll regardless of session type so the live chart works
        # in practice and qualifying too. Reset whenever the session
        # changes (track or session number).
        self._chart_last_lap_seen: dict[int, int] = {}
        self._chart_session_key: tuple | None = None

        # Flag (session-wide) tracking. Watch SessionFlags for newly-set
        # bits we care about; emit a flag event on each transition.
        # Curated whitelist below filters out the spammy / internal bits
        # mobile-Claude was emitting.
        self._prev_session_flags: int = 0

        # Per-car flag tracking (penalty detection). BLACK / DQ / BLUE
        # bits in CarIdxSessionFlags are iRacing's authoritative signal
        # that a penalty has been assessed against a specific car.
        self._prev_car_session_flags: dict[int, int] = {}

        # Slow-lap detection — rolling history of recent lap times per
        # driver. When a new lap is more than SLOW_LAP_THRESHOLD slower
        # than the rolling average, emit a slow_lap event. Useful as a
        # broadcast-camera hint ("driver just did something weird").
        self._lap_history: dict[int, deque[float]] = {}

        # Live broadcast charts — operator picks drivers in the live
        # monitor; the OBS-source chart endpoint shows whatever's
        # currently selected. State is shared across all browsers.
        self._chart_selected: list[int] = []        # car_idx in selection order
        self._chart_type: str = "laptime"           # "laptime" | "position"
        # Full per-driver lap history kept for the duration of the race.
        # Distinct from _lap_history (the bounded slow-lap detector
        # window) because charts want every lap.
        self._chart_lap_data: dict[int, list[dict]] = {}
        # Pre-computed stable color per driver (set in _open_log).
        self._chart_colors: dict[int, str] = {}

        # Background thread for /incidents polling so we don't block
        # the main poll loop on HTTP timeouts.
        self._seen_incidents: set[tuple] = set()
        self._incident_thread = threading.Thread(
            target=self._incident_loop, daemon=True
        )
        self._incident_thread_started = False

    # ----- file lifecycle -------------------------------------------------
    def _open_log(self, session_meta: dict) -> None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        track = _safe_filename(session_meta.get("track", "unknown"))
        config = session_meta.get("track_config", "")
        if config:
            track = f"{track}_{_safe_filename(config)}"
        path = LOGS_DIR / f"{ts}_{track}_race.jsonl"
        self._log_path = path
        self._log_fp = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered
        self._log_started_at = time.time()
        self._log_session_meta = session_meta
        self._laps_logged = 0
        self._incidents_logged = 0
        self._final_written = False
        self._driver_incident_count = {}
        self._seen_incidents.clear()
        self._last_lap_seen.clear()
        self._recent_events.clear()
        self._prev_position_seen.clear()
        self._overtakes_made.clear()
        self._overtakes_against.clear()
        self._last_pos_emit_t = -1e9
        self._pit_in_pit.clear()
        self._pit_entry_t.clear()
        self._pit_entry_lap.clear()
        self._pit_count.clear()
        self._last_pit_duration.clear()
        self._first_session_rank.clear()
        self._prev_session_flags = 0
        self._prev_car_session_flags.clear()
        self._lap_history.clear()
        self._chart_selected.clear()
        self._chart_type = "laptime"
        self._chart_lap_data.clear()
        # Stable per-driver colors for the chart, indexed by sort-order
        # of car_number so the colors are deterministic.
        self._chart_colors = {}
        sorted_drv = sorted(session_meta.get("drivers", []),
                            key=lambda d: d.get("car_number", "ZZZ"))
        for i, d in enumerate(sorted_drv):
            self._chart_colors[d["car_idx"]] = CHART_PALETTE[i % len(CHART_PALETTE)]

        print(f"[logger] Opened {path.name}  ({session_meta.get('track')} "
              f"— {session_meta.get('session_type')})")

        # Write the session_start event
        self._emit({
            "type": "session_start",
            **session_meta,
        })

    def _close_log(self) -> None:
        if self._log_fp:
            # If we never managed to write the official session_end (e.g.
            # the user shut us down before iRacing locked the result, or
            # the session transitioned to Warmup early), drop in a
            # provisional final based on whatever ResultsPositions
            # iRacing has right now. Better than a log with no terminator.
            if not self._final_written:
                self._write_final_provisional()
            try:
                self._log_fp.flush()
                self._log_fp.close()
            except Exception:
                pass
        self._log_fp = None
        self._log_path = None
        self._log_session_key = None

    def stop(self) -> None:
        """Override SDKPoller.stop() so we get a chance to write the
        final classification before the base class shuts the SDK down.
        Once self.ir.shutdown() runs, ir["SessionInfo"] returns None and
        we can no longer read ResultsPositions.
        """
        if self._log_fp is not None and not self._final_written:
            self._write_final_provisional()
        super().stop()

    def _emit(self, event: dict) -> None:
        """Write one JSON line to the active log AND remember it for the
        live monitor timeline."""
        if self._log_fp is None:
            return
        # All events get a wall-clock 't_wall' so post-race tools can
        # correlate with stream replay timestamps.
        if "t_wall" not in event:
            event["t_wall"] = datetime.now().isoformat(timespec="seconds")
        try:
            self._log_fp.write(json.dumps(event, separators=(",", ":")) + "\n")
        except Exception as e:
            print(f"[logger] write error: {e!r}")
        # Keep an in-memory copy for the live race monitor (newest first).
        # Skip the verbose session_start/end blocks — those are big and
        # the monitor renders them from a different code path.
        if event.get("type") in ("lap", "incident", "pit", "flag",
                                 "penalty", "slow_lap"):
            self._recent_events.appendleft(event)

    # ----- iRacing read helpers ------------------------------------------
    def _build_drivers_list(self) -> list[dict]:
        info = self.ir["DriverInfo"] or {}
        out: list[dict] = []
        for d in info.get("Drivers", []) or []:
            cidx = d.get("CarIdx")
            if cidx is None:
                continue
            if d.get("CarIsPaceCar") == 1 or d.get("IsSpectator") == 1:
                continue
            out.append({
                "car_idx":    cidx,
                "car_number": d.get("CarNumber", "") or "",
                "name":       d.get("UserName", "") or "",
                "car":        d.get("CarScreenNameShort") or d.get("CarScreenName", "") or "",
                "car_class":  d.get("CarClassShortName") or "",
                "car_path":   d.get("CarPath", "") or "",
                "team":       d.get("TeamName", "") or "",
                "irating":    int(d.get("IRating") or 0),
                "license":    d.get("LicString", "") or "",
            })
        return out

    def _detect_session_change(self) -> tuple[tuple | None, str, dict]:
        """Returns (session_key, session_type, session_meta).

        session_key is None when we're not in a real session yet.
        """
        weekend = self.ir["WeekendInfo"] or {}
        info    = self.ir["SessionInfo"] or {}
        sessions = info.get("Sessions", []) or []
        sess_num = self.ir["SessionNum"]
        uid      = weekend.get("SessionUniqueID") or weekend.get("SessionID")

        if uid is None or sess_num is None:
            return None, "", {}

        cur_session = next((s for s in sessions if s.get("SessionNum") == sess_num), None)
        if cur_session is None:
            return None, "", {}

        sess_type = (cur_session.get("SessionType") or "").lower()
        meta = {
            "track":            weekend.get("TrackDisplayName", "") or "",
            "track_config":     weekend.get("TrackConfigName", "") or "",
            "track_id":         weekend.get("TrackID"),
            # Internal iRacing track name (e.g. "monza_full") — what
            # render_race.py uses to find the matching tracks/<name>.json.
            "track_name":       (weekend.get("TrackName") or "").strip().lower(),
            "session_type":     cur_session.get("SessionType", "") or "",
            "session_name":     cur_session.get("SessionName", "") or "",
            "session_num":      sess_num,
            "session_unique_id": uid,
            "session_laps":     cur_session.get("SessionLaps", ""),
            "session_time":     cur_session.get("SessionTime", ""),
            "drivers":          self._build_drivers_list(),
            "weather": {
                "track_temp_c": self.ir["TrackTempCrew"],
                "air_temp_c":   self.ir["AirTemp"],
                "skies":        self.ir["Skies"],
                "wetness":      self.ir["TrackWetness"],
            },
        }
        return (uid, sess_num), sess_type, meta

    # ----- overtake tracking ---------------------------------------------
    def _update_overtake_counters(self) -> None:
        """Compare each driver's current CarIdxPosition with the last one
        we saw. Position decreased → positions gained (overtake credit).
        Position increased → positions lost (overtaken).

        Note: counts indirect movement too (someone ahead retires →
        everyone behind picks up a position). That's how iRacing's own
        scoring defines "positions gained" — we match it.
        """
        if self._log_fp is None:
            return
        positions = self.ir["CarIdxPosition"] or []
        for d in self._log_session_meta.get("drivers", []):
            idx = d["car_idx"]
            if idx >= len(positions):
                continue
            cur = int(positions[idx]) if positions[idx] else 0
            if cur <= 0:
                continue
            prev = self._prev_position_seen.get(idx)
            self._prev_position_seen[idx] = cur
            if prev is None or prev <= 0:
                continue
            if cur < prev:
                self._overtakes_made[idx] = (
                    self._overtakes_made.get(idx, 0) + (prev - cur)
                )
            elif cur > prev:
                self._overtakes_against[idx] = (
                    self._overtakes_against.get(idx, 0) + (cur - prev)
                )

    # ----- pit-stop detection --------------------------------------------
    def _maybe_emit_pit_events(self) -> None:
        """Watch CarIdxOnPitRoad transitions per driver.

        Entry (False -> True): record the time and lap.
        Exit  (True  -> False): emit a pit event with the duration and
        the running stop count for that driver. Stops shorter than
        PIT_MIN_DURATION are treated as edge-of-pit-lane noise and
        discarded (no event, no count increment).
        """
        if self._log_fp is None:
            return
        ir = self.ir
        on_pit_arr = ir["CarIdxOnPitRoad"] or []
        lap_arr    = ir["CarIdxLap"] or []
        t_session  = float(ir["SessionTime"] or 0.0)

        for d in self._log_session_meta.get("drivers", []):
            idx = d["car_idx"]
            if idx >= len(on_pit_arr):
                continue
            cur = bool(on_pit_arr[idx])
            prev = self._pit_in_pit.get(idx, False)
            self._pit_in_pit[idx] = cur

            if cur and not prev:
                # Just entered pit road
                self._pit_entry_t[idx] = t_session
                self._pit_entry_lap[idx] = (
                    int(lap_arr[idx]) if idx < len(lap_arr) and lap_arr[idx] else 0
                )
            elif prev and not cur:
                # Just exited pit road
                entry_t = self._pit_entry_t.pop(idx, None)
                entry_lap = self._pit_entry_lap.pop(idx, 0)
                if entry_t is None:
                    continue  # we missed the entry; skip
                duration = t_session - entry_t
                if duration < PIT_MIN_DURATION:
                    continue  # noise — car barely brushed pit lane
                self._pit_count[idx] = self._pit_count.get(idx, 0) + 1
                self._last_pit_duration[idx] = duration
                self._emit({
                    "type":        "pit",
                    "t_session":   round(t_session, 2),
                    "car_idx":     idx,
                    "car_number":  d["car_number"],
                    "driver":      d["name"],
                    "entry_lap":   entry_lap,
                    "duration":    round(duration, 2),
                    "stop_count":  self._pit_count[idx],
                })

    # ----- session flag transitions --------------------------------------
    def _maybe_emit_flag_events(self) -> None:
        """Watch SessionFlags (session-wide) for newly-set bits we
        care about. Each transition fires one flag event.

        Whitelisted bits only — see SESSION_FLAG_BITS. The full bitmask
        contains a lot of internal start-state bits that would otherwise
        spam the log.
        """
        if self._log_fp is None:
            return
        cur_flags = int(self.ir["SessionFlags"] or 0)
        new_bits = cur_flags & ~self._prev_session_flags
        self._prev_session_flags = cur_flags
        if not new_bits:
            return
        t_session = float(self.ir["SessionTime"] or 0.0)
        for bit, name in SESSION_FLAG_BITS.items():
            if new_bits & bit:
                self._emit({
                    "type":      "flag",
                    "t_session": round(t_session, 2),
                    "flag":      name,
                    "raw_bit":   bit,
                })

    # ----- per-car penalty detection -------------------------------------
    def _maybe_emit_penalty_events(self) -> None:
        """Watch each car's CarIdxSessionFlags for newly-set penalty
        bits (BLACK / DQ / BLUE / REPAIR). One event per transition.
        """
        if self._log_fp is None:
            return
        flags_arr = self.ir["CarIdxSessionFlags"] or []
        t_session = float(self.ir["SessionTime"] or 0.0)
        for d in self._log_session_meta.get("drivers", []):
            idx = d["car_idx"]
            if idx >= len(flags_arr):
                continue
            cur = int(flags_arr[idx] or 0)
            prev = self._prev_car_session_flags.get(idx, 0)
            self._prev_car_session_flags[idx] = cur
            new_bits = cur & ~prev
            if not new_bits:
                continue
            for bit, name in CAR_PENALTY_BITS.items():
                if new_bits & bit:
                    self._emit({
                        "type":         "penalty",
                        "t_session":    round(t_session, 2),
                        "car_idx":      idx,
                        "car_number":   d["car_number"],
                        "driver":       d["name"],
                        "penalty_type": name,
                        "raw_bit":      bit,
                    })

    # ----- tire temperature reader (LOCAL PLAYER ONLY) -------------------
    def _read_tire_temps(self) -> dict | None:
        """Return tire surface temperatures for the local player's car,
        or None if not available. iRacing only broadcasts these for the
        local player — there's no per-car array we can use.

        Each tire returns three values: inner / middle / outer surface
        temperature in °C. Matches what the iRacing F-keys black box
        shows.
        """
        ir = self.ir
        info = ir["DriverInfo"] or {}
        local_idx = info.get("DriverCarIdx")
        if local_idx is None:
            return None

        def _tri(prefix):
            l = ir[prefix + "L"]
            m = ir[prefix + "M"]
            r = ir[prefix + "R"]
            if l is None or m is None or r is None:
                return None
            return [round(float(l), 1), round(float(m), 1), round(float(r), 1)]

        lf = _tri("LFtemp")
        rf = _tri("RFtemp")
        lr = _tri("LRtemp")
        rr = _tri("RRtemp")
        if not (lf or rf or lr or rr):
            return None
        return {"local_car_idx": int(local_idx), "lf": lf, "rf": rf, "lr": lr, "rr": rr}

    # ----- per-second position tick (for post-race replay rendering) -----
    def _maybe_emit_position(self) -> None:
        """Once per second, write a `pos` event capturing every car's
        CarIdxLapDistPct. This is the dense positional data
        render_race.py needs to animate the race afterwards; lap events
        alone are far too sparse for smooth animation.

        Format (compact on purpose — this is the largest event type):
          {"type":"pos","t":123.45,"p":{"3":0.234,"7":0.891,...}}

        Only cars currently in the world are written. Pit-road status
        and lap counts come from the lap events; the renderer can
        derive both at any point in time.
        """
        if self._log_fp is None:
            return
        ir = self.ir
        t_session = float(ir["SessionTime"] or 0.0)
        if t_session - self._last_pos_emit_t < 1.0:
            return  # less than a second since last emit
        self._last_pos_emit_t = t_session

        pct_arr = ir["CarIdxLapDistPct"] or []
        surface = ir["CarIdxTrackSurface"] or []
        pos_map: dict[str, float] = {}
        for d in self._log_session_meta.get("drivers", []):
            idx = d["car_idx"]
            if idx >= len(pct_arr):
                continue
            pct = pct_arr[idx]
            if pct is None or pct < 0 or pct > 1.0:
                continue
            # Skip cars not in the world (DNF / garage / disconnected).
            # Their last known position will linger in the renderer from
            # earlier ticks — which is correct: it's where they parked.
            in_world = idx < len(surface) and surface[idx] is not None and int(surface[idx]) != -1
            if not in_world:
                continue
            pos_map[str(idx)] = round(float(pct), 4)

        if not pos_map:
            return
        # Don't go through _emit() — we want this event NOT in the
        # in-memory _recent_events deque (it's noise for the timeline)
        # and we want to keep the JSON as compact as possible.
        try:
            self._log_fp.write(json.dumps({
                "type": "pos",
                "t":    round(t_session, 2),
                "p":    pos_map,
            }, separators=(",", ":")) + "\n")
        except Exception as e:
            print(f"[logger] pos write error: {e!r}")

    # ----- chart history (always on; race + practice + qualifying) -------
    def _update_chart_history(self, session_key) -> None:
        """Maintain `_chart_lap_data` regardless of session type. The
        race-only `_maybe_emit_laps` writes to the JSONL file and to
        chart history, but only fires while a log is open. This method
        runs every poll so the live chart works in practice and
        qualifying too. It uses `_chart_last_lap_seen` so it doesn't
        conflict with the race-emission tracker.
        """
        ir = self.ir
        # Reset chart state whenever the session itself changes (track
        # swap, qualifying -> race, etc). Without this, a previous
        # session's lap history bleeds into the next.
        if session_key is not None and session_key != self._chart_session_key:
            self._chart_lap_data.clear()
            self._chart_last_lap_seen.clear()
            self._first_session_rank.clear()
            # Don't wipe _chart_colors — they're stable per car_idx
            # which is consistent across sessions in the same week.
            self._chart_session_key = session_key

        info = ir["DriverInfo"] or {}
        drivers_raw = info.get("Drivers", []) or []
        lap_arr   = ir["CarIdxLap"] or []
        last_lap_t= ir["CarIdxLastLapTime"] or []
        on_pit    = ir["CarIdxOnPitRoad"] or []
        cls_pos   = ir["CarIdxClassPosition"] or []
        ovr_pos   = ir["CarIdxPosition"] or []

        for d in drivers_raw:
            idx = d.get("CarIdx")
            if idx is None:
                continue
            if d.get("CarIsPaceCar") == 1 or d.get("IsSpectator") == 1:
                continue
            # Lazy color assignment so quali / practice drivers get a
            # color the first time we see them (race opens a fresh
            # color set in _open_log; this fills in for non-race).
            if idx not in self._chart_colors:
                # Pick the next free palette slot deterministically by
                # current count, so a 1-driver session uses palette[0],
                # a 5-driver session uses palette[0..4], and so on.
                self._chart_colors[idx] = CHART_PALETTE[
                    len(self._chart_colors) % len(CHART_PALETTE)
                ]

            if idx >= len(lap_arr):
                continue
            cur_lap = lap_arr[idx]
            if cur_lap is None:
                continue
            prev = self._chart_last_lap_seen.get(idx)
            self._chart_last_lap_seen[idx] = cur_lap
            if prev is None:
                continue  # first observation — no completion to log
            if cur_lap > prev:
                lt = last_lap_t[idx] if idx < len(last_lap_t) else 0.0
                self._chart_lap_data.setdefault(idx, []).append({
                    "lap":       int(prev),
                    "lap_time":  float(lt) if lt and lt > 0 else None,
                    "position":  int(ovr_pos[idx]) if idx < len(ovr_pos) else 0,
                    "class_pos": int(cls_pos[idx]) if idx < len(cls_pos) else 0,
                    "on_pit":    bool(on_pit[idx]) if idx < len(on_pit) else False,
                })

    # ----- lap-completion detection --------------------------------------
    def _maybe_emit_laps(self) -> None:
        if self._log_fp is None:
            return
        ir = self.ir
        lap_arr   = ir["CarIdxLap"] or []
        last_lap_t= ir["CarIdxLastLapTime"] or []
        best_lap  = ir["CarIdxBestLapTime"] or []
        f2_arr    = ir["CarIdxF2Time"] or []
        on_pit    = ir["CarIdxOnPitRoad"] or []
        cls_pos   = ir["CarIdxClassPosition"] or []
        ovr_pos   = ir["CarIdxPosition"] or []
        t_session = ir["SessionTime"] or 0.0

        for d in self._log_session_meta.get("drivers", []):
            idx = d["car_idx"]
            if idx >= len(lap_arr):
                continue
            cur_lap = lap_arr[idx]
            if cur_lap is None:
                continue
            prev = self._last_lap_seen.get(idx)
            self._last_lap_seen[idx] = cur_lap
            if prev is None:
                continue  # first time we've seen this car — nothing to log yet

            if cur_lap > prev:
                # Crossed S/F. The lap they just completed is `prev`.
                lt = last_lap_t[idx] if idx < len(last_lap_t) else 0.0

                event = {
                    "type":        "lap",
                    "t_session":   round(float(t_session), 2),
                    "car_idx":     idx,
                    "car_number":  d["car_number"],
                    "driver":      d["name"],
                    "car":         d.get("car", ""),
                    "car_class":   d.get("car_class", ""),
                    "lap":         int(prev),  # lap just completed
                    "lap_time":    float(lt) if lt and lt > 0 else None,
                    "best_lap":    float(best_lap[idx]) if (idx < len(best_lap) and best_lap[idx] and best_lap[idx] > 0) else None,
                    "position":    int(ovr_pos[idx]) if idx < len(ovr_pos) else 0,
                    "class_pos":   int(cls_pos[idx]) if idx < len(cls_pos) else 0,
                    "gap_to_leader": float(f2_arr[idx]) if (idx < len(f2_arr) and f2_arr[idx] and f2_arr[idx] > 0) else None,
                    "on_pit":      bool(on_pit[idx]) if idx < len(on_pit) else False,
                    # Cumulative-to-this-point overtake counters. Useful
                    # for plotting "positions gained over time" charts.
                    "overtakes":   self._overtakes_made.get(idx, 0),
                    "overtaken":   self._overtakes_against.get(idx, 0),
                }

                # Tire temps — only present when this driver IS the local
                # player. iRacing doesn't broadcast tire temps for
                # non-player cars.
                tire = self._read_tire_temps()
                if tire and tire.get("local_car_idx") == idx:
                    event["tire_temps"] = {
                        "lf": tire["lf"], "rf": tire["rf"],
                        "lr": tire["lr"], "rr": tire["rr"],
                    }

                self._emit(event)
                self._laps_logged += 1

                # Chart history is now maintained by _update_chart_history
                # which runs every poll regardless of session type. The
                # old "append here too" path duplicated each lap entry,
                # so it's been removed.

                # Slow-lap detection. Skip pit laps (they're naturally
                # slow because of pit-lane time) and the first couple
                # of laps before the rolling average is meaningful.
                if event["lap_time"] is not None and not event["on_pit"]:
                    history = self._lap_history.setdefault(idx, deque(maxlen=SLOW_LAP_HISTORY))
                    if len(history) >= 3:
                        avg = sum(history) / len(history)
                        if avg > 0 and event["lap_time"] > avg * SLOW_LAP_THRESHOLD:
                            delta = event["lap_time"] - avg
                            self._emit({
                                "type":       "slow_lap",
                                "t_session":  round(float(t_session), 2),
                                "car_idx":    idx,
                                "car_number": d["car_number"],
                                "driver":     d["name"],
                                "lap":        int(prev),
                                "lap_time":   event["lap_time"],
                                "avg":        round(avg, 3),
                                "delta":      round(delta, 3),
                            })
                    history.append(event["lap_time"])

    # ----- final classification at checkered -----------------------------
    def _maybe_emit_final(self) -> None:
        """Write the session_end event ONLY when iRacing has officially
        closed the session (ResultsOfficial == 1).

        That flag flips after the slowest finisher crosses the line and
        iRacing has locked the classification — typically 30-60 seconds
        after the leader's checkered. By waiting for it, session_end is
        guaranteed to be the truly final result, with no straggler-lap
        events sneaking in afterwards.

        If the user shuts the logger down (Ctrl+C) or the session
        transitions to Warmup before that flag flips, _close_log writes
        a provisional final as a fallback so we never end up with a log
        that has no session_end event at all.
        """
        if self._log_fp is None or self._final_written:
            return
        ir = self.ir
        sess_state = ir["SessionState"] or 0
        if sess_state < 5:  # not yet checkered
            return
        info = ir["SessionInfo"] or {}
        sessions = info.get("Sessions", []) or []
        sess_num = ir["SessionNum"]
        cur = next((s for s in sessions if s.get("SessionNum") == sess_num), None)
        if cur is None:
            return
        if not cur.get("ResultsOfficial"):
            return  # wait for iRacing to lock the result
        self._write_final(cur, official=True)

    def _write_final(self, session: dict, official: bool) -> None:
        """Build and emit the session_end event from an iRacing session
        dict. Idempotent — does nothing if we've already written one.
        """
        if self._log_fp is None or self._final_written:
            return
        results = session.get("ResultsPositions") or []
        if not results:
            return  # nothing to write

        # Map driver info by car_idx for name/number lookup
        d_by_idx = {d["car_idx"]: d for d in self._log_session_meta.get("drivers", [])}

        final = []
        for r in results:
            cidx = r.get("CarIdx")
            drv = d_by_idx.get(cidx, {})
            time_field = r.get("Time", 0.0)
            laps_behind = 0
            if time_field is not None and time_field < 0:
                laps_behind = int(round(-time_field))
                time_field = None
            final.append({
                "position":       r.get("Position", 0) or 0,
                "class_position": r.get("ClassPosition", 0) or 0,
                "car_idx":        cidx,
                "car_number":     drv.get("car_number", ""),
                "driver":         drv.get("name", ""),
                "laps_completed": r.get("LapsComplete", 0) or 0,
                "best_lap":       r.get("FastestTime", 0.0) or 0.0,
                "best_lap_num":   r.get("FastestLap", 0) or 0,
                "incidents":      r.get("Incidents", 0) or 0,
                "time_gap":       time_field,
                "laps_behind":    laps_behind,
                "reason_out":     r.get("ReasonOutStr", "") or "Finished",
            })

        self._emit({
            "type":     "session_end",
            "official": official,
            "final":    final,
        })
        self._final_written = True
        kind = "FINAL" if official else "PROVISIONAL final"
        print(f"[logger] Wrote {kind} classification for "
              f"{self._log_session_meta.get('track')} ({len(final)} cars)")

    def _write_final_provisional(self) -> None:
        """Best-effort attempt to write session_end when we're about to
        close the log without having seen ResultsOfficial flip. Reads
        whatever ResultsPositions iRacing has at this moment. Marked
        official=False so post-race tools can tell.

        Safely handles the case where the SDK is no longer connected
        (returns None for everything) — we just skip in that case.
        """
        try:
            info = self.ir["SessionInfo"]
            if not info:
                return
            sess_num = self.ir["SessionNum"]
            sessions = info.get("Sessions", []) or []
            cur = next((s for s in sessions
                        if s.get("SessionNum") == sess_num), None)
            if cur is None:
                return
            if not (cur.get("ResultsPositions") or []):
                return  # genuinely nothing to write
            official = bool(cur.get("ResultsOfficial", 0))
            self._write_final(cur, official=official)
        except Exception as e:
            print(f"[logger] could not write provisional final: {e}")

    # ----- live driver state for the monitor UI --------------------------
    def _build_drivers_state(self) -> list[dict]:
        """Read live telemetry for every driver and return a list ready
        for rendering in the monitor table. Always available, even
        outside race sessions — useful for monitoring practice and
        qualifying too.
        """
        ir = self.ir
        info = ir["DriverInfo"] or {}
        drivers_raw = info.get("Drivers", []) or []

        ovr_pos   = ir["CarIdxPosition"] or []
        cls_pos   = ir["CarIdxClassPosition"] or []
        lap_arr   = ir["CarIdxLap"] or []
        last_lap  = ir["CarIdxLastLapTime"] or []
        best_lap  = ir["CarIdxBestLapTime"] or []
        f2_arr    = ir["CarIdxF2Time"] or []
        on_pit    = ir["CarIdxOnPitRoad"] or []
        surface   = ir["CarIdxTrackSurface"] or []

        out = []
        for d in drivers_raw:
            idx = d.get("CarIdx")
            if idx is None:
                continue
            if d.get("CarIsPaceCar") == 1 or d.get("IsSpectator") == 1:
                continue
            car_num = d.get("CarNumber", "") or ""
            pos = int(ovr_pos[idx]) if idx < len(ovr_pos) and ovr_pos[idx] else 0
            in_world = (idx < len(surface)
                        and surface[idx] is not None
                        and int(surface[idx]) != -1)
            out.append({
                "car_idx":     idx,
                "car_number":  car_num,
                "name":        d.get("UserName", "") or "",
                "team":        d.get("TeamName", "") or "",
                "car":         d.get("CarScreenNameShort") or d.get("CarScreenName", "") or "",
                "car_class":   d.get("CarClassShortName") or "",
                "position":    pos,
                "class_pos":   int(cls_pos[idx]) if idx < len(cls_pos) and cls_pos[idx] else 0,
                "lap":         int(lap_arr[idx]) if idx < len(lap_arr) and lap_arr[idx] else 0,
                "last_lap":    float(last_lap[idx]) if idx < len(last_lap) and last_lap[idx] and last_lap[idx] > 0 else None,
                "best_lap":    float(best_lap[idx]) if idx < len(best_lap) and best_lap[idx] and best_lap[idx] > 0 else None,
                "gap_to_leader": float(f2_arr[idx]) if idx < len(f2_arr) and f2_arr[idx] and f2_arr[idx] > 0 else None,
                "on_pit":      bool(on_pit[idx]) if idx < len(on_pit) else False,
                "in_world":    in_world,
                "incidents":   self._driver_incident_count.get(idx, 0),
                "overtakes":   self._overtakes_made.get(idx, 0),
                "overtaken":   self._overtakes_against.get(idx, 0),
                "pit_stops":   self._pit_count.get(idx, 0),
                "last_pit_duration": self._last_pit_duration.get(idx),
                # session_rank / gap_to_session_best / session_position_delta
                # are filled in below, once we've sorted everyone by
                # best lap time.
                "session_rank":            0,
                "session_best_lap":        None,
                "gap_to_session_best":     None,
                "first_session_rank":      None,
                "session_position_delta":  0,
            })

        # ---- Per-session ranking (useful in practice / qualifying) ----------
        # Rank drivers by best lap time. Drivers with no lap time set are
        # unranked (session_rank == 0). The fastest driver becomes rank 1
        # and is the reference for gap_to_session_best.
        ranked = [r for r in out if r["best_lap"] and r["best_lap"] > 0]
        ranked.sort(key=lambda r: r["best_lap"])
        session_best = ranked[0]["best_lap"] if ranked else None
        for rank_idx, r in enumerate(ranked, start=1):
            r["session_rank"]        = rank_idx
            r["session_best_lap"]    = session_best
            r["gap_to_session_best"] = max(0.0, r["best_lap"] - session_best)
            # First time we've seen this driver with a valid lap time?
            # Stamp the rank they entered the leaderboard at, so we can
            # later compute "positions gained since first hot lap".
            cidx = r["car_idx"]
            if cidx not in self._first_session_rank:
                self._first_session_rank[cidx] = rank_idx
            r["first_session_rank"] = self._first_session_rank[cidx]
            # Positive delta = gained positions, negative = lost.
            r["session_position_delta"] = (
                self._first_session_rank[cidx] - rank_idx
            )
        # Sort: in-world cars first, then by position (unassigned cars
        # at the bottom). Race sessions populate position correctly so
        # this is the right order. In practice / qualifying iRacing's
        # CarIdxPosition can be zero or stale — fall back to
        # session_rank (best-lap order) so the fastest driver appears
        # at the top, which is what viewers expect.
        def _sort_key(r):
            if r["position"] and r["position"] > 0:
                primary = r["position"]
            elif r["session_rank"] and r["session_rank"] > 0:
                primary = r["session_rank"]
            else:
                primary = 9999
            return (0 if r["in_world"] else 1, primary)
        out.sort(key=_sort_key)
        return out

    # ----- incident polling thread ---------------------------------------
    def _incident_loop(self) -> None:
        if requests is None:
            return
        while self._running:
            time.sleep(INCIDENT_POLL_INTERVAL)
            if self._log_fp is None:
                continue
            try:
                r = requests.get(DASHBOARD_INCIDENTS_URL, timeout=2)
                if r.status_code != 200:
                    continue
                payload = r.json()
            except Exception:
                continue
            # Dashboard /incidents shape: {"incidents": [{"t_session":..., "car_idx":..., "type":..., ...}, ...]}
            items = payload.get("incidents") if isinstance(payload, dict) else payload
            if not items:
                continue
            for inc in items:
                key = (
                    round(float(inc.get("t_session", 0)), 1),
                    inc.get("car_idx"),
                    inc.get("type", ""),
                )
                if key in self._seen_incidents:
                    continue
                self._seen_incidents.add(key)
                # Only log incidents that happened after our log opened.
                # The dashboard keeps a rolling buffer of older incidents
                # which we don't want to retro-log into a new session.
                t_inc_wall = inc.get("t_wall")
                # Best-effort: if dashboard doesn't expose t_wall on each
                # incident, just log everything since open. (Most users
                # start the logger before the race, so this is fine.)
                event = {
                    "type":         "incident",
                    "t_session":    inc.get("t_session"),
                    "car_idx":      inc.get("car_idx"),
                    "car_number":   inc.get("car_number", ""),
                    "driver":       inc.get("name", inc.get("driver", "")),
                    "incident_type": inc.get("type", ""),
                    "details":      inc.get("details", ""),
                }
                self._emit(event)
                self._incidents_logged += 1
                cidx = inc.get("car_idx")
                if cidx is not None:
                    try:
                        cidx_i = int(cidx)
                        self._driver_incident_count[cidx_i] = (
                            self._driver_incident_count.get(cidx_i, 0) + 1
                        )
                    except (TypeError, ValueError):
                        pass

    # ----- main snapshot --------------------------------------------------
    def _read_snapshot(self) -> dict:
        # Lazy-start the incident-polling thread once we're connected so
        # it doesn't spin pointlessly while waiting for iRacing.
        if requests is not None and not self._incident_thread_started:
            self._incident_thread.start()
            self._incident_thread_started = True

        session_key, session_type, meta = self._detect_session_change()

        # The chart history is always updated, regardless of session type,
        # so the live chart on /chart/render and /share/chart works in
        # practice and qualifying — not just races. Must come BEFORE the
        # not-a-race early-return below.
        self._update_chart_history(session_key)

        # Only log RACE sessions. Practice/quali/warmup get skipped.
        is_race = "race" in session_type.lower()

        if session_key is None or not is_race:
            # Not in a race. If a log was open from the previous session,
            # close it.
            if self._log_fp is not None:
                # Make sure we wrote the final classification before closing
                self._maybe_emit_final()
                self._close_log()
            return self._status_snapshot(session_key, session_type, meta)

        # We're in a race. Open a log if this is a new session.
        if session_key != self._log_session_key:
            if self._log_fp is not None:
                self._maybe_emit_final()
                self._close_log()
            self._open_log(meta)
            self._log_session_key = session_key

        # Active race tick — order matters: update overtake counters
        # BEFORE emitting laps so the lap event captures the latest
        # cumulative positions-gained / -lost values.
        self._update_overtake_counters()
        self._maybe_emit_position()
        self._maybe_emit_pit_events()
        self._maybe_emit_flag_events()
        self._maybe_emit_penalty_events()
        self._maybe_emit_laps()
        self._maybe_emit_final()
        return self._status_snapshot(session_key, session_type, meta)

    def _status_snapshot(self, session_key, session_type, meta) -> dict:
        # Live driver telemetry — always available, even outside race
        # sessions. The monitor UI uses this to render the drivers table.
        drivers_state = self._build_drivers_state()

        # Snapshot recent events for the timeline pane (newest first).
        # Convert the deque to a regular list for JSON serialisation.
        recent_events = list(self._recent_events)

        # Session clock + lap-counter info, surfaced so the top bar of
        # the monitor doesn't have to do its own SDK reads.
        ir = self.ir
        elapsed   = float(ir["SessionTime"] or 0.0)
        remaining = float(ir["SessionTimeRemain"] or 0.0)
        lap_total = ir["SessionLapsRemain"]  # iRacing's "laps remaining"
        sess_state = int(ir["SessionState"] or 0)

        # Driver-counting helpers for the top bar
        on_track = sum(1 for d in drivers_state if d["in_world"] and not d["on_pit"])
        in_pits  = sum(1 for d in drivers_state if d["on_pit"])
        out      = sum(1 for d in drivers_state if not d["in_world"])

        # Weather snapshot
        weather = {
            "track_temp_c": ir["TrackTempCrew"],
            "air_temp_c":   ir["AirTemp"],
            "wetness":      ir["TrackWetness"],
            "skies":        ir["Skies"],
        }

        # Tire temps for the local player (None if spectating).
        tire_temps = self._read_tire_temps()

        return {
            "connected":         True,
            "logging":           self._log_fp is not None,
            "log_path":          str(self._log_path) if self._log_path else None,
            "log_filename":      self._log_path.name if self._log_path else None,
            "started_at":        self._log_started_at,
            "session_type":      session_type,
            "session_name":      meta.get("session_name", ""),
            "session_key":       list(session_key) if session_key else None,
            "session_state":     sess_state,
            "track":             meta.get("track", ""),
            "track_config":      meta.get("track_config", ""),
            "drivers_count":     len(meta.get("drivers", [])),
            "drivers":           drivers_state,
            "recent_events":     recent_events,
            "laps_logged":       self._laps_logged,
            "incidents_logged":  self._incidents_logged,
            "final_written":     self._final_written,
            "elapsed":           elapsed,
            "remaining":         remaining,
            "laps_remaining":    int(lap_total) if (lap_total is not None and 0 <= lap_total < 99999) else None,
            "weather":           weather,
            "tire_temps":        tire_temps,
            "counts": {
                "on_track": on_track,
                "in_pits":  in_pits,
                "out":      out,
            },
            # Surface chart selection in /status so the operator panel
            # can show which drivers are pinned without a separate fetch.
            "chart_selected": list(self._chart_selected),
            "chart_type":     self._chart_type,
            "chart_colors":   {str(k): v for k, v in self._chart_colors.items()},
        }

    # ----- chart endpoints support ---------------------------------------
    def get_chart_state(self) -> dict:
        """Build the JSON payload the chart-render page polls.
        Includes the operator's selection plus the full lap history per
        selected driver so the chart can render without further fetches.
        Falls back to live SDK driver info when _log_session_meta is
        empty (i.e., outside a race session) so the chart still has
        names + numbers in practice / qualifying.
        """
        drivers_meta = {
            d["car_idx"]: d
            for d in self._log_session_meta.get("drivers", [])
        }
        # Fallback: read DriverInfo straight from the SDK if our race
        # meta is empty. Same shape (car_idx, car_number, name) as
        # _build_session_drivers so downstream code doesn't care which
        # source served it.
        if not drivers_meta:
            try:
                info = self.ir["DriverInfo"] or {}
                for d in info.get("Drivers", []) or []:
                    cidx = d.get("CarIdx")
                    if cidx is None:
                        continue
                    if d.get("CarIsPaceCar") == 1 or d.get("IsSpectator") == 1:
                        continue
                    drivers_meta[cidx] = {
                        "car_idx":    cidx,
                        "car_number": d.get("CarNumber", "") or "",
                        "name":       d.get("UserName", "") or "",
                    }
            except Exception:
                pass
        selected = []
        for cidx in self._chart_selected:
            meta = drivers_meta.get(cidx, {})
            selected.append({
                "car_idx":    cidx,
                "car_number": meta.get("car_number", "?"),
                "name":       meta.get("name", "Unknown"),
                "color":      self._chart_colors.get(cidx, "#ff6b35"),
                "laps":       list(self._chart_lap_data.get(cidx, [])),
            })
        return {
            "chart_type":   self._chart_type,
            "track":        self._log_session_meta.get("track", ""),
            "session_name": self._log_session_meta.get("session_name", ""),
            "logging":      self._log_fp is not None,
            "selected":     selected,
        }

    def set_chart_selection(self, drivers, chart_type=None) -> None:
        """Operator updates: replace the selection list and/or chart
        type. Drivers list is sanitized — keeps unique valid car_idx
        ints, capped at CHART_MAX_SELECTED to avoid clutter.
        """
        # Allowed chart types must match what the renderer can draw —
        # see /chart/render and the share-side _parse_share_params(),
        # both of which already accept "gap" (gap-to-leader). The
        # operator's local picker had a stale allowlist that silently
        # dropped "gap" requests, so the button on the live monitor
        # appeared inert. Keep this list in sync with the renderer.
        if chart_type in ("laptime", "position", "gap"):
            self._chart_type = chart_type
        if isinstance(drivers, list):
            clean: list[int] = []
            for raw in drivers:
                try:
                    cidx = int(raw)
                except (TypeError, ValueError):
                    continue
                if cidx in clean:
                    continue
                clean.append(cidx)
                if len(clean) >= CHART_MAX_SELECTED:
                    break
            self._chart_selected = clean

    def set_chart_top3(self) -> None:
        """Convenience: pin the current overall top 3 to the chart."""
        state = self._build_drivers_state()
        in_world_top = [r for r in state if r["in_world"] and r["position"] > 0]
        in_world_top.sort(key=lambda r: r["position"])
        self._chart_selected = [r["car_idx"] for r in in_world_top[:3]]


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)
poller = RaceLogger()


@app.before_request
def _gate_remote_to_share():
    """Defense in depth: when a request arrives via Cloudflare (Cf-Ray
    header present), restrict access to /share/* paths only. Even if
    cloudflared is misconfigured to forward everything, the local server
    itself refuses to serve admin endpoints (operator panel, log
    downloads, /chart/select, etc.) to remote viewers.

    Local LAN viewers don't carry that header and access everything as
    before.
    """
    if request.headers.get("Cf-Ray") and not request.path.startswith("/share/"):
        abort(404)


@app.route("/")
def index():
    return render_template_string(STATUS_HTML)


@app.route("/status")
def status():
    return jsonify(poller.get())


@app.route("/log")
def download_log():
    """Download the current log file (or 404 if no race is active)."""
    if not poller._log_path or not poller._log_path.is_file():
        abort(404)
    return send_file(str(poller._log_path), as_attachment=True,
                     download_name=poller._log_path.name,
                     mimetype="application/x-ndjson")


@app.route("/logs")
def list_logs():
    """List every log file we've written, newest first."""
    out = []
    for p in sorted(LOGS_DIR.glob("*.jsonl"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        st = p.stat()
        out.append({
            "name":     p.name,
            "size":     st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    return jsonify({"logs": out})


@app.route("/log/<path:name>")
def download_specific(name: str):
    """Download a previous log by filename."""
    safe = re.sub(r"[^A-Za-z0-9_\-.]+", "", name)
    if safe != name:
        abort(400)
    p = LOGS_DIR / safe
    if not p.is_file():
        abort(404)
    return send_file(str(p), as_attachment=True, download_name=p.name,
                     mimetype="application/x-ndjson")


# ----- Live chart endpoints ------------------------------------------------
# Pattern: operator picks drivers + chart type in the live monitor (which
# POSTs to /chart/select); the OBS browser source loads /chart/render and
# polls /chart/state every second. State is shared across all viewers so
# the operator's selection instantly updates whatever's on screen in OBS.

@app.route("/chart/state")
def chart_state():
    return jsonify(poller.get_chart_state())


@app.route("/chart/select", methods=["POST"])
def chart_select():
    payload = request.get_json(silent=True) or {}
    poller.set_chart_selection(
        payload.get("drivers", []),
        chart_type=payload.get("type"),
    )
    return jsonify({"ok": True,
                    "selected": list(poller._chart_selected),
                    "type":     poller._chart_type})


@app.route("/chart/top3", methods=["POST"])
def chart_top3():
    poller.set_chart_top3()
    return jsonify({"ok": True,
                    "selected": list(poller._chart_selected)})


@app.route("/chart/render")
def chart_render():
    """The OBS browser-source page. Pure SVG chart, polls /chart/state
    every second. Add this URL as a Browser Source in OBS at 600×360."""
    return render_template_string(CHART_HTML)


# ----- Public share endpoints ----------------------------------------------
# Stateless. Driver selection lives entirely in URL parameters, so an
# arbitrary number of remote viewers can each have their own view at
# the same time without affecting the operator's selection or each
# other. Designed to be safe to expose via Cloudflare Tunnel.
#
# Driver identifiers in URL params are CAR NUMBERS (the visible "#11"
# strings) rather than internal car_idx values — they're stable across
# sessions and shareable in URLs ("?drivers=11,23").

def _live_drivers_meta() -> list[dict]:
    """Return a list of {car_idx, car_number, name, car, car_class}
    for the current session — sourced from _log_session_meta when
    we're inside a race log, or live from the SDK otherwise. The SDK
    fallback keeps practice / qualifying chart functionality working
    even though the logger doesn't open a JSONL file for those."""
    meta = poller._log_session_meta.get("drivers", []) or []
    if meta:
        return meta
    out = []
    try:
        info = poller.ir["DriverInfo"] or {}
        for d in info.get("Drivers", []) or []:
            cidx = d.get("CarIdx")
            if cidx is None:
                continue
            if d.get("CarIsPaceCar") == 1 or d.get("IsSpectator") == 1:
                continue
            out.append({
                "car_idx":    cidx,
                "car_number": d.get("CarNumber", "") or "",
                "name":       d.get("UserName", "") or "",
                "car":        d.get("CarScreenNameShort") or d.get("CarScreenName", "") or "",
                "car_class":  d.get("CarClassShortName") or "",
            })
    except Exception:
        pass
    return out


def _car_numbers_to_idxs(car_numbers: list[str]) -> list[int]:
    """Resolve car-number strings to internal car_idx values for the
    current session. Returns idxs in the order the numbers were given;
    silently drops unknown numbers."""
    by_number = {
        str(d.get("car_number", "")): d["car_idx"]
        for d in _live_drivers_meta()
    }
    out = []
    for n in car_numbers:
        idx = by_number.get(str(n).strip())
        if idx is not None and idx not in out:
            out.append(idx)
    return out


def _parse_share_params() -> tuple[list[str], str]:
    """Parse drivers + type from the URL. Returns (car_numbers, type)."""
    raw = request.args.get("drivers", "") or ""
    car_numbers = [s for s in raw.split(",") if s.strip()]
    chart_type = (request.args.get("type") or "laptime").lower()
    if chart_type not in ("laptime", "position", "gap"):
        chart_type = "laptime"
    return car_numbers, chart_type


@app.route("/share/data")
def share_data():
    """Stateless JSON for the public share page. Returns the full
    driver list (so the picker can populate) AND the chart data for
    the selected drivers (so the chart can draw without a second
    fetch).

    URL params:
      drivers  comma-separated car NUMBERS, e.g. "11,23,45"
      type     "laptime" | "position" | "gap"
    """
    car_numbers, chart_type = _parse_share_params()
    selected_idxs = _car_numbers_to_idxs(car_numbers)

    # Build the public-safe driver list (no admin info, no irating, no
    # license, no team data — keep it minimal for public consumption).
    # _live_drivers_meta falls back to live SDK data when no race log is
    # open, so practice + qualifying viewers still get a populated list.
    drivers_meta = _live_drivers_meta()
    drivers_safe = []
    for d in drivers_meta:
        cidx = d["car_idx"]
        drivers_safe.append({
            "car_number": d.get("car_number", ""),
            "name":       d.get("name", "Unknown"),
            "car":        d.get("car", ""),
            "car_class":  d.get("car_class", ""),
            "color":      poller._chart_colors.get(cidx, "#ff6b35"),
        })

    # Chart data per selected driver
    selected = []
    for cidx in selected_idxs:
        meta = next((d for d in drivers_meta if d["car_idx"] == cidx), {})
        selected.append({
            "car_number": meta.get("car_number", "?"),
            "name":       meta.get("name", "Unknown"),
            "color":      poller._chart_colors.get(cidx, "#ff6b35"),
            "laps":       list(poller._chart_lap_data.get(cidx, [])),
        })

    # Session info — prefer the race-log meta, fall back to live SDK.
    sm = poller._log_session_meta
    track        = sm.get("track", "")
    track_config = sm.get("track_config", "")
    session_name = sm.get("session_name", "")
    session_type = sm.get("session_type", "")
    if not track:
        try:
            weekend = poller.ir["WeekendInfo"] or {}
            sess_info = poller.ir["SessionInfo"] or {}
            track        = weekend.get("TrackDisplayName", "") or ""
            track_config = weekend.get("TrackConfigName", "") or ""
            sess_num = poller.ir["SessionNum"]
            for s in sess_info.get("Sessions", []) or []:
                if s.get("SessionNum") == sess_num:
                    session_name = s.get("SessionName", "") or ""
                    session_type = s.get("SessionType", "") or ""
                    break
        except Exception:
            pass

    return jsonify({
        "chart_type":   chart_type,
        "track":        track,
        "track_config": track_config,
        "session_name": session_name,
        "session_type": session_type,
        "logging":      poller._log_fp is not None,
        "all_drivers":  drivers_safe,
        "selected":     selected,
    })


@app.route("/share/standings/data")
def share_standings_data():
    """Stateless live-standings JSON for the public standings page.
    Same shape as the live monitor's drivers table but with admin /
    diagnostic fields stripped."""
    state = poller._build_drivers_state()
    safe = []
    for r in state:
        safe.append({
            "position":     r["position"],
            "car_number":   r["car_number"],
            "name":         r["name"],
            "car":          r["car"],
            "car_class":    r["car_class"],
            "lap":          r["lap"],
            "last_lap":     r["last_lap"],
            "best_lap":     r["best_lap"],
            "gap_to_leader": r["gap_to_leader"],
            "on_pit":       r["on_pit"],
            "in_world":     r["in_world"],
            "incidents":    r["incidents"],
            "pit_stops":    r["pit_stops"],
            "overtakes":    r.get("overtakes", 0),
            "overtaken":    r.get("overtaken", 0),
            "last_pit_duration": r.get("last_pit_duration"),
            # Practice / qualifying-friendly extras
            "session_rank":           r.get("session_rank", 0),
            "session_best_lap":       r.get("session_best_lap"),
            "gap_to_session_best":    r.get("gap_to_session_best"),
            "session_position_delta": r.get("session_position_delta", 0),
        })
    return jsonify({
        "track":        poller._log_session_meta.get("track", ""),
        "track_config": poller._log_session_meta.get("track_config", ""),
        "session_name": poller._log_session_meta.get("session_name", ""),
        "session_type": poller._log_session_meta.get("session_type", ""),
        "drivers":      safe,
    })


@app.route("/share/chart")
def share_chart():
    """Public-safe chart page with a built-in driver picker. State
    lives in URL params, so each viewer's selection is independent
    and shareable."""
    return render_template_string(SHARE_CHART_HTML)


@app.route("/share/standings")
def share_standings():
    """Public-safe live standings table. Mobile-friendly."""
    return render_template_string(SHARE_STANDINGS_HTML)


# ---------------------------------------------------------------------------
# HTML — minimal status page
# ---------------------------------------------------------------------------
STATUS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Race Monitor</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0a0a0f; color: #e8e8ea;
    padding: 16px;
    font-variant-numeric: tabular-nums;
  }

  .app {
    max-width: 1500px; margin: 0 auto;
    display: flex; flex-direction: column; gap: 12px;
  }

  /* === Header bar === */
  .header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 18px;
    background: #14141c;
    border: 1px solid #26262f;
    border-radius: 8px;
  }
  .header h1 {
    font-size: 17px; color: #ff6b35; letter-spacing: 1px;
    font-weight: 800;
  }
  .header .right { display: flex; gap: 10px; align-items: center; }
  .pill {
    display: inline-block;
    padding: 4px 12px; border-radius: 14px;
    font-size: 11px; font-weight: 800; letter-spacing: 1px;
  }
  .pill.rec  { background: #3a1518; color: #ff8a8a;
               border: 1px solid #6c2027; animation: pulse 1.5s ease-in-out infinite; }
  .pill.idle { background: #1f1f2b; color: #8a8aa0; border: 1px solid #2e2e3d; }
  @keyframes pulse {
    0%,100% { opacity: 1; }
    50%     { opacity: 0.55; }
  }
  a.btn {
    display: inline-block; padding: 6px 12px;
    background: #ff6b35; color: #0a0a0f;
    border-radius: 6px; text-decoration: none;
    font-weight: 700; font-size: 12px;
  }
  a.btn:hover { background: #ff8a5b; }
  a.btn[disabled], a.btn.disabled { background: #2a2a38; color: #6a6a7a;
                                     pointer-events: none; }

  /* === Race info bar === */
  .info {
    display: grid;
    grid-template-columns: 1.4fr 1fr 1fr 1fr 1fr;
    background: #14141c;
    border: 1px solid #26262f;
    border-radius: 8px;
    overflow: hidden;
  }
  .info .cell {
    padding: 12px 16px;
    border-right: 1px solid #1f1f2b;
    min-width: 0;
  }
  .info .cell:last-child { border-right: none; }
  .info .label {
    font-size: 10px; text-transform: uppercase; letter-spacing: 1.2px;
    color: #7a7a90; font-weight: 700; margin-bottom: 4px;
  }
  .info .value {
    font-size: 16px; font-weight: 700; color: #fff;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .info .value.small { font-size: 13px; }
  .stype.race  { color: #ff6b35; }
  .stype.qual  { color: #4ade80; }
  .stype.prac  { color: #22c9e0; }
  .weather-pill {
    padding: 1px 10px; border-radius: 12px;
    font-size: 13px; font-weight: 700;
  }
  .weather-pill.dry { background: #2d1f11; color: #ff9f5a;
                      border: 1px solid #5a3a1f; }
  .weather-pill.wet { background: #0f2036; color: #61b4ff;
                      border: 1px solid #254a73; }

  /* === Counts mini-bar === */
  .counts {
    display: flex; align-items: center; gap: 14px;
    padding: 8px 18px;
    background: #1b1b26;
    border: 1px solid #26262f;
    border-radius: 8px;
    font-size: 12px;
  }
  .counts .item { color: #c8c8d8; font-weight: 600; }
  .counts .item .num { font-size: 16px; font-weight: 800;
                        color: #fff; margin-right: 4px; }
  .counts .item .sub { color: #7a7a90; font-weight: 500;
                        text-transform: uppercase; letter-spacing: 1px;
                        font-size: 10px; }

  /* === Two-pane main area === */
  .main {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 12px;
    min-height: 0;
  }

  .panel {
    background: #14141c;
    border: 1px solid #26262f;
    border-radius: 8px;
    overflow: hidden;
    display: flex; flex-direction: column; min-height: 0;
  }
  .panel-header {
    padding: 10px 16px;
    background: #1b1b26;
    border-bottom: 1px solid #26262f;
    font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px;
    color: #9a9aad; font-weight: 800;
    display: flex; align-items: center; justify-content: space-between;
  }

  /* === Drivers table === */
  .drivers {
    overflow-y: auto;
    max-height: 75vh;
  }
  .drv-row {
    display: grid;
    grid-template-columns: 42px 50px 1fr 75px 75px 65px 60px 50px;
    align-items: center;
    padding: 7px 14px;
    border-bottom: 1px solid #1d1d27;
    font-size: 13px;
  }
  .drv-row.head {
    position: sticky; top: 0; z-index: 1;
    background: #1b1b26;
    font-size: 10px; text-transform: uppercase; letter-spacing: 1px;
    color: #7a7a90; font-weight: 800;
    padding: 8px 14px;
  }
  .drv-row.out      { opacity: 0.45; }
  .drv-row.pit-row  { background: rgba(255, 195, 0, 0.04); }
  .drv-row.clickable { cursor: pointer; transition: background .12s; }
  .drv-row.clickable:hover { background: rgba(255,255,255,0.04); }
  .drv-row.charted {
    background: rgba(255,107,53,0.08);
    box-shadow: inset 4px 0 0 var(--chart-color, #ff6b35);
  }

  .drv-pos {
    font-size: 17px; font-weight: 800; color: #fff; text-align: center;
  }
  .drv-pos.p1 { color: #ffd166; }
  .drv-pos.p2 { color: #c0c0d0; }
  .drv-pos.p3 { color: #cd7f32; }
  .drv-num {
    background: #fff; color: #0a0a0f;
    padding: 2px 6px; border-radius: 3px;
    font-size: 11px; font-weight: 800;
    text-align: center; min-width: 36px;
  }
  .drv-name {
    font-weight: 600; color: #fff;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    padding-right: 10px;
  }
  .drv-name .sub {
    display: block;
    font-size: 10px; color: #7a7a90; font-weight: 500;
    margin-top: 1px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .drv-name .sub .class {
    color: #61b4ff; font-weight: 700;
    margin-right: 4px;
  }
  .drv-time { text-align: right; color: #c8c8d8; }
  .drv-best { text-align: right; color: #ffd166; font-weight: 600; }
  .drv-gap  { text-align: right; color: #c8c8d8; font-weight: 600; }
  /* Overtakes / overtaken cell — green up arrow + red down arrow */
  .drv-ot {
    text-align: right; font-size: 11px; font-weight: 700;
    padding-right: 4px;
    font-variant-numeric: tabular-nums;
  }
  .drv-ot .up   { color: #4ade80; }
  .drv-ot .down { color: #ff8888; margin-left: 6px; }
  .drv-ot .zero { color: #3a3a4a; }
  .drv-inc {
    text-align: right; padding-right: 4px;
  }
  .drv-inc .num {
    background: #2a1a1a; color: #ff8888;
    padding: 1px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 700; min-width: 24px;
    display: inline-block;
  }
  .drv-inc .num.zero { background: transparent; color: #4a4a5a; }

  /* === Chart control panel === */
  .chart-bar {
    background: #14141c;
    border: 1px solid #26262f;
    border-radius: 8px;
    padding: 10px 14px;
  }
  .chart-bar .head {
    display: flex; align-items: center; gap: 14px;
    margin-bottom: 8px; flex-wrap: wrap;
  }
  .chart-bar h3 {
    font-size: 11px; color: #9a9aad; text-transform: uppercase;
    letter-spacing: 1.5px; font-weight: 800;
  }
  .chart-bar .seg {
    display: inline-flex; background: #1b1b26;
    border: 1px solid #2e2e3d; border-radius: 6px; overflow: hidden;
  }
  .chart-bar .seg button {
    background: transparent; border: none;
    color: #c8c8d8; font-size: 11px; font-weight: 700;
    padding: 4px 10px; cursor: pointer; letter-spacing: 0.5px;
  }
  .chart-bar .seg button.active {
    background: #ff6b35; color: #0a0a0f;
  }
  .chart-bar button.act {
    background: #2a2a38; border: 1px solid #3a3a4a;
    color: #c8c8d8; font-size: 11px; font-weight: 700;
    padding: 4px 12px; border-radius: 6px; cursor: pointer;
  }
  .chart-bar button.act:hover { background: #3a3a4a; color: #fff; }
  .chart-bar a.dl-url {
    color: #ff6b35; text-decoration: none;
    font-size: 10px; font-family: monospace;
    margin-left: auto;
    padding: 4px 8px;
    border: 1px dashed #3a3a4a; border-radius: 4px;
  }
  .chart-bar a.dl-url:hover { border-style: solid; }
  .chart-bar .chips {
    display: flex; gap: 6px; flex-wrap: wrap;
    min-height: 22px;
  }
  .chart-chip {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 3px 4px 3px 8px; border-radius: 12px;
    background: #1b1b26; border: 1px solid #2e2e3d;
    font-size: 11px; font-weight: 700;
  }
  .chart-chip .swatch {
    width: 8px; height: 8px; border-radius: 50%;
  }
  .chart-chip .num { color: #c8c8d8; }
  .chart-chip .name { color: #fff; }
  .chart-chip .x {
    background: transparent; border: none; color: #7a7a90;
    font-size: 14px; line-height: 1; cursor: pointer;
    padding: 0 4px; margin-left: 2px;
  }
  .chart-chip .x:hover { color: #ff8888; }
  .chart-empty {
    color: #6a6a7a; font-size: 11px; font-style: italic;
  }

  /* === Tire temps panel (local player only) === */
  .tires {
    display: none; /* shown by JS when data available */
    background: #14141c;
    border: 1px solid #26262f;
    border-radius: 8px;
    padding: 10px 14px;
  }
  .tires.visible { display: block; }
  .tires .head {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 8px;
  }
  .tires .head h3 {
    font-size: 11px; color: #9a9aad; text-transform: uppercase;
    letter-spacing: 1.5px; font-weight: 800;
  }
  .tires .head .note {
    font-size: 10px; color: #7a7a90;
  }
  .tires .grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
  }
  .tire-corner {
    display: flex; flex-direction: column; gap: 2px;
    background: #1b1b26;
    border: 1px solid #26262f;
    border-radius: 6px;
    padding: 6px 10px;
  }
  .tire-corner .lbl {
    font-size: 10px; color: #7a7a90; font-weight: 700;
    letter-spacing: 1px;
  }
  .tire-corner .vals {
    display: flex; gap: 10px;
    font-size: 14px; font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .tire-corner .vals span {
    flex: 1; text-align: center;
  }
  /* Tire temp color coding: cool (blue) / warm (green) / hot (red).
     Sim-racing-friendly thresholds for slick tires (~80–105°C optimal). */
  .t-cool { color: #61b4ff; }
  .t-ok   { color: #4ade80; }
  .t-hot  { color: #ff8888; }
  .pit-flag {
    display: inline-block; margin-left: 6px;
    background: #3a1a1a; color: #ff8888;
    border: 1px solid #5c2a2a;
    padding: 1px 6px; border-radius: 3px;
    font-size: 9px; font-weight: 800; letter-spacing: 0.5px;
  }
  .out-flag {
    color: #6a6a7a; font-style: italic; font-size: 11px;
  }

  /* === Event timeline === */
  .timeline {
    overflow-y: auto;
    max-height: 75vh;
    padding: 4px 0;
  }
  .ev {
    padding: 8px 14px;
    border-bottom: 1px solid #1d1d27;
    font-size: 12px;
  }
  .ev .when {
    color: #7a7a90; font-size: 11px; font-family: monospace;
    margin-right: 8px;
  }
  .ev.ev-lap {}
  .ev.ev-lap .who { color: #c8c8d8; font-weight: 600; }
  .ev.ev-lap .lap-time { color: #ffd166; font-weight: 700; }
  .ev.ev-incident { background: rgba(255, 60, 60, 0.05); }
  .ev.ev-incident .icon { color: #ff8888; font-weight: 800; margin-right: 4px; }
  .ev.ev-incident .who { color: #ff8888; font-weight: 700; }
  .ev.ev-incident .desc { color: #c8c8d8; font-size: 11px; margin-top: 2px; }
  .ev.ev-pit { background: rgba(255, 195, 0, 0.05); }
  .ev.ev-pit .icon { color: #ffd166; margin-right: 4px; }
  .ev.ev-pit .who  { color: #ffd166; font-weight: 700; }
  .ev.ev-flag { background: rgba(34, 201, 224, 0.05); }
  .ev.ev-flag .icon { color: #22c9e0; margin-right: 4px; }
  .ev.ev-flag .what { color: #22c9e0; font-weight: 800; text-transform: uppercase; }
  .ev.ev-penalty { background: rgba(255, 60, 60, 0.08); }
  .ev.ev-penalty .icon { color: #ff6b6b; margin-right: 4px; font-weight: 800; }
  .ev.ev-penalty .who  { color: #ff6b6b; font-weight: 700; }
  .ev.ev-penalty .what { color: #ff6b6b; font-weight: 800; text-transform: uppercase; }
  .ev.ev-slow_lap .icon { color: #ff9800; margin-right: 4px; }
  .ev.ev-slow_lap .who  { color: #ff9800; font-weight: 700; }
  .ev .pos {
    display: inline-block;
    background: #1f1f2b;
    color: #c8c8d8; font-weight: 700;
    padding: 0 6px; border-radius: 3px; font-size: 10px;
    margin-right: 6px;
  }

  .empty {
    padding: 30px 16px; text-align: center;
    color: #7a7a90; font-size: 12px;
  }

  /* === Past logs (collapsed below) === */
  .past-logs { font-size: 12px; padding: 8px 14px; }
  .past-logs h3 {
    font-size: 10px; color: #9a9aad; text-transform: uppercase;
    letter-spacing: 1.5px; margin: 8px 0; font-weight: 800;
  }
  .past-logs a { color: #ff6b35; text-decoration: none; font-family: monospace; }
  .past-logs a:hover { text-decoration: underline; }
  .past-row { padding: 3px 0; color: #8a8aa0; font-size: 11px; }
  .past-row .meta { margin-left: 8px; }
</style>
</head>
<body>

<div class="app">

  <!-- HEADER -->
  <div class="header">
    <h1>iRACING RACE MONITOR</h1>
    <div class="right">
      <span id="rec-pill" class="pill idle">idle</span>
      <a id="dl-btn" class="btn disabled" href="/log">Download log</a>
    </div>
  </div>

  <!-- RACE INFO BAR -->
  <div class="info">
    <div class="cell">
      <div class="label">Track</div>
      <div class="value" id="track">—</div>
    </div>
    <div class="cell">
      <div class="label">Session</div>
      <div class="value" id="session">—</div>
    </div>
    <div class="cell">
      <div class="label">Elapsed / Remaining</div>
      <div class="value small" id="time">—</div>
    </div>
    <div class="cell">
      <div class="label">Weather</div>
      <div class="value" id="weather">—</div>
    </div>
    <div class="cell">
      <div class="label">Track temp</div>
      <div class="value" id="temp">—</div>
    </div>
  </div>

  <!-- COUNTS -->
  <div class="counts">
    <div class="item"><span class="num" id="cnt-on">0</span><span class="sub">On track</span></div>
    <div class="item"><span class="num" id="cnt-pit">0</span><span class="sub">In pits</span></div>
    <div class="item"><span class="num" id="cnt-out">0</span><span class="sub">Out</span></div>
    <div class="item" style="margin-left:auto;"><span class="num" id="cnt-laps">0</span><span class="sub">Laps logged</span></div>
    <div class="item"><span class="num" id="cnt-inc">0</span><span class="sub">Incidents logged</span></div>
  </div>

  <!-- CHART CONTROL PANEL — operator picks drivers + chart type for the
       OBS-source chart at /chart/render -->
  <div class="chart-bar">
    <div class="head">
      <h3>Live chart</h3>
      <div class="seg" id="chart-type-seg">
        <button data-type="laptime" class="active">Lap times</button>
        <button data-type="position">Position</button>
        <button data-type="gap">Gap to leader</button>
      </div>
      <button class="act" onclick="chartTop3()">Top 3</button>
      <button class="act" onclick="chartClear()">Clear</button>
      <a class="dl-url" id="chart-url" href="/chart/render" target="_blank">
        OBS source: /chart/render
      </a>
    </div>
    <div class="chips" id="chart-chips">
      <span class="chart-empty">Click drivers in the table to pin them. Up to 5.</span>
    </div>
  </div>

  <!-- TIRE TEMPS (only visible when local player data is available) -->
  <div class="tires" id="tires">
    <div class="head">
      <h3>Your car — tire temperatures</h3>
      <span class="note">surface temps — inner / middle / outer (°C)</span>
    </div>
    <div class="grid">
      <div class="tire-corner">
        <span class="lbl">FRONT LEFT</span>
        <div class="vals" id="t-lf"><span>—</span><span>—</span><span>—</span></div>
      </div>
      <div class="tire-corner">
        <span class="lbl">FRONT RIGHT</span>
        <div class="vals" id="t-rf"><span>—</span><span>—</span><span>—</span></div>
      </div>
      <div class="tire-corner">
        <span class="lbl">REAR LEFT</span>
        <div class="vals" id="t-lr"><span>—</span><span>—</span><span>—</span></div>
      </div>
      <div class="tire-corner">
        <span class="lbl">REAR RIGHT</span>
        <div class="vals" id="t-rr"><span>—</span><span>—</span><span>—</span></div>
      </div>
    </div>
  </div>

  <!-- MAIN: drivers + timeline -->
  <div class="main">

    <div class="panel">
      <div class="panel-header">
        <span>Drivers</span>
        <span style="font-size:10px;color:#7a7a90;">live</span>
      </div>
      <div class="drivers" id="drivers">
        <div class="drv-row head">
          <div>POS</div>
          <div>#</div>
          <div>DRIVER · CAR</div>
          <div style="text-align:right;">LAST LAP</div>
          <div style="text-align:right;">BEST</div>
          <div style="text-align:right;">GAP</div>
          <div style="text-align:right;" title="Positions gained / lost (+/−)">+/−</div>
          <div style="text-align:right;">INC</div>
        </div>
        <div id="drv-rows"><div class="empty">Waiting for iRacing…</div></div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-header">
        <span>Timeline</span>
        <span style="font-size:10px;color:#7a7a90;">recent</span>
      </div>
      <div class="timeline" id="timeline">
        <div class="empty">No events yet. Start a race session and the
          timeline will populate as drivers complete laps and as
          incidents come in from the dashboard.</div>
      </div>
    </div>

  </div>

  <!-- PAST LOGS -->
  <div class="panel">
    <div class="panel-header">
      <span>Past race logs</span>
      <span id="logs-count" style="font-size:10px;color:#7a7a90;"></span>
    </div>
    <div class="past-logs" id="past-logs">loading…</div>
  </div>

</div>

<script>
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}
function fmtClock(secs) {
  if (secs == null || secs < 0 || !isFinite(secs)) return '--:--';
  secs = Math.floor(secs);
  const h = Math.floor(secs/3600);
  const m = Math.floor((secs%3600)/60);
  const s = secs%60;
  return h ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
           : `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}
function fmtLap(t) {
  if (!t || t <= 0) return '—';
  const m = Math.floor(t/60);
  const s = t - m*60;
  return m ? `${m}:${s.toFixed(3).padStart(6,'0')}` : s.toFixed(3);
}
function fmtGap(g) {
  if (g == null || g <= 0) return '—';
  if (g < 60) return '+' + g.toFixed(g < 10 ? 3 : 2);
  const m = Math.floor(g/60); const s = g - m*60;
  return `+${m}:${s.toFixed(2).padStart(5,'0')}`;
}
function fmtBytes(b) {
  if (b < 1024) return b + ' B';
  if (b < 1024*1024) return (b/1024).toFixed(1) + ' KB';
  return (b/(1024*1024)).toFixed(1) + ' MB';
}
function shortTime(iso) {
  if (!iso) return '';
  // 2026-04-26T19:30:15 -> 19:30:15
  return iso.split('T').pop().slice(0, 8);
}

// Tire surface temperature → color class. Tuned for slick race tires:
// optimal window roughly 80–105°C for GT3-class compounds; below = cold,
// above = overheating. Adjust if your league runs different rubber.
function tireClass(temp) {
  if (temp == null) return '';
  if (temp < 70)  return 't-cool';
  if (temp > 105) return 't-hot';
  return 't-ok';
}
function renderTireCorner(id, vals) {
  const el = document.getElementById(id);
  if (!el || !vals) return;
  el.innerHTML = vals.map(v =>
    `<span class="${tireClass(v)}">${v == null ? '—' : v.toFixed(1)}°</span>`
  ).join('');
}

// --- Chart control: pin/unpin drivers, switch chart type ---
// Cache the most recent /status payload so we can map car_idx -> name
// without doing a separate driver lookup when rendering chips.
let _lastDrivers = [];
function renderChartChips(d, selected, colors) {
  _lastDrivers = d.drivers || [];
  const el = document.getElementById('chart-chips');
  if (!selected.size) {
    el.innerHTML = '<span class="chart-empty">' +
      'Click drivers in the table to pin them to the chart. Up to 5.' +
      '</span>';
    return;
  }
  // Preserve the operator's selection ORDER (taken from chart_selected,
  // not the table sort) so the chips line up consistently.
  const order = (d.chart_selected || []).map(Number);
  const byIdx = new Map(_lastDrivers.map(r => [r.car_idx, r]));
  let html = '';
  for (const idx of order) {
    const r = byIdx.get(idx);
    if (!r) continue;
    const c = colors[String(idx)] || '#ff6b35';
    html += `
      <span class="chart-chip">
        <span class="swatch" style="background:${c}"></span>
        <span class="num">#${esc(r.car_number || '?')}</span>
        <span class="name">${esc(r.name || 'Unknown')}</span>
        <button class="x" title="remove from chart"
                onclick="event.stopPropagation();toggleChartDriver(${idx})">×</button>
      </span>`;
  }
  el.innerHTML = html;
}

function syncChartTypeButtons(type) {
  document.querySelectorAll('#chart-type-seg button').forEach(btn => {
    if (btn.dataset.type === type) btn.classList.add('active');
    else btn.classList.remove('active');
  });
}

// Wire the chart-type segmented control once at startup.
document.querySelectorAll('#chart-type-seg button').forEach(btn => {
  btn.addEventListener('click', () => {
    setChartSelection(null, btn.dataset.type);
  });
});

async function toggleChartDriver(carIdx) {
  // Pull current selection from the cached status, toggle, push back.
  const r = await fetch('/status');
  const s = await r.json();
  const sel = (s.chart_selected || []).map(Number);
  const i = sel.indexOf(Number(carIdx));
  if (i >= 0) sel.splice(i, 1);
  else        sel.push(Number(carIdx));
  setChartSelection(sel, null);
}
async function setChartSelection(drivers, chartType) {
  const body = {};
  if (drivers !== null) body.drivers = drivers;
  if (chartType !== null) body.type = chartType;
  await fetch('/chart/select', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  refresh();   // pull new state straight away
}
async function chartTop3() {
  await fetch('/chart/top3', {method: 'POST'});
  refresh();
}
async function chartClear() {
  await setChartSelection([], null);
}
const TRACK_WETNESS = {
  1: ['Dry',                'dry'],
  2: ['Mostly dry',         'dry'],
  3: ['Very lightly wet',   'wet'],
  4: ['Lightly wet',        'wet'],
  5: ['Moderately wet',     'wet'],
  6: ['Very wet',           'wet'],
  7: ['Extremely wet',      'wet'],
};

function render(d) {
  // Header pill + download
  const pill = document.getElementById('rec-pill');
  const dl   = document.getElementById('dl-btn');
  if (d.logging) {
    pill.className = 'pill rec'; pill.textContent = 'RECORDING';
    dl.classList.remove('disabled');
  } else {
    pill.className = 'pill idle'; pill.textContent = 'idle';
    dl.classList.add('disabled');
  }

  // Race info bar
  document.getElementById('track').textContent =
    (d.track || '—') + (d.track_config ? ' · ' + d.track_config : '');
  const stype = (d.session_type || '').toLowerCase();
  const stEl  = document.getElementById('session');
  let stLabel = d.session_type || '—';
  if (d.session_name) stLabel = d.session_name;
  let stClass = '';
  if (stype.includes('race')) stClass = 'stype race';
  else if (stype.includes('qual')) stClass = 'stype qual';
  else if (stype.includes('practice')) stClass = 'stype prac';
  stEl.className = 'value ' + stClass;
  stEl.textContent = stLabel;

  // Time
  let timeText = fmtClock(d.elapsed);
  if (d.remaining > 0) {
    timeText += ' / ' + fmtClock(d.remaining) + ' left';
  } else if (d.laps_remaining != null) {
    timeText += ' (' + d.laps_remaining + ' laps left)';
  }
  document.getElementById('time').textContent = timeText;

  // Weather
  const w = d.weather || {};
  const tw = TRACK_WETNESS[w.wetness];
  const wLabel = tw ? tw[0] : 'Dry';
  const wCls   = tw ? tw[1] : 'dry';
  document.getElementById('weather').innerHTML =
    `<span class="weather-pill ${wCls}">${esc(wLabel)}</span>`;
  document.getElementById('temp').textContent =
    w.track_temp_c != null ? `${(+w.track_temp_c).toFixed(1)}°C` : '—';

  // Counts
  const c = d.counts || {};
  document.getElementById('cnt-on').textContent  = c.on_track ?? 0;
  document.getElementById('cnt-pit').textContent = c.in_pits ?? 0;
  document.getElementById('cnt-out').textContent = c.out ?? 0;
  document.getElementById('cnt-laps').textContent = d.laps_logged ?? 0;
  document.getElementById('cnt-inc').textContent  = d.incidents_logged ?? 0;

  // Tire temps — only visible when iRacing broadcasts them (= local
  // player is in-car). Pure spectators see no panel at all.
  const tirePanel = document.getElementById('tires');
  if (d.tire_temps && d.tire_temps.lf) {
    tirePanel.classList.add('visible');
    renderTireCorner('t-lf', d.tire_temps.lf);
    renderTireCorner('t-rf', d.tire_temps.rf);
    renderTireCorner('t-lr', d.tire_temps.lr);
    renderTireCorner('t-rr', d.tire_temps.rr);
  } else {
    tirePanel.classList.remove('visible');
  }

  // Drivers table
  const rowsEl = document.getElementById('drv-rows');
  const drivers = d.drivers || [];
  if (!drivers.length) {
    rowsEl.innerHTML = '<div class="empty">No drivers in session.</div>';
  } else {
    const chartSelected = new Set((d.chart_selected || []).map(Number));
    const chartColors = d.chart_colors || {};

    let html = '';
    for (const r of drivers) {
      const posCls = r.position === 1 ? 'p1' :
                     r.position === 2 ? 'p2' :
                     r.position === 3 ? 'p3' : '';
      let rowCls = !r.in_world ? 'out' : (r.on_pit ? 'pit-row' : '');
      rowCls += ' clickable';
      const isCharted = chartSelected.has(r.car_idx);
      let chartedStyle = '';
      if (isCharted) {
        rowCls += ' charted';
        const c = chartColors[String(r.car_idx)] || '#ff6b35';
        chartedStyle = `style="--chart-color:${c};"`;
      }
      const incCls = r.incidents > 0 ? '' : 'zero';
      const pitFlag = r.on_pit ? '<span class="pit-flag">PIT</span>' : '';
      const outFlag = !r.in_world ? '<span class="out-flag">DNF/garage</span>' : '';

      // Driver sub-line — class (multi-class only) + car + team + pit
      // stop count. Keeps the table compact while exposing the new fields.
      const subParts = [];
      if (r.car_class) subParts.push(`<span class="class">${esc(r.car_class)}</span>`);
      if (r.car) subParts.push(esc(r.car));
      if (r.team && r.team !== r.name) subParts.push(esc(r.team));
      if (r.pit_stops > 0) subParts.push(`🔧×${r.pit_stops}`);
      const subLine = subParts.length
        ? `<span class="sub">${subParts.join(' · ')}</span>` : '';

      // Overtakes / overtaken — green up + red down. Render zeros muted
      // so the cell stays visually quiet for the field that hasn't moved.
      const ot = r.overtakes || 0;
      const ag = r.overtaken || 0;
      const otCell =
        `<span class="${ot > 0 ? 'up' : 'zero'}">+${ot}</span>` +
        `<span class="${ag > 0 ? 'down' : 'zero'}">−${ag}</span>`;

      html += `
        <div class="drv-row ${rowCls}" ${chartedStyle}
             data-car-idx="${r.car_idx}"
             onclick="toggleChartDriver(${r.car_idx})">
          <div class="drv-pos ${posCls}">${r.position || '—'}</div>
          <div><span class="drv-num">#${esc(r.car_number || '—')}</span></div>
          <div class="drv-name">${esc(r.name || 'Unknown')}${pitFlag}${outFlag}${subLine}</div>
          <div class="drv-time">${fmtLap(r.last_lap)}</div>
          <div class="drv-best">${fmtLap(r.best_lap)}</div>
          <div class="drv-gap">${fmtGap(r.gap_to_leader)}</div>
          <div class="drv-ot">${otCell}</div>
          <div class="drv-inc"><span class="num ${incCls}">${r.incidents}</span></div>
        </div>`;
    }
    rowsEl.innerHTML = html;

    // Render the chart-control panel chips and reflect the current chart type
    renderChartChips(d, chartSelected, chartColors);
    syncChartTypeButtons(d.chart_type);
  }

  // Timeline (recent_events come newest-first from the server)
  const tl = document.getElementById('timeline');
  const events = d.recent_events || [];
  if (!events.length) {
    tl.innerHTML = '<div class="empty">No events yet. Start a race ' +
      'session and lap completions + incidents will appear here.</div>';
  } else {
    let html = '';
    for (const ev of events) {
      const t = shortTime(ev.t_wall);
      if (ev.type === 'lap') {
        const num = ev.car_number || '?';
        const name = ev.driver || 'Unknown';
        const lapNum = ev.lap || '?';
        const lapTime = fmtLap(ev.lap_time);
        const pos = ev.position ? `<span class="pos">P${ev.position}</span>` : '';
        html += `
          <div class="ev ev-lap">
            <span class="when">${t}</span>
            ${pos}
            <span class="who">#${esc(num)} ${esc(name)}</span>
            — lap ${lapNum} <span class="lap-time">${lapTime}</span>
          </div>`;
      } else if (ev.type === 'incident') {
        const num = ev.car_number || '?';
        const name = ev.driver || 'Unknown';
        const desc = ev.details || ev.incident_type || '';
        html += `
          <div class="ev ev-incident">
            <span class="when">${t}</span>
            <span class="icon">⚠</span>
            <span class="who">#${esc(num)} ${esc(name)}</span>
            <div class="desc">${esc(ev.incident_type || '')}${desc ? ' — ' + esc(desc) : ''}</div>
          </div>`;
      } else if (ev.type === 'pit') {
        html += `
          <div class="ev ev-pit">
            <span class="when">${t}</span>
            <span class="icon">🔧</span>
            <span class="who">#${esc(ev.car_number || '?')} ${esc(ev.driver || '')}</span>
            — pit stop #${ev.stop_count || '?'} (lap ${ev.entry_lap || '?'}, ${ev.duration ? ev.duration.toFixed(1) : '?'}s)
          </div>`;
      } else if (ev.type === 'flag') {
        html += `
          <div class="ev ev-flag">
            <span class="when">${t}</span>
            <span class="icon">🚩</span>
            <span class="what">${esc(ev.flag || '?')}</span>
          </div>`;
      } else if (ev.type === 'penalty') {
        html += `
          <div class="ev ev-penalty">
            <span class="when">${t}</span>
            <span class="icon">⚑</span>
            <span class="who">#${esc(ev.car_number || '?')} ${esc(ev.driver || '')}</span>
            <span class="what" style="margin-left:6px;">${esc(ev.penalty_type || '?')}</span>
          </div>`;
      } else if (ev.type === 'slow_lap') {
        html += `
          <div class="ev ev-slow_lap">
            <span class="when">${t}</span>
            <span class="icon">🐢</span>
            <span class="who">#${esc(ev.car_number || '?')} ${esc(ev.driver || '')}</span>
            — lap ${ev.lap || '?'} ${fmtLap(ev.lap_time)} (+${ev.delta ? ev.delta.toFixed(2) : '?'}s vs avg)
          </div>`;
      }
    }
    tl.innerHTML = html;
  }
}

async function refresh() {
  try {
    const r = await fetch('/status'); const s = await r.json();
    render(s);
  } catch (e) { /* keep last view */ }
  setTimeout(refresh, 1000);
}

async function refreshLogs() {
  try {
    const r = await fetch('/logs'); const d = await r.json();
    const ll = document.getElementById('past-logs');
    document.getElementById('logs-count').textContent =
      d.logs && d.logs.length ? `${d.logs.length} file${d.logs.length === 1 ? '' : 's'}` : '';
    if (!d.logs || !d.logs.length) {
      ll.textContent = 'No race logs yet.';
    } else {
      ll.innerHTML = d.logs.map(l =>
        `<div class="past-row"><a href="/log/${esc(l.name)}">${esc(l.name)}</a>` +
        ` <span class="meta">${fmtBytes(l.size)} · ${esc(l.modified)}</span></div>`
      ).join('');
    }
  } catch (e) {}
  setTimeout(refreshLogs, 5000);
}

refresh();
refreshLogs();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTML — chart browser source (for OBS)
# ---------------------------------------------------------------------------
# Designed for a 600×360 browser source. Transparent background by
# default so it composites over the stream layout. Polls /chart/state
# every second and re-renders the SVG line chart from scratch each tick.
CHART_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Live Chart</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 600px; height: 360px; overflow: hidden;
                background: transparent; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    color: #e8e8ea;
  }
  .chart-card {
    width: 600px; height: 360px;
    background: rgba(20, 20, 28, 0.92);
    border: 1px solid #26262f;
    border-radius: 10px;
    padding: 10px 14px;
    display: flex; flex-direction: column;
  }
  .chart-head {
    display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: 4px;
  }
  .chart-title {
    font-size: 12px; text-transform: uppercase; letter-spacing: 1.5px;
    font-weight: 800; color: #ff6b35;
  }
  .chart-meta {
    font-size: 10px; color: #7a7a90;
  }
  .legend {
    display: flex; gap: 12px; flex-wrap: wrap;
    margin-top: 6px;
    font-size: 11px;
  }
  .legend-item { display: flex; align-items: center; gap: 4px; }
  .legend-swatch {
    width: 14px; height: 3px; border-radius: 2px;
  }
  .legend-name { color: #c8c8d8; font-weight: 600; }
  .legend-num  { color: #7a7a90; font-weight: 500; margin-left: 2px; }
  svg.chart {
    flex: 1;
    width: 100%; min-height: 0;
  }
  .empty {
    flex: 1;
    display: flex; align-items: center; justify-content: center;
    color: #7a7a90;
    font-size: 12px;
    text-align: center; padding: 20px;
  }

  /* Stream-mode toggle button (debug-only, hidden in OBS) */
  .stream-toggle {
    position: fixed; top: 4px; right: 4px;
    background: rgba(20, 20, 28, 0.9);
    border: 1px solid #333; color: #999;
    padding: 3px 8px; font-size: 10px; border-radius: 3px;
    cursor: pointer; opacity: 0.5;
  }
  .stream-toggle:hover { opacity: 1; }
  body.solid-bg { background: #0a0a0f; }
  body.solid-bg .chart-card { background: #14141c; }
</style>
</head>
<body>

<button class="stream-toggle" onclick="document.body.classList.toggle('solid-bg')">BG</button>

<div class="chart-card" id="card">
  <div class="chart-head">
    <span class="chart-title" id="chart-title">—</span>
    <span class="chart-meta"  id="chart-meta">—</span>
  </div>
  <div id="chart-area" style="flex:1; display:flex; flex-direction:column; min-height:0;">
    <div class="empty" id="empty-msg">Waiting for selection…</div>
  </div>
</div>

<script>
const SVG_NS = "http://www.w3.org/2000/svg";

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}
function fmtLap(t) {
  if (!t || t <= 0) return '—';
  const m = Math.floor(t/60);
  const s = t - m*60;
  return m ? `${m}:${s.toFixed(2).padStart(5,'0')}` : s.toFixed(2);
}

function drawChart(state) {
  const area = document.getElementById('chart-area');
  area.innerHTML = '';

  const sel = state.selected || [];
  if (!sel.length) {
    area.innerHTML = '<div class="empty">No drivers selected. Pin some in the operator panel at /</div>';
    return;
  }

  // Filter to selections that have at least one lap recorded.
  const hasLaps = sel.filter(d => (d.laps || []).length > 0);
  if (!hasLaps.length) {
    area.innerHTML = '<div class="empty">Selected drivers haven\\'t completed any laps yet.</div>';
    return;
  }

  // Compute axis ranges
  let lapMin = Infinity, lapMax = -Infinity;
  let valMin = Infinity, valMax = -Infinity;
  const isPos = state.chart_type === 'position';
  const isGap = state.chart_type === 'gap';
  // For both position and gap, higher value = lower on chart (P1 / 0s
  // gap at the top, trailing positions / bigger gaps below).
  const inverted = isPos || isGap;
  function valueOf(l) {
    if (isPos) return l.position;
    if (isGap) return (l.gap_to_leader == null ? 0 : l.gap_to_leader);
    return l.lap_time;
  }
  for (const d of hasLaps) {
    for (const l of d.laps) {
      if (l.lap < lapMin) lapMin = l.lap;
      if (l.lap > lapMax) lapMax = l.lap;
      const v = valueOf(l);
      if (v == null) continue;
      if (!isGap && v <= 0) continue;   // 0 is valid for gap (= leader)
      if (v < valMin) valMin = v;
      if (v > valMax) valMax = v;
    }
  }
  if (!isFinite(lapMin) || !isFinite(valMin)) {
    area.innerHTML = '<div class="empty">No usable data points yet.</div>';
    return;
  }
  // Pad ranges a touch so points don't sit on the axis lines
  if (lapMax === lapMin) lapMax = lapMin + 1;
  let padTop, padBot;
  if (isPos) {
    valMin = Math.max(1, Math.floor(valMin));
    valMax = Math.ceil(valMax);
    padTop = padBot = 0.5;
  } else if (isGap) {
    valMin = 0;                          // anchor at the leader (0s)
    const range = valMax || 1;
    padTop = range * 0.08;
    padBot = 0;                          // no padding below 0
  } else {
    const range = valMax - valMin || 1;
    padTop = padBot = range * 0.08;
  }

  // SVG layout
  const W = 572, H = 250;
  const M = { l: 50, r: 12, t: 8, b: 28 };
  const innerW = W - M.l - M.r;
  const innerH = H - M.t - M.b;

  // Y scale: position is INVERTED (P1 at top)
  function xPx(lap) {
    return M.l + ((lap - lapMin) / (lapMax - lapMin)) * innerW;
  }
  function yPx(val) {
    const v0 = valMin - padBot;
    const v1 = valMax + padTop;
    const t = (val - v0) / (v1 - v0);
    if (inverted) {
      return M.t + t * innerH;          // higher value = lower on chart
    }
    return M.t + (1 - t) * innerH;      // higher value = higher on chart
  }

  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "chart");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");

  // --- Y axis grid + labels ---
  const yTicks = isPos
    ? makeIntegerTicks(valMin, valMax, 6)
    : makeNiceTicks(valMin - padBot, valMax + padTop, 5);
  for (const tickVal of yTicks) {
    const y = yPx(tickVal);
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", M.l);
    line.setAttribute("x2", W - M.r);
    line.setAttribute("y1", y);
    line.setAttribute("y2", y);
    line.setAttribute("stroke", "#26262f");
    line.setAttribute("stroke-width", "1");
    line.setAttribute("stroke-dasharray", "2 3");
    svg.appendChild(line);

    const label = document.createElementNS(SVG_NS, "text");
    label.setAttribute("x", M.l - 6);
    label.setAttribute("y", y + 3);
    label.setAttribute("fill", "#7a7a90");
    label.setAttribute("font-size", "9");
    label.setAttribute("text-anchor", "end");
    label.textContent =
      isPos ? `P${tickVal}` :
      isGap ? (tickVal === 0 ? 'LEAD' : `+${tickVal.toFixed(1)}s`) :
      fmtLap(tickVal);
    svg.appendChild(label);
  }

  // --- X axis labels ---
  const xTicks = makeIntegerTicks(lapMin, lapMax, 6);
  for (const tick of xTicks) {
    const x = xPx(tick);
    const label = document.createElementNS(SVG_NS, "text");
    label.setAttribute("x", x);
    label.setAttribute("y", H - 10);
    label.setAttribute("fill", "#7a7a90");
    label.setAttribute("font-size", "9");
    label.setAttribute("text-anchor", "middle");
    label.textContent = `L${tick}`;
    svg.appendChild(label);
  }
  // Axis labels
  const xAxisLbl = document.createElementNS(SVG_NS, "text");
  xAxisLbl.setAttribute("x", M.l + innerW/2);
  xAxisLbl.setAttribute("y", H - 1);
  xAxisLbl.setAttribute("fill", "#5a5a6a");
  xAxisLbl.setAttribute("font-size", "9");
  xAxisLbl.setAttribute("text-anchor", "middle");
  xAxisLbl.textContent = "LAP";
  svg.appendChild(xAxisLbl);

  // --- Lines per driver ---
  for (const d of hasLaps) {
    const pts = [];
    let bestT = Infinity, bestLap = -1;
    for (const l of d.laps) {
      const v = valueOf(l);
      if (v == null) continue;
      if (!isGap && v <= 0) continue;
      pts.push([l.lap, v, l]);
      if (!isPos && !isGap && l.lap_time && l.lap_time < bestT) {
        bestT = l.lap_time; bestLap = l.lap;
      }
    }
    if (!pts.length) continue;

    // Path
    let pathD = '';
    if (isPos) {
      // Step-after for position so the chart looks like a staircase
      // (positions change at the moment the driver crosses S/F).
      for (let i = 0; i < pts.length; i++) {
        const [lp, vl] = pts[i];
        const x = xPx(lp), y = yPx(vl);
        if (i === 0) pathD += `M ${x} ${y}`;
        else {
          const [, vlPrev] = pts[i-1];
          pathD += ` H ${x} V ${yPx(vl)}`;
        }
      }
    } else {
      for (let i = 0; i < pts.length; i++) {
        const [lp, vl] = pts[i];
        const x = xPx(lp), y = yPx(vl);
        pathD += (i === 0 ? `M ${x} ${y}` : ` L ${x} ${y}`);
      }
    }
    const line = document.createElementNS(SVG_NS, "path");
    line.setAttribute("d", pathD);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", d.color);
    line.setAttribute("stroke-width", "2");
    line.setAttribute("stroke-linejoin", "round");
    line.setAttribute("stroke-linecap", "round");
    svg.appendChild(line);

    // Points
    for (const [lp, vl, lapEv] of pts) {
      const x = xPx(lp), y = yPx(vl);
      const isPit = !!lapEv.on_pit;
      const isBest = !isPos && lp === bestLap;
      const r = isBest ? 4 : 3;
      const dot = document.createElementNS(SVG_NS, "circle");
      dot.setAttribute("cx", x);
      dot.setAttribute("cy", y);
      dot.setAttribute("r", r);
      dot.setAttribute("fill", isBest ? "#ffd166" : d.color);
      dot.setAttribute("stroke", "#0a0a0f");
      dot.setAttribute("stroke-width", "1");
      svg.appendChild(dot);
      if (isPit) {
        // Small wrench-ish dot on top to mark a pit lap
        const pitDot = document.createElementNS(SVG_NS, "circle");
        pitDot.setAttribute("cx", x);
        pitDot.setAttribute("cy", y - 8);
        pitDot.setAttribute("r", 2);
        pitDot.setAttribute("fill", "#ffd166");
        svg.appendChild(pitDot);
      }
    }
  }

  area.appendChild(svg);

  // Legend
  const legend = document.createElement('div');
  legend.className = 'legend';
  for (const d of hasLaps) {
    const item = document.createElement('span');
    item.className = 'legend-item';
    item.innerHTML =
      `<span class="legend-swatch" style="background:${d.color}"></span>` +
      `<span class="legend-name">${esc(d.name)}</span>` +
      `<span class="legend-num">#${esc(d.car_number)}</span>`;
    legend.appendChild(item);
  }
  area.appendChild(legend);
}

// "Nice" axis ticks for floating-point values (lap times).
function makeNiceTicks(lo, hi, n) {
  const range = hi - lo;
  if (range <= 0) return [lo];
  const rough = range / (n - 1);
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  let step;
  if (norm < 1.5) step = mag;
  else if (norm < 3) step = 2 * mag;
  else if (norm < 7) step = 5 * mag;
  else step = 10 * mag;
  const start = Math.ceil(lo / step) * step;
  const out = [];
  for (let v = start; v <= hi + step * 0.001; v += step) out.push(v);
  return out;
}
function makeIntegerTicks(lo, hi, n) {
  const range = Math.max(1, Math.ceil(hi) - Math.floor(lo));
  const step = Math.max(1, Math.ceil(range / (n - 1)));
  const out = [];
  for (let v = Math.floor(lo); v <= Math.ceil(hi); v += step) out.push(v);
  if (out[out.length - 1] !== Math.ceil(hi)) out.push(Math.ceil(hi));
  return out;
}

async function refresh() {
  try {
    const r = await fetch('/chart/state');
    const s = await r.json();
    document.getElementById('chart-title').textContent =
      s.chart_type === 'position' ? 'POSITION' :
      s.chart_type === 'gap'      ? 'GAP TO LEADER' :
                                     'LAP TIMES';
    document.getElementById('chart-meta').textContent =
      [s.track, s.session_name].filter(x => x).join(' · ');
    drawChart(s);
  } catch (e) { /* keep last view */ }
  setTimeout(refresh, 1000);
}
refresh();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTML — public share: chart with built-in driver picker
# ---------------------------------------------------------------------------
# Stateless. Driver selection lives in URL params (?drivers=11,23&type=gap).
# Each remote viewer's selection is independent — no server-side state for
# remote viewers. Mobile-friendly responsive layout.
SHARE_CHART_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live Race Chart</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0a0a0f; color: #e8e8ea;
    padding: 14px;
    font-variant-numeric: tabular-nums;
  }
  .wrap { max-width: 900px; margin: 0 auto; display: grid; gap: 12px; }
  .card {
    background: #14141c; border: 1px solid #26262f;
    border-radius: 10px; padding: 12px 14px;
  }
  h1 {
    font-size: 16px; color: #ff6b35; letter-spacing: 1px;
    font-weight: 800;
  }
  .meta {
    font-size: 11px; color: #7a7a90; margin-top: 4px;
  }

  .controls {
    display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
    margin-top: 10px;
  }
  .seg {
    display: inline-flex; background: #1b1b26;
    border: 1px solid #2e2e3d; border-radius: 6px; overflow: hidden;
  }
  .seg button {
    background: transparent; border: none;
    color: #c8c8d8; font-size: 11px; font-weight: 700;
    padding: 6px 12px; cursor: pointer; letter-spacing: 0.5px;
  }
  .seg button.active { background: #ff6b35; color: #0a0a0f; }
  button.act {
    background: #2a2a38; border: 1px solid #3a3a4a;
    color: #c8c8d8; font-size: 11px; font-weight: 700;
    padding: 6px 12px; border-radius: 6px; cursor: pointer;
  }
  button.act:hover { background: #3a3a4a; color: #fff; }

  /* Driver picker */
  .pick-head {
    font-size: 10px; text-transform: uppercase; letter-spacing: 1.5px;
    color: #7a7a90; font-weight: 800; margin-bottom: 8px;
  }
  .pick {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 6px;
    max-height: 200px; overflow-y: auto;
    padding-right: 4px;
  }
  .pick label {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 8px; border-radius: 4px;
    background: #1b1b26; cursor: pointer;
    font-size: 12px;
  }
  .pick label:hover { background: #232331; }
  .pick label.checked {
    background: rgba(255, 107, 53, 0.10);
    box-shadow: inset 4px 0 0 var(--c, #ff6b35);
  }
  .pick input { accent-color: #ff6b35; }
  .pick .num {
    background: #fff; color: #0a0a0f;
    padding: 1px 5px; border-radius: 3px;
    font-size: 10px; font-weight: 800;
    min-width: 30px; text-align: center;
  }
  .pick .name {
    flex: 1; color: #fff; font-weight: 600;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .pick .swatch {
    width: 8px; height: 8px; border-radius: 50%;
  }

  /* Chart card */
  .chart-card {
    background: #14141c; border: 1px solid #26262f;
    border-radius: 10px; padding: 12px 14px;
  }
  .chart-head {
    display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: 6px;
  }
  .chart-title {
    font-size: 12px; text-transform: uppercase; letter-spacing: 1.5px;
    font-weight: 800; color: #ff6b35;
  }
  .live-pill {
    background: #16341f; color: #4ade80;
    border: 1px solid #1d5c30;
    padding: 2px 8px; border-radius: 10px;
    font-size: 10px; font-weight: 800; letter-spacing: 1px;
  }
  .live-pill.idle { background: #1f1f2b; color: #7a7a90; border-color: #2e2e3d; }

  svg.chart {
    width: 100%; height: 360px;
  }
  .legend {
    display: flex; gap: 12px; flex-wrap: wrap;
    margin-top: 6px;
    font-size: 11px;
  }
  .legend-item { display: flex; align-items: center; gap: 4px; }
  .legend-swatch { width: 14px; height: 3px; border-radius: 2px; }
  .legend-name { color: #c8c8d8; font-weight: 600; }
  .legend-num  { color: #7a7a90; font-weight: 500; margin-left: 2px; }
  .empty {
    text-align: center; padding: 60px 20px; color: #7a7a90;
    font-size: 12px;
  }

  .footer {
    text-align: center; font-size: 10px; color: #4a4a5a;
    margin-top: 4px;
  }
</style>
</head>
<body>

<div class="wrap">

  <!-- Header -->
  <div class="card">
    <h1 id="header-title">Live Race Chart</h1>
    <div class="meta" id="header-meta">connecting…</div>
    <div class="controls">
      <div class="seg" id="type-seg">
        <button data-type="laptime" class="active">Lap times</button>
        <button data-type="position">Position</button>
        <button data-type="gap">Gap to leader</button>
      </div>
      <button class="act" onclick="clearAll()">Clear</button>
    </div>
  </div>

  <!-- Driver picker -->
  <div class="card">
    <div class="pick-head" id="pick-head">Pick drivers (up to 5)</div>
    <div class="pick" id="picker">loading drivers…</div>
  </div>

  <!-- Chart -->
  <div class="chart-card">
    <div class="chart-head">
      <span class="chart-title" id="chart-title">LAP TIMES</span>
      <span class="live-pill idle" id="live-pill">idle</span>
    </div>
    <div id="chart-area">
      <div class="empty">Select one or more drivers above to populate the chart.</div>
    </div>
  </div>

  <div class="footer">
    Live data via iRacing Race Logger · selection saved in URL — share this page to share your view.
  </div>
</div>

<script>
const SVG_NS = "http://www.w3.org/2000/svg";
const MAX_PICKED = 5;

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}
function fmtLap(t) {
  if (!t || t <= 0) return '—';
  const m = Math.floor(t/60);
  const s = t - m*60;
  return m ? `${m}:${s.toFixed(2).padStart(5,'0')}` : s.toFixed(2);
}

// --- URL state management -------------------------------------------------
function readState() {
  const url = new URL(location.href);
  const drivers = (url.searchParams.get('drivers') || '')
    .split(',').map(s => s.trim()).filter(Boolean);
  const type = (url.searchParams.get('type') || 'laptime').toLowerCase();
  const validType = ['laptime','position','gap'].includes(type) ? type : 'laptime';
  return { drivers, type: validType };
}
function writeState(drivers, type) {
  const url = new URL(location.href);
  if (drivers.length) url.searchParams.set('drivers', drivers.join(','));
  else url.searchParams.delete('drivers');
  url.searchParams.set('type', type);
  history.replaceState(null, '', url.toString());
}

// --- Picker rendering -----------------------------------------------------
function renderPicker(allDrivers, picked) {
  const el = document.getElementById('picker');
  if (!allDrivers.length) {
    el.innerHTML = '<div style="grid-column:1/-1;color:#7a7a90;text-align:center;">' +
      'No drivers in session. Wait for the race to load.</div>';
    return;
  }
  // Sort by car number
  const sorted = [...allDrivers].sort((a, b) => {
    const an = String(a.car_number);
    const bn = String(b.car_number);
    const ai = parseInt(an, 10);
    const bi = parseInt(bn, 10);
    if (!isNaN(ai) && !isNaN(bi)) return ai - bi;
    return an.localeCompare(bn);
  });
  let html = '';
  for (const d of sorted) {
    const checked = picked.has(String(d.car_number));
    html += `
      <label class="${checked ? 'checked' : ''}" style="--c:${d.color};">
        <input type="checkbox" data-num="${esc(d.car_number)}" ${checked ? 'checked' : ''}>
        <span class="swatch" style="background:${d.color}"></span>
        <span class="num">#${esc(d.car_number)}</span>
        <span class="name">${esc(d.name)}</span>
      </label>`;
  }
  el.innerHTML = html;
  el.querySelectorAll('input').forEach(inp => {
    inp.addEventListener('change', e => {
      const num = inp.dataset.num;
      const state = readState();
      const set = new Set(state.drivers);
      if (inp.checked) {
        set.add(num);
        if (set.size > MAX_PICKED) {
          // Drop the oldest selection (first in URL order)
          const arr = state.drivers.filter(x => x !== num);
          arr.push(num);
          while (arr.length > MAX_PICKED) arr.shift();
          writeState(arr, state.type);
        } else {
          state.drivers.push(num);
          writeState(state.drivers, state.type);
        }
      } else {
        const arr = state.drivers.filter(x => x !== num);
        writeState(arr, state.type);
      }
      refresh();
    });
  });
}

// --- Type segmented control -----------------------------------------------
document.querySelectorAll('#type-seg button').forEach(btn => {
  btn.addEventListener('click', () => {
    const state = readState();
    writeState(state.drivers, btn.dataset.type);
    refresh();
  });
});
function syncTypeButtons(type) {
  document.querySelectorAll('#type-seg button').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.type === type);
  });
}
function clearAll() {
  const state = readState();
  writeState([], state.type);
  refresh();
}

// --- Chart drawing (same approach as /chart/render) -----------------------
function drawChart(state) {
  const area = document.getElementById('chart-area');
  area.innerHTML = '';

  const sel = state.selected || [];
  if (!sel.length) {
    area.innerHTML = '<div class="empty">Select one or more drivers above to populate the chart.</div>';
    return;
  }
  const hasLaps = sel.filter(d => (d.laps || []).length > 0);
  if (!hasLaps.length) {
    area.innerHTML = '<div class="empty">Selected drivers haven\\'t completed any laps yet.</div>';
    return;
  }

  const isPos = state.chart_type === 'position';
  const isGap = state.chart_type === 'gap';
  const inverted = isPos || isGap;
  function valueOf(l) {
    if (isPos) return l.position;
    if (isGap) return (l.gap_to_leader == null ? 0 : l.gap_to_leader);
    return l.lap_time;
  }

  let lapMin = Infinity, lapMax = -Infinity;
  let valMin = Infinity, valMax = -Infinity;
  for (const d of hasLaps) {
    for (const l of d.laps) {
      if (l.lap < lapMin) lapMin = l.lap;
      if (l.lap > lapMax) lapMax = l.lap;
      const v = valueOf(l);
      if (v == null) continue;
      if (!isGap && v <= 0) continue;
      if (v < valMin) valMin = v;
      if (v > valMax) valMax = v;
    }
  }
  if (!isFinite(lapMin) || !isFinite(valMin)) {
    area.innerHTML = '<div class="empty">No usable data points yet.</div>';
    return;
  }
  if (lapMax === lapMin) lapMax = lapMin + 1;
  let padTop, padBot;
  if (isPos) {
    valMin = Math.max(1, Math.floor(valMin));
    valMax = Math.ceil(valMax);
    padTop = padBot = 0.5;
  } else if (isGap) {
    valMin = 0;
    const range = valMax || 1;
    padTop = range * 0.08;
    padBot = 0;
  } else {
    const range = valMax - valMin || 1;
    padTop = padBot = range * 0.08;
  }

  const W = 880, H = 360;
  const M = { l: 56, r: 16, t: 10, b: 32 };
  const innerW = W - M.l - M.r;
  const innerH = H - M.t - M.b;
  function xPx(lap) { return M.l + ((lap - lapMin) / (lapMax - lapMin)) * innerW; }
  function yPx(val) {
    const v0 = valMin - padBot, v1 = valMax + padTop;
    const t = (val - v0) / (v1 - v0);
    if (inverted) return M.t + t * innerH;
    return M.t + (1 - t) * innerH;
  }

  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "chart");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");

  // Y-axis
  const yTicks = isPos
    ? makeIntegerTicks(valMin, valMax, 6)
    : makeNiceTicks(valMin - padBot, valMax + padTop, 5);
  for (const t of yTicks) {
    const y = yPx(t);
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", M.l); line.setAttribute("x2", W - M.r);
    line.setAttribute("y1", y); line.setAttribute("y2", y);
    line.setAttribute("stroke", "#26262f"); line.setAttribute("stroke-dasharray", "2 3");
    svg.appendChild(line);
    const lbl = document.createElementNS(SVG_NS, "text");
    lbl.setAttribute("x", M.l - 8); lbl.setAttribute("y", y + 4);
    lbl.setAttribute("fill", "#7a7a90"); lbl.setAttribute("font-size", "10");
    lbl.setAttribute("text-anchor", "end");
    lbl.textContent =
      isPos ? `P${t}` :
      isGap ? (t === 0 ? 'LEAD' : `+${t.toFixed(1)}s`) :
      fmtLap(t);
    svg.appendChild(lbl);
  }

  // X-axis
  const xTicks = makeIntegerTicks(lapMin, lapMax, 8);
  for (const t of xTicks) {
    const x = xPx(t);
    const lbl = document.createElementNS(SVG_NS, "text");
    lbl.setAttribute("x", x); lbl.setAttribute("y", H - 14);
    lbl.setAttribute("fill", "#7a7a90"); lbl.setAttribute("font-size", "10");
    lbl.setAttribute("text-anchor", "middle");
    lbl.textContent = `L${t}`;
    svg.appendChild(lbl);
  }
  const xL = document.createElementNS(SVG_NS, "text");
  xL.setAttribute("x", M.l + innerW/2); xL.setAttribute("y", H - 2);
  xL.setAttribute("fill", "#5a5a6a"); xL.setAttribute("font-size", "10");
  xL.setAttribute("text-anchor", "middle"); xL.textContent = "LAP";
  svg.appendChild(xL);

  // Lines per driver
  for (const d of hasLaps) {
    const pts = [];
    let bestT = Infinity, bestLap = -1;
    for (const l of d.laps) {
      const v = valueOf(l);
      if (v == null) continue;
      if (!isGap && v <= 0) continue;
      pts.push([l.lap, v, l]);
      if (!isPos && !isGap && l.lap_time && l.lap_time < bestT) {
        bestT = l.lap_time; bestLap = l.lap;
      }
    }
    if (!pts.length) continue;
    let pathD = '';
    if (isPos) {
      for (let i = 0; i < pts.length; i++) {
        const [lp, vl] = pts[i];
        const x = xPx(lp), y = yPx(vl);
        if (i === 0) pathD += `M ${x} ${y}`;
        else pathD += ` H ${x} V ${y}`;
      }
    } else {
      for (let i = 0; i < pts.length; i++) {
        const [lp, vl] = pts[i];
        const x = xPx(lp), y = yPx(vl);
        pathD += (i === 0 ? `M ${x} ${y}` : ` L ${x} ${y}`);
      }
    }
    const line = document.createElementNS(SVG_NS, "path");
    line.setAttribute("d", pathD); line.setAttribute("fill", "none");
    line.setAttribute("stroke", d.color); line.setAttribute("stroke-width", "2.4");
    line.setAttribute("stroke-linejoin", "round"); line.setAttribute("stroke-linecap", "round");
    svg.appendChild(line);
    for (const [lp, vl, lapEv] of pts) {
      const x = xPx(lp), y = yPx(vl);
      const isPit = !!lapEv.on_pit;
      const isBest = !isPos && !isGap && lp === bestLap;
      const r = isBest ? 4.5 : 3.5;
      const dot = document.createElementNS(SVG_NS, "circle");
      dot.setAttribute("cx", x); dot.setAttribute("cy", y); dot.setAttribute("r", r);
      dot.setAttribute("fill", isBest ? "#ffd166" : d.color);
      dot.setAttribute("stroke", "#0a0a0f"); dot.setAttribute("stroke-width", "1");
      svg.appendChild(dot);
      if (isPit) {
        const pd = document.createElementNS(SVG_NS, "circle");
        pd.setAttribute("cx", x); pd.setAttribute("cy", y - 9);
        pd.setAttribute("r", 2.2); pd.setAttribute("fill", "#ffd166");
        svg.appendChild(pd);
      }
    }
  }
  area.appendChild(svg);

  const legend = document.createElement('div');
  legend.className = 'legend';
  for (const d of hasLaps) {
    const item = document.createElement('span');
    item.className = 'legend-item';
    item.innerHTML =
      `<span class="legend-swatch" style="background:${d.color}"></span>` +
      `<span class="legend-name">${esc(d.name)}</span>` +
      `<span class="legend-num">#${esc(d.car_number)}</span>`;
    legend.appendChild(item);
  }
  area.appendChild(legend);
}

function makeNiceTicks(lo, hi, n) {
  const range = hi - lo;
  if (range <= 0) return [lo];
  const rough = range / (n - 1);
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  let step;
  if (norm < 1.5) step = mag;
  else if (norm < 3) step = 2 * mag;
  else if (norm < 7) step = 5 * mag;
  else step = 10 * mag;
  const start = Math.ceil(lo / step) * step;
  const out = [];
  for (let v = start; v <= hi + step * 0.001; v += step) out.push(v);
  return out;
}
function makeIntegerTicks(lo, hi, n) {
  const range = Math.max(1, Math.ceil(hi) - Math.floor(lo));
  const step = Math.max(1, Math.ceil(range / (n - 1)));
  const out = [];
  for (let v = Math.floor(lo); v <= Math.ceil(hi); v += step) out.push(v);
  if (out[out.length - 1] !== Math.ceil(hi)) out.push(Math.ceil(hi));
  return out;
}

// --- Refresh loop ---------------------------------------------------------
async function refresh() {
  const state = readState();
  const params = new URLSearchParams();
  if (state.drivers.length) params.set('drivers', state.drivers.join(','));
  params.set('type', state.type);
  try {
    const r = await fetch('/share/data?' + params.toString());
    const d = await r.json();
    document.getElementById('header-title').textContent =
      [d.track, d.track_config].filter(x => x).join(' — ') || 'Live Race Chart';
    document.getElementById('header-meta').textContent =
      d.session_name ? d.session_name : (d.session_type || 'Waiting…');
    syncTypeButtons(d.chart_type);
    document.getElementById('chart-title').textContent =
      d.chart_type === 'position' ? 'POSITION' :
      d.chart_type === 'gap'      ? 'GAP TO LEADER' :
                                     'LAP TIMES';
    const pill = document.getElementById('live-pill');
    pill.textContent = d.logging ? 'LIVE' : 'idle';
    pill.classList.toggle('idle', !d.logging);
    renderPicker(d.all_drivers || [], new Set(state.drivers));
    drawChart(d);
  } catch (e) { /* keep last view */ }
  setTimeout(refresh, 2000);
}
refresh();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTML — public share: live standings table
# ---------------------------------------------------------------------------
SHARE_STANDINGS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live Standings</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0a0a0f; color: #e8e8ea;
    padding: 12px;
    font-variant-numeric: tabular-nums;
  }
  .wrap { max-width: 760px; margin: 0 auto; display: grid; gap: 10px; }
  .card {
    background: #14141c; border: 1px solid #26262f; border-radius: 10px;
    padding: 12px 14px;
  }
  h1 { font-size: 16px; color: #ff6b35; letter-spacing: 1px; font-weight: 800; }
  .meta { font-size: 11px; color: #7a7a90; margin-top: 4px; }

  .wrap { max-width: 920px; }
  .row {
    /* POS | # | DRIVER | LAST | BEST | GAP | INC | +/- | PIT TIME */
    display: grid;
    grid-template-columns: 36px 46px 1fr 78px 78px 64px 38px 44px 64px;
    align-items: center;
    padding: 7px 10px;
    border-bottom: 1px solid #1d1d27;
    font-size: 13px;
  }
  .row.head {
    background: #1b1b26;
    font-size: 10px; text-transform: uppercase; letter-spacing: 1px;
    color: #7a7a90; font-weight: 800;
    padding: 8px 10px;
  }
  .pos { font-weight: 800; font-size: 15px; color: #fff; text-align: center; }
  .pos.p1 { color: #ffd166; }
  .pos.p2 { color: #c0c0d0; }
  .pos.p3 { color: #cd7f32; }
  .num {
    background: #fff; color: #0a0a0f; padding: 1px 6px;
    border-radius: 3px; font-size: 11px; font-weight: 800;
    min-width: 36px; text-align: center;
  }
  .name {
    font-weight: 600; color: #fff;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    padding-right: 8px;
  }
  .name .sub {
    display: block; font-size: 10px; color: #7a7a90; font-weight: 500;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .name .pit-flag {
    display: inline-block;
    background: #3a1a1a; color: #ff8888; border: 1px solid #5c2a2a;
    padding: 0 5px; border-radius: 3px;
    font-size: 9px; font-weight: 800;
    margin-left: 6px; vertical-align: middle;
  }
  .time { text-align: right; color: #c8c8d8; }
  .best { text-align: right; color: #ffd166; font-weight: 600; }
  .gap  { text-align: right; color: #c8c8d8; font-weight: 600; }
  .inc  { text-align: right; color: #ff9f5a; font-weight: 600; }
  .ot   { text-align: right; font-weight: 700; font-size: 12px; }
  .ot.up   { color: #4ade80; }
  .ot.down { color: #f87171; }
  .ot.flat { color: #6a6a78; }
  .pit-time { text-align: right; color: #c8a8ff; font-weight: 600; font-size: 12px; }

  .row.out { opacity: 0.4; }
  .empty { padding: 60px 20px; text-align: center; color: #7a7a90; font-size: 12px; }

  /* Mid-size: drop pit-time column */
  @media (max-width: 760px) {
    .row {
      grid-template-columns: 34px 42px 1fr 70px 70px 60px 36px 40px;
      padding: 6px 10px;
      font-size: 12px;
    }
    .col-pit-time { display: none; }
  }
  /* Mobile: also drop +/- and INC */
  @media (max-width: 540px) {
    .row {
      grid-template-columns: 30px 40px 1fr 64px 64px 56px;
      padding: 6px 8px;
      font-size: 12px;
    }
    .col-inc, .col-ot, .col-pit-time { display: none; }
  }
</style>
</head>
<body>

<div class="wrap">
  <div class="card">
    <h1 id="title">Live Standings</h1>
    <div class="meta" id="meta">connecting…</div>
  </div>
  <div class="card" style="padding: 0;">
    <div class="row head" id="header-row">
      <div>POS</div>
      <div>#</div>
      <div>DRIVER</div>
      <div style="text-align:right;">LAST LAP</div>
      <div style="text-align:right;">BEST</div>
      <div style="text-align:right;" id="hdr-gap">GAP</div>
      <div class="col-inc" style="text-align:right;" id="hdr-extra1" title="Incidents">INC</div>
      <div class="col-ot" style="text-align:right;" id="hdr-extra2" title="Positions gained / lost">+/&minus;</div>
      <div class="col-pit-time" style="text-align:right;" id="hdr-extra3" title="Last pit-stop duration">PIT TIME</div>
    </div>
    <div id="rows"><div class="empty">Waiting for race data…</div></div>
  </div>
  <div style="text-align:center;font-size:10px;color:#4a4a5a;">
    Live data via iRacing Race Logger
  </div>
</div>

<script>
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}
function fmtLap(t) {
  if (!t || t <= 0) return '—';
  const m = Math.floor(t/60); const s = t - m*60;
  return m ? `${m}:${s.toFixed(2).padStart(5,'0')}` : s.toFixed(2);
}
function fmtGap(g) {
  if (g == null || g <= 0) return '—';
  if (g < 60) return '+' + g.toFixed(g < 10 ? 3 : 2);
  const m = Math.floor(g/60); const s = g - m*60;
  return `+${m}:${s.toFixed(2).padStart(5,'0')}`;
}
function fmtPitDur(t) {
  if (t == null || t <= 0) return '—';
  // Pit stops are typically 20-90 seconds. Show with one decimal.
  if (t < 60) return t.toFixed(1) + 's';
  const m = Math.floor(t/60); const s = t - m*60;
  return `${m}:${s.toFixed(1).padStart(4,'0')}`;
}
function fmtOvertakes(up, down) {
  // Net positions gained/lost — green up arrow, red down arrow,
  // grey dash if neither.
  const net = (up || 0) - (down || 0);
  if (net > 0) return `<span class="ot up">▲${net}</span>`;
  if (net < 0) return `<span class="ot down">▼${-net}</span>`;
  return `<span class="ot flat">—</span>`;
}

function fmtPosDelta(d) {
  // Practice/quali: delta from first observed session-rank.
  // +N green ▲ = climbed N spots; -N red ▼ = dropped; 0 grey dash.
  const n = d || 0;
  if (n > 0) return `<span class="ot up">▲${n}</span>`;
  if (n < 0) return `<span class="ot down">▼${-n}</span>`;
  return `<span class="ot flat">—</span>`;
}

async function refresh() {
  try {
    const r = await fetch('/share/standings/data');
    const d = await r.json();
    document.getElementById('title').textContent =
      [d.track, d.track_config].filter(x => x).join(' — ') || 'Live Standings';
    document.getElementById('meta').textContent =
      d.session_name || d.session_type || 'Waiting…';

    // Session-type-aware column relabelling.
    // Race: GAP column = gap to leader; extras = INC, +/-, PIT TIME.
    // Practice / Qualifying: GAP column = gap to fastest session lap;
    // extras = LAPS (laps completed), Δ POS (positions gained since
    // first hot lap). Pit-time and incidents aren't meaningful enough
    // in non-race sessions to justify the column space.
    const stype = (d.session_type || '').toLowerCase();
    const isRace = stype.includes('race');
    const hdrGap = document.getElementById('hdr-gap');
    const hdrE1  = document.getElementById('hdr-extra1');
    const hdrE2  = document.getElementById('hdr-extra2');
    const hdrE3  = document.getElementById('hdr-extra3');
    if (isRace) {
      hdrGap.textContent = 'GAP';
      hdrGap.title = 'Gap to leader (race)';
      hdrE1.textContent = 'INC';
      hdrE1.title = 'Incident points';
      hdrE2.innerHTML = '+/&minus;';
      hdrE2.title = 'Positions gained / lost';
      hdrE3.textContent = 'PIT TIME';
      hdrE3.title = 'Last pit-stop duration';
    } else {
      hdrGap.textContent = 'GAP';
      hdrGap.title = 'Gap to fastest session lap';
      hdrE1.textContent = 'LAPS';
      hdrE1.title = 'Laps completed in this session';
      hdrE2.innerHTML = 'Δ POS';
      hdrE2.title = 'Positions gained since first hot lap';
      hdrE3.textContent = '';
      hdrE3.title = '';
    }

    const rowsEl = document.getElementById('rows');
    const drivers = d.drivers || [];
    if (!drivers.length) {
      rowsEl.innerHTML = '<div class="empty">No drivers in session.</div>';
    } else {
      let html = '';
      for (const r of drivers) {
        // For race rows we show iRacing's CarIdxPosition. In quali /
        // practice that's stale or zero, so we show session_rank
        // (rank by best lap time) instead.
        const displayPos = isRace
          ? (r.position || '—')
          : (r.session_rank || '—');
        const posCls = displayPos === 1 ? 'p1' :
                       displayPos === 2 ? 'p2' :
                       displayPos === 3 ? 'p3' : '';
        const rowCls = !r.in_world ? 'out' : '';
        const sub = [r.car_class, r.car].filter(x => x).join(' · ');
        const pitFlag = r.on_pit ? '<span class="pit-flag">PIT</span>' : '';

        // Choose the appropriate gap value per session type.
        const gapVal = isRace ? r.gap_to_leader : r.gap_to_session_best;

        // Extras: INC + overtakes + pit-time for race;
        //         laps + position-delta + (blank) for quali / practice.
        const inc = (r.incidents != null && r.incidents > 0)
          ? `${r.incidents}x` : '0x';
        const extra1 = isRace
          ? `<div class="inc col-inc">${inc}</div>`
          : `<div class="inc col-inc">${r.lap || 0}</div>`;
        const extra2 = isRace
          ? `<div class="col-ot">${fmtOvertakes(r.overtakes, r.overtaken)}</div>`
          : `<div class="col-ot">${fmtPosDelta(r.session_position_delta)}</div>`;
        const extra3 = isRace
          ? `<div class="pit-time col-pit-time">${fmtPitDur(r.last_pit_duration)}</div>`
          : `<div class="pit-time col-pit-time"></div>`;

        html += `
          <div class="row ${rowCls}">
            <div class="pos ${posCls}">${displayPos}</div>
            <div><span class="num">#${esc(r.car_number || '—')}</span></div>
            <div class="name">${esc(r.name || '—')}${pitFlag}${sub ? `<span class="sub">${esc(sub)}</span>` : ''}</div>
            <div class="time">${fmtLap(r.last_lap)}</div>
            <div class="best">${fmtLap(r.best_lap)}</div>
            <div class="gap">${fmtGap(gapVal)}</div>
            ${extra1}
            ${extra2}
            ${extra3}
          </div>`;
      }
      rowsEl.innerHTML = html;
    }
  } catch (e) {}
  setTimeout(refresh, 2000);
}
refresh();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("iRacing Race Logger")
    print(f"Logs folder: {LOGS_DIR}")
    print(f"Open:        http://localhost:5009")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    t = threading.Thread(target=poller.run, daemon=True)
    t.start()
    try:
        app.run(host="0.0.0.0", port=5009, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        # Shutdown order matters:
        #   1. poller.stop() — our RaceLogger.stop() override writes a
        #      provisional session_end (if not already written) BEFORE
        #      the base class shuts down the SDK. After ir.shutdown()
        #      we can't read ResultsPositions anymore.
        #   2. _close_log() — flushes + closes the file. session_end
        #      has already been written so it just does the file ops.
        poller.stop()
        if poller._log_fp:
            poller._close_log()


if __name__ == "__main__":
    main()
