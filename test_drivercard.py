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
         "CarIsPaceCar": 0, "IsSpectator": 0, "UserID": kw.get("uid", 0)}
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

print("\n[6] incidents removed from payload (see module docstring)")
p = make_poller([drv(0, "Thomas Herbrig")], 0)
s = p._read_snapshot()
check("no incidents key", "incidents" not in s)

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

print("\n[9] solo session: team from CLS (league with the most drivers of the field)")
dc.proam._teams = {"cas-gt3-wct": {101: "Speed Monkeys II", 102: "REVORYX", 103: "NEON"},
                   "cas-pccd": {101: "Porsche Team X", 104: "Y"}}
field = [drv(i, f"Driver {i}", uid=101 + i) for i in range(3)]
s = make_poller(field, 0)._read_snapshot()
check("CLS team of the matching league", s["team"] == "Speed Monkeys II", repr(s["team"]))
s = make_poller([drv(0, "Driver 0", uid=101, team="Real Team")] + field[1:], 0)._read_snapshot()
check("iRacing team wins", s["team"] == "Real Team", repr(s["team"]))
s = make_poller([drv(0, "Driver 0", uid=101), drv(1, "Other", uid=999)], 0)._read_snapshot()
check("field matches no league -> no team", s["team"] == "", repr(s["team"]))
dc.proam._teams = {}

print("\n[10] team event: line-up from CLS + whoever was seen in the car")
dc.proam._get_json = lambda path, params=None: (
    {"leagues": [{"slug": "cas-iec", "seasons": [{"id": "s4"}]}]} if "leagues" in path else
    {"standings": [
        {"iracingMemberId": "101", "name": "Anna Eins", "countryCode": "DE", "teamName": "Team A"},
        {"iracingMemberId": "201", "name": "Bert Zwei", "countryCode": "FR", "teamName": "Team A"},
        {"iracingMemberId": "301", "name": "Weg Gegangen", "countryCode": "DE", "teamName": "Team A", "retiredAt": "2026-01-01"},
        {"iracingMemberId": "102", "name": "B", "teamName": "Team B"},
        {"iracingMemberId": "103", "name": "C", "teamName": "Team C"},
    ]})
dc.proam._fetch_countries = dc.ProAmRoster._fetch_countries.__get__(dc.proam)
import cls_proam, tempfile, pathlib   # noqa: E402
_tmp = pathlib.Path(tempfile.mkdtemp())
cls_proam.ROSTER_CACHE_PATH = _tmp / "r.json"
cls_proam.TEAM_CACHE_PATH = _tmp / "t.json"
cls_proam.COUNTRY_CACHE_PATH = _tmp / "c.json"
dc.proam._fetch_countries()
field = [dict(drv(i, f"Driver {i}", uid=101 + i, team=f"Team {i}"), TeamID=500 + i) for i in range(3)]
field[0]["UserName"] = "Anna Eins"
field[0]["FlairName"] = "Germany"
p = make_poller(field, 0, WeekendInfo={"TeamRacing": 1, "SessionID": 1, "SubSessionID": 1})
s = p._read_snapshot()
check("team event flagged", s["team_event"] is True)
check("iRacing team name", s["team_name"] == "Team 0", s["team_name"])
names = [x["name"] for x in s["lineup"]]
check("driver in the car first", s["lineup"][0]["current"] and names[0] == "Anna Eins", str(names))
check("team mate from CLS, retired left out", names == ["Anna Eins", "Bert Zwei"], str(names))
check("team mate's flag", s["lineup"][1]["country"] == "fr" and not s["lineup"][1]["current"], str(s["lineup"][1]))
check("flag of the driver in the car", s["lineup"][0]["country"] == "de")
# driver swap to the team mate, then to a substitute CLS does not list
field[0].update(UserName="Bert Zwei", UserID=201)
p._drivers_t = -99
s = p._read_snapshot()
check("swap: highlight moves", [x["name"] for x in s["lineup"]] == ["Bert Zwei", "Anna Eins"], str(s["lineup"]))
field[0].update(UserName="Ersatz Fahrer", UserID=777)
p._drivers_t = -99
s = p._read_snapshot()
names = [x["name"] for x in s["lineup"]]
check("substitute: still the whole team", names == ["Ersatz Fahrer", "Anna Eins", "Bert Zwei"], str(names))
p.ir.d["WeekendInfo"] = {"TeamRacing": 1, "SessionID": 2, "SubSessionID": 2}
p._drivers_t = -99
s = p._read_snapshot()
check("new session forgets substitutes' history", [x["name"] for x in s["lineup"]] == ["Ersatz Fahrer"], str(s["lineup"]))
p = make_poller([drv(0, "Solo Fahrer")], 0)
s = p._read_snapshot()
check("not a team event: no line-up", s["team_event"] is False and s["lineup"] == [])
dc.proam._teams = {}
dc.proam._rosters = {}

print("\n[11] /combo panel: class colour only in a multiclass field")
one = [dict(drv(i, f"D{i}"), CarClassID=1) for i in range(2)]
s = make_poller(one, 0)._read_snapshot()
check("single class: default green", s["panel_color"] is None)
two = [dict(drv(0, "A", cls_color=0x33CEFF), CarClassID=1), dict(drv(1, "B", cls_color=0x1040A0), CarClassID=2)]
s = make_poller(two, 0)._read_snapshot()
check("class colour", s["panel_color"] == "#33ceff", str(s["panel_color"]))
check("dark text on a light colour", s["panel_text"] == "#0a0a0f", str(s["panel_text"]))
s = make_poller(two, 1)._read_snapshot()
check("white text on a dark colour", s["panel_color"] == "#1040a0" and s["panel_text"] == "#ffffff", str(s["panel_text"]))

print(f"\n{'='*50}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
