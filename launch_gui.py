"""
iRacing Overlay Launcher — GUI
------------------------------
A small desktop app to start, stop, and monitor the iRacing overlay
scripts from a single window.

!!! MAINTENANCE RULE !!!
When you add a new iracing_*.py overlay to this folder, you MUST also:
  1. Add it to the OVERLAYS list below
  2. Add it to the SCRIPTS list in launch_all.py
  3. Add a `start "..." cmd /k python ...` line to launch_all.bat
See CLAUDE.md in this folder for details.

Usage:
    python launch_gui.py

Features:
  - Status dot per overlay (green = running, grey = stopped, red = crashed)
  - Start / Stop / Open-in-browser per overlay
  - Start All / Stop All buttons
  - Collapsible log pane showing each script's stdout, tagged by overlay
  - Clean shutdown of all child processes when you close the window
  - No pip dependencies beyond Python itself (uses stdlib tkinter)

Place this next to your iracing_*.py scripts and run it.
"""

import os
import queue
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

# ---------------------------------------------------------------------------
# Overlay definitions — (tag, friendly name, script filename, port, tag color)
# ---------------------------------------------------------------------------
OVERLAYS = [
    ("dashboard", "Live Telemetry Dashboard", "iracing_dashboard.py",      5000, "#9146FF"),  # twitch purple
    ("grid",      "Qualifying Grid",          "iracing_grid.py",           5001, "#22c9e0"),  # cyan
    ("results",   "Race Results (full)",      "iracing_results.py",        5002, "#ffd166"),  # yellow
    ("lite",      "Race Results (lite)",      "iracing_results_lite.py",   5003, "#4ade80"),  # green
    ("live",      "LIVE / REPLAY Indicator",  "iracing_live_indicator.py", 5004, "#DC0028"),  # cas red
    ("standings", "Live Standings",           "iracing_standings.py",      5005, "#ff6b35"),  # orange
    ("livery",    "Livery (On Camera)",       "iracing_livery.py",         5006, "#a371f7"),  # violet
    ("trackmap",  "Track Map",                "iracing_trackmap.py",       5007, "#4ade80"),  # green
    ("flag",      "Flag Overlay",             "flag_overlay.py",           5008, "#61b4ff"),  # bright blue
]

HERE = Path(__file__).resolve().parent

# Theme
COLOR_BG        = "#0a0a0f"
COLOR_PANEL     = "#14141c"
COLOR_PANEL_ALT = "#1b1b26"
COLOR_TEXT      = "#e6e6ef"
COLOR_MUTED     = "#8a8aa0"
COLOR_ACCENT    = "#e63946"
COLOR_ACCENT2   = "#ff6b35"
COLOR_OK        = "#4ade80"
COLOR_WARN      = "#ffd166"
COLOR_BAD       = "#ff4d6d"
COLOR_IDLE      = "#3a3a4a"


# ---------------------------------------------------------------------------
# OverlayController — wraps one subprocess and exposes start/stop/status
# ---------------------------------------------------------------------------
class OverlayController:
    def __init__(self, tag, script, log_queue):
        self.tag = tag
        self.script = script
        self.log_queue = log_queue
        self.proc = None
        self._reader_thread = None

    @property
    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    @property
    def exit_code(self):
        return self.proc.poll() if self.proc else None

    def start(self):
        if self.is_running:
            return
        script_path = HERE / self.script
        if not script_path.exists():
            self.log_queue.put((self.tag, f"ERROR: {self.script} not found next to launcher."))
            return

        popen_kwargs = dict(
            args=[sys.executable, "-u", str(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(HERE),
            bufsize=0,
        )
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            self.proc = subprocess.Popen(**popen_kwargs)
        except Exception as e:
            self.log_queue.put((self.tag, f"ERROR starting: {e}"))
            self.proc = None
            return

        self.log_queue.put((self.tag, f"started (pid {self.proc.pid})"))
        self._reader_thread = threading.Thread(
            target=self._read_output,
            daemon=True,
        )
        self._reader_thread.start()

    def _read_output(self):
        assert self.proc is not None
        try:
            for raw in iter(self.proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self.log_queue.put((self.tag, line))
        except Exception:
            pass
        finally:
            try:
                self.proc.stdout.close()
            except Exception:
                pass
            rc = self.proc.poll()
            self.log_queue.put((self.tag, f"exited (code {rc})"))

    def stop(self, grace=3.0):
        if not self.is_running:
            return
        try:
            if os.name == "nt":
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.proc.terminate()
        except Exception:
            pass

        deadline = time.time() + grace
        while self.is_running and time.time() < deadline:
            time.sleep(0.1)

        if self.is_running:
            try:
                self.proc.kill()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
class LauncherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("iRacing Overlay Launcher")
        self.geometry("780x520")
        self.minsize(720, 420)
        self.configure(bg=COLOR_BG)

        self.log_queue = queue.Queue()
        self.controllers = {
            o[0]: OverlayController(o[0], o[2], self.log_queue)
            for o in OVERLAYS
        }

        self._build_ui()
        self._poll_status()
        self._poll_log_queue()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----- UI construction -------------------------------------------------
    def _build_ui(self):
        self._setup_styles()

        # --- header ---
        header = tk.Frame(self, bg=COLOR_BG)
        header.pack(fill="x", padx=16, pady=(14, 6))

        title = tk.Label(
            header,
            text="iRacing Overlays",
            bg=COLOR_BG, fg=COLOR_TEXT,
            font=("Segoe UI", 17, "bold"),
        )
        title.pack(side="left")

        subtitle = tk.Label(
            header,
            text="Start, stop, and monitor all your OBS overlays",
            bg=COLOR_BG, fg=COLOR_MUTED,
            font=("Segoe UI", 9),
        )
        subtitle.pack(side="left", padx=(10, 0), pady=(6, 0))

        btn_start_all = tk.Button(
            header, text="Start All", command=self._start_all,
            bg=COLOR_ACCENT, fg="white", activebackground=COLOR_ACCENT2,
            activeforeground="white", relief="flat", padx=14, pady=6,
            font=("Segoe UI", 9, "bold"), cursor="hand2",
        )
        btn_start_all.pack(side="right", padx=(6, 0))

        btn_stop_all = tk.Button(
            header, text="Stop All", command=self._stop_all,
            bg=COLOR_PANEL_ALT, fg=COLOR_TEXT, activebackground="#2a2a38",
            activeforeground=COLOR_TEXT, relief="flat", padx=14, pady=6,
            font=("Segoe UI", 9, "bold"), cursor="hand2",
        )
        btn_stop_all.pack(side="right")

        # --- overlay rows ---
        rows_container = tk.Frame(self, bg=COLOR_BG)
        rows_container.pack(fill="x", padx=16, pady=(6, 10))

        self._row_widgets = {}
        for i, (tag, name, script, port, color) in enumerate(OVERLAYS):
            self._row_widgets[tag] = self._build_row(rows_container, tag, name, script, port, color, i)

        # --- log pane (collapsible) ---
        log_header = tk.Frame(self, bg=COLOR_BG)
        log_header.pack(fill="x", padx=16, pady=(4, 0))

        self._log_visible = tk.BooleanVar(value=True)
        self._log_toggle_btn = tk.Button(
            log_header,
            text="▼  Hide log",
            command=self._toggle_log,
            bg=COLOR_BG, fg=COLOR_MUTED,
            activebackground=COLOR_BG, activeforeground=COLOR_TEXT,
            relief="flat", cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )
        self._log_toggle_btn.pack(side="left")

        clear_btn = tk.Button(
            log_header, text="Clear", command=self._clear_log,
            bg=COLOR_BG, fg=COLOR_MUTED,
            activebackground=COLOR_BG, activeforeground=COLOR_TEXT,
            relief="flat", cursor="hand2",
            font=("Segoe UI", 9),
        )
        clear_btn.pack(side="right")

        self._log_frame = tk.Frame(self, bg=COLOR_PANEL)
        self._log_frame.pack(fill="both", expand=True, padx=16, pady=(2, 14))

        self._log_text = tk.Text(
            self._log_frame, bg=COLOR_PANEL, fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT, relief="flat",
            font=("Consolas", 9), wrap="none", height=10,
        )
        self._log_text.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        self._log_text.configure(state="disabled")

        log_scroll = ttk.Scrollbar(self._log_frame, orient="vertical", command=self._log_text.yview)
        log_scroll.pack(side="right", fill="y", pady=8, padx=(0, 4))
        self._log_text.configure(yscrollcommand=log_scroll.set)

        # Color tags for log output
        for tag, _, _, _, color in OVERLAYS:
            self._log_text.tag_configure(f"tag_{tag}", foreground=color)
        self._log_text.tag_configure("muted", foreground=COLOR_MUTED)

    def _build_row(self, parent, tag, name, script, port, color, index):
        row = tk.Frame(parent, bg=COLOR_PANEL if index % 2 == 0 else COLOR_PANEL_ALT)
        row.pack(fill="x", pady=1)

        status = tk.Canvas(row, width=14, height=14, bg=row["bg"], highlightthickness=0)
        status.create_oval(2, 2, 12, 12, fill=COLOR_IDLE, outline="")
        status.pack(side="left", padx=(14, 10), pady=10)

        info = tk.Frame(row, bg=row["bg"])
        info.pack(side="left", fill="x", expand=True)

        name_lbl = tk.Label(
            info, text=name, bg=row["bg"], fg=COLOR_TEXT,
            font=("Segoe UI", 10, "bold"), anchor="w",
        )
        name_lbl.pack(anchor="w")

        meta_lbl = tk.Label(
            info,
            text=f"{script}   ·   http://localhost:{port}",
            bg=row["bg"], fg=COLOR_MUTED,
            font=("Segoe UI", 8), anchor="w",
        )
        meta_lbl.pack(anchor="w")

        btn_open = tk.Button(
            row, text="Open",
            command=lambda p=port: webbrowser.open(f"http://localhost:{p}"),
            bg=row["bg"], fg=COLOR_MUTED,
            activebackground=row["bg"], activeforeground=COLOR_TEXT,
            relief="flat", cursor="hand2", font=("Segoe UI", 9),
            padx=8, pady=4,
        )
        btn_open.pack(side="right", padx=(4, 14))

        btn_stop = tk.Button(
            row, text="Stop",
            command=lambda t=tag: self._stop_one(t),
            bg=COLOR_PANEL_ALT if index % 2 == 0 else COLOR_PANEL,
            fg=COLOR_TEXT, activebackground="#2a2a38",
            activeforeground=COLOR_TEXT, relief="flat",
            cursor="hand2", font=("Segoe UI", 9, "bold"),
            padx=12, pady=4, state="disabled",
        )
        btn_stop.pack(side="right", padx=(4, 0))

        btn_start = tk.Button(
            row, text="Start",
            command=lambda t=tag: self._start_one(t),
            bg=color, fg="white",
            activebackground=color, activeforeground="white",
            relief="flat", cursor="hand2", font=("Segoe UI", 9, "bold"),
            padx=12, pady=4,
        )
        btn_start.pack(side="right", padx=(4, 0))

        return {
            "row": row,
            "status_canvas": status,
            "btn_start": btn_start,
            "btn_stop": btn_stop,
            "btn_open": btn_open,
        }

    def _setup_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Vertical.TScrollbar",
                        background=COLOR_PANEL_ALT,
                        troughcolor=COLOR_PANEL,
                        bordercolor=COLOR_PANEL,
                        arrowcolor=COLOR_MUTED)

    # ----- actions ---------------------------------------------------------
    def _start_one(self, tag):
        self.controllers[tag].start()
        self._update_row_state(tag)

    def _stop_one(self, tag):
        threading.Thread(target=self.controllers[tag].stop, daemon=True).start()

    def _start_all(self):
        for tag in self.controllers:
            self.controllers[tag].start()
            # small stagger so scripts don't race to grab the iRacing handle
            time.sleep(0.15)
        for tag in self.controllers:
            self._update_row_state(tag)

    def _stop_all(self):
        for tag in self.controllers:
            threading.Thread(target=self.controllers[tag].stop, daemon=True).start()

    def _toggle_log(self):
        if self._log_visible.get():
            self._log_frame.pack_forget()
            self._log_toggle_btn.configure(text="▶  Show log")
            self._log_visible.set(False)
        else:
            self._log_frame.pack(fill="both", expand=True, padx=16, pady=(2, 14))
            self._log_toggle_btn.configure(text="▼  Hide log")
            self._log_visible.set(True)

    def _clear_log(self):
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    # ----- status/log polling ---------------------------------------------
    def _update_row_state(self, tag):
        c = self.controllers[tag]
        widgets = self._row_widgets[tag]
        canvas = widgets["status_canvas"]
        canvas.delete("all")

        if c.is_running:
            color = COLOR_OK
            widgets["btn_start"].configure(state="disabled")
            widgets["btn_stop"].configure(state="normal")
        else:
            rc = c.exit_code
            if rc is None or rc == 0:
                color = COLOR_IDLE
            else:
                color = COLOR_BAD
            widgets["btn_start"].configure(state="normal")
            widgets["btn_stop"].configure(state="disabled")

        canvas.create_oval(2, 2, 12, 12, fill=color, outline="")

    def _poll_status(self):
        for tag in self.controllers:
            self._update_row_state(tag)
        self.after(500, self._poll_status)

    def _poll_log_queue(self):
        try:
            for _ in range(200):  # cap per tick
                tag, line = self.log_queue.get_nowait()
                self._append_log(tag, line)
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _append_log(self, tag, line):
        self._log_text.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self._log_text.insert("end", f"{ts} ", ("muted",))
        self._log_text.insert("end", f"[{tag:>9}] ", (f"tag_{tag}",))
        self._log_text.insert("end", line + "\n")
        # keep last ~1000 lines
        lines = int(self._log_text.index("end-1c").split(".")[0])
        if lines > 1000:
            self._log_text.delete("1.0", f"{lines-1000}.0")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    # ----- shutdown --------------------------------------------------------
    def _on_close(self):
        running = [c for c in self.controllers.values() if c.is_running]
        if running:
            self._append_log("launcher", f"stopping {len(running)} overlay(s)…")
            for c in running:
                threading.Thread(target=c.stop, daemon=True).start()
            # give them a moment
            self.after(1500, self.destroy)
        else:
            self.destroy()


if __name__ == "__main__":
    app = LauncherApp()
    app.mainloop()
