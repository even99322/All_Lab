; HFSS 自動化工具（模擬工具）Windows 安裝程式（v3.6 起）。
;   pyinstaller --noconfirm packaging\hfsstool.spec
;   iscc /DAppVersion=3.6 packaging\installer.iss   →  dist\HFSSTool_v3.6_windows-setup.exe
; 裝在目前使用者底下（實驗室電腦不用管理員密碼），開始功能表＋桌面捷徑，「新增或移除程式」可以解除安裝。
; 程式內更新：下載新的安裝程式後直接用 /SILENT /RELAUNCH 執行（只顯示進度條），裝完自動重開。
#ifndef AppVersion
  #define AppVersion "0.0"
#endif

[Setup]
AppId={{9B2D4F7A-3C61-4E8B-A5D2-7F1E0C9B4A63}
AppName=HFSS 自動化工具
AppVersion={#AppVersion}
AppVerName=HFSS 自動化工具 {#AppVersion}
AppPublisher=CCU QEL
DefaultDirName={localappdata}\Programs\HFSSTool
DefaultGroupName=HFSS 自動化工具
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
; 平台控制台從檔名讀版本：HFSSTool_v<版本>_windows-setup.exe
OutputBaseFilename=HFSSTool_v{#AppVersion}_windows-setup
VersionInfoVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\HFSSTool.exe
UninstallDisplayName=HFSS 自動化工具
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
; 舊版的程式檔（換版時 _internal 裡少掉的套件不留著）
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\HFSSTool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\HFSS 自動化工具"; Filename: "{app}\HFSSTool.exe"
Name: "{group}\{cm:UninstallProgram,HFSS 自動化工具}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\HFSS 自動化工具"; Filename: "{app}\HFSSTool.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\HFSSTool.exe"; Description: "{cm:LaunchProgram,HFSS 自動化工具}"; Flags: nowait postinstall skipifsilent
; 程式內更新（v3.6.4）：/SILENT /RELAUNCH /FROM=<舊版> → 裝好自己重開
Filename: "{app}\HFSSTool.exe"; Parameters: "--updated-from {param:FROM|?}"; Flags: nowait runasoriginaluser; Check: ShouldRelaunch

[Code]
function ShouldRelaunch: Boolean;
begin
  Result := WizardSilent and (Pos('/RELAUNCH', UpperCase(GetCmdTail)) > 0);
end;

{ v3.7.1：安裝前結束所有還在跑的 HFSSTool.exe（包含沒有視窗、還連著 AEDT 的工作程序——它們占著 _internal 的檔案，
  Windows 的「關閉應用程式」關不掉，安裝就會失敗）。不用 /T：AEDT 本身不會被關掉。 }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM HFSSTool.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(800);
  Result := '';
end;

{ 從 3.5 以前的單一 exe 版更新時，舊版的更新程式會把這個安裝程式換到舊 exe 的位置（例如 桌面\HFSSTool_v3.5_windows.exe）再執行。
  裝好之後把那個檔刪掉，免得之後點它又跑一次安裝；新的捷徑在開始功能表 / 桌面。 }
procedure CurStepChanged(CurStep: TSetupStep);
var
  Src, Cmd: String;
  Code: Integer;
begin
  if CurStep <> ssDone then
    exit;
  Src := ExpandConstant('{srcexe}');
  if Pos('setup', Lowercase(ExtractFileName(Src))) > 0 then
    exit;
  Cmd := '/C ping -n 6 127.0.0.1 >nul & del /f /q "' + Src + '" & del /f /q "' + Src + '.old"';
  Exec(ExpandConstant('{cmd}'), Cmd, '', SW_HIDE, ewNoWait, Code);
end;
