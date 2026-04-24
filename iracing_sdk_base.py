"""
iracing_sdk_base.py
-------------------
Shared foundation for all iRacing overlay pollers.

Every overlay in this repo has the same connection + polling skeleton:
  - open a connection to iRacing via pyirsdk
  - call _read_snapshot() on a fixed interval
  - store the result under a lock for the Flask thread to read
  - handle graceful shutdown

That skeleton lives here. Each overlay subclasses SDKPoller and only
implements _read_snapshot(), with an optional `tag` class attribute for
log prefixes and an optional `poll_interval` for the loop cadence.

Also exports setup_utf8_stdout(), which every overlay calls at import
time to survive Windows cp1252 consoles. Without it, a single print()
of a non-ASCII driver name inside an except block raises
UnicodeEncodeError and silently kills the poller thread. (We've hit
this exact failure mode once — don't remove.)
"""

from __future__ import annotations
import sys
import threading
import time


def setup_utf8_stdout() -> None:
    """Force UTF-8 on stdout/stderr regardless of the console's codepage.

    Call at import time in every overlay script. Safe to call repeatedly.
    """
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# pyirsdk is a hard dependency — import here so any script that imports
# SDKPoller also gets a clean error message if pyirsdk is missing.
try:
    import irsdk  # noqa: F401 — re-exported so subclasses can use `self.ir`
except ImportError:
    print("ERROR: pyirsdk not installed. Run:  pip install pyirsdk flask")
    raise SystemExit(1)


class SDKPoller:
    """Base poller — connects to iRacing, runs a loop, stores snapshots.

    Subclasses:
      - set the `tag` class attribute (e.g. "grid") — used in log prefixes
      - optionally set `poll_interval` class attribute (default 1.0 s)
      - override `_read_snapshot(self) -> dict` to return the overlay's state
      - may override `_check_connection()` if they need extra diagnostics
        (see iracing_livery.py for an example)

    Thread model:
      - run() runs on a daemon thread started by main()
      - Flask handlers call get() from the request thread
      - A Lock ensures those two threads don't race on self.data
    """

    #: Short lowercase name used in log prefixes like "[grid] Connected…"
    tag: str = "sdk"

    #: Seconds between polls. Override in subclass or pass to __init__.
    poll_interval: float = 1.0

    def __init__(self, poll_interval: float | None = None, tag: str | None = None):
        self.ir = irsdk.IRSDK()
        if poll_interval is not None:
            self.poll_interval = poll_interval
        if tag is not None:
            self.tag = tag
        self.connected: bool = False
        self.data: dict = {"connected": False}
        self._lock = threading.Lock()
        self._running: bool = True

    # -------- subclass must implement ------------------------------------
    def _read_snapshot(self) -> dict:
        raise NotImplementedError(
            f"{type(self).__name__} must implement _read_snapshot()"
        )

    # -------- connection management --------------------------------------
    def _check_connection(self) -> bool:
        """Return True if currently connected. Logs transitions."""
        if self.connected and not (self.ir.is_initialized and self.ir.is_connected):
            try:
                self.ir.shutdown()
            except Exception:
                pass
            self.connected = False
            print(f"[{self.tag}] Disconnected from iRacing")
        elif not self.connected:
            try:
                started = self.ir.startup()
            except Exception:
                started = False
            if started and self.ir.is_initialized and self.ir.is_connected:
                self.connected = True
                print(f"[{self.tag}] Connected to iRacing")
        return self.connected

    # -------- main loop --------------------------------------------------
    def run(self) -> None:
        print(f"[{self.tag}] Poller started (waiting for iRacing...)")
        while self._running:
            try:
                if self._check_connection():
                    snap = self._read_snapshot()
                    with self._lock:
                        self.data = snap
                else:
                    with self._lock:
                        self.data = {"connected": False}
            except Exception as e:
                # Surface poll errors to the console so they don't stay
                # hidden in the 'error' field of the JSON response.
                # Note: we must not let any print() in _read_snapshot raise
                # UnicodeEncodeError on Windows cp1252 — setup_utf8_stdout()
                # in every overlay script is how we avoid that.
                print(f"[{self.tag}] Poll error: {type(e).__name__}: {e!r}")
                with self._lock:
                    self.data = {"connected": False, "error": str(e)}
            time.sleep(self.poll_interval)

    # -------- thread-safe access for Flask handlers ----------------------
    def get(self) -> dict:
        with self._lock:
            return dict(self.data)

    # -------- graceful shutdown ------------------------------------------
    def stop(self) -> None:
        self._running = False
        if self.connected:
            try:
                self.ir.shutdown()
            except Exception:
                pass
