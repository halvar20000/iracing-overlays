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
mode edit RC_ARGS below; for LAN access put {"host": "0.0.0.0"} into
racecontrol/data/server.json (kept across updates).
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


def _saved_args() -> list[str]:
    """racecontrol/data/server.json {"host": "0.0.0.0", "port": 8080} -
    survives updates (data/ is never overwritten); host 0.0.0.0 = other PCs
    in the home network can open the dashboard."""
    import json
    try:
        cfg = json.loads((RC_DIR / "data" / "server.json").read_text("utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    if isinstance(cfg, dict):
        if cfg.get("host"):
            out += ["--host", str(cfg["host"])]
        if cfg.get("port"):
            out += ["--port", str(int(cfg["port"]))]
    return out


def main() -> int:
    server = RC_DIR / "backend" / "server.py"
    if not server.is_file():
        print("ERROR: racecontrol/backend/server.py not found - is the "
              "racecontrol/ folder present next to this launcher?")
        return 1
    # Make `import backend...` resolve to racecontrol/backend, and run from there.
    sys.path.insert(0, str(RC_DIR))
    os.chdir(RC_DIR)
    sys.argv = ["backend.server", *RC_ARGS, *_saved_args()]
    runpy.run_module("backend.server", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
