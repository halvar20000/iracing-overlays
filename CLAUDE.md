# iRacing Overlays — Project Notes for Claude

Location: `/Users/thomasherbrig/Nextcloud/iRacing/python/files/`
GitHub:   https://github.com/halvar20000/iracing-overlays (primary repo,
          source of truth; local folder is where I edit, user pushes via
          git from Terminal).

## Scripts

| Tag         | File                          | Port | Purpose                                    |
|-------------|-------------------------------|------|--------------------------------------------|
| dashboard   | `iracing_dashboard.py`        | 5000 | Live telemetry dashboard (v7)              |
| grid        | `iracing_grid.py`             | 5001 | Qualifying grid with colored silhouettes   |
| results     | `iracing_results.py`          | 5002 | Full race results (gaps, incidents, FL)    |
| lite        | `iracing_results_lite.py`     | 5003 | Minimal results overlay                    |
| live        | `iracing_live_indicator.py`   | 5004 | LIVE / REPLAY badge for OBS                |
| standings   | `iracing_standings.py`        | 5005 | Live race standings + session info bar     |
| livery      | `iracing_livery.py`           | 5006 | Car livery of the driver on camera         |
| trackmap    | `iracing_trackmap.py`         | 5007 | SVG track map + live car dots              |
| flag        | `flag_overlay.py`             | 5008 | Flag status overlay (session flags)        |
| logger      | `iracing_race_logger.py`      | 5009 | Race logger — JSONL log per race           |
| sess        | `iracing_session_info.py`     | 5010 | Session name + total length + remaining    |

All overlays are Flask apps that read iRacing telemetry via `pyirsdk`,
designed to be added as browser sources in OBS. They run in parallel on
different ports.

## Launchers (all three MUST stay in sync when overlays change)

- `launch_all.bat` — Windows batch, one console window per script
- `launch_all.py` — single-terminal CLI launcher, colored prefixes
- `launch_gui.py` — Tkinter desktop app with Start/Stop/Open per overlay,
  Start All / Stop All, collapsible log pane
- `launch_gui.bat` — double-click shortcut that runs `launch_gui.py` via
  `pythonw` so no console window appears

## IMPORTANT: Maintenance rule

**Whenever a new `iracing_*.py` overlay is added to this folder (or an
existing one is renamed / given a new port), ALL FOUR launcher files
must be updated in the same session.** The three that actually list
overlays:

1. `launch_all.bat` — add a new `start "…"  cmd /k python <newscript>.py`
   line and update the port list in the echo block.
2. `launch_all.py` — append a tuple to the `SCRIPTS` list near the top:
   `(tag, "iracing_foo.py", port, "\033[9?m")`.
3. `launch_gui.py` — append a tuple to the `OVERLAYS` list near the top:
   `(tag, friendly_name, "iracing_foo.py", port, "#hexcolor")`.

Keep tags short (single lowercase word). Use distinct colors per overlay
so log output and status dots stay visually clear.

## Other conventions

- Port numbers increment from 5000. Don't reuse.
- **Shared poller base class:** `iracing_sdk_base.py` provides `SDKPoller`
  (IRSDK connection, poll loop, Lock-protected `data` dict, graceful stop)
  and `setup_utf8_stdout()`. 7 of the 8 non-dashboard overlays inherit
  from `SDKPoller` and only implement `_read_snapshot()`. Exceptions:
  `iracing_dashboard.py` keeps its hand-rolled poller (large, fragile,
  never migrated); `flag_overlay.py` is a state machine with a different
  public surface (`get_state()`, not `get()`) and doesn't fit cleanly.
- All scripts use `pyirsdk` + `flask`. `iracing_dashboard.py` additionally
  uses `pywin32` for the "Go Live" keyboard-event feature.
  `iracing_livery.py` additionally uses `pillow` to convert the TGA paint
  files from iRacing's paint cache to PNG on the fly.
  `iracing_trackmap.py` additionally uses `requests` to call the iRacing
  members-ng API; helper lives in `iracing_auth.py`.
- Press `H` on dashboard/grid/results/results_lite/standings/livery/trackmap
  for stream mode (toggles transparent BG for OBS Browser Sources).
  `iracing_live_indicator.py` and `flag_overlay.py` are intentionally
  always-transparent — they're pure overlay elements with no background
  to toggle, so they don't ship a stream-mode key.
- All overlays do a UTF-8 stdout reconfigure at import time to survive
  Windows cp1252 code pages. Without it, a single non-ASCII character in
  a print() call inside an except block can silently kill the poller
  thread. This bit us hard once — don't remove.
- Scripts are Windows-only in practice (iRacing runs on Windows), but the
  Python launcher is cross-platform.

## Track map overlay — offline (SIMRacingApps track library)

`iracing_trackmap.py` is **offline-only** — no iRacing login required.
Track geometry comes pre-bundled in `./tracks/<TrackName>.json`.

Why: iRacing removed the legacy `members-ng.iracing.com/auth` endpoint
on 2025-12-09 and moved to OAuth2, which requires a client_id/client_secret
that iRacing has paused issuing. The old authenticated flow (saved in
`iracing_auth.py`, kept around as dead code for when OAuth becomes
available) can't log in at all right now.

**Replacement data source:** the open-source SIMRacingApps project by
Jeffrey Gilliam (Apache 2.0) has a hand-built library of per-track GPX
routes for ~200 iRacing tracks. We converted those into a single
simplified JSON per track at bundle time. Runtime just:
1. reads `WeekendInfo.TrackName` from the SDK,
2. loads `./tracks/<name>.json`,
3. projects the lat/lon waypoints to 2D with an equirectangular
   projection around the track center,
4. serves an SVG of the outline + pit lane,
5. places car dots by projecting `CarIdxLapDistPct` onto the polyline
   via cumulative arc-length interpolation.

Attribution: `./tracks/NOTICE.txt` credits Jeffrey Gilliam's
SIMRacingApps project as the source of the geometry.

**Coverage:** 205 of ~400 iRacing tracks. Tracks without an ONTRACK GPX
upstream show a friendly "TRACK MAP NOT BUNDLED" message on the
overlay. When SIMRacingApps adds new tracks, re-run the bundling
conversion (`tracks/` folder) to pick them up.

`iracing_auth.py` and `iracing_auth.json` are effectively dead code now.
Keeping them in place for when iRacing resumes OAuth client registration;
at that point the trackmap can optionally add an "update the JSON cache
from iRacing's CDN" path. For now everything works without any login.

## Car brand logos

The `iracing_standings.py` overlay shows a manufacturer logo column.
Resolution is handled by `car_brands.py`:

- `detect_brand(car_path, car_screen_name)` — maps iRacing CarPath (or
  CarScreenName fallback) to a short slug (`porsche`, `bmw`, `ferrari`, …)
- `resolve_logo(slug)` — looks up a file in `./brands/` tolerantly.
  Matches are case-insensitive and accept separator suffixes, so slug
  `ferrari` finds `ferrari-ges.svg`, `mercedes` finds `mercedes-benz.svg`,
  `dallara` finds `Dallara.svg`.
- Flask serves the file via `/brand/<slug>` from the standings overlay.

Add new brands by (a) dropping a `brands/<slug>.svg` file and (b) adding
an entry to `CAR_PREFIX_TO_BRAND` in `car_brands.py` if it's a car family
that isn't already prefix-matched.

## Recent sessions

**April 21, 2026:** Created the 5 overlay scripts.
**April 22, 2026:** Added `launch_all.bat`, `launch_all.py`, `launch_gui.py`
+ `launch_gui.bat` (Tkinter desktop launcher with status dots, Start/Stop
per overlay, Open-in-browser, Start All / Stop All, collapsible log pane,
dark racing theme matching the overlay styling).
**April 22, 2026 (later):** Added `iracing_standings.py` (port 5005) — live
session standings overlay with a top info bar (session type, elapsed,
remaining/total time, weather, track temp), a driver-count bar (on track
/ entered), and a standings list (position, #, driver, interval, best lap).
Race sessions use `CarIdxF2Time` for the interval; quali/practice sort by
best lap time and show gap to P1. All three launchers (`launch_all.bat`,
`launch_all.py`, `launch_gui.py`) were updated in the same session, per
the maintenance rule.

**April 22, 2026 (evening):** Added `car_brands.py` (brand detection +
logo file resolver) and a `brands/` folder for manufacturer SVGs. Added
a brand-logo column to the standings overlay and a brand slot on the
livery overlay. Also added `iracing_livery.py` (port 5006) — the "on
camera" livery overlay. Tracks `CamCarIdx` and for the watched driver:
(1) if a custom paint TGA exists at
`%USERPROFILE%\Documents\iRacing\paint\<carpath>\car_<custid>.tga`,
converts it to PNG via Pillow and serves it; (2) otherwise falls back
to a colored silhouette card built from `CarDesignStr` (pattern + 3
colors); (3) shows driver name, car #, car model, brand logo, license
chip (color from `LicColor`), iRating, best lap, position, pit flag.
Stream-mode toggle via `H`. Trading Paints integration was considered
but deferred — MVP relies on the flat TGA cache which is already there
for anyone who runs Trading Paints. All three launchers and CLAUDE.md
updated per the maintenance rule.

**April 22, 2026 (late):** Added `iracing_trackmap.py` (port 5007) — a
small track-map widget that fetches iRacing's official SVG track assets
from members-ng.iracing.com and overlays live car dots positioned by
`CarIdxLapDistPct`. Uses a new helper `iracing_auth.py` (login + cookie
persistence + /data/track/assets + asset download). Credentials live in
`iracing_auth.json` (template auto-created on first run). Track SVGs
cached forever in `trackmaps/cache/<track_id>/`. Layer draw order
background → inactive → active → pitroad → start-finish → turns, all
re-styled via CSS to match the dark theme. Camera-followed car gets a
halo + brighter fill; pit cars dimmed. All three launchers + CLAUDE.md
updated. Adds `requests` as a dependency (pip install requests).

**April 23, 2026 (dashboard — auto-camera modes + HUD-hide fix):**
Added three new camera-mode buttons to the dashboard, alongside the
existing camera groups:
  1. `MOST EXCITING` — toggles iRacing's `CamUseAutoShotSelection` bit
     (0x0004) via `ir.cam_set_state(...)`.
  2. `FOCUS LEADER` — poller locks camera on overall P1 each tick via
     `cam_switch_num`; disabled during a focus-crashes hold window.
  3. `FOCUS CRASHES` — hooks into `_emit_incident`: on a 2x/4x event
     (lost_control/collision), camera snaps to the crashed car and
     holds for ~12 s before another crash can steal focus.
All three are mutually exclusive and any regular camera-group click
turns them off.

CRITICAL GOTCHAS around `ir.cam_set_state(...)` — discovered the hard
way this session:
  • **Never set `CAM_TOOL_ACTIVE` (0x0001) on the state.** That bit
    puts iRacing into "camera tool" mode, which has the side effect of
    showing the HUD. Once set, every camera operation re-surfaces the
    tool UI. Only write `CAM_USE_AUTO_SHOT` (0x0004) plus whatever was
    already in the `CamCameraState` bitfield (especially
    `CAM_UI_HIDDEN`, 0x0002).
  • **Make `cam_set_state` idempotent.** Read `CamCameraState` first,
    bail out early if the auto-shot bit already matches. Firing
    cam_set_state gratuitously also pops the HUD even with the bits
    above handled correctly. `switch_camera_group` calls
    `set_auto_camera(False)` on every click, which used to fire
    cam_set_state unconditionally — and broke the user's
    spacebar-hidden HUD every time. Now it's a cheap no-op when auto-
    cam wasn't on.

Companion change: HUD-hide tracking. iRacing's broadcast HUD is
toggled with spacebar; every camera switch re-shows it. Dashboard now
tracks `poller.iracing_ui_hidden` (toggled by `/hide_iracing_ui`
endpoint). Every code path that calls `cam_switch_num` /
`cam_set_state` calls `_reassert_ui_hide()`, which — if the flag is
true — re-sends spacebar in a 0.25 s-delayed daemon thread.

**April 26, 2026 (race logger — public share endpoints + Cloudflare):**
Built the public-share path so remote viewers (Twitch chat / Discord)
can open a self-service chart and pick their own drivers without
affecting the operator's OBS source.

- New `/share/data` (stateless JSON), `/share/chart` (picker + chart
  HTML), `/share/standings` (mobile-friendly table), and
  `/share/standings/data` (JSON for the table). All accept driver
  selection via URL params using **car_number** (the user-visible
  "#11" string), not internal car_idx — so URLs are stable across
  sessions and shareable.
- Driver selection lives entirely in URL params on the share page,
  with `history.replaceState` keeping the URL in sync. Each remote
  viewer's selection is independent — no server-side state per
  remote viewer. Operator's chart selection is unaffected.
- New "gap to leader" chart type added to BOTH the operator's
  /chart/render and the share page, alongside the existing lap-time
  and position views. Y-axis inverted (leader at top, gaps falling
  below) — F1-broadcast convention.
- Defense-in-depth gate: a Flask `before_request` middleware detects
  the `Cf-Ray` header (only Cloudflare adds it) and returns 404 for
  any path that isn't `/share/*`. So even if cloudflared is
  misconfigured to forward everything, the local server itself
  refuses to serve admin endpoints (operator panel, log downloads,
  /chart/select, /status, etc.) to remote viewers. Local LAN access
  unchanged.
- New file `CLOUDFLARE_TUNNEL_DE.md` — German setup guide covering
  cloudflared install, quick tunnel command, optional named-tunnel
  config with own domain, and security model.
- Public payloads are filtered: no log paths, no irating, no team
  names — minimum data needed for the chart and standings to render.

**April 26, 2026 (race logger — live charts for OBS):**
Added a broadcast-friendly chart pipeline to the logger:

- New endpoints `/chart/state`, `/chart/select`, `/chart/top3`,
  `/chart/render`. `/chart/render` is the page added as an OBS
  browser source (600×360, transparent BG); the others are the
  operator API.
- Operator UX in the existing live monitor (port 5009 root): every
  driver row is now clickable to pin/unpin from the chart. Pinned
  rows get a colored left border in the row's chart color. A new
  "Live chart" panel above the tire panel shows pinned drivers as
  removable chips, a chart-type segmented control (Lap times /
  Position), Top 3 / Clear buttons, and a link to the OBS source URL.
- Two chart types in v1: **lap times** (line, lower=faster) and
  **position** (step-after line, P1 at top — chart is inverted on
  Y axis). One line per pinned driver, stable color per driver from
  CHART_PALETTE indexed by sorted car number, dot per lap, gold
  outline on the best lap, small wrench dot on pit laps.
- All chart drawing is pure SVG generated client-side — no Chart.js
  / D3 / external libs. Keeps the project's "no CDN dependencies"
  invariant.
- State (selected drivers + chart type) lives in the poller, so all
  browser windows share the same view. Chart_lap_data is a separate
  per-driver lap-history dict (full race kept) distinct from the
  bounded slow-lap detector window. Capped at 5 pinned drivers.

**April 26, 2026 (race logger — defer session_end until ResultsOfficial):**
The session_end event used to fire as soon as `SessionState >= 5` (the
leader's checkered crossing), but `ResultsPositions` at that moment
still showed trailing drivers as "in progress". Trailing-driver lap
events would then continue to be appended to the log AFTER session_end,
making the file confusing to parse.

Fix: `_maybe_emit_final()` now waits for `ResultsOfficial == 1`,
which iRacing flips ~30-60s after the slowest car finishes. By then
the classification is locked. Refactored the final-writing logic
into `_write_final(session, official)` so it can be reused.

Fallback for graceful shutdown: new `_write_final_provisional()`
attempts to write session_end if we're closing the log without ever
having seen ResultsOfficial flip (race abandoned, user Ctrl+C'd, or
session transitioned early). Marked `official=False` so post-race
tools can tell. Also added `RaceLogger.stop()` that overrides
`SDKPoller.stop()` to call the provisional writer BEFORE the base
class shuts down the SDK (after `ir.shutdown()` we can't read
ResultsPositions anymore). Net result: every race log now ends with
a session_end event, and that event is the truly final one whenever
possible.

**April 26, 2026 (race logger — pit / flag / penalty / slow-lap events):**
Lifted four ideas from a mobile-Claude rewrite the user shared and
integrated them properly into the existing logger:

1. **`pit` events** — watches `CarIdxOnPitRoad` transitions per car.
   Records entry time + lap, computes duration on exit, increments a
   per-car stop count, and emits a `pit` event. Drive-throughs <2s
   are filtered as edge-of-pit-lane noise. Pit count is also exposed
   as a `pit_stops` field on the live drivers table (rendered as
   `🔧×N` in the driver name sub-line).

2. **`flag` events** — watches session-wide `SessionFlags` for newly-
   set bits matching a curated whitelist (Green / Yellow / Red /
   White / Checkered / YellowWaving / OneToGreen / Caution). Skips
   internal start-state bits and the `RandomWaving` test signal.
   The mobile version's "every flag bit" approach would have spammed
   the log.

3. **`penalty` events** — watches per-car `CarIdxSessionFlags` for
   newly-set BLACK / DISQUALIFY / BLUE / REPAIR bits. The mobile
   version's `CarIdxF2Time != 0` approach was wrong (F2Time is just
   the gap to the car ahead, which changes constantly). The per-car
   flag bitmask is iRacing's actual penalty signal.

4. **`slow_lap` events** — keeps a per-driver rolling 5-lap window;
   when a new lap is more than 10% slower than the average, emits a
   `slow_lap` event with the delta. Pit laps are skipped (they're
   naturally slower). Useful as a broadcast camera hint.

All four show up in both the JSONL file and the live monitor
timeline (with their own colors and icons). Constants for the
thresholds and bit-name maps are at module top so they're easy to
adjust later.

**April 26, 2026 (race logger — incident count fix):** Two-part bug
fix for the live monitor's "INC" column staying at 0 for everyone:

1. **Logger was fetching the wrong URL.** It hit
   `http://localhost:5000/incidents` but the dashboard didn't have a
   plain `/incidents` route — incidents were only embedded inside
   `/telemetry` under the `incidents` key, so the logger silently 404'd
   on every poll (the try/except swallowed it). Added a focused
   `/incidents` endpoint to `iracing_dashboard.py` that returns
   `{"incidents": [...]}` — same data, smaller payload, matches the
   logger's expectation. The original `/telemetry` route still embeds
   incidents too, so nothing is broken.
2. **Incident count was keyed by `car_number` (string).** Re-keyed by
   `car_idx` (numeric) — always present in the dashboard's payload,
   never empty. Both the count update in `_incident_loop` and the
   lookup in `_build_drivers_state` now use the numeric key.
   Defensive against future edge cases where `car_number` could be
   missing in spectator scenarios.

**April 25, 2026 (race logger — position ticks + render_race.py):**
Added two pieces that together produce a 2D animated MP4 replay of any
logged race:
1. `iracing_race_logger.py` now emits a `pos` event once per second
   during a race, capturing every car's `CarIdxLapDistPct`
   ({"type":"pos","t":...,"p":{"3":0.234,...}}). Compact format —
   adds ~360 KB per 30-min race. Also stamps `WeekendInfo.TrackName`
   into the session_start meta so the renderer can find the matching
   track JSON.
2. `render_race.py` — standalone CLI that reads any race JSONL,
   loads the matching `tracks/<TrackName>.json`, and renders the
   entire race as an MP4. Pillow for frame drawing, ffmpeg for video
   assembly (uses `imageio-ffmpeg`-bundled binary when available so
   Windows users don't have to install ffmpeg manually). Top-down
   view with the track outline, numbered car dots, leaderboard panel
   on the right, lap counter, and incident flashes when an incident
   fires. Linear interpolation between position ticks → smooth 30 fps.
   Self-contained — copies the projection math from
   `iracing_trackmap.py` rather than importing the Flask overlay.
   Limitations: only works on logs recorded after the position-tick
   feature was added; track outline must exist in `tracks/`.

**April 25, 2026 (race logger — car/class, tire temps, overtake counts):**
Extended the logger payload and the live monitor with three new fields:
(a) **car / car_class** — already in the session_start drivers list,
now also stamped on every lap event AND surfaced as a sub-line under
each driver's name in the live table (with the class slug colored
blue for multi-class disambiguation). (b) **tire surface
temperatures** — read via `LFtempL/M/R`, etc. iRacing only broadcasts
these for the LOCAL player's car (no per-car array exists), so they
get stamped on lap events only when the lap belongs to the local
player. The live monitor also shows a "Your car" panel with all four
corners color-coded (cool/ok/hot thresholds tuned for slick GT3
tires); the panel auto-hides for pure-spectator users where no tire
data is broadcast. (c) **overtakes / overtaken** — derived from
CarIdxPosition deltas tick-over-tick. New `_update_overtake_counters`
runs every poll, increments per-car counts whenever a position
changes (also captures indirect movement, matching iRacing's own
"positions gained / lost" definition). Counts are stamped on each
lap event AND shown live in a `+/−` column on the drivers table
(green up arrow / red down arrow / muted zero). Race-scoped: cleared
on each new race via `_open_log`.

**April 25, 2026 (race logger UI expanded → live race monitor):**
The Flask page on port 5009 was a minimal status display. Rewrote it
into a full live race monitor: top bar with track / session /
elapsed / weather / track temp; counts row (on track / in pits / out /
laps logged / incidents logged); two-pane main area with the live
drivers table on the left (position, #, driver, last lap, best lap,
gap to leader, incidents count, pit/DNF flags) and the event timeline
on the right (recent lap completions and incidents, newest first);
past-logs section at the bottom with download links. The drivers
table is always live (works during practice/quali too); the timeline
only populates while logging a race. Same script, same port — just
a much more useful page. New `_build_drivers_state()` helper reads
all the per-car telemetry; `_recent_events` deque(maxlen=80) on the
poller buffers lap+incident events for the timeline.

**April 24, 2026 (race logger added):** New standalone overlay
`iracing_race_logger.py` (port 5009) that writes a JSONL log per race
session into `logs/<timestamp>_<track>_race.jsonl`. Inherits from
`SDKPoller` (Batch 2 base class). Captures: session_start (track,
session type, drivers list, weather), one event per lap completed by
each driver (lap time, position, gap, on-pit), incidents fetched from
the dashboard's `/incidents` feed (deduped by `(t_session, car_idx,
type)`), and a final classification when iRacing flips to checkered
(positions, laps, best lap, incident counts, status). Skips practice
and qualifying sessions. Tiny Flask UI on port 5009 lets the user
download the current log and browse past logs. `logs/` is gitignored
so per-race files don't pollute the repo.

**April 24, 2026 (trackmap — Monza added from user-drawn GPX):**
SIMRacingApps didn't have Monza in its track library, so `monza_full`
was missing from `tracks/`. User drew the racing line in
https://gpx.studio/ and exported GPX. Added `tracks/gpx_to_json.py`
as a reusable converter (argparse: `python gpx_to_json.py <file.gpx>
<iracing_track_name>`) and used it to produce `tracks/monza_full.json`
(248 points, closed loop, center 45.62N 9.29E). `tracks/NOTICE.txt`
updated with a "user-drawn tracks" section to make the provenance
clear alongside the Apache-2.0 SRA-sourced files. Any future track
iRacing runs that SRA doesn't have (Spa, Nürburgring, etc.) can be
added with the same GPX → JSON workflow.

**April 24, 2026 (dashboard — yellow-zone incident detection):**
Added a fourth spec-mode incident detector in `_update_incidents`:
when iRacing sets the LOCAL_YELLOW bit on a car's `CarIdxSessionFlags`
(the per-car bitmask, not the session-wide one), we treat that as
"iRacing detected an incident in this car's zone" and emit a
`lost_control` incident. This is iRacing's own authoritative signal
for "something happened" and catches the incidents our yaw / lap-
regression / stopped-on-track thresholds let through (brief slides,
light taps, quick recoveries). Two layers of dedup: (a) a global
5-second cooldown so the multiple cars that receive the yellow bit
simultaneously from one physical event only fire one emission, (b)
the per-car `_incident_cooldown` inside `_emit_incident` which
suppresses yellow-based emissions when a yaw/regression detector
already caught the same event seconds earlier. An old "intentionally
not emitted" comment was removed — it was about the session-wide
`SessionFlags`, but the per-car `CarIdxSessionFlags` is genuinely
per-event and actionable. Fixes the CAS Porsche Cup comparison with
iOverlay, which was catching a few more incidents than the dashboard.

**April 24, 2026 (flag overlay — session-change reset + hardened
timed-race detection):** Two fixes in `flag_overlay.py`:

1. The state machine (`state`, `_white_shown`, `_check_shown`,
   `_lap_times`, etc.) was only reset on SDK disconnect — NOT on session
   change. After qualifying's checkered fired, `state` stayed `"done"`
   and every tick of the subsequent race(s) bailed out at
   `if self.state == "done": return`. That's why yesterday's CAS stream
   saw the flag in quali but never in the two races. Added
   `_last_session_num` tracking + `_reset_session_state()` that fires
   whenever `SessionNum` changes, clearing per-session state (lap
   times, shown flags, etc.) while preserving connection state.

2. Hardened the timed-race white-flag trigger from a single condition
   (`time_rem < avg_lap AND crossed_sf`) to three alternatives, any of
   which fires at the S/F crossing:
     (a) same as before — time_rem < avg_lap and we have a good avg_lap.
     (b) time_rem <= 0 — iRacing "+1 lap" rule says the first S/F
         crossing after timer expiry starts the leader's final lap.
         Fires even with no avg_lap estimate (short races, first-lap
         leader, pit-stop-inflated avg_lap, …).
     (c) SessionState >= 5 (Checkered / CoolDown).
   The log line now prints which trigger fired so the next race we can
   verify the path lived up to expectations.

**April 24, 2026 (results / results_lite — persist last-race classification
during warmup):** Added `_find_last_completed_race(sessions)` to both
`iracing_results.py` and `iracing_results_lite.py`, and switched
`_read_snapshot()` to use it as the fallback when the current session
isn't a race. The old `_find_race_session()` returned the LAST race in
the weekend plan regardless of whether it had any data; in a Race 1 →
Warmup → Race 2 league format that meant the overlay blanked during
the warmup (it tried to show Race 2, which was still empty). The new
helper walks sessions in reverse and returns the most recent race
whose `ResultsPositions` is populated — so Race 1's final
classification stays visible all through the warmup for broadcast /
debrief purposes. Fixes the CAS Community Porsche Cup complaint.

**April 24, 2026 (standings — live position updates):** Replaced
`CarIdxPosition`-based ordering in `_build_race_standings()` with
live track-progress sorting (`CarIdxLap + CarIdxLapDistPct`).
iRacing only updates `CarIdxPosition` at the start/finish line, so
an overtake mid-lap used to take up to a full lap to appear in the
standings. Now positions update the instant the pass happens — same
technique broadcast tools like iOverlay / RaceControl use. Within
each class we sort in-world cars first, then out-of-world (DNF /
garage) below, both groups by descending progress. `CarIdxF2Time`
still drives the interval column; it doesn't have the S/F-lag
problem because it's a race-time measurement. The old raw iRacing
position is still read and kept under `iracing_pos` on each row for
diagnostics. Fixes the Porsche Cup broadcast complaint: positions
were only updating at S/F crossings.

**April 23, 2026 (standings — iOverlay-style pass + real gap + lap-
down fix):** Multiple iterations on `iracing_standings.py`:
  • Tighter row rhythm (6 px vertical padding), amber accent on pit
    columns, compact top info bar with SVG icons + session pill (no
    labels), class separator rows (uses `CarClassColor` from
    DriverInfo), per-car pit tracking (we record `CarIdxOnPitRoad`
    transitions to derive last-pit lap + pit-lane time), first-name
    abbreviation (`Joseph Johnson → J. Johnson`), bigger driver font
    (32 px) and interval font (28 px).
  • Include drivers with `CarIdxPosition == 0` — hid everyone on the
    formation lap and in replays before. Fallback sort by `lap +
    lap_pct` when no positions are assigned.
  • Out-of-world drivers (CarIdxTrackSurface == -1) now sort to the
    bottom of their class with recomputed `class_position`, rather
    than sitting at their stale last-known position while the field
    laps them.
  • **Interval is now gap-to-car-ahead**, not gap-to-leader.
    `CarIdxF2Time` IS cumulative "race time behind the class leader",
    which we now store as `_gap_to_leader` and diff between consecutive
    rows-in-same-class to get the real per-car interval. Same
    technique the dashboard uses.
  • **Lapped detection uses track progress** (`lap + lap_dist_pct`),
    not integer lap count. The raw count used to flicker "+1 LAP" for
    the whole field every time the leader crossed the finish line
    because iRacing's `CarIdxLap` bumps for the leader a heartbeat
    before the chasing cars hit the line.

**April 23, 2026 (dashboard — incident filter + replay fix +
readability + sector-times removal):**
  • Narrowed incident feed to only the 2x (spin) and 4x+ (collision)
    flavours of `CurDriverIncidentCount` jumps. Removed noisy
    "stopped" / yaw-rate-based "lost_control" / yellow-flag emitters.
    Auto-replay trigger list is now `{"lost_control", "collision"}`.
  • Replay 10s now uses `ir.replay_search_session_time(session_num,
    (t_session - 5) * 1000)` to seek to the actual incident time
    (passed from `/replay_5s` via `incident_id` → stored
    `incident["t_session"]`). Previously rewound 10 s from *now*, so
    if the user paused before clicking the accident was already out of
    the window. Also: seek → cam switch → 0.3 s sleep → play-speed
    1x. Out-of-order ops were leaving playback stuck paused.
  • Removed Sector Times card (UI + JS renderSectors + CSS). Backend
    sector tracking still in place but unused.
  • Dashboard text bumped ~30 % (driver list 12→15 px, race progress
    22→28 px, incident feed 12→15 px, active banner 14→17 px, etc).
    Camera buttons enlarged (12 px → 17 px, 6/12 px padding → 12/22).

**April 23, 2026 (live indicator — ReplayFrameNumEnd meaning):**
`CarIdxFrameNumEnd` is NOT "absolute end-of-buffer frame" — it's
"frames the playhead is BEHIND the live tip" (0 = at tip, 600 = 10 s
back). The original heuristic `(end - frame) <= 60` was nonsense and
falsely reported LIVE during 1x replay playback whenever the playhead
sat near the end.
Fix: `at_end = end <= 60`. Combined with iRacing's direct
`IsReplayPlaying` flag, the decision is now:
  • Not in replay mode (IsReplayPlaying=False) → LIVE.
  • In replay mode AND at the tip AND 1x speed → LIVE (catch-up).
  • Otherwise → `paused` / `rewind` / `fast_forward` / `slow_motion` /
    `replay`.
Also added `/debug` endpoint with raw field values + decision branch.

**April 23, 2026 (all overlays — LAN-accessible):**
Every `app.run(host=...)` flipped from `127.0.0.1` to `0.0.0.0` so
overlays are reachable from other PCs on the LAN. OBS on the same
machine still hits `localhost` fine. `iracing_dashboard_v8.py` (a
legacy unused dashboard) left at `127.0.0.1` since it isn't in the
launcher list.

**April 23, 2026 (flag overlay port fix):** `flag_overlay.py`
hardcoded `port=5007` (already used by trackmap). Fixed to 5008 in
three places: docstring, startup print, and the `app.run` call.

**April 23, 2026 (trackmap goes offline):** iRacing removed the legacy
`/auth` endpoint on 2025-12-09 and moved to OAuth2, which requires a
`client_id`/`client_secret` that iRacing has paused issuing. The
members-ng-based `iracing_trackmap.py` couldn't log in at all. Rewrote
the script to be fully offline, using pre-bundled track geometry from
SIMRacingApps' open-source track library (Apache 2.0, by Jeffrey
Gilliam). Conversion:  cloned `SIMRacingAppsServer` from GitHub, took
its `src/com/SIMRacingApps/Tracks/*.json` metadata + companion
`*-ONTRACK.gpx` / `*-ONPITROAD.gpx` route files, merged each triple
into a single simplified JSON (trackname, latitude, longitude, north,
resolution, merge_point, finish_line, ontrack[][lat,lon], onpitroad[][lat,lon]),
and shipped them in `./tracks/`. 205 tracks covered (of ~400 in
iRacing); tracks without upstream GPX data show "TRACK MAP NOT BUNDLED".
The script now has zero network dependencies — it only reads the local
iRacing SDK. Attribution lives in `./tracks/NOTICE.txt`.
`iracing_auth.py` and `iracing_auth.json` remain in the folder as dead
code for a potential future OAuth path; the main() of the new
`iracing_trackmap.py` no longer imports them.

**April 23, 2026 (flag overlay wired in):** Added `flag_overlay.py`
(port 5008, tag "flag") to all three launchers (`launch_all.py`
SCRIPTS list, `launch_all.bat` echo + start lines, `launch_gui.py`
OVERLAYS list) plus the scripts table in this file — per the
maintenance rule. NOTE: the script itself wasn't inspected in
this session (the file was present on disk but Nextcloud had not
yet synced its contents down — showed as 22 KB of null bytes).
Port 5008 and tag "flag" were picked by convention; if the actual
script uses a different port, the launcher entries will need a
quick edit.

**April 22, 2026 (standings tweak):** Bumped the driver-name font from
15px to 17px and tightened `.driver { padding-right }` from 10px to
4px so the name sits closer to the interval column. Bumped the team
sub-line from 11px to 12px for consistency. Nothing else changed on
`iracing_standings.py` — the transparent-overlay experiment was
reverted in the same session after the user preferred the dark
panel look with columns intact. Also added zebra striping on the
standings rows: `.standings .row:not(.head):nth-child(odd)` gets
`rgba(0,0,0,0.22)`, `:nth-child(even)` gets `rgba(255,255,255,0.04)`,
with `:hover` rule listed after both so equal-specificity order makes
hover win. The header row (`.row.head`) is excluded via `:not(.head)`
so its own dark bar stays unaffected.

**April 22, 2026 (livery minimal):** Stripped `iracing_livery.py`'s overlay
card down to just the car render on the left and the driver name on the
right — no car number, car model, team, license, iRating, best lap,
brand logo, or "On Camera" bar. Made the whole overlay transparent by
default (no body/card/column backgrounds or borders) so it composites
cleanly as an OBS browser source. `html, body { background-color:
rgba(0,0,0,0); }` explicitly because OBS's Chromium needs both. The
toggle button is hidden by default and only appears in debug mode.
Driver name sits inside an inline-block pill (`rgba(20,20,28,0.92)`
background, 8×16px padding, 4px radius, `white-space: nowrap`) so the
dark grey backdrop hugs the text and grows with longer names. The old
"Stream mode" toggle was inverted into "Debug background (H)" —
transparent is now the default; pressing H adds a dark card back +
border for layout debugging. A lot of the original CSS (`.live-bar`,
`.driver-block`, `.stat-row`, `.car-number`, `.meta`, `.brand-slot`,
`.license-chip`, `.pos-badge`, `.pit-flag`, `.team-line`, `.stat`) is
now dead code; kept in place for now in case the fields come back.

**April 22, 2026 (livery rework):** Rewrote `iracing_livery.py` to show a
real 3D rendered car with the driver's livery, not just the flat TGA.
Discovery (via SIMRacingApps source on GitHub): **iRacing runs a local
HTTP render server on `http://127.0.0.1:32034/pk_car.png`** whenever the
sim is running. It accepts query params (`carPath`, `carPat`, `carCol`,
`number`, `numPat`/`numfont`/`numSlnt`/`numcol`, `licCol`, `sponsors`,
`club`, `name`, plus `carCustPaint=<full path to TGA>`) and returns a
PNG of the car with all of that applied — including the custom paint
wrapped onto the 3D model. No auth, no CDN, no Trading Paints. Added a
`/carview/<car_id>/<cust_id>.png` Flask route that proxies this with
in-memory caching. Source-preference chain in the overlay JS:
(1) `/carview` → iRacing render, (2) `/livery` → flat TGA, (3) design
card. Also hardened the poller: `sys.stdout` reconfigured to UTF-8
(Windows cp1252 + arrow chars was killing the thread via
UnicodeEncodeError in an except block — silent failure that took hours
to find). Every print + data assign is now wrapped so a print failure
can never propagate out of the poll loop. `/debug` endpoint exposes
`poller_thread_alive`, `poller_iteration`, `poller_last_branch`,
`poller_last_error`, `last_startup_status`, `last_state` — essential
for diagnosing SDK-connection edge cases. `requests` is a soft import:
if missing, just disables the render feature and logs a note.
