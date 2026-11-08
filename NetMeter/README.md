# QEL 流量監看（QELNetMeter）

裝在每台電腦：連到實驗室網段（預設 `192.168.50.0/24`）時，記錄這台電腦那張網卡的上傳 / 下載量，
每分鐘送到 Hub（NAS）；在 Hub 網頁「網路用量」看全部裝置。

- **Windows**：下載 `QELNetMeter_v<版本>_windows.exe`，雙擊一次 → 複製到 `%LOCALAPPDATA%\QELNetMeter`、
  登入時自動在背景執行（不需要管理員），並顯示狀態視窗。之後 Hub 發佈新版會自己更新。
  SmartScreen 提示時按「其他資訊 → 仍要執行」。
- **macOS**：打開 `QELNetMeter_v<版本>_macos-<晶片>.dmg`，把 App 拖到「應用程式」，第一次用右鍵「打開」。
  會在 `~/Library/LaunchAgents` 登記登入時自動執行。
- 只算連在實驗室網段的那張網卡；離開實驗室網段（在家、手機熱點）不算。剛連上 / 剛離開的那一段、重開機那一段不算。
- 每天的總量存在這台電腦（`state.json`，留 62 天），送到 Hub 時一次送最近 8 天：Hub 停機或網路斷掉，之後會補上。
- 設定（狀態視窗「設定…」或 `config.json`）：Hub 位址（預設 `http://192.168.50.2:8765`）、實驗室網段。
- 停止：狀態視窗「停止並移除」（或 `QELNetMeter.exe --uninstall`）。

開發：`python -m netmeter`（狀態視窗）、`python -m netmeter --agent`（背景程式）、`python -m pytest tests`。
打包：`pyinstaller packaging/netmeter.spec`。

## 版本

### v1.0.0

第一版：Windows / macOS；只在實驗室網段計算、每天總量、送到 Hub、狀態視窗、開機自動啟動、Windows 自動更新。
