; QEL 流量監看 Windows 安裝程式（1.0.1 起；1.0.0 的單一 exe 會被 Windows Defender 誤判成病毒）。
;   pyinstaller --noconfirm packaging\netmeter.spec
;   iscc /DAppVersion=1.0.1 packaging\installer.iss   →  dist\QELNetMeter_v1.0.1_windows-setup.exe
; 1.0.3 起：要管理員，裝在 Program Files；建立系統排程「QEL\NetMeter」——開機就以 SYSTEM 在背景執行（不用登入）、
; 每 10 分鐘檢查一次、當掉 1 分鐘後重新啟動。紀錄在 %ProgramData%\QELNetMeter（一般使用者可寫，狀態視窗也讀同一份）。
; 1.0.2 以前裝在使用者底下的舊版：安裝時自動解除安裝（紀錄由背景程式搬過來，同一台電腦 id 不變）。
; 自動更新：背景程式（SYSTEM）下載新的安裝程式後用 /VERYSILENT 執行；安裝前先停掉舊的背景程式，裝完再啟動。
#ifndef AppVersion
  #define AppVersion "0.0"
#endif

[Setup]
AppId={{4E7C2A91-6B3D-4F58-9A1E-C2D8B5F03E77}
AppName=QEL 流量監看
AppVersion={#AppVersion}
AppVerName=QEL 流量監看 {#AppVersion}
AppPublisher=CCU QEL
DefaultDirName={autopf}\QELNetMeter
DefaultGroupName=QEL 流量監看
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=admin
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

[Dirs]
Name: "{commonappdata}\QELNetMeter"; Permissions: users-modify

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\QELNetMeter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{commonprograms}\QEL 流量監看"; Filename: "{app}\QELNetMeter.exe"

[Run]
; 建立系統排程（開機自動、當掉重來）並立刻啟動背景程式（安靜安裝 = 自動更新時也一樣）
Filename: "{app}\QELNetMeter.exe"; Parameters: "--install-service"; Flags: runhidden waituntilterminated; StatusMsg: "設定開機自動啟動…"
Filename: "{app}\QELNetMeter.exe"; Description: "開啟狀態視窗"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
; 1.0.4：先叫背景程式自己結束（會拿掉 hosts 裡 Hub 網址那一段），再刪系統排程
Filename: "{app}\QELNetMeter.exe"; Parameters: "--quit"; Flags: runhidden waituntilterminated; RunOnceId: "QuitAgent"
Filename: "{app}\QELNetMeter.exe"; Parameters: "--uninstall-service"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveTask"

[Code]
{ 安裝前叫舊的背景程式結束（存好今天的用量），還在就強制結束 }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Old: String;
begin
  { 背景程式先存檔結束（不用 schtasks /End：自動更新時這個安裝程式就是背景程式開的，/End 會連它一起關掉） }
  if FileExists(ExpandConstant('{app}\QELNetMeter.exe')) then
    Exec(ExpandConstant('{app}\QELNetMeter.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
  { 1.0.2 以前裝在使用者底下的舊版：安靜解除安裝（也會拿掉「登入時啟動」） }
  Old := ExpandConstant('{localappdata}\Programs\QELNetMeter\unins000.exe');
  if FileExists(Old) then
    Exec(Old, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM QELNetMeter.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(500);
  Result := '';
end;
