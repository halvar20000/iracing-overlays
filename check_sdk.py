#!/usr/bin/env python3
"""
check_sdk.py — live check of the connection to iRacing (2026-10-10).

Run it on the race PC WHILE iRacing is in the session (driving, spectating
or watching a replay):

    python check_sdk.py

It answers "the overlays get no data — why?":
  1. which Python / pyirsdk is running, and whether this process is elevated
  2. whether iRacing's telemetry memory can be opened at all — and if not,
     WHY (not running / telemetry disabled in app.ini / blocked because
     iRacing runs as administrator and Python does not, or vice versa)
  3. what iRacing reports right now: track, sessions, current session,
     spectator / replay state, drivers, positions, lap counters
  4. which overlay ports answer

Everything is printed AND written to logs/sdk_check_<time>.txt, so it can
be read from the shared folder without copying the console.
"""
from __future__ import annotations

import ctypes
import datetime as _dt
import os
import platform
import socket
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LINES: list[str] = []


def out(s: str = "") -> None:
    print(s)
    LINES.append(s)


def head(t: str) -> None:
    out("")
    out("=" * 70)
    out(t)
    out("=" * 70)


def is_admin() -> str:
    try:
        return "YES" if ctypes.windll.shell32.IsUserAnAdmin() else "no"
    except Exception:
        return "n/a (not Windows)"


def try_memmap() -> str:
    """Open iRacing's shared memory directly to get the real OS error."""
    if os.name != "nt":
        return "n/a (not Windows)"
    import mmap
    try:
        m = mmap.mmap(0, 1164 * 1024, "Local\\IRSDKMemMapFileName", access=mmap.ACCESS_READ)
        m.close()
        return "OK — telemetry memory is readable"
    except PermissionError as e:
        return (f"ACCESS DENIED ({e}) — iRacing and Python run with DIFFERENT rights. "
                f"Start both normally (or both as administrator).")
    except FileNotFoundError as e:
        return f"NOT FOUND ({e}) — iRacing is not running, or telemetry is disabled (see app.ini below)."
    except OSError as e:
        return f"OS error {e!r}"


def app_ini() -> str:
    for base in (Path.home() / "Documents", Path.home() / "OneDrive" / "Documents",
                 Path.home() / "OneDrive" / "Dokumente", Path.home() / "Dokumente"):
        p = base / "iRacing" / "app.ini"
        if p.exists():
            vals = {}
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                k = line.split("=", 1)[0].strip().lower()
                if k in ("irsdkenablemem", "irsdklog", "irsdkenabletelemetry"):
                    vals[k] = line.split("=", 1)[1].split(";")[0].strip() if "=" in line else ""
            return f"{p}: {vals or 'no irsdk keys (defaults = enabled)'}"
    return "app.ini not found in Documents\\iRacing"


def iracing_processes() -> str:
    if os.name != "nt":
        return "n/a"
    try:
        import subprocess
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq iRacingSim64DX11.exe"],
                           capture_output=True, text=True, timeout=10)
        found = [l for l in r.stdout.splitlines() if "iRacing" in l]
        return "\n    ".join(found) if found else "iRacingSim64DX11.exe NOT running"
    except Exception as e:
        return f"tasklist failed: {e}"


def port_answers(port: int) -> str:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return "answers"
    except OSError:
        return "-"


def main() -> int:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    head(f"iRacing SDK check  {stamp}")
    out(f"Python      : {sys.version.split()[0]}  ({sys.executable})")
    out(f"Platform    : {platform.platform()}")
    out(f"Elevated    : {is_admin()}  (this Python process)")
    out(f"Script dir  : {HERE}")
    try:
        import irsdk
        out(f"pyirsdk     : {getattr(irsdk, 'VERSION', '?')}  ({irsdk.__file__})")
    except Exception as e:
        out(f"pyirsdk     : IMPORT FAILED — {e!r}   ->  pip install pyirsdk")
        irsdk = None

    head("iRacing process / telemetry memory")
    out(f"Process     : {iracing_processes()}")
    out(f"Memory map  : {try_memmap()}")
    out(f"app.ini     : {app_ini()}")

    if irsdk is not None and os.name == "nt":
        head("pyirsdk startup gates (each one must pass)")
        # Gate 1: iRacing's local web service must answer "running:1"
        try:
            import urllib.request
            raw = urllib.request.urlopen(getattr(irsdk, "SIM_STATUS_URL",
                  "http://127.0.0.1:32034/get_sim_status?object=simStatus"), timeout=3).read()
            txt = raw.decode("utf-8", "replace").strip()
            out(f"Gate 1 sim status : {'PASS' if 'running:1' in txt else 'FAIL'}  answer={txt[:200]!r}")
        except Exception as e:
            out(f"Gate 1 sim status : FAIL  {e!r}")
        # Gate 2: the "new data" event must fire
        try:
            k32 = ctypes.windll.kernel32
            name = getattr(irsdk, "DATAVALIDEVENTNAME", "Local\\IRSDKDataValidEvent")
            h = k32.OpenEventW(0x00100000, False, name)
            if not h:
                out(f"Gate 2 data event : FAIL  OpenEventW({name!r}) -> 0, GetLastError={k32.GetLastError()}")
            else:
                r = k32.WaitForSingleObject(h, 1000)
                out(f"Gate 2 data event : {'PASS' if r == 0 else 'FAIL'}  WaitForSingleObject -> {r} (0 = signalled, 258 = timeout)")
                k32.CloseHandle(h)
        except Exception as e:
            out(f"Gate 2 data event : FAIL  {e!r}")
        # The memory header itself: status bit 1 = connected, tick must move
        try:
            import mmap, struct
            m = mmap.mmap(0, 1164 * 1024, "Local\\IRSDKMemMapFileName", access=mmap.ACCESS_READ)
            v, st = struct.unpack_from("<ii", m, 0)
            tick1 = struct.unpack_from("<i", m, 48 + 0)[0]
            time.sleep(0.5)
            tick2 = struct.unpack_from("<i", m, 48 + 0)[0]
            out(f"Memory header     : version={v} status={st} ({'connected' if st & 1 else 'NOT connected'})  "
                f"tick {tick1} -> {tick2} ({'moving' if tick1 != tick2 else 'frozen'})")
            m.close()
        except Exception as e:
            out(f"Memory header     : {e!r}")

    if irsdk is not None and os.name == "nt":
        head("Full header dump + test with the version check bypassed")
        try:
            import mmap, struct
            m = mmap.mmap(0, 1164 * 1024, "Local\\IRSDKMemMapFileName", access=mmap.ACCESS_READ)
            names = ["ver", "status", "tickRate", "sessInfoUpdate", "sessInfoLen", "sessInfoOffset",
                     "numVars", "varHeaderOffset", "numBuf", "bufLen", "pad0", "pad1"]
            vals = struct.unpack_from("<12i", m, 0)
            out("  " + "  ".join(f"{n}={v}" for n, v in zip(names, vals)))
            for i in range(4):
                tc, off = struct.unpack_from("<ii", m, 48 + i * 16)
                out(f"  varBuf[{i}] tick={tc} offset={off}")
            m.close()
        except Exception as e:
            out(f"  dump failed: {e!r}")
        try:
            real_version = irsdk.Header.version
            irsdk.Header.version = property(lambda self: max(1, real_version.fget(self)))
            ir2 = irsdk.IRSDK()
            ok2 = ir2.startup()
            out(f"  bypassed startup={ok2} is_initialized={ir2.is_initialized} is_connected={ir2.is_connected}")
            if ok2:
                ir2.freeze_var_buffer_latest()
                out(f"  SessionTime={ir2['SessionTime']}  SessionNum={ir2['SessionNum']}  "
                    f"SessionState={ir2['SessionState']}  CamCarIdx={ir2['CamCarIdx']}")
                wk = ir2["WeekendInfo"] or {}
                ss = ((ir2["SessionInfo"] or {}).get("Sessions")) or []
                out(f"  Track={wk.get('TrackDisplayName')}  sessions={[ (x.get('SessionNum'), x.get('SessionType')) for x in ss]}")
                out(f"  Drivers={len(((ir2['DriverInfo'] or {}).get('Drivers')) or [])}  "
                    f"CarIdxLap[0:6]={(ir2['CarIdxLap'] or [])[:6]}")
                ir2.shutdown()
            irsdk.Header.version = real_version
        except Exception as e:
            out(f"  bypass test failed: {e!r}")

    if irsdk is not None:
        head("pyirsdk connection")
        ir = irsdk.IRSDK()
        ok = False
        for _ in range(10):
            try:
                ok = ir.startup()
            except Exception as e:
                out(f"startup() raised {e!r}")
                ok = False
            if ok and ir.is_initialized and ir.is_connected:
                break
            time.sleep(1)
        out(f"startup={ok}  is_initialized={ir.is_initialized}  is_connected={ir.is_connected}")
        if ok and ir.is_initialized and ir.is_connected:
            ir.freeze_var_buffer_latest()
            g = lambda k: ir[k]
            wk = g("WeekendInfo") or {}
            out(f"Track       : {wk.get('TrackDisplayName')} / {wk.get('TrackConfigName')}  "
                f"TrackName='{wk.get('TrackName')}'")
            out(f"SessionID   : {wk.get('SessionID')}  SubSessionID: {wk.get('SubSessionID')}  "
                f"Event: {wk.get('EventType')}  Official: {wk.get('Official')}")
            sessions = ((g("SessionInfo") or {}).get("Sessions")) or []
            for s in sessions:
                out(f"  session {s.get('SessionNum')}: {str(s.get('SessionType')):<14} "
                    f"'{s.get('SessionName')}'  results={len(s.get('ResultsPositions') or [])}")
            out(f"SessionNum  : {g('SessionNum')}   SessionState: {g('SessionState')}   "
                f"SessionFlags: {g('SessionFlags')}")
            out(f"SessionTime : {g('SessionTime')}   TimeRemain: {g('SessionTimeRemain')}   "
                f"LapsRemain: {g('SessionLapsRemain')}")
            out(f"Replay      : IsReplayPlaying={g('IsReplayPlaying')}  ReplayFrameNumEnd={g('ReplayFrameNumEnd')}  "
                f"ReplayPlaySpeed={g('ReplayPlaySpeed')}")
            out(f"On track    : IsOnTrack={g('IsOnTrack')}  CamCarIdx={g('CamCarIdx')}  "
                f"DriverCarIdx={(g('DriverInfo') or {}).get('DriverCarIdx')}")
            drivers = ((g("DriverInfo") or {}).get("Drivers")) or []
            out(f"Drivers     : {len(drivers)} in DriverInfo  "
                f"(spectators: {sum(1 for d in drivers if d.get('IsSpectator'))}, "
                f"pace car: {sum(1 for d in drivers if d.get('CarIsPaceCar'))})")
            laps = g("CarIdxLap") or []
            pct = g("CarIdxLapDistPct") or []
            pos = g("CarIdxPosition") or []
            surf = g("CarIdxTrackSurface") or []
            live = [i for i in range(len(surf)) if surf[i] is not None and surf[i] >= 0]
            out(f"Cars in world: {len(live)}")
            for d in drivers[:8]:
                i = d.get("CarIdx")
                if i is None or i >= len(laps):
                    continue
                out(f"  #{str(d.get('CarNumber')):>4} {str(d.get('UserName') or '')[:22]:<22} lap={laps[i]:>3} "
                    f"pct={pct[i]:.3f} pos={pos[i]} surface={surf[i]}")
            # values change between two reads = telemetry is live
            t1 = g("SessionTime")
            time.sleep(1.0)
            ir.freeze_var_buffer_latest()
            t2 = g("SessionTime")
            out(f"Live data   : SessionTime {t1} -> {t2}  "
                f"({'MOVING' if t1 != t2 else 'FROZEN — telemetry not updating'})")
            ir.shutdown()

    head("Overlay ports")
    for tag, port in (("dashboard", 5000), ("standings", 5005), ("livery", 5006), ("trackmap", 5007), ("flag", 5008),
                      ("logger", 5009), ("champ", 5010), ("dotd", 5013), ("driver", 5017)):
        out(f"  {tag:<10} {port}: {port_answers(port)}")

    head("Livery overlay (5006) + iRacing car render server (32034)")
    import json as _json
    import urllib.request as _ur
    try:
        dbg = _json.loads(_ur.urlopen("http://127.0.0.1:5006/debug", timeout=3).read())
        for k, v in dbg.items():
            sv = _json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
            out(f"  {k}: {sv[:300]}")
    except Exception as e:
        out(f"  /debug not reachable: {e!r}   (is the livery overlay started in the launcher?)")
    try:
        r = _ur.urlopen("http://127.0.0.1:5006/state", timeout=3).read()
        out(f"  /state: {r[:400].decode('utf-8', 'replace')}")
    except Exception as e:
        out(f"  /state not reachable: {e!r}")
    try:
        r = _ur.urlopen("http://127.0.0.1:32034/pk_car.png?carPath=porsche992cup&size=1", timeout=5)
        body = r.read()
        out(f"  render server: HTTP {r.status}, {len(body)} bytes, PNG={body[:8] == bytes([137, 80, 78, 71, 13, 10, 26, 10])}")
    except Exception as e:
        out(f"  render server: FAILED {e!r}")

    logs = HERE / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / f"sdk_check_{stamp}.txt"
    path.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    out("")
    out(f"Written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
