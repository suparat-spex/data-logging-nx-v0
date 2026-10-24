; Inno Setup 6 script for NX Logger  ->  dist\NX-Logger-Setup-<version>.exe
; Built by packaging\build.ps1 (passes /DAppVersion=...). Needs dist\NX Logger\ from PyInstaller.
; Install: Program Files\NX Logger. User data (Documents\NX Logger, %APPDATA%\NX Logger) is
; never touched by uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "NX Logger"
#define AppExe "NX Logger.exe"

[Setup]
; AppId identifies the product for upgrades - never change it
AppId={{6F1C2D7A-3B54-4E8F-9A21-5C7D0B9E4A13}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=SPEX
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=NX-Logger-Setup-{#AppVersion}
SetupIconFile=..\src\nxlogger\resources\icons\app\nxlogger.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
