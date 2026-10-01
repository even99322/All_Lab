# Lab Control Hub：安裝在 NAS

Hub 是一個小網站，實驗室裡只要一個。它負責：

- **監控**：網頁 `http://<NAS>:8765/` 列出所有開著 Lab Control 的電腦，包括量測節點和控制端的狀態、版本、量測進度。可以從網頁暫停或停止量測。
- **指令中轉**：控制端送出的開始、停止、讀寫儀器、更新，都經由 Hub 送到量測節點。
- **數據中轉**：量測中的即時曲線經由 Hub 送到控制端。量測結束後，資料檔會上傳到 Hub（存在 NAS），控制端自動下載，網頁上也可以下載。
- **發佈**：量測節點更新時，從 Hub 下載新版（讀 Lab APP 的發佈資料夾）。

每台電腦都是**主動連到 Hub**，Hub 不需要連回各電腦，所以內網和 VPN 都能用。Hub 只用 Python 標準函式庫，不需要安裝任何套件。

## 資料夾內容

```
LabControlHub/
  docker-compose.yml   Docker 設定（官方 python:3.12-slim 映像，不需要 build）
  labhub/              Hub 程式
  run_hub.bat          沒有 Docker 時，在 Windows 電腦上執行
  run_hub.sh           沒有 Docker 時，在 Linux / macOS 上執行
  data/                Hub 的資料：state.json、files/（量測檔）
  backups/             Hub 控制台每次更新前的備份
```

## A. Synology（Container Manager）

1. 把整個 `LabControlHub` 資料夾放到 NAS，例如 `/volume1/docker/LabControlHub/`。
2. 確認 `LabControlHub/data/` 資料夾存在（zip 裡已附；Synology 不會自動建立掛載的資料夾，缺少時會出現 `Bind mount failed: ... does not exist`）。
   要讓節點自動更新時，在 `docker-compose.yml` 拿掉 `/releases` 那行開頭的 `#`，並把路徑改成 Lab APP 發佈資料夾的實際位置
   （裡面要有 `LabControl/v0.0.7/` 這樣的子資料夾，資料夾必須已存在）。
3. 開啟 Container Manager，選「專案」→「新增」：
   - 路徑選 `LabControlHub` 資料夾；
   - 來源選「使用現有的 docker-compose.yml」；
   - 按「下一步」，再按「完成」。
4. 到「控制台」→「安全性」→「防火牆」，允許 TCP **8765** 與 **8766**（如果防火牆有開）。
5. token：`docker-compose.yml` 的 `LABHUB_TOKEN`，預設是 `Labqel330`（Lab Control 與 Monitor 的預設值相同，不用另外設定）。
   要換 token 時，改 `docker-compose.yml` 的兩個 `LABHUB_TOKEN`，重新建置專案，再改各電腦的「Hub 連線設定」。

QNAP（Container Station → 應用程式 → 建立）和 ASUSTOR（Portainer → Stacks）一樣用這個 `docker-compose.yml`。Volume 路徑改成該 NAS 的共用資料夾路徑即可。

## B. 沒有 Docker：放在一台常開的電腦

需要 Python 3.8 以上，不需要其他套件：

- Windows：雙擊 `run_hub.bat`（發佈資料夾路徑在檔案內修改）。
- Linux / macOS：`LABHUB_RELEASES=/path/to/Releases ./run_hub.sh`。
- 有裝 Lab Control 的電腦也可以用：`python -m labcontrol hub`。資料會存在 `LAB/hub/`。

這種情況下，各電腦的 Hub 網址要填**這台電腦**的 IP。

## 更新 Hub（Hub 控制台，0.0.9 起）

`docker-compose.yml` 有兩個容器：

- `labcontrol-hub`：Hub 網站（port 8765）；
- `labcontrol-hub-agent`：**控制代理**（port 8766）。它負責停止、備份、換程式、重建、啟動 Hub，並確認新版本真的起來了。

以後的更新流程：

1. 下載新版 `LabControlHub_v*.zip`，**不用解壓縮**。
2. 打開控制台：Lab Control Monitor 的「Hub 控制台」分頁，或瀏覽器開 `http://192.168.50.2:8766/`（token 登入）。
3. 選 zip（或拖進來）。
4. 按「更新網站」。

每一步都會即時顯示：檢查 zip → 停止 → 備份 → 換程式 → 重建 → 啟動 → 確認版本。
新版沒有正常啟動時，會自動換回舊版。控制台也有這些按鈕：

- 「完全重建」：重新下載 python 映像並重建容器；
- 「重新啟動網站」「停止」「啟動」；
- 「回到這版」：從備份還原。

備份存在 `LabControlHub/backups/`，每次更新前自動建立。

### 第一次：加入控制代理（從 0.0.7 / 0.0.8 升級，只需要一次）

0.0.8 以前的專案只有 `labcontrol-hub` 一個容器，要先把控制代理加進去：

1. 解壓縮 `LabControlHub_v0.0.9.zip`，把裡面的 `labhub/`、`backups/`、`docker-compose.yml` 複製到 NAS 的 `LabControlHub` 資料夾（`\\100.114.33.20\docker\LabControlHub\`），覆蓋舊的。`data/` 保留不動。
2. 如果你之前在舊的 `docker-compose.yml` 拿掉了 `/releases` 那行的 `#`，新檔也要同樣改。
3. Container Manager →「專案」→ 選 LabControlHub →「動作」→「停止」→「清除」，**不要刪除資料夾**。
   接著「新增」專案，路徑選同一個 `LabControlHub` 資料夾，來源選「使用現有的 docker-compose.yml」，然後完成。
   另一種做法：在專案的「YAML 設定」貼上新的內容 →「儲存」→「建置」。
4. 確認「容器」裡有 `labcontrol-hub` 和 `labcontrol-hub-agent` 兩個都在執行。
   防火牆有開的話，允許 TCP **8766**。
5. 打開 Monitor →「Hub 控制台」，應該看得到「網站狀態：執行中」和版本。

之後就都在控制台更新，不用再到 NAS 操作。

> 控制代理掛載了 `/var/run/docker.sock`，才能停止 / 啟動 Hub 容器。它只操作名稱為 `labcontrol-hub` 的容器（`HUB_CONTAINER`），而且所有操作都要 token。
> 8766 和 8765 一樣，不要開到外網。

0.0.8 的「從發佈資料夾更新 Hub」「安裝這個程式內附的 Hub」已經由控制台取代。Monitor 的控制台也有「用這個程式內附的 Hub」，不用另外下載 zip。

## 各電腦的設定（每台 Lab Control 一次）

1. 在主視窗選「設定 ▾」→「Hub 連線設定…」。
2. 填入以下兩項，按「測試連線」確認：
   - Hub 網址：預設 `192.168.50.2, 100.114.33.20`（內網、VPN 依序嘗試）；
   - token：`Labqel330`（預設值已經填好）。
3. 按「儲存」。設定會寫進 `LAB/settings.yaml` 的 `remote.hub_urls` 和 `remote.token`。
4. 狀態列會顯示「Hub：✔ 192.168.50.2:8765」。

「設定 ▾ → 開啟 Hub 監控網頁」會自動帶 token 登入。手機或其他電腦也可以直接開 `http://192.168.50.2:8765/`，輸入 token 登入。

## 安全

- token 是實驗室共用的 Hub 存取碼，**不是 NAS 帳號密碼**。Hub 不需要、也不會使用 NAS 帳號。
- 有 token 的人可以在節點上量測、讀寫儀器，也可以用控制台停止 Hub。token 外流時，改 `docker-compose.yml` 的 `LABHUB_TOKEN`，重新建置專案，再把新的 token 發給大家。
- token 寫在 `docker-compose.yml` 和程式的預設值裡，所有拿到 Lab Control 的人都看得到，**不要和 NAS 或其他帳號用同一組密碼**。
- 不要把 8765 開到外網（路由器不要做 port forwarding）。從外面用 VPN 連。
- 需要更嚴格限制時，可以在量測電腦的 `settings.yaml` 設 `remote.node.allowed_users`。停止和暫停不受限制。

## 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `LABHUB_PORT` | 8765 | 埠號 |
| `LABHUB_DATA` | `./labhub-data` | 狀態、token、上傳的量測檔 |
| `LABHUB_RELEASES` | （無） | Lab APP 發佈資料夾（含 `LabControl/v<版本>/`） |
| `LABHUB_TOKEN` | `Labqel330`（compose） | 存取 token；沒設定時自動產生並存在 `data/token.txt` |
| `LABHUB_AUTH` | on | `off`＝不檢查 token（只在完全封閉的網路使用） |
| `LABHUB_OFFLINE_S` | 15 | 超過幾秒沒有回報就視為離線 |
| `LABHUB_KEEP_BATCHES` | 600 | 每個節點保留的即時資料批次數 |
| `LABHUB_LOG` | INFO | 記錄等級（DEBUG 會記錄每個請求） |

控制代理（`labcontrol-hub-agent`）：

| 變數 | 預設 | 說明 |
|---|---|---|
| `HUB_DIR` | `/hubdir` | 掛載的 LabControlHub 資料夾 |
| `HUB_CONTAINER` | `labcontrol-hub` | 要控制的容器名稱 |
| `HUB_URL` | `http://labcontrol-hub:8765` | 確認版本時連的 Hub 網址 |
| `DOCKER_HOST` | `unix:///var/run/docker.sock` | Docker Engine API |
| `AGENT_VERIFY_S` | 60 | 啟動後等 Hub 回應的秒數 |

## 疑難排解

| 狀況 | 檢查 |
|---|---|
| Lab Control 顯示「連不到 Hub」 | 在那台電腦的瀏覽器開 `http://192.168.50.2:8765/api/ping`。打不開表示網路或 NAS 防火牆擋住，或容器沒在執行 |
| 「token 不正確」 | 各電腦的 token 要和 `docker-compose.yml` 的 `LABHUB_TOKEN` 相同 |
| Monitor「Hub 控制台」連不到控制代理 | 確認 `labcontrol-hub-agent` 容器在執行、防火牆允許 8766；還沒加入控制代理時照上面「第一次」的步驟 |
| 控制台「停止」失敗：permission denied | 控制代理需要掛載 `/var/run/docker.sock`（compose 已設定）；確認專案是用新的 compose 建立的 |
| 網頁看得到電腦，但「執行於」沒有節點 | 量測電腦要按「🛰 量測節點」，而且要從**另一台**電腦看。自己這台不會列出自己 |
| 建置時 `Bind mount failed: '…/data' does not exist` | 在 File Station 於 `LabControlHub` 裡建立 `data` 資料夾，再重新建置；`/releases` 那行的路徑也必須存在 |
| 節點無法更新 | 網頁「Hub 上的最新發佈」沒有版本時，表示 `LABHUB_RELEASES` 的路徑不對 |
| 其他問題 | 各電腦的 `LAB/logs/labcontrol.log`，以及 Hub 的容器日誌 |
