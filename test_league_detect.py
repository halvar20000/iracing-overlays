"""
Offline tests for cls_league_detect.LeagueDetector.

Run with:  python test_league_detect.py          (offline, synthetic rosters)
           python test_league_detect.py --live    (also hits the real CLS API)

The cases that matter are the ones that would put a wrong number on
Andreas's stream: a WCT field must not pick IEC just because 14 WCT
drivers are also IEC-registered, a PCCD field must pick the 5th season
and not the empty 6th, and a field nobody recognises must return None
so the overlay keeps its configured league instead of guessing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cls_league_detect as D

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


def detector(seasons):
    """A detector with a hand-built roster map and no network."""
    d = D.LeagueDetector.__new__(D.LeagueDetector)
    import threading
    d._api_base = "http://test.invalid"
    d._lock = threading.Lock()
    d._seasons = seasons
    d._last_ok = 0.0
    d._error = None
    d._thread = None
    return d


# Shaped like the real thing: WCT 37 drivers, IEC 100 with 14 shared.
WCT_IDS = set(range(1000, 1037))
IEC_IDS = set(range(1023, 1037)) | set(range(2000, 2086))   # 14 shared
PCCD5 = set(range(3000, 3027))
PCCD6 = {3000}
SFL9 = set()

SEASONS = [
    {"slug": "cas-gt3-wct", "season_id": "wct14", "season_name": "GT3 WCT 14th",
     "completed_rounds": 3, "ids": WCT_IDS},
    {"slug": "cas-iec", "season_id": "iec4", "season_name": "Season 4",
     "completed_rounds": 2, "ids": IEC_IDS},
    {"slug": "cas-pccd", "season_id": "pccd5", "season_name": "5th season",
     "completed_rounds": 7, "ids": PCCD5},
    {"slug": "cas-pccd", "season_id": "pccd6", "season_name": "6th season",
     "completed_rounds": 0, "ids": PCCD6},
    {"slug": "cas-sfl-cup", "season_id": "sfl9", "season_name": "9th Season",
     "completed_rounds": 0, "ids": SFL9},
]
d = detector(SEASONS)

print("\n[the series being raced wins, despite shared rosters]")
# 20-car WCT field, 8 of them also IEC-registered.
wct_field = sorted(WCT_IDS)[:20]
r = d.detect(wct_field)
check("WCT field -> cas-gt3-wct", r and r["league_slug"], "cas-gt3-wct")
check("  and its season", r and r["season_id"], "wct14")
check("  match count", r and r["matched"], 20)
check("  runner-up is smaller", r and r["runner_up"] < r["matched"], True)
check("  not flagged ambiguous", r and r["ambiguous"], False)

iec_field = sorted(set(range(2000, 2030)))
r = d.detect(iec_field)
check("IEC field -> cas-iec", r and r["league_slug"], "cas-iec")

print("\n[two seasons of one league open at once]")
# The real trap: PCCD 6th is the one the API returns without a season
# param, and it is empty. The raced 5th season must win.
r = d.detect(sorted(PCCD5)[:18])
check("PCCD field -> cas-pccd", r and r["league_slug"], "cas-pccd")
check("  picks the 5th season, not the empty 6th", r and r["season_id"], "pccd5")
# One driver registered in BOTH PCCD seasons, alone, is not a field.
check("single shared driver -> no detection", d.detect([3000]), None)

print("\n[refuses to guess]")
check("empty field", d.detect([]), None)
check("no ids at all", d.detect([None, "", "abc"]), None)
check("pace car / empty slots only", d.detect([0, -1, 0]), None)
check("two known drivers (< MIN_MATCHES)", d.detect(sorted(WCT_IDS)[:2]), None)
# 3 known in a field of 20 clears MIN_MATCHES but not the 50 % share.
mixed = sorted(WCT_IDS)[:3] + list(range(90000, 90017))
check("3 of 20 known (< MIN_MATCH_SHARE)", d.detect(mixed), None)
# Exactly at the thresholds it must fire.
on_edge = sorted(WCT_IDS)[:5] + list(range(91000, 91005))
r = d.detect(on_edge)
check("5 of 10 known (== share) fires", r and r["league_slug"], "cas-gt3-wct")
check("unknown field entirely", d.detect(list(range(80000, 80020))), None)
check("a season with no linked ids is undetectable",
      any(s["season_id"] == "sfl9" for s in SEASONS) and d.detect([]) is None, True)

print("\n[an empty roster map never crashes]")
check("no seasons loaded", detector([]).detect(wct_field), None)

print("\n[ambiguity is reported, not hidden]")
# Two seasons sharing the same drivers exactly.
twin = detector([
    {"slug": "a", "season_id": "a1", "completed_rounds": 1, "ids": {1, 2, 3, 4}},
    {"slug": "b", "season_id": "b1", "completed_rounds": 1, "ids": {1, 2, 3, 4}},
])
r = twin.detect([1, 2, 3, 4])
check("still answers", bool(r), True)
check("  flagged ambiguous", r and r["ambiguous"], True)

print("\n[cache round-trip]")
import json, tempfile
tmp = Path(tempfile.mkdtemp()) / "cache.json"
old = D.CACHE_PATH
try:
    D.CACHE_PATH = tmp
    detector(SEASONS)._save_cache(SEASONS)
    loaded = D.LeagueDetector.__new__(D.LeagueDetector)
    loaded._seasons = []
    loaded._load_cache()
    check("seasons restored", len(loaded._seasons), len(SEASONS))
    check("ids come back as a set",
          isinstance(loaded._seasons[0]["ids"], set), True)
    import threading
    loaded._lock = threading.Lock()
    r = loaded.detect(wct_field)
    check("detects from the cache alone", r and r["league_slug"], "cas-gt3-wct")
finally:
    D.CACHE_PATH = old

if "--live" in sys.argv:
    print("\n[live CLS API]")
    live = D.LeagueDetector()
    live._fetch()
    st = live.status()
    check("seasons fetched", len(st["seasons"]) >= 6, True)
    wct = live.rows_for("cas-gt3-wct", None)
    check("WCT roster non-empty", len(wct) >= 20, True)
    r = live.detect(sorted(wct)[:20])
    check("real WCT ids -> cas-gt3-wct", r and r["league_slug"], "cas-gt3-wct")
    # The 5th PCCD season is the one with the drivers in it.
    p5 = max((s for s in st["seasons"] if s["league_slug"] == "cas-pccd"),
             key=lambda s: s["drivers"])
    ids = live.rows_for("cas-pccd", p5["season_id"])
    r = live.detect(sorted(ids)[:18])
    check("real PCCD ids -> cas-pccd", r and r["league_slug"], "cas-pccd")
    check("  and the populated season", r and r["season_id"], p5["season_id"])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
