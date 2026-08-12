#!/usr/bin/env python3
"""
dump_incidents.py — show what iRacing ACTUALLY publishes about incidents.

Run this while spectating a session in which you KNOW a driver has
incidents (e.g. right after you saw someone spin or collide):

    python dump_incidents.py

It prints, for the live session:
  1. every session in the plan + whether it has a ResultsPositions block
  2. the COMPLETE key list of one ResultsPositions entry  <-- the important bit
  3. per driver: CurDriverIncidentCount / TeamIncidentCount / Results Incidents
  4. a verdict on which source (if any) carries real data

Stop with Ctrl+C. Nothing is written; this is read-only.
"""
import sys
import time

try:
    import irsdk
except ImportError:
    print("ERROR: pyirsdk not installed.  Run:  python -m pip install pyirsdk")
    raise SystemExit(1)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def sep(t=""):
    print("\n" + "=" * 72)
    if t:
        print(f"  {t}")
        print("=" * 72)


def main():
    ir = irsdk.IRSDK()
    if not ir.startup():
        print("Not connected to iRacing. Start the sim / join a session first.")
        return 1
    print("Connected. Reading session data…")

    sess_info = ir["SessionInfo"] or {}
    sessions = sess_info.get("Sessions") or []
    cur_num = ir["SessionNum"]

    sep("1. SESSION PLAN")
    print(f"  Telemetry SessionNum = {cur_num!r}   (type: {type(cur_num).__name__})")
    for s in sessions:
        rp = s.get("ResultsPositions")
        mark = " <-- CURRENT" if s.get("SessionNum") == cur_num else ""
        print(f"  SessionNum={s.get('SessionNum')!r:<5} "
              f"type={str(s.get('SessionType')):<12} "
              f"ResultsPositions={'YES (' + str(len(rp)) + ' rows)' if rp else 'none':<16}"
              f"{mark}")

    # The current session's results block
    chosen = None
    for s in sessions:
        if s.get("SessionNum") == cur_num:
            chosen = s
            break
    if chosen is None:
        print("\n  !! No session matches SessionNum — THIS ALONE WOULD BREAK IT.")

    sep("2. ALL KEYS IN ONE ResultsPositions ENTRY")
    rp = (chosen or {}).get("ResultsPositions") or []
    if not rp:
        print("  Current session has NO ResultsPositions block.")
        print("  -> Any overlay reading incidents from results shows 0 / blank.")
        for s in sessions:
            if s.get("ResultsPositions"):
                print(f"  (SessionNum={s.get('SessionNum')} "
                      f"[{s.get('SessionType')}] does have one)")
    else:
        print("  Keys iRacing publishes per result row:\n")
        for k, v in sorted(rp[0].items()):
            flag = "  <-- incident-ish" if "inc" in k.lower() else ""
            print(f"    {k:<26} = {str(v)[:34]:<34}{flag}")

    sep("3. PER-DRIVER COMPARISON")
    drivers = (ir["DriverInfo"] or {}).get("Drivers", []) or []
    res_by_idx = {r.get("CarIdx"): r for r in rp}
    print(f"  {'CarIdx':<7}{'Driver':<24}{'CurDriver':>10}{'Team':>7}{'Results':>9}")
    print("  " + "-" * 57)
    vals = {"cur": set(), "team": set(), "res": set()}
    for d in drivers:
        if int(d.get("CarIsPaceCar") or 0):
            continue
        cidx = d.get("CarIdx")
        cur = d.get("CurDriverIncidentCount")
        team = d.get("TeamIncidentCount")
        res = (res_by_idx.get(cidx) or {}).get("Incidents")
        vals["cur"].add(cur)
        vals["team"].add(team)
        vals["res"].add(res)
        print(f"  {str(cidx):<7}{str(d.get('UserName'))[:23]:<24}"
              f"{str(cur):>10}{str(team):>7}{str(res):>9}")

    sep("4. VERDICT")
    for name, label in (("cur", "DriverInfo.CurDriverIncidentCount"),
                        ("team", "DriverInfo.TeamIncidentCount"),
                        ("res", "ResultsPositions.Incidents")):
        v = vals[name]
        if not v or v == {None}:
            verdict = "NOT PUBLISHED (field absent)"
        elif v == {-1}:
            verdict = "DEAD — sentinel -1 for every car"
        elif v == {0}:
            verdict = "DEAD — 0 for every car (not populated live)"
        else:
            verdict = f"USABLE — real spread: {sorted(x for x in v if x is not None)[:8]}"
        print(f"  {label:<38} {verdict}")
    print("\n  Any source marked USABLE is what the drivercard should read.")
    print("  If all are dead, iRacing gives spectators no incident data and")
    print("  the Inc field must be derived (like iracing_race_logger.py does)")
    print("  or dropped from the card.\n")

    ir.shutdown()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped.")
