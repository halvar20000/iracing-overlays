@echo off
REM ============================================================
REM  Cloudflare Tunnel setup for iRacing Race Logger
REM  Maps livedata.simracing-hub.com -> http://localhost:5009
REM  Re-runnable: skips steps that are already done.
REM ============================================================
setlocal enabledelayedexpansion

set "TUNNEL_NAME=iracing-livedata"
set "HOSTNAME=livedata.simracing-hub.com"
set "LOCAL_PORT=5009"
set "CFG_DIR=%USERPROFILE%\.cloudflared"

echo.
echo ============================================================
echo  Cloudflare Tunnel - iRacing Race Logger
echo ============================================================
echo  Tunnel name : %TUNNEL_NAME%
echo  Hostname    : %HOSTNAME%
echo  Local URL   : http://localhost:%LOCAL_PORT%
echo  Config dir  : %CFG_DIR%
echo ============================================================
echo.
pause

REM ---------- 1) Ensure cloudflared is installed ----------
where cloudflared >nul 2>&1
if errorlevel 1 (
    echo.
    echo [1/7] cloudflared not found on PATH. Installing via winget...
    winget install --id Cloudflare.cloudflared --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo.
        echo  ERROR: winget install failed. Install manually from
        echo  https://github.com/cloudflare/cloudflared/releases/latest
        echo  Save the .exe somewhere on PATH (e.g. C:\Windows or C:\Tools).
        echo.
        pause
        exit /b 1
    )
    echo.
    echo  cloudflared installed.
    echo  *** Please CLOSE this window, open a NEW cmd, and re-run this script
    echo      so PATH refreshes. ***
    echo.
    pause
    exit /b 0
) else (
    echo [1/7] cloudflared found:
    where cloudflared
)
echo.

REM ---------- 2) Login if cert.pem is missing ----------
if exist "%CFG_DIR%\cert.pem" (
    echo [2/7] cert.pem already present - skipping login.
) else (
    echo [2/7] No cert.pem found. Browser will open for Cloudflare login.
    echo       Pick your zone "simracing-hub.com" and click Authorize.
    echo.
    pause
    cloudflared tunnel login
    if errorlevel 1 (
        echo  ERROR: tunnel login failed.
        pause
        exit /b 1
    )
)
echo.

REM ---------- 3) Create tunnel (if it doesn't exist) ----------
cloudflared tunnel list 2>nul | findstr /R /C:"\<%TUNNEL_NAME%\>" >nul
if errorlevel 1 (
    echo [3/7] Creating tunnel "%TUNNEL_NAME%"...
    cloudflared tunnel create %TUNNEL_NAME%
    if errorlevel 1 (
        echo  ERROR: tunnel create failed.
        pause
        exit /b 1
    )
) else (
    echo [3/7] Tunnel "%TUNNEL_NAME%" already exists - skipping create.
)
echo.

REM ---------- 4) Resolve UUID from tunnel list ----------
set "UUID="
for /f "tokens=1" %%I in ('cloudflared tunnel list 2^>nul ^| findstr /R /C:"\<%TUNNEL_NAME%\>"') do (
    if not defined UUID set "UUID=%%I"
)
if not defined UUID (
    echo  ERROR: could not parse tunnel UUID from "cloudflared tunnel list".
    cloudflared tunnel list
    pause
    exit /b 1
)
echo [4/7] Tunnel UUID: %UUID%
echo.

REM ---------- 5) Write config.yml ----------
set "CRED_FILE=%CFG_DIR%\%UUID%.json"
echo [5/7] Writing %CFG_DIR%\config.yml ...
> "%CFG_DIR%\config.yml" (
    echo tunnel: %UUID%
    echo credentials-file: %CRED_FILE%
    echo.
    echo ingress:
    echo   - hostname: %HOSTNAME%
    echo     service: http://localhost:%LOCAL_PORT%
    echo   - service: http_status:404
)
echo  --- config.yml ---
type "%CFG_DIR%\config.yml"
echo  ------------------
echo.

REM ---------- 6) Route DNS ----------
echo [6/7] Routing DNS %HOSTNAME% -^> %TUNNEL_NAME% ...
cloudflared tunnel route dns %TUNNEL_NAME% %HOSTNAME%
if errorlevel 1 (
    echo  Note: DNS route may already exist - continuing.
)
echo.

REM ---------- 7) Install Windows service ----------
echo [7/7] Install cloudflared as a Windows service so it auto-starts at boot?
choice /C YN /M "Install service now"
if errorlevel 2 goto skip_service

REM If the service is already installed, "service install" can fail.
REM Try uninstall-then-install so the config.yml is picked up cleanly.
sc query cloudflared >nul 2>&1
if not errorlevel 1 (
    echo  Existing service found - reinstalling to pick up new config...
    sc stop cloudflared >nul 2>&1
    cloudflared service uninstall >nul 2>&1
)
cloudflared service install
if errorlevel 1 (
    echo  ERROR: service install failed. You can still run the tunnel manually:
    echo     cloudflared tunnel run %TUNNEL_NAME%
    pause
    exit /b 1
)
sc start cloudflared >nul 2>&1
echo  Service installed and started.
goto done

:skip_service
echo  Skipped service install.
echo  Run manually with:  cloudflared tunnel run %TUNNEL_NAME%

:done
echo.
echo ============================================================
echo  Setup complete.
echo ============================================================
echo  Make sure iracing_race_logger.py is running on port %LOCAL_PORT%.
echo.
echo  Public URLs (share with viewers):
echo    https://%HOSTNAME%/share/chart
echo    https://%HOSTNAME%/share/standings
echo.
echo  Verify the service in:  services.msc  -^>  "Cloudflared agent"
echo  Stop:   sc stop cloudflared
echo  Start:  sc start cloudflared
echo.
pause
endlocal
