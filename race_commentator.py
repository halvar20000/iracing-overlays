"""
Race commentator ("Kommentar") — step 2 of the AI broadcast idea: turns the
REGIE decisions (race_director.py log: what is shown and WHY) into short
German commentary lines for a text overlay.

Two ways to word a line:
  • TEMPLATES (always available, no cost, no network): fixed German sentences
    with some variety, built from the structured facts of each decision.
  • CLAUDE (optional): the same facts plus race context go to the Anthropic
    Messages API, which writes one natural sentence. Needs an API key in
    commentary_config.json ("api_key") or the ANTHROPIC_API_KEY environment
    variable. Runs in a background thread; anything slow or failing falls
    back to the template line, so the overlay never waits on the network.

Facts only: a line never says more than the director knows (who, which
position, gap, what happened). The prompt forbids inventing reasons,
damage or lap times.

Pure logic + urllib; no SDK, no Flask. Tested by test_race_commentator.py.
"""

from __future__ import annotations

import json
import os
import queue
import random
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent / "commentary_config.json"
DEFAULT_CONFIG = {
    "mode": "auto",                 # auto = Claude if a key is there, else templates
                                    # "template" = never call the API, "ai" = same as auto
    "api_key": "",
    "model": "claude-haiku-5-5",    # fast + cheap; "claude-sonnet-5-5" for nicer prose
    "timeout_s": 6.0,
    "workspace_id": "",             # only for a key NOT scoped to a workspace (wrkspc_…)
}
API_URL = "https://api.anthropic.com/v1/messages"
AI_STALE_S = 8.0          # an AI line that took longer than this is dropped (template used)
KEEP_LINES = 40
MIN_GAP_S = 4.0           # lines closer than this: the older one is replaced, not stacked

SYSTEM_PROMPT = (
    "Du bist Live-Kommentator eines iRacing-Ligarennens auf Twitch (Community-Liga, "
    "deutschsprachig, lockerer aber sachlicher Ton). Du bekommst, was die Regie gerade "
    "zeigt und warum, plus etwas Rennkontext. Schreibe GENAU EINEN kurzen deutschen Satz "
    "(höchstens 18 Wörter) zum aktuellen Bild. Regeln: Nutze nur die gegebenen Fakten. "
    "Erfinde nichts – keine Gründe, keine Schäden, keine Rundenzeiten, keine Strafen, keine "
    "Vorgeschichte. Nenne Fahrer mit Vor- und Nachnamen oder nur Nachnamen. Abstände mit "
    "Komma (0,4 Sekunden). Im Qualifying (session = Qualifying): delta_to_pole_s < 0 heißt "
    "schneller als die aktuelle Pole, > 0 langsamer; predicted_position ist eine Hochrechnung "
    "(sag \"auf Kurs zu Platz …\"), lap_time ist offiziell. Fährt ein Fahrer noch ohne Zeit "
    "(kein lap_time), stell ihn vor – Auto, Team, Aufwärmrunde oder schnelle Runde – und sag "
    "NICHT, dass er noch keine Zeit hat. Steht quali_abschnitt im Kontext, ist gerade nur diese "
    "Klasse (AM oder PRO) auf der Strecke – du kannst es erwähnen. Wiederhole nicht Formulierungen der letzten Sätze. Keine Emojis, "
    "keine Anführungszeichen, keine Startnummern mit #, kein Vorspann – nur der Satz."
)


# Qualifying formats of a league (CLS league slug) that moderation and
# commentary should know about. WCT GT3: the first 15 minutes belong to the
# AM drivers, the last 15 to the PRO drivers (Thomas, 2026-10-15).
QUALI_FORMATS = {
    "cas-gt3-wct": {"split_min": 15, "first": "AM", "second": "PRO",
                    "text": "Im Qualifying gehören die ersten 15 Minuten den AM-Fahrern, "
                            "die letzten 15 Minuten den PRO-Fahrern."},
}


def quali_format(league_slug: str | None) -> dict | None:
    return QUALI_FORMATS.get((league_slug or "").split("/")[0])


def quali_phase(fmt: dict, elapsed_s: float) -> str:
    """Which class has the track: 'AM' / 'PRO'."""
    return fmt["first"] if elapsed_s < fmt["split_min"] * 60 else fmt["second"]


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    if not cfg.get("api_key"):
        cfg["api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")
    return cfg


def _gap(v) -> str:
    try:
        return f"{float(v):.1f}".replace(".", ",")
    except (TypeError, ValueError):
        return "?"


def facts_from(entry: dict) -> dict | None:
    """Director log entry -> the facts a line may use (None = nothing to say)."""
    kind, info = entry.get("kind"), entry.get("info") or {}
    f = {"kind": kind, "driver": entry.get("name") or "", "number": entry.get("car_number")}
    if kind == "battle":
        f.update(position=info.get("position"), ahead=info.get("ahead"), gap_s=info.get("gap"))
    elif kind == "field" and info.get("quali"):
        f.update(session="Qualifying", car=info.get("car"), team=info.get("team"),
                 on_flying_lap=bool(info.get("flying")), lap_time=info.get("lap_time_str"),
                 position=info.get("position"), cars_with_time=info.get("times_set"))
    elif kind in ("leader", "field", "pit"):
        f.update(position=info.get("position"))
    elif kind == "incident":
        f.update(event=info.get("event"), position=info.get("position"))
    elif kind == "replay":
        f.update(event=info.get("event"), passed=info.get("passed"), position=info.get("position"))
    elif kind == "seen":
        f.update(event=info.get("event"), passed=info.get("passed"), position=info.get("position"))
    # qualifying (quali_director.py)
    elif kind == "attack":
        if info.get("finished"):
            return None                       # the lap result (quali_lap) says it
        f.update(session="Qualifying", first_lap=bool(info.get("first_lap")),
                 next_lap=bool(info.get("next_lap")), delta_to_pole_s=info.get("delta"),
                 predicted_position=info.get("predicted_position"),
                 pole_sitter=bool(info.get("pole_sitter")), pole=info.get("pole"),
                 pole_time=info.get("pole_time"))
    elif kind == "pole":
        f.update(session="Qualifying", position=1, lap_time=info.get("lap_time_str"))
    elif kind == "quali_lap":
        f.update(session="Qualifying", lap_time=info.get("lap_time_str"), position=info.get("position"),
                 gap_to_pole_s=info.get("gap_to_pole"), new_pole=bool(info.get("new_pole")),
                 margin_s=info.get("margin"), kept_pole=bool(info.get("kept_pole")))
    else:
        return None
    return f


TEMPLATES = {
    # qualifying
    "attack_first": [
        "{driver} ist auf der ersten schnellen Runde unterwegs.",
        "Die erste gezeitete Runde: {driver} legt vor.",
    ],
    "attack_faster": [
        "{driver} ist {delta} Sekunden schneller als die Pole unterwegs!",
        "Achtung, {driver} auf Pole-Kurs – {delta} Sekunden unter der Bestzeit.",
        "{driver} greift die Pole an, aktuell {delta} Sekunden schneller.",
    ],
    "attack_slower": [
        "{driver} auf schneller Runde, {delta} Sekunden hinter der Pole – auf Kurs zu Platz {predicted_position}.",
        "{driver} ist auf einer guten Runde, auf Kurs zu Platz {predicted_position}.",
    ],
    "attack_pole_sitter": [
        "{driver} versucht, die eigene Pole-Zeit noch zu verbessern.",
        "Der Mann auf Pole legt nach: {driver} auf schneller Runde.",
    ],
    "attack_next": [
        "{driver} hängt direkt die nächste schnelle Runde an.",
    ],
    "pole": [
        "{driver} steht mit {lap_time} auf der Pole.",
        "Die Zeit, die es zu schlagen gilt: {lap_time} von {driver}.",
    ],
    "quali_lap_pole": [
        "Neue Pole! {driver} mit {lap_time}.",
        "{driver} setzt sich an die Spitze – {lap_time}!",
        "Bestzeit für {driver}: {lap_time}, das ist die Pole.",
    ],
    "quali_lap_kept": [
        "{driver} verbessert die eigene Pole-Zeit auf {lap_time}.",
        "{driver} legt noch einmal nach: {lap_time}, die Pole wird schneller.",
    ],
    "quali_lap": [
        "{driver} mit {lap_time} – Platz {position}, {gap} Sekunden hinter der Pole.",
        "{lap_time} für {driver}, das ist Platz {position}.",
    ],
    "battle": [
        "Kampf um Platz {position}: {driver} liegt nur {gap} Sekunden hinter {ahead}.",
        "{driver} macht Druck auf {ahead} – {gap} Sekunden, es geht um Platz {position}.",
        "Spannendes Duell um P{position}: {ahead} vorne, {driver} direkt dahinter.",
        "{ahead} gegen {driver}, der Abstand im Kampf um Platz {position}: {gap} Sekunden.",
    ],
    "leader": [
        "Blick an die Spitze: {driver} führt das Rennen an.",
        "Ganz vorne fährt {driver}.",
        "{driver} an der Spitze des Feldes.",
    ],
    # qualifying, a car the director shows "from the field"
    "quali_field_time": [
        "{driver} steht bisher auf Platz {position} mit {lap_time}.",
        "Wir schauen auf {driver}, aktuell P{position} in der Zeitenliste.",
        "{driver} im {car}, bislang Platz {position}.",
    ],
    "quali_field_flying": [
        "{driver} im {car} ist auf seiner schnellen Runde.",
        "Schnelle Runde für {driver}.",
        "{driver} startet in eine gezeitete Runde.",
    ],
    "quali_field": [
        "{driver} im {car} bringt die Reifen auf Temperatur.",
        "Hier ist {driver} im {car} unterwegs.",
        "{driver} fährt für {team}.",
        "{driver} ist auf der Aufwärmrunde.",
        "Wir sehen {driver} im {car}.",
    ],
    "field": [
        "Blick ins Feld: {driver} auf Position {position}.",
        "Wir schauen auf {driver}, aktuell P{position}.",
    ],
    "pit": [
        "{driver} kommt an die Box – bisher auf Platz {position}.",
        "Boxenstopp für {driver}, Position {position}.",
    ],
    "incident_collision": [
        "Kontakt! {driver} ist verwickelt.",
        "Da hat es geknallt – {driver} mittendrin.",
    ],
    "incident_lost_control": [
        "{driver} dreht sich!",
        "Dreher von {driver}!",
    ],
    "replay_overtake": [
        "In der Wiederholung: {driver} geht an {passed} vorbei – jetzt Platz {position}.",
        "Noch einmal das Manöver: {driver} schnappt sich {passed}, P{position}.",
        "Die Wiederholung zeigt es: {driver} an {passed} vorbei.",
    ],
    "replay_collision": [
        "Noch einmal in der Wiederholung: der Kontakt mit {driver}.",
        "Die Wiederholung: hier kommt es zur Berührung, {driver} ist beteiligt.",
    ],
    "replay_lost_control": [
        "In der Wiederholung: der Dreher von {driver}.",
        "Noch einmal: hier verliert {driver} das Auto.",
    ],
    "seen_overtake": [
        "Und da ist es passiert: {driver} ist an {passed} vorbei!",
        "{driver} schiebt sich an {passed} vorbei – Platz {position}!",
    ],
    "seen_collision": [
        "Berührung – {driver} ist beteiligt!",
    ],
    "seen_lost_control": [
        "{driver} hat sich gedreht!",
    ],
}


def template_line(f: dict, rng: random.Random, avoid: set | None = None) -> str | None:
    key = f["kind"]
    if key in ("incident", "replay", "seen"):
        key = f"{key}_{f.get('event')}"
    elif key == "attack":
        d = f.get("delta_to_pole_s")
        key = ("attack_first" if f.get("first_lap") else "attack_next" if f.get("next_lap")
               else "attack_pole_sitter" if f.get("pole_sitter")
               else "attack_faster" if d is not None and d < 0 else "attack_slower")
    elif key == "field" and f.get("session") == "Qualifying":
        key = ("quali_field_time" if f.get("lap_time") and f.get("position") else
               "quali_field_flying" if f.get("on_flying_lap") else "quali_field")
    elif key == "quali_lap":
        key = ("quali_lap_kept" if f.get("kept_pole") else
               "quali_lap_pole" if f.get("new_pole") else "quali_lap")
    options = TEMPLATES.get(key)
    if not options:
        return None
    vals = {k: ("" if v is None else v) for k, v in f.items()}
    vals["gap"] = _gap(f["gap_s"]) if f.get("gap_s") is not None else ""
    if f.get("gap_to_pole_s") is not None:
        vals["gap"] = _gap(f["gap_to_pole_s"])
    vals["delta"] = _gap(abs(f["delta_to_pole_s"])) if f.get("delta_to_pole_s") is not None else ""
    if f.get("lap_time"):
        vals["lap_time"] = f["lap_time"].replace(".", ",")
    # a template needing a fact we do not have is skipped
    usable = [t for t in options
              if all(vals.get(n) not in ("", None) for n in
                     ("position", "ahead", "passed", "gap", "driver", "delta", "lap_time",
                      "predicted_position", "car", "team") if "{" + n + "}" in t)]
    if not usable:
        return None
    fresh = [t for t in usable if t not in (avoid or set())] or usable
    return rng.choice(fresh).format(**vals)


class Commentator:
    def __init__(self, rng: random.Random | None = None, config: dict | None = None,
                 api_call=None):
        self.rng = rng or random.Random()
        self.cfg = config if config is not None else load_config()
        self.lines: deque = deque(maxlen=KEEP_LINES)   # newest first
        self._seen_log: set = set()
        self._recent_tpl: deque = deque(maxlen=6)
        self._next_id = 1
        self._lock = threading.Lock()
        self._api_call = api_call or self._call_claude
        self._q: queue.Queue = queue.Queue()
        self._worker = None
        self.last_error = ""
        self.last_info = ""
        self._thinking_off_ok = True
        self.on_line = None            # callback(line dict) for every published line (voice)
        self.played: set = set()       # line ids the voice page finished (or skipped)
        self.cancelled: set = set()    # show lines cut short (operator "next")

    # -- public ---------------------------------------------------------------
    @property
    def ai_enabled(self) -> bool:
        return self.cfg.get("mode") != "template" and bool(self.cfg.get("api_key"))

    def reset(self):
        with self._lock:
            # moderation lines survive a REGIE switch (the show turns REGIE on)
            keep = [ln for ln in self.lines if ln.get("queue")]
            self.lines.clear()
            self.lines.extend(keep)
            self._seen_log.clear()

    # -- moderation (show_runner.py): spoken in full, in order -----------------
    def say_show(self, text: str, segment: str = "") -> int:
        """A moderation sentence: never replaced, never skipped by the voice
        page (it plays them one after the other and reports each one)."""
        now = time.time()
        with self._lock:
            line = {"id": self._next_id, "t": round(now, 1), "text": text, "kind": "show",
                    "source": "show", "queue": True, "segment": segment}
            self.lines.appendleft(line)
            self._next_id += 1
        if self.on_line is not None:
            try:
                self.on_line(dict(line))
            except Exception:
                pass
        return line["id"]

    def mark_played(self, line_id: int):
        with self._lock:
            self.played.add(line_id)
            if len(self.played) > 400:
                self.played = set(sorted(self.played)[-200:])

    def is_done(self, line_id: int) -> bool:
        return line_id in self.played or line_id in self.cancelled

    def cancel(self, ids):
        with self._lock:
            self.cancelled.update(ids)

    def skip_existing(self, log_entries):
        """Mark entries as already commented (REGIE switched on with an old log)."""
        for e in log_entries or []:
            self._seen_log.add((e.get("t"), e.get("kind"), e.get("car_idx")))

    def feed(self, log_entries, context=None, now: float | None = None):
        """New director log entries (any order, already-seen ones ignored).
        `context` is a dict or a callable returning one (only called when
        there is something new to say)."""
        now = time.time() if now is None else now
        new = []
        for e in log_entries or []:
            k = (e.get("t"), e.get("kind"), e.get("car_idx"))
            if k in self._seen_log:
                continue
            self._seen_log.add(k)
            new.append(e)
        new.sort(key=lambda e: e.get("t") or 0)
        if new and callable(context):
            try:
                context = context()
            except Exception:
                context = {}
        for e in new:
            f = facts_from(e)
            if f is None:
                continue
            if self.ai_enabled:
                self._ensure_worker()
                self._q.put((now, f, dict(context or {})))
            else:
                self._publish_template(f, now)
        if len(self._seen_log) > 500:
            self._seen_log = set(list(self._seen_log)[-200:])

    def status(self) -> dict:
        with self._lock:
            lines = [dict(ln, played=ln["id"] in self.played, cancelled=ln["id"] in self.cancelled)
                     if ln.get("queue") else dict(ln) for ln in self.lines]
            return {"lines": lines, "ai": self.ai_enabled,
                    "model": self.cfg.get("model") if self.ai_enabled else None,
                    "last_error": self.last_error}

    # -- internals ------------------------------------------------------------
    def _publish(self, text: str, f: dict, now: float, source: str):
        with self._lock:
            if (self.lines and now - self.lines[0]["t"] < MIN_GAP_S
                    and self.lines[0]["kind"] not in ("replay", "show")):
                self.lines.popleft()          # the picture moved on before it was read
            line = {"id": self._next_id, "t": round(now, 1), "text": text,
                    "kind": f["kind"], "source": source}
            self.lines.appendleft(line)
            self._next_id += 1
        if self.on_line is not None:
            try:
                self.on_line(dict(line))
            except Exception:
                pass

    def _publish_template(self, f, now):
        line = template_line(f, self.rng, set(self._recent_tpl))
        if line:
            self._recent_tpl.append(line)
            self._publish(line, f, now, "template")

    def _ensure_worker(self):
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()

    def _run(self):
        while True:
            t0, f, ctx = self._q.get()
            # a backlog is pointless: only the newest picture matters
            while not self._q.empty():
                t0, f, ctx = self._q.get_nowait()
            text = None
            t_call = time.time()
            try:
                with self._lock:
                    recent = [ln["text"] for ln in list(self.lines)[:5]]
                text = self._api_call(f, ctx, recent)
                self.last_error = ""
            except Exception as e:          # network, key, quota … -> template
                self.last_error = str(e)[:400]
            took = time.time() - t_call
            if text and time.time() - t0 <= AI_STALE_S:
                self._publish(text, f, time.time(), "claude")
            else:
                if text:
                    self.last_error = f"Claude zu langsam ({took:.1f} s), Vorlage benutzt"
                elif not self.last_error:
                    self.last_error = f"Claude ohne Text ({took:.1f} s): {self.last_info}"
                self._publish_template(f, time.time())

    def _call_claude(self, f: dict, ctx: dict, recent: list) -> str | None:
        user = json.dumps({"bild": f, "rennen": ctx, "letzte_saetze": recent}, ensure_ascii=False)
        payload = {
            "model": self.cfg.get("model") or DEFAULT_CONFIG["model"],
            "max_tokens": 400,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user}],
        }
        # Haiku 5.5 thinks by default and used the whole budget before writing
        # (stop_reason max_tokens, only a thinking block) -> switch it off; a
        # model that rejects the switch gets a big budget instead
        if self._thinking_off_ok:
            payload["thinking"] = {"type": "disabled"}
        else:
            payload["max_tokens"] = 3000
        try:
            data = self._post(payload)
        except RuntimeError as e:
            if self._thinking_off_ok and "HTTP 400" in str(e) and "thinking" in str(e).lower():
                self._thinking_off_ok = False
                payload.pop("thinking", None)
                payload["max_tokens"] = 3000
                data = self._post(payload)
            else:
                raise
        self.last_info = (f"stop_reason={data.get('stop_reason')}, "
                          f"blocks={[b.get('type') for b in data.get('content', [])]}")
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        text = " ".join(p.strip() for p in parts).strip().strip('"„“')
        return text[:220] or None

    def _post(self, payload: dict) -> dict:
        headers = {
            "content-type": "application/json",
            "x-api-key": self.cfg["api_key"],
            "anthropic-version": "2023-06-01",
        }
        if self.cfg.get("workspace_id"):
            headers["anthropic-workspace-id"] = self.cfg["workspace_id"]
        req = urllib.request.Request(API_URL, data=json.dumps(payload).encode("utf-8"),
                                     method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=float(self.cfg.get("timeout_s") or 6.0)) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Anthropic says WHY in the body (credit balance, model, key …)
            try:
                msg = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
            except Exception:
                msg = ""
            raise RuntimeError(f"HTTP {e.code}: {msg or e.reason}") from None
