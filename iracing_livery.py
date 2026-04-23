"""
iRacing Livery Overlay
----------------------
Shows the car livery + driver info of whoever the iRacing broadcast
camera is currently following (CamCarIdx). Updates live as you
spectator-cycle between drivers.

Requirements:  pip install pyirsdk flask pillow
Run:           python iracing_livery.py
Open:          http://localhost:5006

Data sources, in order of preference:
  1. The driver's custom paint file from iRacing's on-disk cache:
       %USERPROFILE%\\Documents\\iRacing\\paint\\<carpath>\\car_<custid>.tga
     — converted to PNG on the fly and served via /livery/<carpath>/<custid>.png.
     This is what iRacing wraps around the 3D model. It looks like a
     flattened UV skin rather than a photo of the car, but it shows the
     real colors, numbers and sponsor layout.
  2. A colored silhouette generated from `CarDesignStr` (the default
     iRacing design string: pattern + 3 hex colors).
  3. A neutral fallback card when neither is available.

Brand logos (from ./brands/, via car_brands.py) are reused here too.

Runs in parallel with the other iracing_*.py scripts.  Press H to toggle
stream mode (transparent background) for OBS browser sources.
"""

from __future__ import annotations
import io
import os
import sys
import threading
import time
from pathlib import Path
from flask import Flask, jsonify, render_template_string, send_file, abort, Response

# `requests` is only needed to proxy Trading Paints previews. If it's not
# installed the overlay still works — the car-render preference is just
# skipped and we fall back to the flat TGA + design card.
try:
    import requests  # type: ignore
    _HAS_REQUESTS = True
except ImportError:
    requests = None  # type: ignore
    _HAS_REQUESTS = False
    print("[livery] requests not installed — Trading Paints renders disabled. "
          "Run 'pip install requests' to enable.")

# When the script is launched via launch_all.py the child process inherits the
# Windows system codepage (often cp1252) for stdout. Any non-ASCII character in
# a print() call then raises UnicodeEncodeError, which can kill the poller
# thread silently if it happens inside an except block. Force UTF-8 so all our
# diagnostic prints are safe regardless of the console's codepage.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import irsdk
except ImportError:
    print("ERROR: pyirsdk not installed. Run:  pip install pyirsdk flask pillow")
    raise SystemExit(1)

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow not installed. Run:  pip install pillow")
    raise SystemExit(1)

from car_brands import detect_brand, resolve_logo


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PAINT_ROOT = Path(os.path.expanduser("~")) / "Documents" / "iRacing" / "paint"

# How often the poller reads from iRacing (seconds). 0.3s = fast enough to
# feel instant when the camera switches drivers without hammering the SDK.
POLL_INTERVAL = 0.3

# iRacing runs a local HTTP render server on 127.0.0.1:32034 whenever the
# sim is running. /pk_car.png returns a 2D render of a car with the given
# paint scheme / number / sponsors / license color / custom TGA applied.
# This is the same service SIMRacingApps uses — no auth, no external CDN.
# Learned from https://github.com/SIMRacingApps/SIMRacingApps
# (com/SIMRacingApps/SIMPlugins/iRacing/iRacingCar.java, getImageUrl()).
IRACING_RENDER_URL = "http://127.0.0.1:32034/pk_car.png"
IRACING_RENDER_TIMEOUT = 5.0  # seconds — first render can be slower
# View options iRacing supports (from reverse engineering): 0..N different
# camera angles. 1 = side view, which is the most "livery" looking.
IRACING_RENDER_VIEW = 1
IRACING_RENDER_SIZE = 2  # 0 = small, 1 = medium, 2 = large (approximate)


# ---------------------------------------------------------------------------
# TGA → PNG conversion (cached)
# ---------------------------------------------------------------------------
_PNG_CACHE: dict[str, bytes] = {}


def _cache_key(path: Path) -> str:
    try:
        st = path.stat()
        return f"{path}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        return str(path)


def tga_to_png_bytes(tga_path: Path) -> bytes | None:
    """Load a TGA and return PNG bytes. Cached by (path, mtime, size)."""
    key = _cache_key(tga_path)
    if key in _PNG_CACHE:
        return _PNG_CACHE[key]
    try:
        with Image.open(tga_path) as img:
            img = img.convert("RGBA")
            # Keep the full resolution — OBS browser sources scale down nicely
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            data = buf.getvalue()
    except Exception as e:
        print(f"[livery] Failed to convert {tga_path.name}: {e}")
        return None
    _PNG_CACHE[key] = data
    # Keep the cache bounded — drop oldest when it grows past a threshold
    if len(_PNG_CACHE) > 60:
        for old_key in list(_PNG_CACHE.keys())[:20]:
            _PNG_CACHE.pop(old_key, None)
    return data


def find_paint_file(car_path: str, cust_id: int) -> Path | None:
    """Return the path to the driver's custom paint TGA, if iRacing has it."""
    if not car_path or not cust_id:
        return None
    folder = PAINT_ROOT / car_path
    candidate = folder / f"car_{cust_id}.tga"
    if candidate.is_file():
        return candidate
    # Some older/shared paints use slightly different names
    for alt in (f"car_num_{cust_id}.tga",):
        p = folder / alt
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# CarDesignStr parsing (fallback livery when no TGA is available)
# ---------------------------------------------------------------------------
def parse_design_str(design: str | None) -> dict:
    """
    CarDesignStr looks like "pattern,colorA,colorB,colorC" where colors are
    6-digit hex (no '#'). Pattern numbers are iRacing's design IDs.
    """
    out = {"pattern": 0, "c1": "1f1f2b", "c2": "4a4a5a", "c3": "e63946"}
    if not design:
        return out
    parts = design.split(",")
    if parts:
        try:
            out["pattern"] = int(parts[0])
        except (ValueError, TypeError):
            pass
    for i, key in enumerate(("c1", "c2", "c3"), start=1):
        if i < len(parts) and parts[i]:
            v = parts[i].strip().lower()
            if len(v) == 6 and all(ch in "0123456789abcdef" for ch in v):
                out[key] = v
    return out


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------
class LiveryPoller:
    def __init__(self, poll_interval: float = POLL_INTERVAL):
        self.ir = irsdk.IRSDK()
        self.poll_interval = poll_interval
        self.connected = False
        self.data = {"connected": False}
        self._lock = threading.Lock()
        self._running = True
        # Diagnostics: remember the last (cam_idx, cust_id, path) we logged so
        # we only print when the on-camera driver changes.
        self._last_logged_key: tuple | None = None
        # Diagnostics: remember the last startup outcome so we only log it
        # once per state change (otherwise every poll would spam the console).
        self._last_startup_status: str = ""
        # Loop-level diagnostics exposed via /debug.
        self.iteration = 0
        self.last_branch = "init"
        self.last_error: str | None = None
        # Raw DriverInfo dict for the current on-camera driver — needed by
        # /carview to build the iRacing render URL (design strings, sponsors,
        # club, number style, …). Protected by _lock like self.data.
        self.current_driver: dict = {}
        self.current_paint_path: str = ""

    def _check_connection(self) -> bool:
        if self.connected and not (self.ir.is_initialized and self.ir.is_connected):
            self.ir.shutdown()
            self.connected = False
            print("[livery] Disconnected from iRacing")
        elif not self.connected:
            # Try to start up. Log the outcome *once per state change* so we
            # can tell the difference between "startup() returned False" and
            # "startup() succeeded but is_initialized/is_connected is False".
            try:
                started = self.ir.startup()
            except Exception as e:
                if self._last_startup_status != f"exc:{e!r}":
                    self._last_startup_status = f"exc:{e!r}"
                    print(f"[livery] startup() raised: {e!r}")
                return False
            status = f"{started}/{self.ir.is_initialized}/{self.ir.is_connected}"
            if status != self._last_startup_status:
                self._last_startup_status = status
                print(f"[livery] startup()={started} is_initialized={self.ir.is_initialized} "
                      f"is_connected={self.ir.is_connected}")
            if started and self.ir.is_initialized and self.ir.is_connected:
                self.connected = True
                print("[livery] Connected to iRacing")
        return self.connected

    def _drivers_by_idx(self) -> dict:
        info = self.ir["DriverInfo"] or {}
        out = {}
        for d in info.get("Drivers", []) or []:
            cidx = d.get("CarIdx")
            if cidx is None:
                continue
            out[cidx] = d
        return out

    def _read_snapshot(self) -> dict:
        ir = self.ir
        cam_idx = ir["CamCarIdx"]
        if cam_idx is None or cam_idx < 0:
            key = ("no_cam", cam_idx)
            if key != self._last_logged_key:
                self._last_logged_key = key
                print(f"[livery] CamCarIdx={cam_idx!r} -- no camera target yet")
            with self._lock:
                self.current_driver = {}
                self.current_paint_path = ""
            return {"connected": True, "on_camera": False, "cam_idx": cam_idx}

        drivers = self._drivers_by_idx()
        d = drivers.get(cam_idx)
        if not d:
            key = ("no_driver", cam_idx, len(drivers))
            if key != self._last_logged_key:
                self._last_logged_key = key
                print(f"[livery] CamCarIdx={cam_idx} but no driver in DriverInfo "
                      f"(have {len(drivers)} drivers)")
            with self._lock:
                self.current_driver = {}
                self.current_paint_path = ""
            return {"connected": True, "on_camera": False, "cam_idx": cam_idx,
                    "note": f"No driver at CarIdx {cam_idx}"}
        if d.get("CarIsPaceCar") == 1:
            key = ("pace", cam_idx)
            if key != self._last_logged_key:
                self._last_logged_key = key
                print(f"[livery] CamCarIdx={cam_idx} is the pace car")
            with self._lock:
                self.current_driver = {}
                self.current_paint_path = ""
            return {"connected": True, "on_camera": False, "cam_idx": cam_idx,
                    "note": "Pace car"}

        car_path   = d.get("CarPath", "") or ""
        car_screen = d.get("CarScreenName", "") or ""
        cust_id    = int(d.get("UserID") or 0)
        car_id     = int(d.get("CarID") or 0)
        design     = d.get("CarDesignStr") or ""
        brand      = detect_brand(car_path, car_screen)

        paint_file = find_paint_file(car_path, cust_id)

        # Stash the raw driver dict (and paint path) for /carview, which
        # calls iRacing's local render server and needs the full set of
        # paint-related fields.
        with self._lock:
            self.current_driver = dict(d)
            self.current_paint_path = str(paint_file) if paint_file else ""

        # Diagnostics: figure out the canonical path we'd look for, regardless
        # of whether it exists. Useful for telling the user why the fallback
        # card is showing (missing file vs. path mismatch vs. no custom paint).
        expected_path = ""
        if car_path and cust_id:
            expected_path = str(PAINT_ROOT / car_path / f"car_{cust_id}.tga")
        log_key = (cam_idx, cust_id, car_path, bool(paint_file))
        if log_key != self._last_logged_key:
            self._last_logged_key = log_key
            name_str = d.get("UserName") or d.get("AbbrevName") or f"idx{cam_idx}"
            if paint_file:
                print(f"[livery] Camera -> {name_str} (custId={cust_id}, car={car_path}) "
                      f"-> paint FOUND: {paint_file}")
            else:
                folder = PAINT_ROOT / car_path if car_path else None
                folder_exists = bool(folder and folder.is_dir())
                print(f"[livery] Camera -> {name_str} (custId={cust_id}, car={car_path}) "
                      f"-> NO paint. Tried: {expected_path or '(no carpath/custid)'} "
                      f"(folder_exists={folder_exists})")

        position = 0
        positions = ir["CarIdxPosition"] or []
        if cam_idx < len(positions):
            position = int(positions[cam_idx] or 0)

        # Live lap data for the watched car (nice to show)
        last_lap = 0.0
        best_lap = 0.0
        ll = ir["CarIdxLastLapTime"] or []
        bl = ir["CarIdxBestLapTime"] or []
        if cam_idx < len(ll):
            last_lap = ll[cam_idx] or 0.0
        if cam_idx < len(bl):
            best_lap = bl[cam_idx] or 0.0
        on_pit_arr = ir["CarIdxOnPitRoad"] or []
        on_pit = bool(on_pit_arr[cam_idx]) if cam_idx < len(on_pit_arr) else False

        return {
            "connected":    True,
            "on_camera":    True,
            "car_idx":      cam_idx,
            "cust_id":      cust_id,
            "car_id":       car_id,
            "name":         d.get("UserName", "") or "",
            "abbrev":       d.get("AbbrevName", "") or "",
            "team_name":    d.get("TeamName", "") or "",
            "car_number":   d.get("CarNumber", "") or "",
            "car_path":     car_path,
            "car_name":     d.get("CarScreenNameShort") or car_screen,
            "car_class":    d.get("CarClassShortName") or "",
            "brand":        brand,
            "brand_logo":   bool(resolve_logo(brand)) if brand else False,
            "irating":      int(d.get("IRating") or 0),
            "license":      d.get("LicString", "") or "",
            "license_color": d.get("LicColor", "") or "",
            "design":       parse_design_str(design),
            "paint_available": bool(paint_file),
            "paint_path":   expected_path,
            "paint_folder_exists": bool(car_path and (PAINT_ROOT / car_path).is_dir()),
            "position":     position,
            "last_lap":     last_lap,
            "best_lap":     best_lap,
            "on_pit":       on_pit,
        }

    def run(self):
        print("[livery] Poller started (waiting for iRacing...)")
        last_heartbeat = 0.0
        while self._running:
            self.iteration += 1
            branch = "?"
            try:
                if self._check_connection():
                    branch = "snapshot"
                    snap = self._read_snapshot()
                    with self._lock:
                        self.data = snap
                else:
                    branch = "not_connected"
                    with self._lock:
                        self.data = {"connected": False}
            except Exception as e:
                branch = f"error:{type(e).__name__}"
                try:
                    self.last_error = f"{type(e).__name__}: {e!r}"
                except Exception:
                    self.last_error = type(e).__name__
                # Safe print: wrap in its own try so a print failure (e.g. a
                # codepage UnicodeEncodeError) never propagates out and kills
                # the poller thread.
                try:
                    print(f"[livery] Poll error ({type(e).__name__}): {e!r}")
                except Exception:
                    try:
                        print(f"[livery] Poll error ({type(e).__name__}) "
                              f"(details unprintable)")
                    except Exception:
                        pass
                try:
                    with self._lock:
                        self.data = {"connected": False, "error": str(e)}
                except Exception:
                    with self._lock:
                        self.data = {"connected": False,
                                     "error": type(e).__name__}
            self.last_branch = branch
            # Heartbeat every ~3 seconds so we can see the loop is alive and
            # which branch is firing. Also surfaces any data/connected mismatch.
            now = time.time()
            if now - last_heartbeat > 3.0:
                last_heartbeat = now
                with self._lock:
                    data_conn = self.data.get("connected")
                print(f"[livery] heartbeat iter={self.iteration} branch={branch} "
                      f"self.connected={self.connected} data.connected={data_conn}")
            time.sleep(self.poll_interval)

    def get(self) -> dict:
        with self._lock:
            return dict(self.data)

    def stop(self):
        self._running = False
        if self.connected:
            self.ir.shutdown()


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)
poller = LiveryPoller()


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
# Populated in main() once the thread is actually started. /debug uses this
# to report whether the poller loop is still alive.
poller_thread: threading.Thread | None = None


@app.route("/")
def index():
    return render_template_string(LIVERY_HTML)


@app.route("/state")
def state():
    return jsonify(poller.get())


@app.route("/debug")
def debug():
    """Raw poller state — useful for diagnosing why /state returns
    {"connected": false}. Open http://localhost:5006/debug in a browser."""
    ir = poller.ir
    info = {
        "poller_thread_alive":   poller_thread.is_alive() if poller_thread else None,
        "poller_iteration":      poller.iteration,
        "poller_last_branch":    poller.last_branch,
        "poller_last_error":     poller.last_error,
        "poller_connected_flag": poller.connected,
        "last_startup_status":   poller._last_startup_status,
        "ir_is_initialized":     bool(getattr(ir, "is_initialized", False)),
        "ir_is_connected":       bool(getattr(ir, "is_connected", False)),
        "paint_root":            str(PAINT_ROOT),
        "paint_root_exists":     PAINT_ROOT.is_dir(),
        "last_state":            poller.get(),
    }
    return jsonify(info)


# ---------------------------------------------------------------------------
# iRacing local render proxy: /carview/<car_id>/<cust_id>.png
# ---------------------------------------------------------------------------
# `_CARVIEW_CACHE[key] = bytes`  → cached PNG bytes
# `_CARVIEW_CACHE[key] = None`   → negative cache
_CARVIEW_CACHE: dict[str, bytes | None] = {}
_CARVIEW_CACHE_LOCK = threading.Lock()
_CARVIEW_CACHE_MAX = 200


def _build_render_params(driver: dict, paint_path: str) -> dict:
    """Translate a raw DriverInfo dict into the query-string params iRacing's
    /pk_car.png endpoint expects. Mirrors the logic in SIMRacingApps'
    iRacingCar.getImageUrl()."""
    params: dict = {
        "view": IRACING_RENDER_VIEW,
        "size": IRACING_RENDER_SIZE,
    }
    car_path = (driver.get("CarPath") or "").strip()
    if car_path:
        params["carPath"] = car_path

    # CarDesignStr = "pattern,color1,color2,color3"
    design = (driver.get("CarDesignStr") or "").strip()
    parts = [p.strip() for p in design.split(",")] if design else []
    if len(parts) >= 1 and parts[0]:
        params["carPat"] = parts[0]
    if len(parts) >= 4:
        params["carCol"] = f"{parts[1]},{parts[2]},{parts[3]}"

    # CarNumberDesignStr = "pattern,slant,color1,color2,color3" (5 fields
    # in modern iRacing; older cars had a 6-field variant with a separate
    # font index — we pass through whichever we have).
    num_design = (driver.get("CarNumberDesignStr") or "").strip()
    num_parts = [p.strip() for p in num_design.split(",")] if num_design else []
    if len(num_parts) >= 1 and num_parts[0]:
        params["numPat"] = num_parts[0]
        # SIMRacingApps also sends numfont for the same value (older API).
        params["numfont"] = num_parts[0]
    if len(num_parts) >= 2 and num_parts[1]:
        params["numSlnt"] = num_parts[1]
    if len(num_parts) >= 5:
        params["numcol"] = f"{num_parts[2]},{num_parts[3]},{num_parts[4]}"

    car_number = (str(driver.get("CarNumber") or "")).strip()
    if car_number:
        params["number"] = car_number

    # LicColor is an int in DriverInfo (e.g. 50946). The render server
    # accepts hex strings, so convert if necessary.
    lic = driver.get("LicColor")
    if lic is not None and lic != "":
        if isinstance(lic, int):
            params["licCol"] = f"{lic:06x}"
        else:
            params["licCol"] = str(lic).lstrip("#").lstrip("0x")

    # Wheel / rim styling (stock defaults if unavailable)
    rim_type = driver.get("CarRimType")
    if rim_type not in (None, ""):
        params["carRimType"] = str(rim_type)
    rim_col = driver.get("CarRimCol")
    if rim_col:
        params["carRimCol"] = str(rim_col)

    # Sponsors
    sp1 = driver.get("CarSponsor_1") or 0
    sp2 = driver.get("CarSponsor_2") or 0
    if sp1 or sp2:
        params["sponsors"] = f"{sp1},{sp2}"

    club = driver.get("ClubID")
    if club:
        params["club"] = str(club)

    # Driver / team name shown on the car
    name = driver.get("TeamName") or driver.get("UserName") or ""
    if name:
        params["name"] = name

    # Custom paint TGA — this is the key param that makes iRacing wrap the
    # driver's uploaded livery onto the 3D model instead of painting a
    # pattern+colors version.
    if paint_path:
        params["carCustPaint"] = paint_path

    return params


def _fetch_iracing_render(driver: dict, paint_path: str) -> bytes | None:
    """Call iRacing's local /pk_car.png render server. Returns the PNG
    bytes, or None if the server isn't reachable / doesn't return an image.
    Never raises.

    Spaces in carPath / carCustPaint MUST be encoded as %20, not '+'. Some
    iRacing car folders have spaces ("mx5 mx52016") and Windows paint file
    paths always do. `requests` uses quote_plus by default, which encodes
    spaces as '+' — iRacing's render server treats that as a literal plus
    sign and then can't find the car, so we build the query string
    ourselves with urllib.parse.urlencode(..., quote_via=quote).
    """
    if not _HAS_REQUESTS:
        return None
    from urllib.parse import urlencode, quote
    params = _build_render_params(driver, paint_path)
    query = urlencode(params, quote_via=quote)
    url = f"{IRACING_RENDER_URL}?{query}"
    try:
        resp = requests.get(url, timeout=IRACING_RENDER_TIMEOUT)
    except Exception as e:
        print(f"[livery] iRacing render fetch failed: {type(e).__name__}: {e}")
        return None
    if resp.status_code != 200:
        print(f"[livery] iRacing render HTTP {resp.status_code} for "
              f"carPath={params.get('carPath', '?')!r}")
        return None
    ctype = resp.headers.get("Content-Type", "").lower()
    if not ctype.startswith("image/"):
        print(f"[livery] iRacing render bad content-type: {ctype!r}")
        return None
    return resp.content


@app.route("/carview/<int:car_id>/<int:cust_id>.png")
def carview(car_id: int, cust_id: int):
    """Rendered car preview with the driver's livery applied, generated by
    iRacing's local render server. car_id and cust_id are carried on the URL
    purely as cache keys / cache-busters — the actual render parameters come
    from the poller's current_driver dict."""
    if car_id <= 0 or cust_id <= 0:
        abort(400)

    # Only render for the currently on-camera driver. A different cust_id
    # means the browser is asking about a driver we no longer have data for
    # — reject instead of guessing.
    with poller._lock:
        driver = dict(poller.current_driver)
        paint_path = poller.current_paint_path
    if not driver:
        abort(404)
    if int(driver.get("UserID") or 0) != cust_id:
        # Stale URL (camera already switched). Tell the browser to use its
        # new /state data rather than serving the wrong driver's render.
        abort(404)

    key = f"{car_id}/{cust_id}/{paint_path}"
    with _CARVIEW_CACHE_LOCK:
        cached = _CARVIEW_CACHE.get(key, "__miss__")

    if cached == "__miss__":
        data = _fetch_iracing_render(driver, paint_path)
        with _CARVIEW_CACHE_LOCK:
            _CARVIEW_CACHE[key] = data
            if len(_CARVIEW_CACHE) > _CARVIEW_CACHE_MAX:
                for old_key in list(_CARVIEW_CACHE.keys())[:50]:
                    _CARVIEW_CACHE.pop(old_key, None)
        cached = data

    if cached is None:
        abort(404)

    resp = Response(cached, mimetype="image/png")
    # Paints don't change during a session.
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/livery/<car_path>/<int:cust_id>.png")
def livery_png(car_path: str, cust_id: int):
    # Sanitization — iRacing car_path values are typically alnum + underscore,
    # but some have spaces too ("mx5 mx52016"), hyphens, dots, etc. We accept
    # a permissive character set and then explicitly confirm the resolved
    # path stays inside PAINT_ROOT so nothing can escape the folder.
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                  "0123456789"
                  "_- .")
    if not car_path or any(ch not in allowed for ch in car_path):
        abort(400)
    # Also reject obvious path-traversal tricks.
    if ".." in car_path or car_path.startswith(("/", "\\")):
        abort(400)
    tga = find_paint_file(car_path, cust_id)
    if not tga:
        abort(404)
    # Defence in depth: the resolved file must be below PAINT_ROOT.
    try:
        tga.resolve().relative_to(PAINT_ROOT.resolve())
    except ValueError:
        abort(400)
    png_bytes = tga_to_png_bytes(tga)
    if not png_bytes:
        abort(500)
    resp = Response(png_bytes, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=1800"
    return resp


@app.route("/brand/<slug>")
def brand_logo(slug: str):
    path = resolve_logo(slug)
    if not path or not path.is_file():
        abort(404)
    return send_file(str(path), max_age=3600)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
LIVERY_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>iRacing Livery — On Camera</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    /* Fully transparent by default so the overlay composites cleanly over
       the iRacing feed in OBS. Only the car image and the driver name
       are visible. H-toggle can add a dark card back for debugging.
       OBS's Chromium needs BOTH html and body to be transparent. */
    html, body {
        background-color: rgba(0, 0, 0, 0);
    }
    body {
        font-family: 'Segoe UI', system-ui, sans-serif;
        color: #e8e8ea;
        min-height: 100vh; padding: 10px;
    }
    body.debug-mode {
        background: #0a0a0f;
    }
    body.debug-mode .card {
        background: #14141c;
        border: 1px solid #26262f;
        border-radius: 10px;
        box-shadow: 0 4px 18px rgba(0,0,0,0.35);
    }
    body.debug-mode .info-col {
        border-left: 1px solid #26262f;
    }
    body.debug-mode .livery-col {
        background: #0f0f16;
    }

    .stream-toggle {
        position: fixed; top: 10px; right: 10px; z-index: 1000;
        background: rgba(20, 20, 28, 0.9);
        border: 1px solid #333; color: #bbb;
        padding: 5px 10px; font-size: 11px; border-radius: 4px;
        cursor: pointer; font-family: inherit;
        /* Hidden by default so nothing shows in OBS. Appears in debug
           mode (H-toggle) when you want to see the layout. */
        display: none;
    }
    body.debug-mode .stream-toggle { display: block; }

    .wrap { max-width: 780px; margin: 0 auto; }

    .card {
        background: transparent;
        border: none;
        border-radius: 0;
        overflow: visible;
        box-shadow: none;
    }

    /* Two-column body: car livery on the left, driver name on the right. */
    .main-row {
        display: grid;
        grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
    }
    .livery-col {
        background: transparent;
        display: flex; align-items: center; justify-content: center;
        min-width: 0;
    }
    .info-col {
        display: flex; align-items: center; justify-content: flex-start;
        padding: 14px 22px;
        min-width: 0;
    }

    .live-bar {
        display: flex; align-items: center; gap: 10px;
        padding: 8px 14px;
        background: #1b1b26;
        border-bottom: 1px solid #26262f;
        font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px;
        color: #9a9aad; font-weight: 700;
    }
    .live-dot {
        width: 8px; height: 8px; border-radius: 50%;
        background: #e63946;
        box-shadow: 0 0 8px #e63946;
        animation: pulse 1.6s ease-in-out infinite;
    }
    @keyframes pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50%      { opacity: 0.5; transform: scale(0.9); }
    }
    .pos-badge {
        margin-left: auto;
        background: #e63946; color: #fff;
        padding: 3px 10px; border-radius: 4px;
        font-size: 12px; font-weight: 800; letter-spacing: 1px;
    }
    .pos-badge.p1 { background: #ffd166; color: #0a0a0f; }

    /* --- Livery preview area ---------------------------------------- */
    .livery {
        width: 100%;
        aspect-ratio: 3 / 2;
        background: transparent;
        display: flex; align-items: center; justify-content: center;
        overflow: hidden; position: relative;
    }
    .livery img {
        width: 100%; height: 100%;
        object-fit: contain;
        image-rendering: -webkit-optimize-contrast;
    }
    .livery.fallback {
        background: linear-gradient(135deg, var(--c1,#1f1f2b), var(--c2,#3a3a4a) 50%, var(--c3,#e63946));
    }
    .livery .fb-label {
        font-size: 11px; text-transform: uppercase; letter-spacing: 2px;
        color: rgba(255,255,255,0.55); font-weight: 700;
        text-shadow: 0 1px 3px rgba(0,0,0,0.6);
    }
    .livery .note {
        position: absolute; bottom: 8px; right: 10px;
        font-size: 10px; color: rgba(255,255,255,0.45);
        letter-spacing: 0.5px;
    }

    /* --- Driver + car info ------------------------------------------ */
    .driver-block {
        padding: 14px 16px 14px;
        display: grid;
        grid-template-columns: 48px 1fr auto;
        gap: 12px;
        align-items: center;
        flex: 1;
        min-width: 0;
    }
    .car-number {
        background: #fff; color: #111;
        padding: 6px 0; border-radius: 6px;
        text-align: center;
        font-size: 20px; font-weight: 900;
        font-family: 'Segoe UI', sans-serif;
        min-width: 48px;
    }
    .name {
        /* inline-block so the dark background hugs the text width and
           grows / shrinks automatically with the driver's name length. */
        display: inline-block;
        background-color: rgba(20, 20, 28, 0.92);
        padding: 8px 16px;
        border-radius: 4px;
        font-size: 26px; font-weight: 700; color: #fff;
        line-height: 1.2;
        white-space: nowrap;
    }
    .meta {
        font-size: 12px; color: #8a8aa0;
        margin-top: 4px;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .meta b { color: #c8c8d8; font-weight: 600; }
    .team-line {
        font-size: 11px; color: #6a6a80;
        margin-top: 2px;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }

    .brand-slot img {
        height: 36px; max-width: 54px;
        object-fit: contain;
    }

    .stat-row {
        display: grid;
        grid-template-columns: 1fr 1fr 1fr;
        border-top: 1px solid #1d1d27;
    }
    .stat {
        padding: 10px 8px;
        text-align: center;
        border-right: 1px solid #1d1d27;
    }
    .stat:last-child { border-right: none; }
    .stat .label {
        font-size: 9px; text-transform: uppercase; letter-spacing: 1px;
        color: #7a7a90; font-weight: 700;
    }
    .stat .value {
        font-size: 14px; font-weight: 700; color: #fff;
        font-variant-numeric: tabular-nums;
        margin-top: 2px;
    }
    .license-chip {
        display: inline-block;
        padding: 1px 8px;
        border-radius: 3px;
        font-size: 12px; font-weight: 800;
        background: #4a4a5a; color: #fff;
    }

    .waiting {
        text-align: center; padding: 48px 16px;
        color: #7a7a90;
    }
    .waiting h2 {
        color: #e63946; margin-bottom: 8px;
        font-size: 18px; letter-spacing: 1px;
    }

    .pit-flag {
        display: inline-block;
        background: #3a1a1a; border: 1px solid #5c2a2a;
        color: #ff8888;
        padding: 2px 8px; border-radius: 3px;
        font-size: 10px; font-weight: 800;
        letter-spacing: 0.5px; margin-left: 6px;
    }
</style>
</head>
<body>

<button class="stream-toggle" onclick="toggleDebugBg()">Debug background (H)</button>

<div class="wrap" id="root">
    <div class="card waiting">
        <h2>WAITING FOR IRACING…</h2>
        <div>Load into a session. The overlay follows the broadcast camera.</div>
    </div>
</div>

<script>
// The overlay is transparent by default (ready for OBS). Pressing H adds a
// dark card background + divider so you can see the layout while editing.
function toggleDebugBg() { document.body.classList.toggle('debug-mode'); }
document.addEventListener('keydown', e => {
    if (e.key === 'h' || e.key === 'H') toggleDebugBg();
});

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
        '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
}

function fmtLap(t) {
    if (!t || t <= 0) return '—';
    const m = Math.floor(t/60);
    const s = t - m*60;
    return m ? `${m}:${s.toFixed(3).padStart(6,'0')}` : s.toFixed(3);
}

// Walks the <img> through its fallback sources (data-sources is a JSON array
// of {url,label}). When all sources fail, converts the .livery box into the
// default-design fallback card using the driver's CarDesignStr colors
// (data-design is a JSON object with pattern/c1/c2/c3).
window.__liveryNext = function(img) {
    let sources = [];
    let design = {};
    try { sources = JSON.parse(img.dataset.sources || '[]'); } catch (e) {}
    try { design  = JSON.parse(img.dataset.design  || '{}'); } catch (e) {}

    let idx = parseInt(img.dataset.idx || '0', 10) + 1;
    if (idx < sources.length) {
        img.dataset.idx = idx;
        img.src = sources[idx].url;
        const note = document.getElementById('livery-note');
        if (note) note.textContent = sources[idx].label;
        return;
    }

    // Out of sources — swap the container to the design-card fallback.
    const box = img.parentElement;
    if (!box) return;
    const c1 = design.c1 || '1f1f2b';
    const c2 = design.c2 || '4a4a5a';
    const c3 = design.c3 || 'e63946';
    box.style.setProperty('--c1', '#' + c1);
    box.style.setProperty('--c2', '#' + c2);
    box.style.setProperty('--c3', '#' + c3);
    box.classList.add('fallback');
    const pattern = (design.pattern != null) ? design.pattern : '—';
    box.innerHTML =
        '<span class="fb-label">Design #' + pattern + '</span>' +
        '<div class="note">default design · render &amp; TGA unavailable</div>';
};

function render(d) {
    const root = document.getElementById('root');

    if (!d.connected) {
        root.innerHTML = `
            <div class="card waiting">
                <h2>WAITING FOR IRACING…</h2>
                <div>Load into a session. The overlay follows the broadcast camera.</div>
            </div>`;
        return;
    }
    if (!d.on_camera) {
        root.innerHTML = `
            <div class="card waiting">
                <h2>NO CAMERA TARGET</h2>
                <div>${esc(d.note || 'Waiting for the broadcast camera to pick a car…')}</div>
            </div>`;
        return;
    }

    // Livery source — preference order:
    //   1) Trading Paints rendered 3D preview (via /carview proxy)
    //   2) Flat TGA from iRacing paint cache (via /livery endpoint)
    //   3) Colored fallback card from CarDesignStr
    // The <img> walks through a JSON-encoded source list on each onerror.
    const sources = [];
    if (d.car_id > 0 && d.cust_id > 0) {
        sources.push({
            url: `/carview/${d.car_id}/${d.cust_id}.png`,
            label: 'iRacing render',
        });
    }
    if (d.paint_available) {
        sources.push({
            url: `/livery/${encodeURIComponent(d.car_path)}/${d.cust_id}.png`,
            label: 'flat skin · iRacing paint cache',
        });
    }

    let liveryHtml;
    if (sources.length > 0) {
        const first = sources[0];
        // Stash the fallback sources + design JSON on data-* attributes
        // (properly HTML-escaped via esc()) so nothing can break out of the
        // attribute quoting. __liveryNext reads them off the element.
        const sourcesAttr = esc(JSON.stringify(sources));
        const designAttr  = esc(JSON.stringify(d.design || {}));
        liveryHtml = `
            <div class="livery" id="livery-box">
                <img id="livery-img" src="${first.url}" alt="Livery"
                     data-sources="${sourcesAttr}"
                     data-design="${designAttr}"
                     data-idx="0"
                     onerror="window.__liveryNext(this)">
                <div class="note" id="livery-note">${esc(first.label)}</div>
            </div>`;
    } else {
        const c1 = d.design?.c1 || '1f1f2b';
        const c2 = d.design?.c2 || '4a4a5a';
        const c3 = d.design?.c3 || 'e63946';
        const style = `--c1:#${c1};--c2:#${c2};--c3:#${c3};`;
        // Explain WHY we fell back, so the user can tell missing-TGA from
        // path-mismatch at a glance (only visible outside stream mode).
        let reason;
        if (!d.paint_folder_exists) {
            reason = `no car folder: ${esc(d.car_path || '?')}`;
        } else if (!d.cust_id) {
            reason = 'no custId';
        } else {
            reason = `no render/TGA for custId ${d.cust_id}`;
        }
        liveryHtml = `
            <div class="livery fallback" style="${style}">
                <span class="fb-label">Design #${d.design?.pattern ?? '—'}</span>
                <div class="note">default design · ${reason}</div>
            </div>`;
    }

    // Brand logo
    const brandHtml = (d.brand && d.brand_logo)
        ? `<img src="/brand/${encodeURIComponent(d.brand)}" alt="${esc(d.brand)}" title="${esc(d.car_name || d.brand)}">`
        : '';

    // Pit/position indicators
    const pit = d.on_pit ? ' <span class="pit-flag">PIT</span>' : '';
    const posCls = d.position === 1 ? 'p1' : '';
    const posBadge = d.position > 0
        ? `<div class="pos-badge ${posCls}">P${d.position}</div>`
        : '';

    // License color: iRacing gives a hex; fall back to neutral
    let licenseStyle = '';
    if (d.license_color && /^[0-9a-fA-F]{6}$/.test(d.license_color)) {
        licenseStyle = `background:#${d.license_color};color:#0a0a0f;`;
    }

    root.innerHTML = `
        <div class="card">
            <div class="main-row">
                <div class="livery-col">
                    ${liveryHtml}
                </div>
                <div class="info-col">
                    <div class="name">${esc(d.name || 'Unknown')}</div>
                </div>
            </div>
        </div>
    `;
}

async function poll() {
    try {
        const r = await fetch('/state');
        const d = await r.json();
        render(d);
    } catch (e) { /* keep last view */ }
    setTimeout(poll, 400);
}
poll();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("iRacing Livery Overlay")
    print(f"Paint folder: {PAINT_ROOT}")
    if not PAINT_ROOT.is_dir():
        print("WARNING: paint folder not found. The overlay will still work")
        print("         but will always fall back to the default-design card.")
    print("Open: http://localhost:5006")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    global poller_thread
    poller_thread = threading.Thread(target=poller.run, daemon=True)
    poller_thread.start()
    try:
        app.run(host="0.0.0.0", port=5006, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()


if __name__ == "__main__":
    main()
