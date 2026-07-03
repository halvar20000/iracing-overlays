# Driving Mode — quick card

On-screen overlays **while you drive** (not for streaming): **Corner Cues**,
**Quali Delta**, **Track Map**. One double-click starts them, one action stops them.

## Start (the only thing to remember)

**Double-click `DRIVE.bat`.**

That's it. It starts the three overlay servers and opens three always-on-top
windows over the sim. A small black console window stays open — that's normal,
it's the "driving mode is running" window.

> First time only: put it on your desktop → right-click `DRIVE.bat` →
> *Send to* → *Desktop (create shortcut)*. Then just double-click the desktop icon.

## Stop

Any one of these:

- **Close the black console window**, or
- press **Ctrl+C** in it, or
- **close all three overlay windows**.

Everything Drive Mode started shuts down cleanly.

## One-time setup

1. **iRacing must run in _borderless windowed_ mode** — Options → Graphics →
   *windowed* + *borderless*. In exclusive fullscreen, nothing can sit on top.
2. Install the two small extras once (in a terminal, in this folder):

   ```
   pip install pywebview pywin32
   ```

   `pywebview` renders the Quali Delta / Track Map windows; `pywin32` is for
   click-through (you already use it elsewhere).

## Moving / placing the windows

- **Quali Delta** and **Track Map** windows are **drag-anywhere** — just grab
  and move them to a corner you can see.
- **Corner Cues** sticks to the right screen edge, centered, and is
  click-through (clicks pass to the sim).
- Want the numbers to persist? Once you like a position, tell me and I'll bake
  the coordinates into `drive.py` so they open there every time.

## If something looks wrong

| Symptom | Fix |
|---|---|
| Nothing appears on top of the sim | iRacing isn't in **borderless windowed** mode. |
| Quali Delta / Track Map window is blank white | `pywebview` not installed → `pip install pywebview`. On Win10/11 the Edge **WebView2** runtime is normally already there. |
| Corner Cues window shows "TRACK MAP NOT BUNDLED" / nothing | That track's geometry isn't bundled in `tracks/` — same coverage as the Track Map overlay. |
| A window steals focus / pauses the sim when clicked | Don't click it; or ask me to enable **click-through** on it (`--click-through`). |
| "did NOT come up on <port>" in the console | That server failed to start — run it once by hand (`python iracing_qualidelta.py`) to see its error. |

## What's under the hood (for later)

- `DRIVE.bat` → runs `drive.py`.
- `drive.py` → starts servers **5012** (Corner Cues), **5014** (Quali Delta),
  **5007** (Track Map), waits for them, opens the windows, and stops everything
  on exit. Servers already running (e.g. from the launcher GUI) are reused and
  left alone.
- `driving_overlay_window.py` → the generic always-on-top window host for any
  HTML overlay (used for Quali Delta and Track Map).
- `driving_line_window.py` → the existing dedicated Corner Cues window.

To add another overlay to Driving Mode later (e.g. Standings on 5005), add one
entry to the `OVERLAYS` list in `drive.py` — just a server script, port, and a
`driving_overlay_window.py` command.
