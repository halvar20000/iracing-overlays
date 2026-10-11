@echo off
REM Launch the iRacing Overlay Launcher GUI without a console window.
REM pythonw from PATH; falls back to the "pyw" launcher (python.org installs
REM have it even when Python was not added to PATH).
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw launch_gui.py) || (start "" pyw -3 launch_gui.py)
