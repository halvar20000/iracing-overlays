# OBS-Overlays — Übersicht (Stand 10.10.2026)

In OBS: **Browserquelle → „Lokale Datei" → Datei aus `obs_loaders/`** wählen
(der Loader wartet von selbst, bis das Overlay läuft). Danach Breite/Höhe wie
unten eintragen und **Strg+R** (Transformation zurücksetzen).

Die Größen sind **empfohlene Quellgrößen**. Alle Stream-Overlays haben einen
transparenten Hintergrund — etwas zu viel Platz ist unsichtbar und schadet
nicht. Größer machen ohne Unschärfe: `?zoom=1.5` an die URL hängen und
Breite/Höhe mit demselben Faktor multiplizieren (dann eine URL-Quelle statt
Loader-Datei verwenden).

✓ = gemessen · ≈ = aus dem Layout geschätzt

## Rennen live

| Overlay | Port / Pfad | Loader-Datei | Größe (B × H) |
|---|---|---|---|
| Standings Tower | 5005 `/` | `standings.html` | ✓ 456 × 640 (22 Fahrer; +25 px je Fahrer). Große Felder: URL-Quelle `http://localhost:5005/?rows=25` = Top 24 + Auto der Kamera ≈ 456 × 720 |
| Schnellste Runde (Banner) | 5005 `/fastest` | `fastest.html` | ✓ 620 × 60 |
| Biggest Movers | 5005 `/movers` | `movers.html` | ✓ 536 × 145 |
| Abstandsbalken (Gap Bar) | 5005 `/gapbar` | `gapbar.html` | ✓ frei wählbar × 135 (füllt die Breite) |
| Überhol-Ticker | 5000 `/ticker` | `ticker.html` | ✓ 650 × 56 |
| Driver Card | 5017 | `driver.html` | ✓ 1000 × 130 |
| Livery (Auto der Kamera) | 5006 | `livery.html` | ≈ 800 × 350 |
| Track Map | 5007 | `trackmap.html` | ≈ 1000 × 600 (skaliert mit) |
| Flaggen (weiß / karierte) | 5008 | `flag.html` | ≈ 280 × 200 |
| Neuer Führender (Banner) | 5018 | `leader.html` | ≈ 760 × 220 |
| Catch-Up Battle | 5015 | `catch.html` | ≈ 1000 × 140 |
| Wetter-Leiste | 5016 | `weather.html` | ≈ 900 × 80 |
| Session-Info (Restzeit) | 5011 | `sess.html` | ≈ 300 × 120 |
| LIVE / REPLAY-Badge | 5004 | `live.html` | ≈ 300 × 80 |
| Meisterschaft live (Top 10) | 5010 `/overlay` | `champ.html` | ✓ 540 × 545 (`?top=0` = alle ≈ 540 × 1530) |
| Titelkampf (Duell) | 5010 `/duel` | `duel.html` | ✓ 676 × 125 |
| Driver of the Day (live, vorläufig) | 5013 | `dotd.html` | ✓ 600 × 520 |
| Rennverlauf-Grafik (Logger) | 5009 `/chart/render` | `logger_chart.html` | ✓ 600 × 360 |
| Twitch-Chat + Zuschauerzahl | — (eigenständig) | `twitch_chat.html` | ✓ 420 × 600 |
| YouTube-Livestream (Bild-im-Bild) | 5005 `/youtube` | **keine** — URL-Quelle `http://localhost:5005/youtube` (lokale Datei geht bei YouTube nicht) | ✓ 1280 × 720, Video wählen unter `/youtube/setup` |

## Qualifying

| Overlay | Port / Pfad | Loader-Datei | Größe (B × H) |
|---|---|---|---|
| Quali-Delta (zur Pole) | 5014 | `delta.html` | ≈ 480 × 160 |
| Quali-Delta (zur eigenen Bestzeit) | 5014 `/own` | `delta_own.html` | ≈ 480 × 160 |
| Startaufstellung | 5001 | `grid.html` | ≈ 920 × 1000 |

## Vor dem Rennen / Moderatoren-Bildschirm (Daten aus CLS, deckende Panels)

| Overlay | Port / Pfad | Loader-Datei | Größe (B × H) |
|---|---|---|---|
| Letzte Runde: Rennen 1 → 2 → Combined | 5010 `/lastrace` | `lastrace.html` | ✓ 576 × 600 (+27 px je Fahrer über 20) |
| Tabelle vor der Runde | 5010 `/table` | `table.html` | ✓ 576 × 780 |
| RSVP-Übersicht | 5010 `/rsvp` | `rsvp.html` | ✓ 576 × 170 |
| Saison-Statistik | 5010 `/stats` | `stats.html` | ✓ 776 × 400 |
| Driver of the Day der letzten Runde | 5010 `/lastdotd` | `lastdotd.html` | ✓ 776 × 300 |

## Nach dem Rennen

| Overlay | Port / Pfad | Loader-Datei | Größe (B × H) |
|---|---|---|---|
| Rennergebnis (voll) | 5002 | `results.html` | ≈ 1120 × 1000 |
| Rennergebnis (Lite) | 5003 | `results_lite.html` | ≈ 780 × 900 |

## Bedienseiten (nicht für den Stream)

| Seite | Adresse | Hinweis |
|---|---|---|
| Dashboard (Kameras, Replays, Vorfälle) | `http://localhost:5000` | `dashboard.html` existiert, ist aber deine Regie-Oberfläche, ≈ 1920 × 1080 |
| Race Logger Monitor | `http://localhost:5009` | `logger.html` existiert, Bedienseite |
| Meisterschaft – Liga/Saison wählen | `http://localhost:5010/` | Konfiguration |
| YouTube – Video wählen | `http://localhost:5005/youtube/setup` | Konfiguration |
| Race Control (Stewards) | `http://localhost:8080` | im Browser öffnen, kein OBS |
| Corner Cues (Kurvenhinweise beim Selberfahren) | 5012 | `line.html` existiert; gedacht als Fahrhilfe (`driving_line_window.py`), ≈ 460 × 200 |
