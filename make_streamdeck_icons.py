"""
make_streamdeck_icons.py — Stream Deck button images for every overlay
-----------------------------------------------------------------------
Writes 288 × 288 PNGs (sharp on the Stream Deck XL / + too) into
`streamdeck_icons/`:

  overlays/   one per OBS loader (same names as obs_loaders/*.html), so a
              button "OBS: source on/off" can be given the matching picture
  dashboard/  camera groups + the dashboard's /streamdeck/<action> commands
  overlays_off/, dashboard_off/  the same pictures greyed out (Andreas's
              design, 2026-10-11) as the second state of a toggle button:
              OBS source hidden / auto mode off. Same file names.

Each image: a coloured strip with the CATEGORY (LIVE, ZWEIKAMPF, QUALI, …,
same groups as OBS_OVERLAYS.md — the colour finds the group at a glance), a
line-art symbol in that colour and a short name.

The PNGs are committed, so nobody has to run this. To change or add icons:
edit the lists below and run `python make_streamdeck_icons.py`. Rendering
uses a headless Chromium-type browser (Microsoft Edge on Windows, chromium /
google-chrome elsewhere) — no extra Python package.
"""

import html
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "streamdeck_icons"
SIZE = 288

# Category → (strip text, colour)
CAT = {
    "live":   ("LIVE",           "#ff6b35"),
    "battle": ("ZWEIKAMPF",      "#f43f8e"),
    "champ":  ("MEISTERSCHAFT",  "#22c9e0"),
    "quali":  ("QUALIFYING",     "#19d36b"),
    "pre":    ("VOR DEM RENNEN", "#a371f7"),
    "post":   ("NACH DEM RENNEN", "#ffd166"),
    "tool":   ("BEDIENUNG",      "#9aa3b5"),
    "cam":    ("KAMERA",         "#61b4ff"),
    "replay": ("REPLAY",         "#ff4d5e"),
    "auto":   ("AUTOMATIK",      "#ffb347"),
}

# Line-art symbols, 24 × 24 viewBox, drawn with currentColor.
# F = filled shape, everything else is a 2-unit round stroke.
G = {
    "tower": '<rect x="3" y="3.5" width="4" height="3.5" rx=".6" class="F"/><path d="M9.5 5.25h11"/>'
             '<rect x="3" y="10.25" width="4" height="3.5" rx=".6" class="F"/><path d="M9.5 12h11"/>'
             '<rect x="3" y="17" width="4" height="3.5" rx=".6" class="F"/><path d="M9.5 18.75h11"/>',
    "stopwatch": '<circle cx="12" cy="13.5" r="7.5"/><path d="M12 13.5V9.5M10 2.5h4M12 2.5v3.5M18.5 6.5l1.5-1.5"/>',
    "updown": '<path d="M7 20V5M3.5 8.5 7 5l3.5 3.5M17 4v15M13.5 15.5 17 19l3.5-3.5"/>',
    "gapbar": '<path d="M2.5 12h19"/><circle cx="5" cy="12" r="2.2" class="F"/><circle cx="11" cy="12" r="2.2" class="F"/>'
              '<circle cx="14.5" cy="12" r="2.2" class="F"/><circle cx="20" cy="12" r="2.2" class="F"/>',
    "overtake": '<path d="M3 16h12M11 12l4 4-4 4M9 8h12M17 4l4 4-4 4"/>',
    "card": '<rect x="2.5" y="5" width="19" height="14" rx="2"/><circle cx="8" cy="11" r="2.2"/>'
            '<path d="M4.8 16.5c.6-1.8 1.8-2.6 3.2-2.6s2.6.8 3.2 2.6M14 10h5M14 14h4"/>',
    "car": '<path d="M3 15.5v-3l2.5-4.5h9l4 4.5 2.5 1v2H3z"/><circle cx="7.5" cy="16.5" r="2" class="F"/>'
           '<circle cx="16.5" cy="16.5" r="2" class="F"/><path d="M7 11.8h10.5"/>',
    "map": '<path d="M5 18c-2-2-1.5-5 .5-6.5S9 9 9 6.5 12 2.5 15 4.5s1 5 3 6.5 3.5 4 1.5 6.5-6 1-8 .5S7 20 5 18z"/>'
           '<circle cx="15" cy="4.6" r="1.8" class="F"/>',
    "flag": '<path d="M5 21.5V3"/><path d="M5 3.5h15v10H5" stroke-width="1.6"/>'
            '<rect x="5" y="3.5" width="3.75" height="3.33" class="F"/><rect x="12.5" y="3.5" width="3.75" height="3.33" class="F"/>'
            '<rect x="8.75" y="6.83" width="3.75" height="3.33" class="F"/><rect x="16.25" y="6.83" width="3.75" height="3.33" class="F"/>'
            '<rect x="5" y="10.16" width="3.75" height="3.34" class="F"/><rect x="12.5" y="10.16" width="3.75" height="3.34" class="F"/>',
    "crown": '<path d="M3 18 2 7l5.5 4.5L12 4l4.5 7.5L22 7l-1 11z"/><path d="M3.5 21h17"/>',
    "cloud": '<path d="M7 19h10.5a4 4 0 0 0 .4-8A6 6 0 0 0 6.4 9.6 4.7 4.7 0 0 0 7 19z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2.5"/>',
    "broadcast": '<circle cx="12" cy="12" r="2.6" class="F"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4'
                 'M4.6 4.6a10.5 10.5 0 0 0 0 14.8M19.4 4.6a10.5 10.5 0 0 1 0 14.8"/>',
    "turn": '<path d="M5 21v-7a6 6 0 0 1 6-6h8"/><path d="M15.5 4.5 19 8l-3.5 3.5"/>',
    "chart": '<path d="M3 3v18h18"/><path d="M6.5 15l4-5 3.5 3 5.5-7"/>',
    "trophy": '<path d="M7 3.5h10v5a5 5 0 0 1-10 0z"/><path d="M7 5.5H3.5c0 3 1.5 4.5 3.8 4.8M17 5.5h3.5c0 3-1.5 4.5-3.8 4.8'
              'M12 13.5V17M8 21h8M9 17h6v4H9z"/>',
    "vs": '<text x="12" y="16.8" text-anchor="middle" class="T" font-size="13">VS</text>',
    "star": '<path d="M12 2.8l2.8 5.9 6.4.8-4.7 4.4 1.2 6.4L12 17.2l-5.7 3.1 1.2-6.4-4.7-4.4 6.4-.8z"/>',
    "delta": '<path d="M12 3.5 21.5 20h-19z"/>',
    "delta_own": '<path d="M12 3.5 21.5 20h-19z"/><circle cx="12" cy="12.6" r="1.9" class="F"/><path d="M8.6 17.6c.7-1.4 1.9-2.1 3.4-2.1s2.7.7 3.4 2.1"/>',
    "grid": '<rect x="4" y="3" width="6" height="4" rx=".8"/><rect x="14" y="6.5" width="6" height="4" rx=".8"/>'
            '<rect x="4" y="10" width="6" height="4" rx=".8"/><rect x="14" y="13.5" width="6" height="4" rx=".8"/>'
            '<rect x="4" y="17" width="6" height="4" rx=".8"/>',
    "target": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1.3" class="F"/>'
              '<path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3"/>',
    "gap": '<text x="12" y="15.6" text-anchor="middle" class="T" font-size="9.5">+0.8s</text><path d="M3 20h18"/>'
           '<path d="M3 18v4M21 18v4" stroke-width="1.6"/>',
    "chase": '<circle cx="18.5" cy="12" r="3" class="F"/><path d="M2.5 12h10M9 8l4 4-4 4"/>',
    "bars4": '<path d="M3 21h18"/><rect x="4" y="9" width="3" height="10" rx=".5" class="F"/><rect x="8.5" y="6" width="3" height="13" rx=".5" class="F"/>'
             '<rect x="13" y="11" width="3" height="8" rx=".5" class="F"/><rect x="17.5" y="8" width="3" height="11" rx=".5" class="F"/>',
    "podium": '<path d="M2.5 21V14h6V9h7v7h6v5z"/><path d="M12 6.5V2.8l-1.2 1"/>',
    "table": '<rect x="3" y="4" width="18" height="16" rx="1.5"/><path d="M3 9h18M3 14h18M9 4v16"/>',
    "rsvp": '<rect x="3" y="4.5" width="18" height="16" rx="2"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4M8.5 15l2.5 2.5 5-5"/>',
    "barchart": '<path d="M3 21h18"/><rect x="5" y="12" width="3.2" height="7" class="F"/><rect x="10.4" y="6" width="3.2" height="13" class="F"/>'
                '<rect x="15.8" y="9" width="3.2" height="10" class="F"/>',
    "list": '<path d="M8.5 6h12M8.5 12h12M8.5 18h12"/><circle cx="4.5" cy="6" r="1.4" class="F"/>'
            '<circle cx="4.5" cy="12" r="1.4" class="F"/><circle cx="4.5" cy="18" r="1.4" class="F"/>',
    "gauge": '<path d="M3.5 17a8.5 8.5 0 1 1 17 0"/><path d="M12 17l4.5-5.5"/><circle cx="12" cy="17" r="1.6" class="F"/>',
    "doc": '<path d="M6 2.5h8l4.5 4.5v14.5H6z"/><path d="M14 2.5V7h4.5M9 12h6.5M9 15.5h6.5M9 19h4"/>',
    "chat": '<path d="M4 4.5h16a1.5 1.5 0 0 1 1.5 1.5v9.5a1.5 1.5 0 0 1-1.5 1.5H10l-5 4v-4H4A1.5 1.5 0 0 1 2.5 15.5V6A1.5 1.5 0 0 1 4 4.5z"/>'
            '<path d="M7 9h10M7 12.5h6"/>',
    "play": '<rect x="2" y="5" width="20" height="14" rx="4"/><path d="M10 9v6l5-3z" class="F"/>',
    "curve": '<path d="M4 20c0-6 3-8 8-8s8-3 8-8"/><path d="M16.5 4H20v3.5"/>',
    "camera": '<path d="M3 8.5a1.5 1.5 0 0 1 1.5-1.5H7l1.6-2.5h6.8L17 7h2.5A1.5 1.5 0 0 1 21 8.5v10a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/>'
              '<circle cx="12" cy="13" r="3.7"/>',
    "next": '<path d="M5 5l8 7-8 7M13 5l8 7-8 7"/>',
    "prev": '<path d="M19 5l-8 7 8 7M11 5l-8 7 8 7"/>',
    "driver_next": '<circle cx="9" cy="8" r="3.4"/><path d="M3 20c.6-3.6 3-5.5 6-5.5s5.4 1.9 6 5.5M16.5 9l4 3.5-4 3.5"/>',
    "driver_prev": '<circle cx="15" cy="8" r="3.4"/><path d="M21 20c-.6-3.6-3-5.5-6-5.5s-5.4 1.9-6 5.5M7.5 9l-4 3.5 4 3.5"/>',
    "replay": '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M3.5 3.5v4.2h4.2"/><path d="M10 9v6l4.8-3z" class="F"/>',
    "golive": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5" class="F"/>',
    "eye_off": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>'
               '<path d="M4 3.5l16 17"/>',
    "follow": '<circle cx="12" cy="12" r="3" class="F"/><path d="M12 2.5v4M12 17.5v4M2.5 12h4M17.5 12h4"/><circle cx="12" cy="12" r="7"/>',
    "auto_replay": '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M3.5 3.5v4.2h4.2"/>'
                   '<text x="12.6" y="15.6" text-anchor="middle" class="T" font-size="8.5">A</text>',
    "auto_ot": '<path d="M3 16h12M11 12l4 4-4 4M9 8h12M17 4l4 4-4 4"/>',
    "crash": '<path d="M12 2.5l2.2 5.6 5.8-1.6-3.3 5 4.8 3.7-6 .6.3 6-3.8-4.6-3.8 4.6.3-6-6-.6 4.8-3.7-3.3-5 5.8 1.6z"/>',
}

# (file name, category, symbol, label) — file names match obs_loaders/*.html.
OVERLAY_ICONS = [
    ("standings",     "live",   "tower",     "Tower"),
    ("fastest",       "live",   "stopwatch", "Schnellste Runde"),
    ("movers",        "live",   "updown",    "Movers"),
    ("gapbar",        "live",   "gapbar",    "Gap Bar"),
    ("ticker",        "live",   "overtake",  "Überhol Ticker"),
    ("driver",        "live",   "card",      "Driver Card"),
    ("driver_simple", "live",   "card",      "Driver Card simpel"),
    ("livery",        "live",   "car",       "Livery"),
    ("trackmap",      "live",   "map",       "Track Map"),
    ("flag",          "live",   "flag",      "Flaggen"),
    ("leader",        "live",   "crown",     "Neuer Führender"),
    ("weather",       "live",   "cloud",     "Wetter"),
    ("sess",          "live",   "clock",     "Restzeit"),
    ("live",          "live",   "broadcast", "LIVE / Replay"),
    ("turn",          "live",   "turn",      "Kurve"),
    ("logger_chart",  "live",   "chart",     "Renn-Grafik"),
    ("twitch_chat",   "live",   "chat",      "Twitch Chat"),
    ("youtube",       "live",   "play",      "YouTube"),
    ("catch",         "battle", "target",    "Catch-Up"),
    ("battle_gap",    "battle", "gap",       "Abstand"),
    ("chasing",       "battle", "chase",     "Chasing"),
    ("last_laps",     "battle", "bars4",     "Letzte 4 Runden"),
    ("champ",         "champ",  "trophy",    "Meisterschaft"),
    ("duel",          "champ",  "vs",        "Titelkampf"),
    ("dotd",          "champ",  "star",      "Driver of the Day"),
    ("delta",         "quali",  "delta",     "Delta Pole"),
    ("delta_own",     "quali",  "delta_own", "Delta eigene"),
    ("grid",          "quali",  "grid",      "Start Grid"),
    ("lastrace",      "pre",    "podium",    "Letzte Runde"),
    ("table",         "pre",    "table",     "Tabelle"),
    ("rsvp",          "pre",    "rsvp",      "RSVP"),
    ("stats",         "pre",    "barchart",  "Statistik"),
    ("lastdotd",      "pre",    "star",      "DotD letzte Runde"),
    ("results",       "post",   "podium",    "Ergebnis"),
    ("results_lite",  "post",   "list",      "Ergebnis Lite"),
    ("dashboard",     "tool",   "gauge",     "Dashboard"),
    ("logger",        "tool",   "doc",       "Race Logger"),
    ("line",          "tool",   "curve",     "Corner Cues"),
]

# Dashboard (port 5000) Stream Deck commands; the URL goes into a
# "Website" action with "Access in background" ticked.
CAMS = ["TV1", "TV2", "TV3", "Chase", "Far Chase", "Rear Chase", "Blimp",
        "Chopper", "Pit Lane", "Cockpit", "Nose", "Gearbox", "Gyro"]
DASH_ICONS = [
    ("cam_next",          "cam",    "next",        "Kamera vor",        "cam_next"),
    ("cam_prev",          "cam",    "prev",        "Kamera zurück",     "cam_prev"),
    ("driver_next",       "cam",    "driver_next", "Fahrer vor",        "driver_next"),
    ("driver_prev",       "cam",    "driver_prev", "Fahrer zurück",     "driver_prev"),
    ("go_live",           "replay", "golive",      "Go Live",           "go_live"),
    ("replay_last",       "replay", "replay",      "Replay letzter",    "replay_last"),
    ("replay_last_spin",  "replay", "crash",       "Replay Dreher",     "replay_last_lost_control"),
    ("hide_ui",           "tool",   "eye_off",     "iRacing UI aus",    "hide_ui"),
    ("auto_follow",       "auto",   "follow",      "Auto-Follow",       "toggle_auto_follow"),
    ("auto_replay",       "auto",   "auto_replay", "Auto-Replay Unfälle", "toggle_auto_replay"),
    ("auto_replay_ot",    "auto",   "auto_ot",     "Auto-Replay Überholer", "toggle_auto_replay_overtakes"),
    # a PAGE, not a /streamdeck command: Website action without "background"
    ("ot_panel",          "tool",   "overtake",    "Überhol Fenster",   "/overtakes/panel"),
]

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;width:%(s)dpx;height:%(s)dpx;overflow:hidden;background:#000}
.k{width:%(s)dpx;height:%(s)dpx;box-sizing:border-box;position:relative;
   background:radial-gradient(circle at 50%% 38%%,%(bg1)s 0%%,%(bg2)s 70%%);
   font-family:'Segoe UI','Noto Sans','DejaVu Sans',Arial,sans-serif;color:#fff}
.strip{position:absolute;left:0;right:0;top:0;height:46px;background:%(c)s;
   color:#0b0b10;font-weight:900;font-size:%(cs)dpx;letter-spacing:2px;
   display:flex;align-items:center;justify-content:center;white-space:nowrap}
svg{position:absolute;left:50%%;top:%(gy)dpx;transform:translateX(-50%%);color:%(c)s;
   filter:drop-shadow(0 3px 6px rgba(0,0,0,.6))}
svg *{fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
svg .F{fill:currentColor;stroke:none}
svg .T{fill:currentColor;stroke:none;font-weight:900;font-family:inherit}
.lbl{position:absolute;left:10px;right:10px;bottom:16px;text-align:center;
   font-weight:800;font-size:%(fs)dpx;line-height:1.08;letter-spacing:.3px;
   text-shadow:0 2px 4px rgba(0,0,0,.7)}
</style></head><body><div class="k"><div class="strip">%(cat)s</div>
<svg width="%(gs)d" height="%(gs)d" viewBox="0 0 24 24">%(glyph)s</svg>
<div class="lbl">%(label)s</div></div></body></html>"""


def _label_html(label):
    """Two lines at most; long single words shrink instead of wrapping."""
    words = label.split()
    if len(label) <= 11 or len(words) == 1:
        return html.escape(label), 1, len(label)
    # best split: the most even two lines
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = max(len(a), len(b))
        if best is None or score < best[0]:
            best = (score, a, b)
    return html.escape(best[1]) + "<br>" + html.escape(best[2]), 2, best[0]


# "off" state: strip + symbol grey, neutral (not blue-tinted) background,
# label stays white — sampled from Andreas's ticker / trackmap examples.
OFF_COLOR = "#949494"
BG_ON = ("#262633", "#14141c")
BG_OFF = ("#202020", "#111111")
# dashboard commands that toggle something get an off picture too
DASH_TOGGLES = {"auto_follow", "auto_replay", "auto_replay_ot", "hide_ui"}


def render_html(cat, glyph, label, off=False):
    cat_text, color = CAT[cat]
    if off:
        color = OFF_COLOR
    bg1, bg2 = BG_OFF if off else BG_ON
    lbl, lines, longest = _label_html(label)
    fs = 44 if longest <= 7 else 38 if longest <= 9 else 33 if longest <= 11 else 28 if longest <= 14 else 24
    gs = 132 if lines == 1 else 112
    gy = 64 if lines == 1 else 60
    return PAGE % {"s": SIZE, "c": color, "bg1": bg1, "bg2": bg2, "cs": 22 if len(cat_text) <= 11 else 18,
                   "cat": html.escape(cat_text), "glyph": G[glyph], "gs": gs, "gy": gy,
                   "fs": fs, "label": lbl}


def find_browser():
    cands = [
        os.environ.get("CHROME"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    cands += [shutil.which(n) for n in ("chromium", "chromium-browser", "google-chrome", "msedge")]
    for c in cands:
        if c and Path(c).is_file():
            return c
    return None


def shoot(browser, page_html, out_png, tmpdir):
    src = Path(tmpdir) / "icon.html"
    src.write_text(page_html, encoding="utf-8")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([browser, "--headless", "--no-sandbox", "--disable-gpu",
                    "--hide-scrollbars", "--force-device-scale-factor=1",
                    "--window-size=%d,%d" % (SIZE, SIZE),
                    "--screenshot=" + str(out_png), src.as_uri()],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                   timeout=60)
    if not out_png.is_file():
        raise RuntimeError("browser wrote no image for " + out_png.name)


def all_icons():
    """(path, cat, glyph, label, off) for every picture."""
    for name, cat, glyph, label in OVERLAY_ICONS:
        yield Path("overlays") / (name + ".png"), cat, glyph, label, False
        yield Path("overlays_off") / (name + ".png"), cat, glyph, label, True
    for cam in CAMS:
        yield (Path("dashboard") / ("cam_" + cam.lower().replace(" ", "_") + ".png"),
               "cam", "camera", cam, False)
    for name, cat, glyph, label, _ in DASH_ICONS:
        yield Path("dashboard") / (name + ".png"), cat, glyph, label, False
        if name in DASH_TOGGLES:
            yield Path("dashboard_off") / (name + ".png"), cat, glyph, label, True


def main():
    # optional args = only these folders, e.g. `overlays_off dashboard_off`
    only = set(sys.argv[1:])
    browser = find_browser()
    if not browser:
        print("No Edge / Chrome / Chromium found (set CHROME=<path to browser>).")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        n = 0
        for rel, cat, glyph, label, off in all_icons():
            if only and rel.parent.name not in only:
                continue
            shoot(browser, render_html(cat, glyph, label, off), OUT / rel, tmp)
            n += 1
    print("%d icons written to %s" % (n, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
