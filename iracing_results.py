"""
iRacing Final Race Results Display
-----------------------------------
Standalone companion script that shows final race results — positions,
gaps, fastest laps, incident points, and position changes vs. the grid.

Requirements:  pip install pyirsdk flask
Run:           python iracing_results.py
Open:          http://localhost:5002

Runs in parallel with:
  - iracing_dashboard.py  (port 5000)
  - iracing_grid.py       (port 5001)

Use cases:
  - Post-race "classification" graphic for your Twitch stream
  - Open as an OBS browser source for the end-of-broadcast wrap-up
  - Press H for stream mode (transparent background)

Data source:
  Once a race session has ended, iRacing finalizes ResultsPositions with
  complete finishing data.  During the race it shows the live running
  order — so it's useful both during and after the race.
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
# Results poller
# -----------------------------------------------------------------------------
class ResultsPoller:
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
            print("[results] Disconnected from iRacing")
        elif not self.connected and self.ir.startup() and self.ir.is_initialized and self.ir.is_connected:
            self.connected = True
            print("[results] Connected to iRacing")
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
            }
        return out

    def _find_race_session(self, sessions: list) -> dict:
        """Return the most recent race session (for heat-race weekends)."""
        race = None
        for s in sessions:
            stype = (s.get("SessionType") or "").lower()
            if "race" in stype:
                race = s  # last wins
        return race

    def _session_is_finalized(self, session: dict) -> bool:
        """Check if iRacing has marked this session as ended."""
        results = session.get("ResultsPositions") or []
        if not results:
            return False
        # iRacing sets ResultsOfficial=1 once the session is officially closed.
        # Before that we may still have provisional results during the race.
        return bool(session.get("ResultsOfficial", 0))

    def _read_snapshot(self) -> dict:
        ir = self.ir
        info = ir["SessionInfo"] or {}
        sessions = info.get("Sessions", []) or []
        weekend = ir["WeekendInfo"] or {}

        drivers = self._driver_map()
        race = self._find_race_session(sessions)

        rows = []
        source = None
        official = False
        session_num = ir["SessionNum"]
        current_session = None
        for s in sessions:
            if s.get("SessionNum") == session_num:
                current_session = s
                break

        # Prefer the active session if it's a race; otherwise fall back to
        # the latest race session found in the weekend.
        chosen = None
        if current_session and "race" in (current_session.get("SessionType") or "").lower():
            chosen = current_session
        elif race:
            chosen = race

        if chosen and chosen.get("ResultsPositions"):
            results = chosen["ResultsPositions"]
            official = bool(chosen.get("ResultsOfficial", 0))
            source = "official" if official else "live"

            for r in results:
                cidx = r.get("CarIdx")
                drv = drivers.get(cidx)
                if not drv:
                    continue

                finish_pos = r.get("Position", 0) or 0
                start_pos  = r.get("ClassPosition", 0) or 0  # may be class; we'll override
                # iRacing exposes StartingPosition directly in some cases
                if "StartingPosition" in r:
                    start_pos = r.get("StartingPosition", 0) or 0
                time_gap   = r.get("Time", 0.0)               # seconds; can be negative
                laps_down  = r.get("LapsLed", 0)              # not useful; real field is Lap
                laps_compl = r.get("LapsComplete", 0) or 0
                fastest    = r.get("FastestTime", 0.0) or 0.0
                fastest_lap_num = r.get("FastestLap", 0) or 0
                incidents  = r.get("Incidents", 0) or 0
                reason_out = r.get("ReasonOutStr", "") or ""
                reason_id  = r.get("ReasonOutId", 0) or 0

                # iRacing returns -LapsDown for lapped cars (e.g. Time=-2 means
                # 2 laps down).  For cars on the lead lap, Time is the gap in
                # seconds to the leader, or 0 for the leader themselves.
                laps_behind = 0
                if time_gap is not None and time_gap < 0:
                    laps_behind = int(round(-time_gap))
                    time_gap = None  # gap is laps, not seconds

                rows.append({
                    **drv,
                    "position":      finish_pos,
                    "start_pos":     start_pos,
                    "position_change": (start_pos - finish_pos) if (start_pos and finish_pos) else 0,
                    "time_gap":      time_gap,
                    "laps_behind":   laps_behind,
                    "laps_complete": laps_compl,
                    "fastest_lap":   fastest,
                    "fastest_lap_num": fastest_lap_num,
                    "incidents":     incidents,
                    "reason_out":    reason_out,
                    "reason_id":     reason_id,
                    "finished":      reason_id == 0,   # 0 = still running/finished
                })

        rows.sort(key=lambda d: (d["position"] == 0, d["position"]))

        # Find fastest lap of the race for highlighting
        fastest_overall = 0.0
        fastest_overall_idx = None
        for r in rows:
            if r["fastest_lap"] > 0 and (fastest_overall == 0.0 or r["fastest_lap"] < fastest_overall):
                fastest_overall = r["fastest_lap"]
                fastest_overall_idx = r["car_idx"]
        for r in rows:
            r["is_fastest_overall"] = (r["car_idx"] == fastest_overall_idx)

        return {
            "connected":    True,
            "official":     official,
            "source":       source,
            "session_name": (chosen or {}).get("SessionName", "Race") if chosen else None,
            "track":        weekend.get("TrackDisplayName", ""),
            "track_config": weekend.get("TrackConfigName", ""),
            "num_cars":     len(rows),
            "results":      rows,
        }

    def run(self):
        print("[results] Poller started (waiting for iRacing...)")
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
poller = ResultsPoller()


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


RESULTS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Race Results</title>
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

    .wrapper { max-width: 1100px; margin: 0 auto; }

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

    .status-pill {
        display: inline-block; margin-top: 6px;
        padding: 3px 10px; border-radius: 12px;
        font-size: 10px; text-transform: uppercase; letter-spacing: 1px;
        font-weight: 700;
    }
    .status-pill.official { background: #1a4d2e; color: #6fe398; }
    .status-pill.live     { background: #4a3f1a; color: #facc15; }
    .status-pill.none     { background: #333; color: #888; }

    .status {
        padding: 6px 14px; border-radius: 4px;
        font-size: 12px; font-weight: 600;
    }
    .status.connected    { background: #1a4d2e; color: #6fe398; }
    .status.disconnected { background: #4d1a1a; color: #ff8080; }

    .card {
        background: #14141c; border: 1px solid #222;
        border-radius: 12px; padding: 0; overflow: hidden;
        /* Fixed maximum height so autoscroll has a viewport to scroll within.
           Leaves room for the title card above. */
        max-height: calc(100vh - 140px);
        position: relative;
    }
    .scroll-viewport {
        height: 100%;
        max-height: calc(100vh - 140px);
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

    /* Keep the table header pinned visually at the top of the viewport so it
       doesn't scroll away with the rows.  We do this by giving the header
       its own wrapper outside the scrolling inner block. */
    .table-header-wrap {
        background: #1a1a22;
        border-bottom: 1px solid #222;
    }
    .table-header-wrap table { table-layout: fixed; }
    .table-body-wrap table   { table-layout: fixed; }

    /* Results table */
    table { width: 100%; border-collapse: collapse; }
    th, td {
        padding: 10px 12px; text-align: left;
        border-bottom: 1px solid #222;
    }
    th {
        background: #1a1a22; color: #888;
        font-size: 10px; text-transform: uppercase; letter-spacing: 1px;
        font-weight: 700;
    }
    tr:last-child td { border-bottom: none; }
    tr { transition: background 0.15s; }
    tr.row:hover { background: #1a1a22; }

    /* Row highlighting */
    tr.podium-1 td:first-child { box-shadow: inset 4px 0 0 #FFD700; }
    tr.podium-2 td:first-child { box-shadow: inset 4px 0 0 #C0C0C0; }
    tr.podium-3 td:first-child { box-shadow: inset 4px 0 0 #CD7F32; }
    tr.dnf td { opacity: 0.55; }

    /* Position cell */
    td.pos {
        font-size: 22px; font-weight: 800; text-align: center;
        font-family: monospace;
        min-width: 56px;
    }
    tr.podium-1 td.pos { color: #FFD700; }
    tr.podium-2 td.pos { color: #C0C0C0; }
    tr.podium-3 td.pos { color: #CD7F32; }

    /* Position change arrow */
    .change {
        font-family: monospace; font-weight: 700;
        font-size: 12px;
        padding: 2px 6px; border-radius: 3px;
        display: inline-block; min-width: 36px; text-align: center;
    }
    .change.gained { background: #1a4d2e; color: #6fe398; }
    .change.lost   { background: #4d1a1a; color: #ff8080; }
    .change.same   { background: #2a2a32; color: #888; }

    /* Car number */
    .car-num {
        background: #0a0a0f; padding: 3px 8px; border-radius: 3px;
        font-family: monospace; font-weight: 700; color: #e8e8ea;
        font-size: 12px;
    }

    /* Driver name */
    td.driver { font-weight: 600; color: #e8e8ea; min-width: 180px; }
    td.driver .iR {
        font-size: 11px; color: #60a5fa; font-weight: 600;
        margin-left: 6px;
    }
    td.driver .lic {
        font-size: 10px; color: #666;
        margin-left: 4px;
    }

    /* Gap column */
    td.gap {
        font-family: monospace; color: #bbb; font-size: 13px;
        white-space: nowrap;
    }
    td.gap.leader { color: #FFD700; font-weight: 700; }
    td.gap.laps-down { color: #ff8080; }

    /* Fastest lap */
    td.fastest {
        font-family: monospace; font-weight: 600;
        color: #bbb; font-size: 13px;
        white-space: nowrap;
    }
    td.fastest.overall-best {
        color: #d946ef; font-weight: 800;
    }
    td.fastest .fl-tag {
        background: #d946ef; color: #fff;
        padding: 1px 5px; border-radius: 2px;
        font-size: 9px; font-weight: 700; margin-right: 4px;
        letter-spacing: 0.5px;
    }

    /* Incidents column */
    td.inc {
        font-family: monospace; font-weight: 700;
        font-size: 13px; text-align: right;
    }
    td.inc.low  { color: #6fe398; }
    td.inc.med  { color: #facc15; }
    td.inc.high { color: #ff8080; }

    /* Laps column */
    td.laps {
        font-family: monospace; color: #bbb; text-align: center;
        font-size: 13px;
    }

    /* Status column */
    td.status-cell {
        font-size: 11px;
        font-weight: 700; text-transform: uppercase;
    }
    .finished { color: #6fe398; }
    .dnf-tag  { color: #ff8080; }

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
            <h1>🏆 Final Classification</h1>
            <div class="subtitle" id="subtitle">Waiting for race results…</div>
        </div>
        <div class="track-info">
            <div class="track-name" id="track-name">—</div>
            <div class="track-config" id="track-config"></div>
            <div class="status-pill none" id="status-pill">No data</div>
            <div style="margin-top:8px">
                <span class="status disconnected" id="status">DISCONNECTED</span>
            </div>
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
const STREAM_KEY = "iracing_results_stream_mode_v1";

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
function fmtGap(r, isLeader) {
    if (isLeader) return "LEADER";
    if (r.laps_behind && r.laps_behind > 0) {
        return `+${r.laps_behind} ${r.laps_behind === 1 ? "lap" : "laps"}`;
    }
    if (r.time_gap !== null && r.time_gap !== undefined && r.time_gap > 0) {
        if (r.time_gap < 60) return `+${r.time_gap.toFixed(3)}`;
        const m = Math.floor(r.time_gap / 60);
        const s = (r.time_gap - m * 60).toFixed(1).padStart(4, "0");
        return `+${m}:${s}`;
    }
    return "—";
}
function fmtChange(delta) {
    if (delta > 0) return {text: `▲${delta}`, cls: "gained"};
    if (delta < 0) return {text: `▼${-delta}`, cls: "lost"};
    return {text: "—", cls: "same"};
}
function incidentClass(n) {
    if (n <= 4)  return "low";
    if (n <= 12) return "med";
    return "high";
}

function renderResults(rows) {
    const host = document.getElementById("results-host");
    if (!rows || !rows.length) {
        host.innerHTML = `
            <div class="empty-state">
                <div class="big">No results yet</div>
                Final classification will appear here once the race has
                finished (or live running order during the race).
            </div>`;
        updateAutoScroll();
        return;
    }

    const sig = rows.map(r => r.car_idx + ":" + r.position + ":" + (r.fastest_lap||0).toFixed(3)
        + ":" + r.incidents + ":" + r.reason_id).join("|");
    if (host.dataset.sig === sig) return;
    host.dataset.sig = sig;

    let html = `
        <table>
            <thead>
                <tr>
                    <th>Pos</th>
                    <th>Chg</th>
                    <th>#</th>
                    <th>Driver</th>
                    <th>Gap</th>
                    <th>Laps</th>
                    <th>Fastest Lap</th>
                    <th style="text-align:right">Inc</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
    `;

    rows.forEach((r, i) => {
        const podium = r.position >= 1 && r.position <= 3 ? `podium-${r.position}` : "";
        const dnfCls = (r.reason_id !== 0) ? "dnf" : "";
        const change = fmtChange(r.position_change);
        const isLeader = i === 0;
        const gapClass = isLeader ? "gap leader"
                      : (r.laps_behind > 0 ? "gap laps-down" : "gap");
        const statusText = r.reason_id === 0 ? "RUNNING" : (r.reason_out.toUpperCase() || "DNF");
        const statusClass = r.reason_id === 0 ? "finished" : "dnf-tag";
        const incCls = incidentClass(r.incidents);
        const fastestCls = r.is_fastest_overall && r.fastest_lap > 0
            ? "fastest overall-best" : "fastest";
        const flTag = r.is_fastest_overall && r.fastest_lap > 0
            ? '<span class="fl-tag">FL</span>' : "";

        html += `
            <tr class="row ${podium} ${dnfCls}">
                <td class="pos">${r.position || "—"}</td>
                <td><span class="change ${change.cls}">${change.text}</span></td>
                <td><span class="car-num">#${r.car_number}</span></td>
                <td class="driver">
                    ${r.name || "—"}
                    ${r.irating ? `<span class="iR">${r.irating} iR</span>` : ""}
                    ${r.license ? `<span class="lic">${r.license}</span>` : ""}
                </td>
                <td class="${gapClass}">${fmtGap(r, isLeader)}</td>
                <td class="laps">${r.laps_complete}</td>
                <td class="${fastestCls}">${flTag}${fmtLap(r.fastest_lap)}</td>
                <td class="inc ${incCls}">${r.incidents}x</td>
                <td class="status-cell ${statusClass}">${statusText}</td>
            </tr>
        `;
    });

    html += `</tbody></table>`;
    host.innerHTML = html;
    requestAnimationFrame(updateAutoScroll);
}

// Auto-scroll controller: animates the inner block up/down when content
// overflows the viewport.  Speed scales with how much overflow there is.
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

        const statusEl = document.getElementById("status");
        if (!d.connected) {
            statusEl.className = "status disconnected";
            statusEl.textContent = "WAITING FOR IRACING";
            document.getElementById("subtitle").textContent = "Waiting for iRacing…";
            document.getElementById("track-name").textContent = "—";
            document.getElementById("track-config").textContent = "";
            document.getElementById("status-pill").className = "status-pill none";
            document.getElementById("status-pill").textContent = "Disconnected";
            renderResults([]);
            return;
        }

        statusEl.className = "status connected";
        statusEl.textContent = "CONNECTED";

        document.getElementById("track-name").textContent = d.track || "—";
        document.getElementById("track-config").textContent = d.track_config || "";

        const subtitle = d.num_cars > 0
            ? `${d.num_cars} classified — ${d.session_name || "Race"}`
            : "Waiting for results…";
        document.getElementById("subtitle").textContent = subtitle;

        const pill = document.getElementById("status-pill");
        if (d.source === "official") {
            pill.className = "status-pill official";
            pill.textContent = "OFFICIAL RESULTS";
        } else if (d.source === "live") {
            pill.className = "status-pill live";
            pill.textContent = "LIVE — PROVISIONAL";
        } else {
            pill.className = "status-pill none";
            pill.textContent = "No results yet";
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
    return render_template_string(RESULTS_HTML)


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
    print("  iRacing Final Race Results Display")
    print("  Open in browser:  http://localhost:5002")
    print("  (Runs in parallel to iracing_dashboard.py [5000] and")
    print("   iracing_grid.py [5001])")
    print("  Press H in the browser for Stream mode (transparent bg)")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=5002, debug=False, use_reloader=False)
    finally:
        poller.stop()
