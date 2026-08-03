# SailLog als Windows-Programm bauen (EXE + Installer)

Ziel: eine `SailLog.exe`, die andere Segler **ohne Python** starten können —
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
- baut **`dist\SailLog\SailLog.exe`** (kompletter, eigenständiger Ordner),
- baut zusätzlich den **Installer**, falls *Inno Setup* installiert ist
  (siehe unten) → `installer\Output\SailLog-Setup-0.1.3.exe`.

Zum Weitergeben genügt schon der Ordner **`dist\SailLog\`** als ZIP —
Empfänger entpacken und `SailLog.exe` starten.

---

## Manuell (falls du es Schritt für Schritt willst)

```bat
cd C:\claude\saillog
python -m pip install --upgrade pyinstaller
python -m PyInstaller --clean --noconfirm saillog.spec
```
Ergebnis: `dist\SailLog\SailLog.exe`.

### Optionale Zusatzfunktionen mit einpacken

Standardmäßig wird nur die Standardbibliothek gebündelt (die App läuft komplett).
Wer **Foto-Verkleinerung/-Vorschau** (Pillow) oder den **seriellen Anschluss**
(pyserial, z.B. Maretron am COM-Port) im EXE haben will, installiert diese
Pakete **vor** dem Build — dann packt PyInstaller sie automatisch mit:

```bat
python -m pip install pillow pyserial
python -m PyInstaller --clean --noconfirm saillog.spec
```

---

## Installer bauen (Inno Setup)

Für den klassischen „Setup.exe"-Installer (Startmenü, Desktop-Symbol,
Deinstaller):

1. **Inno Setup** installieren: <https://jrsoftware.org/isdl.php>
2. Entweder `build_windows.bat` erneut ausführen (erkennt Inno Setup
   automatisch), **oder** manuell:
   ```bat
   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\saillog.iss
   ```
Ergebnis: **`installer\Output\SailLog-Setup-0.1.3.exe`** — das ist die eine
Datei, die du weitergibst.

Das Setup nutzt `assets\icon.ico` als Programm-Symbol, zeigt die MIT-Lizenz an
und bietet optional ein Desktop-Symbol.

---

## Automatisch bauen lassen (GitHub Actions — ohne eigenen Windows-Rechner)

GitHub kann die `SailLog.exe` (und den Installer) auf einem Windows-Server
selbst bauen und als **Release** zum Download bereitstellen. Die fertige
Workflow-Datei liegt als Vorlage unter **[`docs/ci/build-windows.yml`](ci/build-windows.yml)**.

**Einmalig aktivieren** (die Workflow-Datei lässt sich nur über die
GitHub-Weboberfläche anlegen — das ist Absicht von GitHub):

1. Auf GitHub oben **„Add file" → „Create new file"**.
2. Als Dateiname exakt eingeben: `.github/workflows/build-windows.yml`
   (der Ordner entsteht dabei automatisch).
3. Den Inhalt aus `docs/ci/build-windows.yml` hineinkopieren, **„Commit"**.

**Danach nutzen:**
- **Testversion bauen:** Reiter **„Actions" → „Windows-Build" → „Run workflow"**.
  Das Ergebnis (ZIP + Installer) liegt unter dem Lauf als *Artifact*.
- **Richtiges Release für Tester:** eine Version taggen, z.B.
  ```bat
  git tag v0.1.3
  git push origin v0.1.3
  ```
  Dann baut GitHub automatisch und legt ein **Release** mit ZIP + Installer als
  Download an — dieser Release-Link ist das, was du deinen Testern gibst
  (siehe [`TESTING.md`](TESTING.md)).

## Gut zu wissen

- **Version anheben:** in `installer\saillog.iss` (`#define AppVersion`) und
  `pyproject.toml` die Versionsnummer ändern.
- **Datenablage bleibt getrennt:** Logbuch und Einstellungen liegen weiterhin
  unter `%USERPROFILE%\.saillog\` — eine Deinstallation löscht die Daten **nicht**.
- **SmartScreen-Hinweis:** Unsignierte EXE/Installer zeigen bei Erstnutzern evtl.
  eine Windows-SmartScreen-Warnung („Mehr Infos" → „Trotzdem ausführen"). Für
  eine Weitergabe im größeren Stil lohnt sich später ein Code-Signing-Zertifikat.
- **Antivirus-Fehlalarme:** PyInstaller-EXEs werden gelegentlich fälschlich
  angemeckert. Ein Onedir-Build (wie hier) ist davon seltener betroffen als ein
  Onefile-Build.
- Build-Ergebnisse (`build/`, `dist/`) sind in `.gitignore` und gehören **nicht**
  ins Repository.
