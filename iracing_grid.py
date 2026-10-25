"""
iRacing Qualifying Grid Display (v2)
------------------------------------
Standalone companion to iracing_dashboard.py.

Default page (2026-10-25): the starting grid in the WCT broadcast look,
pages of 8 — automatic (?secs=10) or by hand: Stream Deck
http://localhost:5001/nav/next | prev | first | auto, keys → ← Home A.
Options: ?auto=0 ?per= ?zoom= ?title= ?round=LAUF ?race=RENNEN ?logo=<url>|0
?veil=0 ?debug=1 (H) ?demo=1. Old purple card: /?style=classic.
/cars = the same grid with the car liveries (iRacing's local render server),
brand logo, number, gap to pole (DNLS look; ?view= render angle, ?head= /
?accent= hex colours, ?logo=<url>|0 league logo, same paging).
/row = ONE grid row (two cars) on asphalt with painted grid boxes; next =
the next row (by hand; ?auto=1 + ?secs=8 cycles). /row/top = the cars DRAWN
from above in each livery's colours (iRacing renders only the side view).

Requirements:  pip install pyirsdk flask
Run:           python iracing_grid.py
Open:          http://localhost:5001
"""

import threading
from flask import (Flask, Response, abort, jsonify, render_template_string,
                   request, send_file)

from car_brands import detect_brand, resolve_logo
from car_render import CarRenderer, livery_colors
from page_keys import KEY_SCROLL_JS
from class_colors import class_colors
from wct_board import EventInfo, PageNav

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()


# -----------------------------------------------------------------------------
# Grid data poller
# -----------------------------------------------------------------------------
class GridPoller(SDKPoller):
    tag = "grid"
    poll_interval = 2.0

    raw = {}     # CarIdx -> DriverInfo dict, for the /car livery renders

    def _driver_map(self) -> dict:
        info = self.ir["DriverInfo"] or {}
        out = {}
        raw = {}
        ccol = class_colors(info.get("Drivers") or [])
        for d in info.get("Drivers", []) or []:
            cidx = d.get("CarIdx")
            if cidx is None:
                continue
            if d.get("CarIsPaceCar") == 1:
                continue
            if d.get("IsSpectator") == 1:
                continue
            raw[cidx] = d
            out[cidx] = {
                "car_idx":    cidx,
                "name":       d.get("UserName", "") or "",
                "car_number": d.get("CarNumber", "") or "",
                "car":        d.get("CarScreenNameShort") or d.get("CarScreenName", ""),
                "irating":    d.get("IRating", 0) or 0,
                "license":    d.get("LicString", "") or "",
                "team_name":  d.get("TeamName", "") or "",
                "class_id":   int(d.get("CarClassID") or 0),
                "class_name": d.get("CarClassShortName") or "",
                "class_color": ccol.get(int(d.get("CarClassID") or 0), ""),
                "is_spectator": bool(d.get("IsSpectator", 0)),
                "brand":      detect_brand(d.get("CarPath"), d.get("CarScreenName")) or "",
            }
        self.raw = raw
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

    @staticmethod
    def _race_sessions(sessions: list) -> list:
        """All race sessions, ascending by SessionNum. Heats count too."""
        out = []
        for s in sessions:
            if "race" not in (s.get("SessionType") or "").lower():
                continue
            try:
                out.append((int(s.get("SessionNum")), s))
            except (TypeError, ValueError):
                continue
        out.sort(key=lambda t: t[0])
        return out

    def _find_race_session(self, sessions: list) -> dict:
        """The race whose grid we should be showing.

        Priority: the CURRENT session when it is a race (that is the one
        on track), else the NEXT race after the current session number
        (we are sitting in practice or qualifying ahead of it), else the
        last race of the weekend.

        The old version simply took the last race session in the list,
        which in a two-race round meant the overlay was describing race 2
        while race 1 was running.
        """
        races = self._race_sessions(sessions)
        if not races:
            return None
        try:
            cur = int(self.ir["SessionNum"])
        except (TypeError, ValueError):
            return races[-1][1]
        for num, s in races:
            if num == cur:
                return s
        for num, s in races:
            if num > cur:
                return s
        return races[-1][1]

    def _is_first_race(self, sessions: list, race: dict) -> bool:
        """True when `race` is the weekend's first race.

        Qualifying only ever describes the grid of the FIRST race. Race 2
        is gridded from race 1's classification (reverse grid, reverse
        top-N, or straight finishing order), so showing the qualifying
        order there is showing the wrong grid.
        """
        races = self._race_sessions(sessions)
        if not races or not race:
            return True
        try:
            return int(race.get("SessionNum")) == races[0][0]
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _rows_from_race_grid(race: dict, drivers: dict) -> list:
        """Grid rows from the race session's own StartingPosition block.

        StartingPosition is 0-based and -1 when unknown. We deliberately
        do NOT use Position here: once the race is green, Position is the
        live running order, and rendering that as "the grid" is wrong.
        """
        rows = []
        for r in (race or {}).get("ResultsPositions") or []:
            drv = drivers.get(r.get("CarIdx"))
            if not drv:
                continue
            try:
                sp = int(r.get("StartingPosition"))
            except (TypeError, ValueError):
                continue
            if sp < 0:
                continue
            rows.append({
                **drv,
                "_start":         sp,
                "class_position": 0,
                "best_time":      r.get("FastestTime", 0.0) or 0.0,
                "lap_count":      0,
                "interval":       0.0,
            })
        if len(rows) < 2:
            return []
        rows.sort(key=lambda d: d["_start"])
        for i, d in enumerate(rows, start=1):
            d["position"] = i
            d.pop("_start", None)
        return rows

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

        # Source priority mirrors GridBaseline in iracing_sdk_base.py:
        #   1. the target race's OWN StartingPosition block — session
        #      specific, so it is right for race 2 of a two-race round;
        #   2. qualifying, but ONLY when the target race is the weekend's
        #      first race. SessionInfo keeps the qualifying block for the
        #      whole weekend, and using it for race 2 showed race 1's grid.
        # Nothing else: an empty board beats a confidently wrong one.
        race_rows = self._rows_from_race_grid(race, drivers)
        if race_rows:
            source = "race_grid"
            rows = race_rows
        elif (qual and qual.get("ResultsPositions")
                and self._is_first_race(sessions, race)):
            source = "qualifying"
            for r in qual["ResultsPositions"]:
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

        rows.sort(key=lambda d: (d["position"] == 0, d["position"]))
        # single-class hosted sessions send an empty class name: one car
        # model -> its name, else "GRID"
        if rows and not any(r["class_name"] for r in rows):
            cars = {r["car"] for r in rows}
            label = next(iter(cars)) if len(cars) == 1 else "GRID"
            for r in rows:
                r["class_name"] = label

        races = [n for n, _ in self._race_sessions(sessions)]
        try:
            race_no = races.index(int(race.get("SessionNum"))) + 1 if race else 0
        except (TypeError, ValueError):
            race_no = 0

        return {
            "connected":    True,
            "source":       source,
            "track":        weekend.get("TrackDisplayName", ""),
            "track_short":  weekend.get("TrackDisplayShortName", "") or weekend.get("TrackDisplayName", ""),
            "track_config": weekend.get("TrackConfigName", ""),
            "team_event":   str(weekend.get("TeamRacing", 0)) == "1",
            "race_no":      race_no,
            "races":        len(races),
            "num_cars":     len(rows),
            "grid":         rows,
        }



NAV_CMDS = PageNav.CMDS


# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
poller = GridPoller()
event = EventInfo()
nav = PageNav()
renderer = CarRenderer()


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


GRID_HTML_CLASSIC = """
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
    /* When there are more cars than fit, the inner block is animated from JS
       (Web Animations, see updateAutoScroll): hold at the TOP for ?hold=
       seconds (default 10), scroll down, hold at the bottom, snap back,
       repeat — and start again at the top every time OBS shows the source
       (2026-10-21: the highlight video caught it half scrolled). */

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

<!-- Button hidden 2026-10-10 (showed up on stream); H still toggles. -->
<div class="stream-toggle" id="stream-toggle" style="display:none" onclick="toggleStreamMode()">
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
// Transparent (stream) mode is the DEFAULT for OBS browser sources.
// Press H to toggle the dark debug background back on; the choice persists.
try {
    const v = localStorage.getItem(STREAM_KEY);
    applyStreamMode(v === null ? true : v !== "0");
} catch (e) { applyStreamMode(true); }
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
// (typical for 20+ car fields in OBS): hold the first places at the top for
// HOLD_S, scroll to the bottom, hold there, snap back, repeat.
const QS = new URLSearchParams(location.search);
const HOLD_S = Math.max(0, parseFloat(QS.get("hold") || "10"));   // first places stand still this long
const HOLD_BOTTOM_S = 4;
let scrollAnim = null;
function oldAutoScroll() {        // ?autoscroll=1 only (page_keys.py)
    const viewport = document.getElementById("scroll-viewport");
    const inner    = document.getElementById("scroll-inner");
    if (!viewport || !inner) return;

    // Reset any previous animation so we can re-measure
    if (scrollAnim) { scrollAnim.cancel(); scrollAnim = null; }
    inner.style.transform = "";

    // Measure
    const visibleH = viewport.clientHeight;
    const contentH = inner.scrollHeight;
    const overflow = contentH - visibleH;

    if (overflow <= 4) return;  // content fits (or only a couple px off)

    // Scroll time roughly proportional to the overflow, clamped to [6s, 30s].
    const scrollS = Math.max(6, Math.min(30, overflow * 0.06));
    const total = HOLD_S + scrollS + HOLD_BOTTOM_S;
    const down = "translateY(" + (-overflow) + "px)";
    scrollAnim = inner.animate([
        { transform: "translateY(0)", offset: 0 },
        { transform: "translateY(0)", offset: HOLD_S / total },
        { transform: down, offset: (HOLD_S + scrollS) / total },
        { transform: down, offset: 0.999 },
        { transform: "translateY(0)", offset: 1 },
    ], { duration: total * 1000, iterations: Infinity, easing: "linear" });
}
// Back to the top (first places, then the hold) every time the source is shown
function restartScroll() {
    if (!KS_AUTO) return;                        // page keys: page_keys.py goes to the top itself
    if (scrollAnim) { scrollAnim.currentTime = 0; scrollAnim.play(); }
    else updateAutoScroll();
}
/*__KEY_SCROLL__*/
window.addEventListener("obsSourceVisibleChanged", e => { if (!e.detail || e.detail.visible) restartScroll(); });
window.addEventListener("obsSourceActiveChanged", e => { if (!e.detail || e.detail.active) restartScroll(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) restartScroll(); });
// Pause on hover so you can read it on your monitor
document.addEventListener("DOMContentLoaded", () => {
    const vp = document.getElementById("scroll-viewport");
    if (!vp) return;
    vp.addEventListener("mouseenter", () => scrollAnim && scrollAnim.pause());
    vp.addEventListener("mouseleave", () => scrollAnim && scrollAnim.play());
});

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
GRID_HTML_CLASSIC = GRID_HTML_CLASSIC.replace("/*__KEY_SCROLL__*/", KEY_SCROLL_JS)


# Starting grid in the broadcast look of the WCT graphics (2026-10-25,
# Thomas's screenshot "LAUF 5 - CAS GT3 WCT - INDIANAPOLIS RC"): blue
# STARTING GRID tab + title bar, orange rule, class tab + page boxes on the
# left, two staggered columns of 4 (pos box + name over an orange line, car
# number + quali time), league logo at the bottom. Pages of ?per=8 instead
# of the old scroll, turned BY HAND only (2026-10-25, Thomas: no automatic
# paging anywhere): Stream Deck /nav/next | prev | first or the keys → ← Home;
# ?auto=1 brings back the timer (every ?secs=10 s) for one source.
GRID_HTML = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"><title>Starting Grid</title>
<style>
:root { --blue: #3d8be8; --blue-d: #2a6fc4; --orange: #f2a531; --slot: rgba(44,44,50,.92);
        --slot2: rgba(58,58,64,.88); --box: rgba(24,24,28,.96); --text: #f4f4f6; --muted: #d0d0d8; }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: rgba(0,0,0,0); overflow: hidden; }
body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; color: var(--text); }
body.debug { background: #3a4a3a; }
.card { position: relative; width: 900px; height: 620px; padding: 16px 26px 0; transform-origin: 0 0; }
.card.veil { background: rgba(8,8,12,.55); }
.top { display: flex; gap: 12px; height: 34px; }
.tab { width: 200px; background: var(--blue); border-radius: 3px; display: flex; align-items: center;
       justify-content: center; font-size: 18px; font-weight: 800; font-style: italic; letter-spacing: .5px;
       box-shadow: 0 1px 0 rgba(255,255,255,.25) inset; }
.title { flex: 1; min-width: 0; background: rgba(36,36,42,.88); display: flex; align-items: center; justify-content: center;
         font-size: 18px; font-weight: 800; letter-spacing: .4px; white-space: nowrap; overflow: hidden;
         text-overflow: ellipsis; padding: 0 14px; }
.rule { height: 3px; background: var(--orange); margin: 10px 0 18px; }
.main { display: flex; gap: 0; height: 410px; }
.side { width: 76px; display: flex; flex-direction: column; gap: 6px; }
.ctab { height: 22px; border-radius: 3px; background: var(--blue-d); opacity: .55; font-size: 11px; font-weight: 800;
        display: flex; align-items: center; justify-content: center; white-space: nowrap; overflow: hidden;
        padding: 0 4px; border-left: 4px solid transparent; }
.ctab.on { background: var(--blue); opacity: 1; }
.pbox { height: 20px; border-radius: 3px; background: var(--blue); opacity: .45; font-size: 10px; font-weight: 700;
        display: flex; align-items: center; justify-content: center; color: rgba(255,255,255,.85); transition: opacity .3s; }
.pbox.on { opacity: 1; box-shadow: 0 0 0 2px rgba(255,255,255,.85) inset; color: #fff; }
.cols { flex: 1; display: flex; gap: 30px; padding-left: 104px; }
.col { width: 222px; display: flex; flex-direction: column; gap: 20px; }
.col.right { padding-top: 30px; }
.slot { height: 70px; }
.slot.empty { visibility: hidden; }
.r1, .r2 { display: flex; height: 33px; }
.r1 { border-bottom: 3px solid var(--orange); margin-bottom: 1px; }
.pos, .num { width: 34px; flex: none; display: flex; align-items: center; justify-content: center;
             font-size: 15px; font-weight: 800; }
.pos { background: var(--box); }
.num { background: var(--blue); border-radius: 0 0 0 2px; }
.name { flex: 1; min-width: 0; background: var(--slot); display: flex; align-items: center; justify-content: center;
        font-size: 14px; font-weight: 800; letter-spacing: .3px; text-transform: uppercase; padding: 0 8px;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.name span { overflow: hidden; text-overflow: ellipsis; }
.time { flex: 1; background: var(--slot2); display: flex; align-items: center; justify-content: center;
        font-size: 14px; font-style: italic; font-weight: 600; color: var(--muted); font-variant-numeric: tabular-nums; }
.time.none { font-style: normal; font-size: 11px; letter-spacing: 1px; color: #9a9aa4; }
.slot.in { animation: slotIn .45s cubic-bezier(.2,.8,.2,1) both; }
@keyframes slotIn { from { opacity: 0; transform: translateX(-18px); } to { opacity: 1; transform: none; } }
.foot { position: absolute; left: 0; right: 0; bottom: 0; height: 120px; border-top: 2px solid rgba(255,255,255,.08);
        display: flex; align-items: center; justify-content: center; }
.foot .lb { height: 96px; padding: 10px 40px; background: rgba(30,30,34,.85); display: flex; align-items: center; }
.foot img { max-height: 76px; max-width: 420px; }
.empty-msg { flex: 1; display: flex; align-items: center; justify-content: center; font-size: 20px; font-weight: 700;
             color: #c8c8d0; background: rgba(36,36,42,.7); }
.hint { position: absolute; right: 8px; bottom: 4px; font-size: 11px; color: #ffd166; display: none; }
body.debug .hint { display: block; }
</style></head>
<body>
<div class="card" id="card">
  <div class="top"><div class="tab">STARTING GRID</div><div class="title" id="title">—</div></div>
  <div class="rule"></div>
  <div class="main" id="main"></div>
  <div class="foot" id="foot"><div class="lb"><img id="logo" alt=""></div></div>
  <div class="hint" id="hint"></div>
</div>
<script>
const QS = new URLSearchParams(location.search);
const PER = Math.max(2, parseInt(QS.get("per") || "8", 10));
const SECS = Math.max(2, parseFloat(QS.get("secs") || "10"));
const AUTO_PARAM = QS.get("auto") === "1";          // pages turn only by the Stream Deck keys / arrows; ?auto=1 = old timer
const ROUND_WORD = QS.get("round") || "LAUF";
const RACE_WORD = QS.get("race") || "RENNEN";
const card = document.getElementById("card");
if (QS.get("veil") !== "0") card.classList.add("veil");
const zoom = parseFloat(QS.get("zoom") || "1");
if (zoom && zoom !== 1) card.style.transform = "scale(" + zoom + ")";
let debug = QS.get("debug") === "1";
document.body.classList.toggle("debug", debug);

let data = null, pages = [], page = 0, sig = "", manual = !AUTO_PARAM, navSeq = null, timer = null;

function fitTitle() {           // long track names: shrink instead of cutting off
  const el = document.getElementById("title");
  let fs = 18; el.style.fontSize = fs + "px";
  while (el.scrollWidth > el.clientWidth && fs > 11) el.style.fontSize = (--fs) + "px";
}
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function fmtLap(sec) {
  if (!sec || sec <= 0) return "";
  const m = Math.floor(sec / 60), s = (sec - m * 60).toFixed(3).padStart(6, "0");
  return m + ":" + s;
}
function title(d) {
  if (QS.get("title")) return QS.get("title");
  const ev = d.event || {}, parts = [];
  if (ev.round) parts.push(ROUND_WORD + " " + ev.round + (d.races > 1 && d.race_no > 1 ? " · " + RACE_WORD + " " + d.race_no : ""));
  if (ev.league) parts.push(ev.league);
  const trk = [d.track_short || d.track, d.track_config].filter(Boolean).join(" ");
  if (trk) parts.push(trk);
  return parts.join(" - ").toUpperCase() || "—";
}
function buildPages(rows) {
  const order = [], by = {};
  rows.forEach(r => {
    const k = r.class_id || 0;
    if (!by[k]) { by[k] = []; order.push(k); }
    by[k].push(r);
  });
  const out = [];
  order.forEach(k => {
    const list = by[k];
    for (let i = 0; i < list.length; i += PER)
      out.push({ cls: k, name: list[0].class_name || "GRID", color: list[0].class_color || "",
                 rows: list.slice(i, i + PER), from: i + 1, to: Math.min(i + PER, list.length), classStart: i });
  });
  return { pages: out, classes: order.map(k => ({ id: k, name: by[k][0].class_name || "GRID", color: by[k][0].class_color || "" })) };
}
let classes = [];
function slotHtml(r, n, i) {
  if (!r) return '<div class="slot empty"></div>';
  const nm = data.team_event && r.team_name ? r.team_name : r.name;
  const t = fmtLap(r.best_time);
  return '<div class="slot in" style="animation-delay:' + (i * 0.06).toFixed(2) + 's">' +
    '<div class="r1"><div class="pos">' + n + '</div><div class="name"' + (String(nm || "").length > 19 ? ' style="font-size:12px"' : '') + '><span>' + esc(nm || "—") + '</span></div></div>' +
    '<div class="r2"><div class="num">' + esc(r.car_number) + '</div>' +
    (t ? '<div class="time">' + t + '</div>' : '<div class="time none">NO TIME</div>') + '</div></div>';
}
function draw() {
  const main = document.getElementById("main");
  if (!pages.length) {
    main.innerHTML = '<div class="empty-msg">' + (data && data.connected ? "Noch kein Startgrid" : "Warte auf iRacing …") + '</div>';
    return;
  }
  page = ((page % pages.length) + pages.length) % pages.length;
  const pg = pages[page];
  const multi = classes.length > 1;
  let side = classes.map(c => '<div class="ctab' + (c.id === pg.cls ? ' on' : '') + '"' +
      (multi && c.color ? ' style="border-left-color:' + c.color + '"' : '') + '>' + esc(c.name) + '</div>').join("");
  pages.forEach((p, i) => { if (p.cls === pg.cls) side += '<div class="pbox' + (i === page ? ' on' : '') + '">' + p.from + "–" + p.to + '</div>'; });
  // class grid position (= overall in a single-class field)
  const L = [], R = [];
  for (let i = 0; i < PER; i++) {
    const r = pg.rows[i], n = pg.classStart + i + 1;
    (i % 2 === 0 ? L : R).push(slotHtml(r, multi ? n : (r ? r.position : n), i));
  }
  main.innerHTML = '<div class="side">' + side + '</div><div class="cols"><div class="col">' + L.join("") +
                   '</div><div class="col right">' + R.join("") + '</div></div>';
  document.getElementById("hint").textContent = (manual ? "MANUELL" : "AUTO " + SECS + " s") + " · Seite " + (page + 1) + "/" + pages.length;
}
function go(p) { page = p; draw(); schedule(); }
function schedule() {
  clearTimeout(timer);
  if (!manual && pages.length > 1) timer = setTimeout(() => go(page + 1), SECS * 1000);
}
async function tick() {
  try {
    data = await (await fetch("/grid", { cache: "no-store" })).json();
  } catch (e) { return; }
  document.getElementById("title").textContent = title(data);
  fitTitle();
  const logo = document.getElementById("logo"), foot = document.getElementById("foot");
  const src = QS.get("logo") === "0" ? "" : (QS.get("logo") ||
      (data.event && data.event.slug ? "https://league.simracing-hub.com/logos/" + data.event.slug + ".webp" : ""));
  if (!src) foot.style.visibility = "hidden";
  else if (logo.dataset.src !== src) {
    logo.dataset.src = src; foot.style.visibility = "hidden";
    logo.onload = () => foot.style.visibility = ""; logo.onerror = () => foot.style.visibility = "hidden";
    logo.src = src;
  }
  const rows = data.grid || [];
  const s = (data.team_event ? "T" : "") + rows.map(r => r.car_idx + ":" + r.position + ":" + (r.best_time || 0).toFixed(3) + ":" + r.class_id + ":" + r.name + ":" + r.team_name).join("|");
  if (s === sig) return;
  const first = sig === "";
  sig = s;
  const b = buildPages(rows);
  pages = b.pages; classes = b.classes;
  if (first) page = 0;
  draw(); if (first) schedule();
}
async function pollNav() {
  let st;
  try { st = await (await fetch("/nav", { cache: "no-store" })).json(); } catch (e) { return; }
  const wasManual = manual;
  manual = !AUTO_PARAM || st.manual;
  if (navSeq === null) { navSeq = st.seq; if (manual !== wasManual) schedule(); draw(); return; }  // older commands: not ours
  if (st.seq !== navSeq) {
    navSeq = st.seq;
    if (st.cmd === "next") return go(page + 1);
    if (st.cmd === "prev") return go(page - 1);
    if (st.cmd === "first") return go(0);
  }
  if (manual !== wasManual) { schedule(); draw(); }
}
function send(cmd) { fetch("/nav/" + cmd, { method: "POST" }).then(pollNav).catch(() => {}); }
document.addEventListener("keydown", e => {
  const k = e.key;
  if (k === "h" || k === "H") { debug = !debug; document.body.classList.toggle("debug", debug); }
  else if (k === "ArrowRight" || k === "PageDown" || k === " " || k === "n" || k === "N") send("next");
  else if (k === "ArrowLeft" || k === "PageUp" || k === "p" || k === "P") send("prev");
  else if (k === "Home" || k === "1") send("first");
  else if (k === "a" || k === "A") send("auto");
  else return;
  e.preventDefault();
});
// back to page 1 every time OBS shows the source (auto and manual)
function restart() { if (pages.length) go(0); }
window.addEventListener("obsSourceVisibleChanged", e => { if (!e.detail || e.detail.visible) restart(); });
window.addEventListener("obsSourceActiveChanged", e => { if (!e.detail || e.detail.active) restart(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) restart(); });
if (QS.get("demo") === "1") {
  window.fetch = (u => async (url, o) => {
    if (String(url).startsWith("/grid")) {
      const names = ["Maurice Becker","Yannick Wonnenberg","Mike Zocher","Dennis Richter","Marius Becker","Tim Freudling","Andre Brechmann","Andreas Bastian","Remo Cursio","Bastian Kübler","Finn Zhou","Sven Maier"];
      const nums = ["49","03","01","63","14","11","82","921","7","22","5","31"];
      return { json: async () => ({ connected: true, source: "qualifying", track_short: "Indianapolis", track_config: "Road Course",
        races: 1, race_no: 1, team_event: false, event: { league: "CAS GT3 WCT", slug: "cas-gt3-wct", round: 5 },
        grid: names.map((n, i) => ({ car_idx: i, position: i + 1, name: n, car_number: nums[i], best_time: 82.563 + i * 0.1 + (i % 3) * 0.031,
          class_id: 1, class_name: "GT3 2025", class_color: "", team_name: "" })) }) };
    }
    if (String(url).startsWith("/nav")) return { json: async () => ({ seq: 0, cmd: "", manual: false }) };
    return u(url, o);
  })(window.fetch);
}
tick(); setInterval(tick, 2000);
pollNav(); setInterval(pollNav, 300);
</script></body></html>
"""


GRID_CARS_HTML = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"><title>Starting Grid Cars</title>
<style>
:root { --head: #d9302c; --accent: #e65fa0; --panel: rgba(38,38,42,.94); --bar: rgba(44,44,48,.94);
        --box: rgba(28,28,32,.96); --chip: #f2f2f2; --text: #f4f4f6; --muted: #b8b8c2; }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: rgba(0,0,0,0); overflow: hidden; }
body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; color: var(--text); }
body.debug { background: #3a4a3a; }
.card { position: relative; width: 900px; height: 770px; padding: 14px 18px 0; transform-origin: 0 0; }
.hbar { height: 52px; background: var(--head); clip-path: polygon(0 0, 100% 0, calc(100% - 22px) 100%, 0 100%);
        display: flex; align-items: center; justify-content: center; padding: 0 40px;
        font-size: 26px; font-weight: 900; font-style: italic; letter-spacing: .4px; text-transform: uppercase;
        white-space: nowrap; overflow: hidden; }
.sbar { margin-top: 6px; height: 42px; width: 86%; background: var(--bar);
        clip-path: polygon(0 0, 100% 0, calc(100% - 18px) 100%, 0 100%);
        display: flex; align-items: center; justify-content: center; gap: 14px;
        font-size: 22px; font-weight: 900; font-style: italic; letter-spacing: .4px; }
.sbar .cls { font-size: 14px; font-style: normal; font-weight: 800; padding: 3px 10px; border-radius: 3px;
             background: rgba(255,255,255,.12); border-left: 5px solid transparent; }
.sbar .rng { font-size: 13px; font-style: normal; font-weight: 700; color: var(--muted); }
.grid { position: relative; height: 640px; margin-top: 12px; }
.slot { position: absolute; width: 420px; height: 140px; }
.slot.in { animation: slotIn .5s cubic-bezier(.2,.8,.2,1) both; }
@keyframes slotIn { from { opacity: 0; transform: translateX(-24px); } to { opacity: 1; transform: none; } }
.slot .panel { position: absolute; left: 22px; top: 36px; width: 380px; height: 72px; background: var(--panel);
               transform: skewX(-14deg); border-right: 4px solid var(--accent); border-bottom: 4px solid var(--accent); }
.slot .pos { position: absolute; left: 0; top: 0; width: 62px; height: 56px; background: var(--box);
             transform: skewX(-14deg); display: flex; align-items: center; justify-content: center; z-index: 2; }
.slot .pos span { transform: skewX(14deg); font-size: 38px; font-weight: 900; font-style: italic; }
.slot .carimg { position: absolute; right: 18px; top: -6px; height: 82px; max-width: 240px; object-fit: contain;
                z-index: 3; filter: drop-shadow(0 6px 6px rgba(0,0,0,.55)); visibility: hidden; }
.slot .carimg.ok { visibility: visible; }
.slot .brand { position: absolute; left: 30px; top: 78px; width: 84px; height: 26px; background: var(--chip);
               transform: skewX(-14deg); z-index: 2; display: flex; align-items: center; justify-content: center; }
.slot .brand img { transform: skewX(14deg); max-height: 20px; max-width: 64px; }
.slot .brand.none { background: rgba(255,255,255,.18); }
.slot .name { position: absolute; left: 128px; right: 30px; top: 80px; height: 24px; z-index: 2;
              font-size: 16px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.slot .num { position: absolute; left: 12px; top: 112px; width: 58px; height: 24px; background: var(--accent);
             transform: skewX(-14deg); display: flex; align-items: center; justify-content: center; z-index: 2; }
.slot .num span { transform: skewX(14deg); font-size: 15px; font-weight: 800; }
.slot .lap { position: absolute; right: 26px; top: 112px; height: 24px; display: flex; z-index: 2;
             transform: skewX(-14deg); }
.slot .lap b { background: #8a8a92; color: #1b1b1f; font-size: 11px; font-weight: 800; padding: 0 9px;
               display: flex; align-items: center; }
.slot .lap i { background: var(--box); font-style: normal; font-size: 14px; font-weight: 700; min-width: 92px;
               padding: 0 10px; display: flex; align-items: center; justify-content: flex-end;
               font-variant-numeric: tabular-nums; }
.slot .lap b span, .slot .lap i span { transform: skewX(14deg); }
.slot .lap i.none span { font-size: 11px; letter-spacing: 1px; color: var(--muted); }
.empty-msg { position: absolute; inset: 120px 60px auto; height: 80px; display: flex; align-items: center; justify-content: center;
             font-size: 22px; font-weight: 700; color: #c8c8d0; background: rgba(36,36,42,.8); }
.lg { position: absolute; left: 34px; top: 700px; width: 390px; height: 62px; display: flex; align-items: center;
      justify-content: center; background: rgba(30,30,34,.85); transform: skewX(-14deg); visibility: hidden; }
.lg img { transform: skewX(14deg); max-height: 50px; max-width: 330px; }
.hint { position: absolute; right: 8px; bottom: 4px; font-size: 11px; color: #ffd166; display: none; }
body.debug .hint { display: block; }
</style></head>
<body>
<div class="card" id="card">
  <div class="hbar" id="title">—</div>
  <div class="sbar" id="sbar"><span>STARTING GRID</span></div>
  <div class="grid" id="grid"></div>
  <div class="lg" id="foot"><img id="logo" alt=""></div>
  <div class="hint" id="hint"></div>
</div>
<script>
const QS = new URLSearchParams(location.search);
const PER = Math.max(2, parseInt(QS.get("per") || "8", 10));
const SECS = Math.max(2, parseFloat(QS.get("secs") || "10"));
const AUTO_PARAM = QS.get("auto") === "1";          // pages turn only by the Stream Deck keys / arrows; ?auto=1 = old timer
const ROUND_WORD = QS.get("round") || "LAUF";
const RACE_WORD = QS.get("race") || "RENNEN";
const VIEW = QS.get("view") || "";
const DEMO = QS.get("demo") === "1";
const hex = v => /^[0-9a-fA-F]{3,8}$/.test(v || "") ? "#" + v : "";
if (hex(QS.get("head"))) document.documentElement.style.setProperty("--head", hex(QS.get("head")));
if (hex(QS.get("accent"))) document.documentElement.style.setProperty("--accent", hex(QS.get("accent")));
const card = document.getElementById("card");
const zoom = parseFloat(QS.get("zoom") || "1");
if (zoom && zoom !== 1) card.style.transform = "scale(" + zoom + ")";
let debug = QS.get("debug") === "1";
document.body.classList.toggle("debug", debug);

let data = null, pages = [], classes = [], page = 0, sig = "", manual = !AUTO_PARAM, navSeq = null, timer = null;

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function fmtLap(sec) {
  if (!sec || sec <= 0) return "";
  const m = Math.floor(sec / 60), s = (sec - m * 60).toFixed(3).padStart(6, "0");
  return m + ":" + s;
}
function fitTitle() {
  const el = document.getElementById("title");
  let fs = 26; el.style.fontSize = fs + "px";
  while (el.scrollWidth > el.clientWidth && fs > 13) el.style.fontSize = (--fs) + "px";
}
function title(d) {
  if (QS.get("title")) return QS.get("title");
  const ev = d.event || {}, parts = [];
  if (ev.league) parts.push(ev.league);
  if (ev.round) parts.push(ROUND_WORD + " " + ev.round + (d.races > 1 && d.race_no > 1 ? " · " + RACE_WORD + " " + d.race_no : ""));
  const trk = [d.track_short || d.track, d.track_config].filter(Boolean).join(" ");
  if (trk) parts.push(trk);
  return parts.join(" - ") || "STARTING GRID";
}
function buildPages(rows) {
  const order = [], by = {};
  rows.forEach(r => { const k = r.class_id || 0; if (!by[k]) { by[k] = []; order.push(k); } by[k].push(r); });
  const out = [];
  order.forEach(k => {
    const list = by[k];
    const pole = list.find(r => r.best_time > 0);
    for (let i = 0; i < list.length; i += PER)
      out.push({ cls: k, name: list[0].class_name || "GRID", color: list[0].class_color || "", pole: pole ? pole.best_time : 0,
                 rows: list.slice(i, i + PER), from: i + 1, to: Math.min(i + PER, list.length), classStart: i });
  });
  classes = order;
  return out;
}
function carSrc(r) {
  if (DEMO) return "";
  return "/car/" + r.car_idx + ".png?n=" + encodeURIComponent(r.car_number || "") + (VIEW ? "&view=" + encodeURIComponent(VIEW) : "");
}
function slotHtml(r, n, i, pg, multi) {
  const x = i % 2 === 0 ? 0 : 450, y = Math.floor(i / 2) * 142 + (i % 2 ? 66 : 0);
  const nm = data.team_event && r.team_name ? r.team_name : r.name;
  let val, lbl = "LAP", none = false;
  if (!(r.best_time > 0)) { val = "NO TIME"; none = true; }
  else if (n === 1 || !pg.pole || r.best_time <= pg.pole) val = fmtLap(r.best_time);
  else val = "+" + (r.best_time - pg.pole).toFixed(3);
  const numStyle = multi && r.class_color ? ' style="background:' + r.class_color + '"' : '';
  const brand = r.brand ? '<div class="brand"><img src="/brand/' + encodeURIComponent(r.brand) + '" alt="" onerror="this.parentNode.classList.add(\'none\');this.remove()"></div>'
                        : '<div class="brand none"></div>';
  const src = carSrc(r);
  return '<div class="slot in" style="left:' + x + 'px;top:' + y + 'px;animation-delay:' + (i * 0.07).toFixed(2) + 's">' +
    '<div class="panel"></div>' +
    '<div class="pos"><span>' + n + '</span></div>' +
    (src ? '<img class="carimg" src="' + src + '" alt="" onload="this.classList.add(\'ok\')">' : '') +
    brand +
    '<div class="name">' + esc(nm || "—") + '</div>' +
    '<div class="num"' + numStyle + '><span>' + esc(r.car_number) + '</span></div>' +
    '<div class="lap"><b><span>' + lbl + '</span></b><i' + (none ? ' class="none"' : '') + '><span>' + val + '</span></i></div>' +
    '</div>';
}
function draw() {
  const grid = document.getElementById("grid"), sbar = document.getElementById("sbar");
  if (!pages.length) {
    sbar.innerHTML = "<span>STARTING GRID</span>";
    grid.innerHTML = '<div class="empty-msg">' + (data && data.connected ? "Noch kein Startgrid" : "Warte auf iRacing …") + '</div>';
    return;
  }
  page = ((page % pages.length) + pages.length) % pages.length;
  const pg = pages[page], multi = classes.length > 1;
  const same = pages.filter(p => p.cls === pg.cls).length > 1;
  sbar.innerHTML = "<span>STARTING GRID</span>" +
    (multi ? '<span class="cls"' + (pg.color ? ' style="border-left-color:' + pg.color + '"' : '') + '>' + esc(pg.name) + '</span>' : '') +
    (same ? '<span class="rng">' + pg.from + "–" + pg.to + '</span>' : '');
  grid.innerHTML = pg.rows.map((r, i) => slotHtml(r, multi ? pg.classStart + i + 1 : (r.position || pg.classStart + i + 1), i, pg, multi)).join("");
  document.getElementById("hint").textContent = (manual ? "MANUELL" : "AUTO " + SECS + " s") + " · Seite " + (page + 1) + "/" + pages.length;
}
function go(p) { page = p; draw(); schedule(); }
function schedule() {
  clearTimeout(timer);
  if (!manual && pages.length > 1) timer = setTimeout(() => go(page + 1), SECS * 1000);
}
async function tick() {
  try { data = await (await fetch("/grid", { cache: "no-store" })).json(); } catch (e) { return; }
  document.getElementById("title").textContent = title(data);
  fitTitle();
  // league logo from CLS (bottom left, under the left column); ?logo=<url>|0
  const logo = document.getElementById("logo"), foot = document.getElementById("foot");
  const lsrc = QS.get("logo") === "0" ? "" : (QS.get("logo") ||
      (data.event && data.event.slug ? "https://league.simracing-hub.com/logos/" + data.event.slug + ".webp" : ""));
  if (!lsrc) foot.style.visibility = "hidden";
  else if (logo.dataset.src !== lsrc) {
    logo.dataset.src = lsrc; foot.style.visibility = "hidden";
    logo.onload = () => foot.style.visibility = "visible"; logo.onerror = () => foot.style.visibility = "hidden";
    logo.src = lsrc;
  }
  const rows = data.grid || [];
  const s = (data.team_event ? "T" : "") + rows.map(r => [r.car_idx, r.position, (r.best_time || 0).toFixed(3), r.class_id, r.name, r.team_name, r.car_number, r.brand].join(":")).join("|");
  if (s === sig) return;
  const first = sig === "";
  sig = s;
  pages = buildPages(rows);
  if (first) page = 0;
  draw(); if (first) schedule();
}
async function pollNav() {
  let st;
  try { st = await (await fetch("/nav", { cache: "no-store" })).json(); } catch (e) { return; }
  const wasManual = manual;
  manual = !AUTO_PARAM || st.manual;
  if (navSeq === null) { navSeq = st.seq; if (manual !== wasManual) schedule(); draw(); return; }
  if (st.seq !== navSeq) {
    navSeq = st.seq;
    if (st.cmd === "next") return go(page + 1);
    if (st.cmd === "prev") return go(page - 1);
    if (st.cmd === "first") return go(0);
  }
  if (manual !== wasManual) { schedule(); draw(); }
}
function send(cmd) { fetch("/nav/" + cmd, { method: "POST" }).then(pollNav).catch(() => {}); }
document.addEventListener("keydown", e => {
  const k = e.key;
  if (k === "h" || k === "H") { debug = !debug; document.body.classList.toggle("debug", debug); }
  else if (k === "ArrowRight" || k === "PageDown" || k === " " || k === "n" || k === "N") send("next");
  else if (k === "ArrowLeft" || k === "PageUp" || k === "p" || k === "P") send("prev");
  else if (k === "Home" || k === "1") send("first");
  else if (k === "a" || k === "A") send("auto");
  else return;
  e.preventDefault();
});
function restart() { if (pages.length) go(0); }
window.addEventListener("obsSourceVisibleChanged", e => { if (!e.detail || e.detail.visible) restart(); });
window.addEventListener("obsSourceActiveChanged", e => { if (!e.detail || e.detail.active) restart(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) restart(); });
if (DEMO) {
  window.fetch = (u => async (url, o) => {
    if (String(url).startsWith("/grid")) {
      const names = ["Maurice Becker","Yannick Wonnenberg","Mike Zocher","Dennis Richter","Marius Becker","Tim Freudling","Andre Brechmann","Andreas Bastian","Remo Cursio","Bastian Kübler"];
      const nums = ["49","03","01","63","14","11","82","921","7","22"];
      const brands = ["porsche","bmw","mercedes","ferrari","audi","mclaren","lamborghini","bmw","porsche","ford"];
      return { json: async () => ({ connected: true, source: "qualifying", track_short: "Indianapolis", track_config: "Road Course",
        races: 1, race_no: 1, team_event: false, event: { league: "CAS GT3 WCT", slug: "cas-gt3-wct", round: 5 },
        grid: names.map((n, i) => ({ car_idx: i, position: i + 1, name: n, car_number: nums[i], brand: brands[i],
          best_time: 82.563 + i * 0.27 + (i % 3) * 0.031, class_id: 1, class_name: "GT3", class_color: "", team_name: "" })) }) };
    }
    if (String(url).startsWith("/nav")) return { json: async () => ({ seq: 0, cmd: "", manual: false }) };
    return u(url, o);
  })(window.fetch);
}
tick(); setInterval(tick, 2000);
pollNav(); setInterval(pollNav, 300);
</script></body></html>
"""


GRID_ROW_HTML = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"><title>Grid Row</title>
<style>
:root { --accent: #e65fa0; --head: #d9302c; --text: #f4f4f6; --muted: #b8b8c2; --box: rgba(24,24,28,.96); }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: rgba(0,0,0,0); overflow: hidden; }
body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; color: var(--text); }
body.debug { background: #3a4a3a; }
.card { position: relative; width: 1000px; height: 680px; transform-origin: 0 0; overflow: hidden; border-radius: 6px;
        background:
          repeating-linear-gradient(90deg, rgba(255,255,255,.025) 0 2px, transparent 2px 9px),
          repeating-linear-gradient(90deg, rgba(0,0,0,.18) 0 1px, transparent 1px 23px),
          linear-gradient(180deg, #34343a, #2a2a2f 60%, #26262b); }
/* kerbs left / right */
.kerb { position: absolute; top: 0; bottom: 0; width: 26px;
        background: repeating-linear-gradient(180deg, #c8202a 0 34px, #e8e8ea 34px 68px); }
.kerb.l { left: 0; } .kerb.r { right: 0; }
.head { position: absolute; left: 26px; right: 26px; top: 0; height: 44px; background: var(--head);
        display: flex; align-items: center; justify-content: center; font-size: 21px; font-weight: 900;
        font-style: italic; text-transform: uppercase; letter-spacing: .4px; white-space: nowrap; overflow: hidden; padding: 0 20px; }
.check { position: absolute; left: 26px; right: 26px; top: 56px; height: 36px;
         background: conic-gradient(#f2f2f2 25%, #1c1c1f 0 50%, #f2f2f2 0 75%, #1c1c1f 0) 0 0 / 36px 36px; opacity: .9; }
.line { position: absolute; left: 26px; right: 26px; top: 96px; height: 10px; background: #d8b21c; opacity: .9; }
.rowtag { position: absolute; left: 50%; top: 118px; transform: translateX(-50%); background: rgba(16,16,20,.85);
          padding: 4px 16px; border-radius: 3px; font-size: 16px; font-weight: 900; font-style: italic; letter-spacing: 1px;
          white-space: nowrap; }
.rowtag .cls { font-style: normal; font-size: 13px; margin-left: 10px; padding: 1px 8px; border-radius: 3px;
               background: rgba(255,255,255,.14); border-left: 5px solid transparent; }
.slot { position: absolute; width: 400px; height: 470px; }
.slot.in { animation: drop .6s cubic-bezier(.2,.8,.2,1) both; }
@keyframes drop { from { opacity: 0; transform: translateY(40px); } to { opacity: 1; transform: none; } }
.slot.s0 { left: 66px; top: 150px; }
.slot.s1 { left: 534px; top: 205px; }
/* white grid-box marks: front bar + side ticks, like a painted grid slot */
.mark { position: absolute; left: 30px; right: 30px; top: 20px; height: 330px; border-top: 5px solid #e6e6e6;
        opacity: .85; }
.mark::before, .mark::after { content: ""; position: absolute; top: -5px; width: 5px; height: 46px; background: #e6e6e6; }
.mark::before { left: 0; } .mark::after { right: 0; }
.carbox { position: absolute; left: 0; right: 0; top: 30px; height: 330px; display: flex; align-items: center; justify-content: center; }
.carbox img { visibility: hidden; filter: drop-shadow(0 10px 12px rgba(0,0,0,.6)); }
.carbox img.ok { visibility: visible; }
.carbox.side img { max-width: 380px; max-height: 200px; }
.carbox svg.topcar { height: 318px; width: auto; filter: drop-shadow(0 12px 10px rgba(0,0,0,.65)); }
.carbox .ph { width: 150px; height: 300px; border-radius: 40px 40px 26px 26px; background: rgba(255,255,255,.06);
              border: 2px dashed rgba(255,255,255,.18); }
.plate { position: absolute; left: 0; right: 0; top: 368px; height: 92px; display: flex; }
.plate .pos { width: 78px; background: var(--box); display: flex; align-items: center; justify-content: center;
              font-size: 46px; font-weight: 900; font-style: italic; }
.plate .info { flex: 1; min-width: 0; background: rgba(40,40,46,.95); border-bottom: 4px solid var(--accent);
               padding: 8px 14px; display: flex; flex-direction: column; justify-content: center; gap: 6px; }
.plate .nm { font-size: 24px; font-weight: 800; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.plate .sub { display: flex; align-items: center; gap: 10px; font-size: 15px; color: var(--muted); white-space: nowrap; }
.plate .num { background: var(--accent); color: #fff; font-weight: 800; padding: 1px 9px; border-radius: 3px; }
.plate .brand { height: 22px; background: #f2f2f2; border-radius: 3px; padding: 2px 6px; display: flex; align-items: center; }
.plate .brand img { max-height: 18px; max-width: 54px; }
.plate .tm { margin-left: auto; font-weight: 700; color: var(--text); font-variant-numeric: tabular-nums; }
.plate .tm.none { font-size: 12px; letter-spacing: 1px; color: var(--muted); }
.msg { position: absolute; left: 140px; right: 140px; top: 300px; height: 80px; display: flex; align-items: center;
       justify-content: center; background: rgba(20,20,24,.85); font-size: 22px; font-weight: 700; color: #c8c8d0; }
.lg { position: absolute; left: 66px; bottom: 10px; height: 52px; display: flex; align-items: center; visibility: hidden;
      background: rgba(20,20,24,.75); padding: 6px 14px; border-radius: 4px; }
.lg img { max-height: 40px; max-width: 260px; }
.hint { position: absolute; right: 36px; bottom: 6px; font-size: 11px; color: #ffd166; display: none; }
body.debug .hint { display: block; }
</style></head>
<body>
<div class="card" id="card">
  <div class="kerb l"></div><div class="kerb r"></div>
  <div class="check"></div><div class="line"></div>
  <div class="head" id="title">—</div>
  <div class="rowtag" id="rowtag">STARTREIHE 1</div>
  <div id="slots"></div>
  <div class="lg" id="foot"><img id="logo" alt=""></div>
  <div class="hint" id="hint"></div>
</div>
<script>
const QS = new URLSearchParams(location.search);
const SECS = Math.max(2, parseFloat(QS.get("secs") || "8"));
const AUTO_PARAM = QS.get("auto") === "1";          // pages turn only by the Stream Deck keys / arrows; ?auto=1 = old timer
const ROUND_WORD = QS.get("round") || "LAUF";
const RACE_WORD = QS.get("race") || "RENNEN";
const ROW_WORD = QS.get("row") || "STARTREIHE";
const VIEW = QS.get("view") || "";
// /row/top: iRacing renders only the side view, so the car is DRAWN from
// above in the livery's main colours (/car/<idx>/colors)
const TOP = /\/top\/?$/.test(location.pathname) || QS.get("top") === "1";
const colors = {};
const DEMO = QS.get("demo") === "1";
const hex = v => /^[0-9a-fA-F]{3,8}$/.test(v || "") ? "#" + v : "";
if (hex(QS.get("head"))) document.documentElement.style.setProperty("--head", hex(QS.get("head")));
if (hex(QS.get("accent"))) document.documentElement.style.setProperty("--accent", hex(QS.get("accent")));
const card = document.getElementById("card");
const zoom = parseFloat(QS.get("zoom") || "1");
if (zoom && zoom !== 1) card.style.transform = "scale(" + zoom + ")";
let debug = QS.get("debug") === "1";
document.body.classList.toggle("debug", debug);

const DEMO_COLORS = [{ body: "#0c0c0c", accent: "#fc0c0c", trim: "#9c843c" }, { body: "#1aa7a0", accent: "#ffffff", trim: "#111111" },
  { body: "#ff8a00", accent: "#333333", trim: "#ffffff" }, { body: "#2a4fa0", accent: "#ffffff", trim: "#c0c0c0" }];
let data = null, pages = [], page = 0, sig = "", manual = !AUTO_PARAM, navSeq = null, timer = null, multi = false;

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function fmtLap(sec) {
  if (!sec || sec <= 0) return "";
  const m = Math.floor(sec / 60), s = (sec - m * 60).toFixed(3).padStart(6, "0");
  return m + ":" + s;
}
function title(d) {
  if (QS.get("title")) return QS.get("title");
  const ev = d.event || {}, parts = [];
  if (ev.league) parts.push(ev.league);
  if (ev.round) parts.push(ROUND_WORD + " " + ev.round + (d.races > 1 && d.race_no > 1 ? " · " + RACE_WORD + " " + d.race_no : ""));
  const trk = [d.track_short || d.track, d.track_config].filter(Boolean).join(" ");
  if (trk) parts.push(trk);
  return parts.join(" - ") || "STARTING GRID";
}
// one page = one grid row (two cars), in grid order; multiclass: rows per class
function buildPages(rows) {
  const order = [], by = {};
  rows.forEach(r => { const k = r.class_id || 0; if (!by[k]) { by[k] = []; order.push(k); } by[k].push(r); });
  multi = order.length > 1;
  const out = [];
  order.forEach(k => {
    const list = by[k], pole = list.find(r => r.best_time > 0);
    for (let i = 0; i < list.length; i += 2)
      out.push({ rows: list.slice(i, i + 2), row: i / 2 + 1, start: i, cls: list[0].class_name || "", color: list[0].class_color || "",
                 pole: pole ? pole.best_time : 0 });
  });
  return out;
}
function carSrc(r) {
  return DEMO ? "" : "/car/" + r.car_idx + ".png?n=" + encodeURIComponent(r.car_number || "") + (VIEW ? "&view=" + encodeURIComponent(VIEW) : "");
}
// GT3-ish car seen from above, nose up (towards the start line)
function topCar(r, c) {
  const id = "c" + r.car_idx, num = esc(r.car_number);
  const fs = num.length > 2 ? 26 : 32;
  const brand = r.brand ? '<rect x="74" y="74" width="52" height="26" rx="4" fill="#f4f4f6"/>' +
    '<image href="/brand/' + encodeURIComponent(r.brand) + '" x="78" y="76" width="44" height="22" preserveAspectRatio="xMidYMid meet"/>' : '';
  const body = "M100 10 C62 10 38 20 33 46 L30 128 C29 158 40 176 42 200 L42 262 C40 284 28 300 28 330 L30 398 C32 418 62 426 100 426 C138 426 168 418 170 398 L172 330 C172 300 160 284 158 262 L158 200 C160 176 171 158 170 128 L167 46 C162 20 138 10 100 10 Z";
  return '<svg class="topcar" viewBox="0 0 200 440" xmlns="http://www.w3.org/2000/svg">' +
    '<defs><linearGradient id="sh' + id + '" x1="0" x2="1">' +
      '<stop offset="0" stop-color="#000" stop-opacity=".45"/><stop offset=".22" stop-color="#000" stop-opacity="0"/>' +
      '<stop offset=".5" stop-color="#fff" stop-opacity=".10"/><stop offset=".78" stop-color="#000" stop-opacity="0"/>' +
      '<stop offset="1" stop-color="#000" stop-opacity=".45"/></linearGradient>' +
      '<clipPath id="cl' + id + '"><path d="' + body + '"/></clipPath></defs>' +
    // tyres
    '<g fill="#2b2b2e" stroke="#0a0a0a" stroke-width="2"><rect x="10" y="62" width="30" height="64" rx="7"/><rect x="160" y="62" width="30" height="64" rx="7"/>' +
    '<rect x="8" y="298" width="32" height="72" rx="7"/><rect x="160" y="298" width="32" height="72" rx="7"/></g>' +
    // front splitter
    '<path d="M44 16 L156 16 L162 26 L38 26 Z" fill="#151515"/>' +
    '<path d="' + body + '" fill="' + c.body + '"/>' +
    '<g clip-path="url(#cl' + id + ')">' +
      // livery: accent sweep on both flanks + centre stripes in trim
      '<path d="M20 120 C70 170 70 260 20 330 L20 360 C84 280 84 170 20 92 Z" fill="' + c.accent + '"/>' +
      '<path d="M180 120 C130 170 130 260 180 330 L180 360 C116 280 116 170 180 92 Z" fill="' + c.accent + '"/>' +
      '<rect x="86" y="0" width="7" height="440" fill="' + c.trim + '"/><rect x="107" y="0" width="7" height="440" fill="' + c.trim + '"/>' +
      '<rect x="0" y="0" width="200" height="440" fill="url(#sh' + id + ')"/>' +
    '</g>' +
    // headlights, bonnet vents
    '<path d="M44 34 L66 28 L64 40 L42 46 Z" fill="#fff6c8" opacity=".9"/><path d="M156 34 L134 28 L136 40 L158 46 Z" fill="#fff6c8" opacity=".9"/>' +
    '<rect x="58" y="112" width="20" height="26" rx="3" fill="#000" opacity=".35"/><rect x="122" y="112" width="20" height="26" rx="3" fill="#000" opacity=".35"/>' +
    brand +
    // windscreen, roof with the number, rear window
    '<path d="M58 160 C78 150 122 150 142 160 L136 212 C112 206 88 206 64 212 Z" fill="#1b2433"/>' +
    '<path d="M66 162 C84 156 100 155 110 156 L104 168 C92 168 78 170 68 174 Z" fill="#fff" opacity=".18"/>' +
    '<rect x="64" y="222" width="72" height="54" rx="8" fill="#f4f4f6"/>' +
    '<text x="100" y="' + (249 + fs * 0.35) + '" text-anchor="middle" font-family="Segoe UI, Arial, sans-serif" font-weight="900" font-style="italic" font-size="' + fs + '" fill="#111">' + num + '</text>' +
    '<path d="M64 286 C88 282 112 282 136 286 L132 318 C112 314 88 314 68 318 Z" fill="#1b2433"/>' +
    // mirrors, rear wing
    '<ellipse cx="34" cy="172" rx="9" ry="6" fill="' + c.body + '" stroke="#111" stroke-width="2"/><ellipse cx="166" cy="172" rx="9" ry="6" fill="' + c.body + '" stroke="#111" stroke-width="2"/>' +
    '<rect x="96" y="380" width="8" height="30" fill="#111"/>' +
    '<rect x="16" y="396" width="168" height="22" rx="4" fill="#161616"/><rect x="16" y="396" width="168" height="6" rx="3" fill="' + c.accent + '"/>' +
    '<rect x="12" y="388" width="8" height="38" rx="2" fill="#0c0c0c"/><rect x="180" y="388" width="8" height="38" rx="2" fill="#0c0c0c"/>' +
    '</svg>';
}
function loadColors(rows) {
  rows.forEach(r => {
    if (colors[r.car_idx] || DEMO) return;
    colors[r.car_idx] = { body: "#d8d8dc", accent: "#c8202a", trim: "#111111", wait: true };
    fetch("/car/" + r.car_idx + "/colors", { cache: "no-store" }).then(x => x.json()).then(c => {
      colors[r.car_idx] = c; if (pages.length) draw();
    }).catch(() => { delete colors[r.car_idx]; });
  });
}
function slotHtml(r, i, pg) {
  const n = pg.start + i + 1;
  const nm = data.team_event && r.team_name ? r.team_name : r.name;
  let tm, none = false;
  if (!(r.best_time > 0)) { tm = "NO TIME"; none = true; }
  else if (n === 1 || !pg.pole || r.best_time <= pg.pole) tm = fmtLap(r.best_time);
  else tm = "+" + (r.best_time - pg.pole).toFixed(3);
  let car;
  if (TOP) car = topCar(r, colors[r.car_idx] || DEMO_COLORS[(r.car_idx || 0) % DEMO_COLORS.length]);
  else {
    const src = carSrc(r);
    car = src ? '<img src="' + src + '" alt="" onload="this.classList.add(\'ok\')">' : '<div class="ph"></div>';
  }
  const brand = r.brand ? '<span class="brand"><img src="/brand/' + encodeURIComponent(r.brand) + '" alt="" onerror="this.parentNode.remove()"></span>' : '';
  const numStyle = multi && r.class_color ? ' style="background:' + r.class_color + '"' : '';
  return '<div class="slot s' + i + ' in" style="animation-delay:' + (i * 0.18) + 's">' +
    '<div class="mark"></div>' +
    '<div class="carbox ' + (TOP ? 'top' : 'side') + '">' + car + '</div>' +
    '<div class="plate"><div class="pos">' + n + '</div><div class="info">' +
      '<div class="nm">' + esc(nm || "—") + '</div>' +
      '<div class="sub"><span class="num"' + numStyle + '>#' + esc(r.car_number) + '</span>' + brand +
      (data.team_event && r.team_name && r.name ? '<span>' + esc(r.name) + '</span>' : '') +
      '<span class="tm' + (none ? ' none' : '') + '">' + tm + '</span></div>' +
    '</div></div></div>';
}
function draw() {
  const slots = document.getElementById("slots"), tag = document.getElementById("rowtag");
  if (!pages.length) {
    tag.textContent = ROW_WORD;
    slots.innerHTML = '<div class="msg">' + (data && data.connected ? "Noch kein Startgrid" : "Warte auf iRacing …") + '</div>';
    return;
  }
  page = ((page % pages.length) + pages.length) % pages.length;
  const pg = pages[page];
  tag.innerHTML = esc(ROW_WORD + " " + pg.row) +
    (multi && pg.cls ? '<span class="cls"' + (pg.color ? ' style="border-left-color:' + pg.color + '"' : '') + '>' + esc(pg.cls) + '</span>' : '');
  if (TOP) loadColors(pg.rows);
  const html = pg.rows.map((r, i) => slotHtml(r, i, pg)).join("");
  const again = slots.dataset.page === String(page);
  slots.dataset.page = String(page);
  slots.innerHTML = again ? html.replace(/ in" style="animation-delay:[^"]*"/g, '"') : html;
  document.getElementById("hint").textContent = (manual ? "MANUELL" : "AUTO " + SECS + " s") + " · Reihe " + (page + 1) + "/" + pages.length;
}
function go(p) { page = p; draw(); schedule(); }
function schedule() {
  clearTimeout(timer);
  if (!manual && pages.length > 1) timer = setTimeout(() => go(page + 1), SECS * 1000);
}
async function tick() {
  try { data = await (await fetch("/grid", { cache: "no-store" })).json(); } catch (e) { return; }
  const t = document.getElementById("title");
  t.textContent = title(data);
  let fs = 21; t.style.fontSize = fs + "px";
  while (t.scrollWidth > t.clientWidth && fs > 12) t.style.fontSize = (--fs) + "px";
  const logo = document.getElementById("logo"), foot = document.getElementById("foot");
  const lsrc = QS.get("logo") === "0" ? "" : (QS.get("logo") ||
      (data.event && data.event.slug ? "https://league.simracing-hub.com/logos/" + data.event.slug + ".webp" : ""));
  if (!lsrc) foot.style.visibility = "hidden";
  else if (logo.dataset.src !== lsrc) {
    logo.dataset.src = lsrc; foot.style.visibility = "hidden";
    logo.onload = () => foot.style.visibility = "visible"; logo.onerror = () => foot.style.visibility = "hidden";
    logo.src = lsrc;
  }
  const rows = data.grid || [];
  const s = (data.team_event ? "T" : "") + rows.map(r => [r.car_idx, r.position, (r.best_time || 0).toFixed(3), r.class_id, r.name, r.team_name, r.car_number, r.brand].join(":")).join("|");
  if (s === sig) return;
  const first = sig === "";
  sig = s;
  pages = buildPages(rows);
  if (first) page = 0;
  draw(); if (first) schedule();
}
async function pollNav() {
  let st;
  try { st = await (await fetch("/nav", { cache: "no-store" })).json(); } catch (e) { return; }
  const wasManual = manual;
  manual = !AUTO_PARAM || st.manual;
  if (navSeq === null) { navSeq = st.seq; if (manual !== wasManual) schedule(); draw(); return; }
  if (st.seq !== navSeq) {
    navSeq = st.seq;
    if (st.cmd === "next") return go(page + 1);
    if (st.cmd === "prev") return go(page - 1);
    if (st.cmd === "first") return go(0);
  }
  if (manual !== wasManual) { schedule(); draw(); }
}
function send(cmd) { fetch("/nav/" + cmd, { method: "POST" }).then(pollNav).catch(() => {}); }
document.addEventListener("keydown", e => {
  const k = e.key;
  if (k === "h" || k === "H") { debug = !debug; document.body.classList.toggle("debug", debug); }
  else if (k === "ArrowRight" || k === "PageDown" || k === " " || k === "n" || k === "N") send("next");
  else if (k === "ArrowLeft" || k === "PageUp" || k === "p" || k === "P") send("prev");
  else if (k === "Home" || k === "1") send("first");
  else if (k === "a" || k === "A") send("auto");
  else return;
  e.preventDefault();
});
function restart() { if (pages.length) go(0); }
window.addEventListener("obsSourceVisibleChanged", e => { if (!e.detail || e.detail.visible) restart(); });
window.addEventListener("obsSourceActiveChanged", e => { if (!e.detail || e.detail.active) restart(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) restart(); });
if (DEMO) {
  window.fetch = (u => async (url, o) => {
    if (String(url).startsWith("/grid")) {
      const names = ["Maurice Becker","Yannick Wonnenberg","Mike Zocher","Dennis Richter","Marius Becker","Tim Freudling"];
      const nums = ["49","03","01","63","14","11"], brands = ["porsche","bmw","mercedes","ferrari","audi","mclaren"];
      return { json: async () => ({ connected: true, track_short: "Indianapolis", track_config: "Road Course", races: 1, race_no: 1,
        team_event: false, event: { league: "CAS GT3 WCT", slug: "cas-gt3-wct", round: 5 },
        grid: names.map((n, i) => ({ car_idx: i, position: i + 1, name: n, car_number: nums[i], brand: brands[i],
          best_time: 82.563 + i * 0.27, class_id: 1, class_name: "GT3", class_color: "", team_name: "" })) }) };
    }
    if (String(url).startsWith("/nav")) return { json: async () => ({ seq: 0, cmd: "", manual: true }) };
    return u(url, o);
  })(window.fetch);
}
tick(); setInterval(tick, 2000);
pollNav(); setInterval(pollNav, 300);
</script></body></html>
"""



@app.route("/")
def index():
    # ?style=classic = the old purple card with auto-scroll
    if request.args.get("style") == "classic":
        return render_template_string(GRID_HTML_CLASSIC)
    return Response(GRID_HTML, mimetype="text/html")


@app.route("/cars")
def cars_page():
    # starting grid with car liveries (iRacing render server), DNLS-style
    return Response(GRID_CARS_HTML, mimetype="text/html")


@app.route("/row")
def row_page():
    # one grid row (two cars) at a time, seen like a painted grid; next = next row
    return Response(GRID_ROW_HTML, mimetype="text/html")


@app.route("/row/top")
def row_top():
    # same page, cars drawn from above in the livery colours
    return Response(GRID_ROW_HTML, mimetype="text/html")


@app.route("/car/<int:cidx>/colors")
def car_colors(cidx: int):
    # body / accent / trim for the drawn top-down car on /row/top
    d = poller.raw.get(cidx)
    if not d:
        return jsonify({"body": "#d8d8dc", "accent": "#c8202a", "trim": "#111111", "source": "none"})
    return jsonify(livery_colors(d, team=bool(poller.get().get("team_event"))))


@app.route("/car/<int:cidx>.png")
def car_image(cidx: int):
    d = poller.raw.get(cidx)
    if not d:
        return Response(status=404)
    try:
        view = int(request.args.get("view", 1))
    except ValueError:
        view = 1
    data = renderer.render(d, team=bool(poller.get().get("team_event")), view=view)
    if data is None:
        # 404, not cached: the <img> stays hidden and the render server
        # coming up later self-heals on the next page draw
        return Response(status=404)
    resp = Response(data, mimetype="image/png")
    resp.headers["Cache-Control"] = "max-age=300"
    return resp


@app.route("/brand/<slug>")
def brand_logo(slug: str):
    path = resolve_logo(slug)
    if not path or not path.is_file():
        abort(404)
    return send_file(str(path), max_age=3600)


@app.route("/grid")
def grid():
    return jsonify({**poller.get(), "event": event.data})


# The page commands are shared by every WCT-look page (5002 /wct, 5010
# /lastrace/wct, /teams/wct poll this server), hence CORS.
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/nav")
def nav_state():
    return _cors(jsonify(nav.get()))


# Stream Deck "Website" action (access in background) or the page's own keys:
# /nav/next, /nav/prev, /nav/first, /nav/auto (toggle), /nav/auto_on, /nav/auto_off
@app.route("/nav/<cmd>", methods=["GET", "POST"])
def nav_cmd(cmd):
    if cmd not in NAV_CMDS:
        return _cors(jsonify({"ok": False, "error": "unknown command"})), 404
    return _cors(jsonify({"ok": True, **nav.send(cmd)}))


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()
    threading.Thread(target=event.run, daemon=True).start()

    print("\n" + "=" * 60)
    print("  iRacing Qualifying Grid Display v2")
    print("  Open in browser:  http://localhost:5001")
    print("  (Runs in parallel to iracing_dashboard.py on port 5000)")
    print("  Pages: auto every ?secs=10 s, or Stream Deck /nav/next | prev | first | auto")
    print("  With car liveries: http://localhost:5001/cars  (?view=0..3 render angle)")
    print("  One grid row at a time: http://localhost:5001/row  (drawn from above: /row/top)")
    print("  Old design: http://localhost:5001/?style=classic")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)
    finally:
        poller.stop()
