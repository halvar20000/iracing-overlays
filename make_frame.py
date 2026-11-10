"""Render picture frames for OBS (frames/*.png).

A transparent PNG with a dark border, orange accent edges and a soft shadow;
the inside is see-through. In OBS put the picture (image / media / browser
source) UNDER the frame and size it to the opening - the opening is exactly
INNER pixels, at PAD from the top-left of the PNG (the title bar, if any,
sits above the opening).

    python make_frame.py                       # the default set
    python make_frame.py --inner 1200x900 --title "FAHRER DES TAGES" --out frames/x.png
"""
import argparse
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ORANGE = (255, 122, 0, 255)
DARK_A = (21, 21, 27)
DARK_B = (43, 43, 53)
BORDER = 14            # dark frame width around the opening
SHADOW = 36            # room for the drop shadow
TITLE_H = 64           # title bar height
FONTS = ["/usr/share/fonts/truetype/noto/NotoSansDisplay-BoldItalic.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-BoldItalic.ttf",
         "C:/Windows/Fonts/arialbi.ttf"]


def font(size):
    for f in FONTS:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def gradient(w, h):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for x in range(w):
        f = x / max(w - 1, 1)
        d.line([(x, 0), (x, h)], fill=tuple(int(DARK_A[i] + (DARK_B[i] - DARK_A[i]) * f) for i in range(3)))
    return img.convert("RGBA")


def render(iw, ih, title=None, bar=False):
    """bar=True: empty title bar (for an OBS text source on top)."""
    th = TITLE_H if (title or bar) else 0
    fw, fh = iw + 2 * BORDER, ih + 2 * BORDER + th      # the frame itself
    W, H = fw + 2 * SHADOW, fh + 2 * SHADOW
    x0, y0 = SHADOW, SHADOW
    img = Image.new("RGBA", (W, H))
    # shadow
    sh = Image.new("RGBA", (W, H))
    ImageDraw.Draw(sh).rounded_rectangle([x0 + 6, y0 + 10, x0 + fw + 6, y0 + fh + 10], 12, fill=(0, 0, 0, 150))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(SHADOW // 2)))
    # dark frame with a hole
    mask = Image.new("L", (W, H))
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([x0, y0, x0 + fw, y0 + fh], 10, fill=255)
    ox, oy = x0 + BORDER, y0 + BORDER + th              # opening
    md.rectangle([ox, oy, ox + iw - 1, oy + ih - 1], fill=0)
    # the shadow must not show through the opening
    hole = Image.new("L", (W, H), 255)
    ImageDraw.Draw(hole).rectangle([ox, oy, ox + iw - 1, oy + ih - 1], fill=0)
    img.putalpha(Image.composite(img.getchannel("A"), Image.new("L", (W, H)), hole))
    panel = Image.new("RGBA", (W, H))
    panel.paste(gradient(fw, fh), (x0, y0))
    img.paste(panel, (0, 0), mask)
    d = ImageDraw.Draw(img)
    # thin light inner line round the opening
    d.rectangle([ox - 2, oy - 2, ox + iw + 1, oy + ih + 1], outline=(255, 255, 255, 60), width=2)
    # orange accents: top edge, and corner brackets bottom-left / top-right
    d.rectangle([x0 + 10, y0, x0 + fw - 10, y0 + 4], fill=ORANGE)
    L = min(iw, ih) // 6
    d.polygon([(x0, y0 + fh - L), (x0 + 6, y0 + fh - L), (x0 + 6, y0 + fh - 6), (x0 + L, y0 + fh - 6),
               (x0 + L, y0 + fh), (x0, y0 + fh)], fill=ORANGE)
    d.polygon([(x0 + fw - L, y0 + 4), (x0 + fw, y0 + 4), (x0 + fw, y0 + 4 + L), (x0 + fw - 6, y0 + 4 + L),
               (x0 + fw - 6, y0 + 10), (x0 + fw - L, y0 + 10)], fill=ORANGE)
    if th:
        # orange tag + title text in the bar
        ty = y0 + BORDER + 6
        d.polygon([(x0 + BORDER + 16, ty + 4), (x0 + BORDER + 30, ty + 4),
                   (x0 + BORDER + 22, ty + th - 14), (x0 + BORDER + 8, ty + th - 14)], fill=ORANGE)
        if title:
            f = font(38)
            d.text((x0 + BORDER + 44, ty + (th - 20) / 2), title.upper(), font=f, fill=(255, 255, 255, 255),
                   anchor="lm")
    return img, (ox, oy, iw, ih)


DEFAULTS = [  # file, inner w, inner h, title, empty bar
    ("rahmen_16x9.png", 1280, 720, None, False),
    ("rahmen_16x9_titel.png", 1280, 720, None, True),
    ("rahmen_16x9_gross.png", 1600, 900, None, False),
    ("rahmen_4x3.png", 960, 720, None, False),
    ("rahmen_hoch.png", 608, 1080 - 2 * (BORDER + SHADOW), None, False),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inner", help="opening WxH, e.g. 1280x720")
    ap.add_argument("--title", help="text in a title bar above the opening")
    ap.add_argument("--bar", action="store_true", help="empty title bar")
    ap.add_argument("--out", help="output PNG")
    a = ap.parse_args()
    os.makedirs("frames", exist_ok=True)
    jobs = DEFAULTS
    if a.inner:
        w, h = (int(v) for v in a.inner.lower().split("x"))
        jobs = [(a.out or f"frames/rahmen_{w}x{h}.png", w, h, a.title, a.bar)]
    for name, w, h, title, bar in jobs:
        path = name if os.sep in name or "/" in name else os.path.join("frames", name)
        img, (ox, oy, iw, ih) = render(w, h, title, bar)
        img.save(path, optimize=True)
        print(f"{path}: {img.width}x{img.height}, opening {iw}x{ih} at x={ox} y={oy}")


if __name__ == "__main__":
    main()
