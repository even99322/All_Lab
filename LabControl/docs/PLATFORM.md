# 量子工程平台（Quantum Engineering Platform）

實驗室的程式整合成一個平台。量測、讀檔、論文三個模塊各自獨立、可以單獨更新，但它們共用：

- 同一個登入：論文庫（paperlib）的帳號；
- 同一個中繼站：NAS 上的 Hub；
- 同一批標籤：每個標籤都能連到論文庫裡同標籤的論文；
- 同一個數據目錄：量好的檔誰都找得到、開得了。

## 0. 0.0.26 起：量測、讀檔是兩個獨立程式，不用啟動器

| 誰 | 用什麼 | 網址 |
|---|---|---|
| 所有人 | 下載頁：**量測**（Lab Control）、**讀檔**（LabLogViewer）各自的 Windows / Mac 安裝檔 | `http://<NAS>:8765/get`（沒登入打開 `/` 也會到這裡） |
| 所有人 | 平台網頁：數據、標籤、專案、論文 | `http://<NAS>:8765/portal` |
| 所有人 | 論文庫，跟原本一樣 | `http://<NAS>:8080/` |
| 站長 | 控制台：把 .exe / .dmg 拖進來 →「全部發佈」 | 平台網頁 →「控制台」 |

```
 瀏覽器 ── /get 下載頁（不用登入）── LabControl_v*_windows.exe / LabControl_v*_macos-*.dmg
                                   LabLogViewer_v*_windows-setup.exe / LabLogViewer_v*_macos-*.dmg
 量測（Lab Control）── 自己登入平台；收到 Hub 推播（/get/watch）→ 程式內更新：功能更新包或整個程式；量測電腦開機自動上線
 讀檔（LabLogViewer）── 收到 Hub 推播 → 鈴鐺＋寵物通知 →「立即更新」安靜安裝；量測程式的「用讀檔程式開啟」直接開它
 平台網頁 ──「在讀檔程式開啟 / 套用設定 / 開啟專案」→ 送到你電腦上開著的量測程式處理
```

- **下載**：只在第一次從網頁下載。Windows 的瀏覽器可能說「不常下載，開啟前請確定您信任」——
  這是沒有向微軟購買程式碼簽章、下載人數少的程式都會有的提醒（下載頁有教怎麼「保留」）。
  之後的更新由程式自己下載（不經過瀏覽器），不會再出現。
- **量測（Lab Control）打包版**：
  - `packaging/build_windows.bat`（雙擊）→ `dist/LabControl_v<版本>_windows.exe`（單一執行檔，放哪裡都可以執行）；
  - `packaging/build_macos.command`（雙擊）→ `dist/LabControl_v<版本>_macos-<晶片>.dmg`；
  - 打包完會自動跑 `--self-test`（套件、預設檔、驅動、模擬量測存 .nc 再讀回、視窗）。
- **程式內更新**：見下方 0a（0.0.27 起有推播與功能更新包）。量測中不更新。量測電腦的量測節點收到「請更新」也是這樣更新。
- **量測電腦**：Lab Control「設定 ▾ → 量測電腦：開機自動開啟並上線」（不需要系統管理員；會順便移除舊啟動器的自動開啟）。
  Hub token 在「設定 ▾ → Hub 連線設定…」。
- **讀檔（LabLogViewer）**：用它自己的 GitHub Actions 打包；`installer.iss` / `build.yml` 的輸出檔名帶版本
  （`LabLogViewer_v2.1.1_windows-setup.exe`），控制台就認得。檔名沒有版本時，控制台會讀 Windows 安裝檔的版本資源，
  再不行就請站長輸入版本號。
- **API**：`GET /get/info` → `{apps: [{id, name, version, installer_version, downloads: [...], code: {...}}]}`；
  `GET /get/<labcontrol|lablogviewer>/<版本>/<檔名>` 不用登入（安裝檔與功能更新包 zip）。

### 0a. 0.0.27 起：更新推播、版本檢查、功能更新包

**推播**：兩個程式都對 Hub 開一條長輪詢 `GET /get/watch?app=<id>&version=<目前版本>&wait=60`。
站長在控制台發佈新版的那一刻 Hub 就回應，幾秒內：

- Lab Control：右下角跳出通知（立即更新 / 更新內容… / 稍後），狀態列出現「⬆ 更新到 x.y.z」，工作列圖示閃一下；
- LabLogViewer（2.1.2 起）：鈴鐺與寵物通知，更新內容視窗有「立即更新」。

連不到 Hub 時每 10 秒、20 秒…（最多 10 分鐘）再試；Lab Control 另外每 30 分鐘問一次 `/get/info` 當備援。

**版本檢查**：

- Lab Control「設定 ▾ → 版本檢查…」：目前版本、程式本體（.exe 版本與執行環境指紋）、Hub、最新版本、更新方式、更新內容、
  「有新版時通知我（推播）」開關、重新檢查、立即更新；
- LabLogViewer「設定 → 關於 → 檢查更新」。

**Lab Control 的兩種更新**（程式自己選）：

| 更新方式 | 什麼時候 | 下載 | 怎麼換 |
|---|---|---|---|
| 功能更新包 | Hub 上最新的 `LabControl_v<版本>.zip` 的 `requirements.txt` 跟這個 .exe 打包時一樣 | 只有程式碼，約 1 MB | 解壓到 `~/.qel/labcontrol/code/<版本>/`，重開時從那裡載入（.exe 不動） |
| 整個程式 | requirements 不同（加了套件、換了版本） | .exe / .dmg | Windows 舊檔改名 `*.old.exe`、新檔放回原位；Mac 換掉 `.app` |

- 功能更新包下載後檢查 sha256、版本號、requirements 指紋；不合就不裝。
- **安全網**：新版開啟後 1.5 秒回報「正常」；沒有回報（開到一半當掉）→ 下次開啟自動退回上一個可用版本，這版記為不用。
  一開始就載入失敗（例如需要 .exe 沒有的套件）→ 當場改用 .exe 內附的版本。環境變數 `LABCONTROL_NO_OVERLAY=1` 只用內附版本。
- 新版需要新的執行環境但站長還沒上傳安裝檔 → 顯示「等站長上傳安裝檔」，不會裝不相容的程式碼。

**站長發佈流程**：

1. 只改程式碼（大部分的更新）：`python tools/make_release.py` 產生 `LabControl_v<版本>.zip` → 控制台「全部發佈」。
   大家的 Lab Control 幾秒內收到推播，按「立即更新」幾秒就好。不用重新打包 .exe。
2. 改了 `requirements.txt`：另外用 `packaging/build_windows.bat` / `build_macos.command` 打包 .exe / .dmg，一起發佈。
3. LabLogViewer：GitHub Actions 打包的 `LabLogViewer_v<版本>_windows-setup.exe` / `.dmg` 拖進控制台發佈。
   程式下載安裝檔（檢查大小與 sha256）→ Windows 安靜安裝（`/SILENT`，保留安裝位置與圖示）後自動重開；Mac 換掉 `.app` 後重開。

**API（不用登入）**：

| 方法 | 路徑 | 說明 |
|---|---|---|
| `GET` | `/get/info` | `apps[]`：`version`（最新）、`installer_version`、`downloads[]`（安裝檔，含 `sha256`）、`code`（功能更新包：`name, version, size, sha256, url, requirements, python, notes`） |
| `GET` | `/get/watch?app=&version=&wait=` | 長輪詢（`wait` 最多 120 秒）→ `{changed, app}` |
| `GET` | `/get/feed/lablogviewer.json` | LabLogViewer 更新 feed 格式（`version, date, items, downloads`） |

### 0b. 0.0.28 起：從 NAS 一鍵安裝（Windows）

```
新電腦 ── 瀏覽器開 http://<NAS>:8765/get/setup ── 下載 QELSetup_v1.0.0_windows.exe（約 3 MB）
          執行 → 勾「量測」「讀檔」→ 它讀 /get/setup.txt → 從 NAS 下載最新的
          LabControl_v*_windows-setup.exe、LabLogViewer_v*_windows-setup.exe（檢查 sha256）→ /SILENT 安裝
          → %LOCALAPPDATA%\Programs\Lab Control、%LOCALAPPDATA%\Programs\LabLogViewer
之後 ── 兩個程式都從 NAS 收推播、程式內更新（安裝程式同樣 /SILENT，裝在同一個位置）
```

- NAS 就是安裝檔的倉庫：站長在控制台發佈新的安裝程式，一鍵安裝下次就裝新版，它自己幾乎不用更新。
- 一鍵安裝先試實驗室網路 `192.168.50.2`，再試 VPN `100.114.33.20`；命令列 `/HUB=http://主機:埠` 可以指定。
- Lab Control 與 LabLogViewer 的 Windows 安裝程式都是 Inno Setup、裝在使用者資料夾（不需要系統管理員）。
- 打包：All_Lab（GitHub Actions）的 Build Lab Control / Build LabLogViewer；E2E Windows 用測試 Hub 實際跑一次一鍵安裝。
- **Mac（0.0.29）**：終端機貼 `curl -fsSL http://<NAS>:8765/get/mac | bash`（`/get/setup.txt?platform=macos&arch=…` 選這台晶片的 .dmg，
  檢查 sha256，裝到「應用程式」）。用 curl 下載不會被標成「從網路下載」，不會「已損毀，無法打開」。
  沒有向 Apple 付費簽章（Developer ID）的程式用瀏覽器下載會被擋；已經下載的：`xattr -dr com.apple.quarantine "/Applications/Lab Control.app"`。
- **讀檔寵物圖檔（0.0.30）**：`LabLogViewerPets_v<版本>.zip`（模塊 `llvpets`，約 210 MB）不在讀檔程式的安裝檔裡。
  `/get/setup.txt?pets=1` 多一行 `pets|版本|檔名|大小|sha256|網址`：一鍵安裝 1.1.0 的可選元件「pets」（預設不勾）解壓到
  `%LOCALAPPDATA%\LabLogViewer\pets`；Mac 腳本詢問（或 `QEL_PETS=1/0`）後解壓到 `~/Library/Application Support/LabLogViewer/pets`；
  讀檔程式「設定 → 寵物」也能下載。舊的一鍵安裝（1.0.0）不帶 `pets=1`，看不到這行。

### 0c. 0.0.30：存檔位置

- 量測電腦預設存到 `~/Desktop/數據集散地`（`C:\Users\user\Desktop\數據集散地`），資料夾規則 `{project}/{yyyy}/{mm}/Data_{mmdd}`。
- `{project}` = 檔案設置的「專案資料夾」：空白 = 論文庫專案名稱（沒有就建立），也可以選已有的資料夾（每個專案記在 `LAB/project_folders.json`）。
- NAS 上的對應位置 `\\192.168.50.2\ccuqel\數據集散地`（VPN `\\100.114.33.20\ccuqel\數據集散地`）：讀檔程式「檔案 → 開啟實驗室數據集散地」。

### 0d. 0.0.31：論文庫服務帳號、專案範本、專題歸檔、儀器對應、讀檔連動

- **論文庫服務帳號**：Hub 用一個服務帳號查論文庫的專案（量測電腦用裝置 token 時也看得到專案）。
  - 設定：控制台「論文庫服務帳號」（存在 `data/paperlib_service.json`，只在 NAS 上），或 docker-compose 的
    `LABHUB_PAPERLIB_SERVICE_USER` / `LABHUB_PAPERLIB_SERVICE_PASSWORD`（環境變數優先）。
  - 帳號密碼不放在程式、安裝檔、Git 裡。
- **專案範本**：每個論文庫專案 × 每個範本（單張、2D、2D電流異步、雙層分檔、雙層單檔、flux）一份方案，存在 Hub
  `data/project_templates/<專案>/<範本>.json`，所有電腦共用。
  - Lab Control 範本選單：有選專案 → 載入這個專案的版本；方案改了停手 1.5 秒自動寫回；換專案先存回舊的再載入新的。
  - 本機快取 `LAB/project_templates/<專案>/<範本>.json`；Hub 連不到時標成待送，下次再送。
- **專題歸檔**：Lab Control「📦 專題歸檔」把數據（單一檔或整個資料夾）複製到 `\\192.168.50.2\ccuqel\數據集散地\<專案>\`
  （VPN `\\100.114.33.20\...`；Mac `/Volumes/ccuqel/數據集散地`；`LABCONTROL_ARCHIVE_ROOT` 可指定），保留日期資料夾。
- **儀器依量測電腦對應**：`instruments.yaml` 每台儀器寫 `node: LAB-1` / `LAB-2`；方案裡目前儀器清單沒有的電磁鐵組 / VNA，
  依「同一台的第幾個」換成執行那台的（LAB-1 magA、magB、VNA1 ↔ LAB-2 magC、magD、VNA2）。
- **量測電腦對應（0.0.32）**：流程圖 / 儀器伺服器工具列「🔁 量測電腦對應」。每台的電磁鐵組 / VNA 順序存在
  `settings.yaml` 的 `lab_nodes`（預設 LAB-1：magnet_A、magnet_B、VNA1；LAB-2：magnet_C、magnet_D、VNA2），
  儀器接在哪台寫在 `instruments.yaml` 的 `node:`；舊檔依位址自動補上一次。
- **遠端量測的存檔位置**：節點的狀態帶 `data`（root、資料夾規則、專案資料夾清單）；遠端執行時一律存到量測電腦自己的位置。
- **讀檔程式連動**：讀檔程式（2.1.6）開著時聽本機連線 `qel-lablogviewer-<使用者>`，並把啟動方式記在
  `~/.qel/lablogviewer/app.json`。Lab Control 量完送 `{"cmd": "open", "path": …}`（沒開就啟動它）；雙層分檔不送。
  讀檔程式拖出的數據帶檔案網址，拖到 Lab Control 套用該檔的量測設定。
- **上傳完整性**：控制台上傳帶 `?size=`；Hub 收到的長度不對就回 400，控制台顯示 sha256。
- **讀檔程式讀 NAS**：網路磁碟上的檔案先複製到本機快取（`%LOCALAPPDATA%\LabLogViewer\netcache`）再讀；副本與原檔相同時才使用。

### 0e. 1.0.0 正式版

- 量測（Lab Control）、讀檔（LabLogViewer）、Hub、Monitor 都是 1.0.0。讀檔程式的版號重新開始（之前 0.x – 2.1.6）：
  Hub 1.0.0 第一次啟動把讀檔程式比 1.0.0 大的舊版本搬到 `data/modules_legacy/lablogviewer/`（只做一次，標記
  `data/modules/.legacy_archived_lablogviewer`）；讀檔 feed 帶 `"line": 1`，讀檔 1.0.0 起不理沒有 line 的舊 feed。
  已裝 2.x 的電腦手動安裝一次 1.0.0。
- 控制台上傳用 XMLHttpRequest 顯示進度（進度條、速度、剩餘時間、取消）。

### 0f. 1.0.2：分檔命名、背景與去背

- 分檔依外圈的「第幾步」：值有變 → `data_100350mA`（`data.label_style`：nodot / plain / p），值不變（重複掃描）→ `data_p1`、`data_p2`。
  命名規則 `data.split_pattern`（預設 `{stem}_{label}`），每個 Data 方塊可以自己設（點方塊 → 右側「Data 方塊」）。
- 迴圈之後的「設定 → 量測 → Data」是背景階段：主量測完整才執行，存 `<檔名>_bg.nc`；加「去背」方塊 → 主數據每個檔 ÷ 背景（複數相除），
  另存 `<原檔名>_debg.nc` 到指定資料夾（空白 = 同一個資料夾）。範本「2D」就是這種方案（`template_rev: 2`）。

以下第 1–7 節是 0.0.13–0.0.25（有啟動器時）的說明，平台 API、數據目錄、標籤與論文仍然適用。

## 1. 組成

```
                         ┌──────────────── NAS ────────────────┐
  手機 / 平板（瀏覽器）     │  Hub :8765（labhub，只用標準函式庫）    │   論文庫 paperlib :8080
  Windows / macOS 瀏覽器 ──▶│   /portal   平台網頁（PWA）             │◀──（伺服器對伺服器）──▶ 帳號、站長、標籤、論文
                         │   /nodes    量測節點監控                │
  量子工程啟動器 ──────────▶│   /api/platform/…  登入、權限、標籤、     │
   ├ 量測模塊 Lab Control ─▶│        論文、數據目錄、訊息、模塊發佈      │
   ├ 讀檔模塊 LabLogViewer ─▶│   /api/nodes/…  量測節點（原本的功能）    │
   └ 平台監控 Monitor ─────▶│  Agent :8766（更新 / 重建 Hub）          │
                         └─────────────────────────────────────┘
```

| 模塊 | 是什麼 | 平台裡的代號 | 怎麼更新 |
|---|---|---|---|
| 論文模塊 | paperlib（網站） | `papers` | 照原本的方式（paperlib 自己的更新） |
| 量測模塊 | Lab Control（桌面程式） | `labcontrol` | 站長發佈新版 → 啟動器「更新」 |
| 數據讀取模擬模塊 | LabLogViewer（桌面程式） | `lablogviewer` | 同上 |
| 平台監控 | Lab Control Monitor（站長用） | `monitor` | 同上 |
| 數據目錄、量測節點 | Hub 的網頁 | `catalog`、`nodes` | 隨 Hub 更新 |
| 通信模塊 | `qelcomm`（Python 套件，只用標準函式庫） | — | 每個模塊各帶一份（`tools/sync_qelcomm.py`） |
| 量子工程啟動器 | 登入、安裝 / 更新 / 開啟各模塊（桌面程式） | — | 本身也是一般的 Lab APP 程式 |

模塊之間不直接互叫，所有指令、資料、檔案都經過 Hub（「通信模塊」= `qelcomm` 用戶端 ＋ Hub 的平台 API）。

## 2. 登入與權限

- **帳號就是論文庫的帳號**。Hub 在伺服器端把帳密轉給 paperlib 驗證：帶上 `X-PL` 標頭與使用者的來源 IP，支援兩步驟驗證。
  驗證通過後，Hub 發給使用者一個 30 天的 token（`u_…`）。paperlib 的 session 只存在 Hub，不會送到前端。
- Hub 每 10 分鐘向論文庫確認一次帳號。帳號在論文庫被停用時，Hub 這邊也會自動登出。
- 登入失敗的限制：同一個來源 IP 在 10 分鐘內失敗 5 次，Hub 會先擋下。這樣可以避免論文庫把 Hub 的 IP 鎖住，影響所有人。
- **站長 = 論文庫的站長**（`owner`），不另外設角色。站長可以：
  - 每位使用者、每個模塊各自開關（例如關掉某人的量測模塊）；
  - 停用 / 啟用帳號，會寫回論文庫並立刻登出；
  - 發佈各模塊的新版；
  - 看操作紀錄。
- 其他人預設可以使用所有模塊，只有「平台監控」限站長。
- 量測電腦（節點）原本的**裝置 token**（`LABHUB_TOKEN`，例如 `<token>`）照常可用，用於節點上線、Monitor 的 Hub 更新等。
  它不是站長，不能改權限。

| 權限 | 誰可以 |
|---|---|
| 量測 API（節點、儀器、即時資料） | 裝置 token；有「量測模塊」或「量測節點」權限的使用者 |
| 標籤、論文、數據目錄、模塊訊息 | 任何登入（裝置 token 或使用者） |
| 下載某模塊的發佈檔 | 有該模塊權限的使用者（或裝置 token） |
| 使用者、權限、發佈、操作紀錄 | 站長 |
| Hub 更新 / 重新啟動 | 裝置 token 或站長 |

## 3. 共用標籤與論文

- 平台的標籤 = 論文庫的標籤，加上 Hub 記的分類與「只用在數據上的標籤」。
  分類沿用 LabLogViewer 的 Project、Level、Board Design、Data Analysis、Other。
- 建立標籤：站長在論文庫有 `manage` 權限，標籤會同時建在論文庫；其他人建立的標籤只存在平台，數據可以先用，之後站長可以再加進論文庫。
- 每個標籤都有：論文數、數據數、跳到論文庫的連結（`/#/t/<標籤>`），每篇論文也有自己的連結（`/#/p/<id>`）。
  以下地方都可以點標籤看到論文群：
  - Lab Control「檔案設置 → 🏷 標籤與論文」；
  - 啟動器「數據與論文」；
  - 平台網頁的「標籤與論文」與每筆數據。
- Lab Control 的「標籤」欄就是 Labber 檔的 tags，也是上傳到數據目錄時登記的標籤，同一份。

## 3a. 論文庫專案（0.0.16）

論文庫（1.6 以上）的「專案」是最上層的分組：專案裡有論文（在論文庫管理）、數據標籤、量測數據（在平台管理）。

| 在哪裡 | 能做什麼 |
|---|---|
| Lab Control「檔案設置 → 論文庫專案」 | 挑選專案、「＋ 新專案…」直接在論文庫建立；量完的數據歸到這個專案；資料夾規則可以用 `{project}` |
| Lab Control「🏷 標籤與論文」 | 最上面是專案的數據標籤；勾選或新建的標籤記到專案 |
| Lab Control 選單「量子工程平台」 | 用讀檔模塊開啟目前專案的所有數據、在論文庫開啟專案 |
| 啟動器「數據與論文」 | 選專案看數據；「📂 用讀檔模塊開啟整個專案」 |
| 平台網頁「專案」 | 每個專案的數據標籤、論文、數據；新增專案、加標籤；數據可以移到別的專案 |

- 量測方案記在 `output.lab_project = {id, name}`。Labber 檔的 Project 欄位不變（仍是 `MM/MMDD`）。
- 上傳時帶的標籤會自動加到專案的數據標籤。
- **專案資料夾**：Hub 在 `LabControlHub/data/projects/<專案名稱>/<量測電腦>/<原本的相對路徑>` 為每個專案維護一份目錄，
  檔案是 `data/files/` 的硬連結（不多佔空間；不能硬連結時改用複製）。
  - 數據換專案、專案改名時自動搬移；
  - 只刪除 Hub 自己建立的檔案，使用者自己放進去的檔案不會動。
- **讀檔模塊開整個專案**：讀檔模塊的程式不改，啟動器用「資料夾」參數開它（LabLogViewer 會把資料夾當成資料庫開啟）。
  - 先找這台電腦看得到的 NAS 專案資料夾：`LABHUB_PROJECTS_SHARE`（逗號分隔）；沒設定時依論文庫的內網位址推算，
    例如 `\\192.168.50.2\docker\LabControlHub\data\projects`、`/Volumes/docker/LabControlHub/data/projects`；
  - 找不到（例如在外面用 VPN）→ 同步到 `~/QELData/projects/<專案>`，只下載新的或大小不同的檔案。
- 需要論文庫 1.6 以上；舊版論文庫會回「需要 paperlib 1.6 以上」，其他功能照常。

## 4. 數據目錄與模塊之間傳數據

**存檔格式（0.0.20 起）**：量測模塊存成 xarray / NetCDF（`.nc`），不再輸出 Labber。

- 讀法：`xr.open_dataset(path, engine="h5netcdf")`。
- 格式細節見 Lab Control README「讀 .nc 檔」。
- 讀檔模塊 LabLogViewer 2.1.0 起直接開 `.nc`（也照樣開舊的 Labber 檔）：開資料庫、開整個專案時，`.nc` 和 `.hdf5` 一起列出。

數據的來源：

- **量測節點**的檔，量測結束後原本就會上傳到 Hub。現在會連同標籤、量測者、方案名稱一起登記。
- **本機量測**（不是節點）存檔後，在背景上傳到平台並登記（`platform.upload_local_runs`）。
- Hub 第一次啟用平台功能時，會把 `data/files/` 裡舊的上傳檔也登記進目錄。

數據在模塊之間怎麼走：

| 從 | 到 | 怎麼做 |
|---|---|---|
| Lab Control「已存的數據」分頁 | LabLogViewer | 拖過去，或按「在讀檔模塊開啟」 |
| LabLogViewer 資料清單 | Lab Control | 拖過去：套用這個檔的量測設定（Lab Control 檔 → 整個方案；一般 Labber 檔 → VNA 設定與掃描範圍，可以復原） |
| 平台網頁 / 啟動器的數據 | 兩個模塊 | 拖過去，或按「在讀檔模塊開啟」「套用設定到量測模塊」（Hub 上的檔會自動下載到 `~/.qel/cache`） |
| 手機網頁 | 實驗室電腦 | 按鈕送出 → 你登入中的電腦上，啟動器幫你開啟（沒裝就先安裝） |

拖放格式在 `qelcomm/dnd.py`，三種同時帶上：

- 本機檔案的 `text/uri-list`：任何程式都能接；
- `application/x-qel-data`：JSON 格式，內容是路徑、目錄 id、Hub；
- 網頁另外帶 `application/x-qel-catalog`。

Lab Control 0.0.13 匯出的 Labber 檔，根目錄多了 `attrs["labcontrol"]`，存的是完整的量測方案。Labber 會忽略這個屬性。

**訊息匯流排**：`POST/GET /api/platform/bus/<模塊>`。每位使用者、每個模塊各有一個收件匣，用 long-poll 收訊息，只有本人收得到。

- 指令一律先送到啟動器（`launcher`），由它轉交：
  - 模塊已由啟動器開著 → 轉給該模塊；
  - 模塊沒開 → 帶 `QEL_OPEN` 環境變數開啟它；
  - 讀檔模塊例外：一律用命令列參數開一個新視窗（檔案先下載到 `~/.qel/cache`；專案用資料夾）。
- 訊息帶 `host` 時，只有那台電腦的啟動器會處理。

## 5. 啟動器與模塊發佈

- 站長在平台網頁「站長管理」或 Monitor「平台（站長）」上傳某模塊某版本的 zip，版本號一律純數字。
- 啟動器依各模塊的最新版顯示「安裝 / 更新 / 開啟」，每個模塊各自更新。
- 安裝位置是 `~/.qel/modules/<模塊>/<版本>/`。
- 模塊資料夾沿用 Lab APP 的慣例：
  - 標記檔：`.entry`、`.python-version`、`.icon`、`.readme`；
  - 套件清單：`requirements.lock` 優先，沒有才用 `requirements.txt`。
- Python 模塊會建立自己的虛擬環境，`requirements` 變了才重新安裝；打包好的 `.exe` / `.app` 直接執行。
- 只保留最近兩版，可以退回上一版。開發中的模塊可以「用本機資料夾」。
- 解壓時拒絕不安全的路徑（`..`、絕對路徑、符號連結）。
- 啟動器的 Windows / Mac 版（0.0.17）：
  - 站長在 Windows、Mac 各雙擊一次打包工具：`packaging/build_windows.bat`、`build_macos.command`；
  - 把 `QELLauncher_v<版本>_windows.exe`、`QELLauncher_v<版本>_macos-<晶片>.dmg` 丟進發佈中心；
  - 平台網頁的啟動器卡片依作業系統顯示下載按鈕；
  - Hub 的模塊資料有 `downloads`，每個平台 / 晶片各列出最新的安裝檔。
- 開模塊時帶上環境變數：
  - `QEL_GATEWAY`、`QEL_TOKEN`：模塊直接沿用啟動器的登入，不用再登入一次；
  - `QEL_MODULE`；
  - `QEL_OPEN`：要開的檔。

## 5a. 發佈中心：更新只要上傳 zip（0.0.14）

站長只需要做一件事：把新版 zip 拖進 **Monitor「發佈中心」** → 按「▶ 全部發佈」。

| zip | 誰更新 | 怎麼更新 |
|---|---|---|
| Hub（`LabControlHub_v*.zip`） | NAS 控制代理 | 停止 → 備份 → 換程式 → 重建 → 啟動 → 確認，失敗自動換回 |
| 論文庫（paperlib 資料夾的 zip） | NAS 控制代理 | 備份程式 → 換程式 → 建置映像 → 重建容器 → 啟動 → 確認，失敗自動換回。`data/`、`import/`、`docker-compose.yml` 不動 |
| 啟動器的 Windows .exe、macOS .dmg | 平台 | 加到同一版的檔案裡；平台網頁出現下載按鈕，打包版啟動器提示更新 |
| 量測模塊、讀檔模塊、Monitor、啟動器 | 平台 → 各電腦的啟動器 | 啟動器每 5 分鐘檢查，自動安裝新版；執行中的模塊等它關閉後再更新。量測節點也從 Hub 自動更新 |

- 判斷方式：zip 裡的 `qel-module.json`（`{"id", "version", "name", "notes"}`，發佈工具會自動加上），沒有的話看內容。
  新的模塊只要附上 `qel-module.json`，就能用同樣的方式發佈。
- 順序：論文庫 → 各模塊 → Hub（Hub 會重新啟動，放最後）。版本比目前舊時會先問。
- 論文庫可以丟完整包或**部分更新包**（只有改過的 `app/` 檔案）。部分更新包需要的基底檔案缺少時會事前拒絕。
  更新時先建置新映像（舊版照常服務）→ 停止 → 備份資料庫 → 啟動 → 確認；失敗時程式與資料庫都換回。

## 5b. 一個網址、一個 App、一個控制台（0.0.18–0.0.25；0.0.26 起見第 0 節）

| 誰 | 用什麼 | 網址 |
|---|---|---|
| 所有人 | 下載 App「量子工程平台」（Windows .exe / Mac .dmg）；之後 App 自己更新 | `http://<NAS>:8765/`（沒登入會到下載頁 `/get`） |
| 所有人 | 論文庫，跟原本一樣 | `http://<NAS>:8080/` |
| 站長 | 控制台：發佈更新、系統狀態、量測電腦 | 平台網頁 →「控制台」 |
| 量測電腦 | App「⚙ 設定 → 這台是量測電腦」，之後開機自動上線 | — |

`<NAS>`：實驗室裡是 `192.168.50.2`，VPN 是 `100.114.33.20`。

**控制台的發佈流程**

1. 把檔案拖進「發佈更新」：Hub、論文庫（完整包或部分更新包）、量測、讀檔、Monitor、App 的 zip，以及 App 的 .exe、.dmg。
   Hub 判斷每個檔是哪個程式、哪一版，並列出線上目前的版本。
2. 按「全部發佈」，順序是：論文庫 → 各模塊 → 安裝檔 → Hub。
   - 模塊直接加到平台，各電腦的 App 自動更新；
   - Hub、論文庫交給控制代理（`LABHUB_AGENT_URL`），網頁顯示每一步。Hub 重新啟動時，網頁會等它回來。
3. 線上已經是同一版的會略過；比線上舊的會先問。
4. 大的模塊可以只傳**更新包**：zip 裡只有改過的檔案，加上 `qel-module.json`
   （`{"id": "<模塊>-patch", "target": "<模塊>", "version": "新版", "base": "基底版"}`）。
   Hub 拿平台上基底版的完整 zip 合併成新版再發佈；基底版不在平台上時會提示先發佈它。

**量測電腦**

- 名稱用英文、數字、-，例如 `LAB-A`，會用在資料夾名稱與網址。
- App 向 Hub 取得裝置 token（只有站長能取得），存在 `~/.qel/launcher.json`，只有自己能讀。
- App 用 `--node` 開量測模塊，並帶上 `LAB_CONTROL_HUB`、`LAB_CONTROL_HUB_TOKEN`、`LAB_CONTROL_NODE_NAME`。
- 量測模塊關掉或當掉會自動重開。
- 有新版時，App 先問 Hub 節點是否閒置，閒置才關掉、更新、重新上線。

**客戶端連量測電腦**

- App「量測」分頁按「在這台量測」→ 量測模塊以 `--target <名稱>` 開啟，「執行於」直接選那台。
- 用的是自己的登入，需要站長開放「量測模塊」。

## 6. 各平台

| | Windows | macOS | Android | iPhone / iPad |
|---|---|---|---|---|
| 平台網頁（模塊、數據、標籤與論文、站長管理） | ✔ | ✔ | ✔ | ✔ |
| 加到主畫面（PWA） | ✔ | ✔ | ✔（需要 HTTPS） | ✔（需要 HTTPS） |
| 量子工程啟動器 | ✔ | ✔ | — | — |
| 量測模塊、讀檔模塊、平台監控 | ✔ | ✔ | — | — |

手機用平台網頁：

- 可以查數據、看論文、改標籤；
- 站長可以管理權限；
- 可以叫實驗室電腦開檔（電腦上要開著啟動器）。

要「加到主畫面」並離線顯示上次的入口頁，需要 HTTPS，例如 Tailscale `tailscale serve`，或 Cloudflare Tunnel 指到 `:8765`。用 HTTP 一樣能正常使用。

## 7. 安裝 / 升級步驟

**NAS：一個專案、三個容器**（`deploy/platform/`，發佈檔 `QELPlatform_NAS_v*.zip`）

| 容器 | 是什麼 | 埠 |
|---|---|---|
| `paperlib` | 論文庫 | 8080 |
| `labcontrol-hub` | Hub | 8765 |
| `labcontrol-hub-agent` | 控制代理 | 8766 |

- 三個容器在同一個 Docker 專案 `qelplatform`，Hub 用 `http://paperlib:8080` 連論文庫。
- 資料留在原本的 `docker/paperlib/data`、`docker/LabControlHub/data`。
- 從原本兩個專案改成一個的步驟見 `deploy/platform/README.md`（備份 → 放 Hub 新程式 → 清除舊的兩個專案 → 用新的 compose 建立專案）。

**改好之後**

1. 打開 `http://192.168.50.2:8765/`，用論文庫帳號登入。
2. 在 Monitor「發佈中心」一次拖進所有 zip →「▶ 全部發佈」：
   - `LabControl_v*.zip`、LabLogViewer 原本的 zip（例如 `LabLogViewer2_0.zip`）、`LabControlMonitor_v*.zip`、`QELLauncher_v*.zip`；
   - 論文庫的完整包或部分更新包；
   - 之後所有更新（含 Hub 與論文庫網站）都是同樣的方式。
3. 每台電腦第一次裝 `QELLauncher_v*.zip`（Lab APP 或平台網頁下載），之後啟動器會自己更新。

## 8. API 摘要

完整說明在 `labhub/platform_routes.py`，以下是摘要。

**登入**

| 方法 | 路徑 | 說明 |
|---|---|---|
| `POST` | `/api/platform/login` | `{username, password, code?}` → `{token, user, modules, paperlib_url}` |
| `POST` | `/api/platform/logout` | |
| `GET` | `/api/platform/me` | |
| `GET` | `/api/platform/status` | |

**模塊**

| 方法 | 路徑 | 說明 |
|---|---|---|
| `GET` | `/api/platform/modules` | |
| `PUT` | `/api/platform/modules/<id>` | 站長 |
| `PUT` | `/api/platform/modules/<id>/<版本>/<檔名>` | 站長上傳；`GET` 同一路徑 = 下載 |

**站長**

| 方法 | 路徑 | 說明 |
|---|---|---|
| `GET` | `/api/platform/users` | |
| `PATCH` | `/api/platform/users/<uid>` | `{enabled}` |
| `PUT` | `/api/platform/perms` | `{username, module, allowed}` |
| `GET` | `/api/platform/audit` | |
| `GET` | `/api/platform/sessions` | |

**標籤與論文**

| 方法 | 路徑 | 說明 |
|---|---|---|
| `GET` / `POST` | `/api/platform/tags` | |
| `PATCH` | `/api/platform/tags/<名稱>` | |
| `GET` | `/api/platform/papers?tags=a,b` | |

**數據目錄**

| 方法 | 路徑 | 說明 |
|---|---|---|
| `GET` | `/api/platform/catalog` | 參數 `tags`、`mode`、`q`、`node`、`user`、`project`、`limit`、`offset` |
| `GET` | `/api/platform/catalog/<id>` | |
| `GET` | `/api/platform/catalog/<id>/papers` | |
| `PUT` | `/api/platform/catalog/<id>/tags` | |
| `PUT` | `/api/platform/catalog/<id>/project` | `{project}`（空字串 = 不屬於專案） |
| `POST` | `/api/platform/catalog/register` | |
| `PUT` | `/api/files/<節點>/<路徑>` | 上傳。查詢參數：`tags`、`run`、`scheme`、`meta`、`project` |

**專案（0.0.16）**

| 方法 | 路徑 | 說明 |
|---|---|---|
| `GET` | `/api/platform/projects` | 論文庫的專案 ＋ 數據數、數據標籤、資料夾；`?refresh=1` 重新讀論文庫 |
| `POST` | `/api/platform/projects` | `{name, short_name, description, color}`，建在論文庫（需要論文庫的權限） |
| `GET` | `/api/platform/projects/<id>` | 加上論文（`papers`）與標籤資訊（`tag_info`） |
| `PUT` | `/api/platform/projects/<id>/tags` | `{tags, replace?}` |
| `GET` | `/api/platform/projects/<id>/files` | 專案的數據檔（`rel` = `<節點>/<路徑>`）與資料夾名稱 |
| `GET` | `/api/platform/project-templates/<id>` | 這個專案的範本 `{templates: {<範本>: {scheme, time, by}}}`（0.0.31） |
| `PUT` | `/api/platform/project-templates/<id>/<範本>` | `{scheme, by}` 寫入 / 取代（0.0.31） |

**控制台（站長）、下載頁**

| 方法 | 路徑 | 說明 |
|---|---|---|
| `POST` | `/api/platform/console/stage?filename=` | 上傳檔案（body 是檔案內容）→ 判斷模塊、版本、線上版本 |
| `GET` | `/api/platform/console/items` | 上傳過、還沒清掉的檔 |
| `POST` | `/api/platform/console/publish/<id>` | 發佈；Hub / 論文庫回傳控制代理的工作 |
| `GET` | `/api/platform/console/jobs/<id>` | 工作進度（轉問控制代理） |
| `GET` | `/api/platform/console/status` | Hub、控制代理、論文庫、NAS 空間、量測電腦 |
| `POST` | `/api/platform/console/measure-pc` | `{name}` → 裝置 token（設定量測電腦） |
| `GET` / `PUT` | `/api/platform/console/paperlib-service` | 論文庫服務帳號：讀取（不回密碼）/ `{user, password}` 設定（驗證後才存，0.0.31） |
| `GET` | `/get`、`/get/info`、`/get/<版本>/<檔名>` | 下載頁（不用登入；只開放 App 本身） |

**訊息**

| 方法 | 路徑 | 說明 |
|---|---|---|
| `POST` | `/api/platform/bus/<模塊>` | `{type, …}` |
| `GET` | `/api/platform/bus/<模塊>` | 參數 `after`、`wait` |

## 9. 之後可以做

- 原生手機 App（目前用 PWA）。
- 數據目錄的縮圖預覽，例如用 LabLogViewer 的 Quick Preview 在 Hub 產生 PNG。
- 數據的標籤寫回 LabLogViewer 每個檔的標籤：目前同步的是標籤清單，不是每個檔各自的標籤。
- 時域量測（LabLogViewer `interfaces/time_domain`）也接到量測模塊。
