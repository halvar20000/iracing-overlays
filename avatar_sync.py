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
# paid videos per day at most (Thomas 2026-11-19: the intro was ordered again
# and again until the credits were gone); show_config avatar.max_per_day
MAX_PER_DAY = 3
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
    part = out.with_suffix(".part")          # a broken download never looks like a finished video
    with requests.get(g["outputUrl"], stream=True, timeout=120) as d:
        d.raise_for_status()
        with open(part, "wb") as f:
            for chunk in d.iter_content(1 << 16):
                f.write(chunk)
    os.replace(part, out)
    say(f"fertig: {out.name} ({int(time.time() - t0)} s)")
    return out


class AvatarRenderer:
    """Gabi's talking videos for the show, made ahead in the background.

    ensure(kind, seg, text) starts a render unless one for exactly this text
    is ready / running; get(kind, seg, text) -> Path of a finished video for
    this text or None (then the show speaks the segment with the voice only).
    One render at a time (sync.so takes ~2 min per 10 s of video).

    Videos are KEPT and reused: avatar/out/<kind>_<seg>_<hash of the text>.mp4.
    Before ordering, ensure() / get() look for that file — a dashboard restart
    or the same text again costs nothing (Thomas, 2026-10-21: the paid intro
    was ordered again after every update). Delete the file to force a new one."""

    def __init__(self, voice, lipsync_fn=None, out_dir=None):
        self.voice = voice
        self.lipsync = lipsync_fn or lipsync
        self.out_dir = Path(out_dir) if out_dir else OUT_DIR
        self.jobs: dict = {}            # (kind, seg) -> {text, state, file, error, at}
        self.max_per_day = MAX_PER_DAY
        self._q: list = []
        self._lock = threading.Lock()
        self._worker = None

    @staticmethod
    def _tag(text: str) -> str:
        import hashlib
        return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:10]

    def _saved(self, kind: str, seg: str, text: str) -> Path | None:
        """The video made earlier for exactly this text, if it is on disk."""
        p = self.out_dir / f"{kind}_{seg}_{self._tag(text)}.mp4"
        try:
            return p if p.is_file() and p.stat().st_size > 0 else None
        except OSError:
            return None

    def _adopt(self, kind, seg, text, path):
        self.jobs[(kind, seg)] = {"text": text, "state": "ready", "file": str(path), "error": "",
                                  "at": time.time(), "saved": True}

    # -- videos chosen by hand on /show (Thomas 2026-11-19: no credits left, the
    # videos are all there already) -------------------------------------------
    def _picks_file(self) -> Path:
        return self.out_dir / "picks.json"

    def picks(self) -> dict:
        try:
            d = json.loads(self._picks_file().read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def picked(self, kind: str, seg: str) -> Path | None:
        name = self.picks().get(f"{kind}:{seg}")
        if not name:
            return None
        p = self.out_dir / Path(name).name
        try:
            return p if p.is_file() and p.stat().st_size > 0 else None
        except OSError:
            return None

    def pick(self, kind: str, seg: str, name: str = "") -> str | None:
        """Use this file of avatar/out for the segment, whatever the text ("" =
        automatic again). -> error text or None."""
        name = Path(name or "").name
        if name and not (self.out_dir / name).is_file():
            return f"{name} nicht in {self.out_dir}"
        d = self.picks()
        if name:
            d[f"{kind}:{seg}"] = name
        else:
            d.pop(f"{kind}:{seg}", None)
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._picks_file().with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._picks_file())
        except OSError as e:
            return str(e)
        with self._lock:
            j = self.jobs.get((kind, seg))
            if name:
                self.jobs[(kind, seg)] = {"text": (j or {}).get("text", ""), "state": "ready",
                                          "file": str(self.out_dir / name), "error": "", "at": time.time(),
                                          "saved": True, "picked": True}
            elif j and j.get("picked"):
                self.jobs.pop((kind, seg), None)
        return None

    def list_videos(self, kind: str, seg: str, others: bool = False) -> list:
        """The finished videos for this segment in avatar/out, newest first
        (text known only for videos made since 2026-11-19). others=True: then
        every other Gabi video too (`own` False) — the highlight video can use
        a greeting / goodbye made for the show (Thomas 2026-11-19)."""
        out = []
        own = f"{kind}_{seg}_"
        try:
            files = list(self.out_dir.glob("*.mp4" if others else f"{own}*.mp4"))
        except OSError:
            return out
        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size <= 0:
                continue
            text, topic = "", ""
            try:
                m = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
                text, topic = m.get("text") or "", m.get("topic") or ""
            except (OSError, ValueError):
                pass
            out.append({"file": p.name, "at": st.st_mtime, "kb": st.st_size // 1024,
                        "text": text, "topic": topic, "own": p.name.startswith(own)})
        out.sort(key=lambda x: (not x["own"], -x["at"]))
        return out

    # -- reuse + daily limit ---------------------------------------------------
    def reuse_text(self, kind: str, seg: str, topic: str) -> str | None:
        """The text of the newest video already made for this kind / segment /
        topic (league:round:track) — the show takes it word for word instead of
        a new script line, so a rewritten script orders nothing."""
        if not topic:
            return None
        best, at = None, -1.0
        try:
            metas = list(self.out_dir.glob(f"{kind}_{seg}_*.json"))
        except OSError:
            return None
        for m in metas:
            try:
                d = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if d.get("topic") != topic or not d.get("text"):
                continue
            if self._saved(kind, seg, d["text"]) is None:
                continue
            if float(d.get("at") or 0) > at:
                best, at = d["text"], float(d.get("at") or 0)
        return best

    def _orders_file(self) -> Path:
        return self.out_dir / "orders.json"

    def orders_today(self) -> int:
        try:
            d = json.loads(self._orders_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        return int(d.get(time.strftime("%Y-%m-%d")) or 0)

    def _count_order(self):
        day = time.strftime("%Y-%m-%d")
        try:
            d = json.loads(self._orders_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        d = {k: v for k, v in d.items() if k >= time.strftime("%Y-%m-%d", time.localtime(time.time() - 30 * 86400))}
        d[day] = int(d.get(day) or 0) + 1
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._orders_file().with_suffix(".tmp")
            tmp.write_text(json.dumps(d), encoding="utf-8")
            os.replace(tmp, self._orders_file())
        except OSError:
            pass

    def _write_meta(self, out, kind, seg, text, topic):
        try:
            Path(out).with_suffix(".json").write_text(json.dumps(
                {"kind": kind, "seg": seg, "text": text, "topic": topic, "at": time.time()},
                ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def ensure(self, kind: str, seg: str, text: str, topic: str = ""):
        text = (text or "").strip()
        if not text:
            return
        key = (kind, seg)
        chosen = self.picked(kind, seg)
        with self._lock:
            if chosen is not None:                # chosen by hand: that one, never an order
                self.jobs[key] = {"text": text, "state": "ready", "file": str(chosen), "error": "",
                                  "at": time.time(), "saved": True, "picked": True}
                return
            j = self.jobs.get(key)
            if j and j["text"] == text and j["state"] in ("queued", "rendering", "ready"):
                return
            if j and j["text"] == text and j["state"] == "failed" and j.get("no_retry"):
                return                            # out of credits / daily limit: not again and again
            saved = self._saved(kind, seg, text)
            if saved is not None:                 # paid for already: no new order
                self._adopt(kind, seg, text, saved)
                return
            self.jobs[key] = {"text": text, "state": "queued", "file": None, "error": "", "at": time.time(),
                              "topic": topic}
            self._q.append(key)
            if not (self._worker and self._worker.is_alive()):
                self._worker = threading.Thread(target=self._run, daemon=True, name="AvatarRender")
                self._worker.start()

    def available(self) -> bool:
        return bool(api_key())

    def get(self, kind: str, seg: str, text: str) -> Path | None:
        chosen = self.picked(kind, seg)
        if chosen is not None:
            return chosen
        text = (text or "").strip()
        j = self.jobs.get((kind, seg))
        if j and j["state"] == "ready" and j["text"] == text and j["file"] and Path(j["file"]).exists():
            return Path(j["file"])
        if text and not (j and j["text"] == text and j["state"] in ("queued", "rendering")):
            saved = self._saved(kind, seg, text)
            if saved is not None:
                with self._lock:
                    self._adopt(kind, seg, text, saved)
                return saved
        return None

    def status(self, kind: str) -> dict:
        st = {seg: {"state": j["state"], "error": j["error"], "file": Path(j["file"]).name if j["file"] else None,
                    "saved": bool(j.get("saved"))}
              for (k, seg), j in list(self.jobs.items()) if k == kind}
        for key, name in self.picks().items():
            k, _, seg = key.partition(":")
            if k == kind:
                st.setdefault(seg, {"state": "ready", "error": "", "file": name, "saved": True})["pick"] = name
        return st

    def _run(self):
        while True:
            with self._lock:
                if not self._q:
                    return
                key = self._q.pop(0)
                j = self.jobs.get(key)
                if not j or j["state"] != "queued":
                    continue
                limit = int(self.max_per_day or 0)
                if limit > 0 and self.orders_today() >= limit:
                    j["state"], j["no_retry"] = "failed", True
                    j["error"] = (f"Tageslimit: heute schon {limit} Gabi-Videos bestellt "
                                  "(show_config avatar.max_per_day) – nur Stimme")
                    continue
                j["state"], text = "rendering", j["text"]
            try:
                data, mime, _ = self.voice.synth(text)
                name = f"{key[0]}_{key[1]}_{self._tag(text)}"
                out = self.lipsync(data, "a.mp3" if "mpeg" in mime else "a.wav", out_name=name)
                self._count_order()
                self._write_meta(out, key[0], key[1], text, j.get("topic") or "")
                with self._lock:
                    if self.jobs.get(key) is j:          # text not changed meanwhile
                        j["state"], j["file"] = "ready", str(out)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                with self._lock:
                    if self.jobs.get(key) is j:
                        j["state"], j["error"] = "failed", msg
                        # no credits: never ordered again for this text (would only fail again)
                        j["no_retry"] = "402" in msg or "insufficient_credits" in msg


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
