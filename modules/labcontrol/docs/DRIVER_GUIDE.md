# 儀器驅動撰寫規範（Driver Guide）

Lab Control 所有儀器都用同一套寫法：**一個類別 + 一張參數表**。寫好之後，這台儀器自動可以：

- 出現在主視窗左下的儀器參數列表，可拖進流程圖（電源 → Step 節點；量測通道 → 量測節點；可寫的數值參數 → 可固定或掃描，`sweepable` 標為常用）
- 在 `instruments.yaml` 不改程式就覆寫任何參數欄位（上下限、預設值、標籤、甚至 SCPI 指令）
- 被 Station 的 lease 保護（量測中其他前端不能寫）、被網頁面板控制、寫進資料檔的儀器 snapshot
- 在模擬模式（`--sim`）下用同一張參數表模擬

範本：`LAB/plugins/_driver_template.py`（量測儀器）、`LAB/plugins/_example_keithley2400.py`（電源）。
內建參考實作：`labcontrol/drivers/yokogawa/gs.py`、`labcontrol/drivers/rohde_schwarz/vna.py`。

---

## 1. 檔案放哪裡

| 情況 | 位置 |
|---|---|
| 自己實驗室用、還在測試 | `C:\Users\even9\LAB\plugins\<名稱>.py`（丟進去重開即載入；底線開頭的檔案不載入） |
| 驗證過、要給大家用 | 移到 `labcontrol/drivers/<廠牌>/<型號>.py`，在 `labcontrol/drivers/__init__.py` 匯入，隨新版本發佈 |

## 2. 類別骨架

```python
from labcontrol.core import Instrument, ParamSpec, register_driver
from labcontrol.core.capabilities import TraceAcquirer, XAxis

@register_driver("keysight.p5024")          # <廠牌>.<型號>，全小寫；instruments.yaml 的 driver: 用這個名字
class KeysightP5024(Instrument, TraceAcquirer):
    MEASURE_KIND = "vna"                     # 量測種類（參數列表分類、互斥規則、估時、節點顏色都依這個）
    TRACES = ["S21", "S11"]                  # 可量的通道；instruments.yaml traces: 可覆寫
    ERROR_QUERY = ":SYST:ERR?"               # 錯誤佇列；儀器不支援就設 None
    default_timeout_ms = 10000
    sim_driver = "sim.vna"                   # 模擬時換成哪個 driver（可省略）

    PARAMS = [ ... ]                          # 見第 3 節

    def scpi_context(self):                   # SCPI 樣板可用的變數
        return {"ch": int(self.options.get("channel", 1))}

    def on_connect(self):                     # 只讀狀態，不改變輸出！
        self.check_errors()
```

依儀器能力混入的介面（`labcontrol/core/capabilities.py`）：

| 介面 | 用途 | 必須實作 |
|---|---|---|
| `Source` | 電流 / 電壓源（上下限、自動斜坡、lease 由框架處理） | `_write_level`、`_read_level`、`_write_output`、`_read_output`；建構時 `init_source(**source_settings(options, key))` |
| `TraceAcquirer` | 一次量一條向量（VNA、頻譜、示波器） | `configure(**settings)`、`x_axis(name)`、`acquire(name)`；選用 `labber_settings(name)` |
| `ScalarMeter` | 單一數值（溫度、電壓表） | `read()` |

多通道儀器：每個通道是一個 `Channel`（見 `GSChannel`），通道自己也可以有 `PARAMS`。

## 3. 參數表（ParamSpec）

```python
ParamSpec("if_bw", label="IF 頻寬", unit="Hz", display_unit="kHz",
          get=":SENS{ch}:BAND?", set=":SENS{ch}:BAND {value}",
          limits=(1, 30e6), default="10 kHz", readback=True,
          measure_setting=True, sweepable=True,
          doc="手冊 7.3.x；儀器會捨入到 1-1.5-2-3-5-7 級距")
```

| 欄位 | 說明 |
|---|---|
| `name` | 參數名稱（程式與 YAML 用），英數底線 |
| `label` | 顯示名稱（UI、Labber 參數表） |
| `unit` / `display_unit` | 內部 SI 單位 / UI 顯示單位 |
| `get` / `set` | SCPI 樣板；`{value}` 為寫入值，其他 `{…}` 來自 `scpi_context()` |
| `kind` | `float`、`int`、`bool`、`str`、`enum`（配 `choices`） |
| `limits` | SI 上下限，超出丟 `LimitError`（寫入前檢查） |
| `default` | 量測方塊的初始值，可帶單位字串 |
| `measure_setting` | 出現在量測方塊的「儀器設定」 |
| `sweepable` | 常用參數（儀器參數列表預設只顯示常用；所有可寫的數值參數都能固定或掃描） |
| `readback` | 設定後立刻讀回（儀器會自動修正的值，例如 IF 頻寬） |
| `snapshot` | 是否寫進資料檔的儀器設定 |
| `on_off` | bool 寫入時用的字（預設 `ON`/`OFF`） |
| `doc` | **寫上手冊章節**與注意事項 |

樣板表達不了時（例如一個設定要送好幾條指令），在類別裡定義 `_get_<name>()` / `_set_<name>(value)`，
框架會自動改用它們（上下限檢查、讀回仍由框架處理）。例：ZNA 的 `averages`、GS 的 `range`。

## 4. 使用者在 instruments.yaml 可以覆寫的東西

```yaml
VNA1:
  driver: rs.zna
  address: TCPIP0::192.168.1.11::INSTR
  parameters:                         # 任何 ParamSpec 欄位
    power: {limits: [-40, 0], default: -20 dBm, label: 泵浦功率}
    sweep_time: {measure_setting: true}
  traces: [S21, S12]
  measure_defaults: {points: 1001}    # 量測方塊初始值
  block_title: VNA-A                  # 流程圖方塊標題
DC1:
  driver: yokogawa.gs200
  scpi: {level_w: ":SOUR:LEV:FIX {value}"}      # 逐條覆寫 SCPI（Yokogawa 系列）
  channels: {ch1: {parameters: {limiter: {limits: [0, 10]}}}}
```

覆寫了不存在的參數或欄位會在開啟時報錯（不會默默忽略）。

## 5. 安全規則（必須遵守）

1. **`on_connect()` 不改變輸出。** 重開程式不能讓電流 / 功率跳動。需要固定狀態時讓使用者寫在 `instruments.yaml` 的 `on_connect:`，而且只在與目前狀態不同時才寫入（見 `YokogawaSMU.on_connect`）。
2. **會影響輸出的動作要擋。** 例如 Yokogawa 在輸出開啟時拒絕切換量程 / 功能（手冊：感性負載可能跳脫、GS610 切換功能會關閉輸出），除非使用者明確設定 `allow_range_change_while_on`。
3. **依量程解析度取整、檢查量程上限**（見 `GSChannel.quantize` / `_write_level`）。
4. **等待量測完成要有逾時**，逾時丟 `InstrumentTimeout`（Runner 會重試 → 暫停），不要讀回可能過期的資料。
5. **設定後讀錯誤佇列**（`check_errors()`），有錯就丟 `InstrumentError`，訊息要寫出儀器名稱。
6. 每條 SCPI 在註解或 `doc` 寫上手冊章節；沒有在實機驗證過的 driver，檔頭要寫「未實機驗證」。

## 6. 模擬

- 電源：`sim_driver = "sim.current_source"` 即可（沿用 GS 參數表）。
- 量測儀器：`sim_driver = "sim.vna"`；在 `labcontrol/drivers/sim/sim.py` 的 `SimVNA` 會讀 `real_driver` 的 `PARAMS`，
  所以儀器參數列表、上下限在模擬與實機完全一致。若實機 driver 有 `labber_settings_for()` 類別方法，模擬也會輸出同樣結構的 Labber 檔。

## 7. 測試（發佈前必須）

用 `MockTransport` 驗證送出的 SCPI 與手冊一致（參考 `tests/test_drivers.py`）：

```python
t = MockTransport({":SOUR:FUNC?": "CURR", ":SOUR:RANG?": "0.2", ":SOUR:LEV?": "0", ":OUTP?": "1"})
dc = GS200("DC1", transport=t); dc.connect()
assert t.writes == []                        # 連線不寫入
dc.channels["ch1"].set_level(0.002)
assert t.writes[-1] == ":SOUR:LEV 0.002"
```

至少涵蓋：連線唯讀、每個參數的讀寫字串、上下限、錯誤路徑（逾時 / 儀器錯誤）。

## 8. 其他類別屬性

| 屬性 / 方法 | 用途 |
|---|---|
| `IDN_PATTERN` | `*IDN?` 應符合的正規表示式；儀器伺服器掃描 VISA 時據此建議驅動，驅動測試會比對 |
| `param_unit(name)`（通道或儀器上） | 單位會隨狀態改變時（例如 GS 的量程：電流源 A、電壓源 V）提供顯示單位 |
| `ERROR_QUERY` | 錯誤佇列指令；驅動測試每一步都用它檢查指令是否被接受 |

寫完驅動後，可以參考 `labcontrol/testing/fake_scpi.py` 寫一個依手冊語法的假儀器，讓驅動測試在沒有硬體時先跑過一次。

## 9. 上機驗證清單

先用儀器伺服器的「驅動測試」（`docs/INSTRUMENT_TEST.md`），再逐項確認：


- [ ] `python -m labcontrol check`：IDN 正確、面板數值沒有變化
- [ ] 每個參數 `get` 與面板一致；`set` 後面板正確、`:SYST:ERR?` 無錯誤
- [ ] 超出 `limits` 被拒絕
- [ ] 量測儀器：一次 `acquire` 的資料與儀器畫面一致；逾時情境會重試 / 暫停
- [ ] 把驗證結果寫進該版本的 `docs/releases/vX.Y.Z.md`
