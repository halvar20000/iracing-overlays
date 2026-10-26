"""Offline tests for iCASControl step 1: live incidents, replay jump, autosave.

    cd racecontrol && python test_incidents.py

No iRacing, no FastAPI needed (both stubbed).
"""

import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from backend.incidents import (BehaviourDetector, OfficialIncidents,  # noqa: E402
                               SURF_ON_TRACK, SURF_OFF_TRACK,
                               SURF_NOT_IN_WORLD)
from backend.models import Frame, CarFrame, SessionFrame, IncidentEvent  # noqa: E402
from backend.race_state import RaceState  # noqa: E402
from backend.sources import iracing_source as irs  # noqa: E402

PASS = FAIL = 0


def check(name, cond, info=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {info}")


TRACK_M = 4000.0
DT = 0.1


class Sim:
    """Cars as (pct, speed m/s, surface, on_pit) driven by hand."""

    def __init__(self, n):
        self.t = 100.0
        self.cars = {i: [0.1 + 0.05 * i, 50.0, SURF_ON_TRACK, False]
                     for i in range(n)}
        self.det = BehaviourDetector()

    def step(self, secs, racing=True, session=2):
        found = []
        for _ in range(int(round(secs / DT))):
            self.t += DT
            for c in self.cars.values():
                if c[2] != SURF_NOT_IN_WORLD:
                    c[0] = (c[0] + c[1] * DT / TRACK_M) % 1.0
            rows = [(i, c[0], c[2], c[3]) for i, c in self.cars.items()]
            found += self.det.update(self.t, session, racing, rows, TRACK_M)
        return found


def ramp(sim, idx, to, secs, others=None):
    """Change car idx's speed linearly to `to` over `secs`."""
    c = sim.cars[idx]
    n = int(round(secs / DT))
    start = c[1]
    found = []
    for k in range(1, n + 1):
        c[1] = start + (to - start) * k / n
        if others:
            for j in others:
                sim.cars[j][1] = start + (to - start) * k / n
        found += sim.step(DT)
    return found


print("Behaviour detector")
sim = Sim(3)
sim.cars[1][0] = 0.5
sim.cars[2][0] = 0.8
sim.step(3)
found = ramp(sim, 0, 3.0, 1.5) + sim.step(1)
check("solo spin -> 1x spin", [d.kind for d in found] == ["spin"], found)
check("spin text has the speeds", "km/h" in (found[0].text if found else ""))
ramp(sim, 0, 50, 3)
sim.step(10)

sim = Sim(3)
sim.cars[1][0] = sim.cars[0][0] + 0.002          # alongside
sim.cars[2][0] = 0.8
sim.step(3)
found = ramp(sim, 0, 3.0, 1.2, others=[1]) + sim.step(1)
kinds = sorted(d.kind for d in found)
check("two cars collapse together -> contact for both",
      kinds == ["contact", "contact"], found)
check("contact names the other car",
      any(d.car_idx == 0 and d.other_car_idx == 1 for d in found), found)

sim = Sim(2)
sim.cars[1][0] = 0.6
sim.step(3)
found = ramp(sim, 0, 13.0, 2.0) + sim.step(2)    # 47 km/h hairpin
check("braking into a 47 km/h hairpin -> nothing", found == [], found)

sim = Sim(2)
sim.cars[1][0] = 0.6
sim.step(3)
sim.cars[0][3] = True                             # pit road
found = ramp(sim, 0, 2.0, 1.5) + sim.step(1)
check("pit lane stop -> nothing", found == [], found)

sim = Sim(2)
sim.cars[1][0] = 0.6
sim.step(3)
found = sim.step(3, racing=False)
sim.cars[0][1] = 0.0
found += sim.step(5, racing=False)
check("not racing (pace lap / after flag) -> nothing", found == [], found)

sim = Sim(2)
sim.cars[1][0] = 0.6
sim.step(3)
for _ in range(3):                                # connection blinks
    sim.cars[0][2] = SURF_NOT_IN_WORLD
    sim.step(1)
    sim.cars[0][2] = SURF_ON_TRACK
    sim.step(1)
found = ramp(sim, 0, 2.0, 1.5) + sim.step(1)
check("car back from out-of-world is ignored (blink)", found == [], found)

sim = Sim(2)
sim.cars[1][0] = 0.6
sim.step(3)
ramp(sim, 0, 8.0, 12.0)                          # gentle stall, no collapse
sim.cars[0][1] = 0.5
found = sim.step(5)
check("slow stop -> stopped on track", [d.kind for d in found] == ["stopped"],
      found)
found = sim.step(10)
check("stopped said once per 30 s", found == [], found)

sim = Sim(2)
sim.cars[1][0] = 0.6
sim.cars[0][2] = SURF_OFF_TRACK
sim.step(3)
found = ramp(sim, 0, 2.0, 1.5) + sim.step(1)
check("spin off track (gravel) still counts", [d.kind for d in found] == ["spin"],
      found)

sim = Sim(2)
sim.step(3)
sim.t -= 600                                      # replay seek / new session
found = sim.step(2)
check("time jump back resets tracking (no false spin)", found == [], found)

print("Official incident points")
o = OfficialIncidents()
check("attach mid-session = baseline, silent", o.update(4, {0: 6, 1: 2}) == [])
check("a rise -> delta + total", o.update(4, {0: 10, 1: 2}) == [(0, 4, 10)])
check("no change -> nothing", o.update(4, {0: 10, 1: 2}) == [])
check("new session seen live starts at zero",
      o.update(5, {0: 2, 1: 0}) == [(0, 2, 2)])
check("a drop is adopted silently", o.update(5, {0: 0, 1: 0}) == [])
o2 = OfficialIncidents()
o2.seed(5, {"0": 4, "1": 0})
check("seeded baseline (recovery): points while down show up",
      o2.update(5, {0: 8, 1: 0}) == [(0, 4, 8)])


def frame(t, cars, incidents=(), totals=None, replay=False, num=2, key="1-2"):
    return Frame(session=SessionFrame(session_time=t, session_num=num,
                                      session_key=key, in_replay=replay),
                 cars=list(cars), incidents=list(incidents),
                 incident_totals=totals, source_name="iracing", connected=True)


def cf(idx, num, pit=False, surf="OnTrack", lap=3):
    return CarFrame(car_idx=idx, car_number=num, driver_name=f"D{num}",
                    on_pit_road=pit, track_surface=surf, lap=lap,
                    position=idx + 1)


print("Race state")
rs = RaceState()
cars = [cf(0, "7"), cf(1, "14")]
rs.ingest(frame(10, cars, totals={0: 0, 1: 0}))
rs.ingest(frame(11, cars, [IncidentEvent(0, 0, "contact", 3, 1,
                                         source="detected", text="contact")]))
incs = [e for e in rs.events if e.is_incident]
check("detection logged as incident (no points)",
      len(incs) == 1 and incs[0].inc_points == 0
      and "Contact with #14 (detected)" in incs[0].text, incs[0].text if incs else "")
check("detection has the session number (replay seek)", incs[0].session_num == 2)
rs.ingest(frame(15, cars, [IncidentEvent(0, 4, "incident", 3, source="points",
                                         total=4)], totals={0: 4, 1: 0}))
incs = [e for e in rs.events if e.is_incident]
check("official 4x 4 s later lands ON the detection",
      len(incs) == 1 and incs[0].inc_points == 4 and "4x" in incs[0].text
      and "detected" not in incs[0].text, [e.text for e in incs])
check("car incident points = official total", rs.cars[0].incident_points == 4)
check("unresolved counted once", rs.cars[0].unresolved == 1)
check("official_points flag set", rs.official_points)
rs.ingest(frame(60, cars, [IncidentEvent(1, 2, "incident", 3, source="points",
                                         total=2)], totals={0: 4, 1: 2}))
incs = [e for e in rs.events if e.is_incident]
check("points without a detection -> own line", len(incs) == 2
      and incs[1].text == "Incident - 2x (total 2x)", incs[-1].text)

# replay frames: nothing logged, clock stays
n_before = len(rs.events)
past = [cf(0, "7", pit=True), cf(1, "14", surf="OffTrack")]
rs.ingest(frame(20, past, replay=True))
check("replay frame logs nothing", len(rs.events) == n_before,
      [e.text for e in rs.events[n_before:]])
check("replay keeps the live clock", rs.session_time == 60)
check("snapshot says in_replay", rs.snapshot()["session"]["in_replay"])
rs.ingest(frame(20.1, past, [IncidentEvent(1, 4, "incident", 3, source="points",
                                           total=6)], totals={0: 4, 1: 6},
                replay=True))
check("official points during a replay still logged (live clock)",
      rs.events[-1].inc_points == 4 and rs.events[-1].sim_time == 60)
rs.ingest(frame(61, cars))
check("back live", not rs.in_replay)

# decisions survive save / load
ev_id = incs[0].id
rs.resolve_incident(ev_id, "DRIVE THROUGH")
data = json.loads(json.dumps(rs.to_saved()))
rs2 = RaceState()
rs2.ingest(frame(62, cars, totals={0: 4, 1: 6}))
n = rs2.load_saved(data)
check("load_saved restores every line", n == len(rs.events))
ev2 = next(e for e in rs2.events if e.id == ev_id)
check("decision restored", ev2.resolved and ev2.resolution == "DRIVE THROUGH")
check("per-car unresolved restored", rs2.cars[0].unresolved == 0
      and rs2.cars[1].unresolved == 2, (rs2.cars[0].unresolved,
                                        rs2.cars[1].unresolved))
rs2.note_system("after restore")
check("new ids continue after the restored ones",
      rs2.events[-1].id > max(e.id for e in rs.events))

# incidents are never truncated
rs3 = RaceState()
rs3.ingest(frame(1, cars))
rs3.ingest(frame(2, cars, [IncidentEvent(0, 0, "spin", 1, source="detected")]))
for i in range(5100):
    rs3.note_system(f"line {i}")
check("old incident kept when the log overflows",
      any(e.is_incident for e in rs3.events) and len(rs3.events) <= 5001)
pay = rs3.events_payload()
check("payload: all incidents + newest 400 others",
      sum(1 for e in pay["events"] if e["is_incident"]) == 1
      and len(pay["events"]) == 401, len(pay["events"]))

print("iRacing source helpers")
check("replay target 8 s before", irs.replay_target(100.0, 8) == 92000)
check("replay target never negative", irs.replay_target(3.0, 8) == 0)
groups = [{"GroupNum": 1, "GroupName": "Nose"},
          {"GroupNum": 10, "GroupName": "TV1"},
          {"GroupNum": 17, "GroupName": "Scenic"}]
check("cam group kept", irs.pick_cam_group(groups, 1) == 1)
check("Scenic -> TV1", irs.pick_cam_group(groups, 17) == 10)


class FakeIR(dict):
    is_connected = True

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls = []

    def freeze_var_buffer_latest(self):
        pass

    def unfreeze_var_buffer_latest(self):
        pass

    def replay_search_session_time(self, num, ms):
        self.calls.append(("seek", num, ms))
        self["SessionNum"], self["SessionTime"] = num, ms / 1000.0

    def cam_switch_num(self, num, group, cam):
        self.calls.append(("cam", num, group))

    def replay_set_play_speed(self, speed, slow):
        self.calls.append(("play", speed))


def fake_ir(t, totals, replay=False, n=2):
    return FakeIR({
        "WeekendInfo": {"TrackName": "nope", "TrackDisplayName": "Test",
                        "TrackLength": "4.00 km", "SessionID": 111,
                        "SubSessionID": 222},
        "DriverInfo": {"DriverCarIdx": -1, "Drivers": [
            {"CarIdx": i, "CarNumber": str(10 + i), "UserName": f"D{i}",
             "CarClassColor": 0} for i in range(n)]},
        "SessionInfo": {"Sessions": [{"SessionType": "Race",
                                      "ResultsPositions": [
                                          {"CarIdx": i, "Incidents": v}
                                          for i, v in totals.items()]}]},
        "SessionNum": 0, "SessionTime": t, "SessionState": 4,
        "CarIdxLapDistPct": [0.1, 0.5], "CarIdxLap": [3, 3],
        "CarIdxLapCompleted": [2, 2], "CarIdxTrackSurface": [3, 3],
        "CarIdxOnPitRoad": [False, False],
        "IsReplayPlaying": replay, "ReplayFrameNumEnd": 900 if replay else 0,
        "CameraInfo": {"Groups": groups}, "CamGroupNumber": 17,
    })


src = irs.IRacingSource()
src._ir = fake_ir(50, {0: 3, 1: 0})
f1 = src._read_frame(0.1)
check("frame carries session key + number",
      f1.session.session_key == "111-222" and f1.session.session_num == 0)
check("first totals = baseline, no event",
      f1.incidents == [] and f1.incident_totals == {0: 3, 1: 0})
src._inc_next = 0
src._ir = fake_ir(52, {0: 3, 1: 4})
f2 = src._read_frame(0.1)
pts = [i for i in f2.incidents if i.source == "points"]
check("rise -> points event", len(pts) == 1 and pts[0].car_idx == 1
      and pts[0].points == 4 and pts[0].total == 4, f2.incidents)
src._ir = fake_ir(53, {0: 3, 1: 4}, replay=True)
check("in_replay flagged", src._read_frame(0.1).session.in_replay)

ir = fake_ir(53, {})
src._ir = ir
msg = src.send_command("replay_event", session_num=0, sim_time=120.0,
                       number="14", preroll=8)
import time as _t  # noqa: E402
for _ in range(50):
    if not src._replay_busy:
        break
    _t.sleep(0.1)
check("replay_event accepted", "replay of #14" in msg, msg)
check("replay: seek, camera (Scenic -> TV1), play",
      ir.calls == [("seek", 0, 112000), ("cam", "14", 10), ("play", 1)],
      ir.calls)

print("Engine autosave / restore (FastAPI stubbed)")


def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class _App:
    def __init__(self, *a, **k):
        pass

    def _deco(self, *a, **k):
        return lambda f: f
    get = websocket = post = _deco

    def mount(self, *a, **k):
        pass


_stub("fastapi", FastAPI=_App, WebSocket=object, WebSocketDisconnect=Exception)
_stub("fastapi.responses", JSONResponse=dict, Response=dict)
_stub("fastapi.staticfiles", StaticFiles=lambda **k: None)
argv = sys.argv
sys.argv = ["x", "--sim"]
from backend import server  # noqa: E402
sys.argv = argv

tmp = tempfile.mkdtemp()
server.DATA_DIR = __import__("pathlib").Path(tmp)
eng = server.Engine(force_sim=True)
src = irs.IRacingSource()
src._ir = fake_ir(50, {0: 3, 1: 0})
eng.source = src
eng.state = RaceState()
eng.tick()
src._ir = fake_ir(51, {0: 3, 1: 0})
eng.state.resolve_incident(-1, "NO ACTION")       # nothing - just a tick
eng.state.car_action(1, "add_note", "watch him")
eng._maybe_autosave(force=True)
path = os.path.join(tmp, "111-222.json")
check("autosave written as <SessionID-SubSessionID>.json", os.path.isfile(path))
saved = json.load(open(path))
check("autosave holds the official baseline",
      saved.get("official", {}).get("totals") in ({"0": 3, "1": 0}, {0: 3, 1: 0}),
      saved.get("official"))

# "crash": a new engine + source; meanwhile car 1 got 4x
eng2 = server.Engine(force_sim=True)
src2 = irs.IRacingSource()
src2._ir = fake_ir(400, {0: 3, 1: 4})
eng2.source = src2
eng2.state = RaceState()
eng2.tick()                                       # restore happens here
src2._inc_next = 0
eng2.tick()
texts = [e.text for e in eng2.state.events]
check("restored after restart", any("Restored the race" in t for t in texts),
      texts)
check("note survived", any("watch him" in t for t in texts))
check("points scored while down are logged (not swallowed, not doubled)",
      any(e.inc_points == 4 and e.car_idx == 1 for e in eng2.state.events),
      texts)

eng3 = server.Engine(force_sim=True)
check("initial messages serialise (dashboard connect)",
      bool(json.dumps(eng2.initial_messages())))
check("simulator: no autosave path", eng3.save_path() is None)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
