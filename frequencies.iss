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

[Setup]
AppId={{6F1E3C52-9B7A-4D1E-A0C4-5F2B8E7D3A91}
AppName=Frequencies
AppVersion=1.5
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
Filename: "{app}\Frequencies.exe"; Description: "{cm:LaunchProgram,Frequencies}"; Flags: nowait postinstall skipifsilent
