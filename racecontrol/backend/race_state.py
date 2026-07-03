"""The race-state engine.

This is the source-independent core of the application.  It ingests normalised
:class:`~backend.models.Frame` objects (from the simulator *or* the real
iRacing bridge) and maintains everything a race director needs:

* persistent per-car state (incidents, NIW count, pit stops, laps led, ...)
* the race event log, with resolvable incidents
* steward penalties and notes
* the JSON snapshot the front-end renders

The engine never talks to a data source directly - it just processes frames.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from backend.car_brands import logo_url
from backend.models import (
    Frame, CarFrame,
    LOG_INCIDENT, LOG_OFFTRACK, LOG_PIT, LOG_FLAG, LOG_PENALTY,
    LOG_MESSAGE, LOG_INFO,
    SURFACE_OFF_TRACK, SURFACE_NOT_IN_WORLD, SURFACE_ON_TRACK,
    RESOLUTION_NOTED, RESOLUTION_INVESTIGATING, FINAL_RESOLUTIONS,
    RESOLUTION_DSQ, RESOLUTION_TIME_PENALTY,
)


@dataclass
class Car:
    """Persistent per-car race state, accumulated across frames."""
    car_idx: int
    number: str = "0"
    driver: str = ""
    team: str = ""
    car_class: str = ""
    class_color: str = "#6c7a89"
    class_short: str = ""
    car_brand: str = ""

    position: int = 0
    class_position: int = 0
    lap: int = 0
    laps_completed: int = 0
    lap_dist_pct: float = 0.0
    on_pit_road: bool = False
    surface: str = SURFACE_ON_TRACK
    last_lap: float = 0.0
    best_lap: float = 0.0
    gap_to_leader: float = 0.0
    interval: float = 0.0
    laps_down: int = 0
    speed_ms: float = 0.0
    finished: bool = False

    # Accumulated by the engine.
    incident_points: int = 0
    unresolved: int = 0
    niw_count: int = 0
    pit_stops: int = 0
    laps_led: int = 0
    time_penalty: float = 0.0
    dsq: bool = False
    notes: list[str] = field(default_factory=list)

    # Internal bookkeeping.
    _prev_surface: str = SURFACE_ON_TRACK
    _prev_on_pit: bool = False
    _pit_enter_t: float = 0.0
    _seen: bool = False


@dataclass
class RaceEvent:
    """One line in the race log."""
    id: int
    sim_time: float
    real_time: float
    lap: int
    category: str
    text: str
    car_idx: int = -1
    car_number: str = ""
    inc_points: int = 0
    is_incident: bool = False
    resolved: bool = False
    resolution: Optional[str] = None
    noted: bool = False
    investigating: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id, "sim_time": round(self.sim_time, 1),
            "real_time": self.real_time, "lap": self.lap,
            "category": self.category, "text": self.text,
            "car_idx": self.car_idx, "car_number": self.car_number,
            "inc_points": self.inc_points, "is_incident": self.is_incident,
            "resolved": self.resolved, "resolution": self.resolution,
            "noted": self.noted, "investigating": self.investigating,
        }


# How much of the log to keep in memory / send to clients.
_MAX_EVENTS = 600


class RaceState:
    """Holds the full race state and builds client snapshots."""

    def __init__(self) -> None:
        self.cars: dict[int, Car] = {}
        self.events: list[RaceEvent] = []
        self._next_event_id = 1
        self.event_revision = 0          # bumped whenever the log changes

        self.session_track = "Waiting for data"
        self.session_config = ""
        self.session_length_km = 5.0
        self.session_type = "RACE"
        self.session_state = "INVALID"
        self.session_time = 0.0
        self.session_time_remain = -1.0
        self.session_laps_total = 0
        self.session_laps_remain = 0
        self.flags: list[str] = []
        self.start_lights = "off"
        self.sim_date = ""
        self.weather: dict = {}
        self.track_path: Optional[list] = None
        self.pit_path: Optional[list] = None
        self.track_revision = 0

        self.source_name = "none"
        self.connected = False
        self._leader_idx: Optional[int] = None
        self._prev_flags: list[str] = []

    # -- logging ------------------------------------------------------------

    def _log(self, category: str, text: str, *, car: Optional[Car] = None,
             lap: int = 0, inc_points: int = 0,
             is_incident: bool = False) -> RaceEvent:
        ev = RaceEvent(
            id=self._next_event_id, sim_time=self.session_time,
            real_time=time.time(), lap=lap, category=category, text=text,
            car_idx=car.car_idx if car else -1,
            car_number=car.number if car else "",
            inc_points=inc_points, is_incident=is_incident,
        )
        self._next_event_id += 1
        self.events.append(ev)
        if len(self.events) > _MAX_EVENTS:
            self.events = self.events[-_MAX_EVENTS:]
        self.event_revision += 1
        return ev

    # -- frame ingestion ----------------------------------------------------

    def ingest(self, frame: Frame) -> None:
        """Fold one telemetry frame into the persistent race state."""
        s = frame.session
        self.source_name = frame.source_name
        self.connected = frame.connected
        self.session_track = s.track_name
        self.session_config = s.track_config
        self.session_length_km = s.track_length_km
        self.session_type = s.session_type
        self.session_state = s.session_state
        self.session_time = s.session_time
        self.session_time_remain = s.session_time_remain
        self.session_laps_total = s.session_laps_total
        self.session_laps_remain = s.session_laps_remain
        self.flags = s.flags
        self.start_lights = s.start_lights
        self.sim_date = s.sim_date
        self.weather = {
            "air_temp": s.weather.air_temp, "track_temp": s.weather.track_temp,
            "humidity": s.weather.humidity, "wind_ms": s.weather.wind_ms,
            "skies": s.weather.skies, "precipitation": s.weather.precipitation,
            "is_wet": s.weather.is_wet, "track_wetness": s.weather.track_wetness,
        }

        # Track geometry (may arrive late or change on source switch).
        if frame.track_path is not None and frame.track_path != self.track_path:
            self.track_path = frame.track_path
            self.pit_path = frame.pit_path
            self.track_revision += 1
        elif frame.track_path is None and self.track_path is not None \
                and frame.source_name != getattr(self, "_path_source", None):
            self.track_path = None
            self.pit_path = None
            self.track_revision += 1
        self._path_source = frame.source_name

        # Flag changes.
        if frame.cars and s.flags != self._prev_flags:
            added = [f for f in s.flags if f not in self._prev_flags]
            for f in added:
                if f in ("green", "yellow", "caution", "checkered",
                         "red", "white"):
                    self._log(LOG_FLAG, f"{f.upper()} flag", lap=self._leader_lap())
            self._prev_flags = list(s.flags)

        # Per-car updates.
        for cf in frame.cars:
            self._update_car(cf)

        # Explicit incident events from the source (simulator emits these).
        for inc in frame.incidents:
            car = self.cars.get(inc.car_idx)
            if car is None:
                continue
            car.incident_points += inc.points
            car.unresolved += 1
            label = inc.kind.replace("-", " ").title()
            self._log(LOG_INCIDENT,
                      f"{label} - {inc.points}x",
                      car=car, lap=inc.lap, inc_points=inc.points,
                      is_incident=True)

        # Laps-led accounting.
        self._update_laps_led(frame.cars)

    def _update_car(self, cf: CarFrame) -> None:
        car = self.cars.get(cf.car_idx)
        if car is None:
            car = Car(car_idx=cf.car_idx)
            self.cars[cf.car_idx] = car

        first_seen = not car._seen
        car._seen = True
        car.number = cf.car_number
        car.driver = cf.driver_name
        car.team = cf.team_name or cf.driver_name
        car.car_class = cf.car_class
        car.class_color = cf.class_color
        car.class_short = cf.class_short or cf.car_class
        car.car_brand = cf.car_brand
        car.position = cf.position
        car.class_position = cf.class_position
        car.lap = cf.lap
        car.laps_completed = cf.laps_completed
        car.lap_dist_pct = cf.lap_dist_pct
        car.last_lap = cf.last_lap
        car.best_lap = cf.best_lap
        car.gap_to_leader = cf.gap_to_leader
        car.interval = cf.interval
        car.laps_down = cf.laps_down
        car.speed_ms = cf.speed_ms
        car.finished = cf.finished

        if first_seen:
            car._prev_surface = cf.track_surface
            car._prev_on_pit = cf.on_pit_road
            car.surface = cf.track_surface
            car.on_pit_road = cf.on_pit_road
            return

        # Track-surface transitions -> off-track / NIW logging.
        if cf.track_surface != car._prev_surface:
            if cf.track_surface == SURFACE_OFF_TRACK \
                    and car._prev_surface == SURFACE_ON_TRACK:
                self._log(LOG_OFFTRACK, "Off track", car=car, lap=cf.lap)
            elif cf.track_surface == SURFACE_NOT_IN_WORLD:
                car.niw_count += 1
                self._log(LOG_INFO,
                          f"Not in world (NIW #{car.niw_count})",
                          car=car, lap=cf.lap)
            car._prev_surface = cf.track_surface
        car.surface = cf.track_surface

        # Pit-road transitions.
        if cf.on_pit_road != car._prev_on_pit:
            if cf.on_pit_road:
                car._pit_enter_t = self.session_time
                self._log(LOG_PIT, "Entered pit lane", car=car, lap=cf.lap)
            else:
                dur = max(0.0, self.session_time - car._pit_enter_t)
                car.pit_stops += 1
                self._log(LOG_PIT,
                          f"Exited pit lane - stop #{car.pit_stops} "
                          f"({dur:.1f}s)", car=car, lap=cf.lap)
            car._prev_on_pit = cf.on_pit_road
        car.on_pit_road = cf.on_pit_road

    def _update_laps_led(self, cars: list[CarFrame]) -> None:
        if not cars:
            return
        leader = min(cars, key=lambda c: c.position if c.position > 0 else 999)
        prev = self._leader_idx
        if prev != leader.car_idx and leader.car_idx in self.cars:
            if prev is not None:
                self._log(LOG_INFO,
                          f"Car #{self.cars[leader.car_idx].number} "
                          f"takes the lead", car=self.cars[leader.car_idx],
                          lap=leader.lap)
            self._leader_idx = leader.car_idx
        # Credit a led lap when the leader completes one.
        car = self.cars.get(leader.car_idx)
        if car is not None and not hasattr(car, "_last_led_lap"):
            car._last_led_lap = car.laps_completed
        if car is not None and car.laps_completed > getattr(
                car, "_last_led_lap", car.laps_completed):
            if car.laps_completed > 1:
                car.laps_led += 1
            car._last_led_lap = car.laps_completed

    def _leader_lap(self) -> int:
        if not self.cars:
            return 0
        return max((c.lap for c in self.cars.values()), default=0)

    # -- steward actions ----------------------------------------------------

    def resolve_incident(self, event_id: int, resolution: str,
                         message: str = "", seconds: float = 0.0) -> str:
        """Record a steward decision against an incident in the log."""
        ev = next((e for e in self.events if e.id == event_id), None)
        if ev is None or not ev.is_incident:
            return "incident not found"
        if ev.resolved:
            return "incident already resolved"
        car = self.cars.get(ev.car_idx)
        ev.resolution = resolution

        if resolution == RESOLUTION_NOTED:
            ev.noted = True
        elif resolution == RESOLUTION_INVESTIGATING:
            ev.investigating = True
        else:
            # A final resolution.
            ev.resolved = True
            ev.noted = ev.investigating = False
            if car is not None and car.unresolved > 0:
                car.unresolved -= 1

        if car is not None:
            if resolution == RESOLUTION_TIME_PENALTY and seconds:
                car.time_penalty += seconds
            if resolution == RESOLUTION_DSQ:
                car.dsq = True

        suffix = f" - {message}" if message else ""
        if resolution == RESOLUTION_TIME_PENALTY and seconds:
            suffix = f" ({seconds:+.0f}s){suffix}"
        self._log(LOG_PENALTY,
                  f"L{ev.lap} incident #{event_id}: {resolution}{suffix}",
                  car=car, lap=ev.lap)
        self.event_revision += 1
        return f"incident #{event_id} -> {resolution}"

    def car_action(self, car_idx: int, command: str, message: str = "",
                   seconds: float = 0.0) -> str:
        """Apply a steward action directed at a whole car."""
        car = self.cars.get(car_idx)
        if car is None:
            return "car not found"
        cmd = command.lower()
        if cmd == "0x":
            car.incident_points += 0
            car.unresolved += 1
            self._log(LOG_INCIDENT, "0x issued by steward", car=car,
                      lap=car.lap, inc_points=0, is_incident=True)
            return f"0x issued to car #{car.number}"
        if cmd == "dsq":
            car.dsq = True
            self._log(LOG_PENALTY,
                      f"DISQUALIFIED{(' - ' + message) if message else ''}",
                      car=car, lap=car.lap)
            return f"car #{car.number} disqualified"
        if cmd == "time_penalty":
            car.time_penalty += seconds
            self._log(LOG_PENALTY, f"Time penalty {seconds:+.0f}s",
                      car=car, lap=car.lap)
            return f"car #{car.number} time penalty {seconds:+.0f}s"
        if cmd == "clear_penalties":
            self._log(LOG_INFO, "iRacing penalties cleared", car=car,
                      lap=car.lap)
            return f"penalties cleared for car #{car.number}"
        if cmd in ("notify", "wave_around", "eol"):
            label = {"notify": "Notified", "wave_around": "Wave-around",
                     "eol": "Sent to end of line"}[cmd]
            self._log(LOG_MESSAGE,
                      f"{label}{(' - ' + message) if message else ''}",
                      car=car, lap=car.lap)
            return f"{label.lower()} - car #{car.number}"
        if cmd == "add_note":
            car.notes.append(message)
            self._log(LOG_INFO, f"Note: {message}", car=car, lap=car.lap)
            return f"note added to car #{car.number}"
        return f"unknown car command '{command}'"

    def rc_message(self, target: str, text: str,
                   car_idx: int = -1) -> str:
        car = self.cars.get(car_idx)
        who = f"to car #{car.number}" if car else f"to {target}"
        self._log(LOG_MESSAGE, f"RC message {who}: {text}", car=car,
                  lap=self._leader_lap())
        return f"message sent {who}"

    def note_system(self, text: str, category: str = LOG_INFO) -> None:
        """Log a system / command-result line."""
        self._log(category, text, lap=self._leader_lap())

    # -- snapshots ----------------------------------------------------------

    def total_incidents(self) -> int:
        return sum(c.incident_points for c in self.cars.values())

    def snapshot(self) -> dict:
        """Fast-changing data sent to clients ~10x per second."""
        cars = sorted(self.cars.values(),
                      key=lambda c: c.position if c.position > 0 else 999)

        # Best-lap highlighting.
        best_overall = min((c.best_lap for c in cars if c.best_lap > 0),
                           default=0.0)
        best_by_class: dict[str, float] = {}
        for c in cars:
            if c.best_lap > 0:
                b = best_by_class.get(c.car_class, 0.0)
                if b == 0.0 or c.best_lap < b:
                    best_by_class[c.car_class] = c.best_lap

        car_list = []
        for c in cars:
            car_list.append({
                "car_idx": c.car_idx, "number": c.number,
                "driver": c.driver, "team": c.team,
                "car_class": c.car_class, "class_color": c.class_color,
                "class_short": c.class_short,
                "brand": c.car_brand,
                "brand_logo": logo_url(c.car_brand),
                "position": c.position, "class_position": c.class_position,
                "lap": c.lap, "laps_completed": c.laps_completed,
                "lap_dist_pct": c.lap_dist_pct,
                "on_pit_road": c.on_pit_road, "surface": c.surface,
                "in_world": c.surface != SURFACE_NOT_IN_WORLD,
                "off_track": c.surface == SURFACE_OFF_TRACK,
                "last_lap": c.last_lap, "best_lap": c.best_lap,
                "gap": c.gap_to_leader, "interval": c.interval,
                "laps_down": c.laps_down, "laps_led": c.laps_led,
                "speed_ms": c.speed_ms,
                "incidents": c.incident_points, "unresolved": c.unresolved,
                "niw": c.niw_count, "pit_stops": c.pit_stops,
                "time_penalty": c.time_penalty, "dsq": c.dsq,
                "finished": c.finished,
                "notes": len(c.notes),
                "best_overall": c.best_lap > 0 and c.best_lap == best_overall,
                "best_in_class": c.best_lap > 0 and
                c.best_lap == best_by_class.get(c.car_class, -1),
            })

        return {
            "source": self.source_name,
            "connected": self.connected,
            "session": {
                "track": self.session_track, "config": self.session_config,
                "length_km": self.session_length_km,
                "type": self.session_type, "state": self.session_state,
                "time": self.session_time,
                "time_remain": self.session_time_remain,
                "laps_total": self.session_laps_total,
                "laps_remain": self.session_laps_remain,
                "leader_lap": self._leader_lap(),
                "total_incidents": self.total_incidents(),
                "flags": self.flags, "start_lights": self.start_lights,
                "sim_date": self.sim_date,
                "car_count": len(cars),
            },
            "weather": self.weather,
            "cars": car_list,
        }

    def events_payload(self, limit: int = 250) -> dict:
        return {
            "revision": self.event_revision,
            "total": len(self.events),
            "events": [e.to_dict() for e in self.events[-limit:]],
        }

    def track_payload(self) -> dict:
        return {
            "revision": self.track_revision,
            "path": self.track_path,
            "pit": self.pit_path,
            "length_km": self.session_length_km,
        }
