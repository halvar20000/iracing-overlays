@echo off
REM ---------------------------------------------------------------------
REM  Start the race logger from source (no .exe).
REM  First run creates a private virtual environment and installs the three
REM  packages it needs; later runs start instantly.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Get it from python.org ^(tick "Add to PATH"^).
  pause & exit /b 1
)

if not exist ".venv_logger\Scripts\python.exe" (
  echo == First run: setting up ==
  python -m venv .venv_logger
  .venv_logger\Scripts\python.exe -m pip install --upgrade pip
  .venv_logger\Scripts\python.exe -m pip install flask pyirsdk requests
)

echo.
echo Race Logger starting - leave this window open during the race.
echo Setup / upload page:  http://localhost:5009/league
echo.
.venv_logger\Scripts\python.exe iracing_race_logger.py
pause
