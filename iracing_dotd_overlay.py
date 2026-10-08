"""
iRacing Driver of the Day Overlay  (port 5013, tag "dotd")
----------------------------------------------------------
A standalone OBS overlay that nominates the "Driver of the Day" from the
most recent race log written by iracing_race_logger.py.

Unlike the other overlays it does NOT read the iRacing SDK — it watches the
./logs folder, picks the newest *_race.jsonl, and recomputes the DotD via
driver_of_the_day.analyze() whenever the file changes. So it lights up the
moment the logger writes the final classification (session_end), and keeps
showing the result through the cool-down / replay / debrief.

The winner is deliberately NOT the race winner by default — it rewards the
best comeback drive (positions gained, recovery, overtakes) while punishing
incidents. See driver_of_the_day.py for the scoring.

Requirements:  pip install flask          (no SDK, no other deps)
Run:           python iracing_dotd_overlay.py
Open:          http://localhost:5013
Options:       ?profile=positions|balanced|recovery|clean   (default positions)
               ?log=<path>        pin to a specific log instead of the newest
               ?provisional=0     hide the mid-race ranking (show only finals)

PROVISIONAL (2026-10-08): while the newest race is still running, the card
shows a provisional Driver of the Day — the race so far scored as if it
ended at the last lap crossing (driver_of_the_day.provisional_final). It is
labelled "Provisional · after lap N", computed in memory only and never
written to dotd_history.json, so the official award at session_end and the
no-back-to-back rule are untouched.
Stream:        transparent background by default; press H for a debug panel.
"""

import glob
import json
import os
import threading
import time
import urllib.parse
import urllib.request

from flask import Flask, jsonify, render_template_string, request

import driver_of_the_day as dotd
import dotd_streak

dotd.setup_utf8_stdout()

LOGS_DIR = "logs"
LIVE_TTL = 5.0   # s — max age of a cached in-progress (provisional) result
DEFAULT_PROFILE = dotd.DEFAULT_PROFILE

app = Flask(__name__)

# Cache so we only re-parse a log when it changes on disk.
_cache = {"path": None, "mtime": 0, "profile": None, "result": None}
_lock = threading.Lock()


@app.after_request
def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# ---------------------------------------------------------------------------
# Multi-race rounds + the back-to-back rule, the CLS way (2026-10-08, Algarve)
# ---------------------------------------------------------------------------
# CLS crowns ONE Driver of the Day per ROUND: on two-race rounds (PCCD, SFL,
# Combined Cup) both races are combined (driver_of_the_day.analyze_round).
# The overlay used to score only the race on screen, and blocked the local
# history's previous winner — which records a winner per RACE (and test
# races) — so during race 2 it blocked the race-1 winner instead of the
# previous ROUND's. Both now follow CLS.

def _log_head(path):
    """session_start of a log (first line), or {}."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    e = json.loads(line)
                    return e if e.get("type") == "session_start" else {}
    except Exception:
        pass
    return {}


def _has_end(path):
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - 200000))
            return b'"session_end"' in f.read()
    except Exception:
        return False


def round_logs(path):
    """All race logs of the round `path` belongs to, in race order: earlier
    FINISHED races of the same hosted session (same session_unique_id, lower
    session_num) + `path` itself."""
    head = _log_head(path)
    uid, num = head.get("session_unique_id"), head.get("session_num")
    if uid is None or num is None:
        return [path]
    sib = []
    for p in glob.glob(os.path.join(os.path.dirname(path) or ".", "*_race.jsonl")):
        if os.path.abspath(p) == os.path.abspath(path):
            continue
        h = _log_head(p)
        if (h.get("session_unique_id") == uid and isinstance(h.get("session_num"), int)
                and h["session_num"] < num and _has_end(p)):
            sib.append((h["session_num"], p))
    # one log per earlier session (a re-started logger can write two)
    seen, out = set(), []
    for n_, p in sorted(sib):
        if n_ not in seen:
            seen.add(n_)
            out.append(p)
    return out + [path]


_cls_cache = {"t": 0.0, "data": None}


def cls_previous_winner(log_date):
    """The previous ROUND's official Driver of the Day from CLS (the overlay
    feed /api/overlay/round-info). If CLS has already published the round
    raced on `log_date`, it is that round's previous winner. None when CLS
    is unreachable (then the local history is used)."""
    now = time.time()
    if now - _cls_cache["t"] > 300 or _cls_cache["data"] is None:
        try:
            cfg = dotd_streak._read_champ_config()
            base = (cfg.get("api_base") or dotd_streak.DEFAULT_API).rstrip("/")
            params = {"league": cfg.get("league_slug") or dotd_streak.DEFAULT_LEAGUE_SLUG}
            if cfg.get("season_id"):
                params["season"] = cfg["season_id"]
            url = base + "/api/overlay/round-info?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={"User-Agent": "dotd-overlay/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                _cls_cache["data"] = json.load(r)
            _cls_cache["t"] = now
        except Exception as e:
            print(f"[dotd] CLS round-info failed: {e}")
            _cls_cache["t"] = now - 240       # retry in ~1 min
            return None
    lr = (_cls_cache["data"] or {}).get("lastRound") or {}
    dd = lr.get("driverOfTheDay")
    if not dd:
        return None
    same_round = (lr.get("startsAt") or "")[:10] == (log_date or "")[:10]
    return dd.get("previousWinnerName") if same_round else dd.get("winnerName")


def _compute_round(paths, profile, provisional):
    head = _log_head(paths[-1])
    prev = cls_previous_winner(head.get("t_wall"))
    source = "cls"
    if prev is None:
        hist = dotd_streak.load_history(dotd_streak.HISTORY_PATH)
        season = dotd_streak.resolve_season()
        prev = dotd_streak.previous_winner_name(hist, season["key"], current_log=os.path.basename(paths[-1]))
        source = "history"
    events = [dotd.load_events(p) for p in paths]
    if len(paths) > 1:
        result = dotd.analyze_round(events, profile=profile, exclude_names=[prev] if prev else [],
                                    provisional=provisional)
    else:
        result = dotd.analyze(events[0], profile=profile, exclude_names=[prev] if prev else [],
                              provisional=provisional)
    result["season"] = dotd_streak.resolve_season()
    result["previous_winner"] = prev
    result["previous_winner_source"] = source
    result["round_logs"] = [os.path.basename(p) for p in paths]
    return result


def _compute(profile, log=None, provisional=True):
    path = log or dotd.newest_race_log(LOGS_DIR)
    if not path or not os.path.exists(path):
        return {"ok": False, "error": "no race log found yet", "drivers": []}
    try:
        st = os.stat(path)
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        mtime, size = 0, 0
    # the streak rule depends on the winner-history file too, so the cache
    # key includes its mtime — when the logger records a new winner the
    # overlay picks it up.
    try:
        hist_mtime = os.path.getmtime(dotd_streak.HISTORY_PATH)
    except OSError:
        hist_mtime = 0
    key = (path, mtime, size, hist_mtime, profile, provisional)
    with _lock:
        # A race still in progress is recomputed at least every LIVE_TTL s
        # even when the key looks unchanged: on Windows, a log the logger
        # still holds open — read over SMB from the Unraid share — can keep
        # reporting its OLD mtime, which froze the card on "no lap completed
        # yet" for a whole race (2026-10-08, Oulton Park). Size is in the
        # key for the same reason.
        res = _cache.get("result")
        live = bool(res) and (res.get("provisional") or not res.get("ok"))
        fresh = time.time() - _cache.get("t", 0) < LIVE_TTL
        if _cache.get("key") == key and res and (not live or fresh):
            return res
        # read-only: the overlay applies the no-back-to-back rule but never
        # records (the race logger is the authoritative recorder).
        result = _compute_round(round_logs(path), profile, provisional)
        result["log_file"] = os.path.basename(path)
        _cache.update(key=key, result=result, t=time.time())
        return result


@app.route("/data")
def data():
    profile = request.args.get("profile", DEFAULT_PROFILE)
    if profile not in dotd.WEIGHT_PROFILES:
        profile = DEFAULT_PROFILE
    log = request.args.get("log")
    provisional = request.args.get("provisional", "1") != "0"
    return jsonify(_compute(profile, log, provisional))


PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Driver of the Day</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    width: 100%; height: 100%; background: transparent; overflow: hidden;
    font-family: 'Rajdhani','Segoe UI',system-ui,sans-serif; color: #fff;
  }
  body { display: flex; align-items: center; justify-content: center; padding: 18px; }
  body.debug { background: #14141c; }

  .card {
    position: relative; min-width: 420px; max-width: 560px;
    padding: 22px 26px 20px; border-radius: 16px;
    background: linear-gradient(155deg, rgba(24,24,34,0.94), rgba(14,14,20,0.94));
    border: 2px solid rgba(255,107,53,0.55);
    box-shadow: 0 10px 44px rgba(0,0,0,0.6);
    user-select: none;
  }
  .card.empty { border-color: rgba(255,255,255,0.10); opacity: 0.92; }

  .eyebrow {
    display: flex; align-items: center; gap: 10px;
    font-size: 15px; font-weight: 700; letter-spacing: 3px; text-transform: uppercase;
    color: #ff6b35;
  }
  .eyebrow .trophy { font-size: 20px; }
  .eyebrow .spacer { flex: 1; height: 2px;
    background: linear-gradient(90deg, rgba(255,107,53,0.8), rgba(255,107,53,0)); }

  .name-row { display: flex; align-items: baseline; gap: 12px; margin-top: 8px; }
  .car-no {
    font-weight: 800; font-size: 30px; color: #0a0a0f;
    background: #ffd166; border-radius: 8px; padding: 1px 11px; line-height: 1.25;
  }
  .name { font-size: 40px; font-weight: 800; letter-spacing: 1px; line-height: 1.05; }
  .car-name { font-size: 16px; color: #b0b0c0; margin-top: 2px; letter-spacing: 1px; }

  .why { margin-top: 12px; font-size: 18px; font-weight: 600; color: #eaeaf2; }

  .metrics { display: flex; gap: 10px; margin-top: 14px; }
  .metric {
    flex: 1; background: rgba(255,255,255,0.05); border-radius: 10px;
    padding: 8px 6px 9px; text-align: center;
  }
  .metric .v { font-size: 26px; font-weight: 800; font-variant-numeric: tabular-nums; }
  .metric .l { font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: #9a9aaa; margin-top: 2px; }
  .metric.gain .v { color: #4ade80; }
  .metric.rec  .v { color: #38bdf8; }
  .metric.ot   .v { color: #ffd166; }
  .metric.inc  .v { color: #f87171; }

  .runners { margin-top: 14px; border-top: 1px solid rgba(255,255,255,0.10); padding-top: 10px; }
  .runners .h { font-size: 11px; letter-spacing: 2px; text-transform: uppercase; color: #8a8a9a; margin-bottom: 6px; }
  .runner { display: flex; align-items: baseline; gap: 8px; font-size: 15px; padding: 2px 0; color: #c8c8d4; }
  .runner .rk { color: #6a6a7a; width: 16px; }
  .runner .rn { color: #ffd166; font-weight: 700; }
  .runner .rs { margin-left: auto; color: #8a8a9a; font-variant-numeric: tabular-nums; }

  .footer { margin-top: 12px; font-size: 11px; letter-spacing: 1px; color: #6a6a7a;
            display: flex; justify-content: space-between; }

  .card.prov { border-color: rgba(255,209,102,0.55); }
  .prov-tag { font-size: 12px; letter-spacing: 2px; color: #0a0a0f; background: #ffd166;
              border-radius: 4px; padding: 1px 7px; }
  .empty-msg { font-size: 22px; font-weight: 700; color: #6a6a7a; text-align: center; padding: 18px 6px; }

  .note { margin-top: 10px; font-size: 13px; color: #9a9aaa; font-style: italic;
          border-left: 3px solid rgba(255,107,53,0.55); padding-left: 8px; }
</style>
</head>
<body>
<div id="root"></div>
<script>
const params = new URLSearchParams(location.search);
const profile = params.get('profile') || 'positions';
const logParam = params.get('log');
const provParam = params.get('provisional');

function metricCard(cls, val, label) {
  return `<div class="metric ${cls}"><div class="v">${val}</div><div class="l">${label}</div></div>`;
}

function render(d) {
  const root = document.getElementById('root');
  if (!d || !d.ok || !d.winner) {
    const msg = (d && d.error) ? d.error : 'Waiting for the race to finish…';
    root.innerHTML = `<div class="card empty">
        <div class="eyebrow"><span class="trophy">🏆</span> Driver of the Day <span class="spacer"></span></div>
        <div class="empty-msg">${msg}</div></div>`;
    return;
  }
  const w = d.winner;
  const gained = (w.positions_gained >= 0 ? '+' : '') + w.positions_gained;
  const runners = (d.drivers || []).filter(x => x.eligible && x.car_idx !== w.car_idx).slice(0, 3);
  const runnersHtml = runners.length ? `<div class="runners">
      <div class="h">Also outstanding</div>
      ${runners.map((r,i) => `<div class="runner"><span class="rk">${i+2}</span>
        <span class="rn">#${r.car_number}</span><span>${r.name}</span>
        <span class="rs">${r.score.toFixed(3)}</span></div>`).join('')}
    </div>` : '';
  const trackline = [d.track, d.track_config].filter(Boolean).join(' ');
  const noteHtml = d.previous_winner
    ? `<div class="note">${d.previous_winner} won the previous round — not eligible for back-to-back</div>`
    : '';
  const seasonName = (d.season && d.season.name) ? d.season.name : '';
  const footerLeft = [trackline, seasonName].filter(Boolean).join('  ·  ');
  const prov = !!d.provisional;
  const nRaces = (d.round_logs || []).length;
  const provTag = prov
    ? `<span class="prov-tag">PROVISIONAL · ${nRaces > 1 ? `RACE ${nRaces}, ` : ''}LAP ${(d.meta && d.meta.leader_laps) || '–'}</span>`
    : (nRaces > 1 ? `<span class="prov-tag" style="background:#4ade80">ROUND · ${nRaces} RACES</span>` : '');

  root.innerHTML = `<div class="card${prov ? ' prov' : ''}">
    <div class="eyebrow"><span class="trophy">🏆</span> Driver of the Day <span class="spacer"></span>${provTag}</div>
    <div class="name-row"><span class="car-no">#${w.car_number}</span><span class="name">${w.name}</span></div>
    <div class="car-name">${w.car || ''}</div>
    <div class="why">${w.why}</div>
    <div class="metrics">
      ${metricCard('gain', gained, 'Positions')}
      ${metricCard('rec', w.recovery, 'Recovery')}
      ${metricCard('ot', w.overtakes, 'Overtakes')}
      ${metricCard('inc', w.incidents, 'Incidents')}
    </div>
    ${runnersHtml}
    ${noteHtml}
    <div class="footer"><span>${footerLeft}</span><span>score ${w.score.toFixed(3)} · ${profile}</span></div>
  </div>`;
}

async function tick() {
  try {
    let url = '/data?profile=' + encodeURIComponent(profile);
    if (logParam) url += '&log=' + encodeURIComponent(logParam);
    if (provParam) url += '&provisional=' + encodeURIComponent(provParam);
    const r = await fetch(url);
    render(await r.json());
  } catch (e) { /* keep last render */ }
}

document.addEventListener('keydown', e => {
  if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug');
});

setInterval(tick, 3000);
tick();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE_HTML)


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  iRacing Driver of the Day Overlay")
    print("  Open in browser:  http://localhost:5013")
    print("  Reads the newest logs/*_race.jsonl (no SDK needed).")
    print("  Mid-race: provisional ranking (?provisional=0 to hide);")
    print("  the official winner once the race has a final classification.")
    print("  Profiles: ?profile=positions|balanced|recovery|clean")
    print("  Press H in the browser for a debug background.")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")
    app.run(host="0.0.0.0", port=5013, debug=False, use_reloader=False)
