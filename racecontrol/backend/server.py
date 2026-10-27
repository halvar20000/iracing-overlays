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
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backend import APP_NAME, __version__
from backend import access, chat, follow, steward, stream, tunnel
from backend.race_state import RaceState, save_json
from backend.sources.base import DataSource
from backend.sources.simulator import SimulatorSource
from backend.sources.iracing_source import IRacingSource
from backend.sources.replay_source import ReplaySource

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
SNAPSHOT_HZ = 11            # how often the dashboard is refreshed
RECHECK_IRACING_EVERY = 5.0  # seconds between iRacing detection attempts
# Autosave: the race log, every steward decision and the incident baseline
# go to data/<SessionID-SubSessionID>.json, so a crash or restart of this PC
# mid-race picks the race up again (IEC Sebring 2026: the race-control PC
# crashed and every count came back doubled).
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AUTOSAVE_S = 10.0           # save at most this often when something changed
AUTOSAVE_MAX_AGE_S = 12 * 3600
LOCAL_MAX_AGE_S = 3 * 3600  # offline / test sessions (id 0-0): same track only


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
        # connected dashboards -> {"name", "place", "token"} (multi-steward)
        self.clients: dict[WebSocket, dict] = {}
        self.access_cfg = access.load_config()
        self.tokens: dict[str, str] = {}      # download token -> steward name
        self._stewards_sig = None
        self.source: DataSource = self._select_source()
        self._last_event_rev = -1
        self._last_track_rev = -1
        self._last_iracing_check = 0.0
        self._restored_key: Optional[str] = None
        self._saved_rev = -1
        self._saved_at = 0.0
        self.chat_cfg = chat.load_config()
        self.stream_cfg = stream.load_config()
        self.steward_cfg = steward.load_config()
        self.auto = steward.AutoSteward()
        # (laps, deadline) - !pacelaps for an auto caution once it is out
        self._pending_laps: Optional[tuple[int, float]] = None
        self._stream_lock = threading.Lock()
        self._stream_log: list[str] = []
        self.banner = stream.BannerOut(self._banner_done)
        # multi-steward: follow a main iCAS (SUPP) / internet access (tunnel)
        self.follower = follow.Follower()
        self.tunnel = tunnel.Tunnel()

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
            self._restored_key = None
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
            self.state.steward_rules = self.steward_cfg
            self.state.ingest(frame)
        if frame is not None and not self.follower.active:
            # following a main iCAS: its Auto Steward / autosave count, not
            # ours (own iRacing only for replay + camera)
            self._maybe_restore()
            for act in self.auto.check(self.state, self.steward_cfg):
                self._steward_action(act)
            self._maybe_caution_laps()
            self._maybe_autosave()
        # Surface any messages the source generated (auto cautions, etc.).
        drain = getattr(self.source, "drain_command_log", None)
        if callable(drain):
            for line in drain():
                self.state.note_system(line, category="flag")
        self._drain_stream_log()

    # -- Auto Steward (step 3c) ------------------------------------------------

    def _steward_action(self, act: dict) -> None:
        st = self.state
        kind = act["type"]
        if kind == "caution":
            who = ", ".join("#" + n for n in act["numbers"][:8])
            why = (f"{act['points']}x within {act['window']:.0f}s "
                   f"({act['cars']} cars: {who})")
            if act["mode"] == "auto":
                ev = st._log("flag", f"AUTO STEWARD: FULL COURSE CAUTION - {why}",
                             lap=st._leader_lap())
                ev.kind, ev.source = "auto_caution", "auto"
                result = self.to_iracing("pace_deploy", verify=("yellow", -1))
                st.note_system(f"Auto caution: {result}", category="flag")
                self._want_caution_laps()
            else:
                ev = st._log("flag", f"AUTO STEWARD: full course caution? - "
                             f"{why}", lap=st._leader_lap())
                ev.kind, ev.source = "auto_caution_ask", "auto"
        elif kind == "quali":
            text = act["text"]
            ev = st._log("message", f"AUTO STEWARD: RC message to all: {text}",
                         lap=st._leader_lap())
            ev.kind, ev.source = "auto_quali", "auto"
            self.to_iracing("message_all", msg=text)
            self.to_stream("message", "", text=text)
        elif kind in ("blue_warn", "fast_repair"):
            car = act["car"]
            what = ("blue flag for %.0fs" % act["held"] if kind == "blue_warn"
                    else f"{act['used']} fast repair(s) used")
            ev = st._log("flag", f"AUTO STEWARD: private to #{car.number} "
                         f"({what}): {act['text']}", car=car, lap=car.lap)
            ev.kind, ev.source = "auto_" + kind, "auto"
            if self.chat_cfg.get("enabled") and self._chat_capable():
                self.to_iracing("notify", car=car, msg=act["text"])
        elif kind == "blue_dt":
            car = act["car"]
            ev = st._log("penalty", f"AUTO STEWARD: DRIVE THROUGH #{car.number} "
                         f"{car.driver} - blue flag ignored for "
                         f"{act['held']:.0f}s", car=car, lap=car.lap)
            ev.kind, ev.source = "auto_blue_dt", "auto"
            result = self.to_iracing("drive_through", car=car,
                                     verify=CHAT_VERIFY_KIND["drive_through"])
            st.note_system(f"Blue flag drive through #{car.number}: {result}",
                           category="flag")
            self.to_iracing("message_all", msg=act["text"])
            self.to_stream("penalty", "DRIVE THROUGH", car=car,
                           text="Blue flag ignored")
        elif kind == "meatball":
            car = act["car"]
            ev = st._log("flag", f"AUTO STEWARD: RC message: {act['text']}",
                         car=car, lap=car.lap)
            ev.kind, ev.source = "auto_meatball", "auto"
            self.to_iracing("message_all", msg=act["text"])
        elif kind == "blue":
            slow, fast = act["car"], act["fast"]
            again = " (again)" if act["again"] else ""
            ev = st._log("flag", f"Blue flag{again}: let #{fast.number} "
                         f"{fast.driver} pass ({act['gap']:.1f}s behind)",
                         car=slow, lap=slow.lap)
            ev.kind, ev.source = "blue_flag", "auto"
            # only when the chat is on - an "OFF, not sent" line for every
            # blue flag would flood the log
            if self.chat_cfg.get("enabled") and self._chat_capable():
                self.to_iracing("notify", car=slow,
                                msg=f"BLUE FLAG - let #{fast.number} "
                                    f"{fast.driver} pass")

    def _want_caution_laps(self) -> None:
        """Remember the Rules' caution length for the caution just called;
        iRacing only takes !pacelaps once the caution is out."""
        laps = int(self.steward_cfg.get("fcy_laps") or 0)
        if laps > 0 and self.chat_cfg.get("enabled") and self._chat_capable():
            self._pending_laps = (laps, time.monotonic() + CAUTION_LAPS_WAIT_S)

    def _maybe_caution_laps(self) -> None:
        if self._pending_laps is None:
            return
        laps, deadline = self._pending_laps
        st = self.state
        if steward._caution_out(st) and not st.in_replay:
            self._pending_laps = None
            result = self.to_iracing("pace_laps", n=laps)
            st.note_system(f"Auto caution: {laps} caution lap(s) - {result}",
                           category="flag")
        elif time.monotonic() > deadline:
            self._pending_laps = None
            st.note_system(f"Auto caution: no caution seen within "
                           f"{CAUTION_LAPS_WAIT_S:.0f}s - caution laps "
                           f"({laps}) not set", category="flag")

    # -- autosave -----------------------------------------------------------

    def save_path(self) -> Optional[Path]:
        key = self.state.session_key
        if not key or self.source.name != "iracing":
            return None
        if key == "0-0":
            slug = re.sub(r"[^a-z0-9]+", "_", self.state.session_track.lower())
            key = f"local_{slug.strip('_')}"
        return DATA_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '_', key)}.json"

    def _maybe_restore(self) -> None:
        key = self.state.session_key
        if not key or key == self._restored_key or self.source.name != "iracing":
            return
        self._restored_key = key
        path = self.save_path()
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[autosave] {path.name} unreadable: {exc}", file=sys.stderr)
            return
        age = time.time() - float(data.get("saved_at", 0))
        limit = LOCAL_MAX_AGE_S if key == "0-0" else AUTOSAVE_MAX_AGE_S
        if age > limit or (key == "0-0" and
                           data.get("track") != self.state.session_track):
            return
        n = self.state.load_saved(data)
        off = data.get("official") or {}
        official = getattr(self.source, "official", None)
        if official is not None and off.get("session") is not None:
            official.seed(int(off["session"]), off.get("totals") or {})
        self.state.note_system(
            f"Restored the race from the autosave ({n} log lines, "
            f"saved {age / 60:.0f} min ago)")
        print(f"[autosave] restored {n} lines from {path.name}")
        self._saved_rev = self.state.event_revision

    def _maybe_autosave(self, force: bool = False) -> None:
        path = self.save_path()
        if path is None:
            return
        now = time.monotonic()
        if not force and (self.state.event_revision == self._saved_rev
                          or now - self._saved_at < AUTOSAVE_S):
            return
        data = self.state.to_saved()
        official = getattr(self.source, "official", None)
        if official is not None:
            data["official"] = {"session": official.session,
                                "totals": official.totals}
        try:
            save_json(str(path), data)
            self._saved_rev = self.state.event_revision
            self._saved_at = now
        except Exception as exc:
            print(f"[autosave] failed: {exc}", file=sys.stderr)

    # -- chat commands into iRacing (step 2) ----------------------------------

    def _chat_capable(self) -> bool:
        return callable(getattr(self.source, "send_chat", None))

    def chat_status(self) -> dict:
        return {"enabled": bool(self.chat_cfg.get("enabled")),
                "method": self.chat_cfg.get("method", "post"),
                "available": self._chat_capable()}

    def to_iracing(self, kind: str, car=None, msg: str = "", sec: float = 0,
                   verify=None, force: bool = False, n="") -> str:
        """Build the chat command for ``kind`` and type it into iRacing.
        ``verify`` = flag kind to watch for the given car (car_idx added
        here).  Off / no iRacing / no such command -> only logged."""
        text = chat.build(kind, self.chat_cfg.get("commands") or {},
                          num=car.number if car else "",
                          driver=car.driver if car else "", msg=msg, sec=sec,
                          n=n)
        st = self.state
        if text is None:
            st.note_system(f"{_command_label(kind)}: no iRacing command - "
                           f"logged only", category="flag")
            return "logged only (iRacing has no command for it)"
        if not self._chat_capable():
            st.note_system(f"Would send to iRacing: {text} "
                           f"({self.source.name} mode)", category="flag")
            return f"not iRacing ({self.source.name}): {text}"
        if not (self.chat_cfg.get("enabled") or force):
            st.note_system(f"Chat to iRacing is OFF - not sent: {text}",
                           category="flag")
            return f"chat OFF - not sent: {text}"
        if isinstance(verify, str):
            verify = (verify, car.car_idx if car else -1)
        return self.source.send_chat(
            text, self.chat_cfg.get("method", "post"),
            float(self.chat_cfg.get("char_delay_ms", 6) or 6), verify)

    # -- decisions onto the stream (step 3) -----------------------------------

    def steward_status(self) -> dict:
        return dict(self.steward_cfg)

    def stream_status(self) -> dict:
        return {"enabled": bool(self.stream_cfg.get("enabled")),
                "banner_url": self.stream_cfg.get("banner_url", ""),
                "kinds": dict(self.stream_cfg.get("kinds") or {})}

    def to_stream(self, kind: str, headline: str, car=None, text: str = "",
                  force: bool = False, wait: bool = False) -> str:
        """One message for the Race Control banner (5019)."""
        cfg = self.stream_cfg
        if not force:
            if not cfg.get("enabled"):
                return "stream OFF"
            if not (cfg.get("kinds") or {}).get(kind, True):
                return f"{kind} not sent to the stream (setting)"
        url = stream.clean_url(cfg.get("banner_url", ""))
        if not url:
            return "no banner address set"
        body = {"kind": kind, "headline": headline, "text": text,
                "car": car.number if car is not None else "",
                "driver": car.driver if car is not None else "",
                "source": "icas"}
        self.banner.send(url, body, wait=wait)
        return "to the stream banner"

    def _banner_done(self, ok: bool, info: str, body: dict) -> None:
        what = (body.get("headline") or body.get("text") or "")[:60]
        num = f" #{body['car']}" if body.get("car") else ""
        line = (f"On the stream banner: {what}{num}" if ok else
                f"Stream banner NOT reached ({info}): {what}{num} - is the "
                f"Race Control overlay (5019) running at "
                f"{self.stream_cfg.get('banner_url')}?")
        with self._stream_lock:
            self._stream_log.append(line)

    def _drain_stream_log(self) -> None:
        with self._stream_lock:
            lines, self._stream_log = self._stream_log, []
        for line in lines:
            self.state.note_system(line, category="flag")

    # -- client messaging ---------------------------------------------------

    # -- cameras ----------------------------------------------------------------

    def camera(self, msg: dict) -> str:
        """Camera group / car on THIS PC's iRacing (the main iCAS)."""
        op = str(msg.get("op", ""))
        info = self.camera_info() or {}
        if op == "group":
            return self.source.send_command(
                "cam_group", group=int(msg.get("group", 0)))
        cars = self._camera_cars()
        if op in ("next", "prev"):
            order = sorted((c for c in cars if c[1] and c[3]),
                           key=lambda c: c[2] or 999)
            if not order:
                return "no cars"
            idx = [c[0] for c in order]
            cur = info.get("car_idx", -1)
            i = idx.index(cur) if cur in idx else -1
            i = (i + (1 if op == "next" else -1)) % len(order)
            return self.source.send_command("cam_car", number=order[i][1])
        if op == "car":
            want = int(msg.get("car_idx", -1))
            number = next((c[1] for c in cars if c[0] == want), "")
            if not number:
                return "no such car"
            return self.source.send_command("cam_car", number=number)
        return f"camera: unknown '{op}'"

    def _camera_cars(self) -> list[tuple]:
        """(car_idx, number, position, running) - from the main iCAS while
        following (CarIdx is the same on every PC of one iRacing session)."""
        if self.follower.active:
            return [(int(c.get("car_idx", -1)), str(c.get("number", "")),
                     int(c.get("position") or 0), bool(c.get("in_world", True)))
                    for c in self.follower.cars()]
        fc = self.state.frame_cars
        return [(c.car_idx, c.number, c.position,
                 not fc or c.car_idx in fc) for c in self.state.cars.values()]

    def camera_info(self) -> Optional[dict]:
        get = getattr(self.source, "camera_info", None)
        try:
            info = get() if callable(get) else None
        except Exception:
            return None
        if not info:
            return None
        return {k: v for k, v in info.items() if k != "raw_groups"}

    # -- follow a main iCAS / internet access (multi-steward 3 + 4) -------------

    def net_status(self) -> dict:
        fol = self.follower.status()
        if self.follower.active:
            fol["warning"] = self.follower.session_mismatch(
                self.state.session_track, self.state.connected)
        return {"follow": fol, "tunnel": self.tunnel.status(),
                "password": access.has_password(self.access_cfg)}

    def follow_action(self, msg: dict, place: str, who: str) -> str:
        if place != "local":
            return "follow mode can only be set on this iCAS's own PC"
        op = str(msg.get("op", ""))
        if op == "stop":
            text = self.follower.stop()
            self.state.note_system("Follow mode off - own data again")
            return text
        if op == "start":
            url = str(msg.get("url", "")).strip()
            try:
                follow.ws_url(url, "", "")
            except ValueError as exc:
                return str(exc)
            if follow.is_self(url, self.tunnel.port):
                return "that is this iCAS itself - enter the MAIN iCAS's address"
            name = access.clean_name(msg.get("name") or who, "Steward")
            self.state.note_system(f"Following the main iCAS at {url} as {name}")
            return self.follower.start(url, name, str(msg.get("password", "")))
        return f"follow: unknown '{op}'"

    def tunnel_action(self, msg: dict, place: str) -> str:
        if place != "local":
            return "internet access can only be switched on the main iCAS PC"
        op = str(msg.get("op", ""))
        if op == "start":
            if not access.has_password(self.access_cfg):
                return ("set an access password first (STEWARDS > Access "
                        "password) - without one nobody may come in over the "
                        "internet")
            if self.follower.active:
                return "this iCAS follows another one - no internet access here"
            text = self.tunnel.start()
            self.state.note_system(f"Internet access: {text}")
            return text
        if op == "stop":
            text = self.tunnel.stop()
            self.state.note_system(f"Internet access: {text}")
            return text
        if op == "download":
            return self.tunnel.download()
        return f"tunnel: unknown '{op}'"

    def snapshot_message(self) -> dict:
        return {"type": "snapshot", **self.state.snapshot(),
                "running": self.running, "chat": self.chat_status(),
                "stream": self.stream_status(),
                "steward": self.steward_status(),
                "camera": self.camera_info(), "net": self.net_status()}

    def follow_outgoing(self) -> list[tuple[Optional[WebSocket], str]]:
        """Messages from the main iCAS for our dashboards: (only to, text);
        snapshots carry OUR camera + net status."""
        out = []
        for msg in self.follower.drain():
            to = msg.pop("_to", None)
            if msg.get("type") == "snapshot":
                msg = {**msg, "camera": self.camera_info(),
                       "net": self.net_status()}
            out.append((to, json.dumps(msg)))
        return out

    # -- stewards (multi-steward) ----------------------------------------------

    def stewards_payload(self, chat_limit: int = 150) -> dict:
        return {"type": "stewards",
                "online": sorted(({"name": c["name"], "place": c["place"]}
                                  for c in self.clients.values()),
                                 key=lambda c: c["name"].lower()),
                "chat": self.state.steward_chat[-chat_limit:],
                "password": access.has_password(self.access_cfg)}

    def stewards_signature(self) -> tuple:
        return (tuple(sorted((c["name"], c["place"])
                             for c in self.clients.values())),
                self.state.chat_revision,
                access.has_password(self.access_cfg))

    def initial_messages(self) -> list[dict]:
        return [
            {"type": "init", "app": APP_NAME, "version": __version__,
             "source": self.source.name, "running": self.running},
            {"type": "track", **self.state.track_payload()},
            {"type": "events", **self.state.events_payload()},
            self.snapshot_message(),
            self.stewards_payload(),
        ]

    def handle_client_message(self, msg: dict, who: str = "",
                              place: str = "local") -> dict | None:
        """Process a message from the dashboard (``who`` = the steward, for
        the log).  Returns an optional ack."""
        self.state.actor = who
        try:
            return self._handle(msg, who, place)
        finally:
            self.state.actor = ""

    def _handle(self, msg: dict, who: str, place: str) -> dict | None:
        action = msg.get("action")
        st = self.state

        if action == "steward_chat":
            line = st.chat(str(msg.get("text", "")))
            return None if line else {"type": "ack", "text": "empty message"}

        if action == "claim":
            return {"type": "ack", "text": st.claim(int(msg.get("id", -1)),
                                                    bool(msg.get("on", True)))}

        if action == "set_password":
            if place != "local":
                return {"type": "ack", "text": "the access password can only "
                        "be changed on the main iCAS PC itself"}
            pw = str(msg.get("password", ""))
            if pw and len(pw) < 6:
                return {"type": "ack", "text": "password: at least 6 characters"}
            self.access_cfg = access.make_config(pw)
            access.save_config(self.access_cfg)
            if not pw and self.tunnel.status()["state"] != "off":
                self.tunnel.stop()
                st.note_system("Internet access off - no password any more")
            st.note_system("Steward access password " +
                           ("set" if pw else "removed - only this PC and the "
                            "home network can connect"))
            return {"type": "ack", "text": "access password " +
                    ("saved" if pw else "removed")}

        if action == "camera":
            return {"type": "ack", "text": self.camera(msg)}

        if action == "follow":
            return {"type": "ack", "text": self.follow_action(msg, place, who)}

        if action == "tunnel":
            return {"type": "ack", "text": self.tunnel_action(msg, place)}

        if action == "set_running":
            if place != "local":
                return {"type": "ack", "text": "RUN / STOP only on the main "
                        "iCAS PC itself"}
            self.running = bool(msg.get("running", True))
            st.note_system(f"Controller set to {'RUN' if self.running else 'STOP'}")
            return {"type": "ack", "text": f"{'RUN' if self.running else 'STOP'}"}

        if action == "set_chat":
            self.chat_cfg["enabled"] = bool(msg.get("enabled", False))
            if msg.get("method") in ("post", "focus"):
                self.chat_cfg["method"] = msg["method"]
            chat.save_config(self.chat_cfg)
            st.note_system(
                f"Chat to iRacing {'ON' if self.chat_cfg['enabled'] else 'OFF'}"
                f" (method {self.chat_cfg.get('method', 'post')})")
            return {"type": "ack", "text": "chat to iRacing "
                    + ("ON" if self.chat_cfg["enabled"] else "OFF")}

        if action == "set_stream":
            if "enabled" in msg:
                self.stream_cfg["enabled"] = bool(msg.get("enabled"))
            if "banner_url" in msg:
                self.stream_cfg["banner_url"] = stream.clean_url(
                    msg.get("banner_url"))
            if isinstance(msg.get("kinds"), dict):
                for k, v in msg["kinds"].items():
                    if k in stream.KINDS:
                        self.stream_cfg.setdefault("kinds", {})[k] = bool(v)
            stream.save_config(self.stream_cfg)
            st.note_system(
                f"Decisions to the stream banner "
                f"{'ON' if self.stream_cfg['enabled'] else 'OFF'} "
                f"({self.stream_cfg.get('banner_url')})")
            return {"type": "ack", "text": "stream banner "
                    + ("ON" if self.stream_cfg["enabled"] else "OFF")}

        if action == "set_steward":
            cfg = dict(self.steward_cfg)
            for k in steward.DEFAULT_CONFIG:
                if k in msg:
                    cfg[k] = msg[k]
            self.steward_cfg = steward.clean(cfg)
            steward.save_config(self.steward_cfg)
            c = self.steward_cfg
            st.note_system(
                f"Auto Steward {'ON' if c['enabled'] else 'OFF'}: incidents "
                f"{steward.describe(c)}; caution "
                + (f"{c['fcy_incs']}x / {c['fcy_window_s']}s "
                   f"({c['fcy_mode']}, not in the last "
                   f"{c['fcy_end_protect_min']} min)" if c["fcy_enabled"] else "off")
                + "; quali AM/PRO "
                + (f"{c['quali_split_min']} min" if c["quali_split"] else "off")
                + f"; blue flag warn {c['bf_warn_s'] or 'off'}s / DT "
                f"{c['bf_dt_s'] or 'off'}s; fast repair {c['fr_used'] or 'off'}; "
                f"meatball {'on' if c['meatball_enabled'] else 'off'}"
                + ("; own blue hint " + f"{c['blue_gap_s']:.1f}s"
                   if c["blue_enabled"] else ""))
            return {"type": "ack", "text": "Auto Steward "
                    + ("ON" if c["enabled"] else "OFF")}

        if action == "stream_test":
            return {"type": "ack", "text": self.to_stream(
                "message", "", text="TEST · iCASControl", force=True)}

        if action == "chat_test":
            return {"type": "ack", "text": self.to_iracing("test", force=True)}

        if action == "command":
            command = str(msg.get("command", ""))
            if command == "pace_laps":
                n = str(msg.get("n", "")).strip()
                if not re.fullmatch(r"[+-]?\d{1,2}", n) or n.lstrip("+-") == "0":
                    return {"type": "ack", "text": f"caution laps '{n}'?"}
                result = self.to_iracing("pace_laps", n=n)
                if not steward._caution_out(st):
                    result += " (iRacing only accepts this under a caution)"
                st.note_system(f"Caution laps {n}: {result}", category="flag")
                return {"type": "ack", "text": result}
            if command in CHAT_RACE_COMMANDS and self._chat_capable():
                result = self.to_iracing(
                    command, verify=("yellow", -1)
                    if command == "pace_deploy" else None)
                if command == "pace_deploy" and msg.get("auto"):
                    self._want_caution_laps()
                return {"type": "ack", "text": result}
            result = self.source.send_command(command, **msg.get("params", {}))
            st.note_system(f"{_command_label(command)}: {result}", category="flag")
            return {"type": "ack", "text": result}

        if action == "resolve":
            ev_id = int(msg.get("id", -1))
            resolution = str(msg.get("resolution", ""))
            seconds = float(msg.get("seconds", 0.0))
            message = str(msg.get("message", ""))
            ev = next((e for e in st.events if e.id == ev_id), None)
            result = st.resolve_incident(ev_id, resolution, message, seconds)
            kind = RESOLUTION_CHAT.get(resolution)
            car = st.cars.get(ev.car_idx) if ev is not None else None
            if kind and car is not None and "->" in result:
                result += " | " + self.to_iracing(
                    kind, car=car, msg=message, sec=seconds,
                    verify=CHAT_VERIFY_KIND.get(kind))
                if kind in REASON_LINE and message:
                    result += " | " + self.to_iracing(
                        "penalty_reason", car=car,
                        msg=f"{REASON_LINE[kind]} - {message}")
            elif (resolution == "TIME PENALTY" and car is not None
                    and "->" in result and seconds):
                result += " | " + self.to_iracing(
                    "penalty_reason", car=car,
                    msg=f"TIME PENALTY +{seconds:.0f}s"
                    + (f" - {message}" if message else ""))
            banner = stream.RESOLUTION_BANNER.get(resolution)
            if banner and car is not None and "->" in result:
                kind, head = banner
                if resolution == "TIME PENALTY":
                    head = stream.time_headline(seconds)
                text = message
                if kind in ("investigation", "nfa"):
                    other = st.cars.get(getattr(ev, "other_car_idx", -1))
                    if other is not None:
                        text = (f"#{car.number} and #{other.number}"
                                + (f" - {message}" if message else ""))
                result += " | " + self.to_stream(kind, head, car=car, text=text)
            return {"type": "ack", "text": result}

        if action == "replay" and self.follower.active:
            # a line of the MAIN iCAS's log, replayed in OUR iRacing
            ev = self.follower.event(int(msg.get("id", -1)))
            if ev is None:
                return {"type": "ack", "text": "event not found"}
            return {"type": "ack", "text": self.source.send_command(
                "replay_event", session_num=int(ev.get("session_num") or 0),
                sim_time=float(ev.get("sim_time") or 0),
                number=str(ev.get("car_number") or ""),
                preroll=float(msg.get("preroll", 8) or 8))}

        if action == "replay":
            ev = next((e for e in st.events
                       if e.id == int(msg.get("id", -1))), None)
            if ev is None:
                return {"type": "ack", "text": "event not found"}
            car = st.cars.get(ev.car_idx)
            result = self.source.send_command(
                "replay_event", session_num=ev.session_num,
                sim_time=ev.sim_time, number=car.number if car else "",
                preroll=float(msg.get("preroll", 8) or 8))
            return {"type": "ack", "text": result}

        if action == "car":
            car_idx = int(msg.get("car_idx", -1))
            command = str(msg.get("command", ""))
            result = st.car_action(car_idx, command,
                                   str(msg.get("message", "")),
                                   float(msg.get("seconds", 0.0)))
            car = st.cars.get(car_idx)
            reason = str(msg.get("message", ""))
            if command in CAR_CHAT and car is not None:
                kind = CAR_CHAT[command]
                if command == "time_penalty":
                    secs = float(msg.get("seconds", 0.0))
                    reason = f"+{secs:.0f}s" + (f" - {reason}" if reason else "")
                if command in REASON_LINE:
                    line = REASON_LINE[command] + (
                        f" {reason}" if command == "time_penalty"
                        else (f" - {reason}" if reason else ""))
                    if kind != "penalty_reason":
                        result += " | " + self.to_iracing(
                            kind, car=car, verify=CHAT_VERIFY_KIND.get(kind))
                    if reason or command == "time_penalty":
                        result += " | " + self.to_iracing(
                            "penalty_reason", car=car, msg=line)
                else:
                    result += " | " + self.to_iracing(
                        kind, car=car, msg=reason,
                        verify=CHAT_VERIFY_KIND.get(kind))
            banner = stream.CAR_BANNER.get(command)
            if banner and car is not None and result != "car not found":
                kind, head = banner
                if command == "time_penalty":
                    head = stream.time_headline(float(msg.get("seconds", 0.0)))
                result += " | " + self.to_stream(
                    kind, head, car=car, text=str(msg.get("message", "")))
            return {"type": "ack", "text": result}

        if action == "rc_message":
            target = str(msg.get("target", "RC"))
            text = str(msg.get("text", ""))
            car = st.cars.get(int(msg.get("car_idx", -1)))
            result = st.rc_message(target, text, int(msg.get("car_idx", -1)))
            if target == "RC":
                result += " | " + self.to_iracing("message_all", msg=text)
                result += " | " + self.to_stream("message", "", text=text)
            elif target == "PRIVATE" and car is not None:
                result += " | " + self.to_iracing("notify", car=car, msg=text)
            return {"type": "ack", "text": result}

        return {"type": "ack", "text": f"unknown action '{action}'"}


# How long an auto caution may take to show before its !pacelaps is dropped.
CAUTION_LAPS_WAIT_S = 30.0
# Race-control buttons that are iRacing chat commands.
CHAT_RACE_COMMANDS = ("pace_deploy", "pace_end", "pit_open", "pit_close",
                      "clear_all", "red_flag", "green_flag", "wave_lapped")
# Steward decisions on an incident -> chat command kind.
RESOLUTION_CHAT = {"DRIVE THROUGH": "drive_through", "STOP/GO": "stop_go",
                   "DSQ": "dsq", "WARNING": "warning"}
# Selected-car buttons -> chat command kind.
CAR_CHAT = {"wave_around": "wave_around", "eol": "eol",
            "clear_penalties": "clear_penalties", "dsq": "dsq",
            "notify": "notify", "drive_through": "drive_through",
            "stop_go": "stop_go", "add_admin": "add_admin",
            "remove_admin": "remove_admin", "chat_on": "chat_on",
            "chat_off": "chat_off", "remove": "remove",
            "time_penalty": "penalty_reason"}
# Penalties whose command takes no message: the reason goes out as
# "/all #14 Driver: DRIVE THROUGH - Track Limits" right after it.
REASON_LINE = {"drive_through": "DRIVE THROUGH", "stop_go": "STOP AND GO",
               "time_penalty": "TIME PENALTY"}
# How the result shows in the telemetry (IRacingSource._chat_effect).
CHAT_VERIFY_KIND = {"drive_through": "black", "stop_go": "black", "dsq": "dq",
                    "eol": "eol", "wave_around": "waveby",
                    "clear_penalties": "clear"}


def _command_label(command: str) -> str:
    return {
        "pace_deploy": "Pace car deploy", "pace_end": "Pace car end",
        "pace_laps": "Caution laps",
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
    engine._maybe_autosave(force=True)
    engine.follower.stop(quiet=True)
    engine.tunnel.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title=APP_NAME, lifespan=lifespan)


@app.middleware("http")
async def _api_guard(request, call_next):
    """/api/* (race data, the save download) from outside this PC only with a
    logged-in steward's token (?t=) - the page itself is no secret."""
    if request.url.path.startswith("/api/") and _place(request) != "local":
        token = request.query_params.get("t", "")
        open_lan = (_place(request) == "lan"
                    and not access.has_password(engine.access_cfg))
        if not open_lan and token not in engine.tokens:
            return JSONResponse({"error": "log in on the dashboard first"},
                                status_code=401)
    return await call_next(request)


@app.get("/api/status")
async def status() -> JSONResponse:
    return JSONResponse({
        "app": APP_NAME, "version": __version__,
        "source": engine.source.name, "running": engine.running,
        "connected": engine.state.connected,
        "clients": len(engine.clients),
        "sector_timing": getattr(engine.source, "sector_status", None),
    })


@app.get("/api/incidents/debug")
async def incidents_debug() -> JSONResponse:
    """The raw incident counts iRacing sends this PC (ResultsPositions).
    All 0 while iRaceControl on another PC shows points = this PC's iRacing
    account is not admin of the session."""
    return JSONResponse({"source": engine.source.name,
                         **(getattr(engine.source, "inc_debug", None) or {})})


@app.get("/api/save")
async def download_save() -> Response:
    """The whole race (log + decisions) as JSON - for the stewards' files."""
    data = engine.state.to_saved()
    name = (engine.save_path() or Path("icascontrol_race.json")).name
    return Response(json.dumps(data, ensure_ascii=False, indent=1),
                    media_type="application/json",
                    headers={"Content-Disposition":
                             f'attachment; filename="{name}"'})


def _place(conn) -> str:
    host = conn.client.host if conn.client else ""
    return access.where(host, {k.lower(): v for k, v in conn.headers.items()})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """One dashboard.  ``?name=&pw=``: who it is; everybody but this PC needs
    the access password (multi-steward)."""
    await ws.accept()
    place = _place(ws)
    ok, why = access.admit(engine.access_cfg, place, ws.query_params.get("pw", ""))
    if not ok:
        await ws.send_text(json.dumps({"type": "login", "error": why,
                                       "place": place}))
        await ws.close(code=4401)
        return
    name = access.clean_name(ws.query_params.get("name", ""),
                             "Host" if place == "local" else "Steward")
    taken = {c["name"] for c in engine.clients.values()}
    base, n = name, 2
    while name in taken:                 # two "Thomas" -> "Thomas 2"
        name = f"{base[:access.MAX_NAME - 3]} {n}"
        n += 1
    token = access.new_token()
    engine.tokens[token] = name
    engine.clients[ws] = {"name": name, "place": place, "token": token}
    try:
        await ws.send_text(json.dumps({"type": "hello", "name": name,
                                       "place": place, "token": token}))
        for message in engine.initial_messages():
            await ws.send_text(json.dumps(message))
        if engine.follower.active and place == "local":
            for message in engine.follower.initial_messages():
                if message.get("type") == "snapshot":   # OUR camera + net
                    message = {**message, "camera": engine.camera_info(),
                               "net": engine.net_status()}
                await ws.send_text(json.dumps(message))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if (engine.follower.active and place == "local"
                    and msg.get("action") not in follow.LOCAL_ACTIONS):
                ack = await engine.follower.send(msg, reply_to=ws)
            else:
                ack = engine.handle_client_message(msg, name, place)
            if ack is not None:
                await ws.send_text(json.dumps(ack))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        engine.clients.pop(ws, None)
        engine.tokens.pop(token, None)


async def _race_loop() -> None:
    """Background task: drive the simulation/poll and broadcast to clients."""
    interval = 1.0 / SNAPSHOT_HZ
    while True:
        start = time.monotonic()
        engine.tick()

        if engine.follower.active:
            await _send_following()
            elapsed = time.monotonic() - start
            await asyncio.sleep(max(0.0, interval - elapsed))
            continue
        engine.follower.drain()
        if engine.follower.take_lost():
            # back to our own data: resend everything, our own names
            engine._last_event_rev = engine._last_track_rev = -1
            engine._stewards_sig = None
            for client, info in list(engine.clients.items()):
                with contextlib.suppress(Exception):
                    await client.send_text(json.dumps(
                        {"type": "hello", "name": info["name"],
                         "place": info["place"], "token": info["token"]}))

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
        messages.append(json.dumps(engine.snapshot_message()))
        # Who is online + the steward chat, when that changed.
        sig = engine.stewards_signature()
        if sig != engine._stewards_sig:
            engine._stewards_sig = sig
            messages.append(json.dumps(engine.stewards_payload()))

        dead: list[WebSocket] = []
        for client in list(engine.clients):
            try:
                for m in messages:
                    await client.send_text(m)
            except Exception:
                dead.append(client)
        for d in dead:
            engine.clients.pop(d, None)

        elapsed = time.monotonic() - start
        await asyncio.sleep(max(0.0, interval - elapsed))


async def _send_following() -> None:
    """Following a main iCAS: pass its messages on to our dashboards."""
    out = engine.follow_outgoing()
    dead: list[WebSocket] = []
    for client in list(engine.clients):
        if engine.clients[client]["place"] != "local":
            continue                     # only this PC's own browser follows
        try:
            for to, text in out:
                if to is None or to is client:
                    await client.send_text(text)
        except Exception:
            dead.append(client)
    for d in dead:
        engine.clients.pop(d, None)


# Static front-end - mounted last so /ws and /api take precedence.
class _NoCacheStatic(StaticFiles):
    """The dashboard files are always fetched fresh - after an update the
    browser kept the old app.js (no sector columns, 2026-10-27)."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp


if FRONTEND_DIR.is_dir():
    app.mount("/", _NoCacheStatic(directory=str(FRONTEND_DIR), html=True),
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
    engine.tunnel.port = args.port

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
