"""Offline checks for the WCT-look pages (wct_board): final result 5002 /wct,
last CLS round 5010 /lastrace/wct, team championship 5010 /teams/wct, the
shared page commands (5001 /nav).
Run: python test_wct_board.py   (needs flask, requests + pyirsdk or an irsdk stub)"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wct_board
import iracing_results as rs
import iracing_championship as ch
import iracing_grid as gr

failures = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)

def sec(d, label):
    return next((s for s in d["sections"] if s["label"] == label), None)

# ---- final result (5002) ----------------------------------------------------
def row(pos, name, gap, proam, cls=1, lb=0, fl=90.0, out=0, team=""):
    return {"position": pos, "name": name, "car_number": str(pos), "car": "Car", "team_name": team,
            "class_id": cls, "class_name": {1: "GTP", 2: "GT3"}[cls], "class_color": "#123456",
            "time_gap": gap, "laps_behind": lb, "laps_complete": 30 - lb, "fastest_lap": fl,
            "reason_id": out, "reason_out": "Disconnected" if out else "Running", "proam": proam}

rows = [row(1, "A", 0.0, "PRO", fl=91), row(2, "B", 5.0, "AM", fl=90.5), row(3, "C", 7.5, "PRO"),
        row(4, "D", None, "AM", lb=1, fl=89.9), row(5, "E", None, "PRO", out=1)]
ev = {"league": "CAS GT3 WCT", "slug": "cas-gt3-wct", "round": 5}
snap = {"connected": True, "results": rows, "proam_active": True, "team_event": False, "race_no": 1,
        "track_short": "Indianapolis", "track_config": "Road Course"}
d = rs.wct_data(snap, ev)
check("R1 title", d["title"] == "LAUF 5 - CAS GT3 WCT - INDIANAPOLIS ROAD COURSE", d["title"])
check("R2 sections GESAMT/PRO/AM", [s["label"] for s in d["sections"]] == ["GESAMT", "PRO", "AM"])
g = sec(d, "GESAMT")["rows"]
check("R3 overall values", [r["value"] for r in g] == ["30 LAPS", "+5.000", "+7.500", "+1 LAP", "DNF"], str([r["value"] for r in g]))
check("R4 overall marks pro/am", g[0]["mark"] == wct_board.PRO_COLOR and g[1]["mark"] == wct_board.AM_COLOR)
check("R5 overall fastest lap = D", [r["fl"] for r in g] == [False, False, False, True, False])
am = sec(d, "AM")["rows"]
check("R6 AM renumbered + class gaps", [(r["pos"], r["name"], r["value"]) for r in am] == [(1, "B", "30 LAPS"), (2, "D", "+1 LAP")], str(am))
pro = sec(d, "PRO")["rows"]
check("R7 PRO gaps to the class winner", [r["value"] for r in pro] == ["30 LAPS", "+7.500", "DNF"], str([r["value"] for r in pro]))
snap2 = dict(snap, results=[dict(r, time_gap=None) if r["position"] == 1 else r for r in rows])
check("R8 winner gap None counts as 0", [r["value"] for r in sec(rs.wct_data(snap2, ev), "PRO")["rows"]][1] == "+7.500")
mc = [row(1, "A", 0.0, None, cls=1, team="Team A"), row(2, "B", 3.0, None, cls=1, team="Team B"),
      row(3, "C", None, None, cls=2, lb=2, team="Team C"), row(4, "D", None, None, cls=2, lb=3, team="Team D")]
d = rs.wct_data(dict(snap, results=mc, proam_active=False, team_event=True, race_no=2), ev)
check("R9 multiclass: one section per class", [s["label"] for s in d["sections"]] == ["GTP", "GT3"])
gt3 = sec(d, "GT3")["rows"]
check("R10 class pages relative + team names", [(r["pos"], r["name"], r["value"]) for r in gt3] == [(1, "Team C", "28 LAPS"), (2, "Team D", "+1 LAP")], str(gt3))
check("R11 race 2 in title", "LAUF 5 · RENNEN 2" in d["title"], d["title"])
check("R12 empty result", rs.wct_data({"connected": True, "results": []}, {})["sections"] == [])

# ---- last CLS round (5010) --------------------------------------------------
def res(pos, name, pa, pts, lb=0, st="CLASSIFIED", best=90000, fl=False):
    return {"position": pos, "name": name, "iracingMemberId": name, "startNumber": str(pos), "proAmClass": pa,
            "car": "Car", "status": st, "lapsBehind": lb, "gapMs": None, "bestLapMs": best, "fastestLap": fl, "points": pts}
info = {"league": {"slug": "cas-gt3-wct", "name": "CAS GT3 WCT"},
        "lastRound": {"number": 4, "track": "Motegi", "trackConfig": "GP",
                      "races": [{"raceNumber": 1, "results": [res(1, "P1", "PRO", 35), res(2, "A1", "AM", 33, fl=True, best=89000),
                                                              res(3, "P2", "PRO", 31, lb=1), res(4, "A2", "AM", 29, st="DNF")]}],
                      "combined": [{"name": "P1", "iracingMemberId": "P1", "proAmClass": "PRO", "race1": {"position": 1, "points": 35}, "total": 35},
                                   {"name": "A1", "iracingMemberId": "A1", "proAmClass": "AM", "race1": {"position": 2, "points": 35}, "total": 35},
                                   {"name": "P2", "iracingMemberId": "P2", "proAmClass": "PRO", "race1": {"position": 3, "points": 33}, "total": 33},
                                   {"name": "A2", "iracingMemberId": "A2", "proAmClass": "AM", "race1": {"position": 4, "points": 0}, "total": 0}]}}
d = ch._lastrace_wct(info)
check("L1 title", d["title"] == "LAUF 4 - CAS GT3 WCT - MOTEGI GP", d["title"])
check("L2 one race: ERGEBNIS / PRO / AM", [s["label"] for s in d["sections"]] == ["ERGEBNIS", "PRO", "AM"])
e = sec(d, "ERGEBNIS")["rows"]
check("L3 overall: no (overall) points in Pro/Am", all(r["pts"] is None for r in e))
check("L4 overall values", [r["value"] for r in e] == ["", "", "+1 LAP", "DNF"], str([r["value"] for r in e]))
a = sec(d, "AM")["rows"]
check("L5 AM = CLASS points from combined", [(r["pos"], r["name"], r["pts"]) for r in a] == [(1, "A1", 35), (2, "A2", 0)], str(a))
check("L6 AM fastest lap", a[0]["fl"] is True)
info2 = {"league": {"slug": "cas-pccd", "name": "CAS PCC"},
         "lastRound": {"number": 8, "track": "Hungaroring", "races": [
             {"raceNumber": 1, "results": [res(1, "X", None, 41), res(2, "Y", None, 35)]},
             {"raceNumber": 2, "results": [res(1, "Y", None, 41), res(2, "X", None, 35)]}],
             "combined": [{"position": 1, "name": "X", "race1": {"position": 1}, "race2": {"position": 2}, "total": 76},
                          {"position": 2, "name": "Y", "race1": {"position": 2}, "race2": {"position": 1}, "total": 76}]}}
d = ch._lastrace_wct(info2)
check("L7 two races + WERTUNG", [s["label"] for s in d["sections"]] == ["RENNEN 1", "RENNEN 2", "WERTUNG"])
check("L8 race points when not Pro/Am", [r["pts"] for r in sec(d, "RENNEN 1")["rows"]] == [41, 35])
w = sec(d, "WERTUNG")["rows"][0]
check("L9 WERTUNG row", (w["name"], w["sub"], w["pts"]) == ("X", "R1 P1 · R2 P2", 76), str(w))

# ---- team championship (5010) ----------------------------------------------
st = {"league": {"slug": "cas-iec", "name": "CAS IEC"}, "season": {"completedRounds": 2},
      "teams": [{"rank": 1, "teamName": "All", "points": 9}],
      "teamClasses": [{"carClassShortCode": "LMP2", "teams": [{"rank": 1, "teamName": "T1", "points": 193}]},
                      {"carClassShortCode": "GT3", "teams": [{"rank": 1, "teamName": "T2", "points": 204}]},
                      {"carClassShortCode": "PCUP", "teams": []}]}
d = ch._teams_wct(st)
check("T1 team classes win over the plain table", [s["label"] for s in d["sections"]] == ["LMP2", "GT3"])
check("T2 title", d["title"] == "TEAMWERTUNG - CAS IEC - NACH LAUF 2", d["title"])
check("T3 row", d["sections"][0]["rows"][0] == {"pos": 1, "num": "", "name": "T1", "pts": 193})
d = ch._teams_wct({"league": {"name": "W"}, "season": {}, "teams": [{"rank": 1, "teamName": "S", "points": 5}], "teamClasses": []})
check("T4 plain team table", [s["label"] for s in d["sections"]] == ["TEAMS"])
d = ch._teams_wct({"league": {"name": "P"}, "season": {}, "teams": None, "teamClasses": None})
check("T5 league without teams", d["sections"] == [] and "Keine" in d["empty"])
check("T6 old CLS without the field", "noch keine" in ch._teams_wct({"league": {}, "season": {}})["empty"])

# ---- page commands + routes ---------------------------------------------------
n = wct_board.PageNav()
n.send("next"); check("N1 next -> manual", n.get()["manual"] and n.get()["seq"] == 1)
n.send("auto"); check("N2 auto toggles back", not n.get()["manual"])
n.send("auto_off"); n.send("auto_off"); check("N3 auto_off stays off", n.get()["manual"] and n.get()["seq"] == 4)
c = gr.app.test_client()
r = c.get("/nav")
check("N4 grid /nav is CORS open", r.headers.get("Access-Control-Allow-Origin") == "*")
check("N5 unknown command 404", c.get("/nav/zzz").status_code == 404)
check("W1 5002 /wct page", b"/wct/data" in rs.app.test_client().get("/wct").data)
check("W2 5002 /wct/data", rs.app.test_client().get("/wct/data").get_json()["tab"] == "RESULTS")
cc = ch.app.test_client()
check("W3 5010 /lastrace/wct page", b"/lastrace/wct/data" in cc.get("/lastrace/wct").data)
check("W4 5010 /teams/wct page", b"/teams/wct/data" in cc.get("/teams/wct").data)
check("W5 /lastrace/pro still works", cc.get("/lastrace/pro").status_code == 200)

# ---- IEC: per class, one row per team (CLS lists drivers, no class) ----------
def res(pos, name, team, car, laps, mid, status="CLASSIFIED", best=90000, pts=10):
    return {"position": pos, "name": name, "teamName": team, "car": car, "laps": laps, "lapsBehind": 0,
            "iracingMemberId": mid, "status": status, "bestLapMs": best, "points": pts, "startNumber": str(pos)}
iec = {"league": {"name": "CAS IEC", "slug": "cas-iec"},
       "lastRound": {"number": 2, "track": "Fuji", "races": [{"raceNumber": 1, "results": [
           res(1, "A1", "LMP Team", "Dallara P217", 100, "1", best=80000),
           res(2, "B1", "Team B", "Dallara P217", 60, "2", best=81000), res(2, "B2", "Team B x", "Dallara P217", 39, "3"),
           res(3, "G1", "GT Team", "BMW M4 GT3", 95, "4", best=90000),
           res(4, "G2", "GT Two", "BMW M4 GT3", 50, "5", best=89000), res(4, "G3", "GT Two", "BMW M4 GT3", 44, "99"),
           res(5, "G4", "GT Out", "Ferrari 296 GT3", 10, "6", status="DSQ")]}]}}
iec_st = {"standings": [{"iracingMemberId": m, "carClassName": c} for m, c in
                        (("1", "LMP2"), ("2", "LMP2"), ("3", "LMP2"), ("4", "GT3"), ("5", "GT3"), ("6", "GT3"))]}
d = ch._lastrace_wct(iec, iec_st)
labels = [s["label"] for s in d["sections"]]
check("I1 one page set per class, fastest first", labels == ["LMP2", "GT3"], str(labels))
lmp, gt = d["sections"][0]["rows"], d["sections"][1]["rows"]
check("I2 team mates merged, team name shown", [r["name"] for r in lmp] == ["LMP Team", "Team B"], str(lmp))
check("I3 team laps = sum of stints", lmp[1]["value"] == "+1 LAP" and lmp[0]["value"] == "100 LAPS", str(lmp))
check("I4 class positions + unlisted driver's class from the car", [r["pos"] for r in gt] == [1, 2, 3] and gt[1]["name"] == "GT Two", str(gt))
check("I5 no points in a team race, DSQ shown, class FL", all(r["pts"] is None for r in gt) and gt[2]["value"] == "DSQ" and gt[1]["fl"], str(gt))

# ---- driver championship /table/wct -------------------------------------------
def srow(rank, name, pts, **kw):
    return dict({"rank": rank, "name": name, "points": pts, "teamName": "T", "startNumber": str(rank)}, **kw)
d = ch._table_wct({"league": {"name": "CAS PCC"}, "season": {"completedRounds": 8},
                   "standings": [srow(1, "Remo", 484), srow(2, "Maurice", 435), srow(3, "X", 0, excludedAt="x")]})
check("C1 one table, gap to the leader", d["title"] == "MEISTERSCHAFT - CAS PCC - NACH LAUF 8" and
      [(r["pos"], r["value"], r["pts"]) for r in d["sections"][0]["rows"]] == [(1, "", 484), (2, "-49", 435)], str(d))
d = ch._table_wct({"league": {}, "season": {"proAmEnabled": True}, "standings": [
    srow(1, "P", 90, proAmClass="PRO"), srow(2, "A", 182, proAmClass="AM"), srow(3, "A2", 100, proAmClass="AM")]})
check("C2 Pro/Am: PRO then AM, class positions", [(s["label"], [r["pos"] for r in s["rows"]]) for s in d["sections"]]
      == [("PRO", [1]), ("AM", [1, 2])], str(d))
d = ch._table_wct({"league": {}, "season": {}, "standings": [
    srow(1, "L", 203, carClassName="LMP2"), srow(5, "G", 124, carClassName="GT3"), srow(2, "L2", 191, carClassName="LMP2")]})
check("C3 multiclass: one table per class", [(s["label"], [r["name"] for r in s["rows"]]) for s in d["sections"]]
      == [("LMP2", ["L", "L2"]), ("GT3", ["G"])], str(d))
check("C4 /table/wct page (not the /table/<cls> route)", b"/table/wct/data" in cc.get("/table/wct").data)

print()
print("ALL PASS" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
