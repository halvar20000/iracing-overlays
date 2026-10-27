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
    # three sector pieces above, their sum (one full lap) below
    "sectors": '<rect x="2.5" y="5" width="5.5" height="5" rx="1" class="F"/><rect x="9.25" y="5" width="5.5" height="5" rx="1" class="F"/>'
               '<rect x="16" y="5" width="5.5" height="5" rx="1" class="F"/><rect x="2.5" y="14" width="19" height="5" rx="1.2"/>',
    # speedometer with scale ticks, needle near the top end
    "speedo": '<path d="M3.5 17.5a8.5 8.5 0 1 1 17 0"/>'
              '<path d="M5.4 13.7l1.4.8M8.2 10.9l.8 1.4M12 9.9v1.6M15.8 10.9l-.8 1.4M18.6 13.7l-1.4.8"/>'
              '<path d="M12 17.5l4.6-3.9"/><circle cx="12" cy="17.5" r="1.7" class="F"/>',
    "gauge": '<path d="M3.5 17a8.5 8.5 0 1 1 17 0"/><path d="M12 17l4.5-5.5"/><circle cx="12" cy="17" r="1.6" class="F"/>',
    "doc": '<path d="M6 2.5h8l4.5 4.5v14.5H6z"/><path d="M14 2.5V7h4.5M9 12h6.5M9 15.5h6.5M9 19h4"/>',
    "chat": '<path d="M4 4.5h16a1.5 1.5 0 0 1 1.5 1.5v9.5a1.5 1.5 0 0 1-1.5 1.5H10l-5 4v-4H4A1.5 1.5 0 0 1 2.5 15.5V6A1.5 1.5 0 0 1 4 4.5z"/>'
            '<path d="M7 9h10M7 12.5h6"/>',
    "mic": '<rect x="9" y="2.5" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/>',
    "speaker": '<path d="M3.5 9.5h4l5-4.5v14l-5-4.5h-4z"/><path d="M16 9a4 4 0 0 1 0 6M18.8 6.2a8 8 0 0 1 0 11.6"/>',
    "stop": '<rect x="3" y="3" width="18" height="18" rx="4"/><rect x="8.5" y="8.5" width="7" height="7" rx="1" class="F"/>',
    "play": '<rect x="2" y="5" width="20" height="14" rx="4"/><path d="M10 9v6l5-3z" class="F"/>',
    "film": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 8h4M3 12h4M3 16h4M17 8h4M17 12h4M17 16h4"/><path d="M10.5 9.5v5l4-2.5z" class="F"/>',
    "curve": '<path d="M4 20c0-6 3-8 8-8s8-3 8-8"/><path d="M16.5 4H20v3.5"/>',
    "camera": '<path d="M3 8.5a1.5 1.5 0 0 1 1.5-1.5H7l1.6-2.5h6.8L17 7h2.5A1.5 1.5 0 0 1 21 8.5v10a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/>'
              '<circle cx="12" cy="13" r="3.7"/>',
    "next": '<path d="M5 5l8 7-8 7M13 5l8 7-8 7"/>',
    "prev": '<path d="M19 5l-8 7 8 7M11 5l-8 7 8 7"/>',
    "drivers": '<circle cx="9" cy="8" r="3.4"/><path d="M3 20c.6-3.6 3-5.5 6-5.5s5.4 1.9 6 5.5M16 7h5M16 11.5h5M17.5 16h3.5"/>',
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
    "tire": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.6"/>'
            '<path d="M12 3v3.2M12 17.8V21M3 12h3.2M17.8 12H21M5.6 5.6l2.3 2.3M16.1 16.1l2.3 2.3M5.6 18.4l2.3-2.3M16.1 7.9l2.3-2.3"/>',
    "crash": '<path d="M12 2.5l2.2 5.6 5.8-1.6-3.3 5 4.8 3.7-6 .6.3 6-3.8-4.6-3.8 4.6.3-6-6-.6 4.8-3.7-3.3-5 5.8 1.6z"/>',
}

# (file name, category, symbol, label) — file names match obs_loaders/*.html.
OVERLAY_ICONS = [
    ("standings",     "live",   "tower",     "Tower"),
    ("standings_nodelta", "live", "tower",   "Tower ohne +/-"),
    ("standings_cars", "live",  "car",       "Tower Autos"),
    ("standings_cars_nodelta", "live", "car", "Tower Autos ohne +/-"),
    ("fastest",       "live",   "stopwatch", "Schnellste Runde"),
    ("movers",        "live",   "updown",    "Movers"),
    ("gapbar",        "live",   "gapbar",    "Gap Bar"),
    ("pittires",      "live",   "tire",      "Reifen Stopp"),
    ("pittires_list", "live",   "tire",      "Reifen Liste"),
    ("ticker",        "live",   "overtake",  "Überhol Ticker"),
    ("commentary",    "live",   "chat",      "Kommentar"),
    ("commentary_voice", "live", "speaker",  "Stimme"),
    ("driver",        "live",   "card",      "Driver Card"),
    ("driver_simple", "live",   "card",      "Driver Card simpel"),
    ("livery",        "live",   "car",       "Livery"),
    ("trackmap",      "live",   "map",       "Track Map"),
    ("location",      "pre",    "map",       "Streckenort"),
    ("flag",          "live",   "flag",      "Flaggen"),
    ("leader",        "live",   "crown",     "Neuer Führender"),
    ("rc_banner",     "live",   "flag",      "Race Control"),
    ("weather",       "live",   "cloud",     "Wetter"),
    ("sess",          "live",   "clock",     "Restzeit"),
    ("live",          "live",   "broadcast", "LIVE / Replay"),
    ("turn",          "live",   "turn",      "Kurve"),
    ("topspeed",      "live",   "speedo",    "Top Speed"),
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
    ("grid_cars",     "quali",  "car",       "Grid Autos"),
    ("grid_row",      "quali",  "car",       "Grid Reihe"),
    ("grid_row_top",  "quali",  "car",       "Grid Reihe oben"),
    ("sectors",       "quali",  "sectors",   "Beste Sektoren"),
    ("lastrace",      "pre",    "podium",    "Letzte Runde"),
    ("table",         "pre",    "table",     "Tabelle"),
    ("lastrace_pro",  "pre",    "podium",    "Letzte Runde PRO"),
    ("lastrace_am",   "pre",    "podium",    "Letzte Runde AM"),
    ("table_pro",     "pre",    "table",     "Tabelle PRO"),
    ("table_am",      "pre",    "table",     "Tabelle AM"),
    ("rsvp",          "pre",    "rsvp",      "RSVP"),
    ("stats",         "pre",    "barchart",  "Statistik"),
    ("lastdotd",      "pre",    "star",      "DotD letzte Runde"),
    ("results",       "post",   "podium",    "Ergebnis"),
    ("results_lite",  "post",   "list",      "Ergebnis Lite"),
    ("results_solid", "post",   "podium",    "Ergebnis Abspann"),
    ("results_wct",   "post",   "podium",    "Ergebnis neu"),
    ("lastrace_wct",  "pre",    "podium",    "Letzte Runde neu"),
    ("table_wct",     "pre",    "trophy",    "Tabelle neu"),
    ("teams_wct",     "pre",    "trophy",    "Teamwertung"),
    ("live_wct",      "live",   "trophy",    "Tabelle live"),
    ("champ_solid",   "post",   "trophy",    "Meisterschaft Abspann"),
    ("highlight_card", "post",  "film",      "Highlight Titel"),
    ("ask_status",    "live",   "mic",       "Frag Claude Status"),
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
    ("regie",             "auto",   "broadcast",   "REGIE",             "toggle_director"),
    # Sendeablauf (show_runner.py): the AI moderation before the race
    ("show_start",        "auto",   "play",        "Moderation Start",  "show_start"),
    ("show_next",         "auto",   "next",        "Moderation weiter", "show_next"),
    ("show_stop",         "auto",   "stop",        "Moderation Stopp",  "show_stop"),
    # Frag Claude (race_qa.py): press = listen, press again = answer
    ("ask",               "auto",   "mic",         "Frag Claude",       "ask"),
    # WCT-look pages (grid, results_wct, lastrace_wct, teams_wct): pages by
    # hand, http://localhost:5001/nav/<cmd> (the grid server holds the state)
    ("page_next",         "tool",   "next",        "Seite weiter",      "5001/nav/next"),
    ("page_prev",         "tool",   "prev",        "Seite zurück",      "5001/nav/prev"),
    ("page_first",        "tool",   "list",        "Seite 1",           "5001/nav/first"),
    ("page_auto",         "tool",   "clock",       "Auto blättern",     "5001/nav/auto"),
    # PAGES, not /streamdeck commands: Website action without "background"
    ("ot_panel",          "tool",   "overtake",    "Überhol Fenster",   "/overtakes/panel"),
    ("cam_panel",         "tool",   "camera",      "Kamera Fenster",    "/cameras/panel"),
    ("driver_panel",      "tool",   "drivers",     "Fahrer Fenster",    "/drivers/panel"),
    ("incident_panel",    "tool",   "crash",       "Incident Fenster",  "/incidents/panel"),
    ("show_page",         "tool",   "list",        "Sendeablauf",       "/show"),
    ("ask_page",          "tool",   "chat",        "Fragen",            "/ask"),
    ("highlights_page",   "tool",   "film",        "Highlight Video",   "/highlights"),
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
DASH_TOGGLES = {"auto_follow", "auto_replay", "auto_replay_ot", "hide_ui", "regie", "page_auto"}


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


TABLE = HERE / "OVERLAY_TABLE.md"
# overlays that are not in make_obs_loaders.OVERLAYS
EXTRA_PAGES = {
    "twitch_chat": ("Twitch Chat + viewer count", None, "", "obs_loaders/twitch_chat.html"),
    "youtube": ("YouTube picture-in-picture", 5005, "/youtube", None),
}


def write_table():
    """OVERLAY_TABLE.md — every overlay with port, OBS loader and both
    Stream Deck pictures, rendered as images on GitHub. Built from the
    loader list (make_obs_loaders.py) and OVERLAY_ICONS, so it is rewritten
    on every run and cannot drift."""
    sys.path.insert(0, str(HERE))
    import make_obs_loaders
    loaders = {}
    for e in make_obs_loaders.OVERLAYS:
        path = e[3] if len(e) > 3 else "/"
        loaders[e[0]] = (e[1], e[2], path, "obs_loaders/%s.html" % e[0])
    loaders.update(EXTRA_PAGES)
    rows = ["# Overlays — Ports, OBS-Loader, Stream-Deck-Symbole", "",
            "Automatisch erzeugt von `make_streamdeck_icons.py` (nicht von Hand "
            "ändern). Größen für OBS: [OBS_OVERLAYS.md](OBS_OVERLAYS.md) · "
            "Stream-Deck-Einrichtung: [streamdeck_icons/README.md](streamdeck_icons/README.md).",
            "", "„aus“ = zweiter Zustand einer Taste (OBS-Quelle versteckt).", "",
            "| Gruppe | Overlay | Port / Pfad | OBS-Loader | Symbol an | Symbol aus |",
            "|---|---|---|---|:---:|:---:|"]
    for name, cat, _glyph, label in OVERLAY_ICONS:
        friendly, port, path, loader = loaders[name]
        if port is None:
            where = "— (eigenständig)"
        else:
            where = "`%d`" % port + ("" if path == "/" else " `%s`" % path)
        ld = "[`%s`](%s)" % (loader.split("/")[-1], loader) if loader else \
             "keiner — URL-Quelle `http://localhost:%d%s`" % (port, path)
        img = '<img src="streamdeck_icons/%s/%s.png" width="64" alt="%s">'
        group = {"VOR DEM RENNEN": "Vor dem Rennen", "NACH DEM RENNEN": "Nach dem Rennen"
                 }.get(CAT[cat][0], CAT[cat][0].capitalize())
        title = "**%s**" % label + ("" if friendly.lower() == label.lower() else "<br>" + friendly)
        rows.append("| %s | %s | %s | %s | %s | %s |" % (
            group, title, where, ld,
            img % ("overlays", name, label), img % ("overlays_off", name, label + " aus")))
    # a loader without an OVERLAY_ICONS entry still gets a row (no picture)
    # and a warning, so a new overlay can never silently miss the table
    have = {e[0] for e in OVERLAY_ICONS}
    missing = [k for k in loaders if k not in have]
    for name in missing:
        friendly, port, path, loader = loaders[name]
        where = "`%d`" % port + ("" if path == "/" else " `%s`" % path) if port else "—"
        ld = "[`%s`](%s)" % (loader.split("/")[-1], loader) if loader else "—"
        rows.append("| ? | **%s** | %s | %s | fehlt | fehlt |" % (friendly, where, ld))
        print("WARNING: no Stream Deck icon for '%s' — add it to OVERLAY_ICONS "
              "in make_streamdeck_icons.py and run `python make_streamdeck_icons.py "
              "overlays overlays_off`" % name)
    rows += ["", "Bedienseiten (kein OBS): Überhol-Fenster `http://localhost:5000/overtakes/panel` "
             '<img src="streamdeck_icons/dashboard/ot_panel.png" width="48" alt="Überhol-Fenster">, '
             "Kamera-Fenster `http://localhost:5000/cameras/panel` "
             '<img src="streamdeck_icons/dashboard/cam_panel.png" width="48" alt="Kamera-Fenster">, '
             "Fahrer-Fenster `http://localhost:5000/drivers/panel` "
             '<img src="streamdeck_icons/dashboard/driver_panel.png" width="48" alt="Fahrer-Fenster">, '
             "Incident-Fenster `http://localhost:5000/incidents/panel` "
             '<img src="streamdeck_icons/dashboard/incident_panel.png" width="48" alt="Incident-Fenster">, '
             "Race Control `http://localhost:8080`. Kamera- und Replay-Tasten: "
             "[streamdeck_icons/README.md](streamdeck_icons/README.md).", ""]
    TABLE.write_text("\n".join(rows), encoding="utf-8")
    print("wrote", TABLE.name)
    write_overview(loaders)


OVERVIEW = HERE / "OVERLAYS.html"
SIZES_MD = HERE / "OBS_OVERLAYS.md"


def _md_inline(text):
    """`code`, **bold** and links of OBS_OVERLAYS.md cells -> HTML."""
    import re
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    return t


def _read_sizes():
    """OBS_OVERLAYS.md -> ({loader name: size/notes cell}, [operator page rows])."""
    import re
    sizes, tools = {}, []
    try:
        lines = SIZES_MD.read_text(encoding="utf-8").splitlines()
    except OSError:
        return sizes, tools
    section = ""
    for ln in lines:
        if ln.startswith("## "):
            section = ln[3:]
            continue
        if not ln.startswith("|") or ln.startswith("|---") or ln.startswith("| Overlay") \
                or ln.startswith("| Seite"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if section.startswith("Bedienseiten") and len(cells) >= 3:
            tools.append(cells[:3])
            for name in re.findall(r"([a-z0-9_]+)\.html", cells[2]):
                sizes.setdefault(name, cells[2])
            continue
        if len(cells) < 4:
            continue
        for name in re.findall(r"([a-z0-9_]+)\.html", cells[2]):
            sizes.setdefault(name, cells[3])
        # no loader file (YouTube): by "port/path"
        sizes.setdefault(cells[1].replace("`", "").replace(" ", ""), cells[3])
    return sizes, tools


def write_overview(loaders):
    """OVERLAYS.html - one page with every overlay: Stream Deck symbol, URL,
    OBS loader (full path to copy), size + options. Opened from the
    launcher's footer link; generated, never edited by hand (2026-10-27,
    Thomas: "ich suche und suche immer wieder")."""
    sizes, tools = _read_sizes()
    groups = {}
    for name, cat, _glyph, label in OVERLAY_ICONS:
        friendly, port, path, loader = loaders[name]
        groups.setdefault(cat, []).append((name, label, friendly, port, path, loader))
    order = ["live", "battle", "quali", "champ", "pre", "post", "tool"]
    cards = []
    for cat in order + [c for c in groups if c not in order]:
        if cat not in groups:
            continue
        strip, color = CAT[cat]
        cards.append('<h2 style="--c:%s">%s</h2><div class="grid">' % (color, html.escape(strip)))
        for name, label, friendly, port, path, loader in groups[cat]:
            url = "http://localhost:%d%s" % (port, path) if port else ""
            words = " ".join([name, label, friendly, url, loader or "", sizes.get(name, "")]).lower()
            ld = ""
            if loader:
                fn = loader.split("/")[-1]
                ld = ('<div class="k">OBS-Loader</div><div><code>%s</code> '
                      '<button data-copy-file="%s">Pfad kopieren</button></div>'
                      % (html.escape(fn), html.escape(loader)))
            else:
                ld = ('<div class="k">OBS-Loader</div><div>keiner - URL-Quelle '
                      '<code>%s</code></div>' % html.escape(url))
            u = ('<div class="k">Adresse</div><div><a href="%s" target="_blank">%s</a> '
                 '<button data-copy="%s">kopieren</button></div>'
                 % (html.escape(url), html.escape(url), html.escape(url))) if url else ""
            sz = sizes.get(name) or (sizes.get("%s%s" % (port, path)) if port else None)
            sz = '<div class="k">Größe / Optionen</div><div>%s</div>' % _md_inline(sz) if sz else \
                 '<div class="k">Größe / Optionen</div><div class="m">siehe Seite</div>'
            cards.append(
                '<div class="card" data-words="%s"><div class="ic">'
                '<img src="streamdeck_icons/overlays/%s.png" alt="">'
                '<img src="streamdeck_icons/overlays_off/%s.png" alt=""></div>'
                '<div class="tx"><div class="t">%s</div><div class="f">%s</div>'
                '<div class="kv">%s%s%s</div></div></div>'
                % (html.escape(words, quote=True), name, name, html.escape(label),
                   html.escape(friendly), u, ld, sz))
        cards.append("</div>")
    trows = "".join(
        '<tr data-words="%s"><td>%s</td><td>%s</td><td>%s</td></tr>'
        % (html.escape(" ".join(r).lower(), quote=True), _md_inline(r[0]),
           _md_inline(r[1]), _md_inline(r[2])) for r in tools)
    page = OVERVIEW_HTML.replace("@@CARDS@@", "\n".join(cards)).replace("@@TOOLS@@", trows)
    OVERVIEW.write_text(page, encoding="utf-8")
    print("wrote", OVERVIEW.name)


OVERVIEW_HTML = """<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Overlay-Übersicht</title>
<!-- generated by make_streamdeck_icons.py - do not edit by hand -->
<style>
:root { --bg: #121218; --panel: #1c1c26; --line: #2c2c3a; --text: #ececf2; --muted: #a2a2b4; --acc: #ff6b35; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.45 'Segoe UI', Arial, sans-serif; }
header { position: sticky; top: 0; z-index: 2; background: #0d0d12; border-bottom: 1px solid var(--line);
         padding: 12px 20px; display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
header h1 { font-size: 18px; margin: 0; } header h1 span { color: var(--acc); }
#q { flex: 1; min-width: 220px; max-width: 460px; padding: 8px 12px; border-radius: 6px; border: 1px solid var(--line);
     background: var(--panel); color: var(--text); font-size: 15px; }
main { padding: 8px 20px 40px; max-width: 1500px; margin: 0 auto; }
.tips { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; margin: 14px 0;
        color: var(--muted); }
.tips b { color: var(--text); }
h2 { font-size: 13px; letter-spacing: 1.5px; margin: 26px 0 10px; padding-left: 10px; border-left: 5px solid var(--c); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(440px, 1fr)); gap: 10px; }
.card { display: flex; gap: 12px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 10px; }
.ic { display: flex; flex-direction: column; gap: 6px; flex: none; }
.ic img { width: 64px; height: 64px; border-radius: 6px; display: block; }
.tx { min-width: 0; flex: 1; }
.t { font-weight: 700; font-size: 15px; } .f { color: var(--muted); font-size: 12px; margin-bottom: 6px; }
.kv { display: grid; grid-template-columns: 112px 1fr; gap: 3px 8px; font-size: 13px; }
.k { color: var(--muted); } .m { color: var(--muted); }
code { background: #0d0d12; border: 1px solid var(--line); border-radius: 4px; padding: 0 4px; font-size: 12px; word-break: break-all; }
a { color: #7cc4ff; }
button { background: #2a2a38; color: var(--text); border: 1px solid var(--line); border-radius: 4px; font-size: 11px;
         padding: 1px 7px; cursor: pointer; margin-left: 4px; }
button.ok { background: #1f6b3a; }
table { width: 100%; border-collapse: collapse; background: var(--panel); border-radius: 8px; overflow: hidden; }
td { border-top: 1px solid var(--line); padding: 7px 10px; vertical-align: top; font-size: 13px; }
td:first-child { font-weight: 600; width: 26%; } td:nth-child(2) { width: 26%; }
.hide { display: none !important; }
@media (max-width: 520px) { .grid { grid-template-columns: 1fr; } .kv { grid-template-columns: 1fr; } }
</style></head><body>
<header><h1>iRacing Overlays · <span>Übersicht</span></h1>
<input id="q" type="search" placeholder="Suchen: Tower, 5010, PRO, grid_cars, Reifen …" autofocus></header>
<main>
<div class="tips"><b>In OBS:</b> Browserquelle → „Lokale Datei“ → Loader-Datei (Knopf „Pfad kopieren“) → Breite / Höhe eintragen →
<b>Strg+R</b>. <b>Für alle Seiten:</b> <code>?zoom=1.5</code> größer (Breite / Höhe mitmultiplizieren, dann URL-Quelle statt Loader),
<code>?debug=1</code> oder Taste <b>H</b> = dunkler Hintergrund zum Einrichten, <code>?demo=1</code> = Beispieldaten ohne iRacing (wo vorhanden).
<b>Seiten blättern</b> nur per Stream Deck: <code>http://localhost:5001/nav/next</code> · <code>/prev</code> · <code>/first</code>.
Linkes Symbol = Taste an, rechtes = aus.</div>
@@CARDS@@
<h2 style="--c:#9aa3b5">BEDIENSEITEN (NICHT FÜR DEN STREAM)</h2>
<table>@@TOOLS@@</table>
</main>
<script>
const q = document.getElementById("q");
q.addEventListener("input", () => {
  const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
  document.querySelectorAll("[data-words]").forEach(el => {
    const w = el.dataset.words;
    el.classList.toggle("hide", !words.every(x => w.includes(x)));
  });
  document.querySelectorAll("h2").forEach(h => {
    const box = h.nextElementSibling;
    const any = box && [...box.querySelectorAll("[data-words]")].some(e => !e.classList.contains("hide"));
    h.classList.toggle("hide", !any); if (box) box.classList.toggle("hide", !any);
  });
});
function copy(text, btn) {
  const done = () => { const t = btn.textContent; btn.textContent = "kopiert"; btn.classList.add("ok");
                       setTimeout(() => { btn.textContent = t; btn.classList.remove("ok"); }, 1200); };
  if (navigator.clipboard && window.isSecureContext !== false) {
    navigator.clipboard.writeText(text).then(done, () => { prompt("Kopieren:", text); });
  } else { prompt("Kopieren:", text); }
}
document.addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.copy) copy(b.dataset.copy, b);
  if (b.dataset.copyFile) {
    // full path of the loader on THIS PC, from where this page lives
    let p = decodeURIComponent(new URL(b.dataset.copyFile, location.href).pathname);
    if (/^.[A-Za-z]:./.test(p) && p[0] === "/") p = p.slice(1).split("/").join(String.fromCharCode(92));
    copy(p, b);
  }
});
</script></body></html>
"""


def main():
    # optional args = only these folders, e.g. `overlays_off dashboard_off`;
    # `--table` = only rewrite OVERLAY_TABLE.md
    only = set(sys.argv[1:])
    write_table()
    if only == {"--table"}:
        return 0
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
