# Race Logger — standalone

Der **Race Logger** schreibt ein komplettes iRacing-Rennen in eine Datei
(`logs\JJJJMMTT-HHMMSS_strecke_race.jsonl`): jede Runde jedes Fahrers, Boxenstopps,
Überholvorgänge, Flaggen, Strafen und die offizielle Endwertung. Genau diese Datei
braucht die Liga für **Driver of the Day** und für die Rennanalyse im Stint-Planner.

Dieses Paket ist der Logger **allein** — ohne die OBS-Overlays der Broadcast-Suite.
Wer nur mitschreiben will, startet eine Datei und fertig.

---

## Für Fahrer (Windows)

1. `RaceLogger.exe` herunterladen (Liga-Seite → **Race Logger**) und irgendwohin
   legen, z. B. `C:\iRacing\RaceLogger\`. Es wird **kein** Python gebraucht.
2. Doppelklick. Ein schwarzes Fenster bleibt offen — das ist der Logger.
   Zuklappen = Aufnahme aus.
3. Einmalig einrichten: <http://localhost:5009/league> öffnen, den persönlichen
   Schlüssel von der Liga-Seite einfügen, **Test connection** drücken.
4. Ab jetzt: vor dem Rennen starten, laufen lassen, fertig. Das fertige Log geht
   automatisch an die Liga; es bleibt zusätzlich im Ordner `logs`.

Nur **Rennsessions** werden aufgezeichnet — Practice und Qualifying nicht.
Ohne Schlüssel läuft der Logger genauso, dann liegt die Datei eben nur lokal.

Live-Ansicht während des Rennens: <http://localhost:5009>

### Wenn etwas klemmt

| Symptom | Ursache |
|---|---|
| „not sent“ / „failed“ auf der Setup-Seite | Netz weg oder Schlüssel falsch → **Send now** drücken, das Log liegt ja noch da |
| Windows warnt beim Start („unbekannter Herausgeber“) | SmartScreen bei unsignierten Programmen: *Weitere Informationen* → *Trotzdem ausführen* |
| Nichts wird aufgezeichnet | iRacing lief noch nicht, oder die Session ist kein Rennen |
| Port 5009 belegt | Läuft der Logger schon ein zweites Mal? |

---

## For non-German drivers (short version)

Download `RaceLogger.exe`, run it, open <http://localhost:5009/league> once and paste
your personal key from the league site. Start it before each race and leave it
running. Race sessions only; the log is also kept locally in `logs\`.

---

## Für Entwickler / Maintainer

Der Logger ist bewusst genügsam: er braucht nur `iracing_race_logger.py` +
`iracing_sdk_base.py` und die Pakete `flask`, `pyirsdk`, `requests`.

- **Aus dem Quellcode starten:** `start_race_logger.bat` (legt beim ersten Start
  ein eigenes venv an).
- **EXE lokal bauen (Windows):** `build_race_logger_exe.bat` → `dist\RaceLogger.exe`.
- **EXE über GitHub bauen:** Actions → *Build Race Logger* → *Run workflow*
  (oder Tag `race-logger-v1.1.0` pushen). Die Action hängt `RaceLogger.exe` und
  `RaceLogger-source.zip` an ein Release; die Liga-Seite verlinkt fest auf
  `releases/latest/download/…`, also zeigt jeder Download automatisch auf das
  neueste Release.
- **Source-Zip lokal bauen:** `./make_race_logger_zip.sh`.

### Upload-Protokoll

`POST <liga>/api/race-log` mit `Authorization: Bearer <key>` und dem Log als
Multipart-Feld `file`. Antwort: `{ok, duplicate, id, round, message}`.
`GET` auf dieselbe URL prüft nur den Schlüssel (das macht *Test connection*).
Identische Dateien werden serverseitig über einen SHA-256 erkannt — mehrfaches
Senden erzeugt keine Dubletten, und mehrere Fahrer dürfen dasselbe Rennen
hochladen (der Admin nimmt eins davon).

Einstellungen liegen in `league_manager.json` neben dem Programm
(`url`, `token`, `auto`), der Upload-Status je Datei in `logs\upload_state.json`.
