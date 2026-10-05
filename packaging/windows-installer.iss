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
CloseApplications=force
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

[Code]
{ When an older app version starts this installer, the installer inherits that app's
  PyInstaller settings, and the app it restarts would then fail with "Failed to load
  Python DLL". This tells the restarted app to start fresh. }
function SetEnvironmentVariable(lpName: String; lpValue: String): BOOL;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

function InitializeSetup(): Boolean;
begin
  SetEnvironmentVariable('PYINSTALLER_RESET_ENVIRONMENT', '1');
  Result := True;
end;

{ The in-app updater starts this installer and then quits the app. Wait for it to be gone
  (and close it if it doesn't quit), so its files can be replaced instead of rolling back. }
function AppIsRunning(): Boolean;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/C tasklist /FI "IMAGENAME eq AITokenTracker.exe" /NH | find /I "AITokenTracker.exe" >NUL',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := (Code = 0);
end;

procedure CloseRunningApp();
var
  I, Code: Integer;
begin
  I := 0;
  while AppIsRunning() and (I < 40) do
  begin
    Sleep(500);
    I := I + 1;
  end;
  if AppIsRunning() then
  begin
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM AITokenTracker.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Sleep(1500);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseRunningApp();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  CloseRunningApp();
  Result := True;
end;
