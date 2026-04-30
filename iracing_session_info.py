"""
iRacing Session Info Overlay
----------------------------
A minimal standalone OBS overlay that shows the current session name on
top, with the session's total length and remaining time below.

Examples of what it shows:
    RACE
    Total:      45:00
    Remaining:  12:34

    QUALIFYING                 (lap-based session)
    Total:      8 laps
    Remaining:  3 laps

Requirements:  pip install pyirsdk flask
Run:           python iracing_session_info.py
Open:          http://localhost:5010

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
        # If SessionLaps is a number, treat as a lap-based session.
        # Otherwise SessionTime gives us the timed-race length.
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

        if not is_lap_based:
            raw_time = str(cur_session.get("SessionTime", "")).strip()
            if raw_time and "unlimited" not in raw_time.lower():
                # Format is e.g. "1800.0000 sec"
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

        remain_laps = ir["SessionLapsRemain"]
        if remain_laps is None or remain_laps > 1e6 or remain_laps < 0:
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

async function tick() {
    try {
        const r = await fetch('/status');
        const d = await r.json();
        const card = document.getElementById('card');

        if (!d.connected || !d.session_name) {
            card.classList.add('offline');
            document.getElementById('session-name').textContent = '—';
            document.getElementById('total').textContent  = '—';
            document.getElementById('remain').textContent = '—';
            return;
        }
        card.classList.remove('offline');

        document.getElementById('session-name').textContent =
            (d.session_name || d.session_type || '—').toUpperCase();

        // Choose the unit (laps vs time) per session length type
        let totalStr, remainStr;
        if (d.is_lap_based) {
            totalStr  = fmtLaps(d.total_laps)    || '—';
            remainStr = fmtLaps(d.remain_laps)   || '—';
        } else {
            totalStr  = fmtTime(d.total_seconds)  || '—';
            remainStr = fmtTime(d.remain_seconds) || '—';
        }

        document.getElementById('total').textContent  = totalStr;
        document.getElementById('remain').textContent = remainStr;
    } catch (e) {
        // Server unreachable — show offline state
        const card = document.getElementById('card');
        card.classList.add('offline');
        document.getElementById('session-name').textContent = '—';
        document.getElementById('total').textContent  = '—';
        document.getElementById('remain').textContent = '—';
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
    print("  Open in browser:  http://localhost:5010")
    print("  Transparent background — designed as an OBS browser source.")
    print("  Shows the active session name + total / remaining time.")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=5010, debug=False, use_reloader=False)
    finally:
        poller.stop()
