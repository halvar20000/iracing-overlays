"""Steward commands into the iRacing chat (step 2 of iCASControl).

iRacing has NO SDK call for admin actions.  They are chat commands, typed in
the sim's text chat by an ADMIN of the session (official syntax:
support.iracing.com/support/solutions/articles/31000133518):

    !black <driver> [time | laps | D]   black flag; default stop-and-go,
                                        a time = hold seconds, D = drive-through
    !dq / !eol / !waveby / !clear <driver> [message]
    !clearall   !yellow [message]   !pacelaps <+n|-n|n>   !pitopen  !pitclose
    /all <message>   text to everybody     /rc <message>   text to the admins

A driver is "#<car number>".  The SDK can only OPEN the chat
(BroadcastMsg chat_command begin_chat); the text itself is put into the sim
window as Windows key messages:

    method "post"   (default) WM_CHAR per character + Enter, posted to the
                    window - works in the background, no focus change
    method "focus"  brings iRacing to the front for a moment and types with
                    SendInput (KEYEVENTF_UNICODE), then gives the focus back

Neither could be tried on this (Linux) machine - the dashboard reports per
command whether iRacing really did it (flag bits, see IRacingSource).

Config: racecontrol/data/chat_config.json (gitignored, updater-protected):
    {"enabled": false, "method": "post", "char_delay_ms": 6,
     "commands": {"notify": "/all #{num} {msg}", ...}}   # template overrides
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# Templates.  {num} car number, {msg} optional message, {sec} seconds,
# {n} a count.  None = iRacing has no such command (logged only).
COMMANDS: dict[str, Optional[str]] = {
    # race control
    "pace_deploy": "!yellow {msg}",
    "pace_end": "!pacelaps 1",          # caution goes green after 1 more lap
    "pace_laps": "!pacelaps {n}",
    "pit_open": "!pitopen",
    "pit_close": "!pitclose",
    "clear_all": "!clearall",
    "red_flag": None,                   # iRacing has no red flag
    "green_flag": None,
    "wave_lapped": None,                # only per car (!waveby)
    # per car
    "drive_through": "!black #{num} D",
    "stop_go": "!black #{num} {sec}",   # {sec} empty = plain stop-and-go
    "dsq": "!dq #{num} {msg}",
    "eol": "!eol #{num} {msg}",
    "wave_around": "!waveby #{num} {msg}",
    "clear_penalties": "!clear #{num} {msg}",
    "add_admin": "!admin #{num}",
    "remove_admin": "!nadmin #{num}",
    "chat_on": "!chat #{num}",
    "chat_off": "!nchat #{num}",
    "remove": "!remove #{num} {msg}",
    # !black takes no message: the reason follows as a second line
    "penalty_reason": "/all #{num} {driver}: {msg}",
    # messages (iRacing documents no private message; /all reaches everybody)
    # private: "/<car number> text" reaches only that driver
    # (support.iracing.com 31000170165)
    "notify": "/{num} {msg}",
    "warning": "/all #{num} {driver}: WARNING{msg_dash}",
    "message_all": "/all {msg}",
    "message_rc": "/rc {msg}",
    "test": "/rc iCASControl chat test",
}

MAX_LEN = 200

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_PATH = DATA_DIR / "chat_config.json"
DEFAULT_CONFIG = {"enabled": False, "method": "post", "char_delay_ms": 6,
                  "commands": {}}


def _clean(text) -> str:
    return " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())


def build(kind: str, overrides: Optional[dict] = None, **kw) -> Optional[str]:
    """Chat text for a steward action, or None when iRacing has none."""
    tpl = (overrides or {}).get(kind, COMMANDS.get(kind))
    if not tpl:
        return None
    msg = _clean(kw.get("msg", ""))
    sec = kw.get("sec")
    try:
        sec = int(round(float(sec))) if sec not in (None, "") else 0
    except (TypeError, ValueError):
        sec = 0
    vals = {
        "num": _clean(kw.get("num", "")).lstrip("#"),
        "msg": msg,
        "msg_dash": f" - {msg}" if msg else "",
        "sec": str(sec) if sec > 0 else "",
        "n": str(kw.get("n", "")),
        "driver": _clean(kw.get("driver", "")),
    }
    if "{num}" in tpl and not vals["num"]:
        return None
    try:
        text = tpl.format(**vals)
    except (KeyError, IndexError, ValueError):
        return None
    return _clean(text)[:MAX_LEN] or None


def load_config(path: Optional[Path] = None) -> dict:
    path = path or CONFIG_PATH
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[chat] {path.name} unreadable: {exc}", file=sys.stderr)
    return cfg


def save_config(cfg: dict, path: Optional[Path] = None) -> None:
    path = path or CONFIG_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        print(f"[chat] could not save {path.name}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Typing into the sim window (Windows only)
# ---------------------------------------------------------------------------

WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x0100, 0x0101, 0x0102
VK_RETURN = 0x0D
SIM_WINDOW_CLASS = "SimWinClass"


class WinKeys:
    """The few user32 calls needed; replaceable by a fake in tests."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        u, w = self.u, wintypes
        # explicit types: without them ctypes passes a 32-bit int and the
        # key-up lParam 0xC01C0001 would not even convert
        u.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]
        u.FindWindowW.restype = w.HWND
        u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        u.PostMessageW.restype = w.BOOL
        u.GetForegroundWindow.restype = w.HWND
        u.SetForegroundWindow.argtypes = [w.HWND]
        u.SetForegroundWindow.restype = w.BOOL
        u.SendInput.argtypes = [w.UINT, ctypes.c_void_p, ctypes.c_int]
        u.SendInput.restype = w.UINT

    def find_window(self) -> int:
        return self.u.FindWindowW(SIM_WINDOW_CLASS, None) or 0

    def post_char(self, hwnd: int, ch: str) -> None:
        self.u.PostMessageW(hwnd, WM_CHAR, ord(ch), 1)

    def post_enter(self, hwnd: int) -> None:
        self.u.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN, 0x001C0001)
        self.u.PostMessageW(hwnd, WM_KEYUP, VK_RETURN, 0xC01C0001)

    def foreground(self) -> int:
        return self.u.GetForegroundWindow() or 0

    def set_foreground(self, hwnd: int) -> bool:
        return bool(self.u.SetForegroundWindow(hwnd))

    def type_text(self, text: str, delay: float) -> None:
        """SendInput: each character as a unicode key, then Enter."""
        ct, wt = self.ct, self.wt

        class KEYBDINPUT(ct.Structure):
            _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD),
                        ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                        ("dwExtraInfo", ct.c_size_t)]

        class INPUT(ct.Structure):
            class _U(ct.Union):
                _fields_ = [("ki", KEYBDINPUT), ("pad", ct.c_byte * 32)]
            _anonymous_ = ("u",)
            _fields_ = [("type", wt.DWORD), ("u", _U)]

        def send(vk, scan, flags):
            inp = INPUT(type=1)
            inp.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
            self.u.SendInput(1, ct.byref(inp), ct.sizeof(INPUT))

        KEYUP, UNICODE = 0x0002, 0x0004
        for ch in text:
            send(0, ord(ch), UNICODE)
            send(0, ord(ch), UNICODE | KEYUP)
            time.sleep(delay)
        send(VK_RETURN, 0, 0)
        send(VK_RETURN, 0, KEYUP)


class ChatSender:
    """One chat line at a time, in a background thread (typing takes ~1 s).

    ``open_chat`` is the SDK call that opens the chat box;
    ``done(text, ok, info)`` is called after each line."""

    def __init__(self, open_chat: Callable[[], None],
                 done: Callable[[str, bool, str], None],
                 keys_factory: Callable[[], object] = WinKeys) -> None:
        self.open_chat = open_chat
        self.done = done
        self.keys_factory = keys_factory
        self._keys = None
        self._q: list[tuple[str, str, float]] = []
        self._cv = threading.Condition()
        self._thread: Optional[threading.Thread] = None

    def send(self, text: str, method: str = "post",
             char_delay_ms: float = 6, open_wait: float = 0.25) -> None:
        with self._cv:
            self._q.append((text, method, max(0.0, char_delay_ms) / 1000.0,
                            max(0.05, float(open_wait))))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
            self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                if not self._q:
                    self._cv.wait(timeout=30)
                    if not self._q:
                        return
                text, method, delay, wait = self._q.pop(0)
            ok, info = self._type(text, method, delay, wait)
            try:
                self.done(text, ok, info)
            except Exception:
                pass

    def _type(self, text: str, method: str, delay: float,
              wait: float = 0.25) -> tuple[bool, str]:
        try:
            if self._keys is None:
                self._keys = self.keys_factory()
            keys = self._keys
        except Exception as exc:
            return False, f"no Windows keyboard access ({exc})"
        try:
            hwnd = keys.find_window()
            if not hwnd:
                return False, "iRacing window not found"
            if method == "focus":
                prev = keys.foreground()
                keys.set_foreground(hwnd)
                time.sleep(0.15)
                self.open_chat()
                time.sleep(wait)
                keys.type_text(text, delay)
                time.sleep(0.1)
                if prev and prev != hwnd:
                    keys.set_foreground(prev)
                return True, "typed (focus)"
            self.open_chat()
            time.sleep(wait)
            for ch in text:
                keys.post_char(hwnd, ch)
                time.sleep(delay)
            time.sleep(0.05)
            keys.post_enter(hwnd)
            return True, "posted"
        except Exception as exc:
            return False, f"typing failed: {exc}"
