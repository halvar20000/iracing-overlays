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
- **Jump to round** (Start / Runde N + Apply) am Stream-PC: Das Sim-Rig springt an
  diese Stelle und spielt von dort weiter, OBS zeigt das Replay-Bild. Zurück mit
  **Go Live**. Ist das Sim-Rig nicht erreichbar, springt der Stream-PC selbst.
- **Replay zeigt nichts / hängt:** am Stream-PC **Go Live** drücken (Dashboard,
  Kamera-/Fahrer-Panel oder Stream-Deck-Taste `go_live`). Das Sim-Rig geht dann
  ebenfalls sofort live, und OBS schaltet gleich zurück aufs Livebild – auch wenn
  das Sim-Rig nicht antwortet.
- **Replays direkt am Sim-Rig** (in seinem Dashboard oder in iRacing dort gestartet)
  blendet der Stream-PC ebenfalls ein: Er fragt das Sim-Rig alle 0,5 s, ob es live ist.
  Ist es nicht live (Replay, Pause, Zurückspulen), wird die Quelle bzw. Szene eingeblendet.
  Ist es wieder live, wird sie ausgeblendet. Beide PCs brauchen dafür denselben Stand
  (Update auf beiden). Achtung: Steht das Sim-Rig auf Pause oder bleibt es im Replay
  hängen, bleibt die Quelle sichtbar.

## Optionen in `replay_pc_config.json`

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `url` | – | Adresse des Sim-Rigs (IP reicht, Port 5000 wird ergänzt). Leer = aus |
| `scene` | `Replay` | OBS-Szene während des Replays. `""` = OBS nicht anfassen |
| `source` | – | statt Szenenwechsel: diese Quelle (z. B. `Replay NDI`) in der laufenden Szene ein- und danach wieder ausblenden. Mehrere Namen mit Komma, z. B. `.73remoteview, .73Live_Replay, .73Owncarlivery` (das, was die Stream-Deck-Taste „.73Remote“ einblendet). Die NDI-Quelle dafür in die iRacing-Szene legen, **unter** die Overlays |
| `hide_sources` | `[]` | nur mit `source`: diese Quellen solange ausblenden und danach wieder einblenden, z. B. `["Owncarlivery", "Replay/Live"]` (nur die, die gerade an waren) |
| `follow` | `true` | auch Replays einblenden, die direkt am Sim-Rig gestartet wurden. `false` = nur die vom Stream-PC |
| `back_scene` | – | feste Szene danach (sonst die Szene vor dem Replay) |
| `transition` | – | OBS-Übergang (z. B. `Replay Stinger`) nur für die Replay-Wechsel, danach wieder der vorige |
| `fallback_local` | `true` | Sim-Rig weg → Replay auf dem Stream-PC |
| `extra_s` | `20` | so lange nach der Replay-Länge auf das Sim-Rig warten |

Status: `http://localhost:5000/replay_pc` (Stream-PC) und
`http://<Sim-Rig>:5000/replay/remote/status`.

REGIE (KI-Kamera) macht ihre Replays weiterhin auf dem Stream-PC.

## Übergang mit „REPLAY“-Stinger

`stingers/replay_stinger.webm` (1,1 s, transparent) – ein dunkler Balken mit
oranger Kante wischt mit „REPLAY“ übers Bild, dahinter wird geschnitten.

1. OBS › Dock **Szenenübergänge** › **+** › **Stinger**, Name z. B. `Replay Stinger`.
2. Videodatei: `stingers\replay_stinger.webm` im Overlay-Ordner.
3. Übergangspunkt-Typ **Zeit (ms)**, Übergangspunkt **500**.
4. In **⚙ Einstellungen** › Replay-PC › **OBS-Übergang nur für diese Wechsel**:
   `Replay Stinger` eintragen (oder `"transition": "Replay Stinger"` in
   `replay_pc_config.json`). Dann kommt der Stinger NUR beim Wechsel zum Replay
   und zurück, ein Wechsel von Hand (z. B. Moderator → iRacing) bleibt beim
   normalen Übergang.
5. Bei den Szenen **Replay** und **iRacing** darf KEIN „Übergang überschreiben“
   gesetzt sein (Rechtsklick › Übergang überschreiben › Nicht überschreiben) –
   der hätte Vorrang und käme bei jedem Wechsel.

Im `source`-Modus (Quelle ein-/ausblenden) stattdessen bei der NDI-Quelle
Rechtsklick › **Einblendübergang** / **Ausblendübergang** › Stinger.
Anderer Text: `python make_replay_stinger.py --text "WIEDERHOLUNG"`.
