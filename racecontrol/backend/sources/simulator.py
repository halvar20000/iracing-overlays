"""A self-contained multi-class race simulator.

This lets the whole application run - and look alive - on any computer, with
or without iRacing installed.  It is invaluable for development, for demos and
for recording YouTube footage without needing a live session.

The simulator produces exactly the same normalised :class:`Frame` that the
real iRacing bridge produces, so nothing downstream can tell the difference.
"""

from __future__ import annotations

import math
import random
import time
from typing import Optional

from backend.models import (
    CarFrame, Frame, IncidentEvent, SessionFrame, Weather,
    SURFACE_ON_TRACK, SURFACE_OFF_TRACK, SURFACE_IN_PIT_STALL,
    SURFACE_NOT_IN_WORLD,
)
from backend.sources.base import DataSource
from backend.tracks import load_track


# Fictional driver pool - no resemblance to real people is intended.
_DRIVER_NAMES = [
    "Lukas Brandt", "Mateo Rossi", "Erik Lindqvist", "Hugo Moreau",
    "Daniel Kovac", "Sven Aaltonen", "Marco Bianchi", "Tom Whitfield",
    "Niklas Bauer", "Diego Fuentes", "Ade Okafor", "Yuki Tanaka",
    "Rafael Costa", "Owen Pryce", "Felix Hartmann", "Sasha Petrov",
    "Liam Doyle", "Andre Schulz", "Pavel Novak", "Theo Lefevre",
    "Jonas Berg", "Carlos Mendez", "Henrik Olsen", "Bruno Almeida",
    "Max Verhoeven", "Ivan Sokolov", "Kenji Mori", "Leon Fischer",
    "Gabriel Silva", "Aaron Mills", "Stefan Vogel", "Milan Horak",
]

_GT3_TEAMS = [
    "Apex Motorsport", "Nordwind Racing", "Velocità Squadra", "Crown GT",
    "Falkenberg AMG", "Iron Vipers", "Lake District RT", "Meridian Sport",
]
_GT4_TEAMS = [
    "Junior Aces", "Tarmac Bandits", "Blue Ridge RT", "Petrolheads GT",
    "Sundown Racing", "Garage 56 Club",
]


def _make_track_path(samples: int = 220) -> list[list[float]]:
    """Build a stylised closed road-course loop, resampled to roughly equal
    arc-length spacing so cars move smoothly when placed by lap-distance %."""
    raw: list[tuple[float, float]] = []
    fine = 2000
    for i in range(fine):
        t = 2.0 * math.pi * i / fine
        x = 0.50 + 0.40 * math.cos(t) + 0.055 * math.cos(3 * t) - 0.03 * math.cos(2 * t)
        y = 0.50 + 0.31 * math.sin(t) + 0.105 * math.sin(2 * t) - 0.055 * math.sin(4 * t)
        raw.append((x, y))
    # Cumulative arc length.
    cum = [0.0]
    for i in range(1, fine + 1):
        a = raw[i % fine]
        b = raw[i - 1]
        cum.append(cum[-1] + math.hypot(a[0] - b[0], a[1] - b[1]))
    total = cum[-1]
    # Resample at equal arc-length intervals.
    path: list[list[float]] = []
    j = 0
    for s in range(samples):
        target = total * s / samples
        while j < fine and cum[j + 1] < target:
            j += 1
        seg = cum[j + 1] - cum[j] or 1e-9
        f = (target - cum[j]) / seg
        a, b = raw[j % fine], raw[(j + 1) % fine]
        path.append([round(a[0] + (b[0] - a[0]) * f, 5),
                     round(a[1] + (b[1] - a[1]) * f, 5)])
    return path


def _make_pit_path(track: list[list[float]]) -> list[list[float]]:
    """A short pit lane running inside the start/finish straight."""
    n = len(track)
    pit = []
    for k in range(-14, 15):
        idx = k % n
        px, py = track[idx]
        cx, cy = 0.5, 0.5
        # Pull the point ~7% toward the track centre.
        pit.append([round(px + (cx - px) * 0.13, 5),
                    round(py + (cy - py) * 0.13, 5)])
    return pit


class _SimCar:
    """Mutable state for one simulated car. ``dist`` is the master clock:
    laps completed == int(dist), lap-distance % == dist % 1.0."""

    def __init__(self, idx: int, number: str, driver: str, team: str,
                 car_class: str, class_id: int, class_color: str,
                 base_laptime: float):
        self.idx = idx
        self.number = number
        self.driver = driver
        self.team = team
        self.car_class = car_class
        self.class_id = class_id
        self.class_color = class_color
        self.base_laptime = base_laptime
        self.brand = ""               # manufacturer slug, set by the field builder

        self.dist = 0.0
        self.current_laptime = base_laptime
        self.last_lap = 0.0
        self.best_lap = 0.0
        self.laps_led = 0
        self.finished = False
        self.finish_dist = 0.0

        self._lap_clock = 0.0
        self._sector_clock = 0.0
        self._last_floor = 0
        self._last_sector = 0
        self.last_sectors: list[float] = [0.0, 0.0, 0.0]

        self.surface = SURFACE_ON_TRACK
        self.on_pit_road = False
        self.speed_ms = 0.0

        # Transient timers.
        self._slow_timer = 0.0     # off-track / spin recovery
        self._niw_timer = 0.0      # towed / not in world
        self._pit_timer = 0.0      # time being serviced
        self._pit_done_lap = -1    # avoid pitting twice on the same lap
        self.pit_lap = 0           # scheduled pit lap

    @property
    def laps_completed(self) -> int:
        return int(self.dist)

    @property
    def lap_dist_pct(self) -> float:
        return self.dist - int(self.dist)

    @property
    def lap_number(self) -> int:
        """1-based lap the car is currently on."""
        return self.laps_completed + 1


class SimulatorSource(DataSource):
    """Generates a believable GT3 + GT4 endurance-style race."""

    name = "simulator"

    def __init__(self, num_gt3: int = 12, num_gt4: int = 10,
                 race_minutes: float = 45.0, track: str = "silverstone_2019_gp"):
        self.race_seconds = race_minutes * 60.0
        # Run the simulated race on a real circuit when one is available,
        # falling back to a stylised generated loop otherwise.
        real = load_track(track)
        if real:
            self._track = real["path"]
            self._pit = real["pit"]
            self._track_len_km = real["length_km"]
            self._track_display = "Silverstone Circuit"
            self._track_config = "Grand Prix"
        else:
            self._track = _make_track_path()
            self._pit = _make_pit_path(self._track)
            self._track_len_km = 5.13
            self._track_display = "Simulation Circuit"
            self._track_config = "Grand Prix"

        self._cars: list[_SimCar] = []
        self._build_field(num_gt3, num_gt4)

        self._connected = False
        self._last_poll = 0.0
        self.session_time = 0.0
        self._rng = random.Random(20260522)

        # Caution / full-course-yellow state.
        self.caution = False
        self._caution_end_time = 0.0
        self._next_random_caution = 600.0   # earliest auto caution
        self.race_done = False
        self._checkered_at: Optional[float] = None
        self._pending: list[IncidentEvent] = []
        self._command_log: list[str] = []

    # -- field set-up -------------------------------------------------------

    # Manufacturers entered per class (slugs that have a bundled logo).
    _GT3_BRANDS = ["porsche", "bmw", "ferrari", "audi", "mercedes", "mclaren",
                   "lamborghini", "aston-martin", "acura", "chevrolet",
                   "ford", "cadillac"]
    _GT4_BRANDS = ["bmw", "porsche", "mercedes", "mclaren", "aston-martin",
                   "toyota", "ford", "audi", "chevrolet", "hyundai"]

    def _build_field(self, num_gt3: int, num_gt4: int) -> None:
        rng = random.Random(7)
        names = _DRIVER_NAMES[:]
        rng.shuffle(names)
        numbers = rng.sample(range(2, 99), num_gt3 + num_gt4)
        gt3_brands = self._GT3_BRANDS[:]
        gt4_brands = self._GT4_BRANDS[:]
        rng.shuffle(gt3_brands)
        rng.shuffle(gt4_brands)
        idx = 0
        for i in range(num_gt3):
            car = _SimCar(
                idx, str(numbers[idx]), names[idx % len(names)],
                _GT3_TEAMS[i % len(_GT3_TEAMS)], "GT3", 1, "#e6433a",
                base_laptime=104.0 + rng.uniform(-1.4, 2.6))
            car.brand = gt3_brands[i % len(gt3_brands)]
            self._cars.append(car)
            idx += 1
        for i in range(num_gt4):
            car = _SimCar(
                idx, str(numbers[idx]), names[idx % len(names)],
                _GT4_TEAMS[i % len(_GT4_TEAMS)], "GT4", 2, "#2f86d6",
                base_laptime=117.0 + rng.uniform(-1.2, 3.0))
            car.brand = gt4_brands[i % len(gt4_brands)]
            self._cars.append(car)
            idx += 1
        # Spread the field around the opening lap and stagger pit windows.
        for c in self._cars:
            c.dist = rng.uniform(0.0, 0.22)
            c.current_laptime = c.base_laptime
            c.pit_lap = rng.randint(int(self.race_seconds / c.base_laptime * 0.40),
                                    int(self.race_seconds / c.base_laptime * 0.62))

    # -- DataSource interface ----------------------------------------------

    def connect(self) -> bool:
        self._connected = True
        self._last_poll = time.monotonic()
        return True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def poll(self) -> Optional[Frame]:
        if not self._connected:
            return None
        now = time.monotonic()
        dt = min(now - self._last_poll, 0.5)   # cap to survive UI stalls
        self._last_poll = now
        if dt > 0:
            self._advance(dt)
        return self._build_frame()

    # -- simulation ---------------------------------------------------------

    def _advance(self, dt: float) -> None:
        self._pending.clear()
        if not self.race_done:
            self.session_time += dt

        # End-of-caution handling.
        if self.caution and self.session_time >= self._caution_end_time:
            self.caution = False
            self._command_log.append("Full course yellow ended - GREEN FLAG")

        # Random automatic caution (adds drama to the demo).
        if (not self.caution and not self.race_done
                and self.session_time > self._next_random_caution
                and self._rng.random() < dt * 0.004):
            self._start_caution(reason="random full course yellow")
            self._next_random_caution = self.session_time + 540.0

        leader = self._leader()
        leader_done = leader.dist if leader else 0.0

        for c in self._cars:
            self._advance_car(c, dt, leader_done)

        # Leading-lap accounting.
        new_leader = self._leader()
        if new_leader and new_leader.laps_completed > 0:
            new_leader.laps_led += 0  # laps_led updated on lap completion below

        # Checkered flag once race time elapses (leader must finish the lap).
        if not self.race_done and self.session_time >= self.race_seconds:
            if self._checkered_at is None:
                self._checkered_at = new_leader.dist if new_leader else 0.0
            if new_leader and new_leader.dist >= math.ceil(self._checkered_at):
                self.race_done = True
                self._command_log.append("CHECKERED FLAG - race complete")

    def _advance_car(self, c: _SimCar, dt: float, leader_dist: float) -> None:
        if c.finished:
            c.speed_ms = 0.0
            return

        # Recover from transient states.
        if c._niw_timer > 0:
            c._niw_timer -= dt
            c.surface = SURFACE_NOT_IN_WORLD
            c.speed_ms = 0.0
            return
        if c._slow_timer > 0:
            c._slow_timer -= dt
            if c._slow_timer <= 0:
                c.surface = SURFACE_ON_TRACK

        # Pit-stop logic.
        if c._pit_timer > 0:
            c._pit_timer -= dt
            c.on_pit_road = True
            c.surface = SURFACE_IN_PIT_STALL
            c.speed_ms = 0.0
            if c._pit_timer <= 0:
                c.on_pit_road = False
                c.surface = SURFACE_ON_TRACK
            return
        if (not c.on_pit_road and c.laps_completed >= c.pit_lap
                and c._pit_done_lap != c.laps_completed
                and 0.93 < c.lap_dist_pct < 0.99 and not self.caution):
            c._pit_timer = self._rng.uniform(26.0, 34.0)
            c._pit_done_lap = c.laps_completed
            c.on_pit_road = True
            return

        # Speed factor for this tick.
        factor = 1.0
        if c._slow_timer > 0:
            factor = 0.30
            c.surface = SURFACE_OFF_TRACK
        elif self.caution:
            factor = 0.52
            # Catch the car ahead to form a pace train, but never pass it.
            ahead = self._car_ahead(c)
            if ahead and (ahead.dist - c.dist) > 0.014:
                factor = 0.78

        # Per-lap pace noise.
        lap_t = c.current_laptime
        c.dist += (dt / lap_t) * factor

        # Don't overtake under caution.
        if self.caution:
            ahead = self._car_ahead(c)
            if ahead and c.dist > ahead.dist - 0.010:
                c.dist = ahead.dist - 0.010

        c.speed_ms = (self._track_len_km * 1000.0 / lap_t) * factor
        c._lap_clock += dt
        c._sector_clock += dt

        # Sector crossings.
        sector = min(2, int(c.lap_dist_pct * 3.0))
        if sector != c._last_sector:
            c.last_sectors[c._last_sector] = round(c._sector_clock, 3)
            c._sector_clock = 0.0
            c._last_sector = sector

        # Lap completion.
        if c.laps_completed > c._last_floor:
            c._last_floor = c.laps_completed
            c.last_lap = round(c._lap_clock, 3)
            c._lap_clock = 0.0
            if c.best_lap == 0.0 or c.last_lap < c.best_lap:
                c.best_lap = c.last_lap
            # Resample pace for the new lap.
            c.current_laptime = c.base_laptime + self._rng.uniform(-0.7, 1.6)
            if self._leader() is c:
                c.laps_led += 1
            # Mark a finisher once the race is over.
            if self.race_done:
                c.finished = True
                c.finish_dist = c.dist

        # Random incidents & off-tracks (suppressed during caution).
        if not self.caution and c._slow_timer <= 0 and c._pit_timer <= 0:
            p_inc = dt * 0.0036
            if self._rng.random() < p_inc:
                pts = self._rng.choices([0, 1, 2, 4], weights=[6, 52, 24, 18])[0]
                kind = ({0: "investigation", 1: "off-track", 2: "spin",
                         4: "contact"})[pts]
                c._slow_timer = self._rng.uniform(3.0, 7.5)
                c.surface = SURFACE_OFF_TRACK
                self._pending.append(IncidentEvent(
                    car_idx=c.idx, points=pts, kind=kind, lap=c.lap_number))
            elif self._rng.random() < dt * 0.010:
                # Minor off-track excursion, no points.
                c._slow_timer = self._rng.uniform(1.5, 3.0)
                c.surface = SURFACE_OFF_TRACK
            elif self._rng.random() < dt * 0.0006:
                # Rare tow / not-in-world.
                c._niw_timer = self._rng.uniform(6.0, 14.0)

    # -- helpers ------------------------------------------------------------

    def _order(self) -> list[_SimCar]:
        return sorted(self._cars, key=lambda c: c.dist, reverse=True)

    def _leader(self) -> Optional[_SimCar]:
        return self._order()[0] if self._cars else None

    def _car_ahead(self, car: _SimCar) -> Optional[_SimCar]:
        ahead = [c for c in self._cars if c.dist > car.dist]
        return min(ahead, key=lambda c: c.dist) if ahead else None

    def _start_caution(self, reason: str) -> None:
        self.caution = True
        self._caution_end_time = self.session_time + self._rng.uniform(150.0, 220.0)
        self._command_log.append(f"FULL COURSE YELLOW - {reason}")

    # -- frame building -----------------------------------------------------

    def _build_frame(self) -> Frame:
        order = self._order()
        leader = order[0] if order else None
        leader_dist = leader.dist if leader else 0.0
        leader_pace = leader.current_laptime if leader else 105.0

        # Per-class ordering.
        class_counts: dict[int, int] = {}
        cars: list[CarFrame] = []
        # Map idx -> position for interval calculation.
        pos_of = {c.idx: i + 1 for i, c in enumerate(order)}

        for pos, c in enumerate(order, start=1):
            class_counts[c.class_id] = class_counts.get(c.class_id, 0) + 1
            laps_behind = leader_dist - c.dist
            laps_down = int(laps_behind) if laps_behind >= 1.0 else 0
            gap_sec = round((laps_behind - laps_down) * leader_pace, 2)

            # Interval to the car directly ahead on the road.
            interval = 0.0
            if pos > 1:
                ahead = order[pos - 2]
                interval = round((ahead.dist - c.dist) * c.current_laptime, 2)

            surface = c.surface
            cf = CarFrame(
                car_idx=c.idx, car_number=c.number, driver_name=c.driver,
                team_name=c.team, car_class=c.car_class, class_id=c.class_id,
                class_color=c.class_color, class_short=c.car_class,
                car_brand=c.brand,
                position=pos,
                class_position=class_counts[c.class_id],
                lap=c.lap_number, laps_completed=c.laps_completed,
                lap_dist_pct=round(c.lap_dist_pct, 5),
                on_pit_road=c.on_pit_road, track_surface=surface,
                last_lap=c.last_lap, best_lap=c.best_lap,
                gap_to_leader=gap_sec, interval=interval, laps_down=laps_down,
                laps_led=c.laps_led, speed_ms=round(c.speed_ms, 1),
                finished=c.finished,
            )
            cars.append(cf)

        # Session.
        flags: list[str] = []
        state = "RACING"
        if self.race_done:
            flags = ["checkered"]
            state = "CHECKERED"
        elif self.caution:
            flags = ["yellow", "caution"]
        else:
            flags = ["green"]
        remain = max(0.0, self.race_seconds - self.session_time)
        if 0 < remain < 90 and leader:
            flags.append("white")

        session = SessionFrame(
            track_name=self._track_display, track_config=self._track_config,
            track_length_km=self._track_len_km,
            session_type="RACE", session_state=state,
            session_time=self.session_time,
            session_time_remain=remain,
            session_laps_total=0,
            session_laps_remain=0,
            flags=flags, start_lights="off",
            weather=self._weather(),
            sim_date="2026-05-22",
        )

        return Frame(
            session=session, cars=cars, incidents=list(self._pending),
            track_path=self._track, pit_path=self._pit,
            source_name=self.name, connected=True,
        )

    def _weather(self) -> Weather:
        t = self.session_time
        return Weather(
            air_temp=round(21.0 + 1.5 * math.sin(t / 900.0), 1),
            track_temp=round(31.0 + 3.0 * math.sin(t / 800.0), 1),
            humidity=round(0.45 + 0.05 * math.sin(t / 700.0), 2),
            wind_ms=round(2.4 + 1.2 * math.sin(t / 300.0), 1),
            skies="Partly Cloudy", precipitation=0.0,
            is_wet=False, track_wetness="Dry",
        )

    # -- race-control commands ---------------------------------------------

    def send_command(self, command: str, **params) -> str:
        cmd = command.lower()
        if cmd in ("pace_deploy", "fcy"):
            if self.caution:
                return "Full course yellow already active"
            self._start_caution(reason="race control")
            return "Pace car deployed - FULL COURSE YELLOW"
        if cmd in ("pace_end", "green"):
            if not self.caution:
                return "No caution active"
            self.caution = False
            return "Pace car in - GREEN FLAG"
        if cmd == "red_flag":
            return "RED FLAG posted to race log"
        if cmd == "green_flag":
            return "GREEN FLAG posted to race log"
        # Penalties / messages just acknowledge in the simulator.
        return f"[SIM] command '{command}' acknowledged"

    def drain_command_log(self) -> list[str]:
        out = self._command_log[:]
        self._command_log.clear()
        return out
