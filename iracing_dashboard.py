"""
iRacing Live Telemetry Dashboard (Spectator Edition, v8)
--------------------------------------------------------
Requirements:  pip install pyirsdk flask pywin32
Run:           python iracing_dashboard.py
Open:          http://localhost:5000

v7 adds:
  - Race progress strip across the top: session name, laps done/total,
    laps remaining, time elapsed/total, time remaining, with progress bars.
  - Floating "Go Live" button (also keyboard L) that jumps the iRacing
    replay playhead to the latest frame = live playback.
"""

import sys
import threading
import time
from collections import deque
from flask import Flask, jsonify, render_template_string, request

# Windows cp1252 stdout + Unicode in prints = UnicodeEncodeError that can
# kill the poller thread silently. Force UTF-8 like the other overlays do.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import irsdk
except ImportError:
    print("ERROR: pyirsdk not installed. Run:  pip install pyirsdk flask")
    raise SystemExit(1)


# -----------------------------------------------------------------------------
# Send keystrokes to the iRacing window (Windows only)
# -----------------------------------------------------------------------------
try:
    import win32gui
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

VK_SPACE = 0x20


def _find_iracing_window():
    if not HAS_WIN32:
        return None
    found = []

    def _enum(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        cls   = win32gui.GetClassName(hwnd) or ""
        if "iRacing" in title or cls == "SimWinClass":
            found.append((hwnd, title, cls))

    win32gui.EnumWindows(_enum, None)
    for hwnd, title, cls in found:
        if cls == "SimWinClass":
            return hwnd
    return found[0][0] if found else None


def send_key_to_iracing(vk_code: int = VK_SPACE):
    if not HAS_WIN32:
        return False, "pywin32 not installed (pip install pywin32)"
    hwnd = _find_iracing_window()
    if not hwnd:
        return False, "iRacing window not found - is the sim running?"
    try:
        win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, vk_code, 0)
        win32gui.PostMessage(hwnd, win32con.WM_KEYUP,   vk_code, 0)
        return True, "ok"
    except Exception as e:
        return False, f"PostMessage failed: {e}"


# -----------------------------------------------------------------------------
# iRacing track-surface enum values (as returned by CarIdxTrackSurface)
# -----------------------------------------------------------------------------
SURFACE_NOT_IN_WORLD = -1
SURFACE_OFF_TRACK    = 0
SURFACE_IN_PIT_STALL = 1
SURFACE_APPROACHING_PITS = 2
SURFACE_ON_TRACK     = 3


# -----------------------------------------------------------------------------
# Telemetry poller
# -----------------------------------------------------------------------------
class TelemetryPoller:
    def __init__(self, poll_hz: int = 10):
        self.ir = irsdk.IRSDK()
        self.poll_interval = 1.0 / poll_hz
        self.connected = False
        self.data = {"connected": False}
        self._lock = threading.Lock()
        self._running = True

        # Sector tracking
        self._sector_pcts = []
        self._last_lap_pct = {}
        self._last_lap_num = {}
        self._sector_entry_time = {}
        self._current_sector = {}
        self._last_sector_times = {}
        self._best_sector_times = {}
        self._session_best_sectors = []

        # Auto-follow
        self.auto_follow = False
        self._last_auto_switch = 0.0
        self._auto_switch_min_interval = 6.0
        self._manual_override_until = 0.0
        self._starred_car_idxs: set = set()

        # Focus-on-leader: keep camera locked on the overall P1 car.
        self.focus_leader = False
        self._last_leader_car_idx: int | None = None

        # Camera disconnect watchdog. Tracks when the currently followed
        # car went out-of-world (CarIdxTrackSurface == -1) — typically a
        # network disconnect or a tow-to-garage. After a short grace
        # window we proactively switch to another driver so iRacing's
        # scenic camera doesn't kick in (which the user finds annoying
        # because the scene is then unrelated to the race).
        self._cam_lost_since: float | None = None
        self._cam_lost_grace_seconds = 3.0   # how long to wait before switching
        self._last_cam_recover_at = 0.0      # debounce repeat switches

        # Focus-on-crashes: auto-switch camera to a car that just crashed or
        # spun. When a crash is picked up, stick with it for a hold window
        # before allowing the next switch — prevents rapid flipping during
        # a multi-car pile-up (we follow the first crash for a moment).
        self.focus_crashes = False
        self._focus_crash_until = 0.0
        self._focus_crash_hold_seconds = 12.0

        # HUD-hidden state. iRacing re-shows its broadcast HUD every time
        # the camera changes; we track whether the user has toggled it
        # hidden and re-press spacebar after camera actions to reassert
        # the hidden state.
        self.iracing_ui_hidden = False

        # Camera groups
        self._camera_groups = []
        self._current_cam_group = 0
        self._default_camera_applied = False   # set once we've forced TV3 on startup

        # Incident detection
        self._prev_surface = {}          # car_idx -> last seen surface value
        self._prev_yellow  = {}          # car_idx -> last seen yellow flag bool
        self._prev_incidents = {}        # car_idx -> last seen incident count (from driver YAML)
        self._spin_cooldown = {}         # car_idx -> last session_time a spin was emitted
        self._prev_lap_pct = {}          # car_idx -> last seen CarIdxLapDistPct (for backward-movement detection)
        # Stopped-on-track counter (spec-mode crash proxy). In spectator mode
        # CurDriverIncidentCount and CarIdxYawRate are not broadcast, so we
        # infer crashes from (a) backward motion and (b) a car going from
        # moving to static while still on the racing surface.
        self._stopped_ticks = {}         # car_idx -> consecutive polls with delta_pct < 0.0003
        # Last session-time at which a car was actually moving forward on
        # track. Used to back-date "vanished from world" events so the
        # replay rewinds to the actual crash, not the tow-to-garage that
        # iRacing schedules 10-30 s later.
        self._last_moving_t = {}         # car_idx -> t_session
        # Finished-the-race tracker. Once SessionState reaches CHECKERED (5)
        # we record each car's current CarIdxLap; any car that then
        # completes another lap (crosses S/F after the flag) is marked
        # finished and excluded from all incident detection. This
        # suppresses false "stopped on track" / "vanished" alerts for
        # cars rolling to the pits or parking after their final lap.
        self._checker_lap_at_trigger = {}  # car_idx -> lap when checker first seen
        self._finished = set()             # car_idxs considered finished
        self._incidents = deque(maxlen=40)
        self._incident_cooldown = {}     # (car_idx, type) -> last session_time emitted
        # Global cooldown for yellow-zone emissions. When iRacing raises a
        # local yellow, multiple cars in the zone get the flag bit set
        # simultaneously — we only want ONE incident per actual event, so
        # this gate lets the first car emit and suppresses the rest for a
        # short window. Per-car _incident_cooldown still dedupes against
        # yaw/regression-based emissions for the same car.
        self._last_yellow_emit_t = -1e9  # session_time of last yellow-based emit

        # Auto-replay on incidents (opt-in via dashboard toggle)
        self.auto_replay = False
        self._last_auto_replay_at = 0.0            # wall clock time of last auto-replay
        self._auto_replay_cooldown_seconds = 15.0  # min time between auto-replays
        # Types that should trigger an auto-replay when enabled
        # Which incident types are severe enough to auto-trigger a replay.
        # Kept narrow: a spin (2x) or a collision (4x). Off-track minors (1x)
        # are treated as noise and not even added to the feed.
        self._auto_replay_types = {"lost_control", "collision"}

    # --- connection ---------------------------------------------------------
    def _check_connection(self) -> bool:
        if self.connected and not (self.ir.is_initialized and self.ir.is_connected):
            self.ir.shutdown()
            self.connected = False
            self._reset_session_state()
            print("[telemetry] Disconnected from iRacing")
        elif not self.connected and self.ir.startup() and self.ir.is_initialized and self.ir.is_connected:
            self.connected = True
            self._load_sector_boundaries()
            self._load_camera_groups()
            print("[telemetry] Connected to iRacing")
        return self.connected

    def _reset_session_state(self):
        self._sector_pcts = []
        self._last_lap_pct.clear()
        self._last_lap_num.clear()
        self._sector_entry_time.clear()
        self._current_sector.clear()
        self._last_sector_times.clear()
        self._best_sector_times.clear()
        self._session_best_sectors = []
        self._camera_groups = []
        self._default_camera_applied = False
        self._prev_surface.clear()
        self._prev_yellow.clear()
        self._prev_incidents.clear()
        self._spin_cooldown.clear()
        self._prev_lap_pct.clear()
        self._stopped_ticks.clear()
        self._last_moving_t.clear()
        self._checker_lap_at_trigger.clear()
        self._finished.clear()
        self._incidents.clear()
        self._incident_cooldown.clear()
        self._last_yellow_emit_t = -1e9
        self._cam_lost_since = None
        self._last_cam_recover_at = 0.0

    def _load_sector_boundaries(self):
        info = self.ir["SplitTimeInfo"]
        if not info:
            return
        sectors = info.get("Sectors", []) or []
        pcts = [s.get("SectorStartPct", 0.0) for s in sectors]
        if pcts and pcts == sorted(pcts):
            self._sector_pcts = pcts
            self._session_best_sectors = [0.0] * len(pcts)
            print(f"[telemetry] Loaded {len(pcts)} sector boundaries")

    def _load_camera_groups(self):
        info = self.ir["CameraInfo"]
        if not info:
            return
        groups = info.get("Groups", []) or []
        parsed = []
        for g in groups:
            gid = g.get("GroupNum")
            gname = g.get("GroupName", "")
            if gid is None or not gname:
                continue
            parsed.append({"id": int(gid), "name": gname})
        self._camera_groups = parsed
        print(f"[telemetry] Loaded {len(parsed)} camera groups: {[g['name'] for g in parsed]}")

    def _apply_default_camera(self):
        """
        On first connection, force the camera to TV3 so we don't get
        iRacing's default Scenic view.  Runs once per connection.
        """
        if self._default_camera_applied:
            return
        if not self._camera_groups:
            return  # wait until groups are loaded

        # Find a TV3-like group.  iRacing names vary: "TV3", "TV 3", or
        # sometimes track-specific names containing "TV3".
        target = None
        for g in self._camera_groups:
            name = (g["name"] or "").upper().replace(" ", "")
            if name == "TV3":
                target = g
                break
        if target is None:
            for g in self._camera_groups:
                if "TV3" in (g["name"] or "").upper().replace(" ", ""):
                    target = g
                    break
        if target is None:
            print("[telemetry] TV3 camera not available at this track - keeping iRacing default")
            self._default_camera_applied = True   # don't keep retrying
            return

        # We need a car number to switch to.  Use whatever car iRacing's
        # camera is currently on; that keeps the target consistent with
        # what the user is watching.
        cam_car_idx = self.ir["CamCarIdx"]
        drivers = self.ir["DriverInfo"]["Drivers"] if self.ir["DriverInfo"] else []
        car_number = None
        for d in drivers:
            if d.get("CarIdx") == cam_car_idx:
                car_number = str(d.get("CarNumber", "")) or None
                break
        # Fallback: use the leader (first driver by class position).  This
        # handles the case where iRacing hasn't attached the cam to a car yet.
        if car_number is None:
            positions = self.ir["CarIdxClassPosition"] or []
            for d in drivers:
                idx = d.get("CarIdx")
                if idx is None or d.get("CarIsPaceCar") == 1:
                    continue
                if idx < len(positions) and positions[idx] == 1:
                    car_number = str(d.get("CarNumber", "")) or None
                    break

        if car_number is None:
            # No car yet - try again next tick
            return

        try:
            self.ir.cam_switch_num(car_number, int(target["id"]), 0)
            self._current_cam_group = int(target["id"])
            self._default_camera_applied = True
            print(f"[telemetry] Default camera set to '{target['name']}' on #{car_number}")
        except Exception as e:
            print(f"[telemetry] Could not apply default camera: {e}")
            # Don't mark as applied - retry next tick

    # --- sector tracking ----------------------------------------------------
    def _update_sectors(self):
        if not self._sector_pcts:
            return
        laps_arr = self.ir["CarIdxLap"] or []
        pct_arr  = self.ir["CarIdxLapDistPct"] or []
        t_now    = self.ir["SessionTime"] or 0.0
        n_sectors = len(self._sector_pcts)

        for idx in range(len(pct_arr)):
            pct = pct_arr[idx]
            lap = laps_arr[idx] if idx < len(laps_arr) else 0
            if pct is None or pct < 0:
                continue

            prev_pct = self._last_lap_pct.get(idx, pct)
            prev_lap = self._last_lap_num.get(idx, lap)

            if lap > prev_lap or (prev_pct > 0.9 and pct < 0.1):
                entries = self._sector_entry_time.get(idx)
                if entries and len(entries) == n_sectors:
                    t_final = t_now - entries[-1]
                    sectors_this_lap = self._last_sector_times.get(idx, [0.0] * n_sectors)
                    sectors_this_lap[-1] = max(0.0, t_final)
                    self._finalize_lap_sectors(idx, sectors_this_lap)
                self._sector_entry_time[idx] = [t_now] + [0.0] * (n_sectors - 1)
                self._current_sector[idx] = 0
                self._last_sector_times[idx] = [0.0] * n_sectors

            cur = self._current_sector.get(idx, 0)
            if cur < n_sectors - 1:
                next_boundary = self._sector_pcts[cur + 1]
                if prev_pct < next_boundary <= pct:
                    entries = self._sector_entry_time.setdefault(idx, [t_now] + [0.0] * (n_sectors - 1))
                    split = t_now - entries[cur]
                    sec_list = self._last_sector_times.setdefault(idx, [0.0] * n_sectors)
                    sec_list[cur] = max(0.0, split)
                    entries[cur + 1] = t_now
                    self._current_sector[idx] = cur + 1

            self._last_lap_pct[idx] = pct
            self._last_lap_num[idx] = lap

    def _finalize_lap_sectors(self, idx: int, sectors: list):
        if all(s > 0.01 for s in sectors):
            pb = self._best_sector_times.setdefault(idx, [0.0] * len(sectors))
            for i, t in enumerate(sectors):
                if pb[i] == 0.0 or t < pb[i]:
                    pb[i] = t
            if not self._session_best_sectors:
                self._session_best_sectors = [0.0] * len(sectors)
            for i, t in enumerate(sectors):
                sb = self._session_best_sectors[i]
                if sb == 0.0 or t < sb:
                    self._session_best_sectors[i] = t

    def _sector_snapshot(self, car_idx: int) -> dict:
        if not self._sector_pcts or car_idx is None:
            return {"count": 0}
        n = len(self._sector_pcts)
        return {
            "count": n,
            "last": self._last_sector_times.get(car_idx, [0.0] * n),
            "pb":   self._best_sector_times.get(car_idx, [0.0] * n),
            "session_best": self._session_best_sectors or [0.0] * n,
            "current_sector": self._current_sector.get(car_idx, 0),
        }

    # --- incident detection -------------------------------------------------
    def _driver_name(self, car_idx: int):
        drivers = self.ir["DriverInfo"]["Drivers"] if self.ir["DriverInfo"] else []
        for d in drivers:
            if d.get("CarIdx") == car_idx:
                return {
                    "name":       d.get("UserName", "") or "",
                    "car_number": d.get("CarNumber", "") or "",
                }
        return None

    def _emit_incident(self, car_idx: int, inc_type: str, t_session: float, details: str = ""):
        # Dedup: don't emit the same (car, type) within 15 seconds. A single
        # messy moment (off-track → spin → contact) otherwise fires three
        # incidents in a row for the same car; 15 s coalesces them visually
        # while still letting genuinely separate events through.
        key = (car_idx, inc_type)
        last = self._incident_cooldown.get(key, -1e9)
        if t_session - last < 15.0:
            return
        self._incident_cooldown[key] = t_session

        info = self._driver_name(car_idx)
        if not info:
            return
        session_num = self.ir["SessionNum"] if self.ir is not None else 0
        self._incidents.appendleft({
            "id": int(t_session * 1000) + car_idx,   # unique-ish
            "t_session": t_session,
            "session_num": int(session_num or 0),
            "wall_clock": time.time(),
            "car_idx": car_idx,
            "car_number": info["car_number"],
            "name": info["name"],
            "type": inc_type,
            "details": details,
        })
        print(f"[incident] {inc_type:10s} #{info['car_number']:>3s} {info['name']:<20s} {details}")

        # Auto-replay handoff: if enabled and this incident qualifies, kick
        # off the replay flow.  All safety gating happens inside.
        if self.auto_replay and inc_type in self._auto_replay_types:
            self._try_auto_replay(info["car_number"], info["name"], inc_type, t_session)

        # Focus-on-crashes handoff: if enabled and this is a crash/spin,
        # snap the camera straight to the incident car and hold it there
        # for _focus_crash_hold_seconds so we don't immediately hop to a
        # second simultaneous crash.
        if self.focus_crashes and inc_type in self._auto_replay_types:
            now = time.time()
            if now >= self._focus_crash_until:
                try:
                    self.ir.cam_switch_num(
                        str(info["car_number"]), self._current_cam_group, 0
                    )
                    self._reassert_ui_hide()
                    self._focus_crash_until = now + self._focus_crash_hold_seconds
                    print(f"[focus-crashes] -> #{info['car_number']} {info['name']} "
                          f"({inc_type}) holding {self._focus_crash_hold_seconds:.0f}s")
                except Exception as e:
                    print(f"[focus-crashes] switch failed: {e}")

    def _try_auto_replay(self, car_number: str, driver_name: str, inc_type: str, t_session: float):
        """
        Guarded auto-replay trigger.  Respects:
          - global cooldown between auto-replays
          - only fires when we're currently LIVE (no overlapping replays)
          - skips during the first ~15s of the race (formation/start chaos)
          - skips if a manual override is in effect
        """
        now = time.time()

        # Cooldown: don't fire more often than every N seconds
        if now - self._last_auto_replay_at < self._auto_replay_cooldown_seconds:
            return
        # Respect manual overrides (dashboard click just happened)
        if now < self._manual_override_until:
            return

        # Don't trigger while we're already inside a replay
        try:
            frame = self.ir["ReplayFrameNum"]
            end   = self.ir["ReplayFrameNumEnd"]
            speed = self.ir["ReplayPlaySpeed"]
            if frame is not None and end is not None:
                is_live = (end - frame) <= 60 and speed == 1
                if not is_live:
                    return  # already replaying / paused / scrubbed
        except Exception:
            pass

        # Skip during the opening 15s of a race session - formation lap /
        # green flag incidents tend to cluster and we don't want to jump
        # around constantly.
        try:
            sessions = (self.ir["SessionInfo"] or {}).get("Sessions", []) or []
            sess_num = self.ir["SessionNum"] or 0
            for s in sessions:
                if s.get("SessionNum") == sess_num:
                    stype = (s.get("SessionType") or "").lower()
                    if "race" in stype:
                        time_rem = self.ir["SessionTimeRemain"]
                        total = s.get("SessionTime", "")
                        try:
                            total_s = float(str(total).split()[0]) if total else 0.0
                        except (TypeError, ValueError):
                            total_s = 0.0
                        if total_s > 0 and time_rem is not None:
                            elapsed = total_s - time_rem
                            if elapsed < 15.0:
                                return  # too early in the race
                    break
        except Exception:
            pass

        # Fire the replay (non-blocking: replay_5s_of_car schedules its own
        # background thread for auto-return). Pass t_session so the seek
        # uses the actual incident time, not a "rewind from now" estimate.
        # For collisions the "detected at" time can be well after the actual
        # impact (iRacing takes its time before deciding to tow a wrecked
        # car), so we also ask for a longer rewind window.
        sess_num = None
        try:
            sess_num = int(self.ir["SessionNum"] or 0)
        except Exception:
            pass
        rewind = 15.0 if inc_type == "collision" else 10.0
        ok, msg = self.replay_5s_of_car(
            car_number,
            t_session=t_session,
            session_num=sess_num,
            rewind_seconds=rewind,
        )
        if ok:
            self._last_auto_replay_at = now
            print(f"[auto-replay] {inc_type} -> #{car_number} {driver_name}")
        else:
            print(f"[auto-replay] failed: {msg}")

    def _check_slow_sector(self, car_idx: int, sector_idx: int, sector_time: float, t_session: float):
        # DEPRECATED: slow-sector detector disabled (too noisy, fires late).
        # Kept as a no-op so existing call-sites don't break if any remain.
        pass

    def _update_incidents(self):
        t_now = self.ir["SessionTime"] or 0.0
        surfaces = self.ir["CarIdxTrackSurface"] or []
        flags_arr = self.ir["CarIdxSessionFlags"] or []
        pct_arr   = self.ir["CarIdxLapDistPct"] or []
        positions = self.ir["CarIdxClassPosition"] or []
        on_pit    = self.ir["CarIdxOnPitRoad"] or []
        yaw_arr   = self.ir["CarIdxYawRate"] or []
        laps_arr  = self.ir["CarIdxLap"] or []

        # Per-car yellow-flag bit mask from CarIdxSessionFlags:
        #   0x2000 = yellow-waving, 0x4000 = caution
        YELLOW_MASK = 0x00004000 | 0x00002000

        # --- Finished-the-race detection --------------------------------
        # Once the session reaches CHECKERED (5) we snapshot each car's
        # current lap number; any car that then completes another lap
        # has crossed S/F after the flag = finished. Finished cars get
        # excluded from incident detection so parked / rolling-to-pit
        # cars don't generate false stopped-on-track / vanish events.
        # Values in iRacing's SessionState enum:
        #   0=invalid, 1=get-in-car, 2=warmup, 3=parade-laps, 4=racing,
        #   5=checkered, 6=cool-down
        sess_state = self.ir["SessionState"] or 0
        if sess_state >= 5:  # checkered or cool-down
            for idx in range(len(laps_arr)):
                cur_lap = laps_arr[idx]
                if cur_lap is None:
                    continue
                # First time we see this car after the flag: record its
                # current lap as the "threshold". One more than this =
                # they crossed S/F = finished.
                if idx not in self._checker_lap_at_trigger:
                    self._checker_lap_at_trigger[idx] = cur_lap
                elif cur_lap > self._checker_lap_at_trigger[idx]:
                    if idx not in self._finished:
                        self._finished.add(idx)
                        print(f"[finished] idx={idx} crossed finish line "
                              f"(lap {cur_lap})")

        # --- Build an index from CarIdx -> CurDriverIncidentCount ----------
        # iRacing publishes a "CurDriverIncidentCount" per driver inside the
        # DriverInfo.Drivers[] YAML block.  It ticks up with iRacing's own
        # scoring: +1 for minor offs, +2 for off-track (4 wheels), +4 loss of
        # control, +6 car-to-car contact, +8 major collision.  Treating ANY
        # jump as an incident matches exactly what iRacing itself considers
        # an incident.
        incidents_by_idx = {}
        drivers_info = self.ir["DriverInfo"]
        if drivers_info:
            for d in drivers_info.get("Drivers", []) or []:
                cidx = d.get("CarIdx")
                if cidx is None:
                    continue
                # The field name iRacing uses is "CurDriverIncidentCount".
                # Fall back to "TeamIncidentCount" for team races just in case.
                cnt = d.get("CurDriverIncidentCount")
                if cnt is None:
                    cnt = d.get("TeamIncidentCount", 0)
                try:
                    incidents_by_idx[cidx] = int(cnt) if cnt is not None else 0
                except (TypeError, ValueError):
                    incidents_by_idx[cidx] = 0

        for idx in range(len(surfaces)):
            # Skip pace car / spectator-ish slots (position 0 and no lap data)
            pos = positions[idx] if idx < len(positions) else 0
            if pos == 0 and (idx >= len(pct_arr) or pct_arr[idx] < 0):
                continue
            # Skip drivers who have finished the race — they're rolling
            # to pits, parking, or sitting on cool-down, all of which
            # would otherwise register as stopped-on-track / vanish events.
            if idx in self._finished:
                continue

            surf = surfaces[idx]
            prev_surf = self._prev_surface.get(idx, surf)
            self._prev_surface[idx] = surf
            is_pit = bool(on_pit[idx]) if idx < len(on_pit) else False

            # --- stopped / crashed: surface became NotInWorld from in-world ---
            # Intentionally NOT emitted: too many causes (garage, finished,
            # disconnected, tow, load screens) produce it while almost none
            # are incidents the user wants to see.

            # --- iRacing incident points jumped -----------------------------
            # Map iRacing's scored incident severity to our event types:
            #   +1x  → off_track   (wheels off, minor)
            #   +2x  → lost_control (spin / loss of control)
            #   +4x+ → collision   (car-to-car or major off)
            # 3x and 5x jumps are unusual (usually mean two events coincided
            # in one sample) — we round to the nearest bucket.
            # NOTE: In SPECTATOR mode iRacing sets CurDriverIncidentCount to
            # -1 for every car (confirmed via /incidents/debug), so this
            # whole branch is a no-op unless you're the driver. We still
            # keep it in place for when the user drives themselves. The
            # spec-mode detection happens in the lap-pct + surface branches
            # below.
            new_cnt = incidents_by_idx.get(idx)
            # Treat -1 (iRacing's "not available" sentinel in spec mode) as
            # "no data" rather than a real count; otherwise delta arithmetic
            # gets corrupted when the sentinel later flips to a real 0.
            if new_cnt is not None and new_cnt < 0:
                new_cnt = None
            if new_cnt is not None:
                prev_cnt = self._prev_incidents.get(idx, new_cnt)
                self._prev_incidents[idx] = new_cnt
                delta = new_cnt - prev_cnt
                if delta > 0:
                    # Diagnostic: log every scored-incident jump that iRacing
                    # reports, even if it's below our emit threshold or the
                    # per-car cooldown suppresses the feed entry. Helps tell
                    # "iRacing isn't reporting" from "we're filtering".
                    print(f"[incident-delta] idx={idx} {prev_cnt} -> {new_cnt} "
                          f"(delta=+{delta}x)")
                if delta >= 4:
                    self._emit_incident(
                        idx, "collision", t_now,
                        f"collision (+{delta}x, total {new_cnt}x)",
                    )
                elif delta >= 2:
                    self._emit_incident(
                        idx, "lost_control", t_now,
                        f"spin / loss of control (+{delta}x, total {new_cnt}x)",
                    )
                elif delta >= 1:
                    self._emit_incident(
                        idx, "off_track", t_now,
                        f"off track (+{delta}x, total {new_cnt}x)",
                    )

            # --- yaw-rate spin detection (for visible spins iRacing did
            # NOT score itself). Fires when abs(yaw_rate) > 2.5 rad/s AND
            # the car is on the racing surface (on-track OR just off),
            # but NOT in the pit lane / pit approach / out-of-world.
            # 2.5 rad/s (~143 deg/s) catches a fast 45° snap rotation
            # over ~300 ms, as well as anything more violent. Will also
            # fire on some aggressive corner-exit oversteer moments —
            # user-chosen trade-off for catching every visible slide.
            # Per-car 8-second cooldown.
            if (idx < len(yaw_arr)
                    and surf in (SURFACE_OFF_TRACK, SURFACE_ON_TRACK)
                    and not is_pit):
                yaw = yaw_arr[idx]
                # Diagnostic: log near-miss yaw readings so we can see if
                # the threshold is in the right ballpark for this car type.
                if yaw is not None and abs(yaw) > 1.8:
                    deg = abs(yaw) * 57.2958
                    print(f"[yaw-peek] idx={idx} yaw={abs(yaw):.2f} rad/s "
                          f"({deg:.0f} deg/s) surf={surf}")
                if yaw is not None and abs(yaw) > 2.5:
                    last = self._spin_cooldown.get(idx, -1e9)
                    if t_now - last > 8.0:
                        self._spin_cooldown[idx] = t_now
                        deg_per_sec = abs(yaw) * 57.2958
                        where = "on track" if surf == SURFACE_ON_TRACK else "off track"
                        self._emit_incident(
                            idx, "lost_control", t_now,
                            f"spin ({where}, {deg_per_sec:.0f} deg/s)",
                        )

            # --- lap-position regression (car moving BACKWARDS) -----------
            # A car that's been hit or has spun often ends up going the
            # wrong way on track. CarIdxLapDistPct drops noticeably in
            # one poll. We ignore the natural 0.99 → 0.01 S/F wrap and
            # any pit / not-in-world cases.
            # Threshold: -0.003 per poll (~0.3% of lap). On a 4 km track
            # that's ~12 m backward — real spins easily produce that,
            # while GPS jitter stays < 0.0005.
            # Threshold used to be -0.01 (~40 m) which was so high that
            # mid-track spins without a big translation weren't caught;
            # this was the symptom "a car spun and nothing was noted".
            # In SPEC MODE this is our primary crash-detection signal
            # since yaw rate and incident counts aren't broadcast.
            cur_pct = None
            delta_pct_signed: float | None = None
            if (idx < len(pct_arr)
                    and surf in (SURFACE_OFF_TRACK, SURFACE_ON_TRACK)
                    and not is_pit):
                cur_pct = pct_arr[idx]
                prev_pct = self._prev_lap_pct.get(idx)
                self._prev_lap_pct[idx] = cur_pct
                if (prev_pct is not None
                        and cur_pct is not None
                        and prev_pct < 0.9            # not the 0.99→0.01 wrap
                        and cur_pct < prev_pct - 0.003):
                    delta_m_pct = (prev_pct - cur_pct) * 100.0
                    print(f"[lap-regress] idx={idx} {prev_pct:.4f} -> "
                          f"{cur_pct:.4f} (-{delta_m_pct:.2f}%)")
                    last = self._spin_cooldown.get(idx, -1e9)
                    if t_now - last > 8.0:
                        self._spin_cooldown[idx] = t_now
                        self._emit_incident(
                            idx, "lost_control", t_now,
                            f"moved backwards {delta_m_pct:.1f}% of lap",
                        )
                # Store forward-delta for the stopped-on-track check below
                if prev_pct is not None and cur_pct is not None:
                    # Unwrap the natural S/F wrap so delta is continuous
                    raw = cur_pct - prev_pct
                    if raw < -0.5:
                        raw += 1.0
                    delta_pct_signed = raw

            # --- stopped-on-track (spec-mode crash proxy) ------------------
            # When CarIdxYawRate isn't available (spec mode), a car that's
            # crashed / beached typically shows as "still on the racing
            # surface but no longer moving forward". Count consecutive
            # polls with very small |delta|; trip the alarm at 12 polls
            # (~3 s at 250 ms tick) — less sensitive than 1 s because
            # rolling starts, corner exits, and traffic jams produced too
            # many false positives. A real stuck/beached car will easily
            # hold still for 3 s.
            # Guardrail: require at least one OTHER car to be moving
            # forward, otherwise a full-course yellow / red flag /
            # paused session triggers the alarm for every car at once.
            any_other_moving = False
            for jdx in range(len(pct_arr)):
                if jdx == idx:
                    continue
                jsurf = surfaces[jdx] if jdx < len(surfaces) else -1
                if jsurf != SURFACE_ON_TRACK:
                    continue
                jprev = self._prev_lap_pct.get(jdx)
                jcur = pct_arr[jdx] if jdx < len(pct_arr) else None
                if jprev is None or jcur is None:
                    continue
                # unwrap S/F wrap
                jd = jcur - jprev
                if jd < -0.5:
                    jd += 1.0
                if jd > 0.001:
                    any_other_moving = True
                    break

            # Update last-moving timestamp whenever we see real forward
            # progress. Used below to back-date vanish events to the
            # actual crash time, not the tow time.
            if delta_pct_signed is not None and delta_pct_signed > 0.001:
                self._last_moving_t[idx] = t_now

            if (surf == SURFACE_ON_TRACK
                    and not is_pit
                    and delta_pct_signed is not None
                    and cur_pct is not None
                    and any_other_moving
                    # Ignore the start/finish line area — cars here can
                    # legitimately be held for formation / pit-exit lights
                    and 0.02 < cur_pct < 0.98):
                if abs(delta_pct_signed) < 0.0003:
                    self._stopped_ticks[idx] = self._stopped_ticks.get(idx, 0) + 1
                else:
                    if self._stopped_ticks.get(idx, 0) >= 12:
                        print(f"[stopped-on-track] idx={idx} resumed after "
                              f"{self._stopped_ticks[idx]} static ticks")
                    self._stopped_ticks[idx] = 0
                if self._stopped_ticks.get(idx, 0) == 12:
                    last = self._spin_cooldown.get(idx, -1e9)
                    if t_now - last > 15.0:
                        self._spin_cooldown[idx] = t_now
                        print(f"[stopped-on-track] idx={idx} FIRE "
                              f"(pct={cur_pct:.3f})")
                        self._emit_incident(
                            idx, "lost_control", t_now,
                            "stopped on track",
                        )
            else:
                # Car in pits, off-track, out-of-world, or session-wide
                # slowdown — reset counter
                self._stopped_ticks[idx] = 0

            # --- vanished-from-world (spec-mode heavy-crash proxy) --------
            # Surface went 3 -> -1 while the car was mid-lap (not near pit
            # entrance). iRacing moves a heavily damaged car to "not in
            # world" after a big hit — but with a 10-30 s tow delay, so
            # t_now is a poor estimate of when the crash happened.
            #
            # Filter: only emit if the car was actually stuck for at least
            # a second before vanishing (t_now - last_moving_t >= 1.0).
            # This suppresses the "driver retired voluntarily" /
            # "disconnected" case, which looks like moving-then-gone with
            # no stopped period.
            #
            # Back-date: pass last_moving_t as the incident time so the
            # replay rewinds to when the car was actually still racing
            # (and therefore, to a few seconds before the crash).
            if (prev_surf == SURFACE_ON_TRACK
                    and surf == SURFACE_NOT_IN_WORLD):
                last_known_pct = self._prev_lap_pct.get(idx)
                last_moved = self._last_moving_t.get(idx)
                stop_duration = (t_now - last_moved) if last_moved is not None else 0.0
                if last_known_pct is None or last_known_pct >= 0.95:
                    pass  # near pit entrance — probably pitted
                elif stop_duration < 1.0:
                    # Was moving a moment ago: disconnect / retire / tow
                    # from pit. Not a crash.
                    print(f"[vanish-skip] idx={idx} no stop period "
                          f"before vanish (moving {stop_duration:.1f}s ago)")
                else:
                    last = self._spin_cooldown.get(idx, -1e9)
                    if t_now - last > 15.0:
                        self._spin_cooldown[idx] = t_now
                        crash_t = last_moved if last_moved is not None else t_now
                        print(f"[vanish] idx={idx} surface 3->-1 at pct "
                              f"{last_known_pct:.3f}, stopped for "
                              f"{stop_duration:.1f}s, crash_t={crash_t:.1f}")
                        self._emit_incident(
                            idx, "collision", crash_t,
                            f"crashed (stopped {stop_duration:.0f}s then vanished)",
                        )

            # --- iRacing local-yellow zone (spec-mode supplementary) -------
            # CarIdxSessionFlags exposes a per-car bitmask. iRacing sets
            # the LOCAL_YELLOW / yellow-waving bits on cars that are in
            # the zone of an incident — that IS iRacing's own "an incident
            # just happened here" signal. Useful because CarIdxYawRate and
            # CurDriverIncidentCount don't work in spectator mode, so
            # smaller incidents (a light tap, a brief slide that doesn't
            # cross our yaw or lap-regress thresholds) can slip through
            # the other detectors.
            #
            # Two layers of deduplication:
            #   (1) Global 5-second cooldown. One physical incident
            #       usually raises the yellow bit on several cars
            #       simultaneously — the zone is broad. Without this,
            #       we'd fire N incidents for the one event.
            #   (2) Per-car _incident_cooldown inside _emit_incident. If
            #       the yaw or regression detector already fired for this
            #       car in the last 15 s, the yellow-based emission is
            #       suppressed there.
            if idx < len(flags_arr):
                raw_flags = int(flags_arr[idx] or 0)
                cur_yellow_bits = raw_flags & YELLOW_MASK
                cur_yellow = cur_yellow_bits != 0
                prev_yellow = self._prev_yellow.get(idx, False)
                self._prev_yellow[idx] = cur_yellow
                if (cur_yellow
                        and not prev_yellow
                        and surf == SURFACE_ON_TRACK
                        and not is_pit
                        and t_now - self._last_yellow_emit_t > 5.0):
                    self._last_yellow_emit_t = t_now
                    pct_str = (f"{cur_pct:.3f}"
                               if cur_pct is not None else "?")
                    print(f"[yellow-zone] idx={idx} bits=0x{cur_yellow_bits:x} "
                          f"at pct {pct_str}")
                    self._emit_incident(
                        idx, "lost_control", t_now,
                        f"local yellow raised (flags=0x{cur_yellow_bits:x})",
                    )

    # --- driver list --------------------------------------------------------
    def _build_driver_list(self) -> list:
        ir = self.ir
        if not ir["DriverInfo"]:
            return []
        drivers_raw = ir["DriverInfo"]["Drivers"] or []
        positions = ir["CarIdxClassPosition"] or []
        laps      = ir["CarIdxLap"] or []
        lap_pct   = ir["CarIdxLapDistPct"] or []
        on_pit    = ir["CarIdxOnPitRoad"] or []
        best_lap  = ir["CarIdxBestLapTime"] or []
        f2_time   = ir["CarIdxF2Time"] or []
        # Estimated lap time (track+car) — used as the lap-1 interval
        # fallback when CarIdxF2Time hasn't been populated yet.
        est_lap_time = ir["EstLapTime"] or 0.0
        if est_lap_time <= 0:
            est_lap_time = 100.0  # sane default

        rows = []
        for d in drivers_raw:
            idx = d.get("CarIdx")
            if idx is None:
                continue
            if d.get("CarIsPaceCar") == 1 or d.get("IsSpectator") == 1:
                continue
            rows.append({
                "car_idx":     idx,
                "name":        d.get("UserName", "") or "",
                "car_number":  d.get("CarNumber", "") or "",
                "car":         d.get("CarScreenNameShort") or d.get("CarScreenName", ""),
                "position":    positions[idx] if idx < len(positions) else 0,
                "lap":         laps[idx]      if idx < len(laps)      else 0,
                "lap_pct":     lap_pct[idx]   if idx < len(lap_pct)   else 0.0,
                "on_pit_road": bool(on_pit[idx]) if idx < len(on_pit) else False,
                "best_lap":    best_lap[idx] if idx < len(best_lap) else 0.0,
                "gap_to_leader": f2_time[idx] if idx < len(f2_time) else 0.0,
                "starred":     idx in self._starred_car_idxs,
            })
        rows.sort(key=lambda d: (d["position"] == 0, d["position"]))
        for i, r in enumerate(rows):
            if i == 0 or r["position"] == 0:
                r["gap_ahead"] = 0.0
            else:
                prev_r = rows[i - 1]
                # Prefer F2Time-based gap when either car has a populated
                # value > 0. During lap 1 both are often 0; fall back to
                # the lap_pct × EstLapTime estimate.
                my_f2   = r["gap_to_leader"]
                prev_f2 = prev_r["gap_to_leader"]
                if my_f2 > 0 or prev_f2 > 0:
                    g = my_f2 - prev_f2
                    r["gap_ahead"] = g if g > 0 else 0.0
                else:
                    pct_diff = prev_r["lap_pct"] - r["lap_pct"]
                    if pct_diff < 0:
                        pct_diff += 1.0  # leader wrapped S/F before this car
                    r["gap_ahead"] = pct_diff * est_lap_time
        return rows

    # --- auto-follow --------------------------------------------------------
    def _maybe_auto_switch(self, drivers: list, current_cam_idx):
        t_now = time.time()
        if not self.auto_follow:
            return
        if t_now < self._manual_override_until:
            return
        if t_now - self._last_auto_switch < self._auto_switch_min_interval:
            return

        starred_best = None
        any_best = None
        for i in range(1, len(drivers)):
            a = drivers[i]
            b = drivers[i - 1]
            if a["on_pit_road"] or b["on_pit_road"]:
                continue
            if a["position"] == 0 or b["position"] == 0:
                continue
            gap = a["gap_ahead"]
            if gap <= 0:
                continue
            involves_starred = a["starred"] or b["starred"]
            if involves_starred and gap <= 1.5:
                target = a if a["starred"] else b
                if starred_best is None or gap < starred_best[0]:
                    starred_best = (gap, target)
            if gap <= 1.0:
                if any_best is None or gap < any_best[0]:
                    any_best = (gap, a)

        choice = starred_best or any_best
        if choice is None:
            return
        target = choice[1]
        if target["car_idx"] == current_cam_idx:
            return
        try:
            self.ir.cam_switch_num(str(target["car_number"]), self._current_cam_group, 0)
            self._last_auto_switch = t_now
            self._reassert_ui_hide()
            tag = "*" if target["starred"] else "  "
            print(f"[auto-follow] {tag} -> #{target['car_number']} {target['name']} (gap {choice[0]:.2f}s)")
        except Exception as e:
            print(f"[auto-follow] switch failed: {e}")

    # --- focus on leader ----------------------------------------------------
    def _maybe_focus_leader(self, drivers: list, current_cam_idx):
        """Keep the camera locked on the overall P1 car while focus_leader
        is active. Switches only when the leader changes, so we don't
        hammer cam_switch_num at the poll rate."""
        if not self.focus_leader:
            return
        # Don't override a crash-focus hold window.
        t_now = time.time()
        if t_now < self._focus_crash_until:
            return
        # Find the overall P1 (position == 1). We pick the first driver
        # reported at position 1; in multi-class it's the overall leader.
        leader = None
        for d in drivers:
            if d.get("position") == 1:
                leader = d
                break
        if leader is None:
            return
        leader_idx = leader.get("car_idx")
        if leader_idx is None:
            return
        # Already on the leader? nothing to do.
        if leader_idx == self._last_leader_car_idx and leader_idx == current_cam_idx:
            return
        try:
            self.ir.cam_switch_num(
                str(leader["car_number"]), self._current_cam_group, 0
            )
            self._reassert_ui_hide()
            self._last_leader_car_idx = leader_idx
            print(f"[focus-leader] -> P1 #{leader['car_number']} {leader['name']}")
        except Exception as e:
            print(f"[focus-leader] switch failed: {e}")

    # --- camera disconnect watchdog ----------------------------------------
    def _maybe_recover_lost_cam_target(self, drivers: list, current_cam_idx):
        """If the camera target has been out-of-world for more than the
        grace window (default 3 s), proactively switch to a sensible
        fallback. This prevents iRacing from falling back to the scenic
        camera when a driver disconnects / disappears, which is jarring
        on a broadcast.

        Fallback priority:
          1. Current overall leader (P1) if in-world and not in pit
          2. Any in-world non-pit driver near the lost driver's position
          3. Any in-world driver at all
        """
        t_now = time.time()

        # Don't fight a recent manual click or a focus-crash hold window.
        if t_now < self._manual_override_until:
            self._cam_lost_since = None
            return
        if t_now < self._focus_crash_until:
            return

        # If focus_leader or auto_follow is active they'll re-pick on
        # their own; don't fight them either.
        if self.focus_leader or self.auto_follow:
            self._cam_lost_since = None
            return

        if current_cam_idx is None:
            self._cam_lost_since = None
            return

        surfaces = self.ir["CarIdxTrackSurface"] or []
        in_world = (current_cam_idx < len(surfaces)
                    and int(surfaces[current_cam_idx]) != SURFACE_NOT_IN_WORLD)

        if in_world:
            if self._cam_lost_since is not None:
                lost_for = t_now - self._cam_lost_since
                print(f"[cam-watchdog] target idx={current_cam_idx} back "
                      f"in world after {lost_for:.1f}s")
            self._cam_lost_since = None
            return

        # Out of world. Start the timer if not already running.
        if self._cam_lost_since is None:
            self._cam_lost_since = t_now
            return

        elapsed = t_now - self._cam_lost_since
        if elapsed < self._cam_lost_grace_seconds:
            return

        # Debounce: don't keep firing every tick — once we've switched,
        # wait at least 8 s before another watchdog switch (the new
        # target is presumably a real driver, but in degenerate cases
        # they could also be DC'd within seconds).
        if t_now - self._last_cam_recover_at < 8.0:
            return

        # Pick a fallback.
        fallback = self._pick_cam_fallback(drivers, current_cam_idx)
        if fallback is None:
            return

        try:
            self.ir.cam_switch_num(
                str(fallback["car_number"]),
                self._current_cam_group,
                0,
            )
            self._reassert_ui_hide()
            self._last_cam_recover_at = t_now
            self._cam_lost_since = None
            print(f"[cam-watchdog] target idx={current_cam_idx} "
                  f"out-of-world for {elapsed:.1f}s -> switched to "
                  f"#{fallback['car_number']} {fallback['name']}")
        except Exception as e:
            print(f"[cam-watchdog] switch failed: {e}")

    def _pick_cam_fallback(self, drivers: list, lost_cam_idx):
        """Pick a sensible camera target after the current one disappears."""
        surfaces = self.ir["CarIdxTrackSurface"] or []

        def is_eligible(d):
            idx = d.get("car_idx")
            if idx is None:
                return False
            if idx >= len(surfaces):
                return False
            if int(surfaces[idx]) == SURFACE_NOT_IN_WORLD:
                return False
            if d.get("on_pit_road"):
                return False
            return True

        # 1. Overall P1
        for d in drivers:
            if d.get("position") == 1 and is_eligible(d):
                return d

        # 2. Position-nearest driver to the one we lost.
        lost_pos = None
        for d in drivers:
            if d.get("car_idx") == lost_cam_idx:
                lost_pos = d.get("position")
                break
        if lost_pos:
            candidates = sorted(
                (d for d in drivers if is_eligible(d)),
                key=lambda d: abs((d.get("position") or 99) - lost_pos),
            )
            if candidates:
                return candidates[0]

        # 3. Anyone at all
        for d in drivers:
            if is_eligible(d):
                return d

        return None

    # --- race progress ------------------------------------------------------
    def _race_progress(self) -> dict:
        """
        Build the race progress summary: laps done/total and time elapsed/total
        for whichever dimensions apply to the current session.
        """
        ir = self.ir
        info = ir["SessionInfo"] or {}
        sessions = info.get("Sessions", []) or []
        session_num = ir["SessionNum"] or 0

        # Current session object (race / qual / practice)
        cur_sess = None
        for s in sessions:
            if s.get("SessionNum") == session_num:
                cur_sess = s
                break

        session_type = (cur_sess or {}).get("SessionType", "")
        session_name = (cur_sess or {}).get("SessionName", "")

        # Total race length
        total_laps = None
        total_time = None
        if cur_sess:
            laps_raw = cur_sess.get("SessionLaps", "")
            # "unlimited" or a number as string
            try:
                total_laps = int(laps_raw)
            except (TypeError, ValueError):
                total_laps = None
            time_raw = cur_sess.get("SessionTime", "")
            # SessionTime is a string like "5400.0000 sec" or just seconds
            try:
                if isinstance(time_raw, str):
                    total_time = float(time_raw.split()[0])
                else:
                    total_time = float(time_raw)
                if total_time <= 0:
                    total_time = None
            except (TypeError, ValueError):
                total_time = None

        # Live progress
        time_remaining = ir["SessionTimeRemain"]
        laps_remaining = ir["SessionLapsRemainEx"]
        if laps_remaining is None:
            laps_remaining = ir["SessionLapsRemain"]
        race_laps = ir["RaceLaps"] or 0

        # Laps done for the leader
        if total_laps and laps_remaining is not None and laps_remaining >= 0:
            laps_done = max(0, total_laps - laps_remaining)
        else:
            laps_done = race_laps

        # Sanity: if iRacing reports 32767 / negative for "unlimited", zero out
        if total_laps is not None and total_laps > 9000:
            total_laps = None
        if laps_remaining is not None and (laps_remaining < 0 or laps_remaining > 9000):
            laps_remaining = None
        if time_remaining is not None and (time_remaining < 0 or time_remaining > 1e8):
            time_remaining = None

        return {
            "session_type": session_type,
            "session_name": session_name,
            "total_laps":     total_laps,
            "laps_done":      laps_done,
            "laps_remaining": laps_remaining,
            "total_time":     total_time,
            "time_remaining": time_remaining,
        }

    # --- snapshot -----------------------------------------------------------
    def _read_snapshot(self) -> dict:
        ir = self.ir
        ir.freeze_var_buffer_latest()

        if not self._camera_groups:
            self._load_camera_groups()

        # First-time default: force TV3 instead of iRacing's Scenic.
        # Runs once per connection; retries silently until a car is available.
        if not self._default_camera_applied:
            self._apply_default_camera()

        self._update_sectors()
        self._update_incidents()

        cam_car_idx = ir["CamCarIdx"]
        cam_group   = ir["CamGroupNumber"]
        if cam_group is not None:
            self._current_cam_group = int(cam_group)

        drivers     = ir["DriverInfo"]["Drivers"] if ir["DriverInfo"] else []
        track_name  = ""
        if ir["WeekendInfo"]:
            track_name = ir["WeekendInfo"].get("TrackDisplayName", "")

        active_driver = None
        if cam_car_idx is not None:
            for d in drivers:
                if d.get("CarIdx") == cam_car_idx:
                    active_driver = {
                        "car_idx": cam_car_idx,
                        "name":    d.get("UserName", ""),
                        "car":     d.get("CarScreenName", ""),
                        "car_number": d.get("CarNumber", ""),
                    }
                    break

        driver_list = self._build_driver_list()
        self._maybe_auto_switch(driver_list, cam_car_idx)
        self._maybe_focus_leader(driver_list, cam_car_idx)
        self._maybe_recover_lost_cam_target(driver_list, cam_car_idx)

        snap = {
            "connected": True,
            "active_driver": active_driver,
            "track": track_name,
            "auto_follow": self.auto_follow,
            "auto_replay": self.auto_replay,
            "camera_groups": self._camera_groups,
            "current_cam_group": self._current_cam_group,
            "auto_camera_active": self.is_auto_camera_active(),
            "focus_leader": self.focus_leader,
            "focus_crashes": self.focus_crashes,
            "drivers": driver_list,
            "sectors": self._sector_snapshot(cam_car_idx),
            "incidents": list(self._incidents),   # newest first
            "session_time": ir["SessionTime"] or 0.0,
            "race_progress": self._race_progress(),
            "playback": self._playback_status(),
        }
        return snap

    def _playback_status(self) -> dict:
        """
        Determine whether we're watching live or replay.
        We're 'live' when the replay playhead is at (or within a few frames
        of) the end of the buffer AND playback is at 1x.
        """
        ir = self.ir
        frame = ir["ReplayFrameNum"]
        end   = ir["ReplayFrameNumEnd"]
        speed = ir["ReplayPlaySpeed"]
        slow  = ir["ReplayPlaySlowMotion"]

        at_end = False
        if frame is not None and end is not None:
            # Within ~1 second of the live edge counts as live (iRacing can
            # drift by a few frames while streaming).
            at_end = (end - frame) <= 60

        is_live = at_end and (speed == 1) and not slow

        # Human-friendly mode label
        if is_live:
            mode = "live"
        elif speed == 0:
            mode = "paused"
        elif speed is not None and speed < 0:
            mode = "rewind"
        elif speed is not None and speed > 1:
            mode = "fast_forward"
        elif slow:
            mode = "slow_motion"
        else:
            mode = "replay"

        return {
            "is_live":    bool(is_live),
            "mode":       mode,
            "speed":      speed if speed is not None else 0,
            "slow":       bool(slow),
            "frame":      frame if frame is not None else 0,
            "frame_end":  end if end is not None else 0,
        }

    def run(self):
        print("[telemetry] Poller started (waiting for iRacing...)")
        while self._running:
            try:
                if self._check_connection():
                    snap = self._read_snapshot()
                    with self._lock:
                        self.data = snap
                else:
                    with self._lock:
                        self.data = {"connected": False}
            except Exception as e:
                with self._lock:
                    self.data = {"connected": False, "error": str(e)}
            time.sleep(self.poll_interval)

    def get(self) -> dict:
        with self._lock:
            return dict(self.data)

    # --- controls -----------------------------------------------------------
    def switch_camera_to_car_number(self, car_number: str, group: int = None):
        if not self.connected:
            return False
        self._manual_override_until = time.time() + 10.0
        if group is None:
            group = self._current_cam_group
        self.ir.cam_switch_num(str(car_number), int(group), 0)
        self._reassert_ui_hide()
        return True

    def switch_camera_group(self, group: int):
        if not self.connected:
            return False
        # Flipping to a regular camera group implicitly turns all of the
        # automatic camera modes off — otherwise iRacing keeps hopping
        # cars and ignores your group choice.
        self.set_auto_camera(False)
        self.focus_leader = False
        self.focus_crashes = False
        self._current_cam_group = int(group)
        cam_car_idx = self.ir["CamCarIdx"]
        drivers     = self.ir["DriverInfo"]["Drivers"] if self.ir["DriverInfo"] else []
        car_number = None
        for d in drivers:
            if d.get("CarIdx") == cam_car_idx:
                car_number = d.get("CarNumber", "")
                break
        if car_number is None:
            return False
        self.ir.cam_switch_num(str(car_number), int(group), 0)
        self._reassert_ui_hide()
        return True

    # Bits in iRacing's CamCameraState bitfield (see irsdk_defines.h).
    CAM_TOOL_ACTIVE           = 0x0001
    CAM_UI_HIDDEN             = 0x0002
    CAM_USE_AUTO_SHOT         = 0x0004   # "Most Exciting"
    CAM_USE_TEMPORARY_EDITS   = 0x0008
    CAM_USE_KEY_ACCELERATION  = 0x0010
    CAM_USE_KEY_10X_ACCEL     = 0x0020
    CAM_USE_MOUSE_AIM_MODE    = 0x0040

    def set_auto_camera(self, enabled: bool) -> bool:
        """
        Turn iRacing's "Most Exciting" auto-shot selection on or off.
        That's CamUseAutoShotSelection (bit 0x0004) in the camera state
        bitfield.

        We write ONLY the auto-shot bit (plus whatever UI_HIDDEN bit was
        already set). We do NOT set CAM_TOOL_ACTIVE — that bit puts
        iRacing into "camera tool" mode which itself triggers iRacing to
        show its broadcast/camera UI. Earlier versions of this method
        were unconditionally setting CAM_TOOL_ACTIVE, which is why the
        HUD was popping up on every camera change.

        IDEMPOTENT: if the current CamCameraState already reflects the
        desired auto-shot setting, we do NOT call cam_set_state at all.
        Avoids gratuitous SDK writes that can disturb the HUD.
        """
        if not self.connected:
            return False
        try:
            current = int(self.ir["CamCameraState"] or 0)
            currently_auto = bool(current & self.CAM_USE_AUTO_SHOT)
            if currently_auto == bool(enabled):
                # Already in the right state — skip the SDK call entirely.
                return True
            # Build new state: preserve everything except the auto-shot bit.
            state = current & ~self.CAM_USE_AUTO_SHOT
            if enabled:
                state |= self.CAM_USE_AUTO_SHOT
            self.ir.cam_set_state(state)
            self._reassert_ui_hide()
            return True
        except Exception as e:
            print(f"[dashboard] cam_set_state failed: {e}")
            return False

    def is_auto_camera_active(self) -> bool:
        """Read current CamCameraState bitfield, check the auto-shot bit."""
        try:
            state = self.ir["CamCameraState"]
            if state is None:
                return False
            return bool(int(state) & self.CAM_USE_AUTO_SHOT)
        except Exception:
            return False

    def _reassert_ui_hide(self):
        """
        iRacing's broadcast HUD pops back up on every camera switch.
        If the user has asked to keep it hidden, re-send spacebar a beat
        after the switch so the HUD goes away again. Done in a small
        delayed thread so iRacing has time to process the camera change
        before we re-hide.
        """
        if not self.iracing_ui_hidden:
            return
        def _worker():
            time.sleep(0.25)
            try:
                send_key_to_iracing(VK_SPACE)
            except Exception as e:
                print(f"[dashboard] ui-rehide failed: {e}")
        threading.Thread(target=_worker, daemon=True).start()

    def replay_5s_of_car(self, car_number: str,
                         t_session: float | None = None,
                         session_num: int | None = None,
                         rewind_seconds: float = 10.0,
                         buildup_seconds: float = 5.0):
        """
        Show a replay of an incident: switch camera to `car_number`, seek the
        replay playhead to BEFORE the incident, play at 1x, then auto-return
        to live AND to the car we were previously watching.

        Preferred call-path: pass the incident's `t_session` + `session_num`.
        We then seek absolutely to `(t_session - buildup_seconds)` via
        `replay_search_session_time`, so the replay reliably covers the actual
        accident no matter how long after it the user clicks.

        `buildup_seconds` is how much time BEFORE the incident timestamp we
        start the replay. 5s is fine for spins (the "lost_control" path —
        detected immediately when the car moves backward). For "collision"
        events we use more because the vanish detector fires when iRacing
        decides to tow the car, which can be 10-30s after the actual impact.

        Fallback (auto-replay path or missing id): rewind `rewind_seconds`
        from the CURRENT session time. Works fine as long as the click
        happens right after the event.
        """
        if not self.connected:
            return False, "not connected"
        try:
            # 0) Remember which car we were watching so we can restore it.
            original_car_number = None
            prev_cam_idx = self.ir["CamCarIdx"]
            drivers_info = self.ir["DriverInfo"]
            if drivers_info and prev_cam_idx is not None:
                for d in drivers_info.get("Drivers", []) or []:
                    if d.get("CarIdx") == prev_cam_idx:
                        original_car_number = str(d.get("CarNumber", "")) or None
                        break
            cam_group = self._current_cam_group

            # 1) Compute the absolute target session time.
            if t_session is not None:
                target_time_s = max(0.0, float(t_session) - float(buildup_seconds))
                seek_desc = (f"incident_time={t_session:.1f}s -> "
                             f"{target_time_s:.1f}s (buildup={buildup_seconds:.0f}s)")
                sess_num = int(session_num) if session_num is not None \
                           else int(self.ir["SessionNum"] or 0)
            else:
                # Fallback: rewind from now
                cur = self.ir["SessionTime"]
                if cur is None:
                    return False, "session time unavailable"
                target_time_s = max(0.0, float(cur) - float(rewind_seconds))
                seek_desc = (f"now={cur:.1f}s -> {target_time_s:.1f}s "
                             f"(-{rewind_seconds:.0f}s)")
                sess_num = int(self.ir["SessionNum"] or 0)
            target_time_ms = int(target_time_s * 1000.0)

            # --- DIAG: snapshot iRacing's current state before we touch anything.
            before_frame = self.ir["ReplayFrameNum"]
            before_sess_t = self.ir["SessionTime"]
            before_is_replay = self.ir["IsReplayPlaying"]

            # 2) PAUSE first. Going from live playback directly to a
            #    `replay_search_session_time` call is unreliable — iRacing
            #    will sometimes honour the seek only on the next cam
            #    switch, which then visually looks like "the button just
            #    jumped to the car without rewinding". Pausing first
            #    forces iRacing into replay mode cleanly.
            self.ir.replay_set_play_speed(0, False)
            time.sleep(0.10)

            # 3) Seek to the absolute target session timestamp.
            self.ir.replay_search_session_time(sess_num, target_time_ms)

            # Let iRacing process the seek before we read back state.
            time.sleep(0.30)
            after_seek_frame = self.ir["ReplayFrameNum"]
            after_seek_sess_t = self.ir["SessionTime"]

            # 4) Switch camera to the incident car. AFTER the seek so the
            #    camera change doesn't nudge us back to live.
            self.ir.cam_switch_num(str(car_number), cam_group, 0)
            self._reassert_ui_hide()

            # 5) iRacing needs a beat to finish loading frames at the new
            #    playhead position before it will honour a play-speed change.
            time.sleep(0.30)
            after_cam_frame = self.ir["ReplayFrameNum"]

            # 6) Resume playback at 1x speed.
            self.ir.replay_set_play_speed(1, False)

            # 7) Pause auto-follow for the replay window + buffer.
            self._manual_override_until = time.time() + rewind_seconds + 5.0

            print(f"[replay] #{car_number} sess={sess_num} {seek_desc}")
            print(f"[replay] diag  before:     frame={before_frame} "
                  f"sess_t={before_sess_t} is_replay={before_is_replay}")
            print(f"[replay] diag  after_seek: frame={after_seek_frame} "
                  f"sess_t={after_seek_sess_t}")
            print(f"[replay] diag  after_cam:  frame={after_cam_frame}")
            # Sanity check: did the seek actually move the playhead?
            try:
                if (before_frame is not None and after_seek_frame is not None
                        and abs(after_seek_frame - before_frame) < 60):
                    print(f"[replay] WARNING: seek did not appear to move "
                          f"the playhead (delta={after_seek_frame - before_frame} "
                          f"frames). iRacing may have rejected the seek.")
            except Exception:
                pass

            # 7) Auto-return to live + restore original camera.
            def _return_to_live_and_previous_car():
                time.sleep(rewind_seconds + 1.5)
                if not self.connected:
                    return
                try:
                    self.ir.replay_search(irsdk.RpySrchMode.to_end)
                    self.ir.replay_set_play_speed(1, False)
                    if original_car_number:
                        self.ir.cam_switch_num(original_car_number, cam_group, 0)
                        self._reassert_ui_hide()
                        print(f"[replay] back to live, camera on #{original_car_number}")
                    else:
                        print("[replay] back to live")
                except Exception as e:
                    print(f"[replay] auto-return failed: {e}")

            threading.Thread(target=_return_to_live_and_previous_car, daemon=True).start()
            return True, "ok"
        except Exception as e:
            return False, str(e)

    def go_live(self):
        """Jump the replay playhead to the latest frame (= live playback)."""
        if not self.connected:
            return False, "not connected"
        try:
            # The correct enum is RpySrchMode.to_end (value 1), NOT
            # "to_session_end" which doesn't exist in pyirsdk.
            self.ir.replay_search(irsdk.RpySrchMode.to_end)
            # Make sure playback is at 1x (not paused or slow-mo).
            self.ir.replay_set_play_speed(1, False)
            return True, "ok"
        except Exception as e:
            return False, str(e)

    def set_auto_follow(self, enabled: bool):
        self.auto_follow = bool(enabled)
        if self.auto_follow:
            # Other automatic camera modes are mutually exclusive — they'd
            # fight each other over the camera otherwise.
            self.focus_leader = False
            self.focus_crashes = False
            self.set_auto_camera(False)
        print(f"[auto-follow] {'ENABLED' if self.auto_follow else 'disabled'}")

    def set_auto_replay(self, enabled: bool):
        self.auto_replay = bool(enabled)
        print(f"[auto-replay] {'ENABLED' if self.auto_replay else 'disabled'}")

    def set_focus_leader(self, enabled: bool):
        self.focus_leader = bool(enabled)
        if self.focus_leader:
            # Radio-button style: turning on focus-leader disables the
            # other automatic camera modes so they don't fight.
            self.auto_follow = False
            self.focus_crashes = False
            self.set_auto_camera(False)
            # Force the next tick to actually re-resolve the leader,
            # since we cleared other modes that may have set the cam.
            self._last_leader_car_idx = None
        print(f"[focus-leader] {'ENABLED' if self.focus_leader else 'disabled'}")

    def set_focus_crashes(self, enabled: bool):
        self.focus_crashes = bool(enabled)
        if self.focus_crashes:
            self.auto_follow = False
            self.focus_leader = False
            self.set_auto_camera(False)
            self._focus_crash_until = 0.0
        print(f"[focus-crashes] {'ENABLED' if self.focus_crashes else 'disabled'}")

    def set_starred_bulk(self, car_idxs: list):
        self._starred_car_idxs = set(int(x) for x in car_idxs)

    def dismiss_incident(self, incident_id: int):
        # Rebuild deque without the matching entry
        new_items = [i for i in self._incidents if i["id"] != incident_id]
        self._incidents.clear()
        for it in new_items:
            self._incidents.append(it)

    def clear_incidents(self):
        self._incidents.clear()

    def stop(self):
        self._running = False
        if self.connected:
            self.ir.shutdown()


# -----------------------------------------------------------------------------
# Flask app
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
poller = TelemetryPoller(poll_hz=10)


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Broadcast Dashboard</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: 'Segoe UI', system-ui, sans-serif;
        background: #0a0a0f; color: #e8e8ea;
        min-height: 100vh; padding: 16px;
    }

    /* Stream mode keeps the dashboard clean for OBS browser sources */
    body.stream-mode { background: transparent; padding: 12px; }
    body.stream-mode .header { display: none !important; }
    body.stream-mode .card,
    body.stream-mode .panel { background: rgba(20, 20, 28, 0.85); }

    .layout {
        display: grid;
        /* Drivers (left) widened from 360 -> 480 so long names fit
           without truncation. Camera column is 1fr and auto-shrinks;
           the camera-group buttons use flex-wrap so they just flow
           onto more rows as needed. */
        grid-template-columns: 480px 1fr 340px;
        gap: 16px;
        min-height: calc(100vh - 32px);
    }
    body.stream-mode .layout { min-height: auto; }

    .header {
        display: flex; justify-content: space-between; align-items: center;
        padding-bottom: 12px; border-bottom: 1px solid #222; margin-bottom: 16px;
    }
    h1 { font-size: 18px; font-weight: 600; color: #9146FF; }
    .status { padding: 6px 14px; border-radius: 4px; font-size: 12px; font-weight: 600; }
    .status.connected    { background: #1a4d2e; color: #6fe398; }
    .status.disconnected { background: #4d1a1a; color: #ff8080; }
    .meta { font-size: 12px; color: #888; }

    /* Race progress strip */
    .race-progress {
        background: #14141c;
        border: 1px solid #222;
        border-radius: 8px;
        padding: 14px 18px;
        margin-bottom: 16px;
        display: grid;
        grid-template-columns: 1.2fr 1fr 0.9fr 1fr 0.9fr auto;
        gap: 24px;
        align-items: center;
    }
    body.stream-mode .race-progress { background: rgba(20,20,28,0.85); }
    .rp-block { display: flex; flex-direction: column; gap: 4px; }
    .rp-label {
        font-size: 12px; text-transform: uppercase; letter-spacing: 1.5px;
        color: #9a9aa8; font-weight: 600;
    }
    .rp-value {
        font-size: 28px; font-weight: 700; font-family: monospace;
        color: #f2f2f5;
    }
    .rp-value .rp-sep { color: #555; margin: 0 4px; font-weight: 400; }
    .rp-value .rp-total { color: #888; font-weight: 400; }
    .rp-value.rp-remaining { color: #facc15; }
    .rp-bar {
        height: 6px; background: #1a1a22; border-radius: 3px;
        overflow: hidden; margin-top: 4px;
    }
    .rp-fill { height: 100%; transition: width 0.3s linear; }
    .rp-fill.laps { background: #9146FF; }
    .rp-fill.time { background: #DC0028; }

    /* Live indicator pill */
    .live-pill {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 6px 12px; border-radius: 20px;
        font-size: 13px; font-weight: 700; letter-spacing: 1px;
        background: #1a1a22; color: #888;
        border: 1px solid #333;
        min-width: 110px; justify-content: center;
        text-transform: uppercase;
    }
    .live-pill .live-dot {
        width: 10px; height: 10px; border-radius: 50%;
        background: #555;
    }
    .live-pill.live {
        background: #4d1a1a; color: #ff6b6b; border-color: #DC0028;
    }
    .live-pill.live .live-dot {
        background: #ff3347;
        box-shadow: 0 0 0 0 rgba(255, 51, 71, 0.9);
        animation: live-pulse 1.6s infinite ease-in-out;
    }
    @keyframes live-pulse {
        0%   { box-shadow: 0 0 0 0 rgba(255, 51, 71, 0.7); }
        70%  { box-shadow: 0 0 0 8px rgba(255, 51, 71, 0);  }
        100% { box-shadow: 0 0 0 0 rgba(255, 51, 71, 0);  }
    }
    .live-pill.paused { color: #facc15; border-color: #866a12; }
    .live-pill.paused .live-dot { background: #facc15; }
    .live-pill.replay, .live-pill.rewind, .live-pill.fast_forward, .live-pill.slow_motion {
        color: #60a5fa; border-color: #2b4e7e;
    }
    .live-pill.replay .live-dot,
    .live-pill.rewind .live-dot,
    .live-pill.fast_forward .live-dot,
    .live-pill.slow_motion .live-dot { background: #60a5fa; }

    /* Floating controls (top-right, always on top) */
    .floating-controls {
        position: fixed; top: 12px; right: 12px; z-index: 1000;
        display: flex; gap: 8px;
    }
    .fbtn {
        background: rgba(20, 20, 28, 0.9);
        border: 1px solid #333;
        color: #bbb;
        padding: 8px 14px;
        border-radius: 4px;
        font-size: 12px; font-weight: 600;
        cursor: pointer;
        display: flex; align-items: center; gap: 8px;
        user-select: none;
        transition: opacity 0.2s;
    }
    .fbtn:hover { background: rgba(42, 31, 74, 0.9); color: #e8e8ea; }
    .fbtn.on { background: #DC0028; border-color: #ff334f; color: #fff; }
    .fbtn-live {
        background: #1a4d2e; border-color: #3aa566; color: #6fe398;
    }
    .fbtn-live:hover { background: #216338; color: #a5f3c5; }
    .fbtn-live.flash { background: #4ade80; color: #000; }
    /* When we're NOT live, nudge the user by making the button pulse red-ish */
    .fbtn-live.needs-attention {
        background: #4d1a1a; border-color: #DC0028; color: #ff8080;
        animation: needs-attn-pulse 2s infinite ease-in-out;
    }
    @keyframes needs-attn-pulse {
        0%, 100% { box-shadow: 0 0 0 0 rgba(220, 0, 40, 0.5); }
        50%      { box-shadow: 0 0 0 6px rgba(220, 0, 40, 0);  }
    }
    .fbtn .kbd {
        background: rgba(255,255,255,0.12);
        padding: 1px 6px; border-radius: 3px; font-size: 10px; font-family: monospace;
    }
    body.stream-mode .floating-controls { opacity: 0.15; }
    body.stream-mode .floating-controls:hover { opacity: 1; }

    /* Panels */
    .panel {
        background: #14141c; border: 1px solid #222; border-radius: 8px;
        padding: 12px; overflow-y: auto; max-height: calc(100vh - 80px);
    }
    .panel h2 {
        font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
        color: #888; margin-bottom: 10px;
        display: flex; justify-content: space-between; align-items: center;
    }

    /* Driver sidebar */
    .controls { display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; }
    .nav-buttons { display: flex; gap: 6px; }
    .nav-buttons button {
        flex: 1; padding: 8px;
        background: #1f1f2a; border: 1px solid #333;
        color: #e8e8ea; border-radius: 4px; cursor: pointer; font-size: 13px;
    }
    .nav-buttons button:hover { background: #2a2a38; }

    .toggle-btn {
        padding: 10px; border-radius: 4px; cursor: pointer;
        font-size: 12px; font-weight: 600;
        background: #1f1f2a; border: 1px solid #333; color: #e8e8ea;
        display: flex; justify-content: space-between; align-items: center;
    }
    .toggle-btn.on {
        background: linear-gradient(90deg, #2a1f4a, #1f1f2a);
        border-color: #9146FF; color: #d4c5ff;
    }
    .toggle-indicator {
        display: inline-block; width: 28px; height: 14px;
        background: #333; border-radius: 7px; position: relative; transition: background 0.2s;
    }
    .toggle-indicator::after {
        content: ""; position: absolute; top: 2px; left: 2px;
        width: 10px; height: 10px; background: #888; border-radius: 50%;
        transition: all 0.2s;
    }
    .toggle-btn.on .toggle-indicator { background: #9146FF; }
    .toggle-btn.on .toggle-indicator::after { left: 16px; background: #fff; }

    .filter-row { display: flex; gap: 8px; font-size: 13px; color: #9a9aa8; align-items: center; }
    .filter-row label { cursor: pointer; user-select: none; }
    .filter-row input { margin-right: 4px; }

    .driver-row {
        display: grid;
        grid-template-columns: 22px 30px 42px 1fr 64px 60px;
        gap: 6px; padding: 10px 8px; border-radius: 4px; cursor: pointer;
        align-items: center; font-size: 15px;
        border: 1px solid transparent; margin-bottom: 2px;
    }
    .driver-row:hover { background: #1a1a24; }
    .driver-row.active { background: #2a1f4a; border-color: #9146FF; }
    .star { font-size: 17px; text-align: center; cursor: pointer; line-height: 1; color: #444; }
    .star.on { color: #facc15; }
    .star:hover { color: #facc15; }
    .pos { font-weight: 700; color: #DC0028; text-align: center; font-size: 16px; }
    .num {
        background: #1f1f2a; padding: 3px 6px; border-radius: 3px;
        text-align: center; font-family: monospace; font-size: 14px;
    }
    .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .time-small { font-size: 13px; color: #888; font-family: monospace; text-align: right; }
    .gap { font-size: 13px; color: #bbb; font-family: monospace; text-align: right; }
    .gap.battle { color: #facc15; font-weight: 700; }
    .pit-pill {
        background: #facc15; color: #000; font-size: 11px; font-weight: 700;
        padding: 2px 5px; border-radius: 2px; margin-left: 4px;
    }
    .header-row {
        display: grid; grid-template-columns: 22px 30px 42px 1fr 64px 60px;
        gap: 6px; font-size: 11px; color: #666;
        padding: 5px 8px; text-transform: uppercase; letter-spacing: 1px;
    }

    /* Middle column */
    .main { display: flex; flex-direction: column; gap: 16px; }

    .cam-bar {
        display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    }
    .cam-bar .label {
        font-size: 13px; text-transform: uppercase; letter-spacing: 1.5px; color: #aaa; font-weight: 700;
    }
    .cam-btn {
        padding: 12px 22px; border-radius: 6px; font-size: 17px; font-weight: 600;
        cursor: pointer;
        background: #1f1f2a; border: 1px solid #333; color: #ddd;
    }
    .cam-btn:hover { background: #2a2a38; color: #fff; }
    .cam-btn.active { background: #DC0028; border-color: #ff334f; color: #fff; font-weight: 700; }

    /* "Most Exciting" auto-shot button — sits in the camera bar but is
       conceptually a mode toggle, not a camera group. Coloured distinctly
       so it reads as a separate thing. */
    .cam-btn-auto {
        letter-spacing: 1px;
        text-transform: uppercase;
        background: #1a1a2a;
        border-color: #3a3a4e;
        color: #d8b8ff;
    }
    .cam-btn-auto:hover { background: #2a2338; color: #ecd8ff; border-color: #6b3aff; }
    .cam-btn-auto.active {
        background: #6b3aff;
        border-color: #9b6bff;
        color: #fff;
        box-shadow: 0 0 12px rgba(107, 58, 255, 0.5);
    }

    /* Focus on Leader — gold accent, evokes P1 / championship */
    .cam-btn-leader {
        letter-spacing: 1px;
        text-transform: uppercase;
        background: #1f1a0a;
        border-color: #4a3a1a;
        color: #ffd166;
    }
    .cam-btn-leader:hover { background: #2e240f; color: #ffe499; border-color: #ffd166; }
    .cam-btn-leader.active {
        background: #b78c18;
        border-color: #ffd166;
        color: #0a0a0f;
        box-shadow: 0 0 12px rgba(255, 209, 102, 0.55);
    }

    /* Focus on Crashes — red accent, evokes incident / danger */
    .cam-btn-crashes {
        letter-spacing: 1px;
        text-transform: uppercase;
        background: #1f1013;
        border-color: #4a1a22;
        color: #ff8a8a;
    }
    .cam-btn-crashes:hover { background: #2e1219; color: #ffb0b0; border-color: #ef4444; }
    .cam-btn-crashes.active {
        background: #b32222;
        border-color: #ef4444;
        color: #fff;
        box-shadow: 0 0 12px rgba(239, 68, 68, 0.55);
    }

    .active-banner {
        background: linear-gradient(90deg, #2a1f4a, #14141c);
        border: 1px solid #9146FF; border-radius: 8px;
        padding: 14px 18px; font-size: 17px;
    }
    .active-banner .driver-name { font-size: 22px; font-weight: 700; color: #9146FF; }

    .card { background: #14141c; border: 1px solid #222; border-radius: 8px; padding: 14px; }
    .card h2 {
        font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
        color: #888; margin-bottom: 10px;
    }

    /* Sector-times CSS removed along with the UI. */

    /* Incident feed */
    .incident {
        background: #1a1a22; border: 1px solid #2a2a2a;
        border-left: 4px solid #888;
        border-radius: 4px; padding: 12px; margin-bottom: 10px;
        font-size: 15px;
    }
    .incident.off_track       { border-left-color: #facc15; }
    .incident.lost_control    { border-left-color: #c084fc; }
    .incident.collision       { border-left-color: #ef4444; }
    /* Legacy accents kept for any already-queued incidents from older sessions. */
    .incident.stopped         { border-left-color: #f87171; }
    .incident.yellow          { border-left-color: #facc15; }
    .incident.incident_points { border-left-color: #ef4444; }

    .incident-head {
        display: flex; justify-content: space-between; align-items: center;
        margin-bottom: 6px;
    }
    .incident-type {
        font-size: 13px; font-weight: 700; text-transform: uppercase;
        letter-spacing: 1px;
    }
    .incident.off_track .incident-type       { color: #facc15; }
    .incident.lost_control .incident-type    { color: #c084fc; }
    .incident.collision .incident-type       { color: #ef4444; }
    /* Legacy accents kept for any already-queued incidents from older sessions. */
    .incident.stopped .incident-type         { color: #f87171; }
    .incident.yellow .incident-type          { color: #facc15; }
    .incident.incident_points .incident-type { color: #ef4444; }

    .incident-dismiss {
        background: transparent; border: none; color: #555; cursor: pointer;
        font-size: 14px; padding: 0 4px;
    }
    .incident-dismiss:hover { color: #ff8080; }
    .incident-driver { font-weight: 600; font-size: 16px; margin-bottom: 4px; }
    .incident-driver .num-small {
        background: #1f1f2a; padding: 2px 6px; border-radius: 3px;
        font-family: monospace; font-size: 13px; margin-right: 5px;
    }
    .incident-details { color: #888; font-size: 13px; margin-bottom: 10px; }
    .incident-buttons { display: flex; gap: 8px; }
    .incident-buttons button {
        flex: 1; padding: 9px 10px; font-size: 14px; font-weight: 600;
        border-radius: 4px; cursor: pointer; border: 1px solid #333;
        font-weight: 600;
    }
    .btn-jump {
        background: #2a1f4a; color: #d4c5ff; border-color: #9146FF;
    }
    .btn-jump:hover { background: #3a2d5f; }
    .btn-replay {
        background: #4d1a1a; color: #ff8080; border-color: #DC0028;
    }
    .btn-replay:hover { background: #6b2424; }

    .no-incidents {
        color: #555; font-size: 12px; text-align: center; padding: 24px 0;
        font-style: italic;
    }
</style>
</head>
<body>

<div class="floating-controls">
    <div class="fbtn fbtn-live" id="go-live-btn" onclick="goLive()" title="Jump the iRacing view to live playback">
        <span>🔴 Go Live</span>
        <span class="kbd">L</span>
    </div>
    <div class="fbtn" id="hide-iracing-btn" onclick="hideIracingUI()" title="Send Space key to iRacing to toggle its in-game UI">
        <span>🎮 Hide iRacing UI</span>
        <span class="kbd">SPACE</span>
    </div>
    <div class="fbtn" id="stream-toggle" onclick="toggleStreamMode()">
        <span id="stream-toggle-label">📺 Stream mode</span>
        <span class="kbd">H</span>
    </div>
</div>

<div class="header">
    <h1>🏎️  iRacing Broadcast Dashboard</h1>
    <div style="display: flex; gap: 12px; align-items: center;">
        <div class="meta" id="meta"></div>
        <div id="status" class="status disconnected">DISCONNECTED</div>
    </div>
</div>

<!-- Race progress strip: spans full width, above the 3-column layout -->
<div class="race-progress" id="race-progress">
    <div class="rp-block">
        <div class="rp-label">Session</div>
        <div class="rp-value" id="rp-session-name">—</div>
    </div>
    <div class="rp-block">
        <div class="rp-label">Laps</div>
        <div class="rp-value"><span id="rp-laps-done">—</span><span class="rp-sep">/</span><span id="rp-laps-total" class="rp-total">—</span></div>
        <div class="rp-bar"><div id="rp-laps-fill" class="rp-fill laps" style="width:0%"></div></div>
    </div>
    <div class="rp-block">
        <div class="rp-label">Laps remaining</div>
        <div class="rp-value rp-remaining" id="rp-laps-remaining">—</div>
    </div>
    <div class="rp-block">
        <div class="rp-label">Time elapsed</div>
        <div class="rp-value"><span id="rp-time-done">—</span><span class="rp-sep">/</span><span id="rp-time-total" class="rp-total">—</span></div>
        <div class="rp-bar"><div id="rp-time-fill" class="rp-fill time" style="width:0%"></div></div>
    </div>
    <div class="rp-block">
        <div class="rp-label">Time remaining</div>
        <div class="rp-value rp-remaining" id="rp-time-remaining">—</div>
    </div>
    <div class="rp-block rp-playback">
        <div class="rp-label">Playback</div>
        <div class="live-pill" id="live-pill">
            <span class="live-dot"></span>
            <span id="live-text">—</span>
        </div>
    </div>
</div>

<div class="layout">


    <!-- LEFT: driver list -->
    <div class="panel">
        <h2><span>Drivers</span><span id="driver-count" style="color:#555">0</span></h2>
        <div class="controls">
            <div class="toggle-btn" id="auto-follow-btn" onclick="toggleAutoFollow()">
                <span>Auto-follow battles ⭐ first</span>
                <span class="toggle-indicator"></span>
            </div>
            <div class="nav-buttons">
                <button onclick="navDriver(-1)">◀ Prev</button>
                <button onclick="navDriver(+1)">Next ▶</button>
            </div>
            <div class="filter-row">
                <label><input type="checkbox" id="filter-starred" onchange="onFilterChange()">Starred only</label>
                <span style="flex:1"></span>
                <span id="starred-count" style="color:#facc15">⭐ 0</span>
            </div>
        </div>
        <div class="header-row">
            <div></div>
            <div style="text-align:center">P</div>
            <div style="text-align:center">#</div>
            <div>Driver</div>
            <div style="text-align:right">Best</div>
            <div style="text-align:right">Gap</div>
        </div>
        <div id="driver-list"></div>
    </div>

    <!-- MIDDLE: camera + sectors + active driver -->
    <div class="main">
        <div class="active-banner">
            <div class="meta">Camera on:</div>
            <div class="driver-name" id="active-name">—</div>
            <div class="meta" id="active-car">—</div>
        </div>

        <div class="card">
            <h2>Camera Angle</h2>
            <div class="cam-bar">
                <div id="cam-buttons" style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;"></div>
            </div>
        </div>

        <!-- Sector Times card removed -->
    </div>

    <!-- RIGHT: incident feed -->
    <div class="panel">
        <h2>
            <span>Incidents</span>
            <button onclick="clearIncidents()" style="background:#1f1f2a; border:1px solid #333; color:#888; font-size:10px; padding:3px 8px; border-radius:3px; cursor:pointer;">Clear all</button>
        </h2>
        <div class="toggle-btn" id="auto-replay-btn" onclick="toggleAutoReplay()" style="margin-bottom:10px;">
            <span>Auto-replay incidents</span>
            <span class="toggle-indicator"></span>
        </div>
        <div id="incident-list"></div>
    </div>
</div>

<!-- (confirmation modal for Replay 5s removed - button now fires directly) -->

<script>
const STARRED_KEY = "iracing_dashboard_starred_v1";
const STREAM_MODE_KEY = "iracing_dashboard_stream_mode_v1";

let currentDrivers = [];
let activeCarIdx   = null;
let autoFollow     = false;
let autoReplay     = false;
let cameraGroups   = [];
let currentCamGroup = 0;
let showStarredOnly = false;
let dismissedIncidentIds = new Set();

// --- stream mode -----------------------------------------------------------
function applyStreamMode(on) {
    document.body.classList.toggle("stream-mode", on);
    document.getElementById("stream-toggle").classList.toggle("on", on);
    document.getElementById("stream-toggle-label").textContent =
        on ? "📺 Stream mode ON" : "📺 Stream mode";
    try { localStorage.setItem(STREAM_MODE_KEY, on ? "1" : "0"); } catch (e) {}
}
function toggleStreamMode() { applyStreamMode(!document.body.classList.contains("stream-mode")); }
try { if (localStorage.getItem(STREAM_MODE_KEY) === "1") applyStreamMode(true); } catch (e) {}

// --- hide iRacing UI -------------------------------------------------------
async function hideIracingUI() {
    const btn = document.getElementById("hide-iracing-btn");
    btn.classList.add("on");
    try {
        const r = await fetch("/hide_iracing_ui", { method: "POST" });
        const d = await r.json();
        if (!d.ok) { console.warn("hide iRacing UI:", d.message); btn.title = "Failed: " + d.message; }
    } catch (e) { console.error(e); }
    setTimeout(() => btn.classList.remove("on"), 250);
}

// --- go live (jump playback to end of replay buffer = live) ----------------
async function goLive() {
    const btn = document.getElementById("go-live-btn");
    btn.classList.add("flash");
    try {
        const r = await fetch("/go_live", { method: "POST" });
        const d = await r.json();
        if (!d.ok) { console.warn("go live:", d.message); btn.title = "Failed: " + d.message; }
    } catch (e) { console.error(e); }
    setTimeout(() => btn.classList.remove("flash"), 300);
}

// --- starred set -----------------------------------------------------------
function loadStarred() {
    try { const raw = localStorage.getItem(STARRED_KEY); return new Set(raw ? JSON.parse(raw) : []); }
    catch (e) { return new Set(); }
}
function saveStarred(set) { localStorage.setItem(STARRED_KEY, JSON.stringify([...set])); }
let starredSet = loadStarred();
async function pushStarredToServer() {
    try { await fetch("/starred", { method: "POST", headers: {"Content-Type": "application/json"},
                                    body: JSON.stringify({car_idxs: [...starredSet]}) });
    } catch (e) { console.error(e); }
}
pushStarredToServer();

// --- formatting ------------------------------------------------------------
function fmtLap(sec) {
    if (!sec || sec <= 0) return "--:--.---";
    const m = Math.floor(sec / 60);
    const s = (sec - m * 60).toFixed(3).padStart(6, "0");
    return `${m}:${s}`;
}
function fmtSector(sec) { return (!sec || sec <= 0) ? "--.---" : sec.toFixed(3); }
function fmtGap(sec) {
    if (!sec || sec <= 0) return "—";
    if (sec < 10) return "+" + sec.toFixed(2);
    return "+" + sec.toFixed(1);
}
function fmtIncidentTime(wall) {
    const dt = new Date(wall * 1000);
    return dt.toTimeString().slice(0, 8);
}
function fmtDuration(sec) {
    if (sec === null || sec === undefined || sec < 0) return "—";
    sec = Math.max(0, Math.round(sec));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    if (h > 0) return `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
    return `${m}:${String(s).padStart(2,"0")}`;
}
function renderRaceProgress(p) {
    if (!p) return;
    // Session name
    const sname = p.session_name || p.session_type || "—";
    document.getElementById("rp-session-name").textContent = sname;

    // Laps
    const lapsDone  = (p.laps_done !== null && p.laps_done !== undefined) ? p.laps_done : null;
    const lapsTotal = p.total_laps;
    const lapsRem   = p.laps_remaining;
    document.getElementById("rp-laps-done").textContent = (lapsDone !== null) ? lapsDone : "—";
    document.getElementById("rp-laps-total").textContent = (lapsTotal !== null && lapsTotal !== undefined) ? lapsTotal : "∞";
    document.getElementById("rp-laps-remaining").textContent =
        (lapsRem !== null && lapsRem !== undefined) ? lapsRem : "—";
    let lapsPct = 0;
    if (lapsTotal && lapsDone !== null) lapsPct = Math.min(100, 100 * lapsDone / lapsTotal);
    document.getElementById("rp-laps-fill").style.width = lapsPct + "%";

    // Time
    const tTotal = p.total_time;
    const tRem   = p.time_remaining;
    let tDone = null;
    if (tTotal !== null && tTotal !== undefined && tRem !== null && tRem !== undefined) {
        tDone = Math.max(0, tTotal - tRem);
    }
    document.getElementById("rp-time-done").textContent  = fmtDuration(tDone);
    document.getElementById("rp-time-total").textContent = (tTotal !== null && tTotal !== undefined) ? fmtDuration(tTotal) : "∞";
    document.getElementById("rp-time-remaining").textContent = fmtDuration(tRem);
    let timePct = 0;
    if (tTotal && tDone !== null) timePct = Math.min(100, 100 * tDone / tTotal);
    document.getElementById("rp-time-fill").style.width = timePct + "%";
}

const PLAYBACK_LABELS = {
    "live":         "● LIVE",
    "paused":       "⏸ PAUSED",
    "replay":       "▶ REPLAY",
    "rewind":       "◀◀ REWIND",
    "fast_forward": "▶▶ FAST FWD",
    "slow_motion":  "▶ SLOW-MO",
};
function renderPlayback(pb) {
    const pill    = document.getElementById("live-pill");
    const pillTxt = document.getElementById("live-text");
    const liveBtn = document.getElementById("go-live-btn");
    if (!pb) {
        pill.className = "live-pill";
        pillTxt.textContent = "—";
        liveBtn.classList.remove("needs-attention");
        return;
    }
    const mode = pb.mode || "replay";
    pill.className = "live-pill " + mode;
    pillTxt.textContent = PLAYBACK_LABELS[mode] || mode.toUpperCase();
    // Flag the Go Live button as needing attention when we're not live
    liveBtn.classList.toggle("needs-attention", !pb.is_live);
}

// --- server actions --------------------------------------------------------
async function switchToCarNumber(carNumber) {
    try {
        await fetch("/switch_car", { method: "POST", headers: {"Content-Type": "application/json"},
                                     body: JSON.stringify({car_number: carNumber}) });
    } catch (e) { console.error(e); }
}
async function switchCameraGroup(groupId) {
    try {
        await fetch("/switch_cam_group", { method: "POST", headers: {"Content-Type": "application/json"},
                                           body: JSON.stringify({group_id: groupId}) });
    } catch (e) { console.error(e); }
}
async function toggleAutoFollow() {
    const newState = !autoFollow;
    try {
        const r = await fetch("/auto_follow", { method: "POST", headers: {"Content-Type": "application/json"},
                                                body: JSON.stringify({enabled: newState}) });
        const d = await r.json();
        autoFollow = !!d.enabled;
        updateAutoFollowBtn();
    } catch (e) { console.error(e); }
}
function updateAutoFollowBtn() { document.getElementById("auto-follow-btn").classList.toggle("on", autoFollow); }

async function toggleAutoReplay() {
    const newState = !autoReplay;
    try {
        const r = await fetch("/auto_replay", { method: "POST", headers: {"Content-Type": "application/json"},
                                                body: JSON.stringify({enabled: newState}) });
        const d = await r.json();
        autoReplay = !!d.enabled;
        updateAutoReplayBtn();
    } catch (e) { console.error(e); }
}
function updateAutoReplayBtn() { document.getElementById("auto-replay-btn").classList.toggle("on", autoReplay); }

async function clearIncidents() {
    try { await fetch("/incidents/clear", { method: "POST" }); } catch (e) { console.error(e); }
    dismissedIncidentIds.clear();
}
async function dismissIncident(id, ev) {
    ev.stopPropagation();
    dismissedIncidentIds.add(id);
    document.getElementById("incident-list").dataset.sig = "";
    try { await fetch("/incidents/dismiss", { method: "POST", headers: {"Content-Type": "application/json"},
                                              body: JSON.stringify({id}) });
    } catch (e) {}
}

// --- replay (fires immediately, no confirmation) ---------------------------
// incidentId is used server-side to look up the incident's exact session
// time and seek there (much more reliable than "rewind N seconds from now").
async function triggerReplay(carNumber, incidentId) {
    try {
        const body = { car_number: carNumber };
        if (incidentId != null) body.incident_id = incidentId;
        await fetch("/replay_5s", { method: "POST", headers: {"Content-Type": "application/json"},
                                    body: JSON.stringify(body) });
    } catch (e) { console.error(e); }
}

// --- sidebar controls ------------------------------------------------------
function navDriver(delta) {
    const pool = showStarredOnly
        ? currentDrivers.filter(d => starredSet.has(d.car_idx))
        : currentDrivers;
    if (pool.length === 0) return;
    let idx = pool.findIndex(d => d.car_idx === activeCarIdx);
    if (idx < 0) idx = 0;
    idx = (idx + delta + pool.length) % pool.length;
    switchToCarNumber(pool[idx].car_number);
}
function toggleStar(carIdx, ev) {
    ev.stopPropagation();
    if (starredSet.has(carIdx)) starredSet.delete(carIdx); else starredSet.add(carIdx);
    saveStarred(starredSet);
    pushStarredToServer();
    document.getElementById("driver-list").dataset.sig = "";
    updateStarredCount();
}
function onFilterChange() {
    showStarredOnly = document.getElementById("filter-starred").checked;
    document.getElementById("driver-list").dataset.sig = "";
}
function updateStarredCount() {
    document.getElementById("starred-count").textContent = "⭐ " + starredSet.size;
}
updateStarredCount();

// --- render ----------------------------------------------------------------
function renderDrivers(drivers, activeIdx) {
    const list = document.getElementById("driver-list");
    const pool = showStarredOnly ? drivers.filter(d => starredSet.has(d.car_idx)) : drivers;
    document.getElementById("driver-count").textContent = pool.length;

    const signature = pool.map(d =>
        d.car_idx + ":" + d.position + ":" + d.on_pit_road +
        ":" + (d.gap_ahead ? d.gap_ahead.toFixed(2) : "") +
        ":" + (starredSet.has(d.car_idx) ? "1" : "0")
    ).join("|") + "|active=" + activeIdx + "|filter=" + showStarredOnly;
    if (list.dataset.sig === signature) return;
    list.dataset.sig = signature;

    list.innerHTML = "";
    for (const d of pool) {
        const row = document.createElement("div");
        row.className = "driver-row" + (d.car_idx === activeIdx ? " active" : "");
        row.onclick = () => switchToCarNumber(d.car_number);
        const pos = d.position > 0 ? "P" + d.position : "—";
        const pit = d.on_pit_road ? '<span class="pit-pill">PIT</span>' : "";
        const gapClass = (d.gap_ahead > 0 && d.gap_ahead < 1.0) ? "gap battle" : "gap";
        const isStar = starredSet.has(d.car_idx);

        const starEl = document.createElement("div");
        starEl.className = "star" + (isStar ? " on" : "");
        starEl.textContent = isStar ? "★" : "☆";
        starEl.onclick = (ev) => toggleStar(d.car_idx, ev);
        row.appendChild(starEl);

        const rest = document.createElement("div");
        rest.style.display = "contents";
        rest.innerHTML = `
            <div class="pos">${pos}</div>
            <div class="num">#${d.car_number}</div>
            <div class="name" title="${d.name}">${d.name}${pit}</div>
            <div class="time-small">${fmtLap(d.best_lap)}</div>
            <div class="${gapClass}">${fmtGap(d.gap_ahead)}</div>
        `;
        row.appendChild(rest);
        list.appendChild(row);
    }
}

// renderSectors() removed — sector times card is no longer shown.

function renderCameraButtons(groups, activeGroupId, autoCamActive, focusLeader, focusCrashes) {
    const host = document.getElementById("cam-buttons");
    const sig = groups.map(g => g.id + ":" + g.name).join("|")
              + "|active=" + activeGroupId
              + "|auto=" + (autoCamActive ? 1 : 0)
              + "|lead=" + (focusLeader ? 1 : 0)
              + "|crash=" + (focusCrashes ? 1 : 0);
    if (host.dataset.sig === sig) return;
    host.dataset.sig = sig;
    host.innerHTML = "";
    if (!groups.length) {
        host.innerHTML = '<span style="color:#555;font-size:12px">(loading camera groups…)</span>';
        return;
    }
    // Auto-mode toggles first — these are camera STATES, not groups.
    // Mutually exclusive with each other; each turns the others off.
    const autoBtn = document.createElement("div");
    autoBtn.className = "cam-btn cam-btn-auto" + (autoCamActive ? " active" : "");
    autoBtn.textContent = "MOST EXCITING";
    autoBtn.title = "iRacing auto-shot selection (CamUseAutoShotSelection)";
    autoBtn.onclick = () => toggleAutoCamera(!autoCamActive);
    host.appendChild(autoBtn);

    const leaderBtn = document.createElement("div");
    leaderBtn.className = "cam-btn cam-btn-leader" + (focusLeader ? " active" : "");
    leaderBtn.textContent = "FOCUS LEADER";
    leaderBtn.title = "Keep camera on the overall race leader (P1)";
    leaderBtn.onclick = () => toggleFocusLeader(!focusLeader);
    host.appendChild(leaderBtn);

    const crashBtn = document.createElement("div");
    crashBtn.className = "cam-btn cam-btn-crashes" + (focusCrashes ? " active" : "");
    crashBtn.textContent = "FOCUS CRASHES";
    crashBtn.title = "Auto-switch camera to cars that crash or spin";
    crashBtn.onclick = () => toggleFocusCrashes(!focusCrashes);
    host.appendChild(crashBtn);

    // Visual separator between auto-modes and the real camera groups.
    const sep = document.createElement("div");
    sep.style.cssText = "width:1px;height:28px;background:#333;margin:0 6px;";
    host.appendChild(sep);

    for (const g of groups) {
        const btn = document.createElement("div");
        const anyAuto = autoCamActive || focusLeader || focusCrashes;
        const isActive = (!anyAuto) && (g.id === activeGroupId);
        btn.className = "cam-btn" + (isActive ? " active" : "");
        btn.textContent = g.name;
        btn.onclick = () => switchCameraGroup(g.id);
        host.appendChild(btn);
    }
}

async function toggleAutoCamera(enable) {
    try {
        await fetch("/auto_camera", { method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({enabled: !!enable}) });
    } catch (e) { console.error(e); }
}

async function toggleFocusLeader(enable) {
    try {
        await fetch("/focus_leader", { method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({enabled: !!enable}) });
    } catch (e) { console.error(e); }
}

async function toggleFocusCrashes(enable) {
    try {
        await fetch("/focus_crashes", { method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({enabled: !!enable}) });
    } catch (e) { console.error(e); }
}

const INCIDENT_LABELS = {
    "off_track":       "OFF TRACK",
    "lost_control":    "LOST CONTROL",
    "collision":       "COLLISION",
    // Legacy types left in so existing queued incidents (from a previous
    // session) still render readably if the feed hasn't flushed.
    "incident_points": "INCIDENT POINTS",
    "stopped":         "STOPPED / CRASHED",
    "yellow":          "YELLOW FLAG",
};

function renderIncidents(incidents) {
    const host = document.getElementById("incident-list");
    const filtered = incidents.filter(i => !dismissedIncidentIds.has(i.id));
    const sig = filtered.map(i => i.id).join("|");
    if (host.dataset.sig === sig) return;
    host.dataset.sig = sig;

    if (filtered.length === 0) {
        host.innerHTML = '<div class="no-incidents">No active incidents.<br>Off-tracks, spins and collisions will appear here.</div>';
        return;
    }

    host.innerHTML = "";
    for (const inc of filtered) {
        const div = document.createElement("div");
        div.className = "incident " + inc.type;
        div.innerHTML = `
            <div class="incident-head">
                <div class="incident-type">${INCIDENT_LABELS[inc.type] || inc.type.toUpperCase()}</div>
                <button class="incident-dismiss" title="Dismiss">✕</button>
            </div>
            <div class="incident-driver">
                <span class="num-small">#${inc.car_number}</span>${inc.name}
                <span style="color:#666; font-size:10px; margin-left: 6px;">${fmtIncidentTime(inc.wall_clock)}</span>
            </div>
            <div class="incident-details">${inc.details || ""}</div>
            <div class="incident-buttons">
                <button class="btn-jump"   data-action="jump">Jump to car</button>
                <button class="btn-replay" data-action="replay">Replay 10s</button>
            </div>
        `;
        // wire up handlers
        div.querySelector(".incident-dismiss").onclick = (ev) => dismissIncident(inc.id, ev);
        div.querySelector("[data-action=jump]").onclick   = () => switchToCarNumber(inc.car_number);
        div.querySelector("[data-action=replay]").onclick = () => triggerReplay(inc.car_number, inc.id);
        host.appendChild(div);
    }
}

async function tick() {
    try {
        const r = await fetch("/telemetry");
        const d = await r.json();

        const statusEl = document.getElementById("status");
        if (!d.connected) {
            statusEl.className = "status disconnected";
            statusEl.textContent = "WAITING FOR IRACING...";
            document.getElementById("meta").textContent = "";
            return;
        }

        statusEl.className = "status connected";
        statusEl.textContent = "CONNECTED";
        document.getElementById("meta").textContent = d.track || "";

        if (autoFollow !== d.auto_follow) { autoFollow = d.auto_follow; updateAutoFollowBtn(); }
        if (autoReplay !== d.auto_replay) { autoReplay = d.auto_replay; updateAutoReplayBtn(); }

        if (d.active_driver) {
            activeCarIdx = d.active_driver.car_idx;
            document.getElementById("active-name").textContent =
                "#" + d.active_driver.car_number + "  " + d.active_driver.name;
            document.getElementById("active-car").textContent = d.active_driver.car || "";
        }

        cameraGroups    = d.camera_groups || [];
        currentCamGroup = d.current_cam_group || 0;
        renderCameraButtons(cameraGroups, currentCamGroup, !!d.auto_camera_active,
                            !!d.focus_leader, !!d.focus_crashes);

        currentDrivers = d.drivers || [];
        renderDrivers(currentDrivers, activeCarIdx);
        renderIncidents(d.incidents || []);
        renderRaceProgress(d.race_progress);
        renderPlayback(d.playback);
    } catch (e) { console.error(e); }
}

document.addEventListener("keydown", e => {
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
    if (e.key === "h" || e.key === "H") { toggleStreamMode(); return; }
    if (e.key === "l" || e.key === "L") { goLive(); return; }
    if (e.key === " ") { e.preventDefault(); hideIracingUI(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowRight") navDriver(+1);
    if (e.key === "ArrowUp"   || e.key === "ArrowLeft")  navDriver(-1);
});

setInterval(tick, 100);
tick();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/telemetry")
def telemetry():
    return jsonify(poller.get())


@app.route("/switch_car", methods=["POST"])
def switch_car():
    payload = request.get_json(silent=True) or {}
    car_number = payload.get("car_number")
    if car_number is None:
        return jsonify({"ok": False, "error": "car_number required"}), 400
    ok = poller.switch_camera_to_car_number(car_number)
    return jsonify({"ok": ok, "car_number": car_number})


@app.route("/switch_cam_group", methods=["POST"])
def switch_cam_group():
    payload = request.get_json(silent=True) or {}
    group_id = payload.get("group_id")
    if group_id is None:
        return jsonify({"ok": False, "error": "group_id required"}), 400
    ok = poller.switch_camera_group(int(group_id))
    return jsonify({"ok": ok, "group_id": group_id})


@app.route("/auto_camera", methods=["POST"])
def auto_camera():
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled", True))
    ok = poller.set_auto_camera(enabled)
    return jsonify({"ok": ok, "enabled": enabled})


@app.route("/focus_leader", methods=["POST"])
def focus_leader():
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled", True))
    poller.set_focus_leader(enabled)
    return jsonify({"ok": True, "enabled": poller.focus_leader})


@app.route("/focus_crashes", methods=["POST"])
def focus_crashes():
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled", True))
    poller.set_focus_crashes(enabled)
    return jsonify({"ok": True, "enabled": poller.focus_crashes})


@app.route("/auto_follow", methods=["POST"])
def auto_follow():
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled", False))
    poller.set_auto_follow(enabled)
    return jsonify({"enabled": poller.auto_follow})


@app.route("/auto_replay", methods=["POST"])
def auto_replay():
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled", False))
    poller.set_auto_replay(enabled)
    return jsonify({"enabled": poller.auto_replay})


@app.route("/starred", methods=["POST"])
def starred():
    payload = request.get_json(silent=True) or {}
    idxs = payload.get("car_idxs", [])
    poller.set_starred_bulk(idxs)
    return jsonify({"ok": True, "count": len(idxs)})


@app.route("/hide_iracing_ui", methods=["POST"])
def hide_iracing_ui():
    # Spacebar is what actually toggles iRacing's broadcast HUD. We also
    # flip our own boolean so that any subsequent camera switch can auto
    # re-press spacebar and keep the HUD hidden.
    ok, msg = send_key_to_iracing(VK_SPACE)
    if ok:
        poller.iracing_ui_hidden = not poller.iracing_ui_hidden
    return jsonify({
        "ok":     ok,
        "hidden": poller.iracing_ui_hidden,
        "message": msg,
    })


@app.route("/replay_5s", methods=["POST"])
def replay_5s():
    payload = request.get_json(silent=True) or {}
    car_number = payload.get("car_number")
    if car_number is None:
        return jsonify({"ok": False, "error": "car_number required"}), 400

    # If the client supplied the incident id, look it up and pass the
    # stored session_time + session_num so the replay seeks to the actual
    # accident time (much more reliable than "rewind N seconds from now").
    # Unknown ids are tolerated — the poller falls back to the time-based
    # rewind in that case.
    t_session = None
    session_num = None
    inc_type    = None
    incident_id = payload.get("incident_id")
    if incident_id is not None:
        try:
            target_id = int(incident_id)
            for inc in poller._incidents:
                if inc.get("id") == target_id:
                    t_session   = inc.get("t_session")
                    session_num = inc.get("session_num")
                    inc_type    = inc.get("type")
                    break
        except (ValueError, TypeError):
            pass

    # Collisions are back-dated to the last-moving timestamp (the moment
    # the car actually stopped = the crash), so the standard 5s buildup
    # now covers the real impact rather than the aftermath.
    buildup = 7.0 if inc_type == "collision" else 5.0

    ok, msg = poller.replay_5s_of_car(
        str(car_number),
        t_session=t_session,
        session_num=session_num,
        buildup_seconds=buildup,
    )
    return jsonify({"ok": ok, "message": msg})


@app.route("/go_live", methods=["POST"])
def go_live():
    ok, msg = poller.go_live()
    return jsonify({"ok": ok, "message": msg})


@app.route("/incidents/clear", methods=["POST"])
def incidents_clear():
    poller.clear_incidents()
    return jsonify({"ok": True})


@app.route("/incidents/debug")
def incidents_debug():
    """Dump the raw per-car signals the incident detector is working with.
    Used to diagnose why the feed is silent during spectated sessions —
    particularly whether iRacing is populating CurDriverIncidentCount per
    driver (or only for the user's own car) and whether CarIdxYawRate is
    available for other cars."""
    ir = poller.ir
    out = {"drivers": [], "session_info_has_CurDriverIncidentCount": False,
           "my_incident_count": None}

    drivers_info = ir["DriverInfo"]
    if drivers_info:
        # Top-level DriverInfo sometimes carries CurDriverIncidentCount for
        # the USER only.  This tells us whether iRacing publishes that
        # field at session scope vs per driver.
        top_level_cdic = drivers_info.get("CurDriverIncidentCount")
        out["session_info_has_CurDriverIncidentCount"] = top_level_cdic is not None
        out["my_incident_count"] = top_level_cdic

    yaw_arr   = ir["CarIdxYawRate"] or []
    pct_arr   = ir["CarIdxLapDistPct"] or []
    surfaces  = ir["CarIdxTrackSurface"] or []

    if drivers_info:
        for d in drivers_info.get("Drivers", []) or []:
            cidx = d.get("CarIdx")
            if cidx is None:
                continue
            out["drivers"].append({
                "car_idx":   cidx,
                "car_number": d.get("CarNumber"),
                "name":      d.get("UserName"),
                # These two keys are what the detector reads.  If they're
                # both None for every non-user car, that's why the 1x/2x/4x
                # path never fires in spec mode.
                "CurDriverIncidentCount": d.get("CurDriverIncidentCount"),
                "TeamIncidentCount":      d.get("TeamIncidentCount"),
                # Raw telemetry samples for this car.  None / 0 for all
                # non-user cars would mean the yaw + lap-pct heuristics
                # can't work either.
                "yaw_rate":   yaw_arr[cidx]  if cidx < len(yaw_arr)  else None,
                "lap_pct":    pct_arr[cidx]  if cidx < len(pct_arr)  else None,
                "surface":    surfaces[cidx] if cidx < len(surfaces) else None,
                # What the poller has stored from previous ticks, so you
                # can see whether the deltas would be computed correctly.
                "prev_incident_count": poller._prev_incidents.get(cidx),
                "prev_lap_pct":        poller._prev_lap_pct.get(cidx),
            })
    return jsonify(out)


@app.route("/incidents/dismiss", methods=["POST"])
def incidents_dismiss():
    payload = request.get_json(silent=True) or {}
    inc_id = payload.get("id")
    if inc_id is None:
        return jsonify({"ok": False, "error": "id required"}), 400
    poller.dismiss_incident(int(inc_id))
    return jsonify({"ok": True})




# -----------------------------------------------------------------------------
# Stream Deck endpoint  —  simple GET requests, no JSON body needed
# -----------------------------------------------------------------------------
# Configure each button with the built-in "Website" action in Stream Deck
# software pointing at one of these URLs (no plugin required):
#
#   Go Live:                http://localhost:5000/streamdeck/go_live
#   Toggle Auto-Follow:     http://localhost:5000/streamdeck/toggle_auto_follow
#   Toggle Auto-Replay:     http://localhost:5000/streamdeck/toggle_auto_replay
#   Replay last incident:   http://localhost:5000/streamdeck/replay_last
#   Replay last spin:       http://localhost:5000/streamdeck/replay_last_lost_control
#   Replay last collision:  http://localhost:5000/streamdeck/replay_last_incident_points
#   Next camera group:      http://localhost:5000/streamdeck/cam_next
#   Prev camera group:      http://localhost:5000/streamdeck/cam_prev
#   Specific camera group:  http://localhost:5000/streamdeck/cam/4
#   Next driver:            http://localhost:5000/streamdeck/driver_next
#   Prev driver:            http://localhost:5000/streamdeck/driver_prev
# -----------------------------------------------------------------------------
@app.route("/streamdeck/<action>")
@app.route("/streamdeck/<action>/<param>")
def streamdeck(action, param=None):
    snap = poller.get()
    ok, msg = False, f"unknown action: {action}"

    # --- playback ---
    if action == "go_live":
        ok, msg = poller.go_live()

    # --- toggles ---
    elif action == "toggle_auto_follow":
        poller.set_auto_follow(not poller.auto_follow)
        ok, msg = True, f"auto_follow={'on' if poller.auto_follow else 'off'}"

    elif action == "toggle_auto_replay":
        poller.set_auto_replay(not poller.auto_replay)
        ok, msg = True, f"auto_replay={'on' if poller.auto_replay else 'off'}"

    # --- replay last incident (any type) ---
    elif action == "replay_last":
        incidents = snap.get("incidents", [])
        if incidents:
            ok, msg = poller.replay_5s_of_car(incidents[0]["car_number"])
            msg = f"replaying #{incidents[0]['car_number']} ({incidents[0]['type']})"
        else:
            ok, msg = False, "no incidents in feed"

    # --- replay last incident of a specific type ---
    elif action in ("replay_last_lost_control", "replay_last_incident_points",
                    "replay_last_stopped", "replay_last_yellow"):
        wanted_type = action.replace("replay_last_", "")
        incidents = snap.get("incidents", [])
        match = next((i for i in incidents if i["type"] == wanted_type), None)
        if match:
            ok, msg = poller.replay_5s_of_car(match["car_number"])
            msg = f"replaying #{match['car_number']} ({wanted_type})"
        else:
            ok, msg = False, f"no '{wanted_type}' incident in feed"

    # --- camera group cycling ---
    elif action == "cam_next":
        groups = snap.get("camera_groups", [])
        cur    = snap.get("current_cam_group", 0)
        ids    = [g["id"] for g in groups]
        if ids:
            idx = ids.index(cur) if cur in ids else -1
            next_id = ids[(idx + 1) % len(ids)]
            ok = poller.switch_camera_group(next_id)
            msg = f"camera -> group {next_id}"

    elif action == "cam_prev":
        groups = snap.get("camera_groups", [])
        cur    = snap.get("current_cam_group", 0)
        ids    = [g["id"] for g in groups]
        if ids:
            idx = ids.index(cur) if cur in ids else 0
            prev_id = ids[(idx - 1) % len(ids)]
            ok = poller.switch_camera_group(prev_id)
            msg = f"camera -> group {prev_id}"

    # --- camera group by id:  /streamdeck/cam/4 ---
    elif action == "cam" and param is not None:
        try:
            gid = int(param)
            ok = poller.switch_camera_group(gid)
            msg = f"camera -> group {gid}"
        except ValueError:
            ok, msg = False, f"invalid group id: {param}"

    # --- driver cycling ---
    elif action == "driver_next":
        drivers = snap.get("drivers", [])
        cam_idx = (snap.get("active_driver") or {}).get("car_idx")
        if drivers:
            idxs   = [d["car_idx"] for d in drivers]
            pos    = idxs.index(cam_idx) if cam_idx in idxs else -1
            target = drivers[(pos + 1) % len(drivers)]
            ok = poller.switch_camera_to_car_number(target["car_number"])
            msg = f"camera -> #{target['car_number']} {target['name']}"

    elif action == "driver_prev":
        drivers = snap.get("drivers", [])
        cam_idx = (snap.get("active_driver") or {}).get("car_idx")
        if drivers:
            idxs   = [d["car_idx"] for d in drivers]
            pos    = idxs.index(cam_idx) if cam_idx in idxs else 0
            target = drivers[(pos - 1) % len(drivers)]
            ok = poller.switch_camera_to_car_number(target["car_number"])
            msg = f"camera -> #{target['car_number']} {target['name']}"

    print(f"[streamdeck] {action}/{param or ''} -> ok={ok}  {msg}")
    return jsonify({"ok": ok, "message": msg, "action": action})

# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()

    print("\n" + "=" * 60)
    print("  iRacing Broadcast Dashboard v8")
    print()
    print("  Stream Deck  (System: Website action, no plugin needed):")
    print("  Go Live           http://localhost:5000/streamdeck/go_live")
    print("  Replay last       http://localhost:5000/streamdeck/replay_last")
    print("  Replay last spin  http://localhost:5000/streamdeck/replay_last_lost_control")
    print("  Replay collision  http://localhost:5000/streamdeck/replay_last_incident_points")
    print("  Cam next/prev     http://localhost:5000/streamdeck/cam_next")
    print("  Cam by id         http://localhost:5000/streamdeck/cam/4")
    print("  Driver next/prev  http://localhost:5000/streamdeck/driver_next")
    print("  Toggle follow     http://localhost:5000/streamdeck/toggle_auto_follow")
    print("  Open in browser:  http://localhost:5000")
    print("  - TOP: race progress (laps + time remaining)")
    print("  - LEFT: drivers, starred, auto-follow")
    print("  - MIDDLE: camera angle, sector times")
    print("  - RIGHT: incident feed (jump-to-car, replay 10s)")
    print("  - Keys: H=stream mode  SPACE=hide iRacing UI  L=Go Live")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")
    if not HAS_WIN32:
        print("  NOTE: pywin32 not installed - 'Hide iRacing UI' will not work.")
        print("  Install with:  pip install pywin32")
        print()

    try:
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    finally:
        poller.stop()
