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
   Meldungen an einen „ATVO Race Control Server“ schicken (TCP). Dieses
   Overlay spielt diesen Server.

## OBS

Browser-Quelle → Lokale Datei → `obs_loaders/rc_banner.html` (≈ 950 × 120).
Optionen an der URL: `?secs=8` Anzeigedauer, `?zoom=1.2`, `?flags=0` nur
iRaceControl, `?atvo=0` nur iRacing-Flaggen, `?demo=1` Vorschau.

## Operator-Seite

`http://localhost:5019/panel` – zeigt ob iRaceControl verbunden ist, alle
Meldungen, die **Rohdaten** die ankommen, und ein Feld um selbst eine
Meldung einzublenden (auch ohne iRaceControl nutzbar).

## iRaceControl einrichten (beim Stewart)

1. In iRaceControl: **Settings › CACS** → ATVO-Integration einschalten.
2. Server-Adresse = IP des Stream-PCs, Port **5020**, Passwort leer.
   - Stewart im selben Netz: die LAN-IP des Stream-PCs (z. B. 192.168.86.138).
   - Stewart woanders (Internet): die **öffentliche IP** des Anschlusses und
     im Router eine Portweiterleitung TCP 5020 → Stream-PC.
3. Unter CACS die Meldungsarten auf die ATVO-Ziele routen (Race Control
   Messages, Strafen). Überschriften / Dauer dort sind egal.
4. Windows-Firewall auf dem Stream-PC: beim ersten Start fragt Windows –
   **Zugriff zulassen** (privat + ggf. öffentlich), sonst kommt nichts an.

Anderer Port: `rc_config.json` neben den Skripten:
`{"atvo_port": 5020}` (danach Overlay neu starten).

## Erster Test (GT3 WCT, Dienstag)

Das Datenformat von iRaceControl → ATVO ist nirgends beschrieben. Alles,
was ankommt, wird roh mitgeschrieben in `logs/atvo_capture_<Datum>.log`.

1. Overlay starten, `/panel` öffnen: „bereit auf TCP-Port 5020“.
2. Stewart verbindet iRaceControl → im Panel „verbunden: …“.
3. Stewart schickt eine Testmeldung, eine Strafe, ein FCY.
4. Im Panel unter „ATVO Rohdaten“ prüfen, was gelesen wurde.
5. Die Datei `logs/atvo_capture_<Datum>.log` an Claude geben – damit wird
   die Anzeige an das echte Format angepasst.

Falls iRaceControl „nicht verbunden“ meldet, obwohl das Panel eine
Verbindung zeigt: dann erwartet iRaceControl eine Antwort vom Server – auch
das steht in der Capture-Datei und lässt sich nachbauen.
