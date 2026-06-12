# iRacing Overlays

🇬🇧 [English version](README.md)

Eine Sammlung kostenloser, selbst gehosteter Broadcast-Overlays für [iRacing](https://www.iracing.com/), gebaut mit Python + Flask. Jedes Overlay läuft als kleiner lokaler Webserver und wird in [OBS Studio](https://obsproject.com/) als Browser-Quelle eingebunden — keine Abos, keine Accounts, keine Cloud-Dienste.

Alle Telemetriedaten werden lokal über `pyirsdk` aus dem iRacing SDK gelesen. Es verlässt nichts deinen Rechner, außer du aktivierst bewusst das optionale Public-Sharing-Feature.

## Overlays

| Overlay | Skript | Port | Was es zeigt |
|---|---|---|---|
| Dashboard | `iracing_dashboard.py` | 5000 | Operator-Dashboard: Live-Telemetrie, Kamerasteuerung, Incident- & Overtake-Erkennung mit Auto-Replay |
| Grid | `iracing_grid.py` | 5001 | Qualifying-Grid mit farbigen Auto-Silhouetten |
| Results | `iracing_results.py` | 5002 | Komplettes Rennergebnis (Abstände, Incidents, schnellste Runde) |
| Results Lite | `iracing_results_lite.py` | 5003 | Minimales Ergebnis-Overlay |
| Live Indicator | `iracing_live_indicator.py` | 5004 | LIVE- / REPLAY-Badge |
| Standings | `iracing_standings.py` | 5005 | Live-Standings mit Session-Infoleiste, Markenlogos, Pit-Infos |
| Livery | `iracing_livery.py` | 5006 | 3D-gerendertes Auto + Fahrername des Fahrers, der gerade im Bild ist |
| Track Map | `iracing_trackmap.py` | 5007 | SVG-Streckenkarte mit Live-Punkten — komplett offline, ~300 Strecken enthalten |
| Flag | `flag_overlay.py` | 5008 | Flaggenstatus der Session (Grün, Gelb, Weiß, Zielflagge, …) |
| Race Logger | `iracing_race_logger.py` | 5009 | JSONL-Rennlog + Live-Race-Monitor mit Charts |
| Championship | `iracing_championship.py` | 5010 | Live-Meisterschaftsprojektion (benötigt ein externes League-Manager-Backend) |
| Session Info | `iracing_session_info.py` | 5011 | Session-Name + Gesamt- / Restzeit oder Runden |
| Corner Cues | `iracing_drivingline.py` | 5012 | Kurvenhinweise (Richtung, Schwierigkeit, Distanz) für Strecken, auf denen die Racing-Line-Hilfe deaktiviert ist |

Alle Overlays laufen parallel — jedes auf einem eigenen Port.

## Voraussetzungen

- **Windows** (iRacing läuft nur unter Windows)
- **Python 3.10+** — [python.org/downloads](https://www.python.org/downloads/) (bei der Installation "Add Python to PATH" ankreuzen)
- **iRacing** auf demselben Rechner
- **OBS Studio** (oder ein anderes Tool mit Browser-Quellen) zum Streamen

## Installation

```bash
git clone https://github.com/halvar20000/iracing-overlays.git
cd iracing-overlays
pip install -r requirements.txt
```

Das war's. Die Abhängigkeiten sind Flask, pyirsdk, Pillow, requests und (nur Windows) pywin32.

## Verwendung

### Option 1 — GUI-Launcher (empfohlen)

Doppelklick auf **`launch_gui.bat`**. Es öffnet sich eine kleine Desktop-App mit Start/Stop-Button und Status-Punkt pro Overlay, dazu Start All / Stop All und ein Log-Bereich.

### Option 2 — Batch-Launcher

**`launch_all.bat`** ausführen — startet jedes Overlay in einem eigenen Konsolenfenster.

### Option 3 — Ein Terminal

```bash
python launch_all.py
```

Startet alle Overlays in einem Terminal mit farbigen Log-Präfixen. Plattformübergreifend.

Jedes Overlay lässt sich auch einzeln starten: `python iracing_standings.py`

### Overlays in OBS einbinden

1. Gewünschte Overlays starten.
2. In OBS: **Quellen → + → Browser**, URL `http://localhost:<port>` (z. B. `http://localhost:5005` für die Standings).
3. Die meisten Overlays starten mit dunklem Debug-Hintergrund. In die Browser-Quelle klicken (Interagieren) und **`H`** drücken, um den transparenten Stream-Modus umzuschalten. Live Indicator und Flag-Overlay sind immer transparent.

Die Overlays binden an `0.0.0.0` — ein zweiter PC im LAN erreicht sie also über `http://<deine-ip>:<port>`. Praktisch für einen dedizierten Streaming-PC.

### Tipp: selbstheilende Browser-Quellen (kein manuelles Aktualisieren)

OBS lädt jede Browser-Quelle nur einmal beim Start — läuft der Overlay-Server in dem Moment noch nicht, bleibt die Quelle leer, bis man auf **Aktualisieren** klickt. Um das zu vermeiden, die Loader-Seiten aus `obs_loaders/` verwenden: in den Eigenschaften der Browser-Quelle **Lokale Datei** ankreuzen und z. B. `obs_loaders/standings.html` auswählen statt eine URL einzutragen. Der Loader bettet das Overlay ein und versucht es automatisch erneut, bis der Server antwortet — die Startreihenfolge spielt keine Rolle mehr, und die Overlays kommen auch nach einem Server-Neustart von selbst zurück. Nach dem Hinzufügen eines neuen Overlays die Loader-Seiten mit `python make_obs_loaders.py` neu generieren.

## Track Map — Offline-Streckenbibliothek

Die Streckenkarte braucht **keinen iRacing-Login und kein Internet**. Die Geometrie für ~300 Streckenvarianten liegt als JSON in `tracks/`. Das Overlay liest den Streckennamen aus dem SDK und lädt die passende Datei; fehlt eine Strecke, erscheint der Hinweis "TRACK MAP NOT BUNDLED".

Geometriequellen: die Open-Source-Streckenbibliothek von [SIMRacingApps](https://github.com/SIMRacingApps/SIMRacingApps) (Apache 2.0) und [OpenStreetMap](https://www.openstreetmap.org/) (ODbL). Vollständige Attribution in `tracks/NOTICE.txt`, Abdeckungsstatus in `tracks/MISSING_TRACKS.md`.

## Corner Cues — Fahrhilfe auf dem Bildschirm

Für Sessions, in denen iRacing die Racing-Line-Hilfe deaktiviert, analysiert `iracing_drivingline.py` (Port 5012) die mitgelieferte Streckengeometrie und liefert Hinweise zur nächsten Kurve: Richtungspfeil, Kurvennummer, Schwierigkeit (HAIRPIN → FAST), geschätzte Scheitelgeschwindigkeit und einen Distanz-Countdown.

Zwei Anzeigemöglichkeiten:

- **OBS-Browser-Quelle** unter `http://localhost:5012` (für den Stream), oder
- **`driving_line_window.py`** — ein transparentes, klick-durchlässiges, immer im Vordergrund liegendes Desktop-Fenster für den Fahrer. Beim Fahren manuell starten (iRacing muss im **randlosen Fenstermodus** laufen). Mit `--debug` lässt sich das Fenster positionieren; die Koordinaten werden zum Übernehmen ausgegeben.

Die Geschwindigkeiten sind fahrzeugunabhängige Schätzwerte. Für Liga-Rennen vorher mit den Stewards klären, ob externe Fahrhilfen erlaubt sind.

## Race Logger

`iracing_race_logger.py` (Port 5009) schreibt pro Rennen eine JSONL-Datei nach `logs/` — Runden, Boxenstopps, Flaggen, Strafen, Incidents, Positionen und das Endergebnis. Die Seite auf Port 5009 ist ein vollwertiger Live-Race-Monitor: Fahrertabelle, Event-Timeline und Rundenzeit- / Positions- / Abstands-Charts, die sich als eigene Browser-Quelle in OBS einbinden lassen (`/chart/render`).

### Optional: Public Sharing für Zuschauer

Der Logger hat öffentliche Nur-Lese-Endpunkte (`/share/chart`, `/share/standings`), über die Twitch-/Discord-Zuschauer selbst ein Chart öffnen und ihre eigenen Fahrer auswählen können — ohne deine Operator-Ansicht zu beeinflussen. Veröffentlichen per kostenlosem [Cloudflare Tunnel](https://www.cloudflare.com/products/tunnel/); an externe Zuschauer werden nur `/share/*`-Pfade ausgeliefert, alle Admin-Endpunkte bleiben lokal. Anleitung: [`CLOUDFLARE_TUNNEL_DE.md`](CLOUDFLARE_TUNNEL_DE.md).

## Auto-Markenlogos

Das Standings-Overlay zeigt Herstellerlogos aus `brands/*.svg`. Neue Marke hinzufügen: `brands/<slug>.svg` in den Ordner legen und bei Bedarf ein Präfix-Mapping in `car_brands.py` ergänzen.

## Fehlerbehebung

- **Overlay zeigt "waiting for iRacing"** — iRacing muss laufen (Session geladen, nicht nur die UI).
- **Skript geändert, aber altes Verhalten** — Overlays behalten den alten Code im Speicher; Overlay neu starten.
- **Schwarzer Kasten statt Transparenz in OBS** — in der Browser-Quelle (Interagieren) `H` drücken, um den Stream-Modus umzuschalten.
- **Port bereits belegt** — ein anderes Overlay oder Programm nutzt den Port; der Port jedes Skripts steht unten in `app.run(...)`.

## Lizenz & Attribution

Streckengeometrie: SIMRacingApps-Projekt von Jeffrey Gilliam (Apache 2.0) und OpenStreetMap-Mitwirkende (ODbL) — siehe `tracks/NOTICE.txt`.
