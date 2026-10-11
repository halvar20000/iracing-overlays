"""
iRacing Overlays — installer / updater core
-------------------------------------------
Pulls the current state of the GitHub repo and copies it over an install
folder. Used by:
  • launch_gui.py   — "update available" banner + "Update now" button
  • install.ps1     — first install (`python updater.py --install <dir>`)
  • by hand         — `python updater.py --check` / `python updater.py --update`

How it decides what is installed: `.install_info.json` (written here after
every install / update) holds the GitHub commit. A folder with no stamp — an
old manual ZIP download — counts as "version unknown", and an update is
offered. A GIT CHECKOUT (a `.git` folder, e.g. Thomas's Unraid clone on the
SMB share) is never touched: it is updated with git, the GUI only reports.

What an update does:
  1. downloads the ZIP of exactly the checked commit (codeload.github.com),
  2. writes every file of that ZIP into the folder (temp file + os.replace),
  3. never deletes anything and never writes the local runtime files below
     (configs, caches, logs — they are gitignored, so they are not in the
     ZIP anyway; the list is a second safety net),
  4. runs `pip install -r requirements.txt` when that file changed (always on
     a first install),
  5. writes `.install_info.json`.

Standard library only — it has to run before `pip install` ever did.
"""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = "halvar20000/iracing-overlays"
BRANCH = "main"
API = "https://api.github.com/repos/" + REPO
ZIP_URL = "https://codeload.github.com/" + REPO + "/zip/{sha}"
STAMP = ".install_info.json"
USER_AGENT = "iracing-overlays-updater"

# Local runtime state — never overwritten by an update, even if a file of
# that name ever lands in the repo by mistake.
PROTECTED = {
    STAMP,
    "iracing_auth.json",
    "championship_config.json",
    "championship_round_cache.json",
    "dotd_history.json",
    "proam_config.json",
    "proam_cache.json",
    "proam_countries.json",
    "cls_league_cache.json",
    "youtube_config.json",
}
PROTECTED_DIRS = ("logs/", ".git/")

HERE = Path(__file__).resolve().parent


# -----------------------------------------------------------------------------
# HTTP
# -----------------------------------------------------------------------------
def _get(url, timeout=10.0):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _get_json(url, timeout=10.0):
    return json.loads(_get(url, timeout).decode("utf-8"))


# -----------------------------------------------------------------------------
# Local state
# -----------------------------------------------------------------------------
def _git_head(root: Path):
    """Commit of a git checkout without needing git installed."""
    git = root / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head or None
        ref = head[4:].strip()
        f = git / ref
        if f.is_file():
            return f.read_text(encoding="utf-8").strip() or None
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except OSError:
        pass
    return None


def local_state(root: Path = HERE) -> dict:
    """{"kind": "git" | "zip" | "unknown", "commit": sha | None, ...}"""
    root = Path(root)
    if (root / ".git").exists():
        return {"kind": "git", "commit": _git_head(root)}
    try:
        info = json.loads((root / STAMP).read_text(encoding="utf-8"))
        if info.get("commit"):
            return {"kind": "zip", "commit": info["commit"],
                    "installed_at": info.get("installed_at")}
    except (OSError, ValueError):
        pass
    return {"kind": "unknown", "commit": None}


# -----------------------------------------------------------------------------
# Check
# -----------------------------------------------------------------------------
def check(root: Path = HERE, timeout=8.0) -> dict:
    """Compare the folder with GitHub. Never raises — errors go in "error".

    Returns kind, local, remote (sha), remote_date, behind (int or None when
    unknown), commits (newest first, first lines of the messages),
    update_available, can_update (False for git checkouts) and error.
    """
    st = local_state(root)
    out = {"kind": st["kind"], "local": st["commit"], "remote": None,
           "remote_date": None, "behind": None, "commits": [],
           "update_available": False, "can_update": st["kind"] != "git",
           "error": None}
    try:
        head = _get_json(API + "/commits/" + BRANCH, timeout)
        out["remote"] = head["sha"]
        out["remote_date"] = head["commit"]["committer"]["date"]
        if st["commit"] and st["commit"] == head["sha"]:
            out["behind"] = 0
            return out
        if st["commit"]:
            cmp_ = _get_json("%s/compare/%s...%s" % (API, st["commit"], head["sha"]),
                             timeout)
            # base = installed, head = GitHub: ahead_by = commits GitHub has
            # that this folder does not.
            out["behind"] = int(cmp_.get("ahead_by", 0))
            msgs = [c["commit"]["message"].splitlines()[0]
                    for c in cmp_.get("commits", [])]
            out["commits"] = list(reversed(msgs))
            if out["behind"] == 0:
                return out
        else:
            out["commits"] = [head["commit"]["message"].splitlines()[0]]
        out["update_available"] = True
    except urllib.error.HTTPError as e:
        if e.code == 404 and st["commit"]:
            # Local commit unknown to GitHub (unpushed work in a checkout):
            # can't tell how far behind — just report the newest commit.
            out["update_available"] = st["kind"] != "git"
            out["error"] = "local commit not on GitHub"
        elif e.code == 403:
            out["error"] = "GitHub rate limit — try again later"
        else:
            out["error"] = "GitHub HTTP %s" % e.code
    except Exception as e:  # offline, DNS, timeout …
        out["error"] = "no connection to GitHub (%s)" % e.__class__.__name__
    return out


# -----------------------------------------------------------------------------
# Apply
# -----------------------------------------------------------------------------
def _python_for_pip():
    """pythonw.exe has no console; pip runs fine with it, but prefer the
    console python next to it so output is captured the normal way."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        cand = exe.with_name("python.exe")
        if cand.is_file():
            return str(cand)
    return str(exe)


def pip_install(root: Path, log=print) -> bool:
    req = Path(root) / "requirements.txt"
    if not req.is_file():
        return True
    cmd = [_python_for_pip(), "-m", "pip", "install", "--disable-pip-version-check",
           "-r", str(req)]
    log("pip install -r requirements.txt …")
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        p = subprocess.Popen(cmd, cwd=str(root), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, **kw)
        for raw in iter(p.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line and not line.startswith("Requirement already satisfied"):
                log("  " + line)
        rc = p.wait()
    except Exception as e:
        log("pip failed to run: %r" % e)
        return False
    log("pip finished (exit %d)" % rc)
    return rc == 0


def _sha256(path: Path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def apply_update(root: Path = HERE, sha: str = None, log=print,
                 run_pip=None) -> dict:
    """Download commit `sha` (default: newest on BRANCH) and copy it over
    `root`. run_pip: None = only when requirements.txt changed, True/False
    forces. Raises RuntimeError with a readable message on failure."""
    root = Path(root)
    if (root / ".git").exists():
        raise RuntimeError("this folder is a git checkout — update it with git pull")
    root.mkdir(parents=True, exist_ok=True)

    if not sha:
        sha = _get_json(API + "/commits/" + BRANCH)["sha"]
    log("downloading %s @ %s …" % (REPO, sha[:7]))
    try:
        data = _get(ZIP_URL.format(sha=sha), timeout=120)
    except Exception as e:
        raise RuntimeError("download failed: %s" % e)
    log("downloaded %.1f MB" % (len(data) / 1e6))
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise RuntimeError("download is not a valid ZIP")

    req_before = _sha256(root / "requirements.txt")
    written = skipped = 0
    for info in zf.infolist():
        name = info.filename
        # GitHub ZIPs have one top folder "<repo>-<sha>/" — strip it.
        rel = name.split("/", 1)[1] if "/" in name else ""
        if not rel or info.is_dir():
            continue
        if (rel in PROTECTED or rel.startswith(PROTECTED_DIRS)
                or ".." in Path(rel).parts or Path(rel).is_absolute()):
            skipped += 1
            continue
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = zf.read(info)
        try:
            if dest.is_file() and dest.stat().st_size == len(payload) \
                    and dest.read_bytes() == payload:
                continue
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=".upd-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
            os.replace(tmp, dest)
        except OSError as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise RuntimeError("could not write %s: %s" % (rel, e))
        written += 1
    log("%d file(s) updated%s" % (written, ", %d protected skipped" % skipped
                                  if skipped else ""))

    req_changed = _sha256(root / "requirements.txt") != req_before
    pip_ok = True
    if run_pip or (run_pip is None and req_changed):
        pip_ok = pip_install(root, log)

    (root / STAMP).write_text(json.dumps({
        "repo": REPO, "branch": BRANCH, "commit": sha,
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, indent=2), encoding="utf-8")
    log("now at %s" % sha[:7])
    return {"commit": sha, "written": written, "requirements_changed": req_changed,
            "pip_ok": pip_ok}


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="iRacing Overlays installer / updater")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="only report (default)")
    g.add_argument("--update", action="store_true", help="update this folder")
    g.add_argument("--install", metavar="DIR", help="fresh install into DIR (+ pip)")
    ap.add_argument("--no-pip", action="store_true", help="skip pip install")
    a = ap.parse_args(argv)

    try:
        if a.install:
            r = apply_update(Path(a.install), run_pip=not a.no_pip)
            return 0 if r["pip_ok"] else 2
        if a.update:
            r = apply_update(HERE, run_pip=False if a.no_pip else None)
            return 0 if r["pip_ok"] else 2
    except RuntimeError as e:
        print("ERROR: %s" % e)
        return 1

    r = check(HERE)
    print("folder:  %s (%s)" % (HERE, r["kind"]))
    print("local:   %s" % ((r["local"] or "unknown")[:7]))
    print("GitHub:  %s  %s" % ((r["remote"] or "?")[:7], r["remote_date"] or ""))
    if r["error"]:
        print("note:    %s" % r["error"])
    if r["update_available"]:
        print("UPDATE AVAILABLE (%s new):" % (r["behind"] if r["behind"] is not None else "?"))
        for m in r["commits"][:20]:
            print("  - " + m)
    elif r["behind"] == 0:
        print("up to date")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
