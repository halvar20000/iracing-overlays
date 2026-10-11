"""
iRacing Catch-Up Battle Overlay  (F1-style "gap + catch prediction")
--------------------------------------------------------------------
Shows, for the ON-CAMERA driver (CamCarIdx), the battle with the next
driver ahead IN THE SAME CLASS:

    [ P4  #12  M. AHEAD ]   GAP 2.34s   ▲ +0.42s/LAP — CATCH IN 6 LAPS   [ P5  #7  T. FOCUS ]

  - Live gap to the car ahead (CarIdxF2Time diff — same technique as the
    standings overlay; F2Time is "race time behind the CLASS leader", so
    the difference of two same-class cars is their real gap and has no
    S/F update lag).
  - Pace delta from the LAST 3 CLEAN LAPS of both drivers (pit in/out
    laps excluded). Positive = focused driver is faster.
  - Catch prediction: gap / pace-delta -> laps until caught, plus the
    approximate wall-clock time that represents.

States shown on the pace chip:
    CATCHING  (green)  — focus is faster, prediction shown
    LOSING    (red)    — focus is slower, gap grows
    HOLDING   (gray)   — pace within +/-0.05 s/lap
    (gathering)        — fewer than 2 clean laps recorded for either car

Only renders during RACE sessions and when the focused car has a
same-class car ahead (the class leader on camera hides the banner).
If the car ahead is a full lap+ up the road, the gap shows "+N LAP"
and the prediction is suppressed (un-lapping isn't a catch battle).

Requirements:  pip install pyirsdk flask
Run:           python iracing_catchup.py
OBS source:    http://localhost:5015   (transparent lower-third banner)

Press H for a debug background, or open http://localhost:5015/?debug=1
"""

import os
import threading
import time
from collections import deque
from pathlib import Path
from statistics import mean

from flask import (Flask, Response, abort, jsonify, render_template_string,
                   send_file)

from car_brands import detect_brand, resolve_logo
from iracing_sdk_base import SDKPoller, setup_utf8_stdout
setup_utf8_stdout()

# `requests` is a soft dependency — only needed for the car-livery renders
# (same pattern as the livery overlay). Without it the banner still works,
# just without car images.
try:
    import requests  # type: ignore
    _HAS_REQUESTS = True
except ImportError:
    requests = None  # type: ignore
    _HAS_REQUESTS = False
    print("[catch] requests not installed — car livery images disabled. "
          "Run 'pip install requests' to enable.")

PORT = 5015

# ---- iRacing local render server (discovered via SIMRacingApps; see the
# livery overlay for the full story). Returns a rendered car PNG with the
# driver's pattern/colors/number — or their custom Trading Paints TGA when
# carCustPaint points at the on-disk paint cache. Only reachable while the
# sim is running.
PAINT_ROOT = Path(os.path.expanduser("~")) / "Documents" / "iRacing" / "paint"
IRACING_RENDER_URL = "http://127.0.0.1:32034/pk_car.png"
IRACING_RENDER_TIMEOUT = 5.0
IRACING_RENDER_VIEW = 1   # 1 = side view — the most "livery" looking
IRACING_RENDER_SIZE = 2   # 0 small / 1 medium / 2 large

# ---- tuning constants -------------------------------------------------------
LAP_WINDOW      = 3      # clean laps averaged per driver
MIN_LAPS        = 2      # laps needed before a prediction is shown
HOLD_BAND_S     = 0.05   # |pace delta| below this -> HOLDING
MIN_DELTA_S     = 0.03   # minimum delta used for a catch computation
LAP_RESOLVE_MIN = 0.3    # s to wait after a lap increment before trusting
LAP_RESOLVE_MAX = 3.0    # CarIdxLastLapTime; give up after this long
DRIVERS_TTL     = 5.0    # s between DriverInfo YAML re-parses
LAP_LOG_LEN     = 8      # every lap (incl. pit laps) kept for /laps


def _design_color(design) -> "str | None":
    """First colour of CarDesignStr ("pattern,c1,c2,c3,...") as a name
    accent, like F1's team colour. Too-dark colours return None (the page
    then uses white) — a black name on a dark panel is unreadable."""
    try:
        c = str(design or "").split(",")[1].strip().lstrip("#")
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (IndexError, ValueError):
        return None
    if 0.2126 * r + 0.7152 * g + 0.0722 * b < 90:
        return None
    return "#%02x%02x%02x" % (r, g, b)


def _abbrev(name: str) -> str:
    """'Joseph Johnson' -> 'J. Johnson' (same rule as the standings overlay)."""
    parts = (name or "").strip().split()
    if len(parts) < 2:
        return name or ""
    return f"{parts[0][0]}. {' '.join(parts[1:])}"


# ---------------------------------------------------------------------------
# Car livery rendering — compact copy of the proven iracing_livery.py logic
# (self-contained on purpose, like render_race.py; importing the livery
# overlay would execute its module-level Flask/poller setup).
# ---------------------------------------------------------------------------
def _car_path_variants(car_path: str) -> list:
    """The MX-5 is the one iRacing car with a NESTED paint folder: CarPath
    "mx5 mx52016" lives at paint\\mx5\\mx52016\\. Try both readings."""
    variants = [car_path]
    if " " in car_path:
        variants.append(car_path.replace(" ", "/"))
    if "\\" in car_path:
        variants.append(car_path.replace("\\", "/"))
    return variants


def find_paint_file(car_path: str, cust_id) -> "Path | None":
    """Path to the driver's custom paint TGA, if iRacing has it cached."""
    if not car_path or not cust_id:
        return None
    for fol in _car_path_variants(car_path):
        folder = PAINT_ROOT / fol
        for name in (f"car_{cust_id}.tga", f"car_num_{cust_id}.tga"):
            p = folder / name
            if p.is_file():
                return p
    return None


def _build_render_params(driver: dict, paint_path: str) -> dict:
    """DriverInfo dict -> /pk_car.png query params (SIMRacingApps mapping)."""
    params: dict = {"view": IRACING_RENDER_VIEW, "size": IRACING_RENDER_SIZE}
    car_path = (driver.get("CarPath") or "").strip()
    if car_path:
        params["carPath"] = car_path
    car_id = driver.get("CarID")
    if car_id:
        params["carId"] = str(car_id)
    design = (driver.get("CarDesignStr") or "").strip()
    parts = [p.strip() for p in design.split(",")] if design else []
    if len(parts) >= 1 and parts[0]:
        params["carPat"] = parts[0]
    if len(parts) >= 4:
        params["carCol"] = f"{parts[1]},{parts[2]},{parts[3]}"
    num_design = (driver.get("CarNumberDesignStr") or "").strip()
    num_parts = [p.strip() for p in num_design.split(",")] if num_design else []
    if len(num_parts) >= 1 and num_parts[0]:
        params["numPat"] = num_parts[0]
        params["numfont"] = num_parts[0]
    if len(num_parts) >= 2 and num_parts[1]:
        params["numSlnt"] = num_parts[1]
    if len(num_parts) >= 5:
        params["numcol"] = f"{num_parts[2]},{num_parts[3]},{num_parts[4]}"
    car_number = (str(driver.get("CarNumber") or "")).strip("\"").strip()
    if car_number:
        params["number"] = car_number
    lic = driver.get("LicColor")
    if lic is not None and lic != "":
        params["licCol"] = (f"{lic:06x}" if isinstance(lic, int)
                            else str(lic).lstrip("#").lstrip("0x"))
    sp1 = driver.get("CarSponsor_1") or 0
    sp2 = driver.get("CarSponsor_2") or 0
    if sp1 or sp2:
        params["sponsors"] = f"{sp1},{sp2}"
    name = driver.get("TeamName") or driver.get("UserName") or ""
    if name:
        params["name"] = name
    if paint_path:
        params["carCustPaint"] = paint_path
    return params


def _fetch_iracing_render(driver: dict, paint_path: str) -> "bytes | None":
    """Fetch the rendered car PNG. Never raises.

    Spaces MUST encode as %20 (not '+') — urlencode(quote_via=quote).
    For nested carPaths the separator variants go FIRST: the render server
    returns a DEFAULT car for an unknown carPath, so a wrong path is
    indistinguishable from success (livery-overlay lesson)."""
    if not _HAS_REQUESTS:
        return None
    from urllib.parse import urlencode, quote
    params = _build_render_params(driver, paint_path)
    raw_cp = params.get("carPath", "")
    cp_variants = ([raw_cp.replace(" ", "\\"), raw_cp.replace(" ", "/"), raw_cp]
                   if " " in raw_cp else [raw_cp])
    for cp in cp_variants:
        if cp:
            params["carPath"] = cp
        url = f"{IRACING_RENDER_URL}?{urlencode(params, quote_via=quote)}"
        try:
            resp = requests.get(url, timeout=IRACING_RENDER_TIMEOUT)
        except Exception as e:
            print(f"[catch] render fetch failed: {type(e).__name__}: {e}")
            return None          # server unreachable — variants won't help
        if resp.status_code != 200:
            continue
        if not resp.headers.get("Content-Type", "").lower().startswith("image/"):
            continue
        return resp.content
    return None


class CatchPoller(SDKPoller):
    tag = "catch"
    poll_interval = 0.25   # 4 Hz — gap updates smoothly, YAML stays cached

    def __init__(self):
        super().__init__()
        self._reset_session_state()
        self._session_key = None
        self._last_session_time = None

    # -------- session lifecycle ------------------------------------------
    def _reset_session_state(self):
        # Per-session trackers. MUST be cleared on session change or a big
        # backwards SessionTime jump (lesson from the June 4 dashboard bug:
        # stale timestamps from the previous session silently mute logic).
        self._drivers = {}           # cidx -> {name, num, class_id, ...}
        self._drivers_t = 0.0
        self._session_static = None  # {"session_type": ...} once per session
        self._lap_hist = {}          # cidx -> deque(maxlen=LAP_WINDOW)
        self._last_lap_num = {}      # cidx -> int
        self._pit_this_lap = {}      # cidx -> bool
        self._pending_lap = {}       # cidx -> (resolve_after, deadline, was_pit, lap_no)
        # Every completed lap incl. pit laps, with its lap NUMBER — the
        # /laps table compares the same lap numbers of two cars.
        self._lap_log = {}           # cidx -> deque[(lap_no, time, pit)]

    def _check_session_change(self, ir):
        key = (ir["SessionUniqueID"], ir["SessionNum"])
        st = ir["SessionTime"] or 0.0
        changed = key != self._session_key
        jumped_back = (self._last_session_time is not None
                       and st < self._last_session_time - 5.0)
        if changed or jumped_back:
            self._reset_session_state()
            self._session_key = key
            print(f"[{self.tag}] Session state reset "
                  f"({'session change' if changed else 'time jump'})")
        self._last_session_time = st

    # -------- cached YAML parses ------------------------------------------
    def _refresh_drivers(self, ir, t_now):
        if self._drivers and t_now - self._drivers_t < DRIVERS_TTL:
            return
        info = ir["DriverInfo"] or {}
        out = {}
        for d in info.get("Drivers", []) or []:
            try:
                cidx = int(d.get("CarIdx"))
            except (TypeError, ValueError):
                continue
            if int(d.get("CarIsPaceCar") or 0):
                continue
            if int(d.get("IsSpectator") or 0):
                continue
            class_color_raw = d.get("CarClassColor")
            try:
                class_color = "#{:06x}".format(int(class_color_raw) & 0xFFFFFF) \
                    if class_color_raw else None
            except (TypeError, ValueError):
                class_color = None
            full = (d.get("UserName") or "").strip()
            parts = full.split()
            team = (d.get("TeamName") or "").strip()
            if team == full:
                team = ""   # solo sessions report the driver as his own team
            out[cidx] = {
                "name":        full,
                "first":       " ".join(parts[:-1]),
                "last":        parts[-1] if parts else "",
                "team":        team,
                "car":         (d.get("CarScreenNameShort")
                                or d.get("CarScreenName") or "").strip(),
                "brand":       detect_brand(d.get("CarPath"),
                                            d.get("CarScreenName")),
                "color":       _design_color(d.get("CarDesignStr")),
                "short":       _abbrev(d.get("UserName") or ""),
                "num":         str(d.get("CarNumber") or "").strip("\""),
                "class_id":    int(d.get("CarClassID") or 0),
                "class_name":  (d.get("CarClassShortName") or "").strip(),
                "class_color": class_color,
                # Raw DriverInfo dict + cust id for the /car livery render.
                "raw":         d,
                "cust_id":     d.get("UserID"),
            }
        if out:
            self._drivers = out
            self._drivers_t = t_now

    def _refresh_session_static(self, ir):
        # One light parse per session — session type only (RACE gate).
        if self._session_static is not None:
            return
        info = ir["SessionInfo"] or {}
        sess_num = ir["SessionNum"]
        for s in info.get("Sessions", []) or []:
            if s.get("SessionNum") == sess_num:
                self._session_static = {
                    "session_type": (s.get("SessionType") or "").strip(),
                }
                return

    # -------- lap-time history --------------------------------------------
    def _update_lap_histories(self, laps, last_lap, on_pit, t_now):
        for cidx in self._drivers:
            lap_now = int(laps[cidx]) if cidx < len(laps) else 0
            in_pit = bool(on_pit[cidx]) if cidx < len(on_pit) else False

            # Any time spent on pit road taints the current lap (covers
            # both the in-lap and — because pit road spans S/F — the
            # out-lap as well).
            if in_pit:
                self._pit_this_lap[cidx] = True

            prev = self._last_lap_num.get(cidx)
            if prev is None:
                self._last_lap_num[cidx] = lap_now
            elif lap_now > prev:
                was_pit = self._pit_this_lap.get(cidx, False)
                # New lap starts now — if it starts on pit road the flag
                # is re-set by the check above on the next polls anyway.
                self._pit_this_lap[cidx] = in_pit
                # CarIdxLap is the lap being DRIVEN, so the one just
                # completed is lap_now - 1.
                self._pending_lap[cidx] = (t_now + LAP_RESOLVE_MIN,
                                           t_now + LAP_RESOLVE_MAX, was_pit,
                                           lap_now - 1)
                self._last_lap_num[cidx] = lap_now
            elif lap_now < prev:
                # Reset / tow / reconnect — history for this car is suspect.
                self._last_lap_num[cidx] = lap_now
                self._lap_hist.pop(cidx, None)
                self._lap_log.pop(cidx, None)
                self._pending_lap.pop(cidx, None)
                self._pit_this_lap[cidx] = False

            # Resolve a pending lap capture once CarIdxLastLapTime has had
            # a moment to settle after the crossing.
            pend = self._pending_lap.get(cidx)
            if pend:
                resolve_after, deadline, was_pit, lap_no = pend
                if t_now >= resolve_after:
                    llt = last_lap[cidx] if cidx < len(last_lap) else 0.0
                    if llt and llt > 0:
                        del self._pending_lap[cidx]
                        if lap_no >= 1:
                            self._lap_log.setdefault(
                                cidx, deque(maxlen=LAP_LOG_LEN)
                            ).append((lap_no, float(llt), bool(was_pit)))
                        if not was_pit:
                            self._lap_hist.setdefault(
                                cidx, deque(maxlen=LAP_WINDOW)
                            ).append(float(llt))
                    elif t_now > deadline:
                        del self._pending_lap[cidx]

    # -------- snapshot ------------------------------------------------------
    def _read_snapshot(self) -> dict:
        ir = self.ir
        t_now = time.monotonic()
        self._check_session_change(ir)
        self._refresh_drivers(ir, t_now)
        self._refresh_session_static(ir)

        base = {"connected": True, "show": False, "reason": ""}

        sess_type = (self._session_static or {}).get("session_type", "")
        if "race" not in sess_type.lower():
            base["reason"] = f"not a race session ({sess_type or 'unknown'})"
            return base

        laps     = ir["CarIdxLap"] or []
        lap_pct  = ir["CarIdxLapDistPct"] or []
        last_lap = ir["CarIdxLastLapTime"] or []
        on_pit   = ir["CarIdxOnPitRoad"] or []
        surface  = ir["CarIdxTrackSurface"] or []
        f2       = ir["CarIdxF2Time"] or []

        self._update_lap_histories(laps, last_lap, on_pit, t_now)

        focus = ir["CamCarIdx"]
        if focus is None or focus < 0 or focus not in self._drivers:
            base["reason"] = "no camera car"
            return base

        focus_cls = self._drivers[focus]["class_id"]

        # Live in-class order by track progress (lap + pct), in-world only —
        # the standard mid-lap-accurate ordering used across this repo.
        def progress(c):
            lp = float(lap_pct[c]) if c < len(lap_pct) else 0.0
            l  = int(laps[c]) if c < len(laps) else 0
            return l + max(0.0, min(lp, 1.0))

        in_class = [
            c for c in self._drivers
            if self._drivers[c]["class_id"] == focus_cls
            and c < len(surface) and surface[c] != -1
        ]
        if focus not in in_class:
            base["reason"] = "camera car not in world"
            return base

        order = sorted(in_class, key=progress, reverse=True)
        idx = order.index(focus)
        if idx == 0:
            base["reason"] = "camera car leads its class"
            return base
        ahead = order[idx - 1]

        # ---- gap (seconds) -----------------------------------------------
        gap = None
        lap_gap = int(progress(ahead) - progress(focus))  # full laps ahead
        f_focus = float(f2[focus]) if focus < len(f2) else 0.0
        f_ahead = float(f2[ahead]) if ahead < len(f2) else 0.0
        if lap_gap < 1:
            if (f_focus > 0 or f_ahead > 0) and f_focus >= f_ahead:
                gap = f_focus - f_ahead
            else:
                # Lap-1 fallback before F2Time populates: progress * est lap.
                est = ir["EstLapTime"]
                if not est or est <= 0:
                    est = 100.0
                gap = max(0.0, (progress(ahead) - progress(focus)) * est)

        # ---- pace + prediction ---------------------------------------------
        h_focus = self._lap_hist.get(focus)
        h_ahead = self._lap_hist.get(ahead)
        focus_avg = mean(h_focus) if h_focus and len(h_focus) >= MIN_LAPS else None
        ahead_avg = mean(h_ahead) if h_ahead and len(h_ahead) >= MIN_LAPS else None

        pace_delta = None
        status = "gathering"
        catch_laps = None
        catch_seconds = None
        if focus_avg and ahead_avg:
            pace_delta = ahead_avg - focus_avg   # + -> focus faster
            if abs(pace_delta) < HOLD_BAND_S:
                status = "holding"
            elif pace_delta > 0:
                status = "catching"
                if gap is not None and pace_delta >= MIN_DELTA_S and lap_gap < 1:
                    catch_laps = gap / pace_delta
                    catch_seconds = catch_laps * focus_avg
            else:
                status = "losing"

        def car(c, pos):
            d = self._drivers[c]
            return {
                "cidx": c,
                "pos": pos, "num": d["num"], "name": d["short"],
                "first": d.get("first", ""), "last": d.get("last", ""),
                "team": d.get("team", ""), "car": d.get("car", ""),
                "brand": d.get("brand"), "color": d.get("color"),
                "full_name": d["name"], "class_name": d["class_name"],
                "class_color": d["class_color"],
                "laps_used": len(self._lap_hist.get(c) or ()),
                "avg_lap": (mean(self._lap_hist[c])
                            if self._lap_hist.get(c) else None),
            }

        return {
            "connected": True, "show": True, "reason": "",
            # Class positions from the live order: focus sits at 0-based
            # index `idx`, so ahead = P(idx), focus = P(idx+1).
            "ahead": car(ahead, idx),
            "focus": car(focus, idx + 1),
            "gap": gap,
            "lap_gap": lap_gap,
            "pace_delta": pace_delta,
            "status": status,
            "catch_laps": catch_laps,
            "catch_seconds": catch_seconds,
            "laps": self._compare_laps(ahead, focus),
        }

    def _compare_laps(self, a, b, n=4):
        """The last `n` lap NUMBERS both cars have completed, with each
        car's time — for the F1 "LAST 4 LAPS" table."""
        la = {no: (t, pit) for no, t, pit in self._lap_log.get(a, ())}
        lb = {no: (t, pit) for no, t, pit in self._lap_log.get(b, ())}
        common = sorted(set(la) & set(lb))[-n:]
        return [{"lap": no,
                 "ahead": la[no][0], "ahead_pit": la[no][1],
                 "focus": lb[no][0], "focus_pit": lb[no][1]}
                for no in common]


# -----------------------------------------------------------------------------
# Flask
# -----------------------------------------------------------------------------
app = Flask(__name__)
poller = CatchPoller()


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
<title>iRacing Catch-Up Battle</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
        width: 100%; height: 100%;
        background: rgba(0,0,0,0);           /* OBS needs both explicit */
        background-color: rgba(0,0,0,0);
        font-family: 'Segoe UI', system-ui, sans-serif;
        color: #fff; overflow: hidden;
    }
    body.debug { background: #123; }
    body {
        display: flex; align-items: flex-end; justify-content: center;
        padding: 14px;
    }

    #banner {
        display: none;
        align-items: stretch;
        border-radius: 10px;
        overflow: hidden;
        /* nearly solid — same readability pass as the driver card */
        background: rgba(14, 14, 20, 0.97);
        border: 1px solid rgba(255, 255, 255, 0.10);
        box-shadow: 0 6px 30px rgba(0, 0, 0, 0.6);
        font-variant-numeric: tabular-nums;
        user-select: none;
    }
    #banner.on { display: flex; }

    .driver {
        display: flex; align-items: center; gap: 10px;
        padding: 10px 18px;
        min-width: 230px;
    }
    .driver.focus { background: rgba(255, 107, 53, 0.14); }
    .driver.focus .name { color: #ffb38a; }

    .posbadge {
        min-width: 42px; text-align: center;
        font-size: 25px; font-weight: 800;
        padding: 3px 8px; border-radius: 6px;
        background: rgba(255, 255, 255, 0.10);
        border-left: 4px solid var(--cls, #888);
    }
    .carnum { font-size: 18px; font-weight: 800; color: #d4d4de; }
    .name   { font-size: 25px; font-weight: 800; white-space: nowrap;
              letter-spacing: 0.4px; }
    .carimg {
        height: 46px; width: auto; max-width: 130px;
        object-fit: contain; display: none;
        filter: drop-shadow(0 2px 4px rgba(0,0,0,0.5));
    }
    .carimg.on { display: block; }

    .center {
        display: flex; flex-direction: column;
        align-items: center; justify-content: center;
        padding: 8px 26px; gap: 2px;
        background: rgba(255, 255, 255, 0.05);
        border-left: 1px solid rgba(255,255,255,0.12);
        border-right: 1px solid rgba(255,255,255,0.12);
        min-width: 240px;
    }
    .gap-label { font-size: 14px; font-weight: 700; letter-spacing: 1.4px; color: #d4d4de;
                 text-transform: uppercase; }
    .gap-value { font-size: 38px; font-weight: 800; line-height: 1.0;
                 color: #ffd166; }
    .pace-chip {
        display: inline-flex; align-items: center; gap: 6px;
        margin-top: 3px; padding: 2px 12px; border-radius: 999px;
        font-size: 16px; font-weight: 800; letter-spacing: 0.5px;
    }
    .pace-chip.catching { background: rgba(25, 211, 107, 0.20); color: #19d36b; }
    .pace-chip.losing   { background: rgba(230, 57, 70, 0.22);  color: #ff6b74; }
    .pace-chip.holding  { background: rgba(255, 255, 255, 0.10); color: #d4d4de; }
    .pace-chip.gathering{ background: rgba(255, 255, 255, 0.08); color: #a8a8b8;
                          font-weight: 600; }
    .catch-line { font-size: 17px; font-weight: 800; color: #19d36b;
                  letter-spacing: 0.4px; margin-top: 1px; min-height: 18px; }
    .catch-line.hide { visibility: hidden; }

    #dbg { position: fixed; top: 6px; left: 8px; font-size: 12px;
           color: #8a8a99; display: none; white-space: pre; }
    body.debug #dbg { display: block; }
</style>
</head>
<body>

<div id="banner">
    <div class="driver" id="ahead">
        <img class="carimg" id="a-car" alt="">
        <span class="posbadge" id="a-pos">P–</span>
        <span class="carnum"   id="a-num">#–</span>
        <span class="name"     id="a-name">—</span>
    </div>
    <div class="center">
        <span class="gap-label">Gap</span>
        <span class="gap-value" id="gap">—</span>
        <span class="pace-chip gathering" id="pace">GATHERING LAP DATA</span>
        <span class="catch-line hide" id="catch"></span>
    </div>
    <div class="driver focus" id="focus">
        <span class="posbadge" id="f-pos">P–</span>
        <span class="carnum"   id="f-num">#–</span>
        <span class="name"     id="f-name">—</span>
        <img class="carimg" id="f-car" alt="">
    </div>
</div>
<div id="dbg"></div>

<script>
const qs = new URLSearchParams(location.search);
if (qs.get('debug') === '1') document.body.classList.add('debug');
document.addEventListener('keydown', e => {
    if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug');
});

function fmtGap(d) {
    if (d.lap_gap >= 1) return `+${d.lap_gap} LAP${d.lap_gap > 1 ? 'S' : ''}`;
    if (d.gap == null) return '—';
    return d.gap.toFixed(2) + 's';
}
function fmtClock(s) {
    s = Math.max(0, Math.round(s));
    const m = Math.floor(s / 60);
    return `${m}:${String(s % 60).padStart(2, '0')}`;
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

const carState = {};   // prefix -> {cidx, failedAt}
const CAR_RETRY_MS = 20000;   // render server may come up after the overlay

function setDriver(prefix, c) {
    document.getElementById(prefix + '-pos').textContent = 'P' + c.pos;
    document.getElementById(prefix + '-pos').style.setProperty('--cls', c.class_color || '#888');
    document.getElementById(prefix + '-num').textContent = '#' + c.num;
    document.getElementById(prefix + '-name').textContent = c.name || '—';

    const img = document.getElementById(prefix + '-car');
    const st = carState[prefix] || (carState[prefix] = { cidx: null, failedAt: 0 });
    const changed = st.cidx !== c.cidx;
    const retry = !img.classList.contains('on')
                  && Date.now() - st.failedAt > CAR_RETRY_MS;
    if (changed || retry) {
        st.cidx = c.cidx;
        img.classList.remove('on');
        img.onload  = () => img.classList.add('on');
        img.onerror = () => { img.classList.remove('on'); st.failedAt = Date.now(); };
        img.src = `/car/${c.cidx}.png`;
    }
}

async function tick() {
    const d = await getStatus();
    const banner = document.getElementById('banner');
    const dbg = document.getElementById('dbg');

    if (!d || !d.connected || !d.show) {
        dbg.textContent = d ? (d.reason || 'hidden') : 'no response';
        if (Date.now() - lastGood > OFFLINE_AFTER_MS || (d && d.connected)) {
            banner.classList.remove('on');   // deliberate hide is immediate
        }
        return;
    }
    lastGood = Date.now();
    banner.classList.add('on');

    setDriver('a', d.ahead);
    setDriver('f', d.focus);
    document.getElementById('gap').textContent = fmtGap(d);

    const pace = document.getElementById('pace');
    const catchEl = document.getElementById('catch');
    pace.className = 'pace-chip ' + d.status;
    if (d.status === 'gathering') {
        pace.textContent = 'GATHERING LAP DATA';
    } else {
        const sign = d.pace_delta >= 0 ? '+' : '−';
        const val = Math.abs(d.pace_delta).toFixed(2);
        const label = d.status === 'catching' ? '▲ CATCHING'
                    : d.status === 'losing'   ? '▼ LOSING'
                    : '● HOLDING';
        pace.textContent = `${label}  ${sign}${val}s/LAP`;
    }
    if (d.catch_laps != null && d.status === 'catching') {
        const lapsTxt = d.catch_laps < 1 ? '<1 LAP'
                      : `~${Math.ceil(d.catch_laps)} LAPS`;
        catchEl.textContent = `CATCH IN ${lapsTxt} (≈${fmtClock(d.catch_seconds)})`;
        catchEl.classList.remove('hide');
    } else {
        catchEl.classList.add('hide');
    }

    dbg.textContent =
        `focus avg ${d.focus.avg_lap ? d.focus.avg_lap.toFixed(3) : '—'} (${d.focus.laps_used} laps)\n` +
        `ahead avg ${d.ahead.avg_lap ? d.ahead.avg_lap.toFixed(3) : '—'} (${d.ahead.laps_used} laps)`;
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


# -----------------------------------------------------------------------------
# F1-style broadcast graphics (2026-10-10) — extra pages on this server, same
# /status data: /gap (current gap, two drivers), /chasing ("CHASING X FOR 4TH")
# and /laps (last 4 laps of both drivers side by side). All follow the camera
# car and the same-class car ahead. ?zoom= scales, ?debug=1 / H = dark bg,
# ?demo=1 = fake data for layout.
# -----------------------------------------------------------------------------
F1_HTML = r"""
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Battle graphics</title>
<style>
  html, body { margin:0; padding:0; background:rgba(0,0,0,0); overflow:hidden;
               font-family:'Titillium Web','Segoe UI',Arial,sans-serif; color:#fff; }
  body.debug { background:#2a3a2a; }
  #root { padding:8px; display:inline-block; transform-origin:0 0; }
  .card { display:none; background:linear-gradient(180deg,#1d1d3a 0%,#131329 100%);
          border:1px solid rgba(255,255,255,0.10); border-radius:3px;
          box-shadow:0 4px 18px rgba(0,0,0,0.45); }
  .card.on { display:flex; }
  .pos { font-size:46px; font-weight:800; width:62px; text-align:center;
         font-variant-numeric:tabular-nums; }
  .who { display:flex; flex-direction:column; justify-content:center; line-height:1.05; }
  .first { font-size:22px; font-weight:500; }
  .last  { font-size:34px; font-weight:800; text-transform:uppercase; letter-spacing:.5px; }
  .sub   { font-size:17px; font-weight:600; text-transform:uppercase; color:#e8e8f0;
           display:flex; align-items:center; gap:6px; white-space:nowrap; }
  .sub img { height:17px; }
  .accent { color:#ff8a1f; }
  /* /gap */
  #gap { align-items:stretch; height:96px; }
  #gap .side { display:flex; align-items:center; gap:10px; padding:0 16px; min-width:330px; }
  #gap .side.r { justify-content:flex-end; text-align:right; }
  #gap .side.r .who { align-items:flex-end; }
  #gap .mid { display:flex; flex-direction:column; justify-content:center; align-items:center;
              border-left:1px solid rgba(255,255,255,0.18); border-right:1px solid rgba(255,255,255,0.18);
              padding:0 34px; min-width:220px; }
  #gap .gv { font-size:46px; font-weight:800; color:#3ee06a; font-variant-numeric:tabular-nums; line-height:1; }
  #gap .gv small { font-size:28px; }
  #gap .gl { font-size:18px; font-weight:600; letter-spacing:1px; margin-top:4px; }
  /* /chasing */
  #chase { align-items:center; height:96px; padding-right:30px; }
  #chase .who { min-width:250px; }
  #chase .txt { font-size:30px; font-weight:800; text-transform:uppercase; text-align:center;
                line-height:1.15; padding-left:30px; }
  #chase .txt .accent { font-size:34px; }
  #chase sup { font-size:18px; }
  /* /laps */
  #laps { flex-direction:column; padding:10px 18px 12px; }
  #laps table { border-collapse:collapse; }
  #laps th { font-size:22px; font-weight:800; text-transform:uppercase; text-align:center;
             padding:0 16px 6px; white-space:nowrap; }
  #laps th.t { text-align:left; padding-left:0; min-width:250px; }
  #laps td { font-size:26px; font-weight:700; text-align:center; padding:2px 16px;
             font-variant-numeric:tabular-nums; white-space:nowrap; }
  #laps td.n { text-align:left; padding-left:0; font-size:24px; font-weight:800; text-transform:uppercase; }
  #laps td.n span.p { display:inline-block; width:34px; font-size:22px; }
  #laps .fast { color:#3ee06a; }
  #laps .pit  { font-size:13px; color:#ffd24d; margin-left:4px; vertical-align:top; }
  #msg { display:none; color:#aab; font-size:14px; padding:6px; }
  body.debug #msg { display:block; }
</style></head>
<body>
<div id="root">
  <div class="card" id="gap">
    <div class="side l"><div class="pos" id="g_pa"></div><div class="who">
      <div class="first" id="g_fa"></div><div class="last" id="g_la"></div><div class="sub" id="g_sa"></div></div></div>
    <div class="mid"><div class="gv" id="g_v"></div><div class="gl">CURRENT GAP</div></div>
    <div class="side r"><div class="who">
      <div class="first" id="g_fb"></div><div class="last" id="g_lb"></div><div class="sub" id="g_sb"></div></div><div class="pos" id="g_pb"></div></div>
  </div>
  <div class="card" id="chase">
    <div class="pos" id="c_p"></div>
    <div class="who"><div class="first" id="c_f"></div><div class="last" id="c_l"></div><div class="sub" id="c_s"></div></div>
    <div class="txt">CHASING <span class="accent" id="c_t"></span><br>FOR <span id="c_for"></span></div>
  </div>
  <div class="card" id="laps"><table id="l_tab"></table></div>
  <div id="msg"></div>
</div>
<script>
const qs = new URLSearchParams(location.search);
const VIEW = (location.pathname.replace(/\/+$/, '').split('/').pop()) || 'gap';
const DEMO = qs.get('demo') === '1';
if (qs.get('debug') === '1') document.body.classList.add('debug');
document.addEventListener('keydown', e => { if (e.key==='h'||e.key==='H') document.body.classList.toggle('debug'); });
const z = parseFloat(qs.get('zoom') || '1');
if (z > 0 && z !== 1) document.getElementById('root').style.transform = 'scale(' + z + ')';
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function fmtLap(t) {
  if (t == null || !(t > 0)) return '';
  const m = Math.floor(t / 60), s = t - m * 60;
  return m + ':' + (s < 10 ? '0' : '') + s.toFixed(3);
}
function ordinal(n) {
  const v = n % 100, s = (v >= 11 && v <= 13) ? 'th' : (['th','st','nd','rd'][n % 10] || 'th');
  return n + '<sup>' + s.toUpperCase() + '</sup>';
}
function sub(c) {
  const logo = c.brand ? '<img src="/brand/' + encodeURIComponent(c.brand) + '" onerror="this.remove()">' : '';
  return esc(c.team || c.car || '') + logo;
}
function nameColor(el, c) { el.style.color = c.color || '#ffffff'; }
let last = '';
function setHTML(id, html) { const el = $(id); if (el.innerHTML !== html) el.innerHTML = html; }

function render(d) {
  const sig = JSON.stringify(d);
  if (sig === last) return; last = sig;
  for (const id of ['gap','chase','laps']) $(id).classList.remove('on');
  if (!d || !d.show) { $('msg').textContent = (d && d.reason) || 'waiting…'; return; }
  $('msg').textContent = '';
  const a = d.ahead, b = d.focus;
  if (VIEW === 'gap') {
    $('g_pa').textContent = a.pos; $('g_pb').textContent = b.pos;
    $('g_fa').textContent = a.first; $('g_fb').textContent = b.first;
    $('g_la').textContent = a.last;  $('g_lb').textContent = b.last;
    nameColor($('g_la'), a); nameColor($('g_lb'), b);
    setHTML('g_sa', sub(a)); setHTML('g_sb', sub(b));
    let g;
    if (d.lap_gap >= 1) g = '+' + d.lap_gap + ' LAP' + (d.lap_gap > 1 ? 'S' : '');
    else if (d.gap != null) g = '+' + d.gap.toFixed(3) + '<small>s</small>';
    else g = '—';
    setHTML('g_v', g);
    $('gap').classList.add('on');
  } else if (VIEW === 'chasing') {
    $('c_p').textContent = b.pos; $('c_f').textContent = b.first;
    $('c_l').textContent = b.last; nameColor($('c_l'), b);
    setHTML('c_s', sub(b));
    $('c_t').textContent = a.last; $('c_t').style.color = a.color || '#ff8a1f';
    setHTML('c_for', ordinal(a.pos));
    $('chase').classList.add('on');
  } else if (VIEW === 'laps') {
    const laps = d.laps || [];
    if (!laps.length) { $('msg').textContent = 'no common completed laps yet'; return; }
    let h = '<tr><th class="t">LAST ' + laps.length + ' LAP' + (laps.length > 1 ? 'S' : '') + '</th>';
    for (const l of laps) h += '<th>LAP ' + l.lap + '</th>';
    h += '</tr>';
    for (const [key, c] of [['ahead', a], ['focus', b]]) {
      const other = key === 'ahead' ? 'focus' : 'ahead';
      h += '<tr><td class="n"><span class="p">' + c.pos + '</span>' + esc(c.last) + '</td>';
      for (const l of laps) {
        const mine = l[key], theirs = l[other];
        const pit = l[key + '_pit'] ? '<span class="pit">PIT</span>' : '';
        // F1 convention: the slower car shows its lap time, the faster
        // one shows how much quicker it was (green, negative).
        if (mine < theirs) h += '<td class="fast">-' + (theirs - mine).toFixed(3) + pit + '</td>';
        else h += '<td>' + fmtLap(mine) + pit + '</td>';
      }
      h += '</tr>';
    }
    setHTML('l_tab', h);
    $('laps').classList.add('on');
  }
}

const DEMO_DATA = {show:true, gap:0.787, lap_gap:0,
  ahead:{pos:5, first:'Lando', last:'Norris', team:'McLaren', car:'', color:'#ff8a1f', brand:null},
  focus:{pos:6, first:'Kimi', last:'Antonelli', team:'Mercedes-AMG', car:'', color:'#27d9c2', brand:null},
  laps:[{lap:12, ahead:107.830, focus:107.166}, {lap:13, ahead:107.731, focus:107.128},
        {lap:14, ahead:107.346, focus:107.323}, {lap:15, ahead:106.892, focus:106.990, ahead_pit:false}]};

async function tick() {
  if (DEMO) { render(DEMO_DATA); return; }
  try { render(await (await fetch('/status', {cache:'no-store'})).json()); }
  catch (e) { render(null); }
}
(function loop() { tick().finally(() => setTimeout(loop, 500)); })();
</script>
</body></html>
"""


@app.route("/gap")
@app.route("/chasing")
@app.route("/laps")
def f1_pages():
    return Response(F1_HTML, mimetype="text/html")


@app.route("/brand/<slug>")
def brand_logo(slug: str):
    path = resolve_logo(slug)
    if not path or not path.is_file():
        abort(404)
    return send_file(str(path), max_age=3600)


# In-memory render cache. Key = (carPath, cust_id, design) so a mid-session
# livery change (rare) or a car swap gets a fresh render; bounded like the
# livery overlay's PNG cache.
_CAR_PNG_CACHE: dict = {}


@app.route("/car/<int:cidx>.png")
def car_image(cidx: int):
    d = poller._drivers.get(cidx)
    if not d:
        return Response(status=404)
    raw = d.get("raw") or {}
    paint = find_paint_file((raw.get("CarPath") or "").strip(), d.get("cust_id"))
    paint_path = str(paint) if paint else ""
    key = (raw.get("CarPath"), d.get("cust_id"),
           raw.get("CarDesignStr"), paint_path)
    data = _CAR_PNG_CACHE.get(key)
    if data is None:
        data = _fetch_iracing_render(raw, paint_path)
        if data is None:
            # 404 (not 500) so the <img> onerror hides cleanly; NOT cached,
            # so the render server being up later self-heals.
            return Response(status=404)
        _CAR_PNG_CACHE[key] = data
        if len(_CAR_PNG_CACHE) > 60:
            for old in list(_CAR_PNG_CACHE.keys())[:20]:
                _CAR_PNG_CACHE.pop(old, None)
    resp = Response(data, mimetype="image/png")
    # Override the global no-store: the browser may cache a render briefly
    # so camera flips between the same cars don't re-hit the render server.
    resp.headers["Cache-Control"] = "max-age=300"
    return resp


if __name__ == "__main__":
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()

    print("\n" + "=" * 60)
    print("  iRacing Catch-Up Battle Overlay (F1-style)")
    print(f"  OBS browser source:  http://localhost:{PORT}")
    print("  Shows on-camera driver vs the same-class car ahead:")
    print("  live gap, 3-lap pace delta, laps until caught.")
    print(f"  F1 graphics: /gap  /chasing  /laps   (?demo=1 for a preview)")
    print("  Press H (or ?debug=1) for a debug background.")
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host="0.0.0.0", port=PORT, debug=False,
                use_reloader=False, threaded=True)
    finally:
        poller.stop()
