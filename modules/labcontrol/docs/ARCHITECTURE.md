# 架構

## 設計原則

1. **核心不認識任何 GUI。** `labcontrol.core / measure / data` 不 import PyQt 或 Flask。前端只做兩件事：訂閱 EventBus、呼叫 Runner/Station API。同一次量測可以同時被 Qt 視窗、手機網頁、logview 觀看。
2. **儀器清單只有一份。** 位址、型號、安全限制都在 `LAB/instruments.yaml`（預設 `C:\Users\even9\LAB`），所有程式共用。
   App 的可調值（存檔、預設值、單位、規則、估時）都在 `LAB/settings.yaml`，程式碼一律用 `setting("a.b")` 讀取，不寫死。
3. **量測程序依賴「能力」而不是型號。** `TraceSweep` 只知道「有一個 Source 可以掃、有一個 TraceAcquirer 可以讀」，所以換 VNA、換成 SHFQC、多加一台電表都只改 YAML。
4. **擴充 = 新增檔案，不改核心。** driver / procedure / hook / writer / reader 都走註冊表，丟進 `LAB/plugins/` 就生效。
5. **安全機制集中。** 上下限、斜坡、lease 在 Source/Station 層強制執行，不靠每個前端自己記得。
6. **輸出向下相容。** Labber 檔的通道名稱與結構與舊 `save_labber.py` 相同，舊分析程式與 logview 不用改。

## 分層

```
┌──────────────────────────── 前端 apps/ ────────────────────────────┐
│  CLI        Qt monitor (bridge.py)       Web panel (server.py)    logview │
└───────▲──────────────▲──────────────────────▲──────────────────────▲─────┘
        │ 呼叫 API     │ 訂閱事件             │                      │
┌───────┴──────────────┴── measure/ ──────────┴──────────────────────┴─────┐
│  Experiment（讀 YAML、組裝）→ Runner（狀態機、指令佇列、錯誤策略、lease） │
│      SweepPlan（軸、nD、snake）  Procedure（每點量什麼）  Hooks（每點後檢查）│
└───────────────────────────────▲───────────────────────────────▲─────────┘
                                │ Parameter.set / acquire       │ write_point
┌───────────── core/ ───────────┴──────────┐   ┌──────── data/ ──┴────────┐
│ Station：ref 解析、連線順序、lease、snapshot│   │ Dataset / PointRecord      │
│ Instrument / Channel / Parameter           │   │ HDF5Writer（逐點、可回溯） │
│ 能力：Source / TraceAcquirer / ScalarMeter  │   │ LabberWriter（3.8 子程序） │
│ safety：Limits、RampPolicy、ramp()          │   │ readers：open_dataset()    │
│ EventBus、registry（plugins）、units        │   │ naming：資料夾/檔名規則    │
└──────────────────▲─────────────────────────┘   └───────────────────────────┘
                   │ transport.write/query（每台一把鎖）
┌──────────── drivers/ ──────────────────────────────────────────────────────┐
│ yokogawa.gs200/gs610/gs820   rs.vna   sim.*   virtual.interleaved_pair/group │
└────────────────────────────────────────────────────────────────────────────┘
```

## 量測方案層（labcontrol/scheme）

量測方案（Scheme）是實驗設定的「可視化前端」，不改變引擎：

```
流程圖（節點圖 SchemeGraph：節點、位置、連線）──▶ Scheme（方塊樹，存成 .scheme.yaml，scheme: 2）
                                      │ compile_scheme()：檢查、估時
                                      ▼
                         實驗設定 dict（與 LAB/experiments/*.yaml 同格式）
                                      │ Experiment
                                      ▼
                                 Runner（引擎）
```

節點圖與方塊樹一一對應（`labcontrol/scheme/graph.py`，不依賴 Qt）：每個節點一個輸入；`next` 輸出接下一步；
掃描節點另有 `body` 輸出（每一點 ⟲），接出去的鏈就是迴圈內容（= 方塊樹的 children）。沒接到「開始」的節點不執行。
舊格式（scheme: 1，純方塊樹）開啟時自動排版成節點圖。

| 流程圖 | 編譯結果 |
|---|---|
| 掃描節點（迴圈框） | sweep 軸，依巢狀順序由外到內；interp / alternate / 每軸掃完後動作（run.park） |
| 固定值的 set 方塊 | setup，開始前設定一次 |
| 量測方塊 | readouts + setup[儀器]（被掃描的參數自動略過） |
| Data 在第 d 層迴圈內 | `output.split_by` = 最外 d 個軸 → 每個外圈值一個檔（SegmentExportHook 背景匯出） |
| wait 方塊 | 加到所在迴圈的 settle；最外層 → `run.settle_first` |
| 方案 output（右下「檔案設置」） | 檔名、格式、同名處理、Labber project / user / tags / comment；沒有 Data 節點 = 一個檔 |
| 方案 run.point_delay | 最內圈 settle 至少這麼久（Labber 的 Delay between step and measure） |

方塊庫（catalog）由 instruments.yaml 與各 driver 的參數表（ParamSpec）產生：`group: magnet` → 電磁鐵組、其他 Source → 單台電源、TraceAcquirer → 量測方塊（`measure_setting` 參數成為量測欄位）、其他可寫的數值參數 → 儀器參數（`sweepable` 標為常用）。新增儀器後主視窗的儀器參數列表自動出現，不用改 UI。

儀器伺服器（`labcontrol/apps/qt/server/`，對應 Labber Instrument Server）與主視窗在同一個程序、共用同一個 Station；
新增 / 移除儀器發出 `station.changed`，主視窗據此重建儀器參數列表。`instruments.yaml` 的編輯在 `core/labfile.py`（ruamel 保留註解）。
驅動測試與 VISA 掃描在 `labcontrol/diagnostics.py`（不依賴 Qt，CLI 共用）；`labcontrol/testing/fake_scpi.py` 依手冊語法模擬
GS200 / ZNA 的 SCPI 端，讓真驅動在沒有硬體時也能跑完整流程（不認得的指令回 -113）。

遠端量測（`labcontrol/remote/`，見 `docs/REMOTE.md`）：所有電腦都主動連到 Lab Control Hub（`labhub/`，NAS 上的 HTTP 服務，
只用標準函式庫；指令與即時資料用 long-poll）。`hub.py` 是 Hub 的 HTTP client（urllib，略過 proxy），
`node.py`（量測節點：心跳、指令、即時事件批次、量測檔上傳）、`client.py`（節點清單、指令、`LiveFeed` 把節點事件重播到本機 EventBus、
`RemoteRunnerProxy` 讓監控面板的按鈕送到節點）、`codec.py`（事件 ↔ JSON）、`updater.py` / `versioning.py`（版本比較與節點更新）。
控制端的 MonitorPanel 與本機完全相同，只是訂閱的 EventBus 來自 `LiveFeed`。
節點的即時資料只在量測執行緒排隊（`NodeService._enqueue`），由上傳執行緒送到 Hub，量測不等網路。
`remote/instruments.py` 由位址算出實體儀器識別碼（`net:<IP>`、`usb:<vid>:<pid>:<序號>`），各電腦回報後 Hub 建立儀器登錄；
共用網路儀器的歸屬（拉取群組）存在 Hub，`Station.connect_guard` 在連線前檢查。`measure/watchdog.py` 偵測量測卡住並存執行緒堆疊。
`labmonitor/` 是只依賴 PyQt6 的監控程式（打包成 exe），透過同一組 Hub API 操作；`labhub/selfupdate.py` 讓 Hub 線上更新自己。

主視窗（`labcontrol/apps/qt/workbench/`）：`doc.py`（共用狀態、復原）、`graph_view.py`（流程圖）、`channels.py`（儀器參數）、
`monitor_panel.py`（即時監控）、`files_panel.py`（檔案設置）、`dialogs.py`（Step / 量測設定視窗）、`window.py`（四格版面）。

Qt 前端共同規則（0.0.6 起）：

- **顏色**：用 `labcontrol/apps/qt/theme.py` 的語意顏色（`theme.c("err")`、`theme.qc("text")`、`theme.tint("ok")`），
  一般文字 / 底色用 stylesheet 的 `palette(text)`、`palette(base)`；自己畫的元件與 pyqtgraph 圖表用 `theme.on_change` / `theme.style_plot`
  登記，切換淺色 / 深色時自動重畫。不要寫死 `#rrggbb`。
- **背景執行緒**：用 `apps/qt/worker.py` 的 `run_bg`（要結果）或 `start_thread`（不要結果），不要直接 `threading.Thread`，
  確保視窗物件在 GUI 執行緒釋放。

## 關鍵概念

**ref 命名**：`DC1`（儀器）、`DC5.ch2`（通道）、`VNA1.power`（參數）、`DC1.level`（單通道可省略 ch1）。任何 Parameter 都能當掃描軸。

**虛擬儀器**：`magnet_A`（`virtual.interleaved_pair`）把兩台 GS200 包成一台 Source，level = 平均電流，設定時自動拆成 ceil/floor，序列與舊 sweep_main 完全相同（有測試比對 857 點）。web 面板的「合併」同理對應 `virtual.group`。

**lease**：Runner 開始時鎖住用到的儀器（含虛擬儀器底下的實體儀器）。其他執行緒（web 面板）仍可讀，但寫入會得到 `InstrumentBusy`（網頁回 409）。

**Runner 狀態機**：`idle → preparing → running ⇄ paused → finished / aborted / failed`。所有控制（pause、rollback、manual、accept…）都是丟進 queue 的指令，任何執行緒都能呼叫。量測失敗先重試 `retries` 次，仍失敗就自動暫停等人處理（`on_error: pause`）。結束時依 `park` 斜坡停靠，停靠不會被中斷。

**資料流**：每個點 → `PointRecord`（可含多個 shot，手動保留用）→ raw HDF5 立即寫入（當機不丟）→ 結束後（或 QC 挑選後）`Experiment.export()` 產生 Labber 檔。

## 事件（EventBus 主題）

| topic | payload | 用途 |
|---|---|---|
| `log` | level, message | 訊息列 |
| `run.state` | state, previous | 按鈕狀態 |
| `run.started` | dataset | 初始化圖表 |
| `run.progress` | index, total, eta, setpoints, display | 進度 / ETA |
| `point.shot` | index, shot, setpoints | 即時 trace（含手動模式每一筆） |
| `point.committed` | record | 熱圖、logview |
| `point.rollback` | index | 清除熱圖欄 |
| `manual.waiting` / `manual.shots` | index / count | 手動模式 UI |
| `run.hook` | hook, action, reason | 自動暫停提示 |
| `run.finished` | dataset, status, error | QC / 匯出 |
| `instrument.connected` / `instrument.error` / `instrument.closed` | name, … | 儀器狀態 |
| `station.changed` | action（add / remove）, name | 儀器伺服器新增 / 移除儀器 → 主視窗重建列表 |
| `station.lease` | owner, instruments, acquired | 網頁顯示「量測中」 |
| `data.exported` | path, writer | 通知 logview 開檔 |

## 如何擴充

### 1. 新增一台儀器
**請依 `docs/DRIVER_GUIDE.md`**（範本 `LAB/plugins/_driver_template.py`、`_example_keithley2400.py`）：
1. 繼承 `Instrument`，依能力混入 `Source` / `TraceAcquirer` / `ScalarMeter`；可調的值寫成 `PARAMS = [ParamSpec(...)]`，只有樣板表達不了的才寫 `_get_/_set_` 方法。
2. `@register_driver("廠牌.型號")`，設定 `sim_driver` 讓它在 `--sim` 下可用。
3. `on_connect()` 只讀狀態，不改輸出。
4. 在 `LAB/instruments.yaml` 加一段。完成——它立刻可以被掃描、被讀取、出現在 web 面板、被 lease 保護。

### 2. 新增量測邏輯
繼承 `Procedure`，實作 `setup()`（回傳 ChannelSpec）與 `measure()`（回傳一個 shot）。掃描、暫停、回溯、手動、存檔全部由 Runner 提供。大多數情況直接用 `trace_sweep` 改 YAML 即可。

### 3. 新增自動判斷
繼承 `Hook`，實作 `after_point()` 回傳 `HookResult("pause" | "stop", 原因)`。範例：`dip_shape_pause`（內建）、`LAB/plugins/dip_edge_stop.py`（自動找電流邊界）。

### 4. 新增檔案格式 / 讀檔
`@register_writer` 實作 `export()`（或串流的 `open/write_point/truncate/close`）；`@register_reader` 實作 `can_read/read`，logview 透過 `open_dataset()` 自動支援。

### 5. 新增前端
訂閱 EventBus（Qt 用 `QtEventBridge`），呼叫 `Runner` 控制方法。參考 `apps/qt/monitor.py`（約 230 行，含手動模式與熱圖）。

### 6. logview 整合
見 `labcontrol/integrations/logview.py`：
- **A 檔案層**（現在可用）：Labber 輸出與舊檔相同；或讀原生檔 `open_dataset()`。
- **B 即時層**：實作 `Viewer` 介面，`attach_viewer(bus, viewer)` 即時接收每一點。
- **C 指令層**：原生檔 metadata 含完整實驗設定與儀器 snapshot，可做「用這個檔案的設定重跑」。
