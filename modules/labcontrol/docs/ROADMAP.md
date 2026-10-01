# 版本規劃

版本規則：`X.Y.Z` 純數字，每次發佈遞增最後一位（0.0.10 → 0.0.11），不再使用字母字尾。
原則：每一版都能單獨上線使用；舊程式在新版通過實機驗收前持續保留；Labber 輸出結構不變。
已完成的內容見 `CHANGELOG.md`。

## 0.0.1a / 0.0.1b — 第一個 Lab APP 版本（Lab APP v0.0.1 / v0.0.2）
- 量測方案編輯器、LAB 設定資料夾、驅動規範（ParamSpec）、依手冊修正 GS / ZNA 驅動

## 0.0.3 — Labber 式主視窗
- ✅ 四格工作台：流程圖（節點自由拖動、拉線連接）、儀器參數列表、即時監控、檔案設置
- ✅ Labber Step setup：中心 / 寬度、對數間隔、來回掃、每軸「掃完後」動作、每點量測前等待
- ✅ Labber tags / 註解、檔案設置集中在主視窗
- **驗收**：模擬模式全流程；實機只做唯讀連線檢查（`python -m labcontrol check`）

## 0.0.4 — 儀器伺服器
- ✅ Labber 式 Instrument Server：儀器清單、新增 / 移除 / 停用（寫回 instruments.yaml）、VISA 掃描與 *IDN? 辨識
- ✅ 儀器控制視窗：讀取 / 寫入、電源輸出與斜坡、VNA trace、收發紀錄、指令耗時統計、手動 SCPI
- ✅ 驅動測試（讀取 / 寫回相同值 / 量測一次 / 小幅度輸出）與報告；命令列 `test`、`resources`

## 0.0.5 — 遠端量測
- ✅ 量測節點 + NAS 基站（共用資料夾）：遠端開始 / 監看 / 停止、讀寫儀器
- ✅ 新版開啟時自動請較舊的節點更新（量測中延後）

## 0.0.6 — 淺色 / 深色模式
- ✅ 工具列 🌙 / ☀ 即時切換、「設定 ▾ → 外觀」淺色 / 深色 / 跟隨系統，寫回 `settings.yaml app.theme`
- ✅ 依實機驅動測試報告修正 ZNA 讀 x 軸（儀器資料格式停在二進位時失敗）

## 0.0.7 — Lab Control Hub
- ✅ NAS 上的中繼網站取代 SMB 共用資料夾：監控網頁（所有電腦、節點進度、量測檔下載、暫停 / 停止）、指令與即時資料 long-poll
- ✅ 數據中轉：量測檔上傳 Hub、控制端自動下載；節點更新從 Hub 下載新版
- 待上線確認：NAS Docker、防火牆 8765、VPN

## 0.0.8 — 跨電腦儀器管理
- ✅ 儀器登錄（掛在哪台電腦）、共用網路儀器拉取群組、全部連線 / 斷線、遠端控制儀器
- ✅ Lab Control Monitor（exe）、Hub 線上更新；量測不再被 Hub 拖住、卡住偵測
- 待確認：0.0.7 卡住的實際原因（需要量測節點的 log / stall 檔）

## 0.0.9–0.0.12 — Hub 控制台、遠端直接控制、即時監控（目前）
- ✅ Hub 控制台（控制代理 8766）：選 zip 更新網站、每一步即時顯示、失敗自動換回；完全重建 / 重新啟動 / 停止 / 啟動 / 回到備份
- ✅ Monitor 改用 Lab APP 發佈
- ✅ 在一般電腦直接命令量測節點連線，並控制它的 DC 群組與 VNA
- ✅ 右上「即時監控 / 量測監控」分頁、即時監控獨立視窗；電源群組卡片（合併 / 拆開、M1–M3、微調）
- ✅ 儀器參數只顯示已連線；儀器伺服器群組（各節點＋未歸屬、拖曳移動、每組全部連線）
- ✅ 新外觀（深色、卡片、藍色主要按鈕）
- ✅ 0.0.10：即時監控版面（左電流、右上 VNA 參數、右下曲線）、掃描時間控制
- ✅ 0.0.11：曲線九宮格排版、拖曳換位置、數據點；版本號改為純數字
- ✅ 0.0.12：範本（單張 / 2D / 2D 電流異步 / N 層）、多個 Data、即時監控精簡＋疊圖雙軸、marker
- 待上線確認：NAS 加入 labcontrol-hub-agent（docker.sock 權限、防火牆 8766）

## 0.0.x — 上機修正
- 依 `docs/MIGRATION.md` 上機前檢查逐項驗證，發現問題以下一個版本號修正
- 把驗證過的安全值（limits / ramp_rate / max_jump / ZNA 功率範圍）寫進預設 `instruments.yaml`

## 0.1.0 — 取代 sweep_main.py
- ✅（0.0.3 已完成）編輯器與監控視窗合一
- 自動載入上次方案；Labber 的 Skip（跳過內圈）
- 移植 QC 挑選視窗（`analysis/qc.py`），含量測中提前檢視
- 儀器狀態側欄、VNA 解鎖按鈕、檔名日期提示
- 設定頁：在 App 內編輯 `settings.yaml` / `instruments.yaml`（含檢查）
- **驗收**：同一樣品新舊程式各跑一次，Labber 檔結構一致、資料在雜訊內一致

## 0.2.0 — 量測與網頁面板合一
- 主程式內建網頁面板，手機控制與量測共用同一個 Station，不再搶 VISA
- 網頁顯示 lease 狀態（量測中改為唯讀）；斜坡由伺服器端執行（手機休眠也會走完）
- 存取限制（token / 只允許實驗室網段）

## 0.3.0 — logview 整合（依 logview 形式調整）
- `LabberReader`：logview 用同一個 `open_dataset()` 讀新舊檔
- 即時層：量測中即時顯示（HDF5 SWMR）；指令層：「用這組設定重新量測」
- 匯出後自動以 logview 開檔（`settings.yaml` → `logview.command`）

## 0.4.0 — 量測邏輯擴充
- 磁場單位：依每組電磁鐵校正把 mT 換算成電流
- 迴圈內多個量測步驟、條件分支；共振追蹤 hook（依最深點平移 VNA 視窗）
- 連續斜坡程序（精確邊界搜尋）；實驗佇列（過夜量測）

## 0.5.0 — SHFQC 實機
- SHFQC driver（zhinst-toolkit，沿用 shfqc_app 程式碼；參數表已定義，只需實作 connect / configure / acquire）

## 1.0.0 — Instrument Server 獨立程序（多程序架構；Hub 已提供遠端通道）
- Station 成為獨立服務程序，獨佔所有 VISA 連線；Qt、網頁、Jupyter、logview 都是 client
- 凍結 driver API（ParamSpec）與檔案 schema `labcontrol/1`

## 1.x — 更多儀器 / 實驗管理
- 溫控 / 稀釋冷凍機讀值（ScalarMeter + `limit_pause`）、訊號產生器、磁鐵電源
- 量測索引（SQLite）、推播通知、遠端監看

## 待確認事項
| 項目 | 影響版本 |
|---|---|
| logview 的形式（PyQt 程式？讀 Labber 檔？是否要即時顯示？） | 0.3.0 |
| 各儀器 `limits` / `ramp_rate` / `max_jump` 與 ZNA 功率範圍的實際安全值 | 0.0.x |
| 連線時是否要自動開輸出（舊版會 `OUTP 1`；新版預設只讀，可用 `on_connect`） | 0.0.x |
| 主要存檔格式是否長期維持 Labber | 1.0.0 |
