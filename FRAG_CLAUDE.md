# Frag Claude — der Co-Kommentator auf Zuruf

Für Streams, die **du (oder Andreas) selbst kommentierst** — nicht die
KI-Moderation / REGIE. Du drückst eine Stream-Deck-Taste, stellst eine Frage
ins Mikrofon, drückst nochmal, und ein paar Sekunden später antwortet die
Stimme im Stream:

> „Claude, welchen Platz hatte Maurice Becker im letzten Rennen?"
> → „Maurice Becker hat das erste Rennen in Zandvoort gewonnen und wurde im zweiten Dritter."

Claude antwortet **nur aus unseren eigenen Daten** (und optional einer
Websuche für Strecken-Trivia). Steht es nirgends, sagt er, dass er es nicht weiß
— er erfindet keine Ergebnisse.

## Was Claude nachschauen kann

| Werkzeug | Quelle | Beispiel-Fragen |
|---|---|---|
| live_session | Dashboard (5000) | Wo fahren wir? Wie viele Runden noch? Wer ist im Bild? |
| live_standings | Tower (5005) | Wer führt? Abstand zwischen X und Y? Wer fuhr die schnellste Runde? |
| race_results | Ergebnis (5002) | Wie ging das Rennen aus? Wie viele Plätze hat X gutgemacht? |
| championship | Meisterschaft live (5010) | Wer führt die Meisterschaft, wenn das Rennen jetzt endet? |
| round_info | CLS, letzte Runde (5010) | Welchen Platz hatte X letztes Mal? Wer war Fahrer des Tages? Wann ist die nächste Runde? |
| league_table | CLS, Tabelle vor der Runde (5010) | Wie viele Punkte hat X? Wer führt bei den AM? |
| track_notes | `show_notes/<strecke>.md` | Was ist das Besondere an der Strecke? |
| web_search | Websuche (Anthropic) | Wann fuhr die Formel 1 zuletzt hier? |

Die Overlays, aus denen er liest, müssen laufen (Dashboard 5000, Tower 5005,
Ergebnis 5002, Meisterschaft 5010). Fehlt eines, sagt Claude „weiß ich nicht"
statt zu raten.

## Einrichten (einmal)

1. **Pakete**: `pip install -r requirements.txt` (neu: `faster-whisper` für die
   Spracherkennung, `sounddevice` fürs Mikrofon). Das Update im Launcher macht
   das von selbst.
2. **Claude-Schlüssel**: derselbe wie für den Kommentar —
   `"api_key"` in `commentary_config.json`.
3. **Stimme in OBS**: die Quelle `obs_loaders/commentary_voice.html` muss in der
   Szene sein (wie bei der REGIE). Ohne sie bleiben die Antworten stumm — die
   Seite /ask warnt dann rot.
4. **Stream Deck**: Aktion *System → Website*, Haken bei **„Im Hintergrund
   aufrufen"**, URL `http://localhost:5000/streamdeck/ask`, Bild
   `streamdeck_icons/dashboard/ask.png`.
5. **Vor dem Rennen einmal** `http://localhost:5000/ask` öffnen. Dabei wird das
   Sprachmodell geladen — beim allerersten Mal auch heruntergeladen (~480 MB,
   „small"). Steht in der Kopfzeile nicht mehr „lädt …", ist es bereit.

## Bedienen

- **Taste drücken** → „● hört zu" → Frage stellen → **Taste nochmal drücken**.
  Spätestens nach 20 Sekunden hört er von selbst auf.
- Zum **Halten** (Taste gedrückt = zuhören) braucht es ein Stream-Deck-Plugin,
  das beim Drücken und beim Loslassen je eine URL aufruft:
  `/streamdeck/ask_start` und `/streamdeck/ask_stop`.
- **Abbrechen**: `/streamdeck/ask_cancel` (oder der Knopf auf /ask).
- Die Seite **/ask** zeigt den Zustand, die letzten Fragen und Antworten, welche
  Quellen Claude benutzt hat und wie lange es dauerte. Dort kann man eine
  Frage auch **tippen** (geht ohne Mikrofon — gut zum Ausprobieren).
- Die Antwort spricht dieselbe Stimme wie die Moderation. Läuft gleichzeitig
  die REGIE, kommt die Antwort vor dem nächsten Kommentarsatz.

Dauer: Spracherkennung ~1 s, Claude mit 1–2 Nachschlagen ~3–6 s, mit Websuche
etwas länger. Kurz vorher „Fragen wir mal Claude …" sagen überbrückt das gut.

## Einstellungen (`commentary_config.json`, alle optional)

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `qa_model` | `claude-haiku-5-5` | schnell und günstig; `claude-sonnet-5-5` antwortet überlegter, ist aber langsamer |
| `qa_web_search` | `true` | Websuche für Trivia erlauben |
| `qa_mic` | `""` | Teil des Mikrofon-Namens (z. B. `"Wave XLR"`) oder die Nummer; leer = Windows-Standard |
| `qa_stt_model` | `small` | Whisper-Größe: `base` schneller/ungenauer, `medium` genauer/langsamer |
| `qa_max_record_s` | `20` | längste Aufnahme |
| `qa_timeout_s` | `25` | Wartezeit auf Claude |
| `qa_sources` | — | andere Adressen für `standings` / `results` / `championship` / `roundinfo` |

Fahrernamen versteht die Spracherkennung besser, weil ihr das aktuelle
Fahrerfeld als Hinweis mitgegeben wird; Claude ordnet einen falsch erkannten
Namen dem ähnlichsten Fahrer zu.

## Grenzen (Version 1)

- Nur das Mikrofon am Stream-PC. Andreas über Discord bräuchte eine eigene
  Instanz auf seinem PC oder einen Audio-Abgriff — noch nicht gebaut.
- Ausgelöst wird nur über die Taste — bewusst ohne Weckwort (Thomas,
  20.10.2026: zuverlässiger in einem lauten Rennen).
- Eine Frage nach der anderen; während er zuhört oder nachdenkt, wird eine
  zweite ignoriert.
