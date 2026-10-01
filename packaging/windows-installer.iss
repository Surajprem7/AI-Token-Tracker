; Inno Setup script: builds AITokenTracker-Setup.exe from the PyInstaller output.
; Installs for the current user only (no admin rights), adds a Start menu entry
; and a normal uninstaller (Settings > Apps).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6B0C2E53-4B1A-4E0B-9C0D-7A1C3F2E9A41}
AppName=AI Token Tracker
AppVersion={#AppVersion}
AppPublisher=AI Token Tracker
DefaultDirName={localappdata}\Programs\AI Token Tracker
DefaultGroupName=AI Token Tracker
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=AITokenTracker-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=AI Token Tracker
; Close a running copy before replacing it (used by the in-app updater too).
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\AITokenTracker.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\ai-tokens.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\AI Token Tracker"; Filename: "{app}\AITokenTracker.exe"
Name: "{group}\Uninstall AI Token Tracker"; Filename: "{uninstallexe}"
Name: "{userdesktop}\AI Token Tracker"; Filename: "{app}\AITokenTracker.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AITokenTracker.exe"; Description: "Open AI Token Tracker now"; Flags: nowait postinstall skipifsilent
; After an automatic (silent) update, start the app again.
Filename: "{app}\AITokenTracker.exe"; Flags: nowait; Check: WizardSilent
