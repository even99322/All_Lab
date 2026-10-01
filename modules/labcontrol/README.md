# Lab Control 0.0.13

CCU QEL 實驗室的儀器控制與量測程式：調整電磁鐵電流（一對 Yokogawa 電供控制一組電磁鐵）→ VNA 或 SHFQC 量測 → 存成 Labber 檔。
操作邏輯參考 Labber Measurement Editor；量測流程用可自由拖動、連線的**流程圖**設計。

> 版本規則：`X.Y.Z` 純數字，每次發佈遞增最後一位（0.0.10 → 0.0.11），不再使用字母字尾。
> 0.0.1a、0.0.1b 在 Lab APP 上分別發佈為 v0.0.1、v0.0.2；0.0.9 的修正版改稱 0.0.10；本版 0.0.13。
> 每一版的說明在 `docs/releases/`，完整歷史在 `CHANGELOG.md`。

> **QEL Lab 大程式**（0.0.13 起）：從 QEL Lab 大程式開啟時，存檔會把量測方案與標籤寫進數據檔並登錄到大程式；
> 讀檔模塊的數據拖到主視窗就套用那次的量測設置；Tags 提示共用標籤。設定在 `settings.yaml` 的 `qel:`，
> 說明見整個專案的 `docs/ARCHITECTURE.md`。單獨執行時與以前相同。

---

## 一、使用者

### 1. 開啟

在 Lab APP「程式庫」選 **Lab Control** →「啟動」（或桌面捷徑）。

第一次開啟會自動建立設定資料夾 **`C:\Users\even9\LAB`**，放入預設設定檔。之後升級版本**不會覆寫**你改過的檔案；新版新增的設定項目會自動帶預設值。

### 2. 設定資料夾（所有設定都在這裡）

```
C:\Users\even9\LAB\
  settings.yaml       App 設定：存檔位置、Labber、預設值、單位、規則、估時…
  instruments.yaml    儀器清單：位址、型號、安全限制、參數覆寫、電磁鐵組
  templates\          範本方案（編輯器「範本」選單）
  schemes\            你存的量測方案
  experiments\        YAML 實驗設定（命令列用）
  plugins\            自訂儀器 driver / 自動判斷 hook（丟 .py 即載入）
  logs\               錯誤記錄 labcontrol.log
```

編輯器右上角「設定 ▾」可以直接開啟資料夾與設定檔。改 `settings.yaml` 後按「重新載入設定」；改 `instruments.yaml` 請重新開啟程式。
想放在別的位置：設定環境變數 `LAB_CONTROL_HOME`。

**常改的設定**

| 想改的東西 | 檔案 → 位置 |
|---|---|
| 資料存到哪裡、資料夾 / 分檔命名規則 | `settings.yaml` → `data:` |
| Labber 的 Python 3.8 路徑、user、tags | `settings.yaml` → `labber:` |
| 移到起點 / 停靠速率、重試次數、錯誤時暫停或中斷 | `settings.yaml` → `run_defaults:` |
| 新方塊的初始值（例如 DC set 100 → 150 mA、步進 0.5） | `settings.yaml` → `editor.new_blocks:` |
| 單位選單、小數位數、方塊顏色 | `settings.yaml` → `editor:` |
| 哪些量測不能同時用（預設 VNA 與 SHFQC） | `settings.yaml` → `rules.exclusive_measure_kinds` |
| 開啟時預設模擬模式 | `settings.yaml` → `app.simulate` |
| 儀器位址、電流上下限、斜坡速率、單次跳動上限 | `instruments.yaml` → 各儀器 `source:` |
| 某個儀器參數的上下限 / 預設值 / 名稱 / 能否掃描 | `instruments.yaml` → 該儀器 `parameters:`（見 `docs/DRIVER_GUIDE.md` §4） |
| 電磁鐵組由哪兩台電供組成 | `instruments.yaml` → `magnet_A` / `magnet_B` 的 `sources:` |

### 3. 主視窗

```
┌──────────────────────────────┬─────────────────────────┐
│ 量測方案（流程圖）             │ 監控：即時監控 / 量測監控    │
├──────────────────────────────┼─────────────────────────┤
│ 儀器參數                      │ 檔案設置                  │
└──────────────────────────────┴─────────────────────────┘
```

**左下 儀器參數**（= Labber 的 Channels）：列出所有儀器可用的參數。

- 拖到流程圖就建立節點：電源 / 電磁鐵組 / 儀器參數 → Step 節點（放下後會開 Step 設定視窗），量測通道（如 S21）→ 量測節點。
- 雙擊或「加到流程」：接在選取的節點後面（選取的是掃描節點且迴圈內還空著 → 放進迴圈）。
- 「取值」讀儀器目前的值；「設定值…」立刻寫入儀器（電源超過單次跳動上限會自動斜坡）；「連線儀器」唯讀連線，不改變輸出。
- 預設只顯示**已連線**儀器的常用參數；取消「只顯示已連線」「只顯示常用」可看到全部（例如編輯方案時）。

**左上 量測方案（流程圖）**：

- 節點可自由拖動。從節點的圓點拉線到另一個節點就連起來；拉到空白處會跳出選單直接新增並連上。
- 掃描節點有兩個出口：**每一點 ⟲**（接迴圈內容，例如內圈掃描或量測）與 **完成後**（迴圈結束後做的事）。
- 迴圈範圍自動畫框，框越外面 = 迴圈越外層（等同手繪流程圖的括號）。
- **Data 節點**決定分檔：接在某個迴圈的「每一點」裡 → 那個迴圈每個值存一個檔；不放 Data → 整個量測一個檔。
- 雙擊節點開設定：Step 節點 = Labber 的 Step setup（單一值 / 範圍、起訖或中心寬度、步進或點數、線性 / 對數、
  每步等待、來回掃、掃完後回到第一點 / 停在最後 / 到指定值），右側預覽所有 step 值並檢查安全上下限。
- 右鍵：編輯、切換掃描 / 固定值、複製、中斷連線、刪除。Delete 刪除選取；Ctrl + 滾輪縮放；中鍵拖曳平移。
- 沒接到「開始」的節點不會執行（檢查會提醒）。
- 可以放多個 Data 節點（例如外圈每個值一個檔＋另外一個完整檔）；第二個以後的 Data 雙擊設定檔名與格式。
- 掃描兩台交錯的電磁鐵組時，Step 設定可以勾「電流異步」：每一點只有一台前進「每台步進」（與舊 sweep_main 相同）。
- 「範本 ▾」：單張、2D、2D 電流異步、N 層量測等。

**右上 監控**有兩個分頁：

- **即時監控**（還沒量測時）：主視窗只顯示 VNA 的 dB 曲線與「▶ 量測 / 循環」；按「⧉ 獨立視窗」打開完整功能（本機或「執行於」的量測節點都可以）：
  - 電源群組卡片：拖曳合併 / 拆開、M1–M3 記憶（0.05 mA/s 斜坡回去）、兩台時 ± 微調、目標 + 速率、輸出開關；
  - VNA：S11 / S12 / S21 / S22、頻率 / 點數 / 功率 / IF / 平均 / 掃描時間、RF 開關；
  - 曲線：dB / Linear / Real / Imag / Phase / Unwrap / IQ，分開（九宮格）或疊圖（左右兩軸）；
    拖曲線 = 上下移動、在軸上拖曳 = 縮放該軸、雙擊 = 加 marker（可多個，下方列出讀數）。
- **量測監控**：開始 / 暫停 / 退回 / 停止、手動步進、進度與剩餘時間。
  - 左邊通道表：各掃描軸目前值與位置、量測通道最新結果（點選切換顯示的通道）；
  - 右邊：最新曲線（dB / 相位 / 實部 / 虛部）與 2D 影像（外圈每換一個值重畫）。
  - 開始量測時自動切到這個分頁。

**右下 檔案設置**：檔名、資料根目錄、資料夾規則、同名處理、格式、Labber Project / User / Tags / 註解，
下方即時顯示實際存檔路徑與分檔結果。「執行」分頁：每點量測前等待、移動 / 停靠速率、錯誤處理、自動暫停；
「檢查與估時」分頁：所有錯誤與警告（點一下跳到對應節點）、總點數、預估時間。空白欄位使用 `settings.yaml` 的預設值。

### 4. 儀器伺服器（連線、直接控制、驅動測試）

主視窗工具列「🖧 儀器伺服器」，或 `python main.py --server` 只開伺服器。對應 Labber 的 Instrument Server：

- **儀器清單**：`instruments.yaml` 的所有儀器與狀態（已連線 / 未連線 / 量測使用中 / 測試中 / 停用 / 錯誤）。
- **新增儀器**：選驅動、名稱、位址；「掃描 VISA」會用 `*IDN?` 辨識並建議驅動；電源可設安全上下限與斜坡。
  新增 / 移除 / 停用都會寫回 `instruments.yaml`（保留註解，原檔先備份到 `LAB/logs/backup/`），主視窗的儀器參數列表同步更新。
- **連線**只讀狀態、不改變輸出；**控制視窗**（雙擊儀器）可讀取全部、寫入變更、設定電源輸出（自動斜坡、可中止）、
  開關輸出、斜坡歸零後關閉、取得 VNA trace、看收發紀錄與指令耗時、手動送 SCPI。量測中的儀器只能讀。
- **驅動測試**：逐項讀取 / 寫回相同值 / 量測一次 / 小幅度輸出，每一步查錯誤佇列；報告存在 `LAB/logs/`。
  上機步驟見 `docs/INSTRUMENT_TEST.md`。

主視窗與儀器伺服器共用同一組連線（同一台儀器只開一次 VISA）。

### 5. 遠端量測（其他電腦控制量測電腦）

- 量測電腦：工具列「🛰 量測節點」（或 `settings.yaml remote.node_enabled: true`、`python main.py --node`）。
- 其他電腦：工具列「執行於」選節點 → 儀器列表換成節點的儀器 → ▶ 開始量測在節點上執行，右上即時顯示、可暫停 / 停止；
  結束後量測檔經 Hub 自動下載到自己的資料夾。（自己這台的「執行於」不會列出自己的節點。）
- 中間是 NAS 上的 **Lab Control Hub**（網站，`http://192.168.50.2:8765/`；VPN：100.114.33.20）：網頁可以看所有電腦與量測進度、
  下載量測檔。安裝見 `deploy/hub/README.md`；每台電腦在「設定 ▾ → Hub 連線設定…」填 Hub 的 token（不是 NAS 帳密）。
- 任何人用新版開啟時，會自動請較舊的節點更新（量測中等結束，新版從 Hub 下載）。詳見 `docs/REMOTE.md`。
- **儀器跨電腦管理**：儀器伺服器以群組顯示，每台量測節點一個群組，另有「未歸屬」。
  - Hub 記錄每台儀器掛在哪台電腦；
  - 兩台電腦都連得到的網路儀器要拖到某個群組才能連線（其他電腦自動中斷）；
  - 每組都有全部連線 / 全部斷線；
  - 在任何電腦都能命令量測節點連線，並直接控制它的 DC 與 VNA。
- **Lab Control Monitor**（Lab APP 程式 `LabControlMonitor`）：看所有節點與儀器、控制儀器。
  「Hub 控制台」選新版 zip →「更新網站」（每一步即時顯示），也可以完全重建、重新啟動、停止、啟動。

### 6. 淺色 / 深色模式

- 工具列右上 **🌙 / ☀** 一鍵切換（主視窗與儀器伺服器都有），所有視窗、流程圖、圖表即時換色。
- 「設定 ▾ → 外觀」可選 **淺色 / 深色 / 跟隨系統**（跟隨 Windows 的深色設定，系統切換時自動跟著變）。
- 選擇會寫回 `settings.yaml` 的 `app.theme`（`light` | `dark` | `system`），下次開啟沿用；`python main.py --theme dark` 只影響這次。

### 7. 模擬模式

沒有接儀器也能操作：`settings.yaml` 設 `app.simulate: true`，或命令列 `python main.py --sim`。工具列會顯示橘色「模擬模式」。

### 8. 命令列（進階）

```bash
python main.py [方案.scheme.yaml] [--sim] [--lab 其他 instruments.yaml]
python -m labcontrol init                    # 建立 / 補齊 LAB 資料夾
python -m labcontrol check                   # 連線所有儀器（唯讀）並顯示狀態
python -m labcontrol get DC3.level
python -m labcontrol set DC3.level 0.1586    # 超過 max_jump 自動走斜坡
python -m labcontrol run LAB\schemes\xxx.scheme.yaml
python -m labcontrol unlock VNA1             # VNA 卡住時恢復
python -m labcontrol resources               # 掃描 VISA 資源並用 *IDN? 辨識
python -m labcontrol test DC1 --write-same   # 驅動測試（報告存到 LAB/logs）
python -m labcontrol nodes [--update]        # 列出 Hub 上的量測節點與電腦（--update 請較舊的節點更新）
python -m labcontrol hub                     # 在這台電腦執行 Lab Control Hub（沒有 NAS Docker 時）
python -m labcontrol node                    # 不開視窗，以量測節點執行
python -m labcontrol.apps.web.server         # 網頁電源面板（port 在 settings.yaml web:）
```

---

## 二、開發者

### 1. 專案結構（符合 Lab APP 發佈格式）

| 檔案 | 內容 |
|---|---|
| `main.py` | 主程式（`.entry`） |
| `requirements.in` / `requirements.txt` | 套件清單（開發另有 `requirements-dev.txt`） |
| `.python-version` | 3.12 |
| `.entry` / `.readme` / `.icon` | `main.py` / `README.md` / `assets/icon.ico` |
| `labcontrol/` | 程式碼（`__init__.py` 內 `__version__`、`APP_NAME`） |
| `labcontrol/defaults/` | 第一次啟動複製到 LAB 資料夾的預設檔 |
| `labhub/` | Lab Control Hub（NAS 中繼網站，只用標準函式庫；`__version__` 與 Lab Control 相同） |
| `deploy/hub/` | Hub 的 Docker 設定（`docker-compose.yml`）與安裝說明 |
| `labmonitor/`、`monitor_main.py` | Lab Control Monitor（只用 PyQt6；`tools/make_release.py` 產生 Lab APP 程式資料夾）；`labmonitor/style.py` 是共用外觀 |
| `labcontrol/apps/qt/live/` | 即時監控（電源群組卡片、VNA） |
| `tools/make_release.py` | 產生三個發佈檔（Lab Control、Monitor、Hub） |
| `docs/releases/vX.Y.Z*.md` | 每一版的版本說明 |
| `CHANGELOG.md` | 歷史修改紀錄 |

程式碼裡**不寫死**任何實驗室專屬的值：可調的值一律從 `labcontrol.settings.setting("...")` 讀（預設值在 `labcontrol/defaults/settings.yaml`），儀器相關的值一律在 driver 的參數表或 `instruments.yaml`。

### 2. 發佈新版本（每一版都要做）

1. 修改程式；新增儀器請依 `docs/DRIVER_GUIDE.md`。
2. 更新版本號：`labcontrol/__init__.py`、`labhub/__init__.py`、`labmonitor/__init__.py` 的 `__version__` 與 `pyproject.toml` 的 `version`。
3. 新增 `docs/releases/v<版本>.md`（可複製上一版的格式），並在 `CHANGELOG.md` 最上面加一段。
4. 確認沒有程式檔被 git 排除：`git status --ignored`（Ignored 清單不能出現 `labcontrol/` 底下的檔案；0.0.1a 就是因此啟動失敗）。
5. 測試：`pip install -r requirements-dev.txt`，`pytest`（Windows PowerShell 直接執行即可；Linux 無螢幕時加 `QT_QPA_PLATFORM=offscreen`）。
6. Lab APP →「發佈」：專案資料夾選本資料夾、程式名稱 `LabControl`、主程式 `main.py`、說明文件 `README.md`、圖示 `assets/icon.ico`、Python 3.12。
   - 版本號與 `labcontrol/__init__.py` 的 `__version__` 一致（純數字，可以用 Lab APP 的「+修正」遞增）。
   - 「版本說明」貼上 `docs/releases/v<版本>.md` 的「摘要」段落。
7. 已發佈的版本不能覆寫；發現問題就發佈下一個版本號。

### 3. 文件

- `docs/DRIVER_GUIDE.md` — 儀器 driver 撰寫規範（新增儀器必讀）
- `docs/INSTRUMENT_TEST.md` — 上機測試指南（用儀器伺服器驗證驅動）
- `docs/REMOTE.md` — 遠端量測（Lab Control Hub、量測節點、監控網頁、自動更新、安全）
- `deploy/hub/README.md` — 在 NAS 安裝 / 更新 Hub
- `deploy/monitor/README.md` — （選用）把 Monitor 打包成單一 exe
- `docs/ARCHITECTURE.md` — 架構、事件、如何擴充
- `docs/MIGRATION.md` — 舊程式對照、行為差異、上機前檢查
- `docs/ROADMAP.md` — 版本規劃
- `CHANGELOG.md`、`docs/releases/` — 版本紀錄
