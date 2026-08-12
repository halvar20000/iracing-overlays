"""Bridge to the real iRacing telemetry SDK (via ``pyirsdk``).

Only works on Windows with iRacing running.  On any other platform - or when
``pyirsdk`` is not installed - :meth:`connect` simply returns ``False`` and the
application falls back to the simulator.

What this source reads reliably from the SDK
---------------------------------------------
* Live timing for every car: position, lap, lap-distance %, last/best lap,
  pit-road state, track surface, gaps and intervals.
* Session: type, state, time/laps remaining, flags, weather.

Known SDK limitation (see ROADMAP)
----------------------------------
iRacing's SDK does **not** expose a per-car incident count for cars other than
your own.  So this source does not invent incident *points*; instead the
race-state engine logs off-track excursions from the track-surface data, and
the steward assigns points / penalties manually.  Behavioural incident
detection is a roadmap item.
"""

from __future__ import annotations

import contextlib
import io
import time
from typing import Optional

from backend.models import (
    CarFrame, Frame, SessionFrame, Weather,
    SURFACE_ON_TRACK, SURFACE_OFF_TRACK, SURFACE_IN_PIT_STALL,
    SURFACE_APPROACHING_PITS, SURFACE_NOT_IN_WORLD,
)
from backend.sources.base import DataSource
from backend.tracks import load_track
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
        if not _HAVE_IRSDK or self._ir is None:
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
            behind = leader_dist - this_dist
            laps_down = int(behind) if behind >= 1.0 else 0
            gap = round((behind - laps_down) * est_lap, 2)
            r = rank.get(idx, 0)
            interval = 0.0
            if r > 0:
                ahead_idx = order[r - 1][0]
                interval = round((dist_by_idx[ahead_idx] - this_dist) * est_lap, 2)

            ll = float(at(last_lap, idx, 0.0))
            bl = float(at(best_lap, idx, 0.0))
            cars.append(CarFrame(
                car_idx=idx,
                car_number=str(d.get("CarNumber", "0")),
                driver_name=str(d.get("UserName", "")),
                team_name=str(d.get("TeamName", "") or d.get("UserName", "")),
                car_class=str(d.get("CarClassShortName", "")
                               or d.get("CarClassID", "")),
                class_id=int(d.get("CarClassID", 0) or 0),
                class_color="#%06x" % (int(d.get("CarClassColor", 0)) & 0xFFFFFF),
                class_short=str(d.get("CarClassShortName", "")),
                car_brand=detect_brand(d.get("CarPath"), d.get("CarScreenName")),
                position=int(at(positions, idx, r + 1)) or (r + 1),
                class_position=int(at(class_pos, idx, 0)),
                lap=int(at(laps, idx, 0)),
                laps_completed=int(at(laps_done, idx, 0)),
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
        )

        return Frame(
            session=session, cars=cars, incidents=[],
            track_path=self._track_path, pit_path=self._pit_path,
            source_name=self.name, connected=True,
        )

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
        if not _HAVE_IRSDK or self._ir is None or not self._ir.is_connected:
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

    def drain_command_log(self) -> list[str]:
        out = self._command_log[:]
        self._command_log.clear()
        return out
