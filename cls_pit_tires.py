"""
Live pit-stop tire data from the drivers' Race Loggers, via CLS
---------------------------------------------------------------
iRacing shows tread depth to the DRIVER only (no CarIdx array), so the
broadcast PC cannot read anybody's tires. Each driver's RaceLogger.exe sends
its own stop to the league site the moment the car leaves the pit lane
(POST /api/race-log/live); this module reads them back:

    GET https://league.simracing-hub.com/api/overlay/pit-tires?subsession=<id>&after=<id>

`subsession` is iRacing's WeekendInfo.SubSessionID — the hosted race every
driver and the broadcast PC share — so a second race running at the same time
never mixes in. `after` makes each poll return only what is new.

Polls only while an overlay page is actually asking (`want()` within the last
DEMAND_S seconds), every POLL_S seconds. No page in OBS = no traffic.
Drivers without the logger (or without a league key) simply never appear.
urllib only, like cls_proam.py.
"""

import json
import threading
import time
import urllib.parse
import urllib.request

API_BASE = "https://league.simracing-hub.com"
POLL_S = 4.0         # a stop shows up within ~5 s of the car leaving the pit lane
DEMAND_S = 60.0      # keep polling this long after the page last asked
RETRY_S = 15.0       # after a failed fetch
KEEP_STOPS = 400     # per race; a 6 h IEC race with 40 cars stays far below


class PitTireFeed:
    def __init__(self, api_base: str = API_BASE, fetch=None, clock=time.time):
        self.api_base = api_base.rstrip("/")
        self._fetch = fetch or self._http_get
        self._clock = clock
        self._lock = threading.Lock()
        self._subsession = None
        self._stops: list = []
        self._after = 0
        self._wanted_at = 0.0
        self._last_ok = None
        self._last_error = ""
        self._thread = None

    # ----- called by the Flask routes -------------------------------------
    def want(self, subsession) -> None:
        """An overlay page is showing. A NEW race (other subsession id)
        starts with an empty list."""
        with self._lock:
            self._wanted_at = self._clock()
            if subsession != self._subsession:
                self._subsession = subsession
                self._stops = []
                self._after = 0

    def stops(self) -> list:
        with self._lock:
            return list(self._stops)

    def status(self) -> dict:
        with self._lock:
            return {
                "subsession": self._subsession,
                "stops": len(self._stops),
                "last_ok": self._last_ok,
                "last_error": self._last_error,
                "polling": self._active(),
            }

    # ----- polling --------------------------------------------------------
    def _active(self) -> bool:
        return self._clock() - self._wanted_at < DEMAND_S

    def _http_get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "iracing-overlays/pit-tires"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def poll_once(self) -> bool:
        """One fetch + merge. Returns True on success (also used by tests)."""
        with self._lock:
            sub, after = self._subsession, self._after
        q = {"after": after}
        if sub:
            q["subsession"] = sub
        else:
            q["hours"] = 3      # no id from iRacing yet: recent stops only
        url = f"{self.api_base}/api/overlay/pit-tires?{urllib.parse.urlencode(q)}"
        try:
            data = self._fetch(url)
            new = [s for s in (data.get("stops") or []) if isinstance(s, dict)]
        except Exception as e:
            with self._lock:
                self._last_error = f"{e}"
            return False
        with self._lock:
            if sub != self._subsession:
                return True     # the race changed while we were fetching
            self._merge(new)
            self._last_ok = self._clock()
            self._last_error = ""
        return True

    def _merge(self, new: list) -> None:
        """A re-sent stop (logger retry) comes back with a NEW id: it
        replaces the copy of the same driver + stop number."""
        for s in new:
            sid = s.get("id")
            if isinstance(sid, int) and sid > self._after:
                self._after = sid
            key = (s.get("iracingMemberId") or s.get("driver"), s.get("sessionNum"), s.get("stopCount"))
            self._stops = [x for x in self._stops
                           if (x.get("iracingMemberId") or x.get("driver"),
                               x.get("sessionNum"), x.get("stopCount")) != key
                           or s.get("stopCount") is None]
            self._stops.append(s)
        if len(self._stops) > KEEP_STOPS:
            self._stops = self._stops[-KEEP_STOPS:]

    def _run(self) -> None:
        while True:
            if self._active():
                ok = self.poll_once()
                time.sleep(POLL_S if ok else RETRY_S)
            else:
                time.sleep(1.0)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="pit-tires")
            self._thread.start()


def verdict(stop: dict) -> str:
    """Short broadcast label for what the crew did with the tires."""
    tires = stop.get("tires") or {}
    changed = [c for c in ("lf", "rf", "lr", "rr") if c in (stop.get("tiresChanged") or [])]
    if not tires:
        return ""
    if not changed:
        if any((t or {}).get("changed") is None for t in tires.values()):
            # iRacing gave one reading only: trust the service the driver
            # ordered — no tire box ticked means a fuel-only stop. Otherwise
            # say nothing rather than guess on stream.
            requested = stop.get("serviceRequested")
            if requested and not any(r.endswith("_tire") for r in requested):
                return "NO TIRE CHANGE"
            return ""
        return "NO TIRE CHANGE"
    fresh = stop.get("leftOnFresh")
    kind = "NEW" if fresh else ("USED" if fresh is False else "")
    if len(changed) == 4:
        what = "4 TIRES"
    elif set(changed) == {"lf", "rf"}:
        what = "FRONTS"
    elif set(changed) == {"lr", "rr"}:
        what = "REARS"
    elif set(changed) == {"lf", "lr"}:
        what = "LEFTS"
    elif set(changed) == {"rf", "rr"}:
        what = "RIGHTS"
    else:
        what = f"{len(changed)} TIRE{'S' if len(changed) > 1 else ''}"
    return f"{kind} {what}".strip()


def join_with_field(stops: list, rows: list) -> list:
    """Attach the live tower row (position, class, Pro/Am, team, brand) to each
    stop. Matched by iRacing customer id first, the car number second."""
    by_uid = {str(r.get("user_id")): r for r in rows if r.get("user_id")}
    by_num = {str(r.get("car_number")): r for r in rows if r.get("car_number")}
    out = []
    for s in stops:
        uid = s.get("iracingMemberId") or s.get("custId")
        row = by_uid.get(str(uid)) if uid else None
        if row is None and s.get("carNumber"):
            row = by_num.get(str(s.get("carNumber")))
        o = dict(s)
        o["verdict"] = verdict(s)
        if row:
            o.update({
                "in_field": True,
                "position": row.get("class_position") or row.get("position"),
                "class_name": row.get("class_name") or "",
                "class_color": row.get("class_color") or "",
                "proam": row.get("proam"),
                "country": row.get("country"),
                "team_name": row.get("team_name") or "",
                "brand": row.get("brand") if row.get("brand_logo") else None,
                "car_idx": row.get("car_idx"),
                "name_iracing": row.get("name") or "",
            })
        else:
            o["in_field"] = False
        out.append(o)
    return out
