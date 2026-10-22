"""Drone flight over a track: a Google Earth Studio project that flies one lap
low over the circuit (start / finish -> start / finish, looking ahead), then
the finished video with a name card per corner, a mini map and, optionally,
Gabi's voice (edge-tts, de-DE-KatjaNeural = the moderation's voice).

  1) python make_drone_flight.py esp drone/hungaroring.json
       -> drone/out/hungaroring.esp
     Open it in Google Earth Studio (earth.google.com/studio, "Import" a
     project), render 1920x1080 / 30 fps (JPEG), unzip the frames.
  2) python make_drone_flight.py finish drone/hungaroring.json <frames folder>
       -> drone_videos/hungaroring_drone.mp4        (cards, no sound)
          drone_videos/hungaroring_drone_voice.mp4  (cards + Gabi's voice)

drone/<track>.json: title, subtitle, path (dense [lat, lon] lap starting at
the S/F line, in driving direction), start_say, corners [{turn, name, at =
lap fraction of the corner entry, say = what the voice reads}].
Options for "esp": --height (m, default 70), --speed (m/s, default 22).
Earth Studio's altitude is taken as height above sea level; if the camera
sits too low / too high in the preview, re-run with another --height.
Needs Pillow + numpy + ffmpeg; the voice needs the edge-tts package.
"""
import argparse, glob, io, json, math, os, subprocess, sys, urllib.request
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ESP = os.path.join(HERE, "drone", "out")
OUT_VID = os.path.join(HERE, "drone_videos")
DEM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
DEM_CACHE = os.path.join(HERE, "track_zoom", "demcache")

W, H, FPS = 1920, 1080, 30
KEY_EVERY_S = 1.0        # one Earth Studio keyframe per second of flight
LOOK_M = 110.0           # the camera looks at the track this far ahead
SMOOTH_M = 25.0          # camera path smoothing (hairpins stay round)
LEAD_S = 3.5             # a corner card appears this long before the entry
VOICE = "de-DE-KatjaNeural"
VOICE_RATE = "+5%"
ORANGE = (255, 140, 30)
R_EARTH = 6371000.0


def fonts():
    for b, r in (("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                 ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf")):
        if os.path.exists(b):
            return b, r
    raise SystemExit("no font found")


# ---------------------------------------------------------------- geometry
def load(cfg_file):
    cfg = json.load(open(cfg_file, encoding="utf-8"))
    p = json.load(open(os.path.join(os.path.dirname(cfg_file), cfg["path"]), encoding="utf-8"))
    pts = np.array(p["path"] if isinstance(p, dict) else p, dtype=float)
    if np.hypot(*(pts[0] - pts[-1])) > 1e-7:
        pts = np.vstack([pts, pts[:1]])
    return cfg, pts


def to_xy(pts, lat0, lon0):
    k = math.radians(1) * R_EARTH
    return np.c_[(pts[:, 1] - lon0) * k * math.cos(math.radians(lat0)), (pts[:, 0] - lat0) * k]


def to_ll(xy, lat0, lon0):
    k = math.radians(1) * R_EARTH
    return np.c_[xy[:, 1] / k + lat0, xy[:, 0] / (k * math.cos(math.radians(lat0))) + lon0]


def resample(xy, step=1.0):
    seg = np.hypot(*np.diff(xy, axis=0).T)
    s = np.r_[0, np.cumsum(seg)]
    L = s[-1]
    ss = np.arange(0, L, step)
    return np.c_[np.interp(ss, s, xy[:, 0]), np.interp(ss, s, xy[:, 1])], L


def loop_smooth(a, n):
    """Moving average over a closed loop (n samples each side)."""
    if n < 1:
        return a.copy()
    k = 2 * n + 1
    ext = np.r_[a[-n:], a, a[:n]]
    c = np.cumsum(np.r_[np.zeros((1,) + a.shape[1:]), ext], axis=0)
    return (c[k:] - c[:-k]) / k


def dem_height(lat, lon, z=14, cache={}):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    tx, ty = int(x), int(y)
    key = (tx, ty)
    if key not in cache:
        path = os.path.join(DEM_CACHE, str(z), str(tx), f"{ty}.png")
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            req = urllib.request.Request(DEM.format(z=z, x=tx, y=ty), headers={"User-Agent": "cas-drone"})
            open(path, "wb").write(urllib.request.urlopen(req, timeout=30).read())
        cache[key] = np.asarray(Image.open(path).convert("RGB"), dtype=float)
    a = cache[key]
    px, py = min(255, int((x - tx) * 256)), min(255, int((y - ty) * 256))
    r, g, b = a[py, px]
    return r * 256 + g + b / 256 - 32768


def flight(pts, height, speed):
    """Camera keyframes along the lap: [(t, lat, lon, alt, heading, tilt)]."""
    lat0, lon0 = pts[:, 0].mean(), pts[:, 1].mean()
    track, L = resample(to_xy(pts, lat0, lon0), 1.0)
    cam = loop_smooth(track, int(SMOOTH_M))
    N = len(track)
    ll = to_ll(cam, lat0, lon0)
    ground = np.array([dem_height(*ll[i]) for i in range(0, N, 5)])
    ground = np.interp(np.arange(N), np.arange(0, N, 5), ground)
    ground = loop_smooth(ground[:, None], 100)[:, 0]
    T = L / speed
    nkey = int(math.ceil(T / KEY_EVERY_S))
    times = np.linspace(0, T, nkey + 1)
    idx = np.minimum((times * speed).astype(int), N - 1)
    head = []
    for i in idx:
        tgt = track[(i + int(LOOK_M)) % N]
        dx, dy = tgt - cam[i]
        head.append(math.degrees(math.atan2(dx, dy)) % 360)    # compass bearing
    # unwrapped (a lap turns 360 deg), then smoothed over +-2 keyframes
    head = np.degrees(np.unwrap(np.radians(head)))
    head = np.convolve(np.pad(head, 2, mode="edge"), np.ones(5) / 5, mode="valid")
    tilt = 90 - math.degrees(math.atan2(min(height, 50.0), LOOK_M))
    return [(times[k], ll[i][0], ll[i][1], ground[i] + height, head[k], tilt)
            for k, i in enumerate(idx)], L, T


def esp(cfg, keys, T, name):
    frames = int(round(T * FPS))
    tt = [k[0] / T for k in keys]

    def track(kind, vals, lo, hi, extra=None):
        v = {"relative": (vals[0] - lo) / (hi - lo), "minValueRange": lo, "maxValueRange": hi}
        v.update(extra or {})
        return {"type": kind, "value": v, "inTimeline": True,
                "keyframes": [{"time": t, "value": (x - lo) / (hi - lo)} for t, x in zip(tt, vals)]}

    heads = [k[4] for k in keys]
    hlo, hhi = math.floor(min(heads)) - 1, math.ceil(max(heads)) + 1
    pos = [track("longitude", [k[2] for k in keys], -180, 180),
           track("latitude", [k[1] for k in keys], -90, 90),
           track("altitude", [k[3] for k in keys], -500, 65117481, {"logarithmic": False})]
    rot = [track("rotationX", heads, hlo, hhi),
           track("rotationY", [k[5] for k in keys], 0, 180),
           {"type": "rotationZ", "value": {"relative": 0}}]
    return {
        "modelVersion": 18,
        "settings": {"name": name, "frameRate": FPS, "dimensions": {"width": W, "height": H},
                     "duration": frames, "timeFormat": "frames"},
        "scenes": [{
            "animationModel": {"roving": False, "logarithmic": False, "groupedPosition": True},
            "duration": frames,
            "attributes": [{
                "type": "cameraGroup", "inTimeline": True,
                "attributes": [
                    {"type": "cameraPositionGroup", "inTimeline": True,
                     "attributes": [{"type": "position", "inTimeline": True, "attributes": pos}]},
                    {"type": "cameraRotationGroup", "inTimeline": True, "attributes": rot},
                ]}],
            "cameraExport": {"logarithmic": False, "modelVersion": 2}}],
        "playbackManager": {"range": {"start": 0, "end": frames}},
    }


# ---------------------------------------------------------------- timeline
def timeline(cfg, L, T):
    """Cards: [(t_start, t_end, top, big, say)]. A card stays until the next
    one comes (max 7 s)."""
    v = L / T
    cards = [(0.6, 6.5, cfg.get("subtitle", ""), cfg["title"], cfg.get("start_say", ""))]
    for c in cfg["corners"]:
        t0 = max(0.0, c["at"] * L / v - LEAD_S)
        label = ("KURVEN " if "–" in c["turn"] or "-" in c["turn"] else "KURVE ") + c["turn"]
        cards.append([t0, t0 + 7.0, label, c["name"].upper(), c.get("say", "")])
    for a, b in zip(cards[1:], cards[2:]):
        a[1] = min(a[1], b[0] - 0.2)
    return [tuple(c) for c in cards]


# ---------------------------------------------------------------- finish
def voice_clips(cfg_name, cards):
    import asyncio
    import edge_tts
    out = os.path.join(OUT_ESP, "voice")
    os.makedirs(out, exist_ok=True)
    clips, last_end = [], 0.0
    for i, c in enumerate(cards):
        if not c[4]:
            continue
        f = os.path.join(out, f"{cfg_name}_{i:02d}.mp3")
        asyncio.run(edge_tts.Communicate(c[4], VOICE, rate=VOICE_RATE).save(f))
        dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                             "-of", "csv=p=0", f]).strip())
        start = max(c[0] + 0.4, last_end + 0.3)
        if start > c[0] + 1.5:
            print(f"  note: '{c[3]}' starts {start - c[0]:.1f} s after its card (previous line is long)")
        clips.append((start, f, dur))
        last_end = start + dur
    return clips


def draw_card(fr, card, t, f_big, f_small):
    t0, t1, top, big, _ = card
    a = min(1.0, (t - t0) / 0.4, (t1 - t) / 0.4)
    if a <= 0:
        return
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    y0 = H - 250 + int(24 * (1 - a))
    tw = max(d.textlength(big, font=f_big), d.textlength(top, font=f_small))
    d.rectangle([70, y0, 70 + tw + 80, y0 + 150], fill=(14, 14, 20, int(225 * a)))
    d.rectangle([70, y0, 80, y0 + 150], fill=ORANGE + (int(255 * a),))
    d.text((110, y0 + 16), top, font=f_small, fill=ORANGE + (int(255 * a),))
    d.text((108, y0 + 58), big, font=f_big, fill=(255, 255, 255, int(255 * a)))
    fr.alpha_composite(ov)


class MiniMap:
    SIZE = 330

    def __init__(self, pts):
        lat0, lon0 = pts[:, 0].mean(), pts[:, 1].mean()
        self.track, self.L = resample(to_xy(pts, lat0, lon0), 2.0)
        x, y = self.track[:, 0], -self.track[:, 1]
        k = (self.SIZE - 50) / max(np.ptp(x), np.ptp(y))
        self.px = np.c_[(x - x.min()) * k + 25 + (self.SIZE - 50 - np.ptp(x) * k) / 2,
                        (y - y.min()) * k + 25 + (self.SIZE - 50 - np.ptp(y) * k) / 2]
        self.base = Image.new("RGBA", (self.SIZE, self.SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(self.base)
        d.rounded_rectangle([0, 0, self.SIZE - 1, self.SIZE - 1], 18, fill=(14, 14, 20, 170))
        line = [tuple(p) for p in self.px] + [tuple(self.px[0])]
        d.line(line, fill=(255, 255, 255, 230), width=5, joint="curve")
        sx, sy = self.px[0]
        d.rectangle([sx - 3, sy - 9, sx + 3, sy + 9], fill=ORANGE + (255,))

    def draw(self, fr, frac):
        m = self.base.copy()
        d = ImageDraw.Draw(m)
        x, y = self.px[int(frac * len(self.px)) % len(self.px)]
        d.ellipse([x - 11, y - 11, x + 11, y + 11], fill=ORANGE + (255,), outline=(255, 255, 255, 255), width=3)
        fr.alpha_composite(m, (W - self.SIZE - 40, 40))


def finish(cfg_file, frames_dir):
    cfg, pts = load(cfg_file)
    name = os.path.splitext(os.path.basename(cfg_file))[0]
    meta = json.load(open(os.path.join(OUT_ESP, f"{name}_flight.json")))
    L, T = meta["length_m"], meta["duration_s"]
    files = sorted(f for e in ("jpg", "jpeg", "png") for f in glob.glob(
        os.path.join(frames_dir, "**", f"*.{e}"), recursive=True))
    if not files:
        raise SystemExit(f"no frames in {frames_dir}")
    expect = int(round(T * FPS))
    if abs(len(files) - expect) > 2:
        print(f"note: {len(files)} frames, the project has {expect} - timing scaled")
    scale = len(files) / FPS / T          # real seconds per planned second
    cards = [(a * scale, b * scale, c, d, e) for a, b, c, d, e in timeline(cfg, L, T)]
    fb, fr_ = fonts()
    f_big, f_small = ImageFont.truetype(fb, 64), ImageFont.truetype(fr_, 34)
    f_att = ImageFont.truetype(fr_, 16)
    mm = MiniMap(pts)
    os.makedirs(OUT_VID, exist_ok=True)
    silent = os.path.join(OUT_VID, f"{name}_drone.mp4")
    total = len(files) / FPS
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow",
                           "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", silent],
                          stdin=subprocess.PIPE)
    for i, f in enumerate(files):
        t = i / FPS
        fr = Image.open(f).convert("RGBA")
        if fr.size != (W, H):
            fr = fr.resize((W, H), Image.LANCZOS)
        mm.draw(fr, t / total)
        for c in cards:
            if c[0] <= t <= c[1]:
                draw_card(fr, c, t, f_big, f_small)
        ImageDraw.Draw(fr).text((W - 20, 24), "Strecke: OpenStreetMap", font=f_att,
                                fill=(255, 255, 255, 140), anchor="ra")
        fade = min(1.0, t / 0.6, (total - t) / 0.8)
        out = fr.convert("RGB")
        if fade < 1:
            out = Image.blend(Image.new("RGB", (W, H)), out, max(0.0, fade))
        ff.stdin.write(out.tobytes())
        if i % 300 == 0:
            print(f"frame {i}/{len(files)}", flush=True)
    ff.stdin.close()
    ff.wait()
    print("done", silent)
    clips = voice_clips(name, cards)
    if not clips:
        return
    voiced = os.path.join(OUT_VID, f"{name}_drone_voice.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", silent]
    for _, f, _ in clips:
        cmd += ["-i", f]
    chains = [f"[{k + 1}:a]adelay={int(s * 1000)}|{int(s * 1000)}[a{k}]" for k, (s, _, _) in enumerate(clips)]
    mix = "".join(f"[a{k}]" for k in range(len(clips)))
    chains.append(f"{mix}amix=inputs={len(clips)}:normalize=0,apad[out]")
    cmd += ["-filter_complex", ";".join(chains), "-map", "0:v", "-map", "[out]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-shortest", voiced]
    subprocess.check_call(cmd)
    print("done", voiced)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["esp", "finish"])
    ap.add_argument("config")
    ap.add_argument("frames", nargs="?")
    ap.add_argument("--height", type=float, default=70.0)
    ap.add_argument("--speed", type=float, default=22.0)
    a = ap.parse_args()
    name = os.path.splitext(os.path.basename(a.config))[0]
    if a.cmd == "finish":
        if not a.frames:
            raise SystemExit("finish needs the folder with the rendered frames")
        return finish(a.config, a.frames)
    cfg, pts = load(a.config)
    keys, L, T = flight(pts, a.height, a.speed)
    os.makedirs(OUT_ESP, exist_ok=True)
    out = os.path.join(OUT_ESP, f"{name}.esp")
    json.dump(esp(cfg, keys, T, f"{name} drone"), open(out, "w"))
    json.dump({"length_m": L, "duration_s": T, "height": a.height, "speed": a.speed,
               "keys": keys}, open(os.path.join(OUT_ESP, f"{name}_flight.json"), "w"))
    print(f"{out}: {L:.0f} m, {T:.0f} s ({int(round(T * FPS))} frames), {len(keys)} keyframes, "
          f"ground {min(k[3] for k in keys) - a.height:.0f}-{max(k[3] for k in keys) - a.height:.0f} m")
    for c in timeline(cfg, L, T):
        print(f"  {c[0]:6.1f}-{c[1]:6.1f} s  {c[2]} {c[3]}")


if __name__ == "__main__":
    main()
