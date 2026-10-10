# 模型、建構器與模型庫
> 了解內建模型的物理假設與單位；需要時用模型建構器寫自己的模型，存進模型庫重複使用。

## 最短流程（用建構器做一個新模型）
1. **開啟建構器。** YIG 分析視窗左側 **FIT MODEL** → **Model Builder...**。 ![](model_builder.png)
2. **寫理想模型。** **1. Ideal Model**：填 **Function Name**、**Frequency Variable**（預設 `w`，單位 Hz），在 **S_ideal =** 輸入運算式，可用 Python 寫法或 LaTeX，例如 `1 - \frac{\kappa_e}{i(w-w_0)+\kappa/2}`。上方即時顯示渲染後的公式。
3. **設定參數。** **2. Parameters** 會列出運算式中的參數：填單位（GHz、MHz、rad、ns…）；初值與上下界可以留白，擬合時會依資料估計。
4. **加上選用項。** **3. Optional Model Terms**：**Fano phase**（S = B + (S_ideal − B)·e^{iθ_F}）、**Environment**（A·exp{i[φ₀ − 2π(w − w_c)τ]}，並選 **Delay Reference w_c**）、**Conjugate output**（IQ 繞行方向相反時）、**Also generate a |S| model**。
5. **預覽、存檔並載入。** **Preview with Current Data** 用目前的資料畫出自動猜測的曲線；**Save and Load** 存成 `.py` 並直接載入主視窗。勾 **4. Model Library** 的 **Add to Model Library when saving** 可同時存進模型庫並附上標題與備註。

## 內建模型的物理假設
**`S11_single`（單顆 YIG，反射；開啟分析視窗時預設載入的檔案 formulas_example.py）**
- 理想反射：r = 1 − κ_m·e^{iθ_F} ／ (Γ_m/2 − i(ω − ω_m − Δ_m))
- κ_m = κ_b·sin²φ（有效耦合）；Γ_m = (κ_m + α_r)／2；Δ_m = −(α_r／4)·sin 2φ
- 環境：S = A·e^{i[φ_0 − 2π(ω − ω_m)τ]}·r，最後取共軛（S11 的慣例）
- 單位：`w_m` GHz；`alpha_r`、`kappa_b` MHz；`phi`、`phi_0`、`theta_fano` rad；`t` ns；`A` 無因次
- 頻率都是「一般頻率」（Hz），不是角頻率。

**`S11_node`（檔案 formula_yig_node.py，配合相位／Node 使用）**
- 與 `S11_single` 相同，但頻移 Δ_m = −(γ_0／4)·sin 2φ 使用獨立參數 `gamma_0`（MHz），不再與 α_r 綁在一起。
- 建議角色：`kappa_b`、`alpha_r`、`gamma_0`、`t`、`theta_fano` 全片共用；`w_m`、`A`、`phi_0` 每片獨立；`phi` 由相位線決定。

**`S21_coupled`／`S21_single_mode`（檔案 formula_s21_coupled.py，穿透）**
- S_ideal = 1 + e^{iθ_F}·κ／[i(ω_p − ω_w) − (κ + α) + g²／(i(ω_p − ω_d) − ξ)]，ω_w 為波導模式、ω_d 為 YIG，g 為耦合；`single_mode` 為 g = 0。
- 單位：`w_w`、`w_d` GHz；`kappa`、`alpha`、`g`、`xi` MHz；`t` ns。
- 名稱結尾 `_abs` 的版本只擬合 |S|，φ_0 與 τ 在絕對值中消失，所以沒有這兩個參數。

## 選模型的原則
- 有相位資訊（複數 S）就用複數模型；只有 |S| 時用 `_abs` 版本，但 φ 相關參數會更難確定。
- 需要物理的 Node／Antinode 或相位連結時，必須用宣告 κ_m = κ_b·sin²φ 的模型（`S11_single`、`S11_node`）。
- 模型項越多（Fano、第二模式）越能貼合，但參數相關性越高、誤差越大；殘差已經像雜訊時，不要再加項。

## 模型庫
**Model Library...** 列出所有模型：可搜尋標題、函式與備註，選取後顯示方程式與備註。**Add Current Model** 把目前載入的模型加入；**Add from File...** 加入一個 `.py`；**Open in Model Builder** 用建構器開啟由建構器產生的模型；**Edit in Library...** 修改標題與備註；刪除時可選擇是否一併刪除模型檔。**Copy model files into the library when adding** 會把檔案複製到模型庫資料夾，原檔移走也不影響。

## 自己寫 `.py` 模型的規則
- 第一個參數是頻率 `w`（Hz）的函式都會出現在 **Model Function** 選單；底線開頭與 `guess_*` 函式會被忽略。
- 回傳值：`np.hstack([real, imag])`（長度 2N，複數擬合）、複數陣列（長度 N，自動拆開）、或實數陣列（長度 N，擬合 |S|）。
- 選用：`guess_<函式名>(freq, s)` 回傳 `{參數: (初值, 下界, 上界)}`，**Auto Guess** 會使用；沒有時用內建估計。
- 選用：`UNITS = {參數: 單位}` 或 `UNITS_<函式名>`；沒寫時依名稱推測（`_GHz`、`_MHz` 後綴、`theta`／`phi` → rad、`t` → ns）。頻率單位決定連續擬合如何移動視窗。

## 安全確認
`.py` 模型是一般的 Python 程式。載入前一律先做靜態檢查（不執行）：含有 `subprocess`、`os`、`socket`、`eval`、`exec`、`__import__` 等危險寫法的檔案會被**直接拒絕**。其他不認得的檔案第一次載入時會詢問是否信任；答案依檔案的 SHA-256 記住，檔案被修改後會再問一次。內建模型與建構器產生的模型自動信任。

## 做不出來時先檢查
- **「No usable model functions were found」**：函式第一個參數不是頻率，或參數少於兩個。
- **「This file was not created by the Model Builder」**：只有建構器產生的檔案能用 **Open in Model Builder** 開啟。
- **「Model file not found … using the bundled model of the same name」**：原本的模型檔被移走了，程式改用同名的內建模型；請確認這是你要的模型。
- **載入時被拒絕**：檔案含有被禁止的程式碼；把數學以外的部分移除。
- **擬合結果單位怪怪的**：檢查參數表 **Unit** 欄；建構器勾 **Convert units to Hz / s / rad** 時，運算式要用 SI 單位寫。
