"""
Offline verification for iracing_qualidelta.py — stubbed irsdk + flask.
Focus: the OWN-BEST reference mode added alongside the existing POLE mode.
Run:  python test_qualidelta.py
"""
import sys
import types

# ---- stub flask + irsdk BEFORE importing the overlay -------------------------
flask_stub = types.ModuleType("flask")


class _FakeApp:
    def __init__(self, name):
        pass

    def route(self, *a, **k):
        return lambda f: f

    def after_request(self, f):
        return f

    def run(self, *a, **k):
        pass


flask_stub.Flask = _FakeApp
flask_stub.jsonify = lambda *a, **k: None
flask_stub.render_template_string = lambda s: s
sys.modules["flask"] = flask_stub

irsdk_stub = types.ModuleType("irsdk")


class FakeIR:
    def __init__(self):
        self.d = {}
        self.is_initialized = True
        self.is_connected = True

    def startup(self):
        return True

    def shutdown(self):
        pass

    def freeze_var_buffer_latest(self):
        pass

    def __getitem__(self, k):
        return self.d.get(k)


irsdk_stub.IRSDK = FakeIR
sys.modules["irsdk"] = irsdk_stub

import iracing_qualidelta as iq  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


def approx(a, b, tol=0.05):
    return a is not None and abs(a - b) <= tol


# =============================================================================
# 1) DRIVING MODE — own-best vs session-best both present and distinct
# =============================================================================
def test_driving():
    print("driving mode (own vs pole predictive deltas):")
    p = iq.QualiDeltaPoller()
    ir = p.ir
    ir.d.update({
        "SessionUniqueID": 1, "SessionNum": 0, "SessionTime": 200.0,
        "IsOnTrack": True,
        "LapDistPct": 0.5, "Lap": 2, "OnPitRoad": False,
        "LapDeltaToSessionBestLap": 0.10, "LapDeltaToSessionBestLap_OK": True,
        "LapDeltaToBestLap": -0.25, "LapDeltaToBestLap_OK": True,
        "LapBestLapTime": 90.0, "LapLastLapTime": 91.0,
        "PlayerCarPosition": 3,
        "SplitTimeInfo": {"Sectors": [{"SectorStartPct": 0.0},
                                      {"SectorStartPct": 0.3333},
                                      {"SectorStartPct": 0.6667}]},
        "DriverInfo": {"DriverCarIdx": 0, "Drivers": [
            {"CarIdx": 0, "CarNumber": "10", "UserName": "Alice Alpha",
             "LicString": "A 4.0", "LicColor": 0x27d367}]},
        "SessionInfo": {"Sessions": [{"SessionNum": 0, "SessionType": "Qualify"}]},
    })
    p._read_snapshot()            # seed lap counter
    snap = p._read_snapshot()

    check("mode is driving", snap["mode"] == "driving")
    sess = snap["refs"]["session"]
    own = snap["refs"]["own"]
    check("session delta = LapDeltaToSessionBestLap", approx(sess["delta"], 0.10))
    check("own delta = LapDeltaToBestLap", approx(own["delta"], -0.25))
    check("session label is Pole", sess["ref_label"] == "Pole")
    check("own label is Own Best", own["ref_label"] == "Own Best")
    check("own ref_lap = own best lap", approx(own["ref_lap"], 90.0))
    check("own has_reference", own["have_reference"] is True)


# =============================================================================
# 2) SPECTATOR MODE — own-best reference differs from the pole reference
# =============================================================================
def base_spectator(p):
    ir = p.ir
    ir.d.update({
        "SessionUniqueID": 2, "SessionNum": 0, "SessionTime": 0.0,
        "IsOnTrack": False,
        "CamCarIdx": 0,
        "SplitTimeInfo": {"Sectors": [{"SectorStartPct": 0.0},
                                      {"SectorStartPct": 0.3333},
                                      {"SectorStartPct": 0.6667}]},
        "DriverInfo": {"DriverCarIdx": 0, "Drivers": [
            {"CarIdx": 0, "CarNumber": "10", "UserName": "Alice Alpha",
             "LicString": "A 4.0", "LicColor": 0x27d367},
            {"CarIdx": 1, "CarNumber": "11", "UserName": "Bob Beta",
             "LicString": "A 4.0", "LicColor": 0x27d367}]},
        "SessionInfo": {"Sessions": [{"SessionNum": 0, "SessionType": "Qualify"}]},
    })


def feed(p, t, laps, pcts, surfaces, bestlaps):
    ir = p.ir
    n = len(pcts)
    ir.d["SessionTime"] = t
    ir.d["CarIdxLap"] = laps
    ir.d["CarIdxLapDistPct"] = pcts
    ir.d["CarIdxTrackSurface"] = surfaces
    ir.d["CarIdxOnPitRoad"] = [False] * n
    ir.d["CarIdxBestLapTime"] = bestlaps
    ir.d["CarIdxLastLapTime"] = [0.0] * n
    ir.d["CarIdxPosition"] = [1, 2]
    return p._read_snapshot()


def run_lap(p, idx, pace, start_t, official, laps_after):
    """Drive car `idx` around one clean lap at constant `pace` (elapsed =
    pct*pace); the other car is out of world. Returns end SessionTime."""
    other = 1 - idx
    surf = [-1, -1]
    surf[idx] = 3
    laps = [1, 2]                      # baseline lap numbers
    laps[idx] = laps_after - 1
    best = [0.0, 0.0]
    best[other] = 88.0 if other == 1 else 0.0   # keep car1's pole best alive

    def pv(v):
        pp = [0.0, 0.0]
        pp[idx] = v
        return pp

    feed(p, start_t, list(laps), pv(0.0), list(surf), list(best))   # seed
    for k in range(1, 21):
        pct = k / 20.0
        feed(p, start_t + pct * pace, list(laps), pv(pct), list(surf), list(best))
    laps[idx] = laps_after
    best[idx] = official
    feed(p, start_t + pace, list(laps), pv(0.0), list(surf), list(best))
    return start_t + pace


def test_spectator():
    print("spectator mode (own-best vs pole reference):")
    p = iq.QualiDeltaPoller()
    base_spectator(p)

    # Car1 sets pole (88.0), car0 out of world.
    run_lap(p, idx=1, pace=88.0, start_t=0.0, official=88.0, laps_after=2)
    # Car0 sets its own best (90.0), car1 out of world.
    end = run_lap(p, idx=0, pace=90.0, start_t=100.0, official=90.0, laps_after=2)

    # Car0 now on a flying lap at pole pace (88) — at half-distance it is
    # 1.0s under its OWN 90 best but exactly on the 88 pole.
    snap = feed(p, end + 0.5 * 88.0, [2, 2], [0.5, 0.0], [3, -1], [90.0, 88.0])

    sess = snap["refs"]["session"]
    own = snap["refs"]["own"]
    check("mode is spectator", snap["mode"] == "spectator")
    check("pole reference exists", sess["have_reference"] is True)
    check("own reference exists", own["have_reference"] is True)
    check("pole delta ~ 0.0 (on pole pace)", approx(sess["delta"], 0.0))
    check("own delta ~ -1.0 (under own best)", approx(own["delta"], -1.0))
    check("own delta_ok", own["delta_ok"] is True)
    check("pole ref_lap = 88", approx(sess["ref_lap"], 88.0))
    check("own ref_lap = car's own best 90", approx(own["ref_lap"], 90.0))
    check("pole ref_driver is BETA", sess["ref_driver"] == "BETA")
    check("own ref_driver blank (it's the driver themselves)",
          own["ref_driver"] == "")
    check("own delta differs from pole delta",
          not approx(own["delta"], sess["delta"], 0.2))


# =============================================================================
# 3) Before the on-camera car has a best lap, own mode has no reference
# =============================================================================
def test_own_no_reference_yet():
    print("spectator: own reference absent until the car sets a lap:")
    p = iq.QualiDeltaPoller()
    base_spectator(p)
    # Only car1 has run a lap; camera is on car0 (no own best yet).
    run_lap(p, idx=1, pace=88.0, start_t=0.0, official=88.0, laps_after=2)
    # car0 appears mid-lap, cam=0, no completed lap of its own
    snap = feed(p, 300.0, [1, 2], [0.4, 0.0], [3, -1], [0.0, 88.0])
    own = snap["refs"]["own"]
    sess = snap["refs"]["session"]
    check("own has NO reference yet", own["have_reference"] is False)
    check("own delta is None", own["delta"] is None)
    check("pole reference still available", sess["have_reference"] is True)


# =============================================================================
# 3c) Pole driver LEAVES the quali (2026-11-24): pole stays his lap
# =============================================================================
def _with_uids(p):
    for i, d in enumerate(p.ir.d["DriverInfo"]["Drivers"]):
        d["UserID"] = 1000 + i


def test_pole_driver_left():
    print("spectator: pole driver has left the session:")
    p = iq.QualiDeltaPoller()
    base_spectator(p)
    _with_uids(p)
    run_lap(p, idx=1, pace=88.0, start_t=0.0, official=88.0, laps_after=2)
    end = run_lap(p, idx=0, pace=90.0, start_t=100.0, official=90.0, laps_after=2)
    # Bob (pole, car1) leaves: iRacing drops his best (-1), car out of world
    snap = feed(p, end + 0.5 * 88.0, [2, -1], [0.5, -1.0], [3, -1], [90.0, -1.0])
    sess = snap["refs"]["session"]
    check("pole time still 88 (not P2's 90)", approx(sess["ref_lap"], 88.0))
    check("pole driver still BETA", sess["ref_driver"] == "BETA")
    check("pole delta ~ 0.0 on pole pace (old code: -2.0)",
          approx(sess["delta"], 0.0))

    # his slot is taken by a NEW driver with no lap yet
    p.ir.d["DriverInfo"]["Drivers"][1].update(
        {"UserName": "Carl Gamma", "UserID": 2000})
    snap = feed(p, end + 0.6 * 88.0, [2, 1], [0.6, 0.1], [3, 3], [90.0, 0.0])
    sess = snap["refs"]["session"]
    check("slot reused: pole still 88 / BETA",
          approx(sess["ref_lap"], 88.0) and sess["ref_driver"] == "BETA")


def test_pole_left_before_start():
    print("spectator: pole lap only in ResultsPositions (overlay started later):")
    p = iq.QualiDeltaPoller()
    base_spectator(p)
    _with_uids(p)
    p.ir.d["SessionInfo"]["Sessions"][0]["ResultsPositions"] = [
        {"CarIdx": 1, "FastestTime": 88.0}, {"CarIdx": 0, "FastestTime": 90.0}]
    # only car0's lap seen; car1 never reports a best (left before)
    end = 0.0
    p.ir.d["CamCarIdx"] = 0
    other_best = -1.0
    # run car0's lap by hand (run_lap gives car1 a live 88 best)
    feed(p, 0.0, [1, -1], [0.0, -1.0], [3, -1], [0.0, other_best])
    for k in range(1, 21):
        feed(p, k / 20.0 * 90.0, [1, -1], [k / 20.0, -1.0], [3, -1], [0.0, other_best])
    feed(p, 90.0, [2, -1], [0.0, -1.0], [3, -1], [90.0, other_best])
    p._official_t = -1e9
    snap = feed(p, 90.0 + 0.5 * 88.0, [2, -1], [0.5, -1.0], [3, -1], [90.0, other_best])
    sess = snap["refs"]["session"]
    check("pole = official 88", approx(sess["ref_lap"], 88.0))
    check("pole driver from ResultsPositions = BETA", sess["ref_driver"] == "BETA")
    check("delta ~ 0.0 on pole pace (curve scaled 90 -> 88)",
          approx(sess["delta"], 0.0, 0.1))


def test_pole_lap_deleted():
    print("spectator: pole lap deleted afterwards (driver still there):")
    p = iq.QualiDeltaPoller()
    base_spectator(p)
    _with_uids(p)
    run_lap(p, idx=1, pace=88.0, start_t=0.0, official=88.0, laps_after=2)
    end = run_lap(p, idx=0, pace=90.0, start_t=100.0, official=90.0, laps_after=2)
    # iRacing deletes Bob's 88; his best is now 89.5, he is still in the car
    snap = feed(p, end + 0.5 * 88.0, [2, 2], [0.5, 0.0], [3, 3], [90.0, 89.5])
    sess = snap["refs"]["session"]
    check("deleted lap forgotten (pole no longer 88)",
          not approx(sess["ref_lap"], 88.0, 0.01))


# =============================================================================
# 3b) Stale lap-start (replay rewind / session-time jump) is hidden, not shown
# =============================================================================
def test_stale_lapstart_guard():
    print("spectator: stale lap-start delta is hidden (the -1027 bug):")
    p = iq.QualiDeltaPoller()
    ref_p = [0.0, 0.5, 1.0]
    ref_t = [0.0, 25.0, 50.0]
    # lap-start far in the "future" vs t -> elapsed hugely negative
    p._car_lap_start = {0: 5000.0}
    d, ok = p._spec_cam_delta(0, ref_p, ref_t, [0.5, 0.5], [False, False],
                              [3, 3], 100.0)
    check("hugely negative elapsed -> hidden", d is None and ok is False)
    # a normal in-progress lap still reads a sane delta
    p._car_lap_start = {0: 100.0}
    d, ok = p._spec_cam_delta(0, ref_p, ref_t, [0.5, 0.5], [False, False],
                              [3, 3], 124.0)
    check("normal lap -> delta shown", approx(d, -1.0) and ok is True)


# =============================================================================
# 4) _colorize_sectors faster/slower/pending logic
# =============================================================================
def test_colorize():
    print("sector colorization vs a reference:")
    out = iq.QualiDeltaPoller._colorize_sectors(
        [44.0, None, 30.0], [45.0, 20.0, 29.0])
    check("S1 faster (44 < 45)", out[0]["state"] == "faster")
    check("S2 pending (no time)", out[1]["state"] == "pending")
    check("S3 slower (30 > 29)", out[2]["state"] == "slower")
    check("S1 delta ~ -1.0", approx(out[0]["delta"], -1.0))


def test_team_event():
    print("spectator: team event shows the pole lap's TEAM (IEC, 2026-11-28):")
    p = iq.QualiDeltaPoller()
    base_spectator(p)
    _with_uids(p)
    p.ir.d["WeekendInfo"] = {"TeamRacing": 1}
    p.ir.d["DriverInfo"]["Drivers"][0]["TeamName"] = "Alpha Racing"
    p.ir.d["DriverInfo"]["Drivers"][1]["TeamName"] = "WS Racing eSports LMP2 I [CAS IEC]"
    run_lap(p, idx=1, pace=88.0, start_t=0.0, official=88.0, laps_after=2)
    end = run_lap(p, idx=0, pace=90.0, start_t=100.0, official=90.0, laps_after=2)
    snap = feed(p, end + 0.5 * 88.0, [2, 2], [0.5, 0.0], [3, -1], [90.0, 88.0])
    sess = snap["refs"]["session"]
    check("team event: pole driver + his team, league tag cut",
          sess["ref_driver"] == "BETA" and sess["ref_team"] == "WS Racing eSports LMP2 I")
    # the pole driver leaves: team kept with the remembered lap
    snap = feed(p, end + 0.6 * 88.0, [2, -1], [0.6, -1.0], [3, -1], [90.0, -1.0])
    check("pole driver left: team still shown", snap["refs"]["session"]["ref_team"] == "WS Racing eSports LMP2 I")
    # solo session: no team line (iRacing reports the driver as his own team)
    q = iq.QualiDeltaPoller()
    base_spectator(q)
    q.ir.d["WeekendInfo"] = {"TeamRacing": 0}
    q.ir.d["DriverInfo"]["Drivers"][1]["TeamName"] = "Bob Beta"
    run_lap(q, idx=1, pace=88.0, start_t=0.0, official=88.0, laps_after=2)
    end = run_lap(q, idx=0, pace=90.0, start_t=100.0, official=90.0, laps_after=2)
    snap = feed(q, end + 0.5 * 88.0, [2, 2], [0.5, 0.0], [3, -1], [90.0, 88.0])
    check("solo session: no team line", snap["refs"]["session"]["ref_team"] == "")


if __name__ == "__main__":
    test_driving()
    test_spectator()
    test_own_no_reference_yet()
    test_pole_driver_left()
    test_pole_left_before_start()
    test_pole_lap_deleted()
    test_stale_lapstart_guard()
    test_colorize()
    test_team_event()
    print(f"\n{PASS}/{PASS + FAIL} checks passed"
          + ("" if FAIL == 0 else f"  ({FAIL} FAILED)"))
    sys.exit(1 if FAIL else 0)
