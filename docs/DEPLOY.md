# 部署到 NAS

需要能跑 Docker 的 NAS（Synology DSM 7.2 以上 Container Manager，或 QNAP Container Station）。

## 一、第一次安裝

1. 在電腦上產生安裝資料夾：`python tools/make_nas_bundle.py` → `dist/qel-nas/`（或 `dist/qel-nas.zip`）。
2. 放到 NAS，例如 `/volume1/docker/qel/`。
3. **沿用現有資料**（已經在用論文庫與 Hub 的話）：
   - 先在 Container Manager 停止舊的 `paperlib` 與 `LabControlHub` 專案（不要刪資料夾）；
   - 把舊論文庫的 `data/`、`import/` 複製到 `qel/paperlib/data/`、`qel/paperlib/import/`（帳號、站長、論文、標註全部沿用）；
   - 把舊 `LabControlHub/data/` 複製到 `qel/labhub/data/`（量測檔、節點紀錄沿用）。
4. 編輯 `qel/docker-compose.yml`：
   - `LABHUB_TOKEN`（兩處）：與各量測電腦的 Hub token 相同（原本是 `Labqel330`）；
   - `QEL_AGENT_TOKEN`：改成一長串隨機字，抄下來放安全的地方（大程式網站壞掉時救援用）；
   - 數據共用資料夾：`portal` 的 `volumes` 拿掉 `/volume1/ccuqel:/shares/ccuqel:ro` 前面的 `#` 並改成實際路徑，`QEL_DATA_ROOTS` 寫「各電腦看到的路徑前綴＝容器內路徑」，網頁與手機才能下載數據檔；
   - 節點自動更新：`labhub` 的 `/releases` 那行（同原本 Hub 的設定）。
5. Container Manager →「專案」→「新增」→ 路徑選 `qel` 資料夾 →「使用現有的 docker-compose.yml」→ 完成。
   第一次會 build 論文庫映像（幾分鐘）。
6. 防火牆允許 TCP **8090**（大程式）、**8080**（論文庫）、**8765**（Hub）；**8767**（更新代理）只在內網／VPN。
7. 開 `http://<NAS>:8090/` 用論文庫的站長帳號登入 →「管理」：
   - 確認每個人的模塊開關（新帳號預設：論文、讀檔開，量測關）；
   - 「模塊發佈」上傳各桌面模塊（`python tools/package.py` 產生的 `labcomm`、`labcontrol`、`lablogviewer`、`launcher`、`monitor` zip）。

> 論文庫還沒有站長時，先到論文庫「管理 → 使用者與權限」對自己按「設為站長」。

## 二、各電腦

1. 大程式網頁「下載」→ **QEL Lab 大程式** → 下載自己平台的安裝檔：
   - Windows：`QELLab-windows.exe`，直接雙擊（第一次若出現「Windows 已保護您的電腦」，按「其他資訊 → 仍要執行」）；
   - macOS：`QELLab-macos.zip`（Apple 晶片）或 `QELLab-macos-intel.zip`，解壓縮後把 App 拖到「應用程式」，第一次按右鍵 →「打開」。
2. **不需要先安裝 Python**：第一次安裝模塊時，大程式會自動下載可攜版 Python 3.12（約 30 MB，放在 `QELLab/python`，免系統管理員權限）。
   實驗室電腦沒有外網時：把同一個檔案放到 NAS，設定環境變數 `QEL_PYTHON_URL` 指向它；或照舊安裝 Python 3.12。
3. 大程式網址填 `192.168.50.2:8090, 100.114.33.20:8090`（內網、VPN 依序嘗試），用論文庫帳號登入。
4. 安裝要用的模塊；之後有新版會在卡片上出現「更新到 vX」。

量測電腦原本的 `LAB/` 設定資料夾（`settings.yaml`、`instruments.yaml`、方案）完全沿用。原本「Hub 連線設定」也照舊（量測節點仍直接連 Hub）。

### 安裝檔從哪裡來（站長）

NAS 是 Linux，沒辦法直接產生 Windows 的 exe；安裝檔由 GitHub Actions 在 Windows 與 macOS 上自動打包：

1. GitHub → Actions →「build-desktop」→「Run workflow」（改到大程式、監控程式、通信模塊時也會自動執行）。
2. 完成後在那次執行的 Artifacts 下載：`installers-windows`、`installers-macos`、`installers-macos-intel`（安裝檔），
   `module-packages`（各模塊的發佈 zip）。推 `desktop-v1.0.1` 這種標籤時會另外建立 GitHub Release。
3. 大程式網頁「管理 → 模塊發佈」：
   - 「安裝檔」：選大程式／監控程式與平台，上傳 exe 或 zip；
   - 「發佈模塊新版本」：只要選模塊 zip，模塊與版本直接讀 zip 裡的 `module.json`。

也可以在自己的 Windows／Mac 上打包：`pip install pyinstaller PySide6 pillow`，`python tools/build_desktop.py launcher`。

## 三、監控程式（站長）

解壓縮 `monitor` 的 zip，雙擊 `QELMonitor-windows.bat`／`QELMonitor-mac.command`，用站長帳號登入：

- 總覽：各服務狀態、版本、回應時間、流量、異常（會跳系統通知）；
- 服務更新：選服務 → 拖入新版 zip（`tools/package.py portal` 等產生）→「更新」，每一步即時顯示，失敗自動換回；也有重新啟動、重建、停止、啟動、回到備份；
- 模塊發佈、使用者權限、容器日誌。

大程式網站本身打不開時：登入視窗按「緊急模式…」，輸入 `QEL_AGENT_TOKEN`，可以重新啟動或回到備份。

## 四、從外網連線（不用 VPN）

沿用論文庫 README「從外網連線」的 Cloudflare Tunnel 做法，多加一個 Public Hostname：

| 網址 | Service |
|---|---|
| `lab.你的網域` | `http://qel-portal:8090` |
| `papers.你的網域` | `http://paperlib:8080` |

再設定 `PAPERLIB_PUBLIC_URL: https://papers.你的網域`、`QEL_COOKIE_DOMAIN: .你的網域`（論文庫與大程式共用登入）。
**Hub（8765）與更新代理（8767）不要開到外網**；遠端量測一律經大程式網站轉送。

## 五、環境變數

| 變數 | 服務 | 預設 | 說明 |
|---|---|---|---|
| `QEL_PORTAL_DATA` | portal | `./portal-data` | 資料庫（`portal.db`）、模塊發佈檔 |
| `QEL_PORTAL_PORT` | portal | 8090 | |
| `QEL_SITE_NAME` | portal | QEL Lab | |
| `PAPERLIB_URL` | portal | `http://127.0.0.1:8080` | 容器之間連論文庫 |
| `PAPERLIB_PUBLIC_URL` | portal | 空白 | 給瀏覽器的論文庫網址（論文連結） |
| `LABHUB_URL`、`LABHUB_TOKEN` | portal | | 連 Hub（token 只在 NAS 上） |
| `QEL_AGENT_URL` | portal | 空白 | 健康檢查顯示更新代理 |
| `QEL_DATA_ROOTS` | portal | 空白 | `前綴=容器路徑;…`，網頁下載數據檔用 |
| `QEL_SESSION_DAYS` | portal | 30 | 登入保持天數 |
| `QEL_COOKIE_DOMAIN`、`QEL_SSO`、`QEL_SECURE_COOKIE` | portal | | 單一登入與 cookie |
| `QEL_RECHECK_S` | portal | 600 | 每隔幾秒向論文庫確認帳號仍有效 |
| `QEL_STACK_DIR` | agent | `/stack` | NAS 上的 qel 資料夾 |
| `QEL_AGENT_TOKEN` | agent | 空白 | 緊急 token |
| `QEL_PORTAL_URL` | agent | `http://qel-portal:8090` | 驗證站長登入 |
| `QEL_AGENT_SERVICES` | agent | 空白 | JSON 檔，覆寫或新增服務（容器名稱、程式資料夾、確認網址） |
| `QEL_AGENT_VERIFY_S` | agent | 90 | 啟動後等服務回應的秒數 |

## 六、備份

- `portal/data/portal.db`：權限、共用標籤、數據登錄、模塊發佈紀錄；`portal/data/releases/`：發佈的 zip。
- `paperlib/data/`：論文庫（論文庫自己的備份功能照用）。
- `labhub/data/`：量測檔。
- `backups/`：更新代理每次更新前的程式備份（每個服務保留 20 份）。
