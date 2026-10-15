"""
Race voice ("Stimme") — step 3 of the AI broadcast idea: the KOMMENTAR lines
(race_commentator.py) as SPOKEN audio for OBS.

The dashboard hands every new commentary line to `Voice.say(id, text)`; a
background worker turns it into audio and keeps the last few clips in memory.
The OBS page `/commentary/voice` (browser source with "Control audio via
OBS") polls `/commentary`, and plays each clip once it is ready — so the voice
is its own channel in the OBS audio mixer (volume, mute, filters, delay).

Engines (commentary_config.json "tts", default "auto"):
  • "edge"  — Microsoft's neural voices (the Edge read-aloud service, via the
              `edge-tts` package): natural German, free, needs internet. Not an
              official API — if it breaks, auto falls back to SAPI.
  • "sapi"  — the voice built into Windows (SAPI5 via pywin32, offline; on a
              German Windows usually "Microsoft Hedda"). Sounds robotic.
  • "auto"  — edge, else sapi.   "off" — no audio at all.
Other keys: "tts_voice" (edge voice, default de-DE-ConradNeural; e.g.
de-DE-KatjaNeural, de-DE-KillianNeural, de-DE-FlorianMultilingualNeural),
"tts_rate" ("+10%"), "sapi_voice" (part of the SAPI voice name).

Audio is only made while a voice page is listening (it polls with ?voice=1),
so nothing is sent anywhere when the voice source is not in OBS.

Pure logic; the engines are imported lazily. Tested by test_race_voice.py.
"""

from __future__ import annotations

import asyncio
import os
import re
import queue
import tempfile
import threading
import time
from collections import OrderedDict

from race_commentator import load_config

DEFAULT_VOICE = "de-DE-ConradNeural"
DEFAULT_RATE = "+10%"
KEEP_CLIPS = 40
LISTEN_S = 15.0          # a voice page counts as listening this long after its last poll
EDGE_TIMEOUT_S = 8.0
EDGE_RETRY_S = 60.0      # after an edge failure, use SAPI for this long before trying again
MAX_AGE_S = 12.0         # a line still waiting this long in the queue is not spoken any more
KEEP_MAX_AGE_S = 300.0   # ...a moderation line (keep=True) waits much longer


def _edge_synth(text: str, voice: str, rate: str) -> tuple[bytes, str]:
    import edge_tts                      # pip install edge-tts

    async def run():
        data = bytearray()
        async for ch in edge_tts.Communicate(text, voice, rate=rate).stream():
            if ch.get("type") == "audio":
                data += ch["data"]
        return bytes(data)

    data = asyncio.run(asyncio.wait_for(run(), EDGE_TIMEOUT_S))
    if not data:
        raise RuntimeError("edge-tts: keine Audiodaten")
    return data, "audio/mpeg"


def _sapi_rate(rate: str) -> int:
    """'+10%' -> 1 (SAPI rate is -10..10)."""
    try:
        return max(-10, min(10, round(int(str(rate).strip().rstrip("%")) / 10)))
    except ValueError:
        return 0


def _sapi_synth(text: str, want: str, rate: str) -> tuple[bytes, str]:
    import pythoncom                     # pywin32, Windows only
    import win32com.client
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    pythoncom.CoInitialize()
    try:
        v = win32com.client.Dispatch("SAPI.SpVoice")
        voices, pick = v.GetVoices(), None
        for i in range(voices.Count):
            tok = voices.Item(i)
            desc = tok.GetDescription() or ""
            if want and want.lower() in desc.lower():
                pick = tok
                break
            if pick is None and not want:
                langs = (tok.GetAttribute("Language") or "").lower().split(";")
                if "407" in langs or "c07" in langs or "807" in langs:   # de-DE / de-AT / de-CH
                    pick = tok
        if pick is not None:
            v.Voice = pick
        v.Rate = _sapi_rate(rate)
        st = win32com.client.Dispatch("SAPI.SpFileStream")
        st.Open(path, 3, False)          # SSFMCreateForWrite
        v.AudioOutputStream = st
        v.Speak(text, 0)
        st.Close()
        with open(path, "rb") as fh:
            data = fh.read()
    finally:
        pythoncom.CoUninitialize()
        try:
            os.remove(path)
        except OSError:
            pass
    if len(data) < 100:
        raise RuntimeError("SAPI: keine Audiodaten")
    return data, "audio/wav"


_LAP = re.compile(r"\b(\d{1,2}):(\d{2})[,.](\d{1,3})\b")
_DEC = re.compile(r"\b(\d+),(\d+)\b")


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


_ONES = ["null", "eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun",
         "zehn", "elf", "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn",
         "achtzehn", "neunzehn"]
_TENS = {2: "zwanzig", 3: "dreißig", 4: "vierzig", 5: "fünfzig", 6: "sechzig", 7: "siebzig",
         8: "achtzig", 9: "neunzig"}


def _de(n: int) -> str:
    """0..99 as a German word — the voice then cannot read the digits its own way."""
    if n < 20:
        return _ONES[n]
    t, o = divmod(n, 10)
    return _TENS[t] if not o else ("ein" if o == 1 else _ONES[o]) + "und" + _TENS[t]


def _two(n: int) -> str:
    return f"null {_ONES[n]}" if n < 10 else _de(n)


def _say_lap(m) -> str:
    """1:49,300 -> 'eins 49 30' = "eins neunundvierzig dreißig" (minutes,
    seconds, hundredths — how a commentator reads a lap time)."""
    mins, secs = int(m.group(1)), int(m.group(2))
    hund = min(99, round(int(m.group(3).ljust(3, "0")) / 10))
    return f"{_de(mins)} {_two(secs)} {_two(hund)}"


PRONOUNCE = {"CAS": "Kas"}


def speakable(text: str, extra: dict | None = None) -> str:
    """Numbers the way a commentator says them: lap time 1:39,512 ->
    'eins 39 51' = "eins neununddreißig einundfünfzig" (not 'ein Uhr
    neununddreißig'), 0,44 -> '0 Komma 4 4',
    the class AM -> 'A M', PRO -> 'Pro'."""
    text = _LAP.sub(_say_lap, text)
    text = _DEC.sub(lambda m: f"{m.group(1)} Komma {' '.join(m.group(2))}", text)
    # Pro/Am classes: "AM" spelled A M (not the German word "am"), "PRO" as the word Pro
    text = re.sub(r"\bAM\b", "A M", text)
    text = re.sub(r"\bPRO\b", "Pro", text)
    # "Pole" (position) is English: "Pol", not the German "Pole" — also in
    # Pole-Position / Polesitter / Pole-Zeit; "Polen" stays
    text = re.sub(r"\b([Pp])ole(?!n)", r"\1ol", text)
    # names said as a word, not spelled (CAS = "Kas"); more / other in
    # commentary_config.json "aussprache": {"CAS": "Kas", ...}
    for word, say in dict(PRONOUNCE, **(extra or {})).items():
        text = re.sub(rf"\b{re.escape(word)}\b", say, text)
    return text


class Voice:
    def __init__(self, config: dict | None = None, engines: dict | None = None):
        self.cfg = config if config is not None else load_config()
        self.engines = engines or {"edge": _edge_synth, "sapi": _sapi_synth}
        self._clips: OrderedDict = OrderedDict()      # line id -> (bytes, mime, engine)
        self._lock = threading.Lock()
        self._q: queue.Queue = queue.Queue()
        self._worker = None
        self._listen_until = 0.0
        self._edge_failed_at = None
        self.requested: set = set()                   # line ids handed to the worker
        self.last_error = ""
        self.last_engine = ""

    @property
    def mode(self) -> str:
        return (self.cfg.get("tts") or "auto").lower()

    def listening(self, now: float | None = None) -> None:
        """A voice page polled — keep making audio for the next LISTEN_S."""
        self._listen_until = (time.time() if now is None else now) + LISTEN_S

    def say(self, line_id, text: str, now: float | None = None, keep: bool = False) -> None:
        """keep=True (moderation): made in order, never dropped for a newer line."""
        now = time.time() if now is None else now
        if self.mode == "off" or not text or now > self._listen_until:
            return
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()
        self.requested.add(line_id)
        if len(self.requested) > 500:
            self.requested = set(sorted(self.requested)[-200:])
        self._q.put((now, line_id, text, keep))

    def get(self, line_id):
        with self._lock:
            c = self._clips.get(line_id)
        return (c[0], c[1]) if c else None

    def has(self, line_id) -> bool:
        with self._lock:
            return line_id in self._clips

    def status(self) -> dict:
        return {"mode": self.mode, "listening": time.time() <= self._listen_until,
                "engine": self.last_engine, "last_error": self.last_error,
                "voice": self.cfg.get("tts_voice") or DEFAULT_VOICE,
                "mouth_gain": _num(self.cfg.get("mouth_gain"), 1.0)}

    # -- internals ------------------------------------------------------------
    def _order(self, now: float) -> list:
        m = self.mode
        if m == "edge":
            return ["edge"]
        if m == "sapi":
            return ["sapi"]
        if self._edge_failed_at is not None and now - self._edge_failed_at < EDGE_RETRY_S:
            return ["sapi", "edge"]
        return ["edge", "sapi"]

    def synth(self, text: str, now: float | None = None):
        """-> (bytes, mime, engine); raises when every engine failed."""
        now = time.time() if now is None else now
        text = speakable(text, self.cfg.get("aussprache"))
        errors = []
        for name in self._order(now):
            fn = self.engines.get(name)
            if fn is None:
                continue
            try:
                if name == "edge":
                    data, mime = fn(text, self.cfg.get("tts_voice") or DEFAULT_VOICE,
                                    self.cfg.get("tts_rate") or DEFAULT_RATE)
                    self._edge_failed_at = None
                else:
                    data, mime = fn(text, self.cfg.get("sapi_voice") or "",
                                    self.cfg.get("tts_rate") or DEFAULT_RATE)
                return data, mime, name
            except Exception as e:            # missing package, no internet, no voice …
                if name == "edge":
                    self._edge_failed_at = now
                errors.append(f"{name}: {type(e).__name__}: {e}"[:200])
        raise RuntimeError(" | ".join(errors) or "keine Sprachausgabe verfügbar")

    def _run(self):
        while True:
            items = [self._q.get()]
            while not self._q.empty():
                items.append(self._q.get_nowait())
            # moderation lines all, in order; of the commentary only the newest
            last_plain = max((i for i, it in enumerate(items) if not it[3]), default=None)
            for i, (t0, line_id, text, keep) in enumerate(items):
                if not keep and i != last_plain:
                    continue
                if time.time() - t0 > (KEEP_MAX_AGE_S if keep else MAX_AGE_S):
                    continue
                self._make(line_id, text)

    def _make(self, line_id, text):
        try:
            data, mime, engine = self.synth(text)
            self.last_engine, self.last_error = engine, ""
            with self._lock:
                self._clips[line_id] = (data, mime, engine)
                while len(self._clips) > KEEP_CLIPS:
                    self._clips.popitem(last=False)
        except Exception as e:
            self.last_error = str(e)[:400]
