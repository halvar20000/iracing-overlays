# SimHub Dashboards — Project Plan (free, open, LSR-inspired)

Status: **IN PROGRESS** — plugin foundation authored (2026-07-05); dashboards still
to be built on the Windows race PC (SimHub Dash Studio + iRacing live). Kept completely
**beside** the OBS overlays (this is a separate deliverable, own folder `simhub-dashboards/`).

Last updated: 2026-07-05.

## Progress log

**2026-07-05 (foundation built):** Chose "foundation first" — authored the open-source C#
SimHub plugin that gates the pro-tier dashboards, delivered as `simhub-dashboards/`
(README, MIT LICENSE, NOTICE "not a clone", PROPERTIES.md, `.gitignore`, `plugin/` VS
project + BUILD.md, `dashboards/*/SPEC.md`).
  • **Plugin = `SimHubProDash`** (net48, `User.SimHubProDash.dll`), implements
    `IPlugin/IDataPlugin/IWPFSettingsV2`; publishes everything under `ProDash.*`.
    Four computed modules: **FuelModule** (fuel-to-finish / per-lap burn / save target /
    to-add / status), **RelativeModule** (relative box + per-driver iRating + licence +
    class colour + live gap, plus **SoF** via iRacing's formula), **PitModule** (service
    time, pit-lane loss, total loss, rejoin window — estimates, track-loss configurable),
    **ProximityModule** (spotter via iRacing `CarLeftRight` + nearest ahead/behind).
  • **Key design:** raw iRacing sample read reflectively in `IRacingRaw.cs`
    (`GetRawDataObject()` → channels by name: `CarIdxLapDistPct`, `FuelLevel`,
    `DriverInfo.Drivers[].IRating`, `WeekendInfo.TrackLength`, `CarLeftRight`, …). So the
    project compiles referencing only `SimHub.Plugins` + `GameReaderCommon`, no hard bind
    to `IRacingReader`/`iRacingSDK`, and the channel names are the SAME ones the Python
    overlays use — the analysis ports over. Session-change reset mirrors the Python lesson.
  • **Honest constraint recorded:** iRacing exposes no per-car lateral coordinates, so a
    dot-radar is impossible; the proximity module uses iRacing's own `CarLeftRight` spotter
    signal instead (documented in NOTICE/PROPERTIES).
  • **Reference used (not copied):** DahlDesignProperties (MIT) for the
    `GetRawDataObject() as IRacingReader.DataSampleEx` pattern; giantorth/moza-simhub-plugin
    decompiled API notes; SimHub SDK wiki. All implementations original.
  • **Compiled & loaded (2026-07-05, same session):** built in Visual Studio 2022
    (Community, on the race PC) → `User.SimHubProDash.dll`, auto-installed to
    `C:\Program Files (x86)\SimHub\`, and confirmed loaded — "Pro Dash" shows in SimHub's
    left menu. Everything compiled first try except one fix: **`SimHub.Logging` lives in a
    separate assembly** (`SimHub.Logging.dll`) that wasn't referenced (error CS0234). Rather
    than add the reference, the three log calls were swapped to
    `System.Diagnostics.Trace.WriteLine` so the plugin builds against only `SimHub.Plugins`
    + `GameReaderCommon` (both already proven). Also made the post-build copy to
    Program Files `ContinueOnError` so a non-elevated build still reports success.
  • **Verified live + first dashboard built (2026-07-05, same session):** properties
    confirmed populating in a live iRacing session; **Relative & Standings dashboard DONE**
    and working live (SoF 3478, 29 cars, real iRatings/licences/colour-coded gaps, player
    row highlighted). Delivered as `dashboards/relative-standings/ProDash Standings.simhubdash`
    (generator: `dashboards/relative-standings/gen_standings.py`).
  • **HOW the dashboard was built (important — Dash Studio can't be driven via desktop
    control; SimHub's WPF ignores synthetic clicks):** we did NOT hand-build in Dash Studio.
    Instead: user exported an empty dashboard, we reverse-engineered the `.simhubdash` format
    (zip of `<Name>\\<Name>.djson` + `.metadata` + `JavascriptExtensions/`), and GENERATE the
    dashboard as an importable file with `gen_standings.py`. User just does Dash Studio ->
    Import. Full schema + the critical property-prefix fact are documented at the top of
    `gen_standings.py`.
  • **CRITICAL naming fact (verified):** SimHub references plugin properties as
    `<PluginClassName>.<registeredName>` → **`ProDashPlugin.ProDash.Field.SoF`** etc.
    (not `ProDash.Field.SoF`). Bindings use JS: `$prop('ProDashPlugin.ProDash.<...>')`.
  • **Remaining / next:** (a) camera-follow fallback in the plugin (use `CamCarIdx` when
    spectating/broadcasting, not just `PlayerCarIdx`) — high value for streaming;
    (b) DDU 800x480 variant of the relative; (c) the Tyre & Fuel dashboard
    (`dashboards/tyre-fuel/SPEC.md`); (d) the `.simhubdash` binary lives in Documents\\SimHub
    + session outputs — copy into the repo folder and push (network drive can't take binary
    writes from the file tools).
  • **Session logistics note:** built with Cowork desktop control at the rig. The running
    iRacing sim constantly stole foreground focus, making live Dash Studio control
    unreliable — hence foundation-first. Also: the repo was reached via the **Y:** network
    drive (mapped `\\Tower\ai\Projects`), and Cowork's file tools **cannot create new
    directories on a network drive**, so the folder was delivered as a zip to extract into
    the repo root. (Writing into folders that already exist on Y: works fine.)

## Goal

Add a set of **SimHub driver dashboards** to this project — the on-rig display
(tablet / 2nd monitor / USB DDU device) equivalent of the OBS overlays. Same
philosophy as the overlays: **fully open source, free of use, no ads, no
subscription, no plugin paywall.**

## Ambition bar (non-negotiable — Thomas's framing)

Only worth doing if it reaches the level of the **Lovely paid / plugin tier** —
a genuinely pro-grade free alternative, not a cut-down toy. If we can't hit that
level it isn't worth building. Inspired by Lovely Sim Racing (lsr.gg), **NOT a
clone** — their UI design is license-protected, so all designs must be original.

Bonus goal: the build process and the finished dashboards make great content for
the new YouTube channel.

## Why this needs real work (the honest part)

- A `.simhubdash` file is just a zip of a JSON layout + fonts/images + optional
  JavaScript, built in **Dash Studio** (Windows-only WYSIWYG editor). The visual
  layout is the easy part.
- The hard part is the **computed "pro" data**: fuel-to-finish, relative with
  iRating / SoF, pit-stop predictor, proximity radar, incident data. Lovely
  precomputes all of that in their **closed-source C# "Lovely Plugin"**, which is
  why their dashboards can't run without it.
- To match that tier *without* their plugin, the plan is to write **our own
  small, free, open-source SimHub plugin** (C#) that exposes the same kind of
  computed properties, then build the dashboards on top of it. That plugin is the
  real engineering project; the dashboards sit on top.
- Note: a lot of this telemetry logic we've already worked out once in the OBS
  overlays (standings ordering, gaps, weather, delta, fuel thinking) — good
  reference material even though the code stacks are different (Flask/browser vs
  SimHub WPF + C#).

## Decisions so far (2026-07-05)

- **Target devices:** landscape tablet / 2nd monitor **and** USB DDU units
  (e.g. 480x272 / 800x480). Build a roomy landscape layout + a tight DDU variant.
- **First dashes to prototype:** (1) **tyre & fuel** focus, (2) **relative &
  standings**.
- **Distribution:** as `.simhubdash` files in this repo under `simhub-dashboards/`
  (community norm — DahlDesignDash, Blumlaut, mihi4 all publish dashes on GitHub).

## Build location & method (why it's deferred)

Must be done on the **Windows race PC** with SimHub Dash Studio + iRacing running:
Claude drives Dash Studio via desktop control, reads live iRacing property names,
and import-tests each dash against a real (or AI) session. This is **not**
buildable from the Cowork sandbox (no Windows/SimHub; the project folder is often
not even shell-mounted here). Hand-generating the JSON blind would just create
rework.

## When we resume (checklist for the Windows session)

1. SimHub open with **Dash Studio**; iRacing running with a live/AI session.
2. Decide plugin scope first (which computed properties to expose) — this gates
   the "pro tier" dashboards.
3. Prototype the **tyre & fuel** dash (landscape), then the DDU variant.
4. Prototype the **relative & standings** dash.
5. Commit `.simhubdash` files + the plugin source under `simhub-dashboards/`,
   push to GitHub (keep the repo up to date per project rule).

Could eventually be formalised as a Claude Skill ("author a SimHub dash from a
spec") for repeatability.

---

## STATUS 2026-07-07 — COMPLETE & BUNDLED

All six dashboards built and verified live in iRacing, plugin working:

| Dashboard            | Size      | Notes |
|----------------------|-----------|-------|
| ProDash DDU          | 800×480   | GT3 in-car, black + fine-white-line design (user's white base) |
| ProDash Round        | 800×800   | Round wheel; LSR-geometry delta fan, rotated fuel/AVG/laps, 5-box control fan, RPM + throttle/brake fill arcs |
| ProDash Pit Wall     | 1920×1080 | Leaderboard + tyres + speed/rpm/throttle-brake charts + STATUS + fuel + track map (user-tuned) |
| ProDash Relative     | 800×480   | 4 ahead / 4 behind, player centred (RelativeModule fixed 4/4) |
| ProDash Leaderboard  | 800×480   | Full-field board lifted from Pit Wall design |
| ProDash Standings    | 1280×720  | Session standings overlay |

**Bundled for GitHub** under `simhub-dashboards/`:
- `release/` — the six `.simhubdash` (user's "Final Version" exports, cleanly named)
- `generators/` — Python generators (+ `template.zip`) that reproduce them
- `install/` — one-click installer: `Install ProDash.bat` → `Install-ProDash.ps1`
  (copies `User.SimHubProDash.dll` into SimHub, extracts dashboards into
  `Documents\SimHub\DashTemplates\`); `install/dist/` holds the built DLL
- `INSTALL.md` — install guide; README + this plan updated

**Plugin lesson:** SimHub CircularGaugeItem fills via the **`Value`** binding
(with `JSExt:1`), NOT `ValueEx` — the round dash's RPM/throttle/brake arcs were
dead until fixed. Built DLL is **`User.SimHubProDash.dll`** (SimHub only loads
plugin DLLs prefixed `User.`). csproj post-build now copies the DLL into both
SimHub and `install/dist/`.

**Remaining manual step:** build the plugin once in Visual Studio (populates
`install/dist/User.SimHubProDash.dll`), then run `push_to_github.bat`.
