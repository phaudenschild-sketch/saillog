; Inno Setup Skript für SailLog — erzeugt einen Windows-Installer.
;
; Voraussetzung: der PyInstaller-Build wurde schon erstellt
;   pyinstaller --clean --noconfirm saillog.spec   ->  dist\SailLog\
;
; Installer bauen: Inno Setup (https://jrsoftware.org/isdl.php) installieren,
; dann diese Datei mit dem "Inno Setup Compiler" öffnen und auf "Compile"
; klicken — oder auf der Kommandozeile:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\saillog.iss
;
; Ergebnis: installer\Output\SailLog-Setup-0.1.0.exe

#define AppName "SailLog"
#define AppVersion "0.1.0"
#define AppPublisher "Peter Haudenschild"
#define AppExeName "SailLog.exe"

[Setup]
AppId={{9E7B4C2A-6D3F-4A1B-9C2E-71D0C0FE0001}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Segel-Logbuch, kein Admin nötig -> Installation ins Benutzerprofil möglich
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=SailLog-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE

[Languages]
Name: "deutsch"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Der gesamte PyInstaller-Ordner (onedir-Build)
Source: "..\dist\SailLog\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
