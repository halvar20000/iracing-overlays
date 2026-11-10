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
`source` set (e.g. "Replay NDI") = no scene switch: that source is shown in
the scene on air (the NDI picture placed in the iRacing scene, below the
overlays) and hidden again at the same moment the scene would switch back.
`follow` (default on): the sim rig's OWN replays (started on its dashboard or
in iRacing there) bring the source / scene up too - a background thread polls
the rig's /replay/remote/status, which carries its playback mode; not live =
show, live again (2 polls in a row) = hide. A replay sent from here is left to
its own watcher. The rig needs the same version (older ones send no mode).
`transition` (e.g. "Replay Stinger"; 2026-11-10, Thomas: a stinger as the
iRacing scene's override played on EVERY switch to iRacing, Moderator too) =
the OBS transition used for THESE scene switches only: set as the current
transition just before the switch, the previous one put back
TRANSITION_RESTORE_S later. The scenes must have no transition override
(an override wins over the current transition).

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
DEFAULTS = {"url": "", "scene": "Replay", "source": "", "back_scene": "", "fallback_local": True,
            "timeout_s": 3.0, "extra_s": 20.0, "poll_s": 0.5, "follow": True,
            "transition": ""}
TRANSITION_RESTORE_S = 2.5  # previous OBS transition back after this (stinger ~1.1 s)
FOLLOW_LIVE_POLLS = 2       # rig reported live this many polls in a row = hide
FOLLOW_LOST_POLLS = 6       # rig unreachable this many polls while shown = hide
FOLLOW_QUIET_S = 3.0        # after a replay sent from here, its return to live is not a new replay


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
        self._shown = None                   # (scene, source) shown in source mode
        self.last = {}                       # status for /replay_pc
        self._watching = 0                   # replays sent from here still being watched
        self._following = False              # source / scene up because of the rig's own replay
        self.follow_state = ""               # rig's playback mode as last seen by the follower
        self._follower = None
        self._live_n = self._lost_n = 0
        self._quiet_until = 0.0              # just after a replay sent from here: the rig settles
        self._trans_prev = None              # OBS transition to put back after a replay switch
        self._trans_token = 0

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

    def _show(self, cfg):
        source = cfg.get("source") or ""
        scene = "" if source else (cfg.get("scene") or "")
        if source:
            cur = self._obs_call(lambda o: o.current_scene())
            if cur and self._obs_call(lambda o: o.set_visible(cur, source, True) or True):
                with self._lock:
                    if self._shown is None:
                        self._shown = (cur, source)
            elif cur:
                print(f"[replay-pc] source '{source}' not in scene '{cur}'")
        if scene:
            cur = self._obs_call(lambda o: o.current_scene())
            with self._lock:
                if cur is not None and cur != scene and self._back_to is None:
                    self._back_to = cfg.get("back_scene") or cur
            if cur is not None and cur != scene:
                self._switch(scene, cfg)

    def _switch(self, scene, cfg):
        """Scene switch, with cfg "transition" for this switch only."""
        trans = cfg.get("transition") or ""
        if trans:
            prev = self._obs_call(lambda o: o.current_transition())
            with self._lock:
                if prev and prev != trans and self._trans_prev is None:
                    self._trans_prev = prev          # an earlier switch may still hold it
                self._trans_token += 1
                token, restore = self._trans_token, self._trans_prev is not None
            if prev and prev != trans:
                self._obs_call(lambda o: o.set_transition(trans))
            if restore:
                threading.Thread(target=self._restore_transition, args=(token,), daemon=True).start()
        self._obs_call(lambda o: o.set_scene(scene))

    def _restore_transition(self, token):
        self._sleep(TRANSITION_RESTORE_S)
        with self._lock:
            if token != self._trans_token or self._trans_prev is None:
                return                                # a newer switch restores it
            prev, self._trans_prev = self._trans_prev, None
        self._obs_call(lambda o: o.set_transition(prev))

    def _hide(self, cfg, why):
        source = cfg.get("source") or ""
        scene = "" if source else (cfg.get("scene") or "")
        with self._lock:
            shown, self._shown = self._shown, None
        if shown:
            self._obs_call(lambda o: o.set_visible(shown[0], shown[1], False))
            print(f"[replay-pc] '{shown[1]}' hidden in '{shown[0]}' ({why})")
        if scene:
            with self._lock:
                back = self._back_to
                self._back_to = None
            cur = self._obs_call(lambda o: o.current_scene())
            if back and cur == scene:
                self._switch(back, cfg)
                print(f"[replay-pc] back to scene '{back}' ({why})")
            elif back:
                print(f"[replay-pc] scene changed by hand ('{cur}') - left alone ({why})")

    def _watch(self, gen, seq, seconds, cfg):
        with self._lock:
            self._watching += 1
            self._following = False          # this replay owns the picture now
        try:
            self._watch_one(gen, seq, seconds, cfg)
        finally:
            with self._lock:
                self._watching -= 1
                self._quiet_until = self._clock() + FOLLOW_QUIET_S

    def _watch_one(self, gen, seq, seconds, cfg):
        self._show(cfg)
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
        self._hide(cfg, why)

    def go_live(self) -> str:
        """Go Live pressed on the stream PC (2026-11-10, Thomas: sometimes the
        replay shows nothing and the local Go Live did not help): the rig goes
        live too, the watcher of a running replay steps aside, OBS goes back at
        once - also when the rig does not answer."""
        cfg = self.config()
        with self._lock:
            self._gen += 1                   # running watchers step aside
            self._following = False
            self._live_n = self._lost_n = 0
            self._quiet_until = self._clock() + FOLLOW_QUIET_S
        try:
            self._http("POST", cfg["url"] + "/replay/remote/live", {}, float(cfg["timeout_s"]))
            rig = "replay PC live"
        except Exception as e:
            rig = f"replay PC not reachable ({e})"
        self.last["state"] = "live"
        self._hide(cfg, "Go Live by hand")
        print(f"[replay-pc] Go Live by hand: {rig}")
        return rig

    # -- follow the rig's own replays -----------------------------------------
    def start_follow(self):
        if self._follower is None:
            self._follower = threading.Thread(target=self._follow_loop, daemon=True)
            self._follower.start()

    def _follow_loop(self):
        while True:
            try:
                self.follow_tick()
            except Exception as e:
                print(f"[replay-pc] follow: {e}")
            self._sleep(float(self.cfg.get("poll_s", 0.5)))

    def follow_tick(self):
        """One poll of the rig's playback mode (called every poll_s by the
        follower thread). Shows / hides only what it showed itself."""
        cfg = self.config()
        if not cfg["url"] or not cfg.get("follow", True):
            if self._following:
                self._following = False
                self._hide(cfg, "follow off")
            return
        if self._watching or self._clock() < self._quiet_until:
            self._live_n = self._lost_n = 0
            return                           # a replay sent from here is on
        try:
            st = self._http("GET", cfg["url"] + "/replay/remote/status", None,
                            float(cfg["timeout_s"]))
        except Exception:
            self._lost_n += 1
            if self._following and self._lost_n >= FOLLOW_LOST_POLLS:
                self._following = False
                self._hide(cfg, "sim rig not reachable")
            return
        self._lost_n = 0
        mode = st.get("mode")
        self.follow_state = mode or ""
        if mode is None:
            return                           # older rig version: no playback mode
        replaying = bool(st.get("connected", True)) and mode not in ("live", "")
        if replaying:
            self._live_n = 0
            if not self._following:
                self._following = True
                print(f"[replay-pc] sim rig plays its own replay ({mode}) - showing it")
                self._show(cfg)
        elif self._following:
            self._live_n += 1
            if self._live_n >= FOLLOW_LIVE_POLLS:
                self._following = False
                self._live_n = 0
                self._hide(cfg, "sim rig live again")

    def status(self) -> dict:
        cfg = self.config()
        return {"enabled": bool(cfg["url"]), "url": cfg["url"], "scene": cfg.get("scene"),
                "source": cfg.get("source"),
                "back_scene": cfg.get("back_scene"), "transition": cfg.get("transition") or "", "follow": bool(cfg.get("follow", True)),
                "following": self._following, "rig_mode": self.follow_state,
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

    def abort(self):
        """Go Live from the stream PC: whatever was replaying is over."""
        with self._lock:
            self.state = "live"

    def done(self, seq: int, failed: bool = False):
        with self._lock:
            if seq == self.seq:
                self.state = "live"

    def get(self) -> dict:
        with self._lock:
            return {"seq": self.seq, "state": self.state}
