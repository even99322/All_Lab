# QEL Lab 大程式

把實驗室現有的程式（release 0.0.0：論文庫、Lab Control 量測、Lab Control Hub／Monitor、LabLogViewer 讀檔）整合成一個平台：

- **一個帳號**：直接用論文庫（paperlib）的帳號登入；站長＝論文庫的站長，可以開關每個人能用哪些模塊。
- **一個 NAS 中繼站**：所有資料輸出輸入、量測指令都經過 NAS 上的大程式網站（再轉給論文庫與 Lab Control Hub）。
- **同一批標籤**：量測、讀檔、論文模塊共用；每個標籤可以連到論文，數據旁邊直接看到對應論文、點一下開論文庫。
- **量測 ↔ 讀檔**：量測存好的數據直接交給讀檔模塊開啟；讀檔模塊把數據檔拖到量測模塊，就套用那次的量測設置。
- **模塊獨立**：每個模塊各自版本、各自更新、各自的 Python 環境；模塊之間只透過**通信模塊**（labcomm）溝通。
- **全平台**：網頁（電腦、Android、iPhone／iPad，可安裝成 App）＋ Windows／macOS 桌面大程式。
- **獨立的監控程式**：監控大程式網站與 NAS 服務、異常通知、更新／回復各服務、發佈模塊新版。

```
          電腦（Windows / macOS）                                 手機、平板、任何瀏覽器
 ┌───────────────────────────────────────┐                 ┌──────────────────────────┐
 │ QEL Lab 大程式（launcher）            │                 │ 大程式網頁（PWA）         │
 │  ├ 量測模塊 Lab Control ─┐            │                 │ 數據・標籤・論文・量測狀態 │
 │  ├ 讀檔模塊 LabLogViewer ┼ labcomm ───┼──┐              └────────────┬─────────────┘
 │  └ 通信模塊 labcomm（本機傳遞）        │  │                           │
 └───────────────────────────────────────┘  │  HTTP（使用者登入）         │
                                            ▼                            ▼
 ┌──────────────────────────── NAS（Docker） ─────────────────────────────────────┐
 │  大程式網站 qel-portal :8090  ── 帳號（轉論文庫）、權限、共用標籤、數據登錄、事件、  │
 │       │                         模塊發佈、量測中繼（代填 Hub token）、網頁          │
 │       ├─▶ 論文庫 paperlib :8080        （帳號、站長、論文、標籤）                  │
 │       └─▶ Lab Control Hub :8765        （量測指令、即時資料、量測檔）◀── 量測節點   │
 │  更新代理 qel-agent :8767 ── 停止／備份／換程式／重建／確認／自動回復各服務          │
 └────────────────────────────────────────────────────────────────────────────────┘
          ▲
 QEL Lab 監控程式（站長，Windows / macOS）
```

## 資料夾

| 資料夾 | 內容 |
|---|---|
| `comm/` | **通信模塊 labcomm**：大程式 API 用戶端、共用標籤、本機傳遞、數據檔↔量測設置（只用標準函式庫） |
| `portal/` | **大程式網站**（NAS）：登入、權限、標籤、數據、事件、中繼、發佈、網頁（只用標準函式庫） |
| `agent/` | **更新代理**（NAS）：各服務的更新、備份、回復、容器日誌 |
| `launcher/` | **桌面大程式**（Windows／macOS）：登入、安裝／更新／開啟模塊、轉交動作 |
| `monitor/` | **監控程式**（站長，Windows／macOS）：狀態、異常通知、服務更新、模塊發佈、使用者權限 |
| `modules/paperlib/` | 論文模塊（原 LAB-QEL 論文庫 1.5.1，未修改） |
| `modules/labcontrol/` | 量測模塊（原 Lab Control 0.0.12，含 Hub 與 Monitor；加上大程式整合） |
| `modules/lablogviewer/` | 數據讀取模擬模塊（原 LabLogViewer 1.0.3；加上大程式整合） |
| `deploy/` | NAS 的 `docker-compose.yml` |
| `tools/` | `package.py`（打包各模塊）、`make_nas_bundle.py`（NAS 第一次安裝）、`dev_stack.py`（本機開發） |
| `docs/` | 架構、通信協定、部署、模塊規範 |

## 快速開始

**NAS（第一次）**：`python tools/make_nas_bundle.py` → 把 `dist/qel-nas/` 放到 NAS → 照 [docs/DEPLOY.md](docs/DEPLOY.md)。
已經在用的論文庫與 Hub 資料（帳號、論文、量測檔）直接沿用。

**電腦**：大程式網頁「下載」→ QEL Lab 大程式 → 解壓縮 → 雙擊 `QELLab-windows.bat`（Windows）或 `QELLab-mac.command`（macOS）→ 用論文庫帳號登入 → 安裝要用的模塊。

**手機、平板**：瀏覽器開 `http://<NAS>:8090/` → 登入 → 加到主畫面。

**站長**：網頁「管理」或監控程式（`monitor/QELMonitor-*.{bat,command}`）開關每個人的模塊權限、發佈新版、更新 NAS 服務。

**開發**：

```bash
pip install -r modules/paperlib/requirements.txt PySide6 h5py pytest
python tools/dev_stack.py --demo        # 論文庫 :8080、Hub :8765、大程式 :8090（站長 boss / bosspass1）
python -m pytest tests comm/tests       # 平台：真的論文庫 + Hub + 大程式 + 更新代理 + 桌面程式
(cd modules/labcontrol && QT_QPA_PLATFORM=offscreen python -m pytest)
(cd modules/lablogviewer && QT_QPA_PLATFORM=offscreen python -m pytest)
```

## 文件

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：架構、資料流程（量測→讀檔、讀檔→量測、標籤→論文）、權限、安全
- [docs/PROTOCOL.md](docs/PROTOCOL.md)：通信模塊與大程式 API（所有模塊共用的規格）
- [docs/DEPLOY.md](docs/DEPLOY.md)：NAS 部署、從現有論文庫與 Hub 搬過來、外網、更新
- [docs/MODULES.md](docs/MODULES.md)：模塊規範（module.json、發佈、獨立更新、新增模塊）
