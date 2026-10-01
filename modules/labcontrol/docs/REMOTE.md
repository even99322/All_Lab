# 遠端量測（Lab Control Hub）

量測電腦開成「**量測節點**」，其他人用自己的電腦（Lab Control）設計方案、在節點上量測、看即時資料、拿到量測檔。

中間是放在 NAS 上的 **Lab Control Hub**：一個小網站，同時是監控台、指令中轉站和數據中轉站。
每台電腦都只**主動連到 Hub 的網址**（HTTP，預設 `http://192.168.50.2:8765`，VPN 用 `100.114.33.20`）。
所以：

- 不需要 SMB 共用資料夾；
- 不需要在 Windows 登入 NAS；
- 不需要各電腦互相連得到。

```
 控制端（任何電腦）                 Lab Control Hub（NAS，Docker）             量測節點（量測電腦）
 Lab Control ── 指令 ─────────▶  http://NAS:8765  ◀── 取指令（long-poll）── Lab Control（🛰 量測節點）
             ◀─ 即時資料 / 量測檔 ──  狀態、指令、即時資料、檔案  ◀── 心跳 / 即時資料 / 上傳檔 ──  └ 儀器（VISA）
 瀏覽器 / 手機 ── 監控網頁 ──────▶  http://NAS:8765/
```

延遲：

- 指令：節點一直掛著 long-poll，指令送出後立即收到，一般在 0.1 秒以內。
- 即時資料：約 0.5 秒（`settings.yaml remote.live_interval_s`）。

儀器通訊仍在節點本機，量測速度不受影響。

## 1. 安裝 Hub（管理員，一次）

照 `deploy/hub/README.md` 做。Synology 用 Container Manager 匯入 `docker-compose.yml`：

- 映像是官方 `python:3.12-slim`，不需要 build。
- NAS 防火牆開 TCP 8765。
- 第一次啟動會產生存取 token，存在 `data/token.txt`。

沒有 Docker 時，可以在任何一台常開的電腦執行 `python -m labcontrol hub`（或 `run_hub.bat`）。

## 2. 每台電腦設定一次

主視窗「設定 ▾ → **Hub 連線設定…**」：

- 網址：預設 `192.168.50.2, 100.114.33.20`，依序嘗試；
- token：填 Hub 的 token。

按「測試連線」確認後儲存，設定寫到 `LAB/settings.yaml`（`remote.hub_urls`、`remote.token`）。
狀態列顯示「**Hub：✔ 192.168.50.2:8765**」表示連上了。連不上時，原因會顯示在狀態列的提示文字，也會寫進 `LAB/logs/labcontrol.log`。

token 是實驗室共用的 Hub 存取碼，**不是 NAS 帳號密碼**。Lab Control 和 Hub 都不需要 NAS 帳號。

## 3. 監控網頁

「設定 ▾ → **開啟 Hub 監控網頁**」會自動登入；其他裝置（手機）開 `http://192.168.50.2:8765/` 後輸入 token。網頁上有：

- **量測節點**：
  - 狀態（閒置、量測中、暫停、離線）、版本（比最新舊會標「可更新」）；
  - 目前量測的名稱、誰開始的、進度條、剩餘時間；
  - 儀器連線狀態與電源輸出值、最近訊息；
  - ⏸ 暫停、⏹ 停止量測。
- **所有電腦**：每一台開著 Lab Control 的電腦，包括控制端和節點，顯示使用者、版本、目前在做什麼、IP、最後回報時間。
- **量測檔**：節點上傳到 Hub 的檔案，可以直接下載。

線上或離線由 **Hub 收到回報的時間**判斷（預設 15 秒，`LABHUB_OFFLINE_S`），不受各電腦時鐘誤差影響。

## 4. 量測電腦：開啟量測節點

任一種方式：

- 主視窗工具列按「**🛰 量測節點**」（下次開啟會記住）；
- `settings.yaml` 設 `remote.node_enabled: true`；
- `python main.py --node`；
- 不開視窗：`python -m labcontrol node`。

節點名稱預設是電腦名稱，可在 `settings.yaml remote.node.name` 改。同名節點不能同時在兩台電腦上線，Hub 會拒絕第二台。

> **自己這台的「執行於」不會列出自己的節點。** 要從**另一台**電腦才看得到。網頁的「量測節點」可以確認節點有沒有上線。

節點可以限制遠端能做的事（`settings.yaml remote.node`）：

| 設定 | 預設 | 說明 |
|---|---|---|
| `allow_remote_run` | true | 允許遠端開始量測 |
| `allow_remote_set` | true | 允許遠端設定儀器 |
| `allow_update` | true | 允許遠端要求更新 |
| `allowed_users` | [] | 限定使用者（例如 `["alice@PC-01"]`）；空白 = 所有有 token 的人。停止 / 暫停不受限制 |

## 5. 其他電腦：在節點上量測

1. 工具列「**執行於**」選節點，例如「🛰 QEL-PC（閒置 · v0.0.7）」。
2. 左下儀器參數列表、流程圖檢查換成**節點的儀器**：Lab Control 用節點的 instruments.yaml 建立模擬鏡像，參數表與上下限都相同。取值、設定值直接作用在節點的儀器上。
3. 設計方案後按「▶ 開始量測」：
   - 方案送到節點，節點重新檢查後執行；
   - 資料存在**節點**的資料根目錄。
4. 右上即時監控顯示節點的進度、曲線、2D 影像；暫停、繼續、退回、停止會送到節點。
5. 量測結束後：
   - 節點把資料檔（Labber / HDF5 與 `_raw/` 原始檔）**上傳到 Hub**；
   - 你的電腦**自動下載**到自己的資料根目錄，保留同樣的 `yyyy/mm/Data_mmdd` 結構；
   - 相關設定：`remote.download_results`、`remote.download_root`（下載位置）、節點端的 `remote.upload_results`；
   - 別人開始的量測不會自動下載，可以在網頁下載。
6. 選回「💻 本機」，回到自己的儀器。

規則：

- 同一時間節點只跑一個量測；有人在量測時，其他人開始會被拒絕（可以看、可以停止）。
- 儀器 lease 照舊：量測中的儀器不能被別人寫入。
- 停止、暫停任何人都可以按（安全優先），節點會記錄是誰按的。

## 6. 儀器：掛在哪台電腦、共用儀器的歸屬、跨電腦控制（0.0.8 / 0.0.9）

每台 Lab Control（節點和控制端）回報 Hub 時，會一起回報自己的儀器，包括設定檔中的儀器與「掃描 VISA」偵測到的。
Hub 依**位址**辨識是不是同一台實體儀器，不看各電腦取的名稱：

| 位址 | 識別碼 | 說明 |
|---|---|---|
| `TCPIP0::192.168.1.11::INSTR`、`TCPIP::192.168.1.11::hislip0::INSTR` | `net:192.168.1.11` | 網路儀器：可能兩台電腦都連得到 →「共用」 |
| `USB0::0x0B21::0x0039::90ZC38697::0::INSTR` | `usb:0b21:0039:90zc38697` | 依廠商 / 產品 / 序號；換插到別台電腦也認得 |
| `GPIB0::5::INSTR`、`ASRL…` | `gpib@<電腦>:0:5` | 只屬於那台電腦 |

Hub 會記錄每台儀器**掛在哪些電腦**，包括首次與最後一次看到的時間。這些資訊在三個地方看得到：
- Lab Control Monitor 的「儀器」分頁；
- Hub 網頁的「儀器（掛載位置與歸屬）」；
- `GET /api/instruments`。

**共用的網路儀器要主動拉取**：兩台以上電腦都設定了同一台網路儀器時（例如兩個量測節點都能連 `192.168.1.11` 的 ZNA），
它會標成「⚠ 共用，未拉取」。拉取到某一台電腦後：

- 那台電腦成為這台儀器的**歸屬**（群組），Hub 會記住，重開也不會忘；
- 其他電腦上連著的會被**自動中斷**：節點收到 Hub 的 release 指令，控制端在下一次回報時自行中斷；
- 其他電腦之後**連不上**：「連線 / 全部連線」會略過它，並顯示「目前歸 X 使用」；
- 量測中的儀器不能被拉走，要等量測結束。

拉取的地方：Monitor「儀器 → ⇩ 拉取到…」、Hub 網頁的「拉取到…」、Lab Control 儀器伺服器的「⇩ 拉取到這台」。
只有一台電腦有的儀器（USB 等）不需要拉取。

**全部連線 / 全部斷線**（以量測節點下的儀器為主）：

- Monitor「量測節點」分頁、Hub 網頁的節點卡片：🔌 全部連線、⏏ 全部斷線、🔍 掃描儀器；
- Lab Control 儀器伺服器：「全部連線」「全部斷線」；
- 連線是唯讀，不改變輸出。歸別台的共用儀器會被略過；量測中的儀器不會被中斷。每台的結果會列出來。

**在別的電腦控制儀器**：Monitor 在「量測節點」展開節點雙擊儀器，或在「儀器」分頁按「🎛 控制…」，會開出遠端控制視窗：

- 讀取全部參數、寫入單一參數或全部變更；
- 連線 / 中斷、自動讀取；
- 電源輸出值預設以 mA 顯示。

指令經 Hub 送到那台節點執行，照樣受 lease 保護：量測中的儀器只能讀、不能寫。
要被遠端控制的電腦，請開「🛰 量測節點」。

### 6.1 儀器伺服器的群組（0.0.9）

儀器伺服器改成**群組**顯示：每一台量測節點（電腦）一個群組，列出它下轄的儀器；最後是「**未歸屬**」。

- 未歸屬：多台電腦都偵測得到、但還沒歸到任何群組的網路儀器，以及只被掃描偵測到、沒有任何電腦設定的儀器。
  **未歸屬的共用儀器不能連線**（「全部連線」會略過並說明原因），要先移到某個群組。
- **移動**：把儀器拖到別的群組，或右鍵「移到群組」、工具列「移到群組…」。
  - 目標電腦還沒有這台儀器的設定時，會自動把設定（driver、位址、上下限…）加到那台的 `instruments.yaml`；
  - 然後 Hub 記錄歸屬，其他電腦上連著的會自動中斷；
  - 拖到「未歸屬」＝清除歸屬。
  - USB / GPIB 儀器實體接在某台電腦，不能用軟體移動。
- 每個群組都有「**全部連線 / 全部斷線**」。別台量測節點的群組也可以按，指令經 Hub 送過去。
- 別台節點的儀器也可以選取後「連線」「中斷」，雙擊會開遠端控制視窗。
- 沒有連上 Hub 時，只顯示「這台電腦」群組，操作與以前相同。

### 6.2 在一般電腦直接控制量測節點的 DC 與 VNA（0.0.9）

主視窗「執行於」選量測節點 A 之後，右上「監控」的「即時監控」分頁就是 A 的儀器：

- 「全部連線」：命令 A 連線它群組內的儀器；
- 電源群組：前往目標、M1–M3 記憶、微調、輸出開關；
- VNA：S11 / S12 / S21 / S22、參數、RF 開關、曲線。

這些都經由 Hub 在 A 上執行，也照樣受 lease 保護。
節點新增的指令：`sources`、`group_ramp`、`group_stop`、`ramp_status`、`fine_step`、`output`、`vnas`、`vna_catalog`、`vna_trace`、`add_instrument`。
量測進行中，即時監控只顯示數值，不能控制。

## 7. Lab Control Monitor（Lab APP 程式）

獨立的監控程式，只需要 PyQt6，不含量測套件，可以在任何電腦開。0.0.9 起和 Lab Control 一樣**用 Lab APP 發佈與安裝**，程式名稱是 `LabControlMonitor`。
分頁如下：

- **量測節點**：每個節點的狀態、量測進度、儀器連線數。展開後看每台儀器的狀態與電源值，下方顯示節點訊息。
  按鈕有全部連線 / 全部斷線 / 掃描儀器 / 暫停 / 停止 / 控制儀器。
- **儀器**：Hub 的儀器登錄，包括掛載位置、型號、序號、狀態、歸屬，可以連線、中斷、控制、拉取、取消歸屬。
- **所有電腦**：所有開著 Lab Control 的電腦。
- **Hub 控制台**：更新 / 重建 / 重新啟動 NAS 上的 Hub 網站（見下方）。

第一次開啟會讀這台電腦的 `LAB\settings.yaml` 取得 Hub 網址與 token（預設 `Labqel330`）；在「⚙ 連線設定」可以修改。設定存在 `%APPDATA%\LabControlMonitor\config.json`。
Lab Control 主視窗「設定 ▾ → Lab Control Monitor…」也可以直接開同一個畫面。
還是需要單一 exe 時，`deploy\monitor\build_exe.bat` 仍然可以用。

**Hub 控制台（更新 Hub）**：NAS 上的控制代理（`labcontrol-hub-agent`，port 8766）執行，Monitor 或瀏覽器 `http://NAS:8766/` 都能操作。

1. 下載新版 `LabControlHub_v*.zip`，不用解壓縮。
2. 打開「Hub 控制台」。
3. 選 zip 或把 zip 拖進來。
4. 按「更新網站」。

進度會即時顯示：檢查 zip → 停止 → 備份 → 換程式 → 重建 → 啟動 → 確認版本。新版沒起來就自動換回。

- 其他按鈕：「完全重建」「重新啟動網站」「停止」「啟動」，以及備份的「回到這版」。
- 「用這個程式內附的 Hub」：不用另外下載 zip。
- 第一次要把控制代理加進 NAS 的 docker 專案，步驟見 `deploy/hub/README.md`。

## 8. 量測卡住時

- 節點上的量測**不會等待網路**：即時資料先排隊，由另一個執行緒上傳。Hub 很慢或斷線時量測照常進行，資料之後補送；
  斷線太久時只丟掉最舊的曲線。0.0.7 的節點在量測執行緒裡上傳，Hub 慢時會拖住量測。
- **卡住偵測**：量測中超過 `run_defaults.stall_warn_s`（預設 120 秒）沒有任何進度（點、斜坡、狀態）時，
  程式會把所有執行緒正在做什麼存到 `LAB/logs/stall_*.txt`，並在量測訊息顯示路徑。**請把這個檔傳給開發者。**
  暫停、手動步進等使用者時不算卡住。
- 移到起點的斜坡會顯示目標與剩餘時間（例如「斜坡移動 magnet_A：12.3 → 158.6 mA，剩約 4:52」）。
  電流從 0 開始時，以 0.5 mA/s 移到 158 mA 大約要 5 分鐘，這不是卡住。

## 9. 新版本發佈後自動更新節點

1. 開發者用 Lab APP 發佈新版（例如 0.0.8）。Hub 的 `LABHUB_RELEASES` 指向 Lab APP 的發佈資料夾（含 `LabControl/v0.0.8/`），網頁上會顯示「Hub 上的最新發佈 v0.0.8」。
2. 任何人用新版 Lab Control 開啟時，會檢查所有線上節點，比自己舊的節點會收到更新要求。相關設定與手動方式：
   - 自動檢查的開關：`remote.auto_update_nodes`；
   - 手動：`python -m labcontrol nodes --update`。
3. 節點確認 Hub 上有這個版本後：
   - 量測進行中 → **等量測結束**才更新，網頁顯示「等待更新」；
   - 閒置時：
     - 從 Hub 下載 `v<版本>.zip`，解壓到 `LAB\app\v<版本>\`；
     - Python 環境：套件沒變就沿用目前的；有變動就建立新環境（有 uv 用 uv）；
     - 以同樣的參數啟動新版並關閉舊版，節點自動重新上線。
4. 更新失敗時，節點繼續用舊版，錯誤顯示在網頁和控制端。

`remote.update.method: labapp` 可以改成呼叫 Lab APP 安裝（命令在 `remote.update.command`）。這組命令列參數**尚未驗證**，確認可用前請維持預設的 `hub`。

## 10. 命令列

```bash
python -m labcontrol nodes              # Hub 上的節點與所有電腦（● 線上 ○ 離線）與網頁網址
python -m labcontrol nodes --update     # 請較舊的節點更新到這台的版本
python -m labcontrol node               # 不開視窗，以節點執行（Ctrl+C 結束）
python -m labcontrol hub                # 在這台電腦執行 Hub（沒有 NAS Docker 時）
```

## 11. Hub API（開發用）

所有 API 都要帶 `Authorization: Bearer <token>`，完整清單在 `labhub/server.py` 開頭：

- 各電腦每 2 秒 `POST /api/devices/heartbeat`；
- 節點用 `GET /api/nodes/<名稱>/commands?wait=20` long-poll 取指令；
- 控制端用 `POST /api/nodes/<名稱>/commands?wait=20` 送指令並等回覆；
- 即時資料：節點 `POST …/live/batch`，控制端 `GET …/live?after=<序號>&wait=5`；
- 量測檔：`PUT /api/files/<節點>/<路徑>` 上傳，`GET` 同一路徑下載；
- 儀器登錄：`GET /api/instruments`；共用儀器的歸屬：`POST /api/instruments/claim {key, host}`、`/release`；
- Hub 本身：`GET /api/hub`，更新 `POST /api/hub/update {version}`，上傳安裝 `POST /api/hub/install`（zip），重新啟動 `POST /api/hub/restart`。

節點指令（`POST /api/nodes/<節點>/commands`）另有：`connect_all`、`disconnect_all`、`connect {names, force}`、
`disconnect {names}`、`scan`、`describe {name}`、`get_all {name}`、`get`、`set`、`release {key}`。

Hub 的狀態每 10 秒存到 `data/state.json`，重開後仍記得有哪些電腦與節點。即時資料只存在記憶體，每個節點保留最近 600 批。

## 12. 安全

- 有 Hub token 的人就能在節點上量測、讀寫儀器。token 只發給實驗室成員；外流時，刪掉 Hub 的 `data/token.txt`，重新啟動 Hub，再換新的 token。
- 8765 不要開到外網，從外面用 VPN 連。
- Lab Control 的程式碼與預設設定檔（會發佈到 NAS 的 git）**不包含任何帳號、密碼或 token**。token 只存在各電腦的 `LAB/settings.yaml`。
- 如果 NAS admin 密碼曾經貼在聊天、文件或程式裡，請更換。

## 13. 限制

- 開發環境用本機 Hub 做了完整測試：兩個視窗、節點、即時資料、停止、檔案上傳下載、更新下載。實際 NAS 的 Docker、防火牆與 VPN 請上線後確認。
- 手動步進模式的遠端控制已接上指令，但畫面仍以自動量測為主。
- 節點上開始的量測使用節點的 settings.yaml（Labber 路徑、資料根目錄預設值）。
