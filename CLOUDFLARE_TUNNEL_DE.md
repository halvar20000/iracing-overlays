# Live-Charts ins Internet teilen — Anleitung mit Cloudflare Tunnel

> Mit dieser Anleitung kannst Du die Live-Charts und die Live-Standings
> Deines iRacing-Stream-Setups öffentlich freigeben — z.B. um sie in
> Twitch-Chat oder Discord zu posten. Zuschauer können die Seite öffnen
> und sich selbst aussuchen, welche Fahrer sie sehen wollen, **ohne
> Deinen lokalen OBS-Stream zu beeinflussen**.

---

## Was wir hier machen

1. Wir installieren **Cloudflare Tunnel** (kostenlos, sicher) auf
   Deinem Sim-PC.
2. Cloudflare gibt uns eine öffentliche URL, die nur die `/share/*`
   Seiten Deines Race Loggers freigibt — alles andere (Operator-Panel,
   Log-Downloads, OBS-Steuerung) bleibt geschützt.
3. Du teilst die URL mit Deinem Twitch-Chat / Discord.
4. **Sicherheit doppelt abgesichert:** auch wenn Cloudflare
   versehentlich mehr durchlässt, blockiert der Race Logger selbst alle
   nicht-`/share/*`-Pfade von außen.

---

## Voraussetzungen

- Race Logger läuft bereits auf Port 5009 (über `launch_gui.py` oder
  direkt mit `python iracing_race_logger.py`)
- Windows 10 oder 11
- Internet-Verbindung (klar)

---

## Schritt 1 — `cloudflared` herunterladen

1. Browser öffnen: **<https://github.com/cloudflare/cloudflared/releases/latest>**
2. Suche nach der Datei mit dem Namen
   **`cloudflared-windows-amd64.exe`** und lade sie herunter.
3. Verschiebe die heruntergeladene Datei in einen Ordner, den Du
   wiederfindest, z.B. `C:\Tools\cloudflared.exe`. Du kannst sie dann
   einfach mit dem vollen Pfad aufrufen.

> 💡 **Alternative:** wenn Du Chocolatey oder winget kennst, geht auch:
> ```
> winget install --id Cloudflare.cloudflared
> ```

---

## Schritt 2 — Quick Tunnel starten (für eine einzelne Session)

Das ist der **einfachste Weg** und reicht für die meisten Stream-Tage.
Du bekommst eine zufällige URL die nur so lange lebt, wie Du den
Befehl laufen lässt.

1. Eingabeaufforderung (`cmd`) öffnen.
2. Tippe (Pfad anpassen, falls Du `cloudflared.exe` woanders gespeichert
   hast):

   ```
   C:\Tools\cloudflared.exe tunnel --url http://localhost:5009
   ```

3. Nach 5-10 Sekunden bekommst Du eine Ausgabe wie:

   ```
   +--------------------------------------------------------------------------+
   |  Your quick Tunnel has been created! Visit it at (it may take some time) |
   |  to be reachable):                                                       |
   |  https://random-name-here.trycloudflare.com                              |
   +--------------------------------------------------------------------------+
   ```

4. **Diese URL ist Deine öffentliche Adresse.** Sie funktioniert nur
   so lange, wie das `cloudflared`-Fenster offen bleibt.

5. URLs zum Teilen mit Deinen Zuschauern:

   - **Live-Chart (Zuschauer wählen Fahrer selbst):**
     ```
     https://random-name-here.trycloudflare.com/share/chart
     ```

   - **Live-Standings:**
     ```
     https://random-name-here.trycloudflare.com/share/standings
     ```

   - **Vorausgewählte Fahrer im Chart** (sehr nützlich um
     direkt mit ausgewählten Fahrern zu starten):
     ```
     https://random-name-here.trycloudflare.com/share/chart?drivers=11,23,45&type=laptime
     ```
     Optionen für `type=`: `laptime`, `position`, `gap`.

6. **Wenn der Stream vorbei ist**, einfach das `cloudflared`-Fenster
   schließen mit Strg+C oder mit dem X. Die URL ist dann sofort tot.

---

## Schritt 3 (optional) — Stabile URL mit eigener Domain

Wenn Du eine **gleichbleibende URL** willst (statt jedes Mal eine neue
zufällige), brauchst Du:

- Ein kostenloses Cloudflare-Konto: <https://dash.cloudflare.com/sign-up>
- Eine eigene Domain (z.B. `simracing-hub.com` — die hast Du ja
  sowieso!) bei Cloudflare verwaltet

Dann:

1. `cloudflared tunnel login` — öffnet Deinen Browser, Du loggst Dich
   bei Cloudflare ein, autorisierst.
2. `cloudflared tunnel create iracing-stream` — erzeugt einen neuen
   Tunnel.
3. Erstelle eine Konfigurations-Datei `~/.cloudflared/config.yml`
   (typischerweise `C:\Users\<Du>\.cloudflared\config.yml`):

   ```yaml
   tunnel: iracing-stream
   credentials-file: C:\Users\<Du>\.cloudflared\<UUID>.json

   ingress:
     # Nur die /share/* Pfade öffentlich freigeben
     - hostname: live.simracing-hub.com
       path: /share/.*
       service: http://localhost:5009
     # Alles andere abblocken
     - service: http_status:404
   ```

4. DNS-Eintrag erstellen:
   ```
   cloudflared tunnel route dns iracing-stream live.simracing-hub.com
   ```

5. Tunnel starten:
   ```
   cloudflared tunnel run iracing-stream
   ```

6. Jetzt ist die Live-Chart-Seite immer unter
   <https://live.simracing-hub.com/share/chart> erreichbar — solange
   der Tunnel läuft.

> 💡 **Tipp:** Du kannst den Tunnel auch als Windows-Dienst
> installieren, damit er automatisch startet:
> `cloudflared service install`

---

## Was passiert wenn jemand versucht, die anderen Seiten zu öffnen?

Der Race Logger erkennt automatisch wenn ein Request über Cloudflare
kommt (am `Cf-Ray`-Header), und blockiert dann alles, was nicht mit
`/share/` beginnt.

Wenn ein Zuschauer also versucht, `https://...trycloudflare.com/log`
oder `https://...trycloudflare.com/` (Operator-Panel) zu öffnen,
bekommt er eine **404-Fehlermeldung**.

Du selbst auf Deinem Sim-PC kannst weiter `http://localhost:5009/`
benutzen wie immer — der lokale Zugriff hat keinen `Cf-Ray`-Header und
ist nicht eingeschränkt.

---

## Troubleshooting

### "cloudflared wird nicht erkannt"

Du musst den vollen Pfad zur `.exe` angeben (z.B. `C:\Tools\cloudflared.exe`),
oder den Ordner zur PATH-Umgebungsvariable hinzufügen.

### Die URL gibt "Bad Gateway" oder "502" zurück

Der Race Logger läuft nicht oder läuft auf einem anderen Port. Prüfe
mit `python iracing_race_logger.py` direkt im Terminal, dass er auf
Port 5009 läuft.

### Die URL funktioniert lokal aber nicht für meine Zuschauer

- Stelle sicher, dass Dein `cloudflared`-Fenster offen geblieben ist
  (Quick Tunnels überleben keine PC-Neustarts).
- Prüfe die URL nochmal — Quick-Tunnel-URLs sind lang und Sonderzeichen
  enthalten manchmal Zeichen die per WhatsApp/Discord verschluckt
  werden.

### Ich will die URL nicht für die Welt teilen

Cloudflare Tunnel ist von Natur aus öffentlich. Wenn Du nur einer
Person Zugriff geben willst (z.B. Co-Moderator), nutze stattdessen
**Tailscale** — das ist ein VPN, das nur Personen Zugriff gibt, die
auf Deinem privaten "tailnet" eingeladen sind. Frag Thomas wenn das
besser passt.

---

## Sicherheitshinweise

- **Quick Tunnels haben keine Authentifizierung.** Jeder mit der URL
  kann zuschauen.
- Da nur `/share/*` freigegeben ist, kann **niemand etwas an Deinem
  Setup ändern** — die OBS-Steuerung, Race-Logger-Konfiguration und
  Datei-Downloads sind alle blockiert.
- Live-Daten sind während des Rennens öffentlich. Nach dem Rennen
  sind Daten nicht mehr verfügbar (sobald der Race Logger das nächste
  Rennen lädt, sind die alten Daten weg — sie sind nur in der
  JSONL-Datei lokal gespeichert).

Viel Erfolg und gutes Streaming! 🏁
