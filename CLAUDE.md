# iRacing Overlays — Project Notes for Claude

GitHub:   https://github.com/halvar20000/iracing-overlays — the primary
          repo and the source of truth. Sync between machines via git,
          never by copying folders (June 26 lesson).

Checkouts, and which is which:
  • `/mnt/user/AI/Projects/iracing-overlays-main` (Unraid) — a real git
    CLONE with `origin` set, and where Claude sessions work. Commits and
    pushes happen here.
  • `/Users/thomasherbrig/Nextcloud/iRacing/python/files/` (Mac) — where
    Thomas edits, inside Nextcloud. Historically a ZIP download rather
    than a clone, which is how the two lineages diverged in June 2026;
    `push_to_github.sh` exists for it.
  • The stream PC ran the overlays straight off the Unraid SMB share
    (`Y:\iracing-overlays-main`) until 2026-10-11. Since then Thomas uses
    a LOCAL install made with install.bat (updater, no git) — so changes
    reach the stream PC only once they are PUSHED, via the launcher's
    "Update & restart" banner; and new race logs (`logs/`) are written on
    that PC, not on the share — ask for them for post-mortems.

**The "stale / truncated mount" and "Nextcloud ate the folder" warnings
scattered through the session notes below are about the MAC path.** The
Unraid clone is plain local storage and has never been on Nextcloud —
do not reach for that explanation when something looks odd here (it was
reached for once, on 2026-10-10, to explain a 691-line diff in
`iracing_race_logger.py` that was simply unpushed work). Check the
content: `git show HEAD:<file>` against the working copy, then compile
and run its test.

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
| delta       | `iracing_qualidelta.py`       | 5014 | Quali live delta + per-sector splits; /sectors, /speed |
| catch       | `iracing_catchup.py`          | 5015 | F1-style catch-up battle (gap + catch prediction) |
| weather     | `iracing_weather.py`          | 5016 | Weather strip (temps, rain, wind, sky + live trends) |
| driver      | `iracing_drivercard.py`       | 5017 | Driver card (name, team, iRating, license, laps, inc) |
| rcmsg       | `iracing_rc_banner.py`        | 5019 | Race Control banner (iRacing flags + iRaceControl via ATVO TCP 5020) |
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
must be updated in the same session.** The files that actually list
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
5. `make_streamdeck_icons.py` — append to `OVERLAY_ICONS` (same name as
   the loader; category, symbol, short German label) and run
   `python make_streamdeck_icons.py overlays overlays_off`. That renders
   the on/off Stream Deck pictures AND rewrites `OVERLAY_TABLE.md` (the
   GitHub table of every overlay: port, loader, icons) and `OVERLAYS.html`
   (the searchable overview the launcher's footer link opens: symbol, URL,
   loader path to copy, size + options taken from OBS_OVERLAYS.md — so give
   a new overlay its OBS_OVERLAYS.md row too). `--table` = table + page only. Step 4 also
   rewrites the table and prints a WARNING for any loader without an icon
   — commit OVERLAY_TABLE.md together with the new overlay.

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

**October 29, 2026 (iCASControl 0.1.19 — "was black flagged" but no flag;
Le Mans AI test, read live from the stream PC's iCAS over the LAN):** five
DTs for #2 David LoVecchio: iRacing ANSWERED "David LoVecchio (#2) was black
flagged." each time (Thomas's chat screenshot), but CarIdxSessionFlags never
showed black (iCAS + race logger) and the car never served one; the same
"!black #2 D" by hand at 2932 s and from iCAS at 3184 s DID work (served
3012 / 3283, 32 s), #14 / #449 (first live auto blue-flag DT) worked at once.
Cause unknown. A second DT while one is open is not owed twice. A blind retry
was built and DROPPED (would double a penalty iRacing did give). Now a black /
DQ that does not show = "NO black flag at the car after 6 s … check the car, if
it has no flag give the penalty again (no reply in the chat = not admin)"
instead of "is this PC admin?". 0.1.20 (same evening, Thomas: "difficult if it fails on Saturday"; 9 of 14 DTs
failed, no pattern in lap pct / pit / time since stop): a black flag not at the
car after 6 s is typed ONCE more, slower (CHAT_RETRY_DELAY_MS 40, chat open
wait 0.8 s; `retry` on the check, log "(second try, typed slower)"), then the
"NO black flag … after two tries" note. Safe because no unconfirmed DT was
ever served and a second DT while one is open was not owed twice. It did NOT
help (#2 / #128 failed twice more); `!black #128 D` typed BY HAND a minute later
worked (served 4813 s) - iCAS's "post" typing is the difference, cause unknown.
0.1.21: method "focus" was tried live and Windows REFUSED SetForegroundWindow -
SendInput typed the blue-flag lines into Thomas's browser (his chat with
Claude). Now focus types only while GetForegroundWindow == iRacing (checked
before opening the chat, before typing and per character; else "NOTHING
typed"). RC message: the log said "to car #2" for an
RC (= /all) line when a car was selected → the TARGET decides ("to all (RC)",
"PRIVATE to car #N", PRIVATE without a car refused). test_incidents 304/304.
0.1.24 (same session, after RUN every DT worked except #30 Tyler (auto blue
DT) and #96 Robert Nash: both tries failed, no clear / pct / pit pattern;
"@PRIVATE>" in the chat is just iRacing's admin reply format): a black flag
still missing after the 2 quick tries is typed AGAIN every CHAT_RETRY_LATER_S
30 s up to CHAT_RETRY_TRIES 5 (`_chat_later`; a flag that shows up meanwhile =
"came late", nothing typed; a new black / DQ / clear for that car drops the
waiting retry), then the "NO black flag … after 5 tries" note.
test_incidents 319/319. FIRST LIVE RUN: #96 Robert Nash, all 5 tries over
2 min failed, and iRacing answered "Robert Nash (#96) was black flagged." in
the chat each time (Thomas) = the typing ARRIVES, iRacing accepts and does
not apply the flag for that car. Cause in iRacing, not fixable here; seen
only in AI sessions so far (#2, #128, #30, #96).
THEN #96 worked (10 s after a !clear #96 - maybe a hidden pending penalty;
an earlier clear → black for #96 had failed, so unproven). Same evening
three !yellow (2464 / 2489 / 2509 s) did nothing, the 4th worked (2557 s,
Thomas: it showed in the chat) → 0.1.25: !yellow is typed once more, slower,
too (no 30 s later tries - a caution 2 min late is not wanted), then "NO
caution after 2 tries … are full course cautions on?" (was "is this PC
admin?"). test_incidents 321/321.
Then the pace car stayed out ~2 laps after !pacelaps 1 again (caution
2557 s leader lap 10, !pacelaps 1 at 2666 s = start of lap 11, the field
caught the pace car at the end of lap 11, ONE TO GREEN 3794 s = start of lap
13) - iRacing seems to count from the line-up. Thomas: do the "−1" by
itself → 0.1.26: `fcy_lineup_adj` (default −1, 0 = off, Rules dialog): when
the LEADER is ≤ LINEUP_M 250 m behind the pace car for LINEUP_HOLD_S 5 s in
lap 1 (after the !pacelaps) → `!pacelaps -1` once (log "has caught the pace
car (… m)"); caught already in lap 0 or later = log line only. Pace car
position = NEW SessionFrame / RaceState `pace_car_prog` (its CarIdxLapDistPct,
−1 = not in the world; pct only, its CarIdxLap may count differently).
steward.clean keeps the sign (`_SIGNED`). test_incidents 331/331. NOT tried
in iRacing - watch the chat answer to "!pacelaps -1" and when ONE TO GREEN
comes.

**October 29, 2026 (flag overlay 5008: YELLOW + GREEN flags too):** Thomas:
not only white / checkered. NEW `CautionWatch` in flag_overlay.py (pure, race
sessions only - `_session_race` from SessionType): FULL COURSE caution =
SessionFlags 0x4000 / 0x8000 only (0x08 / 0x100 are local yellows, iCAS 0.1.8
lesson); one-to-green 0x200 counts only after a caution (it also flies on
the pace lap). Events: yellow "FULL COURSE YELLOW · SAFETY CAR" 10 s,
one_to_green "SAFETY CAR IN · RESTART NEXT LAP" 8 s, restart "GREEN FLAG ·
RACING RESUMES" 8 s, start (SessionState parade -> racing or startGo edge)
"GREEN FLAG · RACE START" 8 s; first update of a session only records
(mid-race attach shows nothing old, a caution in progress still gets its
restart). /state carries caution / caution_seq / caution_out; white /
checkered win over them. `?caution=0` = old behaviour, `?yellow=hold` keeps
the yellow up for the whole caution. test_flag_overlay 56/56 (+19); yellow +
green rendered. NOT tried live. Restart 5008.

**October 29, 2026 (RC banner 5019: hide other OBS sources while a message
is on air):** Thomas: an overlay should go away whenever the banner shows
something. rc_config.json `"hide_sources": [OBS source names]` → `ObsHider`
(obs_ws, OBS from rc_config "obs" or show_config.json "obs"): the banner page
POSTs `/rc/onair {secs}` when it shows a message (not with ?demo=1 / ?hide=0);
every VISIBLE scene item of those sources in every scene is hidden, and
restored HIDE_PAD_S 1.2 s after the last message's end (queued messages = one
hide; one report ≤ HIDE_MAX_S 30 s). Items that were off stay off. `/rc/hide`
status (reports = posts from the banner page, found = matching items; sources inside OBS GROUPS are searched too — GetGroupSceneItemList), line on /panel. FIRST TRY on the stream PC: hide_sources ["driver_simple"] loaded, nothing hidden, no error -> the OBS banner source still ran the OLD page (no /rc/onair) or the source sits in a group. test_rc_banner H1-H7; checked over a real websocket
against fake_obs. NOT tried with real OBS. Restart 5019.

**October 29, 2026 (iCASControl 0.1.18 — SHORT CAUTION, Le Mans):** Thomas:
a caution should last 1 lap. `fcy_short` (default OFF) in steward
`_short_caution`, for EVERY caution (auto / button / iRacing's own, edge of
_caution_out in a race): lap the caution comes out = lap 0 (leader = max
lap + pct); `fcy_pit_open_s` 5 s later `!pitopen`; when the leader's lap >
lap 0: `!pacelaps fcy_short_laps` (1) once + `!waveby` for every car (not on
pit road) ≥ 1.0 lap of progress behind its class leader (`fcy_wave` "class",
or "overall" / "off"). While on, `fcy_laps` after an auto caution is skipped.
Log kinds auto_caution_short / auto_caution_wave. test_incidents 301/301.
NOT tried in iRacing — UNKNOWN: whether !pacelaps 1 at the start of lap 1
means "green at the end of this lap" (assumed) and whether iRacing accepts
!waveby for every car at that moment (it only accepts a car in the pace field).
FIRST LIVE TEST (same day, Le Mans AI session, 0.1.20): Thomas "FCY worked".
Log: !yellow confirmed 5533 s (leader in lap 22), !pitopen 5 s later,
!pacelaps 1 sent at 5763 s when the leader began lap 23 (~4 min behind the pace
car at Le Mans); all 7 !waveby NOT confirmed (pace bit never set) - probably
the lapped cars were not lined up in the pace field yet; open whether a later
waveby (e.g. on one-to-green) would land. THEN Thomas: "They are overtaking"
- the wave-arounds DID work although CarIdxPaceFlags stayed 0x0 → 0.1.22: an
unseen wave-around bit is no longer "did NOT confirm" but "no wave-around bit
seen (iRacing often does not show it); check that the car drives past".
Pace car then stayed out ~2 laps after !pacelaps 1 (sent 5763 s, one-to-green
6894 s) - cause OPEN (post-typed command lost like !black, or iRacing waits for
the field to line up; no telemetry shows the caution laps left). iCAS was
restarted twice during that caution and each restart began the short caution
again (new lap 0, !pitopen / !pacelaps / !waveby again) → 0.1.23: RaceState
`short_caution` (autosaved, with session key + num) is taken over by a fresh
AutoSteward; cleared at the green. test_incidents 312/312.

**October 29, 2026 (iCASControl 0.1.17 — blue flag rule for multiclass +
lapping, IEC; new logo):** Thomas's rule, `cb_*` keys in steward.py
(`_blue_rule`, default OFF; ON replaces iRacing's-blue-flag rule and the own
hint): a car of a FASTER CLASS (`class_pace`: CarClassEstLapTime — new on
CarFrame / Car as `class_est_lap`, Car also `class_id` — else the class's
median best lap; ≥ 1 % quicker) or a car LAPPING him (≥ 0.5 lap ahead in the
race) within `cb_gap_s` 1 s behind on track → private `cb_text`, again every
`cb_every_s` 30 s; not let past after one of HIS laps (max `cb_dt_max_s` 180 —
Le Mans) and still ≤ 2 s → DT (`blue_dt`, one per car per window) + `cb_dt_text`
to all. Period ends on the pass, a car leaving, or 10 s > 3 s back. UNLAP
exception: close pairs are watched for passes; passing a car that is a lap+
ahead in the race = unlap → that pair (unlapper as slow) is blue-free for
`cb_grace_s` 120 s (log kind auto_blue_unlap); still ≤ 1 s afterwards = the
normal rule. From lap `cb_from_lap` 2, no caution. Log kinds auto_blue /
auto_blue_dt. test_incidents 290/290 (+19). NOT tried in iRacing. Also:
new iCAS Control logo (racecontrol/iCAS_Control_Overlay-Logo*.png, by Thomas)
in the header, login dialog, favicon (frontend/img/) and on the website's
iCASControl pages.

**October 28, 2026 (trackmap 5007: turn numbers):** Thomas wanted the turn
numbers on the map. The track SVG now carries a small dark numbered disc per
corner, 36 px from the apex on the OUTSIDE of the bend (`_turn_labels`). The
corners and numbers are the SAME as the corner-cue overlay 5012 and its /turn
label: `iracing_drivingline.load_track_corners` (geometry, imported lazily)
+ cues/<track>.json "turn" (official number) / "hide". Geometry numbering can
differ from the official one (Hungaroring 16 vs 14, chicane halves count), and
cues/ is where that gets fixed per track. `?turns=0` hides them. /state carries
`track_length_m`. Checked by rendering Hungaroring, Spa, The Bend GT. Restart 5007.

**October 28, 2026 (RC banner 5019: black flag on pit road = "PIT LANE
SPEEDING"):** WCT The Bend: Maurice Becker's black flag came 1.5 s after his
pit entry (race log: pit entry 2175.5 s, flag 2177.0, stop 92.6 s vs ~34 s
normal) and the stream could not say why. FlagWatcher.tick takes
CarIdxOnPitRoad; a black flag while the car is on pit road or within
PIT_SPEEDING_AFTER_S 5 s after leaving it → headline "BLACK FLAG · PIT LANE
SPEEDING" (still kind penalty → tower "PEN" tag). On track = "BLACK FLAG ·
PENALTY" as before. Thomas did not have 5019 on air that night. Also from
André: live incident points need the account to be entered as SESSION admin
for the race (league admin is not enough); he then joins via Watch as a
spectator. test_rc_banner PS1-PS4. Restart 5019.

**October 28, 2026 (iCASControl 0.1.16 — WCT The Bend race save reviewed):**
Thomas's autosave 326084573-89682872.json (PC not session admin): official
points were NOT live in any session — each session's points came in one go
at its end (practice 4512 s, quali 2060, warmup 315, race 3943), so the
15x / then 6x incident rule fired only after the flag (Rissler 21x, Warnow
18x at 21:01; iRacing's own black flags had come during the race). Live
points need the admin account on the iCAS PC. BUG fixed: DriverInfo
TeamIncidentCount / CurDriverIncidentCount are NOT reset at a session change,
so at the green of quali, warmup and race every car got the PREVIOUS
session's final count logged as a new incident. Now the values read at a
session change are that session's baseline (`_di_base`, first read = nothing
counted, later reads minus the baseline); a value below its baseline = iRacing
reset them → baseline dropped (checked BEFORE subtracting). test_incidents
269/269 (+6, 4 fail on the old code). Also seen: sequencer quali messages
(00:00:01 / 00:15:00) went to chat + banner; fast-repair (6) and PRO-behind
(4) hints sent; the 21 "Blue flag: let … pass" lines are the OWN gap-based
hint (Thomas's saved steward_config still has it ON — 0.1.11 made it default
OFF); iRacing's blue-flag rule (20 s warn / 30 s DT) never fired.

**October 28, 2026 (tower intervals wrong — Björn's WCT The Bend photo):**
blank intervals mid-tower with +58 s / +50 s next to them, and "+17 LAPS"
for every OUT car although they stopped in lap 12. (1) The interval was the
diff of CarIdxF2Time (refreshed only at timing lines, in iRacing's OWN
order) while the tower sorts by live progress — after the stops the two
disagreed: negative diff clamped to 0 = blank, the next car got both gaps.
NEW `GapClock`: each poll records (lap + pct, SessionTime) per in-world car;
interval = now − when the car ahead passed this car's current progress
(interpolated; 600 s window; reset on session change / time jump back /
progress going back). F2Time only without history, and a negative F2Time
diff now falls to the pct × EstLapTime estimate instead of a blank. (2) OUT
cars read lap / pct −1 → laps down from the leader's lap + 2: the tower now
keeps each car's last in-world progress (`_last_prog`) and uses it; never
seen running = no "+N LAPS"; OUT = no interval. NEW test_gap_clock.py 10/10
(6 fail on the old code exactly like the photo); standings tests pass.
CODE_VERSION "2026-10-28 gap-clock". Restart 5005.

**October 27, 2026 (driver card: CLS team under the name + FULL name):** the
card now shows the full name with first name (?short=1 = old "M. Becker"). Solo sessions
(WCT, PCCD) send no real team (TeamName = driver → hidden), so the card
(/ and /simple) had no team line. cls_proam now also collects `teamName` per
league in the all-league roster fetch (`_teams` {slug: {iracing id: team}},
cache proam_teams.json — gitignored + updater PROTECTED); `team(uid,
field_ids)` takes the league whose roster holds the most drivers of the field
(≥ 3), so a driver in two leagues gets the right team. The card uses iRacing's
team first, else this. test_drivercard 22/22; live CLS: Maurice Becker →
REVORYX eSports. Restart 5017.

**October 27, 2026 (/table/wct: points only):** the "-20" gap to the class
leader in front of the points is gone (Thomas). Restart 5010.

**October 27, 2026 (The Bend track files swapped BACK + slideshow):** 6fcdfbf
(Oct 22) was wrong: it put the 4.95 km International loop into thebend_gt /
thebend_gtcircuit and the 7.76 km GT loop into thebend_international / _intl
(checked by drawing both on the satellite image: the GT circuit includes the
outer eastern loop). Restored from 6fcdfbf^; zoom_videos/thebend_gt.mp4
re-rendered. thebend.json (bare slug) is still the International loop.
Slideshow for the stream start: drone_videos/thebend_diashow.mp4 (63 s,
seamless loop, Esri imagery) + stills in drone_videos/thebend_diashow/.
Restart 5007 + 5012.

**October 27, 2026 (MASTER SWITCH for the AI show — `"ki_show": false`):**
the moderation auto-started at start_at during a PCC stream (OBS streaming)
and handed over to REGIE. show_config.json `"ki_show": false` now switches
the whole AI show off: show_runner.show_enabled() (file re-read every 2 s by
mtime — no restart) → ShowRunner.tick returns (a running show is stopped),
start() / prepare() refuse with a log line (no script, no paid Gabi video),
dashboard _maybe_post_show skips the Abspann; /show and /abspann say "KI-Show
AUS". No file / no key = on (old behaviour). REGIE itself only ever comes on
by hand or from the show. test_show_runner +7. Stream PC had auto_start true,
start_at 19:05. Restart 5000 once for the code.

**October 27, 2026 (overtake ticker: only PRO > PRO and AM > AM in GT3 WCT):**
dashboard `_ot_classes()` groups by (CarClassID, PRO / AM / UNRATED) when
cls_proam says the field is WCT (poller.proam.lookup_field), so a PRO passing
an AM is not an overtake and "for P3" = P3 inside PRO / AM; record carries
`proam`, ticker shows "P3 AM". Lapping was never counted (rank by lap + pct).
Affects the dashboard feed / REGIE overtakes too. test_overtakes O8 / O9.
Restart 5000.

**October 27, 2026 (tower: slim race layout + brand logos):** Thomas's WCT
screenshot: no logo for the Corvette (brand "chevrolet" had no file), McLaren /
Aston Martin read as thin dark lines (wide wordmarks at 20 px). NEW
brands/chevrolet.svg (drawn bowtie on a white chip); mclaren.svg = the red
speedmark only on a white square; aston-martin.svg = wings only, dark strokes,
cropped wide (wordmark below removed). Compact RACE tower (not teams / cars)
`.tower.slim` 360 px instead of 440 (name column ~160 px, "B. Krumpschmied"
fits); `?slim=0` = old width. CODE_VERSION "2026-10-27 tower-slim". Rendered
with the stream PC's live /standings. Restart 5005 (logos also on 5001 / 5015).

**October 27, 2026 (battle overlay 5015: PRO / AM in GT3 WCT):** Thomas: an
AM on camera must battle the AM ahead, not a PRO in between. CatchPoller
`_proam_of()` (cls_proam.ProAmRoster, same "WCT field" rule as the tower) →
a PRO / AM focus keeps only cars of the same Pro/Am class (positions = class
positions inside PRO / AM; gap via F2Time diff stays valid — same car class);
UNRATED focus or a non-WCT field = car class as before. Payload cars carry
`proam`; banner position box red / green; /chasing "FOR 2ND AM". `/proam`
status route. /gap (battle_gap): same data, red / green bar under both
positions (rendered). test_catchup 48/48 (+8). Restart 5015.

**October 27, 2026 (/lastrace/wct showed "?" instead of AM):** CLS WCT
standings carry a CAR class on 2 of 38 registrations (Maurice Becker "Pro",
Alexander Thieme "Am" — admin data, tell Thomas), so `_class_of` saw two
classes and took the IEC multiclass path: sections "?" (everyone without a
class) and "PRO". Now Pro/Am rounds never split by car class, and
`_class_of` needs a class on at least half of the standings rows. Checked
with live CLS: WCT before R4 → ERGEBNIS / PRO / AM (class points 35), IEC R2
→ LMP2 / GT3 / PORSCHE CUP. Restart 5010.

**October 27, 2026 (OVERLAYS.html — one page with every overlay):** Thomas:
"ich suche und suche immer wieder". make_streamdeck_icons.write_overview()
(called by write_table) generates OVERLAYS.html: cards per Stream Deck
category (on / off symbol, name, URL + copy, loader file + "Pfad kopieren" =
full Windows path derived from the page's own file:// location, size /
options cell parsed from OBS_OVERLAYS.md by loader name, YouTube by
port/path), general tips (zoom / debug / demo / nav keys), the operator pages
table, live search (all words must match). OVERVIEW_HTML is a NON-raw Python
string — no backslashes in its JS (a "\\" became "\" and broke the script
in the first commit; fixed with String.fromCharCode(92)). Launcher footer link
(packed side=bottom under the log) opens it via webbrowser. Added the
Frag-Claude status row to OBS_OVERLAYS.md (was missing). Rendered; search
checked over CDP (tower → 4, "5010 wct" → 4, reifen → 2).

**October 27, 2026 (5010 `/live/wct` — PROVISIONAL championship in the
board look, PRO / AM):** Thomas wanted the live projection (old /overlay view
B) in the new design. `_live_wct(_state())` = board data from
build_projection's champ_rows: Pro/Am seasons → PRO / AM sections (proj_points
= class total + class race points), multiclass → per class, else FAHRER;
pos = projected class position (proj_points desc, CLS rank tiebreak), value =
"▲2 +25" (places won / lost INSIDE the class vs pre_points order + points of
this round incl. earlier races of the round), pts = projected total. Title
"MEISTERSCHAFT PROVISORISCH - <league> - LAUF n+1" + tab LIVE while a race /
finished race / earlier race of the round counts, else "LIVE … NACH LAUF n".
Loader live_wct.html (63), icon "Tabelle live" (rendered with chromium
--headless=new; the script's old --headless hung here), OVERLAY_TABLE,
OBS_OVERLAYS. Checked with the live WCT CLS table + a faked race (last AM
wins his class: +40, rises). Restart 5010.

**October 27, 2026 (WCT: BLACK bar for a driver not rated PRO / AM yet):**
Thomas: tonight a driver races in WCT who CLS has not classified. NEW
`cls_proam.ProAmRoster.lookup_field(user_id, active)` = "PRO" / "AM", in a WCT
field (active) a driver with no class in CLS (or not on the roster at all) =
`UNRATED`, not a WCT field / id <= 0 (pace car) = None. Used by the tower 5005
(all .pa bars: classic, tower, movers / fastest / gapbar / pit pages →
class `nr`: black + 1 px white ring), driver card 5017, trackmap 5007 (black
dot, white ring + white number; camera car stays yellow), results 5002 /wct
(wct_board.UNRATED_COLOR #000000 + white ring). PRO / AM class pages leave
him out (he is neither). iCAS's PRO-behind rule ignores him. test_proam
14/14 (+4); tower rendered. Restart 5002, 5005, 5007, 5017.

**October 27, 2026 (iCASControl 0.1.15 — PRO behind an AM, GT3 WCT):**
Thomas: an AM driver (per CLS) should get ONE hint when a PRO is behind him,
not repeated (the AM may be faster). NEW `racecontrol/backend/proam.py`
(wraps the repo root's cls_proam.ProAmRoster — same CLS roster + "is this a
WCT field" rule as the tower bars; started only once a car with an iRacing
UserID is seen, so simulator / tests fetch nothing); CarFrame / Car carry
`user_id`, Car `proam`. Steward rule `pro_enabled` (default ON — saved
configs get it), `pro_gap_s` 1.0, `pro_from_lap` 3, `pro_text` "PRO CAR
BEHIND YOU - #%pcnr% %pdname%": race, racing, no caution, both cars racing,
PRO behind on track within the gap (its best / last lap × pct diff), NOT a
lap+ apart (= blue flag); once per (AM, PRO) pair per session → private
notify + log kind auto_pro_behind. Rules dialog fieldset. test_incidents
263/263. NOT tried in iRacing.

**October 27, 2026 (launcher: iCAS dot stayed grey after the first run —
Andre):** first Start = green (sim demo, Silverstone), after "off" Start never
went green again and the browser on :8080 still showed the old demo. Cause:
the old iCAS kept running with nobody owning it (closing the launcher stopped
overlays in daemon threads and destroyed the window after a FIXED 1.5 s —
before the 3 s grace + kill, and uvicorn does not quit at once), and Start
refuses a busy port (only a log line). Now: `_on_close` waits until every
stop is done (≤ 10 s); stop() ends the whole tree (`taskkill /T /F`, iCAS's
cloudflared too); Start on a busy port asks netstat (state word is localised —
matched by the 0.0.0.0:0 remote address) + tasklist for the owner and ends it
if it is python*.exe (anything else is left alone, old error line). Tested on
Unraid with faked owner / kill (python holder ended + started, Teams.exe left).
NOT tried on Windows.

**October 27, 2026 (iCASControl German guide + website):** NEW
`ICASCONTROL_ANLEITUNG.md` (repo root, du-form: install of the overlay app,
launcher row, updates, UI, incidents, replay, chat commands, race control,
Auto Steward + sequencer with WCT values, stream banner / tower, several
stewards incl. follow mode + tunnel, autosave, troubleshooting, data files),
screenshots in racecontrol/docs/. Linked from racecontrol/README.md + INSTALL.md.
Shim `iracing_racecontrol.py` now reads optional racecontrol/data/server.json
{"host": "0.0.0.0", "port": …} (LAN access without a code edit that updates
would overwrite). Website: simracing-hub.com overlays/racecontrol pages updated +
new overlays/de/racecontrol-anleitung.html.

**October 27, 2026 (iCASControl 0.1.13 — following YOURSELF refused):**
Thomas tested Follow with his own tunnel link on the same PC: STEWARDS showed
"Thomas", "Thomas 2", "Thomas 2 2 → main iCAS" and chat lines never arrived
(the iCAS followed itself: its own actions went upstream to itself, its own
broadcast stopped while following). is_self() only caught localhost URLs.
Now every "hello" carries `icas` = follow.INSTANCE_ID (uuid per process);
a Follower that gets its own id back = "refused" ("that link leads back to
THIS iCAS"). A steward must follow from HIS OWN PC (or just open the link
in a browser). test_incidents 251/251; real run: self-follow via the LAN IP
refused, chat works again.
CONFIRMED by Thomas (same day): from a second device over the tunnel link
it works ("habe ich so gemacht und es funktioniert") - first real use of
the multi-steward login / tunnel.

**October 27, 2026 (iCASControl 0.1.12 — sequencer):** Thomas's
iRaceControl SEQUENCER screenshot (QUALIFY: MSGT 00:00:01 "Start Qualifying
AM Klasse", 00:15:00 "Ende Qualifying AM Klasse / Start Qualifying PRO
Klase"). The fixed quali_split switch is replaced by `seq_enabled` +
`sequence` [{session quali|race|practice|warmup|any, at seconds, text}]
(steward.clean_sequence: "HH:MM:SS" / "MM:SS" / seconds, ≤ 30, sorted);
AutoSteward._sequence sends each step once while at ≤ SessionTime < at + 120
→ log kind auto_sequence + /all + stream banner. Old saved configs are
converted (_migrate: untouched old English defaults → the new WCT texts,
own texts → two steps). Rules dialog: row list + "+ Add message". Default
OFF (iCAS also runs IEC). Not built: RANDOM YELLOW (WCT does not use it).
test_incidents 249/249; dialog rendered.

**October 27, 2026 (iCASControl 0.1.14 — replay did nothing, host AND
follower; camera switching worked):** `_replay_worker` sought straight from the
live picture; iRacing often ignores that (the dashboard and highlight_runner
both PAUSE first) → now `replay_set_play_speed(0)` + 0.1 s before the seek; a
jump that does not land names wanted vs actual session / time and goes back
to live. Follow mode: the LIVE button (`command replay_live`) went to the MAIN
PC → `follow.is_local()` (LOCAL_COMMANDS) keeps it here; source notes
("Replay jump did not land") were written to the hidden own log → toast (ack)
to this PC's browser; the REPLAY bar showed MAIN's in_replay → snapshot
`session.in_replay` = OUR state. test_incidents 252/252.
THEN Thomas: "Sorry, falsch. Es funktioniert" - replay works (the report was
a mistake); the 0.1.14 changes stay (pause first = same as dashboard).

**October 27, 2026 (iCASControl 0.1.11 — GT3 WCT Auto Steward ruleset
from Thomas's iRaceControl screenshot):** race only: iRacing's BLUE flag
(CarIdxSessionFlags 0x20) shown 20 s (`bf_warn_s`) -> private "BLUE FLAG -
LET PASS FASTER CAR"; 30 s (`bf_dt_s`) -> !black #N D + "/all #N (driver) -
DRIVE THROUGH FOR BLUE FLAG" (+ stream banner penalty); once per blue period,
not under caution / in the pits. Fast repairs (NEW CarIdxFastRepairsUsed on
CarFrame / Car `fast_repairs_used`) reaching `fr_used` 1 -> private "YOU'VE
USED UP ALL YOUR FASTREPAIR" once (first sighting = silent baseline).
Meatball (0x100000, edge) -> "/all #N (driver) - HAS A MEATBALL FLAG". FCY
defaults now 40x / 15 s, `fcy_end_protect_min` 5 = no auto caution in the
last 5 min (session_time_remain). Texts take %cnr% / %dname% (steward.fill).
The old gap-based blue hint is now OFF by default ("Own blue-flag hint").
PRIVATE chat = "/14 text" (support.iracing.com 31000170165) - chat template
`notify` changed from "/all #14 text" (menu Notify, RC PRIVATE, blue hints).
NOTE: a saved data/steward_config.json keeps its old values (e.g. 10x / 10 s,
blue hint on) - set the WCT values once in Rules.
test_incidents.py 243/243; rules dialog rendered. NOT tried in iRacing.

**October 27, 2026 (iCASControl 0.1.10 — SUPP follow mode + internet
tunnel, multi-steward steps 3 + 4):** NEW `racecontrol/backend/follow.py`
(`Follower`: websockets client to the MAIN iCAS's /ws?name=&pw=, caches
init / track / events / snapshot / stewards, passes them to THIS PC's own
browser (hello as the upstream name, `following`), every action except
LOCAL_ACTIONS (follow, tunnel, camera, replay) goes upstream, acks routed to
the sender, reconnect 2-30 s, "login" = refused + stop; password in memory
only, data/follow_config.json {url, name}). While following: own Auto
Steward / autosave / restore paused, replay = the MAIN log line
(session_num, sim_time, car_number) in OUR iRacing, camera next / prev / car
from MAIN's cars (CarIdx same on every PC), snapshot carries OUR camera +
`net` (follow status, session-mismatch warning). NEW `backend/tunnel.py`
(cloudflared quick tunnel `tunnel --no-autoupdate --url
http://127.0.0.1:<port>`, link parsed from its output, "Registered tunnel
connection" = on; named tunnel via data/tunnel_config.json token + hostname;
binary from data/bin / PATH / Program Files, Download button = official
GitHub release, curl fallback). Tunnel only with an access password, only
from the main PC itself; removing the password stops it; set_running only
local. UI: STEWARDS panel rows Internet (Start / Stop / Copy link /
Download) + Follow (dialog URL / name / password). test_incidents.py
229/229. REAL test from Unraid: two sim servers, follower over the LAN
(wrong password refused, chat both ways, own camera, RUN/STOP refused) AND
over a real trycloudflare.com tunnel (page 200, /api 401 without token, WS
place "remote", follow works; link dead after Stop). NOT tried: Windows
(cloudflared.exe download / CREATE_NO_WINDOW), real iRacing replay on a
follower.

**October 27, 2026 (iCASControl 0.1.9 — multi-steward steps 1 + 2: camera
buttons, login, steward chat):** Thomas: camera switching like iRaceControl,
and several stewards at once, spread over Germany (not one LAN). Agreed
plan: MAIN iCAS (admin PC, NOT the stream PC) + remote stewards; camera /
replay act on the iRacing of the PC that runs the iCAS server → step 3 = SUPP
mode (own iCAS following MAIN), step 4 = Cloudflare tunnel. DONE now:
NEW `racecontrol/backend/access.py` — where(host, headers): local (loopback,
no proxy headers) / lan / remote (cf-* / x-forwarded-* headers or public IP);
admit(): local always, password (PBKDF2 hash + salt in
data/access_config.json, gitignored dir) when set, LAN free without one,
internet REFUSED without one. /ws?name=&pw= → {"type":"login", error} + close
4401, else "hello" {name, place, token}; duplicate names get " 2";
engine.clients = dict ws → {name, place, token}; handle_client_message(msg,
who, place) sets RaceState.actor → RaceEvent.by / decided_by; actions
steward_chat (RaceState.steward_chat, 300 kept, saved), claim (claimed_by,
someone else's claim refused), set_password (local only, ≥ 6 chars, "" =
remove), camera (group / next / prev / car). Broadcast "stewards" {online,
chat, password} on change. HTTP middleware: /api/* from non-local only with
?t=<token> (LAN free while no password). IRacingSource.camera_info()
(CameraInfo groups cached 2 s, no Scenic, CamGroupNumber, CamCarIdx), cam_group
keeps the car, cam_car now keeps the group (was always group 1;
pick_cam_group = Scenic → TV1); simulator fake groups. UI: STEWARDS header
button (count, unread badge, panel with online chips, chat, Name, Access
password), login dialog, Camera tab, "· name" + "👀 name" log badges, "I take
it" / Release in the incident actions. test_incidents 204/204; real server run
(sim): internet without password refused, wrong password refused, right one
in, chat reaches the host, camera, /api 401 without / 200 with token;
rendered. NOT tried with real iRacing / over the internet.

**October 27, 2026 (iCASControl 0.1.8 — stayed on CAUTION after the
restart):** Thomas: the race was green again, iCAS still said caution. The
source mapped SessionFlags yellow 0x08 and yellow waving 0x100 to "yellow",
and steward `_caution_out` counted "yellow" as a caution — those are LOCAL
yellows (waved at a corner) and can stay on after the restart. Now ONLY
0x4000 / 0x8000 (+ one-to-green 0x200 while racing) are the caution;
0x08 / 0x100 = "local_yellow" (flag box: "GREEN · local yellow"); green when
nothing but local yellow / blue / per-car bits are set. iRacing keeps green
0x04 on through a caution, so the log never got a GREEN line: RaceState now
logs "ONE TO GREEN - restart next lap" and "GREEN flag - caution over" when
the caution bits go off. Snapshot `flags_raw` (flag box tooltip shows the hex)
for the next report. test_incidents 175/175.
CONFIRMED by Thomas in iRacing (same day): the caution end is detected
("Caution Ende --> funktioniert").

**October 27, 2026 (iCASControl 0.1.7 — caution length):** Thomas: how long
does the pace car stay out? iRacing decides itself; `!pacelaps N|+n|-n` (admin,
under a caution) sets it. Race-control panel: row "Caution laps" (number +
Set, −1, +1 → command `pace_laps` {n}, validated `[+-]?\d{1,2}`, not 0; a hint
when no caution is out). Rules: `fcy_laps` (0 = iRacing decides, max 20) →
after an AUTO caution (and the ask line's DEPLOY, sent with `auto: true`)
Engine._pending_laps waits up to CAUTION_LAPS_WAIT_S 30 s for the caution
flag, then `!pacelaps N` once; only with iRACING CHAT on. test_incidents.py
168/168; panel + rules rendered. NOT tried in iRacing.

**October 27, 2026 (iCASControl 0.1.5 step 3c — Auto Steward):** Thomas's
rules: incident limit DT at 15x, then every 6x — ONLY a message (the league's
iRacing settings give the penalty); full course caution when too many
incidents in a short time; GT3 WCT quali message "first 15 min AM" + after
15 min "AM finished, PRO started"; blue flag message to the car being lapped
when the faster car is within 1 s. NEW `racecontrol/backend/steward.py`
(config data/steward_config.json, `AutoSteward` kept by the Engine, reset per
session key / num; acts only live, not in replay): limit = RaceState
`_auto_steward()` (Car.steward_level autosaved; attach at 28x = ONE line for
DT #3; counts that drop reset the level; race only); FCY = the official incident POINTS of all cars together >= fcy_incs within
fcy_window_s under green (0.1.6, Thomas: "configurable, e.g. 10 incs within
10 s" — default 10x / 10 s, mode auto = !yellow at once; ask = log line +
DEPLOY CAUTION button, 2 min), cooldown 180 s, a caution blocks the window;
quali split (default OFF) by SessionTime (start msg only < 3 min, PRO msg in
[split, split+2 min]) → /all + stream banner; blue = lapping car (progress
diff > 0.5 lap) behind on track with d × its best/last lap ≤ blue_gap_s,
cars of the LAST frame only (stale disconnected cars gave phantom flags in the
test), not pit / caution, repeat after 20 s, re-armed after the pass; chat
only when iRACING CHAT is on. UI: header AUTO STEWARD + Rules dialog (#sdlg),
amber auto rows + toast. test_incidents.py 155/155; header, log + rules
dialog rendered with the simulator.
CONFIRMED by Thomas in iRacing (0.1.6, same day): the points-based FCY
works ("Funktioniert").

**October 27, 2026 (iCASControl: right-click menu like iRaceControl):**
right PRESS on a timing row selects the car and opens `#ctx-menu` (taken on
pointerdown — rows are rebuilt ~11x/s): car info, Penalties ▸ (clear, time,
DT, S&G, DQ), Pacing ▸ (EOL, wave around), Notify, add / remove admin, chat
on / off, remove. Reason dialog `#dlg` (askReason(): presets Track Limits /
Forcing another driver off the track / Causing a collision, seconds row for
time penalties) also replaces prompt()/confirm() on the incident decision
buttons. Server: car commands drive_through / stop_go / time_penalty /
add_admin (!admin) / remove_admin (!nadmin) / chat_on (!chat) / chat_off
(!nchat) / remove (!remove); `!black` takes no text, so a reason goes out as a
second line `/all #N Driver: DRIVE THROUGH - reason` (REASON_LINE, also for
resolve). test_incidents.py 100/100; menu checked in Chromium (opens on the
right car 8/8, preset → car action sent + logged). NOT tried in iRacing.

**October 27, 2026 (STEP 3b — open penalties on the tower):** 5019 keeps
them: `PenaltyBoard` (Feed.board; every feed message with a car is applied
AFTER the _steward_said skip): `penalty_of(kind, headline + text)` → dsq / dt
("DRIVE T(H)ROUGH") / sg (+ secs) / time ("10 SECOND TIME PENALTY", "+5s";
they ADD UP) / black (iRacing's generic flag, only if no steward DT/SG is open)
/ pen / clear / warn (counted) / inv (until nfa / warning / penalty / clear,
max INVESTIGATION_MAX_S 600) / nfa. Served = FlagWatcher sees F_BLACK go OFF
(not with DQ) → oldest DT/SG removed. Session change (RcPoller key) resets.
`GET /rc/penalties` (CORS *). Tower 5005: `RcPenalties` thread polls
127.0.0.1:5019 every 2 s only while /standings was asked in the last 30 s
(5019 off = {}), /standings carries `penalties` (?pen=0 leaves them out), JS
`penTags()` after the name: DT / SG white-outlined black, +10s light, DSQ
red, INV blue; ?warn=1 adds W<n>. CODE_VERSION "2026-10-27 tower-penalties".
test_rc_banner P1-P11 (66 total), NEW test_tower_penalties.py T1-T6 (real
HTTP 5019 → RcPenalties → /standings); tower rendered with fake field.
Other standings tests pass. NOT tried live. Restart 5019 + 5005.

**October 27, 2026 (iCASControl STEP 3a — decisions onto the stream banner
5019; Thomas: step 2 all tested, "then start step 3"):** NEW
`racecontrol/backend/stream.py` (BannerOut: urllib POST in a thread,
`data/stream_config.json` {enabled OFF by default, banner_url, kinds}) →
5019 NEW `POST /rc/steward` {kind, headline, text, car number, driver, secs},
source "icas" (car via poller.find_car, else number + driver from iCAS;
`?icas=0` hides; `_steward_said` covers "icas" too → iRacing's black flag /
DQ for the same car within 20 s not shown twice). Engine.to_stream():
resolve UNDER INVESTIGATION → investigation ("#14 and #7 - reason"), NO ACTION
/ RACE INCIDENT → nfa, WARNING → warning, DT / S&G / TIME PENALTY ("10 SECOND
TIME PENALTY") / DSQ → penalty, car menu DT / S&G / time / DSQ / clear, RC
message → message; NOTED / EOL / wave-by / admin never. Result in the log
("On the stream banner: …" / "NOT reached (…) - is 5019 running at …?").
Header STREAM BANNER + Address (dialog) + Test. iCAS often runs on the
ADMIN's PC, the banner on the stream PC → address = LAN URL. Version 0.1.4.
test_incidents 120/120, test_rc_banner 55 (W5-W9); real HTTP iCAS sender →
banner server checked, header rendered. NOT tried live. Next: penalties on
the tower, Auto Steward. Restart 5019 + iCAS.

**October 27, 2026 (iCASControl STEP 2 — steward commands into the iRacing
chat; Thomas: incidents now match iRaceControl, selection works, go on):**
the old IRACECONTROL folder (Y:\mnt\AI\Projects\IRACECONTROL) is the May 22
v0.1, byte-identical to the June merge — chat sending was NEVER built (it only
showed the command text). NEW `racecontrol/backend/chat.py`: templates in the
OFFICIAL syntax (support.iracing.com 31000133518: `!black #14 D` drive-through,
`!black #14 [sec]` stop-and-go, `!dq / !eol / !waveby / !clear #14`,
`!clearall`, `!yellow`, `!pacelaps 1`, `!pitopen / !pitclose`, `/all`, `/rc`;
red / green flag, wave lapped, time pen = none → log only; no documented
private message → notify = `/all #14 text`), overridable in
data/chat_config.json (enabled OFF by default, method, char_delay_ms,
commands). `ChatSender` (thread, one line at a time): SDK
`chat_command(begin_chat)` opens the chat, then method "post" = WM_CHAR per
char + VK_RETURN posted to FindWindow("SimWinClass") (background), "focus" =
SetForegroundWindow + SendInput unicode, focus back; ctypes argtypes set
(0xC01C0001 lParam). IRacingSource.send_chat + verification for 6 s in the
telemetry: CarIdxSessionFlags black 0x10000 / DQ 0x20000, CarIdxPaceFlags EOL
0x01 / waved 0x04, SessionFlags caution → log "iRacing confirmed" or "did NOT
confirm … admin? method?". Engine.to_iracing routes: race-control buttons,
incident decisions (DT, S&G, DSQ, WARNING), car buttons, RC / private
messages; sim / replay = "Would send …" in the log. Header switch iRACING CHAT
+ method + Test (`/rc iCASControl chat test`, admins only, works while OFF);
confirm() for yellow / pit close / clear all / DT / S&G. NEEDS the admin PC:
the stream PC's account is not admin. test 91/91 (fake keys, fake IR flags,
engine routing). NOT tried on Windows / in iRacing at all.
FIRST REAL TEST (same day, Thomas's own hosted session, admin): method
"post" works — messages and commands land in the iRacing chat. `!eol #11`
with no caution out: iRacing answered "Can't set EOL when not under caution",
but we logged "iRacing confirmed" — #11's EOL pace bit was ALREADY set. Now
send_chat records the state BEFORE the command and only an off → on change
confirms; already set = "cannot confirm"; EOL / wave-by without a caution =
"iRacing only accepts this under a caution". test 103/103.
THEN (same session): EOL of the leader UNDER a caution and Deploy Pace Car
both confirmed. Version 0.1.2 (visible in the header) + raw flag bits
[session / car / pace] in the not-confirmed lines. Official INC still 0x
for everyone while iRaceControl on the SAME PC showed points → 0.1.3 also
reads DriverInfo.Drivers[].TeamIncidentCount / CurDriverIncidentCount (live
for an admin; ResultsPositions lagged), highest real value per car wins;
/api/incidents/debug shows team_inc / driver_inc beside incidents. test 105.
CONFIRMED by Thomas after the update: official points now show live (AI
session, ResultsPositions[].Incidents stayed 0 for all 20 cars after 4 laps —
the DriverInfo counts are the live source for an admin).
STEP 2 FULLY TESTED by Thomas (same session): pace car, EOL / wave-around
(iRacing: only under caution, wave-around only for a car in the pace field),
drive through / stop & go with reason, clear, DQ via the button — all work.

**October 27, 2026 (iCASControl first real run — Thomas's Spa AI practice
screenshot):** detection works live (spins, "Contact with #28", pit lane).
Fixed: CLS column read "None" for every car — hosted / AI sessions send
CarClassShortName as YAML null and str() made it "None"; `class_labels()`
names the class after its single model or the words all models share ("GT3").
LAP showed -1 (CarIdxLapCompleted before the first lap) → clamped to 0.
Practice / quali: order + gap by BEST LAP (was distance), no "takes the lead"
lines outside a race. Leaving the pit stall at session start logged
"Exited pit lane - stop #1 (73.8s)" → "Left the pits", no stop counted
(`_pit_enter_t` None = entry not seen). INC cell: red "0x (1)" = open
detections without a decision, tooltip. Still open: official points 0 in
that practice (nobody had a timed lap yet - check again in the race); RC
messages are only logged, not sent to iRacing (step 2). test 58/58.
SAME DAY (Thomas's iRaceControl screenshot: sector times + car type):
timing table gained S1..Sn before "Last" (as many as SplitTimeInfo has, else
3 equal) = the lap in progress, the previous lap stays until S1 of the new one
is done; purple = session best of that sector, green = the car's own best.
From the shared `field_sectors.py` (repo root, imported via sys.path; it now
keeps `car_last` per car); reset per session, replay footage never counts.
"Car" column = CarScreenName (replay source: the log's car, sim: brand).
Fixed on the way: driver names were RIGHT-aligned (`#timing tbody td` beat
`td.c-name`). test 64/64; table rendered with the simulator.
THEN: iRaceControl (on Thomas's own PC) showed 2x for Terence Groening, we
showed nothing. Same source (ResultsPositions[].Incidents) — expected cause:
the stream PC's iRacing account is not session admin. NEW
`GET /api/incidents/debug` (8080) = raw rows exactly as iRacing sent them
(number, driver, incidents, laps), whose car this PC is, SessionInfoUpdate,
the baseline. test 65/65.
Sector columns did not show on the stream PC at first (fine after a
reload): StaticFiles sent no Cache-Control, the browser kept the old app.js →
`_NoCacheStatic` (no-store on every dashboard file); /api/status shows
`sector_timing` (on, sector count, from SplitTimeInfo or not, import error).
Version 0.1.1. Thomas confirmed: not admin on the stream PC = no live points.
test 66/66.
Spa RACE screenshot: BEST empty after lap 1 (iRacing leaves
CarIdxBestLapTime unset for the first race lap; iRaceControl shows last =
best) → the source keeps the fastest LAST lap per car and session
(`_best_seen`, not from replay frames) and uses it when iRacing's best is
empty or slower. Class read "Class 0": the GT3 field had no common word
because of "GT3.R" → names split on punctuation. test 69/69.
Track map dot colours like iRaceControl (manual 3.1.3: Car / Class /
License / Relative / Laps behind leader): selector in the map header (kept in
localStorage), default CAR = main livery colour from CarDesignStr
(`design_color`); relative = vs the selected car (else leader): red a lap+
ahead, blue a lap+ behind, white same lap; laps = white / blue 1 / purple 2+;
license = LicColor. Our dots were all white = AI session class colour. Rings
(leader gold, selected red, off-track yellow) unchanged. test 71/71; rendered.
Selecting a driver needed several clicks / often failed: the timing table
is rebuilt with innerHTML ~11x a second, so press and release land on
different row elements and the browser never fires `click` (same for the
race log on every new line). Rows now act on `pointerdown`; double press
(camera on the car / replay of a log line) via `isDoublePress()` 400 ms.
CDP test with real mouse press + release 150 ms apart while the simulator
streams: old code 0 of 12 selected, new 12 of 12.

**October 26, 2026 (iCASControl = our iRaceControl clone, `racecontrol/`
8080 — STEP 1 of 3: live incidents, replay per line, autosave):** Thomas:
finishing our own clone would be simpler than the SignalR bridge. It was
untouched since the June merge (v0.1: UI, timing, map, decision buttons,
nothing reaching iRacing, NO incidents in iRacing mode, no save). FINDING:
ResultsPositions[].Incidents is NOT live for a spectator — every race log
(Algarve, Zolder, VIR, Oulton) has all counts 0 until the finish, then the
whole tally in one tick; live points need the controller PC to be session
ADMIN (as iRaceControl). So: NEW `racecontrol/backend/incidents.py` —
`OfficialIncidents` (deltas; attach = silent baseline, a session seen
starting begins at 0, seed() from the autosave) + `BehaviourDetector` (the
dashboard's speed collapse: spin / contact with the other car / stopped on
track, pit + not-racing excluded, blink = 90 s ignored, time jump resets).
IRacingSource reads them (ResultsPositions every 2 s), flags `in_replay`
(IsReplayPlaying + ReplayFrameNumEnd > 60) → RaceState logs NOTHING from
replay frames and keeps the live clock; official points within 20 s of a
detection of the same car go ON that line (MATCH_POINTS_S). Replay:
WS action `replay` {id, preroll} → `replay_event` (thread: seek, wait until
SessionNum/SessionTime landed ±3 s, 6 s, one retry; camera = current group,
Scenic → TV1; play 1x); UI ▶ Replay / -20s / LIVE, dblclick a line, violet
REPLAY bar. Autosave every 10 s to racecontrol/data/<SessionID-SubSessionID>
.json (gitignored + updater PROTECTED_DIRS; id 0-0 = local_<track>, 3 h,
same track) incl. the official baseline → restart mid-race restores log +
decisions and logs points scored while down once. /api/save downloads it.
Log keeps incidents / penalties forever (5000 lines, payload = all of them +
newest 400 others). FIXED on the way: the save helper briefly swallowed
track_payload (dashboard WS closed at once) — caught by rendering. "Total
Inc" shows `*` while no official points came. test: `cd racecontrol &&
python test_incidents.py` 53/53 (fake IR, FastAPI stubbed); dashboard run
for real on the Algarve log in Chromium. NOT tried: real iRacing (admin
points, detection, replay jump). Step 2 = chat commands into iRacing,
step 3 = decisions to the RC banner / tower + Auto Steward.

**October 26, 2026 (RC banner: iRaceControl's ATVO link is SignalR 2):**
Thomas installed iRaceControl on his PC and pointed ATVO at the stream PC.
5019's /rc/state showed what arrives: `GET /signalr/signalr/negotiate?
clientProtocol=2.1&connectionData=[{"Name":"RaceControlHub"}]&password=`,
User-Agent SignalR.Client.NetStandard 2.4.3 — ASP.NET SignalR 2, not a raw
TCP stream. The raw receiver never answered (no connection) and put the HTTP
header lines ON AIR as banners. `AtvoReceiver` is now a minimal SignalR 2
server (its own Flask app on 5020, werkzeug make_server): negotiate
(ProtocolVersion 2.1, TryWebSockets false), connect/reconnect via
serverSentEvents ("data: initialized" + {"S":1}, keep-alive 10 s) or
longPolling, start, send (form `data` = {"H","M","A","I"} → {"I"}), poll,
ping, abort. Hub method names / args UNKNOWN: `hub_text()` reads JSON
objects (_from_json) or strings (an all-caps first string = headline),
`hub_kind()` penalty / warning from method or headline; every request incl.
decoded `data` → logs/atvo_capture_<date>.log; panel lists `hub H.M` + args.
NEW rc_config.json `atvo_password` (checked on every request, 403). Public
address: NOT overlay.smarthomeworld68.fr — that is a Cloudflare tunnel
(HTTP only); needs the public IP / a DNS-only name + router forward 5020,
and then a password. test_rc_banner.py N1-N13 (real HTTP: SSE + longPolling
+ password + hub calls). NOT tried against the real .NET client yet
(installing dotnet was blocked) — Thomas's iRaceControl after the update.
FIRST REAL CONNECT (same day): SSE connect worked; its first hub call is
`RaceControlHub.GetVersion` (no args, awaits a result) → HUB_RESULTS
answers "R": "1.0.0" (guess — watch whether iRaceControl accepts it). The
POST body did NOT land in request.form on the stream PC (URL-encoded line
went on the feed) → `form_data()` decodes the raw body itself.
THEN iRaceControl CRASHED on connect: its second call is
`GetAllIncidents` (no args) and our null result killed it → `hub_result()`:
every GetAll… / Get…s call returns [] (never null).
WORKING (same day, Thomas's hosted AI session): nothing came until the
messages were ROUTED to the ATVO targets in iRaceControl's Settings › CACS
routing table (default = iRacing chat only). Real format:
`RaceControlHub.SendMessage({EntryId, IdType, SessionName, Type, Header,
Text, DisplayTime "00:00:10", Source "iRaceControl"})`, Type 1-6 = INFO,
WARNING, PENALTY, INVESTIGATION, NO FURTHER ACTION, CLEAR PENALTY (ATVO_TYPES
→ kinds message / warning / penalty / investigation / nfa / clear). One
message routed to all six targets arrived 6× → same text + entry within
ATVO_DUP_S 3 s shown once. DisplayTime → feed `secs` (banner duration).
EntryId → car via RcPoller.find_car (car number, UserID or CarIdx — which
one iRaceControl sends is NOT known yet, EntryId was empty in the test).
Banner hides the headline "RACE CONTROL" (the tag says it), warning tag
white, investigation blue. NOT seen yet: the ATVO Incidents calls
(investigation / decision / served) — they may expect results like
GetAllIncidents did; watch the panel's raw list. test_rc_banner N18-N21.
PENALTY TEST (same day): EntryId is "" even for car penalties; texts like
"DRIVE TROUGH PENALTY FOR #07 (CRO) - …", "#14 (KAE) HAVE BEEN DISQUALIFIED:
Track Limits". Still routed to all six targets, so the copy that arrived
first decided the colour (the drive-through showed as WARNING). Now copies
are GATHERED ATVO_GATHER_S 0.5 s → one banner; several Types → kind from the
text (TEXT_KINDS: penalty / warning / investigation, header of that target);
car = EntryId or the first "#NN" in the text (CAR_IN_TEXT → find_car). And
iRacing's own black flag / DQ for a car iRaceControl named in the last
STEWARD_COVERS_S 20 s is skipped (Feed._steward_said) — it came 1 s later
as a second banner. N22-N23.
Thomas's CACS routing then set to one ATVO target per type (RC message /
sequencer → INFO, noted / investigating → INVESTIGATION, race incident →
NFA, clear penalties → CLEAR, time / DT / SG / DSQ → PENALTY, the rest none —
FCY / green / red / EOL / wave around / black flag come from iRacing).
iRacing's furled black flag (WARNING, no reason) now OFF by default (AI cars:
4 in 3 min would flood a full race); rc_config "iracing_warnings": true
brings it back. F13-F14.
Restart 5019.

**October 25, 2026 (board look for PCCD + IEC too; driver championship
`5010/table/wct`):** Thomas: the new tables for Porsche Cup and IEC too, not
only WCT. The board pages were already league-neutral (5010 follows the
detected / configured league), but: (1) NEW `/table/wct` (`_table_wct`) =
the DRIVER championship: PRO / AM in Pro/Am seasons, one table per
carClassName in multiclass (IEC), else one "FAHRER" table; class positions,
"-N" behind the class leader, sub = team, excluded drivers dropped. (2)
`/lastrace/wct` in IEC showed one mixed driver list — CLS round results have
NO class and list every DRIVER (team mates share the position, their
teamName can even differ). `_class_of()` joins the standings' carClassName
(car → majority class for drivers not in the standings), `_lastrace_classes()`
= one section per class (fastest best lap first, class_colors.PALETTE),
`_merge_teams()` = one row per position, laps = SUM of the stints (each
driver's lapsBehind is his own), team name shown, NO points (CLS gives points
per driver by his own distance — teams would read 0; the team table has
them). Icons renamed "… neu" (files unchanged), loader table_wct.html,
OBS_OVERLAYS. test_wct_board +9; rendered with live CLS (IEC R2 Fuji, PCCD
5th). Restart 5010.

**October 25, 2026 (normal tower, RACE: only pos · name · brand ·
interval · pit):** Thomas: the normal tower (not /cars) shows in a race only
position, driver name, brand logo, interval and a pit column — "L5" = last
stop entered in lap 5 (StandingsPoller._last_pit_lap = CarIdxLap at pit
entry), amber "PIT" while in the pit lane (replaces the name tag). No flag,
last lap, +/- (`COMPACT`, body class `compact`; /nodelta is now the same in a
race). Pro/Am / class bar before the name kept; one-make fields still hide
the logo (?brand=1 forces). `?layout=full` = old columns; /cars unchanged;
practice / quali unchanged. FIX on the way: pit stops were never reset per
session (a practice stop would read "L3" in the race) — `_update_pit_tracking`
clears on (SessionUniqueID, SessionNum) change. CODE_VERSION "2026-10-25
tower-compact-race". Rendered with a fake 10-car field. Restart 5005.

**October 25, 2026 (NO automatic paging / scrolling on any overlay):**
Thomas: pages move only with the Stream Deck page keys (5001 /nav). Grid
5001 (/, /cars, /row), wct_board pages (5002 /wct, 5010 /lastrace/wct,
/teams/wct): AUTO_PARAM = `?auto=1` opt-in (was default on). 5010 /lastrace
(RACE 1 → 2 → COMBINED) and /table (PRO → AM): no timer unless `?cycle=N`;
they now poll /nav (`pollNav`, next / prev / first, keys too). Results 5002,
results lite 5003, classic grid: NEW `page_keys.py` KEY_SCROLL_JS (injected
via `/*__KEY_SCROLL__*/`): next = one screen down (wraps to the top), prev =
up, first = top; old scroller kept as oldAutoScroll() for `?autoscroll=1`.
Highlight reel: the grid scene sends `first` at the start and `next` halfway
(`page_nav`, GRID_NAV) since the grid no longer turns itself. Side effect:
the AI moderation's unpinned /lastrace stays on race 1 unless a key is
pressed (the Pro/Am pin still selects the class). Event queues (ticker, RC
banner, fastest lap) unchanged. page_auto key only matters with ?auto=1.
Page JS node-checked; test_highlight_runner 116, test_car_render,
test_wct_board, test_championship_rounds pass. Restart 5000, 5001, 5002,
5003, 5010.

**October 25, 2026 (grid ONE ROW at a time, 5001 `/row` + `/row/top`):** from an F1 TV
top-down grid picture: two cars = one grid row on asphalt (kerbs, chequered
line, painted grid boxes, pole left + higher), plate below each car (position,
name / team, number, brand chip, pole time or +gap / NO TIME), "STARTREIHE n"
(+ class pill in multiclass, rows per class), league logo under the pole
slot; title like /cars. 1000 × 680. BY HAND by default — the shared /nav
next / prev / first (Stream Deck "Seite" keys, → ← Home in the page); ?auto=1
?secs=8 cycles. Liveries from the shared /car route (CarRenderer). iRacing's
render server gives ONLY the side view: checked on the stream PC over the LAN
(192.168.86.138:5001/car/11.png?view=0..9 = byte-identical PNGs) — `view` is
ignored. So FOLLOW-UP same day (Thomas: "not shown from above", chose "both"):
`/row/top` = the cars DRAWN as an SVG GT3 seen from above (topCar(): body,
tyres, splitter, wing, glass, number roundel on the roof, brand chip on the
bonnet, shading) in the livery's colours from `/car/<idx>/colors` =
car_render.livery_colors(): the custom paint TGA's main colours
(paint_colors(): middle band of the texture, coarse palette, body = most
pixels, accents = VIVID colours or white only — template grey and the dull
background of unused texture areas are not livery; real Speed Monkeys BMW TGA
→ black / red / gold), else CarDesignStr. /row stays the real side render.
Loaders grid_row.html + grid_row_top.html (61), icons "Grid Reihe" / "Grid
Reihe oben", OBS_OVERLAYS rows. test_car_render 36 PASS. Restart 5001.

**October 25, 2026 (tower with car liveries `5005/cars`):** Thomas: the
liveries of 5001/cars instead of the brand logos in the tower. `/cars` and
`/cars/nodelta` (or `?cars=1`) = the same TOWER_HTML with a 70 px livery
column in the brand slot (`SHOW_CARS`, img hidden until it loads; shown in
one-make fields too — the liveries differ), tower 440 → 482 px wide
(`.tower.cars`). Server: StandingsPoller keeps `raw` DriverInfo dicts,
`/car/<idx>.png` via car_render.CarRenderer (team paint in team events,
`?view=`). car_render.trim() now crops the transparent margin around the
render (Pillow; no alpha / error = unchanged) so the car fills a 23 px row —
the grid page gets it too. Loaders standings_cars(.html) /
standings_cars_nodelta (59), icons "Tower Autos", OVERLAY_TABLE,
OBS_OVERLAYS.md. CODE_VERSION "2026-10-25 tower-cars". test_car_render.py
29/29 (+ trim, tower routes); rendered with a fake render server. NOT tried:
the real render server (margin, angle). Restart 5001 + 5005.

**October 25, 2026 (grid with car liveries `5001/cars`, DNLS look):**
Thomas sent the DNLS Community Cup "STARTING GRID" graphic (car renders
over skewed panels). NEW page GRID_CARS_HTML on the grid server (900 × 770,
?zoom=): red title bar (league · LAUF n · track, from 5010 like the WCT grid)
+ "STARTING GRID" bar (class pill in multiclass, page range), 8 per page in
two staggered columns: big position, car render, white brand chip
(car_brands, `/brand/<slug>` now on 5001 too), team / driver name, number
tag (accent pink; class colour in multiclass), LAP box = pole time, else
+gap to the class pole, NO TIME. League logo from CLS bottom left under the left
column (?logo=<url>|0, added after Thomas missed it). Same paging as the WCT grid (`/nav`, keys,
Stream Deck page keys, back to page 1 on show). `?view=` render angle
(default 1 = side; the image's 3/4 view may be another number — try 0-3
on the stream PC), `?head=` / `?accent=` hex colours, `?demo=1`. Cars from
iRacing's local render server via NEW shared `car_render.py` (CarRenderer:
params, %20, nested MX-5 carPath variants, TEAM paint car_team_<TeamID>.tga
in team events, cache, failures not cached → `/car/<idx>.png` 404 and the
image stays hidden). GridPoller keeps the raw DriverInfo dicts (`raw`) and
a `brand` per row. Loader grid_cars.html (57), icon "Grid Autos",
OVERLAY_TABLE, OBS_OVERLAYS.md. test_car_render.py 22/22; rendered against a
fake render server (real route, 10 cars). NOT tried: the real render
server's look (transparent background? angle?). Restart 5001.

**October 25, 2026 (zoom-in videos for every upcoming WCT / PCCD 6th / IEC
round):** rounds from CLS round-info → 16 new videos in zoom_videos/
(gitignored, render on Unraid, copy to the stream PC by hand): Spa
(spa_2024_grandprix), Fuji GP, Misano GP, Sebring Intl, Le Mans, Road
Atlanta, Red Bull Ring (spielberg_gp), Sachsenring, Brands Hatch GP,
Barcelona GP, Montreal, Hockenheim GP, Laguna Seca, Nürburgring GP, Summit
Point, Daytona road (+ cota_gp, thebend_gt, hungaroring from before).
make_track_zoom.py: end zoom now fits the lap into 72 % × 60 % of the frame
(capped at the old 15.7 — Hungaroring / COTA / The Bend unchanged; Le Mans
13.6, Spa 14.9). NEW tracks/summit_point.json + daytona_road.json from OSM
(Overpass from Unraid needs a User-Agent, else 406; busy = 502 → backoff;
NEVER put personal data in the UA — once sent the user's email, don't).
Daytona = the loop of oneway raceway ways whose length matches 3.56 mi
(DFS over the way graph, NASCAR chicane / pits excluded). Every final frame
checked against the satellite image. WCT R12 "SPECIAL EVENT" has no track.

**October 25, 2026 (WCT-look results: final result, last CLS round, team
championship — `wct_board.py`):** same look as the new grid, from Thomas's
WCT "RESULTS" graphic; Pro/Am and team standings asked for. NEW shared
module `wct_board.py`: BOARD_HTML (paged list: tab + title + orange rule,
section pills, rows pos · blue number box · name (+ PRO red / AM green /
class bar, purple fastest-lap dot) · car · value · "n PKT", league logo from
CLS, title auto-shrinks; ?per=10 ?secs=10 ?auto=0 ?section=PRO ?tab= ?title=
?logo= ?veil=0 ?zoom= ?debug=1 / H ?demo=1), PageNav + EventInfo (moved out
of the grid). ONE page-command source: grid 5001 /nav (now CORS open) —
every WCT page polls it (?nav= overrides), so the four grid Stream Deck keys
page whichever WCT page is on air; grid must run. Pages:
  • 5002 `/wct` (+ /wct/data, `wct_data()`): GESAMT, then PRO / AM in Pro/Am
    fields (class-relative positions, gaps, laps, fastest lap; the overall
    winner's gap None = 0); multiclass = one section per class; team event =
    team names; values "N LAPS" / +gap / "+1 LAP" / DNF / DSQ; title LAUF n
    [· RENNEN 2] from 5010.
  • 5010 `/lastrace/wct` (`_lastrace_wct`, data via api_roundinfo() so league
    detect + replayed round apply): per race ERGEBNIS / RENNEN n (+ PRO / AM),
    WERTUNG (round total, "R1 P3 · R2 P1") for two-race rounds. CLS has NO
    gaps for imported rounds → points column; in Pro/Am the race `points`
    are OVERALL, so the overall page shows none and PRO / AM show the CLASS
    points from combined[].raceN.points (AM winner 35, not 19).
  • 5010 `/teams/wct` (`_teams_wct`): CLS standings `teams` or, IEC,
    `teamClasses` per class; "TEAMWERTUNG - <league> - NACH LAUF n".
CLS league-manager v2.45.0 (DEPLOYED with Thomas's OK): /api/overlay/
standings carries `teams` + `teamClasses` (null for leagues without teams
and with ?beforeRound). Checked: WCT table = the public standings Team tab
(Speed Monkeys II 225, REVORYX 223, …), IEC LMP2 / GT3 / PCUP groups.
Loaders results_wct / lastrace_wct / teams_wct (56), icons, OVERLAY_TABLE.
test_wct_board.py 36/36 (sections, class gaps / points, teams, nav, routes);
pages rendered with live CLS data (WCT R5, PCCD R8, IEC teams) + a synthetic
final result. Restart 5001, 5002, 5010.

**October 25, 2026 (grid 5001 — WCT broadcast look, pages by Stream Deck):**
Thomas sent the WCT "STARTING GRID" graphic (LAUF 5 - CAS GT3 WCT -
INDIANAPOLIS RC) and wanted ours to look like it, paged by hand instead of
the auto-scroll. NEW default page `GRID_HTML` (900 × 620, ?zoom=): blue
STARTING GRID tab + title bar, orange rule, class tab(s) + page boxes
("1–8", "9–16") on the left, two staggered columns of 4 (pos box + name over
an orange line, blue car-number box + italic quali time / NO TIME), league
logo at the bottom from CLS `https://league.simracing-hub.com/logos/<slug>.webp`
(?logo=<url>|0), translucent veil (?veil=0). Title = "LAUF n[ · RENNEN 2] -
<league> - <TrackDisplayShortName config>" from 5010 /api/roundinfo
(`EventInfo` thread, 30 s; 5010 off = track only; ?title= / ?round= / ?race=
override). Team event → team name; multiclass → one page set per class in
grid order, numbers = CLASS grid position, class colour on the tab
(class_colors). Pages of ?per=8: AUTO every ?secs=10 s, or by hand —
`PageNav` on the server, `/nav/next|prev|first|auto|auto_on|auto_off` (Stream
Deck Website action in background; icons dashboard/page_next/_prev/_first/
_auto (+ off)); next/prev/first switch to MANUAL (kept across page reloads)
until auto; each open page polls /nav (300 ms) and applies each seq once
(commands older than the page are ignored); keys → / ← / Home / A in the page
send the same commands. Back to page 1 whenever OBS shows the source. Old
design at `/?style=classic` (GRID_HTML_CLASSIC). Rendered 1-class (WCT) +
3-class team field; nav tested in Chromium over CDP against a running server
(SD next / wrap / prev / stays manual 11 s / first / key → / key A). Highlight
reel grid scene (20 s) now shows page 1 then page 2. Restart 5001.

**October 25, 2026 (NEW overlay `iracing_rc_banner.py` — Race Control
banner, tag "rcmsg", port 5019):** Thomas: the stewards' race control
software (iRaceControl, iracecontrol.com — used for IEC and GT3 WCT) writes
violet race control messages into the iRacing chat and calls full cautions;
show them on stream. The SDK has NO chat. Two sources: (1) iRacing flags,
`FlagWatcher` (pure, edge-triggered, first tick of a session only records):
caution → "FULL COURSE YELLOW", one-to-green AFTER a caution → "SAFETY CAR IN
THIS LAP" (the pace-lap one-to-green before the start says nothing), green →
"GREEN FLAG", red; per car CarIdxSessionFlags black / DQ / furled (warning) /
repair, CarIdxPaceFlags EOL / wave around / free pass, 30 s per car + flag;
car = number, team (TeamRacing) or driver, class colour (class_colors).
iRacing does not say WHICH penalty a black flag is. (2) iRaceControl's manual:
it can send race control messages, incidents under review and penalties to
SDK Gaming or to an "ATVO Race Control Server" over TCP (Settings > CACS:
address, port, password; routing per message type). `AtvoReceiver` IS such a
server (TCP 5020, rc_config.json `atvo_port` — gitignored + PROTECTED). The
wire format is UNDOCUMENTED: every byte → logs/atvo_capture_<date>.log (JSON
lines: connect / data with hex + text / disconnect); chunks split on newline /
NUL (UTF-16 = every other byte NUL → split on "\n\0" only) or a 0.4 s pause;
`extract_text()` tries JSON (message/text/headline keys, nested), XML, plain
text, UTF-16, printable runs in binary; `readable()` (≥ 3 letters) decides
what goes on air. We never answer — if iRaceControl wants a handshake, the
capture shows it. Banner `/` (violet; FCY yellow, green, red, black for
penalties; queue, 8 s or longer for long text; ?flags=0 ?atvo=0 ?secs ?zoom
?debug ?demo), operator `/panel` (ATVO status, clients, raw bytes + what was
read, manual message POST /rc/say, test). Loader rc_banner.html (53), icon
"Race Control", OVERLAY_TABLE, all launchers, guide RACE_CONTROL.md.
test_rc_banner.py 30/30 (flag edges, extraction incl. UTF-16, real socket:
split packets, NUL, pause flush, capture log; Flask). FIRST REAL TEST:
GT3 WCT Tuesday 27.10. — get the capture log from Thomas and fit the parser.

**October 25, 2026 (IEC multiclass: class colours on tower + trackmap,
overtakes only inside a class):** NEW `class_colors.py` — one colour per car
class for both overlays: iRacing's CarClassColor, a class without one or
with a colour already taken gets the next of PALETTE (no yellow = camera
car, no PRO red / AM green); classes coloured in class-id order, pace car
skipped. Tower (5005): `_driver_map` uses it for class_color; in a
multiclass field a class-colour bar sits in the Pro/Am slot before the
name (Pro/Am wins in WCT; `?classbar=0` hides). Trackmap (5007): car dots in
the class colour (inline fill, number black / white by luminance), the
camera car KEEPS its class colour with a white ring + glow; pit = 45 %;
`/state` `classes` → key top-left (since 2026-10-29 only with `?legend=1`). Single class keeps
orange / yellow. Dashboard overtakes (5000, feeds /ticker + REGIE): rank is
now the CLASS rank and a swap only counts between cars of the same class —
a GTP lapping through GT3s was reported as an overtake, and "for P2" was
the overall place. `_ot_classes()` (DriverInfo every 5 s, monotonic).
test_overtakes +O6 / O7 (both fail on the old code). Tower + map rendered
with a 3-class field. Restart 5000, 5005, 5007.

**October 25, 2026 (tower for the first IEC TEAM race — class order,
?classrows=N):** checked before Thomas's first IEC team stream: team names
(TeamRacing → team instead of driver, no flag column, since 2026-10-10) and
the class blocks (tab + colour per class) were already there; the driver
card shows the team under the driver name. Two gaps fixed: (1) classes were
listed in CarClassID order (says nothing about speed) → `_class_order()`
ranks them by CarClassEstLapTime, FASTEST first (< 20 s ignored, no estimate
= after, id order), stable re-sort after the standings are built (race and
timed). (2) `?rows=N` cut the slower classes off in a multiclass field → NEW
`?classrows=N` = top N per class, the camera car is added to its block after
a gap. CODE_VERSION "2026-10-25 class-order". Rendered a 3-class team field.
Restart 5005.

**October 23, 2026 (tower variant without +/-):** `5005/nodelta` (or any
tower URL with `?delta=0`) = the tower without the +/- (places gained / lost
vs the grid) column — the race grid gets 82 px time columns instead. Loader
standings_nodelta.html (52), icons "Tower ohne +/-", OVERLAY_TABLE.
CODE_VERSION "2026-10-23 nodelta". Both variants rendered. Restart 5005.

**October 23, 2026 (tower: no brand logo in a one-make field):** Thomas: with
one car model (PCCD) the logo behind the flag says nothing. TOWER_HTML
`brandColumn()` — every row of the WHOLE field (before ?rows= trims it) the
same brand → column width `--bw` 0 and no logo; `?brand=1|0` forces it.
CODE_VERSION "2026-10-23 one-make-no-brand". Rendered one-make vs mixed.
Restart 5005.

**October 23, 2026 (New Race Leader + overtake ticker: never in laps 1-2):**
NEW `iracing_sdk_base.StartLapGate` — fed the leader's live progress
(lap + pct); the first racing tick anchors on floor(progress), so the gate
ends at the start of lap 3 whether iRacing counts the opening lap as 0 or 1;
first seen in lap >= 3 (started mid-race) = no gate. Leader overlay (5018):
`LeaderState.update(quiet=)` tracks a lead change silently while the gate is
on (the leader after lap 2 is not news either); /state `start_quiet`.
Dashboard: overtake records carry `start_laps` (feed + operator panel keep
them), /ticker skips them. test_race_leader.py +1 (needs an irsdk stub on
Unraid), test_overtakes ALL PASS. Restart 5000 + 5018.

**October 23, 2026 (trackmap — thicker track, bigger dots):** Thomas: car
numbers hard to read. SVG road stroke 19 → 28 (viewBox 1000×600), pit 3 → 5,
centre dash 2 px; car dots r 8 → 13, camera car 11/12 → 17, number font
10 → 15 px bold 800 (three digits still fit). Rendered on the Hungaroring.
SAME DAY, Thomas wanted the dots bigger still: r 17, camera car 22, font
19 px. Restart 5007.

**October 22, 2026 (driver + incident panels `5000/drivers/panel`, `5000/incidents/panel`):** the dashboard's other two columns as stand-alone windows; both share `PANEL_FRAME` (header, LIVE/REPLAY pill, ⛶ / F, ?zoom=). Drivers: from /telemetry, click = /switch_car, ★ uses the dashboard's own localStorage key + POST /starred (shared with the dashboard in the same browser), "★ nur" filter, Auto-Follow, Go Live. Incidents: /incidents now also carries connected / track / auto_replay / playback_mode (the logger only reads `incidents`); replay 10 / 20 s, camera, dismiss, clear, Auto-Replay. Icons driver_panel / incident_panel (new symbol "drivers"). Tested with the Flask test client + rendered at 1024 and 360 px. Restart 5000.

**October 22, 2026 (camera panel `5000/cameras/panel`):** stand-alone window like /overtakes/panel: every camera group of the loaded track as a button (custom sets bring Turn 1 … / Drone T1 …), current one cyan, NEVER_CAMS names greyed (still clickable), driver ◀ ▶, Go Live, LIVE/REPLAY + REGIE pills, ⛶ / F, ?zoom=. Buttons fire the /streamdeck GETs (cam/<id>, driver_next/prev, go_live), so a click pauses REGIE ~20 s like a key. /cameras JSON gained connected / track / playback_mode / active_driver / director / auto_camera. Icon dashboard/cam_panel.png, README + OBS_OVERLAYS + OVERLAY_TABLE rows. Tested with the Flask test client (stubbed poller) and rendered with The Bend's 35 FCP groups. Custom cams need an iRacing RESTART after copying the .cam files (Thomas). Restart 5000.

**October 21, 2026 (Abspann: PRO / AM segments showed the MIXED table):**
Thomas: the Abspann showed the overall table, not per class. Two causes in
the code / OBS: (1) `race_summary` shares the "Ergebnis" page with
results_pro / _am but had NO class → unpinned = mixed field for ~35 s; now
`ShowRunner._seg_class()` pins it to the class of the following segment on the
same page (PRO). (2) only the `_solid` pages follow the pin; a source still on
results.html / champ.html shows the mixed field under every class segment.
`ShowRunner._solid_pages()` at the show start reads GetInputSettings of
"Ergebnis" / "Meisterschaft" and, if needed, points them at
results_solid.html / champ_solid.html (same folder as the old loader, else
this install's obs_loaders/) or the /solid URL (`solid_fix`, `SOLID_PAGES`),
with a log line; "OBS prüfen" shows the same as a red hint. obs_ws
`input_settings()`, fake_obs `browsers=`. test_show_runner 131 PASS,
highlight_runner 115, obs_ws pass. Not tried against the real OBS (its
websocket was not reachable from Unraid). Restart 5000.

**October 21, 2026 (Gabi's clip played ~1.5 times — the "intro started
again" of the race night too):** read from Thomas's OBS over the LAN
(obs-websocket 192.168.86.138:4455): source "Gabi" had `looping: True`
(copied from the idle source), "Streckenvideo" looping + restart_on_activate.
A looping ffmpeg source never reports ENDED, so `_tick_segment` waited for
the duration + 3 s safety net — a ~12 s clip ran ~15-18 s. Now
`MEDIA_ONCE` {looping: False, restart_on_activate: False} is sent with every
`set_media_file` (Gabi, per-track video) and via `_play_once()` before every
track-video restart; and a clip whose cursor jumps back > 1 s (after the
1.5 s settle) counts as played once. `obs_ws.set_input_settings()`;
`set_media_file(**settings)`. fake_obs merges SetInputSettings and loops
like OBS. test_show_runner +3 (fail on the old code: 5.0 s for a 2 s clip).
Also seen: "Gabi Ruhe" plays gabi_still.mp4 (4 s, jumps at the loop), not
gabi_idle_loop.mp4 (forward + backward, seamless).

**October 21, 2026 (dashboard CamCameraState bits fixed):** the dashboard's
constants were one bit off (0x0001 "tool", 0x0002 "UI hidden", 0x0004 "auto
shot"); the real irsdk.CameraState bits are session screen 0x0001 / scenic
active 0x0002 (both read-only), cam tool 0x0004, UI hidden 0x0008, auto shot
0x0010, temp edits 0x0020, key accel 0x0040, 10x 0x0080, mouse aim 0x0100;
changeable mask 0x01FC. So MOST EXCITING really switched on the camera TOOL
(the April "HUD pops up" symptom). `_write_cam_bit()` writes only changeable
bits, always clears the cam tool, only when the bit differs;
`set_auto_camera` uses it. Hide UI (`/hide_iracing_ui`, Stream Deck
`hide_ui`) = `toggle_ui_hidden()`: flips from the REAL ui_hidden bit via
cam_set_state (spacebar only when CamCameraState is unreadable);
`_reassert_ui_hide()` sets the bit again after camera / replay actions
(idempotent, no key press, no thread). test_cam_state.py 19/19 (fake ir).
NOT tried in real iRacing from the dashboard (the same bits were verified via
highlight_runner on a real replay). Restart 5000.

**October 21, 2026 (HIGHLIGHT VIDEO from replay + race log):** NEW
`highlights.py` (pure picker) + `highlight_runner.py` (run) + dashboard page
`5000/highlights` (German operator UI) and title card `5000/highlights/card`
(loader highlight_card.html, 50 loaders; icons overlays/highlight_card +
dashboard/highlights_page, "film" glyph). Picker: running order rebuilt from
the per-second `pos` events — laps counted from the pct WRAP, not from the lap
events (iRacing logs "lap 0" both on the pace lap and after the green, and the
counter flips a tick off the pct); at the green a car with pct > 0.5 is still
behind the line (lap −1); towed cars resynced to CarIdxLap mid-lap. Passes =
swaps held 5 s between cars ≤ 0.03 laps apart, not under caution, not near
either car's pit stay, not after the checkered, churn dropped, multiclass only
inside a class (car_class, else guessed from the car name). False pit-exit
spins ("moved backwards" within 20 s of the car's pit exit) + pace car
dropped. Scenes: start / caution / restart / finish always, then pass (lead,
class lead, podium), contact / spin, fastest lap (lap end = the consecutive
crossings whose difference matches the best lap — the lap events' lap_time
lags a lap), DotD (his best non-start pass), AM class winner; greedy by prio
with a crowding penalty (180 s) and ≤ 2 per car, ≤ 5 passes / 4 incidents,
overlaps merged if ≤ 36 s. Indy 20.10. → 11 scenes / 230 s incl. caution
547.7 and restart 916. Run: per scene pause → seek (log session_num, 1.5 s
preroll) → cam (TV1-3, Gyro for fastest / DotD, never NEVER_CAMS) → play 1x →
OBS StartRecord / ResumeRecord when the replay reaches the scene → wait on
SessionTime (stall → play again, TV rotation every 12 s) → PauseRecord; at
the end StopRecord (outputPath shown). Options: card, voice (say_show with
`mood`, new optional arg), Gabi intro / outro (AvatarRenderer kind
"highlights", Moderator scene, voice fallback), record off = rehearsal.
PollerReplay turns REGIE / auto-follow / focus / auto-replays off and holds
the watchdog, restores afterwards. fake_obs.py gained Start/Pause/Resume/
Stop/GetRecordStatus. Tests: test_highlights.py 44 (real Indy log + synthetic),
test_highlight_runner.py 50 (fake replay at 20x + FakeObs: record timing per
scene, stop, already recording, stall, voice, Gabi, adapter on a fake poller);
scratchpad integ: real dashboard Flask app + fake IR + FakeObs 19/19; page and
card rendered. NOT tried: real iRacing replay, real OBS. Guide HIGHLIGHTS.md.
FOLLOW-UP same day (Thomas: grid first, then always the start): scene kind
`grid` (must, overlay "grid") = the last 15 s of the pace lap ending where the
start scene begins; the runner shows OBS source sources["grid"]
("Startaufstellung" = obs_loaders/grid.html, 5001 reads the quali result in a
replay too) on the race scene, no title card over it, Blimp / Chopper / TV2;
contiguous scenes (b.t_start − a.t_end ≤ 1 s) play on without seek or record
pause, so grid → start is one take. Missing source = hint in the log.
Tests 45 / 60, integ 19.
FIX same day (first real run: Gabi's intro LOOPED for 31 s until Thomas
stopped it): the OBS source "Gabi" loops, so it never reports ENDED. The
intro / outro now ends at the clip's duration (−0.15 s) or when the cursor
jumps back, then media_stop + hide. Test: LoopObs (test_highlight_runner 63).
SECOND FIX (next run: "ResumeRecord: 503", Gabi silent): (1) a freshly loaded
media file reports duration −1 / OPENING — the loop fix took −1 as the length
and ended the intro at once; length / cursor now count only once the clip
PLAYS. (2) OBS starts the record output ASYNCHRONOUSLY: the PauseRecord sent
right after StartRecord was accepted but lost, so ResumeRecord got 503 (not
paused). Every record step now waits for GetRecordStatus (active / paused),
and a resume when OBS is not paused just goes on. Test RealishObs (start lag,
lost pause, OPENING −1) fails on the old code exactly like the real run; 67.
THIRD (Thomas: "why do we lose the rendered video every time — too
expensive"): avatar_sync.AvatarRenderer now REUSES the file on disk
(avatar/out/<kind>_<seg>_<sha1 of the text>.mp4) — ensure() adopts it
instead of ordering, get() finds it after a restart; download goes to .part
then os.replace. Highlights page looks for saved videos after "Szenen
wählen" / a text edit (POST /highlights/avatar {check}). Helps the show's
intro / outro / goodbye too. Test 73 (restart = new renderer: no order).
FOURTH (third real run, "nur Standbilder", tower said PRACTICE): this PC IS
the stream PC (dashboard on localhost:5000, read with pyirsdk directly).
(1) A replay_search_session_time into ANOTHER session takes iRacing ~2.5 s;
our play + cam_switch 0.3 s later left the replay stuck in practice (speed 1,
time frozen) — measured by hand: the same jump alone lands. PollerReplay.seek
now waits until SessionNum / SessionTime are there (±3 s, 6 s, one retry),
returns False otherwise -> run stops with a hint. Checked against the real
replay: 5 jumps practice <-> race land, rehearsal of grid + start + caution
plays. (2) Thomas's OBS profile: Simple output, RecQuality "Stream" (= same as
stream) — OBS cannot PAUSE then (PauseRecord accepted, nothing happens), so
the seeks were recorded. The run reads the profile (GetProfileParameter)
before StartRecord and stops with a German hint (also Advanced + "use stream
encoder"). Tests 79.
FIFTH (Thomas: always check the iRacing UI is hidden): PollerReplay reads
CamCameraState and sets ui_hidden (0x0008) + clears iRacing's own auto shot
(0x0010) via cam_set_state only when needed — on prepare, after every switch /
play, every 1 s in hold(); restore() puts the auto shot back. No spacebar any
more (a toggle can un-hide). Real replay: UI shown + auto shot on before the
rehearsal -> 0 of 454 samples with the UI shown, auto shot back after. FOUND:
the DASHBOARD's own constants are WRONG — CAM_UI_HIDDEN 0x0002 is
"scenic active" (read-only), CAM_USE_AUTO_SHOT 0x0004 is "cam tool active"
(irsdk CameraState), so its MOST EXCITING button sets the cam tool (the April
"HUD pops up" symptom). Fixed separately (cb5a126). Tests 83.
SIXTH (after the update "Start" stayed grey): the local install's logs/ had
the real race plus fragments the RACE LOGGER wrote while the replay played
(every seek starts a new file; they even carry an official session_end) — the
newest fragment was preselected, 0 scenes. list_logs now ranks WHOLE races
first (session_end + green flag + >= 50 lap events), options show date, track,
"Rennen komplett" / "Teilstück", laps; 0 scenes -> a German hint. Open: the
logger writing fragments during replays at all (not changed).
SEVENTH (Gabi silent again, idle loop only): measured on Thomas's real OBS —
after TriggerMediaInputAction STOP, a RESTART of the SAME file reports PLAYING
with the cursor stuck at 0 (the first run of the day worked: new file).
_play_talk(): hide, SetInputSettings local_file "" then the file, show
(restart_on_activate plays it), RESTART only after 1.2 s without a FRESH
cursor (0.05-1.5 s and rising — the first readings can carry the old run's
cursor), give up after 6 s (-> voice). Played = cursor moved; never stopped,
only hidden. Real OBS: 3 runs in a row incl. a STOP between, 7.0 / 7.0 /
8.1 s each. StickyObs test fails on the old code like the real run; 86.
EIGHTH (first good reel, 4:15; Thomas: DotD came "mid-race", cuts choppy):
(1) select() moves the dotd scene right before "finish". (2) FADES: OBS's own
fade transition through an empty scene "Highlight Schwarz" (CreateScene if
missing): scene end -> program black (recorded) -> PauseRecord -> seek ->
ResumeRecord on black -> program race (fade in, the wait ends fade_s earlier so
it is done at t_start); Moderator <-> race the same; starts and ends on black,
program back to race and OBS's transition / duration restored afterwards;
fade_s 0.6, page checkbox "Blenden", no fade transition in OBS -> hard cuts.
(3) Gyro dropped from the plans (a dark cockpit in the reel). Real test on
Thomas's OBS + replay (2 scenes, recorded): luma 57 -> 15 -> 75 across the
cut, fade in / out at start / end. Tests 47 / 96.
NINTH (Thomas: "always a short black screen between the scenes"; grid graphic
"already scrolled too far"): (1) CROSSFADE OUT OF A STILL instead of the black
dip — _leave(): SaveSourceScreenshot of the program scene (jpg, canvas size
from GetVideoSettings, alternating temp files cas_highlight_still_0/1.jpg —
image-source cache), into image source "Highlight Standbild" (CreateInput in
"Highlight Schwarz" if missing), CUT to it (same picture), record pauses,
replay jumps, record resumes on the still, FADE into the new scene. Only the
very start / end fade from / to black (still hidden); a failed screenshot dips
to black. (2) iracing_grid.py: the CSS ticker looped from page load, so when
OBS showed the source it was mid-scroll. Now Web Animations from JS: hold the
top HOLD_S (?hold=, default 10 s), scroll, hold the bottom 4 s, snap back;
restarts at the top on obsSourceVisibleChanged / ActiveChanged /
visibilitychange; hover pauses. Highlight grid scene 15 -> 20 s. Checked in
the browser with the real grid (still at 6 s, scrolling at 11 s, reset on the
visibility event). Tests 47 / 104. The real-OBS check of the still crossfade
is NOT done yet — OBS was recording (Thomas) when I tried; nothing touched.
TENTH (Thomas: after the finish the final result PRO / AM and the championship
PRO / AM before Gabi's outro): _tables() after the scenes — Moderator scene,
Abspann sources "Ergebnis" (results_solid) / "Meisterschaft" (champ_solid),
pages results PRO, AM, standings PRO, AM (opts proam from the pick info, else
one page each), class pinned via `pin` = show_runner.pin_class (5010
/api/prerace_pin; 5002 /solid follows it), while the record is PAUSED: pin,
show, 2 s for the page, record on, crossfade in; table_s 10; spoken line per
page with voice; pages hidden + pin released at start and end; the last page
fades to black when no outro follows. Page checkbox "Ergebnis + Tabelle".
Also fixed: _overlay's "source missing" hint fired always (_obs returns None
on success too). Real OBS + replay test (finish + 4 pages, recorded): pages
right (Zocher / Kuebler, provisional PRO / AM tables), crossfades, no black.
The still crossfade between race scenes was checked the same way. NOTE: the
Moderator scene holds "Ergebnis" TWICE (items 66, 75); GetSceneItemId takes
the first. Tests 115.
Restart 5000 + 5001.

**October 21, 2026 (launcher: "Update & restart" button missing unless the
window was dragged very wide):** the banner packed its LABEL first and the
buttons after; pack hands out space in packing order, and the commit list
(long subjects, no wraplength) claimed the whole width — at 780 px both
buttons were not even mapped (measured with real Tk on the old code). Now the
buttons are packed first (anchor n), the label wraps to the room left of them
(`_wrap_banner` on <Configure>), and each commit line is cut at
`BANNER_LINE_MAX` 110 chars. Checked at 780 px: both buttons inside the
window, text wrapped.

**October 21, 2026 (Andreas's feedback on the WCT Indianapolis show):**
(1) "Gabi vanished after the greeting" — the recording shows the EMPTY studio
during every page: the idle loop was not on, and the code never touched it.
New source key `avatar_idle` ("Gabi Ruhe"): switched ON at every start of
moderation / Abspann (`ShowRunner.start`), listed (optional) in "OBS prüfen".
(2) Tables should sit BESIDE her, she stays in the picture while the voice
reads → the Abspann `_solid` pages are now an OPAQUE table on a TRANSPARENT
page (reverses the 2026-10-20 full-screen dark page, whose purpose was to
hide her); placement is OBS layout (SENDEABLAUF.md: pages on one side, not
over the centre). (3) "Chase camera looks unrealistic" → `NEVER_CAMS` +=
"CHASE" (Chase / Far Chase / Rear Chase, also as the fallback), CAMS lists
without chase. (4) "tables didn't always match, overall standings": the
pre-race pages in the recording all matched (PRO/AM checked frame by frame) —
probably the Abspann sources still pointing at results.html / champ.html,
which do not follow the class pin; told Thomas to use the _solid loaders.
Tests: test_race_director +3, test_show_runner +1 (idle on to the end),
both pages checked in Edge (page transparent, table rgb(20,20,28)).
Restart 5000, 5002, 5010.
FOLLOW-UP same day (Gabi's Abspann goodbye was ordered only at the Abspann
START — sync.so ~2 min vs ~2.5-3 min of Abspann before it, often too late):
dashboard `_maybe_post_show` calls `post_show.prepare_early()` at the
chequered flag (thread): a full Abspann script ahead of time → the goodbye's
video is ordered during post_delay_s; `early_goodbye` (text, clock) is kept
and `prepare()` at the start reuses it word for word for `EARLY_GOODBYE_S`
(30 min), so AvatarRenderer.ensure sees the same text (no second render, no
second payment). test_show_runner +4 (123 PASS; the 2 Windows-only fails as
before).

**October 20, 2026, after the race (caution + voice mood + pit-exit
false spins; WCT Indianapolis):** (1) every pit exit was "moved backwards
75 % of lap" → lost_control + REGIE replay: the lap-regression detector kept
the pit-ENTRY pct while the car was in the pits (pit lane crosses the S/F
line) — `_prev_lap_pct` is now dropped while in the pits / out of the world
(test_pit_exit.py, fails on the old code). (2) FULL-COURSE YELLOW was not
handled at all: dashboard `_caution_phase()` (SessionFlags 0x4000/0x8000 =
caution, 0x0200 one-to-green — only after a caution, it also flies before
the start) → `RaceDirector.tick(caution=)`: each phase change (start /
one_to_green / restart) = log entry kind "caution" + cut to the leader (only
logged while the operator has the camera); under yellow no battles, leader
prio 65; no overtake replays under yellow nor RESTART_SKIP_LAPS 0.5 after
the green; dashboard overtake detection pauses (ranks follow silently).
Commentator: caution_start / _one_to_green / _restart templates, caution
lines are never replaced by the next picture, prompt explains phases.
(3) VOICE MOOD: every commentary line has `mood` (race_commentator.mood_of:
hype = incident / seen / overtake replay / new pole / faster attack /
caution start + restart, calm = pit / one_to_green, else normal; Claude gets
`stimmung`); race_voice `MOODS` → edge-tts rate / pitch / volume per line
(hype +16 % / +10 Hz / +15 %, calm −8 % / −3 Hz), `"moods"` override in
commentary_config.json; engines without pitch/volume fall back to rate.
Checked with real edge-tts (same sentence: hype 3.7 s, normal 4.25 s, calm
4.56 s). test_caution.py 28/28 with tonight's flag timeline (compressed).
Video review (OBS recording from 19:06): moderation run 2 clean — greeting
once, hand-over is its own clip (audio cross-check vs the greeting: no
match). OPEN: Thomas saw "the intro started again before REGIE took over" —
not visible in the recording (it starts 19:06:03, after run 1 was stopped by
hand at 19:06:00); ask him for the moment in the video.
Restart 5000.

**October 20, 2026, evening (FRAG CLAUDE — voice Q&A co-commentator, for
streams Thomas / Andreas commentate THEMSELVES, not REGIE):** NEW `race_qa.py`
(`RaceQA`, pure logic, mic / STT / API injectable). Stream Deck
`/streamdeck/ask` (toggle: press = listen, press again = answer, auto-stop
`qa_max_record_s` 20 s; `ask_start` / `ask_stop` for hold plugins,
`ask_cancel`) → `MicRecorder` (sounddevice, 16 kHz mono, `qa_mic` = name part
or index) → `WhisperSTT` (faster-whisper `qa_stt_model` "small", int8 CPU,
German, `initial_prompt` = track + the session's driver names) → Claude
Messages API via urllib (`qa_model` claude-haiku-5-5, thinking disabled with
the commentator's retry-without fallback, ≤ 6 tool rounds, pause_turn
continued, refusal = error) with tools live_session (dashboard
`_qa_session()`), live_standings 5005, race_results 5002, championship 5010
/api/state, round_info + league_table (5010 /api/roundinfo split into "info"
and "standings" — together they were cut off), track_notes
(show_runner.find_notes) and Anthropic web_search (`web_search_20250305` for
Haiku, `_20260209` otherwise; `qa_web_search`). Prompt: answer ONLY from tool
results, else say it is not known; 1-3 spoken German sentences. Overlay JSON
goes through `compact()` (drops colours / urls / ids / solo team = name,
rounds floats, lists ≤ 40, depth ≤ 12 — depth 6 had turned the CLS race rows
into "…") and a 24 000-char cap; every real 5005/5002/5010 payload of the
WCT Indianapolis evening fits uncut. Answer → `Commentator.say_show(text,
"frage")` = queued voice line (played in full by /commentary/voice, before
REGIE lines). Operator page `5000/ask` (state, Q&A list with sources and
seconds, typed question, warns when no voice page listens; opening it preloads
the speech model — first run downloads ~480 MB). Icons dashboard/ask.png
(mic) + ask_page.png (rendered with local Edge — the claude-agent container's
chromium hangs: ~3300 zombie chromium processes, nothing reaps them).
requirements + faster-whisper, sounddevice. Guide FRAG_CLAUDE.md (German).
Tests: test_race_qa.py 60/60 (fake recorder / STT / API: tool loop,
compaction, split, errors, toggle / hold / too short / auto stop / cancel,
real Commentator queue). Scratchpad venv: real faster-whisper on three
edge-tts German questions = word-perfect incl. "Remo Cursio und Bastian
Kübler", ~1 s each; dashboard Flask test client (/ask, /ask/text,
/ask/status, /streamdeck/ask twice, /commentary carries the queued answer)
all pass; real mic capture (Elgato Wave XLR) works; /ask rendered.
NOT tried: a real Claude answer (no key on this PC), sound through OBS.
Trigger is the Stream Deck BUTTON only — Thomas decided: no wake word,
not even later; a few seconds' wait is fine. Not built: Andreas via Discord.
FOLLOW-UP 2026-10-22 (first real answers, no iRacing running): "the WCT race
at Indianapolis" got PCCD data — round_info / league_table only read 5010,
which knows ONE league (manual mode, pinned to PCCD 5th; auto-detect needs a
grid). Both now take optional `liga` (CLS slug) + `runde` → CLS
/api/overlay/round-info and /standings directly (`beforeRound` = runde + 1),
new tool `cls_leagues` (/api/overlay/leagues), `qa_cls_url`; runde without
liga = the league 5010 is on; nothing given = the old 5010 path. Prompt: no
league named = the session's league, else cls_leagues → liga, say which
league. test_race_qa.py 73/73; checked against live CLS (WCT R5 Indy, R3
Zandvoort, table after R4 — all uncut).
FOLLOW-UP 2026-10-22 (Thomas: "a sign when the answer is there, then I play
it"): HOLD mode, `qa_hold` default true — a finished answer becomes state
"ready" (`_pending`, not spoken); the SAME ask key (toggle) or
`/streamdeck/ask_play` / POST /ask/play speaks it, ask_cancel drops it,
`qa_hold_max_s` 300 expires it; while one waits, a new question is refused
("Antwort wartet"). Status "speaking" until the voice page reports the line
played (`is_done` = Commentator.is_done, SPEAKING_MAX_S 90 fallback). NEW OBS
page `5000/ask/overlay` (ASK_OVERLAY_HTML: HÖRT ZU red / DENKT NACH amber /
ANTWORT BEREIT green pulsing / ANTWORTET cyan / "KEINE ANTWORT" 6 s on an
error, hidden when idle; ?text=1 question + answer for the streamer, ?zoom,
?debug=1 / H, ?demo=1); loader ask_status.html (51), icon overlays(_off)/
ask_status ("mic", live), OVERLAY_TABLE; /ask shows the waiting answer with
▶ Abspielen / Verwerfen. test_race_qa.py 88/88 (old checks with qa_hold off,
new block 19); scratchpad: real dashboard Flask app — voice question → ready,
nothing queued, same key plays, speaking until /commentary/played, cancel,
ask_play — 12/12; overlay demo rendered. Icons rendered with local CHROME:
Edge had a pending update (two versions side by side) and its headless
wrote no file. make_obs_loaders.py regenerates delta_own.html WITHOUT the
committed comments (old drift) — restored it with git checkout.
FOLLOW-UP 2026-10-23 (Hungaroring PCCD stream 22.10., checked against Thomas's
Twitch transcript — the Q&A was only in memory then): (1) the F1 question was
read out with an ENGLISH plan first ("The user asks about Formel 1 … the track
notes contain F1 history, but …") — with web_search (a SERVER tool, run inside
the same reply) Claude writes text BEFORE the search, and ask_claude joined all
text blocks. Now `final_text()` = only the text after the last tool block;
`looks_english()` (stop-word count) → one extra call without tools
(`GERMAN_PROMPT`, `_to_german`), source "übersetzt"; a failed rewrite keeps the
original. (2) "wie viele Rennen ist der André gefahren" → it took the CAMERA car
(Remo) and said so aloud; (3) "Tabelle Platz 3 und 4" → live order. Prompt now:
always German (translate sources), no text before / between tool calls and no
explaining, names → closest driver in field / table, NEVER just the camera car
unless asked, "Tabelle / Meisterschaft / Punkte" = championship, live_standings
only for "im Rennen / gerade / aktuell". (4) LOG: `logs/frag_claude_<date>.jsonl`
(`qa_log`, `LOG_DIR`), events answer / played / discarded / expired with
question, answer, tools, error, took_s, model; a log error never stops an
answer; tests point LOG_DIR at a temp dir. test_race_qa.py 117/117 (block 20 =
the three live cases, 21 = log); real dashboard Flask run 12/12.
Also seen in the transcript (NOT changed): Gabi's greeting replayed when Thomas
switched back to the Moderator scene at the end — the OBS source "Gabi" restarts
its last clip on scene activation (restart_on_activate on the source he used).

**October 20, 2026, later (Abspann PRO / AM pictures, WCT GT3 race at
Indianapolis that evening):** the Abspann talked per class but showed mixed
pages. Now `available_post_segments` splits `results` → results_pro/_am and
`standings_after` → standings_after_pro/_am (`POST_PROAM_SPLIT`, own
POST_BRIEFs + templates) when the facts carry both classes; they share the
plain sources Ergebnis / Meisterschaft (stay up PRO → AM) and pin the class
via 5010 /api/prerace_pin. 5002 now has a ProAmRoster (row `proam`,
`proam_active`, `/proam`, `/pin` = proxy of the 5010 pin) and /solid shows
only the pinned class: positions, gaps (to the class winner, laps too), grid
change (class start order) and FL inside the class. 5010 /overlay/solid
follows the pin too (class ranks before/after + Δ inside the class, always
view B). BUG FIXED on the way: build_post_facts read `rows` but 5002 serves
`results` — the Abspann never had the final result (segment dropped).
test_show_runner.py 118 PASS (the 2 Windows-only fails — track video path,
avatar — fail on HEAD too); both pages run for real (stubbed SDK) with a
fake WCT field, PRO and AM checked by text + screenshot. Minor: the champ
sub-line "Race P4" is still the OVERALL race position. Restart 5000, 5002,
5010.

**October 20, 2026 (Abspann sources opaque, for Gabi):** the Abspann shows
`Ergebnis` / `Meisterschaft` on the Moderator scene OVER the Gabi idle loop,
and the normal results (5002, stream mode = transparent, remembered in
localStorage) and champ (5010 /overlay, translucent 540 px panel) let her
show through while the voice speaks. NEW opaque variants: `5002/solid` and
`5010/overlay/solid` (`?solid=1` works too; champ centred + `?zoom=`,
default 1.6), loaders results_solid.html / champ_solid.html (48),
SENDEABLAUF.md points the two Abspann sources at them, OBS_OVERLAYS.md +
OVERLAY_TABLE.md rows. Checked in Edge: both pages fully opaque.
Icons rendered in the container (see below).
WORKING FROM THE WINDOWS PC (Y: = the Unraid share): .git/objects and
streamdeck_icons/ are NOT writable from there, there are no GitHub
credentials, the Unraid HOST has no browser. Commit, push and icon renders go
through `ssh unraid` → `docker exec -e HOME=/home/agent claude-agent …`
(chromium + gh login, git with `-c safe.directory=*` and
`-c credential.helper='!gh auth git-credential'`). Windows-side git reads
need `GIT_CONFIG_NOSYSTEM=1` (corrupt system gitconfig on that PC).

**October 15, 2026 (LIVE pit-stop tire wear for the stream, WCT GT3 — and
RaceLogger v1.4.0 found broken):** Thomas wants an overlay after a stop
showing each driver's tire wear. Tread is LOCAL-CAR-ONLY in the SDK, so the
broadcast PC can't read it → chain (Thomas chose "live"): driver's logger →
CLS → overlay.
- **v1.4.0 (released 15.10. 11:42 as "Latest") had LOST the CLS upload.**
  626c6ff (10.10.) committed an OLD local logger and silently dropped: CLS
  upload + `/league` page, the frozen-exe `HERE` (logs went to the PyInstaller
  temp dir!), `weather`, `driver_change`, dashboard-free SDK `incident`s.
  Restored from 626c6ff^ (v1.3.1) hunk by hunk, keeping `inc` and the tire
  work; `diff` vs v1.3.1 now shows only the tire/inc changes. LESSON: before
  committing a big logger diff, `git diff` it against HEAD and read every
  `-` block — and check `git log -S` for functions that vanish.
- Logger (LOGGER_VERSION 1.4.1): when the LOCAL car leaves pit road and the
  pit event has `tires`, `cls_send_live_async()` POSTs `{v, session:
  {subsession_id, session_id, session_num, session_name, track}, driver:
  {cust_id}, event}` (no raw samples) to `/api/race-log/live`; 2 tries, 10 s,
  thread. `league_manager.json` gains `live` (default true), checkbox + last
  status on `/league`. session meta now carries `subsession_id` /
  `session_id` (WeekendInfo.SubSessionID = the id ALL PCs of a hosted race
  share — telemetry SessionUniqueID is not, see Oct 8).
- CLS (league-manager, `src/lib/live-pit-store.ts`, `api/race-log/live`,
  `api/overlay/pit-tires?subsession=&after=&hours=`): IN MEMORY (one Next
  process on Hetzner), 6 h, 3000 stops, 60/driver/hour, validated field by
  field, a re-sent stop replaces its copy with a new id. No schema change.
- Overlay on the standings server (5005): `cls_pit_tires.PitTireFeed` polls
  CLS every 4 s ONLY while a page asked in the last 60 s, per subsession
  (new race = empty list), `after=` incremental; `join_with_field()` matches
  stops to tower rows by customer id, car number fallback; `verdict()` →
  "NEW 4 TIRES" / "USED FRONTS" / "NO TIRE CHANGE" (single reading: label
  only when no tire was ORDERED, else blank — never "?" on stream).
  `/pittires` card (newest stop ~15 s, history skipped on load, queue, 8 s
  each when 3+ wait; `?last=1` pins the newest), `/pittires/list` (all stops,
  tread that came off per corner), `/pittires/data`. Loaders pittires.html /
  pittires_list.html (42), icon "tire", OVERLAY_TABLE, OBS_OVERLAYS.md,
  guide REIFEN_LIVE.md. CODE_VERSION "2026-10-15 pittires".
- Tests: test_cls_live.py 32 (live send + every restored v1.3.1 feature),
  test_pit_tires.py 25, CLS store 15 (scratchpad, transpiled TS),
  test_tire_capture 44, test_inc_points 29. End-to-end in the scratchpad:
  three simulated driver PCs (real logger code, real requests) → fake CLS
  running the REAL transpiled store → standings Flask + real urllib feed →
  Chromium screenshots of card + list; a wrong token is refused.
  NOT tried: a real stop with the new .exe, the deployed CLS route.

**October 15, 2026 (race logger — tire condition, setup name, fresh-or-used,
for Johann):** Johann Solowej asked whether the logger could record the tire
condition ("available when the car is in the box"), the name of the setup being
driven, and whether the car left the box on fresh or used rubber. All three are
in the SDK, all three are LOCAL-CAR-ONLY (no CarIdx array, same limitation as
tire temps and fuel) — which is fine now that drivers run `RaceLogger.exe`
themselves.

- **Tread**: `LFwearL/M/R` & co, fraction remaining → logged as percent. iRacing
  only REFRESHES these while the car sits in the pit box, so there is nothing to
  sample per lap; `_sample_local_pit_state()` runs every tick, clears its buffers
  on pit entry and keeps every CHANGED reading of the stop in a
  `deque(maxlen=WEAR_SAMPLES_MAX)`.
- **Fresh or used**: the most-worn reading of a stop is what came off, the last
  reading is what went out; a corner whose tread JUMPED UP got a different set
  (`tires_changed`), and `left_on_fresh` is true when every changed corner is at
  `WEAR_FRESH_MIN` (98 %) or above — a remounted scuffed set shows as changed but
  not fresh, which is exactly the distinction Johann wanted. When iRacing
  published only one reading we emit `changed: null` instead of guessing, and
  `service` (from `PitSvFlags`, sampled DURING the stop because iRacing clears
  the flags as the crew works) still says what was ordered. The raw
  `tire_wear_samples` go into the event so the rule can be sharpened later
  without shipping a new .exe.
- **Setup**: `DriverInfo.DriverSetupName` / `DriverSetupIsModified` /
  `DriverSetupLoadTypeName` / `DriverSetupPassedTech` + `CarSetup.UpdateCount` —
  session string, not telemetry. New `setup` event on first sight and on every
  change (`modified` matters: the file name lies the moment someone turns a click
  in the garage). One event per race, one per iteration in practice.
- **Found on the way**: the per-lap `tire_temps` were reading `LFtempL/M/R`,
  which is the PIT-BOX measurement — frozen during a run, so every lap of a stint
  carried the same numbers. Left in place (the live monitor's colour thresholds
  are tuned for it) but now tagged `source: "pit_box"`, with the live carcass
  temps added alongside as `tire_temps_live` (`LFtempCL/CM/CR`). Decide later
  whether the lap field should switch over — the first uploaded log will have
  both side by side.
- CLS needs no change to keep accepting the logs: `dotd-log.ts` and
  `race-log-field.ts` read named keys and ignore unknown event types.
- **Unverifiable here**: no sim and no pyirsdk on Unraid, and no log in `logs/`
  has local-car data (Thomas always spectates). So the var NAMES are from the
  SDK docs, every read is guarded, and `check_sdk.py` got a "Tire condition +
  setup" section that prints each variable and `MISSING` where nothing comes —
  run it FROM THE PIT STALL to confirm.
- Test: `python3 test_tire_capture.py` — 44 checks, stubbed irsdk/flask, scripted
  stops (four new tires / scuffed set / fuel only / nothing changed / spectated
  car / pit-lane noise / two stops in one race / setup changes).

**October 14, 2026 (REGIE — race director, step 1 of the AI broadcast idea):**
Andreas's idea: a live AI commentator (voice, maybe an avatar) for the small
cups (CC, SFL, fun league), with automatic camera work; ~10 s stream delay is
acceptable (OBS delay hides the commentary latency), practise on REPLAYS.
Plan: 1 director → 2 commentary as a text ticker (Claude on the director log
+ events) → 3 voice (TTS into OBS) → 4 avatar. STEP 1 DONE: NEW
`race_director.py` (pure logic, `RaceDirector.tick()`), used by the dashboard
(5000) as camera mode **REGIE** (button, POST/GET `/director`, Stream Deck
`/streamdeck/toggle_director`). Candidates each tick with priorities:
incident 100 (fresh collision/lost_control, 10 s, TV cams) > overtake 70
(minus position) > same-CLASS battle 60 (gap ≤ 1.0 s to enter, 1.5 s to keep,
front weighs more) > top-5 pit stop 45 > class leaders 35 > the field 20
(least screen time first). Pacing: MIN_SHOT 8 s (only a +25 stronger subject
interrupts), MAX_SHOT per kind (battle 40, leader 16, field 12 …), then the
best OTHER subject; a car shown in the last 45 s scores −25; camera group
rotates inside long shots every 14 s (not right before a cut) and each new
shot of a kind starts on the next camera of its list (CAMS, by name, Scenic
never). Every decision is logged WITH its reason (`director.log`, `/director`)
— that log is the input for step 2. Runs live AND during a 1x replay; holds
still when paused / FF / slow-mo and during the dashboard's own replays
(`_own_replay_until`). Operator clicks (camera group, car, Stream Deck cam /
driver keys) pause it 20 s; REGIE and the other auto modes exclude each other;
the lost-camera watchdog leaves it alone. Driver rows gained class_id /
class_name / est_lap (CarClassEstLapTime) for per-class gaps.
test_race_director.py 27/27; integration with the real poller + fake SDK
(scratchpad) 10/10; dashboard JS node-checked, button + "why" line rendered.
NOT yet tried with real iRacing — first test: a recorded replay at 1x.
FOLLOW-UP same day, first real test (Motegi WCT replay, watched live via
192.168.86.138:5000/director): (1) 5 cuts in 3 s at the green — at the S/F
line CarIdxLapDistPct wraps to 0 a few ticks BEFORE CarIdxLap counts up, so
crossing cars dropped a lap, the battle on air looked weak (prio fell) and
anything "+25 stronger" cut it. Fix: dashboard `_director_progress()` adds
the missing lap; before MIN_SHOT the margin is measured against the shot's
`cut_prio`. (2) only "0.0s" battles — CarClassEstLapTime AND EstLapTime read
1.0 in that replay. Values < 20 s are now ignored (`_plausible_lap`, fallback
median best lap / 100 s), in the dashboard and in the director.
test_race_director.py +2 (both fail on the old code).
SECOND TEST same evening (Thomas: "too many cuts from one to the next, you
lose the overview" — it hopped battle → battle every 8 s, leader never shown):
battles now hold ≥ 15 s, leader ≥ 10 s (`MIN_BY_KIND`); before that only
EVENTS (incident / overtake, +25) interrupt; after it another subject needs
`SWITCH_MARGIN` (+10), not just "one place further up"; a battle whose gap
jitters out stays on for `GONE_GRACE_S` 3 s; the class leader becomes due
(`LEADER_DUE_PRIO` 75) after `LEADER_EVERY_S` 120 s off screen — being the
car AHEAD in a shown battle counts as on screen (both pair cars go into
last_shown, which also spreads screen time). Simulated tight pack (seed 9):
median shot 9 s → 28 s, cuts in 10 min 43 → 22. Test 13 (fails on old code).
THIRD (Thomas, same evening — events): contacts / spins are ALWAYS shown, as
a REPLAY, unless they were in the live picture already; overtakes are ignored
in the first 2 laps after the green (`OVERTAKE_SKIP_LAPS`, leader progress vs
the first racing tick), afterwards replayed the same way. "In the picture" =
camera car, both cars of the battle on air, or within `VISIBLE_S` 1.5 s of
the camera car, looked up over `SEEN_WINDOW_S` (overtake 10 s, collision 15 s
— detection lags the event). Seen live → only a "seen" log line. Replays at
most every `REPLAY_GAP_S` (incident 15 s, overtake 45 s; a blocked incident is
cut to LIVE instead, a blocked overtake dropped). Overtakes are no longer cut
to live at all. The director returns a Shot kind "replay" → dashboard
`_director_replay()` → the existing `replay_5s_of_car` (same rewind/buildup
as auto-replay, marks the feed entry replayed); the shot on air gets
`REPLAY_HOLD_S` 16 s afresh afterwards. While REGIE is on the dashboard's own
auto-replay toggles do nothing (`_try_auto_replay` returns). `replay_5s_of_car`
now returns to WHERE IT WAS (+ elapsed) when started from a recording
(ReplayFrameNumEnd > 60 = behind the live tip) instead of jumping to the end
of the recording — matters for REGIE practice on replays; live unchanged.
test_race_director.py 38/38; scratchpad integ2.py (real poller + fake SDK:
replay call, mark replayed, auto-replay off, return path) 7/7.
FOURTH run: random back-markers shown as "leader: … (P1)" and NO overtake
replay at all. iRacing's S/F glitch goes BOTH ways — besides the pct-wraps-
first lag, the lap can count up while the pct still reads 0.99 (car jumps a
lap AHEAD → leader for a tick → "leader due" cut to it; at the green it also
set `green_lead` a lap too high, so laps 1-3 were skipped instead of 1-2).
`_director_progress()` now takes, near the line (pct < 0.1 or > 0.9), whichever
of lap-1 / lap / lap+1 (+pct) is within 0.05 of the car's previous progress;
anything farther is a replay seek and is taken as is.
Then Thomas: overtakes from LAP 2 on → `OVERTAKE_SKIP_LAPS` 2.0 → 1.0.
FIFTH run: two overtake replays worked. Thomas unsure he SAW the Zocher /
Richter pass that REGIE logged as "seen" — the camera was on the P5 battle
(Chase cam), the pass 1-2 s ahead. Now `VISIBLE_S` 0.8 and an overtake counts
as seen only if BOTH cars were in the picture together. Also: every replay
seeks back > 5 s → the dashboard's `_reset_detection_state` wipes the feeds
and the detectors then re-report what happens in the replayed footage; such
events (wall_clock inside `_own_replay_from`.._until) are not handed to the
director, and the director drops a re-report of the same (type, car, passed
car) within `SAME_EVENT_S` 90 s. (The feed wipe itself is old behaviour of
every replay, also the manual button — not changed.)
SIXTH (Thomas: return from a replay not smooth — driver / camera changed too
soon after it): the return went to the car iRacing showed when the replay
STARTED (could differ from the director's shot → instant re-assert cut) and
the director resumed 2.5 s later with its shot clock already run down.
Now `replay_5s_of_car(return_car=, return_group=, on_return=)`: REGIE replays
return to the director's current car + camera group; `_after_director_replay`
keeps the director still `REPLAY_SETTLE_S` 5 s more (events reported in that
window are ignored too) and `director.after_replay(resume)` restarts the shot
on air at the resume moment (full MIN before a cut, CAM_VARY_EVERY before a
camera change; the replay gap counts from there). integ2.py 10/10.
After that run Thomas: "looks good" → STEP 2 DONE same evening: NEW
`race_commentator.py` (`Commentator`): each new director log entry (battle,
leader, field, pit, live incident, replay, "seen") → its facts → one short
German line. TEMPLATES always (no cost, offline, variety, a template needing
a missing fact is skipped — never "None" / "?"); CLAUDE when a key is set
(`commentary_config.json` {mode auto|template, api_key, model
claude-haiku-5-5, timeout_s} — gitignored + updater PROTECTED — or env
ANTHROPIC_API_KEY): Messages API via urllib in a background thread, system
prompt = one German sentence ≤ 18 words from the given facts only (no
reasons / damage / lap times), recent lines passed to avoid repetition;
backlog collapsed to the newest picture; slower than 8 s or any error →
template line, error in `/commentary` status. A line within 4 s of the
previous one replaces it (picture moved on), except after a replay line.
Dashboard: `poller.commentator.feed(director.log, context)` every tick while
REGIE is on (context: track, leader lap, laps / minutes left, cars running),
`skip_existing` on switch-on; `GET /commentary` (lines, ai, model,
last_error); OBS page `/commentary/overlay` = cyan KOMMENTAR tag + line,
slides in, fades after ?secs=10, ?width= ?zoom= ?debug=1 ?demo=1; loader
commentary.html (39), icon, OVERLAY_TABLE, OBS_OVERLAYS.md. Dashboard shows
the newest line in italics under the REGIE line. Also: events detected
during our own replay are now marked seen for good (after the NEXT replay
moved the window they used to become news again). test_race_commentator.py
17/17; integ2.py 13/13; overlay rendered (demo) in Chromium.
FIRST CLAUDE RUN (stream PC, same evening) — three API gotchas, each now shown
in `/commentary` last_error: (1) "HTTP Error 400" alone said nothing → the
error BODY is read (Anthropic says why). (2) the key was not scoped to a
workspace ("must include the anthropic-workspace-id header") → optional
`workspace_id` in commentary_config.json; Thomas made a workspace key instead.
(3) claude-haiku-5-5 THINKS by default and spent all 80 tokens on it (only a
thinking block, stop_reason max_tokens) → `thinking: {type: disabled}`,
max_tokens 400; if a model rejects that switch, one retry without it and
3000 tokens, remembered. A reply not used says why (too slow / no text +
stop_reason). Then real Claude lines, e.g. "Mike Zocher lauert auf Dennis
Richter, nur 0,44 Sekunden dahinter auf Platz zwei."
STEP 3 (voice) same evening, while Thomas raced: NEW `race_voice.py`
(`Voice`): every published commentary line (Commentator.on_line) → audio in a
background thread, last 12 clips in memory, backlog collapsed to the newest,
a line > 12 s old dropped. Engines (commentary_config.json "tts"): "edge" =
Microsoft neural voices via the `edge-tts` package (unofficial Edge read-aloud
service, free, internet; default de-DE-ConradNeural, "tts_voice" / "tts_rate"
+10 %; ~1 s per line), "sapi" = Windows' own SAPI5 voice via pywin32 (offline,
robotic, German voice picked by language 407/c07/807 or "sapi_voice"), "auto"
= edge, SAPI for 60 s after an edge failure; "off". Audio is only made while
a voice page polls `/commentary?voice=1` (15 s) — nothing goes out when the
source is not in OBS. Dashboard: `/commentary` lines carry `audio`, plus
`voice` status and `now`; `GET /commentary/audio/<id>`; OBS page
`/commentary/voice` (invisible; ?debug=1 / H shows engine, voice, errors,
last line; ?vol= ?wait=8 ?maxage=15): plays one line at a time, newest only,
skips history on load. Loader commentary_voice.html is a REDIRECT loader (top
level page → OBS gets the sound), in OBS tick "Control audio via OBS" → own
mixer channel. Icon "Stimme" (speaker), OVERLAY_TABLE, OBS_OVERLAYS.md;
requirements.txt + edge-tts (the updater pip-installs on the change).
test_race_voice.py 16/16 (+ --live real edge-tts 1.0 s); end-to-end in the
scratchpad: real dashboard Flask app + real edge-tts + the voice page in
headless Chromium over CDP → clips fetched and played to the end in order.
SAPI path NOT run (no Windows here) — first real test on the stream PC.
QUALIFYING (2026-10-15, Thomas's show plan: quali is commentated too, "show
the fastest driver and the chaser"): NEW `quali_director.py` (`QualiState`),
used by RaceDirector when the dashboard ticks it with `mode="quali"`
(session type / name contains "qualify"; `t_session` = SessionTime; rows now
carry `best` = CarIdxBestLapTime and `surface`). Same pacing / log / camera
code, no replays; a mode change resets the director. Per car it follows the
lap in progress: a FLYING lap starts at an S/F crossing on track; pit road /
out of world / off-track spoil it. Live DELTA to the class pole = elapsed at
the current pct vs the pole lap's own (pct → time) curve, recorded while it
was driven (kept when a finished clean lap is within 0.35 s of the new
official best), scaled to the official time; without a curve pole × pct,
flagged approx. Candidates: `attack` (clean flying lap predicted top 10 and
an improvement, ≤ +1.5 s; prio 80 − 40/s slower + 10×pct − 2×pos; +12 for a
CHALLENGER under pole pace, −5 for the pole sitter's own lap — Driver 0's
new pole went unseen before that; held 5 s past the line; a car on air going
straight into its next lap stays), "first flying lap" while nobody has a
time, `pole` (pole sitter on track; due after 120 s; pole taken from ALL cars
with a time — a pole sitter in the garage is still on pole), `field` (flying
laps first), live `incident` only on a promising lap. Best-lap improvements
→ log kind `quali_lap` (time, position, gap, new_pole, kept_pole = improved
his own pole) for the top 10 / the car on air; bests seen on the first tick
are not news. Commentator: facts + German templates for attack (first /
faster / slower → "auf Kurs zu Platz n" / pole sitter / next lap), pole,
quali_lap (new / kept / position + gap); prompt explains delta sign and that
predicted_position is a projection; context = Qualifying, minutes left, cars
with a time. test_quali_director.py 20/20 (simulated quali with a non-linear
lap shape: pole + margin, lap shown to the line + hold, slow car never an
attack, curve-based delta = true gap ±0.15 s, pit-spoiled lap, challenger
over pole sitter, mode reset, no news at switch-on); commentator 23/23;
scratchpad integ3.py = real dashboard poller in a fake "Lone Qualify"
session: quali mode, results, attack shots, quali lines.
SENDEABLAUF (2026-10-15, same request — Thomas raced SFL meanwhile, nothing
tried live yet): stream 19:30 Intro (song + slides, by hand) → practice (by
hand) → at `start_at` 19:40 the AI takes over. Thomas chose: VTube Studio
avatar, automatic + Stream Deck, track info "both" (notes, else safe general
knowledge), track video = existing OBS media source. NEW `obs_ws.py` (minimal
obs-websocket v5 client on `websocket-client`: Hello/Identify with the
sha256 auth string, requests, reconnect once; scenes, scene items,
visibility, media restart/stop/status, stream status), `fake_obs.py` (test
server on `websockets`), NEW `show_runner.py` (`ShowRunner`, injected OBS /
speaker / clock): facts from 5010 `/api/roundinfo` (CLS lastRound races top 5
+ fastest lap + DotD, standings top 5 + gap + Pro/Am top 3, next round) +
iRacing WeekendInfo (track, config, weather) + `show_notes/<word of the track
name>.md` + the video length from OBS; segments intro / last_race /
standings / track / outro (no last round / no completed round → those are
dropped); script by Claude (`model` claude-sonnet-5-5, thinking disabled,
JSON {segments}, track words sized to the video, facts only, no numbers
without notes) or templates; written 5 min ahead, readable on `/show`.
Run: Moderator scene, per segment show its source ("Letztes Rennen" /
"Tabelle") or restart the "Streckenvideo", speak the sentences, wait until
all are reported played AND the video ended (deadline = words/2.4 × 1.6 + 15 s
so a deaf voice page cannot hang it), hide, next; then iRacing scene +
REGIE on. Auto start only inside start_at + 20 min, only while OBS STREAMS,
once a day (new day → ready again). `/show` operator page (state, script per
segment, log, facts, OBS check: scenes + sources found), POST
/show/start|next|stop|prepare, Stream Deck show_start / show_next /
show_stop (+ icons regie (on/off), show_*, show_page). show_config.json and
vts_token.json gitignored + PROTECTED; requirements + websocket-client.
Moderation speech: `Commentator.say_show()` → line kind "show", `queue`
(never replaced), `played` / `cancelled` sets, POST /commentary/played/<id>;
Voice `keep=True` lines are all made in order (commentary still newest-only),
`speakable()` reads 1:39,512 as "eins neununddreißig einundfünfzig" (WORDS — checked with vosk ASR: edge-tts read "eins 49 38" digit by digit) and 0,44 as "0 Komma 4 4";
a listener appearing late gets the open show lines voiced (first e2e run:
the page loaded 9 s after the start, the intro was never made). Voice page:
queue lines first, oldest first, ack each, stop on cancel; commentary as
before. AVATAR: the same page drives VTube Studio (ws://127.0.0.1:8001,
plugin "CAS Regie", token kept server-side in vts_token.json): WebAudio
analyser on the playing clip → RMS → MouthOpen via InjectParameterDataRequest
every 40 ms while speaking, a few zeros after — no virtual audio cable.
Tests: test_obs_ws.py 15/15, test_show_runner.py 43/43 (facts, segments,
templates, Claude JSON + thinking fallback, notes match, full run vs fake
OBS: source visibility per segment, video wait, next/stop, deaf-voice
deadline, auto start window / streaming / once a day / next day, missing
sources), test_race_voice.py 21/21. End-to-end in the scratchpad: real
dashboard Flask app + real edge-tts + the voice page in headless Chromium
+ FakeObs + a fake VTube Studio → all 4 sentences voiced, played in order and
acked, sources / video / hand-over / REGIE right, VTS token saved, MouthOpen
0…1 following the real audio. Guide: SENDEABLAUF.md (German). NOT tried:
real OBS, real VTube Studio, a real Claude script (no key here).
FOLLOW-UPS same day (first real runs): /api/roundinfo now runs the league
auto-detect itself (the show had IEC facts over an SFL replay — detection
only ran when a championship page polled) and refuses the old league's data
while switching. PRO/AM: facts per class (class podium / fastest lap, a
table per class; proAmEnabled is only on the STANDINGS season), segments
last_race_pro/_am + standings_pro/_am, and 5010 `/api/prerace_pin?cls=` pins
/lastrace + /table to the class being spoken (polled 1 s, expires 10 min;
the source stays up PRO → AM). Voice page reports its state each poll
(`/commentary` → `pages`: playing, blocked, play() error, AudioContext, OBS
or browser) and skips a clip that stops moving after 3 tries — a silent run
was a second voice page / stale OBS cache; the next run played every line.
`mouth_gain` (commentary_config.json, ?mouth=). EDITABLE SCRIPT on /show
(textarea per segment, POST /show/script; empty = skipped; an edited script
is never rewritten automatically for 12 h and survives restarts (an AUTOMATIC one does not — it could stem from older data / code) in
show_script.json — gitignored + PROTECTED). Track segment: text FIRST, then
the video (`video_after_speech`, default true; false = old talk-over, sized
to the video). Pro/Am: own OBS sources per class ("Letztes Rennen PRO/AM",
"Tabelle PRO/AM" = 5010 /lastrace/pro|am, /table/pro|am, loaders
lastrace_pro/_am, table_pro/_am → 46 generated loaders); missing in the scene → the
plain source + pin (CLASS_SOURCE_FALLBACK). speakable(): "AM" → "A M",
"PRO" → "Pro" (edge-tts read AM as the word "am"), "CAS" → "Kas" (said as a word), "Pole…" → "Pol…" (not the German "Pole"; "Polen" stays;
`PRONOUNCE` + commentary_config.json "aussprache": {"WORD": "say as"}). Mouth: level relative to
the recent loudness (peak, ~1 s) so syllable dips close it (~3-4 closes/s
instead of open all sentence); mouth_gain = how wide (1 → 76 %, 2.5 → 100 %). Voice page resets its
played set when the dashboard restarts (/commentary `boot`).
test_show_runner.py 69/69.
QUALI first real replay (same evening): (1) commentary said "noch ohne
gemessene Zeit" for every field shot — quali `field` shots now carry car /
team / flying / class position, own templates (quali_field, _flying, _time)
and the prompt says to introduce a driver without a time; (2) the tower
(5005) showed END-of-quali times at the start of a quali REPLAY — in a
recorded replay (IsReplayPlaying and ReplayFrameNumEnd > 600) it now ranks
by telemetry bests, not ResultsPositions (which already holds the final
result), and skips the quali memory; (3) REGIE in-car camera = "Gyro", never
"Cockpit" (`NEVER_CAMS` with Scenic, also for the any-group fallback).
WCT QUALI FORMAT (first 15 min AM drivers, last 15 min PRO): `QUALI_FORMATS`
in race_commentator.py (by CLS league slug). Dashboard learns the league on
the grid from 5010 /api/detect (thread, 60 s); in quali the commentary
context carries quali_format + quali_abschnitt, and it SAYS the start ("the
first 15 minutes belong to the AM drivers…", only if seen in the first 3 min)
and the switch to PRO once per session (say_show line). The moderation's
facts carry qualifying_format; the outro explains it.
SCHEDULE segment (after the table): OBS source per league
(`schedule_sources` {"cas-gt3-wct": "WCT"}), facts `saisonplan` from CLS
round-info `rounds` (dates, today, next 3, final; dates spoken "27. Oktober")
+ tracks from show_notes/schedule-<slug>.md matched by date (CLS only knows
the next round's track); skipped for a league without a source.
TRACK LOCATION: 5007 `/location` (LOCATION_HTML, Leaflet 1.9.4 from cdnjs +
Esri World Imagery / boundaries tiles — a keyless Google Maps embed loaded
unreliably): pin on the country, then flyTo onto the circuit, name card;
coordinates WeekendInfo.TrackLatitude/-Longitude ("36.53 m" strings), track
JSON centre as fallback; `/state` carries `location`; replays the flight on
obsSourceVisibleChanged (redirect loader location.html). Show: source
"Streckenort" up during the track talk, down before the video; facts `ort`
= TrackCity, TrackCountry.
REPLAY OF AN EARLIER ROUND (the usual way videos get made): the moderation
uses the season as it stood BEFORE that round. show_runner.replay_round():
iRacing track matches a COMPLETED round (CLS round tracks, else
show_notes/schedule-<slug>.md by date; generic words like circuit / speedway
ignored) and not the next round's → N; show_config "as_of_round": N forces,
0 disables. 5010 `/api/roundinfo?before=N` → CLS round-info + standings with
`?beforeRound=N` (league-manager v2.44.0, DEPLOYED 2026-10-15 with
Thomas's OK; round-info rounds now carry `track`). FOLLOW-UP: the OBS pages
(/lastrace, /table …) still showed the results after Motegi — now 5010's
plain /api/roundinfo itself serves the before-N data whenever iRacing runs an
earlier round's track (`_replay_round_now`, its own poller's track_name), so
every pre-race page and the show agree. Checked against live CLS: Motegi →
R3 Zandvoort / table after 3 / AM leader Kuebler 108; Indianapolis → live.
GRAFIK (race_graphics.py, `GraphicsDirector`, pure logic): while REGIE runs
in a RACE the dashboard brings up OBS sources on the iRacing scene — one at
a time, `gap_s` 45 between, never during our replay, hidden after `secs`:
"Wetter" once 40 s after the green (+ spoken weather_line from 5016
/status), "Duell" when REGIE's shot has been a battle for 6 s (≤ every
180 s; the catch-up graphic follows the camera car = the chaser), "Fahrer
des Tages" once at 2/3 of the race (`at` marks; provisional winner from 5013
/data, spoken dotd_line). Missing source → skipped for the session; REGIE
off hides it; show_config "graphics" overrides. test_race_graphics.py 17/17;
integ/integ2 (real poller) run with it.
TALKING POINTS: written with the show script (prepare): claude_points()
(POINTS_PROMPT, JSON {points}, `points_count` 15) or template_points() from
the facts; editable on /show (one per line, saved with the script, kept in
show_script.json with points_used). During REGIE in quali / race (SessionState
racing) the dashboard speaks the next unused one when the commentary has
been quiet `points_quiet_s` 30 s, at most every `points_every_s` 150 s, never
during our replay (say_show line, kind talking_point).
Quali roll call fix (2026-10-16): Claude still said "noch ohne gezeitete
Runde" for every field shot — quali field facts now carry only keys that
have a value (no lap_time / position / cars_with_time = null), the context
drops fahrer_mit_zeit = 0, the prompt forbids it, and the commentator says at
most one quali "field" line per QUALI_FIELD_EVERY_S 45 s (REGIE cuts every
~12 s); the quiet gaps are where talking points come in.
WCT quali camera split: after split_min (15; show_config
"quali_camera_split_min", e.g. 17) REGIE shows only PRO cars, before it only
AM (dashboard `_quali_class_filter`: cls_proam ProAmRoster per UserID →
car "eligible": False + "ineligible_since_t"; quali_director drops them as
subjects, except an AM flying lap begun before the switch, shown to the line).
ABSPANN (post-race show): a second ShowRunner(kind="post") — segments
race_summary / results / standings_after / next_round / goodbye
(POST_SYSTEM + POST_BRIEF, claude_script(system=, briefs=) or
template_post_script), facts build_post_facts(): 5002 /results (top 10,
fastest lap, biggest gainer, retirements, per class via cls_proam by name),
5010 /api/state projection (table incl. tonight, per class, class rank
before), next round = tonight's + 1 from CLS rounds (+ schedule file), and
`verlauf` = the race's own live commentary lines (dashboard _race_story).
Starts post_delay_s 120 after SessionState >= 5 while REGIE runs, once per
race; REGIE off, Moderator scene, sources "Ergebnis" / "Meisterschaft",
stays on Moderator (post_end_scene). Page /abspann (same SHOW_HTML, BASE),
/abspann/status|script|<action>, Stream Deck post_start|post_next|post_stop.
ALSO: 5010 projection (/api/state) during a replay of round N projects onto
the table BEFORE round N (today's table already holds it — counted twice).
VIDEO REVIEW (Thomas's 52-min recording 2026-10-16, Drive → curl, vosk ASR
with timestamps + ffmpeg contact sheets every 10 s): pre-show data right
(before-R4), but → fixes: (1) per-track video: show_notes/videos/<word of
the track>.mp4 is put into "Streckenvideo" via SetInputSettings local_file
(obs_ws.set_media_file); videos in the folder but none for the track → no
video and facts streckenvideo=False (no announcement) — the Algarve video ran
over Motegi; (2) quali attack lines only from 40 % of the lap, every 25 s,
once per car per 100 s (ATTACK_SAY_*) — it said "X greift an …" every 10 s;
(3) gaps rounded (hundredths < 1 s, tenths above) — "null Komma sieben drei
fünf"; (4) the DRIVER's class (log entry "klasse" from cls_proam) — AM drivers
were called "in der Pro-Quali" after the switch; (5) battle graphic not
before 5 min of racing (after_green_s) — "GATHERING LAP DATA" in lap 1.
Not changed: OBS layout (pre-race pages cover the avatar), REGIE idle in
warm-up / before the green (cockpit view for minutes).
AVATAR (2026-10-16): photoreal moderator = "Gaby Ferrari", an AI persona
Andreas made in Photoshop/Firefly (avatar/gaby_full.mp4, 4 s; gaby_calm.mp4 =
the calm hands-folded part 1.85-3.35 s). The blonde woman in the first clips is
a REAL person — never use. Lip-sync via sync.so (avatar_sync.py: multipart
POST /v2/generate video+audio, model lipsync-2-pro, sync_mode bounce, poll
GET /v2/generate/{id}, download outputUrl to avatar/out/ — gitignored); key
"sync_api_key" in commentary_config.json. Test page /avatar (one sentence →
voice → video). Hobby plan: 300 credits ≈ 36 s sync-2-pro — on camera only
for greeting / hand-over / goodbye. Not yet wired into the show. 2026-10-17: Gabi now a
BLONDE variant (black top): avatar/gabi_speaking.mp4 is the lip-sync base
(DEFAULT_CLIP — with gabi_still.mp4, mouth closed, sync.so barely moved the
lips), gabi_idle_loop.mp4 = gabi_still forward + backward (8 s) to wait
between sentences without credits. (Not the real person from the first clips.) The dark-haired Gaby is
OUT (clips removed). SHOW INTEGRATION: avatar_sync.AvatarRenderer (one render
at a time in the background, keyed (kind, segment), re-render on a text
change, failed → voice) is given to both ShowRunners; after prepare / hand
edits they order videos for cfg "avatar" segments (intro, outro, goodbye);
_next_segment plays a ready one (set_media_file on source "Gabi" + visible +
restart, waits for the media to end, hides it — the "Gabi Ruhe" idle loop
underneath stays on), else speaks it. With a sync key the script is written
avatar.prepare_min 20 min ahead. /show shows "🎬 Gabi: …" per segment. Media
"ended" is ignored for 1.5 s after a restart (old state of a fresh file). Keep Gabi's paid on-camera time
small (Thomas): _avatar_segs() never includes a segment with a picture
(SEGMENT_SOURCES / track), avatar.max_words 45 (longer = voice), briefs
intro 25-40 / outro 12-25 / goodbye 12-25 words, the quali format moved from
the outro to the schedule segment, and the Abspann's race_summary shows the
results page, next_round the league schedule (no voice segment without a
picture). Source names matched to OBS ignoring case / spaces (match_name); "OBS prüfen" lists similar names when one is missing.

**October 13, 2026 (trackmap — Pro/Am dot colours, WCT only):** car dots
red = PRO, green = AM (same colours as the tower bars), via `cls_proam`
ProAmRoster — only when the field IS a WCT field (auto mode: ≥ 50 % and ≥ 3
drivers on the roster), otherwise the old orange. Camera car stays yellow
with the glow, r 12 instead of 8 (11 outside WCT). In the pit a Pro/Am dot
keeps its colour at 45 % opacity. `/state` cars carry `proam`. Rendered in
Chromium on the Motegi map with the real WCT roster. Restart 5007.

**October 13, 2026 (trackmap — no map at Motegi, WCT R4):** the map EXISTS
(motegi_gp / motegi_grandprix / twinring_gp from the June 5 OSM bulk build)
but under GUESSED slugs that never matched iRacing's TrackName. New
`_resolve_track_file()` in iracing_trackmap.py: exact `<TrackName>.json`
first; else a bundled `<word>_<layout>` where word = a word (≥4 chars) of
TrackName / TrackDisplayName and layout = TrackConfigName squeezed
("grandprix") or its initials ("gp"). Nothing certain → still "not bundled"
(a wrong map is worse than none). Prints once which file it used and the
exact name to rename it to. Offline-checked: motegi_fullcourse /
twinring_fullcourse → the GP map, East stays East, unknown stays unknown.
Covers every other guessed-slug map of the bulk build too. Restart 5007.
FOLLOW-UP same evening: still no map — the stream PC's /debug (reachable on
the LAN, 192.168.86.138:5007) said TrackName **"twinring fullrc"**, config
"Full Road Course" (initials "frc", not "gp"), so the fallback could not
match. Added `tracks/twinring_fullrc.json` (copy of motegi_gp = the GP road
course). The other Motegi guesses can go once this is confirmed on stream.

**October 13, 2026 (/lastrace — PRO then AM in Pro/Am seasons):** same
mixed-list complaint as /table. In Pro/Am seasons every /lastrace view (each
race, COMBINED if >1 race) is split into PRO then AM, 20 s each (`cycleSec()`;
?cycle= overrides, non-Pro/Am stays 15 s), restarting at PRO on show. Inside
the class `classRace()` renumbers positions, grid slots, gap / laps behind and
the fastest lap. GOTCHA: race-result `points` are the OVERALL points (AM winner
Bastian P7 overall = 23); class points come from `combined[].raceN.points`
(35), fallback `classPointsTable`. Combined per class shows class positions
in R1/R2. `?class=pro|am|all` as on /table, ?race= still pins a race.
Rendered with the live WCT R3 Zandvoort feed: PRO 12 / AM 10, switch at 20 s,
back at 40 s, points = CLS. Restart 5010.

**October 13, 2026 (/rsvp — WCT GT3 is opt-out):** in WCT GT3 drivers only
DECLINE, nobody accepts, so "No answer" IS racing. /rsvp on 5010: for leagues
in `OPTOUT_LEAGUES` (JS, PRERACE_HTML; only `cas-gt3-wct`, matched on
round-info `league.slug`) Racing = accepted + noResponse, the "No answer" tile
is dropped (3 tiles), foot says "racing unless declined". `?optout=1|0`
overrides per source. Other leagues unchanged. Rendered with the live WCT
feed (R4: 30 racing / 7 declined / 0 maybe of 37). Restart 5010.

**October 13, 2026 (championship /table — PRO then AM in Pro/Am seasons):**
in a WCT GT3 session /table showed one mixed list ("combined"). CLS
standings `points` already IS the Pro/Am total (classRawPoints +
participation; `combinedTotal` is the overall one), so only the split was
missing: Pro/Am seasons (season.proAmEnabled) now show the PRO table, then
the AM one, `?cycle=` s each (default 20 on /table, /lastrace keeps 15),
restarting at PRO every time the source is shown (same OBS visibility hook as
/lastrace). Ranks renumbered inside the class, CLS order (tie-breaks) kept.
`?class=pro|am` pins one, `?class=all` = old mixed list. Other leagues
unchanged. Rendered with the live WCT 14th-season feed in Chromium: PRO 16 /
AM 20, switch at 20 s and back at 40 s. Restart 5010.

**October 12, 2026 (Stream Deck plugin "OBS Seitenwechsel", `streamdeck_plugin/`):**
not an overlay (no port, launchers untouched). Node SDK 3 (@elgato/streamdeck
3.0.2, SDKVersion 3, Node 24, Stream Deck ≥ 7.1; Thomas runs 7.6). Watches
OBS via obs-websocket v5 (127.0.0.1:4455, auth supported but off) and on every
CurrentProgramSceneChanged calls switchToProfile on the decks named
XLlinks / XLrechts: Intro 1, Moderator 2, iRacing 3, Werbung 4, Schedule PCC 5
(`config.json` in the .sdPlugin; unknown scene = no switch; the Plus and the
normal deck are never touched). STREAM DECK RULE: a plugin can only switch to
profiles it BUNDLES — Thomas's exports XLlinks/XLrechts.streamDeckProfile are
inside the plugin (at export time each had ONE populated page; pages 2-5 must
be built in the plugin's copy). `ReadOnly` is rejected by the SDK-3 schema.
SDK marks decks from the registration info as disconnected until a
deviceDidConnect → plugin does not filter on isConnected. Bundle: rollup →
`com.halvar.obspages.sdPlugin/bin/plugin.js` (gitignored); package
`dist/com.halvar.obspages.streamDeckPlugin` (validated with `streamdeck
validate`). `npm test` 19/19 incl. END-TO-END: built plugin spawned against a
fake Stream Deck websocket + fake OBS. CONFIRMED by Thomas on the real decks 2026-10-12.
FOLLOW-UP 2026-10-13 (v1.1, for Andreas: decks "Main"/"Cam", own page per
deck): SETTINGS WINDOW = property inspector of the OBS-Status key
(`ui/settings.html`, vanilla JS, no CDN). OBS port/password + live status;
per deck "nicht umschalten" or a bundled profile whose DeviceType fits; scene
list LIVE from OBS (GetSceneList, re-read on SceneListChanged/Created/Removed/
NameChanged, sorted by sceneIndex DESC = dock order — CONFIRMED by Thomas
against his real OBS dock 2026-10-13) × deck → page, blank = deck stays. Model
`{version:1, obs, decks:{name:profile}, pages:{scene:{deck:page}}}` in the
app's GLOBAL settings (survive plugin updates); nothing stored → config.json
(converted by `fromConfig`, same page for all decks). Plugin owns all logic:
PI sends get/save/reset/apply via sendToPlugin, gets `state` back.
`ObsClient.configure()` reconnects on a port change. No confirm() in the PI
(reset = click twice). npm test 38/38 (save, live apply, restart keeps
settings, port change, reset); full chain (real plugin + settings.html in
headless Chromium via CDP + fake app routing + fake OBS, Andreas's 4 decks)
14/14 — that harness lives only in the session scratchpad. SEEN in Thomas's
real app 2026-10-13 (screenshot): renders fine, OBS connected, 6 decks listed,
only the two XL offer profiles, 7 real OBS scenes.
Same day, no code: stream key light NEEWER GL25B on Tasmota plug
`switch.neewer_gl25b` in Home Assistant, switched by OBS Advanced Scene
Switcher → HA webhooks (GET; ASS's POST body breaks HA's JSON parse).

**October 12, 2026 (best sectors + sector top speed, on 5014):** two pages on
the Quali Delta server, no new port. NEW `field_sectors.py` (`FieldSectors`,
no SDK import): every car's sector times from CarIdxLapDistPct vs SessionTime,
boundaries = the same SplitTimeInfo sectors the delta uses, crossing time
INTERPOLATED between two samples (exact to ~ms at 15 Hz). A sector counts only
if its ENTRY was seen (no half sectors after a mid-lap start), no off-track /
pit-road sample and no gap > 1 s. Session best per sector + driver; ideal lap =
their sum (`car_ideal()` = a driver's own). Top speed: no per-car speed in the
SDK → distance (pct × WeekendInfo.TrackLength) over a 0.3-0.6 s window, max per
car per sector, pit road excluded, reads ~1-2 km/h under a true peak. Replay
scrub / gap / teleport only restart that car's tracking; session change resets
(via `_reset_session`). `_read_snapshot` = old one (now `_read_delta`) + a
`field` key; an exception there never breaks the delta page. Pages (one
FIELD_HTML): `/sectors` (S1..Sn time + driver, THEORETICAL BEST, best lap +
gain), `/speed` (camera car's sector: its top speed this pass, ranking top 5 +
the camera car highlighted if further back, `?top=N`). Both `?zoom` `?debug=1`
`?demo=1`. Works in every session type (sectors are most useful in quali).
LIMIT: iRacing has no per-sector validity — a lap deleted without leaving the
surface can still set a sector best. Loaders sectors.html / topspeed.html (38),
icons + OVERLAY_TABLE.md. test_field_sectors.py 28/28, test_qualidelta 27/27;
both pages rendered in Chromium from a simulated 7-car session. Restart 5014.

**October 11, 2026 (overtake panel, for Andreas):** `5000/overtakes/panel` =
the dashboard's overtake column as a small stand-alone window (portrait
race-control monitor, no room for the full dashboard): list newest first,
▶ 10s / ▶ 20s replay (same /replay_5s + incident_id → seek to the move, marks
"gezeigt"), Kamera (/switch_car), ✕ (/incidents/dismiss), Alle löschen,
Auto-Replay toggle, Go Live, LIVE/REPLAY pill. `/overtakes` now also returns
connected / track / auto_replay_overtakes / playback_mode (the /ticker only
reads `overtakes`). Stream Deck: Website action WITHOUT background opens it;
icon `streamdeck_icons/dashboard/ot_panel.png`, README section "Seiten als
Fenster öffnen". Tested with Flask's test client + a Chromium render at 380 px.
FOLLOW-UP: ⛶ button / F key = browser fullscreen (Fullscreen API), `?zoom=`
scales the page for a big portrait screen.

**October 11, 2026 (Stream Deck icon pack):** `streamdeck_icons/` — 288 px
PNG button images: `overlays/<loader name>.png` (38, same names as
obs_loaders/) and `dashboard/` (13 camera groups + cam/driver next/prev,
go_live, replay_last, replay_last_lost_control, hide_ui, the three auto
toggles). Coloured strip = category (same groups as OBS_OVERLAYS.md), line-art
SVG symbol, short German name. README.md there lists files + the /streamdeck
URLs. FOLLOW-UP (Andreas's design): second "off" state, greyed out (strip +
symbol #949494, neutral #202020→#111 background, white label) in
`overlays_off/` (all 38) and `dashboard_off/` (the 4 toggles), same file
names — for OBS source-visibility two-state keys. `render_html(..., off=True)`;
`python make_streamdeck_icons.py <folder…>` renders only those folders. `OVERLAY_TABLE.md` (linked from both READMEs) = every overlay with
port/path, loader and on/off icon as <img> — GENERATED from
make_obs_loaders.OVERLAYS + OVERLAY_ICONS on every run of the icon script
(`--table` = table only). New overlay → icon entry, then re-run. Generated by `make_streamdeck_icons.py` (headless Edge/Chromium, no
pip package); PNGs are committed. New overlay → add a line there too.

**October 11, 2026 (readability pass, same as the driver card):** Weather
(5016), Catch-Up banner (5015 `/`), Session info (5011), Quali Delta (5014)
and the live DotD card (5013): backgrounds 0.78-0.94 → 0.97, grey 10-12 px
labels → 13-15 px bold #d4d4de, values a few px larger. Weather strip now
≈ 980 × 100, catch ≈ 1100 × 150 (OBS_OVERLAYS.md). Tower, championship
and the F1 pages left alone (their own designs). Weather rendered before /
after in Chromium; overlay tests pass.

**October 11, 2026 (installer + updater):** for Andreas (ZIP installs).
`updater.py` (stdlib only): `check()` compares `.install_info.json` (commit of
the last install, gitignored) with GitHub `commits/main` + `compare/` (count +
commit subjects); `apply_update()` downloads codeload ZIP of exactly that
commit, writes every file via temp + os.replace, NEVER deletes, skips
`PROTECTED` runtime files (configs, caches, dotd_history.json — which IS
tracked in git —, logs/, .git/), pip only when requirements.txt changed
(always on --install). A `.git` folder = developer checkout (the Unraid share):
never written, GUI only reports. No stamp = "version unknown" → update
offered. **Add new runtime/config files to `PROTECTED`** as well as
.gitignore. `install.bat` → `install.ps1` (local or downloaded from raw):
Python 3.10+ with tkinter (winget Python 3.12 if missing, Store alias
skipped), folder prompt, `updater.py --install`, desktop + Start-menu shortcut
to pythonw + launch_gui.py. `launch_gui.py`: version label in the header
(click = re-check), green banner with up to 6 commit subjects + "Update &
restart" → stops running overlays, applies,
[FOLLOW-UP 2026-10-11, first real Windows run on the stream PC: Python 3.14
failed CERTIFICATE_VERIFY_FAILED against api.github.com — Python only trusts
roots already IN the Windows store, Windows fetches missing ones lazily (the
PowerShell raw download worked). `_get()` now retries with certifi, then
Windows' own curl.exe, and keeps whichever worked; tested with a simulated
cert failure (both fallbacks, 404 still surfaces as HTTPError).] relaunches itself with
`--autostart tag,…` (waits up to 15 s for each port to free). Failure → error
dialog, overlays restarted, GUI keeps running. `launch_gui.bat` falls back to
`pyw -3`. Tested: real GitHub install into a temp folder (1565 files), stamp
3 commits back → banner list, update wrote 1 file, config kept; GUI flow
12/12 with stubbed tkinter; install.ps1 parses in PowerShell 7 and the
Python finder works. CONFIRMED on the stream PC 2026-10-11: install.bat (after the cert fix) and the GUI "Update & restart" to dac6b93 both work. Guide: INSTALL.md (German).

**October 10, 2026 (F1-style battle graphics + "TURN 11" label):** from F1 TV
screenshots, all as extra pages on existing servers (no new ports, loaders
added → 36). Catch-up server 5015 (camera car vs same-class car ahead, same
/status): `/gap` (P · first/LAST name · team-or-car + brand logo | "+0.787s
CURRENT GAP" or "+1 LAP" | other driver), `/chasing` ("CHASING NORRIS FOR
5TH"), `/laps` (last 4 lap NUMBERS both cars completed; per lap the slower
car shows its time, the faster one "-0.098" in green, PIT tag on pit laps).
New `_lap_log` keeps every lap incl. pit laps with its number (lap_now - 1 at
the increment); the 3-lap pace window still excludes pit laps. Name colour =
first CarDesignStr colour (too dark → white), team hidden when it equals the
driver name. Corner-cue server 5012: `/turn` = label for the CAMERA car's
current corner (not in pit), holds `?hold=1.5` s after the exit. Numbers are
the GEOMETRY numbering — counts match Algarve (15/15) but differ by one at Spa
/ Nürburgring, so `cues/<track>.json` now also takes `"turn": <official no.>`
and `"hide": true` per detected corner. All pages: `?zoom=`, `?debug=1` / H,
`?demo=1`. test_catchup.py 30 → 40; demo pages rendered in Chromium.

**October 10, 2026 (second driver card):** `5017/simple` = the same card
without iRating and licence / safety rating (license stat now has class
`lic-stat`); `?hide=ir,lic` does the same on any URL. Loader
`obs_loaders/driver_simple.html` (make_obs_loaders entry, 32 loaders).

**October 10, 2026 (tower: no scrollbar, ?rows=N):** a 48-car NEC field
was taller than the OBS source and Chromium drew a scrollbar on stream.
TOWER_HTML now has `html, body { overflow: hidden }` (overflow is cut,
never scrolls) and `?rows=N` = top N only; the on-camera car is kept: if
it is further back it takes the last slot after a 6 px gap (`.gapb`).
CODE_VERSION → "2026-10-10 rows". OBS_OVERLAYS.md lists all loaders.

**October 10, 2026 (standings — TEAM names in team events, IEC / NEC):**
`/standings` carries `team_event` (WeekendInfo.TeamRacing == 1). In team
events the tower shows the TEAM name (header "team") instead of the driver
currently in the car, and drops the flag column (it would be the current
driver's country) so the team name gets the room. Same on /movers, the
/gapbar focus label and /fastest (team name + the lap-setting driver in
small text; fastest-lap events now carry team_name). `?names=driver`
restores driver names per source. Classic page unchanged.
CODE_VERSION → "2026-10-10 team-names".

**October 10, 2026 (small UI requests):** "Stream mode" button hidden in
Results (5002), Results Lite (5003) and Grid (5001) — it showed up on
stream; H still toggles the background (dashboard keeps its button, it is
the operator UI). Driver card (5017), Andreas: card background 0.88 → 0.97
opacity; labels 10 px grey #8a8a99 → 14 px #d4d4de bold, team line 12 →
16 px, values 19 → 24 px, name 23 → 29 px, licence chip 18 px nowrap —
readable without scaling the source up in OBS (card ≈ 880 × 95 px).

**October 10, 2026 (spectating an official NEC race: no data, then wrong
livery):**
  • "Dashboard / logger get nothing" was NOT our code: new `check_sdk.py`
    (run on the race PC, writes logs/sdk_check_<time>.txt to the share)
    showed both pyirsdk startup gates passing (sim status running:1, data
    event signalled) but iRacing's memory header EMPTY — ver 0, status 0,
    numVars 0, session info length 0, only the tick counters moving,
    sessInfoUpdate 80 (it HAD been connected). The sim had lost its
    session; a full iRacing restart fixed it (afterwards ver 2, status 1,
    335 vars). Unofficial vs official races make no difference to the SDK.
    check_sdk.py also dumps gates, header, a version-bypass read, overlay
    ports, the livery /debug + /state and a render-server probe.
  • Livery showed the wrong paint in a TEAM race (NEC, class NECGT3 2026):
    the car on track wears the TEAM paint, which Trading Paints stores as
    `car_team_<TeamID>.tga`; the overlay only looked for the driver's own
    `car_<custid>.tga`. `find_paint_file(car_path, cust_id, team_id)` now
    tries the team file first when `WeekendInfo.TeamRacing == 1` (TeamID
    from DriverInfo), the driver's own file as fallback; /livery takes
    `?team=`. Image URLs carry `?p=<crc32 of the paint path>` so OBS can't
    keep a cached render of a previous paint (the /carview URL only held
    car_id/cust_id with max-age 3600). Verified offline with a fake paint
    cache; not yet confirmed visually in a live team race.

**October 10, 2026 (championship + duel for WCT GT3 — the series is read off
the grid; participation points and drop-weeks):** Andreas wants the overlays
for WCT GT3 too, so both series have to work without anyone remembering to
switch.
  • NOTHING was league-specific to begin with: `DEFAULT_CONFIG["league_slug"]`
    was already `cas-gt3-wct`, points tables / `classPointsTable` / standings /
    the `iracingMemberId` join all come from the API, and `/duel` shares
    `_state()` so the title card follows for free. The configs are gitignored,
    so a fresh clone (Andreas) gets WCT GT3 out of the box.
  • NEW `cls_league_detect.py` (`LeagueDetector`): roster map of EVERY CLS
    league's runnable seasons, `detect(user_ids)` → the (league, SEASON) whose
    roster matches the most drivers in the session. Same identity bridge and
    the same MIN_MATCHES=3 / MIN_MATCH_SHARE=0.5 thresholds as `cls_proam.py`
    (two different answers about "is this that league's session" on one stream
    would be unreadable). Cache `cls_league_cache.json` (gitignored) so it
    works offline at race start; urllib only. Fetch thread starts in `main()`,
    not at import — a test must not reach the network.
  • **It matches per SEASON, not per league, and that is the point.** Two
    leagues have TWO seasons open at once and the API returns the EMPTY new one
    when no `season` is given — `cas-pccd` 5th has 27 linked ids, 6th has 1;
    `cas-sfl-cup` 8th has 19, 9th has 0. That is exactly the trap
    `championship_config.json` worked around by pinning a season id by hand.
    Auto picks the season the drivers are actually in, so the pin stops needing
    a repoint every rollover. Ties break on `completed_rounds`, so an empty new
    season can never outrank the raced one.
  • Rosters DO overlap (GT3 WCT ∩ IEC = 14 drivers) but only drivers PRESENT in
    the session count, so the raced series wins outright — verified live: a real
    20-car WCT grid scored 20/20 WCT vs 6 IEC. Below the thresholds `detect()`
    returns None and the overlay KEEPS its configured league; it never guesses,
    and a field of strangers never blanks the tower. SFL 9th has zero linked
    ids and can never be detected — that is what manual mode stays for.
  • `championship_config.json` gains `"league_mode": "auto" | "manual"`
    (default auto, the `proam_config.json` precedent). Config page has the
    selector plus a live readout of what the grid says (`GET /api/detect`:
    mode, drivers seen, detection, every known season, roster errors).
    `_apply_detected_league()` runs inside `_state()` and only calls
    `fetcher.update_config` on a CHANGE, so the 60 s championship fetch is not
    restarted every tick. RoundMemory needed nothing — `earlier_races()`
    already filters on `season_id`, so two leagues cannot contaminate each other.
  • **Two scoring rules PCCD never needed and WCT GT3 does** — both already
    published in `scoring`, just never read:
      – `participationPoints` (5 in GT3 WCT, 0 in PCCD) at
        `participation_min_distance_pct` (75, config — CLS does not publish the
        threshold). Added in `_score_race`. It counts in the Pro/Am total and
        NOT in the Combined one (`participation_in_combined`, default false =
        GT3 WCT's `participationInCombined`); every other league awards 0, so
        the flag cannot change their numbers. Verified live: P1 Combined +35,
        Pro/Am +40; a car at 65 % of the leader's distance gets position points
        but no participation point; at 45 % it gets neither.
      – `dropWorstNRounds` (3 of 12 → best 9 count). The overlay CANNOT do this
        properly — it would need each driver's per-round history, which the
        standings endpoint does not carry — so `scoring_info.drop_weeks_active`
        says when a plain "add the race" projection stops being exact
        (completed + 1 > total − drop, i.e. **round 10** for GT3 WCT) instead of
        silently overstating. Flagging it beats guessing; add per-round points to
        the API if it ever needs to be exact.
  • Tests: `test_league_detect.py` 23 offline + 5 more with `--live`;
    `test_championship_rounds.py` 18 → **39** (participation, both distance
    thresholds, the drop-week flag, the auto-switch, manual pinning, a missing
    detector). `test_proam.py` / `test_dotd_round.py` / `test_standings_delta.py`
    / `test_fastest_lap.py` unchanged and passing. Backup:
    `iracing_championship.py.bak-20261010-multileague`.
  • No new port and no new page → launchers and `make_obs_loaders.py` untouched.
    **Restart overlay 5010 to pick it up** (the June 4 lesson).
  • Flask is not installed on the Unraid box; a throwaway venv in the scratchpad
    runs the suites.


**October 8, 2026 — evening (PCCD Algarve post-mortem: wrong numbers in
race 2 for championship / title fight, wrong provisional DotD):**
  • CHAMPIONSHIP ROOT CAUSE: RoundMemory keyed races by the TELEMETRY
    `SessionUniqueID` — that is a counter that goes up with every session
    part (3 → 4 → 5 during the event), not the hosted session's id. Race 1
    was re-read from SessionInfo after each change and stored under 3:2,
    4:2 and 5:2 → counted THREE times in race 2 (overlay showed Maurice
    482 / Remo 472; CLS final 400 / 402). Fix: key =
    `WeekendInfo.SessionID-SubSessionID:SessionNum` (stable for the whole
    hosted session — the race logger already used SessionID), and
    `earlier_races()` de-duplicates by (session name, finishing order),
    keeping the most complete copy (one copy was saved while P8 was still
    on his last lap: 14 vs 15 laps). Replaying tonight's real cache with
    this morning's CLS standings now gives EXACTLY the CLS final table.
    test_championship_rounds.py 18/18 (new 10/11 fail on the old file).
  • DOTD ROOT CAUSES: (1) CLS crowns ONE DotD per ROUND — two-race rounds
    are combined (league-manager src/lib/driver-of-the-day.ts
    combineRaceCandidates: positions gained / recovery / overtakes /
    incidents summed, crownable only if classified in EVERY race); the
    overlay scored the race on screen only. (2) The no-back-to-back block
    came from dotd_history.json, which records a winner per RACE (and
    test races, filed under whatever season championship_config pins) →
    during race 2 it blocked the race-1 winner instead of the previous
    ROUND's (Andre Brechmann).
    Fix: `driver_of_the_day.analyze_round()` (same method as CLS; scoring
    extracted into `_score()`, single-race results unchanged — Zandvoort /
    Road Atlanta / Zolder winners identical). Overlay 5013:
    `round_logs()` finds earlier FINISHED race logs of the same hosted
    session (session_start.session_unique_id, lower session_num) and
    scores the round; `cls_previous_winner()` takes the blocked driver
    from CLS /api/overlay/round-info (previous round's official DotD; if
    CLS already published today's round, its previousWinnerName), local
    history only as a fallback. Card tag "PROVISIONAL · RACE 2, LAP n" /
    "ROUND · 2 RACES". Tonight's logs → Maurice Becker 0.7841 (gained 6,
    rec 1, 7 OT, 20 inc) = CLS 0.784 exactly. test_dotd_round.py 10/10
    (skips when the gitignored logs are absent).
  • Known leftover: the race logger still records a DotD per RACE into
    dotd_history.json at session_end; nothing reads it first any more.

**October 8, 2026 (YouTube live picture-in-picture on 5005):** User wants
to show a live YouTube stream in the broadcast. KEY FINDING: YouTube now
refuses embeds on LOCAL-FILE pages ("Error 153 — video player
configuration error"; verified in headless Chromium), so this can NOT be
an obs_loaders/*.html file and should not be wrapped in one — it is
served by the standings server: `/youtube` (OBS URL source, 1280×720,
"Control audio via OBS") + `/youtube/setup` (paste a link, mute toggle) +
`/youtube/config` (GET/POST, saved in youtube_config.json, gitignored;
server extracts the 11-char id from watch?v= / youtu.be / /live/ /embed/
/shorts or a bare id, rejects anything else). The page polls the config
every 5 s and swaps the video live; `?v=` / `?mute=` override per source.
IFrame API error codes → readable message + retry every 60 s (101/150 =
channel disallows embedding — e.g. Lofi Girl's live; "Me at the zoo"
loads fine). Default muted. CODE_VERSION → "2026-10-08 youtube".

**October 8, 2026 (Twitch chat overlay — standalone):** `obs_loaders/
twitch_chat.html` (hand-written, NOT generated by make_obs_loaders.py —
don't add it to that list). Pure browser page, no Python server: reads
chat of `sim_racing_broadcast` ANONYMOUSLY via Twitch IRC over WebSocket
(wss://irc-ws.chat.twitch.tv, PASS SCHMOOPIIE / NICK justinfan…, CAP
tags+commands), so no OAuth token. Shows all messages (user's choice),
max 6, 30 s each; hides common bots and !commands; Twitch emotes from
static-cdn (positions are Unicode code points → Array.from); text badges
HOST/MOD/VIP/SUB; highlighted messages in amber. Respects moderation:
CLEARMSG removes the message, CLEARCHAT removes the user's (or all)
messages. Reconnects with backoff, answers PING. Options ?channel= ?max=
?secs= ?mode=all|highlight|q ?commands=1 ?hide= ?zoom= ?debug=1 ?demo=1.
Tested: real anonymous connect to the channel (status "connected"); node
unit test with crafted IRC lines (emotes, /me, bots, !cmd, highlight,
HTML escaping, delete, timeout) all pass; demo rendered.
  • FOLLOW-UP: live VIEWER COUNT pill ("● LIVE · 23 viewers", top left,
    refreshed every 60 s, hidden while offline, `?viewers=0` hides it).
    The official Helix API needs a registered app + secret, so: primary
    decapi.me/twitch/viewercount/<ch> (plain number or "<ch> is offline",
    CORS *), fallback Twitch's web GQL (gql.twitch.tv, public web Client-Id
    kimne78kx3ncx6brgo4mv6wki5h1ko — unofficial, may break; only used when
    decapi errors). Verified: both returned 48374 for the top live channel;
    fallback works with decapi blocked; offline channel → pill hidden.

**October 8, 2026 (pre-race pages from CLS — new CLS endpoint v2.37.0):**
User wants four overlays shown before a race, all from CLS: last round's
results, standings before the round, RSVP overview, season participation.
  • CLS (league-manager, deployed v2.37.0, user-approved): NEW public
    `GET /api/overlay/round-info?league=&season=` — `lastRound` (latest
    COMPLETED round, results per raceNumber: position, name, country,
    start number, Pro/Am, team, car, status, laps/lapsBehind, gapMs,
    bestLapMs + fastestLap flag, points), `nextRound` (first non-completed
    round + RSVP COUNTS ONLY — accepted/declined/tentative/noResponse of
    APPROVED non-team-manager registrations; user chose no names because
    RSVPs aren't public on the site), `participation` (entries/finishers
    per countsForChampionship round, completed rounds only, + totals).
  • Overlay side, all on the championship server (5010), one shared page
    `PRERACE_HTML` picking the view from the path: `/lastrace` (both races
    alternate every `?cycle=15` s, or `?race=1|2`), `/table` (CLS
    standings before the round), `/rsvp`, `/stats` (bars like the CLS
    stats page + totals). `?top=N`, `?zoom=`, `?debug=1`. Data via
    `/api/roundinfo` = `RoundInfoFetcher` (60 s) + the standings fetcher.
    `/flag/<code>.svg` served here too (country_flags). OBS loaders added
    (30 total).
  • CLS results carry NO total race time for imported rounds (Zolder:
    gapMs null for everyone) → /lastrace shows "grid → result" (P5 ▲2)
    from startPosition instead, and the time gap only when CLS has times.
  • Verified against the live v2.37.0 feed: participation 16/32, 19/36,
    10/18, 15/28, 8/16, 8/16 = the CLS stats page; RSVP R7 9/5/1/12 of 27.
    CLS data oddity: some profiles have country codes AQ / GO (Antarctica
    flag shows) — fix in the CLS admin, not in code.
  • FOLLOW-UP (CLS v2.37.1): the feed's `lastRound` also carries
    `combined` — computed server-side exactly like the round page's
    Combined tab (race pts, class-relative in Pro/Am; + participation when
    participationInCombined; + driver FPR; − manual AND steward penalties
    from the Penalty table under the same "applied immediately" rule; same
    tie-breaks). Steward penalties live in Penalty, NOT on RaceResult —
    summing per-race rows in the overlay would have shown Cursio 46
    instead of 43. And `driverOfTheDay` (RoundDriverOfTheDay: winner,
    score, breakdown, metrics, top-3 eligible runners-up, back-to-back
    note). Overlay: /lastrace cycles RACE 1 → RACE 2 → COMBINED
    (`?race=combined` pins it); new `/lastdotd` page (not /dotd, to keep
    it apart from the live provisional card on 5013). Both verified
    against the live Zolder round page.
  • The pre-race pages run full-screen on the moderator screen → panels
    are SOLID by default (opaque colour vars); `?glass=1` restores the
    translucent tower look. Only the 8 px page margin stays transparent.
  • /lastrace restarts at RACE 1 → RACE 2 → COMBINED every time the source
    is SHOWN (OBS keeps hidden browser sources running, so the cycle used to
    be anywhere): listens to OBS's obsSourceVisibleChanged /
    obsSourceActiveChanged events + visibilitychange. Node-tested.

**October 8, 2026 (trackmap — pit lane drawn around the whole lap):**
Checked Algarve before the PCCD race: `algarve_gp.json` is fine (4648 m vs
4653 official), but its ONPITROAD route is a WHOLE LAP (pit exit → full
lap → pit entry), so the orange pit line covered the entire circuit.
Survey: 219 of 314 bundled files have this SimRacingApps quirk.
Code fix, no data touched: `_trim_pit_lap()` in `_load_track()` — when
the pit route is > PIT_TRIM_MIN_FRAC (0.5) of a lap, keep only its longest
stretch more than PIT_OFF_TRACK_M (3 m) from the racing line (+1 point
each end), joining the end (entry) and start (exit) runs across the wrap.
Shorter pit data is untouched (Oulton Fosters). Result: most tracks 5-30 %
of a lap; 8 rallycross tracks drop their pit line (route never leaves the
racing line). Visually right on Algarve, Zandvoort, Spa, Hockenheim, Nür-
burgring GP, Bristol; Silverstone 2019 GP / Oschersleben still show a
longer stretch than the real pit lane (their pit route wanders) — better
than before, not perfect. Restart the trackmap overlay to load it.

**October 8, 2026 (championship overlay — standings table is the default):**
User expected the championship TABLE (points before + live changes), but
the overlay opened in view A (race order with a GAP column). Now: default
view B; B columns P · Δ · Driver · Before · +Race · Total, with the
"R1 P2 +35 · R2 P1 → +41" breakdown as the name's sub-line. View A's GAP
column replaced by "+Pts" (points for the current position; `race_pts`
added to race_rows). `?view=A|B` pins a browser source (V then toggles
only that source). The full-width "＝" no-change glyph rendered as a box
in some fonts → plain "=". Sizes: 540 px wide; B with 27 PCCD drivers
≈ 1530 px tall, A ≈ 120 px + 31 px per car.
FOLLOW-UP: view B now shows the PROJECTED top 10 by default
(`?top=N`, `?top=0` = all) → 540 × 542 px; fits a 1080p screen.

**October 8, 2026 (trackmap — Oulton Park Fosters added):** A test race
logged TrackName "oulton fosters" → `tracks/oulton_fosters.json` was
missing (only international/island). Built from OSM: Overpass is
reachable DIRECTLY from the Unraid box with curl (use GET; the POST got an
HTML error page) — no browser detour needed here. Oulton is mapped as
named oneway corner ways in driving order; Fosters = Cascades cut at the
Foster's junction + Foster's + Hislop's… back to Old Hall. 18 ways joined
with 0 m gaps, 2681 m vs 2656-2670 official, S/F + pit reused from
oulton_international. Preview rendered via the trackmap's own
_load_track(). NOTICE.txt updated.

**October 8, 2026 (five new broadcast views — no new ports):**
All are extra PAGES on existing servers (tower style, `?zoom=`,
`?debug=1`/H, `?demo=1` fake data), each with an OBS loader in
`obs_loaders/` (make_obs_loaders.py entries added, 26 loaders):
  • `5010/duel` — title-fight card: two championship rows side by side
    (default CLS P1 vs P2, `?a=`/`?b=` custids), live projected points
    incl. RoundMemory (R1 + R2), gap, "before round" score. `/api/duel`;
    `_state()` now shared by /api/state and /api/duel.
  • `5005/fastest` — purple fastest-lap banner, 8 s (`?secs=`), race only
    (`?all=1` = every session). `StandingsPoller._track_fastest()`: per
    class, seeded silently per session (no old lap on a mid-race start),
    departed quali rows ignored, lap number from CarIdxBestLapNum;
    `fastest_lap {seq,event}` in /standings. test_fastest_lap.py 10/10.
  • `5005/movers` — top N gained / lost vs the grid (the tower's
    pos_delta; out-of-world cars excluded), race only.
  • `5005/gapbar` — dots by gap to class leader (tower intervals summed
    per class), 3 lanes + nudge for trains, +LAP box, focus car labelled.
  • `5000/ticker` — overtake lower third from the dashboard detector
    (`/overtakes` feed; overtake records now also carry passed_idx /
    passed_name / passed_number). Skips history on load, queue ≤5, 6 s
    each. test_overtakes.py still ALL PASS; queue logic node-tested.
  • Standings CODE_VERSION → "2026-10-08 fastest-movers-gapbar".

**October 8, 2026 (Driver of the Day — provisional mid-race ranking):**
User wants to show an intermediate DotD during a race (e.g. race 2)
without touching the official award.
  • `driver_of_the_day.provisional_final(events)` builds a provisional
    session_end from the log so far: order = laps completed desc, then
    who crossed first on that lap; incidents = latest official `inc` /
    `inc_snapshot` total (fallback: count of detector `incident` events);
    a car silent for 3 typical laps → "Disconnected" (not crownable).
  • `analyze(..., provisional=True)` uses it ONLY when the log has no
    session_end; with a session_end the result is byte-for-byte the old
    one. Result carries `provisional: True`.
  • `dotd_streak.pick(..., provisional=)` passes it through and NEVER
    records a provisional result (history / no-back-to-back untouched).
    The logger's award at session_end doesn't pass the flag at all.
  • FOLLOW-UP (Oulton Park test race): card froze on "no lap completed
    yet" all race. The log was fine (laps 1-7; analyze gave Finn Zhou) —
    the overlay's cache keyed on the log's mtime, and a file the logger
    still holds open, read from Windows over SMB, keeps reporting the OLD
    mtime. Cache key now includes size, and in-progress / not-ok results
    are recomputed at least every LIVE_TTL (5 s) regardless. Tested with
    a frozen os.stat: picks up the new laps after 5 s.
  • Overlay (5013): provisional on by default (`?provisional=0` hides it);
    yellow "PROVISIONAL · AFTER LAP N" tag + yellow border.
  • Verified on Zandvoort 07.10., WCT Road Atlanta 29.09., PCCD Zolder
    24.09. cut at 30/60/90 %: provisional winner converges on the official
    one (Zandvoort/Zolder from 30 % on); final unchanged; record=True on a
    provisional result writes nothing. Card rendered in headless Chromium.

**October 8, 2026 (standings — new compact tower design, flags):**
User asked for the look of the commercial tower Andreas uses. New default
page `TOWER_HTML` in `iracing_standings.py` (~440 px wide; `?zoom=1.5`
scales, `?debug=1` / H = dark bg): slim info bar (session · remaining ·
air temp · Dry/Wet), class tab (class colour in multiclass) + driver
count, lowercase header, dense rows: position box (blue = on-camera
car via CamCarIdx, name/time orange), Pro/Am bar, abbreviated name,
country flag, brand logo, fastest (purple = class session best) + last
(green = PB); race: interval · last · +/-. The old design is kept at
`/?style=classic`. Served as a plain Response (no Jinja).
  • Flags: `flags/` = flag-icons 7.2.3 SVGs (MIT, flags/NOTICE.txt);
    `country_flags.py` maps iRacing `DriverInfo.Drivers[].FlairName`
    (country name) → ISO code, fallback = CLS registration country, now
    collected by `cls_proam.py` from ALL leagues' current seasons
    (`proam_countries.json`, gitignored). `/standings` rows carry
    `country` + `country_src` ("flair"/"cls") — FlairName is UNVERIFIED
    against a live SDK session; if every row says "cls", the field name
    differs. Flag route `/flag/<code>.svg` only accepts [a-z-].
  • Class tab read "Class": single-class hosted sessions send an EMPTY
    CarClassShortName. `_fill_class_labels()` now derives one — single car
    model → its short name ("Porsche 911 GT3 Cup"), mixed models → the
    words all names share ("GT3"); a real iRacing class name always wins.
  • "Restarted but still old code": the stream PC runs the files straight
    from the Unraid share (SMB, not Nextcloud). Suspected cause: an orphaned
    old overlay process keeps answering on the port — on Windows a second
    werkzeug server binds the same port WITHOUT an error. `launch_gui.py`
    now refuses to Start an overlay whose port already answers
    (`_port_in_use`) and logs how to kill the old one. Standings got a
    `/version` endpoint + `CODE_VERSION` in its banner to prove which code
    is running — bump it on every change.
  • Verified by rendering the page in headless Chromium with the 21
    drivers from Andreas's screenshot. Brands without a logo file (e.g.
    Corvette) show an empty cell; the McLaren SVG is dark on the grey rows.

**October 8, 2026 (championship overlay — two-race rounds, PCCD Algarve):**
User wants the provisional championship after race 1 and again during /
after race 2. CLS only publishes a round once COMPLETED (both races), so
race 1 must come from the overlay itself. Changes in
`iracing_championship.py`:
  • Live race points ONLY in race sessions (before: added in practice /
    quali / warmup too, by track order).
  • After the checkered (SessionState >= 5) the order comes from
    ResultsPositions — finished cars driving to the garage no longer drop
    to the bottom with 0 points.
  • NEW `RoundMemory` + `championship_round_cache.json` (gitignored):
    finished races of the current round (past race sessions from
    SessionInfo, plus the current race once finished), keyed
    `SessionUniqueID:SessionNum`. Projection = CLS points + earlier races
    + live race. Works when race 2 is a SEPARATE hosted session and across
    overlay restarts. Entries only count while season id AND CLS
    completedRounds match what they were recorded with (and < 12 h old) —
    when CLS publishes the round they retire themselves, no double count.
  • CLS rules the API doesn't publish, as config keys:
    `race_points_min_distance_pct` (50, CLS default — below 50 % of the
    leader's laps = 0 points) and `points_table_race2` (None → race-1
    table; PCCD's pointsTableRace2 == pointsTable per
    league-manager/scripts/fix-pccd-r2.ts). PCCD 5th season: no bonuses,
    participation 0, drop-worst-1 can't bite before round 8.
  • View B sub-line now "R1 P3 +30 · R2 P5 → +23"; title shows
    "Championship · live / provisional".
  • GOTCHA: without `season_id` the API now resolves cas-pccd to the 6th
    season (0 rounds). `championship_config.json` pinned to the 5th season
    (cmoefqawn0001l104qb7429mq) — repoint it when season 6 starts.
  • test_championship_rounds.py 12/12; smoke-tested the Flask app against
    the live CLS payload; overlay JS node-checked.

**October 8, 2026 (flags only in quali/race; quali times survive a driver
leaving; +/- investigation):**
  • `flag_overlay.py`: new `_is_flaggable_session()` — flags only in
    QUALIFYING and RACE sessions, never practice / warmup / testing
    (decided by SessionType; a league "WARMUP" is typed Open Practice, the
    name is checked too). Cached per SessionNum, re-evaluated on session
    change. test_flag_overlay.py 37/37 (9 new checks).
  • `iracing_standings.py`: qualifying memory (`_quali_mem`, keyed by
    customer ID, name fallback) of every driver's best OFFICIAL
    ResultsPositions time. A driver who quits keeps his row and time
    (tagged "left") until (SessionUniqueID, SessionNum) changes; a rejoin
    on a new CarIdx doesn't duplicate him and only a quicker lap replaces
    the remembered one. Telemetry-only times are never memorised (they can
    still be invalidated). Qualifying only — practice unchanged.
    test_quali_memory.py 12/12.
  • +/- ("still wrong"): GridBaseline verified CORRECT against the official
    iRacing results JSONs (Zandvoort 07.10., Silverstone 22.09.) — logged
    grid_pos == official starting grid for every car, and the logger's
    +/- matches the official finish. The tower's CURRENT position is its
    own live-progress sort (not CarIdxPosition like the logger); replaying
    Road Atlanta 29.09. through it found no clear defect (iRacing's own
    CarIdxPosition was itself odd at times — #26 P11 with a 10.9 s gap).
    Awaiting a concrete example from the user before changing anything.
  • ROOT CAUSE FOUND (user screenshot, Zandvoort replay at race 00:45):
    BEFORE THE GREEN iRacing freezes every car's lap counter (-1 from
    gridding through the pace lap, all set together at the green), and the
    timing line runs THROUGH the grid — front ten at pct 0.00-0.01, back
    twelve at 0.98-0.999. Live lap+pct sort put the back of the grid in the
    lead → every +/- ±10..12 and "+2 LAPS". Fix: while 0 < SessionState <
    Racing the tower orders by GridBaseline slot (then CarIdxPosition) and
    blanks interval / lap-down; GridBaseline.update() moved before the sort.
    Same guard added to `iracing_championship.py` (its live projection
    would have scored the pace-lap order). test_standings_delta.py +11
    pre-green checks, test_championship_rounds.py 13/13. Verified the old
    path reproduces the bug (back of grid leading).

**October 8, 2026 (standings + driver card — WCT GT3 Pro/Am bars):**
User request: in WCT GT3 only, a small RED bar in front of PRO drivers'
names and a GREEN bar for AM, on the standings tower AND the driver card.
  • NEW shared module `cls_proam.py` (`ProAmRoster`): background thread
    pulls `/api/overlay/standings?league=cas-gt3-wct` from the CLS
    league-manager every 5 min (urllib — no new dependency), maps
    `iracingMemberId` → `proAmClass`, joined to `DriverInfo.UserID`. No
    `season` param → API returns the current season (14th: 16 PRO / 20 AM).
    Last good body cached in `proam_cache.json` (gitignored) for offline
    starts.
  • "Only WCT": mode `auto` (default) shows bars only when ≥50 % of the
    session's drivers AND ≥3 are on the WCT roster, so a WCT driver in a
    PCCD/IEC race gets no bar. Override via optional `proam_config.json`
    (`"mode": "on"|"off"`, also `league_slug` / `season_id`).
  • Standings: `user_id` in `_driver_map`, `proam` per row +
    `proam_active`; `.pa` bar inside `.driver` before the name. Driver card:
    `.pa` bar between number chip and name. Both expose `/proam` (roster
    status) for debugging. No new port → launchers untouched.
  • `test_proam.py` 11/11 (incl. `--live` fetch); test_drivercard 19/19,
    test_standings_delta pass; both pages' JS node-checked. Overlays must
    be restarted to pick it up.

**September 10, 2026 (trackmap — unclosed lap loops, Donington pit
straight missing):** User reported Donington drew no start/finish
straight — only the pit lane ran along that side of the map. Cause: the
bundled SIMRacingApps GPX routes are OPEN polylines, and several of them
stop well short of the S/F line. `iracing_trackmap.py` drew `ontrack` as
a plain `<polyline>`, so that last stretch was simply never drawn — at
Donington a **253 m** hole exactly where the pit straight is, leaving the
orange pit lane alone in the picture.
  • Second, quieter bug from the same gap: `arc_norm` normalised over the
    OPEN length, so a lap's worth of `CarIdxLapDistPct` was squeezed into
    3740 m of a 3993 m lap — every car dot drifted by up to the gap size
    (~6 % of a lap at Donington) and jumped at the S/F line.
  • Fix in `_load_track()`: after projection, if the route's open end is
    within `LOOP_CLOSE_MAX_FRAC` (0.20) of its own run length, append the
    first point to close it — before the bbox, the SVG scaling AND the
    arc-length table, so drawing and dot placement are both fixed. The
    front-end reads its points back out of `polyline.road`, so it inherits
    the closure with no JS change.
  • Survey of all 331 bundled JSONs: 87 had a real gap >20 m — worst
    Road America 603 m, The Bend 412 m, Montreal 366 m, Sebring 361 m,
    plus most ovals (Martinsville 98 m, Milwaukee 184 m, Homestead 265 m,
    Bristol 84 m) and Interlagos / Zolder / Motegi / Mid-Ohio / Coronado /
    Portland / Mexico City / Road Atlanta / Oulton / Barber / Adelaide /
    Laguna. All now close along the S/F straight (verified by rendering
    ten of them — the closing chord lands parallel to the pit lane every
    time, which is what a pit straight looks like).
  • The threshold is what keeps POINT-TO-POINT routes open: the two Mt
    Washington hillclimbs sit at 53 % and are correctly left unclosed.
    294 files gain a closing segment, 37 were already closed, 2 stay open.
  • Not a data fix — no track JSON was touched, so future bundled tracks
    with the same upstream quirk are covered automatically.
  • REMINDER (June 4 lesson): the running trackmap process keeps the old
    code in memory — restart the overlay to see it.

**September 5, 2026 (race logger — OFFICIAL incident points, `inc` /
`inc_snapshot`):** The IEC Sebring race control PC crashed mid-race; on
restart every driver's incident count was DOUBLED, which triggered
drive-throughs that were not owed (and hid some that were). Reconstructing
that needs a per-car incident TIMELINE — and the log did not have one.
  • The existing `incident` events are the DASHBOARD's spin/contact
    DETECTION, pulled from `http://127.0.0.1:5000/incidents`. If the
    dashboard isn't running (the usual case for a driver-side logger, and
    for a replay re-log) the log contains **zero** incident data. Verified
    on the 05.09. Sebring log: 3876 events, not one incident.
  • NEW `_maybe_emit_incident_points()` reads
    `SessionInfo.Sessions[].ResultsPositions[].Incidents` — the ONLY per-car
    incident source in the SDK (telemetry has counts for the local car only:
    `PlayerCarMyIncidentCount` & co., no CarIdx array). In a team event that
    value is the CAR's count, which is what a DT threshold is judged on.
  • Emits `inc` on every change (`delta`, `total`, `lap`, `car_idx`,
    `car_number`, `driver`, `team`, `t_session`, `t_wall`) and
    `inc_snapshot` once a minute with every car's total, so the timeline
    survives a mid-race attach or a logger restart. First sighting of a car
    NEVER emits — otherwise attaching mid-race reports everyone's running
    total as one huge incident; the snapshot records that baseline instead.
    A backwards jump (session reset) is adopted silently.
  • Throttled to `INC_POLL_INTERVAL` (2 s): every read parses the whole
    SessionInfo YAML. `_detect_session_change` already does one such parse
    per 0.5 s tick, so this roughly holds the existing cost.
  • Verified offline: `test_inc_points.py` 29/29 (baseline silence, delta +
    total, throttle, multi-car, late joiner, session reset, snapshot cadence,
    a raising SessionInfo, no-log-open, JSON round-trip).
  • OPEN QUESTION for the replay re-log: whether iRacing rebuilds
    ResultsPositions incident counts while scrubbing a REPLAY, or only shows
    the final tally. Test with a 2-minute replay over a known incident and
    grep the log for `"type":"inc"` before committing to a full 3 h re-log.
  • REPLAY ANSWER (tested the same evening, 40 min of replay): iRacing does
    NOT rebuild incident counts while scrubbing a replay. The very first
    snapshot already carried the FINAL tallies and they never moved, so zero
    `inc` events. An incident timeline cannot be recovered from a replay —
    only live. Whether live works is still unproven (race control shows
    rising INCs from the same source, so it should); test it with a practice
    session and two deliberate wall taps.
  • `launch_gui.py`: new `START_TOGETHER` map — starting the logger also
    starts the dashboard and vice versa (recursion-safe, skips a partner that
    is already running, start-only: stopping stays uncoupled because other
    overlays share the dashboard). Reason: the logger's `incident` feed comes
    from the dashboard on 127.0.0.1:5000, and a survey of all 84 logs showed
    incidents in every race up to 27.08. and NONE after — including the
    Sebring race day. Verified by 12/12 offline checks against a fake
    controller set (tkinter is stubbed; this Mac's python has no _tkinter).
  • Anyone running the packaged `RaceLogger.exe` must REBUILD it
    (`build_race_logger_exe.bat` / the GitHub *Build Race Logger* action) —
    the .exe bundles `iracing_race_logger.py` at build time.

**August 12, 2026 (+/- everywhere = NET vs the STARTING GRID, never
cumulative):**
User report: the standings +/- was not reading as "versus where he
started". Rule he stated, and the rule the code now enforces everywhere:
*"If the driver on pole lost one position in the first corner and regains
his place later, he is at 0. If this happens again several times he still
is at 0. It is not cumulating."*
  • NEW shared class `GridBaseline` in `iracing_sdk_base.py` — single
    owner of "which grid slot did this car start from". Source priority,
    captured ONCE per race session and then frozen:
      1. the QUALIFYING session's `ResultsPositions[].Position` (same
         source `iracing_grid.py` uses, so grid / tower / logger agree,
         and it is correct even when an overlay is started mid-race),
      2. the race session's `ResultsPositions[].StartingPosition`,
      3. a `CarIdxPosition` sample at the green flag — allowed ONLY if we
         were already watching before the green (tracked via
         `_saw_pre_green`, plus a max-lap<=1 guard).
    Values are re-ranked to dense 1-based slots, so the class does not
    care whether a source counts from 0 or 1. `class_grid_pos` gives the
    per-class slot for multi-class. Session change resets it.
  • WHY the old code read wrong: `ResultsPositions[].StartingPosition`
    is not published in practice, so it always fell through to the
    green-flag capture — and that capture had no "were we here before the
    green?" guard. Start the overlay mid-race and it froze the CURRENT
    running order as if it were the grid, so every +/- for the rest of
    the race was measured from "wherever everyone happened to be when I
    hit start". Qualifying results fix that outright.
  • NO baseline is now NEVER invented. Late joiner, or mid-race attach
    with no qualifying results → `pos_delta = None` → **blank cell**
    (user's choice). `=` now means only one thing: he is on his grid slot.
  • `iracing_standings.py` — delta block replaced by `GridBaseline`;
    `pos_delta` + new `grid_pos` per row; the +/- cell renders blank on
    None and carries a "started Pn in class" tooltip.
  • `iracing_race_logger.py` — the +/- column in the drivers table used to
    print the two CUMULATIVE counters side by side (`+3 −3` for a driver
    who is exactly where he started). It now shows `_pos_delta()` =
    grid − current, with the cumulative pass counts moved into the cell
    tooltip. Same for the `/share/standings` page (`fmtOvertakes` →
    `fmtPosDelta`). Lap events now also carry `grid_pos` and `pos_delta`.
  • `overtakes` / `overtaken` are UNCHANGED and stay cumulative on
    purpose — they are "how much position churn did he go through", which
    is a real and different statistic, and it is what
    `driver_of_the_day.py` scores as its `ot` component (0.25 weight).
    Do not "fix" them to be net; the +/- is the net one.
  • `driver_of_the_day.py` — `positions_gained` now measures from
    `grid_pos` when the log has it, falling back to the old "position at
    the end of lap 1" for logs recorded before today. So OLD LOGS SCORE
    EXACTLY AS BEFORE; new races credit the run to turn 1 as well. A
    driver starting P10 who is P5 after lap 1 and finishes P2 now scores
    8 gained instead of 3. `recovery` deliberately still measures from
    the in-race low point, not the grid.
  • Two copies of `iracing_standings.py` existed (project root, and a
    stale ZIP-extraction in `iracing-overlays-main/`) with DIFFERENT
    designs — root cycled the column every 5 min, the nested one had a
    permanent `+/-` column. Both were overwritten with the same fixed
    permanent-column version so they cannot diverge again. The nested
    folder should be deleted; everything else in it is identical to the
    root except an OLDER `iracing_drivercard.py`.
  • Verified offline (stubbed irsdk): `test_pos_delta.py` 29/29
    (lose+regain x7 never accumulates, green-flag path, mid-race attach
    stays blank, session reset, multi-class, 0-based source),
    `test_standings_delta.py` (real `_build_race_standings`),
    `test_dotd_grid.py` (old logs unchanged, new logs grid-based).

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
  • FOLLOW-UP 4 (fixed overlay width): the card used `min-width: 430px` and
    grew with a long driver name, which shifted its OBS placement. Changed to
    a FIXED `width: 460px`; `.name` now `flex:1 1 auto; min-width:0` so long
    names truncate with an ellipsis, and `.ref-name` (pole driver on the
    right) capped at `max-width:150px` + ellipsis. Overall size is now
    constant regardless of name length (both reference modes share the page).
  • FOLLOW-UP 5 (spectator delta glitch — huge value like "-1027.164"): a
    replay rewind / session-time backward jump leaves a car's `_car_lap_start`
    stale, so `elapsed = t - lap_start` goes hugely negative and the computed
    spectator delta showed nonsense (−1027s). Added a sanity guard in
    `_spec_cam_delta`: hide the delta (return None/False → overlay shows "—" +
    "Waiting for a flying lap…") when `elapsed < -0.5` or `> 3600`, or when
    `abs(delta) > 120` (a real quali delta is seconds, not minutes). Self-heals
    at the car's next S/F crossing. Covers both pole and own-best spectator
    views (shared helper). test_qualidelta.py extended (stale-lap-start guard);
    27/27 pass.
  • FOLLOW-UP 6 (OBS browser-source flicker): both delta overlays flickered a
    bit in OBS via the loader. Two front-end fixes (shared page, so both
    modes): (a) `.card` promoted to its own compositor layer
    (`transform: translateZ(0); backface-visibility: hidden`) so OBS repaints
    only the card, not the whole transparent canvas; (b) all render helpers
    (`renderHeader/renderDelta/renderRef/renderSectors` + a `setText` helper)
    now write to the DOM ONLY when a value actually changed (guarded via
    element `dataset`), so unchanged frames trigger no repaint. JS syntax
    node-checked. Needs an overlay restart + OBS source cache clear to load
    the new page.

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
way this session (NOTE 2026-10-21: the bit VALUES below were wrong — tool
0x0004, UI hidden 0x0008, auto shot 0x0010; see the Oct 21 entry):
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
