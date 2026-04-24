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
