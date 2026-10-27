"""
iRacing "Race Control" banner overlay (port 5019)
-------------------------------------------------
An F1-style violet RACE CONTROL banner for OBS, fed from two sources:

1. iRacing itself (always works, no setup):
     - full course yellow / safety car in this lap / green flag / red flag
       (SessionFlags; race sessions only)
     - per car: black flag (penalty), warning (furled black), meatball
       (repair), disqualified (CarIdxSessionFlags), end of line, wave
       around, free pass (CarIdxPaceFlags) — with car number, team (team
       events) or driver name and the class colour of the tower / trackmap.
   iRacing does NOT say which penalty a black flag is (drive-through,
   stop & go …) and the SDK has no chat — the text of the stewards'
   messages has to come from the race control software:

2. iRaceControl (iracecontrol.com) via its ATVO integration: iRaceControl
   sends race control messages, incidents under review and penalties to an
   "ATVO Race Control Server". This overlay IS such a server (default port
   5020, rc_config.json "atvo_port"; optional "atvo_password" = the
   password entered in iRaceControl, empty = any). In iRaceControl's
   settings: ATVO Enabled, server address = this PC, port 5020.
   The link turned out to be ASP.NET SignalR 2 (hub "RaceControlHub", seen
   2026-10-26) — AtvoReceiver is a minimal SignalR server. The hub method
   names are not documented, so every request is written to
   logs/atvo_capture_<date>.log and the text is pulled out of the hub
   arguments tolerantly. Check /panel during the first test.

Pages:
    /          the banner (OBS browser source, transparent; ?secs= ?zoom=
               ?debug=1 / H, ?demo=1, ?flags=0 hides the iRacing flags,
               ?atvo=0 hides the iRaceControl messages)
    /panel     operator page: ATVO status + raw capture, message log, a
               field to send a message by hand, test button
    /rc/state  JSON for both

Requirements:  pip install pyirsdk flask
Run:           python iracing_rc_banner.py
"""

import datetime
import json
import os
import re
import socket
import threading
import time
import urllib.parse
import uuid
from collections import deque

from flask import Flask, jsonify, request

from iracing_sdk_base import SDKPoller, setup_utf8_stdout
from class_colors import class_colors, text_on
setup_utf8_stdout()

PORT = 5019
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "rc_config.json")
LOG_DIR = os.path.join(HERE, "logs")
DEFAULT_CONFIG = {"atvo_port": 5020, "atvo_enabled": True}

SESSION_STATE_RACING = 4

# irsdk Flags
F_GREEN, F_RED = 0x0004, 0x0010
F_ONE_TO_GREEN = 0x0200
F_CAUTION, F_CAUTION_WAVING = 0x4000, 0x8000
F_BLACK, F_DQ, F_FURLED, F_REPAIR = 0x10000, 0x20000, 0x80000, 0x100000
# irsdk PaceFlags
P_EOL, P_FREE_PASS, P_WAVED = 0x01, 0x02, 0x04

CAR_FLAGS = [   # (bit, kind, headline)
    (F_DQ,     "dq",      "DISQUALIFIED"),
    (F_BLACK,  "penalty", "BLACK FLAG · PENALTY"),
    (F_FURLED, "warning", "WARNING"),
    (F_REPAIR, "repair",  "MEATBALL FLAG · REPAIR"),
]
PACE_FLAGS = [
    (P_EOL,       "pace", "END OF LINE"),
    (P_WAVED,     "pace", "WAVE AROUND"),
    (P_FREE_PASS, "pace", "FREE PASS"),
]
CAR_REPEAT_S = 30.0       # same flag for the same car: once per 30 s
STEWARD_SOURCES = ("atvo", "icas")   # steward software: iRaceControl, our iCASControl
STEWARD_KINDS = ("message", "warning", "penalty", "dq", "investigation", "nfa", "clear")
STEWARD_COVERS_S = 20.0   # iRaceControl message about a car hides iRacing's flag for it


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


# =============================================================================
# Message feed (shared by both sources)
# =============================================================================
class Feed:
    def __init__(self, maxlen: int = 60):
        self._lock = threading.Lock()
        self._msgs = deque(maxlen=maxlen)
        self._id = 0

    def add(self, source: str, kind: str, headline: str, text: str = "",
            car: dict | None = None, secs: float | None = None) -> dict | None:
        with self._lock:
            if source == "iracing" and car and self._steward_said(car.get("num")):
                print(f"[rc] iracing: {headline} #{car.get('num')} skipped (iRaceControl message)")
                return None
            self._id += 1
            m = {"id": self._id, "t": time.time(), "source": source,
                 "kind": kind, "headline": headline, "text": text, "car": car,
                 "secs": secs}
            self._msgs.append(m)
        print(f"[rc] {source}: {headline} {text} "
              f"{('#' + car['num'] + ' ' + car['label']) if car else ''}".rstrip())
        return m

    def _steward_said(self, num) -> bool:
        """iRaceControl announced something about car #num in the last
        STEWARD_COVERS_S — its text says more than iRacing's black flag."""
        now = time.time()
        for m in reversed(self._msgs):
            if now - m["t"] > STEWARD_COVERS_S:
                break
            if m["source"] in STEWARD_SOURCES and m.get("car") and m["car"].get("num") == num:
                return True
        return False

    def list(self) -> list:
        with self._lock:
            return list(self._msgs)


# =============================================================================
# iRacing flags -> messages (pure: arrays in, messages out — unit-testable)
# =============================================================================
class FlagWatcher:
    """Edge detector. The first tick of a session only records the state
    (flags already flying when the overlay starts are not news)."""

    def __init__(self, feed: Feed, warnings: bool = False):
        # warnings: iRacing's furled black flag (cutting / track limits
        # warning, no reason given) — off by default since 2026-10-26: AI cars
        # got four in three minutes, a full field would flood the stream.
        # rc_config.json "iracing_warnings": true brings them back.
        self.feed = feed
        self.warnings = warnings
        self.reset()

    def reset(self):
        self._primed = False
        self._prev_sess = 0
        self._prev_car: dict = {}
        self._prev_pace: dict = {}
        self._caution = False
        self._last_car: dict = {}

    def tick(self, is_race: bool, racing: bool, sess_flags: int,
             car_flags: list, pace_flags: list, cars: dict, now: float):
        """cars: car_idx -> {"num", "label", "color", "text"} (real cars)."""
        sf = int(sess_flags or 0)
        if not self._primed:
            self._primed = True
            self._prev_sess = sf
            self._caution = bool(sf & (F_CAUTION | F_CAUTION_WAVING))
            for i in cars:
                self._prev_car[i] = int(car_flags[i]) if i < len(car_flags) else 0
                self._prev_pace[i] = int(pace_flags[i]) if i < len(pace_flags) else 0
            return

        # ---- session: caution / restart / red (race only) --------------
        if is_race:
            rise = sf & ~self._prev_sess
            cau = bool(sf & (F_CAUTION | F_CAUTION_WAVING))
            if cau and not self._caution:
                self.feed.add("iracing", "caution", "FULL COURSE YELLOW")
            elif self._caution and rise & F_ONE_TO_GREEN:
                self.feed.add("iracing", "caution", "SAFETY CAR IN THIS LAP")
            elif self._caution and not cau and not (sf & F_ONE_TO_GREEN) and racing:
                self.feed.add("iracing", "green", "GREEN FLAG", "Racing resumes")
            if rise & F_RED:
                self.feed.add("iracing", "red", "RED FLAG", "Session suspended")
            # one-to-green keeps the caution phase open until the green
            if cau:
                self._caution = True
            elif self._caution and not (sf & F_ONE_TO_GREEN) and racing:
                self._caution = False
        self._prev_sess = sf

        # ---- per car ----------------------------------------------------
        for i, car in cars.items():
            cf = int(car_flags[i]) if i < len(car_flags) else 0
            pf = int(pace_flags[i]) if i < len(pace_flags) else 0
            rise_c = cf & ~self._prev_car.get(i, cf)
            rise_p = pf & ~self._prev_pace.get(i, pf)
            self._prev_car[i], self._prev_pace[i] = cf, pf
            for bit, kind, head in CAR_FLAGS:
                if bit == F_FURLED and not self.warnings:
                    continue
                if rise_c & bit:
                    self._car_msg(i, car, kind, head, now)
                    if bit in (F_DQ, F_BLACK):
                        break       # DQ also sets black; one line is enough
            if is_race:
                for bit, kind, head in PACE_FLAGS:
                    if rise_p & bit:
                        self._car_msg(i, car, kind, head, now)

    def _car_msg(self, i, car, kind, head, now):
        key = (i, head)
        if now - self._last_car.get(key, -1e9) < CAR_REPEAT_S:
            return
        self._last_car[key] = now
        self.feed.add("iracing", kind, head, "", {k: v for k, v in car.items() if k != "uid"})


# =============================================================================
# iRaceControl -> "ATVO Race Control Server" (TCP)
# =============================================================================
_PRINTABLE = re.compile(r"[ -~ -ɏ]{4,}")
TEXT_KEYS = ("message", "msg", "text", "body", "content", "description",
             "Message", "Text", "Body", "Content", "Description")
HEAD_KEYS = ("headline", "title", "header", "Headline", "Title", "Header")


def _from_json(obj):
    """(headline, text) from a decoded JSON value, or None."""
    if isinstance(obj, str):
        return ("", obj)
    if isinstance(obj, list):
        for x in obj:
            r = _from_json(x)
            if r:
                return r
        return None
    if not isinstance(obj, dict):
        return None
    head = next((str(obj[k]) for k in HEAD_KEYS if isinstance(obj.get(k), str)), "")
    text = next((str(obj[k]) for k in TEXT_KEYS if isinstance(obj.get(k), str)), "")
    if text or head:
        return (head, text)
    for v in obj.values():             # nested {"data": {"message": …}}
        if isinstance(v, (dict, list)):
            r = _from_json(v)
            if r:
                return r
    return None


def _looks_utf16(b: bytes) -> bool:
    """.NET programs often send UTF-16: every other byte NUL."""
    if len(b) < 4:
        return False
    odd = b[1::2]
    return odd.count(0) >= len(odd) * 0.6


def extract_text(chunk: bytes):
    """Best guess at the human text in one received chunk.
    Returns (headline, text, how) or None (nothing readable)."""
    if _looks_utf16(chunk):
        t = chunk[: len(chunk) // 2 * 2].decode("utf-16-le", "replace")
        chunk = t.encode("utf-8")
    raw = chunk.strip(b"\x00\r\n\t ")
    if not raw:
        return None
    for enc in ("utf-8", "latin-1"):
        try:
            s = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        s = s.strip("\x00﻿ \r\n")
        # JSON
        if s[:1] in "{[":
            try:
                r = _from_json(json.loads(s))
                if r:
                    return (r[0].strip(), r[1].strip(), "json")
            except ValueError:
                pass
        # XML-ish
        if s.startswith("<") and ">" in s:
            t = re.sub(r"<[^>]+>", " ", s)
            t = re.sub(r"\s+", " ", t).strip()
            if t:
                return ("", t, "xml")
        bad = sum(1 for c in s if ord(c) < 32 and c not in "\r\n\t")
        if bad <= len(s) * 0.05:
            t = re.sub(r"\s+", " ", s).strip()
            if t:
                return ("", t, "text")
        break
    # binary with embedded strings (e.g. length-prefixed fields)
    runs = [m.group(0).strip() for m in _PRINTABLE.finditer(raw.decode("latin-1"))]
    runs = [r for r in runs if r]
    if runs:
        return ("", " · ".join(runs), "strings")
    return None


def readable(text: str) -> bool:
    """Worth putting on stream: a few letters, not a token / password."""
    return len(text) >= 3 and sum(c.isalpha() for c in text) >= 3


# iRaceControl's "ATVO" link is ASP.NET SignalR 2.x (seen 2026-10-26 on the
# stream PC: GET /signalr/signalr/negotiate?clientProtocol=2.1&connectionData=
# [{"Name":"RaceControlHub"}]&password=, User-Agent SignalR.Client.NetStandard
# 2.4.3). So the receiver is a minimal SignalR 2 SERVER: negotiate, connect
# (serverSentEvents or longPolling — no websockets offered), start, send,
# poll, ping, reconnect, abort. Hub calls arrive by POST /send as
# data={"H": hub, "M": method, "A": [args], "I": id}; the method names and
# args are not documented, so every request goes raw into the capture log and
# the text is pulled out of the args tolerantly.
SR_PROTOCOL = "2.1"
SR_KEEPALIVE_S = 10.0       # SSE keep-alive (client times out after 20 s)
SR_POLL_S = 20.0            # longPolling: hold a poll this long
SR_ACTIONS = ("negotiate", "connect", "reconnect", "start", "send",
              "poll", "ping", "abort")


def _strings(x, out):
    if isinstance(x, str):
        if x.strip():
            out.append(x.strip())
    elif isinstance(x, list):
        for v in x:
            _strings(v, out)
    elif isinstance(x, dict):
        for v in x.values():
            _strings(v, out)
    return out


# Hub methods iRaceControl calls that want a RESULT (the .NET client awaits
# Invoke<T>): GetVersion, then GetAllIncidents right after connecting
# (2026-10-26). A null result for GetAllIncidents CRASHED iRaceControl — so
# every "GetAll…" call gets an empty list, never null.
HUB_RESULTS = {"getversion": "1.0.0"}


def hub_result(method: str):
    """(True, result) for a hub method that must return something."""
    m = method.lower()
    if m in HUB_RESULTS:
        return True, HUB_RESULTS[m]
    if m.startswith("getall") or (m.startswith("get") and m.endswith("s")):
        return True, []
    return False, None


def form_data(body: str) -> str:
    """The `data` field of a SignalR POST body (form-encoded), else the body."""
    if body.startswith("data="):
        return urllib.parse.unquote_plus(body[5:].split("&", 1)[0])
    q = urllib.parse.parse_qs(body)
    return q["data"][0] if q.get("data") else body


def hub_text(method: str, args):
    """(headline, text) from one hub invocation, or None."""
    for a in args if isinstance(args, list) else [args]:
        if isinstance(a, (dict, list)):
            r = _from_json(a)
            if r and (r[1] or r[0]):
                return (r[0].strip(), r[1].strip())
    ss = [s for s in _strings(args, []) if readable(s)]
    if not ss:
        return None
    if len(ss) >= 2 and len(ss[0]) <= 40 and ss[0].upper() == ss[0]:
        return (ss[0], " · ".join(ss[1:]))     # ("PENALTY", "#12 drive through")
    return ("", " · ".join(ss))


# RaceControlHub.SendMessage({EntryId, IdType, SessionName, Type, Header,
# Text, DisplayTime "00:00:10", Source "iRaceControl"}) — seen 2026-10-26.
# Type = the ATVO target chosen in iRaceControl's CACS routing, in the order
# of its settings window: INFO, WARNING, PENALTY, INVESTIGATION, NO FURTHER
# ACTION, CLEAR PENALTY.
ATVO_TYPES = {1: "message", 2: "warning", 3: "penalty", 4: "investigation",
              5: "nfa", 6: "clear"}
ATVO_DUP_S = 3.0          # same text again within 3 s = one message routed to several targets
ATVO_GATHER_S = 0.5       # copies of one message arrive within ms — wait this long for them
# iRaceControl sends EntryId "" even for car penalties; the number is in the
# text: "DRIVE THROUGH PENALTY FOR #07 (CRO) - …", "#14 (KAE) HAVE BEEN DISQUALIFIED"
CAR_IN_TEXT = re.compile(r"#\s?(\d{1,3})\b")
TEXT_KINDS = [   # (kind, words) — only used when Type cannot tell (several targets)
    ("penalty", ("PENALTY", "DISQUALIF", "BLACK FLAG", "STOP & GO", "STOP AND GO",
                 "STOP/GO", "DRIVE THROUGH", "DRIVE TROUGH", "DRIVE-THROUGH")),
    ("warning", ("WARNING", "WARNED")),
    ("investigation", ("INVESTIGAT", "UNDER REVIEW", "NOTED")),
]


def text_kind(text: str) -> str:
    t = text.upper()
    for kind, words in TEXT_KINDS:
        if any(w in t for w in words):
            return kind
    return "message"


def parse_span(v) -> float | None:
    """'00:00:10' / '00:00:07.5' / 10 -> seconds."""
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    try:
        parts = [float(x) for x in str(v).split(":")]
    except ValueError:
        return None
    secs = 0.0
    for x in parts:
        secs = secs * 60 + x
    return secs if secs > 0 else None


def hub_kind(method: str, head: str) -> str:
    s = f"{method} {head}".lower()
    for k in ("penalty", "warning"):
        if k in s:
            return k
    return "message"


class AtvoReceiver:
    """Minimal ASP.NET SignalR 2 server for iRaceControl's ATVO link."""

    def __init__(self, feed: Feed, port: int, log_dir: str = LOG_DIR,
                 password: str = ""):
        self.feed, self.port, self.log_dir = feed, port, log_dir
        self.password = password or ""
        self.listening = False
        self.error = ""
        self.clients: dict = {}          # token -> {"addr", "since", "seen", "bytes", "transport"}
        self.raw = deque(maxlen=40)      # recent requests for /panel
        self._lock = threading.Lock()
        self._server = None
        self._closed: set = set()        # aborted tokens (end SSE / polls)
        self.car_lookup = None           # EntryId -> car dict (set by the app)
        self._recent: dict = {}          # (text, entry) -> time, for ATVO_DUP_S
        self._pending: dict = {}         # (text, entry) -> copies being gathered
        self.gather_s = ATVO_GATHER_S
        self.app = self._make_app()

    def start(self):
        threading.Thread(target=self._serve, daemon=True).start()

    def stop(self):
        try:
            if self._server:
                self._server.shutdown()
        except Exception:
            pass

    def _serve(self):
        from werkzeug.serving import make_server
        try:
            self._server = make_server("0.0.0.0", self.port, self.app, threaded=True)
        except (OSError, SystemExit) as e:
            self.error = f"cannot listen on TCP {self.port}: {e}"
            print(f"[rc] {self.error}")
            return
        self.listening = True
        print(f"[rc] ATVO (SignalR) receiver listening on TCP {self.port}")
        self._server.serve_forever()

    # ---- capture ---------------------------------------------------------
    def _log(self, addr: str, what: str, **kw):
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            day = datetime.date.today().isoformat()
            with open(os.path.join(self.log_dir, f"atvo_capture_{day}.log"), "a",
                      encoding="utf-8") as f:
                f.write(json.dumps({
                    "t": datetime.datetime.now().isoformat(timespec="milliseconds"),
                    "from": addr, "event": what, **kw}, ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"[rc] capture log failed: {e}")

    def _raw(self, addr, how, text, parsed=None, shown=False):
        b = text.encode("utf-8", "replace")
        with self._lock:
            self.raw.append({"t": time.time(), "from": addr, "len": len(b),
                             "hex": b[:200].hex(), "text": text[:600],
                             "parsed": parsed, "how": how, "shown": shown})

    def _touch(self, token, addr, transport=None, nbytes=0):
        if not token:
            return
        with self._lock:
            c = self.clients.setdefault(token, {"addr": addr, "since": time.time(),
                                                "bytes": 0, "transport": ""})
            c["seen"] = time.time()
            c["addr"] = addr
            c["bytes"] += nbytes
            if transport:
                c["transport"] = transport

    # ---- hub calls -------------------------------------------------------
    def handle_send(self, addr: str, data: str):
        try:
            msg = json.loads(data)
        except ValueError:
            msg = None
        if isinstance(msg, dict) and "M" in msg:
            method, args = str(msg.get("M") or ""), msg.get("A") or []
            m0 = args[0] if isinstance(args, list) and args and isinstance(args[0], dict) else None
            if method.lower() == "sendmessage" and m0 is not None and ("Text" in m0 or "Header" in m0):
                shown = self._send_message(m0)
                self._raw(addr, f"hub {msg.get('H', '')}.{method}",
                          json.dumps(args, ensure_ascii=False),
                          f"{m0.get('Header') or ''} | {m0.get('Text') or ''}", shown)
                return {"I": str(msg.get("I", "0"))}
            r = hub_text(method, args)
            shown = False
            if r:
                head, text = r
                self.feed.add("atvo", hub_kind(method, head), head, text or head)
                shown = True
            self._raw(addr, f"hub {msg.get('H', '')}.{method}",
                      json.dumps(args, ensure_ascii=False),
                      (r[0] + " | " + r[1]) if r and r[0] else (r[1] if r else None), shown)
            out = {"I": str(msg.get("I", "0"))}
            has, res = hub_result(method)
            if has:
                out["R"] = res
            return out
        r = extract_text(data.encode("utf-8"))      # plain PersistentConnection
        shown = bool(r and readable(r[1] or r[0]))
        if shown:
            self.feed.add("atvo", "message", r[0], r[1] or r[0])
        self._raw(addr, "send", data, (r[1] if r else None), shown)
        return {}

    def _send_message(self, m: dict) -> bool:
        """Copies of one message (routed to several ATVO targets in CACS) come
        within milliseconds: gather them ATVO_GATHER_S, then show ONE banner."""
        head = str(m.get("Header") or "").strip()
        text = str(m.get("Text") or "").strip()
        if not readable(text or head):
            return False
        entry = str(m.get("EntryId") or "").strip()
        key = (text.lower(), entry)
        try:
            typ = int(m.get("Type") or 0)
        except (TypeError, ValueError):
            typ = 0
        now = time.time()
        with self._lock:
            self._recent = {k: t for k, t in self._recent.items() if now - t < ATVO_DUP_S}
            p = self._pending.get(key)
            if p is not None:
                p["types"].setdefault(typ, head)
                return False
            if key in self._recent:
                return False
            self._recent[key] = now
            self._pending[key] = {"types": {typ: head}, "text": text, "entry": entry,
                                  "secs": parse_span(m.get("DisplayTime"))}
        if self.gather_s > 0:
            t = threading.Timer(self.gather_s, self._flush, args=(key,))
            t.daemon = True
            t.start()
        else:
            self._flush(key)
        return True

    def _flush(self, key):
        with self._lock:
            p = self._pending.pop(key, None)
        if not p:
            return
        types, text = p["types"], p["text"]
        if len(types) == 1:
            typ, head = next(iter(types.items()))
            kind = ATVO_TYPES.get(typ) or hub_kind("", head) or text_kind(text)
        else:                       # routed to several targets: Type says nothing
            kind = text_kind(text)
            want = {"penalty": 3, "warning": 2, "investigation": 4}.get(kind, 1)
            head = types.get(want) or types.get(1) or next(iter(types.values()))
        car = None
        if self.car_lookup:
            for e in [p["entry"]] + CAR_IN_TEXT.findall(text)[:1]:
                if not e:
                    continue
                try:
                    car = self.car_lookup(e)
                except Exception:
                    car = None
                if car:
                    break
        self.feed.add("atvo", kind, head, text or head, car, p["secs"])

    # ---- HTTP ------------------------------------------------------------
    def _make_app(self):
        from flask import Flask as _F, Response
        sr = _F("atvo_signalr")
        me = self

        def _json(obj, code=200):
            return Response(json.dumps(obj), status=code, mimetype="application/json",
                            headers={"Cache-Control": "no-cache"})

        @sr.route("/", defaults={"path": ""}, methods=["GET", "POST"])
        @sr.route("/<path:path>", methods=["GET", "POST"])
        def any_path(path):
            addr = f"{request.remote_addr}:{request.environ.get('REMOTE_PORT', '')}"
            q = request.args
            body = request.get_data(as_text=True) if request.method == "POST" else ""
            data = form_data(body)
            me._log(addr, "http", method=request.method, path="/" + path,
                    query=request.query_string.decode("latin-1"), body=body[:4000],
                    data=data[:4000],
                    ua=request.headers.get("User-Agent", ""))
            action = path.rstrip("/").rsplit("/", 1)[-1].lower()
            if action not in SR_ACTIONS:
                me._raw(addr, "http ?", f"{request.method} /{path}")
                return _json({"error": "not a SignalR request"}, 404)
            if me.password and q.get("password", "") != me.password:
                me._raw(addr, "refused", f"{action}: wrong password")
                return _json({"error": "wrong password"}, 403)
            token = q.get("connectionToken", "")
            transport = q.get("transport", "")
            if action == "negotiate":
                cid = uuid.uuid4().hex
                base = "/" + path.rstrip("/").rsplit("/", 1)[0]
                me._raw(addr, "negotiate", f"connectionData={q.get('connectionData', '')}")
                return _json({"Url": base, "ConnectionToken": cid, "ConnectionId": cid,
                              "KeepAliveTimeout": 20.0, "DisconnectTimeout": 30.0,
                              "ConnectionTimeout": 110.0, "TryWebSockets": False,
                              "ProtocolVersion": SR_PROTOCOL,
                              "TransportConnectTimeout": 5.0, "LongPollDelay": 0.0})
            me._touch(token, addr, transport, len(body))
            if action in ("connect", "reconnect"):
                me._raw(addr, action, f"transport={transport}")
                with me._lock:
                    me._closed.discard(token)
                if transport == "serverSentEvents":
                    return Response(me._sse(token, init=(action == "connect")),
                                    mimetype="text/event-stream",
                                    headers={"Cache-Control": "no-cache"})
                if action == "connect":          # longPolling: init at once
                    return _json({"C": "s-0,0", "S": 1, "M": []})
                return _json({"C": "s-0,0", "M": []})
            if action == "start":
                return _json({"Response": "started"})
            if action == "ping":
                return _json({"Response": "pong"})
            if action == "poll":
                end = time.time() + SR_POLL_S
                while time.time() < end and token not in me._closed:
                    time.sleep(0.5)
                return _json({"C": "s-0,0", "M": []})
            if action == "abort":
                with me._lock:
                    me._closed.add(token)
                    me.clients.pop(token, None)
                me._raw(addr, "abort", "client disconnected")
                return _json({})
            # send
            return _json(me.handle_send(addr, data))
        return sr

    def _sse(self, token, init=True):
        yield "data: initialized\n\n"
        if init:
            yield 'data: {"C":"d-0,0","S":1,"M":[]}\n\n'
        last = time.time()
        while token not in self._closed:
            time.sleep(0.5)
            if time.time() - last >= SR_KEEPALIVE_S:
                last = time.time()
                self._touch(token, self.clients.get(token, {}).get("addr", ""))
                yield "data: {}\n\n"

    def status(self) -> dict:
        now = time.time()
        with self._lock:
            for k in [k for k, c in self.clients.items() if now - c.get("seen", 0) > 60]:
                self.clients.pop(k, None)
            return {"port": self.port, "listening": self.listening, "error": self.error,
                    "password": bool(self.password),
                    "clients": [{"addr": c["addr"], "bytes": c["bytes"],
                                 "transport": c.get("transport", ""), "since": c["since"]}
                                for c in self.clients.values()],
                    "raw": list(self.raw)[::-1]}


# =============================================================================
# iRacing poller
# =============================================================================
class RcPoller(SDKPoller):
    tag = "rcmsg"

    def __init__(self, feed: Feed, warnings: bool = False):
        super().__init__(poll_interval=0.25)
        self.flags = FlagWatcher(feed, warnings)
        self._key = None
        self._race_cache: dict = {}
        self._cars: dict = {}
        self._cars_t = -1e9

    def _car_map(self, ir) -> dict:
        now = time.monotonic()
        if now - self._cars_t < 5.0 and self._cars:
            return self._cars
        drivers = (ir["DriverInfo"] or {}).get("Drivers", []) or []
        team = int((ir["WeekendInfo"] or {}).get("TeamRacing") or 0) == 1
        cols = class_colors(drivers)
        multi = len(cols) > 1
        out = {}
        for d in drivers:
            if d.get("CarIsPaceCar") == 1 or d.get("IsSpectator") == 1:
                continue
            try:
                i = int(d.get("CarIdx"))
            except (TypeError, ValueError):
                continue
            name = d.get("UserName") or ""
            tname = (d.get("TeamName") or "").strip()
            col = cols.get(int(d.get("CarClassID") or 0)) if multi else None
            out[i] = {"num": str(d.get("CarNumber") or "").strip('"'),
                      "uid": str(d.get("UserID") or ""),
                      "label": tname if team and tname else name,
                      "driver": name if team and tname and tname != name else "",
                      "class": (d.get("CarClassShortName") or "").strip() if multi else "",
                      "color": col, "text": text_on(col) if col else None}
        self._cars, self._cars_t = out, now
        return out

    def find_car(self, entry: str) -> dict | None:
        """iRaceControl's EntryId -> car (by car number, user id or car idx;
        which one it sends is not documented)."""
        e = str(entry).strip().lstrip("#")
        cars = dict(self._cars)
        for key in ("num", "uid"):
            for c in cars.values():
                if c.get(key) and c[key] == e:
                    return {k: v for k, v in c.items() if k != "uid"}
        if e.isdigit() and int(e) in cars:
            return {k: v for k, v in cars[int(e)].items() if k != "uid"}
        return None

    def _is_race(self, ir, key, num) -> bool:
        if key not in self._race_cache:
            r = False
            for s in (ir["SessionInfo"] or {}).get("Sessions", []) or []:
                if s.get("SessionNum") == num:
                    r = "race" in str(s.get("SessionType") or "").lower()
            self._race_cache[key] = r
        return self._race_cache[key]

    def _read_snapshot(self) -> dict:
        ir = self.ir
        ir.freeze_var_buffer_latest()
        num = ir["SessionNum"]
        key = (ir["SessionUniqueID"], num)
        if key != self._key:
            self._key = key
            self.flags.reset()
            self._cars_t = -1e9
        is_race = self._is_race(ir, key, num)
        racing = is_race and ir["SessionState"] == SESSION_STATE_RACING
        self.flags.tick(is_race, racing, ir["SessionFlags"] or 0,
                        list(ir["CarIdxSessionFlags"] or []),
                        list(ir["CarIdxPaceFlags"] or []),
                        self._car_map(ir), time.monotonic())
        return {"connected": True, "is_race": is_race, "racing": racing}


# =============================================================================
# Flask
# =============================================================================
app = Flask(__name__)
feed = Feed()
_cfg = load_config()
atvo = AtvoReceiver(feed, int(_cfg.get("atvo_port") or 5020),
                     password=str(_cfg.get("atvo_password") or ""))
poller = RcPoller(feed, bool(_cfg.get("iracing_warnings", False)))
atvo.car_lookup = poller.find_car


@app.after_request
def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@app.route("/rc/state")
def rc_state():
    return jsonify({"iracing": poller.get(), "messages": feed.list(),
                    "atvo": atvo.status() if request.args.get("raw") else
                    {k: v for k, v in atvo.status().items() if k != "raw"},
                    "now": time.time()})


@app.route("/rc/say", methods=["POST"])
def rc_say():
    body = request.get_json(silent=True) or request.form
    text = str(body.get("text") or "").strip()[:200]
    if not text:
        return jsonify({"ok": False, "error": "empty"}), 400
    feed.add("manual", "message", str(body.get("headline") or "").strip()[:60], text)
    return jsonify({"ok": True})


@app.route("/rc/steward", methods=["POST"])
def rc_steward():
    """A steward decision from iCASControl (racecontrol/, 8080):
    {kind, headline, text, car (number), driver, secs}."""
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "message")
    if kind not in STEWARD_KINDS:
        kind = "message"
    head = str(body.get("headline") or "").strip()[:80]
    text = str(body.get("text") or "").strip()[:200]
    if not head and not text:
        return jsonify({"ok": False, "error": "empty"}), 400
    num = str(body.get("car") or "").strip().lstrip("#")
    car = None
    if num:
        car = poller.find_car(num) or {
            "num": num, "label": str(body.get("driver") or "").strip()[:40],
            "driver": "", "class": "", "color": None, "text": None}
    try:
        secs = float(body["secs"]) if body.get("secs") else None
    except (TypeError, ValueError):
        secs = None
    feed.add("icas", kind, head, text, car, secs)
    return jsonify({"ok": True})


@app.route("/rc/test", methods=["POST", "GET"])
def rc_test():
    feed.add("manual", "message", "", "TEST · Race Control overlay")
    return jsonify({"ok": True})


BANNER_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Race Control</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { background: transparent; overflow: hidden; }
  body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; color: #fff; padding: 16px; }
  body.debug { background: #3a3a40; }
  #wrap { zoom: var(--z, 1); width: 900px; }
  .rc { display: flex; align-items: stretch; min-height: 76px; border-radius: 6px; overflow: hidden;
        background: rgba(18, 16, 28, 0.97); box-shadow: 0 6px 24px rgba(0,0,0,0.45);
        transform: translateX(-24px); opacity: 0; transition: opacity .35s, transform .35s; }
  .rc.show { opacity: 1; transform: none; }
  .tag { width: 150px; flex: none; background: #7b2ff7; display: flex; flex-direction: column;
         justify-content: center; padding: 0 14px; font-weight: 900; letter-spacing: 1px; line-height: 1.05; }
  .tag small { font-size: 14px; opacity: .85; } .tag b { font-size: 22px; }
  .rc.caution .tag { background: #f5c400; color: #111; }
  .rc.green .tag { background: #1fbf4f; } .rc.red .tag { background: #e3242b; }
  .rc.penalty .tag, .rc.dq .tag { background: #111; border-right: 4px solid #fff; }
  .rc.repair .tag { background: #ff7a00; }
  .rc.warning .tag { background: #fff; color: #111; }
  .rc.investigation .tag { background: #2f6bf7; }
  .body { flex: 1; display: flex; flex-direction: column; justify-content: center; padding: 10px 18px; min-width: 0; }
  .head { font-size: 26px; font-weight: 900; letter-spacing: .5px; }
  .text { font-size: 22px; font-weight: 600; color: #e6e2f5; margin-top: 2px; }
  .head:empty, .text:empty { display: none; }
  .head:empty + .text { font-size: 26px; font-weight: 800; color: #fff; }
  .car { display: flex; align-items: center; gap: 10px; margin-top: 4px; font-size: 22px; font-weight: 700; }
  .num { min-width: 46px; padding: 2px 8px; border-radius: 4px; text-align: center; font-weight: 900;
         background: #fff; color: #111; }
  .cls { font-size: 15px; font-weight: 800; padding: 2px 7px; border-radius: 3px; }
  .drv { color: #b9b6c8; font-weight: 600; font-size: 18px; }
</style></head>
<body><div id="wrap"><div class="rc" id="rc"><div class="tag" id="tag"><small>RACE</small><b>CONTROL</b></div>
<div class="body"><div class="head" id="head"></div><div class="text" id="text"></div><div class="car" id="car"></div></div></div></div>
<script>
const qs = new URLSearchParams(location.search);
const SECS = parseFloat(qs.get('secs') || '8');
const SHOW_FLAGS = qs.get('flags') !== '0', SHOW_ATVO = qs.get('atvo') !== '0', SHOW_ICAS = qs.get('icas') !== '0';
document.getElementById('wrap').style.setProperty('--z', qs.get('zoom') || '1');
if (qs.get('debug') === '1') document.body.classList.add('debug');
document.addEventListener('keydown', e => { if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug'); });
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let lastId = null; const queue = []; let busy = false;
function wanted(m) { return m.source === 'iracing' ? SHOW_FLAGS : m.source === 'atvo' ? SHOW_ATVO : m.source === 'icas' ? SHOW_ICAS : true; }
function show(m) {
  busy = true;
  const rc = document.getElementById('rc');
  rc.className = 'rc ' + (m.kind || '');
  // the tag already says RACE CONTROL (iRaceControl's default INFO headline)
  document.getElementById('head').textContent = /^race\s*control$/i.test(m.headline || '') ? '' : (m.headline || '');
  document.getElementById('text').textContent = m.text || '';
  const c = m.car;
  document.getElementById('car').innerHTML = c ? `<span class="num"${c.color ? ` style="background:${esc(c.color)};color:${esc(c.text)}"` : ''}>#${esc(c.num)}</span>`
      + `<span>${esc(c.label)}</span>${c.driver ? `<span class="drv">${esc(c.driver)}</span>` : ''}`
      + (c.class ? `<span class="cls" style="background:${esc(c.color)};color:${esc(c.text)}">${esc(c.class)}</span>` : '') : '';
  requestAnimationFrame(() => rc.classList.add('show'));
  const words = ((m.headline || '') + ' ' + (m.text || '')).split(/\s+/).length;
  const secs = Math.max(m.secs || SECS, Math.min(20, words * 0.45));
  setTimeout(() => { rc.classList.remove('show'); setTimeout(() => { busy = false; next(); }, 450); }, secs * 1000);
}
function next() { if (!busy && queue.length) show(queue.shift()); }
async function poll() {
  try {
    const d = await (await fetch('/rc/state', {cache: 'no-store'})).json();
    const ms = d.messages || [];
    if (lastId === null) lastId = ms.length ? ms[ms.length - 1].id : 0;   // skip history on load
    for (const m of ms) if (m.id > lastId) { lastId = m.id; if (wanted(m)) queue.push(m); }
    while (queue.length > 6) queue.shift();
    next();
  } catch (e) {}
  setTimeout(poll, 700);
}
if (qs.get('demo') === '1') {
  const demo = [
    {kind: 'caution', headline: 'FULL COURSE YELLOW'},
    {kind: 'message', headline: '', text: 'Car #12 and #44 incident T1 under investigation'},
    {kind: 'penalty', headline: 'BLACK FLAG · PENALTY', car: {num: '12', label: 'Proton Competition', driver: 'M. Becker', class: 'GT3', color: '#ffb020', text: '#0a0a0f'}},
    {kind: 'caution', headline: 'SAFETY CAR IN THIS LAP'},
    {kind: 'green', headline: 'GREEN FLAG', text: 'Racing resumes'}];
  let i = 0; const go = () => { queue.push(demo[i++ % demo.length]); next(); }; go(); setInterval(go, 10000);
} else poll();
</script></body></html>"""


PANEL_HTML = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"><title>Race Control – Panel</title>
<style>
  body { background: #15151c; color: #e8e8f0; font: 15px 'Segoe UI', Arial, sans-serif; margin: 0; padding: 16px; }
  h1 { font-size: 20px; margin: 0 0 12px; } h2 { font-size: 16px; margin: 18px 0 8px; color: #b9a6ff; }
  .box { background: #1f1f29; border-radius: 6px; padding: 10px 12px; }
  .ok { color: #45f063; } .bad { color: #ff6b6b; } .muted { color: #8a8a99; }
  table { border-collapse: collapse; width: 100%; } td, th { text-align: left; padding: 4px 6px; border-bottom: 1px solid #2a2a36; vertical-align: top; }
  code { font-size: 12px; color: #9fd; word-break: break-all; }
  input[type=text] { width: 60%; padding: 6px; background: #0f0f15; color: #fff; border: 1px solid #444; border-radius: 4px; }
  button { padding: 6px 12px; background: #7b2ff7; color: #fff; border: 0; border-radius: 4px; cursor: pointer; font-weight: 700; }
</style></head><body>
<h1>Race Control – Panel</h1>
<div class="box" id="atvo"></div>
<h2>Nachricht von Hand</h2>
<div class="box"><input type="text" id="msg" placeholder="z.B. Incident T1 #12 / #44 under investigation" maxlength="200">
<button onclick="say()">Senden</button> <button onclick="fetch('/rc/test',{method:'POST'})">Test</button></div>
<h2>Meldungen (neueste oben)</h2><div class="box"><table id="msgs"></table></div>
<h2>ATVO Rohdaten (was iRaceControl schickt)</h2><div class="box"><table id="raw"></table></div>
<script>
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const tm = t => new Date(t * 1000).toLocaleTimeString('de-DE');
async function say() {
  const el = document.getElementById('msg'); if (!el.value.trim()) return;
  await fetch('/rc/say', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: el.value})});
  el.value = '';
}
document.getElementById('msg').addEventListener('keydown', e => { if (e.key === 'Enter') say(); });
async function poll() {
  try {
    const d = await (await fetch('/rc/state?raw=1', {cache: 'no-store'})).json();
    const a = d.atvo;
    document.getElementById('atvo').innerHTML =
      `<b>ATVO-Empfänger (iRaceControl, SignalR):</b> ${a.listening ? `<span class="ok">bereit auf Port ${a.port}${a.password ? ' · mit Passwort' : ' · ohne Passwort'}</span>` : `<span class="bad">${esc(a.error || 'nicht aktiv')}</span>`}<br>`
      + (a.clients.length ? a.clients.map(c => `<span class="ok">verbunden: ${esc(c.addr)}</span> · ${esc(c.transport)} · ${c.bytes} Bytes`).join('<br>')
                          : '<span class="muted">keine Verbindung — in iRaceControl bei ATVO diese PC-Adresse + Port eintragen und ATVO einschalten</span>')
      + `<br><span class="muted">iRacing: ${d.iracing.connected ? 'verbunden' : 'nicht verbunden'}${d.iracing.is_race ? ' · Rennen' : ''}</span>`;
    document.getElementById('msgs').innerHTML = '<tr><th>Zeit</th><th>Quelle</th><th>Meldung</th></tr>' +
      (d.messages || []).slice().reverse().map(m => `<tr><td>${tm(m.t)}</td><td>${esc(m.source)}</td><td><b>${esc(m.headline)}</b> ${esc(m.text)}${m.car ? ` · #${esc(m.car.num)} ${esc(m.car.label)}` : ''}</td></tr>`).join('');
    document.getElementById('raw').innerHTML = '<tr><th>Zeit</th><th>Gelesen</th><th>Roh</th></tr>' +
      (a.raw || []).map(r => `<tr><td>${tm(r.t)}<br><span class="muted">${r.len} B · ${esc(r.how || '?')}${r.shown ? ' · <span class=ok>angezeigt</span>' : ''}</span></td><td>${esc(r.parsed || '')}</td><td><code>${esc(r.text)}</code><br><code class="muted">${esc(r.hex)}</code></td></tr>`).join('');
  } catch (e) {}
  setTimeout(poll, 1500);
}
poll();
</script></body></html>"""


@app.route("/")
def index():
    return BANNER_HTML


@app.route("/panel")
def panel():
    return PANEL_HTML


@app.route("/status")
def status():
    return jsonify({"ok": True})


if __name__ == "__main__":
    if _cfg.get("atvo_enabled", True):
        atvo.start()
    threading.Thread(target=poller.run, daemon=True).start()
    print("\n" + "=" * 60)
    print("  iRacing Race Control banner")
    print(f"  Banner (OBS):   http://localhost:{PORT}")
    print(f"  Operator panel: http://localhost:{PORT}/panel")
    print(f"  Preview:        http://localhost:{PORT}/?demo=1")
    print(f"  iRaceControl -> ATVO server: this PC, port {atvo.port} (SignalR)")
    print("  Raw capture:    logs/atvo_capture_<date>.log")
    print("=" * 60 + "\n")
    try:
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)
    finally:
        poller.stop()
        atvo.stop()
