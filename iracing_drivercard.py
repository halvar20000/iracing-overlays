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

Note: an "Inc" stat was removed on 2026-07-16. iRacing does not reliably
publish incident counts to a SPECTATOR: in online races every source
(DriverInfo.CurDriverIncidentCount, TeamIncidentCount and
SessionInfo.Sessions[].ResultsPositions[].Incidents) reads 0 or the -1
sentinel for every car, so the card could only ever show a wrong number.
Verify with dump_incidents.py before trying again; AI races DO publish
real values, which is what made this look like a code bug.

Requirements:  pip install pyirsdk flask
Run:           python iracing_drivercard.py
OBS source:    http://localhost:5017   (transparent lower third)
               http://localhost:5017/combo   (driver + car, see COMBO_HTML)

Press H for a debug background, or open http://localhost:5017/?debug=1
"""

import threading
import time

from flask import Flask, Response, abort, jsonify, render_template_string, send_file

import country_flags
from car_brands import detect_brand, resolve_logo
from car_render import CarRenderer
from iracing_sdk_base import SDKPoller, setup_utf8_stdout
from class_colors import class_colors, text_on
from cls_proam import ProAmRoster
setup_utf8_stdout()

PORT = 5017

DRIVERS_TTL = 5.0   # s between DriverInfo YAML re-parses


def _abbrev(name: str) -> str:
    """'Joseph Johnson' -> 'J. Johnson' (same rule as the standings overlay)."""
    parts = (name or "").strip().split()
    if len(parts) < 2:
        return name or ""
    return f"{parts[0][0]}. {' '.join(parts[1:])}"


def _uid(raw) -> "int | None":
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


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
        self.team_event = False
        self._seen = {}          # TeamID -> {user id: {id, name, country}}
        self._seen_session = None

    def _refresh_drivers(self, ir, t_now):
        # Re-parse every DRIVERS_TTL rather than caching once: drivers join,
        # leave and swap cars mid-session.
        if self._drivers and t_now - self._drivers_t < DRIVERS_TTL:
            return
        info = ir["DriverInfo"] or {}
        weekend = ir["WeekendInfo"] or {}
        self.team_event = str(weekend.get("TeamRacing") or 0) == "1"
        session = (weekend.get("SessionID"), weekend.get("SubSessionID"))
        if session != self._seen_session:
            self._seen_session = session
            self._seen = {}
        out = {}
        # multiclass: same colour per class as the tower / trackmap
        ccol = class_colors(info.get("Drivers", []) or [])
        for d in info.get("Drivers", []) or []:
            try:
                cidx = int(d.get("CarIdx"))
            except (TypeError, ValueError):
                continue
            if int(d.get("CarIsPaceCar") or 0) or int(d.get("IsSpectator") or 0):
                continue
            name = d.get("UserName") or ""
            team = (d.get("TeamName") or "").strip()
            try:
                irating = int(d.get("IRating"))
            except (TypeError, ValueError):
                irating = None
            country = (country_flags.code_from_name((d.get("FlairName") or "").strip())
                       or country_flags.code_from_iso(proam.country(d.get("UserID"))))
            if self.team_event and d.get("TeamID"):
                # iRacing lists only the driver in the car right now, so
                # remember whoever has driven it this session.
                self._seen.setdefault(d["TeamID"], {})[d.get("UserID")] = {
                    "id": d.get("UserID"), "name": name, "country": country}
            brand = detect_brand(d.get("CarPath"), d.get("CarScreenName")) or ""
            panel = ccol.get(int(d.get("CarClassID") or 0)) if len(ccol) > 1 else None
            out[cidx] = {
                "raw":         d,
                "panel_color": panel,
                "panel_text":  text_on(panel) if panel else None,
                "team_id":     d.get("TeamID"),
                "team_raw":    team,
                "country":     country,
                "brand":       brand if brand and resolve_logo(brand) else "",
                "name":        name,
                "user_id":     d.get("UserID"),
                "short":       _abbrev(name),
                "team":        team if team and team != name else "",
                "num":         str(d.get("CarNumber") or "").strip("\""),
                "irating":     irating,
                "lic":         (d.get("LicString") or "").strip(),
                "lic_color":   _int_color(d.get("LicColor")),
                "class_name":  (d.get("CarClassShortName") or "").strip(),
                "class_color": _int_color(d.get("CarClassColor")),
            }
        if out:
            self._drivers = out
            self._drivers_t = t_now

    def _lineup(self, d: dict) -> list:
        """The team's drivers, the one in the car first: CLS registrations
        plus anyone seen driving this car (a replacement CLS does not list)."""
        field = [x["user_id"] for x in self._drivers.values()]
        seen = self._seen.get(d["team_id"], {})
        members = []
        for uid in [d["user_id"], *seen]:
            members = proam.team_members(uid, field)
            if members:
                break
        out = [{"name": d["name"], "country": d["country"], "current": True}]
        ids = {_uid(d["user_id"])}
        for m in [*members, *seen.values()]:
            if _uid(m["id"]) in ids:
                continue
            ids.add(_uid(m["id"]))
            out.append({"name": m["name"], "current": False,
                        "country": country_flags.code_from_iso(m.get("country"))})
        return out

    def _read_snapshot(self) -> dict:
        ir = self.ir
        t_now = time.monotonic()
        self._refresh_drivers(ir, t_now)

        # WCT GT3 Pro/Am bar — only when the field is a WCT field
        # (see cls_proam.py); None hides the bar.
        proam_cls = None
        if proam.active_for(x["user_id"] for x in self._drivers.values()):
            focus_d = self._drivers.get(ir["CamCarIdx"])
            proam_cls = proam.lookup_field(focus_d["user_id"], True) if focus_d else None

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
            "name": d["short"], "full_name": d["name"],
            # iRacing's team (team events), else the driver's CLS team
            "team": d["team"] or proam.team(d["user_id"], (x["user_id"] for x in self._drivers.values())) or "",
            "user_id": d["user_id"], "team_event": self.team_event,
            "panel_color": d["panel_color"], "panel_text": d["panel_text"],
            "team_name": d["team_raw"], "country": d["country"], "brand": d["brand"],
            "lineup": self._lineup(d) if self.team_event else [],
            "num": d["num"], "irating": d["irating"],
            "lic": d["lic"], "lic_color": d["lic_color"],
            "class_name": d["class_name"], "class_color": d["class_color"],
            "position": pos,
            "proam": proam_cls,
            "best_lap": best,
            "last_lap": last,
            # Personal best just set? (near-equality — both values come
            # from the same telemetry source unrounded)
            "last_is_best": (best is not None and last is not None
                             and abs(best - last) < 1e-4),
        }


# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
poller = DriverCardPoller()
proam = ProAmRoster()
car_renderer = CarRenderer()


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
        /* nearly solid (Andreas, 2026-10-10: the race showed through) */
        background: rgba(14, 14, 20, 0.97);
        border: 1px solid rgba(255, 255, 255, 0.10);
        box-shadow: 0 6px 30px rgba(0, 0, 0, 0.6);
        font-variant-numeric: tabular-nums;
        user-select: none;
    }
    #card.on { display: flex; }

    .ident {
        display: flex; align-items: center; gap: 12px;
        padding: 12px 20px;
        border-right: 1px solid rgba(255,255,255,0.12);
    }
    .numchip {
        display: flex; flex-direction: column; align-items: center;
        gap: 2px; min-width: 64px;
        padding: 5px 9px;
        background: rgba(255, 255, 255, 0.10);
        border-left: 4px solid var(--cls, #888);
        border-radius: 0 6px 6px 0;
    }
    .numchip .num { font-size: 25px; font-weight: 800; }
    .numchip .cls { font-size: 13px; font-weight: 800; letter-spacing: 1px;
                    color: #e2e2ea; text-transform: uppercase; }
    /* WCT GT3 Pro/Am marker in front of the name: red PRO, green AM. */
    .pa { width: 6px; align-self: stretch; border-radius: 2px; display: none; }
    .pa.pro { display: block; background: #e63946; }
    .pa.am  { display: block; background: #2ecc71; }
    .pa.nr  { display: block; background: #000; box-shadow: 0 0 0 1px rgba(255,255,255,0.85); }   /* not rated yet */
    .who { display: flex; flex-direction: column; gap: 1px; }
    .who .name { font-size: 29px; font-weight: 800; letter-spacing: 0.4px;
                 white-space: nowrap; color: #ffb38a; }
    .who .team { font-size: 16px; font-weight: 700; color: #d4d4de;
                 letter-spacing: 0.4px; white-space: nowrap; }

    .stat {
        display: flex; flex-direction: column;
        align-items: center; justify-content: center;
        padding: 9px 18px; gap: 2px;
        border-right: 1px solid rgba(255,255,255,0.12);
        min-width: 96px;
    }
    .stat:last-child { border-right: none; }
    /* labels were 10px grey (#8a8a99) — unreadable without scaling the
       whole card up in OBS (Andreas, 2026-10-10) */
    .stat .label { font-size: 14px; font-weight: 700; letter-spacing: 1px; color: #d4d4de;
                   text-transform: uppercase; white-space: nowrap; }
    .stat .value { font-size: 24px; font-weight: 800; line-height: 1.15;
                   white-space: nowrap; }

    .stat.ir  .value { color: #22c9e0; }
    /* first visible stat after the ident block keeps a clean edge */
    .stat.pos .value { color: #ffd166; }
    .licchip {
        font-size: 18px; font-weight: 800; white-space: nowrap;
        padding: 2px 12px; border-radius: 999px;
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
        <div class="pa" id="pa"></div>
        <div class="who">
            <span class="name" id="name">—</span>
            <span class="team" id="team"></span>
        </div>
    </div>
    <div class="stat ir">
        <span class="label">iRating</span>
        <span class="value" id="ir">—</span>
    </div>
    <div class="stat lic-stat">
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
</div>
<div id="dbg"></div>

<script>
const qs = new URLSearchParams(location.search);
// /simple (2026-10-10): the same card without iRating and licence / safety
// rating. ?hide=ir,lic does the same on any URL.
const HIDE = new Set((qs.get('hide') || '').split(',').filter(Boolean));
if (location.pathname.replace(/\/+$/, '') === '/simple') { HIDE.add('ir'); HIDE.add('lic'); }
for (const k of HIDE) {
    const el = document.querySelector(k === 'ir' ? '.stat.ir' : k === 'lic' ? '.stat.lic-stat' : null);
    if (el) el.style.display = 'none';
}
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
    document.getElementById('pa').className =
        'pa' + (d.proam === 'PRO' ? ' pro' : d.proam === 'AM' ? ' am' : d.proam === 'UNRATED' ? ' nr' : '');
    // full name incl. first name (2026-10-27, Thomas); ?short=1 = "M. Becker"
    document.getElementById('name').textContent =
        (qs.get('short') === '1' ? d.name : (d.full_name || d.name)) || '—';
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

    dbg.textContent = `cidx=${d.cidx} ${d.full_name}`;
}
(function loop() { tick().finally(() => setTimeout(loop, 500)); })();
</script>
</body>
</html>
"""


COMBO_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Driver + Car</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
        width: 100%; height: 100%;
        background: rgba(0,0,0,0);
        font-family: 'Segoe UI', system-ui, sans-serif;
        color: #fff; overflow: hidden;
    }
    body.debug { background: #567; }
    body { display: flex; align-items: flex-end; justify-content: flex-start; padding: 8px; }
    #card { display: none; width: 680px; --panel: #1f9e3a; --ink: #fff;
            --shade: 0 1px 3px rgba(0,0,0,0.55); user-select: none;
            font-variant-numeric: tabular-nums;
            filter: drop-shadow(0 4px 12px rgba(0,0,0,0.55)); }
    #card.on { display: block; }

    .bar { display: flex; align-items: center; gap: 12px; height: 48px; padding-right: 14px;
           background: linear-gradient(90deg, #15151b, #2b2b35); border-radius: 8px 8px 0 0; }
    .tab { display: flex; align-items: center; justify-content: center;
           min-width: 92px; height: 100%; margin-left: 14px; padding: 0 20px;
           background: var(--panel); color: var(--ink); font-size: 28px; font-weight: 800; font-style: italic;
           clip-path: polygon(14px 0, 100% 0, calc(100% - 14px) 100%, 0 100%); }
    .pa { width: 7px; height: 30px; border-radius: 2px; display: none; flex: none; }
    .pa.pro { display: block; background: #e63946; }
    .pa.am  { display: block; background: #2ecc71; }
    .pa.nr  { display: block; background: #000; box-shadow: 0 0 0 1px rgba(255,255,255,0.85); }
    .title { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; padding: 0 5px;
             font-size: 29px; font-weight: 800; font-style: italic; text-transform: uppercase; }
    .barflag { height: 28px; width: 40px; object-fit: cover; flex: none; border-radius: 2px; }
    .clschip { flex: none; padding: 2px 9px; font-size: 15px; font-weight: 800;
               background: rgba(255,255,255,0.12); border-left: 4px solid var(--cls, #888); }

    .panel { position: relative; height: 136px; overflow: hidden; border-radius: 0 0 8px 8px;
             background: linear-gradient(100deg, rgba(0,0,0,0.12), rgba(0,0,0,0) 55%), var(--panel);
             border-top: 2px solid rgba(255,255,255,0.28); }
    /* a white chip: logos are multicoloured, so they cannot be turned white */
    .brand { position: absolute; left: 12px; top: 10px; height: 46px; max-width: 110px;
             padding: 4px 8px; background: #fff; border-radius: 4px; object-fit: contain; }
    .car { position: absolute; left: 62px; bottom: 3px; width: 256px; height: 104px;
           object-fit: contain; object-position: left bottom; display: none;
           filter: drop-shadow(0 3px 4px rgba(0,0,0,0.5)); }
    .right { position: absolute; right: 14px; top: 0; bottom: 0; width: 346px;
             display: flex; flex-direction: column; justify-content: center; gap: 3px;
             color: var(--ink); text-shadow: var(--shade); text-transform: uppercase; }

    .drv { display: flex; align-items: center; justify-content: flex-end; gap: 9px; }
    .drv .nm { min-width: 0; white-space: nowrap; overflow: hidden; font-size: 19px; padding: 0 5px;
               font-weight: 500; font-style: italic; }
    .drv img { width: 32px; height: 21px; object-fit: cover; flex: none; border-radius: 2px; }
    .drv.cur .nm { font-size: 28px; font-weight: 300; }
    .drv.cur .nm b { font-weight: 800; }
    .drv.cur img { width: 42px; height: 28px; }

    .teamline { white-space: nowrap; overflow: hidden; text-align: right; font-size: 23px; padding: 0 5px;
                font-weight: 800; font-style: italic; margin-bottom: 7px; }
    .stats { display: flex; justify-content: flex-end; gap: 6px; text-shadow: none; }
    .stat { display: flex; flex-direction: column; align-items: center; min-width: 76px;
            padding: 4px 10px; background: rgba(0,0,0,0.72); color: #fff; border-radius: 4px; }
    .stat .label { font-size: 12px; font-weight: 700; letter-spacing: 0.8px; color: #e4e4ea;
                   white-space: nowrap; }
    .stat .value { font-size: 22px; font-weight: 800; line-height: 1.15; white-space: nowrap; }
    .stat.pos .value { color: #ffd166; }
    .stat .value.pb { color: #6dffa8; }
    #dbg { position: fixed; top: 6px; left: 8px; font-size: 12px; color: #ccc; display: none; }
    body.debug #dbg { display: block; }
</style>
</head>
<body>
<div id="card">
    <div class="bar">
        <div class="tab" id="num"></div>
        <div class="pa" id="pa"></div>
        <div class="title" id="title"></div>
        <span class="clschip" id="cls"></span>
        <img class="barflag" id="barflag" alt="">
    </div>
    <div class="panel">
        <img class="brand" id="brand" alt="">
        <img class="car" id="car" alt="">
        <div class="right" id="right"></div>
    </div>
</div>
<div id="dbg"></div>
<script>
const qs = new URLSearchParams(location.search);
const card = document.getElementById('card');
if (qs.get('debug') === '1') document.body.classList.add('debug');
if (qs.get('zoom')) document.body.style.zoom = qs.get('zoom');
const FIXED_COLOR = /^[0-9a-fA-F]{6}$/.test(qs.get('color') || '');
if (FIXED_COLOR) card.style.setProperty('--panel', '#' + qs.get('color'));
document.addEventListener('keydown', e => {
    if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug');
});

const DEMO = {
    1: { connected: true, show: true, team_event: true, num: '27', team_name: 'Demo Racing Team',
         lineup: [{ name: 'Alex Beispiel', country: 'ca', current: true },
                  { name: 'Bruno Muster', country: 'br' }, { name: 'Tom Sample', country: 'gb' }] },
    2: { connected: true, show: true, team_event: false, num: '63', full_name: 'Alex Beispiel',
         country: 'de', team: 'Demo Racing Team', class_name: 'GT3', class_color: '#ffd166',
         proam: 'AM', position: 4, best_lap: 112.301, last_lap: 112.301, last_is_best: true },
}[qs.get('demo')];

function fmtLap(t) {
    if (t == null) return '—';
    const m = Math.floor(t / 60);
    const s = (t - m * 60).toFixed(3).padStart(6, '0');
    return m > 0 ? `${m}:${s}` : s;
}
// iRacing tells namesakes apart by a digit at the end ("Max Muster2")
const clean = n => (n || '').replace(/\s*\d+$/, '');
function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
}
function flagImg(code) {
    const i = el('img');
    i.alt = '';
    i.onerror = () => i.remove();
    i.src = '/flag/' + encodeURIComponent(String(code).toLowerCase()) + '.svg';
    return i;
}
function fit(e, max) {              // long team names shrink instead of being cut
    let size = max;
    e.style.fontSize = size + 'px';
    while (e.scrollWidth > e.clientWidth && size > 13) e.style.fontSize = --size + 'px';
}
function drvRow(p) {
    const row = el('div', 'drv' + (p.current ? ' cur' : ''));
    const nm = el('span', 'nm');
    const name = clean(p.name);
    if (p.current) {
        const cut = name.lastIndexOf(' ');
        nm.append(name.slice(0, cut + 1), el('b', '', name.slice(cut + 1)));
    } else nm.textContent = name;
    row.append(nm);
    if (p.country) row.append(flagImg(p.country));
    return row;
}
function stat(cls, label, value, pb) {
    const s = el('div', 'stat ' + cls);
    s.append(el('span', 'label', label), el('span', 'value' + (pb ? ' pb' : ''), value));
    return s;
}

const MAX_DRIVERS = parseInt(qs.get('max')) || 4;
let sig = '', carKey = '', carRetry = 0;

function setCar(d) {
    const car = document.getElementById('car');
    const key = d.cidx == null ? '' : `${d.cidx}-${d.num}-${d.user_id}`;
    if (key === carKey && Date.now() < carRetry) return;
    if (key === carKey && car.style.display === 'block') return;
    carKey = key;
    carRetry = Date.now() + 20000;   // the render server may come up later
    if (key) car.src = `/car/${d.cidx}.png?k=${encodeURIComponent(key)}`;
    else car.style.display = 'none';
}
document.getElementById('car').onload = e => { e.target.style.display = 'block'; };
document.getElementById('car').onerror = e => { e.target.style.display = 'none'; };

function render(d) {
    const asTeam = qs.get('team') === '1' ? true : qs.get('team') === '0' ? false : !!d.team_event;
    const now = JSON.stringify([asTeam, d.num, d.team_name, d.team, d.full_name, d.country, d.proam,
        d.class_name, d.class_color, d.panel_color, d.position, d.best_lap, d.last_lap, d.last_is_best, d.lineup, d.brand]);
    setCar(d);
    if (now === sig) return;
    sig = now;

    if (!FIXED_COLOR) {            // class colour in a multiclass field, else green
        card.style.setProperty('--panel', d.panel_color || '#1f9e3a');
        card.style.setProperty('--ink', d.panel_text || '#fff');
        card.style.setProperty('--shade', d.panel_text && d.panel_text !== '#ffffff' ? 'none' : '0 1px 3px rgba(0,0,0,0.55)');
    }
    document.getElementById('num').textContent = d.num || '–';
    const brand = document.getElementById('brand');
    if (d.brand) { brand.src = '/brand/' + encodeURIComponent(d.brand); brand.style.display = ''; }
    else brand.style.display = 'none';

    const title = document.getElementById('title');
    const right = document.getElementById('right');
    const flag = document.getElementById('barflag');
    const cls = document.getElementById('cls');
    right.textContent = '';
    if (asTeam) {
        title.textContent = clean(d.team_name) || clean(d.team) || clean(d.full_name);
        document.getElementById('pa').className = 'pa';
        cls.style.display = 'none';
        flag.style.display = 'none';
        const lineup = d.lineup && d.lineup.length ? d.lineup
            : [{ name: d.full_name, country: d.country, current: true }];
        lineup.slice(0, MAX_DRIVERS).forEach(p => {
            const row = drvRow(p);
            right.append(row);
            fit(row.querySelector('.nm'), p.current ? 28 : 19);
        });
    } else {
        title.textContent = clean(d.full_name || d.name);
        document.getElementById('pa').className =
            'pa' + (d.proam === 'PRO' ? ' pro' : d.proam === 'AM' ? ' am' : d.proam === 'UNRATED' ? ' nr' : '');
        cls.textContent = d.class_name || '';
        cls.style.display = d.class_name ? '' : 'none';
        cls.style.setProperty('--cls', d.panel_color || d.class_color || '#888');
        flag.style.display = d.country ? '' : 'none';
        if (d.country) flag.src = '/flag/' + encodeURIComponent(String(d.country).toLowerCase()) + '.svg';
        if (d.team) {
            const t = el('div', 'teamline', d.team);
            right.append(t);
            fit(t, 23);
        }
        const stats = el('div', 'stats');
        stats.append(stat('pos', 'POS', d.position != null ? 'P' + d.position : '—'),
                     stat('', 'BEST LAP', fmtLap(d.best_lap)),
                     stat('', 'LAST LAP', fmtLap(d.last_lap), d.last_is_best));
        right.append(stats);
    }
    fit(title, 29);
}

let lastGood = Date.now();
async function tick() {
    let d = DEMO;
    if (!d) {
        const ctrl = new AbortController();
        const to = setTimeout(() => ctrl.abort(), 4000);
        try { d = await (await fetch('/status', { signal: ctrl.signal, cache: 'no-store' })).json(); }
        catch (e) { d = null; }
        clearTimeout(to);
    }
    const dbg = document.getElementById('dbg');
    if (!d || !d.connected || !d.show) {
        dbg.textContent = d ? (d.reason || 'hidden') : 'no response';
        if (Date.now() - lastGood > 15000 || (d && d.connected)) card.classList.remove('on');
        return;
    }
    lastGood = Date.now();
    card.classList.add('on');      // before render: fit() needs real widths
    render(d);
    dbg.textContent = `cidx=${d.cidx} ${d.full_name || ''}`;
}
(function loop() { tick().finally(() => setTimeout(loop, 500)); })();
</script>
</body>
</html>
"""


@app.route("/")
@app.route("/simple")
def index():
    return render_template_string(PAGE_HTML)


@app.route("/combo")
def combo():
    return Response(COMBO_HTML, mimetype="text/html")


@app.route("/car/<int:cidx>.png")
def car_image(cidx: int):
    d = poller._drivers.get(cidx)
    data = car_renderer.render(d["raw"], team=poller.team_event) if d else None
    if data is None:
        return Response(status=404)     # not cached: self-heals once iRacing renders
    resp = Response(data, mimetype="image/png")
    resp.headers["Cache-Control"] = "max-age=300"
    return resp


@app.route("/flag/<code>.svg")
def flag(code: str):
    path = country_flags.flag_path(code)
    if not path:
        abort(404)
    return send_file(str(path), mimetype="image/svg+xml", max_age=86400)


@app.route("/brand/<slug>")
def brand_logo(slug: str):
    path = resolve_logo(slug)
    if not path or not path.is_file():
        abort(404)
    return send_file(str(path), max_age=3600)


@app.route("/status")
def status():
    return jsonify(poller.get())


@app.route("/proam")
def proam_status():
    return jsonify(proam.status())


if __name__ == "__main__":
    proam.start()
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()

    print("\n" + "=" * 60)
    print("  iRacing Driver Card Overlay (broadcast lower third)")
    print(f"  OBS browser source:  http://localhost:{PORT}")
    print("  On-camera driver: name, team, iRating, license/SR,")
    print("  class position, best/last lap.")
    print("  Press H (or ?debug=1) for a debug background.")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=PORT, debug=False,
                use_reloader=False, threaded=True)
    finally:
        poller.stop()
