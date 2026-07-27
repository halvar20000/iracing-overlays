"""JSONL race-replay data source.

Plays back a race recorded by the iRacing race-logger (one JSON object per
line) as a normal :class:`DataSource`.  This lets a recorded race be:

* reviewed by a steward after the event,
* used to demo iCASControl without a live session,
* used to test the dashboard against real race data.

The recorder writes ``session_start`` / ``lap`` / ``pos`` / ``incident`` /
``flag`` / ``pit`` / ``penalty`` / ``slow_lap`` / ``session_end`` events.  Car
positions come from the 1 Hz ``pos`` ticks and are interpolated for smooth
playback; everything else is applied on a real-time (or scaled) clock.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from backend.models import (
    CarFrame, Frame, IncidentEvent, SessionFrame, Weather,
    SURFACE_ON_TRACK, SURFACE_IN_PIT_STALL,
)
from backend.sources.base import DataSource
from backend.tracks import load_track
from backend.car_brands import detect_brand

_INC_POINTS = re.compile(r"\+(\d+)\s*x")
_CLASS_COLORS = ["#e6433a", "#2f86d6", "#33c777", "#f5c518", "#9b6dff", "#ff6b35"]
_FLAG_MAP = {
    "green": "green", "one_to_green": "green", "start_go": "green",
    "yellow": "yellow", "caution": "caution", "yellow_waving": "yellow",
    "red": "red", "white": "white", "checkered": "checkered",
}


def _class_short(name: str) -> str:
    """Compact class label, e.g. 'Hosted All Cars' -> 'HAC'."""
    parts = name.split()
    if len(parts) >= 2:
        return "".join(p[0] for p in parts)[:4].upper()
    return name[:4].upper()


class ReplaySource(DataSource):
    """Replays a recorded ``.jsonl`` race log."""

    name = "replay"

    def __init__(self, log_path: str | Path, speed: float = 1.0):
        self.log_path = Path(log_path)
        self.speed = max(0.1, float(speed))
        self._connected = False
        self._t0 = 0.0
        self._cursor = 0
        self._duration = 0.0
        self._events: list[tuple[float, str, dict]] = []
        self._pos: dict[int, list[tuple[float, float]]] = {}
        self._cars: dict[int, dict] = {}
        self._state: dict[int, dict] = {}
        self._classes: dict[str, tuple[int, str]] = {}
        self._track: Optional[dict] = None
        self._track_name = "Replay"
        self._track_config = ""
        self._track_len_km = 5.0
        self._session_type = "RACE"
        self._weather = Weather()
        self._flag = "green"
        self._pending: list[IncidentEvent] = []

    # -- DataSource interface ----------------------------------------------

    def connect(self) -> bool:
        if not self.log_path.is_file():
            print(f"[replay] log file not found: {self.log_path}")
            return False
        try:
            lines = self.log_path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            print(f"[replay] cannot read log: {exc}")
            return False

        timed: list[tuple[float, str, dict]] = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            et = ev.get("type")
            if et == "session_start":
                self._ingest_session_start(ev)
            elif et == "pos":
                t = float(ev.get("t", 0.0))
                for k, pct in (ev.get("p") or {}).items():
                    self._pos.setdefault(int(k), []).append((t, float(pct)))
            elif et in ("lap", "incident", "flag", "pit", "penalty",
                        "slow_lap", "session_end"):
                t = float(ev.get("t_session", ev.get("t", 0.0)) or 0.0)
                timed.append((t, et, ev))

        for series in self._pos.values():
            series.sort(key=lambda x: x[0])
        timed.sort(key=lambda x: x[0])
        self._events = timed

        all_t = [t for t, _, _ in timed]
        all_t += [s[-1][0] for s in self._pos.values() if s]
        self._duration = max(all_t) if all_t else 0.0

        self._connected = True
        self._t0 = time.monotonic()
        self._cursor = 0
        print(f"[replay] loaded {self.log_path.name}: {len(self._cars)} cars, "
              f"{len(timed)} events, {self._duration / 60:.1f} min race, "
              f"track '{self._track_name}', {self.speed}x speed")
        return True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def poll(self) -> Optional[Frame]:
        if not self._connected:
            return None
        self._pending = []
        t = min(self._duration, (time.monotonic() - self._t0) * self.speed)
        while (self._cursor < len(self._events)
               and self._events[self._cursor][0] <= t):
            _, et, ev = self._events[self._cursor]
            self._cursor += 1
            self._apply_event(et, ev)
        return self._build_frame(t, finished=t >= self._duration)

    # -- log ingestion ------------------------------------------------------

    def _ingest_session_start(self, ev: dict) -> None:
        self._session_type = str(ev.get("session_type", "RACE")).upper()
        self._track_name = ev.get("track", "Replay")
        self._track_config = ev.get("track_config", "") or ""
        geo = load_track(str(ev.get("track_name", "") or ""))
        if geo:
            self._track = geo
            self._track_len_km = geo["length_km"]

        w = ev.get("weather") or {}
        wetness = float(w.get("wetness", 0) or 0)
        self._weather = Weather(
            air_temp=float(w.get("air_temp_c", 0) or 0),
            track_temp=float(w.get("track_temp_c", 0) or 0),
            humidity=0.0, wind_ms=0.0,
            skies={0: "Clear", 1: "Partly Cloudy", 2: "Mostly Cloudy",
                   3: "Overcast"}.get(int(w.get("skies", 0) or 0), "Clear"),
            precipitation=min(1.0, wetness / 7.0),
            is_wet=wetness > 1, track_wetness="Wet" if wetness > 1 else "Dry",
        )

        for d in ev.get("drivers") or []:
            idx = int(d.get("car_idx", -1))
            if idx < 0:
                continue
            cname = d.get("car_class", "") or "Race"
            if cname not in self._classes:
                cid = len(self._classes) + 1
                self._classes[cname] = (
                    cid, _CLASS_COLORS[(cid - 1) % len(_CLASS_COLORS)])
            cid, color = self._classes[cname]
            self._cars[idx] = {
                "number": str(d.get("car_number", "0")),
                "driver": d.get("name", ""),
                "car": d.get("car", ""),
                "car_path": d.get("car_path", ""),
                "car_class": cname, "class_id": cid, "class_color": color,
                "brand": detect_brand(d.get("car_path"), d.get("car")),
            }
            self._state[idx] = {
                "lap": 0, "laps_completed": 0, "position": 0, "class_pos": 0,
                "last_lap": 0.0, "best_lap": 0.0, "gap": 0.0,
                "on_pit": False, "pit_stops": 0,
            }

    def _apply_event(self, et: str, ev: dict) -> None:
        idx = int(ev.get("car_idx", -1))
        if et == "lap" and idx in self._state:
            st = self._state[idx]
            lap = ev.get("lap")
            if isinstance(lap, (int, float)):
                st["laps_completed"] = max(0, int(lap))
            st["lap"] = st["laps_completed"] + 1
            if ev.get("lap_time"):
                st["last_lap"] = float(ev["lap_time"])
            if ev.get("best_lap"):
                st["best_lap"] = float(ev["best_lap"])
            if ev.get("position"):
                st["position"] = int(ev["position"])
            if ev.get("class_pos"):
                st["class_pos"] = int(ev["class_pos"])
            if ev.get("gap_to_leader") is not None:
                st["gap"] = float(ev["gap_to_leader"])
            st["on_pit"] = bool(ev.get("on_pit", False))
        elif et == "flag":
            self._flag = _FLAG_MAP.get(str(ev.get("flag", "")).lower(), "green")
        elif et == "pit" and idx in self._state:
            self._state[idx]["pit_stops"] = int(
                ev.get("stop_count", self._state[idx]["pit_stops"] + 1))
        elif et == "incident" and idx >= 0:
            m = _INC_POINTS.search(ev.get("details", "") or "")
            pts = int(m.group(1)) if m else 0
            kind = str(ev.get("incident_type", "incident")).replace("_", "-")
            self._pending.append(IncidentEvent(
                car_idx=idx, points=pts, kind=kind,
                lap=self._state.get(idx, {}).get("lap", 0)))

    # -- position interpolation --------------------------------------------

    def _pct_at(self, idx: int, t: float) -> tuple[float, float]:
        """Interpolated lap-distance % and speed (m/s) for a car at time t."""
        s = self._pos.get(idx)
        if not s:
            return 0.0, 0.0
        if t <= s[0][0]:
            return s[0][1], 0.0
        if t >= s[-1][0]:
            return s[-1][1], 0.0
        lo, hi = 0, len(s) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if s[mid][0] < t:
                lo = mid + 1
            else:
                hi = mid
        t1, p1 = s[lo - 1]
        t2, p2 = s[lo]
        dt = (t2 - t1) or 1e-9
        dp = p2 - p1
        if dp < -0.5:                       # crossed the start/finish line
            dp += 1.0
        f = (t - t1) / dt
        pct = (p1 + dp * f) % 1.0
        speed = abs(dp) * self._track_len_km * 1000.0 / dt
        return pct, speed

    # -- frame building -----------------------------------------------------

    def _build_frame(self, t: float, finished: bool) -> Frame:
        leader_laps = max((s["laps_completed"] for s in self._state.values()),
                          default=0)
        order = sorted(
            self._cars.keys(),
            key=lambda i: (self._state[i]["position"]
                           if self._state[i]["position"] > 0 else 999,
                           -self._state[i]["laps_completed"]))
        cars: list[CarFrame] = []
        for pos_i, idx in enumerate(order, start=1):
            st, meta = self._state[idx], self._cars[idx]
            pct, speed = self._pct_at(idx, t)
            cars.append(CarFrame(
                car_idx=idx, car_number=meta["number"],
                driver_name=meta["driver"], team_name=meta["driver"],
                car_class=meta["car_class"], class_id=meta["class_id"],
                class_color=meta["class_color"],
                class_short=_class_short(meta["car_class"]),
                car_brand=meta.get("brand", ""),
                position=st["position"] if st["position"] > 0 else pos_i,
                class_position=st["class_pos"],
                lap=st["lap"], laps_completed=st["laps_completed"],
                lap_dist_pct=round(pct, 5),
                on_pit_road=st["on_pit"],
                track_surface=SURFACE_IN_PIT_STALL if st["on_pit"]
                else SURFACE_ON_TRACK,
                last_lap=st["last_lap"], best_lap=st["best_lap"],
                gap_to_leader=st["gap"], interval=0.0,
                laps_down=max(0, leader_laps - st["laps_completed"]),
                speed_ms=round(speed, 1), finished=finished,
            ))

        # Derive intervals from the gap-to-leader ladder.
        for i in range(1, len(cars)):
            a, b = cars[i - 1], cars[i]
            if a.gap_to_leader and b.gap_to_leader:
                b.interval = round(b.gap_to_leader - a.gap_to_leader, 2)

        session = SessionFrame(
            track_name=self._track_name, track_config=self._track_config,
            track_length_km=self._track_len_km,
            session_type=self._session_type,
            session_state="CHECKERED" if finished else "RACING",
            session_time=t,
            session_time_remain=max(0.0, self._duration - t),
            session_laps_total=0, session_laps_remain=0,
            flags=["checkered"] if finished else [self._flag],
            start_lights="off", weather=self._weather, sim_date="",
        )
        return Frame(
            session=session, cars=cars, incidents=list(self._pending),
            track_path=self._track["path"] if self._track else None,
            pit_path=self._track["pit"] if self._track else None,
            source_name=self.name, connected=True,
        )

    def send_command(self, command: str, **params) -> str:
        return f"[replay] '{command}' ignored - this is a recorded race"
