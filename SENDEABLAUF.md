# KI-Sendeablauf: Moderation, Qualifying, Rennen

Was ab **19:40** automatisch passiert (bei Rennstart um 20:00):

| Zeit | Was | Wer |
|---|---|---|
| 19:30 | Stream startet auf **Intro** (CAS-Song + Diashow) | du |
| ~19:40 | **Practice** zeigen | du |
| 19:35 | Moderationsskript wird geschrieben (Claude) | automatisch |
| **19:40** | OBS → **Moderator**: Begrüßung → letztes Rennen (Ergebnisse im Bild) → Tabelle (im Bild) → Strecke (erst Text, dann Streckenvideo) → Übergabe | automatisch |
| danach | OBS → **iRacing**, **REGIE an** | automatisch |
| Quali | REGIE zeigt schnellste Runden, den Pole-Fahrer und die Verfolger, mit Kommentar | automatisch |
| Rennen | REGIE + Kommentar wie getestet | automatisch |

Der automatische Start passiert **nur, wenn OBS streamt**. Läuft das Dashboard
an einem anderen Abend einfach mit, passiert nichts. Am selben Tag startet der
Ablauf nur einmal.

## 1. OBS einrichten (einmalig)

1. **WebSocket einschalten:** OBS → Werkzeuge → WebSocket-Server-Einstellungen →
   „WebSocket-Server aktivieren“, Port 4455. Ein Passwort ist möglich, dann
   gehört es in `show_config.json` (siehe unten).
2. **Szenen:** `Moderator` und `iRacing`. Wenn sie anders heißen, trag die
   Namen in `show_config.json` ein.
3. **Quellen in der Szene Moderator** (die Namen müssen genau stimmen):
   - `Letztes Rennen`: Browserquelle, Lokale Datei `obs_loaders\lastrace.html`
   - `Tabelle`: Browserquelle, Lokale Datei `obs_loaders\table.html`
   - `Streckenvideo`: deine Medienquelle mit dem Video. Bitte **„Wiedergabe
     neu starten, wenn Quelle aktiv wird“ ausschalten**, der Ablauf startet sie
     selbst.
   - dazu der Avatar (siehe 3.) und alles, was sonst dauerhaft zu sehen ist.

   - **Nur für Pro/Am-Ligen (WCT GT3):** vier weitere Browserquellen, je eine
     Klasse, die Namen müssen genau stimmen:
     `Letztes Rennen PRO` → `obs_loaders\lastrace_pro.html`,
     `Letztes Rennen AM` → `obs_loaders\lastrace_am.html`,
     `Tabelle PRO` → `obs_loaders\table_pro.html`,
     `Tabelle AM` → `obs_loaders\table_am.html`.
     Dann zeigt das Bild immer genau die Klasse, über die gerade gesprochen
     wird. Fehlen sie, nimmt der Ablauf die normalen Quellen und stellt dort
     die Klasse um.

   - **Ein Video pro Strecke:** Lege die Videos in den Ordner
     `show_notes\videos\` (der Dateiname enthält ein Wort der Strecke, z. B.
     `motegi.mp4`, `portimao-algarve.mp4`). Der Ablauf setzt dann vor dem Abspielen
     die passende Datei in die Quelle `Streckenvideo`. Gibt es für die Strecke
     keins, läuft **kein** Video (statt eines falschen), und die Moderation
     kündigt keins an. Ohne den Ordner spielt die Quelle wie bisher, was in ihr steckt.
   - **Streckenort** (optional): Browserquelle `obs_loaders\location.html`,
     1280 × 720 bzw. bildschirmfüllend. Satellitenkarte: erst das Land mit
     Markierung, dann Flug hinunter auf die Strecke, mit Name und Ort. Sie ist
     im Bild, während die Moderation über die Strecke spricht, danach kommt
     das Streckenvideo. Braucht die Streckenkarte (Port 5007) und Internet.
   - **Saisonplan:** pro Liga eine Quelle mit dem Plan, z. B. `WCT` für die
     WCT GT3 (`"schedule_sources": {"cas-gt3-wct": "WCT"}` in
     `show_config.json`, für weitere Ligen ergänzen). Die Termine kommen aus
     CLS, die Strecken späterer Runden aus `show_notes/schedule-<liga>.md`
     (eine Zeile pro Runde: `20.10.26 Indianapolis Motor Speedway - GP`).

   Diese Quellen blendet der Ablauf selbst ein und aus.
4. **Stimme:** die Browserquelle `obs_loaders\commentary_voice.html` mit
   **„Audio über OBS steuern“**. Sie muss in **beiden** Szenen liegen, in
   Moderator **und** iRacing, denn OBS gibt nur den Ton der Szene aus, die
   gerade läuft. Am einfachsten über „Bestehende hinzufügen“.
   Damit **du** die Stimme auch hörst (nicht nur der Stream): Mixer → Zahnrad →
   „Erweiterte Audioeigenschaften“ → bei der Quelle „Audioüberwachung“ auf
   **„Überwachen und ausgeben“**; das Monitoring-Gerät (Einstellungen → Audio)
   ist dein Kopfhörer.
5. Zum Prüfen öffnest du `http://localhost:5000/show` → **„OBS prüfen“**. Dort
   bekommt jede Szene und Quelle einen Haken, oder es steht da, was fehlt.

## 1a. Abspann nach dem Rennen

Läuft die REGIE, startet **2 Minuten nach der Zielflagge** der Abspann (einmal
pro Rennen; `"post_delay_s"`, `"post_auto": false` = nur per Knopf). Die REGIE
geht aus, OBS schaltet auf **Moderator**, dann:

1. **Rennverlauf** — erzählt aus den Live-Kommentaren des Rennens
2. **Endergebnis** — Quelle `Ergebnis` (Browserquelle `obs_loaders\results.html`)
3. **Meisterschaft danach** — Quelle `Meisterschaft` (`obs_loaders\champ.html`),
   vorläufig inkl. dieses Rennens, bei Pro/Am je Klasse
4. **Nächstes Rennen** — Runde, Datum, Strecke
5. **Verabschiedung**

Seite: `http://localhost:5000/abspann` (Skript lesen und ändern, Start,
Weiter, Stopp). Stream Deck: `/streamdeck/post_start`, `post_next`,
`post_stop`. Braucht Race Results (5002) und die Meisterschaft (5010).

## 1b. Grafiken während der REGIE (Szene iRacing, optional)

Läuft die REGIE, blendet sie im Rennen passende Overlays kurz ein, immer nur
eines, mit Pause dazwischen und nie während eines Replays:

| Quelle in der Szene iRacing | Datei | Wann |
|---|---|---|
| `Wetter` | `obs_loaders\weather.html` | einmal ~40 s nach dem Start, 12 s, mit Satz zum Wetter |
| `Duell` | `obs_loaders\catch.html` (oder battle_gap.html) | wenn die REGIE ein Duell zeigt, 10 s, höchstens alle 3 Min. |
| `Fahrer des Tages` | `obs_loaders\dotd.html` | im letzten Drittel des Rennens, 14 s, mit Zwischenstand |

Die Quellen bleiben in OBS ausgeblendet, die REGIE blendet sie selbst ein.
Fehlt eine, wird sie einfach übersprungen. Andere Namen oder Zeiten:
`"graphics"` in `show_config.json`, z. B.
`{"graphics": {"battle": {"source": "Catch-Up", "every_s": 240}}}`,
`{"graphics": {"enabled": false}}` schaltet alles ab.

## 2. Moderation

- Die Seite **`http://localhost:5000/show`** zeigt das Skript, sobald es
  geschrieben ist (5 Min. vor dem Start; mit „Skript neu schreiben“ jederzeit
  sofort). Hier liest du vorher, was gesagt wird.
- **Skript anpassen:** Solange die Moderation nicht läuft, ist jeder Abschnitt
  auf `/show` ein Textfeld. Text ändern → **„💾 Skript speichern“**. Ein leerer
  Abschnitt wird übersprungen. Ein von Hand geändertes Skript wird nicht mehr
  automatisch neu geschrieben, auch nicht 5 Minuten vor dem Start, und es
  übersteht einen Neustart (`show_script.json`, 12 Stunden gültig). Nur „Skript
  neu schreiben“ ersetzt es, nach einer Rückfrage.
- **Talking Points:** Mit dem Skript entstehen 10–20 kurze Talking Points
  (Tabelle, Abstände, letztes Rennen, Saison, Strecke). Sie stehen auf `/show`
  unter dem Skript, einer pro Zeile, und lassen sich genauso ändern. Im
  Qualifying und im Rennen liest die Stimme den nächsten, wenn der Kommentar
  30 Sekunden still war, höchstens alle 2½ Minuten, jeden nur einmal.
- **Pro/Am-Ligen (WCT GT3):** letztes Rennen und Tabelle werden je Klasse
  besprochen, erst PRO, dann AM; die Seiten im Bild zeigen dabei genau diese
  Klasse.
- **Die Daten:** die CLS-Ergebnisse der letzten Runde, die Tabelle und der
  Fahrer des Tages kommen vom Meisterschafts-Overlay (Port 5010, muss laufen).
  Strecke und Wetter kommen aus iRacing.
- **Streckeninfos:** Notizen aus dem Ordner `show_notes/` (siehe README dort).
  Ohne Notizen erzählt Claude nur Allgemeines, ohne Zahlen.
- Ohne API-Schlüssel spricht der Ablauf einfache Sätze aus Vorlagen.
- Am Saisonanfang gibt es noch kein letztes Rennen und keine Tabelle. Diese
  Abschnitte fallen dann einfach weg.
- Strecke: erst spricht die Moderation über die Strecke, danach läuft das
  Streckenvideo ganz durch, dann geht es weiter. Wer lieber über das Video
  sprechen lassen will: `"video_after_speech": false` in `show_config.json`
  (dann ist der Text auf die Länge des Videos zugeschnitten).

**Stream Deck** (Website-Aktion, „Im Hintergrund aufrufen“; Bilder in
`streamdeck_icons/dashboard/`):

| Taste | URL |
|---|---|
| Moderation Start | `http://localhost:5000/streamdeck/show_start` |
| Moderation weiter | `http://localhost:5000/streamdeck/show_next` |
| Moderation Stopp | `http://localhost:5000/streamdeck/show_stop` |
| REGIE an/aus | `http://localhost:5000/streamdeck/toggle_director` |
| Sendeablauf (Seite) | `http://localhost:5000/show` (ohne „Hintergrund“) |

„Weiter“ beendet den laufenden Abschnitt sofort, auch mitten im Satz. „Stopp“
blendet alles aus, bleibt auf der Moderator-Szene und gibt dir die Kontrolle
zurück.

## 3. Avatar mit VTube Studio

1. VTube Studio starten und ein Modell laden. Die mitgelieferten Modelle
   reichen zum Ausprobieren.
2. VTube Studio → Einstellungen → **„Start API (allow plugins)“** einschalten,
   Port 8001.
3. Kurz nachdem die Stimmen-Quelle in OBS geladen ist, fragt VTube Studio:
   Plugin **„CAS Regie“** zulassen? → **Erlauben**. Das ist nur einmal nötig,
   der Schlüssel wird in `vts_token.json` gespeichert.
4. Den Avatar in OBS in die Szene Moderator holen: Spielaufnahme bzw.
   Fensteraufnahme von VTube Studio mit transparentem Hintergrund, oder
   Spout2.
5. Die Stimmen-Seite misst die Lautstärke der Stimme und bewegt damit den
   Mund des Modells (Parameter `MouthOpen`). Ein virtuelles Audiokabel brauchst
   du nicht. Ob VTube Studio verbunden ist, zeigt
   `http://localhost:5000/commentary/voice?debug=1`.

Die Gesichtserkennung (Kamera) in VTube Studio sollte für den Avatar aus sein,
sonst steuern sich Kamera und Stimme gegenseitig den Mund. Bewegung und
Blinzeln übernehmen die Idle-Animationen des Modells.

## 4. `show_config.json` (optional)

Eine Datei neben `iracing_dashboard.py`. Sie wird von Updates nie
überschrieben. Nur was abweicht, muss drinstehen, z. B.:

```json
{
  "start_at": "19:40",
  "obs": {"password": "dein-obs-passwort"},
  "scenes": {"moderator": "Moderator", "iracing": "iRacing"},
  "sources": {"last_race": "Letztes Rennen", "standings": "Tabelle", "track_video": "Streckenvideo"},
  "host_name": "Max"
}
```

Weitere Schalter:
- `"auto_start": false` schaltet den Automatik-Start ab, dann nur noch per
  Taste.
- `"only_while_streaming": false` startet auch ohne laufenden Stream, z. B.
  für eine Probe mit Aufnahme.
- `"segments"` legt Reihenfolge und Auswahl der Abschnitte fest.
- `"model"` wählt das Claude-Modell für das Skript (Standard
  `claude-sonnet-5-5`).
- `"host_name"` ist der Name, mit dem sich die KI vorstellt.

Nach einer Änderung startest du das Dashboard (5000) neu.

## Probe ohne Stream

`"only_while_streaming": false` setzen oder einfach „▶ Moderation starten“
drücken. Die Quellen kannst du vorher mit „OBS prüfen“ kontrollieren.
