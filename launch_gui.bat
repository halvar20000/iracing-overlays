@echo off
REM Launch the iRacing Overlay Launcher GUI without a console window.
cd /d "%~dp0"
start "" pythonw launch_gui.py
