# SimHub Pro Dashboards (free & open)

On-rig driver dashboards for **iRacing** inside **SimHub** — the tablet / 2nd-monitor /
USB-DDU counterpart to this project's OBS overlays. Same philosophy as the overlays:
**fully open source, free to use, no ads, no subscription, no plugin paywall.**

> **Ambition bar:** genuinely pro-grade — the free equivalent of the paid/plugin tier
> people expect from commercial dash packs. Inspired by that tier, but **not a clone** of
> anyone's UI. All visual designs here are original. See `NOTICE.md`.

## Why there's a plugin, not just dashboards

A `.simhubdash` file is only a visual layout built in SimHub's **Dash Studio**. The layout
is the easy part. The hard part — the "pro" data — is **computed telemetry** that SimHub
does not expose natively for iRacing:

- **Fuel-to-finish** — per-lap burn, laps of fuel left, fuel to add, save target
- **Relative with iRating / SoF** — the cars around you, their iRating, licence, live gap
- **Pit-stop predictor** — service time, pit loss, undercut/overcut window
- **Proximity / spotter** — side awareness + closing car ahead/behind

Commercial packs precompute these in a **closed-source** plugin, which is why their dashes
can't run without it. To match that tier while staying free and open, this project ships
its **own small, open-source SimHub plugin** (`plugin/`) that exposes the same kind of
computed properties. The dashboards (`dashboards/`) are built on top of it in Dash Studio
and bind to those properties.

## Layout

```
simhub-dashboards/
├── plugin/                     # C# SimHub plugin — the computed "pro" properties (the engine)
│   ├── SimHubProDash.sln
│   ├── SimHubProDash.csproj
│   ├── Plugin.cs               # IPlugin / IDataPlugin / IWPFSettingsV2 entry point
│   ├── PluginSettings.cs
│   ├── IRacingRaw.cs           # safe access to the raw iRacing SDK data (Telemetry + SessionData)
│   ├── IProModule.cs           # module interface
│   ├── FuelModule.cs           # fuel-to-finish
│   ├── RelativeModule.cs       # relative + iRating / SoF
│   ├── PitModule.cs            # pit-stop predictor
│   ├── ProximityModule.cs      # spotter / proximity
│   ├── LeaderboardModule.cs     # full-field leaderboard (ProDash.Board.*)
│   ├── SectorModule.cs         # live S1/S2/S3 sector timing
│   ├── AssemblyInfo.cs
│   └── BUILD.md                # compile in Visual Studio + install into SimHub
├── release/                    # the six installable dashboards (.simhubdash) — SHIP THESE
│   ├── ProDash DDU.simhubdash
│   ├── ProDash Round.simhubdash
│   ├── ProDash Pit Wall.simhubdash
│   ├── ProDash Relative.simhubdash
│   ├── ProDash Leaderboard.simhubdash
│   └── ProDash Standings.simhubdash
├── generators/                 # Python generators that produce the .simhubdash files
│   ├── gen_ddu.py  gen_round.py  gen_pitwall.py
│   ├── gen_relative_ddu.py  gen_leaderboard_ddu.py
│   └── template.zip            # base SimHub export the generators build from
├── install/                    # one-click installer
│   ├── Install ProDash.bat     # double-click (elevates)
│   ├── Install-ProDash.ps1     # copies DLL to SimHub + extracts dashboards to DashTemplates
│   └── dist/                   # put the built User.SimHubProDash.dll here (build does this)
├── dashboards/                 # layout specs (design notes)
│   ├── tyre-fuel/SPEC.md
│   └── relative-standings/SPEC.md
├── INSTALL.md                  # install guide
├── PROPERTIES.md               # reference: every property the plugin exposes
├── NOTICE.md                   # attribution + "not a clone" statement
└── LICENSE                     # MIT
```

## Status — shipping

All six dashboards are built and verified live in iRacing, on top of the plugin:

| Dashboard            | Size      |
|----------------------|-----------|
| ProDash DDU          | 800×480   |
| ProDash Round        | 800×800   |
| ProDash Pit Wall     | 1920×1080 |
| ProDash Relative     | 800×480   |
| ProDash Leaderboard  | 800×480   |
| ProDash Standings    | 1280×720  |

**Install:** see `INSTALL.md`. Build the plugin once (Visual Studio → drops
`User.SimHubProDash.dll` into `install/dist/`), then double-click
`install/Install ProDash.bat` — it copies the plugin into SimHub and extracts all
six dashboards into `Documents\SimHub\DashTemplates\`.

## How it fits the rest of the project

The computed logic mirrors what the Python OBS overlays already work out
(`iracing_standings.py` ordering/gaps, fuel thinking, deltas). The stacks differ
(Flask/browser vs. SimHub WPF + C#), but the **telemetry model is identical**: SimHub hands
the plugin the raw iRacing SDK sample (`CarIdx*` arrays + `DriverInfo.Drivers[]`), the same
data `pyirsdk` exposes. So the analysis ports over rather than being reinvented.

See `../SIMHUB_DASHBOARDS_PLAN.md` for the project plan and decisions.
