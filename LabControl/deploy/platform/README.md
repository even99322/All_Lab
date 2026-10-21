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

## 加上內網 https（1.0.15，只做一次）

目標：實驗室裡不開 VPN 也能用 https，而且跟 VPN 是**同一個網址**：

| | 網址 |
|---|---|
| 平台（Hub） | `https://<NAS>.<tailnet>.ts.net:8443` |
| 論文庫 | `https://<NAS>.<tailnet>.ts.net:8444` |

`<NAS>.<tailnet>.ts.net` 是 Tailscale 給 NAS 的名稱（Tailscale 管理頁 Machines 看得到；Hub 控制台「內網 https」也會顯示）。
Hub 會自己向 NAS 上的 Tailscale 取憑證、到期前自動換新，控制台顯示還剩幾天。

1. **更新 Hub 到 1.0.15**（控制台「從 GitHub 取得新版」或丟 `LabControlHub_v1.0.15.zip`）。
2. **docker-compose**：把這個資料夾 `docker-compose.yml` 裡 `labhub:` 新增的三行抄到 `docker/QELPlatform/docker-compose.yml`：
   ```yaml
       ports:
         - "8765:8765"
         - "192.168.50.2:8443:8443"
         - "192.168.50.2:8444:8444"
       volumes:
         - /var/packages/Tailscale/var:/tailscale:ro     # Tailscale 套件的 socket（DSM 7）
   ```
   Container Manager → 專案 `qelplatform` →「停止」→「YAML 設定」貼上 → 儲存 →「建置」（或「啟動」）。
   打開控制台「內網 https」：應該是「剩 xx 天」、Hub :8443 ✔、論文庫 :8444 ✔。
   - 來源顯示「還沒設定」→ socket 不在 `/var/packages/Tailscale/var/tailscaled.sock`：用 SSH `ls /var/packages/Tailscale/var/` 確認位置。
   - NAS 防火牆有開的話：允許內網 TCP 8443、8444（以及下面 DNS 的 53）。
3. **VPN 那邊也開 8444**（論文庫在 VPN 上也用同一個網址；原本的 443 留著，舊書籤照用）。SSH 到 NAS：
   ```sh
   sudo tailscale serve --bg --https=8444 http://127.0.0.1:8080
   sudo tailscale serve status          # 應該有 :443、:8443、:8444
   ```
   （找不到 `tailscale` 指令時用 `/var/packages/Tailscale/target/bin/tailscale`。）
4. **實驗室 DNS：把 ts.net 網址指到 192.168.50.2**（只有這個名稱，其他網址照常）。用 NAS 的 DNS Server 套件：
   1. 套件中心安裝「DNS Server」。
   2. DNS Server →「區域」→「新增」→「主要區域」：網域類型「正向區域」、網域名稱 `<NAS>.<tailnet>.ts.net`、主要 DNS 伺服器 `192.168.50.2` → 確定。
   3. 選這個區域 →「編輯」→「資源記錄」→「新增」→「A 類型」：名稱**留空**（就是網域本身）、IP `192.168.50.2`、TTL 預設 → 確定。
   4. 「解析服務」：勾「啟用解析服務」與「啟用轉發器」，轉發器填**原本上游的 DNS**——學校 / 公司網路一定要填學校的 DNS
      （路由器 ADVANCED Home 的 Internet Port「Domain Name Server」看得到；校內才查得到的網址、校方擋掉外部 DNS 時都靠它），
      家用網路才用 `8.8.8.8`、`1.1.1.1` → 套用。改路由器之前，先把一台電腦的 DNS 手動設成 192.168.50.2，校內網站、一般網站、ts.net 都試過再改。
      **不要填路由器 192.168.50.1**：下一步路由器會把查詢轉給 NAS，兩邊互轉會變成迴圈。
   5. 路由器：讓大家的查詢經過 NAS。
      - **NETGEAR（例如 Nighthawk RAX200）**：DHCP 一律把路由器 192.168.50.1 當 DNS 發給裝置、不能改，所以改的是路由器自己往外查的 DNS：
        ADVANCED → Setup → **Internet Setup** →「Domain Name Server (DNS) Address」選「Use These DNS Servers」→
        Primary DNS `192.168.50.2`、Secondary DNS **留空** → Apply（網路會斷幾秒）。LAN Setup 那頁不用動。
      - ASUS 等可以設定 DHCP 發的 DNS 的路由器：區域網路 → DHCP 伺服器 →「DNS 伺服器 1」`192.168.50.2`、「DNS 伺服器 2」留空、
        「通告路由器 IP 為 DNS」選「否」。
      Secondary / DNS 2 不要填別的：別的 DNS 查不到 ts.net 名稱，裝置有時候會打不開。
      注意：之後 NAS 關機時整個實驗室查不到網址（恢復方法：把 DNS 改回自動取得）。
   6. NETGEAR 那種做法裝置的 DNS 本來就是路由器，不用重新連線，最多等幾分鐘快取過期；ASUS 那種要重新連一次 Wi-Fi / 網路線。

   路由器本身能加自訂 DNS 記錄（例如 Asuswrt-Merlin 的 dnsmasq：`address=/<NAS>.<tailnet>.ts.net/192.168.50.2`）時，也可以只在路由器加、不用 DNS Server 套件。
5. **確認**：在沒開 VPN 的電腦上
   - `nslookup <NAS>.<tailnet>.ts.net` → `192.168.50.2`；
   - 開 `https://<NAS>.<tailnet>.ts.net:8443`，網址列是鎖頭、不會跳警告；
   - 控制台「內網 https」兩個網址後面是「這台裝置：✔ 打得開」。之後大家改用這個網址（App 也用這個網址重裝一次）。

打不開時：iPhone「設定 → Wi-Fi → 這個網路 →（i）→ 限制 IP 位址追蹤」與 iCloud 私密轉送、Android「私人 DNS」、瀏覽器「安全 DNS」會繞過實驗室 DNS，
在實驗室 Wi-Fi 關掉；或在那台裝置開 Tailscale（走 VPN 一樣是這個網址）。

**憑證監測**：Tailscale 憑證 90 天，剩 30 天左右它會自己續約；Hub 每 12 小時取一次，換新會自動套用。
剩 20 天以內控制台、系統狀態與站長首頁會警告——通常是 NAS 上的 Tailscale 停了或登出，到套件中心確認 Tailscale 在執行、已登入，
再按控制台「立即檢查／取新憑證」。

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
