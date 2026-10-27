"""Bridge to the real iRacing telemetry SDK (via ``pyirsdk``).

Only works on Windows with iRacing running.  On any other platform - or when
``pyirsdk`` is not installed - :meth:`connect` simply returns ``False`` and the
application falls back to the simulator.

What this source reads reliably from the SDK
---------------------------------------------
* Live timing for every car: position, lap, lap-distance %, last/best lap,
  pit-road state, track surface, gaps and intervals.
* Session: type, state, time/laps remaining, flags, weather.

Incidents (backend/incidents.py)
--------------------------------
* official points per car from ``ResultsPositions[].Incidents`` - live only
  when this PC is an ADMIN of the session (a spectator gets them at the
  finish), read every ``INC_POLL_S``;
* spins / contacts / stopped cars detected from car motion, always.

Replays: while iRacing shows a replay (``IsReplayPlaying`` and behind the
live tip) the frame is flagged ``in_replay`` - the engine logs nothing from
it.  ``replay_event`` jumps the sim's replay to an incident.
"""

from __future__ import annotations

import contextlib
import io
import threading
import time
from typing import Optional

from backend.models import (
    CarFrame, Frame, SessionFrame, Weather,
    SURFACE_ON_TRACK, SURFACE_OFF_TRACK, SURFACE_IN_PIT_STALL,
    SURFACE_APPROACHING_PITS, SURFACE_NOT_IN_WORLD, IncidentEvent,
)
from backend.sources.base import DataSource
from backend.incidents import BehaviourDetector, OfficialIncidents
from backend.tracks import load_track

# Sector timing is shared with the Quali Delta overlay (repo root).
import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(
    _os.path.dirname(_os.path.abspath(__file__)))))
if _ROOT not in _sys.path:
    _sys.path.append(_ROOT)
try:
    from field_sectors import FieldSectors
except Exception:                       # racecontrol copied on its own
    FieldSectors = None
from backend.car_brands import detect_brand

try:                                  # pyirsdk is Windows-only in practice
    import irsdk                      # type: ignore
    _HAVE_IRSDK = True
except Exception:                     # pragma: no cover - non-Windows / missing
    irsdk = None                      # type: ignore
    _HAVE_IRSDK = False


# iRacing enum values (kept local so the module imports even without irsdk).
_SURFACE_MAP = {
    -1: SURFACE_NOT_IN_WORLD, 0: SURFACE_OFF_TRACK, 1: SURFACE_IN_PIT_STALL,
    2: SURFACE_APPROACHING_PITS, 3: SURFACE_ON_TRACK,
}
_SESSION_STATE_MAP = {
    0: "INVALID", 1: "GET IN CAR", 2: "WARMUP", 3: "PARADE LAPS",
    4: "RACING", 5: "CHECKERED", 6: "COOLDOWN",
}
# SessionFlags bitfield -> our flag vocabulary.
_FLAG_BITS = [
    (0x00000001, "checkered"), (0x00000002, "white"), (0x00000004, "green"),
    (0x00000008, "yellow"), (0x00000010, "red"), (0x00000020, "blue"),
    (0x00000040, "debris"), (0x00000080, "crossed"),
    (0x00000100, "yellow"), (0x00004000, "caution"),
    (0x00008000, "caution"), (0x00010000, "black"),
    (0x00020000, "disqualify"), (0x00080000, "furled"),
]


INC_POLL_S = 2.0          # ResultsPositions = a full session-info parse
REPLAY_PREROLL_S = 8.0    # replay starts this long before the incident
REPLAY_BEHIND_FRAMES = 60  # > 1 s behind the live tip = watching a replay


def replay_target(t_event: float, preroll: float) -> int:
    """Replay seek target in ms (never before the session start)."""
    return int(max(0.0, t_event - preroll) * 1000)


def pick_cam_group(groups: list[dict], current: int) -> int:
    """Keep the operator's camera group, never Scenic (falls back to TV1,
    then the first other group)."""
    by_num = {int(g.get("GroupNum", -1)): str(g.get("GroupName", ""))
              for g in groups}
    if current in by_num and "scenic" not in by_num[current].lower():
        return current
    for num, name in by_num.items():
        if name.replace(" ", "").lower() == "tv1":
            return num
    for num, name in by_num.items():
        if "scenic" not in name.lower():
            return num
    return current


def class_labels(drivers: list) -> dict[int, str]:
    """CarIdx -> class label.  Hosted / AI sessions often send NO
    CarClassShortName (YAML null - str() made it "None"): then a class with
    one car model is named after the model, a mixed one after the words all
    its models share ("GT3"), else "Class <id>"."""
    def txt(v) -> str:
        return "" if v is None else str(v).strip()
    models: dict[int, set] = {}
    for d in drivers:
        if d.get("CarIsPaceCar", 0) == 1:
            continue
        models.setdefault(int(d.get("CarClassID", 0) or 0), set()).add(
            txt(d.get("CarScreenNameShort")) or txt(d.get("CarScreenName")))
    derived: dict[int, str] = {}
    for cid, names in models.items():
        names.discard("")
        if len(names) == 1:
            derived[cid] = next(iter(names))
        elif names:
            lists = [n.split() for n in names]
            common = [w for w in lists[0] if all(w in l for l in lists[1:])]
            derived[cid] = " ".join(common) or f"Class {cid}"
        else:
            derived[cid] = f"Class {cid}"
    out = {}
    for d in drivers:
        idx = int(d.get("CarIdx", -1))
        cid = int(d.get("CarClassID", 0) or 0)
        out[idx] = txt(d.get("CarClassShortName")) or derived.get(cid, "")
    return out


class IRacingSource(DataSource):
    """Reads live data from a running copy of iRacing."""

    name = "iracing"

    def __init__(self) -> None:
        self._ir = None
        self._connected = False
        self._track_path: Optional[list] = None     # real circuit, or None
        self._pit_path: Optional[list] = None
        self._loaded_track: Optional[str] = None
        self._last_poll = time.monotonic()
        self._speed_ms: dict[int, float] = {}
        self._prev_pct: dict[int, float] = {}
        self._command_log: list[str] = []
        self.detector = BehaviourDetector()
        self.official = OfficialIncidents()
        self._inc_next = 0.0
        self._totals: Optional[dict[int, int]] = None
        self._session_num = 0
        self._replay_busy = False
        self.sectors = FieldSectors() if FieldSectors else None
        self.inc_debug: dict = {}
        self._sector_key = None

    # -- DataSource interface ----------------------------------------------

    @staticmethod
    def available() -> bool:
        """Whether the pyirsdk dependency is importable on this machine."""
        return _HAVE_IRSDK

    @staticmethod
    def _quiet_startup(ir) -> bool:
        """Call pyirsdk startup() while suppressing its console chatter."""
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                return bool(ir.startup())
        except Exception:
            return False

    def connect(self) -> bool:
        if not _HAVE_IRSDK:
            return False
        try:
            self._ir = irsdk.IRSDK()
            ok = self._quiet_startup(self._ir)
            self._connected = bool(ok and self._ir.is_connected)
            return self._connected
        except Exception:
            self._connected = False
            return False

    def disconnect(self) -> None:
        try:
            if self._ir is not None:
                self._ir.shutdown()
        except Exception:
            pass
        self._connected = False

    @property
    def is_connected(self) -> bool:
        try:
            return bool(self._ir is not None and self._ir.is_connected)
        except Exception:
            return False

    # -- polling ------------------------------------------------------------

    def poll(self) -> Optional[Frame]:
        if self._ir is None:
            return None
        # Reconnect transparently if iRacing was restarted.
        if not self._ir.is_connected:
            self._quiet_startup(self._ir)
            if not self._ir.is_connected:
                self._connected = False
                return None
        self._connected = True

        now = time.monotonic()
        dt = max(1e-3, now - self._last_poll)
        self._last_poll = now

        try:
            self._ir.freeze_var_buffer_latest()
            frame = self._read_frame(dt)
        except Exception:
            frame = None
        finally:
            try:
                self._ir.unfreeze_var_buffer_latest()
            except Exception:
                pass
        return frame

    def _g(self, name: str, default=None):
        """Safe telemetry / session-info getter."""
        try:
            val = self._ir[name]
            return default if val is None else val
        except Exception:
            return default

    def _read_frame(self, dt: float) -> Frame:
        ir = self._ir

        weekend = self._g("WeekendInfo", {}) or {}
        # Load the real circuit geometry the first time we see this track.
        track_slug = str(weekend.get("TrackName", "") or "")
        if track_slug and track_slug != self._loaded_track:
            self._loaded_track = track_slug
            geo = load_track(track_slug)
            self._track_path = geo["path"] if geo else None
            self._pit_path = geo["pit"] if geo else None
        driver_info = self._g("DriverInfo", {}) or {}
        session_info = self._g("SessionInfo", {}) or {}
        drivers = driver_info.get("Drivers", []) or []

        # Track length (e.g. "5.13 km").
        track_len_km = 5.0
        try:
            track_len_km = float(str(weekend.get("TrackLength", "5 km")).split()[0])
        except Exception:
            pass

        # Current session block.
        cur_num = int(self._g("SessionNum", 0) or 0)
        sessions = session_info.get("Sessions", []) or []
        cur_session = sessions[cur_num] if cur_num < len(sessions) else {}
        session_type = str(cur_session.get("SessionType", "RACE")).upper()
        self._session_num = cur_num
        session_key = "%s-%s" % (weekend.get("SessionID", 0) or 0,
                                 weekend.get("SubSessionID", 0) or 0)
        in_replay = bool(self._g("IsReplayPlaying", False)) and \
            int(self._g("ReplayFrameNumEnd", 0) or 0) > REPLAY_BEHIND_FRAMES

        # Telemetry arrays (indexed by CarIdx).
        pct = self._g("CarIdxLapDistPct", []) or []
        laps = self._g("CarIdxLap", []) or []
        laps_done = self._g("CarIdxLapCompleted", []) or []
        positions = self._g("CarIdxPosition", []) or []
        class_pos = self._g("CarIdxClassPosition", []) or []
        on_pit = self._g("CarIdxOnPitRoad", []) or []
        surface = self._g("CarIdxTrackSurface", []) or []
        last_lap = self._g("CarIdxLastLapTime", []) or []
        best_lap = self._g("CarIdxBestLapTime", []) or []
        f2_time = self._g("CarIdxF2Time", []) or []

        def at(arr, i, default=0):
            return arr[i] if i < len(arr) else default

        # Build cars.
        cars: list[CarFrame] = []
        leader_pct_dist = 0.0
        # First pass: absolute distance for ordering / gaps.
        dist_by_idx: dict[int, float] = {}
        for d in drivers:
            idx = int(d.get("CarIdx", -1))
            if idx < 0 or d.get("CarIsPaceCar", 0) == 1:
                continue
            lc = max(0, int(at(laps_done, idx, 0)))
            p = max(0.0, float(at(pct, idx, 0.0)))
            dist_by_idx[idx] = lc + p
        leader_dist = max(dist_by_idx.values()) if dist_by_idx else 0.0

        # Estimate a pace lap time for gap conversion.
        est_lap = 100.0
        try:
            est_lap = float(driver_info.get("DriverCarEstLapTime", 100.0)) or 100.0
        except Exception:
            pass

        order = sorted(dist_by_idx.items(), key=lambda kv: kv[1], reverse=True)
        rank = {idx: i for i, (idx, _) in enumerate(order)}
        is_race = "RACE" in session_type
        bests = {i: float(at(best_lap, i, 0.0)) for i in dist_by_idx}
        if not is_race:
            # practice / qualifying: ranked by best lap, cars without a time
            # after (by distance); gaps are lap-time gaps to the fastest
            timed = sorted((i for i in dist_by_idx if bests[i] > 0),
                           key=lambda i: bests[i])
            rest = [i for i, _ in order if bests[i] <= 0]
            order = [(i, dist_by_idx[i]) for i in timed + rest]
            rank = {idx: i for i, (idx, _) in enumerate(order)}
        fastest = min((b for b in bests.values() if b > 0), default=0.0)
        labels = class_labels(drivers)

        for d in drivers:
            idx = int(d.get("CarIdx", -1))
            if idx < 0 or d.get("CarIsPaceCar", 0) == 1 or idx not in dist_by_idx:
                continue
            p = max(0.0, float(at(pct, idx, 0.0)))
            # Instantaneous speed from lap-distance delta.
            prev = self._prev_pct.get(idx)
            spd = self._speed_ms.get(idx, 0.0)
            if prev is not None:
                dp = (p - prev) % 1.0
                if dp < 0.5:                       # ignore the S/F wrap glitch
                    inst = dp * track_len_km * 1000.0 / dt
                    spd = spd * 0.7 + inst * 0.3   # smooth
            self._prev_pct[idx] = p
            self._speed_ms[idx] = spd

            this_dist = dist_by_idx[idx]
            r = rank.get(idx, 0)
            interval = 0.0
            if is_race:
                behind = leader_dist - this_dist
                laps_down = int(behind) if behind >= 1.0 else 0
                gap = round((behind - laps_down) * est_lap, 2)
                if r > 0:
                    ahead_idx = order[r - 1][0]
                    interval = round((dist_by_idx[ahead_idx] - this_dist)
                                     * est_lap, 2)
            else:
                laps_down = 0
                gap = round(bests[idx] - fastest, 3) if bests[idx] > 0 else 0.0
                if r > 0 and bests[idx] > 0:
                    interval = round(bests[idx] - bests[order[r - 1][0]], 3)

            ll = float(at(last_lap, idx, 0.0))
            bl = float(at(best_lap, idx, 0.0))
            cars.append(CarFrame(
                car_idx=idx,
                car_number=str(d.get("CarNumber", "0")),
                driver_name=str(d.get("UserName", "")),
                team_name=str(d.get("TeamName", "") or d.get("UserName", "")),
                car_class=labels.get(idx, ""),
                class_id=int(d.get("CarClassID", 0) or 0),
                class_color="#%06x" % (int(d.get("CarClassColor", 0) or 0)
                                       & 0xFFFFFF),
                class_short=labels.get(idx, ""),
                car_brand=detect_brand(d.get("CarPath"), d.get("CarScreenName")),
                car_model=str(d.get("CarScreenName") or ""),
                position=(int(at(positions, idx, r + 1)) or (r + 1))
                if is_race else r + 1,
                class_position=int(at(class_pos, idx, 0)),
                lap=max(0, int(at(laps, idx, 0))),
                laps_completed=max(0, int(at(laps_done, idx, 0))),
                lap_dist_pct=round(p, 5),
                on_pit_road=bool(at(on_pit, idx, False)),
                track_surface=_SURFACE_MAP.get(int(at(surface, idx, 3)),
                                               SURFACE_ON_TRACK),
                last_lap=ll if ll > 0 else 0.0,
                best_lap=bl if bl > 0 else 0.0,
                gap_to_leader=gap, interval=interval, laps_down=laps_down,
                laps_led=0, speed_ms=round(spd, 1),
                is_pace_car=False,
                is_player=(idx == int(driver_info.get("DriverCarIdx", -2))),
                finished=False,
            ))

        # Session-wide state.
        state_id = int(self._g("SessionState", 4) or 4)
        flags_val = int(self._g("SessionFlags", 0) or 0)
        active_flags = []
        for bit, label in _FLAG_BITS:
            if flags_val & bit and label not in active_flags:
                active_flags.append(label)
        if not active_flags:
            active_flags = ["green"]

        start_lights = "off"
        if flags_val & 0x10000000:        # startReady
            start_lights = "ready"
        if flags_val & 0x20000000:        # startSet
            start_lights = "set"
        if flags_val & 0x40000000:        # startGo
            start_lights = "go"

        session = SessionFrame(
            track_name=str(weekend.get("TrackDisplayName",
                                       weekend.get("TrackName", "iRacing Track"))),
            track_config=str(weekend.get("TrackConfigName", "") or ""),
            track_length_km=track_len_km,
            session_type=session_type,
            session_state=_SESSION_STATE_MAP.get(state_id, "RACING"),
            session_time=float(self._g("SessionTime", 0.0) or 0.0),
            session_time_remain=float(self._g("SessionTimeRemain", -1.0) or -1.0),
            session_laps_total=int(cur_session.get("SessionLaps", 0) or 0)
            if str(cur_session.get("SessionLaps", "")).isdigit() else 0,
            session_laps_remain=int(self._g("SessionLapsRemain", 0) or 0),
            flags=active_flags, start_lights=start_lights,
            weather=self._read_weather(),
            sim_date=str(weekend.get("WeekendOptions", {}).get("Date", "")),
            session_num=cur_num, session_key=session_key, in_replay=in_replay,
        )

        self._read_sectors(cars, cur_num, session_key, session.session_time,
                           track_len_km, pct, surface, on_pit, in_replay)
        incidents = self._read_incidents(cur_num, cur_session, cars,
                                         session.session_time, state_id,
                                         track_len_km, in_replay)
        return Frame(
            session=session, cars=cars, incidents=incidents,
            incident_totals=self._totals,
            track_path=self._track_path, pit_path=self._pit_path,
            source_name=self.name, connected=True,
        )

    def _read_sectors(self, cars: list[CarFrame], session_num: int,
                      session_key: str, t: float, track_km: float,
                      pcts, surfaces, on_pits, in_replay: bool) -> None:
        """Sector times of each car's lap in progress (field_sectors, the
        sectors of SplitTimeInfo).  Replay footage never counts - it would
        set bests from the past."""
        fs = self.sectors
        if fs is None:
            return
        key = (session_key, session_num)
        if key != self._sector_key:
            self._sector_key = key
            fs.reset()
        bounds = []
        try:
            for sec in (self._g("SplitTimeInfo", {}) or {}).get("Sectors") or []:
                if sec.get("SectorStartPct") is not None:
                    bounds.append(float(sec["SectorStartPct"]))
        except Exception:
            bounds = []
        fs.configure(bounds or [0.0, 1 / 3, 2 / 3], track_km * 1000.0)
        if not in_replay:
            fs.update(t, list(pcts), list(surfaces), list(on_pits))
        n = fs.n
        for c in cars:
            row = fs.car_last.get(c.car_idx) or [None] * n
            mine = fs.car_best.get(c.car_idx) or [None] * n
            out = []
            for i in range(n):
                v = row[i] if i < len(row) else None
                if v is None:
                    out.append(None)
                    continue
                best = fs.best[i]
                kind = ("overall" if best and v <= best[0] + 1e-9 else
                        "personal" if mine[i] is not None
                        and v <= mine[i] + 1e-9 else "")
                out.append([round(v, 3), kind])
            c.sectors = out

    def _read_incidents(self, session_num: int, cur_session: dict,
                        cars: list[CarFrame], t: float, state_id: int,
                        track_km: float, in_replay: bool) -> list[IncidentEvent]:
        out: list[IncidentEvent] = []
        laps = {c.car_idx: c.lap for c in cars}
        # official points (throttled: every read parses the session YAML)
        now = time.monotonic()
        if now >= self._inc_next:
            self._inc_next = now + INC_POLL_S
            totals: dict[int, int] = {}
            raw = []
            for r in cur_session.get("ResultsPositions") or []:
                try:
                    totals[int(r["CarIdx"])] = int(r.get("Incidents", 0) or 0)
                    raw.append({"car_idx": int(r["CarIdx"]),
                                "incidents": r.get("Incidents"),
                                "laps": r.get("LapsComplete")})
                except (KeyError, TypeError, ValueError):
                    continue
            self._note_inc_debug(session_num, cur_session, raw)
            for idx, delta, total in self.official.update(session_num, totals):
                out.append(IncidentEvent(car_idx=idx, points=delta,
                                         kind="incident", lap=laps.get(idx, 0),
                                         source="points", total=total))
            # cars without a result row yet keep their last known total
            self._totals = dict(self.official.totals)
        # behaviour - never from replay footage
        if not in_replay:
            surf = self._g("CarIdxTrackSurface", []) or []
            pct = self._g("CarIdxLapDistPct", []) or []
            rows = []
            for c in cars:
                i = c.car_idx
                rows.append((i, float(pct[i]) if i < len(pct) else -1.0,
                             int(surf[i]) if i < len(surf) else -1,
                             c.on_pit_road))
            for d in self.detector.update(t, session_num, state_id == 4, rows,
                                          track_km * 1000.0):
                out.append(IncidentEvent(car_idx=d.car_idx, points=0,
                                         kind=d.kind, lap=laps.get(d.car_idx, 0),
                                         other_car_idx=d.other_car_idx,
                                         source="detected", text=d.text))
        return out

    def _note_inc_debug(self, session_num: int, cur_session: dict,
                        raw: list) -> None:
        """What iRacing really sent - for /api/incidents/debug."""
        di = self._g("DriverInfo", {}) or {}
        me = int(di.get("DriverCarIdx", -1) if di.get("DriverCarIdx")
                 is not None else -1)
        names = {int(d.get("CarIdx", -1)): (str(d.get("CarNumber", "")),
                                            str(d.get("UserName", "")))
                 for d in di.get("Drivers", []) or []}
        for r in raw:
            r["number"], r["driver"] = names.get(r["car_idx"], ("", ""))
        self.inc_debug = {
            "session_num": session_num,
            "session_type": cur_session.get("SessionType"),
            "session_info_update": self._g("SessionInfoUpdate", None),
            "my_car_idx": me,
            "my_name": names.get(me, ("", ""))[1],
            "result_rows": len(raw),
            "cars_with_points": sum(1 for r in raw if (r["incidents"] or 0) > 0),
            "rows": raw,
            "baseline": {str(k): v for k, v in self.official.totals.items()},
        }

    def _read_weather(self) -> Weather:
        return Weather(
            air_temp=float(self._g("AirTemp", 0.0) or 0.0),
            track_temp=float(self._g("TrackTempCrew", 0.0) or 0.0),
            humidity=float(self._g("RelativeHumidity", 0.0) or 0.0),
            wind_ms=float(self._g("WindVel", 0.0) or 0.0),
            skies={0: "Clear", 1: "Partly Cloudy", 2: "Mostly Cloudy",
                   3: "Overcast"}.get(int(self._g("Skies", 0) or 0), "Clear"),
            precipitation=float(self._g("Precipitation", 0.0) or 0.0),
            is_wet=bool(self._g("WeatherDeclaredWet", False)),
            track_wetness={1: "Dry", 2: "Mostly Dry", 3: "Very Lightly Wet",
                           4: "Lightly Wet", 5: "Moderately Wet", 6: "Very Wet",
                           7: "Extremely Wet"}.get(
                int(self._g("TrackWetness", 1) or 1), "Dry"),
        )

    # -- race-control commands ---------------------------------------------

    def send_command(self, command: str, **params) -> str:
        """Execute a command against iRacing.

        Camera and replay commands use real SDK broadcast messages.  iRacing's
        SDK has no broadcast for admin actions (FCY, black flags, pit open /
        close), so for those this returns the iRacing chat command text the
        steward should issue.  Automating chat delivery is a roadmap item.
        """
        if self._ir is None or not self._ir.is_connected:
            return "iRacing not connected"
        cmd = command.lower()
        try:
            if cmd == "cam_car" and "number" in params:
                self._ir.cam_switch_num(str(params["number"]))
                return f"Camera switched to car #{params['number']}"
            if cmd == "replay_live":
                self._ir.replay_search(irsdk.ReplaySearchMode.to_end)
                self._ir.replay_set_play_speed(1)
                return "Replay set to live"
            if cmd == "replay_event":
                return self._start_replay(params)
        except Exception as exc:                  # pragma: no cover
            return f"command failed: {exc}"

        # Admin commands -> iRacing chat command text.
        chat = {
            "pace_deploy": "!yellow", "pace_end": "!pacelaps 0",
            "pit_open": "!pitopen", "pit_close": "!pitclose",
            "red_flag": "(post red flag in race log)",
            "wave_around": "!waveby {num}", "eol": "!eol {num}",
            "black_flag": "!black {num} {sec}", "dq": "!dq {num}",
            "clear": "!clear {num}",
        }.get(cmd)
        if chat:
            self._command_log.append(f"iRacing chat command needed: {chat}")
            return (f"SDK cannot send admin commands directly. "
                    f"iRacing chat command: {chat}")
        return f"command '{command}' acknowledged (logged only)"

    def _start_replay(self, params: dict) -> str:
        if self._replay_busy:
            return "a replay jump is already running"
        try:
            session_num = int(params.get("session_num", self._session_num))
            t_event = float(params["sim_time"])
        except (KeyError, TypeError, ValueError):
            return "replay: no time for this incident"
        number = str(params.get("number", "") or "")
        preroll = float(params.get("preroll", REPLAY_PREROLL_S) or REPLAY_PREROLL_S)
        self._replay_busy = True
        threading.Thread(target=self._replay_worker,
                         args=(session_num, t_event, number, preroll),
                         daemon=True).start()
        return (f"replay of #{number or '?'} from "
                f"{max(0.0, t_event - preroll):.0f}s")

    def _replay_worker(self, session_num: int, t_event: float, number: str,
                       preroll: float) -> None:
        """Seek -> WAIT until iRacing is there -> camera -> play 1x.

        A jump into another session takes iRacing ~2.5 s; switching the camera
        and pressing play before it landed left the replay stuck (found with
        the highlight runner, 2026-10-21).  One retry, then give up."""
        ir = self._ir
        target_ms = replay_target(t_event, preroll)
        try:
            landed = False
            for _attempt in range(2):
                ir.replay_search_session_time(session_num, target_ms)
                t_end = time.monotonic() + 6.0
                while time.monotonic() < t_end:
                    time.sleep(0.2)
                    try:
                        if (int(ir["SessionNum"]) == session_num and
                                abs(float(ir["SessionTime"]) * 1000 - target_ms)
                                < 3000):
                            landed = True
                            break
                    except Exception:
                        pass
                if landed:
                    break
            if not landed:
                self._command_log.append(
                    "Replay jump did not land (is the replay recorded "
                    "that far back?)")
                return
            if number:
                groups = (self._g("CameraInfo", {}) or {}).get("Groups", []) or []
                group = pick_cam_group(groups,
                                       int(self._g("CamGroupNumber", 0) or 0))
                ir.cam_switch_num(number, group, 0)
                time.sleep(0.3)
            ir.replay_set_play_speed(1, False)
        except Exception as exc:                  # pragma: no cover
            self._command_log.append(f"Replay failed: {exc}")
        finally:
            self._replay_busy = False

    def drain_command_log(self) -> list[str]:
        out = self._command_log[:]
        self._command_log.clear()
        return out
