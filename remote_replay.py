"""
Replay on a SECOND PC (2026-11-08, Thomas: the stream PC stays live, the sim
rig spectates the same session with a second iRacing account and plays the
replays; its picture comes into OBS over NDI / DistroAV).

Stream PC side (`RemoteReplay`): replay_pc_config.json
    {"url": "http://192.168.86.50:5000", "scene": "Replay", "back_scene": ""}
url set = every dashboard replay (feed buttons, Stream Deck replay_last*,
auto-replay; REGIE keeps its own local replays) is sent to the sim rig's
dashboard (POST /replay/remote — same car, session number and SessionTime:
every PC in a hosted session shares them), OBS switches to `scene`, and back
to the scene that was on before (or `back_scene`) as soon as the sim rig
reports it is live again (GET /replay/remote/status) — or after the replay
length + `extra_s` if it never answers. A scene the operator changed in the
meantime is left alone. Sim rig not reachable -> local replay as before
(`fallback_local`, default on). The file is re-read when it changes (no
restart). OBS connection = show_config.json "obs".

Sim rig side: the same dashboard; /replay/remote plays it with the normal
replay_5s_of_car (pause, seek, camera, 1x, back to live) — see the routes in
iracing_dashboard.py.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "replay_pc_config.json"
DEFAULTS = {"url": "", "scene": "Replay", "back_scene": "", "fallback_local": True,
            "timeout_s": 3.0, "extra_s": 20.0, "poll_s": 0.5}


def normalize_url(url: str) -> str:
    """'192.168.86.50' -> 'http://192.168.86.50:5000' (no trailing slash)."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    p = urlsplit(url)
    netloc = p.netloc if p.port else f"{p.hostname}:5000"
    return f"{p.scheme}://{netloc}{p.path}".rstrip("/")


def _http_json(method: str, url: str, body: dict | None, timeout: float) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


class RemoteReplay:
    def __init__(self, obs_factory=None, http=_http_json, config_file: Path = CONFIG_FILE,
                 clock=time.monotonic, sleep=time.sleep):
        self._obs_factory = obs_factory      # () -> ObsClient (lazy: OBS may be down at start)
        self._obs = None
        self._http = http
        self._file = Path(config_file)
        self._mtime = None
        self.cfg = dict(DEFAULTS)
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._gen = 0                        # newest replay; older watchers step aside
        self._back_to = None                 # scene to return to (the one before the FIRST replay)
        self.last = {}                       # status for /replay_pc

    # -- config ---------------------------------------------------------------
    def config(self) -> dict:
        try:
            m = self._file.stat().st_mtime
        except OSError:
            m = None
        if m != self._mtime:
            self._mtime = m
            cfg = dict(DEFAULTS)
            if m is not None:
                try:
                    cfg.update(json.loads(self._file.read_text(encoding="utf-8")) or {})
                except (OSError, ValueError) as e:
                    print(f"[replay-pc] {self._file.name} unreadable: {e}")
            cfg["url"] = normalize_url(cfg.get("url", ""))
            self.cfg = cfg
        return self.cfg

    @property
    def enabled(self) -> bool:
        return bool(self.config()["url"])

    # -- OBS --------------------------------------------------------------------
    def _obs_call(self, fn):
        if self._obs is None and self._obs_factory:
            self._obs = self._obs_factory()
        if self._obs is None:
            return None
        try:
            return fn(self._obs)
        except Exception as e:
            print(f"[replay-pc] OBS: {e}")
            return None

    # -- replay ---------------------------------------------------------------
    def replay(self, car_number, t_session=None, session_num=None,
               buildup_seconds=5.0, replay_seconds=10.0):
        """Send the replay to the sim rig. -> (ok, message). ok False with
        message 'unreachable: …' = the caller may play it locally."""
        cfg = self.config()
        body = {"car_number": str(car_number), "t_session": t_session,
                "session_num": session_num, "buildup_seconds": float(buildup_seconds),
                "replay_seconds": float(replay_seconds)}
        try:
            res = self._http("POST", cfg["url"] + "/replay/remote", body, float(cfg["timeout_s"]))
        except Exception as e:
            self.last = {"ok": False, "error": f"unreachable: {e}", "car": str(car_number),
                         "at": time.time()}
            print(f"[replay-pc] {cfg['url']} not reachable: {e}")
            return False, f"unreachable: {e}"
        if not res.get("ok"):
            msg = res.get("message") or res.get("error") or "refused"
            self.last = {"ok": False, "error": msg, "car": str(car_number), "at": time.time()}
            print(f"[replay-pc] sim rig refused: {msg}")
            return False, f"replay PC: {msg}"
        seq = res.get("seq")
        with self._lock:
            self._gen += 1
            gen = self._gen
        self.last = {"ok": True, "car": str(car_number), "seq": seq, "at": time.time(),
                     "state": "replaying"}
        print(f"[replay-pc] #{car_number} replaying on {cfg['url']} (seq {seq})")
        threading.Thread(target=self._watch, args=(gen, seq, float(replay_seconds), cfg),
                         daemon=True).start()
        return True, f"replay on the second PC (#{car_number})"

    def _watch(self, gen, seq, seconds, cfg):
        scene = cfg.get("scene") or ""
        if scene:
            cur = self._obs_call(lambda o: o.current_scene())
            with self._lock:
                if cur is not None and cur != scene:
                    self._back_to = cfg.get("back_scene") or cur
            if cur is not None and cur != scene:
                self._obs_call(lambda o: o.set_scene(scene))
        deadline = self._clock() + seconds + float(cfg["extra_s"])
        why = "timeout"
        while self._clock() < deadline:
            self._sleep(float(cfg["poll_s"]))
            if gen != self._gen:
                return                       # a newer replay owns the scene now
            try:
                st = self._http("GET", cfg["url"] + "/replay/remote/status", None,
                                float(cfg["timeout_s"]))
            except Exception:
                continue
            if seq is None or (st.get("seq", 0) >= seq and st.get("state") == "live"):
                why = "sim rig live again"
                break
        if gen != self._gen:
            return
        self.last["state"] = "live"
        if scene:
            with self._lock:
                back = self._back_to
                self._back_to = None
            cur = self._obs_call(lambda o: o.current_scene())
            if back and cur == scene:
                self._obs_call(lambda o: o.set_scene(back))
                print(f"[replay-pc] back to scene '{back}' ({why})")
            elif back:
                print(f"[replay-pc] scene changed by hand ('{cur}') - left alone ({why})")

    def status(self) -> dict:
        cfg = self.config()
        return {"enabled": bool(cfg["url"]), "url": cfg["url"], "scene": cfg.get("scene"),
                "back_scene": cfg.get("back_scene"),
                "fallback_local": bool(cfg.get("fallback_local", True)), "last": self.last}


class RemoteState:
    """Sim rig side: seq of the newest /replay/remote and whether it is back live."""

    def __init__(self):
        self.seq = 0
        self.state = "live"
        self._lock = threading.Lock()

    def begin(self) -> int:
        with self._lock:
            self.seq += 1
            self.state = "replaying"
            return self.seq

    def done(self, seq: int, failed: bool = False):
        with self._lock:
            if seq == self.seq:
                self.state = "live"

    def get(self) -> dict:
        with self._lock:
            return {"seq": self.seq, "state": self.state}
