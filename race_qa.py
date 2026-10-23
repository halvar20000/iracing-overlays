"""
Race Q&A ("Frag Claude") — a co-commentator the streamer can ASK during a
race he commentates himself (not the AI moderation / REGIE):

    Stream Deck key (push-to-talk) -> microphone -> German speech to text
    (faster-whisper, local) -> Claude with tools that read OUR live data
    -> a short spoken German answer through the existing voice
    (Commentator.say_show -> race_voice -> OBS /commentary/voice).

Tools (all read-only, all local HTTP to the other overlays):
  • live_session   — track, session, laps / time left, camera car (dashboard)
  • live_standings — 5005 /standings: live order, gaps, best / last laps
  • race_results   — 5002 /results: final classification of the last race
  • championship   — 5010 /api/state: live championship projection
  • round_info     — 5010 /api/roundinfo "info": last round's results (per race
                     + combined, Pro/Am), DotD, next round, season calendar
  • league_table   — 5010 /api/roundinfo "standings": the CLS table before
                     this round (split from round_info so neither half is cut)
    Both take an optional `liga` (CLS slug) and `runde`: then they ask CLS
    directly (/api/overlay/round-info, /standings with beforeRound) — 5010
    only knows ONE league (the one on the grid or pinned), so "the WCT race
    at Indianapolis" was unanswerable while 5010 sat on PCCD (2026-10-22).
  • cls_leagues    — CLS /api/overlay/leagues: every CAS league + season
  • track_notes    — show_notes/<track>.md (show_runner.find_notes)
  • web_search     — Anthropic's server tool, for track history / trivia
                     ("qa_web_search": false switches it off)
Claude may only answer from tool results; otherwise it says it does not know.

Trigger: toggle (one key: press = listen, press again = answer; stops by
itself after qa_max_record_s) or hold (ask_start / ask_stop for a Stream
Deck plugin that sends key-down and key-up). A typed question on /ask works
without a microphone.

Config: commentary_config.json (same file / key as race_commentator.py):
  qa_model (claude-haiku-5-5), qa_timeout_s (25), qa_web_search (true),
  qa_stt_model (faster-whisper size, "small"), qa_mic (part of the input
  device name or its index; "" = Windows default), qa_max_record_s (20),
  qa_sources {"standings": url, ...} to move a source.

Pure logic; microphone, STT and the API are injectable. Tested by
test_race_qa.py.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import date
from pathlib import Path

from race_commentator import API_URL, load_config

HERE = Path(__file__).resolve().parent
SAMPLE_RATE = 16000
KEEP_ITEMS = 30
MAX_ROUNDS = 6               # tool round trips per question
TOOL_RESULT_MAX = 24000      # characters of one tool result handed to Claude (~7k tokens)
MIN_RECORD_S = 0.4           # shorter = a mis-press, nothing to transcribe

DEFAULT_SOURCES = {
    "standings": "http://127.0.0.1:5005/standings",
    "results": "http://127.0.0.1:5002/results",
    "championship": "http://127.0.0.1:5010/api/state",
    "roundinfo": "http://127.0.0.1:5010/api/roundinfo",
}
DEFAULTS = {
    "qa_model": "claude-haiku-5-5",
    "qa_timeout_s": 25.0,
    "qa_web_search": True,
    "qa_stt_model": "small",
    "qa_mic": "",
    "qa_max_record_s": 20.0,
    "qa_cls_url": "https://league.simracing-hub.com",
    "qa_hold": True,            # answer waits ("ready") until the streamer plays it
    "qa_hold_max_s": 300.0,     # ...and is dropped after this long unplayed
    "qa_log": True,             # every question / answer -> logs/frag_claude_<date>.jsonl
}
LOG_DIR = HERE / "logs"           # tests point this at a temp folder
SPEAKING_MAX_S = 90.0           # "speaking" shown until the voice page reports the line played

SYSTEM_PROMPT = (
    "Du bist Co-Kommentator bei einem iRacing-Ligarennen der CAS-Community auf Twitch. "
    "Der Streamer kommentiert selbst und stellt dir live eine Frage (per Spracherkennung). "
    "Deine Antwort wird vorgelesen und ist im Stream zu hören.\n"
    "Regeln:\n"
    "- Antworte IMMER auf Deutsch, auch wenn Quellen oder Suchergebnisse englisch sind "
    "(übersetze sie).\n"
    "- Schreibe vor und zwischen Werkzeugaufrufen KEINEN Text und erkläre nie, wie du "
    "vorgehst oder was du zuordnest – nur die fertige Antwort wird vorgelesen.\n"
    "- Namen können von der Spracherkennung falsch geschrieben sein: nimm den Fahrer aus "
    "Fahrerfeld oder Tabelle, dessen Name am ähnlichsten klingt (\"André\" = André Brechmann, "
    "falls es nur einen André gibt). Nimm NIE einfach den Fahrer im Bild, außer die Frage "
    "meint ihn ausdrücklich (\"der im Bild\"). Passt kein Fahrer eindeutig, sag kurz, wen "
    "du nicht zuordnen kannst.\n"
    "- \"Tabelle\", \"Meisterschaft\", \"Gesamtwertung\", \"Punkte\" meinen die "
    "Meisterschaftstabelle (championship bzw. league_table), NICHT die Reihenfolge im "
    "laufenden Rennen. Die Live-Reihenfolge (live_standings) nur, wenn nach dem Rennen, "
    "der aktuellen Position, Abständen oder \"gerade\" gefragt wird.\n"
    "- Hole dir die Fakten mit den Werkzeugen. Antworte NUR mit dem, was in den "
    "Werkzeug-Ergebnissen steht. Erfinde nie Ergebnisse, Platzierungen, Punkte, Zeiten, "
    "Daten oder Namen.\n"
    "- Steht es in keinem Ergebnis, sag kurz und freundlich, dass du es nicht weißt "
    "(z. B. \"Das habe ich leider nicht in meinen Daten.\").\n"
    "- Antworte auf Deutsch in 1 bis 3 kurzen, gesprochenen Sätzen, höchstens 50 Wörter. "
    "Kein Markdown, keine Listen, keine Quellenangaben, keine URLs, keine Emojis.\n"
    "- Rundenzeiten wie 1:39,512, Abstände mit Komma (0,4 Sekunden).\n"
    "- Für Geschichte und Trivia der Strecke (z. B. Formel-1-Rennen dort) zuerst "
    "track_notes, dann web_search, falls vorhanden. Ligadaten (letztes Rennen, Tabelle, "
    "Fahrer des Tages) nur aus round_info / league_table / championship / race_results.\n"
    "- Es gibt mehrere CAS-Ligen (z. B. GT3 WCT, PCC/PCCD, SFL, IEC, Combined Cup, Fun League). "
    "Ohne Angabe gilt die Liga der laufenden Session. Nennt die Frage eine Liga oder ein Rennen, "
    "das nicht dazu passt, hol dir mit cls_leagues den slug und frag round_info / league_table "
    "mit liga. Für eine bestimmte Runde gib runde an (Nummern und Strecken stehen im "
    "Saisonkalender von round_info). Sag dazu, welche Liga du meinst.\n"
    "- Wiederhole die Frage nicht und fang nicht mit \"Gute Frage\" an."
)

GERMAN_PROMPT = (
    "Übersetze die folgende Antwort eines Co-Kommentators ins Deutsche. Lass jede "
    "Überlegung, jede Beschreibung des Vorgehens und jeden Satz über die Frage selbst weg – "
    "nur die eigentliche Antwort in 1 bis 3 kurzen gesprochenen Sätzen, nichts hinzufügen. "
    "Kein Markdown, keine Anführungszeichen."
)

_TOOL_BLOCKS = ("tool_use", "server_tool_use", "web_search_tool_result", "tool_result",
                "web_fetch_tool_result")
_EN = {"the", "and", "is", "are", "was", "were", "of", "this", "that", "which", "about", "with",
       "not", "but", "user", "asks", "question", "result", "results", "won", "race", "year",
       "it", "they", "has", "have", "from", "for", "in"}
_DE = {"der", "die", "das", "und", "ist", "hat", "nicht", "mit", "auf", "den", "dem", "ein",
       "eine", "im", "zu", "von", "es", "sich", "auch", "er", "sie", "wurde", "war", "noch",
       "für", "platz", "rennen", "punkte"}


def final_text(content: list) -> str:
    """The answer = the text blocks AFTER the last tool block of the final reply.
    With the web search (a server tool, run inside the same reply) Claude often
    writes a short English plan before searching — Hungaroring 22.10.: "The user
    asks about Formel 1 ... the track notes contain F1 history, but ..." was read
    out before the German answer."""
    last = -1
    for i, b in enumerate(content):
        if b.get("type") in _TOOL_BLOCKS:
            last = i
    return " ".join(b.get("text", "").strip() for b in content[last + 1:]
                    if b.get("type") == "text").strip()


def looks_english(text: str) -> bool:
    words = [w.strip(".,;:!?\"'()").lower() for w in text.split()]
    en = sum(w in _EN for w in words)
    de = sum(w in _DE for w in words)
    return en >= 3 and en > 2 * de


TOOLS = [
    {"name": "live_session",
     "description": "Aktuelle iRacing-Session: Strecke, Layout, Session-Art (Training/Qualifying/"
                    "Rennen), Runden/Zeit übrig, Fahrer im Bild (Kamera), Fahrerfeld (Namen).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "live_standings",
     "description": "Live-Reihenfolge der laufenden Session (Turm): Position, Klasse, Fahrer, "
                    "Team, Abstand/Intervall, beste und letzte Runde, +/- seit dem Start, Box.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "race_results",
     "description": "Ergebnis des zuletzt beendeten Rennens dieser Session (Positionen, Abstände, "
                    "Runden, Incidents, schnellste Runde, Startplatz).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "championship",
     "description": "Meisterschaft LIVE: Tabelle vor dem Rennen und Hochrechnung mit dem "
                    "laufenden Rennen (Punkte vorher, Rennpunkte, Gesamt, Positionsänderung).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "round_info",
     "description": "Liga-Daten von CLS zur LETZTEN gefahrenen Runde: Ergebnisse je Rennen und "
                    "kombiniert (Pro/Am, Startplatz, schnellste Runde, Punkte), Fahrer des Tages, "
                    "nächste Runde, Teilnehmerzahlen, Saisonkalender mit Strecken. Mit runde: "
                    "die Ergebnisse genau dieser Runde.",
     "input_schema": {"type": "object", "properties": {
         "liga": {"type": "string", "description": "CLS-Liga-slug aus cls_leagues, z. B. cas-gt3-wct; "
                                                   "leer = Liga der laufenden Session"},
         "runde": {"type": "integer", "description": "Rundennummer der Saison; leer = die letzte "
                                                    "gefahrene Runde"}}}},
    {"name": "league_table",
     "description": "Meisterschaftstabelle der Liga von CLS VOR dieser Runde (Rang, Punkte, Pro/Am, "
                    "Team, Incidents, gefahrene Runden) und das Punkteschema. Mit runde: die "
                    "Tabelle NACH dieser Runde.",
     "input_schema": {"type": "object", "properties": {
         "liga": {"type": "string", "description": "CLS-Liga-slug aus cls_leagues, z. B. cas-gt3-wct; "
                                                   "leer = Liga der laufenden Session"},
         "runde": {"type": "integer", "description": "Rundennummer der Saison; leer = die letzte "
                                                    "gefahrene Runde"}}}},
    {"name": "cls_leagues",
     "description": "Alle CAS-Ligen bei CLS mit slug, Name und aktueller Saison.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "track_notes",
     "description": "Vorbereitete Notizen zur aktuellen Strecke (Geschichte, Fakten, Kurven, "
                    "Rekorde), falls vorhanden.",
     "input_schema": {"type": "object", "properties": {}}},
]

# compaction: what an overlay sends for its page but Claude does not need
_DROP_KEYS = ("color", "colour", "svg", "html", "logo", "flag", "url", "design", "brand",
              "icon", "avatar", "img", "image", "css", "pct", "_src")
# ids, internals and duplicates of "name" (a full 36-driver table must fit)
_DROP_EXACT = {"registrationid", "teamid", "userid", "user_id", "iracingmemberid", "iracing_member",
               "cust_id", "car_idx", "class_id", "car_path", "car_screen", "abbrev", "flair",
               "country", "countrycode", "generatedat", "focus", "ingdc", "iracing_pos", "config",
               "ui", "fetch_age", "reason_id", "_gap_to_leader", "subsession_id", "first_name",
               "last_name", "firstname", "lastname", "seq", "ok"}


def merged_config(cfg: dict | None = None) -> dict:
    out = dict(DEFAULTS)
    out.update(cfg if cfg is not None else load_config())
    return out


def compact(obj, depth: int = 0, list_max: int = 40):
    """Shrink an overlay's JSON for the model: no colours / images / urls,
    floats rounded, long lists cut, deep nesting summarised."""
    if depth > 12:
        return "…"
    if isinstance(obj, dict):
        out = {}
        name = obj.get("name")
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in _DROP_EXACT or any(d in kl for d in _DROP_KEYS):
                continue
            if kl in ("team_name", "teamname") and v == name:     # solo entries: team = driver
                continue
            if v is None or v == "" or v == [] or v == {}:
                continue
            out[k] = compact(v, depth + 1, list_max)
        return out
    if isinstance(obj, list):
        items = [compact(v, depth + 1, list_max) for v in obj[:list_max]]
        if len(obj) > list_max:
            items.append(f"… {len(obj) - list_max} weitere")
        return items
    if isinstance(obj, float):
        return round(obj, 3)
    return obj


def _dumps(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if len(s) > TOOL_RESULT_MAX:
        s = s[:TOOL_RESULT_MAX] + " …(gekürzt)"
    return s


def http_json(url: str, timeout: float = 4.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def stt_hint(session: dict | None) -> str:
    """Words for Whisper's initial prompt: names it would otherwise mishear."""
    s = session or {}
    names = [n for n in (s.get("fahrer") or []) if n][:40]
    parts = ["Frage an Claude zum iRacing-Rennen."]
    if s.get("strecke"):
        parts.append(f"Strecke: {s['strecke']}.")
    if names:
        parts.append("Fahrer: " + ", ".join(names) + ".")
    return " ".join(parts)[:800]


# --- microphone / speech to text (lazy imports: Windows stream PC only) -------
class MicRecorder:
    """Records the microphone while open (sounddevice / PortAudio)."""

    def __init__(self, device: str | int = ""):
        self.device = device
        self._stream = None
        self._chunks: list = []

    def _device(self):
        import sounddevice as sd        # pip install sounddevice
        d = self.device
        if d in ("", None):
            return None
        try:
            return int(d)
        except (TypeError, ValueError):
            pass
        for i, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0 and str(d).lower() in dev["name"].lower():
                return i
        raise RuntimeError(f"Mikrofon '{d}' nicht gefunden")

    def start(self):
        import sounddevice as sd
        self._chunks = []

        def cb(indata, frames, t, status):
            self._chunks.append(indata.copy())

        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                      device=self._device(), callback=cb)
        self._stream.start()

    def stop(self):
        import numpy as np
        st, self._stream = self._stream, None
        if st is not None:
            st.stop()
            st.close()
        if not self._chunks:
            return np.zeros(0, dtype="float32")
        return np.concatenate(self._chunks).reshape(-1)


class WhisperSTT:
    """faster-whisper, German, loaded once (first question takes longer)."""

    def __init__(self, model: str = "small"):
        self.model_name = model or "small"
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel     # pip install faster-whisper
                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        return self._model

    def transcribe(self, audio, hint: str = "") -> str:
        model = self.load()
        segs, _info = model.transcribe(audio, language="de", beam_size=1, vad_filter=True,
                                       initial_prompt=hint or None, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segs).strip()


# --- the co-commentator --------------------------------------------------------
class RaceQA:
    """State machine idle -> recording -> transcribing -> thinking -> ready -> idle.
    With qa_hold (default) a finished answer WAITS as "ready" — the status
    overlay (5000/ask/overlay) shows it, and the streamer plays it with the
    same key (or ask_play) when it suits his commentary; cancel drops it.
    Without qa_hold it is spoken at once. One question at a time; a new press
    while busy is ignored (status says why)."""

    def __init__(self, config: dict | None = None, speak=None, session_fn=None, fetch=None,
                 recorder=None, stt=None, api_post=None, notes_dir: Path | None = None,
                 clock=time.time, is_done=None, log_dir: Path | None = None):
        self.cfg = merged_config(config)
        self.speak = speak                       # callable(text) -> line id (voice)
        self.is_done = is_done                   # callable(line id) -> voice page played it
        self.log_dir = log_dir or LOG_DIR
        self.session_fn = session_fn or (lambda: {})
        self.fetch = fetch or http_json
        self.recorder = recorder
        self.stt = stt
        self._api_post = api_post or self._post
        self.notes_dir = notes_dir or HERE / "show_notes"
        self.clock = clock
        self.state = "idle"
        self.items: deque = deque(maxlen=KEEP_ITEMS)      # newest first
        self.last_error = ""
        self._next_id = 1
        self._lock = threading.Lock()
        self._rec_started = 0.0
        self._rec_token = 0
        self._thinking_off_ok = True
        self._preloaded = False
        self.stt_ready = False
        self._pending = None                     # the answer waiting to be played
        self._ready_at = 0.0
        self._speaking = None                    # (line id, since) of the answer being spoken

    # -- public ---------------------------------------------------------------
    @property
    def ai_enabled(self) -> bool:
        return bool(self.cfg.get("api_key"))

    def status(self) -> dict:
        self._expire()
        with self._lock:
            state = self.state
            now = self.clock()
            if state == "idle" and self._speaking:
                line_id, since = self._speaking
                done = False
                try:
                    done = self.is_done(line_id) if self.is_done else now - since > 8
                except Exception:
                    pass
                if done or now - since > SPEAKING_MAX_S:
                    self._speaking = None
                else:
                    state = "speaking"
            pend = self._pending
            return {"state": state, "items": [dict(i) for i in self.items],
                    "pending": {"id": pend["id"], "question": pend["question"],
                                "answer": pend["answer"],
                                "waiting_s": round(now - self._ready_at, 1)} if pend else None,
                    "hold": bool(self.cfg.get("qa_hold")),
                    "ai": self.ai_enabled, "model": self.cfg.get("qa_model"),
                    "web_search": bool(self.cfg.get("qa_web_search")),
                    "stt_model": self.cfg.get("qa_stt_model"), "stt_ready": self.stt_ready,
                    "recording_s": round(self.clock() - self._rec_started, 1)
                    if self.state == "recording" else None,
                    "max_record_s": self.cfg.get("qa_max_record_s"),
                    "last_error": self.last_error}

    def toggle(self) -> str:
        """One Stream Deck key: listen / answer / play the waiting answer."""
        if self.state == "recording":
            return self.stop_listen()
        if self.state == "ready":
            return self.play()
        return self.start_listen()

    def play(self) -> str:
        """Speak the answer that is waiting (qa_hold)."""
        self._expire()
        with self._lock:
            item = self._pending
            if self.state != "ready" or item is None:
                return f"keine Antwort bereit ({self.state})"
            self._pending = None
            self.state = "idle"
        self._speak(item)
        return "spielt ab"

    def start_listen(self) -> str:
        self._expire()
        with self._lock:
            if self.state == "ready":
                return "Antwort wartet — erst abspielen oder verwerfen"
            if self.state != "idle":
                return f"beschäftigt ({self.state})"
            self.state = "recording"
            self._rec_started = self.clock()
            self._rec_token += 1
            token = self._rec_token
        try:
            if self.recorder is None:
                self.recorder = MicRecorder(self.cfg.get("qa_mic") or "")
            self.recorder.start()
        except Exception as e:
            self._fail(f"Mikrofon: {e}")
            return self.last_error
        # stop by itself after qa_max_record_s (a forgotten second press)
        limit = float(self.cfg.get("qa_max_record_s") or 20.0)
        t = threading.Timer(limit, self._auto_stop, args=(token,))
        t.daemon = True
        t.start()
        return "hört zu"

    def stop_listen(self) -> str:
        with self._lock:
            if self.state != "recording":
                return f"nicht am Zuhören ({self.state})"
            self.state = "transcribing"
            took = self.clock() - self._rec_started
        try:
            audio = self.recorder.stop()
        except Exception as e:
            self._fail(f"Mikrofon: {e}")
            return self.last_error
        if took < MIN_RECORD_S:
            with self._lock:
                self.state = "idle"
            return "zu kurz"
        threading.Thread(target=self._from_audio, args=(audio,), daemon=True).start()
        return "verarbeite"

    def cancel(self) -> str:
        with self._lock:
            was = self.state
            if was == "ready":                   # drop the waiting answer
                if self._pending is not None:
                    self._pending["discarded"] = True
                    self._log("discarded", self._pending)
                self._pending = None
                self.state = "idle"
                return "Antwort verworfen"
            if was == "recording":
                self._rec_token += 1
        if was == "recording":
            try:
                self.recorder.stop()
            except Exception:
                pass
            with self._lock:
                self.state = "idle"
        return f"abgebrochen ({was})"

    def ask_text(self, question: str, wait: bool = False) -> str:
        """A typed question (operator page, tests)."""
        question = (question or "").strip()
        if not question:
            return "leer"
        self._expire()
        with self._lock:
            if self.state == "ready":
                return "Antwort wartet — erst abspielen oder verwerfen"
            if self.state != "idle":
                return f"beschäftigt ({self.state})"
            self.state = "thinking"
        if wait:
            self._answer(question, typed=True)
        else:
            threading.Thread(target=self._answer, args=(question, True), daemon=True).start()
        return "denkt nach"

    def preload(self):
        """Load the speech model in the background (first answer is faster;
        the very first run also downloads it, ~480 MB for "small")."""
        if self._preloaded:
            return
        self._preloaded = True

        def run():
            try:
                if self.stt is None:
                    self.stt = WhisperSTT(self.cfg.get("qa_stt_model") or "small")
                if hasattr(self.stt, "load"):
                    self.stt.load()
                self.stt_ready = True
            except Exception as e:
                self.last_error = f"Spracherkennung: {e}"
        threading.Thread(target=run, daemon=True).start()

    # -- pipeline -------------------------------------------------------------
    def _auto_stop(self, token):
        if self.state == "recording" and token == self._rec_token:
            self.stop_listen()

    def _expire(self):
        """A waiting answer nobody played for qa_hold_max_s goes (the key must not stay stuck)."""
        with self._lock:
            if (self.state == "ready" and self._pending is not None
                    and self.clock() - self._ready_at > float(self.cfg.get("qa_hold_max_s") or 300)):
                self._pending["expired"] = True
                self._log("expired", self._pending)
                self._pending = None
                self.state = "idle"

    def _log(self, event: str, item: dict):
        """logs/frag_claude_<date>.jsonl — every question and what became of it."""
        if not self.cfg.get("qa_log") or self.log_dir is None:
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            rec = {"event": event, "t_wall": time.strftime("%Y-%m-%d %H:%M:%S"),
                   **{k: item.get(k) for k in ("id", "question", "typed", "answer", "tools",
                                               "error", "took_s")}}
            if event == "answer":
                rec["model"] = self.cfg.get("qa_model")
            f = self.log_dir / f"frag_claude_{time.strftime('%Y-%m-%d')}.jsonl"
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass                                 # a log problem never stops an answer

    def _speak(self, item):
        try:
            item["line_id"] = self.speak(item["answer"])
            item["spoken"] = True
            self._log("played", item)
            with self._lock:
                self._speaking = (item["line_id"], self.clock())
        except Exception as e:
            item["error"] = f"Stimme: {e}"

    def _fail(self, msg: str):
        self.last_error = msg[:400]
        with self._lock:
            self.state = "idle"

    def _from_audio(self, audio):
        try:
            if self.stt is None:
                self.stt = WhisperSTT(self.cfg.get("qa_stt_model") or "small")
            try:
                hint = stt_hint(self.session_fn())
            except Exception:
                hint = ""
            text = self.stt.transcribe(audio, hint)
            self.stt_ready = True
        except Exception as e:
            self._fail(f"Spracherkennung: {e}")
            return
        if not text.strip():
            self._fail("Nichts verstanden")
            return
        with self._lock:
            self.state = "thinking"
        self._answer(text, typed=False)

    def _answer(self, question: str, typed: bool):
        t0 = self.clock()
        item = {"id": self._next_id, "t": round(t0, 1), "question": question, "typed": typed,
                "answer": "", "tools": [], "error": "", "took_s": None, "line_id": None}
        with self._lock:
            self._next_id += 1
            self.items.appendleft(item)
        try:
            if not self.ai_enabled:
                raise RuntimeError("kein API-Schlüssel (commentary_config.json api_key)")
            text, tools = self.ask_claude(question)
            item["tools"] = tools
            item["answer"] = text or ""
            self.last_error = ""
        except Exception as e:
            item["error"] = str(e)[:400]
            self.last_error = item["error"]
        item["took_s"] = round(self.clock() - t0, 1)
        self._log("answer", item)
        if item["answer"] and self.speak is not None:
            if self.cfg.get("qa_hold"):
                with self._lock:                 # wait for the streamer's key
                    self._pending = item
                    self._ready_at = self.clock()
                    self.state = "ready"
                return
            self._speak(item)
        with self._lock:
            self.state = "idle"

    # -- tools ----------------------------------------------------------------
    def _src(self, key: str) -> str:
        return (self.cfg.get("qa_sources") or {}).get(key) or DEFAULT_SOURCES[key]

    def run_tool(self, name: str, args: dict | None = None) -> str:
        if name == "live_session":
            return _dumps(compact(self.session_fn() or {}))
        if name == "track_notes":
            from show_runner import find_notes
            s = self.session_fn() or {}
            notes = find_notes(self.notes_dir, s.get("strecke") or "", s.get("layout") or "")
            return notes or "Keine Notizen zu dieser Strecke vorhanden."
        args = args or {}
        if name == "cls_leagues":
            return _dumps(compact(self.fetch(self._cls("leagues"))))
        if name in ("round_info", "league_table") and (args.get("liga") or args.get("runde")):
            return self._cls_round(name, args)
        key = {"live_standings": "standings", "race_results": "results",
               "championship": "championship", "round_info": "roundinfo",
               "league_table": "roundinfo"}.get(name)
        if key is None:
            raise ValueError(f"unbekanntes Werkzeug {name}")
        data = self.fetch(self._src(key))
        # 5010 /api/roundinfo = {"info": last round …, "standings": table before it};
        # one tool each, so neither half is cut off
        if name == "round_info" and isinstance(data, dict) and "info" in data:
            data = data["info"]
        elif name == "league_table" and isinstance(data, dict):
            data = {k: v for k, v in (data.get("standings") or {}).items() if k != "rounds"}
        return _dumps(compact(data))

    def _cls(self, path: str, **params) -> str:
        base = (self.cfg.get("qa_cls_url") or DEFAULTS["qa_cls_url"]).rstrip("/")
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        return f"{base}/api/overlay/{path}" + (f"?{q}" if q else "")

    def _cls_round(self, name: str, args: dict) -> str:
        """round_info / league_table for ANY league (and round), straight from CLS."""
        slug = str(args.get("liga") or "").strip()
        if not slug:                    # only a round given: the league 5010 is on
            info = (self.fetch(self._src("roundinfo")) or {}).get("info") or {}
            slug = (info.get("league") or {}).get("slug") or ""
            if not slug:
                raise ValueError("keine Liga bekannt — liga angeben (cls_leagues)")
        before = None
        if args.get("runde") not in (None, ""):
            before = int(args["runde"]) + 1    # CLS: data as it stood BEFORE that round
        if name == "round_info":
            data = self.fetch(self._cls("round-info", league=slug, beforeRound=before))
        else:
            data = self.fetch(self._cls("standings", league=slug, beforeRound=before))
            if isinstance(data, dict):
                data = {k: v for k, v in data.items() if k != "rounds"}
        return _dumps(compact(data))

    def _tools(self) -> list:
        tools = [dict(t) for t in TOOLS]
        if self.cfg.get("qa_web_search"):
            model = self.cfg.get("qa_model") or ""
            # the dynamic-filtering variant needs Opus / Sonnet; Haiku takes the basic one
            typ = "web_search_20250305" if "haiku" in model else "web_search_20260209"
            tools.append({"type": typ, "name": "web_search", "max_uses": 3})
        return tools

    def ask_claude(self, question: str) -> tuple[str, list]:
        today = date.today().strftime("%d.%m.%Y")
        messages = [{"role": "user", "content": f"(Heute ist der {today}.) Frage: {question}"}]
        used: list = []
        for _ in range(MAX_ROUNDS):
            data = self._call(messages)
            content = data.get("content") or []
            for b in content:
                if b.get("type") == "server_tool_use":
                    used.append(b.get("name") or "web_search")
            stop = data.get("stop_reason")
            if stop == "tool_use":
                results = []
                for b in content:
                    if b.get("type") != "tool_use":
                        continue
                    used.append(b.get("name"))
                    try:
                        out, err = self.run_tool(b.get("name"), b.get("input") or {}), False
                    except Exception as e:      # an overlay that is not running
                        out, err = f"Quelle nicht erreichbar: {e}", True
                    results.append({"type": "tool_result", "tool_use_id": b.get("id"),
                                    "content": out, **({"is_error": True} if err else {})})
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": results})
                continue
            if stop == "pause_turn":            # a long server-side web search: continue it
                messages.append({"role": "assistant", "content": content})
                continue
            if stop == "refusal":
                raise RuntimeError("Claude hat die Antwort abgelehnt")
            text = final_text(content)
            if text and looks_english(text):    # an English answer would be read by a German voice
                text = self._to_german(text) or text
                used.append("übersetzt")
            return text.strip('"„“'), used
        raise RuntimeError("zu viele Werkzeug-Runden")

    def _to_german(self, text: str) -> str | None:
        """One extra call without tools: the same answer, in German."""
        payload = {"model": self.cfg.get("qa_model") or DEFAULTS["qa_model"],
                   "max_tokens": 600, "system": GERMAN_PROMPT,
                   "messages": [{"role": "user", "content": text}]}
        if self._thinking_off_ok:
            payload["thinking"] = {"type": "disabled"}
        try:
            data = self._api_post(payload)
        except Exception:
            return None
        out = final_text(data.get("content") or [])
        return out if out and not looks_english(out) else None

    # -- API ------------------------------------------------------------------
    def _call(self, messages: list) -> dict:
        payload = {"model": self.cfg.get("qa_model") or DEFAULTS["qa_model"],
                   "max_tokens": 1500, "system": SYSTEM_PROMPT,
                   "tools": self._tools(), "messages": messages}
        # same as race_commentator: thinking off for a fast answer; a model that
        # rejects the switch runs without it (and gets more room)
        if self._thinking_off_ok:
            payload["thinking"] = {"type": "disabled"}
        else:
            payload["max_tokens"] = 6000
        try:
            return self._api_post(payload)
        except RuntimeError as e:
            if self._thinking_off_ok and "HTTP 400" in str(e) and "thinking" in str(e).lower():
                self._thinking_off_ok = False
                payload.pop("thinking", None)
                payload["max_tokens"] = 6000
                return self._api_post(payload)
            raise

    def _post(self, payload: dict) -> dict:
        headers = {"content-type": "application/json", "x-api-key": self.cfg["api_key"],
                   "anthropic-version": "2023-06-01"}
        if self.cfg.get("workspace_id"):
            headers["anthropic-workspace-id"] = self.cfg["workspace_id"]
        req = urllib.request.Request(API_URL, data=json.dumps(payload).encode("utf-8"),
                                     method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=float(self.cfg.get("qa_timeout_s") or 25)) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
            except Exception:
                msg = ""
            raise RuntimeError(f"HTTP {e.code}: {msg or e.reason}") from None
