# 單條 trace 擬合
> 把一條 S 參數 trace 擬合成 YIG 共振模型，得到共振頻率、線寬與耦合等參數，並判斷結果可不可信。

## 最短流程
1. **從 Viewer 開啟 YIG 分析。** 在 Viewer 開好一個含複數 S 參數的量測，按工具列的 **分析** 按鈕 → **YIG 鏡像分析⋯**。分析視窗使用這份資料的唯讀快照，原始檔不會被改。 ![](yig_open.png)
2. **選資料與頻率範圍。** 左側 **DATA**：選 **S 參數**；2D 資料再選 **Sweep 軸** 並用 **Slice** 滑桿或點左下 2D 圖挑一個切片。在 **Data Preview** 的 1D trace 上拖曳，框出共振附近的頻率範圍（也可直接填 **Frequency Start／Stop**）。 ![](yig_range.png)
3. **選模型。** **FIT MODEL** → **Model Library⋯** 選一個模型（單顆 YIG 反射量測用 `S11_single`），**Model Function** 下拉選單會列出檔案中的函式。 ![](yig_model.png)
4. **產生初值並預覽。** **SINGLE FIT** → **Auto Guess**：參數表的 **Initial／Lower／Upper** 會自動填好；按 **Preview Initial Fit** 看初值曲線有沒有大致落在資料上。 ![](yig_guess.png)
5. **擬合並看結果。** 按 **Start Fit**。完成後切到 **Fit Results**：6 個小圖（Magnitude、Phase、IQ Plane、Real、Imaginary、Residual）與右下方的文字報告；參數表多出 **Fitted** 與 **Error** 兩欄。 ![](yig_fit.png)

## 結果代表什麼
- **Fitted／Error**：擬合值與 1σ 標準誤差（由協方差矩陣求得，已依殘差縮放）。勾了 **Fixed** 的參數不擬合，報告中標示 *(fixed)*。
- **R² (dB)**：用 |S| 的 dB 值、只在擬合範圍內計算。對「凹陷深度與寬度」敏感，但看不到相位。
- **R² (complex)**：用複數 S 計算，同時檢查振幅與相位；複數模型時比 R² (dB) 更嚴格。
- **χ²_red**：加權殘差平方和除以自由度。只在同一份資料、同樣加權設定之間比較才有意義。
- **Residual 圖**：好的擬合殘差應像雜訊、沒有明顯的 S 形或尖峰；若在共振處有系統性的形狀，代表模型少了某一項（例如 Fano 相位、第二個模式）。
- **IQ Plane**：量測點應落在擬合出的圓上；圓的繞行方向相反時，模型需要取共軛（內建模型已處理 S11 的慣例）。

### 參數意義（內建 `S11_single`）
| 參數 | 單位 | 意義 |
|---|---|---|
| `w_m` | GHz | YIG 共振頻率 |
| `alpha_r` | MHz | 本徵損耗 |
| `kappa_b` | MHz | 最大耦合（位於 antinode 時） |
| `phi` | rad | YIG 在傳輸線上的位置相位；有效耦合 κ_m = κ_b·sin²φ |
| `A` | — | 背景振幅 |
| `phi_0` | rad | 背景相位 |
| `t` | ns | 電纜延遲 |
| `theta_fano` | rad | Fano 相位（共振峰的不對稱） |

**重要：** 單條 trace 只能確定 κ_eff = κ_b·sin²φ，κ_b 與 φ 無法分開（κ_b 大、φ 小 和 κ_b 小、φ 大 會給出同樣的曲線）。要分開兩者，請用 **連續擬合** 加 **相位／Node** 分頁。

## 選項與參數
- **PREPROCESSING**：**Hampel outlier filter** 以滾動中位數找出尖刺並排除（**Window Half-Width** 點數、**Threshold** 幾倍局部雜訊 σ、**Dilation** 尖刺兩側多排除幾點；**Components** 選只看 |S| 或 |S|、Re、Im 都檢查）。**Manual Exclusion** 可輸入頻率範圍（GHz），或勾 **Drag on trace to add exclusion range** 後在圖上拖曳。被排除的點會在圖上標出，不參與擬合。
- **FIT SETTINGS**：
  - **Resonance weighting σ = |S| + ε**（預設開）：|S| 小的共振凹陷處權重較大，能讓凹陷被擬合得更準；ε 越大越接近平均加權。
  - **method**：`trf`（預設，支援邊界）或 `dogbox`。
  - **loss**：`linear` 為一般最小平方法；`soft_l1`、`huber`、`cauchy`、`arctan` 會降低離群點的影響。
  - **maxfev**：最多評估次數；**ftol／xtol**：收斂容忍度。
- **參數表**：可輸入 `pi/2`、`np.deg2rad(30)` 這類運算式；**Unit** 欄可修改單位（頻率單位會影響連續擬合的視窗移動）。
- **Fitted → Initial**：把這次的擬合值當作下一次的初值（換切片時很有用）。
- **Save Settings／Load Settings**：把模型、參數表、擬合設定與頻率範圍存成 JSON。

## 輸出與資料安全
- **Copy Fit Report** 複製右下報告；**Export Result CSV** 輸出參數、誤差與中繼資料，並另存同名 PNG（6 個小圖）。
- **OUTPUT → Folder** 留白時存到來源資料旁的 `fit_results/`；勾 **Save single-fit CSV and plot automatically** 則每次擬合完自動存檔。
- 擬合設定與結果另外存在資料夾的 `fitting/` 中，量測 HDF5 檔永遠只被讀取。

## 做不出來時先檢查
- **「The number of data points is smaller than the number of free parameters」**：範圍太窄或排除太多點；擴大 Frequency 範圍或固定一些參數。
- **擬合很快結束但曲線明顯不對**：初值離太遠。先 **Auto Guess**、用 **Preview Initial Fit** 對照；共振頻率 `w_m` 的初值與上下界最重要。
- **參數卡在上下界**：Fitted 等於 Lower 或 Upper 時結果不可信，放寬邊界再擬合。
- **Error 是 nan 或非常大**：參數之間高度相關（例如同時放開 `kappa_b` 與 `phi`），或該參數對資料不敏感。固定其中一個。
- **R² (dB) 高但 R² (complex) 低**：振幅對但相位不對，檢查 `t`（延遲）與 `phi_0`，或 IQ 圖方向。
- **「Fit Failed」訊息**：多半是模型輸出長度與資料不符或運算溢位，確認模型函式與單位。
