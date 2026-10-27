"""
Offline verification for iracing_catchup.py — stubbed irsdk + flask,
fake clock, synthetic race scenarios. Run:  python test_catchup.py
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
flask_stub.Response = lambda *a, **k: None
flask_stub.jsonify = lambda *a, **k: None
flask_stub.abort = lambda *a, **k: None
flask_stub.send_file = lambda *a, **k: None
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

    def __getitem__(self, k):
        return self.d.get(k)


irsdk_stub.IRSDK = FakeIR
sys.modules["irsdk"] = irsdk_stub

import iracing_catchup as ic  # noqa: E402

# ---- fake clock ---------------------------------------------------------------
_clock = [1000.0]
ic.time = types.SimpleNamespace(monotonic=lambda: _clock[0])


def advance(s):
    _clock[0] += s


# ---- scenario harness -----------------------------------------------------------
def make_poller(drivers, cam_idx, session_type="Race"):
    p = ic.CatchPoller()
    ir = p.ir
    n = len(drivers)
    ir.d.update({
        "SessionUniqueID": 1, "SessionNum": 0, "SessionTime": 0.0,
        "CamCarIdx": cam_idx,
        "DriverInfo": {"Drivers": drivers},
        "SessionInfo": {"Sessions": [{"SessionNum": 0,
                                      "SessionType": session_type}]},
        "CarIdxLap": [1] * n,
        "CarIdxLapDistPct": [0.0] * n,
        "CarIdxLastLapTime": [0.0] * n,
        "CarIdxOnPitRoad": [False] * n,
        "CarIdxTrackSurface": [3] * n,
        "CarIdxF2Time": [0.0] * n,
        "EstLapTime": 90.0,
    })
    # Baseline poll so lap counters are seeded — without this the first
    # simulated lap is (correctly) ignored by the poller, which refuses to
    # trust a lap whose start it never observed.
    p._read_snapshot()
    return p


def drv(cidx, name, num, cls=1):
    return {"CarIdx": cidx, "UserName": name, "CarNumber": num,
            "CarClassID": cls, "CarClassShortName": "GT3",
            "CarClassColor": 0xFF6B35, "CarIsPaceCar": 0, "IsSpectator": 0}


def complete_lap(p, cidx, lap_time, pit=False):
    """Simulate one completed lap for car cidx."""
    ir = p.ir
    if pit:
        ir.d["CarIdxOnPitRoad"][cidx] = True
        ir.d["SessionTime"] = _clock[0]
        p._read_snapshot()
        ir.d["CarIdxOnPitRoad"][cidx] = False
    ir.d["CarIdxLap"][cidx] += 1
    ir.d["CarIdxLastLapTime"][cidx] = lap_time
    ir.d["SessionTime"] = _clock[0]
    p._read_snapshot()          # detects the increment, queues pending
    advance(0.5)
    ir.d["SessionTime"] = _clock[0]
    p._read_snapshot()          # resolves pending -> history append


PASS = []
FAIL = []


def check(label, cond, extra=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'  ' + extra if extra else ''}")


# =========================== 1. CATCHING =====================================
print("\n[1] catching: focus 0.5s/lap faster, 3.0s gap -> ~6 laps")
p = make_poller([drv(0, "Alice Ahead", "11"), drv(1, "Frank Focus", "7")], 1)
ir = p.ir
ir.d["CarIdxLap"] = [10, 10]
ir.d["CarIdxLapDistPct"] = [0.50, 0.30]
ir.d["CarIdxF2Time"] = [5.0, 8.0]
for lt_a, lt_f in [(90.5, 90.0)] * 3:
    complete_lap(p, 0, lt_a)
    complete_lap(p, 1, lt_f)
    advance(2)
# keep same relative order after the simulated laps
ir.d["CarIdxLapDistPct"] = [0.50, 0.30]
ir.d["CarIdxLap"] = [max(ir.d["CarIdxLap"])] * 2
snap = p._read_snapshot()
check("banner shown", snap["show"], snap.get("reason", ""))
check("status catching", snap["status"] == "catching", snap["status"])
check("gap 3.0s", snap["gap"] is not None and abs(snap["gap"] - 3.0) < 1e-6,
      str(snap["gap"]))
check("pace delta +0.5", snap["pace_delta"] is not None
      and abs(snap["pace_delta"] - 0.5) < 1e-6, str(snap["pace_delta"]))
check("catch in ~6 laps", snap["catch_laps"] is not None
      and abs(snap["catch_laps"] - 6.0) < 0.01, str(snap["catch_laps"]))
check("catch time ~540s", snap["catch_seconds"] is not None
      and abs(snap["catch_seconds"] - 540.0) < 1.0, str(snap["catch_seconds"]))
check("ahead name", snap["ahead"]["name"] == "A. Ahead", snap["ahead"]["name"])
check("positions P1/P2", snap["ahead"]["pos"] == 1 and snap["focus"]["pos"] == 2)

# =========================== 2. LOSING =======================================
print("\n[2] losing: focus 0.4s/lap slower")
p = make_poller([drv(0, "Alice Ahead", "11"), drv(1, "Frank Focus", "7")], 1)
ir = p.ir
ir.d["CarIdxF2Time"] = [2.0, 4.5]
for _ in range(3):
    complete_lap(p, 0, 89.8)
    complete_lap(p, 1, 90.2)
    advance(2)
ir.d["CarIdxLap"] = [max(ir.d["CarIdxLap"])] * 2
ir.d["CarIdxLapDistPct"] = [0.6, 0.4]
snap = p._read_snapshot()
check("status losing", snap["status"] == "losing", snap["status"])
check("no catch prediction", snap["catch_laps"] is None)

# =========================== 3. HOLDING ======================================
print("\n[3] holding: pace within 0.05s/lap")
p = make_poller([drv(0, "Alice Ahead", "11"), drv(1, "Frank Focus", "7")], 1)
p.ir.d["CarIdxF2Time"] = [2.0, 4.0]
for _ in range(3):
    complete_lap(p, 0, 90.02)
    complete_lap(p, 1, 90.00)
    advance(2)
p.ir.d["CarIdxLap"] = [max(p.ir.d["CarIdxLap"])] * 2
p.ir.d["CarIdxLapDistPct"] = [0.6, 0.4]
snap = p._read_snapshot()
check("status holding", snap["status"] == "holding", snap["status"])

# =========================== 4. PIT LAP EXCLUDED =============================
print("\n[4] pit lap excluded from the rolling window")
p = make_poller([drv(0, "Alice Ahead", "11"), drv(1, "Frank Focus", "7")], 1)
complete_lap(p, 1, 90.0)
complete_lap(p, 1, 118.0, pit=True)     # in-lap through pit road -> excluded
complete_lap(p, 1, 90.4)
hist = list(p._lap_hist.get(1, []))
check("pit lap not recorded", 118.0 not in hist, str(hist))
check("clean laps kept", hist == [90.0, 90.4], str(hist))

# =========================== 5. MULTICLASS ===================================
print("\n[5] multiclass: other-class car in between is skipped")
p = make_poller([drv(0, "Alice Ahead", "11", cls=1),
                 drv(1, "Frank Focus", "7", cls=1),
                 drv(2, "Larry LMP", "99", cls=2)], 1)
ir = p.ir
ir.d["CarIdxLap"] = [10, 10, 10]
ir.d["CarIdxLapDistPct"] = [0.50, 0.30, 0.40]   # LMP physically in between
ir.d["CarIdxF2Time"] = [5.0, 8.0, 0.0]
snap = p._read_snapshot()
check("banner shown", snap["show"], snap.get("reason", ""))
check("same-class ahead picked", snap["ahead"]["name"] == "A. Ahead",
      snap["ahead"]["name"])

# =========================== 6. CLASS LEADER ON CAMERA =======================
print("\n[6] class leader on camera -> hidden")
p = make_poller([drv(0, "Alice Ahead", "11"), drv(1, "Frank Focus", "7")], 0)
p.ir.d["CarIdxLap"] = [10, 10]
p.ir.d["CarIdxLapDistPct"] = [0.50, 0.30]
snap = p._read_snapshot()
check("hidden", not snap["show"], snap.get("reason", ""))
check("reason mentions leads", "lead" in snap.get("reason", ""))

# =========================== 7. LAPPED AHEAD =================================
print("\n[7] car ahead is a full lap up -> lap gap, no prediction")
p = make_poller([drv(0, "Alice Ahead", "11"), drv(1, "Frank Focus", "7")], 1)
ir = p.ir
for _ in range(3):
    complete_lap(p, 0, 89.0)
    complete_lap(p, 1, 90.0)
    advance(2)
ir.d["CarIdxLap"] = [12, 10]
ir.d["CarIdxLapDistPct"] = [0.60, 0.30]
snap = p._read_snapshot()
check("lap_gap >= 1", snap["lap_gap"] >= 1, str(snap["lap_gap"]))
check("no seconds gap", snap["gap"] is None)
check("no catch prediction", snap["catch_laps"] is None)

# =========================== 8. NOT A RACE ===================================
print("\n[8] practice session -> hidden")
p = make_poller([drv(0, "Alice Ahead", "11"), drv(1, "Frank Focus", "7")], 1,
                session_type="Practice")
snap = p._read_snapshot()
check("hidden in practice", not snap["show"], snap.get("reason", ""))

# =========================== 9. SESSION CHANGE RESET =========================
print("\n[9] session change wipes lap histories")
p = make_poller([drv(0, "Alice Ahead", "11"), drv(1, "Frank Focus", "7")], 1)
complete_lap(p, 1, 90.0)
assert p._lap_hist.get(1)
p.ir.d["SessionUniqueID"] = 2       # new hosted session
p.ir.d["SessionTime"] = 0.0
p._read_snapshot()
check("histories cleared", not p._lap_hist.get(1), str(p._lap_hist))

# =========================== 9b. F1 GRAPHICS DATA ============================
print("\n[9b] /laps comparison + name fields for /gap and /chasing")
d0 = drv(0, "Lando Norris", "4"); d0["TeamName"] = "McLaren"
d0["CarDesignStr"] = "1,ff8a1f,000000,ffffff"
d1 = drv(1, "Andrea Kimi Antonelli", "12"); d1["TeamName"] = "Andrea Kimi Antonelli"
d1["CarDesignStr"] = "1,111111,ffffff,ffffff"
p = make_poller([d0, d1], 1)
ir = p.ir
for i, (ta, tf) in enumerate([(107.830, 107.166), (107.731, 107.128),
                              (107.346, 107.323), (106.892, 106.990),
                              (108.0, 107.0)]):
    complete_lap(p, 0, ta, pit=(i == 4))
    if i < 4:                       # focus has not finished lap 5 yet
        complete_lap(p, 1, tf)
    advance(2)
ir.d["CarIdxLap"] = [ir.d["CarIdxLap"][0], ir.d["CarIdxLap"][0]]
ir.d["CarIdxLapDistPct"] = [0.30, 0.20]
ir.d["CarIdxF2Time"] = [0.0, 0.787]
snap = p._read_snapshot()
laps = snap.get("laps") or []
check("only laps BOTH completed", [l["lap"] for l in laps] == [1, 2, 3, 4],
      str([l["lap"] for l in laps]))
check("times paired per lap", laps and laps[0]["ahead"] == 107.830
      and laps[0]["focus"] == 107.166, str(laps[:1]))
check("first / last name split", snap["focus"]["first"] == "Andrea Kimi"
      and snap["focus"]["last"] == "Antonelli")
check("team kept", snap["ahead"]["team"] == "McLaren")
check("solo team suppressed", snap["focus"]["team"] == "")
check("livery colour accent", snap["ahead"]["color"] == "#ff8a1f",
      str(snap["ahead"]["color"]))
check("dark livery colour -> None", snap["focus"]["color"] is None)
check("gap", abs(snap["gap"] - 0.787) < 1e-9, str(snap["gap"]))
log0 = list(p._lap_log[0])
check("pit lap logged in /laps log", log0[-1] == (5, 108.0, True), str(log0[-1]))
check("pit lap still out of pace window", 108.0 not in p._lap_hist[0])

# =========================== 10. LIVERY RENDER HELPERS ======================
print("\n[10] livery render helpers")
v = ic._car_path_variants("mx5 mx52016")
check("nested carPath variant", "mx5/mx52016" in v, str(v))
check("flat carPath single", ic._car_path_variants("porsche992rgt3") ==
      ["porsche992rgt3"])
params = ic._build_render_params({
    "CarPath": "porsche992rgt3", "CarID": 173,
    "CarDesignStr": "23,ed2129,000000,ffffff",
    "CarNumberDesignStr": "0,0,ffffff,777777,000000",
    "CarNumber": "7", "LicColor": 0x0153DB,
    "UserName": "Frank Focus",
}, "C:\\paint\\porsche992rgt3\\car_123.tga")
check("carPat + carCol", params.get("carPat") == "23"
      and params.get("carCol") == "ed2129,000000,ffffff", str(params.get("carCol")))
check("number + numcol", params.get("number") == "7"
      and params.get("numcol") == "ffffff,777777,000000")
check("licCol hex", params.get("licCol") == "0153db", str(params.get("licCol")))
check("custom paint passed", params.get("carCustPaint", "").endswith("car_123.tga"))
p = make_poller([drv(0, "Alice Ahead", "11"), drv(1, "Frank Focus", "7")], 1)
p.ir.d["CarIdxLap"] = [10, 10]
p.ir.d["CarIdxLapDistPct"] = [0.50, 0.30]
p.ir.d["CarIdxF2Time"] = [5.0, 8.0]
snap = p._read_snapshot()
check("cidx in payload", snap["show"] and snap["focus"]["cidx"] == 1
      and snap["ahead"]["cidx"] == 0)
check("drivers keep raw dict", "raw" in p._drivers[0] and "cust_id" in p._drivers[0])

# ---------------------------------------------------------------------------
# GT3 WCT: an AM on camera battles the AM ahead, not a PRO in between
class FakeRoster:
    def __init__(self, cls, active=True):
        self.cls, self.active = cls, active
    def active_for(self, ids):
        return self.active
    def lookup_field(self, uid, active):
        if not active or not uid:
            return None
        return self.cls.get(uid, "UNRATED")

def wct_drv(cidx, name, num, uid):
    d = drv(cidx, name, num); d["UserID"] = uid; return d

real_proam = ic.proam
ic.proam = FakeRoster({101: "AM", 102: "PRO", 103: "AM", 104: "PRO"})
field = [wct_drv(0, "Alan Amleader", "1", 101), wct_drv(1, "Peter Pro", "2", 102),
         wct_drv(2, "Arne Amfocus", "3", 103), wct_drv(3, "Paul Probehind", "4", 104),
         wct_drv(4, "Nils Norating", "5", 105)]
p = make_poller(field, 2)
p.ir.d["CarIdxLap"] = [10] * 5
p.ir.d["CarIdxLapDistPct"] = [0.60, 0.50, 0.40, 0.30, 0.20]
p.ir.d["CarIdxF2Time"] = [0.0, 2.0, 4.0, 6.0, 8.0]
snap = p._read_snapshot()
check("AM focus chases the AM ahead", snap["show"] and snap["ahead"]["cidx"] == 0,
      str(snap.get("ahead")))
check("AM gap skips the PRO", snap["gap"] is not None and abs(snap["gap"] - 4.0) < 1e-6,
      str(snap.get("gap")))
check("AM class positions P1/P2", snap["ahead"]["pos"] == 1 and snap["focus"]["pos"] == 2)
check("proam in payload", snap["focus"]["proam"] == "AM" and snap["ahead"]["proam"] == "AM")
p.ir.d["CamCarIdx"] = 3
snap = p._read_snapshot()
check("PRO focus chases the PRO ahead", snap["show"] and snap["ahead"]["cidx"] == 1
      and snap["focus"]["pos"] == 2, str(snap.get("ahead")))
p.ir.d["CamCarIdx"] = 0
snap = p._read_snapshot()
check("AM class leader hides", not snap["show"], snap.get("reason", ""))
p.ir.d["CamCarIdx"] = 4
snap = p._read_snapshot()
check("UNRATED focus keeps the car class", snap["show"] and snap["ahead"]["cidx"] == 3,
      str(snap.get("ahead")))
ic.proam = FakeRoster({}, active=False)
p.ir.d["CamCarIdx"] = 2
snap = p._read_snapshot()
check("not a WCT field = car class", snap["show"] and snap["ahead"]["cidx"] == 1
      and snap["focus"]["proam"] is None)
ic.proam = real_proam

# ---------------------------------------------------------------------------
print(f"\n{'='*50}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
