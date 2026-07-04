# Custom iRacing Cameras — Broadcast Angles + Stream Deck Wiring

Your colleague's "FPV cams" are **custom camera sets**: hand-tuned iRacing
cameras that give better, more cinematic broadcast angles than the stock
TV1/TV2/TV3. They are **not a separate app** — they're camera files iRacing
loads per track, and they show up as ordinary named **camera groups**.

Because they're just camera groups, the dashboard already controls them.
No new overlay is needed — only the discovery helper added on 2026-07-04.

---

## 1. What they are

iRacing groups every camera into named sets: `TV1`, `TV2`, `TV3`, `Scenic`,
`Cockpit`, `Chase`, `Nose`, etc. A "custom camera set" replaces or extends
those groups **per track** with better compositions — closer trackside
shots, sweeping cinematic pans, drone/FPV swoops, real-TV replica angles.

Stored in:
- `Documents\iRacing\cameras\tracks\<track name>\`  (per-track sets)
- `Documents\iRacing\cameras\cars\<car name>\`      (per-car sets)

---

## 2. Where to get them (broadcast-focused)

| Option | Cost | Best for |
|--------|------|----------|
| **TrackCams — "Track Cams for Gourmets"** (trackcams22.com) | Paid, per-track packs | The go-to commercial broadcast packs. Real-TV-style angles, dynamic compositions. Their **RaceFan / broadcast** packages are aimed exactly at streamers. |
| **Studio DaVeed** (YouTube tutorials + shared sets) | Free | Learning to build/tune your own, plus solid free sets. |
| **Community sets** (iRacing forums, broadcast Discords) | Free | Track-specific sets shared by other broadcasters. |
| **Build your own** (in-sim Camera Tool, `Ctrl-F12`) | Free | Full control; reusable once saved. Best long-term for a consistent PCCD/CAS look. |

Recommendation for you: start with **TrackCams** on the handful of tracks
your league runs most (fast, professional result), and keep the built-in
Camera Tool in your back pocket to tweak individual shots.

---

## 3. Install (2 minutes per track)

1. Download and **unzip** the pack.
2. Copy the track folders into `Documents\iRacing\cameras\tracks\`
   (matching iRacing's track-name folders). Car sets go under `...\cars\`.
3. In iRacing, enter a replay, press **`Ctrl-F12`** to open the Camera Tool,
   and use **Load Track** / **Load Car** if the pack needs manual loading.
   Most packs are picked up automatically once the files are in place.
4. `Esc` to leave the Camera Tool.

The new angles now appear as selectable **camera groups** in that session.

---

## 4. Wire them to the Stream Deck (no plugin needed)

The dashboard exposes every camera group by **name** — names stay stable
across tracks even though iRacing renumbers the IDs, so always map buttons
to names, not IDs.

**Step 1 — see what the loaded set exposes.** With iRacing running and a
session loaded, open in a browser (or just read the JSON):

```
http://localhost:5000/cameras?format=text
```

You'll get a copy-paste list like:

```
Camera groups in this session (12):
  id   0  Nose             http://localhost:5000/streamdeck/cam_name/Nose
  id   2  TV 1             http://localhost:5000/streamdeck/cam_name/TV%201   <-- current
  id   5  Drone            http://localhost:5000/streamdeck/cam_name/Drone
  ...
```

**Step 2 — make the buttons.** In the Stream Deck app, add a
**System → Website** action per camera. Paste the matching URL, e.g.
`http://localhost:5000/streamdeck/cam_name/Drone`, and **check
"Access in background"** so it fires without opening a browser tab.
Label the key with the camera name.

That's it — pressing the key switches iRacing to that camera group live.

Notes:
- Spaces in a name become `%20` in the URL (`TV 1` → `cam_name/TV%201`).
  The `/cameras?format=text` output already encodes them for you.
- Matching is case-insensitive and forgiving: exact → space-insensitive
  (`TV 1` == `TV1`) → substring, so partial names still work.
- Custom packs often use their own names (e.g. `Drone`, `Heli`, `Barrier`).
  Re-run `/cameras?format=text` after installing to grab the exact names.

---

## 4b. The FCP pack (`custom_cameras_2/` — the one that gets installed)

Two FCP packs are in the project: `custom_cameras/` (older, 95 tracks /
65 cars) and **`custom_cameras_2/` (newer and more complete: 109 tracks /
97 cars)**. Pack 2 is a full superset of pack 1 — every shared camera is
byte-identical except 16 track sets that pack 2 improved (notably
**Bathurst**, **Sebring International**, and **Homestead** were
substantially reworked), and pack 2 adds ~16 tracks and ~32 cars,
including the **Porsche 992 Cup / 992 GT3** and other modern GT3s your
league runs. The only folders unique to pack 1 (`mtwashington`, `oran`)
are **empty** — no cameras — so nothing is lost.

**The installer uses `custom_cameras_2/`.** Both packs use the **standard
iRacing camera group names**, just re-tuned for cinematic broadcast
angles, so your existing `cam_name` buttons work unchanged either way.

`custom_cameras/` (pack 1) is now redundant and can be deleted.

Track (spectator/broadcast) groups: `TV1`, `TV2`, `TV3`, `Chase`,
`Far Chase`, `Rear Chase`, `Blimp`, `Chopper`, `Pit Lane`.
Car (onboard) groups: `Cockpit`, `Nose`, `Gearbox`, `Gyro`, `Roll Bar`,
and the suspension cams.

**Install it (do this on the Windows PC that runs iRacing, iRacing CLOSED):**

- **Easy way:** double-click **`install_fcp_cameras.bat`** in the project
  folder. It backs up your current cameras to a timestamped
  `Documents\iRacing\cameras_backup_<date>` folder, then copies the FCP
  `cars\` and `tracks\` into `Documents\iRacing\cameras\`.
- **Manual way:** copy the contents of `custom_cameras_2\cars` and
  `custom_cameras_2\tracks` into `Documents\iRacing\cameras\cars` and
  `...\tracks` respectively (merge/overwrite).

To undo: delete the `cameras` folder and rename the backup back to
`cameras`.

**Ready-to-paste Stream Deck buttons** (System → Website, "Access in
background" checked):

```
TV1        http://localhost:5000/streamdeck/cam_name/TV1
TV2        http://localhost:5000/streamdeck/cam_name/TV2
TV3        http://localhost:5000/streamdeck/cam_name/TV3
Chase      http://localhost:5000/streamdeck/cam_name/Chase
Far Chase  http://localhost:5000/streamdeck/cam_name/Far%20Chase
Rear Chase http://localhost:5000/streamdeck/cam_name/Rear%20Chase
Blimp      http://localhost:5000/streamdeck/cam_name/Blimp
Chopper    http://localhost:5000/streamdeck/cam_name/Chopper
Pit Lane   http://localhost:5000/streamdeck/cam_name/Pit%20Lane
Nose       http://localhost:5000/streamdeck/cam_name/Nose
Cockpit    http://localhost:5000/streamdeck/cam_name/Cockpit
```

After installing, load a session and hit `http://localhost:5000/cameras?format=text`
to confirm the exact names that track exposes (a few tracks add extras).

---

## 5. Endpoints reference

| Endpoint | Purpose |
|----------|---------|
| `GET /cameras` | JSON list of current camera groups (id, name, current, streamdeck_url) |
| `GET /cameras?format=text` | Same, as a copy-paste list with full Stream Deck URLs |
| `GET /streamdeck/cam_name/<name>` | Switch to camera group by name (use this on buttons) |
| `GET /streamdeck/cam/<id>` | Switch by numeric id (avoid — ids change per track) |
| `GET /streamdeck/cam_next` · `/cam_prev` | Cycle through groups |
