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

/clock = the round "race clock" (IMSA look): league logo (from CLS via the
championship server 5010), session name, remaining time and a flag bar
(green / yellow = full course caution or pace laps / red / white /
checkered).  ?size=300 ?logo=<url>|0 ?flag=0 ?debug=1 ?demo=1
"""

import threading
from flask import Flask, Response, jsonify, render_template_string

import wct_board

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()


# -----------------------------------------------------------------------------
# Session-info poller
# -----------------------------------------------------------------------------
class SessionInfoPoller(SDKPoller):
    tag = "sess"

    def __init__(self, poll_hz: int = 2):
        # 2 Hz is plenty — session name / total length are static and the
        # remaining-time countdown only needs a sub-second refresh.
        super().__init__(poll_interval=1.0 / poll_hz)
        self._cache_key = None       # (SessionUniqueID, SessionNum)
        self._cache_static = None    # parsed once per session
        self._caution_key = None     # session the caution memory belongs to
        self._caution_seen = False   # a full course caution is (was) out

    def _parse_static(self, ir, sess_num):
        """Parse the heavy SessionInfo/WeekendInfo YAML for the current
        session's STATIC fields (name, type, total length, track). Returns
        None if the session block isn't available yet (so we don't cache it)."""
        info    = ir["SessionInfo"] or {}
        weekend = ir["WeekendInfo"] or {}
        cur = None
        for s in info.get("Sessions", []) or []:
            if s.get("SessionNum") == sess_num:
                cur = s
                break
        if cur is None:
            return None
        session_name = (cur.get("SessionName") or "") or (cur.get("SessionType") or "")
        session_type = (cur.get("SessionType") or "")
        total_laps = None
        total_seconds = None
        is_lap_based = False
        raw_laps = str(cur.get("SessionLaps", "")).strip().lower()
        if raw_laps and raw_laps != "unlimited":
            try:
                total_laps = int(raw_laps)
                is_lap_based = total_laps > 0
            except ValueError:
                total_laps = None
        raw_time = str(cur.get("SessionTime", "")).strip()
        if raw_time and "unlimited" not in raw_time.lower():
            try:
                total_seconds = float(raw_time.split()[0])
            except (ValueError, IndexError):
                total_seconds = None
        return {
            "session_name":  session_name,
            "session_type":  session_type,
            "is_lap_based":  is_lap_based,
            "total_seconds": total_seconds,
            "total_laps":    total_laps,
            "track":         (weekend.get("TrackDisplayName") or "") or
                             (weekend.get("TrackName") or ""),
        }

    def _flag(self, ir, key) -> str:
        """green / yellow / red / white / checkered / pre for the clock bar.
        ONLY 0x4000 / 0x8000 are a full course caution (0x08 / 0x100 are
        local yellows - iCAS 0.1.8 lesson); the one-to-green lap belongs to
        it, but its bit also flies on the pace lap and a tick after the green
        (iCAS 0.1.38) - so it only counts after a caution was seen."""
        try:
            flags = int(ir["SessionFlags"] or 0)
            state = int(ir["SessionState"] or 0)
        except Exception:
            return "green"
        if key != self._caution_key:
            self._caution_key, self._caution_seen = key, False
        caution = bool(flags & 0xC000)
        if caution:
            self._caution_seen = True
        elif not flags & 0x0200:
            self._caution_seen = False       # back to green: caution over
        if flags & 0x0010:
            return "red"
        if state >= 5 or flags & 0x0001:
            return "checkered"
        if caution or (flags & 0x0200 and self._caution_seen):
            return "yellow"
        if state == 3:
            return "yellow"                  # pace laps: pace car out
        if 0 < state < 3:
            return "pre"                     # get in car / warmup on the grid
        if flags & 0x0002:
            return "white"
        return "green"

    def _read_snapshot(self) -> dict:
        ir = self.ir
        sess_num = ir["SessionNum"]   # cheap telemetry int

        # Parse the (GROWING) SessionInfo YAML only when the session actually
        # changes. Re-parsing it every poll is CPU/GIL-heavy, and as the YAML
        # accumulates race results it eventually starves the web handler — the
        # card would blank a few minutes into a race. Name/total-length are
        # static within a session, so one parse per session is enough.
        key = (ir["SessionUniqueID"], sess_num)
        if key != self._cache_key or self._cache_static is None:
            static = self._parse_static(ir, sess_num)
            if static is not None:
                self._cache_static = static
                self._cache_key = key

        if self._cache_static is None:
            return {
                "connected": True, "session_name": "", "session_type": "",
                "is_lap_based": False, "total_seconds": None, "total_laps": None,
                "remain_seconds": None, "remain_laps": None, "track": "",
            }

        # Cheap per-poll telemetry (NOT the YAML) — the live countdown.
        remain_seconds = ir["SessionTimeRemain"]
        if remain_seconds is None or remain_seconds > 1e6:
            remain_seconds = None
        remain_laps = ir["SessionLapsRemain"]
        if remain_laps is None or remain_laps > 9000 or remain_laps < 0:
            remain_laps = None

        out = dict(self._cache_static)
        out["connected"] = True
        out["flag"] = self._flag(ir, key)
        out["remain_seconds"] = float(remain_seconds) if remain_seconds is not None else None
        out["remain_laps"] = int(remain_laps) if remain_laps is not None else None
        return out


# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
poller = SessionInfoPoller(poll_hz=4)
event = wct_board.EventInfo()       # league from 5010 (logo on /clock)


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
        /* nearly solid — same readability pass as the driver card */
        background: rgba(20, 20, 28, 0.97);
        border: 2px solid rgba(255, 107, 53, 0.6);
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
        color: #d4d4de;
        font-weight: 700;
        letter-spacing: 1px;
        text-transform: uppercase;
        font-size: 0.8em;
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

// Keep the last good card on screen until there has been NO good response
// for a sustained period. Time-based (not poll-count) so it's robust both to
// the dev server stalling for a few seconds AND to OBS throttling this page's
// timer. The card only blanks after OFFLINE_AFTER_MS of continuous trouble.
let lastGood = Date.now();
// 30s: OBS throttles a background browser source's JS timers (you race with
// OBS behind iRacing), occasionally pausing the poll loop for several seconds.
// A wide window means such a pause holds the last card instead of blanking it.
const OFFLINE_AFTER_MS = 30000;

function showOffline() {
    document.getElementById('card').classList.add('offline');
    document.getElementById('session-name').textContent = '—';
    document.getElementById('total').textContent  = '—';
    document.getElementById('remain').textContent = '—';
}

async function getStatus() {
    // Hard timeout so a hung request can't occupy a browser connection slot
    // (only ~6 per host — without this they pile up over minutes, exhaust the
    // pool, and every fetch stalls -> the card blanks).
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 4000);
    try { const r = await fetch('/status', { signal: ctrl.signal, cache: 'no-store' }); return await r.json(); }
    catch (e) { return null; }
    finally { clearTimeout(to); }
}

async function tick() {
    const d = await getStatus();

    if (!d || !d.connected || !d.session_name) {
        // No good data this tick — hold the last card unless it's been too long.
        if (Date.now() - lastGood < OFFLINE_AFTER_MS) return;
        showOffline();
        return;
    }
    lastGood = Date.now();

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
// Self-scheduling loop: never start a new request until the previous one has
// finished, so requests can never accumulate.
(function loop() { tick().finally(() => setTimeout(loop, 500)); })();
</script>
</body>
</html>
"""


CLOCK_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>Session Clock</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 100%; height: 100%; background: transparent; overflow: hidden; }
  body.debug { background: #3a4a5a; }
  body { display: flex; align-items: center; justify-content: center;
         font-family: 'Segoe UI', 'Arial Narrow', system-ui, sans-serif; color: #fff; }
  .clock {
    --s: 300px;
    width: var(--s); height: var(--s); border-radius: 50%;
    position: relative; user-select: none;
    /* dark face, slightly lighter top left like the glass of the picture */
    background:
      radial-gradient(circle at 35% 25%, rgba(255,255,255,.07), transparent 55%),
      radial-gradient(circle at 50% 50%, #15171c 0%, #0b0c10 70%, #050608 100%);
    box-shadow:
      0 0 0 calc(var(--s) * .022) #2a2d34,          /* rim */
      0 0 0 calc(var(--s) * .030) #0d0e12,
      inset 0 0 calc(var(--s) * .08) rgba(0,0,0,.9),
      0 calc(var(--s) * .02) calc(var(--s) * .09) rgba(0,0,0,.6);
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: calc(var(--s) * .028);
    padding: calc(var(--s) * .12);
  }
  .clock::after {                                    /* glass highlight */
    content: ""; position: absolute; inset: 4%; border-radius: 50%;
    background: linear-gradient(160deg, rgba(255,255,255,.08), transparent 38%);
    pointer-events: none;
  }
  .logo { height: calc(var(--s) * .25); display: flex; align-items: center; justify-content: center;
          width: 78%; }
  .logo img { max-width: 100%; max-height: 100%; object-fit: contain; display: none;
              filter: drop-shadow(0 1px 3px rgba(0,0,0,.6)); }
  .logo.ok img { display: block; }
  .logo .txt { font-weight: 800; font-size: calc(var(--s) * .07); letter-spacing: .04em;
               text-align: center; line-height: 1.05; text-transform: uppercase; }
  .logo.ok .txt { display: none; }
  .sess { font-weight: 700; font-size: calc(var(--s) * .072); letter-spacing: .05em;
          text-transform: uppercase; text-align: center; line-height: 1.1; color: #f2f2f2;
          max-width: 84%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
          text-shadow: 0 1px 2px rgba(0,0,0,.8); }
  .time { font-weight: 700; font-size: calc(var(--s) * .17); line-height: 1;
          font-variant-numeric: tabular-nums; letter-spacing: .03em; color: #fff;
          text-shadow: 0 0 calc(var(--s) * .02) rgba(255,255,255,.25); }
  .time.laps { font-size: calc(var(--s) * .13); }
  .bar { width: 46%; height: calc(var(--s) * .062); border-radius: calc(var(--s) * .008);
         background: #555; transition: background .3s; }
  .bar.green  { background: #1fc65a; box-shadow: 0 0 calc(var(--s)*.03) rgba(31,198,90,.55); }
  .bar.yellow { background: #ffd200; box-shadow: 0 0 calc(var(--s)*.03) rgba(255,210,0,.55); }
  .bar.red    { background: #ff2a2a; box-shadow: 0 0 calc(var(--s)*.03) rgba(255,42,42,.55); }
  .bar.white  { background: #ffffff; box-shadow: 0 0 calc(var(--s)*.03) rgba(255,255,255,.5); }
  .bar.pre    { background: #4a4d55; }
  .bar.checkered {
    background: repeating-conic-gradient(#fff 0 25%, #111 0 50%) 0 0 /
                calc(var(--s) * .031) calc(var(--s) * .031);
  }
  .bar.hide { visibility: hidden; }
  .clock.offline .time, .clock.offline .sess { color: #50535c; text-shadow: none; }
</style>
</head>
<body>
<div class="clock offline" id="clock">
  <div class="logo" id="logo"><img id="logoimg" alt=""><div class="txt" id="logotxt"></div></div>
  <div class="sess" id="sess">—</div>
  <div class="time" id="time">--:--</div>
  <div class="bar pre" id="bar"></div>
</div>
<script>
const Q = new URLSearchParams(location.search);
const clock = document.getElementById('clock');
if (Q.get('size')) clock.style.setProperty('--s', (+Q.get('size') || 300) + 'px');
if (Q.get('debug') === '1') document.body.classList.add('debug');
document.addEventListener('keydown', e => { if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug'); });
const showFlag = Q.get('flag') !== '0';
const logoQ = Q.get('logo');
const img = document.getElementById('logoimg');
img.onload = () => document.getElementById('logo').classList.add('ok');
img.onerror = () => document.getElementById('logo').classList.remove('ok');
let logoSrc = null;
function setLogo(url, name) {
  document.getElementById('logotxt').textContent = name || '';
  if (logoQ === '0') url = '';
  else if (logoQ) url = logoQ;
  if (url === logoSrc) return;
  logoSrc = url;
  document.getElementById('logo').classList.remove('ok');
  if (url) img.src = url; else img.removeAttribute('src');
}
function fmtTime(sec) {
  if (sec == null) return null;
  const t = Math.max(0, Math.floor(sec));
  const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = t % 60;
  const p = n => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}
function render(d) {
  clock.classList.remove('offline');
  const lg = d.league || {};
  setLogo(lg.slug ? 'https://league.simracing-hub.com/logos/' + lg.slug + '.webp' : '', lg.league || '');
  document.getElementById('sess').textContent = (d.session_name || d.session_type || '').toUpperCase() || '—';
  const tm = document.getElementById('time');
  const lapView = d.total_seconds == null && (d.total_laps != null || d.remain_laps != null);
  if (lapView) {
    tm.classList.add('laps');
    tm.textContent = d.remain_laps != null ? d.remain_laps + (d.remain_laps === 1 ? ' LAP' : ' LAPS') : '—';
  } else {
    tm.classList.remove('laps');
    tm.textContent = fmtTime(d.remain_seconds) || '--:--';
  }
  document.getElementById('bar').className = 'bar ' + (showFlag ? (d.flag || 'green') : 'hide');
}
let lastGood = Date.now();
function offline() {
  clock.classList.add('offline');
  document.getElementById('time').textContent = '--:--';
  document.getElementById('bar').className = 'bar ' + (showFlag ? 'pre' : 'hide');
}
async function getStatus() {
  const c = new AbortController(); const to = setTimeout(() => c.abort(), 4000);
  try { const r = await fetch('/status', {signal: c.signal, cache: 'no-store'}); return await r.json(); }
  catch (e) { return null; } finally { clearTimeout(to); }
}
async function tick() {
  const d = await getStatus();
  if (!d || !d.connected || !d.session_name) {
    if (Date.now() - lastGood > 30000) offline();
    if (d && d.league) setLogo(d.league.slug ? 'https://league.simracing-hub.com/logos/' + d.league.slug + '.webp' : '', d.league.league);
    return;
  }
  lastGood = Date.now();
  render(d);
}
if (Q.get('demo') === '1') {
  const flags = ['green', 'yellow', 'green', 'white', 'checkered', 'red'];
  let t = 8 * 3600 + 19 * 60 + 47, i = 0;
  const slug = Q.get('league') || 'cas-gt3-wct';
  setInterval(() => { t--; if (t % 4 === 0) i++;
    render({session_name: 'Race', total_seconds: 86400, remain_seconds: t,
            flag: flags[i % flags.length], league: {slug: slug, league: 'CAS GT3 WCT'}}); }, 1000);
} else {
  (function loop() { tick().finally(() => setTimeout(loop, 500)); })();
}
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE_HTML)


@app.route("/clock")
def clock():
    return Response(CLOCK_HTML, mimetype="text/html")


@app.route("/status")
def status():
    d = dict(poller.get())
    d["league"] = dict(event.data)
    return jsonify(d)


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()
    threading.Thread(target=event.run, daemon=True).start()

    print("\n" + "=" * 60)
    print("  iRacing Session Info Overlay")
    print("  Open in browser:  http://localhost:5011")
    print("  Race clock:       http://localhost:5011/clock")
    print("  Transparent background — designed as an OBS browser source.")
    print("  Shows the active session name + total / remaining time.")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=5011, debug=False,
                use_reloader=False, threaded=True)
    finally:
        poller.stop()
