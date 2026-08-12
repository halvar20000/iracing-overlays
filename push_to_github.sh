#!/bin/bash
# ============================================================================
# push_to_github.sh — deploy the local iracing-overlays-main folder to
# https://github.com/halvar20000/iracing-overlays (branch main).
#
# The local folder is a plain download (NOT a git clone), so this script:
#   1. clones the repo into a temp dir (SSH, deploy key ~/.ssh/id_ed25519)
#   2. rsyncs the working files over it (runtime junk excluded)
#   3. commits + pushes whatever changed
#
# Run in Mac Terminal:   bash /Volumes/AI/Projects/iracing-overlays-main/push_to_github.sh
# ============================================================================
set -euo pipefail

SRC="/Volumes/AI/Projects/iracing-overlays-main"
REPO="git@github.com:halvar20000/iracing-overlays.git"

# GitHub deploy keys are REPO-SPECIFIC: ~/.ssh/id_ed25519 belongs to the
# SimRacing-News repo and is rejected here ("Permission denied to deploy
# key"). This repo uses its own key. On first run the script generates it
# and tells you how to register it on GitHub, then exits.
KEY="$HOME/.ssh/id_ed25519_iracing"
if [ ! -f "$KEY" ]; then
    echo "==> No deploy key for iracing-overlays yet — generating one ..."
    ssh-keygen -t ed25519 -f "$KEY" -N "" -C "iracing-overlays deploy key" >/dev/null
    echo ""
    echo "  Add this PUBLIC key to GitHub (one-time setup):"
    echo "  1. Open https://github.com/halvar20000/iracing-overlays/settings/keys"
    echo "  2. Click 'Add deploy key'"
    echo "  3. Title: 'Mac deploy key' — paste the key below"
    echo "  4. IMPORTANT: tick 'Allow write access'"
    echo ""
    echo "  ------------------------------------------------------------"
    cat "$KEY.pub"
    echo "  ------------------------------------------------------------"
    echo ""
    echo "  (It is also in your clipboard.)"
    pbcopy < "$KEY.pub" 2>/dev/null || true
    echo "  Then run this script again."
    exit 0
fi
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes"

MSG="${1:-Add Catch-Up Battle overlay (port 5015) + Driving Mode files}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> Cloning $REPO ..."
git clone --depth 1 "$REPO" "$TMP/repo"

echo "==> Syncing working files ..."
rsync -a --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude 'logs' \
    --exclude 'dotd_history.json' \
    --exclude '.smbdelete*' \
    --exclude '*conflicted copy*' \
    --exclude '.DS_Store' \
    --exclude '*.lnk' \
    "$SRC/" "$TMP/repo/"

cd "$TMP/repo"
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
