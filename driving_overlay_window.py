"""
driving_overlay_window.py
-------------------------
Generic ON-TOP-OF-THE-SIM window for any of the HTML overlays.

It hosts one overlay URL (e.g. Quali Delta on port 5014, Track Map on
port 5007) in a small, always-on-top, frameless window you can place
over a corner of the sim while DRIVING — the same idea as
driving_line_window.py, but for the browser-rendered overlays.

You normally never call this directly — `drive.py` (double-click
DRIVE.bat) starts the right windows for you. Run it by hand only to
tune one overlay.

iRacing must run in **borderless windowed mode** (Options -> Graphics
-> windowed + borderless), otherwise the sim's exclusive-fullscreen
surface covers every other window.

Usage:
    python driving_overlay_window.py --url http://localhost:5014/?debug=1 \
        --title "Quali Delta" --x 40 --y 40 --w 380 --h 240

    # add --click-through so mouse clicks fall through to the sim
    # add --debug for a normal titled/resizable window (for positioning)

Positioning: without --click-through the window is drag-anywhere — just
grab it and move it, it remembers nothing, so put the final numbers into
drive.py once you like them. With --debug you get a normal title bar.

Requires:  pip install pywebview        (uses Edge WebView2 on Windows)
Optional:  pip install pywin32          (needed only for --click-through)
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

DARK_BG = "#0a0a0f"   # matches the overlays' dark theme; shows through
                      # transparent overlay pages so they stay readable


def _enable_click_through(title: str) -> None:
    """Best-effort: make the window let mouse clicks pass to the sim.

    Windows-only, needs pywin32. Finds the top-level window by its title
    and adds WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE. Retries
    for a few seconds because the native window appears a moment after
    webview.start().
    """
    try:
        import win32con
        import win32gui
    except Exception as e:  # pragma: no cover - non-Windows / no pywin32
        print(f"[overlay-window] click-through unavailable ({e!r}) — "
              "install pywin32 on Windows for it to work")
        return

    for _ in range(50):  # ~5 s
        hwnd = win32gui.FindWindow(None, title)
        if hwnd:
            try:
                style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                style |= (win32con.WS_EX_LAYERED
                          | win32con.WS_EX_TRANSPARENT
                          | win32con.WS_EX_NOACTIVATE)
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)
                print(f"[overlay-window] click-through enabled for {title!r}")
            except Exception as e:  # pragma: no cover
                print(f"[overlay-window] could not set click-through ({e!r})")
            return
        time.sleep(0.1)
    print(f"[overlay-window] window {title!r} not found for click-through")


def main() -> int:
    ap = argparse.ArgumentParser(description="On-top overlay window host")
    ap.add_argument("--url", required=True, help="overlay URL to display")
    ap.add_argument("--title", default="Overlay", help="window title (must be unique)")
    ap.add_argument("--x", type=int, default=40)
    ap.add_argument("--y", type=int, default=40)
    ap.add_argument("--w", type=int, default=380)
    ap.add_argument("--h", type=int, default=260)
    ap.add_argument("--click-through", action="store_true",
                    help="let clicks fall through to the sim (needs pywin32)")
    ap.add_argument("--debug", action="store_true",
                    help="normal titled, resizable window (for positioning)")
    args = ap.parse_args()

    try:
        import webview
    except ImportError:
        print("ERROR: pywebview not installed. Run:\n"
              "    pip install pywebview\n"
              "(On Windows it uses the Edge WebView2 runtime, which is\n"
              " preinstalled on Windows 10/11. If missing, get it from\n"
              " https://developer.microsoft.com/microsoft-edge/webview2/ )")
        return 1

    frameless = not args.debug
    # drag-anywhere so you can reposition a frameless window; but a
    # click-through window can't be dragged (clicks pass through), so
    # disable easy_drag in that case.
    easy_drag = frameless and not args.click_through

    webview.create_window(
        title=args.title,
        url=args.url,
        x=args.x,
        y=args.y,
        width=args.w,
        height=args.h,
        on_top=True,
        frameless=frameless,
        easy_drag=easy_drag,
        background_color=DARK_BG,
        resizable=args.debug,
    )

    if args.click_through and sys.platform == "win32":
        threading.Thread(target=_enable_click_through,
                         args=(args.title,), daemon=True).start()

    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
