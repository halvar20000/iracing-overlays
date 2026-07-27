@echo off
REM ================================================================
REM  start_launcher.bat
REM
REM  Opens a single console window and runs `python launch_all.py`,
REM  which starts every overlay as a subprocess and prefixes each
REM  one's output with a colored tag (so you can read 10 overlays'
REM  logs in one terminal).
REM
REM  To put this on the desktop: right-click the file -> Send to ->
REM  Desktop (create shortcut). Then double-click the desktop icon
REM  whenever you want to fire everything up.
REM
REM  Press Ctrl+C in the terminal to stop all overlays cleanly.
REM ================================================================

cd /d "%~dp0"
python launch_all.py

REM If the launcher exits (e.g. Ctrl+C), pause so the user can read
REM any final messages instead of the window slamming shut.
echo.
echo (Launcher exited. Press any key to close this window.)
pause >nul
