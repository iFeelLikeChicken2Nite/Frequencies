; Inno Setup script for the Windows installer (WP2).
;
; Run by build-windows.ps1 after PyInstaller, not by hand. Packs
; dist\Frequencies into dist\Frequencies-Setup.exe: Start menu entry,
; optional desktop icon, uninstaller. Installs for the current user
; only (no admin prompt), in %LOCALAPPDATA%\Programs\Frequencies.
; Settings in %APPDATA%\frequencies stay when it is uninstalled.

#ifndef SetupName
  #define SetupName "Frequencies-Setup"
#endif
#ifndef AppBuild
  #define AppBuild "0"
#endif

; Updates (build 125, task 177): the app downloads this installer and runs
; it as it quits with /VERYSILENT /SUPPRESSMSGBOXES /CLOSEAPPLICATIONS
; /NORESTARTAPPLICATIONS /UPDATE. /UPDATE waits until no Frequencies.exe
; holds the "FrequenciesRunning" mutex (the app and its browser helper
; each hold it) and never starts the app afterwards; /RELAUNCH (an
; installer that didn't finish, run again at launch) does start it.
; The AppId never changes, so every build upgrades the same install.

[Setup]
AppId={{6F1E3C52-9B7A-4D1E-A0C4-5F2B8E7D3A91}
AppName=Frequencies
AppVersion=1.5.{#AppBuild}
VersionInfoVersion=1.5.{#AppBuild}.0
AppPublisher=Frequencies
DefaultDirName={autopf}\Frequencies
DefaultGroupName=Frequencies
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename={#SetupName}
SetupIconFile=frequencies.ico
UninstallDisplayIcon={app}\Frequencies.exe
UninstallDisplayName=Frequencies
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\Frequencies\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An update replaces the whole program folder, so files an older
; build had and this one doesn't can't linger.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\Frequencies"; Filename: "{app}\Frequencies.exe"
Name: "{group}\Uninstall Frequencies"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Frequencies"; Filename: "{app}\Frequencies.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Frequencies.exe"; Description: "{cm:LaunchProgram,Frequencies}"; Flags: nowait postinstall skipifsilent; Check: NotUpdate
Filename: "{app}\Frequencies.exe"; Flags: nowait; Check: Relaunch

[Code]
function HasSwitch(Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/' + Name) = 0 then
      Result := True;
end;

function NotUpdate: Boolean;
begin
  Result := not HasSwitch('UPDATE');
end;

function Relaunch: Boolean;
begin
  Result := HasSwitch('RELAUNCH');
end;

function InitializeSetup(): Boolean;
var
  Waited: Integer;
begin
  // An update: wait (up to a minute) for every Frequencies.exe to end.
  if HasSwitch('UPDATE') then
  begin
    Waited := 0;
    while CheckForMutexes('FrequenciesRunning') and (Waited < 240) do
    begin
      Sleep(250);
      Waited := Waited + 1;
    end;
  end;
  Result := True;
end;
