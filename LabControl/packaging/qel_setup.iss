; 量子工程平台 Windows 一鍵安裝程式（0.0.28）：QELSetup_v<版本>_windows.exe
;   iscc /DSetupVersion=1.0.0 packaging\qel_setup.iss
;
; 這個小程式本身不含量測 / 讀檔程式：執行時從實驗室 NAS 的 Hub 讀清單 /get/setup.txt，
; 下載勾選的程式最新的安裝程式（檢查 sha256），再一個一個安靜安裝（/SILENT）。
; 所以它幾乎不用更新：站長在網頁控制台發佈新版，下一個人用一鍵安裝就會裝到新版。
; Hub 位址：先 http://192.168.50.2:8765（實驗室網路），再 http://100.114.33.20:8765（VPN）；
; 也可以用命令列 /HUB=http://主機:埠 指定。
#ifndef SetupVersion
  #define SetupVersion "1.0.0"
#endif

[Setup]
AppId={{2F6B8E31-6C1D-4E7A-9B54-7D1E0C3A9F12}
AppName=QEL Platform Setup
AppVersion={#SetupVersion}
AppPublisher=CCU QEL
CreateAppDir=no
Uninstallable=no
PrivilegesRequired=lowest
DisableWelcomePage=no
DisableReadyPage=no
OutputDir=..\dist
OutputBaseFilename=QELSetup_v{#SetupVersion}_windows
VersionInfoVersion={#SetupVersion}
VersionInfoProductTextVersion={#SetupVersion}
VersionInfoProductName=QEL Platform Setup
VersionInfoDescription=Installs Lab Control and LabLogViewer from the lab NAS
SetupIconFile=..\assets\icon.ico
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel1=量子工程平台 安裝程式
WelcomeLabel2=從實驗室的 NAS 下載並安裝：%n%n  • 量測（Lab Control）%n  • 讀檔（LabLogViewer）%n%n兩個程式都裝在你的使用者資料夾，不需要系統管理員。裝好之後程式會自己收到更新。%n%n請先連上實驗室網路或 VPN。
SelectComponentsLabel2=勾選要安裝的程式（已經裝過的會更新到最新版）。

[Types]
Name: "full"; Description: "量測＋讀檔"
Name: "custom"; Description: "自訂"; Flags: iscustom

[Components]
Name: "labcontrol"; Description: "量測 Lab Control（流程圖量測、即時數據、儀器伺服器）"; Types: full custom
Name: "lablogviewer"; Description: "讀檔 LabLogViewer（Labber / .nc 量測檔、擬合、圖）"; Types: full custom

[Run]
Filename: "{localappdata}\Programs\Lab Control\LabControl.exe"; Description: "開啟 Lab Control"; Flags: postinstall nowait skipifsilent unchecked; Check: Installed('labcontrol')
Filename: "{localappdata}\Programs\LabLogViewer\LabLogViewer.exe"; Description: "開啟 LabLogViewer"; Flags: postinstall nowait skipifsilent unchecked; Check: Installed('lablogviewer')

[Code]
var
  Hubs: TStringList;
  Done: TStringList;
  CurrentName: String;

function Field(S: String; Index: Integer): String;
var
  i, p: Integer;
begin
  for i := 1 to Index do
  begin
    p := Pos('|', S);
    if p = 0 then
    begin
      Result := '';
      exit;
    end;
    Delete(S, 1, p);
  end;
  p := Pos('|', S);
  if p > 0 then
    Result := Copy(S, 1, p - 1)
  else
    Result := S;
end;

function Installed(App: String): Boolean;
begin
  Result := Done.IndexOf(App) >= 0;
end;

function OnProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  if ProgressMax > 0 then
  begin
    WizardForm.ProgressGauge.Max := 1000;
    WizardForm.ProgressGauge.Position := Integer(Progress * 1000 div ProgressMax);
  end;
  WizardForm.StatusLabel.Caption := '下載 ' + CurrentName + ' … ' + IntToStr(Integer(Progress div 1048576)) + ' / ' +
    IntToStr(Integer(ProgressMax div 1048576)) + ' MB';
  Result := True;
end;

procedure InitializeWizard;
var
  Given: String;
begin
  Hubs := TStringList.Create;
  Done := TStringList.Create;
  Given := ExpandConstant('{param:HUB|}');
  if Given <> '' then
    Hubs.Add(Given);
  Hubs.Add('http://192.168.50.2:8765');
  Hubs.Add('http://100.114.33.20:8765');
end;

function ReadPlan(var Base: String; Lines: TStringList): Boolean;
var
  i, j: Integer;
  Text: TArrayOfString;
begin
  Result := False;
  for i := 0 to Hubs.Count - 1 do
  begin
    try
      DownloadTemporaryFile(Hubs[i] + '/get/setup.txt', 'qel_setup_plan.txt', '', nil);
      if LoadStringsFromFile(ExpandConstant('{tmp}\qel_setup_plan.txt'), Text) then
      begin
        Lines.Clear;
        for j := 0 to GetArrayLength(Text) - 1 do
          if (Text[j] <> '') and (Copy(Text[j], 1, 1) <> '#') then
            Lines.Add(Text[j]);
        Base := Hubs[i];
        Log('Hub: ' + Base + ', ' + IntToStr(Lines.Count) + ' programs');
        Result := True;
        exit;
      end;
    except
      Log('Hub ' + Hubs[i] + ' not reachable: ' + GetExceptionMessage);
    end;
  end;
end;

procedure Fail(Msg: String);
begin
  Log(Msg);
  RaiseException(Msg);
end;

procedure InstallAll;
var
  Base, Line, App, Name, Path: String;
  Lines: TStringList;
  i, Code: Integer;
  Wanted: Integer;
begin
  Lines := TStringList.Create;
  try
    WizardForm.StatusLabel.Caption := '連線到實驗室的 NAS …';
    if not ReadPlan(Base, Lines) then
      Fail('連不到實驗室的平台（' + Hubs.CommaText + '）。請連上實驗室網路或 VPN 後再執行一次。');
    Wanted := 0;
    for i := 0 to Lines.Count - 1 do
    begin
      Line := Lines[i];
      App := Field(Line, 0);
      if not WizardIsComponentSelected(App) then
        continue;
      Wanted := Wanted + 1;
      Name := Field(Line, 2);
      CurrentName := App + ' ' + Field(Line, 1);
      try
        DownloadTemporaryFile(Base + Field(Line, 5), Name, Field(Line, 4), @OnProgress);
      except
        Fail('下載 ' + CurrentName + ' 失敗：' + GetExceptionMessage);
      end;
      Path := ExpandConstant('{tmp}\') + Name;
      WizardForm.StatusLabel.Caption := '安裝 ' + CurrentName + ' …';
      WizardForm.ProgressGauge.Style := npbstMarquee;
      if not Exec(Path, '/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /SP-', '', SW_SHOW,
                  ewWaitUntilTerminated, Code) then
        Fail('無法執行 ' + Name + '：' + SysErrorMessage(Code));
      WizardForm.ProgressGauge.Style := npbstNormal;
      if Code <> 0 then
        Fail(CurrentName + ' 的安裝程式回傳錯誤碼 ' + IntToStr(Code) + '。');
      Done.Add(App);
      Log('Installed ' + CurrentName);
    end;
    if Wanted = 0 then
      Fail('平台上還沒有勾選的程式的 Windows 安裝程式（請站長在網頁控制台發佈）。');
  finally
    Lines.Free;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    InstallAll;
end;
