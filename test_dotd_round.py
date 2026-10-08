"""Driver of the Day for a two-race round must equal CLS (2026-10-08).

Uses the real PCCD Algarve logs when they are present in ./logs (they are
gitignored, so the test skips elsewhere). CLS's official round-7 DotD:
Maurice Becker 0.784 — gained 6, recovery 1, 7 overtakes, 20 incidents —
with Andre Brechmann (round-6 winner) blocked.
"""
import json
import os
import sys

import driver_of_the_day as D

R1 = "logs/20261008-200046_aut_dromo_internacional_do_algarve_grand_prix_race.jsonl"
R2 = "logs/20261008-203525_aut_dromo_internacional_do_algarve_grand_prix_race.jsonl"
if not (os.path.exists(R1) and os.path.exists(R2)):
    print("SKIP: Algarve logs not present")
    sys.exit(0)

fails, passes = [], 0


def check(name, got, want):
    global passes
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")
    else:
        passes += 1


def load(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


e1, e2 = load(R1), load(R2)
r = D.analyze_round([e1, e2], exclude_names=["Andre Brechmann"])
w = r["winner"]
check("round winner = CLS", w["name"], "Maurice Becker")
check("round score = CLS 0.784", round(w["score"], 3), 0.784)
check("summed metrics = CLS", (w["positions_gained"], w["recovery"], w["overtakes"], w["incidents"]),
      (6, 1, 7, 20))
check("not provisional when both races are finished", r["provisional"], False)

# race 2 still running: race 1 + race 2 so far, provisional
end = max(i for i, e in enumerate(e2) if e.get("type") == "session_end")
rp = D.analyze_round([e1, [e for e in e2[:end // 2] if e.get("type") != "session_end"]],
                     exclude_names=["Andre Brechmann"], provisional=True)
check("mid race 2 is provisional", rp["provisional"], True)
check("mid race 2 combines both races", rp["races"], 2)

# race 2 has no completed lap yet: the round so far is race 1
early = [e for e in e2[:40] if e.get("type") in ("session_start", "pos")]
re_ = D.analyze_round([e1, early], exclude_names=["Andre Brechmann"], provisional=True)
check("race 2 not started: race 1 only", re_["races"], 1)

# a driver who only raced once can be ranked but not crowned
only1 = {d["name"] for d in r["drivers"] if d["races"] == 1}
check("one-race drivers never crowned", all(not d["eligible"] for d in r["drivers"] if d["name"] in only1), True)

# a blocked driver (no back-to-back) is ranked, not crowned — the title
# passes to the next eligible driver
rb = D.analyze_round([e1, e2], exclude_names=["Maurice Becker"])
mb = next(d for d in rb["drivers"] if d["name"] == "Maurice Becker")
check("blocked driver ranked but not crowned", (mb["eligible"], mb["blocked_repeat"]), (False, True))
check("title passes to the next eligible driver", rb["winner"]["name"], "Remo Grossenbacher")

print(f"{passes} passed, {len(fails)} failed")
for f in fails:
    print("  FAIL:", f)
sys.exit(1 if fails else 0)
