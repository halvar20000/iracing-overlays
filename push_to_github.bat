@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM push_to_github.bat -- deploy this folder to
REM   https://github.com/halvar20000/iracing-overlays  (branch main)
REM
REM This local folder is a plain ZIP download (NOT a git clone), so the script:
REM   1. clones the repo into a temp dir (HTTPS)
REM   2. mirrors the working files over it (runtime junk excluded)
REM   3. commits + pushes whatever changed
REM
REM Requires: Git for Windows  ->  https://git-scm.com/download/win
REM First push opens a browser to log in to GitHub (Git Credential Manager);
REM after that it is remembered.
REM
REM Usage:  double-click this file, or from a terminal:
REM         push_to_github.bat "optional commit message"
REM ============================================================================

set "SRC=%~dp0"
set "REPO=https://github.com/halvar20000/iracing-overlays.git"

set "MSG=%~1"
if "%MSG%"=="" set "MSG=Add Stream Deck hide_ui endpoint + STREAMDECK.md, update overlay lists"

where git >nul 2>nul
if errorlevel 1 (
  echo ERROR: git is not installed or not on PATH.
  echo Install "Git for Windows" from https://git-scm.com/download/win then retry.
  pause
  exit /b 1
)

set "TMP=%TEMP%\iracing_push_%RANDOM%"
echo ==^> Cloning %REPO% ...
git clone --depth 1 "%REPO%" "%TMP%\repo"
if errorlevel 1 ( echo Clone failed. & pause & exit /b 1 )

echo ==^> Mirroring working files ...
set "RCLOG=%TMP%\robocopy.log"
robocopy "%SRC%." "%TMP%\repo" /MIR /R:1 /W:1 /XD .git __pycache__ logs custom_cameras /XF dotd_history.json *.lnk desktop.ini .DS_Store *conflicted* .smbdelete* *.smbdelete* /NP /LOG:"%RCLOG%" >nul
set "RC=%errorlevel%"
if %RC% GEQ 16 ( echo Robocopy FATAL error %RC% - nothing copied. See "%RCLOG%". & pause & exit /b 1 )
if %RC% GEQ 8 ( echo WARNING: robocopy skipped some unreadable files ^(code %RC%^) - continuing. Details in "%RCLOG%". )

cd /d "%TMP%\repo"
git add -A
git diff --cached --quiet
if %errorlevel%==0 (
  echo ==^> Nothing to commit - GitHub is already up to date.
  cd /d "%SRC%"
  rd /s /q "%TMP%"
  pause
  exit /b 0
)

echo ==^> Changes to be pushed:
git diff --cached --stat

git commit -m "%MSG%"
git push origin main
if errorlevel 1 ( echo Push failed. & pause & exit /b 1 )

echo.
echo ==^> Done. https://github.com/halvar20000/iracing-overlays
cd /d "%SRC%"
rd /s /q "%TMP%"
pause
