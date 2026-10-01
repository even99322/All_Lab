# 從舊程式遷移

## 對照表

| 舊程式 | 新位置 |
|---|---|
| `yoko_master.py` GS200 / GS610 / GS820 | `labcontrol/drivers/yokogawa/gs.py` |
| `sweep_main.py` `Yokogawa_DC` | 同上（合併成一份） |
| `sweep_main.py` `RSVNA` | `labcontrol/drivers/rohde_schwarz/vna.py` |
| `unlock_vna.py` | `RohdeSchwarzZNA.recover()`；`python -m labcontrol unlock VNA1` |
| `DC_OPTIONS` / `VNA_OPTIONS` / `KNOWN_DEVICES` | `C:\Users\even9\LAB\instruments.yaml` |
| 寫死在程式裡的存檔路徑、Labber user、預設值 | `C:\Users\even9\LAB\settings.yaml` |
| `last_config.json` | `LAB/experiments/dc_vna_sweep.yaml`，或用量測方案編輯器存的 `LAB/schemes/*.scheme.yaml` |
| `sync_ramp_dcs_safe` | `virtual.interleaved_pair` + `Source.ramp_to`（RunOptions.approach_rate / park_rate） |
| DC1/DC2 交錯步進（`c1_list` / `c2_list`） | `virtual.interleaved_pair.split()`（序列與舊版逐點相同，有測試） |
| `MeasurementThread` | `labcontrol/measure/runner.py`（Runner）＋ `procedures.py`（trace_sweep） |
| 吸收峰型態變化自動暫停 | hook `dip_shape_pause` |
| `evaluate_trace`、IQ 象限上色 | `labcontrol/analysis/qc.py` |
| `SaveThread` + `save_labber.py` | `data/writers/labber.py` + `labber_export_script.py` |
| `generate_unique_filename`、日期資料夾、檔名日期防呆 | `labcontrol/data/naming.py` |
| `LivePlotWindow` | `apps/qt/monitor.py`（精簡版；完整版見 ROADMAP 0.1.0） |
| `ReviewSelectionWindow` | 見 ROADMAP 0.1.0 |
| `MainWindow` 設定頁 | 量測方案編輯器（`main.py`） |
| `web_yoko.py` `WebYokogawaWrapper` + Flask | `apps/web/server.py`（網頁 HTML 原封不動搬到 `templates/yoko_panel.html`） |

## 行為差異（請留意）

1. **連線不再改變儀器輸出。** 舊版連線時會送 `:SOUR:FUNC CURR`、`:OUTP 1`。新版預設只讀回狀態；需要舊行為請在 instruments.yaml 該儀器加 `on_connect: {function: CURR, output: true}`（只在與目前狀態不同時才寫入）。
2. **不再預設 VOLT。** 舊 GS610/GS820 在未設定 function 前寫 level 會寫到 `VOLT`；新版連線時讀回實際 function。
3. **大幅跳動自動走斜坡。** 任何前端（含網頁）設定值與目前差距超過 `max_jump` 時，自動以 `ramp_rate` 斜坡。網頁按大位數時會變慢，數值可在 instruments.yaml 調整。
4. **VNA 逾時會報錯。** 舊版等 120 s 後仍讀資料（可能是舊資料）；新版丟出 `InstrumentTimeout`，自動 `recover()` 後重試一次，再失敗就暫停等人處理。
5. **逐點寫入原始檔。** 每次量測在當日資料夾的 `_raw/` 留一份原生 HDF5，當機也不會遺失已量到的點。Labber 檔仍在結束（或 QC）後才產生。
6. **網頁與量測可以同時開。** 在同一個程式（`monitor.py --web 5000`）裡共用 Station，量測中的儀器網頁只能看不能改。

7. **依手冊修正的驅動行為（0.0.1a）。** GS820 改用 `:CHANnel<n>:` 語法；GS610 電流源改設電壓限制器；GS 系列依量程解析度取整、輸出中拒絕切換量程 / 功能；ZNA 每點清除平均、讀回 IF 頻寬、x 軸用實際頻率點。詳見 `docs/releases/v0.0.1a.md`。

## 上機前檢查（0.1.0 驗收前請逐項確認）

先照 `docs/INSTRUMENT_TEST.md` 用儀器伺服器做驅動測試（每台至少「讀取」+「寫回相同值」），報告在 `LAB/logs/`。

- [ ] 每台儀器驅動測試：讀取 ✔、寫回相同值 ✔（報告已保存）
- [ ] VNA 驅動測試「量測一次」✔；電源「小幅度輸出」✔

- [ ] `python -m labcontrol check`：所有儀器 IDN、目前電流正確，且儀器面板數值沒有任何變化
- [ ] `python -m labcontrol get DC3.level` 與儀器面板一致
- [ ] `python -m labcontrol set DC3.level <目前值+0.001 mA>`：直接設定；`+0.2 mA`：確認以 ramp_rate 斜坡
- [ ] 超過 limits 的設定被拒絕
- [ ] 小範圍實際掃描（10 點），與舊程式同條件比較 Labber 檔
- [ ] 量測中拔掉 VNA 網路線：確認重試 → 自動暫停 → 接回後「繼續」可恢復
- [ ] 量測中從網頁嘗試改同一台 DC：應被拒絕（409）
- [ ] 中斷量測：電流斜坡回到起點
- [ ] GS200：`python -m labcontrol get DC1.ch1.range`、`DC1.ch1.limiter` 與面板一致；輸出開啟時 `set DC1.ch1.range 0.01` 被拒絕
- [ ] GS610（使用前）：`set <名稱>.ch1.limiter 5` 後面板電壓限制器為 ±5 V，`:SYST:ERR?` 無錯誤
- [ ] GS820（使用前）：兩個通道 level / output 各自正確；`measured` 讀值與面板一致
- [ ] ZNA：平均 ≥ 2 時，每一點的平均計數從 1 重新開始（面板 AVG 計數）；`if_bw` 設 6 kHz 讀回 7 kHz；`CALC1:PAR:CAT?` 的 trace 名稱與 `trace_map` 相符
- [ ] ZNA：確認功率上下限後寫到 `instruments.yaml` → `VNA1.parameters.power.limits`
- [ ] 多維 Labber 匯出（Data 放在所有迴圈外、兩層以上掃描）：用 Labber Log Browser 開檔確認兩個 step 軸方向正確（目前假設 Labber 的 step channel 內層在前）
- [ ] 分檔匯出：用 logview 開一個分檔，確認外圈值（單一值 step channel）讀得到
