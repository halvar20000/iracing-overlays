#!/usr/bin/env bash
# ============================================================
#  iCASControl - macOS / Linux launcher
#  Runs in SIMULATOR mode (no iRacing on these platforms).
# ============================================================
set -e
cd "$(dirname "$0")"

echo
echo " iCASControl - starting..."
echo

if [ ! -d ".venv" ]; then
    echo " First run: creating Python environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo " Installing dependencies (this only happens once)..."
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
else
    source .venv/bin/activate
fi

python -m backend.server "$@"
