"""Full-course yellow + voice mood — offline checks.

WCT GT3 Indianapolis 2026-10-20: a caution (547 s) and the restart (one to
green 739 s, green 916 s) went by without a word, the director kept cutting
to "battles" in the pace-car queue, and every line sounded the same.

  • race_director: caution start / one_to_green / restart -> a log entry and a
    cut to the leader each; no battles, no overtake replays under yellow nor
    in the first RESTART_SKIP_LAPS after the green.
  • race_commentator: own lines for the three phases, a mood per line.
  • race_voice: rate / pitch / volume per mood reach the TTS engine.
  • iracing_dashboard: SessionFlags -> phase; no overtakes under yellow.

Run: python test_caution.py
"""
import random
import sys
import time
import types

import race_commentator as rc
import race_director as rd
import race_voice as rv

fails = 0


def check(name, ok, extra=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
    if not ok:
        fails += 1


GROUPS = [{"id": i, "name": n} for i, n in enumerate(["Scenic", "TV1", "TV2", "TV3", "Chase", "Pit Lane"])]
EST = 100.0


def car(idx, gap_s, pit=False):
    return {"idx": idx, "number": str(10 + idx), "name": f"Driver {idx}", "class_id": 1,
            "class_name": "GT3", "progress": 5.0 - gap_s / EST, "on_pit": pit, "in_world": True,
            "est_lap": EST}


# -- director: the real flag timeline of tonight, compressed --------------------
# green 0-60 s (a close battle for P2) | caution 60-120 s (queue 0.4 s apart,
# a pass in the pits) | one_to_green 120-140 s | green 140 s on
def phase_at(t):
    return "caution" if 60 <= t < 120 else "one_to_green" if 120 <= t < 140 else None


def cars_at(t):
    if 60 <= t < 140:                                        # queue behind the pace car
        return [car(i, i * 0.4, pit=(i == 5 and 70 <= t < 90)) for i in range(8)]
    return [car(0, 0.0), car(1, 3.0), car(2, 3.5)] + [car(i, 3.5 + i * 5.0) for i in range(3, 8)]


d = rd.RaceDirector(random.Random(3))
now, cam, cuts = 1000.0, None, []
overtakes = {65.0: {"id": 1, "type": "overtake", "car_idx": 6, "passed_idx": 5, "passed_name": "Driver 5",
                    "passed_number": "15", "position": 6},        # under yellow (pit cycle)
             141.0: {"id": 2, "type": "overtake", "car_idx": 3, "passed_idx": 2, "passed_name": "Driver 2",
                     "passed_number": "12", "position": 3}}       # restart chaos
while now < 1000.0 + 200:
    rel = round(now - 1000.0, 1)
    ev = [overtakes[rel]] if rel in overtakes else []
    if ev:
        ev[0]["wall_clock"] = time.time()
    shot = d.tick(now, cars_at(rel), GROUPS, cam, ev, True, caution=phase_at(rel))
    if shot is not None:
        cuts.append((rel, shot.kind, shot.car["idx"], shot.info.get("phase")))
        if shot.kind != "replay":
            cam = shot.car["idx"]
    now = round(now + 0.5, 1)

phases = [(t, ph) for t, k, _, ph in cuts if k == "caution"]
check("caution start: cut to the leader at once", (60.0, "start") in phases
      and any(t == 60.0 and k == "caution" and i == 0 for t, k, i, _ in cuts), cuts)
check("one lap to green: its own cut", (120.0, "one_to_green") in phases, phases)
check("restart: cut to the leader at the green", (140.0, "restart") in phases
      and any(t == 140.0 and i == 0 for t, k, i, _ in cuts), cuts)
check("exactly three caution cuts", len(phases) == 3, phases)
check("no battles under yellow (the queue is 0.4 s apart)",
      not any(k == "battle" and 60 <= t < 140 for t, k, _, _ in cuts), cuts)
check("battles again before the caution", any(k == "battle" and t < 60 for t, k, _, _ in cuts), cuts)
check("no overtake replay under yellow nor in the restart chaos",
      not any(k == "replay" for _, k, _, _ in cuts), cuts)
log_phases = [e["info"].get("phase") for e in reversed(d.log) if e["kind"] == "caution"]
check("director log carries the three phases for the commentary",
      log_phases == ["start", "one_to_green", "restart"], log_phases)
check("the leader is shown under yellow, not only at the phase cuts",
      any(k == "leader" and 60 < t < 140 for t, k, _, _ in cuts) or
      sum(1 for t, k, _, _ in cuts if 60 <= t < 140) >= 2, cuts)

# operator has the camera: the phase is still logged (commentary) but no cut
d2 = rd.RaceDirector(random.Random(1))
d2.tick(10.0, cars_at(0), GROUPS, None, [], True)
d2.manual_override(10.0)
shot = d2.tick(11.0, cars_at(70), GROUPS, 3, [], True, caution="caution")
check("manual camera: caution logged, no cut", shot is None and d2.log[0]["kind"] == "caution"
      and d2.log[0]["info"]["phase"] == "start")

# an overtake well after the restart is replayed again
d3 = rd.RaceDirector(random.Random(1))
t3 = 100.0
for rel in range(0, 30):
    d3.tick(t3 + rel, cars_at(0), GROUPS, 0, [], True, caution="caution" if rel < 10 else None)
late = [{"id": 9, "type": "overtake", "car_idx": 3, "passed_idx": 2, "passed_name": "Driver 2",
         "passed_number": "12", "position": 3, "wall_clock": time.time()}]
moved = [dict(c, progress=c["progress"] + 1.0) for c in cars_at(0)]     # a lap later
shot = d3.tick(t3 + 40, moved, GROUPS, 0, late, True)
check("overtakes are replayed again a lap after the restart", shot is not None and shot.kind == "replay",
      shot and shot.kind)

# -- commentator ----------------------------------------------------------------
c = rc.Commentator(random.Random(1), config={"mode": "template"})
c.feed(list(d.log), {})
cl = [ln for ln in reversed(c.lines) if ln["kind"] == "caution"]
check("three caution lines, none replaced by the next picture", len(cl) == 3, [ln["text"] for ln in c.lines])
check("caution start line says yellow", cl and ("Gelb" in cl[0]["text"] or "Caution" in cl[0]["text"]), cl[:1])
check("restart line names the leader", len(cl) == 3 and "Driver 0" in cl[2]["text"]
      and any(w in cl[2]["text"] for w in ("Grün", "Restart", "frei")), cl[2:])
check("moods: start hype, one to green calm, restart hype",
      [ln["mood"] for ln in cl] == ["hype", "calm", "hype"], [ln.get("mood") for ln in cl])
check("mood: a spin is hype, the leader normal, a pit stop calm",
      rc.mood_of({"kind": "incident", "event": "lost_control"}) == "hype"
      and rc.mood_of({"kind": "leader"}) == "normal" and rc.mood_of({"kind": "pit"}) == "calm"
      and rc.mood_of({"kind": "replay", "event": "overtake"}) == "hype"
      and rc.mood_of({"kind": "quali_lap", "new_pole": True}) == "hype"
      and rc.mood_of({"kind": "battle"}) == "normal")
check("every published line has a mood", all(ln.get("mood") in ("hype", "calm", "normal")
                                              for ln in c.lines if ln["kind"] != "show"))
check("Claude prompt explains caution and the mood", "caution" in rc.SYSTEM_PROMPT
      and "stimmung" in rc.SYSTEM_PROMPT)

# -- voice ----------------------------------------------------------------------
check("prosody: hype faster / higher / louder than the base",
      rv.prosody("hype", "+10%") == {"rate": "+26%", "pitch": "+10Hz", "volume": "+15%"},
      rv.prosody("hype", "+10%"))
check("prosody: calm slower", rv.prosody("calm", "+10%")["rate"] == "+2%")
check("prosody: normal = the base rate", rv.prosody("normal", "+10%") == {"rate": "+10%", "pitch": "+0Hz",
                                                                       "volume": "+0%"})
check("prosody: config override (absolute rate)",
      rv.prosody("hype", "+10%", {"hype": {"rate": "+30%", "pitch": "+10Hz"}})["rate"] == "+30%")
got = []


def edge_full(text, voice, rate, pitch="+0Hz", volume="+0%"):
    got.append((rate, pitch, volume))
    return b"mp3", "audio/mpeg"


v = rv.Voice({"tts": "edge", "tts_rate": "+10%"}, {"edge": edge_full})
v.listening()
v.say(1, "Dreher!", mood="hype")
v.say(2, "Boxenstopp.", keep=True, mood="calm")
t_end = time.time() + 2
while time.time() < t_end and not (v.has(1) and v.has(2)):
    time.sleep(0.02)
check("mood reaches the engine (rate, pitch, volume)",
      ("+26%", "+10Hz", "+15%") in got and ("+2%", "-3Hz", "+0%") in got, got)
old = []
v2 = rv.Voice({"tts": "edge"}, {"edge": lambda text, voice, rate: old.append(rate) or (b"x", "audio/mpeg")})
v2.listening()
v2.say(1, "x", mood="hype")
t_end = time.time() + 2
while time.time() < t_end and not v2.has(1):
    time.sleep(0.02)
check("an engine without pitch / volume still speaks (rate only)", v2.has(1) and old == ["+26%"], old)

# -- dashboard: SessionFlags -> phase, overtakes paused --------------------------
irsdk_stub = types.ModuleType("irsdk")


class _FakeIRSDK:
    def __init__(self):
        self.fields = {}

    def freeze_var_buffer_latest(self):
        pass

    def __getitem__(self, k):
        return self.fields.get(k)


irsdk_stub.IRSDK = _FakeIRSDK
sys.modules["irsdk"] = irsdk_stub
flask_stub = types.ModuleType("flask")


class _App:
    def __init__(self, *a, **k):
        pass

    def after_request(self, f):
        return f

    def route(self, *a, **k):
        return lambda f: f


flask_stub.Flask = _App
flask_stub.Response = object
flask_stub.render_template_string = lambda s, **k: s
flask_stub.jsonify = lambda *a, **k: None
flask_stub.request = types.SimpleNamespace(json=None, args={})
sys.modules.setdefault("flask", flask_stub)
import iracing_dashboard as dash  # noqa: E402

p = dash.TelemetryPoller(poll_hz=10)
seq = [(0x0200, None),            # one to green BEFORE the start: no caution
       (0x0004, None),            # green
       (0x4000, "caution"),
       (0x8000, "caution"),       # caution waving
       (0x4000 | 0x0200, "one_to_green"),
       (0x0200, "one_to_green"),  # caution bit already gone, still the restart lap
       (0x0004, None),            # green
       (0x0200, None)]            # a later one-to-green alone is not a caution
res = []
for flags, want in seq:
    p.ir.fields = {"SessionFlags": flags}
    res.append(p._caution_phase() == want)
check("SessionFlags -> phase (incl. pre-start one-to-green)", all(res), res)

L, DT = 4000.0, 0.1


def fields(t, pcts, flags):
    n = len(pcts)
    return {"SessionTime": t, "SessionNum": 4, "SessionState": 4, "SessionFlags": flags,
            "WeekendInfo": {"TrackLength": "4.00 km"},
            "DriverInfo": {"Drivers": [{"CarIdx": i, "CarNumber": str(10 + i), "UserName": f"D{i}",
                                        "CurDriverIncidentCount": -1} for i in range(n)]},
            "CarIdxTrackSurface": [3] * n, "CarIdxSessionFlags": [0] * n,
            "CarIdxLapDistPct": [x % 1.0 for x in pcts], "CarIdxClassPosition": list(range(1, n + 1)),
            "CarIdxOnPitRoad": [False] * n, "CarIdxLap": [int(x) + 1 for x in pcts],
            "SessionInfo": {"Sessions": [{"SessionNum": 4, "SessionType": "Race", "SessionLaps": "unlimited"}]}}


def run_pass(flags_fn):
    q = dash.TelemetryPoller(poll_hz=10)
    pcts, t = [2.502, 2.500, 2.30, 2.10], 0.0
    for _ in range(160):
        t += DT
        for i in range(4):
            pcts[i] += (51.0 if (i == 1 and t >= 4.0) else 45.0) * DT / L
        q.ir.fields = fields(t, pcts, flags_fn(t))
        q._update_incidents()
    return [o["car_idx"] for o in q._overtakes]


check("a clean pass under green is an overtake", run_pass(lambda t: 0x0004) == [1], run_pass(lambda t: 0x0004))
check("the same pass under yellow is not", run_pass(lambda t: 0x4000) == [])
check("a pass while the caution ends is not counted from the yellow order",
      run_pass(lambda t: 0x4000 if t < 6.0 else 0x0004) == [])

print()
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
