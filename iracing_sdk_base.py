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


# -----------------------------------------------------------------------------
# Starting-grid baseline (shared by the standings tower and the race logger)
# -----------------------------------------------------------------------------
#: irsdk SessionState enum — 4 == Racing (green flag out).
SESSION_STATE_RACING = 4


class GridBaseline:
    """Per-race starting-grid slot per CarIdx, captured once and frozen.

    THE RULE this class exists to enforce:

        A "+/-" column is ALWAYS  grid_position - current_position.

    It is a NET value measured against where the driver STARTED, never a
    running total of position changes. The pole sitter who is shuffled to
    P2 in turn 1 reads -1; the moment he takes the place back he reads 0,
    and he keeps reading 0 no matter how many more times that same swap
    happens. Nothing accumulates. (A cumulative "how many passes did he
    make" number is a different, also useful, statistic — see the race
    logger's `overtakes` / `overtaken` counters — but it is not the +/-.)

    Baseline source priority. The first one that yields at least two cars
    wins, is captured ONCE per race session, and is never recomputed:

      1. The QUALIFYING session's ResultsPositions[].Position — iRacing's
         official grid order. Stable for the whole race and, crucially,
         identical whether the overlay was started before the green or
         attached halfway through. This is the same source iracing_grid.py
         uses, so the grid overlay, the tower and the logger all agree.
      2. The RACE session's ResultsPositions[].StartingPosition, when the
         sim publishes it (0-based there; -1 means unknown).
      3. Live CarIdxPosition sampled the moment SessionState first reaches
         Racing — but ONLY if we were already watching before the green.
         Sampling later would freeze "the running order at the moment the
         overlay happened to start" and then present it as if it were the
         grid. That is exactly the wrong number, so we refuse to do it.

    When none of the three is available (overlay attached mid-race in a
    session with no qualifying results, or a driver who joined after the
    grid was set) the car simply has no baseline. Callers render an empty
    cell for those: no number is better than a wrong one.

    Usage — call update() once per poll tick, then read grid_pos /
    class_grid_pos:

        self._grid = GridBaseline()
        ...
        self._grid.update(self.ir, class_of={r["car_idx"]: r["class_id"] ...})
        start = self._grid.class_grid_pos.get(car_idx)
        delta = (start - class_position) if start else None
    """

    def __init__(self) -> None:
        #: car_idx -> 1-based grid slot across the whole field
        self.grid_pos: dict[int, int] = {}
        #: car_idx -> 1-based grid slot within the car's own class
        self.class_grid_pos: dict[int, int] = {}
        #: which of the three sources was used ("qualifying" | ... | None)
        self.source: str | None = None
        self._session_key = None
        self._saw_pre_green = False

    # -- lifecycle ---------------------------------------------------------
    def reset(self) -> None:
        """Forget the baseline (called automatically on session change)."""
        self.grid_pos = {}
        self.class_grid_pos = {}
        self.source = None
        self._saw_pre_green = False

    @property
    def captured(self) -> bool:
        return bool(self.grid_pos)

    def update(self, ir, class_of: dict | None = None) -> None:
        """Poll-tick entry point. Cheap once the baseline is captured."""
        key = (ir["SessionUniqueID"], ir["SessionNum"])
        if key != self._session_key:
            self._session_key = key
            self.reset()

        state = int(ir["SessionState"] or 0)
        if state and state < SESSION_STATE_RACING:
            # We are watching this session before the green flag, so a
            # green-flag sample (source 3) would be a real grid order.
            self._saw_pre_green = True

        if self.grid_pos:
            return  # captured once, frozen for the rest of the session

        info = ir["SessionInfo"] or {}
        sessions = info.get("Sessions") or []

        raw, source = self._from_qualifying(sessions), "qualifying"
        if not raw:
            raw, source = self._from_race_results(sessions, ir), "race_results"
        if not raw and self._saw_pre_green and state >= SESSION_STATE_RACING:
            raw, source = self._from_green_flag(ir), "green_flag"

        if len(raw) >= 2:
            self._store(raw, source, class_of)

    # -- sources -----------------------------------------------------------
    @staticmethod
    def _from_qualifying(sessions: list) -> dict:
        """{car_idx: qualifying position} from the last qualifying session
        that produced results. Position is 1-based; 0 / missing means the
        driver set no time, so we leave them without a baseline."""
        out: dict[int, int] = {}
        for s in sessions:
            if "qualify" not in (s.get("SessionType") or "").lower():
                continue
            results = s.get("ResultsPositions") or []
            if not results:
                continue
            found: dict[int, int] = {}
            for r in results:
                try:
                    cidx = int(r.get("CarIdx"))
                    pos = int(r.get("Position"))
                except (TypeError, ValueError):
                    continue
                if pos >= 1:
                    found[cidx] = pos
            if len(found) >= 2:
                out = found  # keep the LAST qualifying session with results
        return out

    @staticmethod
    def _from_race_results(sessions: list, ir) -> dict:
        """{car_idx: StartingPosition} from the current race session, when
        the sim publishes that field (0-based there, -1 = unknown)."""
        sess_num = ir["SessionNum"]
        out: dict[int, int] = {}
        for s in sessions:
            if s.get("SessionNum") != sess_num:
                continue
            for r in s.get("ResultsPositions") or []:
                try:
                    cidx = int(r.get("CarIdx"))
                    sp = int(r.get("StartingPosition"))
                except (TypeError, ValueError):
                    continue
                if sp >= 0:
                    out[cidx] = sp
        return out

    @staticmethod
    def _from_green_flag(ir) -> dict:
        """{car_idx: CarIdxPosition} sampled right at the green flag.

        Guarded by a lap check as well as the caller's pre-green check —
        if anyone in the field is already past lap 1 this is not a start,
        it is a mid-race attach, and we must not pretend otherwise.
        """
        laps = ir["CarIdxLap"] or []
        try:
            if laps and max(int(x or 0) for x in laps) > 1:
                return {}
        except (TypeError, ValueError):
            return {}
        positions = ir["CarIdxPosition"] or []
        out: dict[int, int] = {}
        for cidx, pos in enumerate(positions):
            try:
                pos = int(pos or 0)
            except (TypeError, ValueError):
                continue
            if pos > 0:
                out[cidx] = pos
        return out

    # -- storage -----------------------------------------------------------
    def _store(self, raw: dict, source: str, class_of: dict | None) -> None:
        """Re-rank the raw source values into dense 1-based grid slots.

        Re-ranking makes the class source-agnostic: it does not matter
        whether the source counted from 0 or 1, or whether there are holes
        in the numbering — only the ORDER matters.
        """
        ordered = sorted(raw.items(), key=lambda kv: kv[1])
        self.grid_pos = {cidx: rank for rank, (cidx, _v) in enumerate(ordered, start=1)}

        by_class: dict = {}
        for cidx, _v in ordered:
            cid = (class_of or {}).get(cidx, 0)
            by_class.setdefault(cid, []).append(cidx)
        self.class_grid_pos = {}
        for _cid, members in by_class.items():
            for rank, cidx in enumerate(members, start=1):
                self.class_grid_pos[cidx] = rank

        self.source = source
        print(f"[grid-baseline] captured {len(self.grid_pos)} cars from {source}")

    # -- convenience -------------------------------------------------------
    def delta(self, car_idx: int, current_pos: int | None) -> int | None:
        """Net places vs the grid, overall. None when unknown."""
        start = self.grid_pos.get(car_idx)
        if not start or not current_pos or current_pos <= 0:
            return None
        return start - current_pos

    def class_delta(self, car_idx: int, current_class_pos: int | None) -> int | None:
        """Net places vs the grid, within the car's class. None when unknown."""
        start = self.class_grid_pos.get(car_idx)
        if not start or not current_class_pos or current_class_pos <= 0:
            return None
        return start - current_class_pos


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
