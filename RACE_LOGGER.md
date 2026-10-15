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

### Incident-Punkte im Log (`inc` / `inc_snapshot`)

Seit 05.09.2026 schreibt der Logger die **offiziellen iRacing-Incident-Punkte**
jedes Autos mit — die Zahlen, auf die sich eine Durchfahrtsstrafe stützt.

- `inc` — bei **jeder Änderung** eines Autos: `car_idx`, `car_number`, `driver`,
  `team`, `lap`, `delta` (z. B. 2 bei einem 2x), `total` (neuer Stand),
  `t_session` + `t_wall`.
- `inc_snapshot` — einmal pro Minute alle Stände als `{"car_idx": total}`.
  Damit lässt sich die Timeline auch dann rekonstruieren, wenn der Logger
  mitten im Rennen gestartet oder nach einem Absturz neu gestartet wurde.

Quelle ist `SessionInfo.Sessions[].ResultsPositions[].Incidents`; in der
Telemetrie gibt es **kein** CarIdx-Array für Incidents, nur Werte für das
eigene Auto. In Team-Events ist der Wert der **Auto-/Team-Stand** — genau der,
den Race Control für einen DT heranzieht. Gelesen wird alle
`INC_POLL_INTERVAL` (2 s), weil jeder Zugriff das SessionInfo-YAML parst.

**Nicht verwechseln** mit den `incident`-Events: die kommen aus der
Dreher-/Kontakt-**Erkennung** des Dashboards (Port 5000) und fehlen komplett,
wenn das Dashboard nicht mitläuft. `inc` braucht nur iRacing selbst.

Test: `python3 test_inc_points.py` (29 Checks, gestubbtes irsdk + Fake-Uhr).

### Reifenzustand, Setup und frisch/gebraucht (`pit` / `setup`)

Seit 15.10.2026, auf Johanns Vorschlag. iRacing liefert diese Daten **nur für
das eigene Auto** (es gibt kein CarIdx-Array dafür) und die Reifenabnutzung
**nur, während das Auto in der Box steht** — auf der Strecke bleibt der Wert
auf dem Stand des letzten Stopps stehen. Genau deshalb hängt alles am
Boxenstopp und nicht an der Runde.

Das `pit`-Event des eigenen Autos trägt zusätzlich:

| Feld | Bedeutung |
|---|---|
| `tire_wear_in` | Restprofil je Reifen **vor** dem Stopp, drei Streifen (innen/mitte/außen) in % — das, was heruntergekommen ist |
| `tire_wear_out` | Restprofil **nach** dem Stopp — das, was rausgefahren ist |
| `tire_wear_samples` | alle unterschiedlichen Messungen des Stopps mit `t`; die Rohdaten, aus denen die Felder oben abgeleitet sind |
| `tires` | je Ecke `{wear_in, wear_out, changed, fresh}` |
| `tires_changed` | Ecken, deren Profil nach oben gesprungen ist → neuer Satz montiert |
| `left_on_fresh` | `true` = alle gewechselten Ecken ≥ 98 % (ungebrauchter Satz), `false` = gebrauchter Satz aufgezogen |
| `service` | was in der Box **angefordert** war: `requested` (`lf_tire`…`rr_tire`, `fuel`, `windshield_tearoff`, `fast_repair`), `flags`, `fuel_l`, `compound` |

`changed: null` heißt: iRacing hat während des Stopps nur **eine** Messung
veröffentlicht, also ist nicht belegbar, ob gewechselt wurde — dann sagt
`service.requested`, was der Fahrer bestellt hatte, und `tire_wear_out` trotzdem
den Zustand. Geraten wird nicht.

Das `setup`-Event kommt beim ersten Mal und bei **jeder Änderung**:
`name` (Datei aus der Garage), `modified` (seit dem Laden verstellt — ein Name
allein lügt, sobald jemand einen Klick dreht), `load_type`, `passed_tech`,
`update_count`, `first`. Im Rennen also genau eins, in der Practice eines pro
Iteration. Quelle ist `DriverInfo` im Session-String, **nicht** die Telemetrie.

Reifentemperaturen: `tire_temps` am Runden-Event ist die **in der Box gemessene**
Oberflächentemperatur (`LFtempL/M/R`) — sie steht während eines Stints still,
deshalb trägt sie jetzt `source: "pit_box"`. Neu daneben: `tire_temps_live`
(`LFtempCL/CM/CR`, Karkasse, aktualisiert live) — das ist der Wert, den eine
Stint-Auswertung pro Runde plotten will.

Prüfen, ob iRacing die Variablen auf einem PC überhaupt liefert:
`python check_sdk.py` **aus der Box heraus** — der Abschnitt „Tire condition +
setup“ zeigt jede Variable und schreibt `MISSING`, wo nichts kommt.

Test: `python3 test_tire_capture.py` (44 Checks, gestubbtes irsdk/flask,
Stopp-Szenarien: vier neue Reifen, gebrauchter Satz, nur Tanken, Spectator).

### Upload-Protokoll

`POST <liga>/api/race-log` mit `Authorization: Bearer <key>` und dem Log als
Multipart-Feld `file`. Antwort: `{ok, duplicate, id, round, message}`.
`GET` auf dieselbe URL prüft nur den Schlüssel (das macht *Test connection*).
Identische Dateien werden serverseitig über einen SHA-256 erkannt — mehrfaches
Senden erzeugt keine Dubletten, und mehrere Fahrer dürfen dasselbe Rennen
hochladen (der Admin nimmt eins davon).

Einstellungen liegen in `league_manager.json` neben dem Programm
(`url`, `token`, `auto`, `live`), der Upload-Status je Datei in `logs\upload_state.json`.

### Live-Boxenstopps (ab v1.4.1)

Verlässt das EIGENE Auto die Boxengasse und hat der Stopp Reifenwerte, geht
genau dieser `pit`-Event sofort per `POST <liga>/api/race-log/live` an die
Liga (JSON `{v, session:{subsession_id, session_id, session_num, session_name,
track}, driver:{cust_id, car_idx}, event}`, gleicher Bearer-Schlüssel, ohne
die rohen `tire_wear_samples`). Zwei Versuche, 10 s Timeout, im Hintergrund;
ein verlorener Live-Stopp kostet nichts, das Log folgt. Ausschalten:
`"live": false` bzw. der Haken auf `/league`. Das Overlay dazu:
REIFEN_LIVE.md. `session_start` trägt dafür jetzt `subsession_id` /
`session_id`, die Fahrerliste unverändert.

Test: `python3 test_cls_live.py` (32 Checks: Live-Senden, nichts für
fremde Autos / ohne Schlüssel / bei `live: false`, 401 ohne Wiederholung,
5xx mit; dazu alles, was v1.4.0 verloren hatte, siehe unten).

### v1.4.0 war kaputt (behoben in v1.4.1)

Commit 626c6ff (10.10.) hat eine ALTE lokale Fassung des Loggers eingecheckt
und dabei still entfernt: den Liga-Upload samt `/league`-Seite, den
Log-Ordner neben der .exe (eine eingefrorene .exe schrieb ihre Logs damit in
ihren Temp-Ordner, der beim Beenden verschwindet), die Temperatur-Proben
(`weather`), die Fahrerwechsel (`driver_change`) und die Incidents ohne
Dashboard (`incident` mit `source: "sdk"`). Das am 15.10. gebaute Release
v1.4.0 hatte nichts davon. v1.4.1 setzt alles wieder ein (aus 626c6ff^,
Stand v1.3.1) und behält `inc` / `inc_snapshot` und die Reifendaten.
