# iRacing Broadcast Overlays

A suite of eleven Python/Flask overlays that read live telemetry from iRacing via the iRacing SDK and serve web pages you can drop into OBS as Browser Sources. Built for race broadcasters and streamers who want a clean, iOverlay-style look without the subscription. Includes a full race logger with broadcast-friendly live charts plus a standalone tool to render any race as an MP4 replay video.

> Deutsche Anleitung: [INSTALLATION_DE.md](INSTALLATION_DE.md) · Live-Charts via Cloudflare teilen: [CLOUDFLARE_TUNNEL_DE.md](CLOUDFLARE_TUNNEL_DE.md)

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
| **Race logger**    | `iracing_race_logger.py`      | 5009 | JSONL race recorder + live race monitor + broadcast-friendly chart panel     |
| **Session info**   | `iracing_session_info.py`     | 5010 | Compact OBS card showing session name + total length + remaining time/laps   |

Plus a separate offline tool: **`render_race.py`** — turns any logged race into a 2D top-down MP4 replay video.

All overlays run simultaneously as separate Flask apps on different ports. OBS points at each one via its own Browser Source. Nothing else in iRacing or Windows is modified.

---

## Requirements

- **Windows 10 or 11** — iRacing is Windows-only and the SDK reads a Windows shared-memory file.
- **iRacing** — running and logged in.
- **Python 3.10 or newer** — get it from [python.org](https://www.python.org/downloads/). When installing, tick *Add Python to PATH*.
- **OBS Studio** (optional) — only needed if you want to use these on a stream. [obsproject.com](https://obsproject.com/).
- **ffmpeg** (optional) — only required if you want to render race-replay MP4s with `render_race.py`. Easiest install is `pip install imageio-ffmpeg`.

No iRacing API token or members-ng login is required. Everything talks to the local SDK only.

---

## Install

### 1. Get the code

```
git clone https://github.com/halvar20000/iracing-overlays.git
cd iracing-overlays
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
- **pillow** — used by the livery overlay (TGA→PNG paint conversion) and `render_race.py` (frame drawing).
- **pywin32** — used by the dashboard to send a spacebar keystroke to iRacing (re-hides the broadcast HUD after camera changes).
- **requests** — used by the livery overlay (iRacing local render server) and the race logger (incident feed from the dashboard).

If you also want to use the MP4 race renderer:

```
pip install imageio-ffmpeg
```

### 3. That's it

No config files. No API keys. No login. The overlays pick up iRacing via the SDK as soon as the sim is running and you're loaded into a session.

---

## Launching the overlays

You have three ways to start them. Pick whichever you prefer — they do the same thing.

### Option A — `launch_gui.bat` (desktop app, Recommended)

Double-click `launch_gui.bat`. A small Tkinter window opens with a Start / Stop / Open button per overlay, plus Start All / Stop All. Collapsible log pane for troubleshooting. This is the friendliest option — no console windows cluttering your desktop, and you can toggle individual overlays without touching the terminal.

`start_launcher.bat` is a thin wrapper that does the same thing — useful as a desktop shortcut.

### Option B — `launch_all.bat` (one console per overlay)

Double-click `launch_all.bat`. Ten console windows open, one per overlay. Close a window to stop that overlay. Useful if you want to see the live logs of each one at a glance.

### Option C — `launch_all.py` (single-terminal launcher)

Run `python launch_all.py`. All ten overlays' logs stream into one terminal window, colour-coded per overlay. Closest experience to a Linux-style process supervisor. Ctrl-C to stop all at once.

### Manual start (individual overlay)

You can also run any single overlay directly:

```
python iracing_dashboard.py
python iracing_standings.py
python iracing_race_logger.py
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

All overlays are built with transparent backgrounds by default — they composite cleanly over your iRacing capture. Press `H` on most overlays (in a regular browser tab) to toggle a debug background if you want to check layout.

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
- Click any incident to trigger a rewind replay of that incident — iRacing pauses, seeks backward to before the impact, plays through at 1x, then auto-returns to live
- **Auto-replay incidents** toggle — fires the replay automatically the moment an incident is detected
- **Clear all** — wipe the feed

### Top bar

- Race progress (laps remaining / time remaining)
- **GO LIVE** button — jumps replay back to the live playhead
- Live indicator pill

### Built-in safeguards

- **Camera disconnect watchdog** — if the followed driver disappears (network DC, garage retreat, gone-from-world), the camera automatically switches to a fallback driver after a 3-second grace, so iRacing's scenic camera doesn't kick in.
- **Spectator-mode incident detection** — when iRacing doesn't broadcast per-driver incident counts (spectator/broadcast role), spins and crashes are still detected via lap-position regression, on-track stoppage, and surface transitions.
- **Finish-line filter** — drivers who have already crossed the checker stop generating incident events, so cool-down laps don't produce false "stopped on track" alerts.

---

## Using the race logger (port 5009)

The race logger is both an in-browser race-monitor AND a JSONL recorder. While iRacing is running a race session, it writes one line per event to `logs/<timestamp>_<track>_race.jsonl`.

Open `http://localhost:5009` for the live race monitor:

- **Top bar**: track, session type, elapsed, weather, track temp
- **Counts row**: cars on track / in pits / out / laps logged / incidents logged
- **Drivers table** (left): position, #, driver, last lap, best lap, gap to leader, incidents, +/- overtakes, pit count, on-pit / DNF flags
- **Event timeline** (right): newest events first — lap completions, incidents, pit events, flag changes, penalties, slow-lap notifications
- **Live charts panel**: pin up to 5 drivers (click their row to pin / unpin), pick a chart type (Lap times / Position / Gap to leader). The chart at `http://localhost:5009/chart/render` is what you add as an OBS browser source — 600×360, transparent — while the operator pins drivers from the monitor view.
- **Past logs**: download links to every previous race's JSONL.

### Events captured per race

| Event type    | When it fires                                                              |
|---------------|----------------------------------------------------------------------------|
| `session_start` | At the green flag — track, session type, full driver list, weather    |
| `lap`           | Every lap completion by every driver — lap time, position, gap, pit  |
| `pit`           | Pit entry → exit — duration, lap, increments per-car pit count       |
| `flag`          | Session flag changes (Green / Yellow / Red / White / Checkered etc.) |
| `penalty`       | Black / Disqualify / Blue / Repair flags raised on a specific car    |
| `slow_lap`      | A driver's lap is >10 % slower than their 5-lap rolling average      |
| `incident`      | Spin / collision detected by the dashboard's incident feed           |
| `pos`           | Once per second: every car's `CarIdxLapDistPct` — used by the renderer |
| `session_end`   | Final classification when iRacing flips `ResultsOfficial == 1`       |

Practice / qualifying sessions are intentionally not logged.

### Sharing live charts with viewers

The logger ships public-share endpoints designed to go behind a Cloudflare Tunnel so remote viewers (Twitch chat, Discord) can pick their own driver chart and watch live without affecting the operator's OBS source. See [CLOUDFLARE_TUNNEL_DE.md](CLOUDFLARE_TUNNEL_DE.md) for the German setup guide. The tunnel is filtered server-side: even if `cloudflared` is misconfigured to forward everything, only `/share/*` paths are reachable from the public side.

---

## Rendering a race to MP4 (`render_race.py`)

Once a race is logged, you can turn the JSONL into a top-down 2D animated replay:

```
python render_race.py logs/20260426-193015_monza_full_race.jsonl
python render_race.py logs/20260426-193015_monza_full_race.jsonl --out my_race.mp4 --fps 30
```

Output is an MP4 next to the JSONL by default. Shows the track outline, numbered car dots interpolated between position ticks, a leaderboard panel on the right, lap counter, and incident flashes when an incident fires. Uses Pillow for frames and ffmpeg for video assembly.

Notes:
- The animation is fluid 30 fps but resolution-limited by the 1 Hz position ticks in the log. Linear interpolation gives smooth motion but can't show finer-than-second action.
- Only works for races recorded after the position-tick feature was added (every race logged with the current logger).
- Track outline must exist in `./tracks/<TrackName>.json` — see the trackmap section below for adding missing tracks.

---

## Using the standings overlay

Open `http://localhost:5005` directly in a browser to preview, or add it as a Browser Source in OBS.

**Info bar at top** shows: session type (Race / Qualifying / Practice), elapsed time, remaining time or laps, weather, track temperature.

**Row highlights**:
- Gold = P1 (LEADER)
- Orange = lapped (`+1 LAP`, `+2 LAPS`)
- **Amber = battle** (within 1.0 s of the car ahead, from lap 2 onwards)

In Qualifying / Practice, the right column shows each driver's best lap time instead of the race interval.

The overlay is transparent by default for OBS. Press `H` in a browser tab to toggle a dark debug background for layout work.

---

## Using the track map

Open `http://localhost:5007`. The track geometry is bundled offline for 205 of iRacing's ~400 tracks — no login needed. Car dots update live.

**Auto-recovery on track changes**: when iRacing switches sessions and the SDK serves stale YAML, the trackmap auto-reconnects to flush pyirsdk's cache. If for any reason it gets stuck, hit `http://localhost:5007/refresh` to force a manual SDK reconnect.

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
- **White** — final lap (timed and lap-based races; stays visible from when the leader starts the last lap until they cross the line)
- **Checkered** — finish (stays for 60 s)

Add it as a Browser Source set to your full canvas size. Transparent by default so only the flag graphic shows.

Late-join handling: if you connect to a session that's already in its final phase (`SessionState >= 5`), the white flag has already happened and we skip directly to "armed for checkered" so you still see the chequered flag when the leader finishes.

---

## Architecture

A common pattern keeps the codebase consistent:

- **`iracing_sdk_base.py`** provides `SDKPoller` — handles the SDK connection lifecycle, the polling loop, lock-protected snapshot storage, and graceful shutdown. Plus `setup_utf8_stdout()` to survive Windows cp1252 console encoding (a non-ASCII driver name in a `print()` inside an `except` block can otherwise silently kill the poller thread).
- Each overlay subclasses `SDKPoller` and only implements `_read_snapshot()` returning a dict. Two exceptions: the dashboard keeps its hand-rolled poller because of its size and the camera/replay/incident state living on the same class; the flag overlay is a state machine with a different public surface (`get_state()` instead of `get()`).
- Each overlay's Flask app stamps `Cache-Control: no-store` on its responses so OBS / browsers always pull the latest version when you reload — no Ctrl+F5 needed when iterating.
- All overlays bind to `0.0.0.0:<port>` so they're reachable from a second OBS PC on the LAN.

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

**`render_race.py` says "ffmpeg not found"**
Run `pip install imageio-ffmpeg` — that brings in a bundled ffmpeg binary the renderer can use without a system install. Alternatively install ffmpeg manually and put it on your PATH.

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
├── iracing_race_logger.py      # Port 5009 — race logger + live monitor
├── iracing_session_info.py     # Port 5010 — session name + total + remaining
│
├── render_race.py              # Offline tool — render a logged race to MP4
├── iracing_sdk_base.py         # Shared SDKPoller base class for overlays
│
├── launch_all.bat              # Windows: one console per overlay
├── launch_all.py               # Cross-platform: single terminal
├── launch_gui.py               # Tkinter desktop launcher
├── launch_gui.bat              # Double-click shortcut to launch_gui.py
├── start_launcher.bat          # Alternate desktop-shortcut wrapper
│
├── INSTALLATION_DE.md          # German installation guide
├── CLOUDFLARE_TUNNEL_DE.md     # German Cloudflare-tunnel sharing guide
│
├── car_brands.py               # Manufacturer logo resolution helper
├── brands/                     # Manufacturer SVG logos
├── tracks/                     # 205 bundled track JSONs (offline geometry)
│   └── NOTICE.txt              # Apache 2.0 attribution for SIMRacingApps
├── tools/
│   └── add_track.py            # Import user-supplied GPX files
└── logs/                       # Race logger output (gitignored)
```

---

## Extending / contributing

Each overlay is a standalone Flask app following the same pattern: subclass `SDKPoller`, implement `_read_snapshot()` returning a dict, register Flask routes, run. Adding a new overlay:

1. Copy one of the existing ones (the live-indicator is the smallest).
2. Pick a free port (next free is 5010).
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
