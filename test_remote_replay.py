"""
Tests for remote_replay.py (replay on a second PC) — offline: fake HTTP, fake OBS.
    python test_remote_replay.py            (RemoteReplay / RemoteState only)
    python test_remote_replay.py --dash     (+ the dashboard routes; needs flask and an irsdk stub)
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

import remote_replay as rr

PASS = FAIL = 0


def check(name, cond, info=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name} {info}")


class FakeObs:
    def __init__(self, scene="iRacing"):
        self.scene, self.calls = scene, []

    def current_scene(self):
        return self.scene

    def set_scene(self, name):
        self.calls.append(name)
        self.scene = name

    items = {"iRacing": {"Replay NDI": False}}

    def set_visible(self, scene, source, visible):
        if source not in self.items.get(scene, {}):
            raise KeyError(f"{source} not in {scene}")
        self.calls.append(f"{scene}/{source}={visible}")
        self.items[scene][source] = visible


class FakeRig:
    """The sim rig's dashboard: POST /replay/remote, GET /replay/remote/status."""

    def __init__(self, replay_s=0.3, down=False, refuse=False):
        self.state = rr.RemoteState()
        self.replay_s, self.down, self.refuse = replay_s, down, refuse
        self.posts = []

    def http(self, method, url, body, timeout):
        if self.down:
            raise OSError("connection refused")
        if method == "POST":
            self.posts.append((url, body))
            if self.refuse:
                return {"ok": False, "message": "not connected"}
            seq = self.state.begin()
            threading.Timer(self.replay_s, self.state.done, args=(seq,)).start()
            return {"ok": True, "seq": seq}
        return self.state.get()


def make(tmp, cfg, rig, obs):
    f = Path(tmp) / "replay_pc_config.json"
    if cfg is not None:
        f.write_text(json.dumps(cfg))
    return rr.RemoteReplay(obs_factory=lambda: obs, http=rig.http, config_file=f)


def wait(cond, secs=3.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def unit():
    check("U1 url bare ip", rr.normalize_url("192.168.86.50") == "http://192.168.86.50:5000")
    check("U2 url with port + slash", rr.normalize_url("http://simrig:5001/") == "http://simrig:5001")
    check("U3 url empty", rr.normalize_url("") == "")

    with tempfile.TemporaryDirectory() as tmp:
        obs, rig = FakeObs(), FakeRig()
        r = make(tmp, None, rig, obs)
        check("R0 no file = off", not r.enabled)

    with tempfile.TemporaryDirectory() as tmp:
        obs, rig = FakeObs("iRacing"), FakeRig(replay_s=0.4)
        r = make(tmp, {"url": "192.168.86.50", "poll_s": 0.05}, rig, obs)
        check("R1 enabled", r.enabled)
        ok, msg = r.replay("14", t_session=1234.5, session_num=2, buildup_seconds=7, replay_seconds=10)
        check("R2 sent ok", ok, msg)
        url, body = rig.posts[0]
        check("R3 url + payload", url == "http://192.168.86.50:5000/replay/remote"
              and body == {"car_number": "14", "t_session": 1234.5, "session_num": 2,
                           "buildup_seconds": 7.0, "replay_seconds": 10.0}, (url, body))
        check("R4 scene -> Replay", wait(lambda: obs.scene == "Replay"))
        check("R5 back after the rig is live", wait(lambda: obs.scene == "iRacing"), obs.calls)
        check("R6 two switches only", obs.calls == ["Replay", "iRacing"], obs.calls)
        check("R7 status live", r.status()["last"].get("state") == "live", r.status())

    # a second replay while the first is on: one return, to the ORIGINAL scene
    with tempfile.TemporaryDirectory() as tmp:
        obs, rig = FakeObs("Main"), FakeRig(replay_s=0.4)
        r = make(tmp, {"url": "x", "poll_s": 0.05}, rig, obs)
        r.replay("1")
        wait(lambda: obs.scene == "Replay")
        time.sleep(0.2)
        r.replay("2")
        check("R8 back to Main once", wait(lambda: obs.scene == "Main"), obs.calls)
        time.sleep(0.3)
        check("R9 calls", obs.calls == ["Replay", "Main"], obs.calls)

    # operator changes the scene during the replay -> left alone
    with tempfile.TemporaryDirectory() as tmp:
        obs, rig = FakeObs("Main"), FakeRig(replay_s=0.4)
        r = make(tmp, {"url": "x", "poll_s": 0.05}, rig, obs)
        r.replay("1")
        wait(lambda: obs.scene == "Replay")
        obs.scene = "Moderator"
        time.sleep(0.8)
        check("R10 hand change kept", obs.scene == "Moderator" and obs.calls == ["Replay"], obs.calls)

    # back_scene + rig never answering -> timeout after replay + extra
    with tempfile.TemporaryDirectory() as tmp:
        obs, rig = FakeObs("Main"), FakeRig(replay_s=99)
        r = make(tmp, {"url": "x", "poll_s": 0.05, "extra_s": 0.3, "back_scene": "Race"}, rig, obs)
        t0 = time.time()
        r.replay("1", replay_seconds=0.2)
        check("R11 timeout -> back_scene", wait(lambda: obs.scene == "Race"), obs.calls)
        check("R12 not before replay + extra", time.time() - t0 >= 0.45, time.time() - t0)

    # unreachable / refused
    with tempfile.TemporaryDirectory() as tmp:
        obs, rig = FakeObs("Main"), FakeRig(down=True)
        r = make(tmp, {"url": "x"}, rig, obs)
        ok, msg = r.replay("1")
        check("R13 unreachable", not ok and msg.startswith("unreachable") and obs.calls == [], msg)
        rig2 = FakeRig(refuse=True)
        r2 = make(tmp, {"url": "x"}, rig2, obs)
        ok, msg = r2.replay("1")
        check("R14 refused, no scene change", not ok and "not connected" in msg and obs.calls == [], msg)

    # no scene configured = no OBS at all; config re-read on change
    with tempfile.TemporaryDirectory() as tmp:
        obs, rig = FakeObs("Main"), FakeRig(replay_s=0.1)
        r = make(tmp, {"url": "x", "scene": "", "poll_s": 0.05}, rig, obs)
        r.replay("1")
        time.sleep(0.4)
        check("R15 scene '' = OBS untouched", obs.calls == [], obs.calls)
        f = Path(tmp) / "replay_pc_config.json"
        f.write_text(json.dumps({"url": ""}))
        import os
        os.utime(f, (time.time() + 5, time.time() + 5))
        check("R16 file change re-read", not r.enabled)

    # source mode: the NDI source shown in the scene on air, no scene switch
    with tempfile.TemporaryDirectory() as tmp:
        obs, rig = FakeObs("iRacing"), FakeRig(replay_s=0.3)
        obs.items = {"iRacing": {"Replay NDI": False}}
        r = make(tmp, {"url": "x", "source": "Replay NDI", "poll_s": 0.05}, rig, obs)
        r.replay("1")
        check("V1 source shown", wait(lambda: obs.items["iRacing"]["Replay NDI"]), obs.calls)
        time.sleep(0.1)
        r.replay("2")
        check("V2 hidden after the rig is live",
              wait(lambda: not obs.items["iRacing"]["Replay NDI"]), obs.calls)
        time.sleep(0.3)
        check("V3 scene never switched", obs.scene == "iRacing"
              and obs.calls[-1] == "iRacing/Replay NDI=False"
              and obs.calls.count("iRacing/Replay NDI=False") == 1, obs.calls)
        obs.scene, obs.calls = "Moderator", []
        r.replay("3")
        time.sleep(0.6)
        check("V4 source missing in the scene -> nothing", obs.calls == [], obs.calls)

    st = rr.RemoteState()
    a = st.begin()
    b = st.begin()
    st.done(a)
    check("S1 an older replay ending keeps 'replaying'", st.get() == {"seq": 2, "state": "replaying"})
    st.done(b)
    check("S2 newest done = live", st.get() == {"seq": 2, "state": "live"})


def dash():
    """Real dashboard Flask app as BOTH PCs: the stream PC's RemoteReplay talks
    to the same app's /replay/remote through the test client."""
    import iracing_dashboard as d
    client = d.app.test_client()
    calls = []

    def fake_local(car, t_session=None, session_num=None, rewind_seconds=10.0,
                   buildup_seconds=5.0, on_return=None, **kw):
        calls.append((car, t_session, session_num, rewind_seconds, buildup_seconds))
        if on_return:
            threading.Timer(0.2, on_return).start()
        return True, "ok"

    d.poller.replay_5s_of_car = fake_local

    def http(method, url, body, timeout):
        path = url.split(":5000", 1)[1]
        r = client.post(path, json=body) if method == "POST" else client.get(path)
        return r.get_json()

    with tempfile.TemporaryDirectory() as tmp:
        obs = FakeObs("iRacing")
        f = Path(tmp) / "replay_pc_config.json"
        f.write_text(json.dumps({"url": "simrig", "poll_s": 0.05}))
        d.poller.replay_pc = rr.RemoteReplay(obs_factory=lambda: obs, http=http, config_file=f)
        d.poller._incidents.appendleft({"id": 9001, "type": "collision", "car_number": "7",
                                        "t_session": 800.0, "session_num": 2})
        r = client.post("/replay_5s", json={"car_number": "7", "incident_id": 9001,
                                             "replay_seconds": 20}).get_json()
        check("D1 /replay_5s ok", r.get("ok"), r)
        check("D2 played on the 'rig' with the incident time",
              calls and calls[0] == ("7", 800.0, 2, 20.0, 7.0), calls)
        check("D3 scene switched and back", wait(lambda: obs.calls == ["Replay", "iRacing"]), obs.calls)
        check("D4 marked replayed", next(i for i in d.poller._incidents if i["id"] == 9001)["replayed"])
        st = client.get("/replay/remote/status").get_json()
        check("D5 rig status live", st["state"] == "live" and st["seq"] >= 1, st)
        check("D6 /replay_pc", client.get("/replay_pc").get_json()["enabled"])

        # rig down -> local fallback
        calls.clear()
        d.poller.replay_pc._http = lambda *a: (_ for _ in ()).throw(OSError("down"))
        r = client.post("/replay_5s", json={"car_number": "7", "incident_id": 9001}).get_json()
        check("D7 fallback local", r.get("ok") and len(calls) == 1, (r, calls))


if __name__ == "__main__":
    unit()
    if "--dash" in sys.argv:
        dash()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
