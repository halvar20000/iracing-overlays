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


if __name__ == "__main__":
    test_driving()
    test_spectator()
    test_own_no_reference_yet()
    test_stale_lapstart_guard()
    test_colorize()
    print(f"\n{PASS}/{PASS + FAIL} checks passed"
          + ("" if FAIL == 0 else f"  ({FAIL} FAILED)"))
    sys.exit(1 if FAIL else 0)
