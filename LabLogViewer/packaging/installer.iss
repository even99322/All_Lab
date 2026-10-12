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
; LabLogViewer_v<version>_windows-setup.exe: the Quantum Engineering Platform console reads the version from the name
OutputBaseFilename=LabLogViewer_v{#AppVersion}_windows-setup
VersionInfoProductTextVersion={#AppVersion}
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
Source: "..\icons\app\*.ico"; DestDir: "{app}\shortcut_icons"; Flags: ignoreversion

[Icons]
Name: "{group}\LabLogViewer"; Filename: "{app}\LabLogViewer.exe"; IconFilename: "{app}\shortcut_icons\app_icon_{code:IconNumber}.ico"
Name: "{group}\{cm:UninstallProgram,LabLogViewer}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LabLogViewer"; Filename: "{app}\LabLogViewer.exe"; Tasks: desktopicon; IconFilename: "{app}\shortcut_icons\app_icon_{code:IconNumber}.ico"

[Run]
Filename: "{app}\LabLogViewer.exe"; Description: "{cm:LaunchProgram,LabLogViewer}"; Flags: nowait postinstall skipifsilent

[Code]
var
  IconPage: TInputOptionWizardPage;

procedure InitializeWizard;
begin
  IconPage := CreateInputOptionPage(wpSelectTasks, 'Program icon',
    'Which icon should LabLogViewer use?',
    'The icon is used for the shortcuts. You can change it later in Settings > Appearance > App icon.',
    True, False);
  IconPage.Add('Icon 1');
  IconPage.Add('Icon 2');
  IconPage.Add('Icon 3');
  IconPage.Add('Icon 4');
  IconPage.Add('Icon 5 (default)');
  IconPage.Add('Icon 6');
  IconPage.Add('Icon 7');
  IconPage.SelectedValueIndex := 4;
end;

function IconNumber(Param: String): String;
var
  Saved: AnsiString;
begin
  { v2.1.2: an in-app update installs silently; keep the icon chosen before }
  if WizardSilent and LoadStringFromFile(ExpandConstant('{app}\app_icon.txt'), Saved) and (Trim(String(Saved)) <> '') then
    Result := Trim(String(Saved))
  else
    Result := IntToStr(IconPage.SelectedValueIndex + 1);
end;

{ v2.1.5: the pet pictures are no longer part of the program (optional download to
  %LOCALAPPDATA%\LabLogViewer\pets). Updating from an older version: move its pictures there instead of
  leaving 220 MB behind, so the pet keeps working without a download. }
procedure MoveOldPetPictures;
var
  Old, Pets: String;
begin
  Old := ExpandConstant('{app}\_internal\app\pet\assets');
  Pets := ExpandConstant('{localappdata}\LabLogViewer\pets');
  if not DirExists(Old) then
    exit;
  if not DirExists(Pets) then
  begin
    ForceDirectories(ExtractFileDir(Pets));
    if RenameFile(Old, Pets) then
    begin
      Log('Moved the old pet pictures to ' + Pets);
      exit;
    end;
  end;
  DelTree(Old, True, True, True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    MoveOldPetPictures;
  if CurStep = ssPostInstall then
    SaveStringToFile(ExpandConstant('{app}\app_icon.txt'), IconNumber(''), False);
end;
