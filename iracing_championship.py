"""
iRacing Championship Overlay
----------------------------
Live championship overlay for OBS that combines pre-race championship
standings (pulled from the CLS league-manager at league.simracing-hub.com)
with live iRacing race data to produce an F1-broadcast-style view:

  View A — "Race + championship delta"
    Live race standings table. For each driver, a small ▲N / ▼N / =
    indicator shows how many championship positions they would gain or
    lose if the race ended right now.

  View B — "Championship projection"
    The pre-race championship table reordered live, with PROJECTED
    post-race points = pre-race points + points for the driver's current
    race position (looked up in the league's scoring table). Pro/Am
    seasons also project class-relative points.

Two services, one process:
  - / (config page) — pick league + season; saved to championship_config.json
  - /overlay (OBS browser source) — the actual overlay; transparent BG by default

Hotkeys (sent via fetch() from the overlay page):
  V — toggle View A / View B
  H — toggle debug background (transparent ↔ dark panel)

Requirements:  pip install pyirsdk flask requests
Run:           python iracing_championship.py
Port:          5010

Driver matching is by iRacing customer ID (UserID in telemetry →
User.iracingMemberId in league-manager). Names are not used for matching.
Drivers without a registration row are shown as "unranked".

Runs in parallel with the other iracing_*.py overlays; uses its own SDK
connection via the shared SDKPoller base class.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, render_template_string, request

from iracing_sdk_base import SDKPoller, GridBaseline, SESSION_STATE_RACING, setup_utf8_stdout
setup_utf8_stdout()

try:
    import requests
except ImportError:
    print("ERROR: 'requests' is required. Run:  pip install requests")
    raise SystemExit(1)


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
SCRIPT_DIR   = Path(__file__).resolve().parent
CONFIG_PATH  = SCRIPT_DIR / "championship_config.json"
DEFAULT_API  = "https://league.simracing-hub.com"

DEFAULT_CONFIG = {
    "api_base":        DEFAULT_API,
    "league_slug":     "cas-gt3-wct",
    "season_id":       None,           # None → API picks the ACTIVE season
    "refresh_seconds": 60,             # how often to re-fetch championship
    # Scoring rules the overlay API doesn't publish (yet). Defaults match
    # CLS: a driver needs 50 % of the leader's distance for position points,
    # and race 2 of a multi-race round uses the race-1 table unless the
    # league has its own (CLS ScoringSystem.pointsTableRace2 — PCCD sets it
    # equal to pointsTable). Override per league in championship_config.json.
    "race_points_min_distance_pct": 50,
    "points_table_race2":           None,   # {"1": 41, "2": 35, ...} or None
}

# Earlier races of the CURRENT round (race 1 while race 2 runs) are kept in
# this file, so they still count when race 2 is a separate hosted session or
# the overlay was restarted in between. See RoundMemory.
ROUND_CACHE_PATH = SCRIPT_DIR / "championship_round_cache.json"
ROUND_CACHE_MAX_AGE_S = 12 * 3600

# iRacing SessionState: ... Racing=4, Checkered=5, CoolDown=6
SESSION_STATE_CHECKERED = 5


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[championship] Could not read {CONFIG_PATH.name}: {e}")
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"[championship] Config saved -> {CONFIG_PATH.name}")


# -----------------------------------------------------------------------------
# League-manager fetcher (runs in its own daemon thread)
# -----------------------------------------------------------------------------
class ChampionshipFetcher:
    """Periodically pulls /api/overlay/standings into memory.

    Keeps an in-process cache so the overlay tick (1 Hz) doesn't hit Vercel
    on every poll. Refresh is whatever `refresh_seconds` is set to in
    championship_config.json — typically once a minute. The standings
    endpoint itself is cached at Vercel's edge for 30 s.
    """

    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._lock = threading.Lock()
        self._standings: dict | None = None     # last successful fetch
        self._error: str | None = None
        self._last_fetch_at: float = 0.0
        self._running = True

    def update_config(self, cfg: dict) -> None:
        """Swap the active league/season. Triggers a refresh next tick."""
        with self._lock:
            self._cfg = dict(cfg)
            self._last_fetch_at = 0.0  # force immediate refetch

    def get(self) -> dict:
        with self._lock:
            return {
                "data":          self._standings,
                "error":         self._error,
                "last_fetch_at": self._last_fetch_at,
                "config":        dict(self._cfg),
            }

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        print("[championship] Fetcher started")
        while self._running:
            try:
                self._maybe_refresh()
            except Exception as e:
                with self._lock:
                    self._error = f"{type(e).__name__}: {e!r}"
                print(f"[championship] Fetch error: {self._error}")
            time.sleep(2.0)

    def _maybe_refresh(self) -> None:
        with self._lock:
            cfg = dict(self._cfg)
            since = time.time() - self._last_fetch_at
            refresh = cfg.get("refresh_seconds", 60)
        if since < refresh and self._standings is not None:
            return

        base = (cfg.get("api_base") or DEFAULT_API).rstrip("/")
        slug = cfg.get("league_slug") or ""
        if not slug:
            return
        params = {"league": slug}
        sid = cfg.get("season_id")
        if sid:
            params["season"] = sid

        url = f"{base}/api/overlay/standings"
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(body.get("error") or "API returned ok=false")

        with self._lock:
            self._standings = body
            self._error = None
            self._last_fetch_at = time.time()
        season = body.get("season", {}) or {}
        n = len(body.get("standings") or [])
        print(f"[championship] Loaded {n} drivers from "
              f"{slug} / {season.get('name', '?')}")


# -----------------------------------------------------------------------------
# Race poller — minimal live snapshot keyed by iRacing customer ID
# -----------------------------------------------------------------------------
class RacePoller(SDKPoller):
    """Snapshot of the current iRacing session, sorted by live race position
    and indexed by UserID (custid) so we can join to league-manager rows."""

    tag = "championship"
    poll_interval = 1.0

    def __init__(self):
        super().__init__()
        # Starting grid, for the order BEFORE the green (see _read_snapshot).
        self._grid = GridBaseline()

    def _driver_map(self) -> dict[int, dict]:
        info = self.ir["DriverInfo"] or {}
        out: dict[int, dict] = {}
        for d in info.get("Drivers", []) or []:
            cidx = d.get("CarIdx")
            if cidx is None:
                continue
            if d.get("CarIsPaceCar") == 1 or d.get("IsSpectator") == 1:
                continue
            try:
                cust_id = int(d.get("UserID") or 0)
            except (TypeError, ValueError):
                cust_id = 0
            out[cidx] = {
                "car_idx":    cidx,
                "cust_id":    cust_id,
                "name":       d.get("UserName", "") or "",
                "abbrev":     d.get("AbbrevName", "") or "",
                "car_number": d.get("CarNumber", "") or "",
                "car_name":   d.get("CarScreenNameShort")
                              or d.get("CarScreenName", "") or "",
                "team_name":  d.get("TeamName", "") or "",
                "irating":    int(d.get("IRating") or 0),
                "class_id":   int(d.get("CarClassID") or 0),
                "class_name": (d.get("CarClassShortName") or "").strip(),
            }
        return out

    @staticmethod
    def _parse_results(sess: dict, drivers: dict) -> list[dict]:
        """A session's ResultsPositions as [{cust_id, pos, laps, out}] in
        finishing order. `pos` is iRacing's 1-based overall Position."""
        out = []
        for r in sess.get("ResultsPositions") or []:
            try:
                cidx = int(r.get("CarIdx"))
                pos = int(r.get("Position") or 0)
            except (TypeError, ValueError):
                continue
            d = drivers.get(cidx)
            if not d or not d.get("cust_id") or pos <= 0:
                continue
            try:
                laps = int(r.get("LapsComplete") or 0)
            except (TypeError, ValueError):
                laps = 0
            out.append({"cust_id": d["cust_id"], "pos": pos, "laps": max(laps, 0),
                        "out": int(r.get("ReasonOutId") or 0)})
        out.sort(key=lambda x: x["pos"])
        return out

    def _read_snapshot(self) -> dict:
        ir = self.ir
        weekend = ir["WeekendInfo"] or {}
        session_info = ir["SessionInfo"] or {}
        sessions = (session_info.get("Sessions") or []) if session_info else []
        sess_num = ir["SessionNum"] if ir["SessionNum"] is not None else 0
        sess_uid = ir["SessionUniqueID"] or 0
        sess_state = int(ir["SessionState"] or 0)

        current = None
        for s in sessions:
            if s.get("SessionNum") == sess_num:
                current = s
                break
        sess_type = (current or {}).get("SessionType", "") or ""
        sess_name = (current or {}).get("SessionName", "") or ""
        is_race = "race" in sess_type.lower()

        drivers   = self._driver_map()
        positions = ir["CarIdxPosition"] or []
        laps      = ir["CarIdxLap"] or []
        laps_done = ir["CarIdxLapCompleted"] or []
        lap_pcts  = ir["CarIdxLapDistPct"] or []
        on_pit    = ir["CarIdxOnPitRoad"] or []
        f2        = ir["CarIdxF2Time"] or []  # gap to class leader, seconds
        surfaces  = ir["CarIdxTrackSurface"] or []

        rows = []
        for cidx, d in drivers.items():
            pos = positions[cidx] if cidx < len(positions) else 0
            lap = laps[cidx] if cidx < len(laps) else 0
            done = laps_done[cidx] if cidx < len(laps_done) else 0
            pct = lap_pcts[cidx] if cidx < len(lap_pcts) else 0.0
            in_pit = bool(on_pit[cidx]) if cidx < len(on_pit) else False
            gap = float(f2[cidx]) if cidx < len(f2) else 0.0
            surf = surfaces[cidx] if cidx < len(surfaces) else -1
            in_world = surf is not None and surf >= 0
            rows.append({
                **d,
                "iracing_pos": int(pos or 0),
                "lap":         int(lap or 0),
                "laps_done":   max(int(done or 0), 0),
                "lap_pct":     float(pct or 0.0),
                "progress":    float((lap or 0) + (pct or 0.0)),
                "in_pit":      in_pit,
                "gap_leader":  gap,           # seconds behind class leader
                "in_world":    in_world,
            })

        # Live ordering — iRacing's CarIdxPosition only updates at the S/F
        # line, so use track progress for in-world cars. Out-of-world cars
        # (DNF / in garage) drop to the bottom.
        #
        # Before the green lap counters are frozen and the timing line can
        # cut through the grid (Zandvoort: front of the grid at 0.00-0.01,
        # back at 0.98-0.999), so lap+pct puts the back of the grid in the
        # lead. Until SessionState reaches Racing, use the starting grid.
        pre_green = is_race and 0 < sess_state < SESSION_STATE_RACING
        if is_race:
            self._grid.update(ir)
        grid = self._grid.grid_pos
        in_field = [r for r in rows if r["in_world"]]
        out_field = [r for r in rows if not r["in_world"]]
        if pre_green:
            in_field.sort(key=lambda r: (grid.get(r["car_idx"], 9999),
                                         r["iracing_pos"] or 9999, -r["progress"]))
        else:
            in_field.sort(key=lambda r: -r["progress"])
        out_field.sort(key=lambda r: r["iracing_pos"] or 999)
        ordered = in_field + out_field

        # Once the checkered is out, live track progress is meaningless —
        # finished cars drive into the pits / garage while the rest are
        # still on their last lap. From then on the order is iRacing's own
        # classification (ResultsPositions), which settles as each car
        # takes the flag. This is the provisional race result.
        cur_results = self._parse_results(current, drivers) if (current and is_race) else []
        finished = is_race and sess_state >= SESSION_STATE_CHECKERED and bool(cur_results)
        if finished:
            res_pos = {r["cust_id"]: r for r in cur_results}
            classified = sorted((r for r in ordered if r["cust_id"] in res_pos),
                                key=lambda r: res_pos[r["cust_id"]]["pos"])
            rest = [r for r in ordered if r["cust_id"] not in res_pos]
            for r in classified:
                r["laps_done"] = res_pos[r["cust_id"]]["laps"]
            ordered = classified + rest

        for i, r in enumerate(ordered, start=1):
            r["race_pos"] = i

        # Every race of this hosted session that is already over (earlier
        # SessionNum), plus the current one once it is finished. RoundMemory
        # stores them so race 1 still counts while race 2 is running.
        done_races = []
        for s in sessions:
            if "race" not in (s.get("SessionType") or "").lower():
                continue
            num = s.get("SessionNum")
            if num == sess_num:
                if not finished:
                    continue
                res = [{"cust_id": r["cust_id"], "pos": r["race_pos"],
                        "laps": r["laps_done"], "out": 0}
                       for r in ordered if r["cust_id"]]
            elif num is not None and num < sess_num:
                res = self._parse_results(s, drivers)
            else:
                continue
            if res:
                done_races.append({
                    "key":     f"{sess_uid}:{num}",
                    "name":    s.get("SessionName") or f"Race (session {num})",
                    "results": res,
                })

        return {
            "connected":      True,
            "track_name":     weekend.get("TrackDisplayName") or
                              weekend.get("TrackName") or "",
            "session_type":   sess_type,
            "session_name":   sess_name,
            "session_num":    sess_num,
            "session_key":    f"{sess_uid}:{sess_num}",
            "session_state":  sess_state,
            "is_race":        is_race,
            "race_finished":  finished,
            "drivers_count":  len(ordered),
            "rows":           ordered,
            "done_races":     done_races,
        }


class RoundMemory:
    """Races of the CURRENT round that are over but not yet in CLS.

    CLS only publishes a round once it is COMPLETED, i.e. after both races.
    So during race 2 (and between the races) the championship must be the
    CLS total PLUS race 1, which only the overlay knows. Every finished race
    the poller reports is stored here — and in ROUND_CACHE_PATH, so it
    survives an overlay restart and a race 2 run as a separate hosted
    session (where race 1 is no longer in SessionInfo).

    An entry only counts while it can't be in CLS yet: same season, same
    CLS completedRounds as when it was recorded, and younger than
    ROUND_CACHE_MAX_AGE_S. The moment CLS publishes the round,
    completedRounds goes up and the entries drop out by themselves.
    """

    def __init__(self, path: Path = ROUND_CACHE_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        try:
            if path.exists():
                self._entries = json.loads(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            print(f"[championship] Could not read {path.name}: {e}")

    def update(self, race_state: dict | None, champ: dict | None) -> None:
        if not race_state or not champ:
            return
        season = champ.get("season") or {}
        changed = False
        with self._lock:
            for race in race_state.get("done_races") or []:
                old = self._entries.get(race["key"])
                if old and old["results"] == race["results"]:
                    continue
                self._entries[race["key"]] = {
                    "season_id":        season.get("id"),
                    "completed_rounds": season.get("completedRounds"),
                    "track":            race_state.get("track_name"),
                    "name":             race["name"],
                    "saved_at":         old["saved_at"] if old else time.time(),
                    "results":          race["results"],
                }
                changed = True
            # Housekeeping: forget anything that can no longer count.
            now = time.time()
            for k in [k for k, e in self._entries.items()
                      if now - e.get("saved_at", 0) > ROUND_CACHE_MAX_AGE_S]:
                del self._entries[k]
                changed = True
            snapshot = dict(self._entries)
        if changed:
            try:
                self._path.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
            except Exception as e:
                print(f"[championship] Could not write {self._path.name}: {e}")

    def earlier_races(self, champ: dict | None, current_key: str | None) -> list[dict]:
        """Finished races of this round other than the one running now,
        oldest first."""
        if not champ:
            return []
        season = champ.get("season") or {}
        now = time.time()
        with self._lock:
            out = [e for k, e in self._entries.items()
                   if k != current_key
                   and e.get("season_id") == season.get("id")
                   and e.get("completed_rounds") == season.get("completedRounds")
                   and now - e.get("saved_at", 0) <= ROUND_CACHE_MAX_AGE_S]
        return sorted(out, key=lambda e: e["saved_at"])


# -----------------------------------------------------------------------------
# Projection — join race data to championship standings
# -----------------------------------------------------------------------------
def _points_for_position(points_table: dict, pos: int) -> int:
    """Look up race points for finishing position `pos` in the league's
    scoring table. Returns 0 if the position isn't in the table (e.g.
    DNF, or position beyond the bottom row)."""
    if pos is None or pos <= 0:
        return 0
    try:
        return int(points_table.get(str(pos), 0))
    except (TypeError, ValueError):
        return 0


def _distance_ok(laps: int, leader_laps: int, min_pct: float) -> bool:
    """CLS rule: position points need `min_pct` % of the leader's laps
    (floored, like the CLS importer). Before the leader has completed a lap
    everyone is still eligible."""
    if leader_laps <= 0:
        return True
    return int(laps * 100 // leader_laps) >= min_pct


def _score_race(order: list[dict], by_custid: dict, pro_am: bool,
                table: dict, class_table: dict, min_pct: float) -> dict:
    """Points for one race. `order` = [{cust_id, pos, laps, out}] in
    finishing order. Returns {cust_id: {"pos", "pts", "class_pts"}}."""
    leader_laps = max((o.get("laps") or 0 for o in order), default=0)
    seen = {"PRO": 0, "AM": 0}
    out: dict[int, dict] = {}
    for o in order:
        cid = o.get("cust_id")
        if not cid:
            continue
        ok = _distance_ok(o.get("laps") or 0, leader_laps, min_pct)
        pts = _points_for_position(table, o["pos"]) if ok else 0
        class_pts = pts
        if pro_am:
            ch = by_custid.get(cid)
            cls = (ch or {}).get("proAmClass")
            class_pts = 0
            if cls in seen:
                seen[cls] += 1
                class_pts = _points_for_position(class_table, seen[cls]) if ok else 0
        out[cid] = {"pos": o["pos"], "pts": pts, "class_pts": class_pts}
    return out


def build_projection(race_state: dict, champ_payload: dict | None,
                     earlier_races: list[dict] | None = None,
                     cfg: dict | None = None) -> dict:
    """Combine the live race snapshot and the cached championship payload
    into a single overlay state. This is the heart of the overlay.

    Projected total = CLS points (completed rounds only)
                    + every EARLIER race of this round (race 1 while race 2
                      runs — RoundMemory; CLS doesn't have those yet)
                    + the CURRENT race, if a race session is running
                      (live order, or iRacing's classification once the
                      checkered is out).
    Practice / qualifying / warmup add nothing live, so between the races
    the overlay shows the provisional standings after race 1.

    Returns:
      {
        "league": {...},
        "season": {...},
        "track":  "...",
        "session_type": "RACE" / "QUALIFY" / "PRACTICE" / ...,
        "race_rows":    [...],  # live race table for view A
        "champ_rows":   [...],  # championship table for view B
      }

    Both row lists include:
      - rank        (current championship position, pre-race)
      - proj_rank   (projected post-race championship position)
      - delta       (rank - proj_rank: +ve = gain, -ve = loss, 0 = same,
                     None when the driver isn't in the championship)
      - proj_points (pre-race points + projected race points)
    """
    cfg = cfg or {}
    earlier_races = earlier_races or []
    rows = race_state.get("rows") or [] if race_state else []
    race_connected = bool(race_state and race_state.get("connected"))
    live_race = race_connected and bool(race_state.get("is_race"))

    if not champ_payload:
        return {
            "ok":            False,
            "error":         "Championship data not loaded yet",
            "race_connected": race_connected,
        }

    league = champ_payload.get("league") or {}
    season = champ_payload.get("season") or {}
    scoring = champ_payload.get("scoring") or {}
    standings = champ_payload.get("standings") or []
    points_table = (scoring.get("pointsTable") or {})
    class_points_table = (scoring.get("classPointsTable") or points_table)
    table_r2 = cfg.get("points_table_race2") or points_table
    min_pct = float(cfg.get("race_points_min_distance_pct") or 0)
    pro_am = bool(season.get("proAmEnabled"))

    # Index championship rows by iRacing customer ID for O(1) lookup.
    by_custid: dict[int, dict] = {}
    for s in standings:
        mid = s.get("iracingMemberId")
        if not mid:
            continue
        try:
            by_custid[int(mid)] = s
        except (TypeError, ValueError):
            continue

    # ---- Earlier races of this round (race 1 during race 2) ----
    earlier_scores = []
    for i, e in enumerate(earlier_races):
        table = points_table if i == 0 else table_r2
        earlier_scores.append(_score_race(e["results"], by_custid, pro_am,
                                          table, class_points_table, min_pct))

    # ---- The race running now ----
    live_scores: dict[int, dict] = {}
    if live_race:
        table = points_table if not earlier_races else table_r2
        order = [{"cust_id": r.get("cust_id"), "pos": r["race_pos"],
                  "laps": r.get("laps_done") or 0, "out": 0} for r in rows]
        live_scores = _score_race(order, by_custid, pro_am, table,
                                  class_points_table, min_pct)
    live_by_cust = {r.get("cust_id"): r for r in rows if r.get("cust_id")}

    # Build the projected championship rows.
    champ_rows: list[dict] = []
    for s in standings:
        mid = s.get("iracingMemberId")
        try:
            mid_int = int(mid) if mid else None
        except (TypeError, ValueError):
            mid_int = None

        key = "class_pts" if pro_am else "pts"
        earlier = [sc.get(mid_int) for sc in earlier_scores]
        earlier_pts = sum((e or {}).get(key, 0) for e in earlier)
        live = live_by_cust.get(mid_int) if (mid_int and race_connected) else None
        cur = live_scores.get(mid_int) if mid_int else None
        race_pts = (cur or {}).get("pts", 0)
        class_race_pts = (cur or {}).get("class_pts", 0)

        # `points` from the API is `classTotal` — the primary sort key.
        base_points = int(s.get("points") or 0)
        proj_total = base_points + earlier_pts + (cur or {}).get(key, 0)

        champ_rows.append({
            "rank":            int(s.get("rank") or 0),
            "name":            s.get("name") or "",
            "first_name":      s.get("firstName"),
            "last_name":       s.get("lastName"),
            "country":         s.get("countryCode"),
            "start_number":    s.get("startNumber"),
            "team_name":       s.get("teamName"),
            "car_class_name":  s.get("carClassName"),
            "pro_am":          s.get("proAmClass"),
            "iracing_member":  mid_int,
            "pre_points":      base_points,
            "earlier":         [{"pos": e["pos"], "pts": e[key]} if e else None
                                for e in earlier],
            "earlier_pts":     earlier_pts,
            "race_pts":        race_pts,
            "class_race_pts":  class_race_pts,
            "proj_points":     proj_total,
            "in_race":         bool(live) and live_race,
            "race_pos":        live["race_pos"] if (live and live_race) else None,
            "in_pit":          bool(live and live.get("in_pit")),
            "in_world":        bool(live and live.get("in_world")),
        })

    # ---- Compute projected rank ----
    # Sort a copy by projected total (desc), assign proj_rank, then merge
    # back so the returned list keeps its original pre-race order.
    proj_sorted = sorted(
        champ_rows,
        key=lambda c: (-c["proj_points"], c["rank"])  # tiebreak: current rank
    )
    proj_rank_by_mid: dict[int | None, int] = {}
    for i, c in enumerate(proj_sorted, start=1):
        proj_rank_by_mid[c["iracing_member"] or -c["rank"]] = i  # unique key
        c["proj_rank"] = i
        c["delta"] = c["rank"] - i

    for c in champ_rows:
        key = c["iracing_member"] or -c["rank"]
        c["proj_rank"] = proj_rank_by_mid.get(key, c["rank"])
        c["delta"] = c["rank"] - c["proj_rank"]

    # ---- Build the race rows for view A ----
    # Each race row carries the championship delta of its driver, when matched.
    delta_by_mid = {c["iracing_member"]: c["delta"]
                    for c in champ_rows if c["iracing_member"] is not None}
    pre_rank_by_mid = {c["iracing_member"]: c["rank"]
                       for c in champ_rows if c["iracing_member"] is not None}
    proj_rank_by_mid2 = {c["iracing_member"]: c["proj_rank"]
                         for c in champ_rows if c["iracing_member"] is not None}

    race_rows = []
    for r in rows:
        mid = r.get("cust_id") or 0
        in_champ = mid in delta_by_mid
        race_rows.append({
            "race_pos":     r.get("race_pos"),
            "car_number":   r.get("car_number"),
            "name":         r.get("name"),
            "abbrev":       r.get("abbrev"),
            "team_name":    r.get("team_name"),
            "car_class":    r.get("class_name"),
            "in_pit":       r.get("in_pit"),
            "in_world":     r.get("in_world"),
            "gap_leader":   r.get("gap_leader"),
            "cust_id":      mid,
            "in_champ":     in_champ,
            "champ_rank":   pre_rank_by_mid.get(mid),
            "proj_rank":    proj_rank_by_mid2.get(mid),
            "champ_delta":  delta_by_mid.get(mid),
        })

    return {
        "ok":             True,
        "race_connected": race_connected,
        "league":         league,
        "season":         season,
        "track":          race_state.get("track_name") if race_state else "",
        "session_type":   race_state.get("session_type") if race_state else "",
        "session_name":   race_state.get("session_name") if race_state else "",
        "live_race":      live_race,
        "race_finished":  bool(race_state and race_state.get("race_finished")),
        "earlier_races":  [e.get("name") for e in earlier_races],
        "race_rows":      sorted(race_rows, key=lambda x: x["race_pos"] or 999),
        "champ_rows":     sorted(champ_rows, key=lambda x: x["proj_rank"]),
    }


# -----------------------------------------------------------------------------
# Flask app
# -----------------------------------------------------------------------------
config = load_config()
poller = RacePoller()
fetcher = ChampionshipFetcher(config)
round_memory = RoundMemory()

# UI state shared between the overlay page and the control endpoints
ui_state = {
    "view":   "A",       # "A" = race+delta, "B" = projection
    "stream": True,      # True = transparent BG (default for OBS)
}

app = Flask(__name__)


def _state() -> dict:
    """The full projection — shared by /api/state and /api/duel."""
    race = poller.get()
    fetch = fetcher.get()
    champ = fetch.get("data")
    round_memory.update(race, champ)
    earlier = round_memory.earlier_races(champ, (race or {}).get("session_key"))
    payload = build_projection(race, champ, earlier, fetch.get("config"))
    payload["fetch_error"]   = fetch.get("error")
    payload["fetch_age"]     = (time.time() - fetch["last_fetch_at"]) \
                                if fetch.get("last_fetch_at") else None
    payload["ui"]            = dict(ui_state)
    payload["config"]        = fetch.get("config")
    return payload


@app.route("/api/state")
def api_state():
    """JSON state polled by the overlay page (~1 Hz)."""
    return jsonify(_state())


@app.route("/api/duel")
def api_duel():
    """Two championship rows side by side for the title-fight card.
    ?a= / ?b= = iRacing customer IDs; default = CLS P1 vs P2 (pre-round)."""
    st = _state()
    if not st.get("ok"):
        return jsonify({"ok": False, "error": st.get("error")})
    rows = st.get("champ_rows") or []
    by_id = {r["iracing_member"]: r for r in rows if r.get("iracing_member")}
    by_rank = sorted(rows, key=lambda r: r["rank"])

    def pick(param, fallback_idx):
        try:
            r = by_id.get(int(request.args.get(param) or 0))
        except ValueError:
            r = None
        return r or (by_rank[fallback_idx] if len(by_rank) > fallback_idx else None)

    a, b = pick("a", 0), pick("b", 1)
    if not a or not b:
        return jsonify({"ok": False, "error": "need two championship drivers"})
    cars = {r.get("cust_id"): r.get("car_number") for r in st.get("race_rows") or []}
    for r in (a, b):
        r["car_number"] = cars.get(r["iracing_member"]) or r.get("start_number")
    return jsonify({"ok": True, "a": a, "b": b,
                    "live_race": st.get("live_race"),
                    "race_finished": st.get("race_finished"),
                    "earlier_races": st.get("earlier_races"),
                    "season": st.get("season")})


@app.route("/api/leagues")
def api_leagues():
    """Proxy /api/overlay/leagues so the config page doesn't fight CORS."""
    cfg = load_config()
    base = (cfg.get("api_base") or DEFAULT_API).rstrip("/")
    try:
        r = requests.get(f"{base}/api/overlay/leagues", timeout=10)
        r.raise_for_status()
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        cfg = load_config()
        for k in ("api_base", "league_slug", "season_id", "refresh_seconds"):
            if k in body:
                cfg[k] = body[k]
        # Empty string season_id -> None (means "API picks ACTIVE")
        if cfg.get("season_id") in ("", "auto", "null"):
            cfg["season_id"] = None
        save_config(cfg)
        fetcher.update_config(cfg)
        return jsonify({"ok": True, "config": cfg})
    return jsonify({"ok": True, "config": load_config()})


@app.route("/toggle_view", methods=["POST"])
def toggle_view():
    ui_state["view"] = "B" if ui_state["view"] == "A" else "A"
    return jsonify({"ok": True, "view": ui_state["view"]})


@app.route("/toggle_stream", methods=["POST"])
def toggle_stream():
    ui_state["stream"] = not ui_state["stream"]
    return jsonify({"ok": True, "stream": ui_state["stream"]})


# -----------------------------------------------------------------------------
# HTML — Config page (root)
# -----------------------------------------------------------------------------
CONFIG_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>iRacing Championship Overlay — Config</title>
<style>
  :root {
    --bg: #0a0a0f; --panel: #14141c; --line: #2a2a36;
    --text: #e8e8ee; --muted: #8a8a99;
    --orange: #ff6b35; --red: #e63946; --green: #4ade80;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px;
    background: var(--bg); color: var(--text);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI",
                   "Inter", Arial, sans-serif; }
  h1 { font-size: 22px; margin: 0 0 16px; color: var(--orange); }
  .card { background: var(--panel); border: 1px solid var(--line);
    border-radius: 8px; padding: 20px; max-width: 760px;
    margin-bottom: 16px; }
  label { display: block; margin-top: 14px; color: var(--muted);
    font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
  select, input { width: 100%; padding: 10px 12px; margin-top: 6px;
    background: #1c1c26; color: var(--text); border: 1px solid var(--line);
    border-radius: 6px; font: inherit; }
  button { padding: 10px 18px; background: var(--orange);
    color: #fff; border: 0; border-radius: 6px; cursor: pointer;
    font: inherit; font-weight: 600; margin-top: 18px; }
  button.secondary { background: #2a2a36; color: var(--text); }
  .status { padding: 10px 12px; border-radius: 6px;
    margin-top: 12px; font-size: 13px; }
  .status.ok { background: #14321b; color: var(--green); }
  .status.err { background: #321414; color: var(--red); }
  .help { color: var(--muted); font-size: 12px; margin-top: 6px; }
  a { color: var(--orange); }
  code { background: #1c1c26; padding: 2px 6px; border-radius: 3px; }
  .row { display: flex; gap: 16px; }
  .row > * { flex: 1; }
</style>
</head><body>
  <h1>iRacing Championship Overlay</h1>

  <div class="card">
    <strong>OBS browser source URL:</strong>
    <div style="margin-top:6px"><code id="overlayUrl">…</code></div>
    <div class="help">Add this URL as a Browser Source in OBS (recommended
      size: 520 × 720, transparent BG already enabled).</div>
  </div>

  <div class="card">
    <label for="leagueSelect">League</label>
    <select id="leagueSelect"><option>Loading…</option></select>

    <label for="seasonSelect">Season</label>
    <select id="seasonSelect"><option>Pick a league first</option></select>

    <div class="row">
      <div>
        <label for="apiBase">API base URL</label>
        <input id="apiBase" value="">
        <div class="help">Usually <code>https://league.simracing-hub.com</code>.
          Change only if you run the league-manager locally.</div>
      </div>
      <div>
        <label for="refresh">Refresh interval (seconds)</label>
        <input id="refresh" type="number" min="10" step="5" value="60">
      </div>
    </div>

    <button id="saveBtn">Save</button>
    <button id="testBtn" class="secondary">Test fetch</button>
    <div id="status"></div>
  </div>

  <div class="card">
    <strong>Hotkeys (in the overlay window):</strong>
    <ul>
      <li><kbd>V</kbd> — toggle between race-view (with championship deltas)
        and championship-view (with projected points)</li>
      <li><kbd>H</kbd> — toggle debug background (dark panel ↔ transparent)</li>
    </ul>
  </div>

<script>
const $ = (id) => document.getElementById(id);
let leaguesData = null;

document.getElementById("overlayUrl").textContent =
  location.origin + "/overlay";

async function loadLeagues() {
  try {
    const r = await fetch("/api/leagues");
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || "Could not load leagues");
    leaguesData = j.leagues || [];
    const sel = $("leagueSelect");
    sel.innerHTML = "";
    leaguesData.forEach(l => {
      const opt = document.createElement("option");
      opt.value = l.slug;
      opt.textContent = l.name + "  (" + l.slug + ")";
      sel.appendChild(opt);
    });
    sel.addEventListener("change", populateSeasons);
  } catch (e) {
    $("status").className = "status err";
    $("status").textContent = "Could not load leagues: " + e.message;
  }
}

function populateSeasons() {
  const slug = $("leagueSelect").value;
  const league = (leaguesData || []).find(l => l.slug === slug);
  const sel = $("seasonSelect");
  sel.innerHTML = "";
  const auto = document.createElement("option");
  auto.value = "";
  auto.textContent = "(Auto: most recent ACTIVE season)";
  sel.appendChild(auto);
  if (league) {
    league.seasons.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = s.name + " — " + s.status;
      sel.appendChild(opt);
    });
  }
}

async function loadConfig() {
  const r = await fetch("/api/config");
  const j = await r.json();
  const c = j.config || {};
  $("apiBase").value = c.api_base || "";
  $("refresh").value = c.refresh_seconds || 60;
  await loadLeagues();
  if (c.league_slug) {
    $("leagueSelect").value = c.league_slug;
    populateSeasons();
    if (c.season_id) $("seasonSelect").value = c.season_id;
  }
}

async function save() {
  const body = {
    api_base:        $("apiBase").value.trim(),
    league_slug:     $("leagueSelect").value,
    season_id:       $("seasonSelect").value || null,
    refresh_seconds: parseInt($("refresh").value, 10) || 60,
  };
  const r = await fetch("/api/config", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const j = await r.json();
  if (j.ok) {
    $("status").className = "status ok";
    $("status").textContent = "Saved. Overlay will refresh within a few seconds.";
  } else {
    $("status").className = "status err";
    $("status").textContent = "Save failed";
  }
}

async function test() {
  $("status").className = "status";
  $("status").textContent = "Fetching…";
  await save();
  setTimeout(async () => {
    const r = await fetch("/api/state");
    const j = await r.json();
    if (j.ok) {
      $("status").className = "status ok";
      const n = (j.champ_rows || []).length;
      $("status").textContent =
        `OK — ${n} drivers loaded from ${j.league?.name} / ${j.season?.name}.`;
    } else {
      $("status").className = "status err";
      $("status").textContent = "Error: " + (j.error || j.fetch_error || "?");
    }
  }, 2500);
}

$("saveBtn").addEventListener("click", save);
$("testBtn").addEventListener("click", test);
loadConfig();
</script>
</body></html>
"""


# -----------------------------------------------------------------------------
# HTML — Overlay page (OBS browser source)
# -----------------------------------------------------------------------------
OVERLAY_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Championship Overlay</title>
<style>
  :root {
    --bg-panel: rgba(14,14,22,0.92);
    --bg-row:   rgba(255,255,255,0.04);
    --bg-row-2: rgba(0,0,0,0.22);
    --bg-head:  rgba(255,107,53,0.18);
    --text: #f5f5f8; --muted: #8a8a99; --dim: #5a5a66;
    --orange: #ff6b35; --green: #4ade80; --red: #e63946;
    --gold: #d4af37; --blue: #58a6ff;
  }
  html, body {
    background: rgba(0,0,0,0); margin: 0; padding: 0; color: var(--text);
    font: 16px/1.3 -apple-system, BlinkMacSystemFont, "Segoe UI",
                   "Inter", Arial, sans-serif;
    font-variant-numeric: tabular-nums;
  }
  body.debug { background: #0a0a0f; }
  .container {
    width: 100%; max-width: 540px;
    padding: 10px;
  }
  .panel {
    background: var(--bg-panel);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 8px;
    overflow: hidden;
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
  }
  body.stream .panel { background: var(--bg-panel); }
  body:not(.stream) .panel { background: rgba(14,14,22,0.4); }

  .header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 14px;
    background: linear-gradient(90deg, rgba(255,107,53,0.25), transparent);
    border-bottom: 1px solid rgba(255,255,255,0.08);
  }
  .header .title {
    font-size: 14px; font-weight: 700; letter-spacing: .03em;
    text-transform: uppercase; color: var(--orange);
  }
  .header .sub {
    font-size: 11px; color: var(--muted); text-align: right;
  }

  .meta {
    display: flex; gap: 12px; padding: 6px 14px;
    font-size: 11px; color: var(--muted);
    border-bottom: 1px solid rgba(255,255,255,0.05);
  }
  .meta .pill {
    background: rgba(255,255,255,0.06);
    border-radius: 3px; padding: 1px 7px;
    color: var(--text); font-weight: 600;
  }

  table { width: 100%; border-collapse: collapse; }
  thead th {
    text-align: left; font-size: 10px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .04em;
    color: var(--muted); padding: 6px 10px;
    background: rgba(0,0,0,0.25);
    border-bottom: 1px solid rgba(255,255,255,0.06);
  }
  tbody td { padding: 5px 10px;
    border-top: 1px solid rgba(255,255,255,0.04);
    font-size: 14px;
  }
  tbody tr:nth-child(odd) td  { background: var(--bg-row-2); }
  tbody tr:nth-child(even) td { background: var(--bg-row); }
  tbody tr.dnf td  { opacity: 0.55; }
  tbody tr.inpit td { color: var(--gold); }

  .pos { width: 28px; text-align: right; font-weight: 700; }
  .num { width: 36px; color: var(--muted); font-weight: 600; }
  .name { font-weight: 600; }
  .name .team { color: var(--muted); font-size: 11px;
    font-weight: 400; display: block; line-height: 1.1; }
  .name .cls { font-size: 11px; color: var(--blue);
    margin-left: 6px; font-weight: 500; }

  .gap { width: 70px; text-align: right; color: var(--muted);
    font-size: 13px; }
  .pts { width: 60px; text-align: right; font-weight: 700; }
  .pts small { display:block; font-size: 11px; color: var(--muted);
    font-weight: 400; }

  /* Delta arrows */
  .delta { width: 44px; text-align: right; font-weight: 700; font-size: 13px;}
  .delta.up    { color: var(--green); }
  .delta.down  { color: var(--red); }
  .delta.same  { color: var(--dim); }
  .delta.none  { color: var(--dim); font-style: italic;
    font-size: 11px; font-weight: 400; }

  .badge {
    display: inline-block; padding: 1px 6px; border-radius: 3px;
    font-size: 10px; font-weight: 700; margin-left: 4px;
    background: rgba(255,255,255,0.08);
  }
  .badge.pro { background: rgba(255,107,53,0.25); color: var(--orange); }
  .badge.am  { background: rgba(88,166,255,0.18); color: var(--blue); }

  .footer {
    display: flex; justify-content: space-between; align-items: center;
    font-size: 10px; color: var(--muted);
    padding: 5px 14px;
    border-top: 1px solid rgba(255,255,255,0.05);
    background: rgba(0,0,0,0.3);
  }
  .footer .err { color: var(--red); font-weight: 600; }

  .empty { padding: 18px; text-align: center; color: var(--muted);
    font-size: 13px; }
</style>
</head><body class="stream">
<div class="container">
  <div class="panel">
    <div class="header">
      <div class="title" id="title">Championship</div>
      <div class="sub" id="sub">—</div>
    </div>
    <div class="meta">
      <span id="metaLeague">—</span>
      <span class="pill" id="metaSession">—</span>
      <span id="metaTrack">—</span>
    </div>
    <div id="tableHost">
      <div class="empty">Waiting for data…</div>
    </div>
    <div class="footer">
      <span id="viewLabel">View A — Race + championship delta</span>
      <span id="status">—</span>
    </div>
  </div>
</div>

<script>
const STATE = { paused: false };

function fmtDelta(d) {
  if (d === null || d === undefined) return '<span class="delta none">—</span>';
  if (d > 0)  return `<span class="delta up">▲${d}</span>`;
  if (d < 0)  return `<span class="delta down">▼${Math.abs(d)}</span>`;
  return `<span class="delta same">＝</span>`;
}

function fmtGap(s) {
  if (s === null || s === undefined || s <= 0) return '';
  if (s < 60) return '+' + (s < 10 ? s.toFixed(3) : s.toFixed(2));
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `+${m}:${r.toFixed(2).padStart(5,'0')}`;
}

function classBadge(pro_am) {
  if (pro_am === 'PRO') return '<span class="badge pro">PRO</span>';
  if (pro_am === 'AM')  return '<span class="badge am">AM</span>';
  return '';
}

function renderRaceView(d) {
  const rows = d.race_rows || [];
  if (rows.length === 0) {
    return '<div class="empty">No live race data — waiting for iRacing…</div>';
  }
  const head = `<thead><tr>
    <th class="pos">P</th><th class="num">#</th>
    <th>Driver</th><th class="gap">Gap</th><th class="delta">Δ Ch.</th>
  </tr></thead>`;
  const body = rows.map(r => {
    const cls = [];
    if (!r.in_world) cls.push('dnf');
    if (r.in_pit)    cls.push('inpit');
    const team = r.team_name ? `<span class="team">${esc(r.team_name)}</span>` : '';
    const carClass = r.car_class ? `<span class="cls">${esc(r.car_class)}</span>` : '';
    const deltaCell = r.in_champ ? fmtDelta(r.champ_delta)
                                  : `<span class="delta none">unranked</span>`;
    return `<tr class="${cls.join(' ')}">
      <td class="pos">${r.race_pos ?? ''}</td>
      <td class="num">#${esc(r.car_number || '')}</td>
      <td class="name">${esc(r.abbrev || r.name || '—')}${carClass}${team}</td>
      <td class="gap">${r.race_pos === 1 ? 'LEADER' : fmtGap(r.gap_leader)}</td>
      <td>${deltaCell}</td>
    </tr>`;
  }).join('');
  return `<table>${head}<tbody>${body}</tbody></table>`;
}

function renderChampView(d) {
  const rows = d.champ_rows || [];
  if (rows.length === 0) {
    return '<div class="empty">No championship rows loaded.</div>';
  }
  const head = `<thead><tr>
    <th class="pos">P</th>
    <th class="delta">Δ</th>
    <th>Driver</th>
    <th class="pts">PTS</th>
  </tr></thead>`;
  const body = rows.map(r => {
    const team = r.team_name ? `<span class="team">${esc(r.team_name)}</span>` : '';
    // Earlier races of this round (race 1 while race 2 runs) + the live one.
    const nPrev = (d.earlier_races || []).length;
    const bits = [];
    (r.earlier || []).forEach((e, i) => {
      if (e) bits.push(`R${i + 1} P${e.pos} +${e.pts}`);
    });
    if (r.in_race && r.race_pos) {
      const pts = d.season?.proAmEnabled ? r.class_race_pts : r.race_pts;
      const lbl = nPrev ? `R${nPrev + 1} ` : 'Race ';
      bits.push(`${lbl}P${r.race_pos} → +${pts}`);
    }
    const liveBit = `<small>${bits.length ? esc(bits.join(' · ')) : '—'}</small>`;
    return `<tr class="${r.in_race && !r.in_world ? 'dnf' : ''}">
      <td class="pos">${r.proj_rank}</td>
      <td>${fmtDelta(r.delta)}</td>
      <td class="name">${esc(r.name)}${classBadge(r.pro_am)}${team}</td>
      <td class="pts">${r.proj_points}${liveBit}</td>
    </tr>`;
  }).join('');
  return `<table>${head}<tbody>${body}</tbody></table>`;
}

function esc(s) {
  return (s ?? '').toString()
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function tick() {
  try {
    const r = await fetch('/api/state', { cache: 'no-store' });
    const d = await r.json();
    const ui = d.ui || { view: 'A', stream: true };

    document.body.classList.toggle('stream', ui.stream);
    document.body.classList.toggle('debug',  !ui.stream);

    document.getElementById('viewLabel').textContent =
      ui.view === 'A'
        ? 'View A — Race + championship delta'
        : 'View B — Championship projection';

    document.getElementById('title').textContent =
      ui.view === 'A' ? (d.session_type || 'Race')
      : (d.live_race && !d.race_finished ? 'Championship · live'
         : ((d.race_finished || (d.earlier_races || []).length) ? 'Championship · provisional'
            : 'Championship'));

    document.getElementById('sub').textContent =
      (d.season?.name || '—') +
      (d.season?.completedRounds ? ` · R${d.season.completedRounds + (d.race_connected ? 1 : 0)}/${d.season.totalRounds}` : '');

    document.getElementById('metaLeague').textContent  = d.league?.name || '—';
    document.getElementById('metaSession').textContent = d.session_name || d.session_type || '—';
    document.getElementById('metaTrack').textContent   = d.track || '';

    if (!d.ok) {
      document.getElementById('tableHost').innerHTML =
        `<div class="empty">${esc(d.error || d.fetch_error || 'Loading…')}</div>`;
    } else {
      document.getElementById('tableHost').innerHTML =
        (ui.view === 'B') ? renderChampView(d) : renderRaceView(d);
    }

    const errBits = [];
    if (d.fetch_error)  errBits.push('API: ' + d.fetch_error);
    if (!d.race_connected) errBits.push('iRacing offline');
    document.getElementById('status').innerHTML = errBits.length
      ? `<span class="err">${esc(errBits.join(' · '))}</span>`
      : 'OK';
  } catch (e) {
    document.getElementById('status').innerHTML =
      `<span class="err">Fetch failed: ${esc(e.message)}</span>`;
  }
}

document.addEventListener('keydown', async (e) => {
  if (e.key === 'v' || e.key === 'V') {
    await fetch('/toggle_view', { method: 'POST' });
    tick();
  } else if (e.key === 'h' || e.key === 'H') {
    await fetch('/toggle_stream', { method: 'POST' });
    tick();
  }
});

tick();
setInterval(tick, 1000);
</script>
</body></html>
"""


@app.route("/")
def index():
    return render_template_string(CONFIG_HTML)


@app.route("/overlay")
def overlay():
    return render_template_string(OVERLAY_HTML)


# Title-fight duel card (served as a plain string — no Jinja).
DUEL_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Title Fight</title>
<style>
    /* Title-fight duel card (2026-10-08). Same visual language as the
       standings tower. ?a=<custid>&b=<custid> picks the pair (default:
       championship P1 vs P2), ?zoom=1.5 scales, ?debug=1 / H = dark bg,
       ?demo=1 = fake data for styling. */
    :root { --bg: rgba(40,40,45,0.92); --bg2: rgba(28,28,32,0.95);
            --text: #f2f2f4; --muted: #b9b9c2; --accent: #2f7ff0;
            --gold: #ffd166; --up: #45f063; --down: #ff5a6a; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { background: rgba(0,0,0,0); }
    body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; color: var(--text);
           padding: 8px; font-variant-numeric: tabular-nums; }
    body.debug-mode { background: #23262b; }
    .card { width: 660px; display: none; }
    .card.on { display: block; }
    .top { display: flex; align-items: center; height: 26px; background: var(--accent);
           padding: 0 10px; font-size: 14px; font-weight: 800; letter-spacing: 2px; }
    .top .sub { margin-left: auto; font-weight: 600; letter-spacing: .5px; font-size: 13px; opacity: .9; }
    .body { display: grid; grid-template-columns: 1fr 150px 1fr; background: var(--bg); }
    .side { padding: 10px 12px; min-width: 0; }
    .side.b { text-align: right; }
    .side.lead { box-shadow: inset 0 -3px 0 var(--gold); }
    .nm { font-size: 22px; font-weight: 800; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .side.lead .nm { color: var(--gold); }
    .meta { font-size: 13px; color: var(--muted); margin-top: 2px; white-space: nowrap; }
    .num { display: inline-block; background: var(--bg2); padding: 0 6px; border-radius: 3px;
           color: var(--text); font-weight: 700; margin: 0 4px; }
    .race { display: flex; gap: 8px; margin-top: 8px; font-size: 15px; font-weight: 700; }
    .side.b .race { justify-content: flex-end; }
    .chip { background: var(--bg2); padding: 2px 8px; border-radius: 3px; white-space: nowrap; }
    .chip .l { color: var(--muted); font-weight: 600; margin-right: 4px; font-size: 12px; }
    .chip.pts { color: var(--up); }
    .mid { background: var(--bg2); display: flex; flex-direction: column; align-items: center;
           justify-content: center; padding: 6px 4px; }
    .score { font-size: 30px; font-weight: 800; letter-spacing: 1px; white-space: nowrap; }
    .score .sep { color: var(--muted); margin: 0 6px; font-weight: 600; }
    .gap { font-size: 13px; font-weight: 700; color: var(--gold); margin-top: 2px; white-space: nowrap; }
    .pre { font-size: 11px; color: var(--muted); margin-top: 3px; white-space: nowrap; }
    .msg { background: var(--bg); padding: 12px; color: var(--muted); font-size: 14px; }
</style>
</head>
<body>
<div class="card" id="card">
    <div class="top"><span>TITLE FIGHT</span><span class="sub" id="sub"></span></div>
    <div class="body">
        <div class="side a" id="sa"></div>
        <div class="mid" id="mid"></div>
        <div class="side b" id="sb"></div>
    </div>
</div>
<div class="msg" id="msg" style="display:none"></div>
<script>
const qs = new URLSearchParams(location.search);
if (qs.get('debug') === '1') document.body.classList.add('debug-mode');
const zoom = parseFloat(qs.get('zoom') || '1');
if (zoom > 0 && zoom !== 1) document.body.style.zoom = zoom;
document.addEventListener('keydown', e => {
    if (e.key === 'h' || e.key === 'H') document.body.classList.toggle('debug-mode');
});
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function abbrev(full) {
    const p = String(full || '').trim().split(/\s+/);
    return p.length < 2 ? (full || '') : `${p[0][0]}. ${p.slice(1).join(' ')}`;
}

function side(d, lead) {
    const bits = [];
    (d.earlier || []).forEach((e, i) => { if (e) bits.push(`<span class="chip"><span class="l">R${i+1}</span>P${e.pos} +${e.pts}</span>`); });
    if (d.race_pos) {
        const lbl = (d.earlier || []).length ? `R${(d.earlier || []).length + 1}` : 'NOW';
        bits.push(`<span class="chip"><span class="l">${lbl}</span>P${d.race_pos}${d.in_pit ? ' PIT' : ''}</span>`);
        bits.push(`<span class="chip pts">+${d.race_pts}</span>`);
    } else if (d.live_race) {
        bits.push('<span class="chip"><span class="l">NOW</span>not in race</span>');
    }
    const num = d.car_number ? `<span class="num">#${esc(d.car_number)}</span>` : '';
    return { html: `<div class="nm">${esc(abbrev(d.name))}</div>
        <div class="meta">${d.side === 'b' ? '' : num}Champ. P${d.proj_rank}${d.side === 'b' ? num : ''}</div>
        <div class="race">${bits.join('')}</div>`, lead };
}

function render(d) {
    const card = document.getElementById('card'), msg = document.getElementById('msg');
    if (!d || !d.ok || !d.a || !d.b) {
        card.classList.remove('on');
        msg.style.display = 'block';
        msg.textContent = (d && d.error) || 'Waiting for championship data…';
        return;
    }
    msg.style.display = 'none';
    card.classList.add('on');
    const a = d.a, b = d.b;
    const diff = a.proj_points - b.proj_points;
    const sa = side({...a, side: 'a', live_race: d.live_race}, diff > 0);
    const sb = side({...b, side: 'b', live_race: d.live_race}, diff < 0);
    const ea = document.getElementById('sa'), eb = document.getElementById('sb');
    ea.innerHTML = sa.html; eb.innerHTML = sb.html;
    ea.classList.toggle('lead', diff > 0); eb.classList.toggle('lead', diff < 0);
    const leader = diff > 0 ? a : b;
    const gapTxt = diff === 0 ? 'LEVEL ON POINTS'
        : `${esc((leader.last_name || leader.name).toUpperCase())} +${Math.abs(diff)}`;
    const preDiff = a.pre_points - b.pre_points;
    document.getElementById('mid').innerHTML = `
        <div class="score">${a.proj_points}<span class="sep">–</span>${b.proj_points}</div>
        <div class="gap">${gapTxt}</div>
        <div class="pre">before round: ${a.pre_points} – ${b.pre_points}</div>`;
    document.getElementById('sub').textContent =
        d.live_race && !d.race_finished ? 'IF THE RACE ENDED NOW'
        : (d.race_finished || (d.earlier_races || []).length ? 'PROVISIONAL' : 'CHAMPIONSHIP');
}

const DEMO = qs.has('demo');
async function tick() {
    if (DEMO) {
        render({ ok: true, live_race: true, race_finished: false, earlier_races: ['RACE 1'],
            a: { name: 'Remo Grossenbacher', last_name: 'Grossenbacher', car_number: '11', pre_points: 326,
                 proj_points: 326 + 35 + 30, proj_rank: 2, earlier: [{pos: 2, pts: 35}], race_pos: 3, race_pts: 30 },
            b: { name: 'Maurice Becker', last_name: 'Becker', car_number: '49', pre_points: 324,
                 proj_points: 324 + 41 + 35, proj_rank: 1, earlier: [{pos: 1, pts: 41}], race_pos: 2, race_pts: 35 } });
        return;
    }
    try {
        const p = new URLSearchParams();
        if (qs.get('a')) p.set('a', qs.get('a'));
        if (qs.get('b')) p.set('b', qs.get('b'));
        const r = await fetch('/api/duel?' + p.toString(), { cache: 'no-store' });
        render(await r.json());
    } catch (e) { /* keep last view */ }
}
tick();
setInterval(tick, 1000);
</script>
</body>
</html>
"""


@app.route("/duel")
def duel():
    return Response(DUEL_HTML, mimetype="text/html")


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
def main() -> None:
    # Start the iRacing poller
    t = threading.Thread(target=poller.run, daemon=True, name="ChampPoller")
    t.start()
    # Start the championship fetcher
    f = threading.Thread(target=fetcher.run, daemon=True, name="ChampFetcher")
    f.start()

    print("=" * 60)
    print("iRacing Championship Overlay")
    print("=" * 60)
    print(f"  Config page : http://localhost:5010/")
    print(f"  OBS source  : http://localhost:5010/overlay")
    print(f"  Press H in the overlay window for debug background")
    print(f"  Press V in the overlay window to toggle views")
    print("=" * 60)

    try:
        app.run(host="0.0.0.0", port=5010, debug=False,
                use_reloader=False, threaded=True)
    finally:
        poller.stop()
        fetcher.stop()


if __name__ == "__main__":
    main()
