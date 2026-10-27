"""SUPP mode - this iCAS follows a MAIN iCAS (multi-steward, step 3).

A steward somewhere else runs iCASControl on his own PC, next to his own
iRacing (spectating the same session).  In follow mode his dashboard shows
the MAIN iCAS (log, timing, decisions, steward chat - everything), and every
steward action goes to the main iCAS under his name.  Only two things stay on
his own PC, because they act on HIS iRacing:

* replay of a log line (seek, camera, play),
* camera group / car.

So he can watch an incident in his own replay while the main PC's camera
(the stream!) does not move.

The connection is an ordinary steward login on the main iCAS (name + access
password, same as a browser).  URL: whatever the main steward hands out - the
tunnel link (https://….trycloudflare.com) or a LAN address.

Config: data/follow_config.json {"url", "name"} - the password is kept in
memory only (asked again after a restart).
"""

from __future__ import annotations

import asyncio
import collections
import json
import sys
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote, urlsplit

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_PATH = DATA_DIR / "follow_config.json"
# Actions this PC does itself while following; everything else goes to MAIN.
LOCAL_ACTIONS = ("follow", "tunnel", "camera", "replay")
RETRY_S = (2, 4, 8, 15, 30)


def load_config(path: Optional[Path] = None) -> dict:
    path = path or CONFIG_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        print(f"[follow] {path.name} unreadable: {exc}", file=sys.stderr)
        return {}


def save_config(cfg: dict, path: Optional[Path] = None) -> None:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    tmp.replace(path)


def ws_url(url: str, name: str, password: str) -> str:
    """'https://abc.trycloudflare.com' / '192.168.1.5:8080' / 'ws://…/ws'
    -> the main iCAS's websocket with the login.  ValueError if unusable."""
    url = (url or "").strip()
    if not url:
        raise ValueError("no address")
    if "://" not in url:
        url = "http://" + url
    parts = urlsplit(url)
    scheme = {"http": "ws", "ws": "ws", "https": "wss", "wss": "wss"}.get(
        parts.scheme.lower())
    if scheme is None or not parts.hostname:
        raise ValueError(f"not an iCAS address: {url}")
    host = parts.netloc.rsplit("@", 1)[-1]
    return (f"{scheme}://{host}/ws?name={quote(name or '')}"
            f"&pw={quote(password or '')}")


def is_self(url: str, own_port: int) -> bool:
    """The address points at this very iCAS (would follow itself)."""
    try:
        parts = urlsplit(url if "://" in url else "http://" + url)
        port = parts.port or (443 if parts.scheme in ("https", "wss") else 80)
    except ValueError:
        return False
    return (parts.hostname in ("localhost", "127.0.0.1", "::1")
            and port == own_port)


class Follower:
    """The connection to the main iCAS.  Runs as an asyncio task in the
    server's loop; ``connect`` = a websockets-style ``connect(url)`` (tests
    inject a fake)."""

    def __init__(self, connect: Optional[Callable] = None,
                 cfg: Optional[dict] = None) -> None:
        self.cfg = dict(load_config() if cfg is None else cfg)
        self._connect = connect
        self.password = ""
        self.state = "off"          # off | connecting | connected | refused | error
        self.info = ""
        self.as_name = ""           # our name on the main iCAS
        self.snapshot: dict = {}
        self.events: dict[int, dict] = {}
        self.cache: dict[str, dict] = {}    # init / track / events / stewards
        self.outbox: list[dict] = []        # for this iCAS's dashboards
        self.pending: collections.deque = collections.deque()
        self.since = 0.0
        self._ws = None
        self._task: Optional[asyncio.Task] = None
        self._lost = False          # just disconnected -> local data again

    # -- control --------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.state == "connected"

    def start(self, url: str, name: str, password: str) -> str:
        self.stop(quiet=True)
        self.cfg = {"url": url.strip(), "name": name}
        save_config(self.cfg)
        self.password = password
        self.state, self.info = "connecting", ""
        self._task = asyncio.ensure_future(self._run())
        return f"following {url} …"

    def stop(self, quiet: bool = False) -> str:
        was = self.state
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if was == "connected":
            self._lost = True
        self.state, self.info, self._ws = "off", "", None
        self.pending.clear()
        return "" if quiet else "not following any more - own iRacing data"

    def status(self) -> dict:
        return {"state": self.state, "info": self.info,
                "url": self.cfg.get("url", ""), "name": self.cfg.get("name", ""),
                "as": self.as_name,
                "since": round(time.time() - self.since) if self.active else 0}

    def take_lost(self) -> bool:
        """True once after the connection went away (resend own data)."""
        lost, self._lost = self._lost, False
        return lost

    # -- the connection ---------------------------------------------------------

    async def _run(self) -> None:
        tries = 0
        while True:
            try:
                url = ws_url(self.cfg.get("url", ""), self.cfg.get("name", ""),
                             self.password)
            except ValueError as exc:
                self.state, self.info = "error", str(exc)
                return
            try:
                connect = self._connect or _websockets_connect()
                async with connect(url) as ws:
                    self._ws = ws
                    tries = 0
                    async for raw in ws:
                        if self._receive(raw) == "refused":
                            return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.info = f"{type(exc).__name__}: {exc}"[:200]
            if self.state == "connected":
                self._lost = True
            self._ws = None
            self.pending.clear()
            self.state = "connecting"
            wait = RETRY_S[min(tries, len(RETRY_S) - 1)]
            tries += 1
            self.info = (self.info or "connection closed") + \
                f" - again in {wait}s"
            await asyncio.sleep(wait)

    def _receive(self, raw) -> str:
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return ""
        kind = msg.get("type")
        if kind == "login":
            self.state = "refused"
            self.info = "main iCAS: " + str(msg.get("error") or "refused")
            self._ws = None
            self._lost = True
            return "refused"
        if kind == "hello":
            self.as_name = str(msg.get("name", ""))
            self.state, self.info, self.since = "connected", "", time.time()
            self.events, self.cache = {}, {}
            self.outbox.append(self.hello())
            return ""
        if kind == "ack":
            target = self.pending.popleft() if self.pending else None
            self.outbox.append({**msg, "_to": target})
            return ""
        if kind == "snapshot":
            self.snapshot = msg
        elif kind == "events":
            self.events = {int(e.get("id", -1)): e for e in msg.get("events", [])}
        if kind in ("init", "track", "events", "stewards"):
            self.cache[kind] = msg
        if kind in ("init", "track", "events", "stewards", "snapshot"):
            self.outbox.append(msg)
        return ""

    def hello(self) -> dict:
        """What this iCAS's own dashboards are told about 'me'."""
        return {"type": "hello", "name": self.as_name, "place": "local",
                "token": "", "following": True}

    def initial_messages(self) -> list[dict]:
        out = [self.hello()]
        for k in ("init", "track", "events"):
            if k in self.cache:
                out.append(self.cache[k])
        if self.snapshot:
            out.append(self.snapshot)
        if "stewards" in self.cache:
            out.append(self.cache["stewards"])
        return out

    def drain(self) -> list[dict]:
        out, self.outbox = self.outbox, []
        return out

    async def send(self, msg: dict, reply_to=None) -> Optional[dict]:
        """A steward action to the main iCAS (its ack comes back later)."""
        ws = self._ws
        if ws is None or not self.active:
            return {"type": "ack", "text": "not connected to the main iCAS - "
                    "nothing sent"}
        self.pending.append(reply_to)
        try:
            await ws.send(json.dumps(msg))
        except Exception as exc:
            self.pending.pop()
            return {"type": "ack", "text": f"main iCAS: send failed ({exc})"}
        return None

    # -- what stays on this PC ----------------------------------------------------

    def cars(self) -> list[dict]:
        return list(self.snapshot.get("cars") or [])

    def event(self, ev_id: int) -> Optional[dict]:
        return self.events.get(int(ev_id))

    def session_mismatch(self, local_track: str, local_connected: bool) -> str:
        main = (self.snapshot.get("session") or {}).get("track", "")
        if local_connected and local_track and main and local_track != main:
            return (f"your iRacing shows {local_track}, the main iCAS "
                    f"{main} - replay / camera need the same session")
        return ""


def _websockets_connect():
    import websockets                      # requirements: websockets>=12
    return lambda url: websockets.connect(url, max_size=None, open_timeout=10,
                                          ping_interval=20, ping_timeout=20)
