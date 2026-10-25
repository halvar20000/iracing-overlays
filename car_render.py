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
        with self._lock:
            self._cache[key] = data
            if len(self._cache) > CACHE_MAX:
                for old in list(self._cache)[:CACHE_MAX // 3]:
                    self._cache.pop(old, None)
        return data
