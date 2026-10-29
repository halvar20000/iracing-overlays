# Race Control Overlay (Port 5019)

Violettes **RACE CONTROL**-Banner im F1-Stil für OBS. Zwei Quellen:

1. **iRacing selbst** – funktioniert immer, ohne Einrichtung:
   Full Course Yellow, Safety Car in this lap, Green Flag, Red Flag (nur im
   Rennen) und pro Auto Black Flag (Strafe), Warning, Meatball (Reparatur),
   Disqualifiziert, End of Line, Wave Around, Free Pass – mit Startnummer,
   Team (Teamrennen) und Klassenfarbe wie Tower / Trackmap.
   iRacing verrät **nicht**, welche Strafe eine Black Flag ist, und den Chat
   gibt das SDK nicht heraus.
2. **iRaceControl** – die Texte der Stewards. iRaceControl kann seine
   Meldungen an einen „ATVO Race Control Server“ schicken. Dieses Overlay
   spielt diesen Server. Technisch ist das **SignalR** (ein Web-Protokoll
   von Microsoft, Hub „RaceControlHub“) – am 26.10. beim ersten Verbinden
   gesehen.

## OBS

Browser-Quelle → Lokale Datei → `obs_loaders/rc_banner.html` (≈ 950 × 120).
Optionen an der URL: `?secs=8` Anzeigedauer, `?zoom=1.2`, `?flags=0` nur
iRaceControl, `?atvo=0` nur iRacing-Flaggen, `?demo=1` Vorschau.

## Operator-Seite

`http://localhost:5019/panel` – zeigt ob iRaceControl verbunden ist, alle
Meldungen, die **Rohdaten** die ankommen, und ein Feld um selbst eine
Meldung einzublenden (auch ohne iRaceControl nutzbar).

## iRaceControl einrichten (beim Stewart)

Im iRaceControl-Einstellungsfenster, Bereich **ATVO SETTINGS**:

| Feld | Wert |
|---|---|
| ATVO Enabled | an |
| Server address | Adresse des Stream-PCs (siehe unten) |
| Server Port | **5020** |
| Server password | das Passwort aus `rc_config.json` (`atvo_password`), sonst leer |
| Headlines / Sec | egal, so lassen |
| ATVO Incidents | an = auch Incidents schicken |

**Wichtig – Routing:** iRaceControl schickt eine Meldung nur an ATVO, wenn
sie in **Settings › CACS** in der **Routing-Tabelle** einem ATVO-Ziel
zugeordnet ist (Standard: nur iRacing-Chat). Jede Meldungsart **einem**
ATVO-Ziel zuordnen (Info → INFO, Strafen → PENALTY, Verwarnungen → WARNING …).
Dieselbe Meldung an mehrere Ziele kommt mehrfach an (wir zeigen sie nur
einmal). Verbunden = Statusleiste „ATVO: <Adresse:Port> CONNECTED“.

Adresse:
- Stewart im selben Netz: die LAN-IP des Stream-PCs (192.168.86.138).
- Stewart woanders (Internet): die **öffentliche IP** des Anschlusses (oder
  ein DNS-Name, der direkt darauf zeigt – **nicht** über den Cloudflare-
  Tunnel / orange Wolke, der leitet nur Webseiten über 443 weiter) und im
  Router eine Portweiterleitung TCP 5020 → 192.168.86.138:5020.
  Dann unbedingt ein **Passwort** setzen: sonst kann jeder, der den Port
  findet, Texte auf den Stream schicken.

Windows-Firewall auf dem Stream-PC: beim ersten Start fragt Windows –
**Zugriff zulassen**, sonst kommt nichts an.

`rc_config.json` neben den Skripten (danach Overlay 5019 neu starten):
`{"atvo_port": 5020, "atvo_password": "einWort"}`
iRacings eigene Verwarnung (gefaltete schwarze Flagge, ohne Grund) wird
nicht gezeigt; `"iracing_warnings": true` blendet sie wieder ein.

**Andere Overlays ausblenden, solange eine Meldung läuft:** in `rc_config.json`
`"hide_sources": ["Duell", "Fahrerkarte"]` (die OBS-Quellennamen, Groß/klein
egal). Sobald das Banner eine Meldung zeigt, werden diese Quellen in allen
Szenen ausgeblendet und gut 1 s nach der letzten Meldung wieder eingeblendet.
Eine Quelle, die du selbst ausgeschaltet hattest, bleibt aus. Für die Verbindung
zu OBS gilt der Block `"obs"` aus `show_config.json` (WebSocket-Server in OBS
einschalten). Alternativ kannst du in `rc_config.json` einen eigenen Block
`"obs": {"host": "127.0.0.1", "port": 4455, "password": ""}` eintragen.
Ob es greift, zeigt `/panel`. Bei einer Banner-Quelle mit `?hide=0` passiert nichts.

Bewährte Routing-Tabelle (CACS, je Art höchstens EIN ATVO-Ziel):
RACE_CONTROL_MESSAGE / SEQUENCER_MESSAGE → ATVO_INFO · NOTED / INVESTIGATING
→ ATVO_INVESTIGATION · RACE_INCIDENT → ATVO_NO_FURTHER_ACTION ·
CLEAR_PENALTIES → ATVO_CLEAR_PENALTY · TIME_PENALTY / DRIVE_THROUGH / STOP_GO
/ DSQ → ATVO_PENALTY · alles andere (v. a. PRIVATE) kein ATVO-Ziel.

## Aus unserem iCASControl (8080)

iCAS schickt Steward-Entscheidungen direkt hierher (`POST /rc/steward`,
Quelle „icas“): im iCAS-Header **STREAM BANNER** einschalten, unter
**Address** die Adresse dieses Overlays eintragen (auf dem Stream-PC
`http://192.168.86.138:5019`, auf demselben PC `http://localhost:5019`),
**Test** zeigt „TEST · iCASControl“. `?icas=0` blendet diese Meldungen in
einer Quelle aus.

## Offene Strafen im Tower

Das Overlay merkt sich je Auto die offenen Strafen (`/rc/penalties`) — aus
iCAS, iRaceControl und iRacings eigener schwarzer Flagge. Der Tower (5005)
zeigt sie hinter dem Namen: **DT**, **SG**, **+10s** (Zeitstrafen addiert),
rot **DSQ**, blau **INV** (in Untersuchung, bis zur Entscheidung, max.
10 min). DT / SG verschwinden, sobald iRacings schwarze Flagge für das Auto
aus ist (abgesessen) oder die Strafen gelöscht werden. Neue Session = leer.
Im Tower: `?pen=0` aus, `?warn=1` zeigt auch Verwarnungen (W2). Läuft 5019
nicht, zeigt der Tower einfach keine Strafen.

## Erster Test (GT3 WCT, Dienstag)

Welche Hub-Methoden iRaceControl aufruft (Namen, Argumente), ist nirgends
beschrieben. Jede Anfrage wird mitgeschrieben in
`logs/atvo_capture_<Datum>.log`; der Text wird aus den Argumenten gelesen.

1. Overlay starten, `/panel` öffnen: „bereit auf Port 5020“.
2. Stewart verbindet iRaceControl → im Panel „verbunden: …“.
3. Stewart schickt eine Testmeldung, eine Strafe, ein FCY.
4. Im Panel unter „ATVO Rohdaten“ prüfen, was gelesen wurde.
5. Die Datei `logs/atvo_capture_<Datum>.log` an Claude geben – damit wird
   die Anzeige an das echte Format angepasst.

