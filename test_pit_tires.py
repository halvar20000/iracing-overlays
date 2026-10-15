"""
Offline tests for the live pit-stop tire feed (cls_pit_tires.py) and the
/pittires pages on the standings server (5005).

Run with:  python test_pit_tires.py
The Flask part runs only when flask is installed (irsdk is stubbed).
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cls_pit_tires as P  # noqa: E402

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


N = [100.0, 100.0, 100.0]


def stop(id_, member="345678", count=1, changed=("lf", "rf", "lr", "rr"), fresh=True, num="7", sub=81234567):
    tires = {c: {"wearIn": [60.0, 58.0, 55.0], "wearOut": N if c in changed else [60.0, 58.0, 55.0],
                 "fresh": fresh if c in changed else False, "changed": c in changed}
             for c in ("lf", "rf", "lr", "rr")}
    return {"id": id_, "driver": "Johann Solowej", "iracingMemberId": member, "custId": int(member) if member else None,
            "carNumber": num, "subsessionId": sub, "sessionNum": 2, "stopCount": count,
            "entryLap": 18, "duration": 51.3, "tires": tires, "tiresChanged": list(changed),
            "leftOnFresh": fresh if changed else None}


print("\n1. verdict")
check("four new", P.verdict(stop(1)), "NEW 4 TIRES")
check("fronts, used set", P.verdict(stop(1, changed=("lf", "rf"), fresh=False)), "USED FRONTS")
check("rights", P.verdict(stop(1, changed=("rf", "rr"))), "NEW RIGHTS")
check("no change", P.verdict(stop(1, changed=())), "NO TIRE CHANGE")
one = stop(1, changed=())
for t in one["tires"].values():
    t["changed"] = None
check("one reading, tires ordered -> no label", P.verdict(one), "")
one["serviceRequested"] = ["fuel"]
check("one reading, fuel only ordered -> no change", P.verdict(one), "NO TIRE CHANGE")

print("\n2. feed: polling only on demand, merge, race change")
clock = [1000.0]
answers = []
urls = []


def fetch(url):
    urls.append(url)
    return {"stops": answers.pop(0) if answers else []}


f = P.PitTireFeed("https://cls.example", fetch=fetch, clock=lambda: clock[0])
check("idle without a page", f._active(), False)
f.want(81234567)
check("active once a page asks", f._active(), True)
answers.append([stop(5), stop(6, member="111", num="12")])
f.poll_once()
check("asks for this subsession", "subsession=81234567" in urls[-1] and "after=0" in urls[-1], True)
check("two stops", len(f.stops()), 2)
answers.append([stop(9, count=1)])           # a re-sent stop 1 of the same driver
f.poll_once()
check("next poll asks after the newest id", "after=6" in urls[-1], True)
check("re-sent stop replaces, not duplicates", sorted(s["id"] for s in f.stops()), [6, 9])
answers.append([stop(10, count=2)])
f.poll_once()
check("second stop of the same driver kept", len(f.stops()), 3)
f.want(99999)
check("a new race starts empty", f.stops(), [])
f._fetch = lambda url: (_ for _ in ()).throw(OSError("offline"))
check("fetch error reported", (f.poll_once(), "offline" in f.status()["last_error"]), (False, True))
f.want(None)
f._fetch = fetch
f.poll_once()
check("no subsession id yet -> last 3 hours", "hours=3" in urls[-1] and "subsession" not in urls[-1], True)
clock[0] += P.DEMAND_S + 1
check("stops polling a minute after the page went away", f._active(), False)

print("\n3. join with the tower rows")
rows = [{"user_id": 345678, "car_number": "7", "class_position": 4, "position": 9, "class_name": "GT3",
         "class_color": "#ffda59", "proam": "PRO", "country": "de", "team_name": "", "brand": "porsche",
         "brand_logo": True, "car_idx": 3, "name": "Johann Solowej"},
        {"user_id": 222, "car_number": "12", "position": 1, "name": "Maurice Becker", "brand": "bmw", "brand_logo": False}]
j = P.join_with_field([stop(1), stop(2, member=None, num="12"), stop(3, member="999", num="99")], rows)
check("matched by customer id", (j[0]["in_field"], j[0]["position"], j[0]["proam"], j[0]["brand"]),
      (True, 4, "PRO", "porsche"))
check("fallback by car number, no logo -> no brand", (j[1]["in_field"], j[1]["name_iracing"], j[1]["brand"]),
      (True, "Maurice Becker", None))
check("unknown driver kept, marked", j[2]["in_field"], False)
check("verdict attached", j[0]["verdict"], "NEW 4 TIRES")

print("\n4. /pittires routes on the standings server")
try:
    import flask  # noqa: F401
except ImportError:
    print("  (flask not installed — skipped)")
else:
    sys.modules.setdefault("irsdk", types.SimpleNamespace(IRSDK=lambda: None))
    import iracing_standings as S
    S.poller.get = lambda: {"connected": True, "session_type": "Race", "team_event": False,
                            "subsession_id": 81234567, "standings": rows}
    S.pit_tires._fetch = lambda url: {"stops": [stop(1)]}
    c = S.app.test_client()
    d = c.get("/pittires/data").get_json()
    check("data route registers the race", S.pit_tires.status()["subsession"], 81234567)
    S.pit_tires.poll_once()
    d = c.get("/pittires/data").get_json()
    check("data route returns joined stops", (len(d["stops"]), d["stops"][0]["position"]), (1, 4))
    page = c.get("/pittires").get_data(as_text=True)
    check("card page served", "PIT STOP" in page and "/pittires/data" in page, True)
    check("list page served", c.get("/pittires/list").status_code, 200)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
