"""Tests for replay_log_events.py (REGIE incidents from the live race log in a replay)."""
import json
import sys
import tempfile
from pathlib import Path

import replay_log_events as rl

FAILS = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {info}"))
    if not ok:
        FAILS.append(name)


def write(p, events):
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


START = {"type": "session_start", "subsession_id": 90213832, "session_num": 2, "track": "Oschersleben"}
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    live = d / "20261118-200716_osch_race.jsonl"
    write(live, [START,
                 {"type": "incident", "t_session": 238.9, "car_idx": 2, "car_number": "67", "driver": "A", "incident_type": "off_track"},
                 {"type": "incident", "t_session": 257.9, "car_idx": 0, "car_number": "15", "driver": "B", "incident_type": "lost_control"},
                 {"type": "incident", "t_session": 1106.0, "car_idx": 5, "car_number": "75", "driver": "C", "incident_type": "collision"},
                 {"type": "incident", "t_session": 1106.0, "car_idx": 6, "car_number": "666", "driver": "D", "incident_type": "collision"},
                 {"type": "incident", "t_session": 1107.5, "car_idx": 5, "car_number": "75", "driver": "C", "incident_type": "collision"},
                 {"type": "lap", "t_session": 300}])
    # the log the logger writes while the replay plays: same session, later, must not be used
    frag = d / "20261119-115514_osch_race.jsonl"
    write(frag, [START] + [{"type": "incident", "t_session": 252.9, "car_idx": i, "car_number": str(i),
                            "driver": "X", "incident_type": "lost_control"} for i in range(7)])
    write(d / "20261118-203824_osch_race.jsonl", [dict(START, session_num=4)])
    check("live log chosen (earliest of the session), not the replay's", rl.find_live_log(d, 90213832, 2) == live)
    check("other session number / unknown session = other / none",
          rl.find_live_log(d, 90213832, 4).name.startswith("20261118-203824") and rl.find_live_log(d, 1, 2) is None)
    ev = rl.load_incidents(live)
    check("only contacts / spins, burst merged", [(e["t_session"], e["car_idx"], e["type"]) for e in ev]
          == [(257.9, 0, "lost_control"), (1106.0, 5, "collision"), (1106.0, 6, "collision")], ev)
    r = rl.ReplayLogEvents(d)
    check("first look emits nothing", r.feed(90213832, 2, 255.0) == [] and r.active)
    got = r.feed(90213832, 2, 258.0)
    check("spin emitted when the replay passes it", [e["car_idx"] for e in got] == [0]
          and got[0]["source"] == "racelog" and got[0]["session_num"] == 2, got)
    check("not twice", r.feed(90213832, 2, 259.0) == [])
    check("seek forward over the contact: nothing", r.feed(90213832, 2, 1200.0) == [])
    r.feed(90213832, 2, 1100.0)                      # back (REGIE's own replay seek)
    got = r.feed(90213832, 2, 1104.0) + r.feed(90213832, 2, 1107.0)
    check("contact of both cars when played again", sorted(e["car_idx"] for e in got) == [5, 6], got)
    r2 = rl.ReplayLogEvents(d / "nichts")
    check("no logs folder: inactive, nothing", r2.feed(90213832, 2, 1.0) == [] and r2.feed(90213832, 2, 2.0) == []
          and not r2.active)

print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
