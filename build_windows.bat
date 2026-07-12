@echo off
REM ============================================================
REM  TripLog — Windows-Build (EXE + optional Installer)
REM  Doppelklick oder in der Eingabeaufforderung ausfuehren.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo === TripLog Windows-Build ===
echo.

REM 1) Python vorhanden?
python --version >nul 2>&1
if errorlevel 1 (
  echo [Fehler] Python wurde nicht gefunden. Bitte Python 3.9+ installieren
  echo          und beim Setup "Add Python to PATH" anhaken.
  pause
  exit /b 1
)

REM 2) PyInstaller sicherstellen
echo [1/3] PyInstaller pruefen/installieren...
python -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
  echo [Fehler] PyInstaller konnte nicht installiert werden.
  pause
  exit /b 1
)

REM (optional) Pillow + pyserial mitpacken, wenn gewuenscht:
REM   python -m pip install pillow pyserial

REM 3) Build
echo [2/3] Baue TripLog.exe ...
python -m PyInstaller --clean --noconfirm triplog.spec
if errorlevel 1 (
  echo [Fehler] Der Build ist fehlgeschlagen.
  pause
  exit /b 1
)

echo.
echo [OK] Fertig: dist\TripLog\TripLog.exe
echo.

REM 4) Optional: Installer bauen, falls Inno Setup vorhanden
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
  echo [3/3] Baue Installer mit Inno Setup ...
  "%ISCC%" installer\triplog.iss
  echo.
  echo [OK] Installer: installer\Output\TripLog-Setup-0.1.0.exe
) else (
  echo [Hinweis] Inno Setup nicht gefunden ^(optional^).
  echo           Fuer einen richtigen Installer: https://jrsoftware.org/isdl.php
  echo           installieren und dieses Skript erneut ausfuehren.
  echo           Zum Weitergeben genuegt sonst der Ordner dist\TripLog\ als ZIP.
)

echo.
pause
