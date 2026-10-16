"""Offline checks for race_commentator.py — no network, no iRacing.
Run: python test_race_commentator.py
"""
import random
import time

import race_commentator as rc

fails = 0


def check(name, ok, extra=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
    if not ok:
        fails += 1


TEMPLATE_CFG = {"mode": "template", "api_key": "", "model": "x", "timeout_s": 1}


def entry(t, kind, name, info, idx=1, num="11"):
    return {"t": t, "kind": kind, "car_idx": idx, "car_number": num, "name": name,
            "group": "TV1", "reason": "", "info": info}


battle = entry(100.0, "battle", "Mike Zocher",
               {"pair": [3, 1], "position": 2, "gap": 0.34, "ahead": "Dennis Richter", "ahead_number": "63"})
replay_ot = entry(130.0, "replay", "Klaus Oberlaender",
                  {"event": "overtake", "passed": "Manfred Baar", "position": 7}, idx=5)
replay_spin = entry(160.0, "replay", "Torsten Folkers", {"event": "lost_control"}, idx=8)
leader = entry(200.0, "leader", "Maurice Becker", {"position": 1}, idx=0)
seen_ot = entry(230.0, "seen", "Mike Zocher", {"event": "overtake", "passed": "Dennis Richter", "position": 2})

# 1) templates: every kind gives a German line with the facts in it
c = rc.Commentator(random.Random(1), dict(TEMPLATE_CFG))
c.feed([battle], now=100.0)
ln = c.lines[0]["text"]
check("battle line names both drivers or the place", ("Zocher" in ln and ("Richter" in ln or "2" in ln)), ln)
check("gap with a German decimal comma", "0,3" in ln or "0,34" in ln or "Platz 2" in ln or "P2" in ln, ln)
c.feed([replay_ot], now=130.0)
check("overtake replay line names both cars", "Oberlaender" in c.lines[0]["text"] and "Baar" in c.lines[0]["text"],
      c.lines[0]["text"])
c.feed([replay_spin], now=160.0)
check("spin replay line", "Folkers" in c.lines[0]["text"], c.lines[0]["text"])
c.feed([leader], now=200.0)
check("leader line", "Becker" in c.lines[0]["text"], c.lines[0]["text"])
c.feed([seen_ot], now=230.0)
check("live overtake line", "Zocher" in c.lines[0]["text"] and "Richter" in c.lines[0]["text"], c.lines[0]["text"])
check("source = template without a key", all(l["source"] == "template" for l in c.lines))

# 2) the same log fed again (every tick) never repeats a line
n = len(c.lines)
c.feed([battle, replay_ot, replay_spin, leader, seen_ot], now=240.0)
check("log entries are commented once", len(c.lines) == n, len(c.lines))

# 3) a missing fact never produces "None" / "{...}" / "Platz ."
c2 = rc.Commentator(random.Random(2), dict(TEMPLATE_CFG))
c2.feed([entry(10.0, "replay", "A B", {"event": "overtake", "passed": None, "position": None})], now=10.0)
c2.feed([entry(20.0, "battle", "C D", {"position": 3, "gap": None, "ahead": "E F"})], now=20.0)
for k in range(20):   # every template variant, not just the one the rng picks first
    c2.feed([entry(30.0 + k * 10, "battle", "C D", {"position": 3, "gap": None, "ahead": "E F"}, idx=k)],
            now=30.0 + k * 10)
bad = [l["text"] for l in c2.lines if "None" in l["text"] or "{" in l["text"] or "Platz ." in l["text"]
       or "?" in l["text"] or "  " in l["text"]]
check("no placeholders or None in lines", not bad, bad)

# 4) two lines within MIN_GAP_S: the newer replaces the older (picture moved on)
c3 = rc.Commentator(random.Random(3), dict(TEMPLATE_CFG))
c3.feed([entry(50.0, "battle", "X Y", {"position": 5, "gap": 0.3, "ahead": "Z W"})], now=50.0)
c3.feed([entry(51.0, "leader", "L M", {"position": 1}, idx=9)], now=51.0)
check("rapid second line replaces the first", len(c3.lines) == 1 and "M" in c3.lines[0]["text"],
      [l["text"] for l in c3.lines])

# 5) variety: ten battles in a row do not all read the same
c4 = rc.Commentator(random.Random(4), dict(TEMPLATE_CFG))
for k in range(10):
    c4.feed([entry(1000.0 + k * 20, "battle", "A B", {"position": 4, "gap": 0.5, "ahead": "C D"}, idx=k)],
            now=1000.0 + k * 20)
check("templates vary", len({l["text"] for l in c4.lines}) >= 3, {l["text"] for l in c4.lines})

# 6) Claude mode: the API answer is used; facts + recent lines go into the call
seen_calls = []
def fake_api(f, ctx, recent):
    seen_calls.append((f, ctx, recent))
    return "Zocher klebt am Heck von Richter, gerade einmal drei Zehntel."
c5 = rc.Commentator(random.Random(5), {"mode": "auto", "api_key": "k", "model": "m", "timeout_s": 1},
                    api_call=fake_api)
c5.feed([battle], context=lambda: {"strecke": "Motegi", "runde_fuehrender": 4}, now=time.time())
for _ in range(50):
    if c5.lines:
        break
    time.sleep(0.02)
check("Claude line published", c5.lines and c5.lines[0]["source"] == "claude", list(c5.lines))
check("call got the facts and the context", seen_calls and seen_calls[0][0]["ahead"] == "Dennis Richter"
      and seen_calls[0][1]["strecke"] == "Motegi", seen_calls[:1])

# 7) Claude fails -> template line, error kept for the status page
def broken_api(f, ctx, recent):
    raise OSError("network down")
c6 = rc.Commentator(random.Random(6), {"mode": "auto", "api_key": "k", "model": "m", "timeout_s": 1},
                    api_call=broken_api)
c6.feed([leader], now=time.time())
for _ in range(50):
    if c6.lines:
        break
    time.sleep(0.02)
check("API failure falls back to the template", c6.lines and c6.lines[0]["source"] == "template", list(c6.lines))
check("error visible in status", "network down" in c6.status()["last_error"])

# 8) mode "template" never calls the API even with a key
calls = []
c7 = rc.Commentator(random.Random(7), {"mode": "template", "api_key": "k", "model": "m"},
                    api_call=lambda *a: calls.append(a) or "x")
c7.feed([leader], now=1.0)
check("template mode ignores the key", not calls and c7.lines and c7.lines[0]["source"] == "template")

# 9) switching REGIE on with an old log: nothing old is commented
c8 = rc.Commentator(random.Random(8), dict(TEMPLATE_CFG))
c8.skip_existing([battle, leader])
c8.feed([battle, leader], now=300.0)
check("skip_existing: old log stays silent", not c8.lines)

# 10) qualifying lines (quali_director.py log entries)
cq = rc.Commentator(random.Random(9), dict(TEMPLATE_CFG))
def q(t, kind, name, info, idx=1):
    cq.feed([entry(t, kind, name, info, idx=idx)], now=t)
    return cq.lines[0]["text"] if cq.lines and cq.lines[0]["t"] == t else None
ln = q(10.0, "quali_lap", "Mike Zocher", {"lap_time_str": "1:39.512", "position": 1, "new_pole": True})
check("new pole line with German lap time", ln and "Zocher" in ln and "1:39,512" in ln, ln)
ln = q(20.0, "quali_lap", "Mike Zocher", {"lap_time_str": "1:39.400", "position": 1, "new_pole": True,
                                          "kept_pole": True}, idx=2)
check("own pole improved is not 'new leader'", ln and ("verbessert" in ln or "legt noch einmal nach" in ln), ln)
ln = q(30.0, "quali_lap", "Dennis Richter", {"lap_time_str": "1:40.112", "position": 3, "gap_to_pole": 0.71},
       idx=3)
check("lap result with position and gap", ln and "Platz 3" in ln and "Richter" in ln, ln)
ln = q(40.0, "attack", "Andreas Bastian", {"delta": -0.23, "predicted_position": 1, "pct": 0.6}, idx=4)
check("attack under pole pace", ln and "Bastian" in ln and "0,2" in ln, ln)
ln = q(80.0, "attack", "Andreas Bastian", {"delta": 0.4, "predicted_position": 4, "pct": 0.7}, idx=5)
check("attack slower: predicted, not final", ln and ("auf Kurs zu Platz 4" in ln or "Platz 4" in ln)
      and "reicht" not in ln, ln)
# video 2026-10-16: attack lines every 10 s, three decimals, AM drivers called PRO
ca = rc.Commentator(random.Random(4), {"mode": "template", "api_key": ""})
att = lambda t, car, pct: dict(entry(t, "attack", f"Fahrer {car}", {"delta": -0.05, "predicted_position": 1, "pct": pct}),
                               car_idx=car)
ca.feed([att(100.0, 1, 0.2)], now=100.0)          # too early in the lap
ca.feed([att(110.0, 2, 0.6)], now=110.0)          # said
ca.feed([att(120.0, 3, 0.7)], now=120.0)          # within 25 s of the last attack line
ca.feed([att(140.0, 2, 0.8)], now=140.0)          # same car again within 100 s
ca.feed([att(150.0, 4, 0.5)], now=150.0)          # said
said_a = [l["text"] for l in ca.lines]
check("attack lines: from 40 % of the lap, every 25 s, once per car", len(said_a) == 2
      and "Fahrer 2" in said_a[1] and "Fahrer 4" in said_a[0], said_a)
fq = rc.facts_from(dict(entry(1.0, "quali_lap", "Manfred Baar", {"lap_time_str": "1:49.900", "position": 2,
                                                                  "gap_to_pole": 0.7351, "margin": None}),
                        klasse="AM"))
check("gaps rounded to hundredths, driver's class handed on", fq["gap_to_pole_s"] == 0.74 and fq["klasse"] == "AM", fq)
check("gaps over a second to tenths", rc.facts_from(entry(1.0, "quali_lap", "X", {"gap_to_pole": 1.664}))["gap_to_pole_s"] == 1.7)
check("finished attack is silent", rc.facts_from(entry(60.0, "attack", "X", {"finished": True})) is None)

# quali, a car from the field before anyone has a time: introduce him, never "no time yet"
qr = random.Random(3)
nt = rc.facts_from(entry(61.0, "field", "Bastian Krumpschmied",
                         {"quali": True, "car": "Corvette Z06 GT3.R", "team": "CAS Tech", "flying": False,
                          "lap_time_str": None, "position": None, "times_set": 0}))
lines = {rc.template_line(nt, qr) for _ in range(40)}
check("quali field without a time: varied intros", len(lines) >= 3
      and all("Krumpschmied" in l and "Zeit" not in l and "None" not in l for l in lines), lines)
check("quali field facts carry car + team", nt["car"] == "Corvette Z06 GT3.R" and nt["team"] == "CAS Tech"
      and nt["session"] == "Qualifying")
fl = rc.facts_from(entry(62.0, "field", "Mike Zocher", {"quali": True, "car": "Porsche", "flying": True}))
check("quali field on a flying lap", "schnelle" in rc.template_line(fl, qr).lower() or "gezeitete" in rc.template_line(fl, qr))
wt = rc.facts_from(entry(63.0, "field", "Mike Zocher", {"quali": True, "car": "Porsche", "lap_time_str": "1:49.311",
                                                      "position": 5, "times_set": 9}))
l = rc.template_line(wt, random.Random(1))
check("quali field with a time: position", "Zocher" in l and ("5" in l), l)
check("no empty facts handed on (no lap_time / position keys without a time)",
      "lap_time" not in nt and "position" not in nt and "cars_with_time" not in nt, nt)
cq = rc.Commentator(random.Random(2), {"mode": "template", "api_key": ""})
qf = lambda t, n: entry(t, "field", n, {"quali": True, "car": "Porsche"})
cq.feed([qf(100.0, "Fahrer A")], now=100.0)
cq.feed([qf(112.0, "Fahrer B")], now=112.0)
cq.feed([qf(124.0, "Fahrer C")], now=124.0)
cq.feed([qf(150.0, "Fahrer D")], now=150.0)
said_q = [l["text"] for l in cq.lines]
check("quali: a car from the field at most every 45 s (no roll call)", len(said_q) == 2
      and any("Fahrer A" in x for x in said_q) and any("Fahrer D" in x for x in said_q), said_q)
nt2 = rc.facts_from(entry(64.0, "field", "Ohne Team", {"quali": True, "car": "BMW", "team": None}))
check("team template skipped without a team", all("fährt für" not in rc.template_line(nt2, random.Random(i)) for i in range(30)))

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
raise SystemExit(1 if fails else 0)
