#!/bin/bash
# Build RaceLogger-source.zip locally (Mac/Linux) — the Python variant that
# drivers can run with start_race_logger.bat. The .exe is built by the
# GitHub Actions workflow (.github/workflows/build-race-logger.yml) or by
# build_race_logger_exe.bat on a Windows PC.
set -e
cd "$(dirname "$0")"
OUT="dist/RaceLogger-source.zip"
rm -rf pack/RaceLogger "$OUT"
mkdir -p pack/RaceLogger dist
cp iracing_race_logger.py iracing_sdk_base.py start_race_logger.bat \
   build_race_logger_exe.bat RaceLogger.spec RACE_LOGGER.md pack/RaceLogger/
(cd pack && zip -r "../$OUT" RaceLogger >/dev/null)
rm -rf pack
echo "built $OUT"
