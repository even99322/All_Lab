; Step 4 (Windows): installer with Inno Setup.
;   iscc /DAppVersion=0.19E packaging\installer.iss
; Installs for the current user (no administrator password needed on lab computers).
#ifndef AppVersion
  #define AppVersion "0.0"
#endif

[Setup]
AppId={{6F1C8E2A-5B7D-4C1E-9A3F-2D8B4E6C0A91}
AppName=LabLogViewer
AppVersion={#AppVersion}
AppPublisher=CCU QEL
DefaultDirName={localappdata}\Programs\LabLogViewer
DefaultGroupName=LabLogViewer
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=LabLogViewer-Windows-setup
SetupIconFile=..\build\LabLogViewer.ico
UninstallDisplayIcon={app}\LabLogViewer.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\LabLogViewer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\LabLogViewer"; Filename: "{app}\LabLogViewer.exe"
Name: "{group}\{cm:UninstallProgram,LabLogViewer}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LabLogViewer"; Filename: "{app}\LabLogViewer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LabLogViewer.exe"; Description: "{cm:LaunchProgram,LabLogViewer}"; Flags: nowait postinstall skipifsilent
