"""Shared data structures.

A *data source* (the simulator or the real iRacing SDK) produces a normalized
:class:`Frame` every poll.  The race-state engine ingests frames and maintains
the persistent, source-independent race state (event log, incident records,
penalties, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------------------
# Vocabulary - kept as plain strings so everything serialises to JSON cleanly.
# --------------------------------------------------------------------------

# Session types reported by iRacing.
SESSION_TYPES = ("UNKNOWN", "PRACTICE", "OPEN QUALIFY", "LONE QUALIFY",
                 "WARMUP", "RACE", "TESTING")

# Session status / state.
SESSION_STATES = ("INVALID", "GET IN CAR", "WARMUP", "PARADE LAPS",
                  "RACING", "CHECKERED", "COOLDOWN")

# Track surface for a single car.
SURFACE_NOT_IN_WORLD = "NotInWorld"
SURFACE_OFF_TRACK = "OffTrack"
SURFACE_IN_PIT_STALL = "InPitStall"
SURFACE_APPROACHING_PITS = "ApproachingPits"
SURFACE_ON_TRACK = "OnTrack"

# Race log categories - used by the front-end filter buttons.
LOG_INCIDENT = "incident"      # a car earned incident points
LOG_OFFTRACK = "offtrack"      # a car went off track
LOG_PIT = "pit"                # pit entry / exit
LOG_FLAG = "flag"              # flag / FCY / restart events
LOG_PENALTY = "penalty"        # a steward penalty was issued
LOG_MESSAGE = "message"        # a race control / RC text message
LOG_INFO = "info"              # session changes, NIW, general info

LOG_CATEGORIES = (LOG_INCIDENT, LOG_OFFTRACK, LOG_PIT, LOG_FLAG,
                  LOG_PENALTY, LOG_MESSAGE, LOG_INFO)

# Incident-resolution outcomes a steward can record against an incident.
RESOLUTION_NOTED = "NOTED"
RESOLUTION_INVESTIGATING = "UNDER INVESTIGATION"
RESOLUTION_NO_ACTION = "NO ACTION"
RESOLUTION_RACE_INCIDENT = "RACE INCIDENT"
RESOLUTION_WARNING = "WARNING"
RESOLUTION_DRIVE_THROUGH = "DRIVE THROUGH"
RESOLUTION_STOP_GO = "STOP/GO"
RESOLUTION_TIME_PENALTY = "TIME PENALTY"
RESOLUTION_DSQ = "DSQ"

# Outcomes that mark an incident as finally resolved (cannot be changed).
FINAL_RESOLUTIONS = {RESOLUTION_NO_ACTION, RESOLUTION_RACE_INCIDENT,
                     RESOLUTION_DRIVE_THROUGH, RESOLUTION_STOP_GO,
                     RESOLUTION_TIME_PENALTY, RESOLUTION_DSQ}


# --------------------------------------------------------------------------
# Normalised frame produced by a data source on every poll.
# --------------------------------------------------------------------------

@dataclass
class CarFrame:
    """A single car's state for one telemetry frame."""
    car_idx: int
    car_number: str = "0"
    driver_name: str = ""
    team_name: str = ""
    car_class: str = ""
    class_id: int = 0
    class_color: str = "#6c7a89"
    class_short: str = ""
    car_brand: str = ""            # manufacturer slug, e.g. 'porsche'
    position: int = 0
    class_position: int = 0
    lap: int = 0
    laps_completed: int = 0
    lap_dist_pct: float = 0.0
    on_pit_road: bool = False
    track_surface: str = SURFACE_ON_TRACK
    last_lap: float = 0.0          # seconds, 0 == no time yet
    best_lap: float = 0.0          # seconds, 0 == no time yet
    gap_to_leader: float = 0.0     # seconds behind the leader
    interval: float = 0.0          # seconds to the car directly ahead
    laps_down: int = 0             # whole laps behind the leader (0 == lead lap)
    laps_led: int = 0
    speed_ms: float = 0.0          # current speed, metres per second
    is_pace_car: bool = False
    is_player: bool = False
    finished: bool = False

    @property
    def in_world(self) -> bool:
        return self.track_surface != SURFACE_NOT_IN_WORLD


@dataclass
class IncidentEvent:
    """A discrete incident detected by a data source during one frame."""
    car_idx: int
    points: int = 1                # 0, 1, 2 or 4
    kind: str = "incident"         # 'off-track', 'contact', 'spin', 'incident'
    lap: int = 0
    other_car_idx: Optional[int] = None


@dataclass
class Weather:
    air_temp: float = 0.0          # deg C
    track_temp: float = 0.0        # deg C
    humidity: float = 0.0          # 0..1
    wind_ms: float = 0.0           # m/s
    skies: str = "Clear"
    precipitation: float = 0.0     # 0..1
    is_wet: bool = False
    track_wetness: str = "Dry"


@dataclass
class SessionFrame:
    track_name: str = "Unknown Track"
    track_config: str = ""
    track_length_km: float = 5.0
    session_type: str = "RACE"
    session_state: str = "RACING"
    session_time: float = 0.0          # seconds elapsed in session
    session_time_remain: float = 0.0   # seconds remaining (negative if unlimited)
    session_laps_total: int = 0        # 0 == time limited / unlimited
    session_laps_remain: int = 0
    flags: list[str] = field(default_factory=list)
    start_lights: str = "off"          # off / ready / set / go
    weather: Weather = field(default_factory=Weather)
    sim_date: str = ""                 # display date the race started on


@dataclass
class Frame:
    """Everything a data source reports for one poll cycle."""
    session: SessionFrame = field(default_factory=SessionFrame)
    cars: list[CarFrame] = field(default_factory=list)
    incidents: list[IncidentEvent] = field(default_factory=list)
    track_path: Optional[list[list[float]]] = None  # normalised closed loop
    pit_path: Optional[list[list[float]]] = None     # normalised pit lane
    source_name: str = "unknown"
    connected: bool = False
