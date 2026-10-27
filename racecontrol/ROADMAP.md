# Roadmap — toward full iRaceControl parity

Version 0.1 delivers the foundation: a swappable data source (simulator, live
iRacing bridge and recorded-race replay), live timing, real circuit maps for
200+ tracks, an animated track map, the incident/event log, the steward
decision workflow, race-control commands and car-manufacturer logos.

This roadmap lists what the real iRaceControl does that this clone does not yet
do, grouped into sensible build phases. Nothing here is blocked — each item can
be picked up in a future session.

**Done since v0.1** — real per-track maps (200+ circuits, projected from
bundled geometry) and a recorded-race replay source, both integrated from
Thomas's companion overlay project.

## Phase 1 — make iRacing mode fully trustworthy

**Done (October 2026, step 1 of 3):** live incidents (official points when
admin + spin / contact / stopped detection), replay of any log line, replay
footage never logged, autosave + recovery. Also sector times, best lap from
lap 1, map colour modes. **Step 2 done (October 27, tested in a hosted session as
admin):** admin chat commands into iRacing (`backend/chat.py`, see README).
**Step 3a done (October 27):** decisions go to the Race Control banner 5019
(`backend/stream.py`, header STREAM BANNER). Next: penalties on the tower,
then the Auto Steward.

These items turn iRacing mode from "accurate timing" into a tool you can steward
a real CAS Community race with.

**Behavioural incident detection.** iRacing's SDK does not report a per-car
incident count for other cars. The real iRaceControl infers incidents from car
behaviour. We would do the same: watch track-surface changes, sudden speed
drops, and two cars close together going slow, and classify those into 1x / 2x
/ 4x incident estimates that the steward can confirm or adjust.

**Admin command delivery.** iRacing has no SDK broadcast for full-course
yellows, black flags or pit open/close — those are admin *chat commands*
(`!yellow`, `!black`, `!pitclose`, …). Built: the SDK opens the chat, the text
is posted as WM_CHAR (or typed with the window focused), confirmations for
penalties, and the result is checked in the flag bits. Needs its first test in
a hosted session where the PC running it is admin.

**Sector times and blue-flag logging.** Add the three-sector split to the
timing table, and calculate blue flags (iRacing does not expose them) using the
"seconds behind / delay / minimum time" tuning the manual describes.

*Real per-track maps are now done* — 200+ circuits are bundled in
`assets/tracks/` and drawn for live, simulated and replayed races. A future
refinement would be importing layouts for any circuit not yet covered.

## Phase 2 — the Sequencer

A scriptable list of timed actions for a session: random full-course yellows by
time or by lap with a probability; stage yellows; scheduled race-control
messages; timed pit open/close. Includes the "IGNORE" mechanism (a flashing
button that lets the director cancel a pending random yellow) and the ability
to save/load modular Sequencer sets.

## Phase 3 — the Auto Steward

Rule-based automatic officiating. Triggers include total incident count, "every
Nth incident", incidents within N seconds, first-lap incidents, per-car incident
totals, clean-driving streaks, blue-flag-ignored, and fast-repairs-used.
Actions include full-course yellow, RC message, incident drop, warning, drive-
through, stop/go, DSQ and time penalty. Rules can be limited to certain sessions
and car classes, with end-of-race protection.

## Phase 4 — exports and race data

**PDF race report** with league logos, configurable sections and the final
classification. **CSV export** compatible with iRacing's results format.
**Save / load / autosave** of race data so a session can be reviewed offline or
recovered after a crash (the real tool autosaves every 5 minutes).

## Phase 5 — multi-operator networking

Let several copies of the app cooperate: one **MAIN** controller acts as the
server, others connect as **SUPP** (support) controllers. The race log and race
notes sync between them, so a team of stewards can share the workload. The
browser-based design already makes a lighter version of this easy — several
people can open the same dashboard on the LAN today.

## Phase 6 — broadcast and hardware integrations

**StreamDeck plugin** for one-touch race-control commands. **SDK Gaming**
integration to push race-control messages to live-timing overlays. **ATVO**
integration (Appgineer TV-overlay) for incidents-under-review and applied
penalties. **Keyboard / wheel-button shortcuts** mapped to commands.

## Phase 7 — configuration and polish

A proper **Settings** screen: configurable timing columns, custom car classes,
roundel colour schemes, weather units, autojump behaviour, qualifying cutoff
line, incident grouping, and saveable "sets of settings". Plus replay/camera
control panel, and a packaged Windows installer so there is no Python setup at
all.

---

## Suggested next step

Phase 1 gives the biggest real-world payoff: it is what makes the difference
between "nice demo" and "I can run an IEC race with this". With real track maps
already done, the next high-value piece is behavioural incident detection —
and it can be developed and tuned directly against the bundled replay logs
without needing a live session.

> **Repeatability tip:** the daily/weekly rhythm of league races makes this a
> strong candidate for a Claude *skill* later on — e.g. "set up iCASControl
> for tonight's CAS race" could preload the right Sequencer and Auto Steward
> rules. Worth revisiting once Phases 2–3 exist.
