"""One colour per car class — shared by the standings tower (tab + bar in
front of the name) and the trackmap (car dots), so a class looks the same
on both (multiclass, e.g. IEC, 2026-10-25).

iRacing's own CarClassColor first (what its UI shows — viewers know it).
A class without one, or with the SAME colour as a class already coloured,
gets the next free colour of PALETTE instead, so two classes never share
a colour.
"""

# distinct on the dark tower and on the white track line; no pure yellow
# (the trackmap's camera car) and no PRO red / AM green (WCT bars)
PALETTE = ["#ff5888", "#33ceff", "#ffb020", "#b07cff", "#7ee0a0", "#ff7a3c"]


def _hex(raw) -> str:
    try:
        v = int(raw) & 0xFFFFFF
    except (TypeError, ValueError):
        return ""
    return f"#{v:06x}" if v else ""


def class_colors(drivers) -> dict:
    """drivers: iRacing DriverInfo.Drivers entries (dicts with CarClassID /
    CarClassColor). Returns {class_id: "#rrggbb"}; pace car / spectators
    are skipped. Classes are coloured in class-id order, so the result does
    not depend on the order of the driver list."""
    raw = {}
    for d in drivers or []:
        if d.get("CarIsPaceCar") == 1 or d.get("IsSpectator") == 1:
            continue
        try:
            cid = int(d.get("CarClassID") or 0)
        except (TypeError, ValueError):
            cid = 0
        if cid not in raw or not raw[cid]:
            raw[cid] = _hex(d.get("CarClassColor"))
    out, used = {}, set()
    for cid in sorted(raw):
        c = raw[cid]
        if not c or c.lower() in used:
            c = next((p for p in PALETTE if p not in used), PALETTE[len(out) % len(PALETTE)])
        out[cid] = c
        used.add(c.lower())
    return out


def text_on(color: str) -> str:
    """Black or white text for a background colour (relative luminance)."""
    try:
        r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except (TypeError, ValueError, IndexError):
        return "#0a0a0f"
    return "#0a0a0f" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45 else "#ffffff"
