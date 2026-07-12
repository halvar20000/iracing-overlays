# NOTICE — attribution & originality

## Not a clone

These dashboards are **inspired by** the pro/paid tier of commercial SimHub dash packs
(e.g. the level people associate with Lovely Sim Racing). They are **not a copy**. Those
products' UI designs are their own intellectual property. Every layout, colour scheme,
widget arrangement, and graphic in this project is **original work** created from scratch.
If any element ever looks derivative, that is a bug — open an issue.

## Third-party components used at runtime

This plugin compiles against, and runs inside, **SimHub** by Wotever
(<https://www.simhubdash.com/>). SimHub and its bundled assemblies
(`SimHub.Plugins.dll`, `GameReaderCommon.dll`, `IRacingReader.dll`, `iRacingSDK.dll`,
`Newtonsoft.Json.dll`) are the property of their respective authors and are **not
redistributed** here — the plugin references the copies already installed on the user's
machine.

## References consulted (not copied)

Public, open-source material used only as API reference while writing original code:

- **SimHub Plugin & extensions SDK** — SHWotever/SimHub wiki (official SDK notes).
- **DahlDesignProperties** by Andreas Dahl (MIT) — a free, open iRacing SimHub plugin;
  consulted for the `GetRawDataObject() as IRacingReader.DataSampleEx` access pattern.
- **giantorth/moza-simhub-plugin `docs/simhub.md`** — a community-maintained, decompiled
  SimHub plugin API reference.
- **iRacing SDK** telemetry channel names (`CarIdx*`, `DriverInfo.Drivers[]`) — the same
  channels this project's own Python overlays already use.

No source code from those projects is included here; only the documented, public API shapes
were used to write original implementations.
