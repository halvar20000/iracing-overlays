#!/usr/bin/env python3
"""
check_setup.py — preflight check for the iRacing overlays.

Run this FIRST whenever the overlays "don't show up" or after setting the
project up on a new PC:

    python check_setup.py

It verifies, in order:
  1. which Python interpreter is actually running (the classic pip/python mismatch)
  2. every dependency the overlays import, mapped to what breaks without it
  3. whether the project sits on a local disk (network drives break OBS + admin)
  4. whether the overlay ports are free, already in use, or already serving
  5. that every overlay script is actually present

Exit code 0 = ready to launch. 1 = something needs fixing.
"""
from __future__ import annotations

import importlib
import os
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (module, pip name, what breaks without it, fatal?)
DEPS = [
    ("flask",    "flask",        "every overlay (no web server at all)",        True),
    ("irsdk",    "pyirsdk",      "every SDK overlay — they SystemExit on import", True),
    ("PIL",      "pillow",       "iracing_livery.py (TGA->PNG paint cache)",    False),
    ("requests", "requests",     "livery + catchup car renders",                False),
    ("fastapi",  "fastapi",      "iracing_racecontrol.py (port 8080)",          False),
    ("uvicorn",  "uvicorn",      "iracing_racecontrol.py (port 8080)",          False),
    ("win32api", "pywin32",      "dashboard Go-Live keystroke, click-through",  False),
    ("webview",  "pywebview",    "Driving Mode (DRIVE.bat / drive.py)",         False),
]

# (script, port)
OVERLAYS = [
    ("iracing_dashboard.py", 5000), ("iracing_grid.py", 5001),
    ("iracing_results.py", 5002), ("iracing_results_lite.py", 5003),
    ("iracing_live_indicator.py", 5004), ("iracing_standings.py", 5005),
    ("iracing_livery.py", 5006), ("iracing_trackmap.py", 5007),
    ("flag_overlay.py", 5008), ("iracing_race_logger.py", 5009),
    ("iracing_championship.py", 5010), ("iracing_session_info.py", 5011),
    ("iracing_drivingline.py", 5012), ("iracing_dotd_overlay.py", 5013),
    ("iracing_qualidelta.py", 5014), ("iracing_catchup.py", 5015),
    ("iracing_weather.py", 5016), ("iracing_drivercard.py", 5017),
    ("iracing_race_leader.py", 5018), ("iracing_racecontrol.py", 8080),
]

OK, WARN, BAD = "  OK  ", " WARN ", " FAIL "
problems: list[str] = []
warnings: list[str] = []


def head(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


def check_python() -> None:
    head("1. Python interpreter")
    print(f"  executable : {sys.executable}")
    print(f"  version    : {sys.version.split()[0]}")
    if sys.version_info < (3, 10):
        print(f"[{BAD}] Python 3.10+ required.")
        problems.append("Python too old — install 3.10+ from python.org.")
    else:
        print(f"[{OK}] version fine")

    # The #1 new-PC trap: pip installed into a different interpreter.
    if "WindowsApps" in sys.executable:
        print(f"[{BAD}] This is the Microsoft Store Python stub.")
        problems.append(
            "Microsoft Store Python detected. Uninstall it and install from "
            "python.org WITH 'Add Python to PATH' ticked."
        )
    print("\n  Always install deps with:  python -m pip install -r requirements.txt")
    print("  (the 'python -m' guarantees the SAME interpreter the launcher uses)")


def check_deps() -> None:
    head("2. Dependencies")
    missing_fatal, missing_opt = [], []
    for mod, pip_name, breaks, fatal in DEPS:
        if mod in ("win32api", "webview") and sys.platform != "win32":
            continue
        try:
            importlib.import_module(mod)
            print(f"[{OK}] {pip_name}")
        except Exception:
            print(f"[{BAD if fatal else WARN}] {pip_name:<10} missing -> breaks: {breaks}")
            (missing_fatal if fatal else missing_opt).append(pip_name)
    if missing_fatal:
        problems.append(
            "Missing required packages: " + ", ".join(missing_fatal) +
            "\n     Fix:  python -m pip install -r requirements.txt"
        )
    if missing_opt:
        warnings.append("Optional packages missing: " + ", ".join(missing_opt))


def check_location() -> None:
    head("3. Project location")
    print(f"  folder: {HERE}")
    drive = str(HERE)[:2]
    if sys.platform == "win32" and len(drive) == 2 and drive[1] == ":":
        try:
            import ctypes
            # DRIVE_REMOTE == 4
            dtype = ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\")
            if dtype == 4:
                print(f"[{WARN}] {drive} is a NETWORK drive.")
                warnings.append(
                    f"Project is on network drive {drive}. Mapped drive letters are "
                    "per-logon-session: if OBS runs as administrator it CANNOT see "
                    f"{drive}, and 'Local file' browser sources fail with an error "
                    "page. Use the UNC path (\\\\server\\share\\...) or copy the "
                    "project to a local C:\\ folder (recommended for streaming)."
                )
            else:
                print(f"[{OK}] local drive")
        except Exception:
            print(f"[{WARN}] could not determine drive type")
    if str(HERE).startswith("\\\\"):
        warnings.append("Running from a UNC path — cmd.exe cannot use UNC as a "
                        "working directory; the .bat launchers may misbehave.")


def check_scripts() -> None:
    head("4. Overlay scripts present")
    missing = [s for s, _ in OVERLAYS if not (HERE / s).exists()]
    for s in missing:
        print(f"[{BAD}] {s} not found")
    if missing:
        problems.append("Missing scripts: " + ", ".join(missing) +
                        " — incomplete download? Re-clone the repo.")
    else:
        print(f"[{OK}] all {len(OVERLAYS)} scripts found")


def port_state(port: int) -> str:
    """serving | free"""
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return "serving"
    except Exception:
        return "free"
    finally:
        s.close()


def check_ports() -> None:
    head("5. Ports")
    serving = [p for _, p in OVERLAYS if port_state(p) == "serving"]
    if serving:
        print(f"[{OK}] answering on 127.0.0.1: {', '.join(map(str, serving))}")
    free = [p for _, p in OVERLAYS if p not in serving]
    if free:
        print(f"[{WARN}] nothing listening on: {', '.join(map(str, free))}")
        print("        (normal if the overlays aren't started yet)")


def main() -> int:
    print("=" * 62)
    print("iRacing Overlays — setup check")
    print("=" * 62)
    check_python()
    check_deps()
    check_location()
    check_scripts()
    check_ports()

    head("Summary")
    if problems:
        print(f"{len(problems)} problem(s) MUST be fixed:\n")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}\n")
    if warnings:
        print(f"{len(warnings)} warning(s):\n")
        for i, w in enumerate(warnings, 1):
            print(f"  {i}. {w}\n")
    if not problems:
        print("No blockers found. Start the overlays with:  python launch_all.py")
        print("Then re-run this script — section 5 should show ports 'serving'.")
    print()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
