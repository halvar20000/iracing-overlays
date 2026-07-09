# Building & installing the SimHub Pro Dash plugin

## Prerequisites

- **Windows** with **SimHub** installed (the race PC). Default path:
  `C:\Program Files (x86)\SimHub\`.
- **Visual Studio 2022** (Community is fine) with the **.NET desktop development**
  workload, **or** the **.NET Framework 4.8 Developer Pack** + `dotnet` / `msbuild` CLI.

The project references DLLs that ship *inside* SimHub — nothing is downloaded. If SimHub is
installed elsewhere, pass the folder when building (keep the trailing backslash):

```
msbuild SimHubProDash.csproj /p:Configuration=Release /p:SimHubDir="D:\Games\SimHub\"
```

## Build in Visual Studio

1. **Close SimHub** (the build copies the DLL into the SimHub folder; it can't overwrite a
   DLL that's loaded).
2. Open `SimHubProDash.sln`.
3. Set configuration to **Release**, platform **Any CPU**.
4. **Build → Build Solution** (Ctrl+Shift+B).
5. On success, `User.SimHubProDash.dll` is compiled and — via the post-build step —
   copied into the SimHub folder automatically. To disable auto-copy, build with
   `/p:InstallToSimHub=false` and copy the DLL yourself.

## Build from the command line

```
cd simhub-dashboards\plugin
msbuild SimHubProDash.csproj /p:Configuration=Release
```

(or `dotnet build -c Release` if the .NET SDK is installed.)

## First run in SimHub

1. Start **SimHub**.
2. SimHub detects the new plugin and asks whether to enable it → **Yes**.
3. A **"Pro Dash"** entry appears in the left menu.
4. Start iRacing (a test/AI session is enough). SimHub's status goes to **Running**.
5. In **Dash Studio**, the computed properties are available under
   `SimHubProDash.ProDash.*` — see `../PROPERTIES.md`.

## Verifying the raw-data accessors (first compile)

The plugin reads the raw iRacing sample by *name* through reflection
(`IRacingRaw.cs`), so it compiles without referencing `IRacingReader` / `iRacingSDK`.
The channel names used (`CarIdxLapDistPct`, `FuelLevel`, `DriverInfo.Drivers[].IRating`,
`WeekendInfo.TrackLength`, `CarLeftRight`, …) are the standard iRacing SDK names — the same
ones this repo's Python overlays use — so they're stable.

If any value reads 0 in a live session, open SimHub's log
(`Documents\SimHub\logs`) and check for `[ProDash]` lines, or add a temporary
`AttachDelegate` dumping the raw object's members. The reflection layer means a wrong name
fails soft (returns default) rather than crashing.

## Troubleshooting

- **Build error: cannot find `SimHub.Plugins.dll`** → set `SimHubDir` to your SimHub folder.
- **DLL copy fails / "file in use"** → SimHub is still running; close it and rebuild.
- **Plugin not listed in SimHub** → confirm `User.SimHubProDash.dll` is in the SimHub root,
  and that you clicked "enable" on first launch (SimHub → Settings → Plugins to re-enable).
- **`TypeLoadException` on load** → the SimHub runtime DLLs are newer than expected; this
  plugin only depends on stable `IPlugin`/`IDataPlugin`/`IWPFSettingsV2` members, so this is
  unlikely, but rebuild against the current `SimHub.Plugins.dll` if it occurs.
