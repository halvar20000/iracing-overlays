"""
car_brands.py
-------------
Maps an iRacing CarPath (and CarScreenName as fallback) to a short brand
slug, and resolves that slug to an image file in the ./brands/ folder.

Used by iracing_standings.py (and potentially other overlays) to show a
manufacturer logo next to each driver.

Add new cars to CAR_PREFIX_TO_BRAND as iRacing releases them. Matching is
done on CarPath prefix (lowercased), so `porsche911cup` and `porsche992cup`
both match the `porsche` prefix rule.

Brand logos go in ./brands/ as SVG or PNG. Filename matching is tolerant:
  slug "ferrari"       matches  ferrari-ges.svg, ferrari.svg, Ferrari.png
  slug "mercedes"      matches  mercedes-benz.svg, mercedesamg.svg
  slug "vw"            matches  VW.svg, volkswagen.svg

Resolution preference: exact match → starts-with-slug → contains-slug.
"""

from __future__ import annotations
import os
from pathlib import Path

BRANDS_DIR = Path(__file__).resolve().parent / "brands"
ALLOWED_EXT = (".svg", ".png", ".jpg", ".jpeg", ".webp")


# ---------------------------------------------------------------------------
# CarPath prefix → brand slug
# ---------------------------------------------------------------------------
# Longer / more specific prefixes should come first. Matching is case-insensitive.
CAR_PREFIX_TO_BRAND: list[tuple[str, str]] = [
    # Porsche
    ("porsche",            "porsche"),

    # BMW
    ("bmw",                "bmw"),

    # Ferrari
    ("ferrari",            "ferrari"),

    # Audi
    ("audi",               "audi"),

    # Mercedes
    ("mercedesamg",        "mercedes"),
    ("mercedes",           "mercedes"),

    # McLaren
    ("mclaren",            "mclaren"),

    # Lamborghini
    ("lamborghini",        "lamborghini"),

    # Ford
    ("fordgt",             "ford"),
    ("fordmustang",        "ford"),
    ("fordfiesta",         "ford"),
    ("fordfocus",          "ford"),
    ("ford",               "ford"),

    # Chevrolet / GM
    ("chevrolet",          "chevrolet"),
    ("chevy",              "chevrolet"),
    ("chevyvette",         "chevrolet"),
    ("chevycamaro",        "chevrolet"),
    ("chevysilverado",     "chevrolet"),
    ("chevyimpala",        "chevrolet"),
    ("chevymonte",         "chevrolet"),
    ("chevroletcorvette",  "chevrolet"),
    ("chevroletcamaro",    "chevrolet"),

    # Cadillac
    ("cadillac",           "cadillac"),

    # Aston Martin
    ("astonmartin",        "aston-martin"),
    ("aston",              "aston-martin"),

    # Acura
    ("acura",              "acura"),

    # Hyundai
    ("hyundai",            "hyundai"),

    # Toyota / Lexus
    ("toyota",             "toyota"),
    ("lexus",              "toyota"),  # Toyota-owned; if you have a lexus.svg, add that slug here

    # Renault
    ("renault",            "renault"),

    # Volkswagen
    ("vw",                 "vw"),
    ("volkswagen",         "vw"),

    # Dallara
    ("dallara",            "dallara"),

    # Common IndyCar / NASCAR / SRX / Prototype patterns that benefit from
    # falling back to a manufacturer tag
    ("indycar",            "dallara"),   # IR18 Dallara chassis
    ("stockcar",           None),        # multi-brand (Toyota/Chevy/Ford), let screen-name logic decide
]


# Fallback: substrings found inside CarScreenName when CarPath didn't match
SCREEN_NAME_FALLBACK: list[tuple[str, str]] = [
    ("porsche",      "porsche"),
    ("bmw",          "bmw"),
    ("ferrari",      "ferrari"),
    ("audi",         "audi"),
    ("mercedes",     "mercedes"),
    ("mclaren",      "mclaren"),
    ("lamborghini",  "lamborghini"),
    ("ford",         "ford"),
    ("chevrolet",    "chevrolet"),
    ("chevy",        "chevrolet"),
    ("corvette",     "chevrolet"),
    ("camaro",       "chevrolet"),
    ("cadillac",     "cadillac"),
    ("aston",        "aston-martin"),
    ("acura",        "acura"),
    ("hyundai",      "hyundai"),
    ("toyota",       "toyota"),
    ("lexus",        "toyota"),
    ("renault",      "renault"),
    ("volkswagen",   "vw"),
    (" vw ",         "vw"),
    ("dallara",      "dallara"),
    ("indycar",      "dallara"),
]


def detect_brand(car_path: str | None, car_screen_name: str | None) -> str | None:
    """Return a brand slug, or None if we can't determine the brand."""
    if car_path:
        path = car_path.lower()
        for prefix, brand in CAR_PREFIX_TO_BRAND:
            if brand is None:
                continue
            if path.startswith(prefix):
                return brand

    if car_screen_name:
        name = (" " + car_screen_name.lower() + " ")
        for needle, brand in SCREEN_NAME_FALLBACK:
            if needle in name:
                return brand

    return None


# ---------------------------------------------------------------------------
# Brand slug → on-disk file
# ---------------------------------------------------------------------------
def _scan_brands_dir() -> dict[str, Path]:
    """Build a tolerant index of files in ./brands/.

    Returns: { "normalized_filename_stem": Path }
    'normalized' means lowercase, with '-' and '_' kept so substring matching
    against a slug like 'mercedes' still works against 'mercedes-benz'.
    """
    index: dict[str, Path] = {}
    if not BRANDS_DIR.is_dir():
        return index
    for p in BRANDS_DIR.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() not in ALLOWED_EXT:
            continue
        key = p.stem.lower()
        index[key] = p
    return index


_INDEX_CACHE: dict[str, Path] | None = None
_INDEX_MTIME: float = 0.0


def _get_index(refresh: bool = False) -> dict[str, Path]:
    """Scan brands/ once, then cache. Auto-refresh if the folder mtime changed."""
    global _INDEX_CACHE, _INDEX_MTIME
    try:
        mtime = BRANDS_DIR.stat().st_mtime if BRANDS_DIR.is_dir() else 0.0
    except Exception:
        mtime = 0.0

    if refresh or _INDEX_CACHE is None or mtime != _INDEX_MTIME:
        _INDEX_CACHE = _scan_brands_dir()
        _INDEX_MTIME = mtime
    return _INDEX_CACHE


def resolve_logo(slug: str | None) -> Path | None:
    """Find a logo file for the given brand slug, or None.

    Preference order:
      1. exact filename stem == slug
      2. filename stem starts with 'slug-' or 'slug_'
      3. filename stem contains slug
    """
    if not slug:
        return None
    s = slug.lower().strip()
    if not s:
        return None

    idx = _get_index()
    if not idx:
        return None

    # 1) exact
    if s in idx:
        return idx[s]
    # 2) prefix with separator
    for key, path in idx.items():
        if key.startswith(s + "-") or key.startswith(s + "_"):
            return path
    # 3) contains
    for key, path in idx.items():
        if s in key:
            return path
    return None


def available_slugs() -> list[str]:
    """List the brand slugs we have logos for (useful for diagnostics)."""
    return sorted(_get_index().keys())


if __name__ == "__main__":
    # Quick self-test
    print("Brands folder:", BRANDS_DIR)
    print("Available logo files:", available_slugs())
    samples = [
        ("porsche911cup",      "Porsche 911 GT3 Cup (992)"),
        ("ferrari296gt3",      "Ferrari 296 GT3"),
        ("bmwm4gt3",           "BMW M4 GT3"),
        ("audir8lmsevo2gt3",   "Audi R8 LMS evo II GT3"),
        ("mercedesamggt3",     "Mercedes-AMG GT3 2020"),
        ("cadillacvseriesrgt", "Cadillac V-Series.R GTP"),
        ("dallarairindycar",   "Dallara IR-18"),
        ("fordmustanggt3",     "Ford Mustang GT3"),
        ("mysteryunknowncar",  "Some new iRacing car"),
    ]
    for path, name in samples:
        brand = detect_brand(path, name)
        logo  = resolve_logo(brand)
        print(f"  {path:<25s} → brand={brand!s:<14s} logo={logo.name if logo else '—'}")
