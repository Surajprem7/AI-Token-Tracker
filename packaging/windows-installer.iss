; Inno Setup script: builds ClaudeTokenTracker-Setup.exe from the PyInstaller output.
; Installs for the current user only (no admin rights), adds a Start menu entry
; and a normal uninstaller (Settings > Apps).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6B0C2E53-4B1A-4E0B-9C0D-7A1C3F2E9A41}
AppName=Claude Token Tracker
AppVersion={#AppVersion}
AppPublisher=AI Token Tracker
DefaultDirName={localappdata}\Programs\Claude Token Tracker
DefaultGroupName=Claude Token Tracker
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=ClaudeTokenTracker-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Claude Token Tracker

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\ClaudeTokenTracker.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\claude-tokens.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Claude Token Tracker"; Filename: "{app}\ClaudeTokenTracker.exe"
Name: "{group}\Uninstall Claude Token Tracker"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Claude Token Tracker"; Filename: "{app}\ClaudeTokenTracker.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ClaudeTokenTracker.exe"; Description: "Open Claude Token Tracker now"; Flags: nowait postinstall skipifsilent
