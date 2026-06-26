"""
iRacing Session Info Overlay
----------------------------
A minimal standalone OBS overlay that shows the current session name on
top, with the session's total length and remaining time below.

Examples of what it shows:
    RACE                       (timed session — incl. "laps OR time"
    Total:      45:00           league configs, where the lap cap is
    Remaining:  12:34           never reached)

    RACE                       (genuinely lap-limited session: lap
    Total:      20 laps         count set, no finite time cap)
    Remaining:  7 laps

Requirements:  pip install pyirsdk flask
Run:           python iracing_session_info.py
Open:          http://localhost:5011

Designed as an OBS browser source — transparent background, centred
content, scales to whatever source size you set.
"""

import threading
from flask import Flask, jsonify, render_template_string

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()


# -----------------------------------------------------------------------------
# Session-info poller
# -----------------------------------------------------------------------------
class SessionInfoPoller(SDKPoller):
    tag = "sess"

    def __init__(self, poll_hz: int = 4):
        super().__init__(poll_interval=1.0 / poll_hz)

    def _read_snapshot(self) -> dict:
        ir = self.ir

        weekend  = ir["WeekendInfo"] or {}
        info     = ir["SessionInfo"] or {}
        sess_num = ir["SessionNum"]

        # Find the active session block in the SessionInfo YAML
        cur_session = None
        for s in info.get("Sessions", []) or []:
            if s.get("SessionNum") == sess_num:
                cur_session = s
                break

        if cur_session is None:
            return {
                "connected":     True,
                "session_name":  "",
                "session_type":  "",
                "is_lap_based":  False,
                "total_seconds": None,
                "total_laps":    None,
                "remain_seconds": None,
                "remain_laps":   None,
            }

        session_name = (cur_session.get("SessionName") or "") or \
                       (cur_session.get("SessionType") or "")
        session_type = (cur_session.get("SessionType") or "")

        # ─── total length ───────────────────────────────────────────────
        # iRacing reports session length in TWO different ways:
        #   - "SessionLaps":  string. "unlimited" or a number, e.g. "8"
        #   - "SessionTime":  string. e.g. "1800.0000 sec" or "unlimited"
        # We collect both so the snapshot is informative, but the
        # rendered card always shows TIME (heat-race format puts a time
        # cap on top of a lap count, and the user wants the wall-clock
        # view rather than the lap counter).
        total_laps = None
        total_seconds = None
        is_lap_based = False

        raw_laps = str(cur_session.get("SessionLaps", "")).strip().lower()
        if raw_laps and raw_laps != "unlimited":
            try:
                total_laps = int(raw_laps)
                is_lap_based = total_laps > 0
            except ValueError:
                total_laps = None

        # Always read SessionTime when it's specified — heat-race
        # sessions have BOTH a lap count AND a time cap, and we want
        # the time cap as the "total" for the time-view card.
        raw_time = str(cur_session.get("SessionTime", "")).strip()
        if raw_time and "unlimited" not in raw_time.lower():
            try:
                total_seconds = float(raw_time.split()[0])
            except (ValueError, IndexError):
                total_seconds = None

        # ─── remaining ──────────────────────────────────────────────────
        # iRacing exposes both. SessionTimeRemain is huge (~1e7) when the
        # session has no time limit; treat that as "no time remaining
        # info available". SessionLapsRemain is similar — large for
        # unlimited.
        remain_seconds = ir["SessionTimeRemain"]
        if remain_seconds is None or remain_seconds > 1e6:
            remain_seconds = None

        # iRacing uses 32767 as the "unlimited" sentinel for laps (NOT a
        # huge float like the time fields) — treat anything implausibly
        # large as "no lap limit".
        remain_laps = ir["SessionLapsRemain"]
        if remain_laps is None or remain_laps > 9000 or remain_laps < 0:
            remain_laps = None

        return {
            "connected":      True,
            "session_name":   session_name,
            "session_type":   session_type,
            "is_lap_based":   is_lap_based,
            "total_seconds":  total_seconds,
            "total_laps":     total_laps,
            "remain_seconds": float(remain_seconds) if remain_seconds is not None else None,
            "remain_laps":    int(remain_laps) if remain_laps is not None else None,
            # Track / event identification, useful as a sub-line
            "track":          (weekend.get("TrackDisplayName") or "") or
                              (weekend.get("TrackName") or ""),
        }


# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
poller = SessionInfoPoller(poll_hz=4)


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
<title>iRacing Session Info</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
        width: 100%; height: 100%;
        background: transparent;
        font-family: 'Segoe UI', system-ui, sans-serif;
        color: #fff;
        overflow: hidden;
    }
    body {
        display: flex; align-items: center; justify-content: center;
        padding: 16px;
    }

    .card {
        display: inline-flex; flex-direction: column; align-items: stretch;
        gap: 4px;
        padding: 16px 28px;
        border-radius: 14px;
        background: rgba(20, 20, 28, 0.78);
        border: 2px solid rgba(255, 107, 53, 0.5);
        box-shadow: 0 4px 28px rgba(0, 0, 0, 0.55);
        min-width: 260px;
        user-select: none;
    }

    .session-name {
        font-size: clamp(20px, 5vw, 42px);
        font-weight: 800;
        letter-spacing: 2px;
        text-transform: uppercase;
        color: #ff6b35;
        text-align: center;
        line-height: 1.05;
        text-shadow: 0 2px 6px rgba(0, 0, 0, 0.6);
    }

    .row {
        display: flex; align-items: baseline; justify-content: space-between;
        gap: 16px;
        font-size: clamp(14px, 2.6vw, 22px);
        font-weight: 600;
        font-variant-numeric: tabular-nums;
    }
    .row .label {
        color: #b0b0c0;
        letter-spacing: 1px;
        text-transform: uppercase;
        font-size: 0.7em;
    }
    .row .value { color: #fff; font-weight: 800; }
    .row.remain .value { color: #ffd166; }   /* highlight remaining */

    .divider {
        height: 1px; background: rgba(255, 255, 255, 0.12);
        margin: 6px 0 4px 0;
    }

    /* Hidden until we connect */
    .card.offline {
        background: rgba(20, 20, 28, 0.4);
        border-color: rgba(255, 255, 255, 0.08);
    }
    .card.offline .session-name { color: #4a4a55; }
    .card.offline .row .value { color: #4a4a55; }
</style>
</head>
<body>

<div class="card offline" id="card">
    <div class="session-name" id="session-name">—</div>
    <div class="divider"></div>
    <div class="row total">
        <span class="label">Total</span>
        <span class="value" id="total">—</span>
    </div>
    <div class="row remain">
        <span class="label">Remaining</span>
        <span class="value" id="remain">—</span>
    </div>
</div>

<script>
function fmtTime(seconds) {
    if (seconds == null) return null;
    const total = Math.max(0, Math.round(seconds));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
    return `${m}:${String(s).padStart(2,'0')}`;
}

function fmtLaps(n) {
    if (n == null) return null;
    return n === 1 ? '1 lap' : `${n} laps`;
}

// Hold the last good card through brief blips instead of flashing the
// offline "—" state on every dropped request. Only blank after sustained
// trouble — stops the flicker when the dev server drops a request under load.
let badPolls = 0;
const BAD_LIMIT = 4;   // ~2s at 2 Hz before we show offline

function showOffline() {
    document.getElementById('card').classList.add('offline');
    document.getElementById('session-name').textContent = '—';
    document.getElementById('total').textContent  = '—';
    document.getElementById('remain').textContent = '—';
}

async function tick() {
    let d = null;
    try {
        const r = await fetch('/status');
        d = await r.json();
    } catch (e) {
        d = null;
    }

    if (!d || !d.connected || !d.session_name) {
        badPolls++;
        if (badPolls < BAD_LIMIT) return;          // transient — keep last card
        showOffline();
        return;
    }
    badPolls = 0;

    const card = document.getElementById('card');
    card.classList.remove('offline');

    document.getElementById('session-name').textContent =
        (d.session_name || d.session_type || '—').toUpperCase();

    // LAP view only for genuinely lap-limited sessions: a lap count
    // with NO finite time cap. League "100 laps OR 25 min" configs
    // set BOTH — those are really timed races, where the lap cap is
    // never reached, so they keep the time countdown.
    const lapView = d.total_seconds == null
                    && (d.total_laps != null || d.remain_laps != null);
    if (lapView) {
        document.getElementById('total').textContent  =
            fmtLaps(d.total_laps)  || '—';
        document.getElementById('remain').textContent =
            fmtLaps(d.remain_laps) || '—';
    } else {
        document.getElementById('total').textContent  =
            fmtTime(d.total_seconds)  || '—';
        document.getElementById('remain').textContent =
            fmtTime(d.remain_seconds) || '—';
    }
}
setInterval(tick, 500);
tick();
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


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()

    print("\n" + "=" * 60)
    print("  iRacing Session Info Overlay")
    print("  Open in browser:  http://localhost:5011")
    print("  Transparent background — designed as an OBS browser source.")
    print("  Shows the active session name + total / remaining time.")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=5011, debug=False,
                use_reloader=False, threaded=True)
    finally:
        poller.stop()
