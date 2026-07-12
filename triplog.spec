# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Spezifikation für TripLog (Windows-Build).

Baut einen eigenständigen Ordner `dist/TripLog/` mit `TripLog.exe` — Python
und tkinter sind mitgepackt, der Endnutzer braucht kein Python zu installieren.

Bauen:  pyinstaller --clean --noconfirm triplog.spec
Ergebnis: dist/TripLog/TripLog.exe   (danach optional der Inno-Setup-Installer)

Die App selbst ist abhängigkeitsfrei (nur Standardbibliothek). Pillow und
pyserial sind optional — sind sie in der Build-Umgebung installiert, werden sie
mitgepackt (Foto-Verkleinerung bzw. serieller Anschluss), sonst degradiert die
App sauber.
"""

import os

ICON = "assets/icon.ico" if os.path.exists("assets/icon.ico") else None

a = Analysis(
    ["main.py"],
    pathex=["src"],                 # damit `import triplog...` gefunden wird
    binaries=[],
    datas=[],                        # Logo/Icon sind im Code eingebettet (base64)
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "numpy", "pandas"],   # nicht benötigt, hält es schlank
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TripLog",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                   # GUI-App: kein Konsolenfenster
    disable_windowed_traceback=False,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TripLog",
)
