"""Car manufacturer detection + logo resolution.

Maps an iRacing CarPath (or car model name) to a manufacturer slug, and
resolves that slug to a logo file served from ``frontend/brands/``.

Adapted from the car-brand helper in Thomas's iRacing broadcast-overlay
project.  The brand SVGs themselves were carried over from that project.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

BRANDS_DIR = Path(__file__).resolve().parent.parent / "frontend" / "brands"
_ALLOWED = (".svg", ".png", ".webp")

# CarPath prefix -> brand slug. Longer/more specific prefixes first.
_PREFIX_TO_BRAND: list[tuple[str, Optional[str]]] = [
    ("porsche", "porsche"), ("bmw", "bmw"), ("ferrari", "ferrari"),
    ("audi", "audi"), ("mercedesamg", "mercedes"), ("mercedes", "mercedes"),
    ("mclaren", "mclaren"), ("lamborghini", "lamborghini"),
    ("fordgt", "ford"), ("fordmustang", "ford"), ("ford", "ford"),
    ("chevroletcorvette", "chevrolet"), ("chevrolet", "chevrolet"),
    ("chevy", "chevrolet"), ("cadillac", "cadillac"),
    ("astonmartin", "aston-martin"), ("aston", "aston-martin"),
    ("acura", "acura"), ("hyundai", "hyundai"),
    ("toyota", "toyota"), ("lexus", "toyota"),
    ("renault", "renault"), ("volkswagen", "vw"), ("vw", "vw"),
    ("dallara", "dallara"), ("indycar", "dallara"),
]

# Substring fallback against a car's display/model name.
_NAME_FALLBACK: list[tuple[str, str]] = [
    ("porsche", "porsche"), ("bmw", "bmw"), ("ferrari", "ferrari"),
    ("audi", "audi"), ("mercedes", "mercedes"), ("amg", "mercedes"),
    ("mclaren", "mclaren"), ("lamborghini", "lamborghini"),
    ("huracan", "lamborghini"), ("corvette", "chevrolet"),
    ("camaro", "chevrolet"), ("chevrolet", "chevrolet"),
    ("ford", "ford"), ("mustang", "ford"), ("cadillac", "cadillac"),
    ("aston", "aston-martin"), ("acura", "acura"), ("hyundai", "hyundai"),
    ("toyota", "toyota"), ("supra", "toyota"), ("lexus", "toyota"),
    ("renault", "renault"), ("dallara", "dallara"),
]


def detect_brand(car_path: Optional[str],
                 car_name: Optional[str] = None) -> str:
    """Return a manufacturer slug for a car, or '' if unknown."""
    if car_path:
        p = car_path.lower()
        for prefix, brand in _PREFIX_TO_BRAND:
            if brand and p.startswith(prefix):
                return brand
    if car_name:
        n = car_name.lower()
        for needle, brand in _NAME_FALLBACK:
            if needle in n:
                return brand
    return ""


# ---- slug -> served logo URL --------------------------------------------
_logo_cache: dict[str, Optional[str]] = {}


def logo_url(slug: str) -> Optional[str]:
    """Resolve a brand slug to a ``/brands/<file>`` URL, or None.

    Matching is tolerant: exact stem, then 'slug-'/'slug_' prefix, then
    a substring match (so 'ferrari' finds 'ferrari-ges.svg').
    """
    if not slug:
        return None
    if slug in _logo_cache:
        return _logo_cache[slug]
    result: Optional[str] = None
    if BRANDS_DIR.is_dir():
        files = [p for p in BRANDS_DIR.iterdir()
                 if p.suffix.lower() in _ALLOWED]
        index = {p.stem.lower(): p.name for p in files}
        s = slug.lower()
        if s in index:
            result = index[s]
        if result is None:
            for stem, name in index.items():
                if stem.startswith(s + "-") or stem.startswith(s + "_"):
                    result = name
                    break
        if result is None:
            for stem, name in index.items():
                if s in stem:
                    result = name
                    break
    url = f"/brands/{result}" if result else None
    _logo_cache[slug] = url
    return url
