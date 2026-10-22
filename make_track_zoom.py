"""Zoom-in video onto a track: world -> country -> circuit, Esri satellite
imagery + borders/places labels, track outline drawn at the end.
usage: python make_track_zoom.py tracks/<track>.json "<TITLE>" "<subtitle>"
  -> zoom_videos/<track>.mp4 (own folder, gitignored like show_notes/videos/;
  the old form with an output path as the 2nd argument still works).
Needs Pillow + ffmpeg on PATH, internet for the tiles (cached in track_zoom/tilecache).
First made for the PCCD finale at the Hungaroring (2026-10-22): 1080p30, 21 s.
"""
import io, json, math, os, subprocess, sys, threading, urllib.request
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
IMG = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
REF = "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "track_zoom", "tilecache")
VIDEOS = os.path.join(HERE, "zoom_videos")
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

Z0, Z1 = 3.0, 15.7
T_HOLD0, T_ZOOM, T_DRAW, T_HOLD1 = 1.0, 15.0, 2.0, 3.0
ORANGE = (255, 140, 30)

_mem, _lock = {}, threading.Lock()


def tile(url, z, x, y, rgba):
    n = 2 ** z
    if y < 0 or y >= n:
        return None
    x %= n
    key = (url, z, x, y)
    with _lock:
        if key in _mem:
            return _mem[key]
    path = os.path.join(CACHE, "ref" if url is REF else "img", str(z), str(x), f"{y}.png")
    if not os.path.exists(path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except FileExistsError:                    # two threads, same folder (Unraid shfs)
            pass
        req = urllib.request.Request(url.format(z=z, x=x, y=y), headers={"User-Agent": "cas-zoom-video"})
        for _ in range(3):
            try:
                data = urllib.request.urlopen(req, timeout=20).read()
                break
            except Exception:
                data = None
        if not data:
            return None
        tmp = f"{path}.{threading.get_ident()}.part"
        Image.open(io.BytesIO(data)).convert("RGBA" if rgba else "RGB").save(tmp, "PNG")
        os.replace(tmp, path)
    im = Image.open(path).convert("RGBA" if rgba else "RGB")
    with _lock:
        if len(_mem) > 3000:
            _mem.clear()
        _mem[key] = im
    return im


def merc(lat, lon, z):
    s = 256 * 2 ** z
    x = (lon + 180) / 360 * s
    r = math.radians(lat)
    y = (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * s
    return x, y


def view(cx, cy, z, pool):
    """Frame centred on world pixel (cx, cy) at fractional zoom z."""
    zi = min(17, max(0, math.ceil(z - 1e-9)))
    k = 2 ** (zi - z)                       # >= 1: tile pixels per output pixel
    ccx, ccy = cx * 2 ** (zi - z), cy * 2 ** (zi - z)
    hw, hh = W / 2 * k, H / 2 * k
    tx0, ty0 = math.floor((ccx - hw) / 256), math.floor((ccy - hh) / 256)
    tx1, ty1 = math.floor((ccx + hw) / 256), math.floor((ccy + hh) / 256)
    mw, mh = (tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256
    jobs = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
    imgs = list(pool.map(lambda t: tile(IMG, zi, t[0], t[1], False), jobs))
    refs = list(pool.map(lambda t: tile(REF, zi, t[0], t[1], True), jobs))
    mos = Image.new("RGB", (mw, mh), (8, 10, 16))
    for (tx, ty), im, rf in zip(jobs, imgs, refs):
        p = ((tx - tx0) * 256, (ty - ty0) * 256)
        if im:
            mos.paste(im, p)
        if rf:
            mos.paste(rf, p, rf)
    ox, oy = ccx - hw - tx0 * 256, ccy - hh - ty0 * 256
    return mos.transform((W, H), Image.AFFINE, (k, 0, ox, 0, k, oy), resample=Image.BILINEAR)


def ease(t):
    t = min(1.0, max(0.0, t))
    return t * t * t * (t * (6 * t - 15) + 10)       # smootherstep


def main():
    if len(sys.argv) >= 5:
        track_file, out, title, sub = sys.argv[1:5]
    else:
        track_file, title, sub = sys.argv[1:4]
        out = os.path.join(VIDEOS, os.path.splitext(os.path.basename(track_file))[0] + ".mp4")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tr = json.load(open(track_file))
    pts = tr["ontrack"]
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    tlat, tlon = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
    # start: wide view with the target off-centre by the same amount as at the
    # Hungaroring (start there = Europe / Atlantic / Africa); mirrored south of
    # the equator so the start shows land, not Antarctica
    slat, slon = tlat - math.copysign(9.583, tlat), tlon - 14.249
    if pts[0] != pts[-1]:
        pts = pts + [pts[0]]

    f_title = ImageFont.truetype(FONT_B, 64)
    f_sub = ImageFont.truetype(FONT, 34)
    f_att = ImageFont.truetype(FONT, 16)

    # start offset of the target from the screen centre (screen px at Z0)
    sx, sy = merc(slat, slon, Z0)
    tx, ty = merc(tlat, tlon, Z0)
    off0 = (tx - sx, ty - sy)

    total = T_HOLD0 + T_ZOOM + T_DRAW + T_HOLD1
    n = int(total * FPS)
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow",
                           "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
                          stdin=subprocess.PIPE)
    pool = ThreadPoolExecutor(16)
    for i in range(n):
        t = i / FPS
        u = ease((t - T_HOLD0) / T_ZOOM)
        z = Z0 + (Z1 - Z0) * u
        # the target slides to the centre during the first ~60 % of the zoom
        m = 1 - ease(u / 0.6)
        px, py = merc(tlat, tlon, z)
        cx, cy = px - off0[0] * m, py - off0[1] * m
        fr = view(cx, cy, z, pool)
        d = ImageDraw.Draw(fr, "RGBA")
        # target marker while far away (fades out as the track gets big)
        mx, my = W / 2 + off0[0] * m, H / 2 + off0[1] * m
        a = 1 - ease((z - 11) / 2)
        if a > 0:
            pulse = (t * 1.2) % 1
            r = 10 + 30 * pulse
            d.ellipse([mx - r, my - r, mx + r, my + r], outline=ORANGE + (int(220 * (1 - pulse) * a),), width=4)
            d.ellipse([mx - 9, my - 9, mx + 9, my + 9], fill=ORANGE + (int(255 * a),), outline=(255, 255, 255, int(255 * a)), width=3)
        # track outline drawn on at the end
        dt = (t - T_HOLD0 - T_ZOOM) / T_DRAW
        if dt > 0:
            k = max(2, int(len(pts) * min(1.0, ease(dt))))
            xy = []
            for la, lo in pts[:k]:
                x, y = merc(la, lo, z)
                xy.append((x - cx + W / 2, y - cy + H / 2))
            glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(glow).line(xy, fill=ORANGE + (200,), width=22, joint="curve")
            fr.paste(glow.filter(ImageFilter.GaussianBlur(10)), (0, 0), glow.filter(ImageFilter.GaussianBlur(10)))
            d = ImageDraw.Draw(fr, "RGBA")
            d.line(xy, fill=ORANGE + (255,), width=9, joint="curve")
            d.line(xy, fill=(255, 230, 190, 255), width=3, joint="curve")
        # name card
        ca = ease((t - T_HOLD0 - T_ZOOM * 0.75) / 1.2)
        if ca > 0:
            y0 = H - 210 + int(30 * (1 - ca))
            card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            cd = ImageDraw.Draw(card)
            tw = max(cd.textlength(title, font=f_title), cd.textlength(sub, font=f_sub))
            cd.rectangle([70, y0, 70 + tw + 80, y0 + 150], fill=(14, 14, 20, int(225 * ca)))
            cd.rectangle([70, y0, 80, y0 + 150], fill=ORANGE + (int(255 * ca),))
            cd.text((110, y0 + 18), title, font=f_title, fill=(255, 255, 255, int(255 * ca)))
            cd.text((112, y0 + 96), sub, font=f_sub, fill=(220, 220, 230, int(255 * ca)))
            fr.paste(card, (0, 0), card)
            d = ImageDraw.Draw(fr, "RGBA")
        d.text((W - 20, H - 14), "Imagery © Esri, Maxar, Earthstar Geographics · Track: SIMRacingApps / OSM",
               font=f_att, fill=(255, 255, 255, 150), anchor="rd")
        # fade in / out
        fade = min(1.0, t / 0.6, (total - t) / 0.8)
        if fade < 1:
            fr = Image.blend(Image.new("RGB", (W, H)), fr.convert("RGB"), max(0.0, fade))
        ff.stdin.write(fr.convert("RGB").tobytes())
        if i % 60 == 0:
            print(f"frame {i}/{n} z={z:.2f}", flush=True)
    ff.stdin.close()
    ff.wait()
    print("done", out)


if __name__ == "__main__":
    main()
