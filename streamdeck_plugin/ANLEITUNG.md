# Stream Deck blättert automatisch mit der OBS-Szene – Anleitung

Mit dem Plugin **„OBS Seitenwechsel“** springt dein Stream Deck von selbst
auf die passende Seite, sobald du in OBS die Szene wechselst. Dabei ist egal,
ob du per Stream Deck, Ulanzi, Maus oder Makro umschaltest. Beispiel: In der
Szene „iRacing“ zeigt das Deck die Kamera-Tasten, im „Moderator“ die
Moderations-Tasten. Jedes Deck kann dabei seine **eigene** Seite bekommen.

Bei Thomas läuft es mit zwei Stream Deck XL.

---

## Bevor du anfängst: Welche Variante passt?

Stream Deck erlaubt einem Plugin nur, auf Profile umzuschalten, die das
Plugin **selbst mitbringt**. Im Plugin stecken im Moment zwei Profile für ein
**Stream Deck XL** (`XLlinks` und `XLrechts`).

- **Variante A – du hast ein oder zwei Stream Deck XL:** Du kannst das Plugin
  direkt nutzen. → weiter mit Schritt 1.
- **Variante B – anderes Deck** (normales Stream Deck, Stream Deck +, Mini,
  Neo …): Dann muss das Plugin einmal mit einem Profil für dieses Deck neu
  gepackt werden. → siehe unten „Variante B“.

---

## Variante A – Schritt für Schritt (Stream Deck XL)

### Schritt 1: Overlays aktualisieren
Im Overlay-Launcher oben auf **„Update & restart“** klicken, falls ein Update
angezeigt wird. Danach liegt in deinem Installationsordner die Datei

`streamdeck_plugin\dist\com.halvar.obspages.streamDeckPlugin`

### Schritt 2: OBS-WebSocket einschalten
1. OBS öffnen → **Werkzeuge → WebSocket-Server-Einstellungen**.
2. Haken bei **„WebSocket-Server aktivieren“**.
3. Port **4455** lassen.
4. **„Authentifizierung aktivieren“** darf aus oder an sein. Wenn an: das
   Passwort über **„Verbindungsinformationen anzeigen“** kopieren, du brauchst
   es in Schritt 5.
5. **OK**.

### Schritt 3: Plugin installieren
1. Doppelklick auf `com.halvar.obspages.streamDeckPlugin` (aus Schritt 1).
2. Die Stream-Deck-App fragt, ob das Plugin installiert werden soll → **Ja**.
3. Sie fragt, ob die Profile **XLlinks** / **XLrechts** installiert werden
   sollen → **Ja**.

### Schritt 4: Taste „OBS-Status“ anlegen
1. In der Stream-Deck-App rechts in der Aktionsliste die Gruppe
   **„OBS Seitenwechsel“** aufklappen.
2. **„OBS-Status“** auf eine freie Taste ziehen – auf irgendein Deck, z. B.
   das kleine Admin-Deck.
3. Die Taste zeigt „OBS aus“ oder die laufende Szene. Drücken = Seiten für
   die laufende Szene neu setzen (z. B. nachdem du von Hand geblättert hast).

### Schritt 5: Einstellungen – alles in einem Fenster
Die Taste aus Schritt 4 in der App **anklicken**. Unten erscheinen die
Einstellungen. Alles wird **sofort gespeichert** und gilt sofort, ohne
Neustart. Die Einstellungen bleiben auch bei Plugin-Updates erhalten.

**OBS:** Grüner Punkt = verbunden, daneben die laufende Szene. Port 4455
lassen. Passwort nur eintragen, wenn du in Schritt 2 eins eingeschaltet hast.

**Decks:** Alle angeschlossenen Decks mit ihrem Namen aus der App (z. B.
„Main“, „Cam“, „Admin“). Bei jedem XL, das mitblättern soll, ein Profil
wählen:
- erstes XL → **Profil XLlinks**
- zweites XL → **Profil XLrechts**
- alle anderen → **nicht umschalten** (normale Decks und das Stream Deck +
  können ohnehin nicht gewählt werden)

Steht dort noch „XLlinks“ / „XLrechts“ mit „(nicht angeschlossen)“, ist das
Thomas' Voreinstellung: auf „nicht umschalten“ stellen, dann verschwinden sie.

**Szene → Seite:** Die Szenen werden **direkt aus OBS** geholt. Pro Szene
und Deck die Seitennummer eintragen. **Leeres Feld = dieses Deck bleibt bei
dieser Szene, wo es ist.** Die gerade laufende Szene ist mit ● markiert.
Beispiel:

| Szene | Main | Cam |
|---|---|---|
| Intro | 1 | |
| Moderator | 2 | 2 |
| iRacing | 3 | 1 |
| Werbung | 4 | |

Kursiv mit ✕ = Szene, die es in OBS nicht gibt (z. B. Thomas' Voreinstellungen
wie „Schedule PCC“) → mit ✕ entfernen.

**Zurücksetzen** (zweimal klicken) löscht deine Einstellungen; dann gelten
wieder die Werte aus `config.json`.

### Schritt 6: Profile mit Seiten füllen
1. In der Stream-Deck-App beim XL das Profil auswählen, das du ihm in
   Schritt 5 gegeben hast (**XLlinks** bzw. **XLrechts**) – das sind die
   Profile des Plugins. **Nur diese Profile werden umgeschaltet.**
2. Es enthält Thomas' Tasten (Kameras, Go Live, OBS-Quellen). Die
   Dashboard-Tasten (TV1, Go Live, Hide UI …) funktionieren bei dir genauso;
   die OBS-Quellen-Tasten zeigen auf Thomas' Quellen – umstellen oder löschen.

   **Deine bisherige Belegung übernehmen:** dein altes Profil öffnen, die
   Tasten markieren (mit gedrückter Maustaste einen Rahmen um mehrere Tasten
   ziehen), **Strg + C**, ins Plugin-Profil wechseln, auf die Zieltaste
   klicken, **Strg + V**. So wird deine jetzige Seite zu einer der neuen
   Seiten, ohne alles neu einzurichten.
3. Seiten anlegen: unten neben den Seitenpunkten auf **„+“**, bis es so viele
   Seiten sind, wie du in Schritt 5 verwendest, und sie belegen.

**Tipp, damit man sich im Stream nicht verläuft:** Was du immer brauchst
(z. B. die Spalte mit den Szenen-Tasten), auf **jeder** Seite an dieselbe
Stelle legen – einmal kopieren, auf jeder Seite einfügen. Nur die übrigen
Spalten wechseln je nach Szene. Das Deck sieht dann immer vertraut aus, zeigt
aber nur das, was gerade gebraucht wird.

### Schritt 7: Testen
In OBS die Szene wechseln – die XL springen auf die eingestellten Seiten.
Oder in den Einstellungen auf **„Jetzt anwenden“** klicken.

---

## Variante B – anderes Deck

Dann wird das Plugin einmal mit einem Profil für dein Deck neu gepackt.
Schick Thomas:

1. **Dein Profil als Datei:** Stream-Deck-App → Profil-Liste → beim Profil
   auf **⋯ / Zahnrad → Exportieren** → `.streamDeckProfile`-Datei.
2. **Welches Deck** (Modell) und wie es in der App heißt.

Du bekommst dann ein eigenes Plugin-Paket und installierst es wie in Variante
A ab Schritt 2. Szenen und Seiten stellst du danach selbst im Fenster ein.

---

## Wenn es nicht klappt

- **Deck springt nicht:** Hat das Deck in den Einstellungen ein Profil (nicht
  „nicht umschalten“)? Steht bei der Szene für dieses Deck eine Seitennummer?
  Ist im Deck das Profil des Plugins aktiv und nicht ein gleichnamiges altes?
- **Roter Punkt bei OBS:** WebSocket-Server an, Port 4455, Passwort richtig?
- **Log-Datei:**
  `%APPDATA%\Elgato\StreamDeck\Plugins\com.halvar.obspages.sdPlugin\logs\`
  Dort steht, ob OBS verbunden ist, welche Decks gefunden wurden und was bei
  jedem Szenenwechsel passiert ist, z. B.
  `scene "iRacing" -> Main: profile XLlinks, page 3`.
  Diese Datei an Thomas schicken.
