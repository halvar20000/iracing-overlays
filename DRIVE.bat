@echo off
REM ================================================================
REM  DRIVE.bat  —  ONE-CLICK DRIVING MODE
REM
REM  Double-click this to start the on-top overlays for DRIVING:
REM      Corner Cues  +  Quali Delta  +  Track Map
REM
REM  To put it on the desktop: right-click this file -> Send to ->
REM  Desktop (create shortcut). Then just double-click the icon
REM  before a session.
REM
REM  To STOP everything: close this window, or press Ctrl+C, or
REM  close all three overlay windows.
REM
REM  iRacing must run in BORDERLESS WINDOWED mode
REM  (Options -> Graphics -> windowed + borderless).
REM ================================================================

cd /d "%~dp0"
python drive.py

echo.
echo (Driving mode stopped. Press any key to close this window.)
pause >nul
