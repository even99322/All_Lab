# 模塊規範

## 一、module.json

每個模塊的根目錄一個 `module.json`，是發佈、安裝、更新的唯一依據：

```json
{
  "id": "labcontrol",
  "name": "量測模塊",
  "version": "0.0.12",
  "kind": "desktop",
  "entry": "main.py",
  "python": "3.12",
  "requirements": "requirements.txt",
  "platforms": ["windows", "macos"],
  "comm": {"actions": ["apply_scheme", "ping"], "publishes": ["dataset.created"]},
  "version_from": {"file": "labcontrol/__init__.py", "regex": "__version__\\s*=\\s*\"([^\"]+)\""},
  "package": {"exclude": ["tests"]}
}
```

| 欄位 | 說明 |
|---|---|
| `id` | 英文小寫，全平台唯一 |
| `kind` | `desktop`（桌面大程式安裝）、`library`（給其他模塊用，例如 labcomm）、`service`（NAS 服務，更新代理更新）、`web` |
| `version` | 純數字三段（與 Lab APP 相同規則）；必須和程式裡的版本一致（`tools/package.py --check`） |
| `entry`、`python`、`requirements` | 桌面模塊：大程式為它建立獨立的 Python 環境，requirements 改變才重裝 |
| `service` | NAS 服務：`container`（容器名稱）、`code`（程式資料夾）、`health`（確認網址） |
| `package` | 打包時要放進來（`include`）或排除（`exclude`）的路徑 |

目前的模塊：

| id | 位置 | kind |
|---|---|---|
| `labcomm` | `comm/` | library |
| `launcher` | `launcher/` | desktop |
| `monitor` | `monitor/` | desktop（站長） |
| `labcontrol` | `modules/labcontrol/` | desktop |
| `lablogviewer` | `modules/lablogviewer/` | desktop |
| `paperlib` | `modules/paperlib/` | service（web） |
| `portal` | `portal/` | service |
| `labhub` | `modules/labcontrol/labhub/`（`labhub.module.json`） | service |
| `agent` | `agent/` | service |

## 二、發佈新版本（每個模塊各自）

1. 改程式，把程式裡的版本號與 `module.json` 的 `version` 一起遞增。
2. 測試（各模塊自己的 `pytest`；平台在根目錄 `python -m pytest tests comm/tests`）。
3. `python tools/package.py <id>` → `dist/<id>_v<版本>.zip`（或 GitHub Actions「build-desktop」的 `module-packages`）。
   直接把模塊資料夾壓縮成 zip 也可以，只要根目錄（或第一層資料夾）有 `module.json`。
4. 桌面模塊：網頁「管理 → 模塊發佈」或監控程式「模塊發佈」，**只要選 zip**，模塊與版本讀 `module.json`；
   NAS 服務：監控程式「服務更新」。
5. 大程式本體、監控程式另外有安裝檔（exe／App）：`tools/build_desktop.py`，見 DEPLOY.md「安裝檔從哪裡來」。
5. 已發佈的版本不能覆寫；有問題就撤回，再發佈下一個版本號。

更新一個模塊**不需要**同時更新其他模塊。只有協定不相容（`PROTOCOL` 遞增）時才需要一起更新，而這種改動要避免。

## 三、模塊怎麼使用通信模塊

- 從桌面大程式開啟時，`PYTHONPATH` 已經有 labcomm，環境變數有 `QEL_PORTAL_URL`、`QEL_TOKEN`、`QEL_HOME`、`QEL_MODULE_ID`、`QEL_MODULE_VERSION`。
- `import labcomm` 失敗時（單獨執行、舊環境）一律當作沒有大程式，照原本的方式運作。
- 收動作：`labcomm.local.LocalEndpoint(id, version, handler, actions).start()`；handler 在背景執行緒，有畫面的模塊用 Qt signal 交給主執行緒。
- 送動作：`labcomm.local.deliver(目標模塊, 動作, payload)`（目標沒開會請大程式開啟）。
- 共用標籤：`labcomm.tags.shared_taxonomy(client)`（連不上用快取）、`labcomm.tags.normalize_list(...)`。
- 數據檔：`labcomm.handoff.embed / extract_scheme / read_meta / fingerprint`。

整合程式放在模塊自己的一個檔裡：

- Lab Control：`labcontrol/integrations/qel.py`（`app.py` 一行 `attach_window`、`cli.py node` 一行 `QelBridge`、`experiment.py` 的 `data.exported` 多帶方案與標籤、`node.py` 上傳後發 `data.uploaded`）。
- LabLogViewer：`app/interfaces/qel.py`（`main.py` 一行 `attach_browser`、`interfaces/measurement/interface.py`、Online Paper Library 選單、資料列表拖曳多帶檔案網址）。

## 四、新增一個模塊

1. 新資料夾 + `module.json`（`kind: desktop`、`entry`），程式裡用 labcomm 收／送動作。
2. 在 `portal/qelportal/modules.py` 的 `BUILTIN` 加一筆（名稱、說明、是否需要站長開放、預設權限），大程式網站更新一次。
3. `tools/package.py` 的 `MANIFESTS` 加上路徑。
4. 發佈第一個版本；站長在管理頁開放給需要的人。
