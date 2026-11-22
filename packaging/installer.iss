; Inno-Setup-Skript für einen Windows-Installer (Setup.exe).
; Voraussetzung: zuerst "pyinstaller packaging\erp_desk.spec" ausführen.
; Dann mit Inno Setup 6 kompilieren:  iscc packaging\installer.iss

#define AppName "ERP Desk"
#define AppVersion "1.5.0"

[Setup]
AppId={{6B1C9C2E-4F3A-4E0B-9D8C-3E5A7F0D2B41}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=ERP Desk
DefaultDirName={autopf}\ERPDesk
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=ERPDesk-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknüpfung erstellen"; GroupDescription: "Zusätzliche Symbole:"

[Files]
Source: "..\dist\ERPDesk\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\ERPDesk.exe"
Name: "{group}\Datenbankverbindung einrichten"; Filename: "{app}\ERPDesk.exe"; Parameters: "--setup"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\ERPDesk.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ERPDesk.exe"; Description: "{#AppName} starten"; Flags: nowait postinstall skipifsilent
