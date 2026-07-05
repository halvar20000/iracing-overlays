"""
iRacing "New Race Leader" Overlay
---------------------------------
A minimal standalone OBS overlay that flashes a big

        NEW RACE LEADER
        <Driver Name>

banner for ~10 seconds whenever a different car takes over the lead of
the race. It ONLY does anything during an actual **Race** session — in
practice, qualifying / practice / warmup show nothing at all.

Requirements:  pip install pyirsdk flask
Run:           python iracing_race_leader.py
Open:          http://localhost:5018
Preview:       http://localhost:5018/?demo=1   (fires a fake banner every
                                                12 s so you can style it
                                                without iRacing running)

Designed as an OBS browser source:
  - Transparent background (no toggle needed)
  - Banner slides in, holds, then fades out after 10 s
  - Nothing on screen the rest of the time

Runs in parallel with the other iracing_*.py scripts on its own port.

------------------------------------------------------------------------
HOW LEADER DETECTION WORKS
------------------------------------------------------------------------
The "leader" is the car in P1 **overall** (class is ignored — this is a
single overall-leader banner). Position is derived from live track
progress (CarIdxLap + CarIdxLapDistPct) rather than iRacing's
CarIdxPosition, because CarIdxPosition only updates at the start/finish
line — an overtake for the lead mid-lap wouldn't show up for up to a
full lap. This mirrors how iracing_standings.py orders the field.

To avoid firing on jitter (two cars nose-to-tail, progress values
flickering back and forth, a car briefly popping out of the world), a
new leader must be the front-runner for STABLE_POLLS consecutive polls
before it is *confirmed*.

The FIRST confirmed leader of a race is set **silently** — taking the
green flag from pole is not a "new leader" event. Every confirmed change
after that increments a change counter and records the driver's name;
the browser watches that counter and shows the banner for 10 s.
"""

import threading
import time

from flask import Flask, jsonify, render_template_string, request

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()


# iRacing SessionState values (pyirsdk). Racing == 4. We only detect
# leaders while actually racing, so parade laps / grid formation / the
# cool-down lap can't produce false "new leader" pops.
SESSION_STATE_RACING = 4

# How many consecutive polls a car must hold P1 before we trust it as the
# leader. At 4 Hz, 3 polls ~= 0.75 s — long enough to reject side-by-side
# progress jitter, short enough to feel instant on a clean pass.
STABLE_POLLS = 3

# Seconds the banner stays on screen (client-side timer mirrors this).
BANNER_SECONDS = 10


# =============================================================================
# Pure leader state machine (no SDK dependency — unit-testable)
# =============================================================================
class LeaderState:
    """Tracks the confirmed race leader and emits change events.

    Deliberately free of any iRacing / Flask code so it can be exercised
    with plain values in test_race_leader.py.

    Public read fields (snapshot into the JSON response):
        leader_idx    current confirmed leader CarIdx (or None)
        leader_name   current confirmed leader name   ("" if none)
        change_id     monotonically increasing counter; bumped on every
                      genuine lead change (NOT on the silent first set)
        event_name    name to display for the most recent change event
        event_ts      monotonic timestamp of the most recent change
    """

    def __init__(self, stable_polls: int = STABLE_POLLS):
        self.stable_polls = stable_polls
        self.reset()

    def reset(self) -> None:
        """Wipe all state — called when the race session changes."""
        self.leader_idx = None
        self.leader_name = ""
        self.change_id = 0
        self.event_name = ""
        self.event_ts = 0.0
        self._cand_idx = None
        self._cand_count = 0

    def update(self, is_racing: bool, candidate_idx, candidate_name: str,
               now: float | None = None) -> bool:
        """Feed one poll's front-runner in.

        Args:
            is_racing:       True only when a Race session is green (Racing).
            candidate_idx:   CarIdx of the current front-runner, or None.
            candidate_name:  that driver's display name.
            now:             timestamp for the event (defaults to monotonic).

        Returns True iff this call produced a NEW-leader event (i.e. a real
        lead change, not the silent first set).
        """
        if now is None:
            now = time.monotonic()

        # Not racing, or no valid front-runner → clear the debounce window
        # but keep the confirmed leader (so we don't re-fire when racing
        # resumes with the same guy still leading).
        if not is_racing or candidate_idx is None:
            self._cand_idx = None
            self._cand_count = 0
            return False

        # Debounce: the candidate must repeat for `stable_polls` in a row.
        if candidate_idx == self._cand_idx:
            self._cand_count += 1
        else:
            self._cand_idx = candidate_idx
            self._cand_count = 1

        if self._cand_count < self.stable_polls:
            return False

        # Candidate is stable. If it already matches the confirmed leader,
        # nothing to do.
        if candidate_idx == self.leader_idx:
            return False

        # Confirmed leader changed.
        first_of_race = self.leader_idx is None
        self.leader_idx = candidate_idx
        self.leader_name = candidate_name
        if first_of_race:
            # Taking the green from pole is not a "new leader" event.
            return False

        self.change_id += 1
        self.event_name = candidate_name
        self.event_ts = now
        return True

    def snapshot(self) -> dict:
        return {
            "leader_idx":  self.leader_idx,
            "leader_name": self.leader_name,
            "change_id":   self.change_id,
            "event_name":  self.event_name,
        }


# =============================================================================
# Leader poller (SDK-backed)
# =============================================================================
class LeaderPoller(SDKPoller):
    tag = "leader"

    def __init__(self, poll_hz: int = 4):
        super().__init__(poll_interval=1.0 / poll_hz)
        self.state = LeaderState()
        self._session_key = None   # (SessionUniqueID, SessionNum)
        self._is_race_cache = {}   # session_key -> bool (Race?)

    # ---- helpers --------------------------------------------------------
    def _driver_names(self, ir) -> dict:
        """CarIdx -> (name, car_number), excluding pace car & spectators."""
        info = ir["DriverInfo"] or {}
        out = {}
        for d in info.get("Drivers", []) or []:
            cidx = d.get("CarIdx")
            if cidx is None:
                continue
            if d.get("CarIsPaceCar") == 1:
                continue
            if d.get("IsSpectator") == 1:
                continue
            out[cidx] = (
                d.get("UserName", "") or "",
                str(d.get("CarNumber", "") or ""),
            )
        return out

    def _is_race_session(self, ir, session_key, sess_num) -> bool:
        """True if the current session's SessionType is a Race.

        The SessionType string is static per session, so parse the (heavy,
        growing) SessionInfo YAML only once per session and cache it.
        """
        if session_key in self._is_race_cache:
            return self._is_race_cache[session_key]
        info = ir["SessionInfo"] or {}
        is_race = False
        for s in info.get("Sessions", []) or []:
            if s.get("SessionNum") == sess_num:
                stype = (s.get("SessionType") or "").lower()
                is_race = "race" in stype
                break
        # Only cache once the YAML actually contains the session block.
        if info.get("Sessions"):
            self._is_race_cache[session_key] = is_race
        return is_race

    def _leading_car(self, ir, names: dict):
        """Return (car_idx, name) of the overall front-runner, or (None, '').

        Front-runner = greatest track progress (CarIdxLap + CarIdxLapDistPct)
        among cars that are in the world and have a known driver.
        """
        laps    = ir["CarIdxLap"] or []
        lap_pct = ir["CarIdxLapDistPct"] or []
        surface = ir["CarIdxTrackSurface"] or []   # -1 = NotInWorld

        best_idx = None
        best_prog = -1.0
        for cidx, (name, _num) in names.items():
            if cidx < len(surface) and surface[cidx] == -1:
                continue  # not in world (garage / tow / disconnected)
            lap = laps[cidx] if cidx < len(laps) else 0
            pct = lap_pct[cidx] if cidx < len(lap_pct) else 0.0
            if lap is None or lap < 0:
                lap = 0
            if pct is None or pct < 0:
                pct = 0.0
            prog = float(lap) + float(pct)
            if prog > best_prog:
                best_prog = prog
                best_idx = cidx
        if best_idx is None:
            return None, ""
        return best_idx, names[best_idx][0]

    # ---- poll -----------------------------------------------------------
    def _read_snapshot(self) -> dict:
        ir = self.ir
        ir.freeze_var_buffer_latest()

        sess_num = ir["SessionNum"]
        session_key = (ir["SessionUniqueID"], sess_num)

        # Reset all leader tracking when the session changes (e.g. quali → race,
        # or a new race). Also drops the previous race's confirmed leader so the
        # next race silently re-initialises.
        if session_key != self._session_key:
            self._session_key = session_key
            self.state.reset()

        is_race = self._is_race_session(ir, session_key, sess_num)
        sess_state = ir["SessionState"]
        is_racing = bool(is_race) and (sess_state == SESSION_STATE_RACING)

        names = self._driver_names(ir)
        lead_idx, lead_name = self._leading_car(ir, names) if is_racing else (None, "")

        self.state.update(is_racing, lead_idx, lead_name)

        snap = {
            "connected":     True,
            "is_race":       bool(is_race),
            "session_state": int(sess_state) if sess_state is not None else None,
            "is_racing":     is_racing,
            "banner_seconds": BANNER_SECONDS,
        }
        snap.update(self.state.snapshot())
        return snap

    # run/get/stop inherited from SDKPoller.


# =============================================================================
# Flask
# =============================================================================
app = Flask(__name__)
poller = LeaderPoller(poll_hz=4)


@app.after_request
def _no_cache(resp):
    if "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing — New Race Leader</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
        width: 100%; height: 100%;
        background: transparent;
        font-family: 'Rajdhani', 'Segoe UI', system-ui, sans-serif;
        color: #fff;
        overflow: hidden;
    }
    body {
        display: flex; align-items: flex-start; justify-content: center;
        padding: 40px 16px;
    }

    /* The banner is hidden (off-screen + transparent) by default and only
       revealed when .show is added. It slides down from the top and fades. */
    .banner {
        display: inline-flex; flex-direction: column; align-items: center;
        gap: 6px;
        min-width: min(680px, 90vw);
        padding: 18px 46px 20px;
        border-radius: 14px;
        background: linear-gradient(135deg, #12121a 0%, #1c1c28 100%);
        border: 2px solid #ffd700;
        box-shadow: 0 10px 40px rgba(0, 0, 0, 0.55),
                    0 0 24px rgba(255, 215, 0, 0.25);
        text-align: center;
        opacity: 0;
        transform: translateY(-140%);
        transition: opacity 0.35s ease, transform 0.45s cubic-bezier(.2,.9,.3,1.2);
        pointer-events: none;
    }
    .banner.show {
        opacity: 1;
        transform: translateY(0);
    }

    /* Gold accent title with checkered-flag emoji on each side */
    .title {
        position: relative;
        font-size: clamp(22px, 4.5vw, 40px);
        font-weight: 700;
        letter-spacing: 6px;
        text-transform: uppercase;
        color: #ffd700;
        text-shadow: 0 2px 10px rgba(255, 215, 0, 0.35);
    }
    .title::before { content: "\\1F3C1"; margin-right: 14px; font-size: 0.85em; }
    .title::after  { content: "\\1F3C1"; margin-left: 14px;  font-size: 0.85em; }

    .name {
        font-size: clamp(30px, 7vw, 66px);
        font-weight: 800;
        letter-spacing: 1px;
        line-height: 1.05;
        color: #ffffff;
        text-shadow: 0 3px 14px rgba(0, 0, 0, 0.7);
        white-space: nowrap;
    }

    /* Thin animated progress bar showing the 10 s countdown */
    .timer {
        margin-top: 10px;
        width: 100%; height: 4px;
        border-radius: 2px;
        background: rgba(255, 255, 255, 0.12);
        overflow: hidden;
    }
    .timer > i {
        display: block; height: 100%; width: 100%;
        background: linear-gradient(90deg, #ffd700, #ff9d00);
        transform-origin: left center;
        transform: scaleX(1);
    }
    .banner.show .timer > i {
        animation: countdown var(--secs, 10s) linear forwards;
    }
    @keyframes countdown { to { transform: scaleX(0); } }
</style>
</head>
<body>

<div class="banner" id="banner">
    <div class="title">New Race Leader</div>
    <div class="name" id="name">—</div>
    <div class="timer"><i></i></div>
</div>

<script>
const params = new URLSearchParams(location.search);
const DEMO = params.has("demo");

const banner = document.getElementById("banner");
const nameEl = document.getElementById("name");

let bannerSecs = 10;
let hideTimer = null;

function showBanner(name) {
    nameEl.textContent = name || "—";
    // Restart the CSS animations cleanly by removing + re-adding .show.
    banner.classList.remove("show");
    // Force reflow so the animation restarts even on back-to-back events.
    void banner.offsetWidth;
    banner.style.setProperty("--secs", bannerSecs + "s");
    banner.classList.add("show");

    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(() => banner.classList.remove("show"),
                           bannerSecs * 1000);
}

// ---- Demo mode: cycle sample names so styling can be previewed ----------
if (DEMO) {
    const demoNames = ["Max Verstappen", "Juan Manuel Fangio",
                       "Thomas Herbrig", "Ayrton Senna da Silva"];
    let i = 0;
    showBanner(demoNames[i++ % demoNames.length]);
    setInterval(() => showBanner(demoNames[i++ % demoNames.length]), 12000);
}

// ---- Live mode: watch the server's change counter -----------------------
let lastChangeId = null;   // null until first response (prevents stale pop)

async function getStatus() {
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 4000);
    try {
        const r = await fetch("/status", { signal: ctrl.signal, cache: "no-store" });
        return await r.json();
    } catch (e) {
        return null;
    } finally {
        clearTimeout(to);
    }
}

async function tick() {
    if (DEMO) return;                       // demo drives itself
    const d = await getStatus();
    if (!d || !d.connected) return;

    if (typeof d.banner_seconds === "number") bannerSecs = d.banner_seconds;

    const cid = d.change_id || 0;

    // First live reading: sync silently so a mid-race (re)load of the
    // overlay doesn't immediately replay the last leader change.
    if (lastChangeId === null) { lastChangeId = cid; return; }

    // Session reset (new race) — counter went backwards. Re-sync silently.
    if (cid < lastChangeId) { lastChangeId = cid; return; }

    // A genuine new lead change happened — only ever shown in a Race session
    // (the server only bumps change_id while racing).
    if (cid > lastChangeId && d.is_race) {
        lastChangeId = cid;
        showBanner(d.event_name);
    } else {
        lastChangeId = cid;
    }
}
(function loop() { tick().finally(() => setTimeout(loop, 250)); })();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE_HTML)


@app.route("/status")
def status():
    return jsonify(poller.get())


@app.route("/debug")
def debug():
    """Raw state — leader, change_id, is_race, session_state. Handy when the
    banner doesn't fire when you expect it to."""
    return jsonify(poller.get())


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()

    print("\n" + "=" * 60)
    print("  iRacing New Race Leader Overlay")
    print("  Open in browser:  http://localhost:5018")
    print("  Preview styling:  http://localhost:5018/?demo=1")
    print("  Shows 'NEW RACE LEADER' + name for 10s on every lead")
    print("  change — RACE sessions only.")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=5018, debug=False,
                use_reloader=False, threaded=True)
    finally:
        poller.stop()
