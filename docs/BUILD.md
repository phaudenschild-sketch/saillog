# TripLog als Windows-Programm bauen (EXE + Installer)

Ziel: eine `TripLog.exe`, die andere Segler **ohne Python** starten können —
und optional ein richtiger Installer mit Startmenü-Eintrag, Desktop-Symbol und
Deinstaller.

Der Build muss **auf Windows** laufen (PyInstaller erzeugt plattform-spezifische
Programme). Python und tkinter werden mit eingepackt; die App selbst ist
abhängigkeitsfrei.

---

## Schnellweg (empfohlen)

1. **Python 3.9+** installieren (falls nicht vorhanden), beim Setup
   **„Add Python to PATH"** anhaken: <https://www.python.org/downloads/>
2. Im Projektordner **`build_windows.bat`** doppelklicken.

Das Skript:
- installiert bei Bedarf PyInstaller,
- baut **`dist\TripLog\TripLog.exe`** (kompletter, eigenständiger Ordner),
- baut zusätzlich den **Installer**, falls *Inno Setup* installiert ist
  (siehe unten) → `installer\Output\TripLog-Setup-0.1.0.exe`.

Zum Weitergeben genügt schon der Ordner **`dist\TripLog\`** als ZIP —
Empfänger entpacken und `TripLog.exe` starten.

---

## Manuell (falls du es Schritt für Schritt willst)

```bat
cd C:\claude\masarasi
python -m pip install --upgrade pyinstaller
python -m PyInstaller --clean --noconfirm triplog.spec
```
Ergebnis: `dist\TripLog\TripLog.exe`.

### Optionale Zusatzfunktionen mit einpacken

Standardmäßig wird nur die Standardbibliothek gebündelt (die App läuft komplett).
Wer **Foto-Verkleinerung/-Vorschau** (Pillow) oder den **seriellen Anschluss**
(pyserial, z.B. Maretron am COM-Port) im EXE haben will, installiert diese
Pakete **vor** dem Build — dann packt PyInstaller sie automatisch mit:

```bat
python -m pip install pillow pyserial
python -m PyInstaller --clean --noconfirm triplog.spec
```

---

## Installer bauen (Inno Setup)

Für den klassischen „Setup.exe"-Installer (Startmenü, Desktop-Symbol,
Deinstaller):

1. **Inno Setup** installieren: <https://jrsoftware.org/isdl.php>
2. Entweder `build_windows.bat` erneut ausführen (erkennt Inno Setup
   automatisch), **oder** manuell:
   ```bat
   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\triplog.iss
   ```
Ergebnis: **`installer\Output\TripLog-Setup-0.1.0.exe`** — das ist die eine
Datei, die du weitergibst.

Das Setup nutzt `assets\icon.ico` als Programm-Symbol, zeigt die MIT-Lizenz an
und bietet optional ein Desktop-Symbol.

---

## Gut zu wissen

- **Version anheben:** in `installer\triplog.iss` (`#define AppVersion`) und
  `pyproject.toml` die Versionsnummer ändern.
- **Datenablage bleibt getrennt:** Logbuch und Einstellungen liegen weiterhin
  unter `%USERPROFILE%\.triplog\` — eine Deinstallation löscht die Daten **nicht**.
- **SmartScreen-Hinweis:** Unsignierte EXE/Installer zeigen bei Erstnutzern evtl.
  eine Windows-SmartScreen-Warnung („Mehr Infos" → „Trotzdem ausführen"). Für
  eine Weitergabe im größeren Stil lohnt sich später ein Code-Signing-Zertifikat.
- **Antivirus-Fehlalarme:** PyInstaller-EXEs werden gelegentlich fälschlich
  angemeckert. Ein Onedir-Build (wie hier) ist davon seltener betroffen als ein
  Onefile-Build.
- Build-Ergebnisse (`build/`, `dist/`) sind in `.gitignore` und gehören **nicht**
  ins Repository.
