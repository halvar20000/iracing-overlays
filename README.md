# iRacing Overlays

🇩🇪 [Deutsche Version](README.de.md)

A collection of free, self-hosted broadcast overlays for [iRacing](https://www.iracing.com/), built with Python + Flask. Each overlay runs as a small local web server and is added to [OBS Studio](https://obsproject.com/) as a browser source — no subscriptions, no accounts, no cloud services.

All telemetry is read locally from the iRacing SDK via `pyirsdk`. Nothing leaves your machine unless you explicitly enable the optional public sharing feature.

## Overlays

| Overlay | Script | Port | What it shows |
|---|---|---|---|
| Dashboard | `iracing_dashboard.py` | 5000 | Operator dashboard: live telemetry, camera control, incident & overtake detection with auto-replay |
| Grid | `iracing_grid.py` | 5001 | Qualifying grid with colored car silhouettes |
| Results | `iracing_results.py` | 5002 | Full race results (gaps, incidents, fastest lap) |
| Results Lite | `iracing_results_lite.py` | 5003 | Minimal results overlay |
| Live Indicator | `iracing_live_indicator.py` | 5004 | LIVE / REPLAY badge |
| Standings | `iracing_standings.py` | 5005 | Live standings with session info bar, brand logos, pit info |
| Livery | `iracing_livery.py` | 5006 | 3D-rendered car + driver name of whoever is on camera |
| Track Map | `iracing_trackmap.py` | 5007 | SVG track map with live car dots — fully offline, ~300 tracks bundled |
| Flag | `flag_overlay.py` | 5008 | Session flag status (green, yellow, white, checkered, …) |
| Race Logger | `iracing_race_logger.py` | 5009 | JSONL race log + live race monitor with charts |
| Championship | `iracing_championship.py` | 5010 | Live championship projection (requires an external league-manager backend) |
| Session Info | `iracing_session_info.py` | 5011 | Session name + total / remaining time or laps |
| Corner Cues | `iracing_drivingline.py` | 5012 | Corner cues (direction, severity, distance) for tracks where the racing-line aid is disabled |
| Driver of the Day | `iracing_dotd_overlay.py` | 5013 | Driver-of-the-Day nomination from race logs (no SDK needed) |
| Quali Delta | `iracing_qualidelta.py` | 5014 | Live qualifying delta to the session best + per-sector splits |
| Catch-Up | `iracing_catchup.py` | 5015 | F1-style catch-up battle: live gap, pace delta and a "catch in N laps" prediction |
| Weather | `iracing_weather.py` | 5016 | Weather strip: track/air temp, humidity, rain, wind, sky + live trends |
| Driver Card | `iracing_drivercard.py` | 5017 | Lower-third card for the on-camera driver: name, team, iRating, license, laps, incidents |
| New Race Leader | `iracing_race_leader.py` | 5018 | Flashes "NEW RACE LEADER" + driver name for 10 s whenever a new car takes the overall lead — Race sessions only |

All overlays run in parallel — each has its own port.

## Requirements

- **Windows** (iRacing only runs on Windows)
- **Python 3.10+** — [python.org/downloads](https://www.python.org/downloads/) (check "Add Python to PATH" during install)
- **iRacing** running on the same machine
- **OBS Studio** (or any tool that supports browser sources) for streaming

## Installation

```bash
git clone https://github.com/halvar20000/iracing-overlays.git
cd iracing-overlays
pip install -r requirements.txt
```

That's it. The dependencies are Flask, pyirsdk, Pillow, requests and (Windows only) pywin32.

## Usage

### Option 1 — GUI launcher (recommended)

Double-click **`launch_gui.bat`**. A small desktop app opens with a Start/Stop button and status dot per overlay, plus Start All / Stop All and a log pane.

### Option 2 — Batch launcher

Run **`launch_all.bat`** — starts every overlay in its own console window.

### Option 3 — Single terminal

```bash
python launch_all.py
```

Starts all overlays in one terminal with color-coded log prefixes. Cross-platform.

You can also run any single overlay directly: `python iracing_standings.py`

### Adding overlays to OBS

1. Start the overlay(s) you want.
2. In OBS: **Sources → + → Browser**, URL `http://localhost:<port>` (e.g. `http://localhost:5005` for standings).
3. Most overlays start with a dark debug background. Click into the browser source (Interact) and press **`H`** to toggle the transparent stream mode. The Live Indicator and Flag overlays are always transparent.

The overlays bind to `0.0.0.0`, so a second PC on your LAN can reach them via `http://<your-ip>:<port>` — useful for a dedicated streaming PC.

### Tip: self-healing browser sources (no manual refresh)

OBS loads each browser source only once at startup — if the overlay server isn't running yet at that moment, the source stays blank until you click **Refresh**. To avoid this, use the loader pages in `obs_loaders/`: in the browser source properties, check **Local file** and select e.g. `obs_loaders/standings.html` instead of entering a URL. The loader embeds the overlay and retries automatically until the server responds — start order no longer matters, and overlays also come back on their own after a server restart. Regenerate the loader pages with `python make_obs_loaders.py` after adding a new overlay.

## Track Map — offline track library

The track map needs **no iRacing login and no internet**. Track geometry for ~300 track configurations is bundled in `tracks/` as JSON. The overlay reads the track name from the SDK and loads the matching file; if a track isn't bundled it shows a friendly "TRACK MAP NOT BUNDLED" message.

Geometry sources: the open-source [SIMRacingApps](https://github.com/SIMRacingApps/SIMRacingApps) track library (Apache 2.0) and [OpenStreetMap](https://www.openstreetmap.org/) (ODbL). See `tracks/NOTICE.txt` for full attribution and `tracks/MISSING_TRACKS.md` for coverage status.

## Corner Cues — on-screen driving aid

For sessions where iRacing disables the racing-line aid, `iracing_drivingline.py` (port 5012) analyzes the bundled track geometry and serves upcoming-corner cues: direction arrow, turn number, severity (HAIRPIN → FAST), estimated apex speed and a distance countdown.

Two ways to display it:

- **OBS browser source** at `http://localhost:5012` (for the stream), or
- **`driving_line_window.py`** — a transparent, click-through, always-on-top desktop window for the driver. Run it manually while driving (iRacing must be in **borderless windowed** mode). Use `--debug` to position the window; it prints the coordinates to lock in.

Speeds are car-agnostic estimates. For league racing, check with your stewards whether external driving aids are allowed.

## Race Logger

`iracing_race_logger.py` (port 5009) writes one JSONL file per race into `logs/` — laps, pit stops, flags, penalties, incidents, positions and the final classification. The page on port 5009 is a full live race monitor: drivers table, event timeline, lap-time / position / gap charts that can be added to OBS as a separate browser source (`/chart/render`).

### Optional: public sharing for viewers

The logger has read-only public endpoints (`/share/chart`, `/share/standings`) so Twitch/Discord viewers can open a self-service chart and pick their own drivers — without touching your operator view. Expose them with a free [Cloudflare Tunnel](https://www.cloudflare.com/products/tunnel/); only `/share/*` paths are served to remote viewers, all admin endpoints stay local. Setup guide (German): [`CLOUDFLARE_TUNNEL_DE.md`](CLOUDFLARE_TUNNEL_DE.md).

## Car brand logos

The standings overlay shows manufacturer logos from `brands/*.svg`. To add a brand: drop `brands/<slug>.svg` into the folder and, if needed, add a prefix mapping in `car_brands.py`.

## Race Control / Stewarding (iCASControl)

In addition to the broadcast overlays, this project bundles **iCASControl**, a
race-control / stewarding dashboard modelled on
[iRaceControl](https://iracecontrol.com/). It lives in the `racecontrol/`
subfolder and gives a race director live timing, an animated track map and a
full incident log with a steward decision workflow (Noted, Investigating, No
Action, Race Incident, Drive Through, Stop/Go, Time Penalty, DSQ) plus
race-control commands (pace car, pit open/close, flags).

Unlike the overlays, it is **not** an OBS browser source — it is a full-screen
control panel you open in a browser at **http://localhost:8080**. It runs as a
FastAPI server with a swappable data source: it tries live iRacing first and
falls back to a built-in **simulator** (so it works on any OS for learning or
demos), and can also **replay** a recorded `.jsonl` race log.

Start it like any other component from the launchers (row **"Race Control /
Steward"**), or directly:

```bash
python iracing_racecontrol.py
```

It shares the bundled `tracks/` folder with the overlays automatically (same
geometry format), so any circuit you have for the track-map overlay is also
available to the steward. See `racecontrol/README.md` and
`racecontrol/ROADMAP.md` for the full feature list and planned work (Sequencer,
Auto Steward, PDF/CSV exports, multi-operator networking, StreamDeck).

## Troubleshooting

- **Overlay shows "waiting for iRacing"** — iRacing must be running (a session loaded, not just the UI).
- **Changed a script but see old behavior** — overlays keep the old code in memory; restart the overlay.
- **Black box instead of transparency in OBS** — press `H` in the browser source (Interact) to toggle stream mode.
- **Port already in use** — another overlay or app owns that port; each script's port is set at the bottom in `app.run(...)`.
- **An overlay stutters or briefly disappears mid-stream (especially Live Indicator / Session Info)** — this is OBS, not the overlay. OBS uses an embedded Chromium that *throttles* a browser source's timers when the OBS window is in the background (e.g. while you race with iRacing in the foreground), so the overlay's poll loop is slowed and can be paused for a few seconds. The overlays already tolerate this — they hold the last frame for 30 s before showing an offline state, so they shouldn't go invisible. If you still see stutter you don't like, two things help: (1) keep the OBS window **not minimized** while racing (minimized = hardest throttling); (2) point the OBS source at the overlay **directly** (`http://localhost:<port>`) instead of the `obs_loaders/` file — the loader wraps the page in an iframe, which OBS throttles a little harder. The trade-off of the direct URL is that you lose the auto-start-order safety net, so **reload that browser source once before the race starts** if it came up blank.

## License & attribution

Track geometry: SIMRacingApps project by Jeffrey Gilliam (Apache 2.0) and OpenStreetMap contributors (ODbL) — see `tracks/NOTICE.txt`.
