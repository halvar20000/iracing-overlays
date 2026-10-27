"""Offline tests for iCASControl step 1: live incidents, replay jump, autosave.

    cd racecontrol && python test_incidents.py

No iRacing, no FastAPI needed (both stubbed).
"""

import json
import os
import sys
import tempfile
import types
from pathlib import Path

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
# 2026-10-27 hosted session, admin: points only in the driver list
src._inc_next = 0
ir3 = fake_ir(54, {0: 3, 1: 4})
ir3["DriverInfo"]["Drivers"][0]["TeamIncidentCount"] = 7
ir3["DriverInfo"]["Drivers"][0]["CurDriverIncidentCount"] = 5
ir3["DriverInfo"]["Drivers"][1]["TeamIncidentCount"] = -1
src._ir = ir3
f3 = src._read_frame(0.1)
pts = [i for i in f3.incidents if i.source == "points"]
check("live count from DriverInfo TeamIncidentCount (results still 3) -> +4x",
      len(pts) == 1 and pts[0].car_idx == 0 and pts[0].points == 4
      and pts[0].total == 7 and f3.incident_totals[1] == 4, f3.incidents)
check("debug shows both sources", src.inc_debug["rows"][0].get("team_inc") == 7
      and src.inc_debug["rows"][0].get("incidents") == 3, src.inc_debug["rows"])
check("sector status reported", src.sector_status.get("on") is True
      and src.sector_status.get("sectors") == 3, src.sector_status)
dbg = src.inc_debug
check("debug: raw rows as iRacing sent them",
      dbg["result_rows"] == 2 and dbg["cars_with_points"] == 2
      and dbg["rows"][1]["incidents"] == 4 and dbg["rows"][1]["number"] == "11",
      dbg)
src._ir = fake_ir(53, {0: 3, 1: 4}, replay=True)
check("in_replay flagged", src._read_frame(0.1).session.in_replay)

# 2026-10-27: iCAS stayed on CAUTION after the restart - local yellow bits
# (0x08 / 0x100) counted as a caution; iRacing keeps green 0x04 on throughout
from backend.race_state import RaceState as _RS
from backend import steward as _stw
def flagframe(t, bits, state=4):
    ir = fake_ir(t, {0: 3, 1: 4})
    ir["SessionFlags"], ir["SessionState"] = bits, state
    src._ir = ir
    return src._read_frame(0.1)
fs = _RS()
seq = [(60, 0x0004), (61, 0x0004 | 0x8000), (70, 0x0004 | 0x4000 | 0x0008),
       (120, 0x0004 | 0x4000 | 0x0200), (150, 0x0004 | 0x0100 | 0x0008)]
got = []
for t, bits in seq:
    fr = flagframe(t, bits)
    fs.ingest(fr)
    got.append((list(fr.session.flags), _stw._caution_out(fs)))
check("caution bits -> caution", got[1][1] and got[2][1] and "caution" in got[1][0], got)
check("one to green lap still a caution", got[3][1] and "one_to_green" in got[3][0], got)
check("restart: green + local yellow bits left -> NO caution, flags green",
      not got[4][1] and got[4][0][0] == "green" and "local_yellow" in got[4][0], got)
flog = [e.text for e in fs.events if e.category == "flag"] \
    if fs.events and hasattr(fs.events[0], "category") else \
    [e.text for e in fs.events]
check("log: CAUTION, ONE TO GREEN, GREEN - caution over",
      any("CAUTION" in x for x in flog) and any("ONE TO GREEN" in x for x in flog)
      and any("caution over" in x for x in flog), flog)
check("raw bits kept", fs.flags_raw == (0x0004 | 0x0100 | 0x0008))
check("only local yellow, no caution ever -> green",
      flagframe(200, 0x0008).session.flags[0] == "green")
check("one-to-green bit on the pace lap (parade) = not a caution",
      "one_to_green" not in flagframe(5, 0x0200, state=3).session.flags)

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

print("Real-session fixes (Spa AI practice 27.10.)")
labels = irs.class_labels([
    {"CarIdx": 0, "CarClassShortName": None, "CarClassID": 5,
     "CarScreenNameShort": "BMW M4 GT3"},
    {"CarIdx": 1, "CarClassShortName": None, "CarClassID": 5,
     "CarScreenNameShort": "Porsche 911 GT3 R"},
    {"CarIdx": 2, "CarClassShortName": None, "CarClassID": 9,
     "CarScreenNameShort": "Porsche 911 Cup"},
    {"CarIdx": 3, "CarClassShortName": "LMP2", "CarClassID": 7}])
check("class null -> never 'None': GT3 / model / real name",
      labels == {0: "GT3", 1: "GT3", 2: "Porsche 911 Cup", 3: "LMP2"}, labels)
labels2 = irs.class_labels([
    {"CarIdx": i, "CarClassShortName": None, "CarClassID": 0,
     "CarScreenNameShort": n} for i, n in enumerate(
        ["Ferrari 296 GT3", "Chevrolet Corvette Z06 GT3.R", "BMW M4 GT3 EVO"])])
check("'GT3.R' still shares GT3 (Spa race showed 'Class 0')",
      set(labels2.values()) == {"GT3"}, labels2)
check("livery colour from CarDesignStr",
      irs.design_color("22,ff0000,000000,ffffff") == "#ff0000"
      and irs.design_color("") == "" and irs.design_color("1,zz") == "")
check("licence colour int / hex string",
      irs._hex_color(0x0153db) == "#0153db" and irs._hex_color("0x00c702") == "#00c702"
      and irs._hex_color(None) == "")
rir = fake_ir(70, {})
rir["CarIdxLastLapTime"] = [144.95, 145.31]
rir["CarIdxBestLapTime"] = [-1, -1]
src4 = irs.IRacingSource()
src4._ir = rir
rf = {c.car_idx: c for c in src4._read_frame(0.1).cars}
check("race lap 1: best = last (iRacing leaves the best empty)",
      rf[0].best_lap == 144.95 and rf[1].best_lap == 145.31, rf)
rir2 = fake_ir(71, {})
rir2["CarIdxLastLapTime"] = [146.0, 144.0]
rir2["CarIdxBestLapTime"] = [-1, -1]
src4._ir = rir2
rf = {c.car_idx: c for c in src4._read_frame(0.1).cars}
check("best keeps the faster lap", rf[0].best_lap == 144.95
      and rf[1].best_lap == 144.0, rf)
pir = fake_ir(60, {})
pir["SessionInfo"]["Sessions"][0]["SessionType"] = "Practice"
pir["CarIdxBestLapTime"] = [139.5, 138.2]
pir["CarIdxLapCompleted"] = [-1, 3]
pir["CarIdxLap"] = [-1, 4]
src2 = irs.IRacingSource()
src2._ir = pir
pf = {c.car_idx: c for c in src2._read_frame(0.1).cars}
check("practice: order by best lap, gap = lap-time gap",
      pf[1].position == 1 and pf[0].position == 2
      and abs(pf[0].gap_to_leader - 1.3) < 1e-6, (pf[0], pf[1]))
check("lap -1 shown as 0", pf[0].laps_completed == 0 and pf[0].lap == 0)
rs3 = RaceState()
rs3.ingest(frame(1, [cf(0, "7", pit=True), cf(1, "14")]))
rs3.ingest(frame(80, [cf(0, "7"), cf(1, "14")]))
rs3.ingest(frame(90, [cf(0, "7", pit=True), cf(1, "14")]))
rs3.ingest(frame(120, [cf(0, "7"), cf(1, "14")]))
pits = [e.text for e in rs3.events if e.category == "pit"]
check("leaving the pits at the start is no stop; a real one is #1",
      pits == ["Left the pits", "Entered pit lane",
               "Exited pit lane - stop #1 (30.0s)"]
      and rs3.cars[0].pit_stops == 1, pits)
rs4 = RaceState()
pract = frame(1, [cf(0, "7"), cf(1, "14")])
pract.session.session_type = "PRACTICE"
rs4.ingest(pract)
pract2 = frame(2, [cf(1, "14"), cf(0, "7")])
pract2.cars[0].position, pract2.cars[1].position = 1, 2
pract2.session.session_type = "PRACTICE"
rs4.ingest(pract2)
check("no 'takes the lead' in practice",
      not any("lead" in e.text for e in rs4.events))

print("Sector times")
src3 = irs.IRacingSource()
check("sector timing available", src3.sectors is not None)


def sec_ir(t, p0, p1, replay=False):
    ir = fake_ir(t, {})
    ir["SplitTimeInfo"] = {"Sectors": [{"SectorNum": 0, "SectorStartPct": 0.0},
                                       {"SectorNum": 1, "SectorStartPct": 0.5}]}
    ir["CarIdxLapDistPct"] = [p0, p1]
    ir["IsReplayPlaying"] = replay
    ir["ReplayFrameNumEnd"] = 900 if replay else 0
    return ir


# car 0 at 0.01 / lap (fast), car 1 at 0.009 / lap, 1 sample per 0.5 s
last = None
for k in range(260):
    t = 100 + k * 0.5
    src3._ir = sec_ir(t, (0.30 + k * 0.01) % 1.0, (0.28 + k * 0.009) % 1.0)
    last = src3._read_frame(0.5)
by = {c.car_idx: c.sectors for c in last.cars}
check("two sector columns per car", len(by[0]) == 2 and len(by[1]) == 2, by)
s0 = [v for v in by[0] if v]
check("car 0: sector times ~25 s, fastest = overall (purple)",
      s0 and all(abs(v[0] - 25.0) < 0.05 for v in s0)
      and all(v[1] == "overall" for v in s0), by[0])
s1 = [v for v in by[1] if v]
check("car 1: slower, its own best = personal (green)",
      s1 and all(v[1] == "personal" for v in s1), by[1])
rs5 = RaceState()
rs5.ingest(last)
snap = rs5.snapshot()
check("snapshot: sector_count + per-car sectors",
      snap["session"]["sector_count"] == 2
      and snap["cars"][0]["sectors"] == by[snap["cars"][0]["car_idx"]])
before = dict(src3.sectors.best and {i: b for i, b in enumerate(src3.sectors.best)})
for k in range(60):
    t = 50 + k * 0.5                     # replay of earlier footage, faster
    src3._ir = sec_ir(t, (k * 0.03) % 1.0, 0.5, replay=True)
    src3._read_frame(0.5)
check("replay footage sets no sector times",
      {i: b for i, b in enumerate(src3.sectors.best)} == before)

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
    get = websocket = post = middleware = _deco

    def mount(self, *a, **k):
        pass


def snap_of(eng):
    return next(m for m in eng.initial_messages() if m.get("type") == "snapshot")


_stub("fastapi", FastAPI=_App, WebSocket=object, WebSocketDisconnect=Exception)
_stub("fastapi.responses", JSONResponse=dict, Response=dict)
_stub("fastapi.staticfiles", StaticFiles=type("StaticFiles", (), {"__init__": lambda self, **k: None}))
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

print("Chat commands into iRacing (step 2)")
from backend import chat  # noqa: E402
chat.CONFIG_PATH = __import__("pathlib").Path(tmp) / "chat_config.json"
b = chat.build
check("official syntax",
      b("drive_through", num="14") == "!black #14 D"
      and b("stop_go", num="#14") == "!black #14"
      and b("stop_go", num="14", sec=30) == "!black #14 30"
      and b("dsq", num="7", msg="Track limits") == "!dq #7 Track limits"
      and b("eol", num="3") == "!eol #3" and b("wave_around", num="3") == "!waveby #3"
      and b("clear_penalties", num="3") == "!clear #3"
      and b("clear_all") == "!clearall" and b("pace_deploy") == "!yellow"
      and b("pit_close") == "!pitclose" and b("message_all", msg="Hi\nall") == "/all Hi all"
      and b("warning", num="9", driver="A Hill", msg="track limits")
      == "/all #9 A Hill: WARNING - track limits",
      [b("drive_through", num="14"), b("stop_go", num="#14")])
check("no command -> None (red flag, car missing)",
      b("red_flag") is None and b("drive_through") is None)
check("template override from the config",
      b("notify", {"notify": "/#{num} {msg}"}, num="5", msg="box") == "/#5 box")


class FakeKeys:
    def __init__(self):
        self.out = []

    def find_window(self):
        return 77

    def post_char(self, hwnd, ch):
        self.out.append(("c", hwnd, ch))

    def post_enter(self, hwnd):
        self.out.append(("enter", hwnd))

    def foreground(self):
        return 5

    def set_foreground(self, h):
        self.out.append(("fg", h))
        return True

    def type_text(self, text, delay):
        self.out.append(("type", text))


fk = FakeKeys()
done = []
opened = []
cs = chat.ChatSender(lambda: opened.append(1),
                     lambda t, ok, i: done.append((t, ok, i)), lambda: fk)
cs.send("!eol #3", "post", 0)
cs.send("/rc x", "focus", 0)
for _ in range(50):
    if len(done) == 2:
        break
    _t.sleep(0.1)
chars = "".join(o[2] for o in fk.out if o[0] == "c")
check("post: chat opened, every char posted, then Enter",
      chars == "!eol #3" and ("enter", 77) in fk.out and len(opened) == 2
      and done[0] == ("!eol #3", True, "posted"), fk.out)
check("focus: iRacing to the front, typed, focus given back",
      fk.out[-3:] == [("fg", 77), ("type", "/rc x"), ("fg", 5)], fk.out[-4:])
nowin = chat.ChatSender(lambda: None, lambda t, ok, i: done.append((t, ok, i)),
                        lambda: type("K", (FakeKeys,), {"find_window": lambda s: 0})())
nowin.send("x", "post", 0)
for _ in range(30):
    if len(done) == 3:
        break
    _t.sleep(0.1)
check("no iRacing window -> reported, not crashed",
      done[-1] == ("x", False, "iRacing window not found"), done)

# IRacingSource: send + verification by flag bits
chat_ir = fake_ir(80, {})
chat_ir["CarIdxSessionFlags"] = [0, 0]
chat_ir["CarIdxPaceFlags"] = [0, 0]
chat_ir.chat_command = lambda mode: chat_ir.calls.append(("chat", mode))
csrc = irs.IRacingSource()
csrc._ir = chat_ir
csrc._chat.keys_factory = lambda: fk
r = csrc.send_chat("!black #11 D", "post", 0, ("black", 1))
for _ in range(50):
    if csrc._chat_checks:
        break
    _t.sleep(0.1)
check("send_chat: SDK opens the chat (begin_chat)", ("chat", 1) in chat_ir.calls
      and r == "to iRacing: !black #11 D", (chat_ir.calls, r))
csrc._check_chat()
check("not confirmed yet -> still waiting", len(csrc._chat_checks) == 1
      and not csrc.drain_command_log())
chat_ir["CarIdxSessionFlags"] = [0, irs.FLAG_BLACK]
csrc._check_chat()
check("black flag bit set -> 'iRacing confirmed'",
      csrc.drain_command_log() == ["iRacing confirmed: !black #11 D"])
def wait_log():
    for _ in range(50):
        if csrc._command_log or csrc._chat_checks:
            return
        _t.sleep(0.1)
# 2026-10-27 hosted session: EOL with no caution out - iRacing said "Can't set
# EOL when not under caution", the car's EOL bit was already set, we said
# "confirmed"
chat_ir["SessionFlags"] = 0
chat_ir["CarIdxPaceFlags"] = [0, irs.PACE_EOL]
csrc.send_chat("!eol #11", "post", 0, ("eol", 1))
wait_log()
log = csrc.drain_command_log()
check("EOL with no caution out -> no 'confirmed', says caution needed",
      len(log) == 1 and "only accepts this under a full course caution" in log[0]
      and not csrc._chat_checks, log)
chat_ir["SessionFlags"] = 0x4000
csrc.send_chat("!eol #11", "post", 0, ("eol", 1))
wait_log()
log = csrc.drain_command_log()
check("bit already set before the command -> 'cannot confirm', not confirmed",
      len(log) == 1 and "cannot confirm" in log[0] and not csrc._chat_checks, log)
chat_ir["CarIdxPaceFlags"] = [0, 0]
csrc.send_chat("!eol #11", "post", 0, ("eol", 1))
wait_log()
chat_ir["CarIdxPaceFlags"] = [0, irs.PACE_EOL]
csrc._check_chat()
check("under caution, bit goes off -> on -> confirmed",
      csrc.drain_command_log() == ["iRacing confirmed: !eol #11"])
chat_ir["CarIdxPaceFlags"] = [0, 0]
csrc.send_chat("!eol #11", "post", 0, ("eol", 1))
wait_log()
csrc._chat_checks[0]["until"] = 0
csrc._check_chat()
log = csrc.drain_command_log()
check("no effect within the time -> warning with the raw flag bits",
      len(log) == 1 and "did NOT confirm" in log[0] and "caution" in log[0]
      and "pace 0x0" in log[0], log)

# Engine routing
eng4 = server.Engine(force_sim=True)
eng4.state = RaceState()
eng4.state.ingest(frame(10, [cf(0, "7"), cf(1, "14")]))
sent = []
eng4.source = type("S", (), {"name": "iracing",
                             "send_chat": lambda self, *a: sent.append(a) or "to iRacing: " + a[0],
                             "send_command": lambda self, c, **k: "x"})()
check("chat OFF by default: nothing typed, logged",
      not eng4.chat_cfg["enabled"]
      and "chat OFF" in eng4.handle_client_message(
          {"action": "car", "car_idx": 1, "command": "eol"})["text"] and not sent)
eng4.handle_client_message({"action": "set_chat", "enabled": True})
check("switch saved to data/chat_config.json",
      json.load(open(chat.CONFIG_PATH))["enabled"] is True)
eng4.handle_client_message({"action": "car", "car_idx": 1, "command": "eol"})
check("car EOL -> '!eol #14', verify eol on car 1",
      sent[-1] == ("!eol #14", "post", 6.0, ("eol", 1)), sent)
eng4.state.ingest(frame(11, [cf(0, "7"), cf(1, "14")],
                        [IncidentEvent(1, 0, "contact", 3, 0, source="detected",
                                       text="contact")]))
inc = [e for e in eng4.state.events if e.is_incident][-1]
eng4.handle_client_message({"action": "resolve", "id": inc.id,
                            "resolution": "DRIVE THROUGH"})
check("decision DRIVE THROUGH -> '!black #14 D', verify black",
      sent[-1] == ("!black #14 D", "post", 6.0, ("black", 1)), sent[-1])
eng4.handle_client_message({"action": "command", "command": "pace_deploy"})
check("Deploy Pace Car -> '!yellow', verify the caution",
      sent[-1] == ("!yellow", "post", 6.0, ("yellow", -1)), sent[-1])
# right-click menu (car actions with a reason)
k = len(sent)
eng4.handle_client_message({"action": "car", "car_idx": 1, "command": "drive_through",
                            "message": "Track Limits", "seconds": 0})
check("menu Drive through + reason -> '!black #14 D' then the reason line",
      [x[0] for x in sent[k:]] == ["!black #14 D", "/all #14 D14: DRIVE THROUGH - Track Limits"],
      sent[k:])
k = len(sent)
eng4.handle_client_message({"action": "car", "car_idx": 1, "command": "stop_go",
                            "message": "", "seconds": 0})
check("menu Stop and Go without reason -> only '!black #14'",
      [x[0] for x in sent[k:]] == ["!black #14"], sent[k:])
k = len(sent)
eng4.handle_client_message({"action": "car", "car_idx": 1, "command": "time_penalty",
                            "message": "Causing a collision", "seconds": 5})
check("menu Time penalty -> only the announcement (iRacing has none)",
      len(sent) - k == 1 and "TIME PENALTY" in sent[-1][0]
      and "Causing a collision" in sent[-1][0], sent[k:])
for cmd, txt in (("add_admin", "!admin #14"), ("remove_admin", "!nadmin #14"),
                 ("chat_on", "!chat #14"), ("chat_off", "!nchat #14"),
                 ("remove", "!remove #14 Unsportsmanlike")):
    eng4.handle_client_message({"action": "car", "car_idx": 1, "command": cmd,
                                "message": "Unsportsmanlike" if cmd == "remove" else ""})
    check(f"menu {cmd} -> '{txt}'", sent[-1][0] == txt, sent[-1])
check("menu penalties are in the log with the reason",
      any("Track Limits" in e.text for e in eng4.state.events))
n = len(sent)
ack = eng4.handle_client_message({"action": "command", "command": "red_flag"})
check("red flag: no iRacing command, only logged", len(sent) == n
      and "logged only" in ack["text"])
eng4.handle_client_message({"action": "rc_message", "target": "RC",
                            "text": "Track limits T4", "car_idx": -1})
check("RC message -> '/all ...'", sent[-1][0] == "/all Track limits T4")
eng4.handle_client_message({"action": "set_chat", "enabled": False})
eng4.handle_client_message({"action": "chat_test"})
check("Test works while OFF ('/rc', admins only)",
      sent[-1][0] == "/rc iCASControl chat test")
eng5 = server.Engine(force_sim=True)
eng5.state = eng4.state
ack = eng5.handle_client_message({"action": "car", "car_idx": 1, "command": "dsq"})
check("simulator: command only written to the log",
      "not iRacing" in ack["text"]
      and any("Would send to iRacing: !dq #14" in e.text
              for e in eng5.state.events), ack)
check("snapshot carries the chat status",
      snap_of(eng4)["chat"] == {"enabled": False, "method": "post",
                                              "available": True})

print("Decisions to the stream banner (step 3)")
from backend import stream  # noqa: E402
stream.CONFIG_PATH = __import__("pathlib").Path(tmp) / "stream_config.json"
eng6 = server.Engine(force_sim=True)
eng6.state = RaceState()
eng6.state.ingest(frame(10, [cf(0, "7"), cf(1, "14")]))
posted = []
eng6.banner.post = lambda url, body: posted.append((url, body)) or (True, "HTTP 200")
check("stream OFF by default: nothing posted",
      "stream OFF" in eng6.handle_client_message(
          {"action": "car", "car_idx": 1, "command": "dsq"})["text"] and not posted)
eng6.handle_client_message({"action": "set_stream", "enabled": True,
                            "banner_url": "192.168.86.138:5019/"})
check("switch + address saved to data/stream_config.json",
      json.load(open(stream.CONFIG_PATH)) ["banner_url"] == "http://192.168.86.138:5019"
      and stream.load_config()["enabled"] is True)
eng6.banner.send = lambda url, body, wait=False: stream.BannerOut.send(
    eng6.banner, url, body, wait=True)
eng6.handle_client_message({"action": "car", "car_idx": 1, "command": "drive_through",
                            "message": "Track Limits"})
url, body = posted[-1]
check("car drive-through -> banner penalty for #14 with the reason",
      url == "http://192.168.86.138:5019/rc/steward" and body["kind"] == "penalty"
      and body["headline"] == "DRIVE THROUGH PENALTY" and body["car"] == "14"
      and body["text"] == "Track Limits" and body["source"] == "icas", body)
eng6.handle_client_message({"action": "car", "car_idx": 1, "command": "time_penalty",
                            "seconds": 5, "message": ""})
check("time penalty headline carries the seconds",
      posted[-1][1]["headline"] == "5 SECOND TIME PENALTY", posted[-1][1])
eng6.handle_client_message({"action": "car", "car_idx": 1, "command": "clear_penalties"})
check("clear penalties -> kind clear", posted[-1][1]["kind"] == "clear")
k = len(posted)
eng6.handle_client_message({"action": "car", "car_idx": 1, "command": "eol"})
check("EOL is not a stream message", len(posted) == k)
eng6.state.ingest(frame(11, [cf(0, "7"), cf(1, "14")],
                        [IncidentEvent(1, 0, "contact", 3, 0, source="detected",
                                       text="contact")]))
inc = [e for e in eng6.state.events if e.is_incident][-1]
eng6.handle_client_message({"action": "resolve", "id": inc.id,
                            "resolution": "NOTED"})
check("NOTED never goes on air", len(posted) == k)
eng6.handle_client_message({"action": "resolve", "id": inc.id,
                            "resolution": "UNDER INVESTIGATION"})
b6 = posted[-1][1]
check("UNDER INVESTIGATION -> investigation, both cars named",
      b6["kind"] == "investigation" and b6["text"].startswith("#14 and #7"), b6)
eng6.handle_client_message({"action": "resolve", "id": inc.id,
                            "resolution": "WARNING", "message": "Avoidable contact"})
check("WARNING -> warning with the reason",
      posted[-1][1]["kind"] == "warning" and posted[-1][1]["text"] == "Avoidable contact")
def new_inc6(t):
    eng6.state.ingest(frame(t, [cf(0, "7"), cf(1, "14")],
                            [IncidentEvent(1, 0, "contact", 3, 0, source="detected",
                                           text="contact")]))
    return [e for e in eng6.state.events if e.is_incident][-1]
eng6.handle_client_message({"action": "resolve", "id": new_inc6(12).id,
                            "resolution": "TIME PENALTY", "seconds": 10})
check("decision TIME PENALTY -> '10 SECOND TIME PENALTY'",
      posted[-1][1]["headline"] == "10 SECOND TIME PENALTY")
eng6.handle_client_message({"action": "rc_message", "target": "RC",
                            "text": "Pit lane closed", "car_idx": -1})
check("RC message -> banner message", posted[-1][1]["kind"] == "message"
      and posted[-1][1]["text"] == "Pit lane closed" and posted[-1][1]["car"] == "")
eng6.handle_client_message({"action": "set_stream", "kinds": {"warning": False}})
k = len(posted)
ack = eng6.handle_client_message({"action": "resolve", "id": new_inc6(13).id,
                                  "resolution": "WARNING"})
check("a kind switched off is not sent", len(posted) == k
      and "not sent to the stream" in ack["text"], ack)
eng6._drain_stream_log()
check("banner result written to the log",
      any("On the stream banner: DRIVE THROUGH PENALTY #14" in e.text
          for e in eng6.state.events))
eng6.banner.post = lambda url, body: (False, "Connection refused")
eng6.handle_client_message({"action": "stream_test"})
eng6._drain_stream_log()
check("banner not reached -> clear log line",
      any("Stream banner NOT reached (Connection refused)" in e.text
          for e in eng6.state.events))
check("snapshot carries the stream status",
      snap_of(eng6)["stream"]["banner_url"] == "http://192.168.86.138:5019")

print("Auto Steward (step 3c)")
from backend import steward  # noqa: E402
steward.CONFIG_PATH = __import__("pathlib").Path(tmp) / "steward_config.json"
R = steward.clean({})
check("limit levels 14/15/20/21/27 -> 0/1/1/2/3",
      [steward.level(n, R) for n in (14, 15, 20, 21, 27)] == [0, 1, 1, 2, 3])
check("threshold of DT #3 = 27x", steward.threshold(3, R) == 27)


def sframe(t, cars, incidents=(), totals=None, stype="RACE", state="RACING",
           flags=(), num=2, key="9-9", length=4.0):
    return Frame(session=SessionFrame(session_time=t, session_num=num,
                                      session_key=key, session_type=stype,
                                      session_state=state, flags=list(flags),
                                      track_length_km=length),
                 cars=list(cars), incidents=list(incidents),
                 incident_totals=totals, source_name="iracing", connected=True)


def scar(idx, num, lap=3, pct=0.5, best=100.0, pit=False):
    return CarFrame(car_idx=idx, car_number=num, driver_name=f"D{num}", lap=lap,
                    lap_dist_pct=pct, best_lap=best, on_pit_road=pit,
                    position=idx + 1)


eng7 = server.Engine(force_sim=True)
eng7.state = RaceState()
sent7 = []
eng7.source = type("S", (), {"name": "iracing",
                             "send_chat": lambda self, *a: sent7.append(a[0]) or "ok",
                             "send_command": lambda self, c, **k: "x"})()
eng7.chat_cfg["enabled"] = True
# the tests below were written for 10x / 10 s and the own blue-flag hint
eng7.steward_cfg = steward.clean({"fcy_incs": 10, "fcy_window_s": 10,
                                  "blue_enabled": True})


def step(fr):
    eng7.state.steward_rules = eng7.steward_cfg
    eng7.state.ingest(fr)
    for act in eng7.auto.check(eng7.state, eng7.steward_cfg):
        eng7._steward_action(act)
    eng7._maybe_caution_laps()


def autos(kind):
    return [e for e in eng7.state.events if e.kind == kind]


five = [scar(i, str(10 + i), pct=0.1 * i) for i in range(5)]
step(sframe(10, five, totals={0: 14}))
check("14x: no limit message", not autos("auto_steward"))
step(sframe(11, five, totals={0: 15}))
a = autos("auto_steward")
check("15x: one AUTO STEWARD line, drive through due, no chat",
      len(a) == 1 and "reached 15x" in a[0].text and "drive through due" in a[0].text
      and a[0].category == "penalty" and not sent7, [e.text for e in a])
step(sframe(12, five, totals={0: 20}))
check("20x: nothing new", len(autos("auto_steward")) == 1)
step(sframe(13, five, totals={0: 22}))
a = autos("auto_steward")
check("21x passed: DT #2 due", len(a) == 2 and "reached 21x (now 22x)" in a[1].text
      and "drive through #2" in a[1].text, a[-1].text)
saved = eng7.state.to_saved()
check("level is autosaved", saved["cars"]["0"]["steward_level"] == 2)
rs7 = RaceState()
rs7.load_saved(saved)
rs7.steward_rules = R
rs7.ingest(sframe(14, five, totals={0: 22}))
check("restored: no repeat", not [e for e in rs7.events[len(saved["events"]):]
                                  if e.kind == "auto_steward"])
rs8 = RaceState()
rs8.steward_rules = R
rs8.ingest(sframe(5, five, totals={1: 28}))
a8 = [e for e in rs8.events if e.kind == "auto_steward"]
check("attach at 28x: ONE line naming DT #3 (27x)",
      len(a8) == 1 and "reached 27x" in a8[0].text and "#3" in a8[0].text, a8)
rs9 = RaceState()
rs9.steward_rules = R
rs9.ingest(sframe(5, five, totals={1: 16}, stype="LONE QUALIFY"))
check("not in qualifying", not [e for e in rs9.events if e.kind == "auto_steward"])
rs9.steward_rules = steward.clean({"enabled": False})
rs9.ingest(sframe(6, five, totals={1: 16}))
check("Auto Steward OFF: no line", not [e for e in rs9.events if e.kind == "auto_steward"])

# full course caution: incident points of all cars within the window
def det(idx, kind="spin"):
    return IncidentEvent(idx, 0, kind, 3, source="detected", text=kind)


def pts(idx, n):
    return IncidentEvent(idx, n, "incident", 3)


check("defaults = GT3 WCT: 40x within 15 s, automatic, not in the last 5 min",
      R["fcy_incs"] == 40 and R["fcy_window_s"] == 15 and R["fcy_mode"] == "auto"
      and R["fcy_end_protect_min"] == 5 and not R["blue_enabled"]
      and R["bf_warn_s"] == 20 and R["bf_dt_s"] == 30 and R["fr_used"] == 1)
step(sframe(20, five, [pts(1, 4)]))
step(sframe(25, five, [pts(2, 4)]))
check("8x in 10 s: no caution", not autos("auto_caution"))
n0 = len(sent7)
step(sframe(28, five, [pts(3, 2)]))
a = autos("auto_caution")
check("10x within 10 s -> FULL COURSE CAUTION, !yellow typed at once",
      len(a) == 1 and "10x within 10s" in a[0].text and "#11" in a[0].text
      and any(x.startswith("!yellow") for x in sent7[n0:]), (a and a[0].text, sent7[n0:]))
step(sframe(33, five, [pts(4, 4)]))
check("not again inside the cooldown", len(autos("auto_caution")) == 1)
eng7.handle_client_message({"action": "set_steward", "fcy_mode": "ask",
                            "fcy_cooldown_s": 0, "fcy_incs": 6, "fcy_window_s": 5})
check("rules saved (6x / 5 s, ask)", steward.load_config()["fcy_incs"] == 6
      and steward.load_config()["fcy_mode"] == "ask")
eng7.auto._fcy_block_until = -1.0   # the first caution's 180 s cooldown is over
step(sframe(60, five, flags=["caution"]))
step(sframe(61, five, [pts(0, 4), pts(1, 4)], flags=["caution"]))
check("under a caution: nothing", not autos("auto_caution_ask"))
step(sframe(80, five))
step(sframe(81, five, [det(0), det(1), det(2), det(3), det(4)]))
check("detections without points do not count", not autos("auto_caution_ask"))
step(sframe(110, five, [pts(0, 4)]))
step(sframe(117, five, [pts(1, 4)]))
check("8x but spread over 7 s > 5 s window: nothing", not autos("auto_caution_ask"))
n0 = len(sent7)
step(sframe(120, five, [pts(2, 2)]))
a = autos("auto_caution_ask")
check("ask mode: question with DEPLOY, nothing typed",
      len(a) == 1 and "6x within 5s" in a[0].text
      and not any("!yellow" in x for x in sent7[n0:]), a and a[0].text)
step(sframe(160, five, [pts(0, 4), pts(1, 4)], stype="PRACTICE"))
check("practice: no caution", len(autos("auto_caution_ask")) == 1)

import time
# caution length: !pacelaps N once the auto caution is out
check("fcy_laps default 0 (iRacing decides), clamped to 20",
      steward.DEFAULT_CONFIG["fcy_laps"] == 0
      and steward.clean({"fcy_laps": 25})["fcy_laps"] == 20)
eng7.handle_client_message({"action": "set_steward", "fcy_mode": "auto",
                            "fcy_laps": 2, "fcy_incs": 6, "fcy_window_s": 5})
eng7.auto._fcy_block_until = -1.0
n0 = len(sent7)
step(sframe(300, five, [pts(0, 4), pts(1, 4)]))
check("auto caution: !yellow now, !pacelaps waits for the caution",
      any(x.startswith("!yellow") for x in sent7[n0:])
      and not any("pacelaps" in x for x in sent7[n0:]), sent7[n0:])
step(sframe(301, five))
check("no caution yet: still waiting", not any("pacelaps" in x for x in sent7[n0:]))
step(sframe(303, five, flags=["caution"]))
check("caution out -> !pacelaps 2 once", [x for x in sent7[n0:] if "pacelaps" in x]
      == ["!pacelaps 2"], sent7[n0:])
step(sframe(304, five, flags=["caution"]))
check("... and only once", len([x for x in sent7[n0:] if "pacelaps" in x]) == 1)
n0 = len(sent7)
r = eng7.handle_client_message({"action": "command", "command": "pace_laps", "n": "+1"})
check("button +1 -> !pacelaps +1", sent7[n0:] == ["!pacelaps +1"], (r, sent7[n0:]))
r = eng7.handle_client_message({"action": "command", "command": "pace_laps", "n": "4"})
check("Set 4 -> !pacelaps 4", sent7[-1] == "!pacelaps 4", r)
n0 = len(sent7)
for bad in ("0", "abc", "+", "123", "3; !dq #1"):
    eng7.handle_client_message({"action": "command", "command": "pace_laps", "n": bad})
check("bad lap values are refused", len(sent7) == n0, sent7[n0:])
step(sframe(330, five))
r = eng7.handle_client_message({"action": "command", "command": "pace_laps", "n": "-1"})
check("not under a caution: sent, with a hint", "only accepts this under a caution"
      in r["text"], r)
eng7._want_caution_laps()
eng7._pending_laps = (2, time.monotonic() - 1)
n0 = len(sent7)
step(sframe(331, five))
check("caution never came: dropped with a log line",
      eng7._pending_laps is None and not sent7[n0:]
      and any("caution laps (2) not set" in e.text for e in eng7.state.events[-5:]))
eng7.handle_client_message({"action": "command", "command": "pace_deploy", "auto": True})
check("DEPLOY of the ask line also sets the laps", eng7._pending_laps is not None
      and eng7._pending_laps[0] == 2)
eng7._pending_laps = None
eng7.chat_cfg["enabled"] = False
eng7._want_caution_laps()
check("chat OFF: no !pacelaps planned", eng7._pending_laps is None)
eng7.chat_cfg["enabled"] = True
eng7.handle_client_message({"action": "set_steward", "fcy_laps": 0})
eng7._want_caution_laps()
check("fcy_laps 0: iRacing decides, nothing planned", eng7._pending_laps is None)

# sequencer (default = GT3 WCT quali AM / PRO)
eng7.handle_client_message({"action": "set_steward", "seq_enabled": True})
q = [scar(0, "7")]
step(sframe(30, q, stype="OPEN QUALIFY", num=1, key="q-1"))
a = autos("auto_sequence")
check("quali start: AM message to everybody (+ chat /all)",
      len(a) == 1 and "00:00:01" in a[0].text
      and sent7[-1] == "/all Start Qualifying AM Klasse", sent7[-1])
step(sframe(600, q, stype="OPEN QUALIFY", num=1, key="q-1"))
check("no repeat at 10 min", len(autos("auto_sequence")) == 1)
step(sframe(901, q, stype="OPEN QUALIFY", num=1, key="q-1"))
a = autos("auto_sequence")
check("15 min: AM finished, PRO started",
      len(a) == 2 and sent7[-1]
      == "/all Ende Qualifying AM Klasse / Start Qualifying PRO Klasse")
step(sframe(905, q, stype="OPEN QUALIFY", num=1, key="q-1"))
check("PRO message once", len(autos("auto_sequence")) == 2)
eng7.auto.reset()
step(sframe(1300, q, stype="OPEN QUALIFY", num=1, key="q-1"))
check("iCAS started mid-quali (20 min): nothing announced",
      len(autos("auto_sequence")) == 2)
eng7.handle_client_message({"action": "set_steward", "sequence": [
    {"session": "race", "at": "00:05:00", "text": "  Track limits   T4 "},
    {"session": "any", "at": "1:00", "text": "Hello"},
    {"session": "race", "at": "bad", "text": "x"},
    {"session": "quali", "at": 10, "text": ""}]})
seq = eng7.steward_cfg["sequence"]
check("sequence cleaned: times parsed, empty / bad dropped, sorted",
      seq == [{"session": "race", "at": 300, "text": "Track limits T4"},
              {"session": "any", "at": 60, "text": "Hello"}], seq)
rq = [scar(0, "7")]
step(sframe(70, rq, num=2, key="r-1"))
check("'any' step in the race", sent7[-1] == "/all Hello", sent7[-1])
step(sframe(301, rq, num=2, key="r-1"))
check("race step at 5 min", sent7[-1] == "/all Track limits T4", sent7[-1])
check("practice: race step not sent",
      steward.AutoSteward()._sequence(type("S", (), {
          "session_type": "PRACTICE", "session_time": 301.0})(),
          eng7.steward_cfg) == [])
old = steward.clean({"quali_split": True, "quali_split_min": 15,
                     "quali_am_text": "QUALIFYING: THE FIRST 15 MINUTES ARE "
                                      "RESERVED FOR AM DRIVERS",
                     "quali_pro_text": "AM QUALIFYING FINISHED - PRO "
                                       "QUALIFYING STARTED"})
check("old config with old default texts -> new WCT defaults, switch kept",
      old["seq_enabled"] and old["sequence"][0]["text"]
      == "Start Qualifying AM Klasse")
own = steward.clean({"quali_split": False, "quali_split_min": 20,
                     "quali_am_text": "AM now", "quali_pro_text": "PRO now"})
check("old config with own texts -> two steps (20 min)",
      not own["seq_enabled"] and [(x["at"], x["text"]) for x in own["sequence"]]
      == [(1, "AM now"), (1200, "PRO now")], own["sequence"])
eng7.handle_client_message({"action": "set_steward", "seq_enabled": False,
                            "sequence": steward.DEFAULT_CONFIG["sequence"]})

# blue flags (100 s laps -> 0.01 lap = 1 s)
def blue_frame(t, slow_pct, fast_pct, fast_lap=5, slow_lap=4):
    return sframe(t, [scar(0, "7", lap=fast_lap, pct=fast_pct, best=100.0),
                      scar(1, "14", lap=slow_lap, pct=slow_pct, best=103.0)],
                  num=3, key="b-1")


n0 = len(sent7)
step(blue_frame(100, 0.50, 0.48))
check("2 s behind: no blue flag", not autos("blue_flag"))
step(blue_frame(101, 0.50, 0.492))
a = autos("blue_flag")
check("0.8 s behind the car he is lapping -> blue flag to #14",
      len(a) == 1 and a[0].car_number == "14" and "let #7 D7 pass" in a[0].text
      and sent7[-1] == "/14 BLUE FLAG - let #7 D7 pass", (sent7[n0:], a))
step(blue_frame(105, 0.55, 0.542))
check("not repeated within 20 s", len(autos("blue_flag")) == 1)
step(blue_frame(122, 0.70, 0.692))
check("still held up after 20 s -> again", len(autos("blue_flag")) == 2
      and "(again)" in autos("blue_flag")[-1].text)
step(blue_frame(124, 0.72, 0.73))
step(blue_frame(160, 0.90, 0.892))
check("after the pass the pair is re-armed (next lap)", len(autos("blue_flag")) == 3)
step(sframe(170, [scar(0, "7", lap=5, pct=0.492), scar(1, "14", lap=5, pct=0.50)],
            num=3, key="b-1"))
check("same lap fight: no blue flag", len(autos("blue_flag")) == 3)
step(sframe(180, [scar(0, "7", lap=6, pct=0.592),
                  scar(1, "14", lap=5, pct=0.60, pit=True)], num=3, key="b-1"))
check("car in the pit lane: no blue flag", len(autos("blue_flag")) == 3)
eng7.chat_cfg["enabled"] = False
k = len(eng7.state.events)
step(blue_frame(200, 0.30, 0.292, fast_lap=8, slow_lap=6))
check("chat OFF: only the log line, no 'not sent' spam",
      len([e for e in eng7.state.events[k:] if e.category != "pit"]) == 1
      and len(autos("blue_flag")) == 4, [e.text for e in eng7.state.events[k:]])
check("snapshot carries the steward rules",
      snap_of(eng7)["steward"]["dt_first"] == 15)

print("GT3 WCT ruleset (iRaceControl screenshot)")
eng7.chat_cfg["enabled"] = True
eng7.steward_cfg = steward.clean({})
eng7.auto.reset()


def wcar(idx, num, flags=0, fr=-1, pit=False):
    c = scar(idx, num, pit=pit)
    c.car_flags, c.fast_repairs_used = flags, fr
    return c


BL = steward.FLAG_BLUE
n0 = len(sent7)
for t in range(1000, 1021):
    step(sframe(t, [wcar(0, "7"), wcar(1, "14", BL)], num=4, key="w-1"))
a = autos("auto_blue_warn")
check("blue flag 20 s -> private warning to #14",
      len(a) == 1 and "/14 BLUE FLAG - LET PASS FASTER CAR" in sent7[n0:], sent7[n0:])
for t in range(1021, 1031):
    step(sframe(t, [wcar(0, "7"), wcar(1, "14", BL)], num=4, key="w-1"))
a = autos("auto_blue_dt")
check("blue flag 30 s -> DRIVE THROUGH + message to all",
      len(a) == 1 and any(x.startswith("!black #14") for x in sent7[n0:])
      and "/all #14 (D14) - DRIVE THROUGH FOR BLUE FLAG" in sent7[n0:], sent7[n0:])
for t in range(1031, 1060):
    step(sframe(t, [wcar(0, "7"), wcar(1, "14", BL)], num=4, key="w-1"))
check("only once per blue-flag period", len(autos("auto_blue_dt")) == 1
      and len(autos("auto_blue_warn")) == 1)
step(sframe(1060, [wcar(0, "7"), wcar(1, "14")], num=4, key="w-1"))
for t in range(1061, 1075):
    step(sframe(t, [wcar(0, "7"), wcar(1, "14", BL)], num=4, key="w-1"))
check("new blue flag: timer starts again (14 s = nothing yet)",
      len(autos("auto_blue_warn")) == 1)
for t in range(1075, 1100):
    step(sframe(t, [wcar(0, "7"), wcar(1, "14", BL)], num=4, key="w-1",
                flags=["caution"]))
check("under a caution: no blue-flag penalty", len(autos("auto_blue_warn")) == 1
      and len(autos("auto_blue_dt")) == 1)
for t in range(1100, 1130):
    step(sframe(t, [wcar(0, "7"), wcar(1, "14", BL)], num=4, key="w-1",
                stype="OPEN QUALIFY"))
check("qualifying: no blue-flag rule", len(autos("auto_blue_warn")) == 1)

n0 = len(sent7)
step(sframe(1200, [wcar(0, "7", fr=1), wcar(1, "14", fr=0)], num=5, key="w-2"))
check("fast repair already used when iCAS starts: silent", not autos("auto_fast_repair"))
step(sframe(1201, [wcar(0, "7", fr=1), wcar(1, "14", fr=1)], num=5, key="w-2"))
a = autos("auto_fast_repair")
check("#14 uses his fast repair -> private message",
      len(a) == 1 and "/14 YOU'VE USED UP ALL YOUR FASTREPAIR" in sent7[n0:], sent7[n0:])
step(sframe(1202, [wcar(0, "7", fr=1), wcar(1, "14", fr=2)], num=5, key="w-2"))
check("... once", len(autos("auto_fast_repair")) == 1)

MB = steward.FLAG_REPAIR
n0 = len(sent7)
step(sframe(1300, [wcar(0, "7", MB), wcar(1, "14")], num=6, key="w-3"))
step(sframe(1301, [wcar(0, "7", MB), wcar(1, "14", MB)], num=6, key="w-3"))
a = autos("auto_meatball")
check("meatball -> RC message to all (not for one already out at the start)",
      len(a) == 1 and sent7[n0:] == ["/all #14 (D14) - HAS A MEATBALL FLAG"], sent7[n0:])

# FCY 40x / 15 s, not in the last 5 minutes
eng7.auto.reset()
eng7.auto._fcy_block_until = -1.0


def tframe(t, remain, incs=()):
    fr = sframe(t, five, incs, num=7, key="w-4")
    fr.session.session_time_remain = remain
    return fr


step(tframe(2000, 1200, [pts(0, 20), pts(1, 12)]))
step(tframe(2010, 1190, [pts(2, 4)]))
check("36x in 15 s: nothing", not [e for e in autos("auto_caution")
                                    if e.sim_time >= 2000])
step(tframe(2014, 1186, [pts(3, 4)]))
check("40x in 15 s -> caution", len([e for e in autos("auto_caution")
                                      if e.sim_time >= 2000]) == 1)
eng7.auto._fcy_block_until = -1.0
step(tframe(3000, 280, [pts(0, 40)]))
check("last 5 minutes: no caution", len([e for e in autos("auto_caution")
                                         if e.sim_time >= 2000]) == 1)
check("texts: %cnr% / %dname% filled",
      steward.fill("#%cnr% (%dname%) x", type("C", (), {"number": "7",
                                                        "driver": "Max"})()) == "#7 (Max) x")

print("Multi-steward + camera")
from backend import access as acc
check("this PC: in without a password", acc.admit({}, "local", "") == (True, ""))
check("LAN without a password set: in", acc.admit({}, "lan", "")[0])
check("internet without a password set: REFUSED", not acc.admit({}, "remote", "")[0])
pwc = acc.make_config("geheim123")
check("only a hash is stored", "geheim123" not in json.dumps(pwc)
      and acc.password_ok(pwc, "geheim123") and not acc.password_ok(pwc, "x"))
check("password set: LAN / internet need it",
      not acc.admit(pwc, "lan", "")[0] and not acc.admit(pwc, "remote", "nope")[0]
      and acc.admit(pwc, "remote", "geheim123")[0])
check("loopback + Cloudflare header = internet",
      acc.where("127.0.0.1", {"cf-connecting-ip": "1.2.3.4"}) == "remote"
      and acc.where("127.0.0.1", {}) == "local"
      and acc.where("192.168.86.20", {}) == "lan"
      and acc.where("82.67.90.231", {}) == "remote")
check("names cleaned", acc.clean_name("<b>Thomas</b>\n") == "bThomas/b"
      and acc.clean_name("") == "Steward" and len(acc.clean_name("x" * 60)) == 24)

eng8 = server.Engine(force_sim=True)
for _ in range(5):
    eng8.tick()
st8 = eng8.state
ack = eng8.handle_client_message({"action": "steward_chat", "text": "  Ich schaue #14 an "},
                                 "Andreas", "remote")
check("steward chat line with the name", ack is None and st8.steward_chat[-1]["name"]
      == "Andreas" and st8.steward_chat[-1]["text"] == "Ich schaue #14 an", st8.steward_chat)
check("empty chat line refused", eng8.handle_client_message(
    {"action": "steward_chat", "text": "  "}, "Andreas")["type"] == "ack")
check("actor reset after the message", st8.actor == "")
ev = st8._log("incident", "Contact", lap=3)
ev.is_incident, ev.car_number = True, "14"
r1 = eng8.handle_client_message({"action": "claim", "id": ev.id, "on": True}, "Andreas")
r2 = eng8.handle_client_message({"action": "claim", "id": ev.id, "on": True}, "Thomas")
check("claim: Andreas has it, Thomas is told", ev.claimed_by == "Andreas"
      and "Andreas" in r2["text"], (r1, r2))
eng8.handle_client_message({"action": "claim", "id": ev.id, "on": False}, "Thomas")
check("someone else cannot release it", ev.claimed_by == "Andreas")
eng8.handle_client_message({"action": "claim", "id": ev.id, "on": False}, "Andreas")
check("released", ev.claimed_by == "")
st8.actor = "Thomas"
st8.resolve_incident(ev.id, "WARNING")
st8.actor = ""
check("decision carries who decided", ev.decided_by == "Thomas", ev.to_dict().get("decided_by"))
cfg_path = acc.CONFIG_PATH
acc.CONFIG_PATH = Path(tempfile.mkdtemp()) / "access_config.json"
_save = acc.save_config
acc.save_config = lambda c, path=None: _save(c, acc.CONFIG_PATH)
r = eng8.handle_client_message({"action": "set_password", "password": "geheim123"},
                               "Andreas", "remote")
check("password cannot be set from outside", not acc.has_password(eng8.access_cfg)
      and "main iCAS" in r["text"])
r = eng8.handle_client_message({"action": "set_password", "password": "abc"}, "Host")
check("too short refused", not acc.has_password(eng8.access_cfg))
eng8.handle_client_message({"action": "set_password", "password": "geheim123"}, "Host")
check("set on the main PC, saved as a hash", acc.has_password(eng8.access_cfg)
      and "geheim123" not in acc.CONFIG_PATH.read_text())
acc.save_config, acc.CONFIG_PATH = _save, cfg_path
eng8.clients[object()] = {"name": "Andreas", "place": "remote", "token": "t"}
sp = eng8.stewards_payload()
check("stewards payload: online + chat + password flag",
      sp["online"] == [{"name": "Andreas", "place": "remote"}]
      and sp["chat"][-1]["name"] == "Andreas" and sp["password"] is True)
sig = eng8.stewards_signature()
st8.chat("noch eine")
check("signature changes with a chat line", eng8.stewards_signature() != sig)
check("initial messages carry stewards + camera",
      any(m.get("type") == "stewards" for m in eng8.initial_messages())
      and snap_of(eng8)["camera"]["groups"][0]["name"] == "TV1")
# camera on the simulator
r = eng8.handle_client_message({"action": "camera", "op": "group", "group": 3}, "Andreas")
check("camera group", eng8.camera_info()["group"] == 3 and "TV3" in r["text"], r)
check("unknown group refused", "no camera group" in eng8.handle_client_message(
    {"action": "camera", "op": "group", "group": 99})["text"])
cars8 = sorted(eng8.state.cars.values(), key=lambda c: c.position or 999)
if cars8:
    eng8.handle_client_message({"action": "camera", "op": "car",
                                "car_idx": cars8[0].car_idx})
    check("camera on a car", eng8.camera_info()["car_idx"] == cars8[0].car_idx)
    eng8.handle_client_message({"action": "camera", "op": "next"})
    check("next = the car behind", eng8.camera_info()["car_idx"] == cars8[1].car_idx,
          (eng8.camera_info(), [c.car_idx for c in cars8[:3]]))
    eng8.handle_client_message({"action": "camera", "op": "prev"})
    check("prev = back", eng8.camera_info()["car_idx"] == cars8[0].car_idx)
else:
    check("simulator has cars", False)
fakeir = FakeIR({"CameraInfo": {"Groups": [{"GroupNum": 1, "GroupName": "Scenic"},
                 {"GroupNum": 2, "GroupName": "TV1"}, {"GroupNum": 3, "GroupName": "Gyro"}]},
                 "CamGroupNumber": 3, "CamCarIdx": 1,
                 "DriverInfo": {"Drivers": [{"CarIdx": 1, "CarNumber": "7"}]}})
src9 = irs.IRacingSource()
src9._ir = fakeir
ci = src9.camera_info()
check("iRacing camera info: no Scenic, current group + car",
      [g["name"] for g in ci["groups"]] == ["TV1", "Gyro"] and ci["group"] == 3
      and ci["car_number"] == "7", ci)
src9.send_command("cam_group", group=2)
check("group switch keeps the car on screen", fakeir.calls[-1] == ("cam", "7", 2), fakeir.calls)
src9.send_command("cam_car", number="14")
check("car switch keeps the group (Gyro)", fakeir.calls[-1] == ("cam", "14", 3), fakeir.calls)
fakeir["CamGroupNumber"] = 1
src9._cam_cache = None
src9.send_command("cam_car", number="14")
check("from Scenic -> TV1", fakeir.calls[-1] == ("cam", "14", 2), fakeir.calls)


print("Follow a main iCAS (SUPP) + internet access (tunnel)")
import asyncio as _aio
from backend import follow as fol, tunnel as tun
fol.CONFIG_PATH = Path(tempfile.mkdtemp()) / "follow_config.json"
check("address -> websocket",
      fol.ws_url("https://abc-def.trycloudflare.com", "Andreas", "a b&c")
      == "wss://abc-def.trycloudflare.com/ws?name=Andreas&pw=a%20b%26c"
      and fol.ws_url("192.168.86.20:8080/", "X", "")
      == "ws://192.168.86.20:8080/ws?name=X&pw=")
try:
    fol.ws_url("ftp://x", "a", "b"); bad = False
except ValueError:
    bad = True
check("not an address refused", bad)
check("own address detected", fol.is_self("http://localhost:8080", 8080)
      and not fol.is_self("http://localhost:8080", 8090)
      and not fol.is_self("https://x.trycloudflare.com", 8080))


class FakeUp:
    """The main iCAS's websocket, seen from the follower."""
    def __init__(self, script):
        self.script, self.sent, self.url = list(script), [], None
        self.inbox = _aio.Queue()
    def __call__(self, url):
        self.url = url
        return self
    async def __aenter__(self):
        for m in self.script:
            await self.inbox.put(json.dumps(m))
        return self
    async def __aexit__(self, *a):
        return False
    def __aiter__(self):
        return self
    async def __anext__(self):
        m = await self.inbox.get()
        if m is None:
            raise StopAsyncIteration
        return m
    async def send(self, text):
        self.sent.append(json.loads(text))


MAIN_EVENTS = {"type": "events", "events": [
    {"id": 41, "car_number": "14", "session_num": 2, "sim_time": 812.4,
     "text": "Contact"}]}
MAIN_SNAP = {"type": "snapshot", "session": {"track": "Spa"}, "camera": {"x": 1},
             "cars": [{"car_idx": 3, "number": "14", "position": 1, "in_world": True},
                      {"car_idx": 5, "number": "7", "position": 2, "in_world": True}]}


async def follow_run():
    up = FakeUp([{"type": "hello", "name": "Andreas", "place": "remote", "token": "t"},
                 {"type": "init", "version": "x"}, MAIN_EVENTS, MAIN_SNAP,
                 {"type": "stewards", "online": [{"name": "Thomas"}, {"name": "Andreas"}]}])
    f = fol.Follower(connect=up, cfg={})
    f.start("https://abc.trycloudflare.com", "Andreas", "geheim123")
    for _ in range(50):
        await _aio.sleep(0.01)
        if f.active and f.snapshot:
            break
    res = {"active": f.active, "url": up.url, "as": f.as_name,
           "out": [m["type"] for m in f.drain()], "ev": f.event(41)}
    me = object()
    r = await f.send({"action": "steward_chat", "text": "hi"}, reply_to=me)
    await up.inbox.put(json.dumps({"type": "ack", "text": "ok from main"}))
    await _aio.sleep(0.05)
    res["sent"] = up.sent
    res["ack"] = [(m.get("_to") is me, m["text"]) for m in f.drain()]
    res["r"] = r
    res["init"] = [m["type"] for m in f.initial_messages()]
    await up.inbox.put(None)                    # main went away
    await _aio.sleep(0.05)
    res["after"] = (f.state, f.take_lost())
    f.stop(quiet=True)
    up2 = FakeUp([{"type": "login", "error": "wrong password"}])
    f2 = fol.Follower(connect=up2, cfg={})
    f2.start("https://abc.trycloudflare.com", "Andreas", "falsch")
    await _aio.sleep(0.05)
    res["refused"] = (f2.state, f2.info, f2._task.done())
    return res, f

fres, fobj = _aio.run(follow_run())
check("follower logs in with name + password", fres["url"]
      == "wss://abc.trycloudflare.com/ws?name=Andreas&pw=geheim123" and fres["active"]
      and fres["as"] == "Andreas", fres)
check("main's picture passed on (hello as Andreas first)",
      fres["out"] == ["hello", "init", "events", "snapshot", "stewards"], fres["out"])
check("main's log line kept for our replay", fres["ev"]["sim_time"] == 812.4)
check("action goes to main, its ack back to the sender",
      fres["sent"] == [{"action": "steward_chat", "text": "hi"}] and fres["r"] is None
      and fres["ack"] == [(True, "ok from main")], fres)
check("a dashboard opened later gets main's picture",
      fres["init"] == ["hello", "init", "events", "snapshot", "stewards"], fres["init"])
check("main gone: reconnecting, own data again", fres["after"] == ("connecting", True),
      fres["after"])
check("wrong password: stops, says why", fres["refused"][0] == "refused"
      and "wrong password" in fres["refused"][1] and fres["refused"][2], fres["refused"])
check("password not saved to disk", "geheim123" not in fol.CONFIG_PATH.read_text()
      and "abc.trycloudflare.com" in fol.CONFIG_PATH.read_text())


class Recorder:
    name = "iracing"
    def __init__(self):
        self.calls = []
    def send_command(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return f"done {cmd}"
    def camera_info(self):
        return {"groups": [{"num": 2, "name": "TV1"}], "group": 2, "car_idx": 3,
                "car_number": "14"}


eng10 = server.Engine(force_sim=True)
rec = Recorder()
eng10.source = rec
eng10.follower = fobj
fobj.state = "connected"
fobj.snapshot, fobj.events = MAIN_SNAP, {41: MAIN_EVENTS["events"][0]}
r = eng10.handle_client_message({"action": "replay", "id": 41, "preroll": 8}, "Andreas")
check("replay of a MAIN log line in OUR iRacing", rec.calls[-1] == ("replay_event", {
    "session_num": 2, "sim_time": 812.4, "number": "14", "preroll": 8.0}), rec.calls)
eng10.handle_client_message({"action": "camera", "op": "car", "car_idx": 5})
check("camera on a MAIN car in OUR iRacing", rec.calls[-1] == ("cam_car", {"number": "7"}))
eng10.handle_client_message({"action": "camera", "op": "next"})
check("next = main's car behind ours", rec.calls[-1] == ("cam_car", {"number": "7"}))
out = eng10.follow_outgoing()
fobj.outbox.append(dict(MAIN_SNAP))
out = eng10.follow_outgoing()
snapx = json.loads(out[0][1])
check("main's snapshot carries OUR camera + follow status",
      snapx["camera"]["car_number"] == "14" and snapx["net"]["follow"]["state"]
      == "connected" and snapx["cars"][1]["number"] == "7", snapx.get("net"))
eng10.state.session_track, eng10.state.connected = "Monza", True
check("other session in our iRacing: warned",
      "Monza" in eng10.net_status()["follow"]["warning"])
r = eng10.handle_client_message({"action": "follow", "op": "start",
                                 "url": "http://x:8080"}, "Andreas", "remote")
check("follow mode not settable from outside", "own PC" in r["text"])
r = eng10.handle_client_message({"action": "set_running", "running": False},
                                "Andreas", "remote")
check("RUN / STOP not from outside", eng10.running and "main" in r["text"])
fobj.state = "off"


class FakeProc:
    def __init__(self, cmd, **kw):
        self.cmd, self.kw, self.code = cmd, kw, None
        self.lines = _queue.Queue()
        self.stdout = iter(self.lines.get, None)
    def poll(self):
        return self.code
    def terminate(self):
        self.code = 0
        self.lines.put(None)
    def wait(self, timeout=None):
        return 0
    def kill(self):
        self.code = -9


import queue as _queue
procs = []
def fake_popen(cmd, **kw):
    procs.append(FakeProc(cmd, **kw))
    return procs[-1]
tun.find_binary = lambda: "/opt/cloudflared"
eng11 = server.Engine(force_sim=True)
eng11.tunnel = tun.Tunnel(port=8080, popen=fake_popen, cfg={})
eng11.access_cfg = {}
r = eng11.handle_client_message({"action": "tunnel", "op": "start"}, "Host")
check("no tunnel without an access password", not procs and "password" in r["text"])
eng11.access_cfg = acc.make_config("geheim123")
r = eng11.handle_client_message({"action": "tunnel", "op": "start"}, "Andreas", "remote")
check("tunnel not switchable from outside", not procs)
eng11.handle_client_message({"action": "tunnel", "op": "start"}, "Host")
check("quick tunnel to our port", procs and procs[0].cmd == [
    "/opt/cloudflared", "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8080"],
    procs and procs[0].cmd)
procs[0].lines.put("2026-10-27T18:00:00Z INF |  https://brave-red-fox.trycloudflare.com  |")
procs[0].lines.put("2026-10-27T18:00:01Z INF Registered tunnel connection connIndex=0")
check("link read, tunnel ON", tun.wait_for(eng11.tunnel, "on")
      and eng11.net_status()["tunnel"]["url"] == "https://brave-red-fox.trycloudflare.com",
      eng11.net_status()["tunnel"])
_save2, _cp2 = acc.save_config, acc.CONFIG_PATH
acc.CONFIG_PATH = Path(tempfile.mkdtemp()) / "access_config.json"
eng11.handle_client_message({"action": "set_password", "password": ""}, "Host")
acc.CONFIG_PATH = _cp2
check("password removed -> tunnel off", eng11.tunnel.status()["state"] == "off"
      and procs[0].code == 0)
eng11.access_cfg = acc.make_config("geheim123")
eng11.handle_client_message({"action": "tunnel", "op": "start"}, "Host")
procs[1].code = 1
procs[1].lines.put("ERR failed to request quick Tunnel")
time.sleep(0.05)
stt = eng11.tunnel.status()
check("cloudflared died: error with its last line", stt["state"] == "error"
      and "quick Tunnel" in stt["error"], stt)
named = tun.Tunnel(port=8080, popen=fake_popen,
                   cfg={"token": "eyJhIjoi", "hostname": "icas.example.com"})
named.start()
check("named tunnel: token + fixed address", procs[-1].cmd[-3:] == ["run", "--token",
      "eyJhIjoi"] and named.status()["url"] == "https://icas.example.com")
named.stop()

# a follow link that leads back to this very iCAS (own tunnel link)
from backend import follow as _fol
fs = _fol.Follower(cfg={})
fs.state = "connecting"
r = fs._receive(json.dumps({"type": "hello", "name": "Thomas 2",
                            "icas": _fol.INSTANCE_ID}))
check("own tunnel link: refused, not followed",
      r == "refused" and fs.state == "refused" and "THIS iCAS" in fs.info
      and not fs.active, fs.info)
r = fs._receive(json.dumps({"type": "hello", "name": "Thomas 2", "icas": "other"}))
check("another iCAS: followed", fs.state == "connected", fs.state)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
