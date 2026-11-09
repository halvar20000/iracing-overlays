# Replay auf dem zweiten PC (Sim-Rig)

Der Stream-PC bleibt immer live. Die Replays laufen auf dem Sim-Rig, das dieselbe
Session mit einem **zweiten iRacing-Konto** als Zuschauer verfolgt. Sein Bild kommt
per NDI (DistroAV) in OBS.

## Einmal einrichten

**Sim-Rig**
1. Overlay-App installieren (install.bat), im Launcher das **Dashboard (5000)** starten.
2. Windows fragt beim ersten Start nach der Firewall → **Private Netzwerke erlauben**
   (sonst erreicht der Stream-PC Port 5000 nicht).
3. Im Router dem Sim-Rig eine **feste IP** geben (z. B. 192.168.86.50).
4. OBS + NDI Runtime + DistroAV: Spielaufnahme von iRacing, *Tools › DistroAV NDI
   Settings › Main Output* an, Name z. B. `SIMRIG`.
5. Auto-Replay im Dashboard des Sim-Rigs **aus** lassen (der Stream-PC entscheidet).

**Stream-PC**
1. NDI Runtime + DistroAV, Quelle **NDI Source** → `SIMRIG`.
2. OBS-Szene **`Replay`**: NDI-Quelle groß, die Live-iRacing-Aufnahme als kleines Fenster.
3. Im Overlay-Ordner die Datei `replay_pc_config.json` anlegen:

```json
{"url": "192.168.86.50", "scene": "Replay"}
```

Kein Neustart nötig, die Datei wird bei jeder Änderung neu gelesen.

## Benutzen

Alles wie bisher am Dashboard des **Stream-PCs**: ▶ 10s / 20s in der Incident- oder
Überholliste, die Stream-Deck-Tasten `replay_last…`, Auto-Replay.

- Das Sim-Rig springt zu genau dem Moment (gleiche Session-Zeit auf beiden PCs),
  zeigt das Auto und geht danach selbst wieder live.
- OBS schaltet auf `Replay` und **von selbst zurück** auf die Szene davor, sobald das
  Sim-Rig wieder live ist (antwortet es nicht: nach Replay-Länge + 20 s).
- Hast du die Szene inzwischen selbst gewechselt, bleibt sie, wie sie ist.
- Die Listen auf dem Stream-PC bleiben erhalten (der Stream-PC springt ja nicht zurück).
- Ist das Sim-Rig nicht erreichbar, läuft das Replay wie früher auf dem Stream-PC.

## Optionen in `replay_pc_config.json`

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `url` | – | Adresse des Sim-Rigs (IP reicht, Port 5000 wird ergänzt). Leer = aus |
| `scene` | `Replay` | OBS-Szene während des Replays. `""` = OBS nicht anfassen |
| `source` | – | statt Szenenwechsel: diese Quelle (z. B. `Replay NDI`) in der laufenden Szene ein- und danach wieder ausblenden. Die NDI-Quelle dafür in die iRacing-Szene legen, **unter** die Overlays |
| `back_scene` | – | feste Szene danach (sonst die Szene vor dem Replay) |
| `fallback_local` | `true` | Sim-Rig weg → Replay auf dem Stream-PC |
| `extra_s` | `20` | so lange nach der Replay-Länge auf das Sim-Rig warten |

Status: `http://localhost:5000/replay_pc` (Stream-PC) und
`http://<Sim-Rig>:5000/replay/remote/status`.

REGIE (KI-Kamera) macht ihre Replays weiterhin auf dem Stream-PC.
