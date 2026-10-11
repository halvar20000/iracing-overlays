# Installation & Updates (Windows)

## Erstinstallation

**Variante A – ZIP:** https://github.com/halvar20000/iracing-overlays/archive/refs/heads/main.zip
herunterladen, entpacken, **`install.bat`** doppelklicken.

**Variante B – ohne ZIP:** PowerShell öffnen und einfügen:

```
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/halvar20000/iracing-overlays/main/install.ps1 | iex"
```

Der Installer
1. sucht Python 3.10+ (mit tkinter) und installiert Python 3.12 per `winget`, falls keins da ist,
2. fragt nach dem Ordner (Vorschlag: der entpackte Ordner bzw. `C:\Users\<du>\iRacing-Overlays`),
3. lädt die aktuelle Version von GitHub und installiert die Python-Pakete (`pip`),
4. legt die Verknüpfung **„iRacing Overlays"** auf dem Desktop und im Startmenü an.

`install.bat` kann jederzeit erneut laufen (Reparatur) – eigene Einstellungen
(Meisterschaft, YouTube, Pro/Am, Logs …) bleiben erhalten.

## Updates

Der Launcher prüft beim Start, ob es auf GitHub etwas Neues gibt. Dann erscheint
oben ein grüner Balken mit den Änderungen und dem Knopf **„Update & restart"**:
laufende Overlays werden gestoppt, die neuen Dateien geladen (bei geänderten
Paketen auch `pip`), der Launcher startet neu und startet dieselben Overlays
wieder. OBS-Quellen verbinden sich von selbst neu.

Klick auf die Versionsanzeige oben (`version … ↻`) prüft erneut.
Ohne GUI: `python updater.py` (prüfen) bzw. `python updater.py --update`.

Ein Ordner, der ein **git-Checkout** ist (Thomas' Unraid-Freigabe), wird nie
überschrieben – der Launcher zeigt dort nur die Version an; Updates per `git pull`.
