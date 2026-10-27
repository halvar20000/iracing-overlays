"""Who may use this iCASControl - several stewards at once (multi-steward).

The main iCAS runs on one PC; the other stewards sit somewhere else in
Germany and open it in their browser over the internet (a Cloudflare tunnel
to this PC's port 8080).  So:

* the PC itself (127.0.0.1, no proxy headers)  -> always in, no password;
* everybody else (LAN, tunnel)                 -> needs the ACCESS PASSWORD
  once one is set; over the tunnel / internet nothing works without one.

Every connection gives a name ("Thomas", "Andreas") - log lines, decisions,
claims and the steward chat say who it was.

Config: racecontrol/data/access_config.json (gitignored, updater-protected):
    {"password_hash": "<pbkdf2 hex>", "salt": "<hex>"}
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import sys
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG_PATH = DATA_DIR / "access_config.json"
ITERATIONS = 120_000
MAX_NAME = 24
# Headers a reverse proxy / Cloudflare tunnel adds - such a request comes
# from the internet even though it reaches us from 127.0.0.1.
PROXY_HEADERS = ("cf-ray", "cf-connecting-ip", "x-forwarded-for",
                 "x-real-ip", "forwarded")


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               bytes.fromhex(salt), ITERATIONS).hex()


def load_config(path: Optional[Path] = None) -> dict:
    path = path or CONFIG_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        print(f"[access] {path.name} unreadable: {exc}", file=sys.stderr)
        return {}


def save_config(cfg: dict, path: Optional[Path] = None) -> None:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    tmp.replace(path)


def make_config(password: str) -> dict:
    """New config for ``password`` ("" = no password)."""
    if not password:
        return {}
    salt = secrets.token_hex(16)
    return {"password_hash": _hash(password, salt), "salt": salt}


def has_password(cfg: dict) -> bool:
    return bool(cfg.get("password_hash") and cfg.get("salt"))


def password_ok(cfg: dict, password: str) -> bool:
    if not has_password(cfg):
        return False
    try:
        got = _hash(str(password or ""), cfg["salt"])
    except ValueError:
        return False
    return hmac.compare_digest(got, cfg["password_hash"])


def where(host: str, headers) -> str:
    """'local' (this PC), 'lan' (home network) or 'remote' (internet /
    tunnel).  ``headers``: any mapping with lower-case keys."""
    if any(h in headers for h in PROXY_HEADERS):
        return "remote"
    try:
        ip = ipaddress.ip_address((host or "").split("%")[0])
    except ValueError:
        return "remote"
    if ip.is_loopback:
        return "local"
    if ip.is_private or ip.is_link_local:
        return "lan"
    return "remote"


def admit(cfg: dict, place: str, password: str) -> tuple[bool, str]:
    """May this connection in?  (ok, reason for the login dialog)."""
    if place == "local":
        return True, ""
    if has_password(cfg):
        if password_ok(cfg, password):
            return True, ""
        return False, ("wrong password" if password else "password needed")
    if place == "lan":
        return True, ""
    return False, ("no access password is set on the main iCAS yet - set one "
                   "there (STEWARDS) before stewards connect over the internet")


def clean_name(name: str, fallback: str = "Steward") -> str:
    name = re.sub(r"[\x00-\x1f<>]", "", str(name or "")).strip()
    return name[:MAX_NAME] or fallback


def new_token() -> str:
    return secrets.token_urlsafe(24)
