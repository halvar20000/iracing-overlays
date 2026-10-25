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

## Zweiter Zustand „aus" (grau) — Design von Andreas

`overlays_off/` und `dashboard_off/` enthalten dieselben Bilder in Grau, mit
**denselben Dateinamen**. Für Tasten mit zwei Zuständen:

- **OBS → Quelle ein/aus**: Zustand 1 (sichtbar) = Bild aus `overlays/`,
  Zustand 2 (versteckt) = gleiches Bild aus `overlays_off/`. Das Stream Deck
  zeigt dann immer an, ob das Overlay gerade im Bild ist.
- **Dashboard-Schalter** (`auto_follow`, `auto_replay`, `auto_replay_ot`,
  `hide_ui`, `regie`): die Website-Aktion hat nur einen Zustand. Für Farbe/Grau die
  Taste als **Multi-Action-Wechsel** (Multi Action Switch) anlegen, in beide
  Zustände dieselbe Website-URL, Bilder aus `dashboard/` und
  `dashboard_off/`. Achtung: das Stream Deck merkt sich nur seine eigenen
  Klicks — wird der Modus im Dashboard umgeschaltet, stimmt die Farbe nicht
  mehr (einmal drücken bringt es wieder in Gleichschritt).

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
| `commentary.png` | Kommentar (Live) |
| `commentary_voice.png` | Stimme (Live) |
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
| `topspeed.png` | Top Speed (Live) |
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
| `sectors.png` | Beste Sektoren (Qualifying) |
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
| `regie.png` | `http://localhost:5000/streamdeck/toggle_director` — REGIE an/aus (grau: `dashboard_off/regie.png`) |
| `show_start.png` | `http://localhost:5000/streamdeck/show_start` — KI-Moderation jetzt starten |
| `show_next.png` | `http://localhost:5000/streamdeck/show_next` — nächster Moderations-Abschnitt |
| `show_stop.png` | `http://localhost:5000/streamdeck/show_stop` — Moderation abbrechen |
| `ask.png` | `http://localhost:5000/streamdeck/ask` — Frag Claude: drücken = Frage stellen, nochmal drücken = Antwort (FRAG_CLAUDE.md) |
| `page_next.png` | `http://localhost:5001/nav/next` — alle Seiten: nächste Seite / weiter scrollen |
| `page_prev.png` | `http://localhost:5001/nav/prev` — alle Seiten: Seite zurück |
| `page_first.png` | `http://localhost:5001/nav/first` — alle Seiten: Seite 1 / nach oben |
| `page_auto.png` | `http://localhost:5001/nav/auto` — automatisch blättern an/aus — wirkt nur noch auf Quellen mit `?auto=1` (grau: `dashboard_off/page_auto.png`) |

Seit 25.10.2026 blättert und scrollt **kein Overlay mehr von selbst** — nur
diese Tasten bewegen die Seiten: Startaufstellung (`grid.html`, `grid_cars`,
`grid_row`, klassisch), Endergebnis (`results_wct.html`), letzte Runde
(`lastrace_wct.html`, `lastrace.html`: Rennen 1 → 2 → Gesamt), Tabelle
(`table.html`: PRO → AM), Teamwertung (`teams_wct.html`) und die langen Listen
`results.html` / `results_lite.html` (Weiter = eine Bildschirmhöhe nach unten,
am Ende zurück nach oben). Die Befehle laufen über Port 5001 — das
Grid-Overlay muss dafür im Launcher laufen, auch wenn gerade nur ein Ergebnis
gezeigt wird. Wer den alten Timer für eine Quelle zurück will: URL-Quelle mit
`?auto=1` (WCT-Seiten / Grid, Dauer `?secs=`), `?cycle=20` (lastrace / table)
oder `?autoscroll=1` (results, results_lite, klassisches Grid).

## Seiten als Fenster öffnen (z. B. auf dem Hochkant-Monitor)

Gleiche Aktion **System → Website**, aber **ohne** Haken bei „Im
Hintergrund aufrufen" — dann öffnet die Taste die Seite im Browser. Das
jeweilige Overlay muss im Launcher gestartet sein (das Dashboard für das
Überhol-Fenster also auch; seine große Seite muss aber nicht offen sein).

| Datei | URL | Seite |
|---|---|---|
| `dashboard/ot_panel.png` | `http://localhost:5000/overtakes/panel` | Überhol-Fenster: Replay 10 s / 20 s, Kamera, Auto-Replay, Go Live |
| `dashboard/cam_panel.png` | `http://localhost:5000/cameras/panel` | Kamera-Fenster: alle Kameragruppen der Strecke als Tasten (auch Custom Cams), Fahrer vor / zurück, Go Live |
| `dashboard/driver_panel.png` | `http://localhost:5000/drivers/panel` | Fahrer-Fenster: Fahrerliste live (Pos, #, Name, PIT, Bestzeit, Abstand), Klick = Kamera, ★ merken / nur ★, Auto-Follow, Go Live |
| `dashboard/incident_panel.png` | `http://localhost:5000/incidents/panel` | Incident-Fenster: Dreher / Kollisionen / Off-Tracks, Replay 10 s / 20 s, Kamera, Auto-Replay, Go Live |
| `dashboard/show_page.png` | `http://localhost:5000/show` | Sendeablauf: Skript lesen, starten, weiter, stoppen, OBS prüfen |
| `dashboard/ask_page.png` | `http://localhost:5000/ask` | Frag Claude: letzte Fragen + Antworten, Frage tippen |
| `dashboard/highlights_page.png` | `http://localhost:5000/highlights` | Highlight-Video: Rennen wählen, Szenen bearbeiten, Aufnahme starten (HIGHLIGHTS.md) |
| `overlays/dashboard.png` | `http://localhost:5000` | ganzes Dashboard |
| `overlays/logger.png` | `http://localhost:5009` | Race Logger Monitor |
| — | `http://localhost:8080` | Race Control (Stewards) |
| `overlays/<name>.png` | Adresse aus `OBS_OVERLAYS.md` | jedes Overlay zum Anschauen |

Tipp: In Edge/Chrome die Seite einmal öffnen, Fenster auf die gewünschte
Größe und Position ziehen — Windows merkt sich das beim nächsten Öffnen
meistens. Ganz ohne Adressleiste: Edge-Menü → Apps → „Diese Seite als App
installieren", dann öffnet sie in einem eigenen schlanken Fenster.

## Eigene Symbole

Liste in `make_streamdeck_icons.py` ergänzen und `python make_streamdeck_icons.py`
ausführen (nur einzelne Ordner: `python make_streamdeck_icons.py overlays_off`) (nutzt Microsoft Edge im Hintergrund, dauert ein paar Minuten).
