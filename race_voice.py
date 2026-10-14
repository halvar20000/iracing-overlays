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
import queue
import tempfile
import threading
import time
from collections import OrderedDict

from race_commentator import load_config

DEFAULT_VOICE = "de-DE-ConradNeural"
DEFAULT_RATE = "+10%"
KEEP_CLIPS = 12
LISTEN_S = 15.0          # a voice page counts as listening this long after its last poll
EDGE_TIMEOUT_S = 8.0
EDGE_RETRY_S = 60.0      # after an edge failure, use SAPI for this long before trying again
MAX_AGE_S = 12.0         # a line still waiting this long in the queue is not spoken any more


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
        self.last_error = ""
        self.last_engine = ""

    @property
    def mode(self) -> str:
        return (self.cfg.get("tts") or "auto").lower()

    def listening(self, now: float | None = None) -> None:
        """A voice page polled — keep making audio for the next LISTEN_S."""
        self._listen_until = (time.time() if now is None else now) + LISTEN_S

    def say(self, line_id, text: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if self.mode == "off" or not text or now > self._listen_until:
            return
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()
        self._q.put((now, line_id, text))

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
                "voice": self.cfg.get("tts_voice") or DEFAULT_VOICE}

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
            item = self._q.get()
            while not self._q.empty():          # only the newest line matters
                item = self._q.get_nowait()
            t0, line_id, text = item
            if time.time() - t0 > MAX_AGE_S:
                continue
            try:
                data, mime, engine = self.synth(text)
                self.last_engine, self.last_error = engine, ""
                with self._lock:
                    self._clips[line_id] = (data, mime, engine)
                    while len(self._clips) > KEEP_CLIPS:
                        self._clips.popitem(last=False)
            except Exception as e:
                self.last_error = str(e)[:400]
