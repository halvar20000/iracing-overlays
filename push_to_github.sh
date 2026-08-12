#!/bin/bash
# ============================================================================
# push_to_github.sh — push the local iracing-overlays folder to
# https://github.com/halvar20000/iracing-overlays (branch main).
#
# 2026-08-12: the project folder IS a real git clone now, so the normal
# path through this script is simply add + commit + push. The old
# clone-into-temp-and-rsync dance is kept only as a fallback for a machine
# where the folder is still a plain ZIP download (e.g. the Windows PC until
# it is cloned too).
#
# Run:  bash /Volumes/AI-1/Projects/iracing-overlays-main/push_to_github.sh "commit message"
# ============================================================================
set -euo pipefail

# Was /Volumes/AI — the share is mounted as AI-1, which is why the old
# version of this script failed before it did anything.
SRC="/Volumes/AI-1/Projects/iracing-overlays-main"
REPO_HTTPS="https://github.com/halvar20000/iracing-overlays.git"
REPO_SSH="git@github.com:halvar20000/iracing-overlays.git"

MSG="${1:-Update overlays}"

# --- auth ------------------------------------------------------------------
# `gh auth status` shows an HTTPS token with repo scope, so HTTPS via the gh
# credential helper is the path of least resistance. The old repo-specific
# deploy key is used only if gh is unavailable.
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    AUTH_MODE="gh-https"
    gh auth setup-git >/dev/null 2>&1 || true
else
    AUTH_MODE="ssh"
    KEY="$HOME/.ssh/id_ed25519_iracing"
    if [ ! -f "$KEY" ]; then
        echo "ERROR: gh is not authenticated and $KEY does not exist."
        echo "Either run 'gh auth login', or generate the deploy key and"
        echo "register it at https://github.com/halvar20000/iracing-overlays/settings/keys"
        exit 1
    fi
    export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes"
fi

cd "$SRC"

# --- normal path: the folder is a real clone -------------------------------
if [ -d "$SRC/.git" ]; then
    echo "==> $SRC is a git clone — pushing directly (auth: $AUTH_MODE)"
    git add -A
    if git diff --cached --quiet; then
        echo "==> Nothing to commit — GitHub is already up to date."
        exit 0
    fi
    echo "==> Changes to be pushed:"
    git diff --cached --stat | tail -20
    git commit -m "$MSG"
    git push origin main
    echo "==> Done. https://github.com/halvar20000/iracing-overlays"
    exit 0
fi

# --- fallback: folder is a plain download, not a clone ----------------------
echo "==> $SRC is NOT a git clone — falling back to clone + rsync."
echo "    Consider cloning this machine properly; it is what caused the"
echo "    duplicate nested project folder in the first place."

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ "$AUTH_MODE" = "gh-https" ]; then
    git clone --depth 1 "$REPO_HTTPS" "$TMP/repo"
else
    git clone --depth 1 "$REPO_SSH" "$TMP/repo"
fi

# NOTE the --exclude block below. The first group is runtime junk. The
# SECOND group protects files that live ONLY on GitHub — CI and the
# standalone race-logger build. Without them, `rsync --delete` silently
# deletes the build workflow from the repo. That nearly happened on
# 2026-08-12; do not remove these lines.
rsync -a --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude 'logs' \
    --exclude 'dotd_history.json' \
    --exclude '.smbdelete*' \
    --exclude '*conflicted copy*' \
    --exclude '*(Wiederhergestellt)*' \
    --exclude '.DS_Store' \
    --exclude '*.lnk' \
    --exclude '*.zip' \
    --exclude '/custom_cameras/' \
    --exclude '.*.??????' \
    --exclude '/.github/' \
    --exclude '/RaceLogger.spec' \
    --exclude '/RACE_LOGGER.md' \
    --exclude '/build_race_logger_exe.bat' \
    --exclude '/make_race_logger_zip.sh' \
    --exclude '/start_race_logger.bat' \
    --exclude '/iracing_race_logger.py.bak_precls' \
    "$SRC/" "$TMP/repo/"

cd "$TMP/repo"
git add -A
if git diff --cached --quiet; then
    echo "==> Nothing to commit — GitHub is already up to date."
    exit 0
fi

echo "==> Changes to be pushed:"
git diff --cached --stat | tail -20
echo "==> Deletions (should normally be empty):"
git diff --cached --name-status --diff-filter=D | head -20

git commit -m "$MSG"
git push origin main
echo "==> Done. https://github.com/halvar20000/iracing-overlays"
