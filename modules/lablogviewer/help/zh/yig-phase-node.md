# 相位／Node
> 由連續擬合結果求出 YIG 的「相位線」，算出物理的 Node（耦合消失）與 Antinode（耦合最強）位置，並用相位連結擬合分開 κ_b 與 φ。

## 最短流程
1. **先完成連續擬合。** 模型請用宣告了 κ_m = κ_b·sin²φ 的模型（內建 `S11_single` 或 `S11_node`），完成〈連續擬合〉，至少要有 5 片成功。這個分頁只使用連續擬合的結果。 ![](phase_start.png)
2. **確認參數對應。** 切到 **相位／Node** 分頁 → **PHYSICAL MAPPING**：**Phase Parameter φ** 選 `phi`、**Resonance Frequency Parameter** 選 `w_m`、**Coupling Parameter κ_b** 選 `kappa_b`；**Phase Period P** 保持 1 π；勾著 **κ_eff = coupling parameter × sin²(phase parameter)**。程式會依參數名稱自動猜，通常不用改。 ![](phase_mapping.png)
3. **估計相位線。** **PHASE LINE** → **Estimator Source** 選 **κ_eff = κ_b sin²φ (recommended)** → 按 **Estimate from Continuous Fit**。T、φ_ref、f_ref 與 κ_b 會自動填入，下方顯示擬合品質與 Node 間距。 ![](phase_estimate.png)
4. **看 Node／Antinode。** 右側 4 張圖更新：**κm vs fm** 上的點應落在擬合曲線上；**2D Experimental Data** 疊上物理 Node（φ = nP）與 Antinode（φ = (n + ½)P）的位置；下方 **位置結果表** 列出每一點的頻率。 ![](phase_nodes.png)
5. **（進階）相位連結擬合或全域擬合。** 要讓 κ_b 與 φ 真正分開，用 **PHASE-LINKED CONTINUOUS FIT** 或 **GLOBAL LINKED FIT**（見下方〈進階參考〉）。 ![](phase_global.png)

## 這個分析解決什麼問題
單片擬合只能得到有效耦合 κ_eff = κ_b·sin²φ：κ_b（最大耦合）與 φ（YIG 位置的相位）混在一起。但 φ 會隨共振頻率線性變化：φ(f_m) = φ_ref + 2π·T·(f_m − f_ref)，T = x／v_g（YIG 位置除以群速度，單位 ns）。當掃描讓共振頻率移動時，κ_eff 會照 sin²φ 週期性地變大變小——從整條 κ_eff(f_m) 就能同時求出 κ_b、T 與 φ_ref，也就知道哪些頻率是 Node（耦合為零，2D 圖上訊號斷掉的地方）、哪些是 Antinode。

**需要的資料：** 同一個 2D 掃描、以同一模型做完的連續擬合；共振頻率範圍最好涵蓋至少一個 Node 間距（Δf_node = 1／(2T)）。

## 欄位的意義與單位
| 欄位 | 單位 | 意義與初值 |
|---|---|---|
| Phase Period P | π | κ 與 sin²φ 的週期。物理模型為 π，不要改，除非你的模型不同 |
| T = x/v_g | ns | 相位隨頻率變化的斜率。Node 間距 = P／(2π·T)，P = π 時 = 1／(2T) |
| φ_ref | rad | 在 f_ref 處的相位（以 P 折疊） |
| f_ref | GHz | 參考頻率，預設為擬合頻率範圍的中點 |
| κ_b (estimated) | 同耦合參數（通常 MHz） | 由 κ_eff 曲線求得的最大耦合 |
| T Search Maximum | ns | 搜尋 T 的上限（預設 20 ns）。上限太小會找不到正確的 T；太大則會變慢，而且可能挑到太短的週期 |

T、φ_ref、f_ref 可以手動輸入；手動改值後，Node 位置與圖會立即更新。

## Estimator Source：三種估計方式
- **κ_eff = κ_b sin²φ（建議）**：用每片的 κ_eff 擬合 κ_b·sin²(相位線)。最穩定，並會剔除離群片（4σ）。需要 ≥ 5 片成功。
- **Fitted φ (mod π)**：直接用每片擬合出的 φ。單片的 φ 只確定到 mod π，還有 φ ↔ −φ 的鏡像解，所以程式用「折疊 + 網格搜尋」；若使用了鏡像解會註明。
- **Dip candidate frequencies**：沒有可靠的擬合時，按 **Extract Dip Candidates** 由訊號深度找出凹陷較淺的頻率當 Node 候選（**Candidate Threshold** 預設 0.35），或自己輸入頻率，再按 **Estimate from Candidates**，由候選的間距估計 T。至少要 2 個。

**注意：** sin² 對 φ → −φ 對稱，所以 κ_eff 無法分辨 T 的正負，程式慣例回報 T > 0。

## Physical 與 Coarse 的差別，可以混用嗎？
- **Physical（物理位置）**：由相位線算出 φ = nP（Node）與 φ = (n + ½)P（Antinode）。只有模型宣告 κ_m = κ_b·sin²φ 時才會計算，數值有物理意義，可以寫進報告。
- **Coarse（粗略偵測）**：**Coarse Detector (empirical fallback)** 用純經驗方法：逐片找凹陷 → 對凹陷軌跡做線性擬合 → 沿軌跡取平均穿透率並平滑 → 找峰（Node 候選）與谷（Antinode 候選）。參數：**Candidate type**、**Trajectory half-width**（預設 0.25 GHz）、**Smoothing points**、**Minimum sweep distance**、**Prominence**、**Manual dip-depth threshold**。
- **不能混用。** Coarse 的結果是「候選」，不是物理極值；表格的 **Method** 欄會標明來源。粗略候選可以用來：①在沒有好擬合時找大概位置；②當作 **Dip candidate frequencies** 的輸入去估計相位線——但最後的物理位置應該來自相位線。

## 圖和表格代表什麼
- **κm vs fm**：每片的 κ_eff（點）與相位線求出的 κ_b·sin²φ（曲線）。點應沿著曲線；被剔除的離群片會另外標示。
- **Phase Line**：每片 φ 以 P 折疊後對 f_m 作圖，直線是相位線。Node 在折疊後的 0／P。
- **2D Experimental Data**：原始 2D 資料疊上 Node／Antinode 位置。Node 應落在訊號變淡或斷掉的地方——這是最直觀的檢查。
- **Signal Depth**：每片凹陷深度對頻率；Node 附近深度應接近 0。
- **位置結果表**：Type（Node／Antinode）、Method（physical／coarse）、Sweep、Frequency (GHz)、Phase (rad)、κm (Hz)、Status。

## 怎麼判斷可不可信
- 估計訊息的 **R²**（κ_eff 擬合）接近 1、使用片數（used N／M）大部分都有用上。
- κm vs fm 的點沒有系統性偏離曲線；2D 圖上的 Node 對準訊號消失處。
- T 的誤差（± 值）相對 T 很小；Node 間距在頻率範圍內至少出現一次。
- 若只有一小段頻率、看不到 κ_eff 的起伏，T 基本上無法決定——結果不可信。
- 全域擬合後：χ²_red 合理、每片 R² 的最小值與中位數都不低，T、φ_ref 的誤差小。

## 進階參考
### 相位連結連續擬合（PHASE-LINKED CONTINUOUS FIT）
- **Link Mode**：**Hard link**：φ 不擬合，直接由相位線決定；**Soft link**：φ 仍擬合，但限制在相位線預測值 ± **Soft-Link Range**（預設 0.3 rad）內。
- **Skip slices near Nodes (weak signal)**（預設開）：預測 |φ − nP| < 門檻（預設 0.12 rad）的片不擬合，視窗以外插穿過 Node。下方文字會顯示略過的頻率寬度。
- **Fix shared parameters**：把 κ_b、α_r 等共用參數固定成全域擬合或 κ_b 估計的值。φ 與 f_m 連結時，若每片都放開 κ_b 與 γ_0，f_m 會變得無法辨識，建議固定。
- 按 **Start Phase-Linked Continuous Fit** 後，結果取代連續擬合分頁中的結果（可先 **Export CSV** 保留舊的）。

### 全域連結擬合（GLOBAL LINKED FIT）
所有片一起擬合：每個參數在表格中指定角色——**Per Slice**（每片各自，例如 `w_m`、`A`、`phi_0`）、**Shared**（全部共用，例如 `kappa_b`、`alpha_r`、`t`）、**Fixed**、**Phase Line**（只有 φ 能用，由 T 與 φ_ref 決定）。**Reset Roles** 恢復自動建議。
- **Fit T／Fit φref**：是否連 T、φ_ref 一起擬合（預設都擬合）。
- **Slice Stride**、**Use successful slices only**（預設開）、**max nfev**（預設 200，每次評估包含全部片）。
- 完成後報告列出共用參數 ± 誤差、相位線、每片參數與 R²、物理 Node 頻率。**Shared Values → Parameter Initials** 把共用值寫回參數表；**Send Results to Continuous Fit** 把每片結果送到連續擬合分頁檢視；**Export Global Fit CSV** 輸出。

## 輸出與資料安全
- **Export Location Results CSV...** 輸出位置結果表；全域擬合有自己的 CSV；報告文字可從右側文字框複製。
- 所有結果只寫在你選的檔案或輸出資料夾，量測 HDF5 檔不會被修改。

## 做不出來時先檢查
- **「At least five successful fitted traces are required」**：連續擬合成功片太少；先改善連續擬合。
- **「Select the coupling parameter κ_b」**：PHYSICAL MAPPING 沒選耦合參數，或模型沒有這個參數。
- **沒有顯示物理 Node／Antinode**：模型沒有宣告 κ_m = κ_b·sin²φ（例如自己寫的模型），或 T = 0；請改用內建模型，或在模型檔加上 `PHYSICAL_COUPLING_RELATION = "kappa_b * sin(phi)**2"`。
- **Node 位置對不上 2D 圖**：T 可能挑到倍數／分數週期。調整 **T Search Maximum**（例如先設小一點），或改用 **Dip candidate frequencies** 交叉比對。
- **「The T search range is too large」**：頻率範圍太寬或上限太大；降低 **T Search Maximum**。
- **全域擬合「There are no free parameters」或「Fewer than two usable slices」**：檢查角色設定（全部是 Fixed？）與 **Use successful slices only**。
- **Soft link 報錯「requires the phase parameter to be free」**：參數表中 `phi` 被勾了 Fixed。
