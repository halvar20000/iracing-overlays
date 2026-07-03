"""
Offline verification for iracing_drivercard.py — stubbed irsdk + flask.
Run:  python test_drivercard.py
"""
import sys
import types

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

import iracing_drivercard as dc  # noqa: E402

PASS = []
FAIL = []


def check(label, cond, extra=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'  ' + extra if extra else ''}")


def drv(cidx, name, **kw):
    d = {"CarIdx": cidx, "UserName": name, "TeamName": kw.get("team", ""),
         "CarNumber": kw.get("num", "7"), "IRating": kw.get("irating", 3248),
         "LicString": kw.get("lic", "A 4.99"), "LicColor": kw.get("lic_color", 0x0153DB),
         "CarClassShortName": kw.get("cls", "GT3"),
         "CarClassColor": kw.get("cls_color", 0xFF6B35),
         "CurDriverIncidentCount": kw.get("inc", 4),
         "TeamIncidentCount": kw.get("team_inc", 9),
         "CarIsPaceCar": 0, "IsSpectator": 0}
    return d


def make_poller(drivers, cam_idx, **telemetry):
    p = dc.DriverCardPoller()
    n = max(d["CarIdx"] for d in drivers) + 1
    p.ir.d.update({
        "CamCarIdx": cam_idx,
        "DriverInfo": {"Drivers": drivers},
        "CarIdxBestLapTime": [0.0] * n,
        "CarIdxLastLapTime": [0.0] * n,
        "CarIdxClassPosition": [0] * n,
        "CarIdxPosition": [0] * n,
    })
    p.ir.d.update(telemetry)
    return p


print("\n[1] full card readout")
p = make_poller([drv(0, "Thomas Herbrig", team="CAS Racing")], 0,
                CarIdxBestLapTime=[112.301], CarIdxLastLapTime=[112.884],
                CarIdxClassPosition=[5], CarIdxPosition=[12])
s = p._read_snapshot()
check("shown", s["show"], s.get("reason", ""))
check("abbrev name", s["name"] == "T. Herbrig", s["name"])
check("team kept", s["team"] == "CAS Racing")
check("irating", s["irating"] == 3248)
check("license string", s["lic"] == "A 4.99")
check("license color hex", s["lic_color"] == "#0153db", str(s["lic_color"]))
check("class position preferred", s["position"] == 5, str(s["position"]))
check("best lap", abs(s["best_lap"] - 112.301) < 1e-6)
check("last lap", abs(s["last_lap"] - 112.884) < 1e-6)
check("last not best", s["last_is_best"] is False)
check("incidents", s["incidents"] == 4)

print("\n[2] personal best flash: last == best")
p = make_poller([drv(0, "Thomas Herbrig")], 0,
                CarIdxBestLapTime=[112.301], CarIdxLastLapTime=[112.301])
s = p._read_snapshot()
check("last_is_best", s["last_is_best"] is True)

print("\n[3] no laps yet -> None, not 0.0")
p = make_poller([drv(0, "Thomas Herbrig")], 0)
s = p._read_snapshot()
check("best None", s["best_lap"] is None)
check("last None", s["last_lap"] is None)
check("no pb flag", s["last_is_best"] is False)

print("\n[4] team name == driver name -> hidden")
p = make_poller([drv(0, "Thomas Herbrig", team="Thomas Herbrig")], 0)
s = p._read_snapshot()
check("solo team suppressed", s["team"] == "", repr(s["team"]))

print("\n[5] class position 0 -> falls back to overall position")
p = make_poller([drv(0, "Thomas Herbrig")], 0,
                CarIdxClassPosition=[0], CarIdxPosition=[7])
s = p._read_snapshot()
check("fallback position", s["position"] == 7, str(s["position"]))

print("\n[6] driver incident count missing -> team fallback")
p = make_poller([drv(0, "Thomas Herbrig", inc=None, team_inc=9)], 0)
s = p._read_snapshot()
check("team incidents used", s["incidents"] == 9, str(s["incidents"]))

print("\n[7] no camera car -> hidden")
p = make_poller([drv(0, "Thomas Herbrig")], -1)
s = p._read_snapshot()
check("hidden", not s["show"], s.get("reason", ""))

print("\n[8] pace car / spectator filtered")
pace = drv(1, "Pace Car")
pace["CarIsPaceCar"] = 1
p = make_poller([drv(0, "Thomas Herbrig"), pace], 1)
s = p._read_snapshot()
check("pace car not a target", not s["show"], s.get("reason", ""))

print(f"\n{'='*50}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
