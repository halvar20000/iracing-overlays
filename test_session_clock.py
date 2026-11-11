"""Race clock 5011/clock: flag bar logic + routes.  python test_session_clock.py"""
import sys, types
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
irsdk = types.ModuleType("irsdk"); irsdk.IRSDK = object; sys.modules.setdefault("irsdk", irsdk)
import iracing_session_info as m
p = m.poller.__class__.__new__(m.poller.__class__)
p._caution_key = None; p._caution_seen = False
ok = fail = 0
def chk(name, cond, got=None):
    global ok, fail
    if cond: ok += 1; print("  ok  ", name)
    else: fail += 1; print("  FAIL", name, got)
K = (1, 3)
seq = [  # (flags, state, expected)
    (0x0000, 2, "pre"), (0x0200, 3, "yellow"),         # pace laps
    (0x0204, 4, "green"),                              # bit left over at the green (Spa)
    (0x0004, 4, "green"), (0x0004 | 0x0008, 4, "green"),  # local yellow = green
    (0x4004, 4, "yellow"), (0x4204, 4, "yellow"),      # caution, one-to-green lap
    (0x0204, 4, "yellow"),                             # otg lap after the caution
    (0x0004, 4, "green"), (0x0204, 4, "green"),        # restart; stray otg = green
    (0x0006, 4, "white"), (0x0005, 5, "checkered"), (0x0014, 4, "red"),
]
for i, (f, st, exp) in enumerate(seq):
    got = p._flag({"SessionFlags": f, "SessionState": st}, K)
    chk(f"C{i+1} flags {f:#06x} state {st} -> {exp}", got == exp, got)
p._flag({"SessionFlags": 0x4004, "SessionState": 4}, K)
chk("C14 new session forgets the caution",
    p._flag({"SessionFlags": 0x0204, "SessionState": 4}, (1, 4)) == "green")
c = m.app.test_client()
m.event.data = {"league": "CAS GT3 WCT", "slug": "cas-gt3-wct"}
r = c.get("/clock"); chk("C15 /clock served", r.status_code == 200 and b"flag bar" not in r.data and b"id=\"bar\"" in r.data)
j = c.get("/status").get_json(); chk("C16 /status carries the league", j["league"]["slug"] == "cas-gt3-wct", j)
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
