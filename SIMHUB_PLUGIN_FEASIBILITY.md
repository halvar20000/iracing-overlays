# SimHub Plugin — Feasibility Assessment

*Date: 2026-07-01 · Scope: can the Python iRacing-overlays project become / feed a SimHub plugin?*

> **Refined goal (2026-07-01):** the aim is **driving dashboards inside SimHub for
> iRacing** — not streaming. Wanted: Live Standings Tower, Track Map (restyled), Race
> Logger (statistics), Quali Delta, Corner Cues. See the dedicated section
> **"Driving dashboards inside SimHub"** below — it supersedes the streaming-focused
> analysis for this use case.

## TL;DR

**Technically yes, but there is no "convert Python → plugin" path — SimHub plugins are
compiled C# / .NET Framework 4.8 DLLs.** The Python code cannot be wrapped; anything
"native" is a rewrite. The good news: your *rendering* isn't the valuable part — your
**logic** (incident detection, championship deltas, spectator delta, Driver-of-the-Day,
flag state machine) is, and that logic is portable. What's worth doing depends on the goal.

You selected three goals. Verdict per goal:

| Goal | Verdict | Effort | Path |
|------|---------|--------|------|
| Distribute to SimHub community | Feasible, but = a real C# port of the *logic* (not the overlays) | High | A |
| Consume SimHub data in overlays | Easy, but low value for iRacing | Low | B |
| Just show overlays while streaming | **Already solved** — nothing to build | None | C |

---

## Driving dashboards inside SimHub (the real goal)

Good news: this is a **much better fit** than the streaming angle. SimHub is built exactly
for on-screen driving dashboards, and it can push them to a second monitor, a tablet/phone
(web dash), or a dash device — plus drive LEDs and shakers off the same data. But there's
one hard rule to keep in mind:

> **SimHub does not render your existing HTML/CSS overlays.** A "SimHub dashboard" is built
> in SimHub's own **Dash Studio** editor (controls + NCalc/JavaScript bound to
> *properties*) and shared as a `.simhubdash` file. So "using your dashboards in SimHub"
> means **rebuilding the visuals in Dash Studio**, bound to either (a) SimHub's built-in
> iRacing properties, or (b) a **custom C# plugin's** properties where your own logic is
> needed. The look can match; the implementation is native SimHub, not your HTML.

### Per-dashboard verdict

| Dashboard | Data already in SimHub? | Needs a C# plugin? | Effort | Notes |
|-----------|-------------------------|--------------------|--------|-------|
| **Live Standings Tower** | ✅ Yes (native leaderboard, live-sorted, gaps, best lap, classes) | ❌ No | Low–Med | Pure Dash Studio rebuild of the visual. SimHub already sorts live and has class support. |
| **Quali Delta** (driving mode) | ✅ Yes (SimHub computes deltas: session-best, all-time; predictive delta for *your* car) | ❌ No | Low–Med | Your DRIVING mode maps to native properties. Big centre delta + sector chips = a normal dash. (Spectator mode is broadcast-only — irrelevant for driving.) |
| **Track Map** (restyled) | ⚠️ Partial (SimHub has a native map recorder + map control) | ➖ Optional | Med | SimHub's own map may be enough. To use *your* `tracks/*.json` geometry + styling, a light plugin exposes the outline + car pcts and a JS/SVG control draws it. |
| **Corner Cues** | ❌ No native equivalent | ✅ **Yes** | High | Your most distinctive feature. Port the geometry/curvature analysis (`iracing_drivingline.py`) to C#, expose next-corner / distance / severity / direction as properties, render in a dash. Best plugin candidate — SimHub has nothing like it. |
| **Race Logger** (stats) | ❌ No (SimHub doesn't write your JSONL logs) | ✅ Yes, *if* it must live in SimHub | High | But reconsider: logging is a **background** job, not a driving readout. Your Python logger already runs independently while you drive — it doesn't need to be a SimHub plugin at all. |

### Two routes to "dashboards while driving"

**Route 1 — Native SimHub** (what you asked for). Rebuild Standings + Quali Delta as Dash
Studio dashboards (no code); optionally a light plugin for your Track Map style; a real C#
plugin for Corner Cues. Leave Race Logger as your existing Python background process.
- **Pros:** true SimHub integration — tablet/phone display, dash devices, LEDs/shakers off
  the same data; shareable `.simhubdash` files; robust.
- **Cons:** each visual is rebuilt in Dash Studio; Corner Cues needs a C# port of the
  geometry math.

**Route 2 — Your own always-on-top overlay windows** (the shortcut you may not have
considered). You already built this pattern: `driving_line_window.py` is a transparent,
click-through, always-on-top window over borderless-windowed iRacing. Generalise it into a
lightweight **webview wrapper** (e.g. pywebview/CEF) that hosts *any* of your existing HTML
overlays as an on-top window while driving — Standings, Quali Delta, Track Map, Corner Cues
— **reusing your overlays as-is, zero rewrite.**
- **Pros:** near-zero effort, reuses everything you've built, single source of truth with
  your OBS overlays, works today.
- **Cons:** no SimHub hardware/tablet integration; you place the windows yourself;
  single-PC only.

### Recommendation for the driving goal

The deciding question is *why in SimHub specifically*:

- If you want these on a **tablet/phone/second screen or feeding LEDs/shakers** → **Route 1
  (native SimHub)**. Start with **Standings Tower + Quali Delta** (no code, quick wins),
  then invest in the **Corner Cues plugin** as the real value-add. Skip a SimHub Race
  Logger — keep the Python one.
- If you mainly want them **visible on your triple screen while driving** → **Route 2** gets
  you ~80% of the outcome for ~10% of the effort by reusing your existing overlays. This is
  the pragmatic path and it builds directly on code you already wrote.

Many people assume "in SimHub" is the only way to get a driving readout; Route 2 is worth a
serious look before committing to rebuilding visuals in Dash Studio.

---

## Background: how the two systems differ

**Your project:** Python + Flask + `pyirsdk`. Reads iRacing telemetry directly, runs its
own detection/analysis logic, renders its *own* HTML/CSS overlays, served over HTTP and
added to OBS as browser sources. ~16 overlays on ports 5000–5014 + 8080.

**SimHub:** A C# / .NET Framework 4.8 desktop app. It reads the telemetry itself (it has
its own iRacing provider). Plugins are compiled DLLs implementing `IPlugin`,
`IDataPlugin`, `IWPFSettings`, with an `Init()` (runs once) and a per-frame
`DataUpdate(PluginManager, ref GameData)` (must be fast — no loops). A plugin's job is to
expose **properties** and **actions**, which the user then binds inside SimHub's own
dashboard editor, LED profiles, bass-shaker/motion effects, and Stream Deck.

**Key consequence:** SimHub is *driver-HUD and hardware oriented*. It does not embed
arbitrary external web pages as native dashboards, and it does not run Python. So your
overlays cannot be "shipped as SimHub dashboards" without rebuilding each one in SimHub's
editor. What *is* natively shareable is a plugin that provides **properties**.

---

## Goal 1 — Distribute to the SimHub community

**What's actually distributable:** not your HTML overlays, but a **C# plugin that exposes
your distinctive computed values as SimHub properties** — e.g. `IncidentType`,
`IncidentDriver`, `ChampionshipDelta`, `DriverOfTheDay`, `SpectatorDelta`, `FlagState`.
Community members bind those in their own dashboards, LED profiles, and shakers.

**Why a bridge won't do for public release:** a thin plugin that just HTTP-fetches from
your running Python servers would force every user to install Python and run your servers
— a poor community experience and effectively unshippable. For a clean public plugin, the
logic must be **native C#** reading SimHub's `GameData`.

**So the real work is porting the logic, not the rendering.** The candidates worth porting
(they're genuinely novel vs. what SimHub already offers):

- **Incident / spin / collision detection** (your speed-collapse + yellow-zone model).
- **Live championship projection** — but this depends on your CLS league-manager API, so
  it's league-specific, not general community value.
- **Spectator delta** (computing a delta for other cars) — broadcast-specific.
- **Driver-of-the-Day** scoring.
- **Flag / white-flag state machine** for timed races.

**Reality check:** several of these (championship, spectator delta, camera/replay control,
race logging) are *broadcast/production* features. SimHub's audience is mostly drivers
wanting HUDs and haptics. The subset with broad appeal is probably **incident detection**
and maybe **flag state**. Effort is high (real C# reimplementation + testing against live
iRacing), payoff is niche.

**Recommendation:** only pursue if you specifically want a presence in the SimHub
ecosystem. If so, scope it to *one* crowd-pleasing property set (incident detection) as a
v1, not the whole suite.

---

## Goal 2 — Consume SimHub data in your overlays

**Feasible and low-effort, no C# required.** SimHub can expose its properties to external
consumers over the network:

- Its built-in dashboard web server, and
- Community add-ons like **SimHub Property Server** (exposes properties over a TCP socket).

Your Python overlays could read SimHub properties from that channel instead of, or
alongside, `pyirsdk`.

**But: what would you actually gain for iRacing?** You already read iRacing directly via
`pyirsdk`, which gives you everything SimHub has *and more* (replay control, camera
switching, per-car arrays SimHub doesn't surface). SimHub would only add value for values
*it* computes that you don't — or telemetry from *other* sims (ACC, AMS2, rF2). For an
iRacing-only broadcast stack, this is largely redundant.

**Recommendation:** skip unless you plan to support non-iRacing titles, where SimHub's
multi-game normalization would save you writing per-sim readers.

---

## Goal 3 — Just show overlays while streaming

**Already done.** Your overlays are HTTP-served web pages added to OBS as browser sources
— which is exactly how SimHub streamers use SimHub's *own* web dashboards. There is no gap
to close here. SimHub cannot cleanly embed your external web overlays into its native dash
rotation, so OBS remains the correct tool. **No work needed.**

---

## Bottom line & recommended path

1. **Streaming display (Goal 3): nothing to do.** Keep OBS browser sources.
2. **Consuming SimHub data (Goal 2): don't, unless you go multi-sim.** Redundant for
   iRacing-only.
3. **Community distribution (Goal 1): possible but it's a genuine C# project** — a port of
   your *logic* (starting with incident detection), not a repackaging of the overlays.

**Architectural takeaway for the future:** your real IP is the *analysis layer*, not the
Flask rendering. If you keep that logic cleanly separable in the Python (it mostly already
is, via `iracing_sdk_base.py` + per-overlay `_read_snapshot`), a future C# port becomes a
translation exercise rather than a redesign. That's the single most useful thing to keep
tidy if SimHub distribution ever becomes a priority.

**If you want a quick personal win instead of public distribution:** a thin bridge plugin
(C#, ~few hundred lines) that reads your running Python servers and re-exposes a handful of
values as SimHub properties would let SimHub trigger your **LEDs / bass shakers / motion**
from your smart logic. Not shippable to the community, but powerful for your own rig. Say
the word and I'll scaffold it.

---

## Sources

- [Plugin and extensions SDKs — SHWotever/SimHub Wiki](https://github.com/SHWotever/SimHub/wiki/Plugin-and-extensions-SDKs)
- [Using the SimHub SDK — simhubdash.com](https://www.simhubdash.com/community-2/projects/using-the-simhub-sdk/)
- [blekenbleu/SimHubPluginSdk — portable plugin SDK demo](https://github.com/blekenbleu/SimHubPluginSdk)
- [pre-martin/SimHubPropertyServer — properties over TCP](https://github.com/pre-martin/SimHubPropertyServer)
