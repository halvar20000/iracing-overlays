"""Offline checks for iracing_rc_banner.py (Race Control banner):
flag edges from iRacing, the ATVO TCP receiver with a real socket in
several possible wire formats, and the Flask endpoints.
Run: python test_rc_banner.py   (needs pyirsdk or an irsdk stub + flask)"""
import json, os, socket, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import iracing_rc_banner as rc

failures = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)

CARS = {0: {"num": "12", "label": "Proton", "driver": "", "class": "GT3", "color": "#ffb020", "text": "#0a0a0f"},
        1: {"num": "44", "label": "Rowe", "driver": "", "class": "GT3", "color": "#ffb020", "text": "#0a0a0f"}}

def heads(feed): return [m["headline"] for m in feed.list()]

# ---- flags -----------------------------------------------------------------
f = rc.Feed(); w = rc.FlagWatcher(f)
w.tick(True, True, rc.F_CAUTION, [rc.F_BLACK, 0], [0, 0], CARS, 0)        # attach mid-caution
check("F1 attach: nothing", heads(f) == [], str(heads(f)))
w.tick(True, True, rc.F_CAUTION | rc.F_ONE_TO_GREEN, [rc.F_BLACK, 0], [0, 0], CARS, 1)
check("F2 one to green after caution", heads(f) == ["SAFETY CAR IN THIS LAP"], str(heads(f)))
w.tick(True, True, rc.F_ONE_TO_GREEN, [rc.F_BLACK, 0], [0, 0], CARS, 2)
check("F3 still one to green: no green yet", heads(f)[-1] == "SAFETY CAR IN THIS LAP", str(heads(f)))
w.tick(True, True, rc.F_GREEN, [rc.F_BLACK, 0], [0, 0], CARS, 3)
check("F4 green after the caution", heads(f)[-1] == "GREEN FLAG", str(heads(f)))
w.tick(True, True, rc.F_GREEN, [rc.F_BLACK, 0], [0, 0], CARS, 4)
check("F5 green once", heads(f).count("GREEN FLAG") == 1)
w.tick(True, True, rc.F_CAUTION_WAVING, [0, 0], [0, 0], CARS, 5)
check("F6 new caution", heads(f)[-1] == "FULL COURSE YELLOW")
n = len(f.list())
w.tick(True, True, rc.F_CAUTION_WAVING, [0, rc.F_BLACK], [0, 0], CARS, 6)
m = f.list()[-1]
check("F7 black flag with car", len(f.list()) == n + 1 and m["kind"] == "penalty" and m["car"]["num"] == "44", str(m))
w.tick(True, True, rc.F_CAUTION_WAVING, [0, rc.F_BLACK | rc.F_DQ], [0, 0], CARS, 7)
check("F8 DQ once (not also black)", heads(f)[-1] == "DISQUALIFIED" and heads(f).count("BLACK FLAG · PENALTY") == 1, str(heads(f)))
w.tick(True, True, rc.F_CAUTION_WAVING, [0, 0], [rc.P_EOL, 0], CARS, 8)
check("F9 end of line", heads(f)[-1] == "END OF LINE")
w.tick(True, True, rc.F_CAUTION_WAVING, [0, 0], [0, 0], CARS, 9)
w.tick(True, True, rc.F_CAUTION_WAVING, [0, 0], [rc.P_EOL, 0], CARS, 10)
check("F10 same flag within 30 s: once", heads(f).count("END OF LINE") == 1)
f2 = rc.Feed(); w2 = rc.FlagWatcher(f2)
w2.tick(False, False, 0, [0, 0], [0, 0], CARS, 0)
w2.tick(False, False, rc.F_CAUTION | rc.F_ONE_TO_GREEN, [0, rc.F_REPAIR], [rc.P_EOL, 0], CARS, 1)
check("F11 quali: no caution / pace, meatball yes", heads(f2) == ["MEATBALL FLAG · REPAIR"], str(heads(f2)))
f3 = rc.Feed(); w3 = rc.FlagWatcher(f3)
w3.tick(True, False, rc.F_ONE_TO_GREEN, [0, 0], [0, 0], CARS, 0)
w3.tick(True, True, rc.F_GREEN, [0, 0], [0, 0], CARS, 1)
check("F12 race start (pace lap one-to-green): no caution lines", heads(f3) == [], str(heads(f3)))

f7 = rc.Feed(); w7 = rc.FlagWatcher(f7)
w7.tick(True, True, rc.F_GREEN, [0, 0], [0, 0], CARS, 0)
w7.tick(True, True, rc.F_GREEN, [rc.F_FURLED, rc.F_BLACK], [0, 0], CARS, 1)
check("F13 iRacing warnings off by default, black flag still shown", heads(f7) == ["BLACK FLAG · PENALTY"], str(heads(f7)))
f8 = rc.Feed(); w8 = rc.FlagWatcher(f8, warnings=True)
w8.tick(True, True, rc.F_GREEN, [0, 0], [0, 0], CARS, 0)
w8.tick(True, True, rc.F_GREEN, [rc.F_FURLED, 0], [0, 0], CARS, 1)
check("F14 iracing_warnings on: shown", heads(f8) == ["WARNING"], str(heads(f8)))

# ---- text extraction --------------------------------------------------------
cases = [
    (b'{"type":"RaceControl","headline":"RACE CONTROL","message":"Car #12 under investigation"}',
     ("RACE CONTROL", "Car #12 under investigation")),
    (b'{"data":{"Text":"Track limits T4 #44"}}', ("", "Track limits T4 #44")),
    (b'<msg><title>Penalty</title><body>#12 Drive Through</body></msg>', ("", "Penalty #12 Drive Through")),
    (b'Full course yellow, pit lane closed', ("", "Full course yellow, pit lane closed")),
    ("Überholen unter Gelb #7".encode("utf-16-le"), ("", "Überholen unter Gelb #7")),
    (b"\x00\x00\x00\x1b\x01\x05Car #3 warning: blocking\x02\x00", None),
]
for i, (raw, want) in enumerate(cases, 1):
    r = rc.extract_text(raw)
    if want is None:
        ok = r is not None and "Car #3 warning: blocking" in r[1]
    else:
        ok = r is not None and (r[0], r[1]) == want
    check(f"T{i} extract", ok, str(r))
check("T7 binary garbage -> nothing readable", not rc.readable((rc.extract_text(b"\x01\x02\x03\xff\xfe") or ("", "", ""))[1]))
check("T8 password-like token not shown", not rc.readable("x1"))

# ---- SignalR 2 server (iRaceControl's ATVO link), real HTTP ----------------
import urllib.request, urllib.parse, urllib.error
tmp = tempfile.mkdtemp()
s0 = socket.socket(); s0.bind(("127.0.0.1", 0)); port = s0.getsockname()[1]; s0.close()
f4 = rc.Feed(); recv = rc.AtvoReceiver(f4, port, log_dir=tmp, password="geheim"); recv.start()
for _ in range(50):
    if recv.listening: break
    time.sleep(0.05)
check("N1 listening", recv.listening, recv.error)
BASE = f"http://127.0.0.1:{port}/signalr/signalr"
CD = urllib.parse.quote('[{"Name":"RaceControlHub"}]')
def get(action, extra="", pw="geheim"):
    return urllib.request.urlopen(f"{BASE}/{action}?clientProtocol=2.1&connectionData={CD}&password={pw}{extra}", timeout=5)
try:
    get("negotiate", pw="falsch"); code = 200
except urllib.error.HTTPError as e:
    code = e.code
check("N2 wrong password refused", code == 403, str(code))
neg = json.loads(get("negotiate").read())
check("N3 negotiate", neg["ProtocolVersion"] == "2.1" and neg["TryWebSockets"] is False
      and neg["Url"] == "/signalr/signalr" and neg["ConnectionToken"], str(neg))
tok = "&connectionToken=" + urllib.parse.quote(neg["ConnectionToken"])
sse = get("connect", "&transport=serverSentEvents" + tok)
first = [sse.readline().decode().strip() for _ in range(4)]
check("N4 SSE: initialized + init message (S=1)",
      first[0] == "data: initialized" and '"S":1' in first[2], str(first))
check("N5 start", json.loads(get("start", "&transport=serverSentEvents" + tok).read()) == {"Response": "started"})
def send(obj):
    body = urllib.parse.urlencode({"data": json.dumps(obj)}).encode()
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        f"{BASE}/send?transport=serverSentEvents{tok}&password=geheim", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=5).read())
r = send({"H": "racecontrolhub", "M": "SendMessage", "A": ["PENALTY", "#12 Drive Through - causing a collision", 10], "I": 3})
check("N6 hub call answered", r == {"I": "3"}, str(r))
r = send({"H": "racecontrolhub", "M": "Incident", "A": [{"Headline": "RACE CONTROL", "Message": "T1 #12 / #44 under investigation"}], "I": 4})
ms = f4.list()
check("N7 messages on the feed", [(m["kind"], m["headline"], m["text"]) for m in ms] ==
      [("penalty", "PENALTY", "#12 Drive Through - causing a collision"),
       ("message", "RACE CONTROL", "T1 #12 / #44 under investigation")], str(ms))
st = recv.status()
check("N8 client listed", len(st["clients"]) == 1 and st["clients"][0]["transport"] == "serverSentEvents", str(st["clients"]))
check("N9 HTTP lines NOT on air (only the two hub texts)", len(f4.list()) == 2)
neg2 = json.loads(get("negotiate").read())
lp = json.loads(get("connect", "&transport=longPolling&connectionToken=" + neg2["ConnectionToken"]).read())
check("N10 longPolling connect", lp.get("S") == 1, str(lp))
urllib.request.urlopen(urllib.request.Request(f"{BASE}/abort?transport=serverSentEvents{tok}&password=geheim", data=b""), timeout=5)
check("N11 abort ends the client", all(c["transport"] != "serverSentEvents" for c in recv.status()["clients"]))
logf = [x for x in os.listdir(tmp) if x.startswith("atvo_capture_")]
lines = [json.loads(l) for l in open(os.path.join(tmp, logf[0]), encoding="utf-8")] if logf else []
check("N12 capture log has every request incl. the hub body",
      any("Drive Through" in l.get("data", "") for l in lines) and any(l.get("path", "").endswith("negotiate") for l in lines), f"{len(lines)} lines")
check("N13 hub_text plain string", rc.hub_text("Say", ["Pit lane closed"]) == ("", "Pit lane closed"))
# what the stream PC really got from iRaceControl (lower-case %7b, no form parse)
raw_body = "data=%7b%22I%22%3a%220%22%2c%22H%22%3a%22RaceControlHub%22%2c%22M%22%3a%22GetVersion%22%2c%22A%22%3a%5b%5d%7d"
check("N14 form_data decodes the real body", json.loads(rc.form_data(raw_body))["M"] == "GetVersion")
n = len(f4.list())
r = recv.handle_send("x", rc.form_data(raw_body))
check("N15 GetVersion answered with a result, nothing on air", r.get("R") and r["I"] == "0" and len(f4.list()) == n, str(r))
body = "data=" + urllib.parse.quote(json.dumps({"H": "RaceControlHub", "M": "GetVersion", "A": [], "I": 0}))
r = json.loads(urllib.request.urlopen(urllib.request.Request(f"{BASE}/send?transport=serverSentEvents{tok}&password=geheim",
        data=body.encode(), headers={"Content-Type": "text/plain"}), timeout=5).read())
check("N16 send without a form content type", r.get("R") and r["I"] == "0", str(r))
r = recv.handle_send("x", json.dumps({"H": "RaceControlHub", "M": "GetAllIncidents", "A": [], "I": "1"}))
check("N17 GetAllIncidents -> empty list, not null (null crashed iRaceControl)", r.get("R") == [] and "R" in r, str(r))
# ---- the real iRaceControl format (2026-10-26, hosted session) -------------
f5 = rc.Feed(); r5 = rc.AtvoReceiver(f5, 0, log_dir=tmp); r5.gather_s = 0
r5.car_lookup = lambda e: {"num": "12", "label": "Proton"} if e == "12" else None
def sm(typ, head, text, entry=""):
    return json.dumps({"H": "RaceControlHub", "M": "SendMessage", "I": "5", "A": [{"EntryId": entry, "IdType": 0,
        "SessionName": None, "Type": typ, "Header": head, "Text": text, "DisplayTime": "00:00:10", "Source": "iRaceControl"}]})
for typ, head in [(1, "RACE CONTROL"), (2, "WARNING"), (3, "PENALTY"), (4, "RACE CONTROL"), (5, "RACE CONTROL"), (6, "RACE CONTROL")]:
    r = r5.handle_send("x", sm(typ, head, "PACE CAR DEPLOYED BY RACE CONTROL"))
check("N18 one message routed to all 6 targets = shown once", len(f5.list()) == 1 and r == {"I": "5"}, str(f5.list()))
m = f5.list()[0]
check("N19 type / headline / text / DisplayTime", (m["kind"], m["headline"], m["text"], m["secs"]) ==
      ("message", "RACE CONTROL", "PACE CAR DEPLOYED BY RACE CONTROL", 10.0), str(m))
r5.handle_send("x", sm(3, "PENALTY", "Drive Through - avoidable contact", entry="12"))
r5.handle_send("x", sm(2, "WARNING", "Track limits T4"))
ks = [(x["kind"], (x["car"] or {}).get("num")) for x in f5.list()[1:]]
check("N20 penalty with car (EntryId), warning", ks == [("penalty", "12"), ("warning", None)], str(ks))
# all six targets, gathered (the real test: penalty arrived first as WARNING)
f6 = rc.Feed(); r6 = rc.AtvoReceiver(f6, 0, log_dir=tmp)
r6.car_lookup = lambda e: {"num": e, "label": "Nim Cross"} if e in ("07", "14") else None
for typ, head in [(2, "WARNING"), (1, "RACE CONTROL"), (3, "PENALTY"), (4, "RACE CONTROL"), (5, "RACE CONTROL"), (6, "RACE CONTROL")]:
    r6.handle_send("x", sm(typ, head, "DRIVE TROUGH PENALTY FOR #07 (CRO) - bla"))
for typ, head in [(1, "RACE CONTROL"), (2, "WARNING"), (3, "PENALTY")]:
    r6.handle_send("x", sm(typ, head, "Safety car in this lap"))
time.sleep(0.8)
got = [(x["kind"], x["headline"], (x["car"] or {}).get("num")) for x in f6.list()]
check("N22 routed to every target: kind from the text, car from '#07'",
      got == [("penalty", "PENALTY", "07"), ("message", "RACE CONTROL", None)], str(got))
r6.handle_send("x", sm(3, "PENALTY", "#14 (KAE) HAVE BEEN DISQUALIFIED: Track Limits")); time.sleep(0.7)
f6.add("iracing", "dq", "DISQUALIFIED", "", {"num": "14", "label": "Dave Kaemmer"})
f6.add("iracing", "penalty", "BLACK FLAG · PENALTY", "", {"num": "33", "label": "Other"})
got = [x["headline"] for x in f6.list()[2:]]
check("N23 iRacing flag for a car iRaceControl just named is not shown twice",
      got == ["PENALTY", "BLACK FLAG · PENALTY"], str(got))
check("N21 parse_span", rc.parse_span("00:01:05") == 65 and rc.parse_span("00:00:00") is None and rc.parse_span("x") is None)
recv.stop()

# ---- Flask -----------------------------------------------------------------
cl = rc.app.test_client()
check("W1 banner page", b"RACE" in cl.get("/").data)
check("W2 panel page", "ATVO" in cl.get("/panel").get_data(as_text=True))
r = cl.post("/rc/say", json={"text": "Track limits warning #7"})
st = cl.get("/rc/state").get_json()
check("W3 manual message", r.status_code == 200 and st["messages"][-1]["text"] == "Track limits warning #7")
check("W4 empty refused", cl.post("/rc/say", json={"text": " "}).status_code == 400)

print()
print("ALL PASS" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
