"""Incidents for REGIE while a recorded REPLAY plays — taken from the race log
written LIVE during that race (logs/*_race.jsonl, iracing_race_logger.py).

Thomas 2026-11-19: in a replay the dashboard's own detectors miss real
contacts (Oschersleben HEAT 1: the Brandt / Krumpschmied contact at 1106 s was
in the live log, the replay found only an off-track) and invent others (all
seven cars "lost control" at 252.9 s). The live log has the incidents with
their session time, so the director gets them when the replay reaches them.

Which log: same hosted session (subsession_id) and session number. The logger
also writes a log while a replay plays (same ids) — the LIVE one is the
earliest file of that session (the file name starts with its start time).

Pure logic, no SDK. Tested by test_replay_log_events.py."""
from __future__ import annotations

import json
from pathlib import Path

EVENT_TYPES = ("collision", "lost_control")
JUMP_S = 5.0            # replay time moved more than this (seek / jump): nothing emitted for the gap
SAME_S = 3.0            # the same car + type again within this: one event (the detector reports in bursts)


def _first_line(p: Path) -> dict:
    try:
        with open(p, encoding="utf-8") as f:
            return json.loads(f.readline() or "{}")
    except (OSError, ValueError):
        return {}


def find_live_log(logs_dir, subsession_id, session_num) -> Path | None:
    """The race log written live for this session, or None."""
    d = Path(logs_dir)
    if not subsession_id or not d.is_dir():
        return None
    hits = []
    for p in d.glob("*_race.jsonl"):
        m = _first_line(p)
        if m.get("type") != "session_start":
            continue
        if str(m.get("subsession_id")) == str(subsession_id) and int(m.get("session_num") or -1) == int(session_num):
            hits.append(p)
    return min(hits, key=lambda p: p.name) if hits else None


def load_incidents(path) -> list:
    """collision / lost_control events of the log, sorted by session time,
    bursts of the same car + type merged."""
    out, last = [], {}
    try:
        f = open(path, encoding="utf-8")
    except OSError:
        return out
    with f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") != "incident":
                continue
            typ = e.get("incident_type") or e.get("kind")
            if typ not in EVENT_TYPES:
                continue
            try:
                t = float(e.get("t_session"))
                idx = int(e.get("car_idx"))
            except (TypeError, ValueError):
                continue
            key = (idx, typ)
            if key in last and abs(t - last[key]) < SAME_S:
                continue
            last[key] = t
            out.append({"t_session": t, "car_idx": idx, "type": typ,
                        "car_number": str(e.get("car_number") or ""), "name": e.get("driver") or e.get("name") or "",
                        "with_car_idx": e.get("with_car_idx")})
    out.sort(key=lambda x: x["t_session"])
    return out


class ReplayLogEvents:
    """feed(subsession, session_num, t_session) -> the log's incidents the
    replay has just passed (each once per pass; a seek emits nothing)."""

    def __init__(self, logs_dir):
        self.logs_dir = Path(logs_dir)
        self.key = None
        self.path = None
        self.events: list = []
        self.last_t = None

    def _load(self, subsession, session_num):
        key = (str(subsession), int(session_num or 0))
        if key == self.key:
            return
        self.key, self.last_t = key, None
        self.path = find_live_log(self.logs_dir, subsession, session_num)
        self.events = load_incidents(self.path) if self.path else []
        if self.path:
            print(f"[regie] replay: incidents from the race log {self.path.name} ({len(self.events)})")

    @property
    def active(self) -> bool:
        return self.path is not None

    def feed(self, subsession, session_num, t_session) -> list:
        self._load(subsession, session_num)
        if not self.path or t_session is None:
            return []
        t = float(t_session)
        prev, self.last_t = self.last_t, t
        if prev is None or t < prev or t - prev > JUMP_S:
            return []                                # first look / jumped: not "just passed"
        return [dict(e, session_num=int(session_num or 0), source="racelog")
                for e in self.events if prev < e["t_session"] <= t]

    def reset(self):
        self.last_t = None
