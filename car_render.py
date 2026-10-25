"""
Car livery renders from iRacing's local render server (shared helper).

iRacing serves http://127.0.0.1:32034/pk_car.png while the sim runs (found
via SIMRacingApps — see iracing_livery.py for the story): a rendered car with
the driver's pattern / colours / number, or the custom Trading Paints TGA
when carCustPaint points at the on-disk paint cache.

Same logic as iracing_livery.py / iracing_catchup.py (those keep their own
copies), plus the TEAM paint (car_team_<TeamID>.tga) for team events.
`requests` is a soft dependency: without it render() returns None.

    r = CarRenderer()
    png = r.render(driver_info_dict, team=True, view=1)   # bytes or None
"""
import io
import os
import threading
from pathlib import Path
from urllib.parse import quote, urlencode

try:
    import requests  # type: ignore
except ImportError:      # pragma: no cover
    requests = None      # type: ignore

PAINT_ROOT = Path(os.path.expanduser("~")) / "Documents" / "iRacing" / "paint"
RENDER_URL = "http://127.0.0.1:32034/pk_car.png"
RENDER_TIMEOUT = 5.0
DEFAULT_VIEW = 1         # 1 = side view
DEFAULT_SIZE = 2         # 0 small / 1 medium / 2 large
CACHE_MAX = 120


def car_path_variants(car_path: str) -> list:
    """The MX-5 is the one car with a NESTED paint folder: CarPath
    "mx5 mx52016" lives at paint\\mx5\\mx52016\\. Try both readings."""
    variants = [car_path]
    if " " in car_path:
        variants.append(car_path.replace(" ", "/"))
    if "\\" in car_path:
        variants.append(car_path.replace("\\", "/"))
    return variants


def find_paint_file(car_path: str, cust_id, team_id=0, root: Path = None):
    """The custom paint TGA iRacing shows for this car, if cached. In a team
    event the car wears the TEAM paint (car_team_<TeamID>.tga) — tried first,
    the driver's own file is the fallback."""
    if not car_path or not (cust_id or team_id):
        return None
    names = [f"car_team_{team_id}.tga"] if team_id else []
    if cust_id:
        names += [f"car_{cust_id}.tga", f"car_num_{cust_id}.tga"]
    for fol in car_path_variants(car_path):
        folder = (root or PAINT_ROOT) / fol
        for name in names:
            p = folder / name
            if p.is_file():
                return p
    return None


def render_params(driver: dict, paint_path: str = "", view=DEFAULT_VIEW,
                  size=DEFAULT_SIZE) -> dict:
    """DriverInfo dict -> /pk_car.png query params (SIMRacingApps mapping)."""
    params: dict = {"view": view, "size": size}
    car_path = (driver.get("CarPath") or "").strip()
    if car_path:
        params["carPath"] = car_path
    if driver.get("CarID"):
        params["carId"] = str(driver["CarID"])
    design = (driver.get("CarDesignStr") or "").strip()
    parts = [p.strip() for p in design.split(",")] if design else []
    if parts and parts[0]:
        params["carPat"] = parts[0]
    if len(parts) >= 4:
        params["carCol"] = f"{parts[1]},{parts[2]},{parts[3]}"
    num = (driver.get("CarNumberDesignStr") or "").strip()
    nparts = [p.strip() for p in num.split(",")] if num else []
    if nparts and nparts[0]:
        params["numPat"] = nparts[0]
        params["numfont"] = nparts[0]
    if len(nparts) >= 2 and nparts[1]:
        params["numSlnt"] = nparts[1]
    if len(nparts) >= 5:
        params["numcol"] = f"{nparts[2]},{nparts[3]},{nparts[4]}"
    number = str(driver.get("CarNumber") or "").strip("\"").strip()
    if number:
        params["number"] = number
    lic = driver.get("LicColor")
    if lic is not None and lic != "":
        params["licCol"] = (f"{lic:06x}" if isinstance(lic, int)
                            else str(lic).lstrip("#").lstrip("0x"))
    sp1, sp2 = driver.get("CarSponsor_1") or 0, driver.get("CarSponsor_2") or 0
    if sp1 or sp2:
        params["sponsors"] = f"{sp1},{sp2}"
    name = driver.get("TeamName") or driver.get("UserName") or ""
    if name:
        params["name"] = name
    if paint_path:
        params["carCustPaint"] = paint_path
    return params


def fetch_render(params: dict, get=None):
    """PNG bytes or None; never raises. Spaces MUST encode as %20
    (quote_via=quote). Nested carPaths: separator variants FIRST — the server
    returns a DEFAULT car for an unknown carPath, so a wrong path looks like
    success (livery-overlay lesson)."""
    get = get or (requests.get if requests else None)
    if get is None:
        return None
    params = dict(params)
    raw = params.get("carPath", "")
    variants = ([raw.replace(" ", "\\"), raw.replace(" ", "/"), raw]
                if " " in raw else [raw])
    for cp in variants:
        if cp:
            params["carPath"] = cp
        url = f"{RENDER_URL}?{urlencode(params, quote_via=quote)}"
        try:
            resp = get(url, timeout=RENDER_TIMEOUT)
        except Exception as e:
            print(f"[car_render] render fetch failed: {type(e).__name__}: {e}")
            return None          # server unreachable: other variants won't help
        if resp.status_code != 200:
            continue
        if not resp.headers.get("Content-Type", "").lower().startswith("image/"):
            continue
        return resp.content
    return None


def trim(png: bytes, pad: int = 4) -> bytes:
    """Crop the transparent margin around the car so a small slot (tower
    row) shows the car, not empty canvas. No-op without Pillow, without an
    alpha channel or on any error."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        if im.mode not in ("RGBA", "LA") and "transparency" not in im.info:
            return png
        box = im.convert("RGBA").getchannel("A").point(lambda a: 255 if a > 12 else 0).getbbox()
        if not box or box == (0, 0) + im.size:
            return png
        l, t, r, b = box
        im = im.crop((max(0, l - pad), max(0, t - pad), min(im.width, r + pad), min(im.height, b + pad)))
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()
    except Exception:
        return png


class CarRenderer:
    """Bounded in-memory cache in front of fetch_render(). Failures are NOT
    cached, so a render server that comes up later self-heals."""

    def __init__(self, get=None, root: Path = None):
        self._get = get
        self._root = root
        self._cache: dict = {}
        self._lock = threading.Lock()

    def render(self, driver: dict, team: bool = False, view=DEFAULT_VIEW,
               size=DEFAULT_SIZE):
        car_path = (driver.get("CarPath") or "").strip()
        team_id = (driver.get("TeamID") or 0) if team else 0
        paint = find_paint_file(car_path, driver.get("UserID"), team_id, self._root)
        paint_path = str(paint) if paint else ""
        key = (car_path, driver.get("UserID"), team_id, driver.get("CarDesignStr"),
               driver.get("CarNumber"), paint_path, view, size)
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None:
            return hit
        data = fetch_render(render_params(driver, paint_path, view, size), self._get)
        if data is None:
            return None
        data = trim(data)
        with self._lock:
            self._cache[key] = data
            if len(self._cache) > CACHE_MAX:
                for old in list(self._cache)[:CACHE_MAX // 3]:
                    self._cache.pop(old, None)
        return data


# ---- livery colours for a drawn car (iRacing renders only the side view) ----

def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(c) for c in rgb[:3])


def _design_colors(driver: dict) -> list:
    parts = [p.strip() for p in (driver.get("CarDesignStr") or "").split(",")]
    out = []
    for p in parts[1:4]:
        p = p.lstrip("#")
        if len(p) == 6 and all(c in "0123456789abcdefABCDEF" for c in p):
            out.append("#" + p.lower())
    return out


def paint_colors(png_or_path, top: int = 3) -> list:
    """The main colours of a paint TGA: body first, then accents. Counted in
    the middle band of the texture (where iRacing templates put the body
    panels; the corners hold interior / mirror bits) on a coarse palette;
    accents must differ from what was already picked and saturated colours
    weigh more. [] without Pillow or on any error."""
    try:
        from PIL import Image
        import colorsys
        im = Image.open(png_or_path).convert("RGBA")
        w, h = im.size
        im = im.crop((0, int(h * 0.3), int(w * 0.95), int(h * 0.9))).resize((160, 96), Image.NEAREST)
        counts: dict = {}
        for r, g, b, a in im.getdata():
            if a < 200:
                continue
            key = (r // 24 * 24 + 12, g // 24 * 24 + 12, b // 24 * 24 + 12)
            counts[key] = counts.get(key, 0) + 1
        if not counts:
            return []
        def vivid(c):            # saturation x brightness: red 0.94, template grey 0.1
            _, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in c))
            return s * v
        ranked = sorted(counts, key=counts.get, reverse=True)
        picked = [ranked[0]]
        total = sum(counts.values())
        # accents: vivid colours or white only — the template's grey and the
        # dull background colour of unused texture areas are not livery
        rest = [c for c in ranked[1:] if vivid(c) > 0.25 or min(c) > 216]
        rest.sort(key=lambda c: counts[c] * (0.15 + vivid(c)) ** 2, reverse=True)
        for c in rest:
            if len(picked) >= top:
                break
            if counts[c] < total * 0.004:
                continue
            if all(sum(abs(a - b) for a, b in zip(c, p)) > 110 for p in picked):
                picked.append(c)
        return [_hex(c) for c in picked]
    except Exception:
        return []


_color_cache: dict = {}


def livery_colors(driver: dict, team: bool = False, root: Path = None) -> dict:
    """{body, accent, trim, source}: from the custom paint (Trading Paints)
    when cached on disk, else iRacing's CarDesignStr colours."""
    car_path = (driver.get("CarPath") or "").strip()
    team_id = (driver.get("TeamID") or 0) if team else 0
    paint = find_paint_file(car_path, driver.get("UserID"), team_id, root)
    cols, source = [], "design"
    if paint:
        try:
            key = (str(paint), paint.stat().st_mtime)
        except OSError:
            key = (str(paint), 0)
        cols = _color_cache.get(key)
        if cols is None:
            cols = paint_colors(paint)
            if len(_color_cache) > CACHE_MAX:
                _color_cache.clear()
            _color_cache[key] = cols
        source = "paint" if cols else "design"
    if not cols:
        cols = _design_colors(driver)
    for extra in ("#d8d8dc", "#c8202a", "#111111"):
        if len(cols) < 3 and extra not in cols:
            cols.append(extra)
    return {"body": cols[0], "accent": cols[1], "trim": cols[2], "source": source}
