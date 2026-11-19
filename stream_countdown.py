"""
Stream countdown ("GLEICH GEHT'S LOS  3 : 11 : 51 : 43") — the target comes
by itself from the stream calendars, nothing to set up per stream:

  * CLS league-manager: every CAS league's round dates (`startsAt` of each
    round in /api/overlay/round-info, all leagues from /api/overlay/leagues),
    moved to the league's STREAM start on that day (STREAM_START: PCC / SFL /
    Combined 19:40, WCT 19:00, IEC 17:00 local time),
  * Twitch: the channel's stream schedule as iCalendar
    (api.twitch.tv/helix/schedule/icalendar?broadcaster_id=… — public, no
    login; the id comes once from decapi.me/twitch/id/<channel>).

`Calendar.events()` = all known events, sorted; the PAGE picks the first one
whose target (start + ?offset= minutes) is still ahead, so the countdown
moves on to the next stream by itself.  Served by the session-info server
(5011): /countdown (page), /countdown/data (JSON).

Page options: ?league=cas-gt3-wct[,cas-iec]  only these leagues
              ?source=cls|twitch               only one calendar
              ?offset=-40   count to 40 min BEFORE the stream start (pre-race)
              ?to=2026-11-17T19:30   fixed target (local time), no calendar
              ?title=…  ?end=…  (text at zero)  ?live=30 (min the end text
              stays before the next event)  ?info=0 (no event line)
              ?days=0 (no TAGE box)  ?zoom=  ?debug=1 / H  ?demo=1
"""

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CLS_URL = "https://league.simracing-hub.com"
TWITCH_CHANNEL = "sim_racing_broadcast"
UA = "iracing-overlays countdown"
REFRESH_S = 600            # calendars change rarely
KEEP_PAST_S = 6 * 3600     # an event stays in the list this long after its start

# When the STREAM starts (local time, Europe/Berlin) on a league's race day -
# the countdown counts to this, not to the race start CLS lists. A league not
# listed here = the CLS start itself. Pre-race page: ?offset=-40 on top.
STREAM_START = {"cas-pccd": "19:40", "cas-sfl-cup": "19:40", "cas-combined-cup": "19:40",
                "cas-gt3-wct": "19:00", "cas-iec": "17:00"}
TZ_NAME = "Europe/Berlin"


def _tz():
    """Europe/Berlin (needs the tzdata package on Windows), else None = the
    PC's own local time (the stream PC is in that zone anyway)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TZ_NAME)
    except Exception:
        return None


def stream_time(race_t, league, starts=None, tz="auto"):
    """Stream start on the race's day: the league's STREAM_START clock time on
    the local date of the race start; no entry = the race start itself."""
    hhmm = (STREAM_START if starts is None else starts).get(league)
    if not hhmm:
        return race_t
    tz = _tz() if tz == "auto" else tz
    try:
        h, m = (int(x) for x in hhmm.split(":"))
        day = datetime.fromtimestamp(race_t, tz) if tz else datetime.fromtimestamp(race_t)
        return day.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
    except Exception:
        return race_t


# Short names on the info line ("CAS GT3 WCT" is long enough as it is).
LEAGUE_SHORT = {"cas-pccd": "CAS PCC", "cas-iec": "CAS IEC", "cas-gt3-wct": "CAS GT3 WCT",
                "cas-sfl-cup": "CAS SFL CUP", "cas-combined-cup": "CAS COMBINED CUP",
                "cas-fun-league": "CAS FUN LEAGUE"}


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _iso(ts):
    """'2026-11-17T18:00:00.000Z' -> epoch seconds, None if unreadable."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


# --------------------------------------------------------------------- CLS
def cls_events(league, round_info):
    """Events of one league from its round-info payload."""
    out = []
    lg = (round_info or {}).get("league") or {}
    slug = lg.get("slug") or league
    name = LEAGUE_SHORT.get(slug) or (lg.get("name") or slug).upper()
    for r in (round_info or {}).get("rounds") or []:
        t = _iso(r.get("startsAt"))
        if t is None or str(r.get("status") or "").upper() in ("COMPLETED", "CANCELLED"):
            continue
        track = r.get("track") or ""
        e = {"t": stream_time(t, slug), "race_t": t, "source": "cls", "league": slug, "league_name": name,
             "round": r.get("number"), "track": track,
             "title": " · ".join(x for x in (name, f"LAUF {r.get('number')}"
                                             if r.get("number") else "", track.upper()) if x)}
        if "streamer" in r:                       # CLS stream calendar (v2.59.1+)
            e["streamer"] = r.get("streamer")
        out.append(e)
    return out


# ------------------------------------------------------------------ Twitch
def _unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text)


def _ical_time(value, params):
    v = value.strip()
    try:
        if v.endswith("Z"):
            return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
        if "TZID" in params or "T" in v:          # local time of the sender: Twitch sends UTC
            dt = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
            if "TZID" in params:
                try:
                    from zoneinfo import ZoneInfo
                    # Twitch writes "TZID=/Europe/Berlin" (leading slash)
                    tzid = params.split("TZID=")[1].split(";")[0].strip('"').lstrip("/")
                    return dt.replace(tzinfo=ZoneInfo(tzid)).timestamp()
                except Exception:
                    pass
            return dt.replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return None
    return None


def ical_events(text, now=None):
    """VEVENTs of an iCalendar text. RRULE weekly series (Twitch's recurring
    segments) are expanded for the next 5 weeks."""
    now = time.time() if now is None else now
    out = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", _unfold(text), re.S):
        fields = {}
        for line in block.strip().splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            name, _, params = key.partition(";")
            fields[name.upper()] = (val, params)
        if "DTSTART" not in fields:
            continue
        t = _ical_time(*fields["DTSTART"])
        if t is None:
            continue
        summary = fields.get("SUMMARY", ("", ""))[0].replace("\\,", ",").replace("\\;", ";").strip()
        cat = fields.get("CATEGORIES", ("", ""))[0].strip()
        starts = [t]
        rrule = fields.get("RRULE", ("", ""))[0].upper()
        if "FREQ=WEEKLY" in rrule:
            m = re.search(r"INTERVAL=(\d+)", rrule)
            step = 7 * 86400 * (int(m.group(1)) if m else 1)
            k = 1
            while starts[-1] < now + 35 * 86400 and k < 400:
                starts.append(t + k * step)
                k += 1
        for s in starts:
            out.append({"t": s, "source": "twitch", "league": "", "league_name": "",
                        "round": None, "track": "", "title": summary or cat or "STREAM"})
    return out


class Calendar:
    """Fetches both calendars every REFRESH_S in a thread; `events()` =
    upcoming events (and those started < KEEP_PAST_S ago), sorted."""

    def __init__(self, channel=TWITCH_CHANNEL, cls_url=CLS_URL, getter=_get):
        self.channel = channel
        self.cls_url = cls_url.rstrip("/")
        self._get = getter
        self._lock = threading.Lock()
        self._events = []
        self._twitch_id = None
        self.errors = {}
        self.fetched = 0.0
        self._wake = threading.Event()

    def fetch(self):
        events, errors = [], {}
        try:
            leagues = json.loads(self._get(self.cls_url + "/api/overlay/leagues")).get("leagues") or []
            for lg in leagues:
                slug = lg.get("slug")
                if not slug:
                    continue
                try:
                    ri = json.loads(self._get(self.cls_url + "/api/overlay/round-info?league="
                                              + urllib.parse.quote(slug)))
                    events += cls_events(slug, ri)
                except Exception as e:
                    errors["cls:" + slug] = str(e)[:120]
        except Exception as e:
            errors["cls"] = str(e)[:120]
        try:
            if not self._twitch_id:
                tid = self._get("https://decapi.me/twitch/id/" + urllib.parse.quote(self.channel)).strip()
                if tid.isdigit():
                    self._twitch_id = tid
                else:
                    raise ValueError("no Twitch id: " + tid[:60])
            events += ical_events(self._get("https://api.twitch.tv/helix/schedule/icalendar?broadcaster_id="
                                            + self._twitch_id))
        except Exception as e:
            errors["twitch"] = str(e)[:120]
        with self._lock:
            # a calendar that failed completely keeps its last good events
            for src in ("cls", "twitch"):
                if src in errors:
                    events += [e for e in self._events if e["source"] == src]
            # our own Twitch segments (twitch_schedule.py) repeat the CLS days
            cls_t = [e["t"] for e in events if e["source"] == "cls"]
            events = [e for e in events if e["source"] != "twitch"
                      or not any(abs(e["t"] - t) < 120 for t in cls_t)]
            self._events = sorted(events, key=lambda e: e["t"])
            self.errors = errors
            self.fetched = time.time()

    def events(self, now=None):
        now = time.time() if now is None else now
        with self._lock:
            return [e for e in self._events if e["t"] > now - KEEP_PAST_S]

    def data(self):
        return {"now": time.time(), "fetched": self.fetched, "errors": dict(self.errors),
                "events": self.events()[:40]}

    def refresh(self):
        self._wake.set()

    def run(self):
        while True:
            try:
                self.fetch()
            except Exception as e:
                self.errors = {"fetch": str(e)[:120]}
            self._wake.wait(REFRESH_S)
            self._wake.clear()


def pick(events, now, offset_s=0, live_s=1800, league=None, source=None):
    """The event the page shows (same rule as the page's JS, for tests and
    /countdown/data?pick=1): first whose target is ahead; an event whose
    target passed less than live_s ago = 'live' (end text)."""
    want = set(x for x in (league or "").split(",") if x)
    for e in events:
        if want and e.get("league") not in want:
            continue
        if source and e.get("source") != source:
            continue
        target = e["t"] + offset_s
        if target > now:
            return dict(e, target=target, live=False)
        if now - target < live_s:
            return dict(e, target=target, live=True)
    return None


COUNTDOWN_HTML = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><title>Countdown</title>
<style>
html, body { margin: 0; background: rgba(0,0,0,0); overflow: hidden; }
body.debug { background: #3a3a46; }
#wrap { display: inline-flex; flex-direction: column; align-items: center; padding: 14px 18px;
        font-family: 'Bebas Neue', 'Oswald', 'Segoe UI', 'Arial Narrow', Impact, sans-serif;
        color: #fff; transform-origin: 0 0; }
#title { font-family: 'Segoe UI', 'Arial Narrow', sans-serif; font-weight: 800; font-size: 15px;
         letter-spacing: .22em; text-transform: uppercase; margin-bottom: 10px;
         text-shadow: 0 1px 3px rgba(0,0,0,.8); }
#row { display: flex; align-items: flex-start; gap: 10px; }
.unit { display: flex; flex-direction: column; align-items: center; }
.box { min-width: 64px; height: 76px; padding: 0 8px; box-sizing: border-box; border-radius: 9px;
       background: linear-gradient(#2e2e36 0%, #26262d 49.5%, #1d1d23 50.5%, #1a1a20 100%);
       box-shadow: 0 4px 10px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.07);
       display: flex; align-items: center; justify-content: center;
       font-family: 'Segoe UI', 'Arial Narrow', sans-serif; font-weight: 800; font-size: 54px;
       font-stretch: condensed; font-variant-numeric: tabular-nums; line-height: 1; }
.lab { margin-top: 7px; font-family: 'Segoe UI', sans-serif; font-size: 11px; font-weight: 700;
       letter-spacing: .2em; color: #b4b4c0; text-shadow: 0 1px 2px rgba(0,0,0,.8); }
.sep { font-family: 'Segoe UI', sans-serif; font-weight: 800; font-size: 34px; color: #e6e6ee;
       height: 76px; display: flex; align-items: center; text-shadow: 0 2px 4px rgba(0,0,0,.7); }
#info { margin-top: 10px; font-family: 'Segoe UI', sans-serif; font-weight: 700; font-size: 13px;
        letter-spacing: .14em; color: #e0e0ea; text-shadow: 0 1px 3px rgba(0,0,0,.9); min-height: 16px; }
#end { display: none; font-family: 'Segoe UI', sans-serif; font-weight: 800; font-size: 46px;
       letter-spacing: .12em; padding: 14px 26px; border-radius: 9px; background: #1d1d23;
       box-shadow: 0 4px 10px rgba(0,0,0,.55); }
body.live #row { display: none; } body.live #end { display: block; }
body.none #wrap { display: none; }
#dbg { display: none; position: fixed; left: 8px; bottom: 6px; font: 12px monospace; color: #cfcfe0;
       white-space: pre; }
body.debug #dbg { display: block; }
</style></head>
<body>
<div id="wrap">
  <div id="title">GLEICH GEHT'S LOS</div>
  <div id="row">
    <div class="unit" id="u-d"><div class="box" id="d">0</div><div class="lab">TAGE</div></div>
    <div class="sep" id="s-d">:</div>
    <div class="unit"><div class="box" id="h">00</div><div class="lab">STD</div></div>
    <div class="sep">:</div>
    <div class="unit"><div class="box" id="m">00</div><div class="lab">MIN</div></div>
    <div class="sep">:</div>
    <div class="unit"><div class="box" id="s">00</div><div class="lab">SEK</div></div>
  </div>
  <div id="end">WIR SIND LIVE</div>
  <div id="info"></div>
</div>
<div id="dbg"></div>
<script>
const Q = new URLSearchParams(location.search);
const opt = (k, d) => Q.has(k) ? Q.get(k) : d;
const OFFSET = parseFloat(opt('offset', '0')) * 60;     // seconds, negative = before the start
const LIVE = parseFloat(opt('live', '30')) * 60;
const LEAGUE = opt('league', ''), SOURCE = opt('source', '');
const SHOW_DAYS = opt('days', '1');                      // 1 = always, auto = only when > 0, 0 = never
const INFO = opt('info', '1') !== '0';
const $ = id => document.getElementById(id);
if (Q.has('title')) $('title').textContent = Q.get('title');
if (Q.has('end')) $('end').textContent = Q.get('end');
if (Q.has('zoom')) $('wrap').style.transform = 'scale(' + parseFloat(Q.get('zoom')) + ')';
if (Q.get('debug') === '1') document.body.classList.add('debug');
document.addEventListener('keydown', e => { if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug'); });

let events = [], skew = 0, lastData = null;
const fixed = Q.has('to') ? new Date(Q.get('to')).getTime() / 1000 : null;   // local time

function pick(now) {
  if (fixed && !isNaN(fixed)) return {t: fixed, title: '', target: fixed + OFFSET};
  const want = LEAGUE ? LEAGUE.split(',') : null;
  for (const e of events) {
    if (want && !want.includes(e.league)) continue;
    if (SOURCE && e.source !== SOURCE) continue;
    const target = e.t + OFFSET;
    if (target > now || now - target < LIVE) return Object.assign({target}, e);
  }
  return null;
}
const pad = n => String(n).padStart(2, '0');
function set(id, v) { const el = $(id); if (el.textContent !== v) el.textContent = v; }
function render() {
  const now = Date.now() / 1000 + skew;
  const e = pick(now);
  document.body.classList.toggle('none', !e);
  if (!e) return dbg(null);
  let left = Math.max(0, Math.floor(e.target - now));
  document.body.classList.toggle('live', e.target <= now);
  const d = Math.floor(left / 86400); left -= d * 86400;
  const h = Math.floor(left / 3600); left -= h * 3600;
  const m = Math.floor(left / 60), s = left - m * 60;
  const showD = SHOW_DAYS === '1' || (SHOW_DAYS === 'auto' && d > 0);
  $('u-d').style.display = $('s-d').style.display = showD ? '' : 'none';
  set('d', String(d)); set('h', pad(showD ? h : h + d * 24)); set('m', pad(m)); set('s', pad(s));
  set('info', INFO ? (e.title || '') : '');
  dbg(e);
}
function dbg(e) {
  if (!document.body.classList.contains('debug')) return;
  $('dbg').textContent = 'events ' + events.length + (lastData && Object.keys(lastData.errors || {}).length
    ? '  errors ' + JSON.stringify(lastData.errors) : '') + '\n' +
    (e ? 'target ' + new Date(e.target * 1000).toLocaleString() + '  [' + (e.source || 'fixed') + ']' : 'no event');
}
async function load() {
  try {
    const r = await fetch('/countdown/data', {cache: 'no-store'});
    const d = await r.json();
    lastData = d; events = d.events || [];
    skew = d.now - Date.now() / 1000;     // server clock wins (stream PC = server PC anyway)
  } catch (err) {}
}
if (Q.get('demo') === '1') {
  const t = Date.now() / 1000 + 3 * 86400 + 11 * 3600 + 51 * 60 + 43;
  events = [{t, source: 'cls', league: 'cas-gt3-wct', title: 'CAS GT3 WCT · LAUF 9 · FUJI INTERNATIONAL SPEEDWAY'}];
} else if (!fixed) {
  load(); setInterval(load, 60000);
}
render(); setInterval(render, 250);
</script>
</body></html>
"""
