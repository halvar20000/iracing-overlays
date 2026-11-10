"""Render the OBS REPLAY stinger (stingers/replay_stinger.webm).

A dark panel with an orange leading stripe sweeps in from the left, covers
the whole picture with "REPLAY" for a moment (the cut happens behind it),
then wipes off to the right. 1920 x 1080, 60 fps, VP9 with alpha.

OBS: Scene transitions > + > Stinger, this file, transition point
TRANSITION_MS (the picture is fully covered from COVER_IN to COVER_OUT).

    python make_replay_stinger.py [--text REPLAY] [--out stingers/replay_stinger.webm]
"""
import argparse
import os
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 60
DURATION = 1.10          # s
COVER_IN = 0.36          # panel fully across the picture
COVER_OUT = 0.64         # panel starts leaving
TRANSITION_MS = 500      # cut point for OBS (inside COVER_IN..COVER_OUT)
SKEW = 320               # px the top edge runs ahead of the bottom
ORANGE = (255, 122, 0, 255)
DARK_A = (21, 21, 27)
DARK_B = (43, 43, 53)
FONTS = ["/usr/share/fonts/truetype/noto/NotoSansDisplay-BoldItalic.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-BoldItalic.ttf",
         "C:/Windows/Fonts/arialbi.ttf"]


def font(size):
    for f in FONTS:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def ease_out(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    x = min(max(x, 0.0), 1.0)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def band(a, b):
    """Parallelogram between x = a and x = b at the top, leaning back."""
    return [(a, 0), (b, 0), (b - SKEW, H), (a - SKEW, H)]


def panel_art(text):
    """The full-screen panel picture (gradient, stripes, title)."""
    img = Image.new("RGBA", (W, H))
    px = img.load()
    for x in range(W):                       # horizontal gradient
        f = x / (W - 1)
        c = tuple(int(DARK_A[i] + (DARK_B[i] - DARK_A[i]) * f) for i in range(3)) + (255,)
        for y in range(0, H, 1):
            px[x, y] = c
    tex = Image.new("RGBA", (W, H))          # drawn on its own layer: ImageDraw
    d = ImageDraw.Draw(tex)                  # would REPLACE the panel's alpha
    for x in range(-H, W + H, 90):           # faint diagonal texture
        d.polygon([(x, 0), (x + 34, 0), (x + 34 - SKEW, H), (x - SKEW, H)], fill=(255, 255, 255, 7))
    d.rectangle([0, H // 2 + 150, W, H // 2 + 156], fill=(255, 122, 0, 70))
    img.alpha_composite(tex)
    return img


def title_layer(text):
    img = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(img)
    f = font(230)
    tw = d.textlength(text, font=f)
    x, y = (W - tw) / 2 + 40, H / 2 - 175
    shadow = Image.new("RGBA", (W, H))
    ImageDraw.Draw(shadow).text((x + 8, y + 10), text, font=f, fill=(0, 0, 0, 160))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
    d.text((x, y), text, font=f, fill=(255, 255, 255, 255))
    # orange tag left of the word + underline
    tx = x - 110
    d.polygon([(tx + 40, H / 2 - 95), (tx + 80, H / 2 - 95), (tx + 40, H / 2 + 55), (tx, H / 2 + 55)], fill=ORANGE)
    d.polygon([(x + 30, H / 2 + 85), (x + tw + 10, H / 2 + 85), (x + tw - 4, H / 2 + 101), (x + 16, H / 2 + 101)],
              fill=ORANGE)
    return img


def frame(t, art, title):
    out = Image.new("RGBA", (W, H))
    far = W + SKEW + 400
    if t <= COVER_OUT:                       # coming in: leading edge moves right
        lead = -40 + ease_out(t / COVER_IN) * (W + SKEW + 40)
        a, b = -far, lead
        stripes = [(lead + 14, lead + 70, ORANGE), (lead + 88, lead + 104, (255, 255, 255, 230))]
    else:                                    # going out: trailing edge moves right
        tr = ease_in_out((t - COVER_OUT) / (DURATION - COVER_OUT - 0.02)) * (W + SKEW + 160)
        a, b = tr, far
        stripes = [(tr - 70, tr - 14, ORANGE), (tr - 104, tr - 88, (255, 255, 255, 230))]
    mask = Image.new("L", (W, H))
    ImageDraw.Draw(mask).polygon(band(a, b), fill=255)
    layer = art.copy()
    # title: slides in with the panel, drifts while covered, leaves with it
    if t <= COVER_IN:
        dx, alpha = -260 * (1 - ease_out(t / COVER_IN)), ease_out(t / COVER_IN)
    else:
        dx, alpha = 40 * (t - COVER_IN) / (DURATION - COVER_IN), 1.0
    tl = title.transform((W, H), Image.AFFINE, (1, 0, -dx, 0, 1, 0))
    if alpha < 1:
        tl.putalpha(tl.getchannel("A").point(lambda v: int(v * alpha)))
    layer.alpha_composite(tl)
    out.paste(layer, (0, 0), mask)
    d = ImageDraw.Draw(out, "RGBA")
    for s0, s1, col in stripes:
        d.polygon(band(s0, s1), fill=col)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="REPLAY")
    ap.add_argument("--out", default=os.path.join("stingers", "replay_stinger.webm"))
    ap.add_argument("--frames", help="also keep the PNG frames in this folder")
    args = ap.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found")
    art, title = panel_art(args.text), title_layer(args.text)
    tmp = args.frames or tempfile.mkdtemp(prefix="stinger_")
    os.makedirs(tmp, exist_ok=True)
    n = round(DURATION * FPS)
    for i in range(n):
        frame(i / FPS, art, title).save(os.path.join(tmp, f"f{i:03d}.png"))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", os.path.join(tmp, "f%03d.png"),
                    "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-b:v", "0", "-crf", "24",
                    "-row-mt", "1", args.out], check=True)
    if not args.frames:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{args.out}: {n} frames, {DURATION:.2f} s, transition point {TRANSITION_MS} ms")


if __name__ == "__main__":
    main()
