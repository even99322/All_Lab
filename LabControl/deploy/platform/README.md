# 量子工程平台：NAS 安裝（一個專案、四個容器）

| 容器 | 是什麼 | 網址 |
|---|---|---|
| `paperlib` | 論文庫 | `http://192.168.50.2:8080` |
| `labcontrol-hub` | Hub：平台入口、量測中繼 | `http://192.168.50.2:8765` |
| `labcontrol-hub-agent` | 控制代理：更新 Hub 與論文庫 | `:8766`（Monitor 用） |
| `qel-indexer` | 數據索引（1.0.4）：先算好數據集散地每個檔的資訊與預覽 | —（Hub `/api/index/status` 看進度） |

手機看即時量測：`http://192.168.50.2:8765/m`（VPN `http://100.114.33.20:8765/m`），論文庫帳號登入。

三個容器在同一個專案 `qelplatform`、同一個網路。資料留在原本的資料夾：

```
docker/
  QELPlatform/docker-compose.yml   ← 這個專案（只有這個檔）
  paperlib/                        ← 論文庫程式＋data/（原本的資料夾）
  LabControlHub/                   ← Hub 程式 labhub/＋data/＋backups/（原本的資料夾）
```

## 從原本兩個專案改成一個（只做一次）

1. **備份**：把 `docker\paperlib\data\library.db` 複製一份到別的地方。
2. **放 Hub 新程式**：解壓 `LabControlHub_v*.zip`，把裡面的 `labhub` 資料夾覆蓋到 `docker\LabControlHub\labhub`（`data` 不動）。
3. **停掉舊的兩個專案**：Container Manager → 專案：
   - `paperlib` →「停止」→「動作 ▾」→「清除」；
   - `LabControlHub` →「停止」→「動作 ▾」→「清除」。

   「清除」只刪除專案與容器，**資料夾和資料都還在**。到「容器」頁確認沒有 `paperlib`、`labcontrol-hub`、`labcontrol-hub-agent`。
4. **建新專案**：
   - 在 File Station 建立 `docker/QELPlatform`，把這個 `docker-compose.yml` 放進去；
   - 原本論文庫 compose 的 `environment` 有其他設定（例如 AI 金鑰、Cloudflare）時，抄到新檔的 `paperlib:` 段；
   - Container Manager → 專案 →「新增」：名稱 `qelplatform`、路徑 `docker/QELPlatform`、來源「使用現有的 docker-compose.yml」→ 完成。
     第一次會建置論文庫的映像，約 5–10 分鐘。
5. **確認**：
   - `http://192.168.50.2:8080` 論文庫正常、論文都在；
   - `http://192.168.50.2:8765` 用論文庫帳號登入，看到「量子工程平台」；
   - 「容器」頁三個都在執行。

## 加上數據索引（1.0.4，只做一次）

1. 先更新 Hub 到 1.0.4（發佈中心丟 `LabControlHub_v1.0.4.zip`）。
2. 在數據集散地裡建資料夾 `.qel`（Windows：`\\192.168.50.2\ccuqel\數據集散地` → 新增資料夾 → `.qel`）。索引程式把存取鑰匙放在這裡，
   讀得到數據集散地的電腦就能向 Hub 拿索引。
3. 用這個資料夾的 `docker-compose.yml` 換掉 `docker/QELPlatform/docker-compose.yml`（之前改過的環境變數抄過去）；
   數據集散地不在 `/volume1/ccuqel/數據集散地` 時改 `indexer:` 的兩行路徑。
4. Container Manager → 專案 `qelplatform` →「建置」。`qel-indexer` 第一次啟動會安裝 numpy、h5py（約 1–2 分鐘，NAS 要能上網），
   接著開始索引：新的檔先做；幾千個檔第一次要一段時間，之後每 5 分鐘只做新增 / 改過的。
5. 看進度：登入 Hub 後開 `http://192.168.50.2:8765/api/index/status`（`todo`、`done`、`state`）。

## 之後的更新

全部在 Monitor「發佈中心」：把 zip 拖進去 →「▶ 全部發佈」。

- 論文庫：完整包或部分更新包（例如 `paperlib_1.7.0_…_update.zip`）都可以。
  - 控制代理先換好程式、建好新映像（舊版照常服務），再停止 → 備份資料庫 → 啟動新版 → 確認版本；
  - 失敗時程式和資料庫都會換回。
- Hub、各模塊：同上，丟 zip 即可。

只有改 `docker-compose.yml`（例如新增環境變數）時，才需要到 Container Manager 按「建置」。

## 疑難排解

| 狀況 | 處理 |
|---|---|
| 建置失敗 `Bind mount failed … does not exist` | compose 裡的路徑不存在：確認 docker 共用資料夾在 `/volume1`（File Station → 內容），或改成實際路徑 |
| `container name "/paperlib" is already in use` | 舊專案沒清除乾淨：到「容器」頁刪除舊的 `paperlib` 容器（資料在資料夾裡，不會刪掉） |
| `qel-indexer` 一直重新啟動、紀錄有 `Bind mount failed …/.qel` | 數據集散地裡還沒有 `.qel` 資料夾，或數據集散地路徑不同 |
| `/api/index/status` 回「NAS 上還沒有數據索引」 | `qel-indexer` 沒在執行或還在安裝套件：看容器紀錄 |
| Hub 登入顯示「連不到論文庫」 | `paperlib` 容器沒在執行，或不在同一個專案 |
