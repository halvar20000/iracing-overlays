"""
iRacing Overlays - Race Control / Steward launcher shim
-------------------------------------------------------
Starts the iCASControl race-control / stewarding dashboard (FastAPI, port 8080)
so the overlay launchers (launch_gui.py / launch_all.py / launch_all.bat) can
manage it like any other overlay.

iCASControl lives in the `racecontrol/` subfolder and is normally started with
`python -m backend.server` from inside that folder. This shim runs it in-process
so the GUI launcher's start/stop and log capture work unchanged.

Open the dashboard at http://localhost:8080

By default it auto-detects iRacing and falls back to the simulator. To force a
mode or allow LAN access, edit RC_ARGS below.
"""
import os
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RC_DIR = HERE / "racecontrol"

# Default arguments passed to backend.server (empty = auto iRacing/sim).
# Examples: ["--host", "0.0.0.0"] to allow LAN access, or ["--sim"] to force sim.
RC_ARGS: list[str] = []


def main() -> int:
    server = RC_DIR / "backend" / "server.py"
    if not server.is_file():
        print("ERROR: racecontrol/backend/server.py not found - is the "
              "racecontrol/ folder present next to this launcher?")
        return 1
    # Make `import backend...` resolve to racecontrol/backend, and run from there.
    sys.path.insert(0, str(RC_DIR))
    os.chdir(RC_DIR)
    sys.argv = ["backend.server", *RC_ARGS]
    runpy.run_module("backend.server", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
