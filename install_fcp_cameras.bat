@echo off
REM ============================================================================
REM  install_fcp_cameras.bat
REM  Installs the FCP broadcast camera pack (custom_cameras_2\, the newer
REM  and more complete pack) into iRacing.
REM
REM  Run this ON THE WINDOWS PC that runs iRacing, from inside the project
REM  folder (double-click, or run in cmd). It:
REM    1. Backs up your current iRacing cameras to a timestamped folder.
REM    2. Copies custom_cameras_2\cars + custom_cameras_2\tracks into
REM       %USERPROFILE%\Documents\iRacing\cameras (merging/overwriting the
REM       same-named .cam sets only).
REM  iRacing must be CLOSED while this runs.
REM ============================================================================
setlocal

set "SRC=%~dp0custom_cameras_2"
set "DST=%USERPROFILE%\Documents\iRacing\cameras"

echo.
echo  FCP camera pack installer
echo  -------------------------
echo  Source : %SRC%
echo  Target : %DST%
echo.

if not exist "%SRC%\tracks" (
  echo  ERROR: %SRC%\tracks not found. Run this from the project folder that
  echo         contains the custom_cameras_2 folder.
  goto :end
)

if not exist "%DST%" (
  echo  NOTE: %DST% does not exist yet - creating it.
  mkdir "%DST%"
)

REM --- 1. Back up existing cameras -------------------------------------------
set "STAMP=%DATE:/=-%_%TIME::=-%"
set "STAMP=%STAMP: =0%"
set "BACKUP=%USERPROFILE%\Documents\iRacing\cameras_backup_%STAMP%"
echo  Backing up current cameras to:
echo    %BACKUP%
robocopy "%DST%" "%BACKUP%" /E /NFL /NDL /NJH /NJS /NC /NS >nul
echo  Backup done.
echo.

REM --- 2. Install the FCP pack -----------------------------------------------
echo  Installing FCP cars...
robocopy "%SRC%\cars"   "%DST%\cars"   /E /NFL /NDL /NJH /NJS /NC /NS >nul
echo  Installing FCP tracks...
robocopy "%SRC%\tracks" "%DST%\tracks" /E /NFL /NDL /NJH /NJS /NC /NS >nul

echo.
echo  Done. FCP cameras installed.
echo  In iRacing (replay, Ctrl-F12) the tuned TV1/TV2/TV3/Chase/Blimp/Chopper
echo  angles are now active. To undo, delete the cameras folder and rename the
echo  backup folder above back to "cameras".
:end
echo.
pause
endlocal
