# 連續擬合
> 對 2D 掃描（例如磁場或電流掃描）的每一片自動擬合，讓擬合視窗跟著共振頻率移動，得到參數隨掃描變化的曲線。

## 最短流程
1. **先把一片擬合好。** 照〈單條 trace 擬合〉在某一片（最好是訊號清楚的一片）完成擬合，再按 **Fitted → Initial**。這一片的頻率範圍寬度就是之後每一片的視窗寬度。 ![](cont_seed.png)
2. **設定範圍與追蹤參數。** 左側 **CONTINUOUS FIT SETTINGS**：用 **Start／End Slice**（旁邊 **Current** 帶入目前切片）決定要擬合哪些片、**Stride** 每幾片擬一次；**Tracking Parameter 1** 選共振頻率參數（例如 `w_m`）。下方文字會顯示視窗寬度與片數。 ![](cont_settings.png)
3. **開始。** 按 **Start Continuous Fit**。進度條顯示第幾片；擬合在背景程序執行，視窗可以繼續操作，**Cancel** 可隨時停止。 ![](cont_run.png)
4. **看結果。** 切到 **Continuous Fit** 分頁：上方表格每列一片（成功與否、R²、參數），下方圖表 **All Parameters** 一次看全部參數、**Single Parameter** 用 **Y:／X:** 選一個參數對掃描軸作圖。點一列後按 **Preview Selected Slice** 可以看那一片的擬合圖。 ![](cont_results.png)

## 結果代表什麼
- 每一片都是獨立的單條擬合，R²、χ²_red 的意義與單條擬合相同（見〈單條 trace 擬合〉）。
- **ok = 否** 的片不會拿來移動視窗；原因寫在 **msg** 欄（例如 R² 低於門檻、點數不足、擬合失敗）。
- 參數曲線若在某處突然跳動，通常是視窗「跳到」旁邊的另一個模式或雜訊，而不是物理現象；用 **Preview Selected Slice** 看那一片確認。
- 在 Node 附近訊號變弱（κ_eff → 0），那裡的 `kappa`、`phi` 誤差會變大、甚至失敗，這是預期的物理結果。要穿過 Node 請用〈相位／Node〉的相位連結擬合。

## 視窗怎麼移動（重要）
- **視窗 1**：中心 = 上一個「成功」片擬合出的 Tracking Parameter 1 + 偏移量；偏移量 = 你設定的範圍中心 − 追蹤參數的初值，寬度 = 你目前設定的頻率範圍寬度。
- **Tracking Parameter 2（選用）**：第二個模式用自己的 **Window 2 Width／Offset**；擬合資料是兩個視窗的聯集。**= Window 1** 讓寬度相同。
- **Bound frequency parameters to fit window**（預設開）：共振頻率類參數的上下界限制在視窗內，避免跑到視窗外。
- 追蹤參數必須是頻率單位（Hz／kHz／MHz／GHz）；只有數值落在量測頻率範圍內的頻率參數會被視為「位置」而跟著移動，線寬類（同為 MHz）不會。

## 選項與參數
- **Initial Guess Source**：
  - **Previous Fit**（預設）：用上一片的擬合結果當初值，最穩定。
  - **Table Initial Guess (shift with window)**：每片都從參數表的初值出發，只把頻率參數平移到新視窗。
  - **Auto Guess per Slice**：每片重新自動猜初值，適合各片差異大、但較容易不連續。
- **R² Threshold**（預設 0.9）：R² (dB) 低於此值的片視為失敗，不更新追蹤。
- **Stop on failure**：遇到第一個失敗就停止。
- **滾動追蹤表**（Track、Parameter、Unit、± Range、Extrapolate）：勾 **Track** 的參數，初值 = 上一片的值（或勾 **Extrapolate** 時用前兩片線性外推），上下界 = 初值 ± Range。適合 `phi` 這類會連續變化的非頻率參數。**Keep rolling bounds within parameter bounds** 讓滾動範圍不超出參數表邊界。
- 前處理（Hampel、手動排除）會套用到每一片。

## 輸出
- **Export CSV**：每片一列，含視窗範圍、點數、ok、R²、R² (complex)、χ²_red、nfev、msg，以及每個參數與誤差；第一行是中繼資料（模型、單位、設定）。**Load CSV** 可以重新載入之前的結果。
- 勾 **OUTPUT → Export Continuous Fit results automatically** 會在完成時自動存到輸出資料夾（並附參數圖 PNG）。
- 執行中會持續自動備份到輸出資料夾的 `<檔名>_<模型>_batch_autosave.csv`，程式意外關閉也不會全部遺失。
- **Selected Fit → Initial**：把選取那一片的結果放回參數表，方便重擬或當作新的起點。

## 做不出來時先檢查
- **一開始就大量失敗**：起始片沒擬好或視窗太窄。回到步驟 1，在清楚的一片擬好並 **Fitted → Initial**。
- **追蹤中途「跑掉」**：視窗太寬會抓到相鄰模式，太窄會在共振快速移動時跟丟。調整範圍寬度，或把 **Stride** 設 1、改從另一端（Start > End 也可以）開始。
- **「Tracking parameter … has non-frequency unit」**：追蹤參數的 **Unit** 不是頻率單位；到參數表修正單位。
- **「Too few points in the fit window」**：視窗移出了量測頻率範圍，或排除太多點。
- **Node 附近連續失敗**：物理上訊號消失；用〈相位／Node〉的 **Skip slices near Nodes** 或相位連結擬合。
