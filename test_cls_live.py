"""
Offline tests for the race logger's league-site (CLS) side.

Run with:  python test_cls_live.py

Covers two things:
  1. LIVE PIT STOPS — the driver's own stop, with tread readings, is sent to
     /api/race-log/live the moment the car leaves the pit lane; a spectated
     car's stop and a stop without tire data send nothing.
  2. What commit 626c6ff (2026-10-10) had silently dropped and is now back:
     the finished-log upload, the frozen-.exe log folder, weather samples,
     team driver swaps and the dashboard-free SDK incidents. RaceLogger
     v1.4.0 shipped without them; these checks keep them from going again.

irsdk / flask / requests are stubbed; `requests` records every call.
"""
import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

sys.modules.setdefault("irsdk", types.SimpleNamespace(IRSDK=lambda: None))


class _FakeApp:
    def __getattr__(self, name):
        def deco(*a, **k):
            if len(a) == 1 and callable(a[0]) and not k:
                return a[0]
            return lambda f: f
        return deco


_flask = types.ModuleType("flask")
_flask.Flask = lambda *a, **k: _FakeApp()
for _n in ("jsonify", "render_template_string", "send_file", "abort"):
    setattr(_flask, _n, lambda *a, **k: None)
_flask.request = types.SimpleNamespace(args={})
sys.modules["flask"] = _flask


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self.ok = status < 400
        self._body = body

    def json(self):
        return self._body


class FakeRequests(types.ModuleType):
    """Records calls; answers from a queue (default: ok)."""

    def __init__(self):
        super().__init__("requests")
        self.calls = []
        self.answers = []

    def _answer(self):
        return self.answers.pop(0) if self.answers else _Resp(200, {"ok": True, "message": "Uploaded"})

    def post(self, url, **kw):
        if "files" in kw:            # read the file now, the handle closes after
            name, fh, _mime = kw["files"]["file"]
            kw = dict(kw, file_text=fh.read().decode())
        self.calls.append(("POST", url, kw))
        return self._answer()

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._answer()


REQ = FakeRequests()
sys.modules["requests"] = REQ

import iracing_race_logger as L  # noqa: E402

L.requests = REQ
L.time.sleep = lambda s: None          # no retry waits in the test

PASS = FAIL = 0


def check(label, got, want=None, predicate=None):
    global PASS, FAIL
    ok = predicate(got) if predicate else (got == want)
    if ok:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}  (got {got!r}"
              + ("" if predicate else f", want {want!r}") + ")")


# Point the config/state files at a temp folder.
TMP = Path(tempfile.mkdtemp())
L.CLS_CONFIG_PATH = TMP / "league_manager.json"
L.CLS_STATE_PATH = TMP / "upload_state.json"


def configure(token="cls_rl_test", live=True, auto=True):
    L.cls_save_config("https://cls.example", token, auto, live)


class FakeIR:
    def __init__(self, **vars):
        self.vars = vars

    def __getitem__(self, key):
        return self.vars.get(key)

    def set(self, **kw):
        self.vars.update(kw)


def logger(ir):
    lg = L.RaceLogger.__new__(L.RaceLogger)
    lg.ir = ir
    lg._log_fp = object()
    lg.events = []
    lg._emit = lg.events.append
    lg._log_session_meta = {
        "track": "Spa", "session_num": 2, "session_name": "RACE",
        "subsession_id": 81234567, "session_id": 299001,
        "drivers": [
            {"car_idx": 0, "car_number": "7", "name": "Johann Solowej", "team": ""},
            {"car_idx": 1, "car_number": "12", "name": "Maurice Becker", "team": ""},
        ],
    }
    lg._wear_samples = L.deque(maxlen=L.WEAR_SAMPLES_MAX)
    lg._wear_latest = None
    lg._pit_sv_seen = 0
    lg._pit_sv_compound = None
    lg._pit_sv_fuel = None
    lg._local_on_pit = False
    lg._pit_in_pit = {}
    lg._pit_entry_t = {}
    lg._pit_entry_lap = {}
    lg._pit_count = {}
    lg._last_pit_duration = {}
    return lg


def wear_vars(lf, rf, lr, rr):
    out = {}
    for prefix, val in zip(("LF", "RF", "LR", "RR"), (lf, rf, lr, rr)):
        for suffix, v in zip(("wearL", "wearM", "wearR"), val):
            out[prefix + suffix] = v
    return out


FRESH = (1.0, 1.0, 1.0)
WORN = (0.44, 0.41, 0.38)
DRIVER_INFO = {"DriverCarIdx": 0, "Drivers": [
    {"CarIdx": 0, "UserID": 345678, "UserName": "Johann Solowej"},
    {"CarIdx": 1, "UserID": 111, "UserName": "Maurice Becker"},
]}

# Live sends run in a thread in the real logger; run them inline here.
sent = []
L.cls_send_live_async = lambda payload: (sent.append(payload), L.cls_send_live(payload))


def tick(lg, t, pit0, pit1=False, **extra):
    lg.ir.set(SessionTime=t, CarIdxOnPitRoad=[pit0, pit1], CarIdxLap=[12, 11], **extra)
    lg._sample_local_pit_state()
    lg._maybe_emit_pit_events()


print("\n1. live pit stop of the local car")
configure()
REQ.calls.clear()
ir = FakeIR(DriverInfo=DRIVER_INFO, PlayerCarIdx=0, PitSvFlags=0x1F, PitSvFuel=40.0,
            **wear_vars(WORN, WORN, WORN, WORN))
lg = logger(ir)
tick(lg, 100.0, False)
tick(lg, 101.0, True)
tick(lg, 110.0, True)
ir.set(**wear_vars(FRESH, FRESH, FRESH, FRESH))
tick(lg, 125.0, True)
tick(lg, 140.0, False)
pit = [e for e in lg.events if e["type"] == "pit"]
check("pit event has tires", bool(pit and pit[0].get("tires")), True)
check("one live send", len(sent), 1)
p = sent[0] if sent else {}
check("subsession id in payload", p.get("session", {}).get("subsession_id"), 81234567)
check("cust id of the local driver", p.get("driver", {}).get("cust_id"), 345678)
check("raw samples stay in the log", "tire_wear_samples" in p.get("event", {}), False)
check("tires + fresh flag sent", p.get("event", {}).get("left_on_fresh"), True)
check("payload is JSON-serialisable", bool(json.dumps(p)), True)
post = [c for c in REQ.calls if c[0] == "POST"]
check("POST to /api/race-log/live", post[0][1] if post else None, "https://cls.example/api/race-log/live")
check("bearer token", post[0][2]["headers"]["Authorization"] if post else None, "Bearer cls_rl_test")
check("status remembered", L.cls_live_status.get("ok"), True)

print("\n2. nothing is sent when it should not be")
sent.clear()
REQ.calls.clear()
# a spectated car (idx 1) pits: no tire data -> no send
tick(lg, 200.0, False, True)
tick(lg, 230.0, False, False)
check("spectated car's stop: no send", sent, [])
# live switched off
configure(live=False)
lg2 = logger(FakeIR(DriverInfo=DRIVER_INFO, PlayerCarIdx=0, **wear_vars(WORN, WORN, WORN, WORN)))
for t, on in ((1, False), (2, True), (20, True)):
    tick(lg2, t, on)
lg2.ir.set(**wear_vars(FRESH, FRESH, FRESH, FRESH))
tick(lg2, 25, True)
tick(lg2, 40, False)
check("live off: payload built but no HTTP call", [c for c in REQ.calls if c[0] == "POST"], [])
configure(token="")
check("no token: send refused", L.cls_send_live({"event": {}})[0], False)

print("\n3. live send failures")
configure()
REQ.calls.clear()
REQ.answers = [_Resp(401, {"ok": False, "error": "Invalid or unknown logger token"})]
ok, msg = L.cls_send_live({"event": {"stop_count": 2}})
check("401 -> not sent, no retry", (ok, len(REQ.calls)), (False, 1))
REQ.calls.clear()
REQ.answers = [_Resp(503, {}), _Resp(200, {"ok": True})]
check("5xx -> retried once and sent", (L.cls_send_live({"event": {}})[0], len(REQ.calls)), (True, 2))

print("\n4. restored: finished-log upload (was gone in v1.4.0)")
REQ.calls.clear()
REQ.answers = []
log = TMP / "20261015-200000_spa_race.jsonl"
log.write_text('{"type":"session_start"}\n', encoding="utf-8")
ok, msg = L.cls_upload_log(log)
check("upload ok", ok, True)
check("POST /api/race-log with the file", (REQ.calls[0][1], "session_start" in REQ.calls[0][2]["file_text"]),
      ("https://cls.example/api/race-log", True))
check("upload state written", L.cls_load_state().get(log.name, {}).get("status"), "ok")
check("ping uses GET /api/race-log",
      (L.cls_ping()[0], REQ.calls[-1][:2]), (True, ("GET", "https://cls.example/api/race-log")))
closed = []
L.cls_upload_async = closed.append
configure(auto=True)
L.cls_autoupload(log)
check("auto-upload when a log closes", closed, [log])
configure(auto=False)
closed.clear()
L.cls_autoupload(log)
check("auto off: nothing", closed, [])

print("\n5. restored: the rest of what v1.4.0 lost")
src = Path(L.__file__).read_text(encoding="utf-8")
check("frozen .exe keeps logs next to the program", 'getattr(sys, "frozen", False)' in src, True)
check("log close hands the file to the upload", "cls_autoupload(closed_path)" in src, True)
for fn in ("_maybe_emit_weather", "_refresh_driver_names", "_maybe_emit_sdk_incidents",
           "_maybe_emit_incident_points", "_sample_local_pit_state"):
    check(f"tick calls {fn}", f"self.{fn}()" in src, True)
check("/league page is back", '@app.route("/league")' in src and "LEAGUE_HTML" in src, True)

lg3 = logger(FakeIR(SessionTime=500.0, TrackTempCrew=31.5, AirTemp=22.0, TrackWetness=1, Skies=1))
lg3._last_weather_emit_t = -1e9
lg3._maybe_emit_weather()
check("weather sample", [e["type"] for e in lg3.events], ["weather"])

lg4 = logger(FakeIR(SessionTime=600.0, DriverInfo={"Drivers": [
    {"CarIdx": 0, "CarNumber": "7", "UserName": "Anna Wechsel"},
    {"CarIdx": 1, "CarNumber": "12", "UserName": "Maurice Becker"}]}))
lg4._last_driver_refresh_t = -1e9
lg4._refresh_driver_names()
ch = [e for e in lg4.events if e["type"] == "driver_change"]
check("team driver swap recorded", (len(ch), ch[0]["driver"], ch[0]["previous"]) if ch else None,
      (1, "Anna Wechsel", "Johann Solowej"))

drivers_inc = lambda n: {"Drivers": [{"CarIdx": 0, "CurDriverIncidentCount": n}]}
lg5 = logger(FakeIR(SessionTime=10.0, DriverInfo=drivers_inc(0)))
lg5._dashboard_incidents_live = False
lg5._incident_source_written = None
lg5._last_sdk_inc_t = -1e9
lg5._sdk_inc_prev = {}
lg5._driver_incident_count = {}
lg5._incidents_logged = 0
lg5._maybe_emit_sdk_incidents()
lg5.ir.set(SessionTime=12.0, DriverInfo=drivers_inc(4))
lg5._maybe_emit_sdk_incidents()
inc = [e for e in lg5.events if e["type"] == "incident"]
check("incident without dashboard (+4 -> collision)", [e["incident_type"] for e in inc], ["collision"])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
