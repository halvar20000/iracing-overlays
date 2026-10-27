"""Steward decisions onto the stream (step 3 of iCASControl).

Every decision worth showing goes to the Race Control banner overlay
(iracing_rc_banner.py, port 5019) as one message, the way iRaceControl's
ATVO link does it:

    POST <banner_url>/rc/steward   {"kind", "headline", "text", "car", "secs"}

kind = penalty / warning / investigation / nfa / clear / message (the
banner's colours).  ``car`` is the car NUMBER; the banner looks the car up
itself (class colour, team, driver).  The banner usually runs on the stream
PC, iCAS on the admin's PC - hence a URL, not localhost only.

Config: racecontrol/data/stream_config.json (gitignored, updater-protected):
    {"enabled": false, "banner_url": "http://localhost:5019",
     "kinds": {"investigation": true, "nfa": true, ...}}
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_PATH = DATA_DIR / "stream_config.json"
KINDS = ("penalty", "warning", "investigation", "nfa", "clear", "message")
DEFAULT_CONFIG = {"enabled": False, "banner_url": "http://localhost:5019",
                  "kinds": {k: True for k in KINDS}}
TIMEOUT_S = 4.0

# Incident decisions -> (kind, headline).  NOTED is the steward's own
# bookkeeping and never goes on air.
RESOLUTION_BANNER = {
    "UNDER INVESTIGATION": ("investigation", "UNDER INVESTIGATION"),
    "NO ACTION": ("nfa", "NO FURTHER ACTION"),
    "RACE INCIDENT": ("nfa", "RACING INCIDENT - NO FURTHER ACTION"),
    "WARNING": ("warning", "WARNING"),
    "DRIVE THROUGH": ("penalty", "DRIVE THROUGH PENALTY"),
    "STOP/GO": ("penalty", "STOP AND GO PENALTY"),
    "TIME PENALTY": ("penalty", "TIME PENALTY"),
    "DSQ": ("penalty", "DISQUALIFIED"),
}
# Car commands (right-click menu / car panel) -> (kind, headline).
CAR_BANNER = {
    "drive_through": ("penalty", "DRIVE THROUGH PENALTY"),
    "stop_go": ("penalty", "STOP AND GO PENALTY"),
    "time_penalty": ("penalty", "TIME PENALTY"),
    "dsq": ("penalty", "DISQUALIFIED"),
    "clear_penalties": ("clear", "PENALTY CLEARED"),
}


def time_headline(seconds: float) -> str:
    return f"{seconds:.0f} SECOND TIME PENALTY" if seconds else "TIME PENALTY"


def load_config(path: Optional[Path] = None) -> dict:
    path = path or CONFIG_PATH
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        kinds = {**cfg["kinds"], **(stored.get("kinds") or {})}
        cfg.update(stored)
        cfg["kinds"] = kinds
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[stream] {path.name} unreadable: {exc}", file=sys.stderr)
    return cfg


def save_config(cfg: dict, path: Optional[Path] = None) -> None:
    path = path or CONFIG_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        print(f"[stream] could not save {path.name}: {exc}", file=sys.stderr)


def clean_url(url: str) -> str:
    url = str(url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    return url


def _post(url: str, body: dict) -> tuple[bool, str]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return 200 <= r.status < 300, f"HTTP {r.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(getattr(exc, "reason", exc))


class BannerOut:
    """Sends banner messages in a background thread (a stream PC that is
    off must never stall the dashboard).  ``done(ok, info, body)`` reports
    each one; ``post`` is replaceable in tests."""

    def __init__(self, done: Callable[[bool, str, dict], None],
                 post: Callable[[str, dict], tuple[bool, str]] = _post) -> None:
        self.done = done
        self.post = post

    def send(self, base_url: str, body: dict, wait: bool = False) -> None:
        url = clean_url(base_url) + "/rc/steward"

        def run() -> None:
            ok, info = self.post(url, body)
            try:
                self.done(ok, info, body)
            except Exception:
                pass

        if wait:
            run()
        else:
            threading.Thread(target=run, daemon=True).start()
