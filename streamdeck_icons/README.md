# Stream-Deck-Symbole

Fertige Tastenbilder (288 × 288 px) für alle Overlays und die
Dashboard-Befehle. Die Farbe des Balkens oben zeigt die Gruppe:

| Farbe | Gruppe |
|---|---|
| Orange | LIVE |
| Pink | ZWEIKAMPF |
| Türkis | MEISTERSCHAFT |
| Grün | QUALIFYING |
| Lila | VOR DEM RENNEN |
| Gelb | NACH DEM RENNEN |
| Grau | BEDIENUNG |
| Hellblau | KAMERA |
| Rot | REPLAY |
| Bernstein | AUTOMATIK |

## So setzt du ein Symbol

Stream-Deck-App → Taste anklicken → unten links auf das **Bild der Taste**
klicken → **„Aus Datei wählen"** → Datei aus diesem Ordner. Den **Titel**
der Taste leer lassen (der Name steht schon im Bild).

## `overlays/` — für die OBS-Tasten (Quelle ein/aus, Szene …)

Die Dateinamen sind dieselben wie die Loader in `obs_loaders/`
(`weather.png` ↔ `obs_loaders/weather.html`).

| Datei | Overlay |
|---|---|
| `standings.png` | Tower (Live) |
| `fastest.png` | Schnellste Runde (Live) |
| `movers.png` | Movers (Live) |
| `gapbar.png` | Gap Bar (Live) |
| `ticker.png` | Überhol Ticker (Live) |
| `driver.png` | Driver Card (Live) |
| `driver_simple.png` | Driver Card simpel (Live) |
| `livery.png` | Livery (Live) |
| `trackmap.png` | Track Map (Live) |
| `flag.png` | Flaggen (Live) |
| `leader.png` | Neuer Führender (Live) |
| `weather.png` | Wetter (Live) |
| `sess.png` | Restzeit (Live) |
| `live.png` | LIVE / Replay (Live) |
| `turn.png` | Kurve (Live) |
| `logger_chart.png` | Renn-Grafik (Live) |
| `twitch_chat.png` | Twitch Chat (Live) |
| `youtube.png` | YouTube (Live) |
| `catch.png` | Catch-Up (Zweikampf) |
| `battle_gap.png` | Abstand (Zweikampf) |
| `chasing.png` | Chasing (Zweikampf) |
| `last_laps.png` | Letzte 4 Runden (Zweikampf) |
| `champ.png` | Meisterschaft (Meisterschaft) |
| `duel.png` | Titelkampf (Meisterschaft) |
| `dotd.png` | Driver of the Day (Meisterschaft) |
| `delta.png` | Delta Pole (Qualifying) |
| `delta_own.png` | Delta eigene (Qualifying) |
| `grid.png` | Start Grid (Qualifying) |
| `lastrace.png` | Letzte Runde (Vor Dem Rennen) |
| `table.png` | Tabelle (Vor Dem Rennen) |
| `rsvp.png` | RSVP (Vor Dem Rennen) |
| `stats.png` | Statistik (Vor Dem Rennen) |
| `lastdotd.png` | DotD letzte Runde (Vor Dem Rennen) |
| `results.png` | Ergebnis (Nach Dem Rennen) |
| `results_lite.png` | Ergebnis Lite (Nach Dem Rennen) |
| `dashboard.png` | Dashboard (Bedienung) |
| `logger.png` | Race Logger (Bedienung) |
| `line.png` | Corner Cues (Bedienung) |

## `dashboard/` — Kameras und Replays (Aktion „Website")

Aktion **System → Website**, Haken bei **„Im Hintergrund aufrufen"**,
URL wie unten. Das Dashboard (Port 5000) muss laufen.

| Datei | URL |
|---|---|
| `cam_tv1.png` | `http://localhost:5000/streamdeck/cam_name/TV1` |
| `cam_tv2.png` | `http://localhost:5000/streamdeck/cam_name/TV2` |
| `cam_tv3.png` | `http://localhost:5000/streamdeck/cam_name/TV3` |
| `cam_chase.png` | `http://localhost:5000/streamdeck/cam_name/Chase` |
| `cam_far_chase.png` | `http://localhost:5000/streamdeck/cam_name/Far%20Chase` |
| `cam_rear_chase.png` | `http://localhost:5000/streamdeck/cam_name/Rear%20Chase` |
| `cam_blimp.png` | `http://localhost:5000/streamdeck/cam_name/Blimp` |
| `cam_chopper.png` | `http://localhost:5000/streamdeck/cam_name/Chopper` |
| `cam_pit_lane.png` | `http://localhost:5000/streamdeck/cam_name/Pit%20Lane` |
| `cam_cockpit.png` | `http://localhost:5000/streamdeck/cam_name/Cockpit` |
| `cam_nose.png` | `http://localhost:5000/streamdeck/cam_name/Nose` |
| `cam_gearbox.png` | `http://localhost:5000/streamdeck/cam_name/Gearbox` |
| `cam_gyro.png` | `http://localhost:5000/streamdeck/cam_name/Gyro` |
| `cam_next.png` | `http://localhost:5000/streamdeck/cam_next` |
| `cam_prev.png` | `http://localhost:5000/streamdeck/cam_prev` |
| `driver_next.png` | `http://localhost:5000/streamdeck/driver_next` |
| `driver_prev.png` | `http://localhost:5000/streamdeck/driver_prev` |
| `go_live.png` | `http://localhost:5000/streamdeck/go_live` |
| `replay_last.png` | `http://localhost:5000/streamdeck/replay_last` |
| `replay_last_spin.png` | `http://localhost:5000/streamdeck/replay_last_lost_control` |
| `hide_ui.png` | `http://localhost:5000/streamdeck/hide_ui` |
| `auto_follow.png` | `http://localhost:5000/streamdeck/toggle_auto_follow` |
| `auto_replay.png` | `http://localhost:5000/streamdeck/toggle_auto_replay` |
| `auto_replay_ot.png` | `http://localhost:5000/streamdeck/toggle_auto_replay_overtakes` |

## Eigene Symbole

Liste in `make_streamdeck_icons.py` ergänzen und `python make_streamdeck_icons.py`
ausführen (nutzt Microsoft Edge im Hintergrund, dauert ein paar Minuten).
