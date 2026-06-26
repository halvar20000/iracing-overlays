"""iCASControl - application server.

Runs the race loop, streams live race state to the browser dashboard over a
WebSocket, and serves the front-end.  Start it with::

    python -m backend.server                 # auto-detect iRacing, else simulator
    python -m backend.server --sim            # force the simulator
    python -m backend.server --port 8090      # use a different port

Then open  http://localhost:8080  in a browser.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import APP_NAME, __version__
from backend.race_state import RaceState
from backend.sources.base import DataSource
from backend.sources.simulator import SimulatorSource
from backend.sources.iracing_source import IRacingSource
from backend.sources.replay_source import ReplaySource

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
SNAPSHOT_HZ = 11            # how often the dashboard is refreshed
RECHECK_IRACING_EVERY = 5.0  # seconds between iRacing detection attempts


class Engine:
    """Owns the data source, the race state and all connected clients."""

    def __init__(self, force_sim: bool = False,
                 replay_path: Optional[str] = None,
                 replay_speed: float = 1.0) -> None:
        self.force_sim = force_sim
        self.replay_path = replay_path
        self.replay_speed = replay_speed
        self.state = RaceState()
        self.running = True                       # the RUN / STOP toggle
        self.clients: set[WebSocket] = set()
        self.source: DataSource = self._select_source()
        self._last_event_rev = -1
        self._last_track_rev = -1
        self._last_iracing_check = 0.0

    # -- source management --------------------------------------------------

    def _select_source(self) -> DataSource:
        if self.replay_path:
            rep = ReplaySource(self.replay_path, self.replay_speed)
            if rep.connect():
                print(f"[engine] REPLAY mode - {self.replay_path}")
                return rep
            print("[engine] Replay log could not be loaded - falling back.")
        if not self.force_sim and IRacingSource.available():
            ir = IRacingSource()
            if ir.connect():
                print("[engine] iRacing detected - using LIVE iRacing data.")
                return ir
        if not self.force_sim and not IRacingSource.available():
            print("[engine] pyirsdk not available on this platform.")
        sim = SimulatorSource()
        sim.connect()
        mode = "forced" if self.force_sim else "iRacing not detected"
        print(f"[engine] Running in SIMULATOR mode ({mode}).")
        return sim

    def maybe_upgrade_to_iracing(self) -> None:
        """If we're on the simulator but iRacing appears, switch to it live."""
        if self.force_sim or self.source.name in ("iracing", "replay"):
            return
        if not IRacingSource.available():
            return
        now = time.monotonic()
        if now - self._last_iracing_check < RECHECK_IRACING_EVERY:
            return
        self._last_iracing_check = now
        ir = IRacingSource()
        if ir.connect():
            print("[engine] iRacing now detected - switching to LIVE data.")
            with contextlib.suppress(Exception):
                self.source.disconnect()
            self.source = ir
            self.state = RaceState()           # fresh state for the real race
            self.state.note_system("Switched to live iRacing data")

    # -- the race loop ------------------------------------------------------

    def tick(self) -> None:
        if not self.running:
            return
        self.maybe_upgrade_to_iracing()
        try:
            frame = self.source.poll()
        except Exception as exc:                  # never let the loop die
            print(f"[engine] source poll error: {exc}", file=sys.stderr)
            frame = None
        if frame is not None:
            self.state.ingest(frame)
        # Surface any messages the source generated (auto cautions, etc.).
        drain = getattr(self.source, "drain_command_log", None)
        if callable(drain):
            for line in drain():
                self.state.note_system(line, category="flag")

    # -- client messaging ---------------------------------------------------

    def initial_messages(self) -> list[dict]:
        return [
            {"type": "init", "app": APP_NAME, "version": __version__,
             "source": self.source.name, "running": self.running},
            {"type": "track", **self.state.track_payload()},
            {"type": "events", **self.state.events_payload()},
            {"type": "snapshot", **self.state.snapshot(),
             "running": self.running},
        ]

    def handle_client_message(self, msg: dict) -> dict | None:
        """Process a message from the dashboard. Returns an optional ack."""
        action = msg.get("action")
        st = self.state

        if action == "set_running":
            self.running = bool(msg.get("running", True))
            st.note_system(f"Controller set to {'RUN' if self.running else 'STOP'}")
            return {"type": "ack", "text": f"{'RUN' if self.running else 'STOP'}"}

        if action == "command":
            command = str(msg.get("command", ""))
            result = self.source.send_command(command, **msg.get("params", {}))
            st.note_system(f"{_command_label(command)}: {result}", category="flag")
            return {"type": "ack", "text": result}

        if action == "resolve":
            result = st.resolve_incident(
                int(msg.get("id", -1)), str(msg.get("resolution", "")),
                str(msg.get("message", "")), float(msg.get("seconds", 0.0)))
            return {"type": "ack", "text": result}

        if action == "car":
            car_idx = int(msg.get("car_idx", -1))
            command = str(msg.get("command", ""))
            result = st.car_action(car_idx, command,
                                   str(msg.get("message", "")),
                                   float(msg.get("seconds", 0.0)))
            # Mirror controllable commands to the data source as well.
            if command in ("wave_around", "eol", "notify", "clear_penalties"):
                car = st.cars.get(car_idx)
                if car is not None:
                    self.source.send_command(command, number=car.number)
            return {"type": "ack", "text": result}

        if action == "rc_message":
            result = st.rc_message(str(msg.get("target", "RC")),
                                   str(msg.get("text", "")),
                                   int(msg.get("car_idx", -1)))
            return {"type": "ack", "text": result}

        return {"type": "ack", "text": f"unknown action '{action}'"}


def _command_label(command: str) -> str:
    return {
        "pace_deploy": "Pace car deploy", "pace_end": "Pace car end",
        "pit_open": "Pit entry open", "pit_close": "Pit entry close",
        "race_start": "Race start", "race_hold": "Race start hold",
        "red_flag": "Red flag", "green_flag": "Green flag",
        "clear_all": "Clear all penalties", "wave_lapped": "Wave lapped cars",
    }.get(command, command)


# --------------------------------------------------------------------------
# FastAPI application
# --------------------------------------------------------------------------

def _argv_value(flag: str, default=None):
    """Read the value following a flag in sys.argv (e.g. --replay foo.jsonl)."""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


engine = Engine(
    force_sim="--sim" in sys.argv,
    replay_path=_argv_value("--replay"),
    replay_speed=float(_argv_value("--replay-speed", "1.0") or "1.0"),
)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the background race loop for the lifetime of the server."""
    task = asyncio.create_task(_race_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title=APP_NAME, lifespan=lifespan)


@app.get("/api/status")
async def status() -> JSONResponse:
    return JSONResponse({
        "app": APP_NAME, "version": __version__,
        "source": engine.source.name, "running": engine.running,
        "connected": engine.state.connected,
        "clients": len(engine.clients),
    })


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    engine.clients.add(ws)
    try:
        for message in engine.initial_messages():
            await ws.send_text(json.dumps(message))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ack = engine.handle_client_message(msg)
            if ack is not None:
                await ws.send_text(json.dumps(ack))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        engine.clients.discard(ws)


async def _race_loop() -> None:
    """Background task: drive the simulation/poll and broadcast to clients."""
    interval = 1.0 / SNAPSHOT_HZ
    while True:
        start = time.monotonic()
        engine.tick()

        messages: list[str] = []
        # Event log only when it changed.
        if engine.state.event_revision != engine._last_event_rev:
            engine._last_event_rev = engine.state.event_revision
            messages.append(json.dumps(
                {"type": "events", **engine.state.events_payload()}))
        # Track geometry only when it changed.
        if engine.state.track_revision != engine._last_track_rev:
            engine._last_track_rev = engine.state.track_revision
            messages.append(json.dumps(
                {"type": "track", **engine.state.track_payload()}))
        # The fast snapshot, every tick.
        messages.append(json.dumps(
            {"type": "snapshot", **engine.state.snapshot(),
             "running": engine.running}))

        dead: list[WebSocket] = []
        for client in list(engine.clients):
            try:
                for m in messages:
                    await client.send_text(m)
            except Exception:
                dead.append(client)
        for d in dead:
            engine.clients.discard(d)

        elapsed = time.monotonic() - start
        await asyncio.sleep(max(0.0, interval - elapsed))


# Static front-end - mounted last so /ws and /api take precedence.
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True),
              name="frontend")


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} server")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (use 0.0.0.0 to allow LAN access)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--sim", action="store_true",
                        help="force simulator mode even if iRacing is running")
    parser.add_argument("--replay", metavar="LOG.jsonl",
                        help="play back a recorded race log instead of going live")
    parser.add_argument("--replay-speed", type=float, default=1.0,
                        help="replay speed multiplier (e.g. 4 = 4x faster)")
    args, _ = parser.parse_known_args()

    import uvicorn
    mode = ("REPLAY" if args.replay else
            "SIMULATOR" if args.sim else "auto (iRacing / simulator)")
    print("=" * 60)
    print(f"  {APP_NAME}  v{__version__}   [{mode}]")
    print(f"  Open  http://localhost:{args.port}  in your browser")
    print("=" * 60)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
