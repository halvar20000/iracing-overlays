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

# ---- real TCP --------------------------------------------------------------
tmp = tempfile.mkdtemp()
s0 = socket.socket(); s0.bind(("127.0.0.1", 0)); port = s0.getsockname()[1]; s0.close()
f4 = rc.Feed(); recv = rc.AtvoReceiver(f4, port, log_dir=tmp); recv.start()
for _ in range(50):
    if recv.listening: break
    time.sleep(0.05)
check("N1 listening", recv.listening, recv.error)
c = socket.create_connection(("127.0.0.1", port))
c.sendall(b'{"message":"Incident T1 #12 / #44 noted"}\n')
c.sendall(b'Pit lane clo')                     # split across packets,
time.sleep(0.1); c.sendall(b'sed\x00')        # NUL-terminated
c.sendall(b'no delimiter at all')             # flushed after a pause
time.sleep(1.0)
c.sendall("Blaue Flagge für #9\n".encode("utf-16-le"))   # .NET style
time.sleep(0.8)
st = recv.status()
check("N2 client seen", len(st["clients"]) == 1, str(st["clients"]))
c.close(); time.sleep(0.6)
texts = [m["text"] for m in f4.list()]
check("N3 messages in order", texts == ["Incident T1 #12 / #44 noted", "Pit lane closed", "no delimiter at all", "Blaue Flagge für #9"], str(texts))
logf = [x for x in os.listdir(tmp) if x.startswith("atvo_capture_")]
lines = [json.loads(l) for l in open(os.path.join(tmp, logf[0]), encoding="utf-8")] if logf else []
check("N4 capture log: connect, data, disconnect",
      lines and lines[0]["event"] == "connect" and lines[-1]["event"] == "disconnect"
      and any(l["event"] == "data" and "Incident" in l["text"] for l in lines), f"{len(lines)} lines")
check("N5 raw kept for the panel", len(recv.status()["raw"]) == 4)
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
