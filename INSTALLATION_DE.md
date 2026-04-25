# iRacing Overlays — Installations-Anleitung

> Diese Anleitung führt Dich Schritt für Schritt durch die komplette
> Einrichtung. Du brauchst keine Programmier-Kenntnisse — nur ein
> bisschen Geduld. **Etwa 20 Minuten** insgesamt.

---

## Voraussetzungen

- **Windows 10 oder 11** (iRacing läuft sowieso nur auf Windows)
- Internet-Zugang
- iRacing installiert und einsatzbereit
- Optional: OBS Studio, falls Du die Overlays für Dein Streaming nutzen
  möchtest

---

## Schritt 1 — Python installieren

Python ist die Programmiersprache, in der die Overlays geschrieben sind.
Ohne Python können sie nicht starten.

### 1a. Python herunterladen

1. Öffne im Browser: **<https://www.python.org/downloads/>**
2. Klicke auf den großen gelben Button **„Download Python 3.x.x"**
   (irgendeine Version 3.10 oder neuer ist gut, je neuer desto besser)
3. Speichere die Datei (z.B. `python-3.13.0-amd64.exe`) auf den Desktop

### 1b. Python installieren — DAS ALLERWICHTIGSTE!

Doppelklicke auf die heruntergeladene Datei. Es öffnet sich der
Installer.

> ⚠️ **GANZ WICHTIG**: Bevor Du auf „Install Now" klickst, musst Du
> **UNTEN** im Fenster **beide Häkchen** setzen:
>
> - ☑ **Use admin privileges when installing py.exe**
> - ☑ **Add python.exe to PATH**  ← **DAS IST DER WICHTIGSTE!**
>
> Wenn Du den unteren Haken („Add python.exe to PATH") **vergisst**,
> wird Python zwar installiert, aber Windows wird Python nicht finden
> können, und nichts wird funktionieren. Das ist der Grund Nr. 1, warum
> es bei vielen Leuten nicht klappt!

Wenn Du beide Häkchen gesetzt hast, klicke auf **„Install Now"**. Der
Installer braucht 1–2 Minuten.

Am Ende kommt vielleicht noch der Knopf **„Disable path length limit"** —
**klicke da auch drauf**, das ist auch nützlich. Danach **„Close"**.

### 1c. Falls Du den Haken vergessen hast

Kein Drama — geh nochmal in „Apps & Features" / „Programme deinstallieren",
deinstalliere Python, lade den Installer nochmal herunter, und mach es
richtig. Es ist nur eine Frage von 5 Minuten.

---

## Schritt 2 — Prüfen, ob Python funktioniert

Wir testen jetzt, ob Python wirklich installiert ist und Windows es
findet.

1. Drücke die **Windows-Taste**, tippe `cmd` und drücke Enter. Es
   öffnet sich ein schwarzes Fenster (die „Eingabeaufforderung" /
   „Command Prompt").
2. Tippe in diesem Fenster genau das hier ein und drücke Enter:

   ```
   python --version
   ```

3. Erwartet wird etwas wie:

   ```
   Python 3.13.0
   ```

   ✅ Wenn das so aussieht — **super, Python ist installiert!**

   ❌ Wenn da steht:
   ```
   'python' wird nicht als interner oder externer Befehl ... erkannt
   ```
   …dann hast Du den Haken **„Add python.exe to PATH"** vergessen
   (siehe Schritt 1c). Erst Python richtig neu installieren, dann
   weitermachen.

4. Tippe noch zur Sicherheit:

   ```
   pip --version
   ```

   Erwartet wird etwas wie:
   ```
   pip 24.x.x from C:\...\pip (python 3.13)
   ```

   `pip` ist der „Paket-Manager" von Python und wird gleich gebraucht,
   um die Bibliotheken zu installieren.

---

## Schritt 3 — Das Programm herunterladen

Du hast zwei Möglichkeiten — die einfachere ist die ZIP-Variante.

### 3a. Einfache Variante: ZIP herunterladen

1. Gehe im Browser zu:
   **<https://github.com/halvar20000/iracing-overlays>**
2. Klicke oben rechts auf den grünen Button **„Code"** (mit dem
   Pfeil-Symbol)
3. Wähle ganz unten **„Download ZIP"**
4. Speichere die ZIP-Datei und entpacke sie an einen Ort, den Du
   wiederfindest — zum Beispiel:
   ```
   C:\iRacing-Overlays\
   ```
5. Wichtig: nach dem Entpacken sollte der Ordner direkt z.B. die Datei
   `launch_gui.py` enthalten, **nicht** noch einen Unter-Ordner mit dem
   gleichen Namen. Falls doch, einfach die innere Ordner-Ebene rausziehen.

### 3b. Profi-Variante: mit Git klonen

Nur falls Du Git installiert hast und mit Updates arbeiten willst.
Sonst überspringe diesen Abschnitt.

```
git clone https://github.com/halvar20000/iracing-overlays.git
cd iracing-overlays
```

---

## Schritt 4 — Bibliotheken installieren

Das Programm braucht ein paar fertige Python-Bibliotheken (Flask,
pyirsdk, Pillow, requests). Die installieren wir jetzt mit `pip`.

1. Öffne wieder die **Eingabeaufforderung** (Windows-Taste, `cmd`,
   Enter).
2. Wechsle in den Ordner, in den Du das Programm entpackt hast. Wenn
   Du es z.B. unter `C:\iRacing-Overlays\` hast, tippe:

   ```
   cd C:\iRacing-Overlays
   ```

3. Tippe dann:

   ```
   pip install -r requirements.txt
   ```

   Das lädt die Bibliotheken aus dem Internet und installiert sie. Du
   siehst eine Menge Text durchlaufen — keine Sorge, das ist normal.
   Am Ende sollte `Successfully installed flask-... pyirsdk-... pillow-...`
   erscheinen.

   ⏱️ Dauert je nach Internet-Verbindung 30 Sekunden bis 2 Minuten.

> 💡 **Falls `pip install` mit „Permission denied" fehlschlägt**,
> versuche stattdessen:
>
> ```
> pip install -r requirements.txt --user
> ```
>
> Das installiert die Bibliotheken nur für Deinen Benutzer — kein
> Admin-Rechte nötig.

---

## Schritt 5 — Programm starten

Geschafft! Jetzt kannst Du das Programm starten.

1. Starte iRacing und lade in eine beliebige Session (Test Drive
   reicht).
2. Geh in den Ordner mit den Overlay-Dateien (z.B. `C:\iRacing-Overlays\`).
3. **Doppelklick auf `launch_gui.bat`** — das ist der Programm-Starter.

Es öffnet sich ein dunkles Fenster mit einer Liste aller Overlays.
Klicke auf **„Start All"**, oder einzeln auf jeden Overlay, den Du
brauchst. Auf der grünen Status-Anzeige siehst Du, ob alles läuft.

Mit dem **„Open"**-Knopf neben jedem Overlay öffnest Du die
URL im Browser — diese URLs trägst Du dann in OBS als
**„Browser-Quelle"** ein, falls Du streamen willst.

---

## Häufige Probleme & Lösungen

### „python wird nicht als interner oder externer Befehl erkannt"

**Ursache:** Bei der Python-Installation wurde der Haken bei
„Add python.exe to PATH" vergessen.

**Lösung:** Python deinstallieren („Apps & Features" → Python →
Deinstallieren) und den Installer **noch mal** ausführen — diesmal mit
**beiden Haken** unten gesetzt!

### „pip wird nicht als interner oder externer Befehl erkannt"

**Ursache:** Gleiches Problem — PATH ist nicht gesetzt.

**Alternative Lösung:** Versuche statt `pip` den Befehl `py -m pip`:

```
py -m pip install -r requirements.txt
```

Wenn das funktioniert, kannst Du auch `python` durch `py` ersetzen,
wenn Du das Programm startest:

```
py launch_gui.py
```

### „Microsoft Store öffnet sich beim Eingeben von `python`"

**Ursache:** Windows hat eine eigene „App Installer"-Verknüpfung für
Python, die den Microsoft Store öffnet, statt das echte Python zu
nutzen.

**Lösung:**

1. Drücke `Win + I` (Windows-Einstellungen)
2. Suche oben nach **„App-Aliase"** oder **„Manage app execution
   aliases"**
3. **Deaktiviere** die zwei Einträge `python.exe` und `python3.exe`
   („App-Installer")
4. Mach eine NEUE Eingabeaufforderung auf und probiere `python --version`
   nochmal.

### „pip install" gibt Fehler wie „Connection refused" oder „SSL error"

**Ursache:** Firmen- oder Schul-Internet, das pip blockiert. Oder die
Internet-Verbindung ist gerade unzuverlässig.

**Lösung:** Probiere es nochmal in ein paar Minuten, oder von einem
anderen Netzwerk (z.B. Handy-Hotspot).

### Das Programm startet, aber zeigt nichts an

**Ursache:** iRacing ist nicht in einer aktiven Session (also nur in
der Lobby).

**Lösung:** Mach einen Test Drive auf irgendeiner Strecke. Dann
verbinden sich die Overlays automatisch.

### Beim Start kommt: „ModuleNotFoundError: No module named 'flask'" (oder ähnlich)

**Ursache:** Schritt 4 (`pip install -r requirements.txt`) ist nicht
erfolgreich durchgelaufen, oder Du hast es im falschen Ordner
ausgeführt.

**Lösung:** Geh in den Ordner mit der Datei `requirements.txt` und
führe den Befehl nochmal aus:

```
cd C:\iRacing-Overlays
pip install -r requirements.txt
```

---

## Wenn Du nicht weiterkommst

Mach einen **Screenshot vom Fehler** und schicke ihn an Thomas. Der
Screenshot sollte zeigen:

- Den Befehl, den Du eingegeben hast
- Den ganzen Fehler-Text, den Windows zurückgibt

Damit ist meistens schnell klar, woran's hängt.

Viel Erfolg und gutes Racing! 🏁
