# iCASControl

A race control / stewarding tool for iRacing leagues, modelled on the
[iRaceControl](https://iracecontrol.com/) software. It connects to a running
copy of iRacing, shows live timing, an animated track map and a full incident
log, and gives a race director the tools to manage a race.

This is **version 0.1** — a working foundation. The core race-control loop is
built and runs, with real circuit maps for 200+ tracks and the ability to
replay recorded races; the larger automation features (Sequencer, Auto Steward,
exports, networking) are planned in [`ROADMAP.md`](ROADMAP.md).

![dashboard](dashboard_preview.png)

## Three ways to run

The application is built around a swappable *data source*, so it behaves
identically whether the race data is live, simulated or recorded:

**Simulator mode** runs a complete, believable GT3 + GT4 race entirely inside
the app — no iRacing needed. It runs on the real Silverstone circuit and works
on any computer (Windows, Mac, Linux), ideal for learning the interface,
developing new features and recording videos.

**iRacing mode** reads live telemetry from a running copy of iRacing through
its SDK, and draws the real circuit map (200+ tracks bundled). Windows only,
with iRacing running.

**Replay mode** plays back a previously recorded race from a `.jsonl` log —
useful for reviewing incidents after the event, for demos, and for testing.

When you start the app it tries iRacing first; if iRacing is not running it
falls back to the simulator automatically. If you start iRacing later, the app
notices within a few seconds and switches over to the live data on its own.

## Quick start

You need **Python 3.10 or newer** installed.

On **Windows** (your iRacing PC), just double-click **`run_windows.bat`**. The
first run creates a small Python environment and installs everything — that
takes a minute or two. Every run after that starts in a few seconds. When it
is running, open **http://localhost:8080** in your browser.

On **macOS / Linux**, open a terminal in this folder and run `bash run.sh`,
then open the same address. (These platforms always use the simulator.)

To force the simulator even on your iRacing PC, run from a terminal:

```
python -m backend.server --sim
```

## Replaying a recorded race

To play back a recorded race instead of going live, point the app at a
`.jsonl` log file. A few sample races are bundled in the `logs/` folder:

```
python -m backend.server --replay logs/20260519-195232_suzuka_international_racing_course_grand_prix_race.jsonl
```

Add `--replay-speed 4` to play it back at 4× speed. The dashboard behaves
exactly as in a live race — the timing, track map and incident log all update —
so a steward can review a finished race, scrub through the incidents and see
exactly what happened. (These logs come from the companion race-logger; the
roadmap covers recording iCASControl's own races.)

## Setting up iRacing

For iRaceControl-style tools to see every car, iRacing has to be told to
request data for the whole field. In iRacing, go to **Options → Graphics** and
set **Max Cars** to **63**. Run iRacing and this app both *without*
administrator rights (or both *with* — just keep them the same).

## Using the dashboard

The header along the top shows the clocks, the incident counter, the session
type and state, the current flag, and a **RUN / STOP** toggle (STOP pauses data
collection so you can review without new data coming in).

The **track map** on the left animates every car as a numbered roundel in its
class colour. The car you have selected gets a red ring, the leader a gold one,
and any car off-track is highlighted in yellow.

The **live timing** table in the centre lists every car with position, lap,
last/best lap, gap or interval, incident count, pit stops and speed. The small
toggles in its header switch driver/team names, gap/interval and the speed
column. Click a row to select that car; double-click to (in iRacing) jump the
camera to it.

The **race log** on the right records every incident, off-track, pit stop, flag
and penalty. The filter buttons hide categories you do not need. Click an
incident to select it, then use the **incident action** buttons below the
command panel to record a decision (Noted, Investigating, No Action, Race
Incident, Drive Through, Stop/Go, Time Penalty, DSQ).

The **race control** buttons issue commands — deploy/end the pace car, open or
close the pit lane, wave lapped cars, post red/green flags. The **selected car**
panel (bottom left) shows that car's detail and per-car actions (0x, notify,
wave-around, end-of-line, DSQ).

## Incidents, replays and the autosave (October 2026)

**Incidents in iRacing mode come from two places:**

* *Official points* - iRacing's own 1x / 2x / 4x per car. iRacing sends the
  other drivers' points **live only to an admin of the session**; a spectator
  gets them at the finish (seen in every race log we have). So run
  iCASControl on a PC whose iRacing account is admin of the session. Until
  points arrive, the "Total Inc" box shows a yellow `*`.
* *Detected* - spins, contacts (two cars collapsing together, or one alongside)
  and cars stopped on track, from car motion (the dashboard's tested method).
  They appear as `Spin (detected)` / `Contact with #14 (detected)` with a DET
  badge. When official points for that car arrive within 20 s they are put on
  the same line (`Contact with #14 - 4x (total 8x)`), otherwise points get a
  line of their own.

**Replay:** click any log line, then **Replay** (from 8 s before) or **-20s**;
double-clicking a line does the same. iRacing jumps there, puts the camera on
that car (your camera group, never Scenic) and plays at 1x. **LIVE** (or the
violet REPLAY bar at the top) goes back. While iRacing shows a replay nothing
is logged from it - the clocks and the timing table stay on the live race.

**Autosave:** the log, every decision, notes and the incident baseline are
saved every 10 s to `data/<SessionID-SubSessionID>.json`. Restart the app (or
the PC) during the race and it carries on where it was; points scored while it
was down are logged as one line each, never doubled. **Save** (race log
header) downloads the same file.

## Steward commands into iRacing (step 2, October 2026)

Penalties and race control go into the **iRacing chat** as admin commands
(official syntax, support.iracing.com article 31000133518). **The iRacing
account on this PC must be admin of the session** - otherwise iRacing ignores
them. Header switch **iRACING CHAT** (OFF by default, kept in
`data/chat_config.json`); **Test** sends `/rc iCASControl chat test` (only the
admins see it) even while OFF.

| Dashboard | iRacing chat |
|---|---|
| Deploy Pace Car / End Pace Car | `!yellow` / `!pacelaps 1` |
| Pit Open / Pit Close / Clear Penalties | `!pitopen` / `!pitclose` / `!clearall` |
| Drive Thru / Stop/Go / DSQ (incident) | `!black #14 D` / `!black #14` / `!dq #14` |
| Warning (incident) | `/all #14 Driver: WARNING` |
| Car: Wave / EOL / Clr Pens / DSQ / Notify | `!waveby #14` / `!eol #14` / `!clear #14` / `!dq #14` / `/all #14 text` |
| Message RC / Private | `/all text` / `/all #14 text` |
| Red Flag, Green Flag, Wave Lapped, Time Pen. | none in iRacing - log only |

**Right-click a driver** in the timing table (like iRaceControl): Show car
info, Penalties (Clear / Time Penalty / Drive through / Stop and Go / DQ),
Pacing (End of line / Wave around), Notify, Add / Remove admin
(`!admin` / `!nadmin`), Enable / Disable chat (`!chat` / `!nchat`), Remove
(`!remove`). Penalties ask for a reason (free text or Track Limits / Forcing
another driver off the track / Causing a collision); since `!black` takes no
text, the reason follows as `/all #14 Driver: DRIVE THROUGH - Track Limits`.
A time penalty has no iRacing command: only that announcement goes out.

How it types: the SDK opens the chat (`chat_command begin_chat`), then
method **post** (default) posts each character as WM_CHAR + Enter to the sim
window (`SimWinClass`) in the background; method **focus** brings iRacing to
the front for a moment and types with SendInput. After a black flag, DQ, EOL,
wave-by, clear or yellow the flag bits are watched for 6 s: the log says
"iRacing confirmed" or "did NOT confirm (admin? method?)". Templates can be
overridden in `data/chat_config.json` → `"commands": {"notify": "..."}`.
In simulator / replay mode the commands are only written to the log.

## Auto Steward (step 3c, October 2026)

Header switch **AUTO STEWARD** (on by default) + **Rules** (saved in
`data/steward_config.json`). The rules (`backend/steward.py`):

* **Incident limit** — when a car reaches 15x, and again at 21x, 27x … (every
  further 6x), an amber `AUTO STEWARD: … drive through due` line in the log
  plus a pop-up. Only a message: the league's iRacing settings give the
  drive-through. Race sessions only; the level is autosaved per car, so a
  restart does not repeat it.
* **Full course caution** — the official incident points of ALL cars
  together reach *N*x within *S* seconds in a race under green (Rules dialog;
  default 10x within 10 s — points need admin, detections alone do not
  count). Mode *auto* (default): `!yellow` goes out at once; *ask*: an amber
  line with a **DEPLOY CAUTION** button (2 min). Not under a caution, not for
  180 s after one it called; incidents of a caution never count after the
  restart.
  *Caution length* (Rules, 0 = iRacing decides): once the caution is out,
  `!pacelaps N` goes out. By hand: **Caution laps** under the race-control
  buttons — a number + **Set**, or **−1 / +1** (iRacing only takes it under
  a caution, as admin).
* **Qualifying AM / PRO** (GT3 WCT, off by default — switch it on for WCT):
  at the start of a qualifying `/all QUALIFYING: THE FIRST 15 MINUTES ARE
  RESERVED FOR AM DRIVERS`, after 15 min `/all AM QUALIFYING FINISHED - PRO
  QUALIFYING STARTED` (texts editable; also on the stream banner when it is
  on). Started mid-quali (> 3 min) = no start message.
* **Blue flag** — a car about to be LAPPED, with the lapping car ≤ 1.0 s
  behind (track gap × the faster car's lap time): `/all #14 BLUE FLAG - let
  #7 Driver pass` + a log line; again after 20 s if he still holds him up,
  re-armed once the pass is done. Not in the pits, not under a caution, not
  between cars on the same lap.

Chat lines only go out while **iRACING CHAT** is on (blue flags with the chat
off are only logged — no "not sent" line each time).

## Decisions on the stream banner (step 3, October 2026)

Header switch **STREAM BANNER** (off by default) + **Address** (the Race
Control overlay `iracing_rc_banner.py` on the stream PC, e.g.
`http://192.168.86.138:5019`) + **Test**. Saved in `data/stream_config.json`
(`"kinds": {"warning": false}` keeps a kind off the stream).

| Decision | Banner |
|---|---|
| UNDER INVESTIGATION | blue INVESTIGATION, "#14 and #7 - reason" |
| NO ACTION / RACE INCIDENT | NO FURTHER ACTION |
| WARNING | white WARNING + reason |
| DRIVE THROUGH / STOP/GO / TIME PENALTY (+ seconds) / DSQ | black PENALTY + reason |
| Clear penalties (car menu) | CLEAR |
| RC message to all | RACE CONTROL message |

NOTED, EOL, wave-by, admin / chat commands never go on air. Each message is a
`POST /rc/steward` to the banner; the log says "On the stream banner: …" or
"Stream banner NOT reached (…)". iRacing's own black flag / DQ for the same car
within 20 s is not shown a second time by the banner.

The banner also keeps the OPEN penalties per car (`GET /rc/penalties`), and
the standings tower (5005) shows them after the name: **DT**, **SG**, **+10s**
(time penalties add up), red **DSQ**, blue **INV** (under investigation, until
a decision or 10 min). A DT / S&G is gone once iRacing's black flag for the car
goes off (served) or the penalties are cleared. Penalties from iRaceControl
(ATVO) and iRacing's own black flags count too.

## Several stewards at once (0.1.9, October 2026)

One **main iCAS** (the PC whose iRacing account is session admin) holds the
race log, the decisions and the chat into iRacing. The other stewards open
its dashboard in their browser:

* **STEWARDS** (header) - who is online, the steward chat (never reaches
  iRacing; a toast + red counter when a message comes in), your name, and -
  only on the main PC itself - the **Access password**.
* Access: the main PC itself always; the home network without a password as
  long as none is set; over the **internet** (tunnel / proxy headers) ONLY with
  the access password - with none set, internet stewards are refused. Only a
  PBKDF2 hash is stored (`data/access_config.json`).
* Every log line / decision says **who** did it (`· Thomas`), an incident can
  be taken with **I take it** (`👀 Andreas` for the others, Release frees it).
* **Camera** tab: every camera group of the track (no Scenic), ◀ ▶ = car
  ahead / behind, "Selected car". It switches the iRacing on the MAIN iCAS
  PC - not a remote steward's own (use Follow mode for that, below).
* `/api/*` (Save download, status) from outside needs the steward's login
  token (the Save link carries it).

### GT3 WCT ruleset in the Auto Steward (0.1.11)

Race only, all in Rules: iRacing's blue flag shown 20 s = private warning,
30 s = drive through + message to everybody; fast repairs used up = private
message; meatball = message to everybody; full course caution 40x / 15 s,
not in the last 5 minutes. Texts with %cnr% / %dname% like iRaceControl.
Private chat lines are `/14 text` (only that driver sees them).

### Internet access for the stewards (0.1.10)

On the MAIN PC: STEWARDS > **Access password** first, then **Internet > Start**.
iCAS starts `cloudflared` (Cloudflare quick tunnel, no account, no router
setting) and shows a link `https://<words>.trycloudflare.com` - **Copy link**
and send it to the stewards. A new link on every start; Stop = the link is
dead. No cloudflared yet: **Download cloudflared** (official release from
github.com/cloudflare, saved to `data/bin/`) or `winget install
Cloudflare.cloudflared`. Removing the password stops the tunnel. A fixed
address instead: create a named tunnel in Cloudflare (public hostname ->
`http://localhost:8080`) and put `{"token": "...", "hostname": "icas.example.com"}`
into `data/tunnel_config.json`.

### Follow mode (SUPP, 0.1.10)

A steward who has iRacing himself (spectating the same session) runs his own
iCAS and uses STEWARDS > **Follow main iCAS…** (the link, his name, the access
password). His dashboard then shows the MAIN iCAS - log, timing, decisions,
steward chat - and everything he does goes to the main iCAS under his name.
Only **replay** and **camera** stay on his PC: they move HIS iRacing, never the
main PC's (= the stream). His own Auto Steward / autosave pause while
following. A warning shows when his iRacing is in another session. The
password is not saved (asked again after a restart). RUN / STOP of the main
iCAS only on the main PC itself.

## What works today vs. what is honest to flag

Live timing, the real track map, the incident/event log, gaps and intervals,
pit detection, NIW counting, flag handling, manufacturer logos, the steward
decision workflow and all race-control commands work fully **in simulator and
replay mode** — that is what the screenshot above shows.

For **live iRacing**, the timing, positions, surfaces, flags, weather and the
circuit map are read straight from the SDK and should be accurate. Two things
are deliberately honest limitations of this version, both explained in the
roadmap:

* Official incident points arrive live only for a session admin (see above);
  the detection works for everybody.
* Admin actions go in as chat commands (see above) - built, but not yet tried
  in a live session.

These are testable and tunable on your Windows rig — the simulator let the
whole interface be built and verified without a live session.

## Project structure

```
backend/
  server.py              FastAPI server, race loop, WebSocket streaming
  race_state.py          source-independent race engine (log, incidents, penalties)
  models.py              shared data structures
  tracks.py              loads + projects the bundled circuit geometry
  car_brands.py          car-manufacturer detection + logo resolution
  sources/
    base.py              the DataSource interface
    simulator.py         the built-in race simulator
    iracing_source.py    the live iRacing SDK bridge (pyirsdk)
    replay_source.py     plays back a recorded .jsonl race log
frontend/
  index.html             the dashboard
  style.css              dark race-control theme
  app.js                 dashboard logic + WebSocket client
  trackmap.js            animated canvas track map
  brands/                manufacturer logo SVGs
assets/tracks/           200+ bundled circuit geometry files (+ NOTICE.txt)
logs/                    sample recorded races for replay mode
run_windows.bat          one-click launcher for Windows
run.sh                   launcher for macOS / Linux
requirements.txt         Python dependencies
ROADMAP.md               planned features toward full iRaceControl parity
```

## Notes

The app runs entirely on your own machine. The dashboard is served locally and
nothing is sent anywhere. To use it on a second screen, tablet or a co-steward's
laptop on the same network, start it with `python -m backend.server --host
0.0.0.0` and open `http://<your-pc-ip>:8080` from the other device.

## Credits

Circuit geometry in `assets/tracks/` is derived from the
[SIMRacingApps](https://github.com/SIMRacingApps/SIMRacingAppsServer) project by
Jeffrey Gilliam, licensed Apache-2.0 — see `assets/tracks/NOTICE.txt`. The track
files, manufacturer logos and sample race logs were carried over from Thomas's
companion iRacing broadcast-overlay project.
