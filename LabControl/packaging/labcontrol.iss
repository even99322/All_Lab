; Lab Control（量測）Windows 安裝程式（Inno Setup 6；0.0.28 起，跟 LabLogViewer 一樣的安裝方式）
;   iscc /DAppVersion=0.0.28 packaging\labcontrol.iss      （packaging/build_app.py 會自己呼叫）
; 裝在目前使用者底下（不需要系統管理員）：%LOCALAPPDATA%\Programs\Lab Control
; 程式內更新會用 /SILENT 重新執行新版安裝程式（同一個 AppId → 同一個位置），設定（LAB 資料夾）不受影響。
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{C6BDC124-748E-4A9A-8009-E22A5DDE7D67}
AppName=Lab Control
AppVersion={#AppVersion}
AppVerName=Lab Control {#AppVersion}
AppPublisher=CCU QEL
DefaultDirName={localappdata}\Programs\Lab Control
DefaultGroupName=Lab Control
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
; LabControl_v<版本>_windows-setup.exe：平台網頁的控制台從檔名認出版本
OutputBaseFilename=LabControl_v{#AppVersion}_windows-setup
VersionInfoVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoProductName=Lab Control
VersionInfoDescription=Lab Control setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\LabControl.exe
UninstallDisplayName=Lab Control
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; 舊版的程式檔（套件版本變了時，舊的 .pyd / .dll 不能留著）
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\LabControl\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Lab Control"; Filename: "{app}\LabControl.exe"
Name: "{group}\{cm:UninstallProgram,Lab Control}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Lab Control"; Filename: "{app}\LabControl.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LabControl.exe"; Description: "{cm:LaunchProgram,Lab Control}"; Flags: nowait postinstall skipifsilent
