"""
iRacing Overlay Launcher
------------------------
Starts all iRacing overlay scripts as subprocesses from one terminal.

!!! MAINTENANCE RULE !!!
When you add a new iracing_*.py overlay to this folder, you MUST also:
  1. Add it to the SCRIPTS list below
  2. Add it to the OVERLAYS list in launch_gui.py
  3. Add a `start "..." cmd /k python ...` line to launch_all.bat
See CLAUDE.md in this folder for details.

Usage:
    python launch_all.py

Each script's stdout is prefixed with a short colored tag so you can tell
who is logging what. Press Ctrl+C once to shut all of them down cleanly.

Scripts launched (and their default ports):
    dashboard   iracing_dashboard.py       http://localhost:5000
    grid        iracing_grid.py            http://localhost:5001
    results     iracing_results.py         http://localhost:5002
    lite        iracing_results_lite.py    http://localhost:5003
    live        iracing_live_indicator.py  http://localhost:5004
"""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Config — (tag, script filename, port, ANSI color code)
# ---------------------------------------------------------------------------
SCRIPTS = [
    ("dashboard", "iracing_dashboard.py",      5000, "\033[95m"),  # magenta
    ("grid",      "iracing_grid.py",           5001, "\033[96m"),  # cyan
    ("results",   "iracing_results.py",        5002, "\033[93m"),  # yellow
    ("lite",      "iracing_results_lite.py",   5003, "\033[92m"),  # green
    ("live",      "iracing_live_indicator.py", 5004, "\033[91m"),  # red
    ("standings", "iracing_standings.py",      5005, "\033[33m"),  # orange/yellow
    ("livery",    "iracing_livery.py",         5006, "\033[35m"),  # magenta/violet
    ("trackmap",  "iracing_trackmap.py",       5007, "\033[92m"),  # bright green
    ("flag",      "flag_overlay.py",           5008, "\033[94m"),  # bright blue
    ("logger",    "iracing_race_logger.py",    5009, "\033[33m"),  # amber
]
RESET = "\033[0m"

HERE = Path(__file__).resolve().parent


def stream_reader(tag: str, color: str, stream) -> None:
    """Read a subprocess stream line-by-line and print with a colored tag."""
    prefix = f"{color}[{tag:>9}]{RESET} "
    try:
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                print(prefix + line, flush=True)
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def main() -> int:
    # Enable ANSI colors on Windows 10+ terminals
    if os.name == "nt":
        os.system("")

    # Sanity check — all scripts present?
    missing = [s for _, s, _, _ in SCRIPTS if not (HERE / s).exists()]
    if missing:
        print(f"ERROR: missing script(s) next to launcher: {', '.join(missing)}")
        return 1

    print("Launching iRacing overlays from:", HERE)
    print()
    for tag, script, port, _ in SCRIPTS:
        print(f"  {tag:<10} http://localhost:{port}  ({script})")
    print()
    print("Press Ctrl+C to stop all overlays.\n")

    processes = []
    threads = []

    # On Windows, CREATE_NEW_PROCESS_GROUP lets us send CTRL_BREAK_EVENT
    popen_kwargs = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(HERE),
        bufsize=0,
    )
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    for tag, script, _, color in SCRIPTS:
        try:
            p = subprocess.Popen(
                [sys.executable, "-u", str(HERE / script)],
                **popen_kwargs,
            )
        except Exception as e:
            print(f"ERROR starting {script}: {e}")
            continue
        processes.append((tag, color, p))

        t = threading.Thread(
            target=stream_reader,
            args=(tag, color, p.stdout),
            daemon=True,
        )
        t.start()
        threads.append(t)

    if not processes:
        print("Nothing started.")
        return 1

    # Wait until user Ctrl+C or all children exit on their own
    try:
        while any(p.poll() is None for _, _, p in processes):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping all overlays...")

    # Shutdown — try graceful, then force
    for tag, _, p in processes:
        if p.poll() is None:
            try:
                if os.name == "nt":
                    p.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    p.terminate()
            except Exception:
                pass

    deadline = time.time() + 5.0
    for tag, _, p in processes:
        remaining = max(0.1, deadline - time.time())
        try:
            p.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            try:
                p.kill()
            except Exception:
                pass

    print("All overlays stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
