"""
iRacing Qualifying Grid Display (v2)
------------------------------------
Standalone companion to iracing_dashboard.py.

v2 adds:
  - Tighter column spacing
  - Stylized car silhouette next to each driver, tinted with their
    paint colors decoded from DriverInfo.CarDesignStr

Requirements:  pip install pyirsdk flask
Run:           python iracing_grid.py
Open:          http://localhost:5001
"""

import sys
import threading
import time
from flask import Flask, jsonify, render_template_string

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
# Grid data poller
# -----------------------------------------------------------------------------
class GridPoller:
    def __init__(self, poll_interval: float = 2.0):
        self.ir = irsdk.IRSDK()
        self.poll_interval = poll_interval
        self.connected = False
        self.data = {"connected": False}
        self._lock = threading.Lock()
        self._running = True

    def _check_connection(self) -> bool:
        if self.connected and not (self.ir.is_initialized and self.ir.is_connected):
            self.ir.shutdown()
            self.connected = False
            print("[grid] Disconnected from iRacing")
        elif not self.connected and self.ir.startup() and self.ir.is_initialized and self.ir.is_connected:
            self.connected = True
            print("[grid] Connected to iRacing")
        return self.connected

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
                "car_number": d.get("CarNumber", "") or "",
                "car":        d.get("CarScreenNameShort") or d.get("CarScreenName", ""),
                "irating":    d.get("IRating", 0) or 0,
                "license":    d.get("LicString", "") or "",
                "team_name":  d.get("TeamName", "") or "",
                "is_spectator": bool(d.get("IsSpectator", 0)),
            }
        return out

    def _find_qualifying_session(self, sessions: list) -> dict:
        qual_session = None
        for s in sessions:
            stype = (s.get("SessionType") or "").lower()
            if "qualify" in stype:
                results = s.get("ResultsPositions") or []
                if results:
                    qual_session = s
        return qual_session

    def _find_race_session(self, sessions: list) -> dict:
        race = None
        for s in sessions:
            stype = (s.get("SessionType") or "").lower()
            if "race" in stype:
                race = s
        return race

    def _read_snapshot(self) -> dict:
        ir = self.ir
        info = ir["SessionInfo"] or {}
        sessions = info.get("Sessions", []) or []
        weekend = ir["WeekendInfo"] or {}

        drivers = self._driver_map()
        qual = self._find_qualifying_session(sessions)
        race = self._find_race_session(sessions)

        source = None
        rows = []

        if qual and qual.get("ResultsPositions"):
            source = "qualifying"
            results = qual["ResultsPositions"]
            for r in results:
                cidx = r.get("CarIdx")
                drv = drivers.get(cidx)
                if not drv:
                    continue
                rows.append({
                    **drv,
                    "position":     r.get("Position", 0) or 0,
                    "class_position": r.get("ClassPosition", 0) or 0,
                    "best_time":    r.get("FastestTime", 0.0) or 0.0,
                    "lap_count":    r.get("LapsComplete", 0) or 0,
                    "interval":     r.get("Time", 0.0) or 0.0,
                })
        elif race and race.get("ResultsPositions"):
            source = "race_grid"
            results = race["ResultsPositions"]
            for r in results:
                cidx = r.get("CarIdx")
                drv = drivers.get(cidx)
                if not drv:
                    continue
                rows.append({
                    **drv,
                    "position":     r.get("Position", 0) or 0,
                    "class_position": r.get("ClassPosition", 0) or 0,
                    "best_time":    r.get("FastestTime", 0.0) or 0.0,
                    "lap_count":    0,
                    "interval":     0.0,
                })

        rows.sort(key=lambda d: (d["position"] == 0, d["position"]))

        return {
            "connected":    True,
            "source":       source,
            "track":        weekend.get("TrackDisplayName", ""),
            "track_config": weekend.get("TrackConfigName", ""),
            "num_cars":     len(rows),
            "grid":         rows,
        }

    def run(self):
        print("[grid] Poller started (waiting for iRacing...)")
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

    def stop(self):
        self._running = False
        if self.connected:
            self.ir.shutdown()


# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
poller = GridPoller()


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


GRID_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Qualifying Grid</title>
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

    .wrapper { max-width: 900px; margin: 0 auto; }

    .title-card {
        background: linear-gradient(135deg, #2a1f4a 0%, #14141c 100%);
        border: 1px solid #9146FF;
        border-radius: 12px;
        padding: 20px 28px;
        margin-bottom: 20px;
        display: flex; justify-content: space-between; align-items: center;
    }
    .title-card h1 {
        font-size: 26px; font-weight: 700; color: #9146FF;
        letter-spacing: 0.5px;
    }
    .title-card .subtitle { font-size: 13px; color: #d4c5ff; margin-top: 3px; }
    .title-card .track-info { text-align: right; }
    .track-name { font-size: 17px; font-weight: 600; color: #e8e8ea; }
    .track-config { font-size: 12px; color: #888; margin-top: 3px; }
    .source-pill {
        display: inline-block; margin-top: 6px;
        padding: 3px 10px; border-radius: 12px;
        font-size: 10px; text-transform: uppercase; letter-spacing: 1px;
        font-weight: 700;
    }
    .source-pill.qualifying { background: #1a4d2e; color: #6fe398; }
    .source-pill.race_grid  { background: #4a3f1a; color: #facc15; }
    .source-pill.none       { background: #333; color: #888; }

    .status {
        padding: 6px 14px; border-radius: 4px;
        font-size: 12px; font-weight: 600;
    }
    .status.connected    { background: #1a4d2e; color: #6fe398; }
    .status.disconnected { background: #4d1a1a; color: #ff8080; }

    .card {
        background: #14141c; border: 1px solid #222;
        border-radius: 12px; padding: 20px 20px 52px 20px;
        /* Fixed maximum height so autoscroll has a viewport to scroll within.
           Leaves room for the title card above. */
        max-height: calc(100vh - 140px);
        overflow: hidden;
        position: relative;
    }
    /* Viewport for the scrolling content - fills the card */
    .scroll-viewport {
        height: 100%;
        max-height: calc(100vh - 180px);
        overflow: hidden;
        position: relative;
    }
    /* The actual inner content that slides up/down */
    .scroll-inner {
        transition: transform 0.3s;
    }
    /* When there are more cars than fit, animate the inner block.
       Pattern: hold at top -> scroll down -> hold at bottom -> instant snap
       back to top -> repeat.  The "instant snap" is achieved by making the
       final keyframe (100%) the same as 0% with no tween between 99% and 100%. */
    .scroll-inner.autoscroll {
        animation: ticker var(--scroll-duration, 20s) linear infinite;
    }
    /* Pause the ticker on hover so you can read it on your monitor */
    .scroll-viewport:hover .scroll-inner.autoscroll { animation-play-state: paused; }

    @keyframes ticker {
        0%,  15%  { transform: translateY(0); }                          /* hold at top */
        85%, 99%  { transform: translateY(var(--scroll-distance, 0)); }  /* scroll down, hold at bottom */
        100%      { transform: translateY(0); }                          /* instant snap back */
    }

    /* Two-column stagger layout - tight */
    .grid-layout {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 6px 24px;
        position: relative;
    }
    .grid-layout::before {
        content: ""; position: absolute;
        top: 8px; bottom: 8px; left: 50%;
        width: 2px;
        background: repeating-linear-gradient(
            to bottom,
            #2a2a2a 0, #2a2a2a 6px, transparent 6px, transparent 12px
        );
        transform: translateX(-1px);
    }

    .grid-slot {
        display: flex; gap: 10px; align-items: center;
        padding: 8px 12px;
        background: #1a1a22;
        border: 1px solid #2a2a2a;
        border-radius: 8px;
        transition: border-color 0.15s, box-shadow 0.15s;
    }
    .grid-slot:hover { border-color: #9146FF; box-shadow: 0 2px 8px rgba(145,70,255,0.2); }
    .grid-slot.left  { justify-self: end; }
    .grid-slot.right { justify-self: start; flex-direction: row-reverse; text-align: right; }
    /* Real-grid stagger: the RIGHT column (P2, P4, P6...) sits about half a
       slot behind its left-column neighbor.  We use margin-top (not transform)
       so the stagger is part of the layout and doesn't drift on long grids. */
    .grid-slot.right { margin-top: 32px; }

    .pos-box {
        min-width: 48px; height: 48px;
        display: flex; flex-direction: column;
        align-items: center; justify-content: center;
        background: linear-gradient(135deg, #DC0028, #ff334f);
        color: #fff; font-weight: 800;
        border-radius: 6px; flex-shrink: 0;
    }
    .pos-box .pos-label { font-size: 9px; opacity: 0.8; text-transform: uppercase; }
    .pos-box .pos-num   { font-size: 22px; line-height: 1; }
    .grid-slot:nth-child(-n+2) .pos-box {
        background: linear-gradient(135deg, #9146FF, #b873ff);
    }

    .driver-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
    .driver-name {
        font-size: 16px; font-weight: 700; color: #e8e8ea;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .driver-meta {
        display: flex; gap: 6px; align-items: center;
        font-size: 11px; color: #888; flex-wrap: wrap;
    }
    .grid-slot.right .driver-meta { justify-content: flex-end; }
    .car-num {
        background: #0a0a0f; padding: 2px 6px; border-radius: 3px;
        font-family: monospace; font-weight: 700; color: #e8e8ea;
        font-size: 11px;
    }
    .irating { color: #60a5fa; font-weight: 600; }
    .qual-time {
        font-family: monospace; font-weight: 700;
        color: #facc15; font-size: 13px;
    }
    .no-time {
        font-family: monospace; font-weight: 700;
        font-size: 11px; color: #888;
        background: #2a2a2a; padding: 2px 6px; border-radius: 3px;
        letter-spacing: 0.5px;
    }
    .license { color: #888; font-size: 10px; }

    .empty-state {
        padding: 64px; text-align: center; color: #666; font-size: 14px;
    }
    .empty-state .big { font-size: 22px; color: #888; margin-bottom: 10px; }
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
            <h1>🏁 Qualifying Grid</h1>
            <div class="subtitle" id="subtitle">Waiting for iRacing…</div>
        </div>
        <div class="track-info">
            <div class="track-name" id="track-name">—</div>
            <div class="track-config" id="track-config"></div>
            <div class="source-pill none" id="source-pill">Disconnected</div>
            <div style="margin-top:8px">
                <span class="status disconnected" id="status">DISCONNECTED</span>
            </div>
        </div>
    </div>

    <div class="card">
        <div class="scroll-viewport" id="scroll-viewport">
            <div class="scroll-inner" id="scroll-inner">
                <div id="grid-host"></div>
            </div>
        </div>
    </div>
</div>

<script>
const STREAM_KEY = "iracing_grid_stream_mode_v1";

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

function fmtLap(sec) {
    if (!sec || sec <= 0) return "—";
    const m = Math.floor(sec / 60);
    const s = (sec - m * 60).toFixed(3).padStart(6, "0");
    return `${m}:${s}`;
}

function renderGrid(rows) {
    const host = document.getElementById("grid-host");
    if (!rows || !rows.length) {
        host.innerHTML = `
            <div class="empty-state">
                <div class="big">No grid data yet</div>
                Once qualifying has produced results, or the race grid is set,
                drivers will line up here.
            </div>`;
        updateAutoScroll();
        return;
    }
    const sig = rows.map(r => r.car_idx + ":" + r.position + ":" + (r.best_time || 0).toFixed(3)).join("|");
    if (host.dataset.sig === sig) return;
    host.dataset.sig = sig;

    const layout = document.createElement("div");
    layout.className = "grid-layout";

    rows.forEach((r, i) => {
        const slot = document.createElement("div");
        // i=0 (P1), i=2 (P3), i=4 (P5)... go in the LEFT column, no stagger
        // i=1 (P2), i=3 (P4), i=5 (P6)... go in the RIGHT column, staggered down
        slot.className = "grid-slot " + (i % 2 === 0 ? "left" : "right");
        slot.innerHTML = `
            <div class="pos-box">
                <div class="pos-label">Pos</div>
                <div class="pos-num">${r.position}</div>
            </div>
            <div class="driver-info">
                <div class="driver-name">${r.name || "—"}</div>
                <div class="driver-meta">
                    <span class="car-num">#${r.car_number}</span>
                    ${r.irating ? `<span class="irating">${r.irating} iR</span>` : ""}
                    ${r.license ? `<span class="license">${r.license}</span>` : ""}
                    ${r.best_time > 0
                        ? `<span class="qual-time">${fmtLap(r.best_time)}</span>`
                        : `<span class="no-time">NO TIME</span>`}
                </div>
            </div>
        `;
        layout.appendChild(slot);
    });
    host.innerHTML = "";
    host.appendChild(layout);
    // After layout settles, decide whether we need to auto-scroll
    requestAnimationFrame(updateAutoScroll);
}

// Auto-scroll controller.  When the inner content is taller than the viewport
// (typical for 20+ car fields in OBS), we set CSS vars to animate a vertical
// translate from 0 to -(overflow height) and loop.
function updateAutoScroll() {
    const viewport = document.getElementById("scroll-viewport");
    const inner    = document.getElementById("scroll-inner");
    if (!viewport || !inner) return;

    // Reset any previous animation so we can re-measure
    inner.classList.remove("autoscroll");
    inner.style.removeProperty("--scroll-distance");
    inner.style.removeProperty("--scroll-duration");

    // Measure
    const visibleH = viewport.clientHeight;
    const contentH = inner.scrollHeight;
    const overflow = contentH - visibleH;

    if (overflow <= 4) return;  // content fits (or only a couple px off)

    // Pick a duration roughly proportional to content height.
    // ~100ms per pixel of overflow, clamped to [12s, 40s].  Keeps short fields
    // quick to cycle and big fields from racing past too fast.
    let duration = Math.max(12, Math.min(40, overflow * 0.08));
    inner.style.setProperty("--scroll-distance", (-overflow) + "px");
    inner.style.setProperty("--scroll-duration", duration.toFixed(1) + "s");
    inner.classList.add("autoscroll");
}

// Recompute on window resize (OBS sources can be resized live)
window.addEventListener("resize", () => {
    // Small delay so layout settles first
    setTimeout(updateAutoScroll, 100);
});

const SOURCE_LABELS = {
    "qualifying": { label: "FROM QUALIFYING", cls: "qualifying" },
    "race_grid":  { label: "RACE STARTING GRID", cls: "race_grid" },
};

async function tick() {
    try {
        const r = await fetch("/grid");
        const d = await r.json();

        const statusEl = document.getElementById("status");
        if (!d.connected) {
            statusEl.className = "status disconnected";
            statusEl.textContent = "WAITING FOR IRACING";
            document.getElementById("subtitle").textContent = "Waiting for iRacing…";
            document.getElementById("track-name").textContent = "—";
            document.getElementById("track-config").textContent = "";
            document.getElementById("source-pill").className = "source-pill none";
            document.getElementById("source-pill").textContent = "Disconnected";
            renderGrid([]);
            return;
        }

        statusEl.className = "status connected";
        statusEl.textContent = "CONNECTED";

        document.getElementById("track-name").textContent = d.track || "—";
        document.getElementById("track-config").textContent = d.track_config || "";
        document.getElementById("subtitle").textContent =
            d.num_cars > 0 ? `${d.num_cars} cars on the grid` : "No cars yet";

        const src = SOURCE_LABELS[d.source];
        const pill = document.getElementById("source-pill");
        if (src) { pill.className = "source-pill " + src.cls; pill.textContent = src.label; }
        else     { pill.className = "source-pill none"; pill.textContent = "No grid set yet"; }

        renderGrid(d.grid);
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
    return render_template_string(GRID_HTML)


@app.route("/grid")
def grid():
    return jsonify(poller.get())


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()

    print("\n" + "=" * 60)
    print("  iRacing Qualifying Grid Display v2")
    print("  Open in browser:  http://localhost:5001")
    print("  (Runs in parallel to iracing_dashboard.py on port 5000)")
    print("  Press H in the browser for Stream mode (transparent bg)")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)
    finally:
        poller.stop()
