#!/usr/bin/env python3
"""
make_obs_loaders.py — generate self-healing OBS loader pages.

Problem: OBS's Chromium loads a browser source exactly ONCE at startup.
If the overlay server isn't running yet, the load fails and OBS never
retries — you have to click "Refresh" manually.

Fix: point each OBS browser source at a LOCAL html file (checkbox
"Local file" / "Lokale Datei") from the obs_loaders/ folder instead of
http://localhost:<port>. The local file always loads (it's on disk),
embeds the overlay in a transparent iframe, and keeps pinging the
server: it loads the overlay as soon as the server is up and reloads
it automatically if the server is restarted. Start order of OBS vs.
overlays no longer matters.

Usage:  python make_obs_loaders.py
Output: obs_loaders/<name>.html  (one per overlay below)

Maintenance: when a new overlay is added, append it to OVERLAYS and
re-run this script.
"""
import os

OVERLAYS = [
    # (filename stem, friendly name, port)
    ("dashboard",     "Dashboard",        5000),
    ("grid",          "Grid",             5001),
    ("grid_cars",     "Grid (Cars)",      5001, "/cars"),
    ("grid_row",      "Grid (Row)",       5001, "/row"),
    ("grid_row_top",  "Grid (Row, top)",  5001, "/row/top"),
    ("results",       "Results",          5002),
    ("results_lite",  "Results Lite",     5003),
    ("live",          "Live Indicator",   5004),
    ("standings",     "Standings",        5005),
    ("standings_nodelta", "Standings (no +/-)", 5005, "/nodelta"),
    ("standings_cars", "Standings (Cars)", 5005, "/cars"),
    ("standings_cars_nodelta", "Standings (Cars, no +/-)", 5005, "/cars/nodelta"),
    ("livery",        "Livery",           5006),
    ("trackmap",      "Track Map",        5007),
    ("flag",          "Flag",             5008),
    ("logger",        "Race Logger",      5009),
    ("logger_chart",  "Race Logger Chart", 5009, "/chart/render"),
    ("champ",         "Championship",     5010, "/overlay"),
    ("sess",          "Session Info",     5011),
    ("line",          "Corner Cues",      5012),
    ("dotd",          "Driver of the Day", 5013),
    ("delta",         "Quali Delta",      5014),
    ("delta_own",     "Quali Delta (Own Best)", 5014, "/own", "redirect"),
    ("catch",         "Catch-Up Battle",  5015),
    ("weather",       "Weather",          5016),
    ("driver",        "Driver Card",      5017),
    ("driver_simple", "Driver Card (no iRating / SR)", 5017, "/simple"),
    ("driver_combo",  "Driver + Car (team line-up)", 5017, "/combo"),
    ("leader",        "New Race Leader",  5018),
    ("rc_banner",     "Race Control",     5019),
    # Extra pages on existing servers (2026-10-08) — no new ports.
    ("duel",          "Title Fight",      5010, "/duel"),
    ("fastest",       "Fastest Lap",      5005, "/fastest"),
    ("movers",        "Biggest Movers",   5005, "/movers"),
    ("gapbar",        "Gap Bar",          5005, "/gapbar"),
    ("pittires",      "Pit Stop Tires",   5005, "/pittires"),
    ("pittires_list", "Pit Stops Tire List", 5005, "/pittires/list"),
    ("ticker",        "Overtake Ticker",  5000, "/ticker"),
    ("lastrace",      "Last Round Results", 5010, "/lastrace"),
    ("table",         "Standings Before Round", 5010, "/table"),
    # Pro/Am seasons: one class per source (AI moderation shows them in turn)
    ("lastrace_pro",  "Last Round Results PRO", 5010, "/lastrace/pro"),
    ("lastrace_am",   "Last Round Results AM", 5010, "/lastrace/am"),
    ("table_pro",     "Standings Before Round PRO", 5010, "/table/pro"),
    ("table_am",      "Standings Before Round AM", 5010, "/table/am"),
    # ABSPANN: opaque full-page versions, shown over Gabi on the Moderator scene
    ("results_solid", "Results (Abspann, opaque)", 5002, "/solid"),
    # WCT broadcast look (2026-10-25): paged, Stream Deck 5001/nav/...
    ("results_wct",   "Results (WCT look)", 5002, "/wct"),
    ("lastrace_wct",  "Last Round (WCT look)", 5010, "/lastrace/wct"),
    ("table_wct",     "Championship (WCT look)", 5010, "/table/wct"),
    ("teams_wct",     "Team Standings (WCT look)", 5010, "/teams/wct"),
    ("live_wct",      "Championship LIVE / provisional (WCT look)", 5010, "/live/wct"),
    ("champ_solid",   "Championship (Abspann, opaque)", 5010, "/overlay/solid"),
    ("rsvp",          "RSVP Overview",    5010, "/rsvp"),
    ("stats",         "Season Statistics", 5010, "/stats"),
    ("lastdotd",      "Last Round Driver of the Day", 5010, "/lastdotd"),
    # F1-style battle graphics + corner label (2026-10-10).
    ("battle_gap",    "Battle: Current Gap", 5015, "/gap"),
    ("chasing",       "Battle: Chasing",  5015, "/chasing"),
    ("last_laps",     "Battle: Last 4 Laps", 5015, "/laps"),
    ("turn",          "Turn Label",       5012, "/turn"),
    # Whole-field sector graphics on the quali-delta server (2026-10-12).
    ("sectors",       "Best Sectors",     5014, "/sectors"),
    ("topspeed",      "Sector Top Speed", 5014, "/speed"),
    # REGIE commentary lower third on the dashboard (2026-10-14).
    ("commentary",    "Commentary (REGIE)", 5000, "/commentary/overlay"),
    # ...and its spoken version (audio only; top-level page so OBS gets the sound).
    ("commentary_voice", "Commentary Voice (REGIE)", 5000, "/commentary/voice", "redirect"),
    # where the track is: satellite map, flies onto the circuit (AI moderation)
    ("location",      "Track Location Map", 5007, "/location", "redirect"),
    # highlight video (2026-10-21): scene title card during the recording
    ("highlight_card", "Highlight Title Card", 5000, "/highlights/card"),
    # Frag Claude (2026-10-22): listens / thinks / answer READY / speaks
    ("ask_status",    "Frag Claude Status", 5000, "/ask/overlay"),
    # Note: racecontrol (iCASControl, port 8080) is an interactive steward
    # web app, opened directly in a browser — NOT an OBS browser source — so
    # it intentionally has no loader page here.
]

# ONE loader template since 2026-10-31. Every loader navigates THIS page
# straight to the overlay (top-level page, exactly like a URL browser source).
# Before that, most loaders put the overlay into an IFRAME after a fetch()
# "is the server up?" ping. After Thomas's OBS update (Oct 31) none of them
# showed anything while the plain URL http://localhost:5005 worked: the newer
# browser inside OBS blocks a LOCAL FILE page from reaching localhost in the
# background (fetch / iframe), a top-level navigation is still allowed.
# So: ping as before; if the ping keeps failing (server not up yet - or the
# ping itself is blocked) go to the overlay anyway after GIVE_UP_MS. The
# overlay page recovers from later server hiccups on its own (it keeps
# polling), so it is never reloaded. A page opened while its server is DOWN
# shows the browser's error page - refresh the source once the overlay runs.
# The 5th field of an OVERLAYS entry ("redirect") is no longer needed.
TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{name} — loader</title>
<style>
  html, body {{
    margin: 0; padding: 0; width: 100%; height: 100%;
    background: rgba(0,0,0,0); overflow: hidden;
  }}
</style>
</head>
<body>
<script>
  // Loader: waits (up to GIVE_UP_MS) until the overlay server answers, then
  // navigates THIS page straight to the overlay - a top-level page, exactly
  // like a URL browser source. Newer OBS versions block the background ping
  // from a local file, so after GIVE_UP_MS we go anyway.
  var URL_ = "http://localhost:{port}{path}";
  var GIVE_UP_MS = 4000;
  var t0 = Date.now();

  function go() {{ window.location.replace(URL_); }}

  function tick() {{
    fetch(URL_, {{ mode: "no-cors", cache: "no-store" }})
      .then(go)
      .catch(function () {{
        if (Date.now() - t0 >= GIVE_UP_MS) go();
        else setTimeout(tick, 1000);
      }});
  }}
  tick();
</script>
</body>
</html>
"""

def main():
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "obs_loaders")
    os.makedirs(outdir, exist_ok=True)
    for entry in OVERLAYS:
        stem, name, port = entry[0], entry[1], entry[2]
        path = entry[3] if len(entry) > 3 else "/"
        tmpl = TEMPLATE
        out = os.path.join(outdir, stem + ".html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(tmpl.format(name=name, port=port, path=path))
        print(f"  written  {out}  ->  http://localhost:{port}{path}")
    print(f"\n{len(OVERLAYS)} loader pages in {outdir}")
    print("In OBS: Browser source -> check 'Local file' -> pick the .html")
    # keep OVERLAY_TABLE.md (ports / loaders / Stream Deck icons) in step
    try:
        import make_streamdeck_icons
        make_streamdeck_icons.write_table()
    except Exception as e:
        print("OVERLAY_TABLE.md NOT updated: %s" % e)

if __name__ == "__main__":
    main()
