"""Offline-ish checks for cls_proam.ProAmRoster (Pro/Am bars, WCT GT3).

Runs against a canned API body; pass --live to also hit the real CLS API.
"""
import sys
import cls_proam
from cls_proam import ProAmRoster

ok = fail = 0
def check(name, cond):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name)
    ok += bool(cond); fail += (not cond)

body = {"ok": True, "league": {"slug": "cas-gt3-wct"},
        "season": {"name": "GT3 WCT 14th Season"},
        "standings": [
            {"iracingMemberId": "100", "proAmClass": "PRO"},
            {"iracingMemberId": "101", "proAmClass": "AM"},
            {"iracingMemberId": "102", "proAmClass": "am"},
            {"iracingMemberId": "103", "proAmClass": None},
            {"iracingMemberId": None,  "proAmClass": "PRO"},
            {"iracingMemberId": "104", "proAmClass": "PRO"},
        ]}

cls_proam.CACHE_PATH = cls_proam.Path("/nonexistent/proam_cache.json")
r = ProAmRoster()
r._cfg["mode"] = "auto"
r._classes = ProAmRoster._parse(body)

check("PRO lookup (int / str id)", r.lookup(100) == "PRO" and r.lookup("100") == "PRO")
check("AM lookup, case-insensitive", r.lookup(101) == "AM" and r.lookup(102) == "AM")
check("no class -> None", r.lookup(103) is None)
check("unknown / bad id -> None", r.lookup(999) is None and r.lookup(None) is None)
check("auto: WCT field -> on", r.active_for([100, 101, 102, 104, 900]))
check("auto: 2 WCT drivers in a PCCD field -> off", not r.active_for([100, 101] + list(range(900, 920))))
check("auto: under half -> off", not r.active_for([100, 101, 102, 900, 901, 902, 903]))
check("auto: empty field -> off", not r.active_for([]))
check("WCT field: no class in CLS -> UNRATED (black bar)",
      r.lookup_field(103, True) == "UNRATED" and r.lookup_field(999, True) == "UNRATED")
check("WCT field: PRO / AM stay", r.lookup_field(100, True) == "PRO"
      and r.lookup_field(101, True) == "AM")
check("not a WCT field -> None for everybody", r.lookup_field(103, False) is None
      and r.lookup_field(100, False) is None)
check("no / bad id (pace car -1) -> None", r.lookup_field(-1, True) is None
      and r.lookup_field(None, True) is None)
r._cfg["mode"] = "on";  check("mode on -> always", r.active_for([900]))
r._cfg["mode"] = "off"; check("mode off -> never", not r.active_for([100, 101, 102]))

if "--live" in sys.argv:
    r = ProAmRoster(); r._cfg["mode"] = "auto"
    r._fetch()
    st = r.status()
    print("live:", st)
    check("live roster has PRO and AM drivers", st["pro"] > 0 and st["am"] > 0)

print(f"\n{ok}/{ok + fail} passed")
sys.exit(1 if fail else 0)
