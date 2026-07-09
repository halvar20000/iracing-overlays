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
| champ       | `iracing_championship.py`     | 5010 | Live championship overlay (CLS league-manager API) |
| sess        | `iracing_session_info.py`     | 5011 | Session name + total / remaining time card |
| line        | `iracing_drivingline.py`      | 5012 | Corner cues (driving-line substitute)      |
| dotd        | `iracing_dotd_overlay.py`     | 5013 | Driver of the Day (from race logs, no SDK) |
| delta       | `iracing_qualidelta.py`       | 5014 | Qualifying live delta + per-sector splits  |
| catch       | `iracing_catchup.py`          | 5015 | F1-style catch-up battle (gap + catch prediction) |
| weather     | `iracing_weather.py`          | 5016 | Weather strip (temps, rain, wind, sky + live trends) |
| driver      | `iracing_drivercard.py`       | 5017 | Driver card (name, team, iRating, license, laps, inc) |
| racectrl    | `iracing_racecontrol.py`      | 8080 | iCASControl steward dashboard (FastAPI; `racecontrol/`) |

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
4. `make_obs_loaders.py` — append to its `OVERLAYS` list and re-run
   (`python make_obs_loaders.py`) to regenerate the self-healing OBS
   loader pages in `obs_loaders/` (local-file browser sources that
   auto-retry until the overlay server is up; added June 12, 2026 —
   OBS sources should point at these files, not at localhost URLs).

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

**July 9, 2026 (quali delta — "vs OWN BEST" reference mode added):**
User wanted a second version of the Quali Delta overlay that compares each
driver against their OWN fastest lap instead of the session best (pole).
Chosen delivery (user picked): a MODE TOGGLE on the existing overlay
(`iracing_qualidelta.py`, port 5014) — NOT a new port — so no launcher /
make_obs_loaders changes were needed. Reference = the driver's own
session-best lap.
  • BOTH references are computed server-side every snapshot and returned
    under a new `refs` dict: `refs.session` (vs pole, the old behavior) and
    `refs.own` (vs own best). The front-end picks which to show, so you can
    even run TWO OBS browser sources at once: `http://localhost:5014`
    (pole) and `http://localhost:5014/?ref=own` (own best). Live toggle
    with the **M** key; `?ref=own` sets the default.
  • DRIVING mode: own view uses iRacing's predictive `LapDeltaToBestLap`
    (+`_OK`); pole view keeps `LapDeltaToSessionBestLap`. Sector chips were
    already computed vs the driver's own best lap, so both views share them.
  • SPECTATOR mode: refactored the single pole-reference builder into a
    PER-CAR own-best reference (`_car_ref[idx]`, via new `_store_car_ref`);
    the pole reference is simply the fastest car's curve. Own delta =
    on-camera car's live elapsed vs its OWN scaled best-lap curve. Sector
    tracking now records raw times once (`_spec_cam_sector_times`) and
    `_colorize_sectors` tints them against whichever reference is active.
    Anchored to official `CarIdxBestLapTime` (deleted laps never become the
    reference), same guard as pole. Shows "Building this driver's best
    lap…" until the on-camera car has set a clean lap.
  • Verified offline (`test_qualidelta.py`, stubbed irsdk/flask, synthetic
    laps): driving own-vs-pole deltas, spectator own delta (−1.0 under own
    best) distinct from pole delta (0.0 on pole pace), own-reference absent
    until a lap is set, sector colorization. 25/25 pass.
  • NOTE: the cowork SMB mount again served STALE/truncated copies of the
    edited file for the whole session (byte-compile on the mount failed on
    an "unterminated string" that wasn't real) — verified via the direct
    file tools and by running the test from a faithful logic copy in the
    fresh outputs mount. The real file on disk has all edits.
  • FOLLOW-UP: added a self-healing OBS loader for the own-best view —
    `obs_loaders/delta_own.html` and a matching `make_obs_loaders.py` entry
    `("delta_own", "Quali Delta (Own Best)", 5014, "/own")`, so the two
    references can be dropped into OBS as two separate local-file browser
    sources (delta.html = pole, delta_own.html = own best). Loaders share
    the same port/server. No launcher changes (same overlay/port).
  • FOLLOW-UP 2 (OBS loader "Waiting for server…" bug): the first
    delta_own loader pointed the iframe at `http://localhost:5014/?ref=own`.
    In OBS it showed the overlay for a few seconds then fell back to the
    overlay's "Waiting for server…" idle. Root cause suspected: OBS's
    Chromium browser source handles a QUERY STRING inside the loaded iframe
    URL poorly (the working pole loader has none). Fix: added a dedicated
    **`/own` Flask route** (serves the same page; the front-end now sets
    own mode from `location.pathname == "/own"` OR the legacy `?ref=own`),
    and repointed `delta_own.html` + the `make_obs_loaders.py` entry to the
    clean query-string-free `http://localhost:5014/own`. `?ref=own` still
    works for opening directly in a browser.
  • FOLLOW-UP 3 (self-healing loader still "Waiting for server…" in OBS):
    with the /own route the DIRECT browser source (URL) worked in OBS after
    an OBS cache clear, but the self-healing LOCAL-FILE loader still stuck on
    "Waiting for server…". Symptom: the overlay embedded in the loader's
    iframe couldn't reach /status, even though the same URL worked top-level.
    Fix: converted `delta_own.html` from the shared IFRAME loader to a
    REDIRECT loader — it pings until the server answers, then
    `window.location.replace()`s to `http://localhost:5014/own`, so the
    overlay runs as a normal top-level page (identical to the working direct
    source) and self-heals via its own /status polling. Added a
    `REDIRECT_TEMPLATE` to `make_obs_loaders.py` and a 5th entry field
    ("redirect") so regeneration keeps it; the iframe template is unchanged
    for every other loader. Since loaders are "load-once" now, redirect ==
    iframe for self-healing. NOTE: after swapping the file, the OBS source
    must have its cache cleared / be re-added to pick up the new loader.

**July 4, 2026 (standings — periodic "positions gained/lost" view):**
User request: the tower normally shows gap/interval; every 5 min it should
flip for 20 s to show how many places each driver has gained/lost, then
flip back. Implemented in `iracing_standings.py` (no new overlay/port, so
launchers untouched). Design (user chose defaults): baseline = each car's
class position on the STARTING GRID; delta counted WITHIN CLASS; 20 s view
swaps the interval column for ▲N green / ▼N red / = grey + a pulsing
"POSITIONS GAINED / LOST" info-bar pill + "GAINED / LOST" column header;
tower keeps its running order (no re-sort).
  • Cadence constants at module top: `INTERVAL_VIEW_SEC=300`,
    `DELTA_VIEW_SEC=20`. View cycle computed server-side in `_read_snapshot`
    from `time.monotonic()` vs `_cycle_anchor` (phase >= 300 → delta), so
    all browser sources agree. Race sessions only; quali/practice always
    show the interval/lap-time column.
  • Baseline captured ONCE per race in `_build_race_standings`: cars ranked
    within class by iRacing's official `CarIdxPosition` (grid order, stable
    at the start — unlike the live-progress sort in the opening seconds).
    `pos_delta = start_pos - class_position` (+ = gained). Cars with no
    baseline (late joiners) → `=`.
  • Session-change reset: `(SessionUniqueID, SessionNum)` change clears the
    baseline AND re-anchors the cycle, so every race starts fresh in
    interval mode and re-captures its own grid. If the overlay is started
    mid-race, baseline = order at first sight (delta = places since we
    joined) — noted as expected behavior.
  • Verified offline (stubbed irsdk, fake clock): grid capture, +2/-1
    deltas after an overtake, view interval→delta→wrap at 305/330 s,
    session-change reset. 14/14 pass.

**July 4, 2026 (dashboard — `/cameras` endpoint for custom camera sets):**
Context: colleague uses custom iRacing camera sets ("FPV cams") for
better broadcast angles. Custom sets are just camera files dropped into
`Documents\iRacing\cameras\tracks\<track>\` — they appear as ordinary
named camera GROUPS, so the existing `/streamdeck/cam_name/<name>`
endpoint already selects them; NO overlay/launcher change needed.
Added a discovery helper: **`GET /cameras`** on the dashboard (port
5000) returns the loaded session's camera groups (id, name, current
flag, ready-to-use `streamdeck_url`). `?format=text` gives a
copy-paste list with full `http://localhost:5000/streamdeck/cam_name/…`
URLs (spaces %20-encoded) so Stream Deck buttons can be labeled by the
actual group names without guessing. Reads from `poller.get()` snapshot
(camera_groups / current_cam_group already populated). Startup banner +
byte-compile verified; text/json formatting unit-checked offline. Not
an OBS source, so make_obs_loaders.py untouched. Also wrote
`CUSTOM_CAMERAS_GUIDE.md` (pack recommendations + install + wiring).
  • FOLLOW-UP: user added the **FCP broadcast camera pack** to
    `custom_cameras/` (95 tracks + 90 cars, all `fcp.cam`). Binary iRacing
    .cam files; uses STANDARD group names (TV1/TV2/TV3/Chase/Far Chase/
    Rear Chase/Blimp/Chopper/Pit Lane for tracks; Cockpit/Nose/Gearbox/
    Gyro/susp for cars) re-tuned for broadcast — so existing
    `/streamdeck/cam_name/<name>` buttons work unchanged, no code needed.
    Wrote `install_fcp_cameras.bat` (run on the Windows iRacing PC, sim
    closed): backs up `Documents\iRacing\cameras` to a timestamped folder,
    then robocopies `custom_cameras\cars` + `\tracks` in. Guide updated
    with FCP section + ready-to-paste Stream Deck button list. NOTE: the
    281 binary .cam files live in the project folder but must be copied
    into `Documents\iRacing\cameras\` to take effect — syncing the project
    folder alone does NOT install them.
  • FOLLOW-UP 2: user added a SECOND FCP pack `custom_cameras_2/` (109
    tracks / 97 cars). Compared the two: pack 2 is a full SUPERSET of
    pack 1 — all 90 shared car cams + 175/191 shared track cams are
    byte-identical; 16 track sets are IMPROVED in pack 2 (Bathurst
    292KB→542KB, Sebring Intl 235KB→441KB, Homestead roada/roadb, several
    Nürburgring/Suzuka/Silverstone tweaks); pack 2 adds ~16 tracks +32
    cars incl. porsche992rgt3/992cup, mercedesamgevogt3, dallaradw12. The
    2 folders unique to pack 1 (mtwashington, oran) are EMPTY (no .cam) —
    nothing lost. Repointed `install_fcp_cameras.bat` SRC → `custom_cameras_2`.
    Same standard group names, so Stream Deck cam_name buttons unchanged.
    `custom_cameras/` (pack 1) is now redundant/deletable. NOTE: sandbox
    can't delete on the SMB mount (empty mtwashington/oran folders got
    copied into pack 2 during an aborted fold-in and must be removed on
    the Mac). Pack 1 is ALSO undeletable on the Mac right now: it holds
    187 `.smbdelete*` tombstone files (deleted .cam files the SMB server
    hasn't purged because a handle is still open elsewhere) → `rm -rf`
    fails with "Directory not empty". Resolution: added `/custom_cameras/`
    and `.smbdelete*` to `.gitignore` so pack 1 stays out of the repo
    without needing to delete it; the push script already rsync-excludes
    `.smbdelete*`. To physically remove pack 1, eject+remount the "AI"
    share (closes handles → server purges tombstones), then `rm -rf`.

**July 3, 2026 (dashboard — Stream Deck camera-by-NAME endpoint):**
User asked how to drive the dashboard's cameras from a Stream Deck.
Answer: the /streamdeck/<action> GET API already existed (cam_next,
cam_prev, cam/<id>, driver_next/prev, go_live, replay_last*, toggles) —
Stream Deck's built-in "System: Website" action with "Access in
background" checked fires them without opening a browser; no plugin
needed. NEW: /streamdeck/cam_name/<name> — camera group by NAME
(case-insensitive; exact → space-insensitive ("TV 1"=="TV1") →
substring), because group IDs can be renumbered between tracks/sessions
while names are stable. Buttons should use cam_name. Startup banner
updated; byte-compile verified.

**July 3, 2026 (NEW overlay `iracing_drivercard.py` — broadcast driver
card, tag "driver", port 5017):** Lower-third card for the ON-CAMERA
driver (CamCarIdx): name (abbrev) + team, iRating, license/SR chip
(LicColor-tinted), car number + class chip, class position, best/last
lap, incident count. User requested fields incl. incidents.
  • iRating comes from DriverInfo.Drivers[].IRating — iRacing reports
    the rating for THIS session's license category, which IS "the
    iRating of the driven car class" the user asked for. No API needed.
  • Incidents: CurDriverIncidentCount, fallback TeamIncidentCount
    (team sessions). DriverInfo re-parsed every 5 s EVEN when cached —
    unlike other overlays' driver caches, incidents are live data.
  • Position: CarIdxClassPosition, fallback CarIdxPosition when class
    position is 0 (single-class sessions report 0 there).
  • Lap times: CarIdxBestLapTime / CarIdxLastLapTime; 0/negative → None
    (never show "0.000"). LAST cell turns green when last == best
    (personal best just set). Team line hidden when TeamName equals
    UserName (solo sessions report the driver as their own team).
  • Stateless poller (no session-change bookkeeping needed — everything
    read fresh per poll). 2 Hz. Transparent lower third, H / ?debug=1.
  • Four launchers + make_obs_loaders.py updated (19 overlays);
    obs_loaders/driver.html written directly. test_drivercard.py:
    20/20 pass (readout, PB flash, no-laps None, solo-team suppression,
    class-pos fallback, team-incident fallback, hidden states).
  • NOTE: mid-session the Cowork folder connection dropped and
    /Volumes/AI/... was briefly unreachable — user re-selected the
    folder via the picker and all earlier edits were intact.

**July 3, 2026 (NEW overlay `iracing_weather.py` — weather strip, tag
"weather", port 5016):** Horizontal OBS strip: track temp (orange) +
air temp (amber) with trend arrows, humidity %, rain cell (precip % +
TrackWetness label, turns blue when WeatherDeclaredWet or precip ≥1 %),
wind km/h + compass, sky condition, and a green TREND cell.
  • "Forecast" decision (user choice): iRacing's REAL forecast is only
    on the members API (OAuth still paused — trackmap precedent), so
    the overlay shows LIVE TRENDS instead: one sample per 30 s into
    deque(maxlen=20) (~10 min window); trend = mean(last third) vs
    mean(first third) with flat bands (0.15 °C / 0.02 precip); needs
    ≥4 samples (~2 min). Text like "Track cooling · Rain increasing".
  • Telemetry: TrackTempCrew (fallback TrackTemp — some sessions
    report one or the other), AirTemp, RelativeHumidity, Precipitation,
    TrackWetness (enum 0-7 → DRY…EXTREMELY WET), WeatherDeclaredWet,
    WindVel (m/s → km/h), WindDir (radians → 8-point compass), Skies
    (0-3). ALL reads None-safe — pre-rain builds lack the rain vars.
  • Session-change reset clears trend history (June 4 lesson). 1 Hz
    poll (weather moves slowly). Shows in EVERY session type incl.
    practice/quali — conditions matter there too. Transparent strip,
    H / ?debug=1 per convention.
  • All four launchers + make_obs_loaders.py updated (18 overlays);
    obs_loaders/weather.html written directly (stale-mount workaround
    again). Verified offline (test_weather.py, stubbed irsdk, fake
    clock): readout/units, TrackTemp fallback, missing rain vars,
    warming/cooling/rain trends, flat band, declared-wet cell, session
    reset, compass wrap. 26/26 pass.

**July 3, 2026 (NEW overlay `iracing_catchup.py` — F1-style catch-up
battle, tag "catch", port 5015):** Shows, for the ON-CAMERA driver
(CamCarIdx), the battle with the next SAME-CLASS car ahead: live gap,
pace delta from the last 3 clean laps of both drivers, and the F1-style
prediction "CATCH IN ~N LAPS (≈M:SS)". Design decisions (user choices):
camera-follow focus (broadcast use), same-class ahead in multiclass,
3-lap average window, lower-third banner style.
  • Gap = CarIdxF2Time diff between the two cars (F2Time is cumulative
    "behind CLASS leader", so same-class diff = real gap, no S/F lag —
    same technique as the standings overlay). Lap-1 fallback: progress
    diff × EstLapTime. Car ahead a full lap+ up → "+N LAP" shown, no
    prediction (un-lapping isn't a catch battle).
  • Lap-time capture: per-car lap-increment watcher; CarIdxLastLapTime
    is trusted only ≥0.3 s after the crossing (settle window, 3 s
    deadline). Laps touching pit road are EXCLUDED (the pit-road flag
    taints the current lap; since pit road spans S/F this covers both
    in- and out-lap). deque(maxlen=3) per car; car needs ≥2 clean laps
    before a prediction (chip shows GATHERING until then).
  • States: CATCHING (green, prediction shown), LOSING (red), HOLDING
    (gray, |delta| < 0.05 s/lap). Catch laps = gap / pace_delta,
    catch time = catch_laps × focus avg lap.
  • Session-change reset (SessionUniqueID/SessionNum change or >5 s
    backwards SessionTime jump) clears ALL per-car trackers — June 4
    lesson. Renders only in RACE sessions; hides when the camera car
    leads its class or is out of the world. SessionInfo YAML parsed
    ONCE per session, DriverInfo every 5 s (YAML-cost lesson from the
    session-info overlay). 4 Hz poll. Transparent OBS lower-third;
    H / ?debug=1 debug bg per convention.
  • All four launchers updated per the maintenance rule (17 overlays);
    `obs_loaders/catch.html` written directly (copy of the delta loader
    with port 5015) because the sandbox mount again served STALE
    truncated copies — `make_obs_loaders.py` list updated too, so a
    future re-run regenerates it identically.
  • Verified offline (`test_catchup.py`, stubbed irsdk+flask, fake
    clock): catching 3.0s/+0.5 → 6 laps & 540 s, losing, holding band,
    pit-lap exclusion, multiclass picks same-class ahead (LMP in
    between ignored), class-leader hides, lapped-ahead suppresses
    prediction, practice hides, session-change reset. 22/22 pass.
    NOTE: the stale-mount problem forced running the test from a /tmp
    heredoc copy — the mounted test file showed truncated pages for
    minutes after writing.
  • Also this session: confirmed the local folder is AHEAD of GitHub
    (July 1 Driving Mode files, SIMHUB_PLUGIN_FEASIBILITY.md, youtube/,
    requirements.txt pywebview addition were never pushed; everything
    else matches byte-for-byte). Folder is a ZIP download, NOT a git
    clone — added `push_to_github.sh` (clone → rsync → commit → push,
    same pattern as SimRacing-News) for Thomas to run in Mac Terminal.
    LESSON: GitHub deploy keys are REPO-SPECIFIC — ~/.ssh/id_ed25519
    (SimRacing-News) is REJECTED for this repo ("Permission denied to
    deploy key"). This repo uses its own key ~/.ssh/id_ed25519_iracing;
    the push script generates it on first run and prints/copies the
    public key for GitHub → Settings → Deploy keys (write access!).
  • FOLLOW-UP same day (car livery images, F1-style): both driver
    blocks now show the rendered car via iRacing's LOCAL render server
    (http://127.0.0.1:32034/pk_car.png — the livery-overlay discovery),
    incl. custom Trading Paints TGAs from the on-disk paint cache. The
    fetch logic is a compact SELF-CONTAINED copy of iracing_livery.py's
    (_car_path_variants / find_paint_file / _build_render_params /
    _fetch_iracing_render — importing the livery overlay would execute
    its module-level Flask/poller setup; render_race.py precedent).
    All livery-overlay lessons preserved: %20 not '+' in the query
    (urlencode quote_via=quote), nested-MX-5 separator variants FIRST
    (unknown carPath returns a DEFAULT car — wrong path looks like
    success), requests as SOFT dependency (no requests / render server
    down → banner works without images). New Flask route
    /car/<cidx>.png with bounded in-memory cache keyed on
    (carPath, cust_id, design, paint_path); failures NOT cached and
    the front-end retries every 20 s, so a render server that comes up
    later self-heals; route overrides the global no-store with
    max-age=300. Front-end: .carimg at the outer edge of each driver
    block, hidden until onload fires (onerror keeps it hidden — no
    broken-image icons on stream). test_catchup.py extended with
    render-helper checks: 30/30 pass.

**June 26, 2026 (TWO LINEAGES MERGED — DotD + racecontrol folded into the
GitHub repo; Quali Delta moved 5013→5014):** The project had diverged into
two copies that never met: the GitHub `main` lineage (corner-cue overlay +
this session's Quali Delta, dashboard rewind-doubling / jump-to-round / 20s
replay, flag timer-expiry fix) and a Mac-only lineage (Driver-of-the-Day
overlay, June 22, port 5013 + the iCASControl `racecontrol/` merge, June 25,
port 8080). Nextcloud sync mixed them in the Windows folder — the Mac's
files OVERWROTE the working-tree dashboard/launchers (our work survived only
in the GitHub commits). Recovery: `git checkout -- .` restored the GitHub
(Version A) tracked files while the Mac's UNTRACKED files (DotD trio,
`racecontrol/`, `iracing_racecontrol.py`, `img/`) were preserved, then both
were reconciled into one tree:
  • **Port clash resolved:** DotD and Quali Delta both wanted 5013. DotD
    KEEPS 5013 (it's referenced throughout its own code/docs); **Quali Delta
    moved to 5014** (`PORT`, docstring, `obs_loaders/delta.html`, all four
    launchers, `make_obs_loaders.py`).
  • **DotD wired in** (tag "dotd", 5013) to all launchers + `make_obs_loaders`
    (+ debounced `obs_loaders/dotd.html`). It reads the newest race log (no
    SDK) and nominates a Driver of the Day; deps: flask only.
  • **racecontrol wired in** (tag "racectrl", 8080) to `launch_all.py/.bat`
    and `launch_gui.py`. `iracing_racecontrol.py` is a shim that runs
    `racecontrol/backend/server.py` (FastAPI) in-process. NOT an OBS source
    (interactive steward web app) — intentionally excluded from
    `make_obs_loaders.py`. Deps added to root `requirements.txt`: fastapi,
    uvicorn, websockets. `dotd_history.json` added to `.gitignore` (runtime
    state). 16 overlays now. LESSON: the Mac and Windows copies were NOT both
    git clones of the same remote — the Mac was a downloaded ZIP that got the
    racecontrol merge but never pulled GitHub. Keep BOTH machines as clones
    of `halvar20000/iracing-overlays` and sync via git, not Nextcloud, to
    avoid this divergence again.

**June 25, 2026 (dashboard playback UX + new quali-delta overlay, port
5014 — originally built on 5013, moved 2026-06-26):** Three changes, all from viewer feedback.
  • **Progressive rewind / fast-forward** on the dashboard playback strip.
    The two fixed rewind buttons (-1/-2) and two FF buttons (2×/4×) were
    replaced by ONE `◀◀` and ONE `▶▶` button that DOUBLE on each click:
    rewind 1→2→4→8→16×, FF 2→4→8→16× (cap 16×, `PB_MAX_SPEED`). Pressing
    Play resets to 1×. Front-end only — `/playback` already accepted any
    integer speed. `pbSpeed` (JS) mirrors the poller-reported speed and is
    advanced optimistically so several quick clicks double correctly
    between polls; the active button + the live pill show the current ×.
  • **Jump-to-round** replay control. The poller records the SessionTime
    of every lap boundary the OVERALL LEADER crosses, LIVE only
    (`_track_lap_starts`, gated on `is_live` + race; `_leader_max_lap`
    only increases so scrubbing can't add phantom laps). A dropdown
    (`Start` + each completed round) + Apply seeks the replay to that
    boundary via `replay_search_session_time` and plays 1× (new
    `jump_to_replay_time` / `jump_to_lap` methods, `/replay/jump_lap`
    endpoint, `replay_laps` snapshot field). CRITICAL: lap data is NOT
    cleared on backward SessionTime jumps (scrubbing the replay), only on
    a real session change — otherwise jumping back would wipe the list.
    Only knows laps observed while running (lap-start times captured
    live); "Start" = earliest recorded boundary.
  • **NEW overlay `iracing_qualidelta.py` (tag "delta", port 5013):**
    qualifying live delta with TWO auto-switching modes. Big centre-zero
    delta to the SESSION best, green ahead / red behind, bar fills toward
    the side you're gaining, plus per-sector chips.
      – DRIVING (IsOnTrack): iRacing's own predictive delta
        (`LapDeltaToSessionBestLap` + `_OK`) for your car; sectors from
        `SplitTimeInfo.Sectors[].SectorStartPct` (fallback 3 equal) vs
        your OWN best-lap sectors (green faster / red slower / purple
        new personal-best). State machine ARMS only after a clean S/F
        crossing; an off-track/pit lap can't become the reference.
      – SPECTATOR (not in car — the broadcaster case): iRacing exposes
        NO ready-made delta for other cars, so we COMPUTE one. Per-car
        `CarIdxLapDistPct` is sampled vs `SessionTime` into a (pct→
        elapsed) buffer; each car's last CLEAN full lap is stashed. The
        POLE is taken from iRacing's OFFICIAL `CarIdxBestLapTime` (valid
        laps only — a fast-but-DELETED track-limits lap never wins; this
        was a real bug — the self-measured "fastest lap" promoted an
        invalidated lap and showed a pole ~0.3 s too fast). The pole
        car's matching clean-lap buffer becomes the reference curve,
        SCALED so its total equals the official best exactly. The
        on-camera car's (`CamCarIdx`) live elapsed at its current pct is
        interpolated against that curve → delta; follows the camera
        automatically, works for ANY driver (leader or not). Sector chips
        vs the pole lap's sectors. ~15 Hz sampling (vs iRacing's internal
        60 Hz) so it's broadcast-usable but slightly less precise than
        the driving delta. Shows "Building reference lap…" until a pole
        lap exists.
    Transparent OBS source, `H` / `?debug=1` debug bg, 15 Hz poll.
    Verified offline (stubbed irsdk): DRIVING — a synthetic 3-sector
    flying lap sets the reference, faster→best / slower→red chips, the
    invalid-lap guard holds; SPECTATOR — fastest car promoted to pole,
    a slower on-camera car reads correctly behind, ahead-case goes
    negative, sector chips compute vs pole. All four launchers +
    `make_obs_loaders.py` (+ `obs_loaders/delta.html`) updated per the
    maintenance rule (14 overlays now). NOTE: the cowork sandbox mount
    again served STALE/truncated copies of edited files this session —
    `iracing_dashboard.py` couldn't be byte-compiled in the sandbox
    (verified via direct file reads + a JS dry-run instead); the brand-new
    `iracing_qualidelta.py` DID sync fresh and compiled + unit-tested
    cleanly.

**June 11, 2026 (corner-cue overlay — driving-line substitute, port
5012):** New `iracing_drivingline.py` (tag "line") + companion
`driving_line_window.py`. Purpose: usable corner cues in sessions
where iRacing disables the racing-line aid (D class and up — e.g. GT3
Sprint). The true 3D in-world line is IMPOSSIBLE externally (the SDK
exposes no camera/view matrix) — this is the practical substitute.
  • Geometry-only analysis of the bundled `tracks/<TrackName>.json`
    loops (no recorded laps needed, works for every bundled track):
    project lat/lon to meters, resample at 4 m, signed curvature
    (y-south coords → POSITIVE heading change = RIGHT turn), smooth,
    threshold |k|>1/200, merge same-sign runs <25 m apart (sign flips
    split runs → chicanes come out as separate L/R corners), discard
    bends <12°. Per corner: entry/apex/exit pct, dir, min radius,
    total turn angle, severity (HAIRPIN<35 m / TIGHT<80 / MEDIUM<150 /
    FAST<280 m radius), estimated apex speed v=sqrt(12·r) capped at
    270 km/h (car-agnostic ESTIMATE). Distances rescaled to the
    official WeekendInfo.TrackLength.
  • Poller 10 Hz; player car via DriverInfo.DriverCarIdx with
    CamCarIdx fallback when spectating. `/data` serves the cue
    (in_corner + next two corners with live distance), `/corners`
    dumps the full analysis for tuning. Overlay page (`/`) shows
    arrow + T-number + severity + ~speed + countdown bar from 500 m;
    H toggles debug bg per convention.
  • `driving_line_window.py` — on-top-of-the-sim client (user's
    display choice): stdlib Tkinter, polls `/data`, transparent
    colorkey window, topmost, click-through via pywin32
    WS_EX_LAYERED|WS_EX_TRANSPARENT (degrades gracefully without
    pywin32). iRacing must run BORDERLESS WINDOWED. `--debug` =
    visible bg + draggable, prints geometry to transfer into
    WIN_X/WIN_Y constants. NOT in the launchers (desktop window, not
    a Flask overlay) — run manually when driving.
  • Future enrichment hook: `cues/<track_file>.json` with per-corner
    overrides keyed by corner number ({"5": {"gear":2, "brake_m":120,
    "speed_kmh":78, "name":"Hairpin"}}) merges onto the geometry
    corners — a recorded-reference-lap tool can generate these later.
  • Verified offline (`test_drivingline.py`, stubbed irsdk, real
    track JSONs): Okayama 12 corners CW, first corner R, hairpin
    found; Monza 10 incl. Rettifilo as R/L pair 0 m apart; Laguna
    Seca 10 CCW incl. Corkscrew L/R; cue countdown monotonic, S/F
    wrap, corner spanning S/F, override merge. 22/22 pass. NOTE:
    severity is radius-based, so tight chicane elements label as
    HAIRPIN — cosmetic.
  • All four launchers updated per the maintenance rule (port 5012,
    13 overlays). Fairness note: external corner-cue apps are common
    (RaceLab etc.), but for league racing check the stewards.

FOLLOW-UP same day (Watkins Glen Cup built by cut-and-splice; ?debug=1):
First live use hit "track not bundled: watkinsglen_cupcircuit" — only
the fullcourse (boot) layout existed in tracks/. NEW METHOD for alt
configs of already-bundled facilities (no OSM, no browser): cut the
boot out of `watkinsglen_2021_fullcourse.json` and splice the
short-course chute. Cut pair found numerically (target = official
2.45 mi vs full 3.4 mi, chord <500 m); entry junction at the carousel
exit needs the extra ~28° distributed as a TANGENT 250 m ARC (radius
chosen below the detector's 1/200 threshold) — a straight chord makes
the carousel read r≈35-58 HAIRPIN/TIGHT (phantom kink), and a Bézier
fillet reaching back into the carousel is WORSE (control-polygon angle
includes the carousel's own curvature). Wide-radius tangent arc +
straight + tangent rejoin gives carousel r=114.9/137.2° — identical to
the full course. Final: 3924 m vs 3943 official, 8 corners all
matching reality (90, esses, inner-loop R-L-R, carousel, off-camber L,
final R). Preview in _previews/, MISSING_TRACKS.md updated.
SAME DAY: `driving_line_window.py` rewritten to PORTRAIT (user
request — he drives without OBS; the on-top window IS his display):
170×520, vertical countdown line with 100 m tick marks, fill runs
top→down toward a direction-colored corner marker at the bottom,
white car-position chevron on the fill edge, arrow/T-number/severity/
~speed/distance stacked above, "then T#" preview below. Default
position: right screen edge, vertically centered (WIN_X/WIN_Y None);
--debug = draggable + prints WIN_X/WIN_Y. Layout verified offline by
replaying draw_cue() onto a PIL fake canvas (tkinter not installable
in the sandbox). NOTE: the cowork sandbox mount served PERMANENTLY
STALE copies of edited files this session (truncated mid-write,
never refreshed) — verify with the direct file tools, test sandbox
copies via heredoc, and double-check the synced folder afterwards. Overlay
fix in the same session: failed track loads are no longer cached, so
a JSON dropped into tracks/ mid-session is picked up without restart
(success results still cache). Also added `?debug=1` URL param (debug
bg without keyboard focus) and the debug status line now shows "next
T# in #m" while the cue is hidden beyond 500 m.

**June 10, 2026 (trackmap — 2026 S3 new tracks; iRacing-SVG method):**
Added the 2026 Season 3 content: **Qualcomm Circuit (Naval Base
Coronado)** (`coronado.json` + `qualcomm`/`qualcommcircuit` slug
variants — delete the losers when the console prints the wanted name)
and **Laguna Seca 2026 rescan** (`lagunaseca_2026[_full]`/
`lagunaseca2026` copies of the SRA file — same physical circuit).
Coronado is NOT in OSM (temporary street circuit, real-world debut
21.06.); the OSM street-graph stitch came out ~25 % too long because
the course cuts corners off the base road network. NEW BEST METHOD
discovered instead: **iRacing's own track-map SVG assets** — from a
logged-in members-ng tab fetch `/bff/pub/proxy/data/track/assets`
(bare `/data/...` rejects browser cookies; ONLY the `/bff/pub/proxy/`
path authenticates from the web app — this also re-enables live car/
track catalog pulls). Download `active.svg` (one filled ribbon = TWO
closed subpaths, outer+inner edge; sample ONE edge via
getPointAtLength — naive whole-path sampling concatenates both and
corrupts the loop), `start-finish.svg` (rect = S/F line, polygon =
direction arrow; trust the arrow — turn-label snapping gave a
contradictory order), rotate loop to the S/F vertex, match direction
to the arrow, RDP-simplify, rescale to the official length (3.400 mi
→ 5466 m projected, 0.1 % off). `pitroad.svg` is a dashed asset that
doesn't reconstruct into an ordered polyline → shipped without pit
(lagunaseca precedent). Preview `_previews/coronado.png` matches the
official course map (16 turns, CCW, Ellyson S/F kink, Carrier Corner).
tracks/ was tar-snapshotted to session outputs before writing (per the
June 5 lesson). NOTICE.txt + MISSING_TRACKS.md updated.

**June 5, 2026 (trackmap — BULK OSM BUILD, 36 facilities via 8 parallel
agents; Nextcloud incident):** Built ALL remaining buildable tracks from
MISSING_TRACKS.md in one evening: 19 road facilities (incl. alt configs
Donington National, Oulton Island, Motegi East, The Bend GT) + 21 ovals
— 99 new JSONs (2-3 slug-variant copies each, slugs UNCONFIRMED — the
trackmap console prints the wanted filename on first load; delete the
losing variants), plus GPX + previews in tracks/_previews/. Adelaide
and Chicago Street ARE buildable (type=circuit relations — the
disused-raceway/relation trick is 3-for-3 on street circuits). NOT
buildable: Fontana + Motegi oval (demolished, deleted from OSM),
Sonoma Cup chute, several alt-config link roads. Notable: Road
Atlanta's raw OSM order was reversed; Portland is CLOCKWISE; Laguna's
OSM pit way is mis-tagged (JSON ships without pit); Bristol S/F is a
west-straight guess (flip to east mid-straight if wrong on stream);
Indy S/F sits exactly on the OSM Yard-of-Bricks way; rockingham.json
is the NC oval (provisional — UK track may claim the bare slug).
Method per track: Overpass via Claude-in-Chrome (assigned mirrors,
in-page retry/backoff — kumi.systems stalled, overpass-api.de
rate-limited under 8 concurrent agents), RDP-simplify in-page, chunked
transfer (≤900 chars, NO '=' in returned strings — proxy blocks them),
parametrized outputs/build_track.py (loop close, length check,
direction via shoelace+pit-flow, pit-mid S/F, slug fan-out, preview).
INCIDENT during the run: everything in tracks/ predating the bulk
write + 3 root files (iracing_championship.py, iracing_session_info.py,
launch_all.bat) vanished from the LOCAL folder (~210 files); agents
were NOT the cause (sandbox blocks deletes — multiple agents hit
EPERM on their own temp files). Deletions synced to the server; all
files restored from Nextcloud web trash ("Deleted files"), verified
complete (316 track JSONs). Root cause unconfirmed — suspect Nextcloud
FileProvider misbehaving under a ~150-file write burst. LESSON: before
any future bulk write into the synced folder, snapshot tracks/ to a
tar in outputs first (done this time, kept), and prefer staging bulk
output outside the sync root.

**June 5, 2026 (trackmap — St. Petersburg added from OSM):** The 06-04
"NOT OSM-buildable" classification for **stpete** was WRONG — the circuit
IS in OSM (relation 8668325, "Grand Prix of Saint Petersburg Raceway"),
but its ways are tagged `disused:highway=raceway` (temporary circuit),
which the raceway-tag search missed. Lesson for future street circuits:
also query `disused:highway=raceway` and `type=circuit` relations.
Built `tracks/stpete.json` + `tracks/stpete.gpx` via Claude-in-Chrome
Overpass fetch (sandbox web_fetch still returns empty for OSM APIs).
Loop 2903 m vs real 2897 m; pit lane (772 m, incl. "Dali Boulevard"
stub) bundled in onpitroad. S/F = pit-midpoint perpendicular projection
(38 m offset — pit sits on the parallel taxiway). CRITICAL: the
relation's "forward" roles are REVERSED vs the real driving direction —
verified against the official track map (T10 Dali → Bay Shore Dr → Dan
Wheldon Dr → T12/13/14 → runway ENE → S/F → T1 at the bay) and the pit
flow; the loop was reversed before rotation. Rendered SVG visually
matches the official racingcircuits.info map. Don't blindly trust OSM
forward/oneway roles on circuit relations — cross-check pit-lane flow.
MISSING_TRACKS.md updated (stpete moved out of the not-buildable list).

**June 4, 2026 (dashboard — session-change reset; PCCD Hockenheim
forensics):** Race 2 of the evening detected NOTHING (two real spins
missed) and its log re-imported race 1's stale incidents. Root cause:
the two races were SEPARATE HOSTED SESSIONS — SessionTime reset to ~0
between them, but the dashboard poller had NO session-change reset
(same bug class the flag overlay had in April): every cooldown /
kinematics timestamp still held race-1 values (~3000 s), all
"t_now - last" checks went negative, cooldowns never expired →
detectors muted; the feeds kept the old entries (which the race
logger then re-imported into the new file with identical timestamps —
that's the tell in the logs). Fix: `_reset_detection_state()` clears
ALL per-car trackers, cooldowns, kinematics histories, pending queues
and BOTH feeds; triggered on (SessionUniqueID, SessionNum) change or
a >5 s backwards SessionTime jump. Verified offline: back-to-back
sessions with time reset — race-2 spin detected, no stale entries.
Tonight's flag-overlay complaint was NOT a code bug: replaying race
2's real timeline (green 37, expiry 1537, crossings from the log)
through the CURRENT flag overlay gives white at 1526.5 / checkered at
1626.0 — exactly right. The running process was simply never
restarted after the afternoon fix (behavior matched the morning "+1
lap" version: white at the real finish, checkered at the cool-down
crossing). LESSON: after code changes, overlays MUST be restarted —
they keep the old version in memory.

**June 4, 2026 (trackmap — gap analysis + 3 more OSM tracks +
Zandvoort 2023 slugs):** Generated `tracks/MISSING_TRACKS.md` (bundled
205 SRA tracks vs iRacing's ~140 facilities / 400+ configs, with
buildability classification). KEY INSIGHT: the race logs contain the
EXACT TrackName slug for every raced track (session_start →
`track_name`) — that solved the filename problem for: **thruxton**
(3771 m vs 3790), **brandshatch_grandprix** (3900 m vs 3908 — OSM maps
Brands as named corner segments; new endpoint-stitcher joins them, and
removing the "McLaren" link way yields the GP loop instead of Indy),
**miami_gp** (5416 m vs 5412 — alternate-layout way excluded by
trying removals until the closed loop matches the real length), and
**zandvoort_2023_*** (5 renamed copies of the old-scan files — the
2023 rescan changed the TrackName, races were asking for files that
didn't exist). Driving direction verified against OSM oneway tags
(stitching can reverse segments). S/F = pit-midpoint perpendicular
projection (14-16 m from pit-mid on all three). NOT OSM-buildable
(hand-draw in gpx.studio): stpete (slug confirmed), adelaide (slug
confirmed — parklands circuit is only a stub in OSM), Chicago Street,
fictional tracks, Oran Park (demolished). Brands Indy geometry is
cached in the build session but unsaved (slug unconfirmed). The OSM
extraction MUST go through Claude-in-Chrome — overpass/nominatim/OSM
APIs return empty through the sandbox web_fetch.

**June 4, 2026 (livery — MX-5 fixed, nested CarPath):** The livery
overlay failed ONLY for the Mazda MX-5: it is the one iRacing car
with a NESTED paint folder — DriverInfo reports CarPath
"mx5 mx52016" but the on-disk paints live at `paint\mx5\mx52016\`
(the space is really a path separator), so the flat-folder TGA lookup
never matched. New `_car_path_variants()` in `iracing_livery.py`
tries the raw CarPath first (every other car), then space→"/" and
backslash variants — used by `find_paint_file`, the
`/pk_car.png` render fetch (retries carPath variants until one
returns an image), and the debug fields. Verified offline with a
simulated paint cache (5/5). If another nested car ever appears, the
same variants cover it automatically.

FOLLOW-UP same day (wrong car shown — Skippy/Ray FF1600 instead of
MX-5): the render server returns a DEFAULT car image for an unknown
carPath, so a wrong-path request is INDISTINGUISHABLE from success in
code — the variant-retry loop happily accepted the first (raw spaced)
form. Changes: (a) for nested paths the separator forms now go FIRST
(backslash, then slash, then raw — matching the on-disk layout);
(b) `carId=<DriverInfo.CarID>` is sent as an extra hint (harmless if
ignored); (c) NEW debug page **http://localhost:5006/carview_test** —
renders the on-camera car once per carPath variant with images served
straight from the render server, so the correct form can be IDENTIFIED
BY LOOKING during the next MX-5 session. If the backslash-first guess
is still wrong, open that page on an MX-5 and lock in whichever
variant shows the right car.

**June 4, 2026 (dashboard — never show Scenic, TV1 fallback):** After
a replay finished, the auto-return sometimes landed on iRacing's
Scenic view (user rule: Scenic must NEVER appear on stream). Two
causes: (a) when the previously-watched car couldn't be resolved, the
return made NO camera switch at all and iRacing fell back to Scenic
on its own; (b) camera switches reused `_current_cam_group` blindly.
Fix: new `TelemetryPoller._safe_cam_group()` — returns the current
group unless it's Scenic, else TV1 (exact then fuzzy match, handles
"TV 1"), else the first non-Scenic group. ALL automatic camera paths
go through it now: replay start, replay auto-return (which also
always makes an explicit switch — to the previous car or, if unknown,
the live leader), auto-follow, focus-leader, focus-crashes, and the
cam-disconnect watchdog. Explicit user clicks on a camera-group
button still use the group as-is. Startup default remains TV3 (from
April); change `_apply_default_camera` if TV1 should be the global
default too. Unit-checked the group picker offline (5/5).

**June 4, 2026 (trackmap — Okayama added from OpenStreetMap):** PCCD
round 8 (18.06.) runs Okayama Full Course; not in `tracks/` and not in
SIMRacingApps upstream (their Tracks folder last updated Sept 2024 —
don't bother re-checking it for new tracks). New workflow used instead
of the Monza hand-drawing path: extracted the circuit centreline (OSM
way 177330893, closed loop, oneway in DRIVING direction) and pit lane
(way 267173063) via the Overpass API **through Claude-in-Chrome**
(overpass-api.de / nominatim / api.openstreetmap.org all return empty
bodies through the sandbox web_fetch — use the browser's fetch in page
context instead). Built `tracks/okayama_full.json`: rotated the loop
to start at the S/F line (interpolated vertex mid-straight at lat
34.9142, ~60 % up the pit straight — the overlay places car dots by
arc-length from the polyline's FIRST point, finish_line in the JSON is
ignored), kept OSM's point order (matches clockwise driving
direction). Loop length sanity check: 3704 m vs the real 3703 m.
Rendered SVG visually confirmed against the real layout. NOTICE.txt
got an OpenStreetMap/ODbL attribution section. NOTE: filename assumes
iRacing reports TrackName "okayama full" — if the trackmap console
logs a different filename on 18.06., rename the JSON to match. If the
S/F dot position looks offset on stream, adjust the interpolated
vertex (sf_lat in the loop-rotation step).

FOLLOW-UP same day: **Phillip Island added the same way** (OSM way
43598473 + pit entry/lane/Supercars-exit ways stitched). Loop 4459 m
vs real 4448 m. S/F placement improved over the Okayama approach: the
S/F vertex is now the perpendicular foot of the PIT-LANE MIDPOINT
projected onto the loop (landed 11 m from the pit wall — no manual
lat guessing). Saved as BOTH `phillipisland.json` and
`phillipisland_2019.json` since iRacing's exact TrackName for the
2019 rescan is unconfirmed — delete whichever one the trackmap
console doesn't ask for. The OSM workflow is now the standard path
for missing tracks (gpx.studio hand-drawing is the fallback for
circuits poorly mapped in OSM).

**June 4, 2026 (session-info overlay wired in, port 5010→5011):** The
"remaining session time" overlay the user asked for already existed —
`iracing_session_info.py` from a parallel session (30.04.), showing
session name + total / remaining time. LATER SAME DAY: lap-based
races now show "Total: N laps / Remaining: M laps" instead of times —
but ONLY when the session is genuinely lap-limited (lap count set AND
no finite time cap). League "100 laps OR 25 min" configs keep the
time view (the lap cap is never reached there). Note: iRacing's
"unlimited" sentinel for SessionLapsRemain is 32767, NOT ~1e7 like
the time fields — the guard is `> 9000`. It was unusable because it
hardcoded port 5010, which the championship overlay now owns. Fixed:
moved to **port 5011** (docstring, banner, app.run), added tag "sess"
to all launchers (`launch_all.bat`, `launch_all.py`, `launch_gui.py`)
per the maintenance rule, and added the scripts-table row. OBS source:
http://localhost:5011 — transparent card, orange session name, amber
remaining-time row. Also deleted the three Nextcloud "conflicted copy"
launcher files from 30.04. — both KNOWN ISSUEs from the earlier
June 4 session are now resolved.

**June 4, 2026 (dashboard — iOverlay-class incident detection, speed
collapse):** User compared the dashboard's incident feed against
iOverlay Race Control and found it lacking: missed spins, named the
wrong driver, and (when driving) spammed off-tracks. Requirements:
detect collisions + spins (incl. RECOVERED spins where the driver
continues), keep off-tracks visible but quiet, low noise overall.
Spectator/broadcast mode is the priority. Changes in
`iracing_dashboard.py`:
  • NEW PRIMARY (spec-mode) — **speed-collapse detector**: per-car
    speed derived from `CarIdxLapDistPct` × track length at 10 Hz
    (`_update_speed`, history deques, S/F-wrap + teleport + data-gap
    safe). Fires when speed collapses from ≥90 km/h to ≤32 km/h losing
    ≥65 km/h within a 2.5 s window (tuning constants `SC_*` at module
    top). The 32 km/h floor sits below every legitimate corner-apex
    speed, so braking for hairpins / pit entry can NOT fire it.
    Catches recovered spins, names the right car, works for every car
    without driving. Classification via `_is_collision_for`: another
    car currently within 0.45 % of track distance OR a second collapse
    within 1.5 % in the last 3 s → collision, else lost_control.
  • REMOVED — the yaw-rate spin detector read `CarIdxYawRate`, which
    DOES NOT EXIST in the iRacing SDK (only the local car's `YawRate`
    is broadcast). The array was always empty; the detector never
    fired once. Don't reintroduce it.
  • Yellow-zone detector: culprit scoring now weights a recent speed
    collapse (+5) above off-track (+3) / vanished (+2) / stopped-ticks
    — the old picker often blamed a random passing car because the
    actual culprit was still rolling when the yellow appeared. If the
    culprit was already reported via speed-collapse in the last 12 s,
    the yellow-zone emission is SKIPPED (collapse beats the flag by
    1-2 s; this killed the duplicate-entry noise). Classification uses
    `_is_collision_for` (the old `_car_nearby` @ 1.2 % labelled solo
    spins with traffic passing as collisions; it's now unused but kept).
  • NEW — quiet **off_track** entries (spec mode): `CarIdxTrackSurface`
    == 0 sustained ≥4 polls (0.4 s — kerb hops don't reach this) while
    still carrying ≥43 km/h (a car LOSING speed off-track is reported
    by the collapse detector as spin/contact instead). 30 s per-car
    cooldown. UI renders off_track muted (dim border, 0.62 opacity).
    Off-tracks never auto-replay / camera-snap (`_auto_replay_types`
    unchanged). The driving-mode 1x emission stays (also muted).
Verified offline (`test_incident_detection.py`, stubbed irsdk): solo
recovered spin → 1× lost_control; two-car collapse → collision for
both; 47 km/h-hairpin braking → nothing; pit entry → nothing; 0.2 s
kerb hop → nothing; 1 s off at speed → 1× quiet off_track; yellow
2 s after collapse → no duplicate, right culprit. 11/11 pass.

FOLLOW-UP same day (dashboard driver list lagging behind track
position): the operator dashboard's driver list still sorted by
`CarIdxClassPosition`, which iRacing only updates at S/F crossings —
mid-lap overtakes took up to a lap to appear while the standings
overlay (fixed 23.04. with live-progress sorting) was already correct.
`_build_driver_list()` now sorts RACE sessions by live track progress
(`CarIdxLap + CarIdxLapDistPct`, in-world cars first, towed/garage
sink to bottom) and renumbers `position` 1..N live; the stale iRacing
value is kept as `iracing_pos`. Practice/quali keep iRacing's
best-lap ordering (track progress is meaningless there). Verified
offline: mid-lap overtake reorders immediately, out-of-world car
sinks, quali order untouched. 3/3 pass.

FOLLOW-UP same day (Miami stream feedback): "stopped on track" fired
for every car in very slow corners. Root cause: the static test was a
per-poll pct-delta (< 0.0003/poll) — track-length dependent, and the
"12 polls ≈ 3 s" comment assumed 250 ms ticks while the poller runs at
10 Hz, so it tripped after only 1.2 s. Fixed: "stopped" now means REAL
speed (kinematics) < `STOP_MAX_MPS` (1.4 m/s ≈ 5 km/h — below any
driveable corner) held for `STOP_MIN_TICKS` (30 = 3 s). New test
scenarios: staggered 40 km/h slow-corner crawl → nothing; gentle
coast-to-stall → 1× stopped on track (a FAST stop is caught earlier by
the speed-collapse detector and labelled spin — that's correct, only
one report either way). 13/13 pass.

FOLLOW-UP same day (connection-flaky driver spammed the feed): a
driver with network problems blinked out of the world and back
repeatedly; every blink was reported (the telemetry FREEZE before each
blink-out looks like a speed collapse → "spin"; the blink-out itself
fired the vanish detector → "crashed"). Three-layer fix in
`iracing_dashboard.py`:
  1. **Frozen-telemetry guard** in the speed-collapse detector: a car
     about to blink out freezes — its last lap-pct samples are
     bit-identical (an impossible instant stop). A real crashing car
     still translates while decelerating. Frozen → skip, log
     `[speed-collapse-skip]`.
  2. **Vanish confirmation** (`VANISH_CONFIRM_S` = 10 s): vanish
     reports are queued in `_pending_vanish` and only emitted if the
     car STAYS out of the world (a real tow does); a return cancels
     the report. Processed OUTSIDE the per-car loop (vanished cars can
     be skipped by its early-continue guards).
  3. **Unstable window** (`UNSTABLE_WINDOW_S` = 90 s): any car that
     returns from out-of-world is connection-unstable — speed-collapse
     / stopped / lap-regression (reconnects jump backwards!) /
     off-track / vanish all ignore it, the yellow-zone culprit picker
     never blames it. Window refreshes on every new blink.
Verified offline (`test_blink.py`): 3× freeze-blink-return cycle → 0
incidents; real crash + tow 8 s later → exactly 1 (the spin);
instant-freeze permanent disconnect → 1 collision after the confirm
window (unavoidable — indistinguishable from a crash without more
signals). All 13 prior incident scenarios still pass.

FOLLOW-UP same day (replayed marker): incident feed entries now carry
`replayed: False` and get flagged via
`TelemetryPoller.mark_incident_replayed(id)` whenever a replay of them
was shown — by auto-replay (incident id passed through
`_try_auto_replay`), the feed's "Replay 10s" button (`/replay_5s` with
incident_id), or the Stream Deck replay_last* endpoints. UI: green
"▶ replayed" chip next to the type label, button text flips to
"Replay again"; the render signature includes the flag so the chip
appears on the next poll. Lifecycle unit-checked offline (4/4).

FOLLOW-UP same day (overtake detection + priority auto-replay):
NEW "Overtakes" feed (green accent, same card UI incl. replayed-chip,
own Clear-all + /overtakes/clear). LAYOUT (user request): four columns
— standings (480px) | cameras (squeezed to 280px, buttons flex-wrap) |
overtakes (1fr) | incidents (1fr). The old `.sub-panel` CSS is unused
but kept.
Detection in `_update_overtakes` (race sessions only): live progress
rank (lap+pct) swaps the instant a pass happens, and LAPPING NEVER
CHANGES IT (leader already a lap ahead) — so only real position swaps
register. A swap only counts when: cars within 0.6 % at the swap, both
on track (no pit-cycle), overtaken car still at racing speed (≥43 km/h,
re-checked at confirm time because the 0.5 s speed window lags right
after a spin — plus a _collapse_at check), no blue flag on the
overtaken car (user rule), neither car connection-unstable, and the
new order HOLDS for 3 s (side-by-side flicker) with a pair-churn guard
so a failed move + defender re-pass doesn't count either way.
Auto-replay: SEPARATE toggle from incidents (user request) —
`auto_replay_overtakes` flag, own button in the Overtakes sub-panel,
`/auto_replay_overtakes` endpoint, Stream Deck
`/streamdeck/toggle_auto_replay_overtakes`. When enabled, overtakes
run at PRIORITY 2 vs incidents' 1 (`REPLAY_PRIORITY`) — they may jump
the post-replay cooldown of a lower-priority event but never
interrupt an active replay (is_live check). Buildup 8 s before the
start of the move.
Verified offline (`test_overtakes.py`): clean pass → 1 entry;
flicker → 0; pit cycle → 0; blue flag → 0; passing a spun car → 0.
6/6, plus all 13 incident + 3 blink scenarios still pass.

**June 4, 2026 (flag overlay — timed-race white flag fixed, SessionFlags
primary):** White flag wasn't showing in timed league races (PCCD
Silverstone 21.05., both races). Race-log forensics (`logs/*.jsonl` flag
events) found TWO kill switches in `flag_overlay.py`:
  1. League sessions set BOTH `SessionLaps` (e.g. a 100-lap cap) AND
     `SessionTime` (the real 25-min limit). `_get_total_laps()` returned
     100, the exclusive `if total_laps: lap-based else: timed` branch
     waited for lap 100 forever — the timed logic never ran.
  2. The timed branch's late-join detection fired whenever
     `SessionState >= 5`, but iRacing flips that at TIMER EXPIRY
     (mid-lap), so every normal timed race got its white flag silently
     swallowed and checkered fired a lap early.
Rewrite: all detectors now run in PARALLEL, first one wins:
  • PRIMARY — iRacing's own `SessionFlags` white (0x0002) / checkered
    (0x0001) bits. Log analysis proved the white bit fires exactly when
    the leader starts the final lap (Spa 27.05., Thruxton 02.06.,
    Magny-Cours 26.05.) — works for lap AND timed races, any ending
    rule. BUT: both PCCD Silverstone races broadcast NO bits at all
    (league session-ending config?), so fallbacks remain mandatory.
  • Fallback lap: `cur_lap == total_laps` at a crossing (real lap races;
    harmless under a 100-lap cap — never reached).
  • Fallback timed — CORRECTED SAME EVENING (Miami 40-min race, no
    white shown): iRacing's REAL rule is white at the LAST crossing
    BEFORE the clock expires (`time_rem <= lap_estimate`, median of
    the leader's recent laps → EstLapTime → 120 s), checkered at the
    NEXT crossing (first one past expiry, `time_rem <= 0.5` required
    for timed sessions). The morning version used "+1 lap after
    expiry" — one lap LATE, white fired at the real finish, nothing
    visible. Re-analysis of ALL logs (Spa/Thruxton/Magny bit
    timestamps + Silverstone/Miami end sequences) confirms every
    logged league uses the same rule; the "extra lap" seen at
    Silverstone was a cool-down crossing, not racing. Median (not
    mean) lap estimate so one pit/incident lap can't fire white a
    lap early; even if it did, the checkered still waits for expiry
    — white just flies longer.
  • Checkered: checkered bit, OR next crossing ≥15 s after white
    (MIN_FINAL_LAP_S guard — bit and crossing arrive within a tick, an
    unguarded crossed_sf killed white instantly), OR lap counter past
    total, OR safety net (state checkered AND 1.5×avg_lap since white —
    anchored to the WHITE moment, not timer expiry, because the final
    lap can END two laps after the clock hits zero).
  • Late-join skip now gated to the first ~5 s of observing a session
    (`_ticks_in_session < 50`) so it can't hijack mid-race.

FOLLOW-UP same evening (Le Mans IEC: white far too long, checkered
late): TWO fixes. (a) `_find_leader` picked the first car with
CarIdxClassPosition == 1 — WRONG in MULTICLASS races (every class has
a class-P1; whichever is first in the Drivers list won), so the
overlay tracked a class leader's crossings instead of the overall
leader's. Now: overall leader by live progress (CarIdxLap +
CarIdxLapDistPct), class-position fallback only when lap data is
missing. (b) Checkered-via-crossing dropped the `time_rem <= 0.5`
requirement — once white is out, the leader's NEXT crossing IS the
finish (user rule: checkered the moment the leader crosses). To keep
an early white from dragging the finish forward, the white estimate
is now `min(median, most recent lap)` (caution-inflated medians can't
fire a lap early). New multiclass test scenario; 15/15 pass. NOTE:
at Le Mans a correct white still flies ~5 min — that's one full lap
there, inherent to the rule.
Verified offline by replaying log-derived scenarios (PCCD no-bits,
Spa-with-bits, pure lap race, late join) through the state machine with
a stubbed irsdk — 13/13 checks pass. Startup banner no longer claims
"lap-based races only". Also this session: created
`championship_config.json` pinned to CAS PCCD 4th season for the
2026-06-04 Hockenheim stream. KNOWN ISSUE: `iracing_session_info.py`
(from a parallel session, not in launchers) hardcodes port 5010 which
collides with the championship overlay — needs its own port + launcher
entries; three Nextcloud "conflicted copy" launcher files from 30.04.
should be cleaned up.

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

**May 28, 2026 (championship overlay — wired to CLS league-manager):**
Added `iracing_championship.py` (port 5010, tag "champ") — F1-broadcast
style live championship overlay. Two views, hotkey `V` to toggle:
  • **View A — Race + championship delta:** live race standings table
    (sorted by track progress, in-pit/DNF demoted), with a ▲N / ▼N / =
    chip per row showing how many championship positions the driver
    would gain/lose if the race ended right now.
  • **View B — Championship projection:** the pre-race championship
    table reordered live; each row shows projected post-race points =
    pre-race points + race-position points (from `pointsTable`), with
    a `Race P# → +N` sub-line. Pro/Am seasons project class-relative
    points using `classPointsTable` and class position within the live
    race; non-Pro/Am seasons mirror the overall projection.

Stream-mode toggle on `H` (transparent BG default for OBS, dark panel
for layout debugging). Two pages on the same Flask app:
  • `/` — config picker (league + season dropdowns, populated from
    `/api/leagues`; persists to `championship_config.json`).
  • `/overlay` — the OBS browser source URL. 540 px wide, transparent.

Driver matching is strict by iRacing customer ID: `UserID` from the
SDK's `DriverInfo.Drivers[]` joins to `User.iracingMemberId` from the
league-manager. Drivers not registered for the season show as
"unranked" in view A and are absent from view B (they don't influence
the projection). Drivers in the championship but not in this race
keep their pre-race points unchanged for the projection.

Data source is a new public API on the deployed league-manager (Next.js
on Vercel, `league.simracing-hub.com`):
  • `GET /api/overlay/standings?league=<slug>&season=<id?>` — returns
    league/season metadata, the `ScoringSystem` points tables (overall
    + class), all standings rows (rank, name, country, team, car
    class, Pro/Am, points, incidents, iRating) plus the linked
    `iracingMemberId` per row. Reuses `computeDriverStandings()` so
    the overlay always matches what `/leagues/.../standings` shows.
  • `GET /api/overlay/leagues` — list of leagues with their currently
    runnable (ACTIVE / OPEN_REGISTRATION) seasons; used by the config
    picker dropdowns.
Both endpoints are public, CORS-open (`Access-Control-Allow-Origin: *`),
edge-cached briefly. They live in `src/app/api/overlay/{standings,leagues}/`
in the league-manager repo (`halvar20000/simracing-hub-league-manager`)
and deploy automatically on push to `main` via Vercel.

Implementation notes:
  • The overlay has two daemon threads: a `RacePoller` (subclass of
    `SDKPoller`, 1 Hz iRacing snapshots — same pattern as the other
    overlays) and a `ChampionshipFetcher` that re-pulls `/standings`
    every `refresh_seconds` (default 60). The Flask `/api/state`
    handler joins them via `build_projection()`.
  • Live race ordering uses `CarIdxLap + CarIdxLapDistPct` (same fix
    as the standings overlay) so mid-lap overtakes appear immediately,
    not at the next S/F line. Out-of-world cars sink to the bottom.
  • Pro/Am class position is computed by walking the live order and
    assigning sequential class positions to championship-registered
    drivers only — so non-registered drivers in the same race don't
    shift Pro/Am point projections.
  • Projected championship rank is computed by sorting a copy of the
    rows by `(-proj_points, current_rank)` and assigning new positions;
    `delta = current_rank - proj_rank` (+ve = gaining, -ve = losing).

Maintenance: all four launchers (`launch_all.bat`, `launch_all.py`,
`launch_gui.py`, `launch_gui.bat`) updated per the maintenance rule —
`launch_gui.bat` doesn't list scripts so nothing changed there.
Scripts table at the top of this file updated with the new row.

To deploy the API side, run:
  `bash ~/Library/CloudStorage/Nextcloud-admin@cloud․smarthomeworld68․fr/AI/league-manager/outputs/deploy_overlay_api.sh`
(typechecks, commits, pushes; Vercel rebuilds in 1-2 minutes).
