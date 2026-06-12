#!/bin/bash
# Push ~/Nextcloud/iRacing/python/files to github.com/halvar20000/iracing-overlays
# Uses the dedicated deploy key ~/.ssh/id_ed25519_overlays
set -e
SRC="$HOME/Nextcloud/iRacing/python/files"
TMP="$(mktemp -d /tmp/iracing-overlays.XXXX)"
export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/id_ed25519_overlays -o IdentitiesOnly=yes"

echo ">> Cloning repo..."
git clone --depth 1 git@github.com:halvar20000/iracing-overlays.git "$TMP/repo"

echo ">> Copying files..."
rsync -a --delete \
  --exclude '.git' \
  --exclude 'logs/' \
  --exclude '__pycache__/' \
  --exclude '.DS_Store' \
  --exclude 'iracing_auth.json' \
  --exclude '*conflicted copy*' \
  --exclude 'push_overlays_to_github.sh.lock' \
  "$SRC/" "$TMP/repo/"

cd "$TMP/repo"
git add -A
if git diff --cached --quiet; then
  echo ">> Nothing to push - repo already up to date."
else
  git -c user.name="Thomas Herbrig" -c user.email="thomas.herbrig@gmail.com" \
    commit -m "${1:-Corner-cue overlay (port 5012) + portrait window, Coronado/Laguna 2026/Watkins Glen Cup tracks, launcher + doc updates}"
  git push origin main
  echo ">> Pushed successfully."
fi
rm -rf "$TMP"
