@echo off
REM ============================================================
REM  iCASControl - Windows launcher
REM  Double-click this file on your iRacing PC to start the app.
REM ============================================================
cd /d "%~dp0"

echo.
echo  iCASControl - starting...
echo.

REM --- Create a local virtual environment on first run ---
if not exist ".venv\" (
    echo  First run: creating Python environment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    echo  Installing dependencies ^(this only happens once^)...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

REM --- Launch the server ---
python -m backend.server

echo.
echo  iCASControl has stopped.
pause
