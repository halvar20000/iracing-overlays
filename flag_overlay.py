"""
flag_overlay.py
---------------
A standalone Flask overlay that shows:
  - WHITE FLAG  when the race leader starts their final lap
  - CHECKERED FLAG  when the race leader crosses the finish line

Designed as an OBS Browser Source:  http://localhost:5008
Background is transparent — drop it over your iRacing capture.

Runs in parallel with the other iracing_*.py overlays on port 5008.

Requirements:  pip install pyirsdk flask
"""

import sys
import threading
import time
from flask import Flask, Response, render_template_string

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
    print("ERROR: pyirsdk not installed.  Run:  pip install pyirsdk flask")
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
class FlagWatcher:
    """
    Watches the race leader's lap count and lap distance to trigger
    white-flag and checkered-flag events.

    State transitions:
        idle  ->  white_flag   (leader starts last lap)
        white_flag  ->  checkered  (leader crosses finish line on last lap)
        checkered  ->  done       (flag shown long enough, hide it)
    """

    # How long each flag stays visible (seconds)
    WHITE_FLAG_DURATION   = 0.0   # stays until checkered
    CHECKERED_DURATION    = 60.0  # hide after 60s

    def __init__(self):
        self.ir = irsdk.IRSDK()
        self.connected = False
        self._running  = True
        self._lock     = threading.Lock()

        # Public state (read by Flask thread)
        self.state      = "idle"   # idle | white_flag | checkered | done
        self.leader_num = ""
        self.leader_name = ""

        # Session-change detection. When this differs from the SDK's
        # current SessionNum, we zero the session-scoped state so each new
        # session (Practice -> Quali -> Race1 -> Warmup -> Race2, ...)
        # starts from idle and doesn't inherit lap times or "done" state
        # from the previous one. Without this, `self.state == "done"` from
        # a finished quali caused the watcher to bail out of every race
        # tick that followed — the exact failure mode in yesterday's CAS
        # Community stream.
        self._last_session_num: int | None = None

        # Internal tracking — lap-based races
        self._total_laps   = None   # race length in laps (None = timed race)
        self._last_lap     = {}     # car_idx -> last seen lap count
        self._last_pct     = {}     # car_idx -> last seen LapDistPct
        self._white_shown  = False
        self._check_shown  = False
        self._check_shown_at = 0.0

        # Internal tracking — timed races
        # Average lap time is computed from the leader's last N completed laps
        # so it stays accurate as fuel/tyre load evolves during the race.
        self._lap_times        = []    # rolling list of leader's recent lap times (s)
        self._lap_time_max     = 5     # how many laps to average over
        self._last_lap_start_t = None  # session_time when leader last crossed S/F
        self._timed_last_lap   = False # True once we've decided this is the last lap

    # --- helpers ------------------------------------------------------------
    def _find_leader(self):
        """Return (car_idx, car_number, driver_name) of the class-position-1 car."""
        positions = self.ir["CarIdxClassPosition"] or []
        drivers   = (self.ir["DriverInfo"] or {}).get("Drivers", []) or []
        for d in drivers:
            idx = d.get("CarIdx")
            if idx is None:
                continue
            if d.get("CarIsPaceCar") == 1 or d.get("IsSpectator") == 1:
                continue
            pos = positions[idx] if idx < len(positions) else 0
            if pos == 1:
                return idx, str(d.get("CarNumber", "")), d.get("UserName", "")
        return None, "", ""

    def _get_total_laps(self):
        sessions = (self.ir["SessionInfo"] or {}).get("Sessions", []) or []
        sess_num = self.ir["SessionNum"] or 0
        for s in sessions:
            if s.get("SessionNum") == sess_num:
                raw = s.get("SessionLaps", "")
                try:
                    n = int(raw)
                    return n if 0 < n < 9000 else None
                except (TypeError, ValueError):
                    return None
        return None

    # --- main loop ----------------------------------------------------------
    def _tick(self):
        self.ir.freeze_var_buffer_latest()

        # ── Session-change detection ───────────────────────────────────────
        # Reset session-scoped state whenever iRacing's SessionNum changes
        # (Practice -> Quali -> Race1 -> Warmup -> Race2 -> ...). Without
        # this, `self.state` stays at "done" after the first session's
        # checkered flag and every subsequent session's tick bails out
        # early at the `if self.state == "done"` check below. The stale
        # `_lap_times` from quali would also skew the race's avg-lap
        # calculation.
        cur_session_num = self.ir["SessionNum"]
        if cur_session_num is not None and cur_session_num != self._last_session_num:
            if self._last_session_num is not None:
                print(f"[flag] Session change "
                      f"{self._last_session_num} -> {cur_session_num}, "
                      f"resetting state machine")
                self._reset_session_state()
            self._last_session_num = cur_session_num

        # Auto-return from checkered after duration
        with self._lock:
            if self.state == "checkered":
                if time.time() - self._check_shown_at > self.CHECKERED_DURATION:
                    self.state = "done"
                return  # nothing else to do after checkered

            if self.state == "done":
                return

        # Refresh total laps each tick (available after session starts)
        tl = self._get_total_laps()
        if tl is not None:
            self._total_laps = tl

        leader_idx, leader_num, leader_name = self._find_leader()
        if leader_idx is None:
            return

        lap_arr  = self.ir["CarIdxLap"] or []
        pct_arr  = self.ir["CarIdxLapDistPct"] or []
        sess_t   = self.ir["SessionTime"] or 0.0

        cur_lap  = lap_arr[leader_idx] if leader_idx < len(lap_arr) else 0
        cur_pct  = pct_arr[leader_idx] if leader_idx < len(pct_arr) else 0.0
        prev_lap = self._last_lap.get(leader_idx, cur_lap)
        prev_pct = self._last_pct.get(leader_idx, cur_pct)

        self._last_lap[leader_idx] = cur_lap
        self._last_pct[leader_idx] = cur_pct

        with self._lock:
            self.leader_num  = leader_num
            self.leader_name = leader_name

        # Detect S/F crossing: lap counter increments OR pct wraps 0.9 -> 0.1
        crossed_sf = (
            (cur_lap > prev_lap) or
            (prev_pct > 0.85 and cur_pct < 0.15)
        )

        # ── Track average lap time (used for timed-race detection) ───────────
        if crossed_sf and self._last_lap_start_t is not None:
            elapsed = sess_t - self._last_lap_start_t
            if 20.0 < elapsed < 600.0:   # sanity: between 20s and 10min
                self._lap_times.append(elapsed)
                if len(self._lap_times) > self._lap_time_max:
                    self._lap_times.pop(0)
        if crossed_sf:
            self._last_lap_start_t = sess_t

        # ════════════════════════════════════════════════════════════════════
        # LAP-BASED RACE
        # ════════════════════════════════════════════════════════════════════
        if self._total_laps is not None:

            # White flag: leader transitions onto the final lap.
            # iRacing increments lap count at S/F, so cur_lap == total_laps
            # means the car just started its last lap.
            if not self._white_shown and cur_lap == self._total_laps:
                with self._lock:
                    self.state = "white_flag"
                self._white_shown = True
                print(f"[flag] WHITE FLAG (lap) — #{leader_num} {leader_name} "
                      f"started lap {cur_lap}/{self._total_laps}")

            # Checkered flag: leader crosses S/F AGAIN after starting the
            # last lap. Two signals, either one is enough:
            #   (a) cur_lap > total_laps — the counter ticked past the
            #       final lap, which happens when the leader crosses for
            #       the final time.
            #   (b) iRacing's SessionState becomes "Checkered" (value 5).
            #
            # The previous code also had `cur_lap == total_laps AND prev_pct
            # high AND cur_pct low` as a fallback — that fires the SAME
            # tick as the white-flag trigger (because the start-of-last-lap
            # crossing IS prev_pct high → cur_pct low), so the overlay
            # raced past white straight to checkered. Removed.
            if self._white_shown and not self._check_shown:
                # SessionState values — iRacing: Invalid=0, GetInCar=1,
                # Warmup=2, ParadeLaps=3, Racing=4, Checkered=5, CoolDown=6.
                sess_state = self.ir["SessionState"]
                state_checkered = (sess_state is not None and int(sess_state) >= 5)

                if (cur_lap > self._total_laps) or state_checkered:
                    with self._lock:
                        self.state = "checkered"
                    self._check_shown    = True
                    self._check_shown_at = time.time()
                    print(f"[flag] CHECKERED (lap) — #{leader_num} {leader_name} "
                          f"cur_lap={cur_lap} total={self._total_laps} "
                          f"sess_state={sess_state}")

        # ════════════════════════════════════════════════════════════════════
        # TIMED RACE
        # ════════════════════════════════════════════════════════════════════
        else:
            time_rem = self.ir["SessionTimeRemain"]
            if time_rem is None or time_rem > 1e7:
                return   # session time not available yet

            avg_lap = (sum(self._lap_times) / len(self._lap_times)
                       if self._lap_times else None)

            # ── LATE-JOIN DETECTION ────────────────────────────────────────
            # If the overlay started up after iRacing had already flipped
            # SessionState to Checkered (5) or CoolDown (6) — i.e. the
            # leader's final lap was already under way when we connected —
            # we missed the white-flag moment. Skip directly to "ready
            # for checkered" so the next S/F crossing still fires the
            # checkered flag correctly. Without this, joining a session
            # mid-final-lap meant neither flag ever appeared.
            sess_state = self.ir["SessionState"]
            if (not self._white_shown and not self._timed_last_lap):
                already_in_final = (
                    sess_state is not None and int(sess_state) >= 5
                )
                if already_in_final:
                    self._white_shown    = True
                    self._timed_last_lap = True
                    print(f"[flag] LATE JOIN (timed) — SessionState="
                          f"{sess_state} on first observation, skipping "
                          f"white flag, armed for checkered")

            # Both of the conditions below look at crossed_sf, which is True
            # for exactly one tick per S/F crossing. An `if / if` pair
            # would therefore fire BOTH the white-flag and checkered
            # branches on the same start-of-last-lap crossing (same root
            # bug as the lap-based section). Use elif so only one of
            # them can trigger per tick.
            if not self._white_shown and crossed_sf:
                # White-flag trigger for TIMED races. iRacing's rule:
                # white flag flies at the S/F crossing where there is
                # less than one full lap of time remaining — the leader
                # is starting their final lap.
                #
                # We need an estimate of "one lap of time" to decide
                # whether the leader has less than that left. Priority:
                #   1. avg_lap = rolling mean of observed leader laps
                #      (most accurate once we have data)
                #   2. EstLapTime = iRacing's predicted lap time for
                #      the current car/track (always available; useful
                #      when we just joined and haven't seen any laps)
                #   3. Hardcoded 120 s default — last-resort fallback
                #
                # We deliberately do NOT trigger off SessionState >= 5
                # here. iRacing flips SessionState to "Checkered" the
                # moment the TIMER expires, which is typically mid-lap —
                # well before the leader has actually started their
                # final lap. Triggering white off that jumps the flag
                # too early; the next S/F crossing then immediately
                # flips to checkered, so white is barely visible. Late
                # joins are handled separately above.
                if avg_lap is not None:
                    lap_estimate = avg_lap
                    estimate_source = "avg_lap"
                else:
                    est = self.ir["EstLapTime"]
                    if est and est > 0:
                        lap_estimate = float(est)
                        estimate_source = "EstLapTime"
                    else:
                        lap_estimate = 120.0
                        estimate_source = "default_120s"

                trigger_a = time_rem < lap_estimate

                if trigger_a:
                    with self._lock:
                        self.state = "white_flag"
                    self._white_shown    = True
                    self._timed_last_lap = True
                    print(f"[flag] WHITE FLAG (timed, via {estimate_source}) — "
                          f"#{leader_num} {leader_name} "
                          f"time_rem={time_rem:.1f}s "
                          f"lap_estimate={lap_estimate:.1f}s")
            elif not self._check_shown and self._timed_last_lap:
                # Checkered: ONLY fires when the leader actually crosses
                # the finish line again after the white flag. iRacing's
                # SessionState >= 5 is no longer a fallback because it
                # flips at timer-expiry (mid-lap), not at the leader's
                # final S/F crossing — using it caused the white flag to
                # disappear early.
                #
                # If for some reason we miss the crossed_sf tick (the
                # poll cadence is 0.5 s and the leader could pass S/F
                # between polls), the safety net is the SessionState
                # check below — but only if the timer has been
                # significantly negative AND we've covered a full
                # lap-length of additional negative time, meaning we're
                # genuinely past any plausible "leader still on last lap"
                # interpretation.
                sess_state = self.ir["SessionState"]
                state_checkered = (sess_state is not None and int(sess_state) >= 5)
                # "Safety-net" condition: state is checkered AND the timer
                # is more than one full lap into the negative. This is
                # only true once the leader has run a full lap of overtime
                # — well after they should have finished.
                missed_sf = (state_checkered
                             and avg_lap is not None
                             and time_rem < -avg_lap)

                if crossed_sf or missed_sf:
                    with self._lock:
                        self.state = "checkered"
                    self._check_shown    = True
                    self._check_shown_at = time.time()
                    via = "crossed_sf" if crossed_sf else "safety_net"
                    print(f"[flag] CHECKERED (timed, via {via}) — "
                          f"#{leader_num} {leader_name} "
                          f"sess_state={sess_state} time_rem={time_rem:.1f}s")

    def _check_connection(self):
        if self.connected and not (self.ir.is_initialized and self.ir.is_connected):
            self.ir.shutdown()
            self.connected = False
            self._reset()
            print("[flag] Disconnected from iRacing")
        elif not self.connected and self.ir.startup() and self.ir.is_initialized and self.ir.is_connected:
            self.connected = True
            print("[flag] Connected to iRacing")
        return self.connected

    def _reset_session_state(self):
        """Zero state that is scoped to one iRacing session.

        Called on session change (Quali -> Race1, Race1 -> Warmup, ...)
        so each session starts from idle with its own empty lap-time
        rolling window. Does NOT touch connection / session-tracking
        fields; those are managed at a higher level.
        """
        self._total_laps      = None
        self._last_lap.clear()
        self._last_pct.clear()
        self._white_shown     = False
        self._check_shown     = False
        self._check_shown_at  = 0.0
        self._lap_times       = []
        self._last_lap_start_t = None
        self._timed_last_lap  = False
        with self._lock:
            self.state       = "idle"
            self.leader_num  = ""
            self.leader_name = ""

    def _reset(self):
        """Full reset — session state + session-change tracker.

        Called on SDK disconnect (user exits iRacing or switches sims).
        Forces a clean slate on the next startup so we don't think we're
        "continuing" the previous iRacing instance's final session.
        """
        self._reset_session_state()
        self._last_session_num = None

    def run(self):
        print("[flag] Watcher started (waiting for iRacing…)")
        while self._running:
            try:
                if self._check_connection():
                    self._tick()
            except Exception as e:
                print(f"[flag] Error: {e}")
            time.sleep(0.1)   # 10 Hz is plenty for lap transitions

    def get_state(self):
        with self._lock:
            return {
                "state":       self.state,
                "leader_num":  self.leader_num,
                "leader_name": self.leader_name,
            }

    def stop(self):
        self._running = False
        if self.connected:
            self.ir.shutdown()


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app    = Flask(__name__)
watcher = FlagWatcher()


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

OVERLAY_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Flag Overlay</title>
<style>
  /* ── Transparent background for OBS Browser Source ── */
  html, body {
    margin: 0; padding: 0;
    width: 100vw; height: 100vh;
    background: transparent;
    overflow: hidden;
    font-family: 'Georgia', 'Times New Roman', serif;
  }

  /* ── Full-screen flag container ── */
  #flag-wrap {
    position: fixed;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.05s;
  }
  #flag-wrap.visible {
    opacity: 1;
  }

  /* ── Shared flag card ── */
  .flag-card {
    display: none;
    flex-direction: column;
    align-items: center;
    gap: 20px;
    filter: drop-shadow(0 8px 40px rgba(0,0,0,0.7));
  }
  .flag-card.active { display: flex; }

  /* ── SVG flags ── */
  .flag-svg {
    width: 260px;
    height: auto;
    animation: flag-wave 0.9s ease-in-out infinite alternate;
    transform-origin: left center;
  }
  @keyframes flag-wave {
    0%   { transform: rotate(-3deg) skewX(-1deg); }
    100% { transform: rotate(3deg)  skewX(1deg);  }
  }

  /* ── Driver label ── */
  .flag-label {
    background: rgba(0,0,0,0.72);
    border: 2px solid rgba(255,255,255,0.18);
    border-radius: 6px;
    padding: 10px 28px;
    text-align: center;
    backdrop-filter: blur(8px);
    animation: label-fade-in 0.4s ease-out both;
  }
  @keyframes label-fade-in {
    from { opacity:0; transform: translateY(10px); }
    to   { opacity:1; transform: translateY(0);    }
  }
  .flag-label .car-num {
    font-size: 38px;
    font-weight: 900;
    letter-spacing: 2px;
    line-height: 1;
    font-style: italic;
  }
  .flag-label .driver-name {
    font-size: 16px;
    letter-spacing: 3px;
    text-transform: uppercase;
    opacity: 0.85;
    margin-top: 4px;
    font-style: normal;
    font-family: 'Arial Narrow', 'Arial', sans-serif;
  }

  /* ── White flag colours ── */
  #white-flag .flag-label { color: #fff; border-color: rgba(255,255,255,0.3); }
  #white-flag .sub-text {
    font-size: 13px;
    letter-spacing: 4px;
    text-transform: uppercase;
    color: rgba(255,255,255,0.6);
    margin-top: 2px;
  }

  /* ── Checkered flag colours ── */
  #check-flag .flag-label { color: #fff; border-color: rgba(255,255,255,0.25); }
  #check-flag .sub-text {
    font-size: 13px;
    letter-spacing: 4px;
    text-transform: uppercase;
    color: rgba(255,215,0,0.85);
    margin-top: 2px;
  }

  /* ── Entrance animations ── */
  #flag-wrap.visible .flag-svg {
    animation: flag-wave 0.9s ease-in-out infinite alternate,
               flag-in 0.35s cubic-bezier(.22,1,.36,1) both;
  }
  @keyframes flag-in {
    from { opacity:0; transform: scale(0.7) rotate(-8deg); }
    to   { opacity:1; }
  }

  /* ── Checkered shimmer on label ── */
  #check-flag .flag-label {
    background: rgba(20,20,20,0.82);
    position: relative;
    overflow: hidden;
  }
  #check-flag .flag-label::before {
    content: '';
    position: absolute;
    inset: 0;
    background: repeating-linear-gradient(
      45deg,
      rgba(255,255,255,0.04) 0px,
      rgba(255,255,255,0.04) 4px,
      transparent 4px,
      transparent 8px
    );
    pointer-events: none;
  }
</style>
</head>
<body>

<div id="flag-wrap">

  <!-- White Flag -->
  <div class="flag-card" id="white-flag">
    <svg class="flag-svg" viewBox="0 0 240 160" xmlns="http://www.w3.org/2000/svg">
      <!-- pole -->
      <rect x="10" y="0" width="6" height="160" rx="3"
            fill="url(#pole-grad)"/>
      <defs>
        <linearGradient id="pole-grad" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%"   stop-color="#888"/>
          <stop offset="50%"  stop-color="#ddd"/>
          <stop offset="100%" stop-color="#888"/>
        </linearGradient>
        <filter id="flag-shadow">
          <feDropShadow dx="4" dy="6" stdDeviation="6" flood-opacity="0.5"/>
        </filter>
      </defs>
      <!-- white flag panel with subtle wave shape -->
      <path d="M16,8 Q80,0 160,14 Q220,24 230,50
               Q220,76 160,70 Q80,64 16,72 Z"
            fill="white" filter="url(#flag-shadow)"
            stroke="rgba(0,0,0,0.12)" stroke-width="1"/>
      <!-- very subtle fold lines -->
      <path d="M60,10 Q60,40 62,68" stroke="rgba(180,180,180,0.4)"
            stroke-width="1" fill="none"/>
      <path d="M120,12 Q118,41 120,70" stroke="rgba(180,180,180,0.4)"
            stroke-width="1" fill="none"/>
      <path d="M180,14 Q176,42 178,70" stroke="rgba(180,180,180,0.3)"
            stroke-width="1" fill="none"/>
    </svg>
    <div class="flag-label">
      <div class="car-num" id="white-num">#1</div>
      <div class="driver-name" id="white-name">Driver</div>
      <div class="sub-text">FINAL LAP</div>
    </div>
  </div>

  <!-- Checkered Flag -->
  <div class="flag-card" id="check-flag">
    <svg class="flag-svg" viewBox="0 0 240 160" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="pole-grad2" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%"   stop-color="#888"/>
          <stop offset="50%"  stop-color="#ddd"/>
          <stop offset="100%" stop-color="#888"/>
        </linearGradient>
        <filter id="check-shadow">
          <feDropShadow dx="4" dy="6" stdDeviation="6" flood-opacity="0.55"/>
        </filter>
        <clipPath id="flag-clip">
          <path d="M16,8 Q80,0 160,14 Q220,24 230,50
                   Q220,76 160,70 Q80,64 16,72 Z"/>
        </clipPath>
      </defs>
      <!-- pole -->
      <rect x="10" y="0" width="6" height="160" rx="3"
            fill="url(#pole-grad2)"/>
      <!-- flag shape -->
      <path d="M16,8 Q80,0 160,14 Q220,24 230,50
               Q220,76 160,70 Q80,64 16,72 Z"
            fill="white" filter="url(#check-shadow)"/>
      <!-- checkered pattern clipped to flag shape -->
      <g clip-path="url(#flag-clip)">
        <!-- row 1 black squares -->
        <rect x="16" y="8"  width="18" height="16" fill="black"/>
        <rect x="52" y="8"  width="18" height="16" fill="black"/>
        <rect x="88" y="9"  width="18" height="16" fill="black"/>
        <rect x="124" y="10" width="18" height="15" fill="black"/>
        <rect x="160" y="12" width="18" height="15" fill="black"/>
        <rect x="196" y="14" width="18" height="14" fill="black"/>
        <!-- row 2 black squares -->
        <rect x="34" y="24" width="18" height="16" fill="black"/>
        <rect x="70" y="25" width="18" height="16" fill="black"/>
        <rect x="106" y="25" width="18" height="16" fill="black"/>
        <rect x="142" y="25" width="18" height="15" fill="black"/>
        <rect x="178" y="27" width="18" height="14" fill="black"/>
        <rect x="214" y="30" width="16" height="13" fill="black"/>
        <!-- row 3 black squares -->
        <rect x="16" y="40" width="18" height="16" fill="black"/>
        <rect x="52" y="41" width="18" height="16" fill="black"/>
        <rect x="88" y="41" width="18" height="15" fill="black"/>
        <rect x="124" y="40" width="18" height="16" fill="black"/>
        <rect x="160" y="41" width="18" height="15" fill="black"/>
        <rect x="196" y="43" width="18" height="14" fill="black"/>
        <!-- row 4 black squares -->
        <rect x="34" y="56" width="18" height="15" fill="black"/>
        <rect x="70" y="57" width="18" height="14" fill="black"/>
        <rect x="106" y="56" width="18" height="15" fill="black"/>
        <rect x="142" y="55" width="18" height="15" fill="black"/>
        <rect x="178" y="56" width="18" height="14" fill="black"/>
        <rect x="214" y="58" width="16" height="12" fill="black"/>
      </g>
    </svg>
    <div class="flag-label">
      <div class="car-num" id="check-num">#1</div>
      <div class="driver-name" id="check-name">Driver</div>
      <div class="sub-text">RACE WINNER</div>
    </div>
  </div>

</div>

<script>
let lastState = "idle";

// "Joseph Johnson" -> "J. Johnson"
//   • Keeps single-word names whole ("Flako", "Madonna")
//   • Uses the LAST word as the surname so middle names / initials
//     collapse ("Tim C. Huber" -> "T. Huber",
//     "Nathan N Williams" -> "N. Williams")
//   • Skips tokens that contain no alphanumerics (trailing dots etc.)
function abbrevName(full) {
  if (!full) return "";
  const parts = String(full).trim().split(/\s+/).filter(p => p && /[a-zA-Z0-9]/.test(p));
  if (parts.length === 0) return String(full);
  if (parts.length === 1) return parts[0];
  return parts[0].charAt(0).toUpperCase() + ". " + parts[parts.length - 1];
}

async function poll() {
  try {
    const r = await fetch("/state");
    const d = await r.json();

    if (d.state === lastState) return;
    lastState = d.state;

    const wrap       = document.getElementById("flag-wrap");
    const whiteCard  = document.getElementById("white-flag");
    const checkCard  = document.getElementById("check-flag");

    // Reset
    wrap.classList.remove("visible");
    whiteCard.classList.remove("active");
    checkCard.classList.remove("active");

    if (d.state === "white_flag") {
      document.getElementById("white-num").textContent  = "#" + d.leader_num;
      document.getElementById("white-name").textContent = abbrevName(d.leader_name);
      // Small delay lets the CSS reset propagate before re-showing
      setTimeout(() => {
        whiteCard.classList.add("active");
        wrap.classList.add("visible");
      }, 50);

    } else if (d.state === "checkered") {
      document.getElementById("check-num").textContent  = "#" + d.leader_num;
      document.getElementById("check-name").textContent = abbrevName(d.leader_name);
      setTimeout(() => {
        checkCard.classList.add("active");
        wrap.classList.add("visible");
      }, 50);
    }
    // "idle" / "done" -> stay hidden
  } catch (e) {
    // iRacing not running yet, silent
  }
}

// Poll at 5 Hz — lap transitions don't need faster updates
setInterval(poll, 200);
poll();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(OVERLAY_HTML)


@app.route("/state")
def state():
    from flask import jsonify
    return jsonify(watcher.get_state())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=watcher.run, daemon=True)
    t.start()

    print("\n" + "=" * 60)
    print("  iRacing Flag Overlay")
    print()
    print("  OBS Browser Source URL:  http://localhost:5008")
    print("  Set width/height to match your stream resolution")
    print("  Enable: 'Shutdown source when not visible'")
    print("  Enable: 'Refresh browser when scene becomes active'")
    print()
    print("  Flags:")
    print("  WHITE FLAG   — leader starts their final lap")
    print("  CHECKERED    — leader crosses the finish line")
    print("  (auto-hides 12s after checkered)")
    print()
    print("  Works with lap-based races only.")
    print("  Timed races (no fixed lap count) are not supported.")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    app.run(host="0.0.0.0", port=5008, debug=False, use_reloader=False)
