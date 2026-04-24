"""
iRacing LIVE / REPLAY Indicator
-------------------------------
A minimal standalone OBS overlay that shows a big pulsing "LIVE" badge
when the iRacing view is live, and "REPLAY" when it isn't.

Requirements:  pip install pyirsdk flask
Run:           python iracing_live_indicator.py
Open:          http://localhost:5004

Designed specifically as an OBS browser source:
  - Transparent background by default (no toggle needed)
  - Large centered badge visible at a glance
  - Auto-adapts: centers and fills whatever OBS source size you set

Runs in parallel with the other iracing_*.py scripts.  It connects to
iRacing independently, so it works whether or not the main dashboard
is running.
"""

import threading
from flask import Flask, jsonify, render_template_string

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()


# -----------------------------------------------------------------------------
# Live-status poller (minimal — only reads replay state)
# -----------------------------------------------------------------------------
class LivePoller(SDKPoller):
    tag = "live"

    def __init__(self, poll_hz: int = 5):
        super().__init__(poll_interval=1.0 / poll_hz)

    def _read_snapshot(self) -> dict:
        ir = self.ir
        ir.freeze_var_buffer_latest()

        frame = ir["ReplayFrameNum"]
        end   = ir["ReplayFrameNumEnd"]
        speed = ir["ReplayPlaySpeed"]
        slow  = ir["ReplayPlaySlowMotion"]
        # iRacing exposes a direct flag for "is the user currently watching
        # a replay rather than a live session". When present, it's the most
        # reliable signal — far better than geometric at_end / speed
        # heuristics, which get fooled by 1x replay playback near the end
        # of a saved replay file.
        #
        # The exact field name varies between pyirsdk versions, so we try a
        # few candidates defensively. None means "field not available" —
        # we fall back to the old heuristic in that case.
        is_replay_flag = None
        for name in ("IsReplayPlaying", "ReplayPlaying"):
            val = ir[name]
            if val is not None:
                is_replay_flag = bool(val)
                break

        # `ReplayFrameNumEnd` is the number of frames the playhead is
        # BEHIND the live tip (0 = at the tip, 60 = one second back,
        # 600 = ten seconds back). The previous code compared it against
        # ReplayFrameNum as if they were on the same scale — nonsense —
        # which made at_end true almost always and confused the logic.
        at_end = False
        if end is not None:
            at_end = end <= 60

        # Decision logic:
        #   • Not in replay mode at all (IsReplayPlaying=False) → LIVE.
        #   • In replay mode BUT sitting at the live tip at 1x speed →
        #     LIVE ("catch-up" state — the frames being shown are the
        #     fresh live feed, even though iRacing flags it as replay).
        #   • Otherwise → replay / paused / ff / rewind / slow, per speed.
        decision_source = "heuristic"
        if is_replay_flag is not None:
            decision_source = "IsReplayPlaying"
            if not is_replay_flag:
                # Definitely live: iRacing isn't in replay mode at all.
                is_live = (speed == 1) and not slow
            else:
                # In replay mode — LIVE only if we're catching up the
                # live tip at normal speed.
                is_live = at_end and (speed == 1) and not slow
        else:
            # Field unavailable — fall back to pure geometric test.
            is_live = at_end and (speed == 1) and not slow

        if is_live:                               mode = "live"
        elif speed == 0:                          mode = "paused"
        elif speed is not None and speed < 0:     mode = "rewind"
        elif speed is not None and speed > 1:     mode = "fast_forward"
        elif slow:                                mode = "slow_motion"
        else:                                     mode = "replay"

        return {
            "connected":       True,
            "is_live":         bool(is_live),
            "mode":            mode,
            # Diagnostic fields exposed via /debug.
            "replay_frame":    int(frame) if frame is not None else None,
            "replay_end":      int(end)   if end   is not None else None,
            "play_speed":      speed,
            "slow_motion":     bool(slow),
            "at_end":          at_end,
            "is_replay_flag":  is_replay_flag,
            "decision_source": decision_source,
        }

    # run/get/stop inherited from SDKPoller.


# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
poller = LivePoller(poll_hz=5)


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


PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Live Indicator</title>
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

    .badge {
        display: inline-flex; align-items: center; gap: 14px;
        padding: 14px 28px;
        border-radius: 999px;
        border: 3px solid transparent;
        font-size: clamp(20px, 6vw, 52px);
        font-weight: 800;
        letter-spacing: 2px;
        text-transform: uppercase;
        box-shadow: 0 4px 24px rgba(0, 0, 0, 0.5);
        transition: background 0.2s, color 0.2s, border-color 0.2s;
        user-select: none;
    }
    .dot {
        width: 0.7em; height: 0.7em; border-radius: 50%;
        background: #fff;
        flex-shrink: 0;
    }

    /* LIVE - pulsing red */
    .badge.live {
        background: #DC0028;
        border-color: #ff334f;
        color: #fff;
        animation: live-glow 1.6s infinite ease-in-out;
    }
    .badge.live .dot {
        background: #fff;
        box-shadow: 0 0 0 0 rgba(255, 255, 255, 0.9);
        animation: dot-pulse 1.6s infinite ease-in-out;
    }
    @keyframes live-glow {
        0%, 100% { box-shadow: 0 4px 24px rgba(220, 0, 40, 0.4); }
        50%      { box-shadow: 0 4px 36px rgba(220, 0, 40, 0.9); }
    }
    @keyframes dot-pulse {
        0%   { box-shadow: 0 0 0 0 rgba(255, 255, 255, 0.85); }
        70%  { box-shadow: 0 0 0 12px rgba(255, 255, 255, 0);  }
        100% { box-shadow: 0 0 0 0 rgba(255, 255, 255, 0);  }
    }

    /* REPLAY - amber */
    .badge.replay,
    .badge.rewind,
    .badge.fast_forward,
    .badge.slow_motion {
        background: #b8860b;
        border-color: #facc15;
        color: #fff;
    }
    .badge.replay .dot,
    .badge.rewind .dot,
    .badge.fast_forward .dot,
    .badge.slow_motion .dot {
        background: #fff;
    }

    /* PAUSED - gray */
    .badge.paused {
        background: #4a4a55;
        border-color: #888;
        color: #e8e8ea;
    }
    .badge.paused .dot { background: #e8e8ea; }

    /* OFFLINE - dim, almost invisible */
    .badge.offline {
        background: rgba(20, 20, 28, 0.5);
        border-color: #333;
        color: #666;
    }
    .badge.offline .dot { background: #666; }
</style>
</head>
<body>

<div class="badge offline" id="badge">
    <span class="dot"></span>
    <span id="label">—</span>
</div>

<script>
const LABELS = {
    "live":         "LIVE",
    "replay":       "REPLAY",
    "rewind":       "REWIND",
    "fast_forward": "FAST FWD",
    "slow_motion":  "SLOW-MO",
    "paused":       "PAUSED",
    "offline":      "—",
};

async function tick() {
    try {
        const r = await fetch("/status");
        const d = await r.json();
        const badge = document.getElementById("badge");
        const label = document.getElementById("label");
        const mode = d.connected ? (d.mode || "replay") : "offline";
        if (badge.dataset.mode !== mode) {
            badge.dataset.mode = mode;
            // Clear all mode classes then apply the current one
            badge.className = "badge " + mode;
            label.textContent = LABELS[mode] || mode.toUpperCase();
        }
    } catch (e) {
        // Server not reachable - show offline
        const badge = document.getElementById("badge");
        if (badge.dataset.mode !== "offline") {
            badge.dataset.mode = "offline";
            badge.className = "badge offline";
            document.getElementById("label").textContent = "—";
        }
    }
}
setInterval(tick, 200);   // 5 Hz is plenty for a big badge
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


@app.route("/debug")
def debug():
    """Raw telemetry + heuristic state — useful when the indicator
    doesn't match what you see in iRacing. Open /debug in a browser."""
    return jsonify(poller.get())


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()

    print("\n" + "=" * 60)
    print("  iRacing Live / Replay Indicator")
    print("  Open in browser:  http://localhost:5004")
    print("  Transparent background - designed as an OBS browser source.")
    print("  Badge shows LIVE (red pulsing) or REPLAY (amber).")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=5004, debug=False, use_reloader=False)
    finally:
        poller.stop()
