# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Spezifikation für SailLog (Windows-Build).

Baut einen eigenständigen Ordner `dist/SailLog/` mit `SailLog.exe` — Python
und tkinter sind mitgepackt, der Endnutzer braucht kein Python zu installieren.

Bauen:  pyinstaller --clean --noconfirm saillog.spec
Ergebnis: dist/SailLog/SailLog.exe   (danach optional der Inno-Setup-Installer)

Die App selbst ist abhängigkeitsfrei (nur Standardbibliothek). Pillow und
pyserial sind optional — sind sie in der Build-Umgebung installiert, werden sie
mitgepackt (Foto-Verkleinerung bzw. serieller Anschluss), sonst degradiert die
App sauber.
"""

import glob
import os

ICON = "assets/icon.ico" if os.path.exists("assets/icon.ico") else None

# Mitgelieferte Sprachkataloge (lang/*.json) ins Bundle nach `saillog/lang`
# packen — sonst fehlt in der EXE die Englisch-Übersetzung (Logo/Icon sind
# dagegen als base64 im Code eingebettet und brauchen keine Datendateien).
LANG_DATAS = [(f, "saillog/lang") for f in glob.glob("src/saillog/lang/*.json")]

a = Analysis(
    ["main.py"],
    pathex=["src"],                 # damit `import saillog...` gefunden wird
    binaries=[],
    datas=LANG_DATAS,
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
    name="SailLog",
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
    name="SailLog",
)
