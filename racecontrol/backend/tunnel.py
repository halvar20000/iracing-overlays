"""Internet access for the other stewards - a Cloudflare tunnel (step 4).

iCAS listens on 127.0.0.1 only.  ``cloudflared`` on the SAME PC opens an
outgoing connection to Cloudflare and hands out a public https link that
leads to this iCAS - no router port forward, no fixed IP, works behind any
home router.  Two ways:

* quick tunnel (default, no Cloudflare account): a new random
  https://<words>.trycloudflare.com link every start - send it to the
  stewards (STEWARDS > Internet > Copy link);
* named tunnel (fixed address, e.g. icas.example.com): create it in the
  Cloudflare dashboard (Zero Trust > Networks > Tunnels, public hostname ->
  http://localhost:8080) and put its token + hostname into
  data/tunnel_config.json {"token": "...", "hostname": "icas.example.com"}.

Every request through the tunnel carries Cloudflare headers, so access.where()
calls it "remote" -> the access password is required (the tunnel does not
start without one).

cloudflared is found in data/bin/, on PATH or in Program Files; the STEWARDS
panel can download it (the official release from github.com/cloudflare).
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BIN_DIR = DATA_DIR / "bin"
CONFIG_PATH = DATA_DIR / "tunnel_config.json"
RELEASE = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
QUICK_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CONNECTED = re.compile(r"Registered tunnel connection|Connection [0-9a-f-]+ registered",
                       re.I)


def load_config(path: Optional[Path] = None) -> dict:
    path = path or CONFIG_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        print(f"[tunnel] {path.name} unreadable: {exc}", file=sys.stderr)
        return {}


def exe_name() -> str:
    return "cloudflared.exe" if os.name == "nt" else "cloudflared"


def download_asset() -> Optional[str]:
    """Release file for this PC, None where there is no single binary."""
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else (
        "386" if machine in ("x86", "i386", "i686") else "amd64")
    if os.name == "nt":
        return f"cloudflared-windows-{arch}.exe"
    if sys.platform.startswith("linux"):
        return f"cloudflared-linux-{arch}"
    return None                      # macOS: brew install cloudflared


def find_binary() -> Optional[str]:
    own = BIN_DIR / exe_name()
    if own.is_file():
        return str(own)
    found = shutil.which("cloudflared")
    if found:
        return found
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("ProgramFiles")):
            if base:
                p = Path(base) / "cloudflared" / "cloudflared.exe"
                if p.is_file():
                    return str(p)
    return None


class Tunnel:
    """One cloudflared process.  ``popen`` is injectable for the tests."""

    def __init__(self, port: int = 8080, popen=subprocess.Popen,
                 cfg: Optional[dict] = None) -> None:
        self.port = port
        self._popen = popen
        self.cfg = dict(load_config() if cfg is None else cfg)
        self.proc = None
        self.state = "off"           # off | starting | on | error | downloading
        self.url = ""
        self.error = ""
        self.lines: deque = deque(maxlen=30)
        self._lock = threading.Lock()

    # -- status ---------------------------------------------------------------

    def named(self) -> bool:
        return bool(self.cfg.get("token"))

    def status(self) -> dict:
        with self._lock:
            if self.proc is not None and self.proc.poll() is not None \
                    and self.state in ("starting", "on"):
                self.state = "error"
                self.error = ("cloudflared stopped: "
                              + (self.lines[-1] if self.lines else "no output"))
                self.proc = None
            return {"state": self.state, "url": self.url, "error": self.error,
                    "named": self.named(), "binary": bool(find_binary())}

    # -- start / stop -----------------------------------------------------------

    def command(self, exe: str) -> list[str]:
        if self.named():
            return [exe, "tunnel", "--no-autoupdate", "run",
                    "--token", str(self.cfg["token"])]
        return [exe, "tunnel", "--no-autoupdate", "--url",
                f"http://127.0.0.1:{self.port}"]

    def start(self) -> str:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return f"internet access already on {self.url}".rstrip()
            exe = find_binary()
            if exe is None:
                self.state, self.error = "error", "cloudflared not installed"
                return ("cloudflared is not installed - press Download "
                        "(or: winget install Cloudflare.cloudflared)")
            self.lines.clear()
            self.url = ""
            if self.named():
                host = str(self.cfg.get("hostname", "")).strip()
                self.url = host if host.startswith("http") else (
                    f"https://{host}" if host else "")
            kw = {}
            if os.name == "nt":
                kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self.proc = self._popen(
                    self.command(exe), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace", **kw)
            except Exception as exc:
                self.state, self.error = "error", f"cloudflared: {exc}"
                return self.error
            self.state, self.error = "starting", ""
            threading.Thread(target=self._read, args=(self.proc,),
                             daemon=True).start()
        return "internet access starting …"

    def _read(self, proc) -> None:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            with self._lock:
                self.lines.append(line[-300:])
                if proc is not self.proc:
                    return
                m = QUICK_URL.search(line)
                if m and not self.named():
                    self.url = m.group(0)
                # the quick link is printed before the connection is up
                if CONNECTED.search(line):
                    self.state = "on"

    def stop(self) -> str:
        with self._lock:
            proc, self.proc = self.proc, None
            self.state, self.url, self.error = "off", "", ""
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            return "internet access off - the link no longer works"
        return "internet access off"

    # -- download ---------------------------------------------------------------

    def download(self) -> str:
        asset = download_asset()
        if asset is None:
            return "no download for this system - brew install cloudflared"
        if self.state == "downloading":
            return "download running …"
        self.state, self.error = "downloading", ""
        threading.Thread(target=self._download, args=(asset,), daemon=True).start()
        return "downloading cloudflared …"

    def _download(self, asset: str) -> None:
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        target = BIN_DIR / exe_name()
        part = target.with_name(target.name + ".part")
        url = RELEASE + asset
        try:
            try:
                req = urllib.request.Request(url, headers={"User-Agent":
                                                           "iCASControl"})
                with urllib.request.urlopen(req, timeout=120) as resp, \
                        open(part, "wb") as f:
                    shutil.copyfileobj(resp, f)
            except Exception:
                # Windows Python often lacks the root cert (see updater.py):
                # Windows' own curl.exe has it.
                curl = shutil.which("curl")
                if not curl:
                    raise
                subprocess.run([curl, "-fsSL", "-o", str(part), url],
                               check=True, timeout=180,
                               creationflags=getattr(subprocess,
                                                     "CREATE_NO_WINDOW", 0)
                               if os.name == "nt" else 0)
            if part.stat().st_size < 1_000_000:
                raise RuntimeError("download too small")
            os.replace(part, target)
            if os.name != "nt":
                target.chmod(0o755)
            self.state, self.error = "off", ""
        except Exception as exc:
            self.state, self.error = "error", f"download failed: {exc}"[:200]
            _unlink(part)


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def wait_for(tunnel: Tunnel, state: str, timeout: float = 5.0) -> bool:
    """Test helper: wait until the tunnel reaches ``state``."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if tunnel.status()["state"] == state:
            return True
        time.sleep(0.02)
    return False
