"""
Twitch stream schedule filled from the CLS race calendar.

The Twitch dashboard only offers WEEKLY repeating streams; the Helix API can
create single dates (is_recurring false).  This module puts every upcoming
CAS race day (stream_countdown: CLS dates + STREAM_START times) into the
channel's schedule as a one-off segment, moves it when CLS moves the round
and removes it when the round disappears.  Only segments it created itself
are ever changed or deleted (their ids are kept in the state file).

Login = Twitch's Device Code Flow for a PUBLIC app (no secret, no redirect
server): the page shows a code, Thomas confirms it on twitch.tv/activate.
Scope channel:manage:schedule.  Public-client refresh tokens are single-use
and expire after 30 days unused - every sync refreshes, so a weekly run keeps
it alive; otherwise log in again.

State: twitch_schedule.json (client id, tokens, created segments) -
gitignored + updater PROTECTED.  Served by the session-info server 5011:
/twitch (page), /twitch/status, POST /twitch/{client,login,logout,sync,auto}.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import stream_countdown

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "twitch_schedule.json")
SCOPE = "channel:manage:schedule channel:edit:commercial"
AD_SCOPE = "channel:edit:commercial"
AD_LENGTHS = (30, 60, 90, 120, 150, 180)   # what Twitch accepts (seconds)
AD_LEN = 90          # default break: 30 s = 10 min, 90 s = 30 min without pre-rolls
AD_DELAY_S = 60      # auto: this long into a practice / warm-up session
AD_GAP_S = 20 * 60   # auto: never two breaks closer than this
# Thomas's public dev.twitch.tv app "CAS Zeitplan" (a client id is no secret;
# another one can be entered on the page)
DEFAULT_CLIENT_ID = "arjsru8r06eg6iogstizp10byosukn"
API = "https://api.twitch.tv/helix"
ID = "https://id.twitch.tv/oauth2"
TZ = stream_countdown.TZ_NAME
CATEGORY = "iRacing"
HORIZON_DAYS = 42            # how far ahead segments are created
SYNC_EVERY_S = 6 * 3600
# stream length per league (minutes) - Twitch shows it as the end time
DURATION = {"cas-pccd": 150, "cas-sfl-cup": 150, "cas-combined-cup": 150,
            "cas-gt3-wct": 150, "cas-iec": 300}
LEAGUE_TITLE = {"cas-pccd": "CAS Porsche Community Cup", "cas-sfl-cup": "CAS SFL Cup",
                "cas-combined-cup": "CAS Combined Cup", "cas-gt3-wct": "CAS GT3 WCT",
                "cas-iec": "CAS IEC"}


def _http(method, url, headers=None, data=None, form=None, timeout=15):
    """-> (status, parsed JSON or {}). Never raises for an HTTP error."""
    body = None
    headers = dict(headers or {})
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def _rfc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_rfc(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def title_of(e):
    lg = LEAGUE_TITLE.get(e.get("league")) or e.get("league_name") or "CAS"
    parts = [lg]
    if e.get("round"):
        parts.append(f"Lauf {e['round']}")
    if e.get("track"):
        parts.append(e["track"])
    return " · ".join(parts)[:140]


def _ad_len(n):
    """Nearest break length Twitch accepts."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = AD_LEN
    return min(AD_LENGTHS, key=lambda x: abs(x - n))


DEFAULT_STREAMER = "Thomas"   # name in the CLS stream calendar (/admin/stream-calendar)


def has_streamers(events):
    """True when the CLS feed says who streams (round-info `streamer`)."""
    return any("streamer" in e for e in events if e.get("source") == "cls")


def plan(events, now=None, horizon_days=HORIZON_DAYS, streamer=DEFAULT_STREAMER):
    """Wanted segments {key: {start, duration, title}} from the countdown's
    events: CLS race days of the leagues with a stream time, still ahead,
    that the CLS stream calendar gives to `streamer` (None = all)."""
    now = time.time() if now is None else now
    out = {}
    for e in events:
        lg = e.get("league")
        if e.get("source") != "cls" or lg not in stream_countdown.STREAM_START:
            continue
        if not (now < e["t"] < now + horizon_days * 86400):
            continue
        if streamer and str(e.get("streamer") or "").strip().lower() != streamer.strip().lower():
            continue
        key = f"{lg}:{e.get('round')}"
        out[key] = {"start": int(e["t"]), "duration": DURATION.get(lg, 150), "title": title_of(e)}
    return out


class TwitchSchedule:
    def __init__(self, path=STATE_PATH, http=_http, clock=time.time):
        self.path = path
        self.http = http
        self.clock = clock
        self._lock = threading.RLock()
        self.pending = None           # running device login
        self._ad_key, self._ad_since, self._ad_done = None, 0, False
        self.st = self._load()

    # -- state ----------------------------------------------------------
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            d = d if isinstance(d, dict) else {}
        except Exception:
            d = {}
        d.setdefault("client_id", DEFAULT_CLIENT_ID)
        return d

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.st, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)

    def _log(self, text):
        log = self.st.setdefault("log", [])
        log.append({"t": self.clock(), "text": text})
        del log[:-40]

    def status(self):
        with self._lock:
            st = self.st
            p = self.pending
            return {"client_id": st.get("client_id", ""),
                    "logged_in": bool(st.get("refresh_token") or st.get("access_token")),
                    "login": st.get("login", ""), "auto": st.get("auto", True),
                    "streamer": st.get("streamer", DEFAULT_STREAMER),
                    "ads_ok": AD_SCOPE in (st.get("scopes") or []),
                    "ads_auto": st.get("ads_auto", True), "ads_len": st.get("ads_len", AD_LEN),
                    "last_ad": st.get("last_ad"), "last_ad_result": st.get("last_ad_result", ""),
                    "segments": st.get("segments", {}), "last_sync": st.get("last_sync"),
                    "last_result": st.get("last_result", ""), "log": st.get("log", [])[-15:],
                    "pending": ({"user_code": p["user_code"], "uri": p["uri"],
                                 "error": p.get("error", "")} if p else None)}

    def set_client(self, client_id):
        with self._lock:
            client_id = (client_id or "").strip()
            if client_id != self.st.get("client_id"):
                for k in ("access_token", "refresh_token", "expires_at", "user_id", "login"):
                    self.st.pop(k, None)
            self.st["client_id"] = client_id
            self._save()

    def set_streamer(self, name):
        with self._lock:
            self.st["streamer"] = (name or "").strip()[:40] or DEFAULT_STREAMER
            self._save()

    def set_auto(self, on):
        with self._lock:
            self.st["auto"] = bool(on)
            self._save()

    def logout(self):
        with self._lock:
            for k in ("access_token", "refresh_token", "expires_at", "user_id", "login"):
                self.st.pop(k, None)
            self.pending = None
            self._log("Abgemeldet.")
            self._save()

    def set_ads(self, auto=None, length=None):
        with self._lock:
            if auto is not None:
                self.st["ads_auto"] = bool(auto)
            if length is not None:
                self.st["ads_len"] = _ad_len(length)
            self._save()

    # -- ad breaks ----------------------------------------------------------
    def run_ad(self, length=None, why="von Hand"):
        """Start a mid-roll ad break now (stream must be live). -> German line.
        Every 30 s of it keeps pre-rolls off for 10 minutes (Twitch rule)."""
        with self._lock:
            n = _ad_len(length if length is not None else self.st.get("ads_len", AD_LEN))
            if AD_SCOPE not in (self.st.get("scopes") or []):
                msg = "Werbung: erst neu bei Twitch anmelden (Berechtigung für Werbung fehlt)."
            else:
                if not self.st.get("user_id"):
                    self._token()
                code, d = self._api("POST", "/channels/commercial",
                                    data={"broadcaster_id": self.st.get("user_id", ""), "length": n})
                row = (d.get("data") or [{}])[0] if isinstance(d.get("data"), list) else {}
                if code == 200 and row.get("length"):
                    self.st["last_ad"] = self.clock()
                    msg = f"Werbung {row['length']} s gestartet ({why})."
                    if row.get("retry_after"):
                        msg += f" Nächste frühestens in {int(row['retry_after']) // 60} min."
                else:
                    m = str(d.get("message") or row.get("message") or d)
                    if any(x in m.lower() for x in ("not live", "offline", "streaming live")):
                        m = "Kanal ist nicht live"
                    msg = f"Werbung NICHT gestartet ({why}): {code} {m}"
            self.st["last_ad_result"] = msg
            self._log(msg)
            self._save()
            return msg

    def ad_tick(self, session_key, session_type, connected=True):
        """Auto ad break: AD_DELAY_S into each practice / warm-up session,
        at most one per AD_GAP_S. Called every few seconds by 5011.
        Returns the result line when it fired, else None."""
        now = self.clock()
        with self._lock:
            if not connected or not session_key:
                return None
            if session_key != self._ad_key:
                self._ad_key, self._ad_since, self._ad_done = session_key, now, False
            t = str(session_type or "").lower()
            quiet = ("practice" in t or "warm" in t) and "testing" not in t
            if (self._ad_done or not quiet or not self.st.get("ads_auto", True)
                    or AD_SCOPE not in (self.st.get("scopes") or [])
                    or now - self._ad_since < AD_DELAY_S):
                return None
            self._ad_done = True                          # one try per session
            if now - (self.st.get("last_ad") or 0) < AD_GAP_S:
                return None
        return self.run_ad(why=f"automatisch, {session_type}")

    # -- login (device code flow) ----------------------------------------
    def start_login(self, wait=True):
        cid = self.st.get("client_id")
        if not cid:
            raise ValueError("Erst die Client-ID eintragen.")
        code, d = self.http("POST", ID + "/device", form={"client_id": cid, "scopes": SCOPE})
        if code != 200 or "device_code" not in d:
            raise ValueError(f"Twitch lehnt ab ({code}): {d.get('message', d)}")
        with self._lock:
            self.pending = {"device_code": d["device_code"], "user_code": d.get("user_code", ""),
                            "uri": d.get("verification_uri", "https://www.twitch.tv/activate"),
                            "interval": max(2, int(d.get("interval", 5))),
                            "until": self.clock() + int(d.get("expires_in", 1800))}
        if wait:
            threading.Thread(target=self._poll_login, daemon=True).start()
        return self.status()["pending"]

    def poll_login_once(self):
        """One token poll. True = done (logged in or given up)."""
        p = self.pending
        if not p:
            return True
        if self.clock() > p["until"]:
            with self._lock:
                p["error"] = "Code abgelaufen - neu anmelden."
            return True
        code, d = self.http("POST", ID + "/token", form={
            "client_id": self.st.get("client_id", ""), "scopes": SCOPE, "device_code": p["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        if code == 200 and d.get("access_token"):
            with self._lock:
                self._take_token(d)
                self.pending = None
                self._whoami()
                self._log(f"Angemeldet als {self.st.get('login', '?')}.")
                self._save()
            return True
        msg = str(d.get("message", ""))
        if "pending" in msg or "slow_down" in msg:
            return False
        with self._lock:
            p["error"] = f"Anmeldung fehlgeschlagen ({code}): {msg or d}"
        return True

    def _poll_login(self):
        while self.pending and not self.poll_login_once():
            time.sleep(self.pending["interval"] if self.pending else 1)

    def _take_token(self, d):
        self.st["access_token"] = d["access_token"]
        if d.get("refresh_token"):
            self.st["refresh_token"] = d["refresh_token"]
        self.st["expires_at"] = self.clock() + int(d.get("expires_in", 3600))

    def _whoami(self):
        code, d = self.http("GET", ID + "/validate",
                            headers={"Authorization": "OAuth " + self.st.get("access_token", "")})
        if code == 200:
            self.st["user_id"] = d.get("user_id", "")
            self.st["login"] = d.get("login", "")
            self.st["scopes"] = d.get("scopes") or []

    def _token(self):
        """A valid access token (refreshed when < 10 min left), else raises."""
        st = self.st
        if not st.get("access_token") and not st.get("refresh_token"):
            raise ValueError("Nicht bei Twitch angemeldet.")
        if st.get("expires_at", 0) - self.clock() < 600:
            if not st.get("refresh_token"):
                raise ValueError("Anmeldung abgelaufen - neu anmelden.")
            code, d = self.http("POST", ID + "/token", form={
                "client_id": st.get("client_id", ""), "grant_type": "refresh_token",
                "refresh_token": st["refresh_token"]})
            if code != 200 or not d.get("access_token"):
                for k in ("access_token", "refresh_token", "expires_at"):
                    st.pop(k, None)
                self._save()
                raise ValueError(f"Anmeldung abgelaufen ({code}) - bitte neu anmelden.")
            self._take_token(d)
            self._save()
        if not st.get("user_id"):
            self._whoami()
        return st["access_token"]

    def _api(self, method, path, **kw):
        tok = self._token()
        h = {"Authorization": "Bearer " + tok, "Client-Id": self.st.get("client_id", "")}
        return self.http(method, API + path, headers=h, **kw)

    def _category(self):
        if not self.st.get("category_id"):
            code, d = self._api("GET", "/games?name=" + urllib.parse.quote(CATEGORY))
            rows = d.get("data") or []
            if code == 200 and rows:
                self.st["category_id"] = rows[0]["id"]
        return self.st.get("category_id")

    def _existing(self):
        """Segments on Twitch {id: {start, title}} (404 = empty schedule)."""
        out, cursor = {}, None
        for _ in range(10):
            q = "/schedule?first=25&broadcaster_id=" + self.st["user_id"] + (f"&after={cursor}" if cursor else "")
            code, d = self._api("GET", q)
            if code == 404:
                break
            if code != 200:
                raise ValueError(f"Zeitplan lesen: {code} {d.get('message', '')}")
            for s in ((d.get("data") or {}).get("segments") or []):
                out[s["id"]] = {"start": _parse_rfc(s.get("start_time")), "title": s.get("title", "")}
            cursor = (d.get("pagination") or {}).get("cursor")
            if not cursor:
                break
        return out

    # -- sync -------------------------------------------------------------
    def sync(self, events, dry=False):
        """Bring the Twitch schedule in line with plan(events). Returns a
        list of German action lines. dry = only say what would happen."""
        with self._lock:
            now = self.clock()
            who = self.st.get("streamer", DEFAULT_STREAMER)
            if not has_streamers(events):
                # an old CLS without the stream calendar: everything would look
                # like "not my race" and our segments would be deleted
                msg = ("CLS sagt noch nicht, wer welches Rennen streamt "
                       "(Stream-Kalender fehlt im Feed) - nichts geändert.")
                if not dry:
                    self.st["last_result"] = msg
                    self._log(msg)
                    self._save()
                return [msg]
            want = plan(events, now, streamer=who)
            mine = self.st.setdefault("segments", {})
            acts = []
            if not dry:
                self._token()
                cat = self._category()
                there = self._existing()
            else:
                cat, there = None, None
            bid = self.st.get("user_id", "")
            body = lambda w: dict({"start_time": _rfc(w["start"]), "timezone": TZ,
                                   "duration": str(w["duration"]), "title": w["title"]},
                                  **({"category_id": cat} if cat else {}))
            for key, w in sorted(want.items(), key=lambda kv: kv[1]["start"]):
                have = mine.get(key)
                if have and there is not None and have["id"] not in there:
                    have = None                                   # removed on Twitch by hand
                if not have and there:
                    adopt = [i for i, s in there.items()
                             if s["start"] == w["start"] and s["title"] == w["title"]]
                    if adopt:
                        mine[key] = dict(w, id=adopt[0])
                        continue
                when = datetime.fromtimestamp(w["start"]).strftime("%d.%m. %H:%M") if dry else _rfc(w["start"])
                if not have:
                    acts.append(f"NEU {when} {w['title']}")
                    if not dry:
                        b = dict(body(w), is_recurring=False)
                        code, d = self._api("POST", "/schedule/segment?broadcaster_id=" + bid, data=b)
                        segs = ((d.get("data") or {}).get("segments") or [])
                        if code == 200 and segs:
                            # the reply lists the whole schedule - take ours by start time
                            hit = [s for s in segs if _parse_rfc(s.get("start_time")) == w["start"]] or segs
                            mine[key] = dict(w, id=hit[0]["id"])
                        else:
                            acts[-1] = f"FEHLER {code} beim Anlegen: {w['title']} - {d.get('message', '')}"
                elif (have["start"], have["title"], have.get("duration")) != (w["start"], w["title"], w["duration"]):
                    acts.append(f"GEÄNDERT {when} {w['title']}")
                    if not dry:
                        code, d = self._api("PATCH", f"/schedule/segment?broadcaster_id={bid}&id="
                                            + urllib.parse.quote(have["id"]), data=body(w))
                        if code == 200:
                            mine[key] = dict(w, id=have["id"])
                        else:
                            acts[-1] = f"FEHLER {code} beim Ändern: {w['title']} - {d.get('message', '')}"
            for key in list(mine):
                if key in want:
                    continue
                seg = mine[key]
                if seg["start"] <= now:                           # over - just forget it
                    mine.pop(key)
                    continue
                if seg["start"] > now + HORIZON_DAYS * 86400:
                    continue
                acts.append(f"ENTFERNT {seg['title']}")
                if not dry:
                    code, d = self._api("DELETE", f"/schedule/segment?broadcaster_id={bid}&id="
                                        + urllib.parse.quote(seg["id"]))
                    if code in (200, 204, 404):
                        mine.pop(key)
                    else:
                        acts[-1] = f"FEHLER {code} beim Entfernen: {seg['title']}"
            if not dry:
                self.st["last_sync"] = now
                self.st["last_result"] = "; ".join(acts) or "Alles aktuell."
                for a in acts:
                    self._log(a)
                self._save()
            return acts

    def run(self, calendar):
        """Background: sync every SYNC_EVERY_S while logged in and auto on
        (first run 2 min after start so the calendar has been fetched)."""
        time.sleep(120)
        while True:
            try:
                if self.st.get("auto", True) and self.st.get("refresh_token") and calendar.events():
                    self.sync(calendar.events())
            except Exception as e:
                with self._lock:
                    self.st["last_result"] = "Fehler: " + str(e)[:200]
                    self._log(self.st["last_result"])
                    try:
                        self._save()
                    except Exception:
                        pass
            time.sleep(SYNC_EVERY_S)


PAGE_HTML = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><title>Twitch-Zeitplan</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root { --bg:#14141a; --panel:#1e1e26; --text:#e8e8f0; --mut:#9a9aaa; --acc:#9146ff; --ok:#3ecf6e; --err:#ff5c5c; }
body { margin:0; background:var(--bg); color:var(--text); font:15px 'Segoe UI', system-ui, sans-serif; }
main { max-width: 820px; margin: 0 auto; padding: 18px 16px 40px; }
h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 16px; margin: 0 0 10px; }
.sub { color: var(--mut); margin-bottom: 18px; }
section { background: var(--panel); border-radius: 10px; padding: 14px 16px; margin-bottom: 14px; }
ol { margin: 0; padding-left: 20px; line-height: 1.6; } code { background:#2a2a35; padding:1px 5px; border-radius:4px; }
input[type=text] { width: 100%; max-width: 380px; box-sizing: border-box; padding: 8px; border-radius: 6px;
  border: 1px solid #3a3a48; background: #121218; color: var(--text); font: inherit; }
button { padding: 8px 14px; border: 0; border-radius: 6px; background: var(--acc); color: #fff; font: inherit;
  font-weight: 600; cursor: pointer; margin: 6px 6px 0 0; } button.sec { background: #34343f; }
.code { font-size: 30px; font-weight: 800; letter-spacing: .15em; margin: 8px 0; }
.ok { color: var(--ok); } .err { color: var(--err); } .mut { color: var(--mut); }
table { width: 100%; border-collapse: collapse; } td { padding: 5px 4px; border-top: 1px solid #2c2c38; vertical-align: top; }
pre { white-space: pre-wrap; margin: 0; font: 13px ui-monospace, monospace; color: #cfcfe0; }
a { color: #b892ff; }
</style></head><body><main>
<h1>Twitch-Zeitplan aus dem CLS</h1>
<div class="sub">Trägt jeden CAS-Renntag, den du laut CLS-Stream-Kalender streamst (Streamstart PCC / SFL / CC 19:40,
WCT 19:00, IEC 17:00), als einzelnen Termin in den Twitch-Zeitplan ein - die nächsten 6 Wochen, alle 6 Stunden
abgeglichen, solange 5011 läuft. Rennen eines anderen Streamers werden wieder entfernt.</div>

<section id="s-app"><h2>1. Twitch-App (einmalig)</h2>
<ol>
<li><a href="https://dev.twitch.tv/console/apps/create" target="_blank">dev.twitch.tv/console/apps/create</a> öffnen (mit deinem Twitch-Konto anmelden, ggf. Zwei-Faktor einschalten).</li>
<li>Name: z. B. <code>CAS Zeitplan</code> · OAuth-Redirect-URL: <code>http://localhost</code> · Kategorie: <code>Application Integration</code> · Client-Typ: <b>Öffentlich</b>.</li>
<li>Erstellen, dann „Verwalten“ und die <b>Client-ID</b> kopieren (kein Geheimnis nötig).</li>
</ol>
<p><input type="text" id="cid" placeholder="Client-ID"> <button onclick="saveClient()">Speichern</button></p>
</section>

<section><h2>2. Bei Twitch anmelden</h2>
<div id="login"></div></section>

<section><h2>3. Abgleich</h2>
<p>Nur Rennen von <input type="text" id="who" style="width:140px"> <button class="sec" onclick="post('streamer', {name: $('who').value})">Speichern</button>
<span class="mut">(Name wie im CLS-Stream-Kalender)</span></p>
<label><input type="checkbox" id="auto" onchange="post('auto', {on: this.checked})"> automatisch (alle 6 Stunden)</label><br>
<button class="sec" onclick="sync(1)">Vorschau</button><button onclick="sync(0)">Jetzt abgleichen</button>
<div id="res" style="margin-top:10px"></div></section>

<section><h2>4. Werbung</h2>
<div class="sub">Startet eine Werbepause (Stream muss live sein). Jede 30 s Werbung = 10 min ohne Pre-Roll für neue
Zuschauer, 90 s = 30 min. Stream Deck: Website-Aktion „im Hintergrund“ auf
<code>http://localhost:5011/twitch/ad</code> (oder <code>?len=60</code>).</div>
<div id="adwarn"></div>
<p>Länge <select id="adlen" onchange="post('ads', {len: +this.value})">
<option>30</option><option>60</option><option>90</option><option>120</option><option>150</option><option>180</option></select> s
<button onclick="post('ad', {len: +$('adlen').value}).then(d => d.result && alert(d.result))">Werbung jetzt</button></p>
<label><input type="checkbox" id="adauto" onchange="post('ads', {auto: this.checked})"> automatisch 1 min nach Beginn
von Training / Warm-up in iRacing (höchstens alle 20 min)</label>
<div id="adres" class="mut" style="margin-top:6px"></div></section>

<section><h2>Eingetragene Termine</h2><table id="segs"></table></section>
<section><h2>Protokoll</h2><pre id="log"></pre></section>
</main>
<script>
const $ = id => document.getElementById(id);
const fmt = t => new Date(t * 1000).toLocaleString('de-DE', {weekday:'short', day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});
const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'})[c]);
async function post(what, body) {
  const r = await fetch('/twitch/' + what, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                            body: JSON.stringify(body || {})});
  const d = await r.json(); if (!r.ok || d.error) alert(d.error || r.status); load(); return d;
}
function saveClient() { post('client', {client_id: $('cid').value}); }
async function sync(dry) {
  $('res').innerHTML = '<span class="mut">läuft …</span>';
  const d = await post('sync', {dry: !!dry});
  $('res').innerHTML = d.error ? '<span class="err">' + esc(d.error) + '</span>'
    : (dry ? '<b>Vorschau:</b><br>' : '<b>Erledigt:</b><br>') + (d.actions.length ? d.actions.map(esc).join('<br>') : 'Alles aktuell.');
}
let typing = false;
for (const id of ['cid', 'who']) { $(id).addEventListener('focus', () => typing = true); $(id).addEventListener('blur', () => typing = false); }
async function load() {
  const s = await (await fetch('/twitch/status', {cache: 'no-store'})).json();
  if (!typing) { $('cid').value = s.client_id || ''; $('who').value = s.streamer || ''; }
  $('auto').checked = !!s.auto;
  $('adauto').checked = !!s.ads_auto; if (document.activeElement !== $('adlen')) $('adlen').value = s.ads_len;
  $('adwarn').innerHTML = s.logged_in && !s.ads_ok ? '<p class="err">Für Werbung einmal abmelden und neu anmelden (neue Berechtigung).</p>' : '';
  $('adres').textContent = s.last_ad_result ? (s.last_ad ? 'Letzte Werbung ' + fmt(s.last_ad) + ' · ' : '') + s.last_ad_result : '';
  let h;
  if (!s.client_id) h = '<span class="mut">Erst die Client-ID speichern.</span>';
  else if (s.pending && !s.pending.error) h = 'Öffne <a href="' + s.pending.uri + '" target="_blank">' + esc(s.pending.uri) +
     '</a> und gib diesen Code ein:<div class="code">' + esc(s.pending.user_code) + '</div><span class="mut">Wartet auf die Bestätigung …</span>';
  else if (s.logged_in) h = '<span class="ok">Angemeldet als <b>' + esc(s.login) + '</b></span> <button class="sec" onclick="post(\'logout\')">Abmelden</button>';
  else h = (s.pending && s.pending.error ? '<div class="err">' + esc(s.pending.error) + '</div>' : '') +
     '<button onclick="post(\'login\')">Bei Twitch anmelden</button>';
  $('login').innerHTML = h;
  const segs = Object.values(s.segments || {}).sort((a, b) => a.start - b.start);
  $('segs').innerHTML = segs.length ? segs.map(g => '<tr><td>' + fmt(g.start) + '</td><td>' + esc(g.title) + '</td><td class="mut">' +
     g.duration + ' min</td></tr>').join('') : '<tr><td class="mut">noch keine</td></tr>';
  $('log').textContent = (s.last_sync ? 'Letzter Abgleich ' + fmt(s.last_sync) + ': ' + (s.last_result || '') + '\n\n' : '') +
     (s.log || []).slice().reverse().map(l => fmt(l.t) + '  ' + l.text).join('\n');
}
load(); setInterval(load, 3000);
</script></body></html>
"""
