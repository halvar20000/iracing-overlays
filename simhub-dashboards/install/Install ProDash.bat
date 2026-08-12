@echo off
REM Double-click to install SimHub Pro Dash (plugin + dashboards).
REM Elevates so the plugin DLL can be copied into the SimHub program folder.
set "PS=%~dp0Install-ProDash.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%PS%'"
