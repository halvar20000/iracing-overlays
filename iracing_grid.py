"""
iRacing Qualifying Grid Display (v2)
------------------------------------
Standalone companion to iracing_dashboard.py.

Default page (2026-10-25): the starting grid in the WCT broadcast look,
pages of 8 — automatic (?secs=10) or by hand: Stream Deck
http://localhost:5001/nav/next | prev | first | auto, keys → ← Home A.
Options: ?auto=0 ?per= ?zoom= ?title= ?round=LAUF ?race=RENNEN ?logo=<url>|0
?veil=0 ?debug=1 (H) ?demo=1. Old purple card: /?style=classic.

Requirements:  pip install pyirsdk flask
Run:           python iracing_grid.py
Open:          http://localhost:5001
"""

import json
import threading
import time
import urllib.request
from flask import Flask, Response, jsonify, render_template_string, request

from class_colors import class_colors

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()


# -----------------------------------------------------------------------------
# Grid data poller
# -----------------------------------------------------------------------------
class GridPoller(SDKPoller):
    tag = "grid"
    poll_interval = 2.0

    def _driver_map(self) -> dict:
        info = self.ir["DriverInfo"] or {}
        out = {}
        ccol = class_colors(info.get("Drivers") or [])
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
                "class_id":   int(d.get("CarClassID") or 0),
                "class_name": d.get("CarClassShortName") or "",
                "class_color": ccol.get(int(d.get("CarClassID") or 0), ""),
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



class EventInfo:
    """League + round for the title ("LAUF 5 - CAS GT3 WCT - …") from the
    championship server (5010 /api/roundinfo: it detects the league from the
    grid and knows a replayed earlier round). 5010 not running = no title
    parts, the track name alone."""
    URL = "http://127.0.0.1:5010/api/roundinfo"

    def __init__(self):
        self.data = {}

    def run(self):
        while True:
            try:
                with urllib.request.urlopen(self.URL, timeout=8) as r:
                    d = json.loads(r.read().decode("utf-8"))
                info = (d.get("info") or {}) if d.get("ok") else {}
                league = info.get("league") or {}
                self.data = {"league": league.get("name") or "", "slug": league.get("slug") or "",
                             "round": (info.get("nextRound") or {}).get("number")} if league else {}
            except Exception:
                self.data = {}
            time.sleep(30)


class PageNav:
    """Page commands from a Stream Deck / keyboard: every command gets a new
    seq; each open grid page applies it once. `manual` stops the automatic
    page change (also after a page reload) until "auto" is sent."""

    def __init__(self):
        self._lock = threading.Lock()
        self.state = {"seq": 0, "cmd": "", "manual": False}

    def send(self, cmd):
        with self._lock:
            st = self.state
            if cmd == "auto":                         # toggle
                st["manual"] = not st["manual"]
            elif cmd in ("auto_on", "auto_off"):
                st["manual"] = cmd == "auto_off"
            else:                                     # next / prev / first
                st["manual"] = True
            st["seq"] += 1
            st["cmd"] = cmd
            return dict(st)

    def get(self):
        with self._lock:
            return dict(self.state)


NAV_CMDS = ("next", "prev", "first", "auto", "auto_on", "auto_off")


# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
poller = GridPoller()
event = EventInfo()
nav = PageNav()


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
function updateAutoScroll() {
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
    if (scrollAnim) { scrollAnim.currentTime = 0; scrollAnim.play(); }
    else updateAutoScroll();
}
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


# Starting grid in the broadcast look of the WCT graphics (2026-10-25,
# Thomas's screenshot "LAUF 5 - CAS GT3 WCT - INDIANAPOLIS RC"): blue
# STARTING GRID tab + title bar, orange rule, class tab + page boxes on the
# left, two staggered columns of 4 (pos box + name over an orange line, car
# number + quali time), league logo at the bottom. Pages of ?per=8 instead
# of the old scroll: automatic every ?secs=10 s, or by hand via the Stream
# Deck (/nav/next | prev | first | auto) or the keys → ← Home A.
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
const AUTO_PARAM = QS.get("auto") !== "0";
const ROUND_WORD = QS.get("round") || "LAUF";
const RACE_WORD = QS.get("race") || "RENNEN";
const card = document.getElementById("card");
if (QS.get("veil") !== "0") card.classList.add("veil");
const zoom = parseFloat(QS.get("zoom") || "1");
if (zoom && zoom !== 1) card.style.transform = "scale(" + zoom + ")";
let debug = QS.get("debug") === "1";
document.body.classList.toggle("debug", debug);

let data = null, pages = [], page = 0, sig = "", manual = !AUTO_PARAM, navSeq = null, timer = null;

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


@app.route("/")
def index():
    # ?style=classic = the old purple card with auto-scroll
    if request.args.get("style") == "classic":
        return render_template_string(GRID_HTML_CLASSIC)
    return Response(GRID_HTML, mimetype="text/html")


@app.route("/grid")
def grid():
    return jsonify({**poller.get(), "event": event.data})


@app.route("/nav")
def nav_state():
    return jsonify(nav.get())


# Stream Deck "Website" action (access in background) or the page's own keys:
# /nav/next, /nav/prev, /nav/first, /nav/auto (toggle), /nav/auto_on, /nav/auto_off
@app.route("/nav/<cmd>", methods=["GET", "POST"])
def nav_cmd(cmd):
    if cmd not in NAV_CMDS:
        return jsonify({"ok": False, "error": "unknown command"}), 404
    return jsonify({"ok": True, **nav.send(cmd)})


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
    print("  Old design: http://localhost:5001/?style=classic")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)
    finally:
        poller.stop()
