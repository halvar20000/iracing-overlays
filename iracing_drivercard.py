"""
iRacing Driver Card Overlay  (broadcast lower third)
----------------------------------------------------
Shows a broadcast card for the ON-CAMERA driver (CamCarIdx):

    [#7 · GT3]  T. HERBRIG          IR 3248 | A 4.99 | P5
                Team CAS Racing      BEST 1:52.301 | LAST 1:52.884 | 4x

Fields:
  - Driver name (big) + team name (small; hidden when identical to the
    driver name — solo sessions report the driver as their own "team")
  - iRating — DriverInfo.Drivers[].IRating. iRacing reports the rating
    for the LICENSE CATEGORY OF THIS SESSION (road/oval/dirt...), i.e.
    exactly "the iRating of the driven car class" a viewer expects.
  - License + SR (LicString, chip tinted with LicColor)
  - Car number + car-class chip (class color, useful in multiclass)
  - Class position (CarIdxClassPosition, falls back to overall
    CarIdxPosition in single-class sessions where ClassPosition is 0)
  - Best lap / last lap (CarIdxBestLapTime / CarIdxLastLapTime);
    the LAST cell turns green when it equals the driver's session
    best (a personal-best lap just happened)
  - Incidents — DriverInfo.Drivers[].CurDriverIncidentCount (falls back
    to TeamIncidentCount in team sessions)

Requirements:  pip install pyirsdk flask
Run:           python iracing_drivercard.py
OBS source:    http://localhost:5017   (transparent lower third)

Press H for a debug background, or open http://localhost:5017/?debug=1
"""

import threading
import time

from flask import Flask, jsonify, render_template_string

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()

PORT = 5017

DRIVERS_TTL = 5.0   # s between DriverInfo YAML re-parses (incidents live there)


def _abbrev(name: str) -> str:
    """'Joseph Johnson' -> 'J. Johnson' (same rule as the standings overlay)."""
    parts = (name or "").strip().split()
    if len(parts) < 2:
        return name or ""
    return f"{parts[0][0]}. {' '.join(parts[1:])}"


def _int_color(raw) -> "str | None":
    """iRacing int color (low 24 bits RGB) -> '#rrggbb', None-safe."""
    try:
        return "#{:06x}".format(int(raw) & 0xFFFFFF) if raw not in (None, "") \
            else None
    except (TypeError, ValueError):
        return None


class DriverCardPoller(SDKPoller):
    tag = "driver"
    poll_interval = 0.5   # lap times / camera flips — 2 Hz feels instant

    def __init__(self):
        super().__init__()
        self._drivers = {}
        self._drivers_t = 0.0

    def _refresh_drivers(self, ir, t_now):
        # Re-parse every DRIVERS_TTL even when populated: unlike the mostly
        # static caches elsewhere, this one carries the LIVE incident count.
        if self._drivers and t_now - self._drivers_t < DRIVERS_TTL:
            return
        info = ir["DriverInfo"] or {}
        out = {}
        for d in info.get("Drivers", []) or []:
            try:
                cidx = int(d.get("CarIdx"))
            except (TypeError, ValueError):
                continue
            if int(d.get("CarIsPaceCar") or 0) or int(d.get("IsSpectator") or 0):
                continue
            name = d.get("UserName") or ""
            team = (d.get("TeamName") or "").strip()
            inc = d.get("CurDriverIncidentCount")
            if inc in (None, "", -1):
                inc = d.get("TeamIncidentCount")
            try:
                inc = int(inc)
            except (TypeError, ValueError):
                inc = None
            try:
                irating = int(d.get("IRating"))
            except (TypeError, ValueError):
                irating = None
            out[cidx] = {
                "name":        name,
                "short":       _abbrev(name),
                "team":        team if team and team != name else "",
                "num":         str(d.get("CarNumber") or "").strip("\""),
                "irating":     irating,
                "lic":         (d.get("LicString") or "").strip(),
                "lic_color":   _int_color(d.get("LicColor")),
                "class_name":  (d.get("CarClassShortName") or "").strip(),
                "class_color": _int_color(d.get("CarClassColor")),
                "incidents":   inc,
            }
        if out:
            self._drivers = out
            self._drivers_t = t_now

    def _read_snapshot(self) -> dict:
        ir = self.ir
        self._refresh_drivers(ir, time.monotonic())

        base = {"connected": True, "show": False, "reason": ""}
        focus = ir["CamCarIdx"]
        if focus is None or focus < 0 or focus not in self._drivers:
            base["reason"] = "no camera car"
            return base
        d = self._drivers[focus]

        best_arr = ir["CarIdxBestLapTime"] or []
        last_arr = ir["CarIdxLastLapTime"] or []
        cls_pos_arr = ir["CarIdxClassPosition"] or []
        pos_arr = ir["CarIdxPosition"] or []

        def _lap(arr):
            v = arr[focus] if focus < len(arr) else 0.0
            return float(v) if v and v > 0 else None

        best = _lap(best_arr)
        last = _lap(last_arr)

        pos = cls_pos_arr[focus] if focus < len(cls_pos_arr) else 0
        if not pos or pos <= 0:
            # Single-class sessions often report ClassPosition = 0.
            pos = pos_arr[focus] if focus < len(pos_arr) else 0
        pos = int(pos) if pos and pos > 0 else None

        return {
            "connected": True, "show": True, "reason": "",
            "cidx": focus,
            "name": d["short"], "full_name": d["name"], "team": d["team"],
            "num": d["num"], "irating": d["irating"],
            "lic": d["lic"], "lic_color": d["lic_color"],
            "class_name": d["class_name"], "class_color": d["class_color"],
            "position": pos,
            "best_lap": best,
            "last_lap": last,
            # Personal best just set? (near-equality — both values come
            # from the same telemetry source unrounded)
            "last_is_best": (best is not None and last is not None
                             and abs(best - last) < 1e-4),
            "incidents": d["incidents"],
        }


# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
poller = DriverCardPoller()


@app.after_request
def _no_cache(resp):
    if "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


PAGE_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Driver Card</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
        width: 100%; height: 100%;
        background: rgba(0,0,0,0);
        background-color: rgba(0,0,0,0);
        font-family: 'Segoe UI', system-ui, sans-serif;
        color: #fff; overflow: hidden;
    }
    body.debug { background: #123; }
    body { display: flex; align-items: flex-end; justify-content: center;
           padding: 14px; }

    #card {
        display: none;
        align-items: stretch;
        border-radius: 10px;
        overflow: hidden;
        background: rgba(14, 14, 20, 0.88);
        border: 1px solid rgba(255, 255, 255, 0.10);
        box-shadow: 0 6px 30px rgba(0, 0, 0, 0.6);
        font-variant-numeric: tabular-nums;
        user-select: none;
    }
    #card.on { display: flex; }

    .ident {
        display: flex; align-items: center; gap: 12px;
        padding: 10px 18px;
        border-right: 1px solid rgba(255,255,255,0.08);
    }
    .numchip {
        display: flex; flex-direction: column; align-items: center;
        gap: 2px; min-width: 56px;
        padding: 4px 8px;
        background: rgba(255, 255, 255, 0.10);
        border-left: 4px solid var(--cls, #888);
        border-radius: 0 6px 6px 0;
    }
    .numchip .num { font-size: 20px; font-weight: 800; }
    .numchip .cls { font-size: 10px; font-weight: 700; letter-spacing: 1px;
                    color: #b0b0c0; text-transform: uppercase; }
    .who { display: flex; flex-direction: column; gap: 1px; }
    .who .name { font-size: 23px; font-weight: 800; letter-spacing: 0.4px;
                 white-space: nowrap; color: #ffb38a; }
    .who .team { font-size: 12px; font-weight: 600; color: #8a8a99;
                 letter-spacing: 0.6px; white-space: nowrap; }

    .stat {
        display: flex; flex-direction: column;
        align-items: center; justify-content: center;
        padding: 8px 15px; gap: 1px;
        border-right: 1px solid rgba(255,255,255,0.08);
        min-width: 80px;
    }
    .stat:last-child { border-right: none; }
    .stat .label { font-size: 10px; letter-spacing: 1.6px; color: #8a8a99;
                   text-transform: uppercase; white-space: nowrap; }
    .stat .value { font-size: 19px; font-weight: 800; line-height: 1.15;
                   white-space: nowrap; }

    .stat.ir  .value { color: #22c9e0; }
    .stat.pos .value { color: #ffd166; }
    .stat.inc .value { color: #ff6b74; }
    .licchip {
        font-size: 14px; font-weight: 800;
        padding: 2px 10px; border-radius: 999px;
        background: var(--lic, #555); color: #fff;
        text-shadow: 0 1px 2px rgba(0,0,0,0.6);
    }
    .stat.last .value.pb { color: #19d36b; }

    #dbg { position: fixed; top: 6px; left: 8px; font-size: 12px;
           color: #8a8a99; display: none; }
    body.debug #dbg { display: block; }
</style>
</head>
<body>

<div id="card">
    <div class="ident">
        <div class="numchip" id="numchip">
            <span class="num" id="num">#–</span>
            <span class="cls" id="cls"></span>
        </div>
        <div class="who">
            <span class="name" id="name">—</span>
            <span class="team" id="team"></span>
        </div>
    </div>
    <div class="stat ir">
        <span class="label">iRating</span>
        <span class="value" id="ir">—</span>
    </div>
    <div class="stat">
        <span class="label">License</span>
        <span class="licchip" id="lic">—</span>
    </div>
    <div class="stat pos">
        <span class="label">Pos</span>
        <span class="value" id="pos">—</span>
    </div>
    <div class="stat">
        <span class="label">Best lap</span>
        <span class="value" id="best">—</span>
    </div>
    <div class="stat last">
        <span class="label">Last lap</span>
        <span class="value" id="last">—</span>
    </div>
    <div class="stat inc">
        <span class="label">Inc</span>
        <span class="value" id="inc">—</span>
    </div>
</div>
<div id="dbg"></div>

<script>
const qs = new URLSearchParams(location.search);
if (qs.get('debug') === '1') document.body.classList.add('debug');
document.addEventListener('keydown', e => {
    if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug');
});

function fmtLap(t) {
    if (t == null) return '—';
    const m = Math.floor(t / 60);
    const s = (t - m * 60).toFixed(3).padStart(6, '0');
    return m > 0 ? `${m}:${s}` : s;
}
function fmtIR(v) {
    if (v == null) return '—';
    return v.toLocaleString('en-US');
}

let lastGood = Date.now();
const OFFLINE_AFTER_MS = 15000;

async function getStatus() {
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 4000);
    try { const r = await fetch('/status', { signal: ctrl.signal, cache: 'no-store' }); return await r.json(); }
    catch (e) { return null; }
    finally { clearTimeout(to); }
}

async function tick() {
    const d = await getStatus();
    const card = document.getElementById('card');
    const dbg = document.getElementById('dbg');

    if (!d || !d.connected || !d.show) {
        dbg.textContent = d ? (d.reason || 'hidden') : 'no response';
        if (Date.now() - lastGood > OFFLINE_AFTER_MS || (d && d.connected)) {
            card.classList.remove('on');
        }
        return;
    }
    lastGood = Date.now();
    card.classList.add('on');

    document.getElementById('num').textContent = '#' + (d.num || '–');
    document.getElementById('cls').textContent = d.class_name || '';
    document.getElementById('numchip').style.setProperty('--cls', d.class_color || '#888');
    document.getElementById('name').textContent = d.name || '—';
    const team = document.getElementById('team');
    team.textContent = d.team || '';
    team.style.display = d.team ? '' : 'none';

    document.getElementById('ir').textContent = fmtIR(d.irating);
    const lic = document.getElementById('lic');
    lic.textContent = d.lic || '—';
    lic.style.setProperty('--lic', d.lic_color || '#555');
    document.getElementById('pos').textContent = d.position != null ? 'P' + d.position : '—';
    document.getElementById('best').textContent = fmtLap(d.best_lap);
    const last = document.getElementById('last');
    last.textContent = fmtLap(d.last_lap);
    last.classList.toggle('pb', !!d.last_is_best);
    document.getElementById('inc').textContent =
        d.incidents != null ? d.incidents + 'x' : '—';

    dbg.textContent = `cidx=${d.cidx} ${d.full_name}`;
}
(function loop() { tick().finally(() => setTimeout(loop, 500)); })();
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


if __name__ == "__main__":
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()

    print("\n" + "=" * 60)
    print("  iRacing Driver Card Overlay (broadcast lower third)")
    print(f"  OBS browser source:  http://localhost:{PORT}")
    print("  On-camera driver: name, team, iRating, license/SR,")
    print("  class position, best/last lap, incidents.")
    print("  Press H (or ?debug=1) for a debug background.")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=PORT, debug=False,
                use_reloader=False, threaded=True)
    finally:
        poller.stop()
