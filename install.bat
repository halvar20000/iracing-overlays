@echo off
REM iRacing Overlays - installer. Double-click to install or repair.
REM Uses install.ps1 next to this file, or downloads it from GitHub.
setlocal
if not exist "%~dp0install.ps1" goto online
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
goto end
:online
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; $s=(New-Object Net.WebClient).DownloadString('https://raw.githubusercontent.com/halvar20000/iracing-overlays/main/install.ps1'); & ([ScriptBlock]::Create($s))"
:end
echo.
pause
