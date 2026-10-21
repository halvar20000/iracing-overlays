"""Highlight video run (highlight_runner.py) against a fake replay + fake_obs.py.

Needs `websockets` + `websocket-client` (fake OBS server / real obs_ws client).
The fake replay plays SessionTime SPEED x faster than real time, so a 4-minute
reel runs in seconds. Checks: seek / camera / play order per scene, recording
on exactly while the replay is inside the scene, one StartRecord, Pause /
Resume between scenes, StopRecord + output path, never Scenic / Chase /
Cockpit, stop mid-run, OBS already recording, rehearsal without OBS, stalled
replay kicked, voice lines with mood, intro by voice and by Gabi's video,
the dashboard adapter (PollerReplay) on a fake poller + fake irsdk.
"""
import os
import sys
import threading
import time

from pathlib import Path

import highlight_runner as R
from fake_obs import FakeObs
from obs_ws import ObsClient

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

fails, passes = [], 0


def check(name, ok, info=""):
    global passes
    if ok:
        passes += 1
    else:
        fails.append(f"{name} {info}")


GROUPS = [{"id": 1, "name": "Scenic"}, {"id": 2, "name": "TV1"}, {"id": 3, "name": "TV2"},
          {"id": 4, "name": "TV3"}, {"id": 5, "name": "Chase"}, {"id": 6, "name": "Far Chase"},
          {"id": 7, "name": "Cockpit"}, {"id": 8, "name": "Gyro"}, {"id": 9, "name": "Rear Chase"}]
NAME = {g["id"]: g["name"] for g in GROUPS}


class FakeReplay:
    def __init__(self, speed=20.0, ignore_first_plays=0):
        self.speed = speed
        self.base, self.play_at, self.playing = 0.0, None, False
        self.calls = []
        self.ignore = ignore_first_plays
        self.prepared = self.restored = 0
        self.lock = threading.Lock()

    def connected(self):
        return True

    def camera_groups(self):
        return GROUPS

    def session_time(self):
        with self.lock:
            if self.playing:
                return self.base + (time.time() - self.play_at) * self.speed
            return self.base

    def prepare(self):
        self.prepared += 1

    def restore(self):
        self.restored += 1

    def hold(self):
        pass

    def seek(self, sn, t):
        with self.lock:
            self.calls.append(("seek", sn, round(t, 2)))
            self.base, self.playing = t, False

    def play(self):
        with self.lock:
            self.calls.append(("play",))
            if self.ignore > 0:
                self.ignore -= 1
                return
            if not self.playing:
                self.play_at, self.playing = time.time(), True

    def pause(self):
        with self.lock:
            self.calls.append(("pause",))
            if self.playing:
                self.base += (time.time() - self.play_at) * self.speed
            self.playing = False

    def switch(self, car, gid):
        self.calls.append(("cam", str(car), NAME[gid]))


class TracingObs(ObsClient):
    """Real client; remembers the replay's SessionTime at every record call."""
    def __init__(self, port, replay):
        super().__init__("127.0.0.1", port)
        self.replay, self.trace = replay, []

    def call(self, rt, data=None):
        if "Record" in rt:
            self.trace.append((rt, self.replay.session_time()))
        return super().call(rt, data)


class Speaker:
    def __init__(self):
        self.said, self.t = [], {}

    def say(self, text, mood="normal"):
        self.said.append((text, mood))
        self.t[len(self.said)] = time.time()
        return len(self.said)

    def is_done(self, lid):
        return time.time() - self.t[lid] > 0.3


SCENES = [
    {"kind": "start", "title": "Der Start", "t": 268.5, "t_start": 260.5, "t_end": 298.5, "car_number": "49",
     "driver": "Maurice Becker", "session_num": 3, "text": "Grüne Flagge!"},
    {"kind": "pass", "title": "Überholmanöver", "t": 3319, "t_start": 3307, "t_end": 3325, "car_number": "49",
     "driver": "Maurice Becker", "session_num": 3, "text": "Becker geht vorbei!"},
    {"kind": "finish", "title": "Zieleinlauf", "t": 3882, "t_start": 3857, "t_end": 3887, "car_number": "01",
     "driver": "Mike Zocher", "session_num": 3, "text": "Zocher gewinnt!"},
]
CFG = {"settle_s": 0.05, "cam_every_s": 0.5, "card_s": 0.6, "stall_s": 0.5}
OBS_SCENES = {"iRacing": {"Kommentar Stimme": True, "Startaufstellung": True}, "Moderator": {"Gabi Ruhe": False, "Gabi": False}}


def run(scenes, opts, replay=None, obs_scenes=OBS_SCENES, speaker=None, avatar=None, media=None, pre=None):
    fake = FakeObs(obs_scenes, media=media or {"Gabi": 0.8})
    if pre:
        pre(fake)
    rp = replay or FakeReplay()
    obs = TracingObs(fake.port, rp)
    r = R.HighlightRunner(rp, obs, speaker=speaker, avatar=avatar, cfg=CFG)
    ok, msg = r.start(scenes, opts)
    return fake, rp, obs, r, ok


# ---------------------------------------------------------------- full run
fake, rp, obs, r, ok = run(SCENES, {"record": True, "card": True})
check("start accepted", ok)
time.sleep(0.3)
st = r.status()
check("card shown at the start of a scene", st["card"] and st["card"]["title"] == "Der Start", st["card"])
r.wait(30)
st = r.status()
check("run done", st["state"] == "done", st)
rec_calls = [c for c, _ in obs.trace if c != "GetRecordStatus"]
check("status checked before anything", obs.trace[0][0] == "GetRecordStatus")
check("record calls: Start, Pause, Resume, Pause, Resume, Pause, Stop",
      rec_calls == ["StartRecord", "PauseRecord", "ResumeRecord", "PauseRecord",
                    "ResumeRecord", "PauseRecord", "StopRecord"], rec_calls)
check("OBS recorded 3 segments", len(fake.rec_segments) == 3 and not fake.recording, fake.rec_segments)
check("output path reported", st["output"] == "C:/Videos/fake_recording.mp4", st["output"])
# recording on exactly while the replay is inside each scene
ons = [t for c, t in obs.trace if c in ("StartRecord", "ResumeRecord")]
offs = [t for c, t in obs.trace if c == "PauseRecord"]
for sc, a, b in zip(SCENES, ons, offs):
    # (tolerance: ~0.15 s of wall-clock latency x the fake replay's speed 20)
    check(f"{sc['kind']}: record on at scene start", sc["t_start"] - 1.5 <= a <= sc["t_start"] + 3.0, (a, sc["t_start"]))
    check(f"{sc['kind']}: record off at scene end", sc["t_end"] <= b <= sc["t_end"] + 3.0, (b, sc["t_end"]))
seeks = [c for c in rp.calls if c[0] == "seek"]
check("seek per scene with session_num, 1.5 s before", seeks == [("seek", 3, s["t_start"] - 1.5) for s in SCENES], seeks)
cams = [c for c in rp.calls if c[0] == "cam"]
check("camera never Scenic / Chase / Cockpit", all(not any(x in c[2].upper() for x in ("SCENIC", "CHASE", "COCKPIT"))
                                                   for c in cams), cams)
check("camera follows the scene's car", {c[1] for c in cams} == {"49", "01"}, cams)
check("TV camera rotated inside the long start scene", len({c[2] for c in cams if c[1] == "49"}) >= 2, cams)
first = rp.calls.index(seeks[0])
check("seek -> cam -> play order", [c[0] for c in rp.calls[first:first + 3]] == ["seek", "cam", "play"], rp.calls[:4])
check("replay prepared + restored once", rp.prepared == 1 and rp.restored == 1)
check("scene states all fertig", [s["state"] for s in st["scenes"]] == ["fertig"] * 3, st["scenes"])
check("program scene set to iRacing", ("SetCurrentProgramScene", {"sceneName": "iRacing"}) in fake.calls)
check("recorded seconds ~ reel length in wall time",
      0.5 < st["recorded_s"] < (sum(s["t_end"] - s["t_start"] for s in SCENES) / 20.0) * 2 + 3, st["recorded_s"])

# ---------------------------------------------------------------- starting grid -> start, seamless
GRID = [{"kind": "grid", "title": "Startaufstellung", "t": 268.5, "t_start": 245.5, "t_end": 260.5, "car_number": "49",
         "driver": "Maurice Becker", "session_num": 3, "text": "Die Startaufstellung", "overlay": "grid"},
        dict(SCENES[0])]
fake, rp, obs, r, ok = run(GRID, {"record": True, "card": True})
time.sleep(0.3)
check("grid: source Startaufstellung visible, no title card over it",
      fake.scenes["iRacing"]["Startaufstellung"] and r.status()["card"] is None, r.status()["card"])
r.wait(30)
st = r.status()
check("grid: done", st["state"] == "done", st["error"])
rc = [c for c, _ in obs.trace if c != "GetRecordStatus"]
check("grid: one recording, no pause between grid and start", rc == ["StartRecord", "PauseRecord", "StopRecord"], rc)
check("grid: one seek only (start plays on from the grid)", [c for c in rp.calls if c[0] == "seek"]
      == [("seek", 3, 244.0)], rp.calls)
check("grid: source hidden again", not fake.scenes["iRacing"]["Startaufstellung"])
vis = [d["sceneItemEnabled"] for rt, d in fake.calls if rt == "SetSceneItemEnabled"]
check("grid: hidden at run start, shown, hidden", vis == [False, True, False], vis)
gcam = [c for c in rp.calls if c[0] == "cam"][0]
check("grid: wide camera, not TV1 (no Blimp here -> TV2)", gcam[2] == "TV2", gcam)
check("grid: start scene still gets its title card", any("Der Start" in l or "start" in l for l in st["log"]))
fake, rp, obs, r, ok = run(GRID, {"record": True}, obs_scenes={"iRacing": {}, "Moderator": {}})
r.wait(30)
check("grid: missing source -> hint, run goes on", r.status()["state"] == "done"
      and any("obs_loaders/grid.html" in l for l in r.status()["log"]), r.status()["log"])

# ---------------------------------------------------------------- stop mid-run
fake, rp, obs, r, ok = run(SCENES, {"record": True})
time.sleep(0.8)
r.stop()
r.wait(10)
st = r.status()
check("stop: state stopped", st["state"] == "stopped", st["state"])
check("stop: recording stopped", "StopRecord" in [c for c, _ in obs.trace] and not fake.recording)
check("stop: not all scenes ran", sum(1 for s in st["scenes"] if s["state"] == "fertig") < 3, st["scenes"])
check("stop: replay restored", rp.restored == 1)

# ---------------------------------------------------------------- OBS already recording
fake, rp, obs, r, ok = run(SCENES, {"record": True}, pre=lambda f: setattr(f, "recording", True))
r.wait(10)
st = r.status()
check("already recording -> error, nothing touched", st["state"] == "error" and "nimmt schon auf" in st["error"]
      and "StartRecord" not in [c for c, _ in obs.trace] and not rp.calls, st)

# ---------------------------------------------------------------- rehearsal
rp2 = FakeReplay(speed=40)
r = R.HighlightRunner(rp2, None, cfg=CFG)
r.start(SCENES[:2], {"record": True})      # no OBS -> rehearsal
r.wait(20)
check("no OBS: plays the scenes anyway", r.status()["state"] == "done"
      and len([c for c in rp2.calls if c[0] == "seek"]) == 2, r.status())

# ---------------------------------------------------------------- stalled replay
rp3 = FakeReplay(speed=20, ignore_first_plays=2)
fake, rp, obs, r, ok = run(SCENES[1:2], {"record": True}, replay=rp3)
r.wait(20)
check("stalled replay: play kicked again, scene done", r.status()["state"] == "done"
      and sum(1 for c in rp3.calls if c[0] == "play") >= 3
      and any("Replay steht" in l for l in r.status()["log"]), r.status()["log"])

# ---------------------------------------------------------------- voice + intro by voice
sp = Speaker()
fake, rp, obs, r, ok = run(SCENES[:2], {"record": True, "voice": True, "intro": "Willkommen!", "outro": "Tschüss!"},
                           speaker=sp)
r.wait(30)
st = r.status()
check("voice: done", st["state"] == "done", st)
check("voice: intro, 2 scene lines, outro", [t for t, _ in sp.said] == ["Willkommen!", "Grüne Flagge!",
                                                                         "Becker geht vorbei!", "Tschüss!"], sp.said)
check("voice: scene lines hype, moderation normal", [m for _, m in sp.said] == ["normal", "hype", "hype", "normal"])
scs = [d["sceneName"] for rt, d in fake.calls if rt == "SetCurrentProgramScene"]
check("voice: Moderator -> iRacing -> Moderator", scs == ["Moderator", "iRacing", "Moderator"], scs)
check("voice: 4 recorded segments (intro, 2 scenes, outro)", len(fake.rec_segments) == 4, fake.rec_segments)
check("voice: Gabi idle loop switched on", fake.scenes["Moderator"]["Gabi Ruhe"])


# ---------------------------------------------------------------- Gabi intro video
class Avatar:
    def __init__(self):
        self.ordered = []

    def available(self):
        return True

    def ensure(self, kind, seg, text):
        self.ordered.append((kind, seg, text))

    def get(self, kind, seg, text):
        return "C:/avatar/out/highlights_intro.mp4" if seg == "intro" else None

    def status(self, kind):
        return {"intro": {"state": "ready"}}


av = Avatar()
fake, rp, obs, r, ok = run(SCENES[:1], {"record": True, "gabi": True, "intro": "Hallo!", "outro": "Ciao!"}, avatar=av)
r.prepare_avatar("Hallo!", "Ciao!")
r.wait(30)
st = r.status()
check("gabi: done", st["state"] == "done", st)
check("gabi: videos ordered for intro + outro", [(k, s) for k, s, _ in av.ordered] == [("highlights", "intro"),
                                                                                    ("highlights", "outro")])
check("gabi: intro video put into source Gabi", ("SetInputSettings",
      {"inputName": "Gabi", "inputSettings": {"local_file": "C:/avatar/out/highlights_intro.mp4"}}) in fake.calls)
check("gabi: video restarted and hidden afterwards", any(rt == "TriggerMediaInputAction" for rt, _ in fake.calls)
      and not fake.scenes["Moderator"]["Gabi"])
check("gabi: outro skipped without video and voice", any("outro nicht fertig" in l for l in st["log"])
      and len(fake.rec_segments) == 2, (st["log"], fake.rec_segments))

# ---------------------------------------------------------------- Gabi source with "Loop" ticked in OBS
# (Thomas, 2026-10-21: the intro ran in a loop — OBS never reports "ended" for a looping source)
class LoopObs(FakeObs):
    def handle(self, rt, rd):
        if rt == "GetMediaInputStatus" and rd.get("inputName") == "Gabi":
            self.calls.append((rt, rd))
            m = self.media["Gabi"]
            el = (time.time() - m["started"]) if m["started"] else 0
            return True, {"mediaState": "OBS_MEDIA_STATE_PLAYING", "mediaDuration": int(m["duration"] * 1000),
                          "mediaCursor": int((el % m["duration"]) * 1000)}, None
        return super().handle(rt, rd)


lo = LoopObs(OBS_SCENES, media={"Gabi": 1.2})
rp = FakeReplay()
r = R.HighlightRunner(rp, TracingObs(lo.port, rp), avatar=Avatar(), cfg=CFG)
t_start = time.time()
r.start(SCENES[:1], {"record": True, "gabi": True, "intro": "Hallo!"})
t_intro = None
while r.status()["state"] == "running" and time.time() - t_start < 30:
    if t_intro is None and lo.current == "iRacing" and lo.recording:
        t_intro = time.time() - t_start
    time.sleep(0.05)
check("loop: run done", r.status()["state"] == "done", r.status()["error"])
check("loop: intro ends after the clip's length, not after 90 s", t_intro is not None and t_intro < 4, t_intro)
check("loop: Gabi video stopped + hidden", any(rt == "TriggerMediaInputAction" and d["mediaAction"].endswith("STOP")
                                            for rt, d in lo.calls) and not lo.scenes["Moderator"]["Gabi"])

# ---------------------------------------------------------------- OBS as it really behaves (2nd real run,
# 2026-10-21: "ResumeRecord: 503", Gabi silent): the record output starts a moment
# AFTER StartRecord answers, a PauseRecord in that moment is lost, and a freshly
# loaded media file reports duration -1 while it opens.
class RealishObs(LoopObs):
    START_LAG, OPEN_S = 0.5, 0.4

    def handle(self, rt, rd):
        now = time.time()
        if rt == "StartRecord" and not self.recording:
            self.calls.append((rt, rd))
            self.recording, self.rec_paused, self._active_at = True, False, now + self.START_LAG
            self.rec_segments.append([now, None])
            return True, None, None
        active = self.recording and now >= getattr(self, "_active_at", 0)
        if rt == "GetRecordStatus":
            self.calls.append((rt, rd))
            return True, {"outputActive": active, "outputPaused": self.rec_paused}, None
        if rt == "PauseRecord" and self.recording and not active:
            self.calls.append((rt, rd))
            return True, None, None            # accepted, but nothing happens
        if rt == "GetMediaInputStatus" and rd.get("inputName") == "Gabi":
            m = self.media["Gabi"]
            if m["started"] and now - m["started"] < self.OPEN_S:
                self.calls.append((rt, rd))
                return True, {"mediaState": "OBS_MEDIA_STATE_OPENING", "mediaDuration": -1,
                              "mediaCursor": 0}, None
        return super().handle(rt, rd)


ro = RealishObs(OBS_SCENES, media={"Gabi": 1.5})
rp = FakeReplay()
r = R.HighlightRunner(rp, TracingObs(ro.port, rp), avatar=Avatar(), cfg=CFG)
t_start = time.time()
r.start(SCENES[:2], {"record": True, "gabi": True, "intro": "Hallo!"})
t_intro, seen_mod = None, None
while r.status()["state"] == "running" and time.time() - t_start < 40:
    if seen_mod is None and ro.current == "Moderator":
        seen_mod = time.time()
    if t_intro is None and seen_mod and ro.current == "iRacing":
        t_intro = time.time() - seen_mod
    time.sleep(0.05)
st = r.status()
check("realistic OBS: run done, no 503", st["state"] == "done", st["error"])
check("realistic OBS: Gabi spoke her whole clip (>= 1.5 s after it opened)", t_intro is not None and t_intro >= 1.8,
      t_intro)
check("realistic OBS: intro + 2 scenes = 3 recorded segments", len(ro.rec_segments) == 3, ro.rec_segments)

# ---------------------------------------------------------------- Gabi videos are kept (Thomas, 2026-10-21:
# every dashboard restart / update meant ordering - and paying for - the same intro again)
import tempfile
import avatar_sync
with tempfile.TemporaryDirectory() as td:
    orders = []

    def fake_lipsync(data, name, out_name=None, **k):
        orders.append(out_name)
        p = Path(td) / f"{out_name}.mp4"
        p.write_bytes(b"video")
        return p

    vox = type("V", (), {"synth": lambda self, t: (b"a", "audio/mpeg", "edge")})()
    a1 = avatar_sync.AvatarRenderer(vox, lipsync_fn=fake_lipsync, out_dir=td)
    a1.ensure("highlights", "intro", "Willkommen!")
    t0 = time.time()
    while a1.jobs[("highlights", "intro")]["state"] != "ready" and time.time() - t0 < 5:
        time.sleep(0.05)
    check("saved: first order renders once", len(orders) == 1 and a1.get("highlights", "intro", "Willkommen!"))
    a2 = avatar_sync.AvatarRenderer(vox, lipsync_fn=fake_lipsync, out_dir=td)     # = dashboard restarted
    r2 = R.HighlightRunner(FakeReplay(), None, avatar=a2, cfg=CFG)
    r2.check_avatar("Willkommen!", "")
    st2 = r2.status()["avatar"]
    check("saved: after a restart the video is found without an order",
          st2.get("intro", {}).get("state") == "ready" and st2["intro"]["saved"] and len(orders) == 1, (st2, orders))
    r2.prepare_avatar("Willkommen!", "")
    time.sleep(0.2)
    check("saved: ordering the same text again costs nothing", len(orders) == 1, orders)
    a3 = avatar_sync.AvatarRenderer(vox, lipsync_fn=fake_lipsync, out_dir=td)
    check("saved: the run finds it even without a check", a3.get("highlights", "intro", "Willkommen!") is not None)
    a3.ensure("highlights", "intro", "Willkommen zurück!")
    t0 = time.time()
    while len(orders) < 2 and time.time() - t0 < 5:
        time.sleep(0.05)
    check("saved: a changed text is ordered", len(orders) == 2, orders)
    check("saved: same text in another segment is its own video", a3.get("highlights", "outro", "Willkommen!") is None)

# ---------------------------------------------------------------- real reel (Indianapolis), when the log is here
INDY = "logs/WCT/indianapolis/20261020-195328_indianapolis_motor_speedway_road_course_race.jsonl"
if os.path.exists(INDY):
    import highlights as H
    reel = H.pick_file(INDY, 240)["scenes"]
    fake, rp, obs, r, ok = run(reel, {"record": True, "card": True}, replay=FakeReplay(speed=60))
    r.wait(60)
    st = r.status()
    check("Indy reel: done", st["state"] == "done", st["error"])
    joined = sum(1 for a, b in zip(reel, reel[1:]) if R.HighlightRunner._contiguous(a, b))
    check("Indy reel: grid runs into the start", reel[0]["kind"] == "grid" and joined >= 1)
    check("Indy reel: one segment per scene, grid + start as one",
          len(fake.rec_segments) == len(reel) - joined, (len(fake.rec_segments), len(reel), joined))
    caution = next(s for s in reel if s["kind"] == "caution")
    fake, rp, obs, r, ok = run([caution], {"record": True}, replay=FakeReplay(speed=4))
    r.wait(30)
    on = [t for c, t in obs.trace if c in ("StartRecord", "ResumeRecord")]
    off = [t for c, t in obs.trace if c == "PauseRecord"]
    check("Indy reel: the recorded caution covers 547.7 s", on and on[0] <= 547.72 <= off[0], (on, off))
else:
    print("SKIP Indy reel: log not present")


# ---------------------------------------------------------------- PollerReplay (dashboard adapter)
class FakeIR:
    def __init__(self):
        self.calls = []
        self.vals = {"SessionTime": 123.4, "SessionNum": 2}

    def __getitem__(self, k):
        return self.vals.get(k)

    def replay_set_play_speed(self, s, slow):
        self.calls.append(("speed", s))

    def replay_search_session_time(self, sn, ms):
        self.calls.append(("search", sn, ms))

    def cam_switch_num(self, car, grp, cam):
        self.calls.append(("cam", car, grp, cam))


class FakePoller:
    def __init__(self):
        self.ir = FakeIR()
        self.connected = True
        self._camera_groups = GROUPS
        self.director_enabled = True
        self.auto_follow = False
        self.focus_leader = True
        self.focus_crashes = False
        self.auto_replay = True
        self.auto_replay_overtakes = True
        self._manual_override_until = 0.0
        self._own_replay_until = 0.0
        self._own_replay_from = 0.0
        self._current_cam_group = 0
        self.director_calls, self.hud = [], 0

    def set_director(self, on):
        self.director_calls.append(on)
        self.director_enabled = on

    def set_auto_camera(self, on):
        pass

    def _reassert_ui_hide(self):
        self.hud += 1


fp = FakePoller()
pr = R.PollerReplay(fp, sleep=lambda s: None)
pr.prepare()
check("adapter: REGIE / focus / auto-replays off", not fp.director_enabled and not fp.focus_leader
      and not fp.auto_replay and not fp.auto_replay_overtakes and fp.director_calls == [False])
check("adapter: watchdog held back", fp._manual_override_until > time.time() + 20)
pr.seek(3, 547.72)
pr.switch("01", 2)
pr.play()
check("adapter: pause -> search(session_num, ms) -> cam -> play 1x",
      fp.ir.calls == [("speed", 0), ("search", 3, 547720), ("cam", "01", 2, 0), ("speed", 1)], fp.ir.calls)
check("adapter: HUD re-hidden after camera / play", fp.hud == 2)
check("adapter: session time", pr.session_time() == 123.4)
pr.restore()
check("adapter: restore puts REGIE / focus / auto-replays back", fp.director_enabled and fp.focus_leader
      and fp.auto_replay and fp.auto_replay_overtakes and fp.director_calls == [False, True])
check("pick_group: no TV -> Gyro, never Chase / Cockpit / Scenic",
      R.pick_group("pass", [g for g in GROUPS if not g["name"].startswith("TV")])["name"] == "Gyro")
check("pick_group: only forbidden cams -> None",
      R.pick_group("pass", [g for g in GROUPS if g["name"] in ("Scenic", "Chase", "Cockpit")]) is None)

print(f"\n{passes} PASS, {len(fails)} FAIL")
for f in fails:
    print("  FAIL", f)
sys.exit(1 if fails else 0)
