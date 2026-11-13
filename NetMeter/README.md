# QEL 流量監看（QELNetMeter）

裝在每台電腦：連到實驗室網段（預設 `192.168.50.0/24`）時，記錄這台電腦那張網卡的上傳 / 下載量，
每分鐘送到 Hub（NAS）；在 Hub 網頁「網路用量」看全部裝置。

- **Windows**：下載 `QELNetMeter_v<版本>_windows-setup.exe` 執行（要管理員，裝在 `Program Files\QELNetMeter`）。
  裝好就在背景記錄：系統排程「QEL\NetMeter」開機就以 SYSTEM 啟動（不用登入、沒有視窗），每 10 分鐘檢查一次，
  被關掉 / 當掉會再開。紀錄在 `%ProgramData%\QELNetMeter`。開始功能表「QEL 流量監看」開狀態視窗。
  之後 Hub 發佈新版會自己安靜更新。SmartScreen 提示時按「其他資訊 → 仍要執行」。
  解除安裝：Windows「設定 → 應用程式」。
- **macOS**：打開 `QELNetMeter_v<版本>_macos-<晶片>.dmg`，把 App 拖到「應用程式」，第一次用右鍵「打開」。
  會在 `~/Library/LaunchAgents` 登記登入時自動執行（被關掉 / 當掉會再開）。
- 只算連在實驗室網段的那張網卡；離開實驗室網段（在家、手機熱點）不算。剛連上 / 剛離開的那一段、重開機那一段不算。
- 每天的總量存在這台電腦（`state.json`，留 62 天），送到 Hub 時一次送最近 8 天：Hub 停機或網路斷掉，之後會補上。
- 設定（狀態視窗「設定…」或 `config.json`）：Hub 位址（預設 `http://192.168.50.2:8765`）、實驗室網段。
- 停止：狀態視窗「停止並移除」（或 `QELNetMeter.exe --uninstall`）。

開發：`python -m netmeter`（狀態視窗）、`python -m netmeter --agent`（背景程式）、`python -m pytest tests`。
打包：`pyinstaller packaging/netmeter.spec`，Windows 再 `iscc /DAppVersion=<版本> packaging/installer.iss`。

## 版本

### v1.0.4

- **分內網 / 外網 / VPN**：每條連線的資料（Windows：ETW「Kernel-Network」，要系統排程的背景程式；macOS：nettop）
  依比例把實驗室網卡的流量分成 內網（實驗室網段內）、外網（經路由器出去）、VPN（Tailscale 加密封包；另記其中經外網的），
  回報給 Hub（1.0.38 起顯示）；狀態視窗的今天 / 本月也會寫。
- **內網用 https 網址開 Hub**：Hub 回報時給它 https 網域（`*.ts.net`）；Windows 在實驗室網段時把這個網域寫進 hosts
  指到 NAS（只加自己的一段 `# QEL NetMeter BEGIN … END`），離開實驗室網段、結束、解除安裝時拿掉——
  沒開 VPN 也能用同一個 https 網址。設定 `lan_hosts: false` 關掉；`split: false` 不分內外網。

### v1.0.3

Windows 改成**開機就在背景執行**：安裝程式（要管理員一次）建立系統排程「QEL\NetMeter」——開機 30 秒後以 SYSTEM 啟動，
不用有人登入；每 10 分鐘檢查一次，背景程式被關掉 / 當掉會再開；不受電池模式限制。裝到 `Program Files`，
紀錄搬到 `%ProgramData%\QELNetMeter`（1.0.2 以前的紀錄自動搬過來，同一台電腦 id 不變；舊的使用者版自動解除安裝、
拿掉「登入時啟動」）。macOS 的登入自動執行加上「被關掉會再開」。

### v1.0.2

另外記「經 Tailscale（VPN）」的流量（Tailscale 網卡，100.x 位址；在實驗室網段時才記），一起回報給 Hub。
Hub 1.0.37 的「網路用量」把每台電腦的流量分成：跟 NAS 之間（區網）、其中經 VPN、其餘（多半是外網）。

### v1.0.1

Windows 改成正式安裝程式（`QELNetMeter_v1.0.1_windows-setup.exe`，資料夾版＋版本資訊）：1.0.0 的單一 exe
會被 Windows Defender / 瀏覽器誤判成病毒而無法下載。登入自動啟動改由安裝程式登記；自動更新改成安靜執行新的安裝程式。

### v1.0.0

第一版：Windows / macOS；只在實驗室網段計算、每天總量、送到 Hub、狀態視窗、開機自動啟動、Windows 自動更新。
