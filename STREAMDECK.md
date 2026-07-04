# Stream Deck — Dashboard Control

The **Dashboard** overlay (`iracing_dashboard.py`, port 5000) exposes a simple
HTTP API for remote control. Every action is a plain `GET` URL, so **no plugin
is needed** — use Stream Deck's built-in **System → Website** action.

## Setup (per button)

1. Drag **System → Website** onto an empty key.
2. Paste one of the URLs below into the **URL** field.
3. **Tick "Access in background"** — this fires the URL without opening a
   browser tab. (If you leave it unchecked, a browser window pops up on every
   press.)
4. Give the key a title and optional icon.

The dashboard must be running (start it from `launch_gui`). If your Stream Deck
is on a **different PC** than the sim, replace `localhost` with the sim PC's LAN
IP, e.g. `http://192.168.1.50:5000/...` (the overlays already listen on
`0.0.0.0`).

## Endpoint reference

### Cameras

| Action | URL |
|---|---|
| Next camera group | `http://localhost:5000/streamdeck/cam_next` |
| Previous camera group | `http://localhost:5000/streamdeck/cam_prev` |
| Camera group by **ID** | `http://localhost:5000/streamdeck/cam/4` |
| Camera group by **NAME** | `http://localhost:5000/streamdeck/cam_name/TV1` |
| Next driver on camera | `http://localhost:5000/streamdeck/driver_next` |
| Previous driver on camera | `http://localhost:5000/streamdeck/driver_prev` |

**Prefer `cam_name` over `cam/<id>`.** Camera group IDs get renumbered between
tracks and sessions, so an ID button can silently point at the wrong camera.
Names are stable. Matching is case-insensitive and space-insensitive
(`cam_name/TV1` == `cam_name/TV 1`) and falls back to a substring match.
Examples: `/cam_name/TV1`, `/cam_name/Chase`, `/cam_name/Cockpit`,
`/cam_name/Scenic`.

### Playback & broadcast

| Action | URL |
|---|---|
| Go Live (jump to live tip) | `http://localhost:5000/streamdeck/go_live` |
| Hide / show iRacing UI (toggle) | `http://localhost:5000/streamdeck/hide_ui` |

`hide_ui` sends spacebar to iRacing to toggle its broadcast HUD, and remembers
the hidden state so later camera switches keep it hidden. It only works on the
machine actually running iRacing (it injects a local keypress).

### Replays

| Action | URL |
|---|---|
| Replay last incident (any type) | `http://localhost:5000/streamdeck/replay_last` |
| Replay last spin | `http://localhost:5000/streamdeck/replay_last_lost_control` |
| Replay last collision | `http://localhost:5000/streamdeck/replay_last_incident_points` |
| Replay last "stopped on track" | `http://localhost:5000/streamdeck/replay_last_stopped` |
| Replay last yellow-zone incident | `http://localhost:5000/streamdeck/replay_last_yellow` |

### Toggles

| Action | URL |
|---|---|
| Toggle Auto-Follow | `http://localhost:5000/streamdeck/toggle_auto_follow` |
| Toggle Auto-Replay (incidents) | `http://localhost:5000/streamdeck/toggle_auto_replay` |
| Toggle Auto-Replay (overtakes) | `http://localhost:5000/streamdeck/toggle_auto_replay_overtakes` |

## Suggested layout

A practical broadcast page:

```
Row 1:  [Go Live]      [Hide UI]        [Cam: TV1]   [Cam: Chase]  [Cam: Cockpit]
Row 2:  [Driver Prev]  [Driver Next]    [Cam Prev]   [Cam Next]    [Cam: Scenic]
Row 3:  [Replay Last]  [Replay Spin]    [Auto-Follow][Auto-Replay] [OT Auto-Replay]
```

Every response is JSON `{"ok": true/false, "message": "...", "action": "..."}` —
Stream Deck ignores it, but you can open any URL in a browser to test it and see
the result.

## Notes

- After changing dashboard code, **restart the dashboard** — a running process
  keeps the old code (and old endpoints) in memory.
- The `racecontrol/` steward dashboard (port 8080) is a separate app and does
  **not** share this API. Stream Deck support for it is on its roadmap
  (`racecontrol/ROADMAP.md`).
