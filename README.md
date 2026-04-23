# iRacing Broadcast Overlays

A suite of nine Python/Flask overlays that read live telemetry from iRacing via the iRacing SDK and serve web pages you can drop into OBS as Browser Sources. Built for race broadcasters and streamers who want a clean, iOverlay-style look without the subscription.

![Overlay screenshot placeholder — add one from your stream]()

## What's in the box

| Overlay            | File                          | Port | What it shows                                                                 |
|--------------------|-------------------------------|------|-------------------------------------------------------------------------------|
| **Dashboard**      | `iracing_dashboard.py`        | 5000 | Master control: driver list, camera buttons, incident feed, replay triggers  |
| **Grid**           | `iracing_grid.py`             | 5001 | Qualifying grid with coloured car silhouettes                                |
| **Results (full)** | `iracing_results.py`          | 5002 | Race results — position, gaps, incident counts, fastest lap                  |
| **Results (lite)** | `iracing_results_lite.py`     | 5003 | Minimal end-of-race results overlay                                          |
| **Live indicator** | `iracing_live_indicator.py`   | 5004 | `LIVE` / `REPLAY` / `PAUSED` badge — drives OBS scene indicators             |
| **Standings**      | `iracing_standings.py`        | 5005 | Live race standings, session info bar, within-1s "battle" highlight          |
| **Livery**         | `iracing_livery.py`           | 5006 | Rendered 3D car + driver name for the driver on camera                       |
| **Trackmap**       | `iracing_trackmap.py`         | 5007 | SVG track outline with live car dots (205 tracks bundled, offline)           |
| **Flag overlay**   | `flag_overlay.py`             | 5008 | Full-screen flag graphics — green, yellow, white, checkered                  |

All overlays run simultaneously as separate Flask apps on different ports. OBS points at each one via its own Browser Source. Nothing else in iRacing or Windows is modified.

---

## Requirements

- **Windows 10 or 11** — iRacing is Windows-only and the SDK reads a Windows shared-memory file.
- **iRacing** — running and logged in.
- **Python 3.10 or newer** — get it from [python.org](https://www.python.org/downloads/). When installing, tick *Add Python to PATH*.
- **OBS Studio** (optional) — only needed if you want to use these on a stream. [obsproject.com](https://obsproject.com/).

No iRacing API token or members-ng login is required. Everything talks to the local SDK only.

---

## Install

### 1. Get the code

```
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
```

Or download the ZIP from the green **Code** button on GitHub and extract it somewhere sensible (e.g. `C:\iRacing\overlays\`).

### 2. Install Python dependencies

Open a terminal (PowerShell or cmd) inside the project folder and run:

```
pip install pyirsdk flask pillow pywin32 requests
```

What each one is for:

- **pyirsdk** — reads iRacing's live telemetry via the SDK shared-memory file. The package name is `pyirsdk`, the Python import is `irsdk`.
- **flask** — the web server each overlay runs on.
- **pillow** — used by the livery overlay to convert iRacing's `.tga` paint files to `.png`.
- **pywin32** — used by the dashboard to send a spacebar keystroke to iRacing (re-hides the broadcast HUD after camera changes).
- **requests** — used by the livery overlay to fetch rendered car images from iRacing's local render server.

If you don't plan to use the livery or dashboard, you can skip `pillow`, `pywin32`, and `requests` — the overlays import them softly.

### 3. That's it

No config files. No API keys. No login. The overlays pick up iRacing via the SDK as soon as the sim is running and you're loaded into a session.

---

## Launching the overlays

You have three ways to start them. Pick whichever you prefer — they do the same thing.

### Option A — `launch_gui.bat` (desktop app, Recommended)

Double-click `launch_gui.bat`. A small Tkinter window opens with a Start / Stop / Open button per overlay, plus Start All / Stop All. Collapsible log pane for troubleshooting. This is the friendliest option — no console windows cluttering your desktop, and you can toggle individual overlays without touching the terminal.

### Option B — `launch_all.bat` (one console per overlay)

Double-click `launch_all.bat`. Nine console windows open, one per overlay. Close a window to stop that overlay. Useful if you want to see the live logs of each one at a glance.

### Option C — `launch_all.py` (single-terminal launcher)

Run `python launch_all.py`. All nine overlays' logs stream into one terminal window, colour-coded per overlay. Closest experience to a Linux-style process supervisor. Ctrl-C to stop all at once.

### Manual start (individual overlay)

You can also run any single overlay directly:

```
python iracing_dashboard.py
python iracing_standings.py
# ...etc
```

---

## Adding overlays to OBS

For each overlay you want on stream:

1. In OBS, **Sources** → **+** → **Browser**
2. Name it (e.g. "iRacing Standings")
3. **URL**: `http://localhost:5005` (use the port from the table above)
4. **Width / Height**: match your scene — 1920×1080 for a full-screen overlay, smaller for a corner widget
5. Tick **Shutdown source when not visible** (saves CPU when the overlay isn't active)
6. OK

All overlays are built with transparent backgrounds by default — they composite cleanly over your iRacing capture. Press `H` on any overlay window to toggle a dark debug background if you want to check layout.

**Remote PC setup**: the overlays bind to `0.0.0.0`, so you can run them on your gaming PC and pull them into OBS on a second streaming PC over your LAN. Just replace `localhost` with the gaming PC's IP address (e.g. `http://192.168.1.50:5005`).

---

## Using the dashboard (the control hub)

Open `http://localhost:5000` in any browser while an overlay session is running. This is your master control panel — it's not usually on stream, it's a second-screen tool for the broadcaster.

### Left column — Drivers

- Full driver list with positions, car numbers, best-lap times, gaps
- Click any row to switch the iRacing camera to that driver
- **⭐ Starred**: click the star to flag a driver of interest
- **Auto-follow battles**: camera auto-tracks the closest battle (starred drivers first)

### Middle column — Camera

- Camera angle groups (TV1, TV2, Chase, Cockpit, etc.) — click to switch
- **MOST EXCITING** — enables iRacing's built-in auto-shot selection
- **FOCUS LEADER** — camera locks to overall P1
- **FOCUS CRASHES** — on a spin or collision, camera snaps to the crashed car for ~12 s

### Right column — Incidents

- Live feed of detected spins / collisions
- Click any incident to trigger a **5-second rewind replay** of that incident — iRacing pauses, seeks backward, plays through the incident at 1x, then auto-returns to live
- **Auto-replay incidents** toggle — fires the replay automatically the moment an incident is detected
- **Clear all** — wipe the feed

### Top bar

- Race progress (laps remaining / time remaining)
- **GO LIVE** button — jumps replay back to the live playhead
- Live indicator pill

---

## Using the standings overlay

Open `http://localhost:5005` directly in a browser to preview, or add it as a Browser Source in OBS.

**Info bar at top** shows: session type (Race / Qualifying / Practice), elapsed time, remaining time or laps, weather, track temperature.

**Row highlights**:
- Gold = P1 (LEADER)
- Orange = lapped (`+1 LAP`, `+2 LAPS`)
- **Amber = battle** (within 1.0 s of the car ahead, from lap 2 onwards)

In Qualifying / Practice, the right column shows each driver's best lap time instead of the race interval.

---

## Using the track map

Open `http://localhost:5007`. The track geometry is bundled offline for 205 of iRacing's ~400 tracks — no login needed. Car dots update live.

**Missing a track?** If you see "TRACK MAP NOT BUNDLED", it means SIMRacingApps (the upstream data source) doesn't have geometry for that circuit yet. You can import your own GPX file:

```
python tools/add_track.py <trackname> <track_outline.gpx> [pit_lane.gpx]
```

Where `<trackname>` matches the lowercased `WeekendInfo.TrackName` from iRacing (check `http://localhost:5007/debug`). Restart the trackmap after adding.

---

## Live indicator

The live indicator badge (`http://localhost:5004`) shows `LIVE`, `REPLAY`, `PAUSED`, `REWIND`, `FAST FORWARD`, or `SLOW MOTION` depending on iRacing's replay state. Drop it somewhere on your stream scene so viewers can tell at a glance whether they're watching the real race or a replay.

---

## Livery overlay

The livery overlay (`http://localhost:5006`) shows the rendered 3D car with the driver's custom paint (if they have one) plus their name. It uses iRacing's local render server at `http://127.0.0.1:32034/pk_car.png` — no Trading Paints subscription or account needed. Falls back to a design card if no render is available.

---

## Flag overlay

The flag overlay (`http://localhost:5008`) shows a full-screen flag graphic when the corresponding flag is out:

- **Green** — session start / restart
- **Yellow** — caution
- **White** — last lap (timed races use a different heuristic)
- **Checkered** — finish

Add it as a Browser Source set to your full canvas size. Transparent by default so only the flag graphic shows.

---

## Troubleshooting

**"WAITING FOR IRACING…" never goes away**
Make sure iRacing is running AND you're loaded into a session (in a car, not just at the main menu). Also make sure the overlay is running on the same PC as iRacing.

**`ModuleNotFoundError: No module named 'irsdk'`**
Run `pip install pyirsdk`. The package name is `pyirsdk` but the Python import is `irsdk` — confusing but correct.

**Port already in use**
Edit the `port=5000` argument at the bottom of the affected script. Or close whatever else is using that port.

**Track map shows the wrong track**
We auto-detect track changes now, but if iRacing's SDK gets stuck with stale session data, open `http://localhost:5007/refresh` in a browser to force a full SDK reconnect.

**Replay jumps to the car but doesn't rewind**
Check the dashboard's console output for `[replay]` lines. The `diag before/after_seek` lines tell you whether iRacing accepted the seek. A `WARNING: seek did not appear to move the playhead` message means iRacing rejected it — usually because you were already paused or at the start of the buffer.

**Too many false-positive incidents**
Spectator-mode detection relies on lap-pct regression and surface transitions since per-driver incident counts aren't broadcast to spectators. If it's too noisy, you can raise the stopped-on-track threshold in `iracing_dashboard.py` (look for `self._stopped_ticks.get(idx, 0) == 12`).

**OBS shows a background instead of transparent**
The overlays are transparent by default for OBS Browser Sources. If you're seeing a background, you're probably loading the overlay directly in a browser tab — press `H` to toggle the debug background, or just trust that OBS will composite it correctly.

---

## Project structure

```
.
├── iracing_dashboard.py        # Port 5000 — master control hub
├── iracing_grid.py             # Port 5001 — qualifying grid
├── iracing_results.py          # Port 5002 — full results
├── iracing_results_lite.py     # Port 5003 — minimal results
├── iracing_live_indicator.py   # Port 5004 — LIVE/REPLAY badge
├── iracing_standings.py        # Port 5005 — live standings
├── iracing_livery.py           # Port 5006 — on-camera livery
├── iracing_trackmap.py         # Port 5007 — SVG track map
├── flag_overlay.py             # Port 5008 — flag graphics
│
├── launch_all.bat              # Windows: one console per overlay
├── launch_all.py               # Cross-platform: single terminal
├── launch_gui.py               # Tkinter desktop launcher
├── launch_gui.bat              # Double-click shortcut to launch_gui.py
│
├── car_brands.py               # Manufacturer logo resolution helper
├── brands/                     # Manufacturer SVG logos
├── tracks/                     # 205 bundled track JSONs (offline geometry)
│   └── NOTICE.txt              # Apache 2.0 attribution for SIMRacingApps
└── tools/
    └── add_track.py            # Import user-supplied GPX files
```

---

## Extending / contributing

Each overlay is a standalone Flask app following the same pattern: a `Poller` class reads iRacing telemetry on a background thread, Flask serves HTML and JSON endpoints on a port. Adding a new overlay:

1. Copy one of the existing ones (the live-indicator is the smallest).
2. Pick a free port (next free is 5009).
3. Add an entry to all three launchers: `launch_all.bat`, `launch_all.py` (`SCRIPTS` list), `launch_gui.py` (`OVERLAYS` list). See `CLAUDE.md` in the project root for the maintenance-rule details.

Pull requests welcome — open an issue first if you're planning something big.

---

## Credits

- **Track geometry** — 205 per-track GPX routes converted from the [SIMRacingApps](https://github.com/SIMRacingApps/SIMRacingAppsServer) project by Jeffrey Gilliam (Apache 2.0).
- **iRacing SDK access** — via [pyirsdk](https://github.com/kutu/pyirsdk) by kutu.
- **Built by** Thomas Herbrig — [YouTube channel](#) (add link)

---

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, break it, share it. Attribution is appreciated but not required.
