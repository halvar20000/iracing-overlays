"""
AVATAR — the moderator as a lip-synced video (sync.so), 2026-10-16.

Gabi Ferrari (an AI persona Andreas made in Photoshop / Firefly, avatar/
gabi_*.mp4) speaks our moderation text: the voice line we already make
(race_voice / edge-tts) + her calm studio clip go to sync.so, which moves the
mouth to the audio and returns an MP4 (short clips are played forward /
backward to the length of the audio — sync_mode "bounce").

Key: commentary_config.json "sync_api_key" (gitignored, PROTECTED — never in
the repo or a chat) or env SYNC_API_KEY. Model: "sync_model" (default
lipsync-2-pro; Hobby plan ~36 s per 300 credits).

Test from the dashboard: http://localhost:5000/avatar (one sentence -> video).
CLI: python avatar_sync.py "Hallo und herzlich willkommen …"
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
AVATAR_DIR = HERE / "avatar"
OUT_DIR = AVATAR_DIR / "out"                  # gitignored: generated videos
API = "https://api.sync.so/v2"
DEFAULT_CLIP = AVATAR_DIR / "gabi_speaking.mp4"   # a SPEAKING base: a still one (mouth closed) barely moved the lips
IDLE_LOOP = AVATAR_DIR / "gabi_idle_loop.mp4"        # gabi_still.mp4 forward + backward: between sentences, no credits
DEFAULT_MODEL = "lipsync-2-pro"


def _cfg() -> dict:
    try:
        return json.loads((HERE / "commentary_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def api_key() -> str:
    return (_cfg().get("sync_api_key") or os.environ.get("SYNC_API_KEY") or "").strip()


def lipsync(audio: bytes, audio_name: str, clip: Path = DEFAULT_CLIP, model: str | None = None,
            out_name: str = "avatar", status=None, timeout_s: float = 600) -> Path:
    """Audio + clip -> lip-synced MP4 in avatar/out/. Blocks until done."""
    import requests
    key = api_key()
    if not key:
        raise RuntimeError("kein sync.so-Schlüssel (commentary_config.json \"sync_api_key\")")
    model = model or _cfg().get("sync_model") or DEFAULT_MODEL
    say = status or (lambda s: None)
    say("lade hoch …")
    with open(clip, "rb") as v:
        r = requests.post(f"{API}/generate", headers={"x-api-key": key}, timeout=120,
                          files={"video": (clip.name, v, "video/mp4"),
                                 "audio": (audio_name, audio, "audio/mpeg" if audio_name.endswith(".mp3")
                                           else "audio/wav")},
                          data={"model": model, "options": json.dumps({"sync_mode": "bounce"}),
                                "outputFileName": out_name})
    if r.status_code >= 300:
        raise RuntimeError(f"sync.so {r.status_code}: {r.text[:300]}")
    gid = r.json().get("id")
    t0 = time.time()
    while True:
        time.sleep(3)
        g = requests.get(f"{API}/generate/{gid}", headers={"x-api-key": key}, timeout=30).json()
        st = g.get("status")
        say(f"{st} ({int(time.time() - t0)} s)")
        if st == "COMPLETED" and g.get("outputUrl"):
            break
        if st in ("FAILED", "REJECTED"):
            raise RuntimeError(f"sync.so {st}: {g.get('error') or g.get('errorCode')}")
        if time.time() - t0 > timeout_s:
            raise RuntimeError("sync.so: Zeitüberschreitung")
    say("lade Ergebnis …")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{out_name}.mp4"
    with requests.get(g["outputUrl"], stream=True, timeout=120) as d:
        d.raise_for_status()
        with open(out, "wb") as f:
            for chunk in d.iter_content(1 << 16):
                f.write(chunk)
    say(f"fertig: {out.name} ({int(time.time() - t0)} s)")
    return out


class AvatarRenderer:
    """Gabi's talking videos for the show, made ahead in the background.

    ensure(kind, seg, text) starts a render unless one for exactly this text
    is ready / running; get(kind, seg, text) -> Path of a finished video for
    this text or None (then the show speaks the segment with the voice only).
    One render at a time (sync.so takes ~2 min per 10 s of video)."""

    def __init__(self, voice, lipsync_fn=None):
        self.voice = voice
        self.lipsync = lipsync_fn or lipsync
        self.jobs: dict = {}            # (kind, seg) -> {text, state, file, error, at}
        self._q: list = []
        self._lock = threading.Lock()
        self._worker = None

    @staticmethod
    def _tag(text: str) -> str:
        import hashlib
        return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:10]

    def ensure(self, kind: str, seg: str, text: str):
        text = (text or "").strip()
        if not text:
            return
        key = (kind, seg)
        with self._lock:
            j = self.jobs.get(key)
            if j and j["text"] == text and j["state"] in ("queued", "rendering", "ready"):
                return
            self.jobs[key] = {"text": text, "state": "queued", "file": None, "error": "", "at": time.time()}
            self._q.append(key)
            if not (self._worker and self._worker.is_alive()):
                self._worker = threading.Thread(target=self._run, daemon=True, name="AvatarRender")
                self._worker.start()

    def available(self) -> bool:
        return bool(api_key())

    def get(self, kind: str, seg: str, text: str) -> Path | None:
        j = self.jobs.get((kind, seg))
        if j and j["state"] == "ready" and j["text"] == (text or "").strip() and j["file"] and Path(j["file"]).exists():
            return Path(j["file"])
        return None

    def status(self, kind: str) -> dict:
        return {seg: {"state": j["state"], "error": j["error"], "file": Path(j["file"]).name if j["file"] else None}
                for (k, seg), j in list(self.jobs.items()) if k == kind}

    def _run(self):
        while True:
            with self._lock:
                if not self._q:
                    return
                key = self._q.pop(0)
                j = self.jobs.get(key)
                if not j or j["state"] != "queued":
                    continue
                j["state"], text = "rendering", j["text"]
            try:
                data, mime, _ = self.voice.synth(text)
                name = f"{key[0]}_{key[1]}_{self._tag(text)}"
                out = self.lipsync(data, "a.mp3" if "mpeg" in mime else "a.wav", out_name=name)
                with self._lock:
                    if self.jobs.get(key) is j:          # text not changed meanwhile
                        j["state"], j["file"] = "ready", str(out)
            except Exception as e:
                with self._lock:
                    if self.jobs.get(key) is j:
                        j["state"], j["error"] = "failed", f"{type(e).__name__}: {e}"


class AvatarTest:
    """One sentence -> voice -> lip-synced video, for /avatar in the dashboard."""

    def __init__(self, voice):
        self.voice = voice                      # race_voice.Voice (makes the audio)
        self.state, self.msg, self.file, self.text = "idle", "", None, ""

    def start(self, text: str):
        if self.state == "running":
            return False
        self.state, self.msg, self.file, self.text = "running", "Stimme …", None, text
        threading.Thread(target=self._run, args=(text,), daemon=True).start()
        return True

    def _run(self, text):
        try:
            data, mime, engine = self.voice.synth(text)
            name = "test.mp3" if "mpeg" in mime else "test.wav"
            self.file = lipsync(data, name, out_name=f"test_{int(time.time())}",
                                status=lambda s: setattr(self, "msg", s))
            self.state = "done"
        except Exception as e:
            self.state, self.msg = "error", f"{type(e).__name__}: {e}"

    def status(self) -> dict:
        return {"state": self.state, "msg": self.msg, "text": self.text,
                "file": self.file.name if self.file else None, "key": bool(api_key())}


if __name__ == "__main__":
    import race_voice
    text = " ".join(sys.argv[1:]) or "Hallo und herzlich willkommen bei Kas!"
    v = race_voice.Voice()
    data, mime, _ = v.synth(text)
    print(lipsync(data, "t.mp3" if "mpeg" in mime else "t.wav", status=print, out_name="cli_test"))
