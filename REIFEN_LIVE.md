# Reifenverschleiß nach dem Boxenstopp (live)

Zwei OBS-Overlays zeigen nach einem Boxenstopp, wie viel Profil die Reifen
eines Fahrers noch hatten und ob er auf neuen oder gebrauchten Reifen
weiterfährt:

| Overlay | Quelle | Was |
|---|---|---|
| Karte | `obs_loaders/pittires.html` (5005 `/pittires`) | erscheint ~15 s nach jedem Stopp: Fahrer, Position, Standzeit, alle vier Reifen mit Profil, „NEW 4 TIRES“ / „USED FRONTS“ / „NO TIRE CHANGE“ |
| Liste | `obs_loaders/pittires_list.html` (5005 `/pittires/list`) | alle Stopps des Rennens, neueste oben |

## Woher die Daten kommen

iRacing zeigt den Reifenverschleiß **nur dem Fahrer selbst**, nicht den
Zuschauern. Der Stream-PC kann ihn also nicht lesen. Deshalb gilt:

1. Der Fahrer lässt **RaceLogger.exe (ab v1.4.1)** mit seinem Liga-Schlüssel
   laufen.
2. Wenn er die Boxengasse verlässt, schickt der Logger diesen einen Stopp
   sofort an die Liga-Seite (CLS).
3. Das Overlay auf dem Stream-PC holt ihn dort ab. Der Stopp erscheint etwa
   5 Sekunden nach der Ausfahrt.

**Wer den Logger nicht laufen hat, erscheint nicht.** Das Overlay erfindet
nichts. Zuordnung: Stopps kommen nur aus genau diesem Rennen (iRacing
SubSessionID), Fahrer über die iRacing-Kundennummer.

## Für die Fahrer

- Die neue RaceLogger.exe herunterladen (Liga-Seite → Race Logger).
- Auf <http://localhost:5009/league> ist der Haken **„Send my pit stops live“**
  von Anfang an gesetzt. Darunter steht, ob der letzte Stopp angekommen ist.
- Gesendet wird nur der **eigene** Stopp mit Profiltiefe, Standzeit und dem
  bestellten Service, sonst nichts.

## Lesen der Karte

- **Prozentzahl** = Profil, das beim Hereinkommen noch übrig war (Mittel aus
  innen / Mitte / außen). Die drei Balken zeigen die Streifen einzeln: Ungleich
  hohe Balken bedeuten Sturz oder Luftdruck.
- **Farbe:** grün ≥ 85 %, gelbgrün ≥ 70 %, gelb ≥ 55 %, orange ≥ 40 %, rot
  darunter.
- **→ NEW** bedeutet, ein unbenutzter Satz ist drauf. **→ 81 % set** bedeutet,
  ein gebrauchter Satz wurde montiert. **kept** bedeutet, dieser Reifen blieb
  drauf.

## Optionen

`?secs=15` (Anzeigedauer), `?last=1` (immer den letzten Stopp zeigen, zum
Einrichten), `?top=12` (Liste), `?zoom=1.5`, `?debug=1` bzw. Taste H (dunkler
Hintergrund und Statuszeile mit Session-ID, Anzahl Stopps, Fehler),
`?demo=1` (Beispieldaten).

## Grenzen

- Nur Fahrer mit Logger und Schlüssel erscheinen.
- iRacing aktualisiert das Profil nur, solange das Auto in der Box steht. Gibt
  iRacing während eines Stopps nur einen Messwert heraus, ist ein Wechsel nicht
  erkennbar. Dann steht kein Etikett da, außer der Fahrer hat keine Reifen
  bestellt: Dann heißt es „NO TIRE CHANGE“.
- Die Liga-Seite hält die Live-Stopps nur im Speicher, 6 Stunden lang. Ein
  Update der Liga-Seite während des Rennens löscht die bisherigen Live-Stopps.
  Das vollständige Log kommt nach dem Rennen trotzdem an.
- Die Namen der iRacing-Variablen stammen aus der SDK-Doku. Echt geprüft ist
  das erst nach dem ersten Stopp mit der neuen .exe.
