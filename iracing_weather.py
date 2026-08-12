"""
iRacing Weather Overlay  (horizontal strip)
-------------------------------------------
Live track conditions as a slim OBS bar:

    TRACK 38.2°C ↑   AIR 24.1°C →   HUM 62%   RAIN 0% · DRY   WIND 12 km/h NE   ☁ PARTLY CLOUDY

  - Track + air temperature (°C) with live TREND arrows
  - Relative humidity
  - Precipitation intensity + track wetness level (DRY … EXTREMELY WET);
    the whole rain cell turns blue once the track is declared wet
  - Wind speed + compass direction
  - Sky condition (clear / partly cloudy / mostly cloudy / overcast)

"Forecast": iRacing's REAL forecast lives behind the members API, whose
OAuth login is still paused (the trackmap went offline for the same
reason — see CLAUDE.md). So instead of pretending, this overlay derives
LIVE TRENDS: every sample window it records track temp / air temp /
precipitation, fits the recent drift and shows arrows plus a short
trend line like "Track cooling · Rain increasing". Honest, offline,
and updates continuously through the race.

Telemetry used (all standard pyirsdk vars, all optional-safe):
    TrackTempCrew (fallback TrackTemp), AirTemp, RelativeHumidity,
    Precipitation, TrackWetness, WeatherDeclaredWet, WindVel, WindDir,
    Skies

Requirements:  pip install pyirsdk flask
Run:           python iracing_weather.py
OBS source:    http://localhost:5016   (transparent strip)

Press H for a debug background, or open http://localhost:5016/?debug=1
"""

import math
import threading
import time
from collections import deque

from flask import Flask, jsonify, render_template_string

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()

PORT = 5016

# ---- trend tuning -----------------------------------------------------------
SAMPLE_EVERY_S   = 30.0    # one trend sample every 30 s
TREND_WINDOW     = 20      # keep the last 20 samples (~10 min)
TREND_MIN_SAMPLES = 4      # need ~2 min of data before showing a trend
TEMP_FLAT_BAND   = 0.15    # °C drift across the window that counts as "flat"
PRECIP_FLAT_BAND = 0.02    # precip fraction drift that counts as "flat"

# TrackWetness enum (iRacing rain system)
WETNESS_LABELS = {
    0: "UNKNOWN", 1: "DRY", 2: "MOSTLY DRY", 3: "VERY LIGHTLY WET",
    4: "LIGHTLY WET", 5: "MODERATELY WET", 6: "VERY WET", 7: "EXTREMELY WET",
}
SKIES_LABELS = {0: "CLEAR", 1: "PARTLY CLOUDY", 2: "MOSTLY CLOUDY", 3: "OVERCAST"}
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _trend(samples, flat_band):
    """Direction of drift across the sample window: 'up' / 'down' / 'flat'.

    Compares the mean of the first and last thirds — robust against the
    sample-to-sample noise the SDK weather vars show, without needing a
    real regression."""
    if len(samples) < TREND_MIN_SAMPLES:
        return None
    third = max(1, len(samples) // 3)
    first = sum(samples[:third]) / third
    last = sum(samples[-third:]) / third
    diff = last - first
    if diff > flat_band:
        return "up"
    if diff < -flat_band:
        return "down"
    return "flat"


class WeatherPoller(SDKPoller):
    tag = "weather"
    poll_interval = 1.0     # weather moves slowly — 1 Hz is plenty

    def __init__(self):
        super().__init__()
        self._reset_session_state()
        self._session_key = None
        self._last_session_time = None

    def _reset_session_state(self):
        self._hist_track = deque(maxlen=TREND_WINDOW)
        self._hist_air = deque(maxlen=TREND_WINDOW)
        self._hist_precip = deque(maxlen=TREND_WINDOW)
        self._last_sample_t = 0.0

    def _check_session_change(self, ir):
        key = (ir["SessionUniqueID"], ir["SessionNum"])
        st = ir["SessionTime"] or 0.0
        if key != self._session_key or (
                self._last_session_time is not None
                and st < self._last_session_time - 5.0):
            self._reset_session_state()
            self._session_key = key
            print(f"[{self.tag}] Session change — trend history reset")
        self._last_session_time = st

    @staticmethod
    def _f(ir, key):
        """Float telemetry read that treats missing/None as None."""
        v = ir[key]
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _read_snapshot(self) -> dict:
        ir = self.ir
        t_now = time.monotonic()
        self._check_session_change(ir)

        track_temp = self._f(ir, "TrackTempCrew")
        if track_temp is None or track_temp <= -100:
            track_temp = self._f(ir, "TrackTemp")
        air_temp = self._f(ir, "AirTemp")
        humidity = self._f(ir, "RelativeHumidity")      # 0..1
        precip = self._f(ir, "Precipitation")           # 0..1 (None pre-rain builds)
        wind_vel = self._f(ir, "WindVel")               # m/s
        wind_dir = self._f(ir, "WindDir")               # radians, 0 = north
        skies = ir["Skies"]
        wetness = ir["TrackWetness"]
        declared_wet = bool(ir["WeatherDeclaredWet"] or 0)

        # ---- trend sampling ------------------------------------------------
        if t_now - self._last_sample_t >= SAMPLE_EVERY_S:
            self._last_sample_t = t_now
            if track_temp is not None:
                self._hist_track.append(track_temp)
            if air_temp is not None:
                self._hist_air.append(air_temp)
            if precip is not None:
                self._hist_precip.append(precip)

        trend_track = _trend(list(self._hist_track), TEMP_FLAT_BAND)
        trend_air = _trend(list(self._hist_air), TEMP_FLAT_BAND)
        trend_precip = _trend(list(self._hist_precip), PRECIP_FLAT_BAND)

        # Short trend line for the strip's right edge.
        bits = []
        if trend_track == "up":
            bits.append("Track warming")
        elif trend_track == "down":
            bits.append("Track cooling")
        if trend_precip == "up":
            bits.append("Rain increasing")
        elif trend_precip == "down":
            bits.append("Rain easing")
        trend_text = " · ".join(bits)

        compass = None
        if wind_dir is not None:
            compass = COMPASS[int(((math.degrees(wind_dir) % 360) + 22.5) // 45) % 8]

        return {
            "connected": True,
            "track_temp": track_temp,
            "air_temp": air_temp,
            "humidity": humidity * 100.0 if humidity is not None else None,
            "precip": precip * 100.0 if precip is not None else None,
            "wetness": WETNESS_LABELS.get(int(wetness), None)
                       if wetness is not None else None,
            "declared_wet": declared_wet,
            "wind_kmh": wind_vel * 3.6 if wind_vel is not None else None,
            "wind_compass": compass,
            "skies": SKIES_LABELS.get(int(skies), None)
                     if skies is not None else None,
            "trend_track": trend_track,
            "trend_air": trend_air,
            "trend_precip": trend_precip,
            "trend_text": trend_text,
        }


# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
poller = WeatherPoller()


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
<title>iRacing Weather</title>
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
    body { display: flex; align-items: center; justify-content: center; padding: 10px; }

    #strip {
        display: none;
        align-items: stretch;
        border-radius: 10px;
        overflow: hidden;
        background: rgba(14, 14, 20, 0.88);
        border: 1px solid rgba(255, 255, 255, 0.10);
        box-shadow: 0 4px 22px rgba(0, 0, 0, 0.55);
        font-variant-numeric: tabular-nums;
        user-select: none;
    }
    #strip.on { display: flex; }

    .cell {
        display: flex; flex-direction: column;
        align-items: center; justify-content: center;
        padding: 7px 16px; gap: 1px;
        border-right: 1px solid rgba(255,255,255,0.08);
        min-width: 88px;
    }
    .cell:last-child { border-right: none; }
    .label { font-size: 10px; letter-spacing: 1.6px; color: #8a8a99;
             text-transform: uppercase; white-space: nowrap; }
    .value { font-size: 21px; font-weight: 800; line-height: 1.1;
             white-space: nowrap; }
    .sub   { font-size: 10px; font-weight: 700; color: #b0b0c0;
             letter-spacing: 0.6px; white-space: nowrap; }

    .value .arrow { font-size: 15px; vertical-align: 2px; }
    .arrow.up   { color: #ff6b74; }     /* warming / increasing = red   */
    .arrow.down { color: #61b4ff; }     /* cooling / easing     = blue  */
    .arrow.flat { color: #8a8a99; }

    .cell.track .value { color: #ff6b35; }
    .cell.air   .value { color: #ffd166; }
    .cell.rain.wet { background: rgba(36, 86, 196, 0.28); }
    .cell.rain.wet .value { color: #7db8ff; }
    .cell.trend { min-width: 0; padding: 7px 18px; }
    .cell.trend .value { font-size: 13px; font-weight: 700; color: #19d36b;
                         letter-spacing: 0.4px; }

    #dbg { position: fixed; top: 4px; left: 8px; font-size: 12px;
           color: #8a8a99; display: none; }
    body.debug #dbg { display: block; }
</style>
</head>
<body>

<div id="strip">
    <div class="cell track">
        <span class="label">Track</span>
        <span class="value"><span id="track">—</span><span class="arrow" id="track-arrow"></span></span>
    </div>
    <div class="cell air">
        <span class="label">Air</span>
        <span class="value"><span id="air">—</span><span class="arrow" id="air-arrow"></span></span>
    </div>
    <div class="cell">
        <span class="label">Humidity</span>
        <span class="value" id="hum">—</span>
    </div>
    <div class="cell rain" id="rain-cell">
        <span class="label">Rain</span>
        <span class="value"><span id="precip">—</span><span class="arrow" id="precip-arrow"></span></span>
        <span class="sub" id="wetness"></span>
    </div>
    <div class="cell">
        <span class="label">Wind</span>
        <span class="value" id="wind">—</span>
        <span class="sub" id="wind-dir"></span>
    </div>
    <div class="cell">
        <span class="label">Sky</span>
        <span class="value" id="skies" style="font-size:14px; font-weight:700;">—</span>
    </div>
    <div class="cell trend" id="trend-cell" style="display:none;">
        <span class="label">Trend</span>
        <span class="value" id="trend">—</span>
    </div>
</div>
<div id="dbg"></div>

<script>
const qs = new URLSearchParams(location.search);
if (qs.get('debug') === '1') document.body.classList.add('debug');
document.addEventListener('keydown', e => {
    if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug');
});

const ARROWS = { up: ' ▲', down: ' ▼', flat: ' ▶' };

function setArrow(id, trend) {
    const el = document.getElementById(id);
    if (!trend) { el.textContent = ''; el.className = 'arrow'; return; }
    el.textContent = ARROWS[trend] || '';
    el.className = 'arrow ' + trend;
}

let lastGood = Date.now();
const OFFLINE_AFTER_MS = 30000;

async function getStatus() {
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 4000);
    try { const r = await fetch('/status', { signal: ctrl.signal, cache: 'no-store' }); return await r.json(); }
    catch (e) { return null; }
    finally { clearTimeout(to); }
}

async function tick() {
    const d = await getStatus();
    const strip = document.getElementById('strip');

    if (!d || !d.connected || d.track_temp == null) {
        if (Date.now() - lastGood > OFFLINE_AFTER_MS) strip.classList.remove('on');
        return;
    }
    lastGood = Date.now();
    strip.classList.add('on');

    document.getElementById('track').textContent = d.track_temp.toFixed(1) + '°C';
    setArrow('track-arrow', d.trend_track);
    document.getElementById('air').textContent =
        d.air_temp != null ? d.air_temp.toFixed(1) + '°C' : '—';
    setArrow('air-arrow', d.trend_air);
    document.getElementById('hum').textContent =
        d.humidity != null ? Math.round(d.humidity) + '%' : '—';

    document.getElementById('precip').textContent =
        d.precip != null ? Math.round(d.precip) + '%' : '0%';
    setArrow('precip-arrow', d.trend_precip);
    document.getElementById('wetness').textContent = d.wetness || '';
    document.getElementById('rain-cell').classList.toggle('wet',
        !!d.declared_wet || (d.precip != null && d.precip >= 1));

    document.getElementById('wind').textContent =
        d.wind_kmh != null ? Math.round(d.wind_kmh) + ' km/h' : '—';
    document.getElementById('wind-dir').textContent = d.wind_compass || '';
    document.getElementById('skies').textContent = d.skies || '—';

    const tc = document.getElementById('trend-cell');
    if (d.trend_text) {
        tc.style.display = 'flex';
        document.getElementById('trend').textContent = d.trend_text.toUpperCase();
    } else {
        tc.style.display = 'none';
    }

    document.getElementById('dbg').textContent =
        `wet=${d.declared_wet} precip=${d.precip} trends: track=${d.trend_track} air=${d.trend_air} rain=${d.trend_precip}`;
}
(function loop() { tick().finally(() => setTimeout(loop, 1000)); })();
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
    print("  iRacing Weather Overlay (conditions + live trends)")
    print(f"  OBS browser source:  http://localhost:{PORT}")
    print("  Track/air temp, humidity, rain + wetness, wind, sky —")
    print("  with live trend arrows sampled during the session.")
    print("  Press H (or ?debug=1) for a debug background.")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=PORT, debug=False,
                use_reloader=False, threaded=True)
    finally:
        poller.stop()
