"""
iRacing Race Results — Lite
---------------------------
A minimal post-race results view: just finishing position, driver name,
and car.  Designed for clean OBS overlays where gaps/incidents/fastest
laps would be visual clutter.

Requirements:  pip install pyirsdk flask
Run:           python iracing_results_lite.py
Open:          http://localhost:5003

Runs in parallel with:
  - iracing_dashboard.py   (port 5000)
  - iracing_grid.py        (port 5001)
  - iracing_results.py     (port 5002)
"""

import threading
from flask import Flask, jsonify, render_template_string

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()


# -----------------------------------------------------------------------------
# Results poller (slim version - only what the lite view needs)
# -----------------------------------------------------------------------------
class LiteResultsPoller(SDKPoller):
    tag = "results-lite"
    poll_interval = 2.0

    def _driver_map(self) -> dict:
        info = self.ir["DriverInfo"] or {}
        out = {}
        for d in info.get("Drivers", []) or []:
            cidx = d.get("CarIdx")
            if cidx is None:
                continue
            if d.get("CarIsPaceCar") == 1:
                continue
            if d.get("IsSpectator") == 1:
                continue
            out[cidx] = {
                "car_idx":    cidx,
                "name":       d.get("UserName", "") or "",
                "car":        d.get("CarScreenName") or d.get("CarScreenNameShort") or "",
            }
        return out

    def _find_last_completed_race(self, sessions: list) -> dict | None:
        """Most recent race session that has ResultsPositions populated.

        Walks sessions in reverse. Used when we're not currently inside a
        race session, so we can keep the previous race's classification
        visible during warmup / cooldown between Race 1 and Race 2 in
        league formats. The old logic returned the last race regardless
        of whether it had data, which blanked the overlay during warmup
        because Race 2 was empty.
        """
        for s in reversed(sessions):
            stype = (s.get("SessionType") or "").lower()
            if "race" not in stype:
                continue
            if s.get("ResultsPositions"):
                return s
        return None

    def _read_snapshot(self) -> dict:
        ir = self.ir
        info = ir["SessionInfo"] or {}
        sessions = info.get("Sessions", []) or []
        weekend = ir["WeekendInfo"] or {}

        drivers = self._driver_map()

        session_num = ir["SessionNum"]
        current_session = None
        for s in sessions:
            if s.get("SessionNum") == session_num:
                current_session = s
                break

        # Prefer the active session if it's a race (live running order);
        # otherwise show the most recent race that has results, so the
        # Race 1 standings stay visible during the Warmup before Race 2.
        chosen = None
        if current_session and "race" in (current_session.get("SessionType") or "").lower():
            chosen = current_session
        else:
            chosen = self._find_last_completed_race(sessions)

        rows = []
        official = False
        source = None

        if chosen and chosen.get("ResultsPositions"):
            official = bool(chosen.get("ResultsOfficial", 0))
            source = "official" if official else "live"
            for r in chosen["ResultsPositions"]:
                cidx = r.get("CarIdx")
                drv = drivers.get(cidx)
                if not drv:
                    continue
                rows.append({
                    **drv,
                    "position": r.get("Position", 0) or 0,
                })

        rows.sort(key=lambda d: (d["position"] == 0, d["position"]))

        return {
            "connected":    True,
            "official":     official,
            "source":       source,
            "track":        weekend.get("TrackDisplayName", ""),
            "track_config": weekend.get("TrackConfigName", ""),
            "num_cars":     len(rows),
            "results":      rows,
        }



# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
poller = LiteResultsPoller()


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


LITE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Race Results (Lite)</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: 'Segoe UI', system-ui, sans-serif;
        background: #0a0a0f; color: #e8e8ea;
        min-height: 100vh; padding: 24px;
        transition: background 0.2s;
    }
    body.stream-mode { background: transparent; padding: 16px; }
    body.stream-mode .card,
    body.stream-mode .title-card { background: rgba(20,20,28,0.88); }

    .stream-toggle {
        position: fixed; top: 12px; right: 12px; z-index: 1000;
        background: rgba(20, 20, 28, 0.9);
        border: 1px solid #333; color: #bbb;
        padding: 8px 14px; border-radius: 4px;
        font-size: 12px; font-weight: 600; cursor: pointer;
        display: flex; gap: 8px; align-items: center; user-select: none;
    }
    .stream-toggle:hover { background: rgba(42, 31, 74, 0.9); color: #e8e8ea; }
    .stream-toggle.on { background: #DC0028; border-color: #ff334f; color: #fff; }
    .stream-toggle .kbd {
        background: rgba(255,255,255,0.12);
        padding: 1px 6px; border-radius: 3px; font-size: 10px; font-family: monospace;
    }
    body.stream-mode .stream-toggle { opacity: 0.15; }
    body.stream-mode .stream-toggle:hover { opacity: 1; }

    .wrapper { max-width: 760px; margin: 0 auto; }

    .title-card {
        background: linear-gradient(135deg, #2a1f4a 0%, #14141c 100%);
        border: 1px solid #9146FF;
        border-radius: 12px;
        padding: 18px 24px;
        margin-bottom: 16px;
        display: flex; justify-content: space-between; align-items: center;
    }
    .title-card h1 {
        font-size: 22px; font-weight: 700; color: #9146FF;
        letter-spacing: 0.5px;
    }
    .title-card .subtitle { font-size: 12px; color: #d4c5ff; margin-top: 2px; }
    .track-name { font-size: 14px; font-weight: 600; color: #e8e8ea; text-align: right; }

    .card {
        background: #14141c; border: 1px solid #222;
        border-radius: 12px; padding: 0; overflow: hidden;
        max-height: calc(100vh - 120px);
        position: relative;
    }
    .scroll-viewport {
        height: 100%;
        max-height: calc(100vh - 120px);
        overflow: hidden;
        position: relative;
    }
    .scroll-inner { transition: transform 0.3s; }
    .scroll-inner.autoscroll {
        animation: ticker var(--scroll-duration, 20s) linear infinite;
    }
    .scroll-viewport:hover .scroll-inner.autoscroll { animation-play-state: paused; }
    @keyframes ticker {
        0%,  15%  { transform: translateY(0); }
        85%, 99%  { transform: translateY(var(--scroll-distance, 0)); }
        100%      { transform: translateY(0); }
    }

    .row {
        display: grid;
        grid-template-columns: 48px 1fr auto;
        gap: 16px; align-items: center;
        padding: 10px 18px;
        border-bottom: 1px solid #222;
    }
    .row:last-child { border-bottom: none; }

    .pos {
        font-family: monospace; font-weight: 800;
        font-size: 22px; color: #e8e8ea;
        text-align: center;
    }
    .row.podium-1 .pos { color: #FFD700; }
    .row.podium-2 .pos { color: #C0C0C0; }
    .row.podium-3 .pos { color: #CD7F32; }
    .row.podium-1 { box-shadow: inset 4px 0 0 #FFD700; }
    .row.podium-2 { box-shadow: inset 4px 0 0 #C0C0C0; }
    .row.podium-3 { box-shadow: inset 4px 0 0 #CD7F32; }

    .driver-name {
        font-size: 17px; font-weight: 700; color: #e8e8ea;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .car-name {
        font-size: 13px; color: #888;
        text-align: right;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        max-width: 260px;
    }

    .empty-state {
        padding: 48px 32px; text-align: center; color: #666; font-size: 13px;
    }
    .empty-state .big { font-size: 18px; color: #888; margin-bottom: 8px; }

    .status-pill {
        display: inline-block;
        padding: 3px 10px; border-radius: 12px;
        font-size: 10px; text-transform: uppercase; letter-spacing: 1px;
        font-weight: 700;
        margin-top: 4px;
    }
    .status-pill.official { background: #1a4d2e; color: #6fe398; }
    .status-pill.live     { background: #4a3f1a; color: #facc15; }
    .status-pill.none     { background: #333; color: #888; }
</style>
</head>
<body>

<div class="stream-toggle" id="stream-toggle" onclick="toggleStreamMode()">
    <span id="stream-toggle-label">📺 Stream mode</span>
    <span class="kbd">H</span>
</div>

<div class="wrapper">
    <div class="title-card">
        <div>
            <h1>🏆 Race Results</h1>
            <div class="subtitle" id="subtitle">Waiting for race…</div>
        </div>
        <div>
            <div class="track-name" id="track-name">—</div>
            <div style="text-align:right"><span class="status-pill none" id="status-pill">No data</span></div>
        </div>
    </div>

    <div class="card">
        <div class="scroll-viewport" id="scroll-viewport">
            <div class="scroll-inner" id="scroll-inner">
                <div id="results-host"></div>
            </div>
        </div>
    </div>
</div>

<script>
const STREAM_KEY = "iracing_results_lite_stream_mode_v1";

function applyStreamMode(on) {
    document.body.classList.toggle("stream-mode", on);
    document.getElementById("stream-toggle").classList.toggle("on", on);
    document.getElementById("stream-toggle-label").textContent =
        on ? "📺 Stream mode ON" : "📺 Stream mode";
    try { localStorage.setItem(STREAM_KEY, on ? "1" : "0"); } catch (e) {}
}
function toggleStreamMode() { applyStreamMode(!document.body.classList.contains("stream-mode")); }
try { if (localStorage.getItem(STREAM_KEY) === "1") applyStreamMode(true); } catch (e) {}
document.addEventListener("keydown", e => {
    if (e.key === "h" || e.key === "H") toggleStreamMode();
});

function renderResults(rows) {
    const host = document.getElementById("results-host");
    if (!rows || !rows.length) {
        host.innerHTML = `
            <div class="empty-state">
                <div class="big">No results yet</div>
                Finishing order will appear here once the race is running.
            </div>`;
        updateAutoScroll();
        return;
    }

    const sig = rows.map(r => r.car_idx + ":" + r.position).join("|");
    if (host.dataset.sig === sig) return;
    host.dataset.sig = sig;

    let html = "";
    rows.forEach(r => {
        const podium = r.position >= 1 && r.position <= 3 ? `podium-${r.position}` : "";
        html += `
            <div class="row ${podium}">
                <div class="pos">${r.position || "—"}</div>
                <div class="driver-name">${r.name || "—"}</div>
                <div class="car-name">${r.car || ""}</div>
            </div>
        `;
    });
    host.innerHTML = html;
    requestAnimationFrame(updateAutoScroll);
}

function updateAutoScroll() {
    const viewport = document.getElementById("scroll-viewport");
    const inner    = document.getElementById("scroll-inner");
    if (!viewport || !inner) return;

    inner.classList.remove("autoscroll");
    inner.style.removeProperty("--scroll-distance");
    inner.style.removeProperty("--scroll-duration");

    const visibleH = viewport.clientHeight;
    const contentH = inner.scrollHeight;
    const overflow = contentH - visibleH;
    if (overflow <= 4) return;

    let duration = Math.max(12, Math.min(40, overflow * 0.08));
    inner.style.setProperty("--scroll-distance", (-overflow) + "px");
    inner.style.setProperty("--scroll-duration", duration.toFixed(1) + "s");
    inner.classList.add("autoscroll");
}
window.addEventListener("resize", () => setTimeout(updateAutoScroll, 100));

async function tick() {
    try {
        const r = await fetch("/results");
        const d = await r.json();

        const statusPill = document.getElementById("status-pill");
        if (!d.connected) {
            document.getElementById("subtitle").textContent = "Waiting for iRacing…";
            document.getElementById("track-name").textContent = "—";
            statusPill.className = "status-pill none";
            statusPill.textContent = "Disconnected";
            renderResults([]);
            return;
        }

        document.getElementById("track-name").textContent = d.track || "—";
        document.getElementById("subtitle").textContent =
            d.num_cars > 0 ? `${d.num_cars} drivers` : "Waiting for results…";

        if (d.source === "official") {
            statusPill.className = "status-pill official";
            statusPill.textContent = "OFFICIAL";
        } else if (d.source === "live") {
            statusPill.className = "status-pill live";
            statusPill.textContent = "LIVE";
        } else {
            statusPill.className = "status-pill none";
            statusPill.textContent = "No results";
        }

        renderResults(d.results);
    } catch (e) { console.error(e); }
}
setInterval(tick, 2000);
tick();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(LITE_HTML)


@app.route("/results")
def results():
    return jsonify(poller.get())


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()

    print("\n" + "=" * 60)
    print("  iRacing Race Results (Lite)")
    print("  Open in browser:  http://localhost:5003")
    print("  Minimal view: position, driver name, car.")
    print("  Runs in parallel to the other iracing_*.py scripts.")
    print("  Press H for Stream mode (transparent bg)")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=5003, debug=False, use_reloader=False)
    finally:
        poller.stop()
