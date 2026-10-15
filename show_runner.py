"""
Show runner ("Sendeablauf") — the pre-race part of the AI broadcast
(Thomas, 2026-10-15). Stream starts 19:30 on the Intro scene (song + slide
show, by hand), then practice by hand; at `start_at` (19:40) the AI takes
over:

  1. OBS → Moderator scene (the VTube Studio avatar sits there)
  2. moderation, segment by segment, each spoken by the voice
     (race_voice.py via the commentator's show lines) while OBS shows what
     it is about:
        intro      — welcome, league, round, track, format
        last_race  — results of the last round   (source "Letztes Rennen" on)
        standings  — championship table           (source "Tabelle" on)
        track      — background info on the track while the track video
                     (OBS media source) plays
        outro      — hand-over to qualifying
  3. OBS → iRacing scene, REGIE on — it follows qualifying and then the race
     on its own (race_director.py / quali_director.py).

The script is written by Claude from the facts (CLS round info + standings
from the championship server on 5010, iRacing track + weather, a prepared
notes file per track in `show_notes/` if there is one) — or from templates
without an API key. It is written ahead (`prepare()`), so it can be read on
the /show page before it is spoken.

Start: automatically at `start_at` when OBS is STREAMING (so a dashboard left
running on another evening does nothing), or by hand / Stream Deck
(/streamdeck/show_start). /show/next ends the current segment, /show/stop
aborts. Config: show_config.json (gitignored, updater-protected).

Pure logic with injected OBS / speaker / clock; tested by test_show_runner.py.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "show_config.json"
SCRIPT_FILE = HERE / "show_script.json"   # the last script (incl. hand edits), survives a restart
EDIT_KEEP_S = 12 * 3600                   # an edited script is never rewritten automatically within this
DEFAULT_CONFIG = {
    "obs": {"host": "127.0.0.1", "port": 4455, "password": ""},
    "scenes": {"moderator": "Moderator", "iracing": "iRacing"},
    "sources": {                      # in the Moderator scene
        "last_race": "Letztes Rennen",
        "standings": "Tabelle",
        "track_video": "Streckenvideo",
        # where the track is (5007 /location, loader location.html): shown while
        # the track is talked about, before the video (optional)
        "track_location": "Streckenort",
        # Pro/Am seasons: one source per class (5010 /lastrace/pro, /lastrace/am,
        # /table/pro, /table/am). Missing in OBS -> the plain source above,
        # pinned to the class.
        "last_race_pro": "Letztes Rennen PRO",
        "last_race_am": "Letztes Rennen AM",
        "standings_pro": "Tabelle PRO",
        "standings_am": "Tabelle AM",
    },
    "segments": ["intro", "last_race", "standings", "schedule", "track", "outro"],
    # season schedule picture per league (CLS slug -> OBS source in the
    # Moderator scene); a league without one skips the schedule segment
    "schedule_sources": {"cas-gt3-wct": "WCT"},
    "auto_start": True,
    "start_at": "19:40",              # local time
    "start_window_min": 20,           # auto start only within this many minutes after start_at
    "only_while_streaming": True,     # auto start only while OBS streams
    "director_after": True,           # REGIE on after the hand-over
    "video_after_speech": True,       # track: speak first, then play the video (False = talk over it)
    "model": "claude-sonnet-5-5",
    "host_name": "",                  # name the AI host introduces itself with ("" = none)
    "notes_dir": "show_notes",
    "champ_url": "http://127.0.0.1:5010/api/roundinfo",
}
SEGMENT_SOURCES = {"last_race": "last_race", "standings": "standings",
                   "last_race_pro": "last_race_pro", "last_race_am": "last_race_am",
                   "standings_pro": "standings_pro", "standings_am": "standings_am",
                   "schedule": "schedule"}
CLASS_SOURCE_FALLBACK = {"last_race_pro": "last_race", "last_race_am": "last_race",
                         "standings_pro": "standings", "standings_am": "standings"}
# Pro/Am seasons (WCT GT3): results and table are talked about per class, and
# the championship pages (5010) are pinned to that class while it is spoken.
SEGMENT_CLASS = {"last_race_pro": "pro", "last_race_am": "am",
                 "standings_pro": "pro", "standings_am": "am"}
PROAM_SPLIT = {"last_race": ["last_race_pro", "last_race_am"],
               "standings": ["standings_pro", "standings_am"]}
WORDS_PER_S = 2.4                     # edge-tts at +10 %: ~2.4 German words / second
DEADLINE_PAD_S = 15.0                 # a segment moves on this long after its speech should have ended


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        user = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


# -- facts ---------------------------------------------------------------------
def _ms(ms) -> str | None:
    if not ms:
        return None
    s = ms / 1000.0
    m, s = divmod(s, 60)
    return f"{int(m)}:{s:06.3f}".replace(".", ",")


def fetch_roundinfo(url: str, timeout: float = 5.0, tries: int = 6, wait_s: float = 1.5) -> dict | None:
    """CLS round info via the championship overlay. While it is switching to
    the series it just detected on the grid (or still loading), wait a few
    seconds rather than writing the script from no / the old league's data."""
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception:
            return None
        if d.get("ok"):
            return d
        if i + 1 < tries:
            time.sleep(wait_s)
    return None


def _norm(name: str) -> str:
    return " ".join(str(name).split()).casefold()


def match_name(want: str, have) -> str | None:
    """The OBS source in `have` that is `want`, ignoring case and spacing."""
    if want in have:
        return want
    w = _norm(want)
    return next((h for h in have if h and _norm(h) == w), None)


def pin_class(cfg: dict, cls: str | None, timeout: float = 2.0) -> None:
    """Pin the championship pages (/lastrace, /table on 5010) to a Pro/Am
    class while the moderation talks about it; None releases them."""
    import urllib.parse
    base = (cfg.get("champ_url") or DEFAULT_CONFIG["champ_url"]).split("/api/")[0]
    q = urllib.parse.urlencode({"cls": cls or ""})
    with urllib.request.urlopen(f"{base}/api/prerace_pin?{q}", timeout=timeout) as r:
        r.read()


_GENERIC = {"circuit", "international", "internacional", "speedway", "raceway", "motor", "motorsport",
            "park", "autodromo", "autódromo", "course", "road", "grand", "prix", "track", "full", "resort",
            "world", "shell", "power", "national", "gp"}


def _words(name: str) -> set:
    """Distinctive words of a track name ("Mobility Resort Motegi" -> motegi,
    mobility) — not the ones half the calendar shares (circuit, speedway …)."""
    return {w for w in re.findall(r"[a-zäöüßó]+", (name or "").lower()) if len(w) >= 4 and w not in _GENERIC}


def replay_round(roundinfo: dict | None, track_name: str, notes_dir: Path | None = None) -> int | None:
    """A replay of an EARLIER round is running: the iRacing track is not the
    next round's but matches a completed one -> that round's number (the show
    then uses the season as it stood before it). Tracks per round from CLS,
    else from show_notes/schedule-<league>.md by date. None = live / unknown."""
    info = (roundinfo or {}).get("info") or {}
    want = _words(track_name)
    if not want:
        return None
    nxt = info.get("nextRound") or {}
    if _words(nxt.get("track") or "") & want:
        return None                                   # today's track: live
    sched = schedule_tracks(notes_dir or HERE / "show_notes", (info.get("league") or {}).get("slug"))
    hits = []
    for r in info.get("rounds") or []:
        if r.get("status") != "COMPLETED":
            continue
        track = r.get("track") or sched.get(_date(r.get("startsAt")) or "", "")
        if _words(track) & want:
            hits.append(r.get("number"))
    return max(hits) if hits else None


def find_notes(notes_dir: Path, track: str, track_cfg: str = "") -> str | None:
    """Prepared notes for the track: show_notes/<file>.md|.txt whose name
    shares a word (>= 4 letters) with the track name. Longest match wins."""
    if not notes_dir.is_dir() or not track:
        return None
    words = {w for w in re.findall(r"[a-zäöüß]+", (track + " " + track_cfg).lower()) if len(w) >= 4}
    best, score = None, 0
    for p in notes_dir.iterdir():
        if p.suffix.lower() not in (".md", ".txt") or p.name.lower().startswith(("schedule-", "readme")):
            continue
        name = set(re.findall(r"[a-zäöüß]+", p.stem.lower()))
        sc = sum(len(w) for w in words & name)
        if sc > score:
            best, score = p, sc
    if best is None:
        return None
    try:
        return best.read_text(encoding="utf-8")[:6000]
    except OSError:
        return None


def build_facts(roundinfo: dict | None, track: dict, notes: str | None, video_s: float | None,
                notes_dir: Path | None = None) -> dict:
    """Everything the moderation may say — and nothing else."""
    info = (roundinfo or {}).get("info") or {}
    st = (roundinfo or {}).get("standings") or {}
    league = info.get("league") or st.get("league") or {}
    season = info.get("season") or st.get("season") or {}
    nxt = info.get("nextRound") or {}
    facts = {
        "liga": league.get("name"),
        "liga_slug": league.get("slug"),
        "saison": season.get("name"),
        "runde": nxt.get("number"),
        "runden_gesamt": season.get("totalRounds"),
        "strecke": track.get("name") or nxt.get("track"),
        "layout": track.get("config") or nxt.get("trackConfig"),
        "ort": track.get("place") or None,              # iRacing: city, country
        "wetter": track.get("weather"),
        "streckennotizen": notes,
        "video_sekunden": round(video_s) if video_s else None,
    }
    sched = _schedule(info, schedule_tracks(notes_dir or HERE / "show_notes", league.get("slug")))
    if sched:
        facts["saisonplan"] = sched
    from race_commentator import quali_format
    qf = quali_format(league.get("slug"))
    if qf:
        facts["qualifying_format"] = qf["text"]          # WCT: AM first, then PRO
    lr = info.get("lastRound") or {}
    rows = st.get("standings") or []
    # Pro/Am season (the flag is only on the STANDINGS season, not round-info's):
    # CLS points are CLASS points, so one mixed list would put
    # an AM driver "in the lead" — everything is split by class instead.
    classes = [c for c in ("PRO", "AM")
               if any(r.get("proAmClass") == c for r in rows)
               or any(x.get("proAmClass") == c for race in lr.get("races") or []
                      for x in race.get("results") or [])] \
        if (season.get("proAmEnabled") or (st.get("season") or {}).get("proAmEnabled")) else []
    if classes:
        facts["pro_am"] = classes

    def entry(r, pos=None):
        return {"platz": pos or r.get("position"), "name": r.get("name"), "team": r.get("teamName"),
                "auto": r.get("car"), "start": r.get("startPosition")}

    def fastest(res, by_time=False):
        if by_time:
            timed = [r for r in res if r.get("bestLapMs")]
            fl = min(timed, key=lambda r: r["bestLapMs"]) if timed else None
        else:
            fl = next((r for r in res if r.get("fastestLap")), None)
        return {"name": fl.get("name"), "zeit": _ms(fl.get("bestLapMs"))} if fl else None

    races = []
    for race in lr.get("races") or []:
        res = race.get("results") or []
        item = {"rennen": race.get("raceNumber"), "starter": len(res)}
        if classes:
            item["gesamtsieger"] = entry(res[0]) if res else None
            item["klassen"] = {}
            for c in classes:
                cr = sorted([r for r in res if r.get("proAmClass") == c],
                            key=lambda r: r.get("position") or 999)
                if cr:
                    item["klassen"][c] = {
                        "top3": [dict(entry(r, i + 1), gesamtplatz=r.get("position"))
                                 for i, r in enumerate(cr[:3])],
                        "schnellste_runde": fastest(cr, by_time=True), "starter": len(cr)}
        else:
            item["top5"] = [entry(r) for r in res[:5]]
            item["schnellste_runde"] = fastest(res)
        races.append(item)
    if races:
        dotd = lr.get("driverOfTheDay") or {}
        facts["letzte_runde"] = {"name": lr.get("name"), "strecke": lr.get("track"),
                                 "rennen": races,
                                 "fahrer_des_tages": dotd.get("winnerName")}
    done = season.get("completedRounds") or 0
    if rows and done:
        def row(r, rank=None):
            return {"rang": rank or r.get("rank"), "name": r.get("name"), "punkte": r.get("points"),
                    "team": r.get("teamName")}

        def table(rs):
            t = {"top5": [row(r, i + 1) for i, r in enumerate(rs[:5])]}
            if len(rs) >= 2 and rs[0].get("points") is not None and rs[1].get("points") is not None:
                t["abstand_p1_p2"] = rs[0]["points"] - rs[1]["points"]
            return t
        tab = {"nach_runden": done}
        if classes:
            tab["klassen"] = {c: table([r for r in rows if r.get("proAmClass") == c]) for c in classes
                              if any(r.get("proAmClass") == c for r in rows)}
        else:
            tab.update(table(rows))
            tab["top5"] = [row(r) for r in rows[:5]]
        facts["tabelle"] = tab
    return facts


_MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
           "Oktober", "November", "Dezember"]


def _date(iso: str | None, spoken: bool = False) -> str | None:
    """CLS UTC time -> local date (the stream PC's own time zone): '20.10.'
    or, to be read out, '20. Oktober'."""
    if not iso:
        return None
    try:
        d = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None
    return f"{d.day}. {_MONTHS[d.month - 1]}" if spoken else d.strftime("%d.%m.")


def schedule_tracks(notes_dir: Path, slug: str | None) -> dict:
    """show_notes/schedule-<league slug>.md: lines 'dd.mm.yy Track' -> {'dd.mm.': track}."""
    if not slug:
        return {}
    try:
        text = (notes_dir / f"schedule-{slug.split('/')[0]}.md").read_text(encoding="utf-8")
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*(\d{1,2})\.(\d{1,2})\.(?:\d{2,4})?\s+(.+?)\s*$", line)
        if m:
            out[f"{int(m.group(1)):02d}.{int(m.group(2)):02d}."] = m.group(3)
    return out


def _schedule(info: dict, tracks: dict | None = None) -> dict | None:
    """The season's rounds from CLS round-info: today's round, what is left,
    the next dates and the final. CLS has no tracks for later rounds — only
    the next one — so nothing else may be said about them."""
    rounds = sorted(info.get("rounds") or [], key=lambda r: r.get("number") or 0)
    if not rounds:
        return None
    nxt = info.get("nextRound") or {}
    open_ = [r for r in rounds if r.get("status") != "COMPLETED"]
    today = next((r for r in open_ if r.get("number") == nxt.get("number")), open_[0] if open_ else None)
    later = [r for r in open_ if today is None or (r.get("number") or 0) > (today.get("number") or 0)]
    tracks = tracks or {}

    def item(r):
        d = _date(r.get("startsAt"))
        it = {"runde": r.get("number"), "name": r.get("name"), "datum": _date(r.get("startsAt"), spoken=True)}
        if tracks.get(d):
            it["strecke"] = tracks[d]
        return it
    out = {"runden_gesamt": len(rounds),
           "gefahren": sum(1 for r in rounds if r.get("status") == "COMPLETED"),
           "heute": dict(item(today), strecke=nxt.get("track") or item(today).get("strecke")) if today else None,
           "danach_noch": len(later),
           "naechste_termine": [item(r) for r in later[:3]],
           "finale": item(rounds[-1]) if later else None}
    return out


def schedule_source(cfg: dict, facts: dict) -> str | None:
    slug = (facts.get("liga_slug") or "").split("/")[0]
    return (cfg.get("schedule_sources") or {}).get(slug) or (cfg.get("sources") or {}).get("schedule")


def available_segments(cfg: dict, facts: dict) -> list[str]:
    out = []
    classes = [c.lower() for c in facts.get("pro_am") or []]
    for s in cfg.get("segments") or DEFAULT_CONFIG["segments"]:
        if s == "last_race" and not facts.get("letzte_runde"):
            continue
        if s == "standings" and not facts.get("tabelle"):
            continue
        if s == "schedule" and not (facts.get("saisonplan") and schedule_source(cfg, facts)):
            continue
        if s in PROAM_SPLIT and classes:
            out += [x for x in PROAM_SPLIT[s] if SEGMENT_CLASS[x] in classes]
            continue
        out.append(s)
    return out


# -- script --------------------------------------------------------------------
SYSTEM_PROMPT = (
    "Du bist {host} eines deutschsprachigen Twitch-Streams für iRacing-Ligarennen der "
    "Community CAS. Du sprichst live vor dem Qualifying, deine Texte werden von einer "
    "Sprachausgabe vorgelesen. Schreibe natürlich, locker, sympathisch, kurze Sätze, keine "
    "Aufzählungen, keine Emojis, keine Sonderzeichen; Rundenzeiten wie 1:39,5 sind ok. "
    "Nutze für Fakten zu Liga, Ergebnissen und Tabelle AUSSCHLIESSLICH die gegebenen Daten. "
    "Zur Strecke: wenn 'streckennotizen' da sind, nutze sie; sonst nur allgemein bekannte, "
    "sichere Aussagen ohne konkrete Zahlen (keine Rekorde, Baujahre, Längen), lieber kürzer. "
    "In Pro/Am-Ligen ('pro_am' in den Daten) gibt es zwei Wertungen, PRO und AM, mit eigenen "
    "Siegern und eigenen Tabellenführern; sprich immer klar von der jeweiligen Klasse und "
    "vermische sie nie. "
    "Antworte NUR mit JSON: {{\"segments\": {{\"<id>\": \"<Text>\", ...}}}} für genau die "
    "verlangten Segment-IDs."
)
SEGMENT_BRIEF = {
    "intro": "Begrüßung: Liga, Saison, welche Runde, welche Strecke, dass gleich Qualifying und "
             "Rennen kommen. 35-55 Wörter.",
    "last_race": "Rückblick auf die letzte Runde: Sieger und Podium (pro Rennen), wer überrascht "
                 "hat (z. B. weit vorgefahren), schnellste Runde, Fahrer des Tages. 60-90 Wörter. "
                 "Dabei ist die Ergebnisliste im Bild.",
    "standings": "Meisterschaftsstand: wer führt, Abstand, wer dahinter lauert, bei Pro/Am beide "
                 "Klassen kurz. 50-80 Wörter. Die Tabelle ist im Bild.",
    "track": "Die Strecke vorstellen: wo sie liegt (ort; dabei ist eine Karte im Bild), Charakter, Schlüsselkurven, wo man überholen kann. {words}.",
    "schedule": "Der Saisonplan der Liga ist im Bild (daten.saisonplan): welche Runde heute ist, "
                "wie viele noch folgen, die nächsten Termine mit Datum, das Finale bzw. Special Event. "
                "Strecken späterer Runden nur, wenn sie bei der Runde stehen (strecke). 35-60 Wörter.",
    "outro": "Überleitung: gleich geht es live zum Qualifying, viel Spaß. Steht 'qualifying_format' "
             "in den Daten, erkläre es kurz. 20-45 Wörter.",
    # Pro/Am seasons: one segment per class, the page shows only that class
    "last_race_pro": "Rückblick auf die letzte Runde, Klasse PRO (daten.letzte_runde, jeweils "
                     "klassen.PRO): Klassensieger und Podium, schnellste Runde der Klasse; Gesamtsieger "
                     "oder Fahrer des Tages nur wenn passend. 45-70 Wörter. Im Bild ist die Ergebnisliste "
                     "der PRO-Klasse.",
    "last_race_am": "Weiter mit der Klasse AM in der letzten Runde (klassen.AM): Klassensieger und "
                    "Podium, schnellste Runde der Klasse. Mit einer kurzen Überleitung beginnen, die "
                    "PRO-Klasse wurde gerade besprochen. 40-60 Wörter. Im Bild ist die AM-Ergebnisliste.",
    "standings_pro": "Meisterschaft, Klasse PRO (daten.tabelle.klassen.PRO): wer führt, Abstand, wer "
                     "dahinter lauert. Die Punkte sind Klassenpunkte. 35-55 Wörter. Im Bild ist die "
                     "PRO-Tabelle.",
    "standings_am": "Meisterschaft, Klasse AM (daten.tabelle.klassen.AM): wer führt, Abstand, wer "
                    "dahinter lauert. Kurze Überleitung von der PRO-Klasse. 35-55 Wörter. Im Bild ist "
                    "die AM-Tabelle.",
}


def _track_words(facts, cfg=None):
    if (cfg or {}).get("video_after_speech", True):
        return "70-110 Wörter. Zum Schluss auf das Streckenvideo überleiten, das direkt danach läuft"
    v = facts.get("video_sekunden")
    if v:
        n = int(min(220, max(50, v * WORDS_PER_S * 0.9)))
        return f"{n - 15}-{n} Wörter, gesprochen während das Streckenvideo läuft"
    return "80-120 Wörter, gesprochen während das Streckenvideo läuft"


def claude_script(cfg: dict, api_cfg: dict, facts: dict, segments: list[str], post=None) -> dict:
    """{segment: text} from Claude. Raises on any failure (caller falls back)."""
    host = cfg.get("host_name") or ""
    host_desc = f"{host}, die KI-Moderation" if host else "die KI-Moderation"
    brief = {s: SEGMENT_BRIEF[s].format(words=_track_words(facts, cfg)) for s in segments if s in SEGMENT_BRIEF}
    user = json.dumps({"segmente": brief, "daten": facts}, ensure_ascii=False)
    payload = {"model": cfg.get("model") or DEFAULT_CONFIG["model"], "max_tokens": 6000,
               "thinking": {"type": "disabled"},
               "system": SYSTEM_PROMPT.format(host=host_desc),
               "messages": [{"role": "user", "content": user}]}
    if post is None:
        from race_commentator import Commentator
        c = Commentator(config=dict(api_cfg, timeout_s=120))
        post = c._post
    try:
        data = post(payload)
    except RuntimeError as e:
        if "thinking" not in str(e).lower():
            raise
        payload.pop("thinking")
        payload["max_tokens"] = 16000      # a model that thinks anyway needs room left for the answer
        data = post(payload)
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    segs = _parse_segments(text)
    if segs is None:
        kinds = ",".join(b.get("type", "?") for b in data.get("content", [])) or "keine"
        start = re.sub(r"\s+", " ", text.strip())[:120]
        raise RuntimeError(f"Claude: kein JSON im Skript (stop_reason {data.get('stop_reason')}, "
                           f"Blöcke {kinds}, Antwort: „{start or 'leer'}“)")
    out = {s: str(segs[s]).strip() for s in segments if segs.get(s)}
    if not out:
        raise RuntimeError("Claude: leeres Skript")
    return out


def _parse_segments(text: str) -> dict | None:
    """{segment: text} from Claude's reply: plain JSON, in a ``` block, with
    or without the "segments" wrapper. None if nothing usable."""
    t = re.sub(r"```(?:json)?", "", text or "")
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        d = json.loads(t[a:b + 1])
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    segs = d.get("segments", d)
    return segs if isinstance(segs, dict) else None


def template_script(cfg: dict, facts: dict, segments: list[str]) -> dict:
    out = {}
    liga, strecke = facts.get("liga") or "unserer Liga", facts.get("strecke") or "der heutigen Strecke"
    host = cfg.get("host_name")
    if "intro" in segments:
        r = f"Runde {facts['runde']} " if facts.get("runde") else ""
        out["intro"] = (f"Hallo und herzlich willkommen zur {liga}! "
                        + (f"Ich bin {host}. " if host else "")
                        + f"Heute steht {r}auf dem Programm, wir sind in {strecke}. "
                        "Gleich gibt es das Qualifying und danach das Rennen.")
    lr = facts.get("letzte_runde")
    if "last_race" in segments and lr and not facts.get("pro_am"):
        parts = [f"Ein kurzer Blick zurück auf die letzte Runde in {lr.get('strecke') or 'der letzten Runde'}."]
        for race in lr["rennen"]:
            top = race["top5"]
            nr = f"Rennen {race['rennen']}: " if len(lr["rennen"]) > 1 else ""
            if top:
                pod = ", ".join(t["name"] for t in top[1:3])
                parts.append(f"{nr}Gewonnen hat {top[0]['name']}" + (f", vor {pod}." if pod else "."))
            if race.get("schnellste_runde"):
                parts.append(f"Die schnellste Runde fuhr {race['schnellste_runde']['name']}.")
        if lr.get("fahrer_des_tages"):
            parts.append(f"Fahrer des Tages wurde {lr['fahrer_des_tages']}.")
        out["last_race"] = " ".join(parts)
    for c in ("PRO", "AM"):
        sid = f"last_race_{c.lower()}"
        if sid in segments and lr:
            parts = ([f"Ein kurzer Blick zurück auf die letzte Runde, zuerst die Klasse {c}."] if c == "PRO"
                     else [f"Und jetzt die Klasse {c}."])
            for race in lr["rennen"]:
                k = (race.get("klassen") or {}).get(c)
                if not k:
                    continue
                top = k["top3"]
                nr = f"Rennen {race['rennen']}: " if len(lr["rennen"]) > 1 else ""
                pod = ", ".join(t["name"] for t in top[1:3])
                parts.append(f"{nr}Klassensieg für {top[0]['name']}" + (f", vor {pod}." if pod else "."))
                if k.get("schnellste_runde"):
                    parts.append(f"Die schnellste Runde der Klasse fuhr {k['schnellste_runde']['name']}.")
            if c == "AM" and lr.get("fahrer_des_tages"):
                parts.append(f"Fahrer des Tages wurde {lr['fahrer_des_tages']}.")
            out[sid] = " ".join(parts)
    tab = facts.get("tabelle")
    for c in ("PRO", "AM"):
        sid = f"standings_{c.lower()}"
        k = ((tab or {}).get("klassen") or {}).get(c)
        if sid in segments and k and k.get("top5"):
            t = k["top5"]
            s = (f"In der Meisterschaft der Klasse {c} führt {t[0]['name']} mit {t[0]['punkte']} Punkten."
                 if c == "PRO" else f"Bei den {c}-Fahrern führt {t[0]['name']} mit {t[0]['punkte']} Punkten.")
            if len(t) > 1:
                s += f" Dahinter folgt {t[1]['name']}" + (f", {k['abstand_p1_p2']} Punkte zurück." if k.get("abstand_p1_p2") is not None else ".")
            if len(t) > 2:
                s += f" Auf Platz drei liegt {t[2]['name']}."
            out[sid] = s
    if "standings" in segments and tab and tab.get("top5"):
        t = tab["top5"]
        s = f"In der Meisterschaft führt {t[0]['name']} mit {t[0]['punkte']} Punkten."
        if len(t) > 1:
            s += f" Dahinter folgt {t[1]['name']}" + (f", {tab['abstand_p1_p2']} Punkte zurück." if tab.get("abstand_p1_p2") is not None else ".")
        if len(t) > 2:
            s += f" Auf Platz drei liegt {t[2]['name']}."
        out["standings"] = s
    sp = facts.get("saisonplan")
    if "schedule" in segments and sp:
        t = sp.get("heute") or {}
        parts = [f"Ein Blick auf den Saisonplan: heute steht Runde {t.get('runde')} von {sp['runden_gesamt']} an."
                 if t.get("runde") else f"Ein Blick auf den Saisonplan mit {sp['runden_gesamt']} Runden."]
        nt = sp.get("naechste_termine") or []
        if nt and nt[0].get("datum"):
            parts.append(f"Weiter geht es am {nt[0]['datum']} mit Runde {nt[0]['runde']}"
                         + (f" in {nt[0]['strecke']}." if nt[0].get("strecke") else "."))
        fin = sp.get("finale")
        if fin and fin.get("datum") and fin.get("runde") != (nt[0].get("runde") if nt else None):
            parts.append(f"Das Saisonfinale steigt am {fin['datum']}"
                         + (" mit einem Special Event." if "special" in (fin.get("strecke") or fin.get("name") or "").lower()
                            else "."))
        out["schedule"] = " ".join(parts)
    if "track" in segments:
        out["track"] = (f"Jetzt ein Blick auf die Strecke: {strecke}"
                        + (f" in {facts['ort']}" if facts.get("ort") else "")
                        + (f", gefahren wird das Layout {facts['layout']}." if facts.get("layout") else ".")
                        + " Schauen wir uns eine Runde an.")
    if "outro" in segments:
        out["outro"] = ("Das war der Überblick. Jetzt geht es live ins Qualifying. "
                        + (facts["qualifying_format"] + " " if facts.get("qualifying_format") else "")
                        + "Viel Spaß beim Zuschauen!")
    return out


def split_sentences(text: str, max_len: int = 220) -> list[str]:
    """Speakable chunks: whole sentences, short ones joined up to max_len."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    out, cur = [], ""
    for s in sents:
        if cur and len(cur) + 1 + len(s) > max_len:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


# -- runner --------------------------------------------------------------------
class ShowRunner:
    """speaker: say(text, segment) -> id, is_done(id) -> bool, cancel(ids)
    obs: obs_ws.ObsClient (or a fake with the same methods)
    facts_fn() -> facts dict; script_fn(facts, segments) -> ({seg: text}, source)
    director_on() switches REGIE on."""

    def __init__(self, cfg, obs, speaker, facts_fn, script_fn, director_on,
                 clock=time.time, local_now=None, listening=lambda: True, pin=None, store=None):
        self.cfg = cfg
        self.obs = obs
        self.speaker = speaker
        self.facts_fn = facts_fn
        self.script_fn = script_fn
        self.director_on = director_on
        self.clock = clock
        self.local_now = local_now or _dt.datetime.now
        self.listening = listening
        self.pin = pin or (lambda cls: None)   # pin the 5010 pages to "pro" / "am" / None
        self.state = "idle"            # idle | preparing | running | done | stopped
        self.script: dict = {}
        self.script_source = ""
        self.script_at = None
        self.facts: dict = {}
        self.segments: list = []
        self.seg_i = -1
        self.seg: dict | None = None   # running segment: {id, ids, started, media, sources, deadline}
        self.ran_on = None             # date of the last (auto) run
        self.log: list = []
        self.last_error = ""
        self._lock = threading.RLock()
        self._thread = None
        self._preparing = False
        self._shown = None             # source kept up across PRO -> AM segments
        self.as_of_round = None        # replay of an earlier round: data as before it
        self._items = None             # source names in the Moderator scene (read at start)
        self.order: list = []          # all segments the script was written for (an emptied one is skipped)
        self.edited = False            # changed by hand on /show -> no automatic rewrite
        self.store = store             # Path to keep the script across restarts (None = memory only)
        self._load()

    # -- public ---------------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            return {"state": self.state, "segment": self.seg["id"] if self.seg else None,
                    "segments": self.segments, "script": self.script,
                    "script_source": self.script_source, "script_at": self.script_at,
                    "start_at": self.cfg.get("start_at"), "auto_start": self.cfg.get("auto_start"),
                    "ran_today": self.ran_on == self.local_now().date(),
                    "log": self.log[-30:], "last_error": self.last_error,
                    "facts": self.facts, "order": self.order or self.segments,
                    "as_of_round": self.as_of_round,
                    "edited": self.edited}

    def set_script(self, texts: dict) -> str | None:
        """Hand edits from /show: {segment: text}. An empty text drops the
        segment. Returns an error text or None."""
        with self._lock:
            if self.state == "running":
                return "Läuft gerade – erst stoppen"
            order = self.order or self.segments
            if not order:
                return "Noch kein Skript"
            for sid, text in (texts or {}).items():
                if sid in order:
                    self.script[sid] = str(text or "").strip()
            self.segments = [s for s in order if self.script.get(s)]
            self.edited = True
            self.script_at = self.clock()
            self._note("Skript von Hand geändert: " + (", ".join(self.segments) or "leer"))
            self._save()
            return None

    def _keep_edit(self) -> bool:
        return self.edited and self.script_at is not None and self.clock() - self.script_at < EDIT_KEEP_S

    def _save(self):
        if not self.store:
            return
        try:
            self.store.write_text(json.dumps({
                "script": self.script, "order": self.order, "segments": self.segments,
                "source": self.script_source, "at": self.script_at, "edited": self.edited,
                "facts": self.facts}, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            self._note(f"Skript nicht gespeichert: {e}")

    def _load(self):
        if not self.store:
            return
        try:
            d = json.loads(self.store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not d.get("at") or self.clock() - d["at"] > EDIT_KEEP_S:
            return
        self.script, self.order, self.segments = d.get("script") or {}, d.get("order") or [], d.get("segments") or []
        self.script_source, self.script_at = d.get("source") or "", d["at"]
        self.edited, self.facts = bool(d.get("edited")), d.get("facts") or {}

    def prepare(self):
        """Collect the facts and write the script (blocking: a few seconds with Claude)."""
        self._preparing = True
        with self._lock:
            prev = self.state
            if prev not in ("running",):
                self.state = "preparing"
        try:
            facts = self.facts_fn()
            segs = available_segments(self.cfg, facts)
            script, source = self.script_fn(facts, segs)
            with self._lock:
                self.facts, self.segments, self.script, self.script_source = facts, \
                    [s for s in segs if script.get(s)], script, source
                self.order = list(segs)
                self.edited = False
                self.script_at = self.clock()
                self._note(f"Skript fertig ({source}): {', '.join(self.segments)}")
                self._save()
        except Exception as e:
            self.last_error = f"Skript: {e}"
            self._note(self.last_error)
        finally:
            self._preparing = False
            with self._lock:
                if self.state == "preparing":
                    self.state = prev if prev != "preparing" else "idle"

    def start(self):
        with self._lock:
            if self.state == "running":
                return
            self.state = "running"
            self.ran_on = self.local_now().date()
        if not self._keep_edit() and (not self.script or (self.script_at and self.clock() - self.script_at > 3 * 3600)):
            self.prepare()
            with self._lock:
                self.state = "running"
        with self._lock:
            if not self.segments:
                self._note("kein Skript – Ablauf abgebrochen")
                self.state = "stopped"
                return
            self._obs(lambda: self.obs.set_scene(self.cfg["scenes"]["moderator"]),
                      "Szene Moderator")
            try:
                self._items = set(self.obs.scene_items(self.cfg["scenes"]["moderator"]))
            except Exception:
                self._items = None             # unknown: try the class sources
            for key in set(SEGMENT_SOURCES.values()) | {"track_video", "track_location"}:   # nothing left on from a rehearsal
                name = self._name(key)
                if name and (self._items is None or name in self._items):
                    self._source(key, False)
            self._shown = None
            self._pin(None)
            self.seg_i = -1
            self._next_segment()

    def next(self):
        with self._lock:
            if self.state == "running" and self.seg:
                self.speaker.cancel([i for i in self.seg["ids"] if not self.speaker.is_done(i)])
                self._end_segment()
                self._next_segment()

    def stop(self):
        with self._lock:
            was = self.state
            if was in ("running", "preparing"):
                self.state = "stopped"          # before _end_segment: hide, never keep up for "next"
            if self.seg:
                self.speaker.cancel([i for i in self.seg["ids"] if not self.speaker.is_done(i)])
                self._end_segment()
            self.seg = None
            if was in ("running", "preparing"):
                self._note("gestoppt")

    def tick(self):
        """Call every ~0.5 s."""
        with self._lock:
            if self.state == "running":
                self._tick_segment()
                return
            if self.state in ("done", "stopped") and self.ran_on != self.local_now().date():
                self.state = "idle"                   # a new day
            if self.state != "idle" or not self.cfg.get("auto_start"):
                return
            now = self.local_now()
            due = self._due(now)
            if due is None:
                return
        # outside the lock: may block on OBS / Claude
        if due == "prepare" and not self._preparing and not self._keep_edit() and (
                not self.script or self.clock() - (self.script_at or 0) > 3600):
            self._preparing = True
            threading.Thread(target=self.prepare, daemon=True).start()
        elif due == "start":
            if self.cfg.get("only_while_streaming"):
                try:
                    if not self.obs.streaming():
                        return
                except Exception as e:
                    self.last_error = f"OBS: {e}"
                    return
            self._note("Automatischer Start")
            self.start()

    def run_forever(self, every=0.5):
        def loop():
            while True:
                try:
                    self.tick()
                except Exception as e:          # never kill the thread
                    self.last_error = f"tick: {e}"
                time.sleep(every)
        self._thread = threading.Thread(target=loop, daemon=True, name="ShowRunner")
        self._thread.start()

    # -- internals ------------------------------------------------------------
    def _due(self, now):
        try:
            hh, mm = [int(x) for x in str(self.cfg.get("start_at") or "").split(":")]
        except ValueError:
            return None
        start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if self.ran_on == now.date():
            return None
        if start <= now < start + _dt.timedelta(minutes=int(self.cfg.get("start_window_min") or 20)):
            return "start"
        if start - _dt.timedelta(minutes=5) <= now < start:
            return "prepare"
        return None

    def _note(self, msg):
        self.log.append({"t": round(self.clock(), 1), "msg": msg})
        if len(self.log) > 200:
            del self.log[:100]

    def _obs(self, fn, what):
        try:
            fn()
            return True
        except Exception as e:
            self.last_error = f"OBS {what}: {e}"
            self._note(self.last_error)
            return False

    def _src_key(self, sid):
        """Source key for a segment: a class segment uses its own source when it
        exists in the Moderator scene, else the plain one (pinned to the class)."""
        key = SEGMENT_SOURCES.get(sid)
        fb = CLASS_SOURCE_FALLBACK.get(key)
        if fb:
            name = self._name(key)
            if not name or (self._items is not None and name not in self._items):
                return fb
        return key

    def _has(self, key) -> bool:
        """An optional source: configured and (when the scene is known) in it."""
        name = self._name(key)
        return bool(name) and (self._items is None or name in self._items)

    def _name(self, key):
        """OBS source name for a key — matched to the scene without caring about
        upper / lower case or extra spaces ("Tabelle Pro" = "Tabelle PRO")."""
        name = (schedule_source(self.cfg, self.facts) if key == "schedule"
                else (self.cfg.get("sources") or {}).get(key))
        if name and self._items:
            return match_name(name, self._items) or name
        return name

    def _pin(self, cls):
        try:
            self.pin(cls)
        except Exception as e:
            self._note(f"Klasse {cls or 'frei'}: {e}")

    def _source(self, key, visible):
        name = self._name(key)
        if name:
            self._obs(lambda: self.obs.set_visible(self.cfg["scenes"]["moderator"], name, visible),
                      f"{'zeigen' if visible else 'ausblenden'} {name}")

    def _next_segment(self):
        self.seg_i += 1
        if self.seg_i >= len(self.segments):
            self._finish()
            return
        sid = self.segments[self.seg_i]
        text = self.script.get(sid) or ""
        seg = {"id": sid, "ids": [], "started": self.clock(), "media": None}
        if sid in SEGMENT_SOURCES:
            self._pin(SEGMENT_CLASS.get(sid))
            key = self._src_key(sid)
            if self._shown != key:
                if self._shown:
                    self._source(self._shown, False)
                self._source(key, True)
                self._shown = key
        if sid == "track" and self._has("track_location"):
            self._source("track_location", True)              # the map while the track is talked about
            seg["map"] = True
        if sid == "track" and (self.cfg.get("sources") or {}).get("track_video") \
                and self.cfg.get("video_after_speech", True):
            seg["video_pending"] = self.cfg["sources"]["track_video"]   # started once the text is spoken
        elif sid == "track" and (self.cfg.get("sources") or {}).get("track_video"):
            vid = self.cfg["sources"]["track_video"]
            self._source("track_video", True)
            if self._obs(lambda: self.obs.media_restart(vid), f"Video {vid}"):
                seg["media"] = vid
        chunks = split_sentences(text)
        for ch in chunks:
            seg["ids"].append(self.speaker.say(ch, sid))
        words = len(text.split())
        # safety net: no voice page listening / audio lost -> move on anyway
        seg["deadline"] = self.clock() + words / WORDS_PER_S * 1.6 + DEADLINE_PAD_S
        self.seg = seg
        self._note(f"Segment {sid}: {len(chunks)} Sätze" + (f", Video {seg['media']}" if seg["media"] else ""))

    def _tick_segment(self):
        seg = self.seg
        if seg is None:
            return
        spoken = all(self.speaker.is_done(i) for i in seg["ids"])
        if seg.get("video_pending"):
            if not (spoken or self.clock() > seg["deadline"]):
                return
            if not spoken:
                self._note(f"Segment {seg['id']}: Stimme nicht fertig gemeldet – Video startet")
                self.speaker.cancel(seg["ids"])
            vid, seg["video_pending"] = seg["video_pending"], None
            if seg.get("map"):
                self._source("track_location", False)
                seg["map"] = False
            self._source("track_video", True)
            if self._obs(lambda: self.obs.media_restart(vid), f"Video {vid}"):
                seg["media"], seg["video_at"] = vid, self.clock()
                self._note(f"Video {vid} läuft")
            seg["deadline"] = self.clock() + DEADLINE_PAD_S
            return
        media_done = True
        if seg["media"]:
            try:
                st = self.obs.media_status(seg["media"])
                media_done = st["state"] in ("OBS_MEDIA_STATE_ENDED", "OBS_MEDIA_STATE_STOPPED",
                                             "OBS_MEDIA_STATE_ERROR", "OBS_MEDIA_STATE_NONE")
                if st.get("duration_s") and self.clock() - seg.get("video_at", seg["started"]) > st["duration_s"] + 3:
                    media_done = True
            except Exception:
                media_done = True
        late = self.clock() > seg["deadline"] and (not seg["media"] or media_done)
        if (spoken and media_done) or late:
            if late and not spoken:
                self._note(f"Segment {seg['id']}: Stimme nicht fertig gemeldet – weiter")
                self.speaker.cancel(seg["ids"])
            self._end_segment()
            self._next_segment()

    def _end_segment(self):
        seg = self.seg
        if not seg:
            return
        if seg["id"] in SEGMENT_SOURCES:
            src = self._src_key(seg["id"])
            nxt = self.segments[self.seg_i + 1] if 0 <= self.seg_i + 1 < len(self.segments) else None
            if not (self.state == "running" and nxt in SEGMENT_SOURCES
                    and self._src_key(nxt) == src):        # one shared page PRO -> AM: stays up
                self._source(src, False)
                self._shown = None
                self._pin(None)
        if seg.get("map"):
            self._source("track_location", False)
        if seg["media"]:
            self._obs(lambda: self.obs.media_stop(seg["media"]), "Video stoppen")
            self._source("track_video", False)
        self.seg = None

    def _finish(self):
        self._obs(lambda: self.obs.set_scene(self.cfg["scenes"]["iracing"]), "Szene iRacing")
        if self.cfg.get("director_after"):
            try:
                self.director_on()
                self._note("REGIE an")
            except Exception as e:
                self.last_error = f"REGIE: {e}"
        self.state = "done"
        self.seg = None
        self._note("Übergabe an iRacing")
