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
    ("results",       "Results",          5002),
    ("results_lite",  "Results Lite",     5003),
    ("live",          "Live Indicator",   5004),
    ("standings",     "Standings",        5005),
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
    # Note: racecontrol (iCASControl, port 8080) is an interactive steward
    # web app, opened directly in a browser — NOT an OBS browser source — so
    # it intentionally has no loader page here.
]

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
  iframe {{
    border: 0; width: 100%; height: 100%;
    background: transparent; display: block;
  }}
</style>
</head>
<body>
<iframe id="ov" allowtransparency="true"></iframe>
<script>
  var URL_ = "http://localhost:{port}{path}";
  var frame = document.getElementById("ov");
  var loaded = false;
  var fails = 0;
  var FAIL_LIMIT = 3;   // consecutive failed pings before we treat the server as down

  function ping() {{
    // no-cors: we only care whether the server answers at all.
    return fetch(URL_, {{ mode: "no-cors", cache: "no-store" }})
      .then(function () {{ return true; }})
      .catch(function () {{ return false; }});
  }}

  function tick() {{
    ping().then(function (up) {{
      if (up) {{
        fails = 0;
        if (!loaded) {{
          // cache-buster forces a real reload after a server restart
          frame.src = URL_ + (URL_.indexOf("?") < 0 ? "?" : "&") + "r=" + Date.now();
          loaded = true;
        }}
      }} else {{
        // Ignore the occasional dropped request — only reload once the server
        // has really been unreachable for several pings. Reloading on a single
        // blip is what made overlays flicker.
        fails++;
        if (loaded && fails >= FAIL_LIMIT) {{
          loaded = false;
        }}
      }}
      // Poll fast while there's trouble, slow once stable.
      setTimeout(tick, (loaded && fails === 0) ? 5000 : 1500);
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
        out = os.path.join(outdir, stem + ".html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(TEMPLATE.format(name=name, port=port, path=path))
        print(f"  written  {out}  ->  http://localhost:{port}{path}")
    print(f"\n{len(OVERLAYS)} loader pages in {outdir}")
    print("In OBS: Browser source -> check 'Local file' -> pick the .html")

if __name__ == "__main__":
    main()
