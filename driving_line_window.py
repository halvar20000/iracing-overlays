"""
driving_line_window.py
----------------------
On-top-of-the-sim client for the corner-cue overlay (port 5012).
PORTRAIT layout: vertical countdown line with the corner info
stacked alongside it. Transparent, always-on-top, CLICK-THROUGH.

iRacing must run in **borderless windowed mode** (Options ->
Graphics -> windowed + borderless), otherwise the sim's exclusive-
fullscreen surface covers every other window.

Usage:
    python driving_line_window.py            # transparent, click-through
    python driving_line_window.py --debug    # visible bg, draggable,
                                             # prints geometry on move

Position/size: defaults to the right edge of the primary screen,
vertically centered — tune the constants below. In --debug mode drag
the window where you want it, read the printed "WIN_X=..., WIN_Y=..."
line and put the values into the constants.

Requires only the standard library + (optionally) pywin32 for
click-through. Without pywin32 the window is still topmost and
transparent but eats mouse clicks — fine at the screen edge, risky
over the middle of the sim.
"""

from __future__ import annotations

import json
import sys
import tkinter as tk
import urllib.request

DATA_URL = "http://localhost:5012/data"
POLL_MS = 100
SHOW_FROM_M = 500          # cue appears this far before the corner

WIN_W, WIN_H = 170, 520
WIN_X = None               # None = stick to right screen edge (with margin)
WIN_Y = None               # None = vertically centered
EDGE_MARGIN = 30           # px from the right edge when WIN_X is None

TRANSPARENT = "#0e0e10"    # color key — anything painted in this is invisible
COL_CARD = "#101018"       # card backdrop (kept visible)
COL_EDGE = "#33333f"
COL_L = "#4da3ff"
COL_R = "#ffb14d"
COL_TXT = "#ffffff"
COL_DIM = "#9aa0ae"
COL_WARN = "#ffd24d"
COL_NOW = "#ff5050"
COL_OK = "#4dd06a"
COL_BAR_BG = "#2c2c36"

DEBUG = "--debug" in sys.argv


def make_clickthrough(root: tk.Tk) -> None:
    """WS_EX_LAYERED | WS_EX_TRANSPARENT so clicks fall through to iRacing."""
    try:
        import win32con
        import win32gui
        hwnd = win32gui.GetParent(root.winfo_id()) or root.winfo_id()
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        style |= win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)
        print("[cue-window] click-through enabled")
    except Exception as e:
        print(f"[cue-window] click-through unavailable ({e!r}) — "
              "install pywin32 for clicks to pass through to the sim")


def fetch() -> dict | None:
    try:
        with urllib.request.urlopen(DATA_URL, timeout=0.5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


class CueWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Corner Cues")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        if not DEBUG:
            self.root.attributes("-transparentcolor", TRANSPARENT)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = WIN_X if WIN_X is not None else sw - WIN_W - EDGE_MARGIN
        y = WIN_Y if WIN_Y is not None else (sh - WIN_H) // 2
        self.root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")
        bg = "#23232e" if DEBUG else TRANSPARENT
        self.canvas = tk.Canvas(self.root, width=WIN_W, height=WIN_H,
                                bg=bg, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        if DEBUG:
            self._drag = None
            self.canvas.bind("<ButtonPress-1>", self._drag_start)
            self.canvas.bind("<B1-Motion>", self._drag_move)
        else:
            self.root.after(200, lambda: make_clickthrough(self.root))
        self.root.after(POLL_MS, self.tick)

    # --- debug dragging ---------------------------------------------------
    def _drag_start(self, e):
        self._drag = (e.x_root - self.root.winfo_x(),
                      e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        if self._drag:
            x = e.x_root - self._drag[0]
            y = e.y_root - self._drag[1]
            self.root.geometry(f"+{x}+{y}")
            print(f"[cue-window] -> set WIN_X={x}, WIN_Y={y}")

    # --- render -----------------------------------------------------------
    def tick(self):
        d = fetch()
        c = self.canvas
        c.delete("all")
        cue = (d or {}).get("cue") if d else None
        if d and d.get("connected") and d.get("track_available") and cue and cue.get("next"):
            in_c = cue.get("in_corner")
            nxt = cue["next"]
            corner = in_c or nxt[0]
            dist = 0 if in_c else nxt[0]["dist_m"]
            if in_c or dist <= SHOW_FROM_M:
                self.draw_cue(corner, dist, bool(in_c),
                              nxt[1] if (len(nxt) > 1 and not in_c) else None)
        elif DEBUG:
            msg = "waiting for overlay…" if not d else \
                  ("track not bundled" if not d.get("track_available") else "no position")
            c.create_text(WIN_W // 2, WIN_H // 2, text=msg, fill=COL_DIM,
                          font=("Segoe UI", 11), width=WIN_W - 20)
        self.root.after(POLL_MS, self.tick)

    def draw_cue(self, corner: dict, dist: float, in_corner: bool,
                 nxt2: dict | None):
        c = self.canvas
        col_dir = COL_L if corner["dir"] == "L" else COL_R
        cx = WIN_W // 2

        # card backdrop (visible — NOT the transparent key)
        c.create_rectangle(2, 2, WIN_W - 2, WIN_H - 2, fill=COL_CARD,
                           outline=COL_EDGE, width=1)

        # --- direction arrow (top) -----------------------------------------
        ay = 52
        s = 30
        if corner["dir"] == "L":
            pts = [cx - s - 6, ay, cx + 2, ay - s * 0.78, cx + 2, ay - 11,
                   cx + s - 2, ay - 11, cx + s - 2, ay + 11, cx + 2, ay + 11,
                   cx + 2, ay + s * 0.78]
        else:
            pts = [cx + s + 6, ay, cx - 2, ay - s * 0.78, cx - 2, ay - 11,
                   cx - s + 2, ay - 11, cx - s + 2, ay + 11, cx - 2, ay + 11,
                   cx - 2, ay + s * 0.78]
        c.create_polygon(*pts, fill=col_dir, outline="")

        # --- corner number + info ------------------------------------------
        name = f"T{corner['num']}"
        c.create_text(cx, 110, text=name, fill=COL_TXT,
                      font=("Segoe UI", 30, "bold"))
        if corner.get("name"):
            c.create_text(cx, 140, text=corner["name"], fill=COL_DIM,
                          font=("Segoe UI", 11, "bold"))
        c.create_text(cx, 162, text=corner["severity"], fill=col_dir,
                      font=("Segoe UI", 13, "bold"))
        sub = f"~{corner['est_kmh']} km/h"
        if corner.get("gear"):
            sub += f"  ·  {corner['gear']}."
        c.create_text(cx, 184, text=sub, fill=COL_DIM,
                      font=("Segoe UI", 12, "bold"))

        # --- distance ------------------------------------------------------
        if in_corner:
            dtxt, dcol = "APEX", COL_NOW
        else:
            dtxt = f"{int(round(dist))} m"
            dcol = COL_NOW if dist < 80 else (COL_WARN if dist < 200 else COL_TXT)
        c.create_text(cx, 218, text=dtxt, fill=dcol,
                      font=("Segoe UI", 24, "bold"))

        # --- vertical countdown line ----------------------------------------
        # Bar fills DOWNWARD toward the corner marker at the bottom — the
        # falling edge is "you, approaching the corner".
        bar_top, bar_bot = 248, WIN_H - 56
        bw = 30
        bx0, bx1 = cx - bw // 2, cx + bw // 2
        c.create_rectangle(bx0, bar_top, bx1, bar_bot, fill=COL_BAR_BG,
                           outline=COL_EDGE)
        # distance ticks every 100 m (0 at bottom, SHOW_FROM_M at top)
        span = bar_bot - bar_top
        m = 100
        while m < SHOW_FROM_M:
            ty = bar_bot - span * m / SHOW_FROM_M
            c.create_line(bx0 - 6, ty, bx0, ty, fill=COL_DIM)
            c.create_text(bx0 - 9, ty, text=str(m), fill=COL_DIM,
                          anchor="e", font=("Segoe UI", 8))
            m += 100
        frac = 1.0 if in_corner else max(0.0, min(1.0, 1 - dist / SHOW_FROM_M))
        bcol = COL_NOW if frac > 0.84 else (COL_WARN if frac > 0.6 else COL_OK)
        fill_top = bar_bot - span * (1 - frac)   # fill grows downward
        # filled portion = distance already covered (from the top down,
        # closing in on the corner marker at the bottom)
        c.create_rectangle(bx0 + 2, bar_top, bx1 - 2, fill_top,
                           fill=bcol, outline="")
        # moving "car" marker at the fill edge
        c.create_polygon(bx0 - 4, fill_top, bx0 - 14, fill_top - 7,
                         bx0 - 14, fill_top + 7, fill=COL_TXT, outline="")
        # corner marker at the bottom of the line
        c.create_rectangle(bx0 - 8, bar_bot, bx1 + 8, bar_bot + 8,
                           fill=col_dir, outline="")

        # --- next corner preview (bottom) -----------------------------------
        if nxt2:
            ar = "←" if nxt2["dir"] == "L" else "→"
            c.create_text(cx, WIN_H - 30,
                          text=f"then T{nxt2['num']} {ar}",
                          fill=COL_DIM, font=("Segoe UI", 12, "bold"))
            c.create_text(cx, WIN_H - 14,
                          text=f"{nxt2['severity']} · {int(round(nxt2['dist_m']))} m",
                          fill=COL_DIM, font=("Segoe UI", 10))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    print("[cue-window] portrait mode, reading", DATA_URL,
          "(DEBUG: visible + draggable)" if DEBUG else "(--debug to position it)")
    CueWindow().run()
