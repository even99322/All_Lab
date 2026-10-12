# 修改紀錄（CHANGELOG）

版本規則：`X.Y.Z` 純數字，每次發佈遞增最後一位（0.0.10 → 0.0.11），不再使用字母字尾。（0.0.1a、0.0.1b 是早期的字母版本）
每一版的詳細說明在 `docs/releases/v<版本>.md`。最新的版本在最上面。

---

## 0.0.29 — 2026-10-12

修正 Mac「Lab Control 已損毀，無法打開」（改 Info.plist 後沒有重新簽名）；Mac 從 NAS 一行安裝（`curl -fsSL http://<NAS>:8765/get/mac | bash`）；LabLogViewer 2.1.4 不再打包用不到的 Qt（WebEngine 等）。詳見 [docs/releases/v0.0.29.md](docs/releases/v0.0.29.md)。

---

## 0.0.28 — 2026-10-11

Windows 一鍵安裝（從 NAS 下載量測＋讀檔並安裝）、Lab Control 改用安裝程式；修正檔名沒套用（存成 aaa_1）；不再存 _raw/；DC 微調可選步進；VNA 參數滾輪調位數；流程圖顯示 VNA 設定；讀檔程式「介面 → 量測」與拖曳。詳見 [docs/releases/v0.0.28.md](docs/releases/v0.0.28.md)。

---

## 0.0.27 — 2026-10-11

程式內更新推播與版本檢查：Hub 一發佈新版，Lab Control 與 LabLogViewer（2.1.2）幾秒內通知，按「立即更新」就在程式裡更新；Lab Control 只改程式碼時只下載約 1 MB 的「功能更新包」，不用換整個 .exe。詳見 [docs/releases/v0.0.27.md](docs/releases/v0.0.27.md)。

---

## 0.0.26 — 2026-10-11

不用啟動器：量測（Lab Control）、讀檔（LabLogViewer）是兩個獨立程式，全部從下載頁安裝；Lab Control 打包成單一 .exe / Mac app，程式內自己更新。詳見 [docs/releases/v0.0.26.md](docs/releases/v0.0.26.md)。

---

## 0.0.25 — 2026-10-10

量測面板改成「左：設計流程（流程圖＋儀器參數）、右：檔案設置」；量測監控在開始量測後以獨立視窗彈出，即時數據用工具列按鈕開獨立視窗。詳見 [docs/releases/v0.0.25.md](docs/releases/v0.0.25.md)。

---

## 0.0.24 — 2026-10-10

電磁鐵組：儀器伺服器「＋ 電磁鐵組…」（兩台 GS200，或 GS820 的 CH1 + CH2），配對的電源收在電磁鐵組底下，平時只看到電磁鐵組。詳見 [docs/releases/v0.0.24.md](docs/releases/v0.0.24.md)。

---

## 0.0.23 — 2026-10-10

修正 0.0.22：用工具列「🛰 量測節點」開的量測電腦（LAB-2）拖曳 / 寫了 `node:` 之後，DC1–DC4、magnet_A / magnet_B 仍列在這台。詳見 [docs/releases/v0.0.23.md](docs/releases/v0.0.23.md)。

---

## 0.0.22 — 2026-10-10

儀器歸屬哪台量測電腦（`instruments.yaml` 的 `node:`），USB 電源不再出現在另一台；VNA 自動偵測 port，可量 S31、S43…，即時監控有「偵測 port」按鈕。詳見 [docs/releases/v0.0.22.md](docs/releases/v0.0.22.md)。

---

## 0.0.21 — 2026-10-09

控制台可以發佈模塊的更新包（只有改過的檔案，Hub 合併成完整新版）；讀檔模塊 2.1.0 讀得到 .nc。詳見 [docs/releases/v0.0.21.md](docs/releases/v0.0.21.md)。

---

## 0.0.20 — 2026-10-09

量測存檔改成 xarray .nc，不再輸出 Labber。詳見 [docs/releases/v0.0.20.md](docs/releases/v0.0.20.md)。

**變更**
- 新格式 NetCDF（`xr.open_dataset(path, engine="h5netcdf")`）：軸、單位、複數、標籤、專案、量測方案都在檔案裡。
- 舊方案的 Labber 自動換成 .nc；Labber 的 Project 欄位拿掉。
- 新套件：xarray、h5netcdf。

---

## 0.0.19 — 2026-10-09

外觀和讀檔模塊（LabLogViewer 2.0）一致：同樣的顏色、玻璃按鈕、分頁、輸入框、箭頭。詳見 [docs/releases/v0.0.19.md](docs/releases/v0.0.19.md)。

---

## 0.0.18 — 2026-10-09

一個網址、一個 App、一個控制台。詳見 [docs/releases/v0.0.18.md](docs/releases/v0.0.18.md)。

**新增**
- 公開下載頁 `/get`：沒登入打開網址就到這裡，自動給 Windows .exe 或 Mac .dmg。
- 網頁控制台（站長）：上傳檔案發佈所有程式（Hub、論文庫交給控制代理）、系統狀態、量測電腦。
- Lab Control：沿用 App 的登入、`--target`、`LAB_CONTROL_NODE_NAME`。

---

## 0.0.17 — 2026-10-08

平台網頁可以直接下載啟動器的 Windows .exe、macOS .dmg。詳見 [docs/releases/v0.0.17.md](docs/releases/v0.0.17.md)。

**新增**
- 平台網頁：啟動器卡片依作業系統顯示下載按鈕（Mac 依晶片）。
- 發佈中心：接受 .exe / .dmg，加到同一版的檔案裡。
- Hub：發佈檔的 `platform` / `arch`、模塊的 `downloads`；`latest` 只算有 zip 的版本。

---

## 0.0.16 — 2026-10-08

論文庫「專案」串起量測、數據、論文；讀檔模塊直接開整個專案。詳見 [docs/releases/v0.0.16.md](docs/releases/v0.0.16.md)。

**新增**
- 量測模塊「檔案設置 → 論文庫專案」：挑選或新增專案；資料夾規則 `{project}`；上傳時歸到專案。
- 標籤挑選列出專案的數據標籤，勾選或新建的標籤記到專案。
- Hub：數據歸屬專案、專案的數據標籤、NAS 上每個專案一個資料夾（硬連結）；網頁「專案」頁。
- 啟動器：用讀檔模塊開整個專案（NAS 資料夾或同步到本機），讀檔模塊的程式不用改。

---

## 0.0.15 — 2026-10-08

NAS 改成一個專案、三個容器（論文庫、Hub、控制代理）；發佈中心支援論文庫部分更新包。詳見 [docs/releases/v0.0.15.md](docs/releases/v0.0.15.md)。

**變更**
- `deploy/platform/docker-compose.yml`：同一個專案 `qelplatform`，Hub 用 `http://paperlib:8080` 連論文庫；新的發佈檔 `QELPlatform_NAS_v*.zip`。
- 控制代理：論文庫部分更新包（只覆蓋包裡的檔案、檢查缺少的基底檔案）、停機後備份資料庫、失敗時程式與資料庫都換回、先建置再停機。

---

## 0.0.14 — 2026-10-08

發佈中心：把新版 zip 丟進 Monitor，一鍵更新 Hub、論文庫網站與各模塊。詳見 [docs/releases/v0.0.14.md](docs/releases/v0.0.14.md)。

**新增**
- Monitor「發佈中心」：
  - 自動辨識 zip（`qel-module.json` 或內容）；
  - 依序發佈：論文庫 → 模塊 → Hub；
  - 失敗自動換回。
- 控制代理可以更新論文庫網站：建置映像、重建容器、確認版本；`data/` 不動。
- 啟動器自動更新模塊與自我更新；量測節點也能用發佈中心上傳的版本更新。

---

## 0.0.13 — 2026-10-07

量子工程平台：論文庫帳號登入、站長權限、共用標籤與論文、數據目錄、模塊之間拖放與傳指令、各模塊單獨發佈。詳見 [docs/releases/v0.0.13.md](docs/releases/v0.0.13.md)、[docs/PLATFORM.md](docs/PLATFORM.md)。

**新增**
- Hub：
  - `/api/platform/…`：登入（論文庫帳號，伺服器對伺服器）、站長權限、模塊發佈、共用標籤、論文、數據目錄、訊息、操作紀錄；
  - 平台網頁 `/portal`（PWA）；
  - 節點監控頁改到 `/nodes`。
- Lab Control：
  - 「平台 ▾」選單；
  - 「🏷 標籤與論文」（共用標籤，點標籤看論文庫的論文）；
  - 本機量測上傳到數據目錄；
  - 「已存的數據」可以拖到讀檔模塊；
  - 把數據檔拖進來 = 套用它的設定（可以復原）；
  - 接收平台送來的「套用設定」。
- Labber 匯出的檔內嵌完整量測方案（`attrs["labcontrol"]`）。
- Monitor「平台（站長）」分頁：狀態、使用者 × 模塊權限、停用帳號、發佈新版、操作紀錄。
- `qelcomm` 通信模塊（內附）。

---

## 0.0.12 — 2026-10-01

量測方案範本（單張 / 2D / 2D 電流異步 / N 層）、多個 Data 方塊、即時監控精簡＋獨立視窗疊圖（雙 Y 軸、拖曲線、軸縮放、marker）。詳見 [docs/releases/v0.0.12.md](docs/releases/v0.0.12.md)。

**新增**
- 範本：單張、2D、2D 電流異步（兩台交錯，與舊 sweep_main 相同）、N 層量測。
- Step 設定「電流異步」：每點只有一台前進「每台步進」（`interleaved_pair` 的交錯網格）。
- 多個 Data 方塊：各自的分檔方式、檔名、格式（`output.extra`）。
- 即時監控：主視窗只顯示 dB 與量測 / 循環；獨立視窗有疊圖（左右兩軸）、拖曲線上下移動、在軸上拖曳縮放、多個 marker 與讀數。

**修正**
- 節點的即時監控指令改用另一條佇列，不會因慢指令而逾時。
- 背景執行改用單一常駐轉送物件（偶發「_Relay has been deleted」）。
- 測試偶發 segfault：QApplication 在測試之間被銷毀（conftest 改為整個測試期間保留一個）。

---

## 0.0.11 — 2026-10-01

VNA 曲線九宮格排版、拖曳換位置、數據點；版本號改為純數字。詳見 [docs/releases/v0.0.11.md](docs/releases/v0.0.11.md)。

**變更**
- 曲線最多 5 張：2 張上下、3 張上左右＋下、4 張四方格、5 張四方格＋下；拖曳標題列交換位置（順序會記住）。
- 「數據點」：在曲線上加畫每個量測點。
- 頻率軸連動改用數值同步（不同寬度的圖不再錯開）。
- 版本號改為純數字：0.0.9a 改稱 0.0.10，本版 0.0.11。

---

## 0.0.10 — 2026-10-01

（原本稱 0.0.9a）即時監控修正與調整。詳見 [docs/releases/v0.0.10.md](docs/releases/v0.0.10.md)。

**修正**
- 即時監控「獨立視窗」是空的（電源與 VNA 搬移後沒有重新顯示）。
- 連續讀取遇到上一次還沒讀完時會停止；獨立視窗 / 曲線視窗關閉時可能當機。

**變更**
- 版面固定：左電流、右上 VNA 參數（可拉小）、右下曲線。
- 曲線可複選 dB / Linear / Real / Imag / Phase / Unwrap / IQ，每種一張圖；「⧉ 曲線視窗」單獨拉出來。
- 掃描速度：ZNA 掃描時間與自動掃描時間可設定；連續讀取間隔可設，預設「最短」（刷新間隔 = 量測時間）。

---

## 0.0.9 — 2026-10-01

Hub 控制台、在一般電腦直接控制量測節點的 DC / VNA、即時監控分頁、儀器伺服器群組、新外觀。詳見 [docs/releases/v0.0.9.md](docs/releases/v0.0.9.md)。

**新增**
- Hub 控制台：NAS 上多一個控制代理 `labcontrol-hub-agent`（port 8766）。在 Monitor「Hub 控制台」或 `http://NAS:8766/` 選 zip →「更新網站」。
  - 每一步即時顯示：停止 → 備份 → 換程式 → 重建 → 啟動 → 確認版本；失敗自動換回。
  - 另有完全重建、重新啟動網站、停止、啟動、回到備份。
- Lab Control Monitor 改用 Lab APP 發佈（程式 `LabControlMonitor`）；`tools/make_release.py` 產生三個發佈檔。
- 在一般電腦直接控制量測節點：連線儀器、電源群組斜坡 / 微調 / 輸出、VNA 參數與曲線。
  - 節點新增指令：`sources`、`group_ramp`、`fine_step`、`output`、`vna_trace`、`add_instrument`…
  - 本機與遠端共用 `remote/backend.py`。
- 右上「即時監控 / 量測監控」分頁，即時監控可以開成獨立視窗。
  - 電源群組：拖曳合併 / 拆開、M1–M3 記憶（0.05 mA/s）、兩台時 ± 微調、合併時覆寫或平均；
  - VNA：S11 / S12 / S21 / S22、參數、RF、曲線；
  - 開始量測自動切到量測監控。
- 儀器參數預設只顯示已連線儀器的參數。
- 儀器伺服器以群組顯示（各量測節點＋未歸屬）：拖曳移動（自動複製設定）、每組全部連線 / 全部斷線、控制別台節點的儀器。
- 新外觀（深色、卡片、藍色主要按鈕），Lab Control、儀器伺服器、Monitor 共用 `labmonitor/style.py`。

**變更**
- 共用但未歸屬的網路儀器不能連線，要先移到某個群組。
- Hub token 預設 `<token>`。
- Monitor 移除舊的 Hub 更新按鈕與「Hub 資料夾」設定；部分失敗改用不擋畫面的提示。

---

## 0.0.8 — 2026-09-30

跨電腦儀器管理、Lab Control Monitor、Hub 線上更新、量測卡住修正。詳見 [docs/releases/v0.0.8.md](docs/releases/v0.0.8.md)。

**新增**
- 儀器登錄：Hub 記錄每台儀器（依 IP / USB 序號辨識）掛在哪些電腦、在哪台連線；VISA 掃描結果也回報。
- 共用網路儀器的「拉取群組」：拉到一台電腦後，其他電腦自動中斷且連不上；量測中不能拉走。
- 全部連線 / 全部斷線（節點、Hub 網頁、儀器伺服器）；遠端控制儀器（describe / get_all / set / connect / disconnect / scan）。
- Lab Control Monitor（`labmonitor/`、`deploy/monitor/build_exe.bat` → exe）：節點、儀器、所有電腦、Hub 更新。
- Hub 線上更新（從發佈資料夾或 Monitor 內附版本；失敗自動退回）；Hub 網頁的儀器表與節點操作。
- 量測卡住偵測（`LAB/logs/stall_*.txt`）；斜坡顯示目標與剩餘時間。

**修正**
- 節點在量測執行緒裡上傳即時資料，Hub 慢時量測被拖住 → 改成佇列 + 上傳執行緒。
- Hub 偶發逾時就斷線 → 連續 3 次才斷線、逾時 15 秒。

---

## 0.0.7 — 2026-09-30

Lab Control Hub：NAS 網站取代共用資料夾。詳見 [docs/releases/v0.0.7.md](docs/releases/v0.0.7.md)、[docs/REMOTE.md](docs/REMOTE.md)、[deploy/hub/README.md](deploy/hub/README.md)。

**新增**
- Lab Control Hub（`labhub/`，標準函式庫）：所有電腦的狀態、指令與即時資料中轉（long-poll）、量測檔中轉、發佈版本下載、token 驗證。
- 監控網頁 `http://NAS:8765/`：節點進度、所有電腦、量測檔下載、暫停 / 停止。
- `deploy/hub/`：Docker compose（官方 python 映像，免 build）、Windows / Linux 啟動檔、安裝說明。
- 量測檔上傳 Hub，開始量測的控制端自動下載；「Hub 連線設定…」「開啟 Hub 監控網頁」；`python -m labcontrol hub`。
- VNA Trace：RF 輸出關閉時提示 trace 只是雜訊。

**變更**
- `settings.yaml remote:` 改用 `hub_urls` / `token`；節點更新從 Hub 下載（`update.method: hub`）；移除 SMB 共用資料夾與「NAS 登入」。
- Hub 連線錯誤寫進 `LAB/logs/labcontrol.log`。

---

## 0.0.6 — 2026-09-30

淺色 / 深色模式；依實機驅動測試報告修正 ZNA。詳見 [docs/releases/v0.0.6.md](docs/releases/v0.0.6.md)。

**新增**
- 淺色 / 深色模式：工具列 🌙 / ☀ 即時切換（主視窗、儀器伺服器），「設定 ▾ → 外觀」可選淺色 / 深色 / 跟隨系統；
  寫回 `settings.yaml app.theme`；`--theme` 命令列參數。流程圖、即時監控圖表、控制視窗 trace 圖、所有狀態顏色都會換色。
- 顏色集中在 `labcontrol/apps/qt/theme.py`（語意顏色表），UI 不再寫死顏色。

**修正**
- R&S ZNA：儀器的資料格式（FORMat）停在二進位（其他程式設定的）時，讀 x 軸 `CALC:DATA:STIM?` 失敗
  「'ascii' codec can't decode byte 0x95」→ 每次讀陣列前都先送 `FORM ASC`（或 `data_format: real64` 時送 `FORM REAL,64`）。
- 驅動測試：儀器上沒建立的 trace（例如只建了 S21 時的 S11 / S12 / S22）改為「略過」而不是警告；x 軸讀取失敗改為 ✖。

**變更**
- Qt 前端的背景執行緒統一由 `worker.start_thread` 啟動，視窗物件一律在 GUI 執行緒釋放。

---

## 0.0.5 — 2026-09-30

遠端量測（量測節點 + NAS 基站）。詳見 [docs/releases/v0.0.5.md](docs/releases/v0.0.5.md)、[docs/REMOTE.md](docs/REMOTE.md)。

**新增**
- 量測節點：其他電腦透過 NAS 共用資料夾在量測電腦上量測、看即時資料、暫停 / 停止、讀寫儀器。
- 主視窗「執行於」選節點；「🛰 量測節點」開關；NAS 登入（Windows 認證管理員）；`--node`、`labcontrol node / nodes`。
- 自動請較舊的節點更新（量測中延後），節點從 NAS Releases 複製新版並重新啟動。

**修正**
- 遠端連續兩次開始量測的競態（剛啟動的量測也算進行中）。

---

## 0.0.4 — 2026-09-30

儀器伺服器（Labber Instrument Server）與驅動測試。詳見 [docs/releases/v0.0.4.md](docs/releases/v0.0.4.md)。

**新增**
- 儀器伺服器：儀器清單與狀態、新增（VISA 掃描、*IDN? 辨識與驅動建議）/ 移除 / 停用，寫回 instruments.yaml（保留註解、自動備份）。
- 儀器控制視窗：讀取 / 寫入、電源輸出與斜坡、VNA trace、收發紀錄、指令耗時、手動 SCPI；量測中唯讀。
- 驅動測試：讀取 / 寫回相同值 / 量測一次 / 小幅度輸出，每步查錯誤佇列，報告存 LAB/logs；命令列 `test`、`resources`。
- `docs/INSTRUMENT_TEST.md` 上機測試指南；`settings.yaml` 新增 `server:`；新套件 ruamel.yaml。

---

## 0.0.3 — 2026-09-30

主視窗改成 Labber 式四格工作台。詳見 [docs/releases/v0.0.3.md](docs/releases/v0.0.3.md)。

**新增**
- 左上流程圖：節點自由拖動、拉線連接、迴圈自動畫框、從列表拖入（放在連線上插入 / 節點上接續）、右鍵選單、縮放平移。
- 左下儀器參數列表：所有儀器參數與量測通道，拖進流程圖建立節點；取值、設定值、唯讀連線、自動更新。
- 右上即時監控：控制、進度、剩餘時間、斜坡進度、掃描軸目前值、曲線（dB / 相位 / 實部 / 虛部）與 2D 影像。
- 右下檔案設置：檔名、資料夾、同名處理、格式、Labber Project / User / Tags / 註解、存檔路徑預覽、執行設定、檢查與估時。
- Labber 式 Step 設定：起訖 / 中心寬度、步進 / 點數、線性 / 對數、每步等待、來回掃、掃完後動作；每點量測前等待。
- 方案檔 `scheme: 2`（節點位置與連線）；所有可寫數值參數都能當 Step；Labber 匯出註解。

**變更**
- 移除舊的方塊庫 / 屬性面板編輯器；方案不再內建執行預設值（改用 settings.yaml run_defaults）。

---

## 0.0.1b — 2026-09-30（Lab APP v0.0.2）

bug 修正。詳見 [docs/releases/v0.0.1b.md](docs/releases/v0.0.1b.md)。

**修正**
- 0.0.1a 啟動失敗 `No module named 'labcontrol.data'`：`.gitignore` 的 `data/` 把 `labcontrol/data/` 套件排除在 git 之外，Lab APP 發佈時沒有上傳。改為只忽略根目錄 `/data/`。
- 新增回歸測試：`labcontrol/` 與 Lab APP 必要檔不可被 `.gitignore` 排除。

---

## 0.0.1a — 2026-09-30（Lab APP v0.0.1）

第一個以 **Lab Control** 名稱發佈的版本（Lab APP 格式；Lab APP 上為 v0.0.1，因上述問題無法啟動）。詳見 [docs/releases/v0.0.1a.md](docs/releases/v0.0.1a.md)。

**新增**
- App 名稱 Lab Control、圖示（`assets/icon.ico`）、Lab APP 發佈檔（`main.py`、`.entry`、`.readme`、`.icon`、`.python-version`、`requirements.in`）。
- LAB 設定資料夾 `C:\Users\even9\LAB`：`settings.yaml`、`instruments.yaml`、`templates/`、`schemes/`、`experiments/`、`plugins/`、`logs/`；第一次啟動自動建立，升級不覆寫。
- `settings.yaml`：存檔、Labber、執行預設值、新方塊初始值、單位選單 / 小數位、方塊顏色、互斥量測規則、估時係數、hook 預設、網頁面板、logview。
- 驅動規範 `ParamSpec`（參數表）+ `docs/DRIVER_GUIDE.md` + `LAB/plugins/_driver_template.py`；`instruments.yaml` 可覆寫任何參數欄位與 SCPI。
- 方塊庫、量測方塊欄位、Labber 參數表名稱改由 driver 參數表產生（不再寫死 VNA / SHFQC 欄位）。
- 範本改為 `LAB/templates/*.scheme.yaml`，可「把目前方案存成範本」。
- 編輯器「設定 ▾」選單：開啟 LAB 資料夾 / 設定檔、重新載入設定、關於。
- `python -m labcontrol init`；錯誤記錄寫到 `LAB/logs/labcontrol.log`。

**修正（依儀器手冊）**
- GS820：通道指令改為 `:CHANnel<n>:SOURce:…` / `:CHANnel<n>:OUTPut`（舊程式的 `:SOURce<n>:` 不是 GS820 的語法）。
- GS610：電流源時的限制器是**電壓**限制器（`:SOUR:VOLT:PROT:ULIM/LLIM`），舊程式寫到電流限制器。
- GS200 / GS610 / GS820：依量程解析度取整、檢查量程上限；輸出開啟時拒絕切換量程 / 功能（感性負載保護）；讀到 sweep 模式、pulse 波形、自動量程時警告；`OUTP?` 回 `ZERO` 時視為未輸出。
- R&S ZNA：開啟平均時每一點量測前 `:SENS:AVER:CLE`（避免上一點被平均進來）；改用 `CALC:DATA:TRAC? '<trace>', SDAT` 讀資料；x 軸用 `CALC:DATA:STIM?` 實際頻率；IF 頻寬 / 功率設定後讀回；可設定通道號；設定後讀 `SYST:ERR?`。
- driver 名稱 `rs.vna` → `rs.zna`（舊名稱仍可用）。

**變更**
- 套件改名 `labmaster` → `labcontrol`；例外基底 `LabControlError`（`LabMasterError` 保留為別名）；HDF5 schema `labcontrol/1`（仍可讀 `labmaster/1`）。
- `config/lab.yaml` → `LAB/instruments.yaml`；`data_root`、`labber`、`logview` 移到 `settings.yaml`。

---

## 開發版（未發佈到 Lab APP）

### v0.2-dev — 2026-09-29
- 量測方案編輯器（PyQt6）：流程圖 + Labber 式參數表 + 屬性面板，四個範本對應手繪流程圖。
- 方案檢查與估時（含內圈斜坡回起點時間、蛇形掃描建議）；存讀 `.scheme.yaml`；直接執行。
- Data 方塊放在外圈 → 每個外圈值一個檔（量測中背景匯出）；多維 Labber 匯出。
- SHFQC 量測方塊（模擬）；VNA / SHFQC 不同時量測的檢查。

### v0.1 — 2026-09-29
- 框架骨架：Station（唯一儀器清單、lease、模擬模式）、Parameter / 能力介面、EventBus、plugin 註冊。
- Driver：GS200 / GS610 / GS820、R&S VNA、模擬儀器、虛擬雙電源交錯步進。
- 引擎：nD 掃描、Runner（暫停 / 回溯 / 手動 / 重試 / 停靠）、吸收峰型態變化自動暫停。
- 資料：原生 HDF5 逐點寫入、Labber 匯出（與舊檔相容）、檔名規則。
- 前端：CLI、Qt 監控視窗、舊網頁電源面板改接共用 Station。
