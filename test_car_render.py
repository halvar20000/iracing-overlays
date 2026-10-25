"""Offline checks for car_render.py (iRacing render server helper) and the
grid's livery page 5001 /cars. Run: python test_car_render.py
(needs flask + an irsdk stub or pyirsdk for the grid part)."""
import os, sys, tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import car_render as cr

failures = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)

class Resp:
    def __init__(self, code=200, ctype="image/png", body=b"PNG"):
        self.status_code, self.headers, self.content = code, {"Content-Type": ctype}, body

DRV = {"CarPath": "bmwm4gt3", "CarID": 132, "UserID": 4711, "TeamID": 99, "CarNumber": "\"03\"",
       "CarDesignStr": "12,1aa7a0,ffffff,111111", "CarNumberDesignStr": "0,0,ffffff,000000,ff0000",
       "UserName": "Yannick Wonnenberg", "TeamName": "Speed Monkeys", "LicColor": 0x00c702}

p = cr.render_params(DRV)
check("P1 design + colours", p["carPat"] == "12" and p["carCol"] == "1aa7a0,ffffff,111111")
check("P2 number without quotes", p["number"] == "03", p["number"])
check("P3 number colours", p["numcol"] == "ffffff,000000,ff0000")
check("P4 licence hex", p["licCol"] == "00c702", p["licCol"])
check("P5 view / size", p["view"] == 1 and p["size"] == 2 and cr.render_params(DRV, view=3)["view"] == 3)

urls = []
def get_ok(url, timeout):
    urls.append(url); return Resp()
cr.fetch_render(p, get_ok)
check("F1 %20 not +", "name=Speed%20Monkeys" in urls[-1], urls[-1])
urls.clear()
seq = iter([Resp(404), Resp(200, "text/html"), Resp()])
mx5 = cr.render_params({"CarPath": "mx5 mx52016"})
got = cr.fetch_render(mx5, lambda u, timeout: (urls.append(u), next(seq))[1])
check("F2 nested car path: variants tried, backslash first",
      got == b"PNG" and "carPath=mx5%5Cmx52016" in urls[0] and "carPath=mx5%20mx52016" in urls[2], str(urls))
def get_down(url, timeout):
    raise ConnectionError("refused")
check("F3 server down -> None, no raise", cr.fetch_render(p, get_down) is None)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "bmwm4gt3").mkdir()
    (root / "bmwm4gt3" / "car_4711.tga").write_bytes(b"x")
    check("T1 own paint", cr.find_paint_file("bmwm4gt3", 4711, 0, root).name == "car_4711.tga")
    check("T2 team id without team file -> own paint", cr.find_paint_file("bmwm4gt3", 4711, 99, root).name == "car_4711.tga")
    (root / "bmwm4gt3" / "car_team_99.tga").write_bytes(b"x")
    check("T3 team paint first", cr.find_paint_file("bmwm4gt3", 4711, 99, root).name == "car_team_99.tga")
    (root / "mx5" / "mx52016").mkdir(parents=True)
    (root / "mx5" / "mx52016" / "car_5.tga").write_bytes(b"x")
    check("T4 nested MX-5 folder", cr.find_paint_file("mx5 mx52016", 5, 0, root) is not None)

    calls = []
    def get_cnt(url, timeout):
        calls.append(url); return Resp()
    r = cr.CarRenderer(get=get_cnt, root=root)
    r.render(DRV); r.render(DRV)
    check("C1 cached", len(calls) == 1)
    r.render(DRV, team=True)
    check("C2 team render = other key + team paint", len(calls) == 2 and "car_team_99.tga" in calls[-1])
    check("C3 solo render uses the own paint", "car_4711.tga" in calls[0])
    flaky = iter([Resp(500), Resp()])
    r2 = cr.CarRenderer(get=lambda u, timeout: next(flaky), root=root)
    check("C4 failure not cached (self-heals)", r2.render(DRV) is None and r2.render(DRV) == b"PNG")

# ---- trim: transparent margin around the car is cut off ----------------------
try:
    import io
    from PIL import Image
    im = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    im.paste((200, 0, 0, 255), (100, 80, 300, 140))
    b = io.BytesIO(); im.save(b, "PNG")
    out = Image.open(io.BytesIO(cr.trim(b.getvalue(), pad=4)))
    check("R1 trim crops to the car + pad", out.size == (208, 68), str(out.size))
    opaque = Image.new("RGB", (50, 20), (9, 9, 9)); b2 = io.BytesIO(); opaque.save(b2, "PNG")
    check("R2 no alpha -> unchanged", cr.trim(b2.getvalue()) == b2.getvalue())
    check("R3 not an image -> unchanged", cr.trim(b"CAR") == b"CAR")
except ImportError:
    print("[SKIP] trim checks (no Pillow)")

# ---- grid server -----------------------------------------------------------
import iracing_grid as g
g.renderer = cr.CarRenderer(get=lambda u, timeout: Resp(body=b"CAR"))
g.poller.raw = {3: DRV}
c = g.app.test_client()
page = c.get("/cars").data
check("G1 /cars page", b"/car/" in page and b"STARTING GRID" in page)
rr = c.get("/car/3.png?view=2")
check("G2 /car render", rr.status_code == 200 and rr.data == b"CAR" and "max-age=300" in rr.headers["Cache-Control"])
check("G3 unknown car 404", c.get("/car/9.png").status_code == 404)
check("G4 /brand bmw", c.get("/brand/bmw").status_code == 200)
check("G5 /brand unknown 404", c.get("/brand/zzz").status_code == 404)
check("G6 default page unchanged", b"/grid" in c.get("/").data)

# ---- standings tower /cars (5005) -------------------------------------------
import iracing_standings as st
st.car_renderer = cr.CarRenderer(get=lambda u, timeout: Resp(body=b"CAR"))
st.poller.raw = {3: DRV}
sc = st.app.test_client()
tp = sc.get("/cars").data
check("S1 tower /cars page has the livery switch", b"SHOW_CARS" in tp and b"/car/" in tp)
check("S2 /cars/nodelta served", sc.get("/cars/nodelta").status_code == 200)
r5 = sc.get("/car/3.png")
check("S3 tower /car render", r5.status_code == 200 and r5.data == b"CAR")
check("S4 tower unknown car 404", sc.get("/car/7.png").status_code == 404)

print()
print("ALL PASS" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
