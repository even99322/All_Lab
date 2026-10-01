# 通信協定（labcomm／大程式 API v1）

所有模塊只透過這份規格溝通。Python 模塊直接用 `labcomm`；其他語言照下面的 HTTP 規格即可。
協定版本 `PROTOCOL = 1`：只在不相容時遞增；新增欄位、新增 API 不算不相容（用戶端要忽略不認得的欄位）。

## 一、連線與登入

- 網址：`QEL_PORTAL_URL`（可多個，逗號分隔，依序嘗試，例如內網、VPN）。預設 `http://192.168.50.2:8090, http://100.114.33.20:8090`。
- token：桌面大程式啟動模塊時帶 `QEL_TOKEN`，也寫在 `<QEL_HOME>/session.json`（只有本人可讀）。
- 標頭：`Authorization: Bearer <token>`、`X-QEL: 1`、`X-QEL-Protocol: 1`。
- 錯誤：`{"ok": false, "error": "可以直接顯示的中文訊息"}`；401＝沒登入或已失效、403＝沒有權限（站長沒開放）。

```python
import labcomm
c = labcomm.connect()                  # 用目前的登入狀態
c.datasets(tag="BIC")
c.papers_for_tags(["BIC", "Mirror"])
```

## 二、HTTP API（前綴 `/api/v1`）

| 方法 | 路徑 | 權限 | 說明 |
|---|---|---|---|
| GET | `/ping` | 公開 | `{server, version, protocol}` |
| GET | `/site` | 公開 | 網站名稱、論文庫網址、目前使用者、標籤分類 |
| POST | `/auth/login` | 公開 | `{username, password, code?, client}` → `{token, user}`（`client` 以 `web` 開頭時改設 cookie）；要兩步驟驗證時回 `{need_2fa: true}` |
| POST | `/auth/logout` | 登入 | |
| POST | `/auth/register` | 公開 | 轉給論文庫的註冊申請 |
| POST | `/sso/ticket` | 登入 | 一次性登入票 `{ticket, url}`；瀏覽器開 `url&next=<路徑或論文庫網址>` |
| GET | `/me` | 登入 | 使用者、可用模塊、每個模塊的開關 |
| GET | `/modules` | 登入 | 模塊清單（`allowed`、`latest`） |
| GET | `/modules/<id>/releases` | 該模塊 | 版本清單（`sha256`、說明） |
| GET | `/modules/<id>/releases/<ver>.zip` | 該模塊 | 下載 |
| PUT | `/modules/<id>/releases/<ver>?notes=` | 站長 | body＝zip（根目錄或第一層要有 `module.json`，id、版本一致） |
| DELETE | `/modules/<id>/releases/<ver>` | 站長 | 撤回 |
| GET | `/tags` | 登入 | `{categories, tags:[{name, category, color, description, aliases, papers, paper_count, datasets}]}` |
| POST | `/tags` | 登入 | 新增標籤；修改分類等只有站長或建立的人 |
| POST | `/tags/<name>/rename` | 站長 | 改名（數據上的標籤一起改，舊名變別名） |
| DELETE | `/tags/<name>` | 站長 | |
| GET | `/tags/<name>/papers` | 論文模塊 | 手動連結＋論文庫同名標籤的論文（含 `url`） |
| PUT / POST / DELETE | `/tags/<name>/papers[/<pid>]` | 論文模塊 | 設定／加入／取消連結 |
| POST | `/datasets` | 量測或讀檔 | 登錄數據 `{name, path, fingerprint, tags?, scheme?, source, hub_file?, meta}`；同 fingerprint 或 path 視為同一筆並更新；`tags` 沒給時不改 |
| GET | `/datasets?tag=&q=&limit=&offset=` | 量測、讀檔或論文 | |
| GET | `/datasets/lookup?fingerprint=&path=` | 量測或讀檔 | |
| GET / PATCH / DELETE | `/datasets/<id>` | | 詳細（含方案）／改標籤、名稱／移除登錄（檔案不刪） |
| GET | `/datasets/<id>/scheme` | 量測或讀檔 | 量測方案 |
| GET | `/datasets/<id>/papers` | | 依標籤分組的論文 `[{tag, tag_url, papers}]` |
| GET | `/datasets/<id>/file` | 量測或讀檔 | 下載（Hub 上的檔，或 `QEL_DATA_ROOTS` 對應到的共用資料夾） |
| GET | `/papers?q=&tag=` | 論文模塊 | 搜尋論文庫 |
| POST | `/papers/by-tags` | | `{tags}` → 依標籤分組的論文 |
| GET | `/papers/<id>/link` | 登入 | 論文庫網址 |
| GET | `/events?after=&wait=&topics=` | 登入 | long-poll（最多 55 秒）；`after=-1` 從現在開始 |
| POST | `/events` | 登入 | 模塊自訂事件，主題必須以 `app.` 開頭 |
| POST | `/handoff` | 該模塊 | `{module, action, payload}` → 送給同一個使用者其他電腦上的大程式 |
| * | `/relay/<路徑>` | 量測模塊 | 轉給 Lab Control Hub 的 `/api/<路徑>`（Hub token 由大程式代填；指令標上真正的使用者；`hub/*` 不開放） |
| GET | `/health` | 登入 | 各服務狀態；站長另有流量、錯誤、空間、Hub 節點 |
| GET / PUT | `/admin/users[/<username>]` | 站長 | 使用者與模塊開關 `{access:{模塊: bool}, blocked}` |
| GET / PUT | `/admin/settings` | 站長 | 新帳號預設權限、論文庫網址 |
| GET | `/admin/audit`、`/admin/sessions`、`/admin/traffic` | 站長 | 紀錄、登入中的裝置（可登出）、流量 |

### 事件主題

| 主題 | 資料 |
|---|---|
| `dataset.created`、`dataset.updated` | 數據（同 `/datasets/<id>`） |
| `tags.changed` | `{tag, ...}` |
| `modules.changed` | `{module, version, withdrawn?}` |
| `access.changed` | `{username}`（只送給那個人） |
| `handoff` | `{module, action, payload}`（只送給本人） |
| `app.*` | 模塊自訂 |

## 三、本機傳遞（同一台電腦，不經網路）

每個開著的模塊在 `127.0.0.1` 開一個埠，寫 `<QEL_HOME>/run/<模塊>.json`（埠號、密語，只有本人可讀）。
一次連線一個請求：一行 JSON `{"secret","action","payload","from"}` → 一行 `{"ok":true,"result":…}` 或 `{"ok":false,"error":"…"}`。

| 動作 | 收的模塊 | payload |
|---|---|---|
| `open_file` | lablogviewer | `{path}` 或 `{dataset_id}`（先下載） |
| `apply_scheme` | labcontrol | `{path}`、`{dataset_id}` 或 `{scheme}` |
| `show_papers` | lablogviewer | `{tags}` |
| `launch` | launcher | `{module, action, payload}`：開啟模塊再轉交 |
| `ping` | 全部 | → `{module, version, pid}` |

```python
from labcomm import local, actions
local.deliver("labcontrol", actions.APPLY_SCHEME, {"path": "D:/data/x.hdf5"})   # 沒開會請大程式開啟
```

## 四、數據檔裡的屬性（HDF5 根屬性）

| 屬性 | 內容 |
|---|---|
| `qel/scheme` | 量測方案 JSON（與 Lab Control 的 `*.scheme.yaml` 相同結構） |
| `qel/meta` | `{"tags": [...], "source": {"module","version","host","user","writer"}, "dataset_id": n, "labcomm": 版本}` |

Labber 原本的 `/Tags`（Project、Tags、User）照舊；讀檔模塊兩邊都讀。

內容指紋 `qfp1:`＝sha256（大小＋開頭 4 MB＋結尾 1 MB）前 40 字：檔案搬家、改名後仍認得是同一筆數據。
