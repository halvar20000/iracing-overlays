@echo off
REM ---------------------------------------------------------------------
REM  Build the standalone RaceLogger.exe on THIS Windows PC.
REM  Result: dist\RaceLogger.exe  (one file, ~15 MB, no Python needed)
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed or not on PATH. Get it from python.org.
  pause & exit /b 1
)

echo == Installing build dependencies ==
python -m pip install --upgrade pip
python -m pip install pyinstaller flask pyirsdk requests
if errorlevel 1 ( echo pip failed & pause & exit /b 1 )

echo == Building ==
python -m PyInstaller --noconfirm --clean RaceLogger.spec
if errorlevel 1 ( echo build failed & pause & exit /b 1 )

echo.
echo Done:  %cd%\dist\RaceLogger.exe
echo Copy that single file anywhere and double-click it.
pause
