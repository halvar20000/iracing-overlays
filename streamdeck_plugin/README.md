# OBS Seitenwechsel – Stream-Deck-Plugin

Wechselt die beiden **Stream Deck XL** automatisch auf die passende Seite,
sobald in OBS die Szene wechselt – egal ob per Ulanzi, Maus oder Makro.
Die anderen Decks (Stream Deck, Stream Deck +) werden nie angefasst.

| OBS-Szene | Seite auf XLlinks und XLrechts |
|---|---|
| Intro | 1 |
| Moderator | 2 |
| iRacing | 3 |
| Werbung | 4 |
| Schedule PCC | 5 |
| jede andere Szene | Decks bleiben, wo sie sind |

## Installation (Streaming-PC)

1. In OBS: *Werkzeuge → WebSocket-Server-Einstellungen* → Server aktiv,
   Port **4455** (Authentifizierung aus – so ist es eingestellt).
2. Doppelklick auf **`dist/com.halvar.obspages.streamDeckPlugin`** →
   Stream-Deck-App fragt „Installieren?“ → Ja.
3. Die App fragt, ob die Profile **XLlinks** und **XLrechts** installiert
   werden sollen → **Ja**. Es sind die Profile aus deinem Export.
4. Die Decks müssen in der Stream-Deck-App **XLlinks** und **XLrechts**
   heißen (Geräte-Einstellungen). Groß-/Kleinschreibung und Leerzeichen sind
   egal.

## Wichtig: Seiten anlegen

Stream Deck erlaubt einem Plugin nur, auf **seine eigenen** Profile zu
wechseln. Nach der Installation gibt es deshalb zwei Profile namens XLlinks
(und zwei XLrechts): deine alten und die des Plugins. **Bearbeite nur die
des Plugins** – das sind die, auf die beim Szenenwechsel umgeschaltet wird.
Deine alten kannst du danach löschen.

Deine exportierten Profile haben bisher nur **eine** Seite mit Tasten. Lege im
Plugin-Profil die Seiten 2–5 an (rechts neben den Seitenpunkten „+“) und
belege sie. Fehlt eine Seite, kann der Wechsel auf sie nicht klappen (was
die App dann genau anzeigt, ließ sich ohne echtes Deck nicht testen).

## Zuordnung ändern

Szenennamen, Seitennummern, Deck-Namen und OBS-Port stehen in
`config.json` im installierten Plugin-Ordner:

`%APPDATA%\Elgato\StreamDeck\Plugins\com.halvar.obspages.sdPlugin\config.json`

Nach dem Ändern die Stream-Deck-App neu starten. (Ein Plugin-Update
überschreibt die Datei – Änderungen deshalb auch hier im Repo eintragen.)

## Taste „OBS-Status“ (optional)

In der Aktionsliste unter **OBS Seitenwechsel**. Zeigt „OBS aus“ oder die
aktuelle Szene. Drücken = Seiten für die laufende Szene erneut setzen, z. B.
nachdem du von Hand geblättert hast.

## Fehlersuche

Log: `%APPDATA%\Elgato\StreamDeck\Plugins\com.halvar.obspages.sdPlugin\logs\`
– dort steht, ob OBS verbunden ist, welche Decks gefunden wurden und was bei
jedem Szenenwechsel umgeschaltet wurde („scene "iRacing" -> XLlinks: …“).

## Entwickeln

```
npm install
npm run build     # bündelt src/ nach com.halvar.obspages.sdPlugin/bin/
npm test          # 19 Prüfungen, inkl. echter Lauf gegen Fake-Stream-Deck + Fake-OBS
npm run pack      # baut dist/com.halvar.obspages.streamDeckPlugin
```

Neue Profile exportieren → als `XLlinks.streamDeckProfile` /
`XLrechts.streamDeckProfile` in `com.halvar.obspages.sdPlugin/` legen →
Versionsnummer in `manifest.json` erhöhen → `npm run pack`.
