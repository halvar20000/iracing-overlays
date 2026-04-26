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
from flask import Flask, jsonify, render_template_string, send_file, abort

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
        self._prev_session_flags = 0
        self._prev_car_session_flags.clear()
        self._lap_history.clear()

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
            })
        # Sort: in-world cars first, then by position (unassigned cars at the bottom)
        out.sort(key=lambda r: (
            0 if r["in_world"] else 1,
            r["position"] if r["position"] > 0 else 9999,
        ))
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
        }


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)
poller = RaceLogger()


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
    let html = '';
    for (const r of drivers) {
      const posCls = r.position === 1 ? 'p1' :
                     r.position === 2 ? 'p2' :
                     r.position === 3 ? 'p3' : '';
      const rowCls = !r.in_world ? 'out' : (r.on_pit ? 'pit-row' : '');
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
        <div class="drv-row ${rowCls}">
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
