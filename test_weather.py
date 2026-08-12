"""
Offline verification for iracing_weather.py — stubbed irsdk + flask,
fake clock. Run:  python test_weather.py
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

import iracing_weather as iw  # noqa: E402

_clock = [1000.0]
iw.time = types.SimpleNamespace(monotonic=lambda: _clock[0])


def advance(s):
    _clock[0] += s


PASS = []
FAIL = []


def check(label, cond, extra=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'  ' + extra if extra else ''}")


def make_poller(**telemetry):
    p = iw.WeatherPoller()
    p.ir.d.update({
        "SessionUniqueID": 1, "SessionNum": 0, "SessionTime": 0.0,
        "TrackTempCrew": 38.2, "AirTemp": 24.1, "RelativeHumidity": 0.62,
        "Precipitation": 0.0, "WindVel": 3.4, "WindDir": 0.785,
        "Skies": 1, "TrackWetness": 1, "WeatherDeclaredWet": 0,
    })
    p.ir.d.update(telemetry)
    return p


print("\n[1] basic readout")
p = make_poller()
s = p._read_snapshot()
check("track temp", abs(s["track_temp"] - 38.2) < 1e-6, str(s["track_temp"]))
check("air temp", abs(s["air_temp"] - 24.1) < 1e-6)
check("humidity %", abs(s["humidity"] - 62.0) < 1e-6, str(s["humidity"]))
check("precip %", s["precip"] == 0.0)
check("wetness label", s["wetness"] == "DRY", str(s["wetness"]))
check("wind km/h", abs(s["wind_kmh"] - 12.24) < 0.01, str(s["wind_kmh"]))
check("wind compass NE", s["wind_compass"] == "NE", str(s["wind_compass"]))
check("skies label", s["skies"] == "PARTLY CLOUDY", str(s["skies"]))
check("not declared wet", s["declared_wet"] is False)
check("no trend yet", s["trend_track"] is None)

print("\n[2] TrackTempCrew fallback to TrackTemp")
p = make_poller(TrackTempCrew=None, TrackTemp=35.5)
s = p._read_snapshot()
check("fallback used", abs(s["track_temp"] - 35.5) < 1e-6, str(s["track_temp"]))

print("\n[3] missing rain vars (pre-rain build) -> None, no crash")
p = make_poller(Precipitation=None, TrackWetness=None, WeatherDeclaredWet=None)
s = p._read_snapshot()
check("precip None", s["precip"] is None)
check("wetness None", s["wetness"] is None)
check("declared wet False", s["declared_wet"] is False)

print("\n[4] warming trend: track +2C over 10 samples")
p = make_poller()
for i in range(10):
    p.ir.d["TrackTempCrew"] = 30.0 + i * 0.2
    advance(31)
    p._read_snapshot()
s = p._read_snapshot()
check("trend up", s["trend_track"] == "up", str(s["trend_track"]))
check("trend text warming", "Track warming" in s["trend_text"], s["trend_text"])

print("\n[5] cooling + rain increasing")
p = make_poller()
for i in range(10):
    p.ir.d["TrackTempCrew"] = 38.0 - i * 0.2
    p.ir.d["Precipitation"] = i * 0.05
    advance(31)
    p._read_snapshot()
s = p._read_snapshot()
check("trend down", s["trend_track"] == "down", str(s["trend_track"]))
check("rain up", s["trend_precip"] == "up", str(s["trend_precip"]))
check("combined text", s["trend_text"] == "Track cooling · Rain increasing",
      s["trend_text"])

print("\n[6] flat conditions -> flat trend, empty text")
p = make_poller()
for _ in range(10):
    advance(31)
    p._read_snapshot()
s = p._read_snapshot()
check("flat", s["trend_track"] == "flat", str(s["trend_track"]))
check("no text", s["trend_text"] == "", repr(s["trend_text"]))

print("\n[7] declared wet + wetness label")
p = make_poller(WeatherDeclaredWet=1, TrackWetness=5, Precipitation=0.35)
s = p._read_snapshot()
check("declared wet", s["declared_wet"] is True)
check("moderately wet", s["wetness"] == "MODERATELY WET", str(s["wetness"]))
check("precip 35%", abs(s["precip"] - 35.0) < 1e-6)

print("\n[8] session change resets trend history")
p = make_poller()
for i in range(6):
    p.ir.d["TrackTempCrew"] = 30.0 + i * 0.3
    advance(31)
    p._read_snapshot()
assert p._read_snapshot()["trend_track"] == "up"
p.ir.d["SessionUniqueID"] = 2
p.ir.d["SessionTime"] = 0.0
s = p._read_snapshot()
check("trend cleared", s["trend_track"] is None, str(s["trend_track"]))

print("\n[9] compass wrap: 350 degrees -> N")
p = make_poller(WindDir=6.1087)
s = p._read_snapshot()
check("compass N", s["wind_compass"] == "N", str(s["wind_compass"]))

print(f"\n{'='*50}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
