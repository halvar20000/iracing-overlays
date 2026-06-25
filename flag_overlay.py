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

import json
import os
import sys
import threading
import time
from flask import Flask, Response, render_template_string

# Forensic log so no-bit timed/heat races can be diagnosed after the fact.
# One JSON line per leader S/F crossing and per flag fire. Lives in logs/
# (gitignored). Delete it any time; it's append-only and low-volume.
DEBUG_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "logs", "flag_debug.jsonl")

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

    # iRacing SessionFlags bits (pyirsdk Flags). These are iRacing's OWN
    # session-wide flag broadcast — the authoritative signal when present.
    # Race-log analysis (2026-06-04) showed the white bit fires at the
    # exact moment the leader starts the final lap (Spa 27.05., Thruxton
    # 02.06., Magny-Cours 26.05.) — but in BOTH PCCD Silverstone races
    # (21.05.) the bits never appeared at all, so heuristic fallbacks
    # below are still required.
    FLAG_BIT_CHECKERED = 0x00000001
    FLAG_BIT_WHITE     = 0x00000002

    # A leader lap can't plausibly be shorter than this; used to stop the
    # checkered trigger from firing on the SAME S/F crossing that raised
    # the white flag (the white flag-bit and the crossing arrive within
    # the same tick or two).
    MIN_FINAL_LAP_S = 15.0

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
        self._timed_seen       = False # SessionTimeRemain was finite at least once
        self._ticks_in_session = 0     # ticks since we started watching this session
        self._white_fired_at   = 0.0   # wall-clock time the white flag was raised

    # --- helpers ------------------------------------------------------------
    def _find_leader(self):
        """Return (car_idx, car_number, driver_name) of the OVERALL race
        leader, by live track progress (CarIdxLap + CarIdxLapDistPct).

        Was previously "first car with CarIdxClassPosition == 1" — WRONG in
        multiclass races (every class has a class-P1 car, and whichever
        appears first in the Drivers list won), so at the Le Mans IEC race
        the overlay tracked a class leader instead of the overall leader:
        white flew far too long and the checkered missed the real finish.
        Live progress also avoids the position-array lag at the line.
        """
        drivers = (self.ir["DriverInfo"] or {}).get("Drivers", []) or []
        laps    = self.ir["CarIdxLap"] or []
        pcts    = self.ir["CarIdxLapDistPct"] or []
        best = None   # (progress, idx, number, name)
        for d in drivers:
            idx = d.get("CarIdx")
            if idx is None:
                continue
            if d.get("CarIsPaceCar") == 1 or d.get("IsSpectator") == 1:
                continue
            lap = laps[idx] if idx < len(laps) else None
            pct = pcts[idx] if idx < len(pcts) else None
            if lap is None or pct is None or pct < 0:
                continue
            prog = (lap or 0) + max(pct, 0.0)
            if best is None or prog > best[0]:
                best = (prog, idx, str(d.get("CarNumber", "")),
                        d.get("UserName", ""))
        if best is not None:
            return best[1], best[2], best[3]
        # Fallback (no lap data yet): old class-position approach
        positions = self.ir["CarIdxClassPosition"] or []
        for d in drivers:
            idx = d.get("CarIdx")
            if idx is None:
                continue
            if d.get("CarIsPaceCar") == 1 or d.get("IsSpectator") == 1:
                continue
            if idx < len(positions) and positions[idx] == 1:
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

    def _dbg(self, tag, **kw):
        """Append one diagnostic JSON line (best-effort, never raises)."""
        try:
            rec = {"tag": tag, "wall": time.strftime("%Y-%m-%dT%H:%M:%S")}
            rec.update(kw)
            os.makedirs(os.path.dirname(DEBUG_LOG), exist_ok=True)
            with open(DEBUG_LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        except Exception:
            pass

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
        # UNIFIED DETECTION — all triggers run in parallel, first one wins.
        #
        # WHY (rewritten 2026-06-04, after PCCD Silverstone 21.05. failed):
        # League sessions routinely set BOTH SessionLaps (e.g. a 100-lap
        # cap) and SessionTime (the real 25-min limit). The old exclusive
        # `if total_laps is not None: lap-based else: timed` branch saw
        # "100 laps" and waited for lap 100 forever — the timed logic
        # never ran, so no flag ever appeared. Detectors must run side
        # by side; whichever recognises the final lap first wins.
        # ════════════════════════════════════════════════════════════════════
        self._ticks_in_session += 1

        sess_state    = self.ir["SessionState"]
        session_flags = self.ir["SessionFlags"] or 0
        time_rem      = self.ir["SessionTimeRemain"]

        # SessionState: Invalid=0, GetInCar=1, Warmup=2, ParadeLaps=3,
        # Racing=4, Checkered=5, CoolDown=6.
        racing          = sess_state is not None and int(sess_state) >= 4
        state_checkered = sess_state is not None and int(sess_state) >= 5

        # Is there a finite race clock? ("unlimited" shows up as a huge
        # sentinel value, typically 604800.)
        timed_clock = time_rem is not None and 0 <= time_rem < 1e6
        if timed_clock:
            self._timed_seen = True

        avg_lap = (sum(self._lap_times) / len(self._lap_times)
                   if self._lap_times else None)

        # Leader lap-time estimate for the timed-race white-flag rule.
        # MEDIAN of the rolling window — robust against one pit-stop or
        # incident lap inflating the estimate and firing white too early.
        # Fallbacks: iRacing's EstLapTime, then a 120 s default.
        if self._lap_times:
            srt = sorted(self._lap_times)
            median = srt[len(srt) // 2]
            # min(median, most recent lap): a caution-inflated median must
            # not fire the white a lap early — the checkered now follows
            # the NEXT crossing unconditionally, so an early white would
            # drag the finish forward with it.
            lap_estimate = min(median, self._lap_times[-1])
            estimate_src = "median/last_lap"
        else:
            est = self.ir["EstLapTime"]
            if est and est > 0:
                lap_estimate = float(est)
                estimate_src = "EstLapTime"
            else:
                lap_estimate = 120.0
                estimate_src = "default_120s"

        # ── LATE-JOIN DETECTION (first ~5 s of watching this session ONLY) ──
        # If we start observing a session that is ALREADY in its checkered
        # phase, we missed the white-flag moment — arm straight for
        # checkered. The tick-count gate is essential: iRacing flips
        # SessionState to Checkered at TIMER EXPIRY (mid-lap, before the
        # leader starts the final lap), so without the gate this branch
        # hijacked every normal timed race and silently swallowed the
        # white flag — the exact failure of the May 2026 streams.
        if (not self._white_shown and not self._timed_last_lap
                and self._ticks_in_session < 50
                and state_checkered):
            self._white_shown    = True
            self._timed_last_lap = True
            print(f"[flag] LATE JOIN — SessionState={sess_state} on first "
                  f"observation, skipping white flag, armed for checkered")

        # ── DIAGNOSTICS: one line per leader crossing ───────────────────────
        # Captures the exact inputs the decision is made on, so a no-bit
        # timed/heat race that misbehaves can be analysed afterwards.
        if crossed_sf:
            self._dbg("crossing",
                      t=round(sess_t, 1), leader=leader_num, cur_lap=cur_lap,
                      total_laps=self._total_laps,
                      time_rem=(round(time_rem, 1) if time_rem is not None else None),
                      laps_rem=self.ir["SessionLapsRemain"],
                      timed_seen=self._timed_seen,
                      avg_lap=(round(avg_lap, 1) if avg_lap else None),
                      lap_est=round(lap_estimate, 1), estimate_src=estimate_src,
                      sess_state=sess_state, flags=hex(int(session_flags)),
                      white_shown=self._white_shown, check_shown=self._check_shown)

        # ── WHITE FLAG ──────────────────────────────────────────────────────
        fired_white_this_tick = False
        if racing and not self._white_shown:
            white_via = None

            # (1) AUTHORITATIVE: iRacing's own session-wide white-flag bit.
            #     Verified against race logs: fires exactly when the leader
            #     starts the final lap — in lap-based AND timed races, and
            #     regardless of the league's "session ending" setting.
            if session_flags & self.FLAG_BIT_WHITE:
                white_via = "SessionFlags white bit"

            # (2) FALLBACK, lap-count: leader just started the final lap of
            #     a genuinely lap-limited race. (In "100 laps OR 25 min"
            #     league configs this never fires — the timer fallback
            #     below covers those.)
            elif (self._total_laps is not None
                    and cur_lap == self._total_laps and crossed_sf):
                white_via = f"lap_count {cur_lap}/{self._total_laps}"

            # (3) FALLBACK, timed: iRacing's actual rule (verified against
            #     ALL logged races, 2026-06-04 evening): the leader takes
            #     the white flag at the LAST S/F crossing BEFORE the clock
            #     expires — i.e. the crossing where the remaining time no
            #     longer fits a full lap — and the checkered at the NEXT
            #     crossing (the first one past expiry). Confirmed by the
            #     white/checkered bit timestamps at Spa 27.05. / Thruxton
            #     02.06. / Magny-Cours 26.05., and by the end sequences of
            #     the no-bits PCCD Silverstone 21.05. and Miami 04.06.
            #     races (race over at the first crossing after expiry,
            #     cooldown right behind it).
            #     NOTE: the morning-of-2026-06-04 version used a "+1 lap
            #     after expiry" rule — one lap LATE; white fired at the
            #     real finish. The Miami 40-min race exposed it. The
            #     `time_rem <= lap_estimate` form also covers a missed
            #     earlier crossing (negative time_rem still matches).
            elif (self._timed_seen and crossed_sf
                    and time_rem is not None and time_rem <= lap_estimate):
                white_via = (f"timed_last_crossing time_rem={time_rem:.1f}s "
                             f"< {lap_estimate:.1f}s ({estimate_src})")

            # (4) FALLBACK, timer-expiry safety net: the race clock has run out
            #     (iRacing flips SessionState to Checkered at expiry, normally
            #     mid-way through the leader's final lap) but none of the above
            #     caught the final-lap start — e.g. a no-bit league race where
            #     the lap-time estimate missed the right crossing. This is the
            #     failure the heat/feature time-based races hit: NOTHING fired.
            #     Show the white now so the sequence isn't missed entirely; the
            #     checkered still follows at the leader's next crossing. Gated
            #     past the late-join window so it can't hijack the race start,
            #     and to timed sessions so pure lap races are unaffected.
            elif (self._timed_seen and state_checkered
                    and self._ticks_in_session >= 50):
                white_via = f"timer_expiry sess_state={sess_state}"

            if white_via:
                with self._lock:
                    self.state = "white_flag"
                self._white_shown     = True
                self._timed_last_lap  = True
                self._white_fired_at  = time.time()
                fired_white_this_tick = True
                self._dbg("WHITE", via=white_via, leader=leader_num,
                          cur_lap=cur_lap, t=round(sess_t, 1),
                          time_rem=(round(time_rem, 1) if time_rem is not None else None))
                print(f"[flag] WHITE FLAG (via {white_via}) — "
                      f"#{leader_num} {leader_name} lap={cur_lap}")

        # ── CHECKERED FLAG ──────────────────────────────────────────────────
        if (self._white_shown and not self._check_shown
                and not fired_white_this_tick):
            check_via = None
            white_age = time.time() - self._white_fired_at

            # (1) AUTHORITATIVE: iRacing's checkered bit.
            if session_flags & self.FLAG_BIT_CHECKERED:
                check_via = "SessionFlags checkered bit"

            # (2) Leader crosses S/F again after the white flag — that IS
            #     the finish, fire the checkered immediately (user rule:
            #     "checkered as soon as the leader crosses the line").
            #     The old extra requirement `time_rem <= 0.5` was meant to
            #     protect against a too-early white, but it delayed the
            #     checkered whenever the timing was off in the other
            #     direction. The MIN_FINAL_LAP_S guard still stops this
            #     firing on the same crossing that raised the white flag
            #     (bit + crossing arrive within a tick of each other).
            #     _white_fired_at is 0 on late-join, so late joins pass.
            elif (crossed_sf
                    and (self._white_fired_at == 0.0
                         or white_age > self.MIN_FINAL_LAP_S)):
                check_via = f"crossed_sf time_rem={time_rem}"

            # (3) Lap counter ticked past the final lap (lap races).
            elif (self._total_laps is not None
                    and cur_lap > self._total_laps):
                check_via = f"lap_count {cur_lap}>{self._total_laps}"

            # (4) Safety net: state says checkered AND 1.5 lap-lengths have
            #     passed since the white flag — the leader's final lap is
            #     long over, so we must have missed the crossing between
            #     polls. Anchored to the white-flag moment, NOT to timer
            #     expiry: under the +1-lap rule the final lap can END up to
            #     two full laps after the clock hits zero, so any
            #     "time_rem < -avg_lap" style condition fires mid-final-lap.
            elif (state_checkered and avg_lap is not None
                    and self._white_fired_at > 0.0
                    and white_age > 1.5 * avg_lap + 5.0):
                check_via = "safety_net"

            if check_via:
                with self._lock:
                    self.state = "checkered"
                self._check_shown    = True
                self._check_shown_at = time.time()
                self._dbg("CHECKERED", via=check_via, leader=leader_num,
                          cur_lap=cur_lap, t=round(sess_t, 1), sess_state=sess_state)
                print(f"[flag] CHECKERED (via {check_via}) — "
                      f"#{leader_num} {leader_name} "
                      f"sess_state={sess_state}")

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
        self._timed_seen      = False
        self._ticks_in_session = 0
        self._white_fired_at  = 0.0
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
    print("  (auto-hides 60s after checkered)")
    print()
    print("  Supports lap-based AND timed races (incl. 'laps OR time').")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    app.run(host="0.0.0.0", port=5008, debug=False, use_reloader=False)
