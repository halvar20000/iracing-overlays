"""Steward penalties on the tower: 5019 /rc/penalties -> 5005 /standings ->
tags after the name. Real HTTP between the two Flask apps.
Run: python test_tower_penalties.py   (needs flask + pyirsdk or an irsdk stub)"""
import os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from werkzeug.serving import make_server
import iracing_rc_banner as rc
import iracing_standings as st

failures = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)

srv = make_server("127.0.0.1", 0, rc.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}/rc/penalties"

pen = st.RcPenalties(url)
pen.poll_once()
check("T1 nothing open -> empty", pen.get() == {})
bc = rc.app.test_client()
bc.post("/rc/steward", json={"kind": "penalty", "headline": "DRIVE THROUGH PENALTY", "car": "14"})
bc.post("/rc/steward", json={"kind": "penalty", "headline": "5 SECOND TIME PENALTY", "car": "14"})
bc.post("/rc/steward", json={"kind": "investigation", "headline": "UNDER INVESTIGATION", "car": "7"})
pen.poll_once()
g = pen.get()
check("T2 tower reads 5019", [t["label"] for t in g["14"]["tags"]] == ["DT", "+5s"]
      and g["7"]["tags"][0]["type"] == "inv", str(g))

off = st.RcPenalties("http://127.0.0.1:1/rc/penalties")
off.poll_once()
check("T3 5019 not running -> no tags, no error", off.get() == {})

st.rc_penalties = pen
sc = st.app.test_client()
j = sc.get("/standings").get_json()
check("T4 /standings carries the penalties", j["penalties"]["14"]["tags"][0]["label"] == "DT")
check("T5 ?pen=0 leaves them out", "penalties" not in sc.get("/standings?pen=0").get_json())
html = sc.get("/").get_data(as_text=True)
check("T6 tower page renders penalty tags", "penTags(r)" in html and ".tag.pen.dsq" in html)
srv.shutdown()
print()
print("ALL PASS" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
