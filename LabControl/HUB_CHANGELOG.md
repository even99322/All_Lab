# Lab Control Hub 更新紀錄

1.0.36 起 Hub 跟 Lab Control 分開發佈（GitHub 發佈 `hub-v<版本>`）；1.0.35 以前的紀錄在 [CHANGELOG.md](CHANGELOG.md)。
每一版的說明在 [docs/releases/hub/](docs/releases/hub/)。

## 1.0.46 — 2026-11-17

網路用量新增「流量分析」（每小時、各設備、華碩 Traffic Analyzer 的 App）與「設備登記」（沒備註的手機 / 電腦寬限後停網，掃 QR 碼登記）。詳見 [docs/releases/hub/v1.0.46.md](docs/releases/hub/v1.0.46.md)。

## 1.0.45 — 2026-11-17

流量監看回報的回應帶最新版本，流量監看 1.0.8 起看到新版就馬上自己更新。詳見 [docs/releases/hub/v1.0.45.md](docs/releases/hub/v1.0.45.md)。

## 1.0.44 — 2026-11-17

別間實驗室網段不同（如 192.168.10.x）時依路由器位址自動認得；站長可以從「量測設置」移除離線的量測電腦。詳見 [docs/releases/hub/v1.0.44.md](docs/releases/hub/v1.0.44.md)。

## 1.0.43 — 2026-11-16

網路用量改版：上方切換內網連線／VPN 連接、各設備一張表（使用類型、改名、站長可封鎖上網）、其他頁收進分頁；可監看另一間實驗室（華碩路由器，經 VPN 代讀）；提醒沒裝流量監看的電腦。詳見 [docs/releases/hub/v1.0.43.md](docs/releases/hub/v1.0.43.md)。

## 1.0.42 — 2026-11-14

新增「論文匯出」頁（/gptexport）：依 CCU Magnonics 規範 1.1 把論文庫匯出成 GPT 用的資料夾（CSV、manifest、PDF、筆記），並可逐篇設定主要主題與受控標籤。詳見 [docs/releases/hub/v1.0.42.md](docs/releases/hub/v1.0.42.md)。

## 1.0.41 — 2026-11-13

網路用量新增「NAS 上誰在用網路」：NAS 每張網卡、每個容器、每個程式與連線對方的收送量（記帳容器讀 /proc 與連線追蹤）。詳見 [docs/releases/hub/v1.0.41.md](docs/releases/hub/v1.0.41.md)。

## 1.0.40 — 2026-11-13

網路用量：列出路由器上連著的每個裝置（有線 / 無線 / 訪客）並標出沒被監看的；即時頁新增「監看不到」＝路由器 − 已知各裝置的外網量。詳見 [docs/releases/hub/v1.0.40.md](docs/releases/hub/v1.0.40.md)。

## 1.0.39 — 2026-11-13

新增「版本紀錄」頁（/versions）：各程式每一版的內容、發佈時間、上線到這台 Hub 的時間，可搜尋。詳見 [docs/releases/hub/v1.0.39.md](docs/releases/hub/v1.0.39.md)。

## 1.0.38 — 2026-11-13

每台電腦的流量分成內網／外網／VPN（流量監看 1.0.4 回報）；回報時給 https 網域，讓流量監看在內網把它指到 NAS（沒開 VPN 也能用 https 網址）。詳見 [docs/releases/hub/v1.0.38.md](docs/releases/hub/v1.0.38.md)。

## 1.0.37 — 2026-11-13

NAS 流量分流（區網 / VPN / 外網直接；控制代理自動建立只計數的 labcontrol-netacct 容器）；每台電腦的流量分成跟 NAS 之間、其中經 VPN、其餘（多半外網）。詳見 [docs/releases/hub/v1.0.37.md](docs/releases/hub/v1.0.37.md)。

## 1.0.36 — 2026-11-13

Hub 改成自己發佈（`hub-v*`），控制台「從 GitHub 取得新版」多看 Hub 的發佈；Lab Control 的發佈不再附 Hub。詳見 [docs/releases/hub/v1.0.36.md](docs/releases/hub/v1.0.36.md)。
