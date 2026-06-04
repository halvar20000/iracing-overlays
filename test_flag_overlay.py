"""Offline replay test for flag_overlay.FlagWatcher.

Simulates the SDK tick stream for scenarios reconstructed from real race
logs and asserts the white/checkered flags fire at the right moments.
"""
import sys, types, time

# ── stub irsdk + flask before importing the overlay ─────────────────────────
irsdk_stub = types.ModuleType("irsdk")
class _FakeIRSDK:
    def __init__(self): self.fields = {}
    def freeze_var_buffer_latest(self): pass
    def __getitem__(self, k): return self.fields.get(k)
    def startup(self): return True
    def shutdown(self): pass
    is_initialized = True
    is_connected = True
irsdk_stub.IRSDK = _FakeIRSDK
sys.modules["irsdk"] = irsdk_stub

flask_stub = types.ModuleType("flask")
class _App:
    def __init__(self, *a, **k): pass
    def after_request(self, f): return f
    def route(self, *a, **k): return lambda f: f
    def run(self, *a, **k): pass
flask_stub.Flask = _App
flask_stub.Response = object
flask_stub.render_template_string = lambda s: s
flask_stub.jsonify = lambda *a, **k: None
sys.modules["flask"] = flask_stub

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import flag_overlay
FlagWatcher = flag_overlay.FlagWatcher

WHITE, CHECK = 0x2, 0x1
GREEN = 0x4

def make_watcher():
    w = FlagWatcher()
    w.ir.fields = {
        "SessionNum": 4,
        "DriverInfo": {"Drivers": [{"CarIdx": 3, "CarNumber": "99",
                                    "UserName": "Andre Rajkovic"}]},
        "CarIdxClassPosition": [0, 0, 0, 1],
    }
    return w

def run_race(w, lap_time, n_laps, time_limit, flag_bit_mode,
             tick=0.5, monkeypatch_clock=None):
    """Drive the watcher through a race. Leader crosses S/F every lap_time.
    flag_bit_mode: 'none' (PCCD style) | 'bits' (bit set when leader starts
    lap that is final per +1 rule).
    Returns dict of (event -> sim_time)."""
    events = {}
    f = w.ir.fields
    # figure out final lap per +1 rule: first crossing with time_rem <= 0
    final_lap_start = None
    t = 0.0
    sim_now = [1000.0]
    real_time = time.time
    flag_overlay.time.time = lambda: sim_now[0]
    try:
        lap = 1   # leader on lap 1 after start
        crossings = [i * lap_time for i in range(1, n_laps + 2)]
        next_cross = 0
        sess_state = 4
        flags = GREEN
        race_over_at = None
        while t < (n_laps + 2) * lap_time:
            t += tick
            sim_now[0] += tick
            time_rem = time_limit - t
            # leader crossing?
            if next_cross < len(crossings) and t >= crossings[next_cross]:
                t_cross = crossings[next_cross]
                lap += 1
                next_cross += 1
                # iRacing's REAL timed-race rule (verified 2026-06-04 from
                # all logged races): white at the LAST crossing BEFORE the
                # clock expires (no further full lap fits), checkered at
                # the NEXT crossing (first one past expiry).
                if (final_lap_start is None
                        and (time_limit - t_cross) < lap_time):
                    final_lap_start = lap          # this lap is the final one
                    if flag_bit_mode == "bits":
                        flags |= WHITE
                elif final_lap_start is not None and lap == final_lap_start + 1:
                    # leader finished
                    race_over_at = t
                    if flag_bit_mode == "bits":
                        flags = (flags & ~WHITE) | CHECK
                    sess_state = 6
            # SessionState flips to checkered at TIMER EXPIRY (observed)
            if time_rem <= 0 and sess_state == 4:
                sess_state = 5
            pct = (t % lap_time) / lap_time
            f.update({
                "CarIdxLap": [0, 0, 0, lap],
                "CarIdxLapDistPct": [0, 0, 0, pct],
                "SessionTime": t,
                "SessionTimeRemain": time_rem,
                "SessionState": sess_state,
                "SessionFlags": flags,
                "SessionInfo": {"Sessions": [{"SessionNum": 4,
                                              "SessionLaps": "100"}]},
                "EstLapTime": lap_time,
            })
            prev_state = w.state
            w._tick()
            if w.state != prev_state:
                events[w.state] = (t, lap)
            if race_over_at and t > race_over_at + 30:
                break
    finally:
        flag_overlay.time.time = real_time
    return events, final_lap_start

failures = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not cond: failures.append(name)

# ── Scenario 1: PCCD Silverstone style — 100-lap cap + 1500 s, NO bits ─────
w = make_watcher()
ev, final_lap = run_race(w, lap_time=121.0, n_laps=14, time_limit=1500,
                         flag_bit_mode="none")
check("S1 white fired", "white_flag" in ev)
check("S1 white on final lap", ev.get("white_flag", (0, 0))[1] == final_lap,
      f"white at lap {ev.get('white_flag')}, final={final_lap}")
check("S1 checkered fired", "checkered" in ev)
check("S1 checkered one lap after white",
      ev.get("checkered", (0, 0))[1] == final_lap + 1, str(ev.get("checkered")))
check("S1 white before checkered",
      ev.get("white_flag", (9e9,))[0] < ev.get("checkered", (0,))[0])

# ── Scenario 2: Spa style — bits broadcast ──────────────────────────────────
w = make_watcher()
ev, final_lap = run_race(w, lap_time=131.0, n_laps=10, time_limit=1200,
                         flag_bit_mode="bits")
check("S2 white fired", "white_flag" in ev)
check("S2 white on final lap", ev.get("white_flag", (0, 0))[1] == final_lap,
      f"white at {ev.get('white_flag')}, final={final_lap}")
check("S2 checkered fired", "checkered" in ev)
gap = ev.get("checkered", (0,))[0] - ev.get("white_flag", (9e9,))[0]
check("S2 white visible a full lap", 100 < gap < 165, f"gap={gap:.1f}s")

# ── Scenario 3: genuine lap race (5 laps, no meaningful clock) ─────────────
w = make_watcher()
f = w.ir.fields
events3 = {}
sim_now = [1000.0]; real_time = time.time
flag_overlay.time.time = lambda: sim_now[0]
lap_time, n_laps = 90.0, 5
t = 0.0; lap = 1; crossings = [i*lap_time for i in range(1, n_laps+1)]; nc = 0
while t < (n_laps+1)*lap_time:
    t += 0.5; sim_now[0] += 0.5
    if nc < len(crossings) and t >= crossings[nc]:
        lap += 1; nc += 1
    f.update({"CarIdxLap": [0,0,0,lap],
              "CarIdxLapDistPct": [0,0,0,(t % lap_time)/lap_time],
              "SessionTime": t, "SessionTimeRemain": 604800.0,
              "SessionState": 4 if lap <= n_laps else 6,
              "SessionFlags": GREEN,
              "SessionInfo": {"Sessions": [{"SessionNum": 4,
                                            "SessionLaps": str(n_laps)}]},
              "EstLapTime": lap_time})
    prev = w.state; w._tick()
    if w.state != prev: events3[w.state] = (t, lap)
flag_overlay.time.time = real_time
check("S3 white fired on lap 5", events3.get("white_flag", (0,0))[1] == 5,
      str(events3.get("white_flag")))
check("S3 checkered on lap 6 counter", events3.get("checkered", (0,0))[1] == 6,
      str(events3.get("checkered")))

# ── Scenario 4: late join mid-final-lap (state already 5) ──────────────────
w = make_watcher()
f = w.ir.fields
events4 = {}
t = 1600.0; lap = 14
for i in range(400):
    t += 0.5
    pct = ((t - 1600) % 120) / 120
    if i == 240: lap = 15   # leader finishes
    f.update({"CarIdxLap": [0,0,0,lap],
              "CarIdxLapDistPct": [0,0,0,pct],
              "SessionTime": t, "SessionTimeRemain": 1500 - t,
              "SessionState": 5, "SessionFlags": GREEN,
              "SessionInfo": {"Sessions": [{"SessionNum": 4,
                                            "SessionLaps": "100"}]},
              "EstLapTime": 120.0})
    prev = w.state; w._tick()
    if w.state != prev: events4[w.state] = (t, lap)
check("S4 late join: no white", "white_flag" not in events4, str(events4))
check("S4 late join: checkered on crossing", "checkered" in events4,
      str(events4))

print()
print("ALL PASS" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
