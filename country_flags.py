"""
Country flags for the overlays
------------------------------
Resolves a driver's country to a flag file in ./flags/ (flag-icons, MIT —
see flags/NOTICE.txt).

Sources, first hit wins:
  1. iRacing "flair" — the flag a driver picks in his iRacing profile.
     DriverInfo.Drivers[].FlairName carries the country NAME ("Germany",
     "Switzerland"); it is mapped to an ISO code via flags/country.json.
     Non-country flairs (none, pride, ...) simply don't map.
  2. The CLS registration country (cls_proam.ProAmRoster.country), keyed
     by iRacing customer ID — covers league drivers whose flair is empty.

Everything returns lowercase ISO-3166 alpha-2 codes (plus flag-icons'
gb-eng / gb-sct / gb-wls), or None.
"""

import json
from pathlib import Path

FLAGS_DIR = Path(__file__).resolve().parent / "flags"

# iRacing / common spellings that differ from flag-icons' country.json names.
_ALIASES = {
    "usa": "us", "united states of america": "us", "united states": "us",
    "great britain": "gb", "uk": "gb", "united kingdom": "gb",
    "england": "gb-eng", "scotland": "gb-sct", "wales": "gb-wls",
    "northern ireland": "gb-nir",
    "czech republic": "cz", "czechia": "cz",
    "south korea": "kr", "korea": "kr", "republic of korea": "kr",
    "russia": "ru", "russian federation": "ru",
    "the netherlands": "nl", "holland": "nl",
    "türkiye": "tr", "turkey": "tr",
    "ivory coast": "ci", "taiwan": "tw", "vietnam": "vn",
    "uae": "ae", "united arab emirates": "ae",
}


def _load_names() -> dict:
    names = {}
    try:
        for c in json.loads((FLAGS_DIR / "country.json").read_text(encoding="utf-8")):
            names[c["name"].strip().lower()] = c["code"].lower()
    except Exception as e:
        print(f"[flags] Could not read flags/country.json: {e}")
    names.update(_ALIASES)
    return names


_NAMES = _load_names()


def has_flag(code) -> bool:
    return bool(code) and (FLAGS_DIR / f"{code}.svg").is_file()


def code_from_name(name) -> "str | None":
    """'Germany' -> 'de'. None for empty / non-country flairs."""
    key = (name or "").strip().lower()
    if not key:
        return None
    code = _NAMES.get(key)
    if not code and len(key) == 2:
        code = key            # already an ISO code
    return code if has_flag(code) else None


def code_from_iso(iso) -> "str | None":
    code = (iso or "").strip().lower()
    return code if has_flag(code) else None


def flag_path(code) -> "Path | None":
    code = (code or "").strip().lower()
    # Only [a-z-] codes — this value comes from a URL.
    if not code or any(ch not in "abcdefghijklmnopqrstuvwxyz-" for ch in code):
        return None
    p = FLAGS_DIR / f"{code}.svg"
    return p if p.is_file() else None
