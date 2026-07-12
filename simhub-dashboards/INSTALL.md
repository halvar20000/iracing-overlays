# Installing SimHub Pro Dash

Two parts: the **plugin** (a DLL that computes the pro telemetry) and the
**dashboards** (six `.simhubdash` layouts). The one-click installer does both.

## Quick install (Windows)

1. **Get the plugin DLL.** Open `plugin/SimHubProDash.sln` in Visual Studio 2022
   and Build (Release). This drops `User.SimHubProDash.dll` into `install/dist/`
   (and into your SimHub folder). *If someone already committed the DLL to
   `install/dist/`, skip this step.*
2. **Close SimHub completely** (also exit it from the system tray — it locks the DLL).
3. Double-click **`install/Install ProDash.bat`** and approve the admin prompt.
   It will:
   - copy `User.SimHubProDash.dll` into your SimHub program folder, and
   - extract all six dashboards into `Documents\SimHub\DashTemplates\`.
4. Start SimHub. If it asks to enable the **SimHub Pro Dash** plugin, click **Yes**.
5. The dashboards appear in **Dash Studio** as `ProDash ...`. Add one as an OBS
   Browser source or show it on your wheel/DDU.

If the installer can't find SimHub, run it pointing at your install folder:

```
powershell -ExecutionPolicy Bypass -File "install\Install-ProDash.ps1" -SimHubDir "D:\SimHub"
```

## The dashboards

| Dashboard            | Size      | Use                                                    |
|----------------------|-----------|--------------------------------------------------------|
| ProDash DDU          | 800×480   | In-car GT3 DDU (gear, delta, tyres, fuel, controls)    |
| ProDash Round        | 800×800   | Round wheel display (RPM/throttle/brake arcs, controls)|
| ProDash Pit Wall     | 1920×1080 | Full pit-wall (leaderboard, tyres, charts, status, map)|
| ProDash Relative     | 800×480   | Relative — 4 ahead / 4 behind, you centred             |
| ProDash Leaderboard  | 800×480   | Full-field leaderboard (POS/#/driver/iR/best/gap)      |
| ProDash Standings    | 1280×720  | Session standings overlay                              |

Most values are cross-sim (native SimHub); iRacing-only values (SoF, per-car
iRating, computed sectors, relative) come from the plugin. See `PROPERTIES.md`.

## Editing & re-generating

The `.simhubdash` files in `release/` are the shipped versions. They're produced
by the Python generators in `generators/` (run from that folder). If you tweak a
dashboard in Dash Studio, export it and it becomes the new release copy.
