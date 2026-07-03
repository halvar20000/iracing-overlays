"""
drive.py — ONE-CLICK "DRIVING MODE" for the on-top overlays.

Double-click DRIVE.bat (Windows) or run `python drive.py`.

It starts, in the background, and shows as always-on-top windows over
the sim:

  • Corner Cues   — iracing_drivingline.py (port 5012) + driving_line_window.py
  • Quali Delta   — iracing_qualidelta.py  (port 5014) + on-top window
  • Track Map     — iracing_trackmap.py    (port 5007) + on-top window

Stopping is just as simple:
  • close this console window, or press Ctrl+C, or
  • close all three overlay windows.
Everything drive.py started is shut down cleanly either way.

REQUIREMENTS
  • iRacing running in BORDERLESS WINDOWED mode (Options -> Graphics),
    or the sim's fullscreen surface covers the overlays.
  • pip install pywebview   (the Quali Delta / Track Map windows)
  • pip install pywin32     (click-through; already used elsewhere)

Servers that are ALREADY running (e.g. you started the launcher GUI)
are reused and left running when driving mode ends — drive.py only
stops what it started itself.
"""

from __future__ import annotations

import atexit
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable  # same interpreter that launched drive.py

# On Windows, start child processes without extra console windows.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# --------------------------------------------------------------------------
# What to run.  Each overlay = a Flask SERVER + an on-top WINDOW.
# Tune the window x/y/w/h to taste — the Quali Delta and Track Map windows
# are drag-anywhere, so you can also just grab them and move them, then
# copy the numbers you like back into here.
# --------------------------------------------------------------------------
OVERLAYS = [
    {
        "tag": "cornercues",
        "name": "Corner Cues",
        "server": "iracing_drivingline.py",
        "port": 5012,
        # its own dedicated Tkinter window (already click-through)
        "window": [PY, str(HERE / "driving_line_window.py")],
    },
    {
        "tag": "qualidelta",
        "name": "Quali Delta",
        "server": "iracing_qualidelta.py",
        "port": 5014,
        "window": [
            PY, str(HERE / "driving_overlay_window.py"),
            "--url", "http://localhost:5014/?debug=1",
            "--title", "Quali Delta",
            "--x", "40", "--y", "40", "--w", "380", "--h", "240",
        ],
    },
    {
        "tag": "trackmap",
        "name": "Track Map",
        "server": "iracing_trackmap.py",
        "port": 5007,
        "window": [
            PY, str(HERE / "driving_overlay_window.py"),
            "--url", "http://localhost:5007",
            "--title", "Track Map",
            "--x", "40", "--y", "320", "--w", "380", "--h", "380",
        ],
    },
]

# processes WE started, so we only tear down our own
_servers: list[subprocess.Popen] = []
_windows: list[subprocess.Popen] = []
_shutting_down = False


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex((host, port)) == 0


def wait_for_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            return True
        time.sleep(0.3)
    return False


def start_server(ov: dict) -> None:
    """Start an overlay's Flask server unless it's already up."""
    if port_open(ov["port"]):
        print(f"  • {ov['name']:<12} server already running on "
              f"{ov['port']} — reusing")
        return
    script = HERE / ov["server"]
    if not script.exists():
        print(f"  ! {ov['name']:<12} server script missing: {script.name}")
        return
    print(f"  • {ov['name']:<12} starting server ({ov['server']}, "
          f"port {ov['port']}) …")
    proc = subprocess.Popen(
        [PY, str(script)],
        cwd=str(HERE),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
    )
    _servers.append(proc)


def start_window(ov: dict) -> None:
    print(f"  • {ov['name']:<12} opening on-top window …")
    proc = subprocess.Popen(
        ov["window"],
        cwd=str(HERE),
        creationflags=_CREATE_NO_WINDOW,
    )
    _windows.append(proc)


def shutdown(*_args) -> None:
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    print("\nStopping driving mode …")
    for proc in _windows:
        _terminate(proc)
    for proc in _servers:
        _terminate(proc)
    print("All overlays stopped. You can close this window.")


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass


def main() -> int:
    print("=" * 60)
    print("  DRIVING MODE — Corner Cues · Quali Delta · Track Map")
    print("=" * 60)
    print("Reminder: iRacing must be in BORDERLESS WINDOWED mode.\n")

    atexit.register(shutdown)
    try:
        signal.signal(signal.SIGINT, lambda *_: shutdown())
        signal.signal(signal.SIGTERM, lambda *_: shutdown())
    except Exception:
        pass

    print("Starting servers:")
    for ov in OVERLAYS:
        start_server(ov)

    print("\nWaiting for servers to come up:")
    ready = []
    for ov in OVERLAYS:
        if wait_for_port(ov["port"]):
            print(f"  • {ov['name']:<12} ready on {ov['port']}")
            ready.append(ov)
        else:
            print(f"  ! {ov['name']:<12} did NOT come up on {ov['port']} "
                  "— skipping its window")

    if not ready:
        print("\nNo overlays came up. Is Python/Flask installed? "
              "Is another program using the ports?")
        shutdown()
        return 1

    print("\nOpening windows:")
    for ov in ready:
        start_window(ov)

    print("\n" + "-" * 60)
    print("  DRIVING MODE ACTIVE.")
    print("  Stop it by: closing this window, pressing Ctrl+C,")
    print("  or closing all overlay windows.")
    print("-" * 60)

    # Idle until the user stops us OR every overlay window is closed.
    try:
        while not _shutting_down:
            time.sleep(1.0)
            if _windows and all(p.poll() is not None for p in _windows):
                print("\nAll overlay windows closed.")
                break
    except KeyboardInterrupt:
        pass

    shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
