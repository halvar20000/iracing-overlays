# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the STANDALONE race logger.

Builds one self-contained RaceLogger.exe that records an iRacing race to a
.jsonl and (optionally) uploads it to the CLS league site. No Python, no OBS
overlays, no pip on the driver's PC.

    pip install pyinstaller
    pyinstaller RaceLogger.spec        ->  dist/RaceLogger.exe

Everything the logger needs is iracing_race_logger.py + iracing_sdk_base.py;
the rest of the overlay suite is deliberately NOT bundled.
"""

a = Analysis(
    ['iracing_race_logger.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['irsdk', 'yaml'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PIL', 'matplotlib', 'numpy', 'pandas'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RaceLogger',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
