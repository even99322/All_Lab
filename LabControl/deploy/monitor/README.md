# Lab Control Monitor（選用：打包成 exe）

> 0.0.9 起 Monitor 用 **Lab APP** 發佈與安裝（程式 `LabControlMonitor`，由 `tools/make_release.py` 產生）。只有需要單一 exe 時才用這裡的方法。

監看所有量測節點與儀器、跨電腦連線 / 控制儀器、拉取共用儀器、更新 Hub 的 Windows 程式。
程式碼在 `labmonitor/`，只需要 PyQt6，不需要 Lab Control 的量測套件。

## 產生 exe

在一台 Windows 電腦上雙擊 `deploy\monitor\build_exe.bat`（視窗會停住顯示結果；所有訊息也寫在 `deploy\monitor\build_log.txt`，失敗時傳給開發者）：

- 需要 Python 3.9 以上（找 `py -3`，其次 `python`；沒有時會提示到 python.org 安裝並勾選「Add python.exe to PATH」），會自動安裝 PyInstaller、PyQt6；
- 也可以直接執行 `py -3 deploy\monitor\build_exe.py`；
- 產出 `dist\LabControlMonitor.exe`（單一檔案，約 40–60 MB），複製到任何 Windows 電腦都能直接開；
- exe 內附同版本的 Hub（`labhub`），可以直接用 exe 更新 NAS 上的 Hub。

不想打包時，有 Python 與 PyQt6 的電腦也可以直接執行 `python monitor_main.py`；有裝 Lab Control 的電腦可以從主視窗「設定 ▾ → Lab Control Monitor…」開啟。

## 設定

- 第一次開啟會讀 `C:\Users\<你>\LAB\settings.yaml` 的 `remote.hub_urls` / `remote.token`。沒有的話，在「⚙ 連線設定」填入 Hub 網址（例如 `100.114.33.20`）與 token。
- 設定存在 `%APPDATA%\LabControlMonitor\config.json`。
- 「控制代理網址」空白時使用 Hub 同一台主機的 8766（Hub 控制台）。

token 是 Hub 的存取碼，**不是 NAS 帳號密碼**。
