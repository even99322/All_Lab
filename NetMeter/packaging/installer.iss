; QEL 流量監看 Windows 安裝程式（1.0.1 起；1.0.0 的單一 exe 會被 Windows Defender 誤判成病毒）。
;   pyinstaller --noconfirm packaging\netmeter.spec
;   iscc /DAppVersion=1.0.1 packaging\installer.iss   →  dist\QELNetMeter_v1.0.1_windows-setup.exe
; 裝在目前使用者底下（不用管理員密碼），登入時自動在背景執行，開始功能表有「QEL 流量監看」（狀態視窗）。
; 自動更新：背景程式下載新的安裝程式後用 /VERYSILENT 執行；安裝前會先叫舊的背景程式結束，裝完再啟動新版。
#ifndef AppVersion
  #define AppVersion "0.0"
#endif

[Setup]
AppId={{4E7C2A91-6B3D-4F58-9A1E-C2D8B5F03E77}
AppName=QEL 流量監看
AppVersion={#AppVersion}
AppVerName=QEL 流量監看 {#AppVersion}
AppPublisher=CCU QEL
DefaultDirName={localappdata}\Programs\QELNetMeter
DefaultGroupName=QEL 流量監看
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=QELNetMeter_v{#AppVersion}_windows-setup
VersionInfoVersion={#AppVersion}
VersionInfoCompany=CCU QEL
VersionInfoDescription=QEL 流量監看 安裝程式
VersionInfoProductName=QEL 流量監看
VersionInfoProductTextVersion={#AppVersion}
UninstallDisplayIcon={app}\QELNetMeter.exe
UninstallDisplayName=QEL 流量監看
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\QELNetMeter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\QEL 流量監看"; Filename: "{app}\QELNetMeter.exe"
Name: "{group}\{cm:UninstallProgram,QEL 流量監看}"; Filename: "{uninstallexe}"

[Registry]
; 登入時自動在背景執行（目前使用者）
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "QELNetMeter"; \
  ValueData: """{app}\QELNetMeter.exe"" --agent"; Flags: uninsdeletevalue

[Run]
; 裝好就開始背景記錄（安靜安裝 = 自動更新時也啟動）
Filename: "{app}\QELNetMeter.exe"; Parameters: "--agent"; Flags: nowait runasoriginaluser
Filename: "{app}\QELNetMeter.exe"; Description: "開啟狀態視窗"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{app}\QELNetMeter.exe"; Parameters: "--quit"; Flags: runhidden waituntilterminated; RunOnceId: "QuitAgent"

[Code]
{ 安裝前叫舊的背景程式結束（存好今天的用量），還在就強制結束 }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  if FileExists(ExpandConstant('{app}\QELNetMeter.exe')) then
    Exec(ExpandConstant('{app}\QELNetMeter.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM QELNetMeter.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(500);
  Result := '';
end;
