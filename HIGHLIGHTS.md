# Highlight-Video aus Replay + Race-Logger

Aus dem Log des Race-Loggers sucht das Dashboard die besten Szenen eines Rennens
(3–5 Minuten), spielt sie im iRacing-Replay ab und nimmt sie mit OBS als **ein**
Video auf. Seite: **http://localhost:5000/highlights** (Stream-Deck-Bild
`streamdeck_icons/dashboard/highlights_page.png`).

## Was ausgewählt wird

Immer dabei, in dieser Reihenfolge am Anfang: **Startaufstellung** (Quali-Ergebnis
als Grafik über den letzten 15 s der Einführungsrunde hinter dem Safety Car), dann
nahtlos — ohne Schnitt — **der Start** (grüne Flagge). Außerdem **Gelbphase** und
**Restart** sowie der **Zieleinlauf** des Siegers. Dazu, nach Wichtigkeit, bis die Länge erreicht ist:

- **Überholmanöver** um die Führung, das Podium und die Klassenführung (PRO/AM in
  der WCT) — berechnet aus den sekündlichen Positionen aller Autos. Nicht gezählt:
  unter Gelb, Boxenstopps, Seite-an-Seite-Wechsel, die keine 5 s halten, und nach
  der Zielflagge. In Multiclass-Rennen nur innerhalb einer Klasse.
- **Kontakte und Dreher** — ohne die falschen „Dreher“ an jeder Boxenausfahrt, die
  der Dashboard-Detektor vor dem 21.10.2026 meldete (stehen noch in alten Logs).
- **Schnellste Runde** (das Ende der Runde), **Fahrer des Tages** (sein bestes
  Überholmanöver), **Klassensieger AM**.

Höchstens 5 Überholmanöver und 4 Zwischenfälle, über das Rennen verteilt, keine
Überschneidungen. Kamera: nur TV1/TV2/TV3 (Blimp / Chopper für die Startaufstellung),
**nie** Scenic, Cockpit, Gyro oder eine Chase-Kamera.

## Ablauf

1. iRacing: das Rennen als **Replay** öffnen (gleiche Session, wie im Log).
2. Launcher: **Dashboard** starten. OBS läuft, obs-websocket an (wie beim
   Sendeablauf, `show_config.json` → `obs`).
3. In OBS, Szene **iRacing**: Browserquelle **Startaufstellung**
   (`obs_loaders/grid.html`, versteckt; im Launcher das **Grid**-Overlay 5001
   starten — es liest das Quali-Ergebnis auch im Replay). Fehlt sie, läuft nur die
   Einführungsrunde (Hinweis im Protokoll). Optional, ebenfalls Szene iRacing: Browserquelle **Highlight Titel**
   (`obs_loaders/highlight_card.html`) — zeigt am Anfang jeder Szene Titel + Satz,
   sonst unsichtbar. Für die Stimme die Quelle **Kommentar Stimme**
   (`commentary_voice.html`) wie bei der REGIE.
4. Seite `/highlights`: Rennen wählen, Länge (Standard 240 s), **Szenen wählen**.
5. Liste prüfen: ◀5 = 5 s früher anfangen, 5▶ = länger, −5 = kürzer, ▲▼ =
   Reihenfolge, 👁 = im Replay ansehen (ohne Aufnahme), ✕ = raus. Der Text jeder
   Szene ist änderbar (Titelkarte / Stimme).
6. Häkchen: **Aufnahme (OBS)** (ohne = Probelauf), **Titelkarte**, **Stimme**,
   **Gabi Intro / Outro**.
7. **▶ Start**. Pro Szene: Replay anhalten → hinspulen → Kamera → Play 1× →
   Aufnahme läuft → am Szenenende Aufnahme pausiert. Am Schluss stoppt die
   Aufnahme; der Pfad der MP4 steht oben (OBS-Aufnahmeordner und -Format).
   **■ Stop** bricht ab und beendet die Aufnahme sauber.

Zwischen den Szenen wird **überblendet** (Häkchen **Blenden**): am Szenenende hält
OBS das letzte Bild fest, das Replay springt, und die neue Szene blendet aus diesem
Standbild heraus auf — kein schwarzes Bild dazwischen. Nur der Anfang blendet aus
Schwarz auf und das Ende auf Schwarz ab. Dafür legt der Ablauf in OBS einmal die
Szene **„Highlight Schwarz“** mit der Bildquelle **„Highlight Standbild“** an (bitte
stehen lassen) und benutzt OBS' Übergang „Fade“ (0,6 s); danach steht der Übergang
wieder wie vorher. Gabi ↔ Rennen wird genauso überblendet. **Fahrer des Tages** kommt direkt vor dem
Zieleinlauf.

Während der Aufnahme sind REGIE, Auto-Follow, Fokus-Modi und Auto-Replays aus;
danach wieder wie vorher. Die **iRacing-UI** (Replay-Leiste usw.) wird vor jeder
Szene und laufend während der Aufnahme geprüft und versteckt, iRacings eigene
Automatik-Kamera („Most Exciting“) ist während der Aufnahme aus und danach wieder
wie vorher. (Die Maus nicht über das iRacing-Fenster bewegen — das zeigt die
Replay-Leiste kurz an.)

## Gabi Intro / Outro

Texte stehen vorgeschlagen in den Feldern (änderbar). **Gabi-Videos bestellen**
lässt sync.so die beiden Videos machen (dauert Minuten, kostet Credits — kurz
halten). Fertig → Gabi spricht sie in der Szene **Moderator** (Quellen „Gabi“ /
„Gabi Ruhe“ wie im Sendeablauf). Nicht fertig → die Stimme sagt den Text; ohne
Stimme fällt Intro/Outro weg.

Fertige Videos werden **aufgehoben** (`avatar/out/highlights_intro_<Kennung>.mp4`)
und für denselben Text wiederverwendet — auch nach einem Neustart oder Update des
Dashboards. Nach „Szenen wählen“ steht dann „ready (gespeichert, kostenlos)“, und
„Gabi-Videos bestellen“ kostet nichts. Nur ein geänderter Text wird neu bestellt.

## Einstellungen (optional)

`show_config.json` → `"highlights"`: `scenes` (`race`, `moderator`), `sources`
(`grid`, `avatar_talk`, `avatar_idle`), `preroll_s` (1.5), `card_s` (5), `cam_every_s`
(12 = nächste TV-Kamera in langen Szenen).

## Gut zu wissen

- **OBS muss pausieren können:** Einstellungen → Ausgabe → Aufnahme →
  Aufnahmequalität **nicht** „Gleich wie Stream“ (z. B. „Hohe Qualität, mittlere
  Dateigröße“); im erweiterten Modus einen eigenen Video-Encoder statt „Stream-Encoder
  verwenden“. Sonst bricht der Start mit diesem Hinweis ab — OBS würde das Spulen
  zwischen den Szenen mit aufnehmen.
- **Startaufstellung**: die Grafik (Grid-Overlay 5001) zeigt beim Einblenden zuerst
  10 s still die ersten Plätze, dann scrollt sie (`?hold=` = andere Sekundenzahl);
  die Szene dauert 20 s.
- Das Replay darf in einer anderen Session stehen (z. B. Training): der Sprung ins
  Rennen dauert ~2–3 s, das Dashboard wartet ihn ab.
- OBS darf beim Start nicht schon aufnehmen (sonst Abbruch mit Meldung).
- Das Video zeigt, was OBS in der Szene iRacing zeigt — Overlays, die nicht ins
  Highlight-Video gehören, dort vorher ausblenden.
- Kommandozeile ohne Dashboard: `python highlights.py <log.jsonl> [Sekunden]`
  listet die gewählten Szenen.
