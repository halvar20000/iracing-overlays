@echo off
REM ============================================================
REM  iRacing Overlay Launcher — starts all overlay scripts in
REM  their own console windows. Close a window to stop that
REM  overlay.
REM
REM  !!! MAINTENANCE RULE !!!
REM  When a new iracing_*.py overlay is added to this folder,
REM  also update:
REM    - the SCRIPTS list in launch_all.py
REM    - the OVERLAYS list in launch_gui.py
REM    - add a new `start "..." cmd /k python ...` line below
REM  See CLAUDE.md in this folder for details.
REM ============================================================

cd /d "%~dp0"

echo Starting iRacing overlays...
echo.
echo   Dashboard         http://localhost:5000
echo   Grid              http://localhost:5001
echo   Results (full)    http://localhost:5002
echo   Results (lite)    http://localhost:5003
echo   Live indicator    http://localhost:5004
echo   Live standings    http://localhost:5005
echo   Livery overlay    http://localhost:5006
echo   Track map         http://localhost:5007
echo   Flag overlay      http://localhost:5008
echo   Race logger       http://localhost:5009
echo   Session info      http://localhost:5010
echo.

start "iRacing Dashboard"       cmd /k python iracing_dashboard.py
start "iRacing Grid"            cmd /k python iracing_grid.py
start "iRacing Results"         cmd /k python iracing_results.py
start "iRacing Results Lite"    cmd /k python iracing_results_lite.py
start "iRacing Live Indicator"  cmd /k python iracing_live_indicator.py
start "iRacing Live Standings"  cmd /k python iracing_standings.py
start "iRacing Livery"          cmd /k python iracing_livery.py
start "iRacing Trackmap"        cmd /k python iracing_trackmap.py
start "iRacing Flag Overlay"    cmd /k python flag_overlay.py
start "iRacing Race Logger"     cmd /k python iracing_race_logger.py
start "iRacing Session Info"    cmd /k python iracing_session_info.py

echo All 11 overlays launched. You can close this window.
timeout /t 5 >nul
