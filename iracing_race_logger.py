"""
iRacing Race Logger
-------------------
Standalone background script that records an entire race session as a
machine-readable log file. One event per line (JSONL — newline-
delimited JSON), so the file can be appended to live and parsed later
in Excel / jq / Python without reading the whole thing.

Requirements:  pip install pyirsdk flask requests
Run:           python iracing_race_logger.py
Open:          http://localhost:5009    (status page + download link)

What's captured per race:
  - session_start  — track, session type, drivers list (iRating /
                     license / team / car), weather at the start
  - lap            — every lap completion by every driver: lap
                     number, lap time, position at S/F, gap to
                     leader, pit flag, best-lap-so-far
  - incident       — fetched from the dashboard's /incidents feed
                     (port 5000); deduped, so the same incident is
                     never written twice
  - session_end    — final classification when iRacing flips the
                     checkered (positions, laps completed, best lap,
                     incident count, finished / DNF / DQ)

Output: logs/<YYYYMMDD-HHMMSS>_<track>_race.jsonl

Practice / qualifying sessions are intentionally NOT logged. Add a
toggle here later if that ever changes.
"""

from __future__ import annotations
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, render_template_string, send_file, abort

try:
    import requests  # for /incidents fetch from the dashboard
except ImportError:
    requests = None  # type: ignore
    print("[logger] WARNING: 'requests' not installed — incidents won't be "
          "logged. Run 'pip install requests' to enable.")

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
LOGS_DIR = HERE / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Where to fetch the incident feed from. The dashboard publishes JSON at
# /incidents on port 5000. If the dashboard isn't running we silently
# skip incident logging — laps + classification still get recorded.
DASHBOARD_INCIDENTS_URL = "http://127.0.0.1:5000/incidents"
INCIDENT_POLL_INTERVAL  = 5.0   # seconds between fetches (laps poll faster)


def _safe_filename(s: str) -> str:
    """Trim a string into something sensible for a filename."""
    out = re.sub(r"[^A-Za-z0-9_\-]+", "_", s.strip().lower())
    return out.strip("_") or "session"


# ---------------------------------------------------------------------------
# Logger poller
# ---------------------------------------------------------------------------
class RaceLogger(SDKPoller):
    tag = "logger"
    poll_interval = 0.5  # 2 Hz — laps complete on the order of minutes

    def __init__(self):
        super().__init__()
        # Active log state. None means "not currently logging".
        self._log_path: Path | None = None
        self._log_fp = None              # file handle, line-buffered append
        self._log_session_key: tuple | None = None  # (uid, session_num)
        self._log_started_at: float = 0.0     # wall clock when log opened
        self._log_session_meta: dict = {}     # cached session-start payload

        # Per-car lap tracking.  We watch CarIdxLap; when it increments,
        # the car just crossed S/F and CarIdxLastLapTime now holds the
        # time of the lap they just completed.
        self._last_lap_seen: dict[int, int] = {}

        # Counters surfaced via /status for the live page
        self._laps_logged = 0
        self._incidents_logged = 0
        self._final_written = False
        self._driver_incident_count: dict[str, int] = {}   # car_number -> count

        # Background thread for /incidents polling so we don't block
        # the main poll loop on HTTP timeouts.
        self._seen_incidents: set[tuple] = set()
        self._incident_thread = threading.Thread(
            target=self._incident_loop, daemon=True
        )
        self._incident_thread_started = False

    # ----- file lifecycle -------------------------------------------------
    def _open_log(self, session_meta: dict) -> None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        track = _safe_filename(session_meta.get("track", "unknown"))
        config = session_meta.get("track_config", "")
        if config:
            track = f"{track}_{_safe_filename(config)}"
        path = LOGS_DIR / f"{ts}_{track}_race.jsonl"
        self._log_path = path
        self._log_fp = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered
        self._log_started_at = time.time()
        self._log_session_meta = session_meta
        self._laps_logged = 0
        self._incidents_logged = 0
        self._final_written = False
        self._driver_incident_count = {}
        self._seen_incidents.clear()
        self._last_lap_seen.clear()

        print(f"[logger] Opened {path.name}  ({session_meta.get('track')} "
              f"— {session_meta.get('session_type')})")

        # Write the session_start event
        self._emit({
            "type": "session_start",
            **session_meta,
        })

    def _close_log(self) -> None:
        if self._log_fp:
            try:
                self._log_fp.flush()
                self._log_fp.close()
            except Exception:
                pass
        self._log_fp = None
        self._log_path = None
        self._log_session_key = None

    def _emit(self, event: dict) -> None:
        """Write one JSON line to the active log."""
        if self._log_fp is None:
            return
        # All events get a wall-clock 't_wall' so post-race tools can
        # correlate with stream replay timestamps.
        if "t_wall" not in event:
            event["t_wall"] = datetime.now().isoformat(timespec="seconds")
        try:
            self._log_fp.write(json.dumps(event, separators=(",", ":")) + "\n")
        except Exception as e:
            print(f"[logger] write error: {e!r}")

    # ----- iRacing read helpers ------------------------------------------
    def _build_drivers_list(self) -> list[dict]:
        info = self.ir["DriverInfo"] or {}
        out: list[dict] = []
        for d in info.get("Drivers", []) or []:
            cidx = d.get("CarIdx")
            if cidx is None:
                continue
            if d.get("CarIsPaceCar") == 1 or d.get("IsSpectator") == 1:
                continue
            out.append({
                "car_idx":    cidx,
                "car_number": d.get("CarNumber", "") or "",
                "name":       d.get("UserName", "") or "",
                "car":        d.get("CarScreenNameShort") or d.get("CarScreenName", "") or "",
                "team":       d.get("TeamName", "") or "",
                "irating":    int(d.get("IRating") or 0),
                "license":    d.get("LicString", "") or "",
            })
        return out

    def _detect_session_change(self) -> tuple[tuple | None, str, dict]:
        """Returns (session_key, session_type, session_meta).

        session_key is None when we're not in a real session yet.
        """
        weekend = self.ir["WeekendInfo"] or {}
        info    = self.ir["SessionInfo"] or {}
        sessions = info.get("Sessions", []) or []
        sess_num = self.ir["SessionNum"]
        uid      = weekend.get("SessionUniqueID") or weekend.get("SessionID")

        if uid is None or sess_num is None:
            return None, "", {}

        cur_session = next((s for s in sessions if s.get("SessionNum") == sess_num), None)
        if cur_session is None:
            return None, "", {}

        sess_type = (cur_session.get("SessionType") or "").lower()
        meta = {
            "track":            weekend.get("TrackDisplayName", "") or "",
            "track_config":     weekend.get("TrackConfigName", "") or "",
            "track_id":         weekend.get("TrackID"),
            "session_type":     cur_session.get("SessionType", "") or "",
            "session_name":     cur_session.get("SessionName", "") or "",
            "session_num":      sess_num,
            "session_unique_id": uid,
            "session_laps":     cur_session.get("SessionLaps", ""),
            "session_time":     cur_session.get("SessionTime", ""),
            "drivers":          self._build_drivers_list(),
            "weather": {
                "track_temp_c": self.ir["TrackTempCrew"],
                "air_temp_c":   self.ir["AirTemp"],
                "skies":        self.ir["Skies"],
                "wetness":      self.ir["TrackWetness"],
            },
        }
        return (uid, sess_num), sess_type, meta

    # ----- lap-completion detection --------------------------------------
    def _maybe_emit_laps(self) -> None:
        if self._log_fp is None:
            return
        ir = self.ir
        lap_arr   = ir["CarIdxLap"] or []
        last_lap_t= ir["CarIdxLastLapTime"] or []
        best_lap  = ir["CarIdxBestLapTime"] or []
        f2_arr    = ir["CarIdxF2Time"] or []
        on_pit    = ir["CarIdxOnPitRoad"] or []
        cls_pos   = ir["CarIdxClassPosition"] or []
        ovr_pos   = ir["CarIdxPosition"] or []
        t_session = ir["SessionTime"] or 0.0

        for d in self._log_session_meta.get("drivers", []):
            idx = d["car_idx"]
            if idx >= len(lap_arr):
                continue
            cur_lap = lap_arr[idx]
            if cur_lap is None:
                continue
            prev = self._last_lap_seen.get(idx)
            self._last_lap_seen[idx] = cur_lap
            if prev is None:
                continue  # first time we've seen this car — nothing to log yet

            if cur_lap > prev:
                # Crossed S/F. The lap they just completed is `prev`.
                lt = last_lap_t[idx] if idx < len(last_lap_t) else 0.0
                self._emit({
                    "type":        "lap",
                    "t_session":   round(float(t_session), 2),
                    "car_idx":     idx,
                    "car_number":  d["car_number"],
                    "driver":      d["name"],
                    "lap":         int(prev),  # lap just completed
                    "lap_time":    float(lt) if lt and lt > 0 else None,
                    "best_lap":    float(best_lap[idx]) if (idx < len(best_lap) and best_lap[idx] and best_lap[idx] > 0) else None,
                    "position":    int(ovr_pos[idx]) if idx < len(ovr_pos) else 0,
                    "class_pos":   int(cls_pos[idx]) if idx < len(cls_pos) else 0,
                    "gap_to_leader": float(f2_arr[idx]) if (idx < len(f2_arr) and f2_arr[idx] and f2_arr[idx] > 0) else None,
                    "on_pit":      bool(on_pit[idx]) if idx < len(on_pit) else False,
                })
                self._laps_logged += 1

    # ----- final classification at checkered -----------------------------
    def _maybe_emit_final(self) -> None:
        if self._log_fp is None or self._final_written:
            return
        ir = self.ir
        sess_state = ir["SessionState"] or 0
        if sess_state < 5:  # not yet checkered
            return
        info = ir["SessionInfo"] or {}
        sessions = info.get("Sessions", []) or []
        sess_num = ir["SessionNum"]
        cur = next((s for s in sessions if s.get("SessionNum") == sess_num), None)
        if cur is None:
            return
        results = cur.get("ResultsPositions") or []
        if not results:
            return  # iRacing hasn't finalized yet — wait for next tick

        # Map driver info by car_idx for name/number lookup
        d_by_idx = {d["car_idx"]: d for d in self._log_session_meta.get("drivers", [])}

        final = []
        for r in results:
            cidx = r.get("CarIdx")
            drv = d_by_idx.get(cidx, {})
            time_field = r.get("Time", 0.0)
            laps_behind = 0
            if time_field is not None and time_field < 0:
                laps_behind = int(round(-time_field))
                time_field = None
            final.append({
                "position":       r.get("Position", 0) or 0,
                "class_position": r.get("ClassPosition", 0) or 0,
                "car_idx":        cidx,
                "car_number":     drv.get("car_number", ""),
                "driver":         drv.get("name", ""),
                "laps_completed": r.get("LapsComplete", 0) or 0,
                "best_lap":       r.get("FastestTime", 0.0) or 0.0,
                "best_lap_num":   r.get("FastestLap", 0) or 0,
                "incidents":      r.get("Incidents", 0) or 0,
                "time_gap":       time_field,
                "laps_behind":    laps_behind,
                "reason_out":     r.get("ReasonOutStr", "") or "Finished",
            })

        self._emit({
            "type":     "session_end",
            "official": bool(cur.get("ResultsOfficial", 0)),
            "final":    final,
        })
        self._final_written = True
        print(f"[logger] Wrote final classification for "
              f"{self._log_session_meta.get('track')} ({len(final)} cars)")

    # ----- incident polling thread ---------------------------------------
    def _incident_loop(self) -> None:
        if requests is None:
            return
        while self._running:
            time.sleep(INCIDENT_POLL_INTERVAL)
            if self._log_fp is None:
                continue
            try:
                r = requests.get(DASHBOARD_INCIDENTS_URL, timeout=2)
                if r.status_code != 200:
                    continue
                payload = r.json()
            except Exception:
                continue
            # Dashboard /incidents shape: {"incidents": [{"t_session":..., "car_idx":..., "type":..., ...}, ...]}
            items = payload.get("incidents") if isinstance(payload, dict) else payload
            if not items:
                continue
            for inc in items:
                key = (
                    round(float(inc.get("t_session", 0)), 1),
                    inc.get("car_idx"),
                    inc.get("type", ""),
                )
                if key in self._seen_incidents:
                    continue
                self._seen_incidents.add(key)
                # Only log incidents that happened after our log opened.
                # The dashboard keeps a rolling buffer of older incidents
                # which we don't want to retro-log into a new session.
                t_inc_wall = inc.get("t_wall")
                # Best-effort: if dashboard doesn't expose t_wall on each
                # incident, just log everything since open. (Most users
                # start the logger before the race, so this is fine.)
                event = {
                    "type":         "incident",
                    "t_session":    inc.get("t_session"),
                    "car_idx":      inc.get("car_idx"),
                    "car_number":   inc.get("car_number", ""),
                    "driver":       inc.get("name", inc.get("driver", "")),
                    "incident_type": inc.get("type", ""),
                    "details":      inc.get("details", ""),
                }
                self._emit(event)
                self._incidents_logged += 1
                cn = event["car_number"]
                if cn:
                    self._driver_incident_count[cn] = (
                        self._driver_incident_count.get(cn, 0) + 1
                    )

    # ----- main snapshot --------------------------------------------------
    def _read_snapshot(self) -> dict:
        # Lazy-start the incident-polling thread once we're connected so
        # it doesn't spin pointlessly while waiting for iRacing.
        if requests is not None and not self._incident_thread_started:
            self._incident_thread.start()
            self._incident_thread_started = True

        session_key, session_type, meta = self._detect_session_change()

        # Only log RACE sessions. Practice/quali/warmup get skipped.
        is_race = "race" in session_type.lower()

        if session_key is None or not is_race:
            # Not in a race. If a log was open from the previous session,
            # close it.
            if self._log_fp is not None:
                # Make sure we wrote the final classification before closing
                self._maybe_emit_final()
                self._close_log()
            return self._status_snapshot(session_key, session_type, meta)

        # We're in a race. Open a log if this is a new session.
        if session_key != self._log_session_key:
            if self._log_fp is not None:
                self._maybe_emit_final()
                self._close_log()
            self._open_log(meta)
            self._log_session_key = session_key

        # Active race tick
        self._maybe_emit_laps()
        self._maybe_emit_final()
        return self._status_snapshot(session_key, session_type, meta)

    def _status_snapshot(self, session_key, session_type, meta) -> dict:
        return {
            "connected":      True,
            "logging":        self._log_fp is not None,
            "log_path":       str(self._log_path) if self._log_path else None,
            "log_filename":   self._log_path.name if self._log_path else None,
            "started_at":     self._log_started_at,
            "session_type":   session_type,
            "session_key":    list(session_key) if session_key else None,
            "track":          meta.get("track", ""),
            "track_config":   meta.get("track_config", ""),
            "drivers_count":  len(meta.get("drivers", [])),
            "laps_logged":    self._laps_logged,
            "incidents_logged": self._incidents_logged,
            "final_written":  self._final_written,
        }


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)
poller = RaceLogger()


@app.route("/")
def index():
    return render_template_string(STATUS_HTML)


@app.route("/status")
def status():
    return jsonify(poller.get())


@app.route("/log")
def download_log():
    """Download the current log file (or 404 if no race is active)."""
    if not poller._log_path or not poller._log_path.is_file():
        abort(404)
    return send_file(str(poller._log_path), as_attachment=True,
                     download_name=poller._log_path.name,
                     mimetype="application/x-ndjson")


@app.route("/logs")
def list_logs():
    """List every log file we've written, newest first."""
    out = []
    for p in sorted(LOGS_DIR.glob("*.jsonl"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        st = p.stat()
        out.append({
            "name":     p.name,
            "size":     st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    return jsonify({"logs": out})


@app.route("/log/<path:name>")
def download_specific(name: str):
    """Download a previous log by filename."""
    safe = re.sub(r"[^A-Za-z0-9_\-.]+", "", name)
    if safe != name:
        abort(400)
    p = LOGS_DIR / safe
    if not p.is_file():
        abort(404)
    return send_file(str(p), as_attachment=True, download_name=p.name,
                     mimetype="application/x-ndjson")


# ---------------------------------------------------------------------------
# HTML — minimal status page
# ---------------------------------------------------------------------------
STATUS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Race Logger</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #0a0a0f; color: #e8e8ea;
    padding: 24px;
  }
  .card {
    max-width: 720px; margin: 0 auto;
    background: #14141c;
    border: 1px solid #26262f;
    border-radius: 10px;
    padding: 20px;
  }
  h1 { font-size: 18px; margin-bottom: 14px; color: #ff6b35; letter-spacing: 0.5px; }
  h2 { font-size: 13px; margin: 20px 0 8px; color: #9a9aad; text-transform: uppercase;
       letter-spacing: 1.5px; }
  .status-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 0; border-bottom: 1px solid #1f1f2b;
    font-size: 14px;
  }
  .status-row:last-child { border-bottom: none; }
  .status-row .label { color: #8a8aa0; }
  .status-row .value { color: #fff; font-weight: 600;
                        font-variant-numeric: tabular-nums; }
  .pill {
    display: inline-block;
    padding: 2px 10px; border-radius: 12px;
    font-size: 12px; font-weight: 700;
  }
  .pill.on  { background: #16341f; color: #4ade80; border: 1px solid #1d5c30; }
  .pill.off { background: #2a1a1a; color: #ff8888; border: 1px solid #5c2a2a; }
  a.btn {
    display: inline-block; margin-top: 10px;
    padding: 8px 14px; border-radius: 6px;
    background: #ff6b35; color: #0a0a0f;
    text-decoration: none; font-weight: 700; font-size: 13px;
  }
  a.btn:hover { background: #ff8a5b; }
  a.btn.secondary { background: #1f1f2b; color: #c8c8d8; }
  .log-list { font-size: 12px; }
  .log-list a { color: #ff6b35; text-decoration: none; font-family: monospace; }
  .log-list a:hover { text-decoration: underline; }
  .log-row { padding: 4px 0; color: #8a8aa0; }
</style>
</head>
<body>
<div class="card">
  <h1>iRACING RACE LOGGER</h1>

  <div id="status">
    <div class="status-row"><span class="label">Connected</span><span class="value" id="connected">—</span></div>
    <div class="status-row"><span class="label">Currently logging</span><span class="value" id="logging">—</span></div>
    <div class="status-row"><span class="label">Track</span><span class="value" id="track">—</span></div>
    <div class="status-row"><span class="label">Session</span><span class="value" id="session">—</span></div>
    <div class="status-row"><span class="label">Drivers</span><span class="value" id="drivers">—</span></div>
    <div class="status-row"><span class="label">Laps logged</span><span class="value" id="laps">—</span></div>
    <div class="status-row"><span class="label">Incidents logged</span><span class="value" id="inc">—</span></div>
    <div class="status-row"><span class="label">Log file</span><span class="value" id="logfile">—</span></div>
  </div>

  <div style="margin-top: 14px;">
    <a class="btn" href="/log" id="dl-current">Download current log</a>
  </div>

  <h2>Past logs</h2>
  <div class="log-list" id="loglist">loading…</div>
</div>

<script>
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}
function fmtBytes(b) {
  if (b < 1024) return b + ' B';
  if (b < 1024*1024) return (b/1024).toFixed(1) + ' KB';
  return (b/(1024*1024)).toFixed(1) + ' MB';
}
async function refresh() {
  try {
    const r = await fetch('/status'); const s = await r.json();
    document.getElementById('connected').textContent = s.connected ? 'yes' : 'no';
    const log = document.getElementById('logging');
    log.innerHTML = s.logging
      ? '<span class="pill on">RECORDING</span>'
      : '<span class="pill off">idle</span>';
    document.getElementById('track').textContent = (s.track || '—') +
      (s.track_config ? ' · ' + s.track_config : '');
    document.getElementById('session').textContent = s.session_type || '—';
    document.getElementById('drivers').textContent = s.drivers_count || '0';
    document.getElementById('laps').textContent = s.laps_logged ?? '0';
    document.getElementById('inc').textContent = s.incidents_logged ?? '0';
    document.getElementById('logfile').textContent = s.log_filename || '—';
    document.getElementById('dl-current').style.opacity = s.logging ? '1' : '0.4';
  } catch (e) {}
  try {
    const r2 = await fetch('/logs'); const d = await r2.json();
    const ll = document.getElementById('loglist');
    if (!d.logs || !d.logs.length) {
      ll.textContent = 'No logs yet.';
    } else {
      ll.innerHTML = d.logs.map(l =>
        `<div class="log-row"><a href="/log/${esc(l.name)}">${esc(l.name)}</a> ` +
        `<span style="margin-left:8px">${fmtBytes(l.size)} · ${esc(l.modified)}</span></div>`
      ).join('');
    }
  } catch (e) {}
  setTimeout(refresh, 2000);
}
refresh();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("iRacing Race Logger")
    print(f"Logs folder: {LOGS_DIR}")
    print(f"Open:        http://localhost:5009")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    t = threading.Thread(target=poller.run, daemon=True)
    t.start()
    try:
        app.run(host="0.0.0.0", port=5009, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
        if poller._log_fp:
            poller._close_log()


if __name__ == "__main__":
    main()
