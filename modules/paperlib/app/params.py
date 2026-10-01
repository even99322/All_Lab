"""參數：magnon 參數名稱對照表、各平台預估值表、magnon ↔ qubit／cavity 語言對照，以及每篇論文的參數（AI 抽取＋人工修改）。

三張參考表的內容在第一次升級時寫入資料庫（seed），之後由有「管理」權限的人在網站上修改；
論文參數存在 paper_params，單位統一用 param_defs.unit（頻率類一律是「÷2π 之後的 Hz 單位」、線寬一律換成 HWHM）。
"""
import csv
import io
import json
import math
import re

from . import ai, db
from .jobs import handler

# ------------------------------------------------------------------ 參數名稱對照表（預設內容）
# key, 名稱, 符號, 其他寫法（逗號分隔）, 統一單位, 分群, 定義, 慣例與陷阱, qubit／cavity 對應, 放進比較表
DEFS = [
    ("omega_m", "magnon 頻率（Kittel 模）", "ω_m/2π", "f_m, ω_K, f_K, f_FMR, ω_r（部分文獻）, Kittel mode, uniform mode", "GHz", "頻率與磁場",
     "均勻進動模的共振頻率。球體 ω_m = γ(B_0 + μ0H_an)（球的退磁項相消）；薄膜用 Kittel 公式 ω = γ√(B(B+μ0M_s))（面內）或 γ(B − μ0M_s)（垂直）。",
     "文獻寫 ω_m 常其實是 ω_m/2π（單位 Hz）；若單位是 rad/s 要除以 2π。", "resonator 頻率 ω_r；接 qubit 時對應被耦合的玻色模，而不是 qubit 本身", 1),
    ("omega_c", "腔／光子模頻率", "ω_c/2π", "ω_a, ω_r, f_c, f_0, cavity mode, photon mode", "GHz", "頻率與磁場",
     "與 magnon 耦合的微波腔或平面共振器模態頻率。", "同上，注意 rad/s 與 Hz。", "resonator 頻率 ω_r（cQED）；cavity QED 的腔頻 ω_c", 1),
    ("delta", "失諧", "Δ/2π", "δ, detuning, ω_m − ω_c", "MHz", "頻率與磁場",
     "Δ = ω_m − ω_c；掃磁場時 Δ 隨 B 線性變化。", "有些文獻定義 Δ = ω_c − ω_m，正負號相反。", "qubit–resonator 失諧 Δ = ω_q − ω_r", 0),
    ("b0", "偏置磁場", "μ0H_0", "B_0, H_0, H_ext, H_bias, B_ext, static field", "mT", "頻率與磁場",
     "決定 magnon 頻率的靜磁場（量測時通常是反交叉中心的磁場）。", "真空中 1 Oe ↔ 1 G ↔ 0.1 mT；H[A/m] × μ0 = B[T]。", "flux bias Φ（調 transmon 頻率）；原子的 Zeeman 場", 1),
    ("g_mc", "magnon–光子耦合強度", "g/2π", "g_m, g_mc, g_eff, g_c, g_ma, J, coupling strength, 反交叉間距的一半", "MHz", "耦合",
     "Kittel 模與腔模的集體耦合率，g = g_0√N。在反交叉點兩支模態相距 2g（真空 Rabi 分裂）。",
     "有的論文報的是分裂 2g，要除以 2；rad/s 要 ÷2π。", "qubit–resonator 耦合 g（Jaynes–Cummings）；原子–腔耦合 g", 1),
    ("g0", "單自旋耦合", "g_0/2π", "g_s, single-spin coupling", "Hz", "耦合",
     "單一自旋與腔模的耦合，約 (γ/2)√(μ0ħω_c/V_c)（填充與偏振因子≈1 時）。", "通常只有 mHz～Hz，集體耦合才靠 √N 放大。", "單一 qubit／單一原子的 g（沒有 √N 放大）", 0),
    ("n_spin", "自旋數", "N", "N_s, number of spins", "", "耦合",
     "參與 Kittel 模的自旋數；YIG 自旋密度約 2.1×10²² cm⁻³，1 mm 球約 10¹⁹ 個。", "", "Tavis–Cummings 模型的原子數 N", 0),
    ("j_coh", "相干耦合（實數耦合）", "J/2π", "J, g_coh, ω_12, coherent coupling, exchange coupling", "MHz", "耦合",
     "兩個模態之間的厄米耦合，產生能級排斥（level repulsion）。經波導的間接耦合 J ∝ sin φ。",
     "與耗散耦合同時存在時寫成 J − iΓ；兩者的比例由傳播相位 φ 決定。", "qubit–qubit 耦合 J；波導 QED 的交換作用 J_12", 1),
    ("gamma_d", "耗散耦合", "Γ/2π", "Γ, g_d, γ_d, κ_12, dissipative coupling, ig", "MHz", "耦合",
     "透過共同耗散通道（行波、波導）形成的虛數耦合，產生能級吸引（level attraction）。經波導時 Γ ∝ cos φ。",
     "符號與 i 的位置各文獻不同（J − iΓ、g e^{iΦ}、Γ√(κ1κ2) 形式）；請看原文的 H_eff。", "波導 QED 的關聯衰減 Γ_12（superradiance／subradiance 的來源）", 1),
    ("kappa_m", "magnon 損耗率（線寬）", "κ_m/2π", "γ, γ_m, κ_m, δ_m, Δf_m, magnon linewidth, damping rate", "MHz", "損耗與線寬",
     "Kittel 模的總（振幅）衰減率，統一記 HWHM。均勻展寬部分 κ_m ≈ α ω_m。",
     "先確認 HWHM 或 FWHM（FWHM = 2 × HWHM）；γ 同時也常指旋磁比，看上下文。", "qubit 衰減 γ_1 = 1/T_1（去相干 γ_2 = γ_1/2 + γ_φ）；原子自發輻射 Γ", 1),
    ("kappa_c", "腔損耗率", "κ_c/2π", "κ, κ_a, κ_r, Γ_c, cavity linewidth", "MHz", "損耗與線寬",
     "腔模總損耗 κ_c = κ_int + κ_ext，統一記 HWHM。", "Q = ω_c/(2κ_c)（κ 為 HWHM）＝ ω_c/κ_FWHM。", "resonator 的 κ（cQED）；cavity QED 的 κ", 1),
    ("kappa_ext", "外部（輻射）耦合率", "κ_e/2π", "κ_ext, κ_1, κ_2, γ_e, Γ_1D, Γ_r, radiative damping, external damping", "MHz", "損耗與線寬",
     "模態漏到量測埠（傳輸線、波導）的速率，決定 S 參數凹陷的深度；雙埠 κ_e = κ_1 + κ_2。",
     "臨界耦合 κ_e = κ_i；波導中「每個方向」的漏失率與總和容易混淆。", "κ_ext（cQED）；波導 QED 的 Γ_1D（輻射進波導）", 1),
    ("kappa_int", "內部損耗率", "κ_i/2π", "κ_int, κ_0, γ_int, κ_nr, Γ', intrinsic damping, non-radiative", "MHz", "損耗與線寬",
     "材料、傳導、表面等本身的耗散。", "", "κ_int（cQED）；波導 QED 的 Γ'（非輻射或漏到其他通道）", 1),
    ("alpha_g", "Gilbert 阻尼", "α", "α_G, α_eff, damping constant", "", "損耗與線寬",
     "LLG 方程的無因次阻尼。κ_m(HWHM) ≈ α ω_m；FMR 場線寬 μ0ΔH(FWHM) = μ0ΔH_0 + 2αω/γ。",
     "ΔH_0 是非均勻展寬，不能算進 α。", "沒有直接對應；角色類似材料本身的 1/Q", 1),
    ("dh", "FMR 場線寬", "μ0ΔH", "ΔH, ΔH_pp, ΔH_FWHM, field linewidth", "mT", "損耗與線寬",
     "以掃磁場量到的共振線寬。換成頻率線寬：Δf = (γ/2π)·μ0ΔH。",
     "微分訊號的 peak-to-peak 與 FWHM 差 √3 倍（Lorentzian：ΔH_FWHM = √3 ΔH_pp）。", "", 0),
    ("q_factor", "品質因子", "Q", "Q_L, Q_i, Q_e, Q_c, loaded Q", "", "損耗與線寬",
     "Q = ω/(2κ)（κ 為 HWHM）。", "載入 Q：1/Q_L = 1/Q_i + 1/Q_e。", "resonator Q（cQED 同名）", 0),
    ("coop", "協同度", "C", "cooperativity, C_mc", "", "導出量與區間",
     "C = g²/(κ_m κ_c)（κ 為 HWHM）＝ 4g²/(κ_FWHM γ_FWHM)。C > 1 才看得到明顯的混成。",
     "兩種寫法數值相同，前提是每個 κ 的定義一致；混用 HWHM/FWHM 會差 4 倍。", "cQED 的 C = 4g²/(κγ)（同一概念）", 1),
    ("regime", "耦合區間", "", "strong, weak, Purcell, MIT, ultrastrong (USC), deep strong", "", "導出量與區間",
     "強耦合 g > κ_m, κ_c；Purcell 區 κ_c > g > κ_m；磁致透明（MIT）κ_m > g > κ_c；超強耦合 g/ω ≳ 0.1。", "", "cQED 同名（strong、dispersive、USC、DSC）", 1),
    ("chi", "色散位移", "χ/2π", "χ, dispersive shift, g²/Δ", "MHz", "導出量與區間",
     "|Δ| ≫ g 時兩模態互相推移約 g²/Δ。", "magnon–qubit 系統裡 qubit 譜線會依 magnon 數分裂（number splitting）。", "dispersive shift χ（cQED 讀取）；原子的 AC Stark shift", 0),
    ("kerr", "magnon Kerr 係數", "K/2π", "K, K_m, Kerr coefficient, magnon nonlinearity", "nHz", "導出量與區間",
     "H_K = ħK (m†m)²，來自磁晶異向性；與樣品體積成反比，需大量 magnon 才顯現（雙穩態）。", "正負號與晶軸方向有關。", "transmon 的非諧性 α（= −E_C/ħ）與 self-Kerr", 0),
    ("ep", "例外點（EP）條件", "", "exceptional point, EP2, EP3, PT／anti-PT", "", "非厄米與波導",
     "兩模態非厄米系統 [[ω−iκ1, g],[g, ω−iκ2]] 在 g = |κ1 − κ2|/2 時本徵值與本徵向量同時合併（κ 為 HWHM）。", "條件依模型與耦合型態（相干／耗散）而不同，請看原文 H_eff。", "非厄米 qubit／腔系統同名", 1),
    ("phase", "傳播相位", "φ", "kd, θ, 2πd/λ, ωτ, round-trip phase", "rad", "非厄米與波導",
     "兩個耦合點之間（或樣品到鏡面）的傳播相位，決定相干耦合與耗散耦合的比例。",
     "鏡面架設常用來回相位 2kd；節點／腹點的判斷要看是電場還是磁場（電流）耦合。", "巨原子各耦合點間的相位；鏡前原子的 atom–mirror 相位", 1),
    ("nonrecip", "非互易比／隔離度", "ΔS21", "isolation, nonreciprocity, |S21|/|S12|", "dB", "非厄米與波導",
     "正向與反向傳輸的差。", "", "", 1),
    ("ms", "飽和磁化", "μ0M_s", "M_s, 4πM_s, saturation magnetization", "mT", "材料與樣品",
     "YIG 室溫 4πM_s ≈ 1750 G（175 mT），低溫約 2470 G。", "4πM_s[G] 數值 ≈ μ0M_s[mT] × 10；M_s[kA/m] × 1.2566 = μ0M_s[mT]。", "", 0),
    ("gyro", "旋磁比", "γ/2π", "γ_e, g-factor", "GHz/T", "材料與樣品",
     "電子 γ/2π ≈ 28.0 GHz/T（g ≈ 2）。", "γ 也常被拿來代表線寬，看上下文。", "", 0),
    ("material", "材料", "", "YIG, Bi:YIG, Py (NiFe), CoFeB, LSMO, 反鐵磁", "", "材料與樣品", "磁性材料。", "", "", 1),
    ("size", "樣品尺寸", "d", "diameter, thickness, t", "mm", "材料與樣品", "球徑或膜厚（統一記 mm）。", "", "", 1),
    ("temp", "溫度", "T", "temperature, base temperature", "K", "量測與環境",
     "量測溫度。", "GHz 要進量子區需 ħω ≫ k_BT：5 GHz 對應約 240 mK。", "cQED 在 10–50 mK 稀釋製冷機", 1),
    ("power", "輸入功率", "P_in", "drive power, P, probe power", "dBm", "量測與環境", "送進樣品的微波功率（注意是否已扣除線路衰減）。", "", "驅動強度 ε／平均光子數 n̄", 0),
    ("platform", "架設／平台", "", "3D cavity, CPW, LC, waveguide, stripline, loop", "", "量測與環境",
     "耦合結構，例如 3D 腔、共平面波導、LC 共振器、開放波導、鏡面。", "", "", 1),
]

# ------------------------------------------------------------------ 各平台預估值（數量級，供初估）
_SRC = "常見數量級（回顧文章與代表性實驗的典型範圍，請以原文核對）"
TYPICAL = [
    ("YIG 球＋3D 微波腔（室溫）", "omega_m", "5–15 GHz（B ≈ 0.2–0.5 T）", 5, 15, "", _SRC),
    ("YIG 球＋3D 微波腔（室溫）", "g_mc", "10–100 MHz（0.3–1 mm 球）；大球／高填充可達數百 MHz", 10, 100, "g ∝ √(樣品體積／腔體積)", _SRC),
    ("YIG 球＋3D 微波腔（室溫）", "kappa_m", "0.5–3 MHz（HWHM，拋光球）", 0.5, 3, "表面粗糙與溫度會增加線寬", _SRC),
    ("YIG 球＋3D 微波腔（室溫）", "kappa_c", "1–10 MHz（銅腔）", 1, 10, "", _SRC),
    ("YIG 球＋3D 微波腔（室溫）", "coop", "10²–10⁴", 1e2, 1e4, "", _SRC),
    ("YIG 球＋3D 微波腔（室溫）", "alpha_g", "3×10⁻⁵–10⁻⁴", 3e-5, 1e-4, "", _SRC),
    ("YIG 球＋3D 腔（mK）", "kappa_m", "≈ 1 MHz", 0.5, 2, "低溫下 YIG 線寬改善有限（雜質弛豫）", _SRC),
    ("YIG 球＋3D 腔（mK）", "kappa_c", "0.1–2 MHz（超導或高 Q 腔）", 0.1, 2, "", _SRC),
    ("YIG 球＋3D 腔（mK）", "temp", "10–50 mK", 0.01, 0.05, "", _SRC),
    ("YIG 球／膜＋平面共振器（CPW、LC）", "g_mc", "數十～數百 MHz", 10, 500, "模態體積小，g_0 較大", _SRC),
    ("YIG 球／膜＋平面共振器（CPW、LC）", "kappa_c", "0.1–10 MHz（超導較小）", 0.1, 10, "", _SRC),
    ("YIG＋開放波導／傳輸線（無腔）", "kappa_ext", "0.1–10 MHz（依位置與磁場分布）", 0.1, 10, "對應波導 QED 的 Γ_1D", _SRC),
    ("YIG＋開放波導／傳輸線（無腔）", "kappa_m", "0.5–3 MHz（本徵部分）", 0.5, 3, "", _SRC),
    ("YIG＋開放波導／傳輸線（無腔）", "phase", "0–2π（由耦合點距離與頻率決定）", 0, 6.283, "", _SRC),
    ("magnon＋超導 qubit（mK）", "g_mc", "magnon–qubit 有效耦合約數 MHz～十數 MHz", 1, 20, "通常經由共同的腔模間接耦合", _SRC),
    ("magnon＋超導 qubit（mK）", "temp", "10–50 mK", 0.01, 0.05, "", _SRC),
    ("金屬鐵磁膜（Py、CoFeB）＋共振器", "alpha_g", "5×10⁻³–10⁻²", 5e-3, 1e-2, "", _SRC),
    ("金屬鐵磁膜（Py、CoFeB）＋共振器", "kappa_m", "100 MHz–1 GHz", 100, 1000, "線寬大，強耦合較難", _SRC),
    ("金屬鐵磁膜（Py、CoFeB）＋共振器", "g_mc", "10–200 MHz", 10, 200, "", _SRC),
    ("材料常數", "ms", "YIG：室溫 175 mT（4πM_s ≈ 1750 G）；低溫約 247 mT", 175, 247, "", "材料常數"),
    ("材料常數", "gyro", "28.0 GHz/T", 28, 28, "", "物理常數"),
    ("材料常數", "n_spin", "YIG 自旋密度 2.1×10²² cm⁻³；1 mm 球約 10¹⁹", 1e19, 1e19, "", "材料常數"),
]

# ------------------------------------------------------------------ magnon ↔ qubit／cavity 語言對照
# 分群, magnon 描述, 超導電路（cQED）, 原子腔／波導 QED, 公式或換算, 備註
MAP = [
    ("模態本身", "Kittel 模（大量自旋的均勻進動，集體激發）", "諧振子（resonator）；受強驅動或耦合到 qubit 才顯出非線性", "原子系綜（低激發時的集體模）",
     "Holstein–Primakoff：S⁻ ≈ √(2S) m", "magnon 在低激發時近乎線性，比較像 resonator 而不是 two-level qubit"),
    ("模態本身", "調磁場改變 ω_m（Zeeman 調頻）", "用磁通 Φ 調 transmon 頻率", "Zeeman／Stark 調原子頻率", "ω_m = γ(B_0 + μ0H_an)", ""),
    ("哈密頓量", "H = ħω_c a†a + ħω_m m†m + ħg(a†m + a m†)", "兩個耦合諧振子（beam-splitter）；耦合到 qubit 時是 Jaynes–Cummings", "Tavis–Cummings（N 個原子）",
     "旋轉波近似；g/ω ≳ 0.1 時要加 a m + a†m† 項", ""),
    ("耦合", "magnon–光子耦合 g（反交叉間距 2g）", "qubit–resonator 耦合 g（真空 Rabi 分裂 2g）", "原子–腔耦合 g", "g = g_0√N", "集體放大 √N 是 magnon 耦合大的原因"),
    ("耦合", "耗散耦合 Γ（level attraction）", "經共同傳輸線的關聯衰減", "波導 QED 的 Γ_12（superradiance／subradiance）",
     "H_eff 非對角項 J − iΓ；經波導時 J ∝ sin φ、Γ ∝ cos φ", "相干耦合 J 產生能級排斥；耗散耦合 Γ 產生能級吸引"),
    ("損耗", "magnon 線寬 κ_m（HWHM）", "qubit γ_1 = 1/T_1；γ_2 = γ_1/2 + γ_φ", "自發輻射率 Γ", "κ_m ≈ α ω_m（均勻展寬部分）", "注意 HWHM／FWHM"),
    ("損耗", "腔線寬 κ_c", "resonator κ", "腔衰減 κ", "Q = ω_c/(2κ_c)", ""),
    ("損耗", "外部耦合 κ_e ／內部損耗 κ_i", "κ_ext／κ_int（Q_e、Q_i）", "Γ_1D／Γ'（β 因子）", "β = κ_e/(κ_e + κ_i)", "β 越接近 1，S 參數凹陷越深"),
    ("區間", "強耦合 g > κ_m, κ_c", "strong coupling g > κ, γ", "strong coupling", "協同度 C = g²/(κ_m κ_c) = 4g²/(κγ)_FWHM", ""),
    ("區間", "色散區 |Δ| ≫ g", "dispersive regime：χ = g²/Δ，用來讀取 qubit", "AC Stark／light shift", "χ ≈ g²/Δ", "magnon–qubit 可依此數 magnon 數"),
    ("區間", "Purcell：magnon 經腔輻射", "qubit Purcell 衰減", "Purcell 增強", "κ_P ≈ (g/Δ)² κ_c", ""),
    ("非線性", "magnon Kerr：ħK(m†m)²（雙穩態）", "transmon 非諧性 α = −E_C/ħ、self-Kerr", "兩能階的飽和", "", "magnon 的 K 很小，要大量激發才看得到"),
    ("量測", "S21 側耦合凹陷：S21 = 1 − κ_e/[κ_e + κ_i − i(ω − ω_m)]", "notch-type resonator 的傳輸", "單一原子在波導中的穿透 t = 1 − β（共振時）",
     "共振時 S21 = κ_i/(κ_i + κ_e)", "κ 為振幅衰減率（HWHM）；鏡面、雙埠時係數會變"),
    ("量測", "反交叉圖（S21 對頻率與磁場的二維圖）", "qubit–resonator 反交叉（頻率 vs 磁通）", "vacuum Rabi splitting 光譜", "", ""),
    ("非厄米", "例外點（EP）", "非厄米 qubit／腔的 EP（PT、anti-PT）", "同名", "兩模態：g = |κ1 − κ2|/2", ""),
    ("波導", "YIG 在波導短路端（鏡面）前：節點／腹點", "鏡前的 transmon（atom in front of a mirror）", "鏡前原子",
     "有效輻射 ≈ Γ_1D(1 + cos φ) = 2Γ_1D cos²(φ/2)；Lamb shift ∝ sin φ", "鏡子型態（開路／短路）與耦合場（電場／磁場）會讓 cos 變 −cos"),
    ("波導", "magnon 以多點（或延伸）方式耦合到波導", "巨原子（giant atom）：transmon 在多個點耦合到傳輸線", "巨原子", "耦合點間相位 φ = ωτ 造成頻率相依的耦合與無退相干交換", ""),
    ("環境", "室溫 GHz magnon 熱占據 n_th ≈ k_BT/ħω ~ 10³（古典）", "mK 下 n_th ≪ 1（量子）", "光頻原子室溫即量子", "n_th = 1/(e^{ħω/k_BT} − 1)", ""),
]


def seed(con) -> None:
    """寫入三張參考表的預設內容（只在表是空的時候）。"""
    if not con.execute("SELECT 1 FROM param_defs").fetchone():
        for i, d in enumerate(DEFS):
            con.execute("INSERT INTO param_defs(key, name, symbol, aliases, unit, grp, definition, convention, cqed, in_compare, sort) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (*d, i))
    if not con.execute("SELECT 1 FROM param_typical").fetchone():
        for i, t in enumerate(TYPICAL):
            con.execute("INSERT INTO param_typical(platform, key, range_text, lo, hi, note, source, sort) VALUES(?,?,?,?,?,?,?,?)", (*t, i))
    if not con.execute("SELECT 1 FROM param_map").fetchone():
        for i, m in enumerate(MAP):
            con.execute("INSERT INTO param_map(grp, magnon, cqed, cavity, formula, note, sort) VALUES(?,?,?,?,?,?,?)", (*m, i))


def defs(con, compare_only=False) -> list[dict]:
    q = "SELECT * FROM param_defs" + (" WHERE in_compare=1" if compare_only else "") + " ORDER BY sort, id"
    return [dict(r) for r in con.execute(q)]


# ------------------------------------------------------------------ 數值解析
_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")


def parse_num(s) -> float | None:
    """'≈ 10.5 MHz' → 10.5；'3×10⁻⁵' → 3e-5；'10–20' → 10（取第一個數）。"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s) if math.isfinite(s) else None
    t = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+", lambda m: "^" + m.group(0).translate(_SUP), str(s))
    t = t.replace("−", "-").replace("–", " ").replace("—", " ").replace(",", "").split("=")[-1].replace("2π", "")
    cands = []
    for rx, fn in ((r"(-?\d+(?:\.\d+)?)\s*[x×*]\s*10\s*\^\s*\(?([-+]?\d+)", lambda m: float(m.group(1)) * 10 ** int(m.group(2))),
                   (r"(?<![\d.])10\s*\^\s*\(?([-+]?\d+)", lambda m: 10.0 ** int(m.group(1))),
                   (r"(?<![\d.])(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", lambda m: float(m.group(1)))):
        m = re.search(rx, t)
        if m:
            cands.append((m.start(), -len(m.group(0)), fn(m)))
    if not cands:
        return None
    return sorted(cands)[0][2]


# ------------------------------------------------------------------ 論文參數
def paper_params(con, pid: int) -> dict:
    return {r["key"]: dict(r) for r in con.execute(
        "SELECT pp.*, u.display_name AS who FROM paper_params pp LEFT JOIN users u ON u.id=pp.author_id WHERE pp.paper_id=?", (pid,))}


def set_param(con, pid: int, key: str, value: str, user_id=None, source="human", raw="", note="", page=None) -> None:
    value = str(value or "").strip()
    if not value:
        con.execute("DELETE FROM paper_params WHERE paper_id=? AND key=?", (pid, key))
        return
    con.execute("INSERT INTO paper_params(paper_id, key, value, num, raw, note, page, source, author_id, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(paper_id, key) DO UPDATE SET value=excluded.value, num=excluded.num, raw=excluded.raw, note=excluded.note, "
                "page=excluded.page, source=excluded.source, author_id=excluded.author_id, updated_at=excluded.updated_at",
                (pid, key, value[:300], parse_num(value), str(raw or "")[:500], str(note or "")[:500], page, source, user_id, db.now()))


def extract(con, pid: int) -> dict:
    """AI 讀全文，依對照表抽出參數。回傳 {key: {value, raw, note, page}}，數值已換成對照表的單位。"""
    c = ai.config(con)
    p, body = ai._paper_text(con, pid, c["max_chars"])
    if len(body) < 200 and not p["abstract"]:
        raise RuntimeError("這篇沒有可讀的全文（可能是掃描檔，請先做 OCR）")
    ds = defs(con, compare_only=True)
    spec = "\n".join(f"- {d['key']}：{d['name']}（{d['symbol'] or '—'}；其他寫法：{d['aliases']}；單位：{d['unit'] or '無'}）"
                     + (f" 注意：{d['convention']}" if d["convention"] else "") for d in ds)
    system = ("你是凝態物理實驗室（magnon、腔／波導 QED、非厄米物理）的研究助理，負責從論文抽出實驗或理論參數。"
              "只輸出一個 JSON 物件，key 必須是清單裡的 key。每個值是物件：{\"value\": 換成指定單位後的值（數字或簡短文字，可寫範圍如 \"10–20\"）, "
              "\"raw\": 論文原本的寫法（含原單位）, \"page\": 出現的頁碼或 null, \"note\": 條件或換算說明（例如『原文為 FWHM，已除以 2』『原文 rad/s，已除以 2π』）}。"
              "頻率與速率一律換成 ω/2π 的 Hz 單位；線寬一律換成 HWHM。論文沒提到的參數不要輸出，不要猜。多個樣品或多組數值時，填主要結果那組，其他寫在 note。")
    user = f"參數清單：\n{spec}\n\n標題：{p['title']}\n期刊：{p['venue']} {p['year'] or ''}\n摘要：{p['abstract']}\n\n全文：\n{body}"
    out = ai._json_from(ai.chat(con, system, user, 3000))
    if not isinstance(out, dict):
        raise ValueError("AI 回傳格式不符")
    keys = {d["key"] for d in ds}
    res = {}
    for k, v in out.items():
        if k not in keys:
            continue
        if not isinstance(v, dict):
            v = {"value": v}
        val = str(v.get("value", "")).strip()
        if not val or val.lower() in ("null", "none", "n/a"):
            continue
        pg = v.get("page")
        res[k] = {"value": val, "raw": str(v.get("raw") or ""), "note": str(v.get("note") or ""),
                  "page": int(pg) if str(pg or "").isdigit() else None}
    return res


def apply_extracted(con, pid: int, found: dict, overwrite_ai_only=True) -> int:
    """寫入 AI 抽到的參數；人工填過的不覆蓋。"""
    cur = paper_params(con, pid)
    n = 0
    for k, v in found.items():
        if k in cur and (cur[k]["source"] != "ai" or not overwrite_ai_only):
            continue
        set_param(con, pid, k, v["value"], None, "ai", v.get("raw", ""), v.get("note", ""), v.get("page"))
        n += 1
    return n


@handler("params")
def _job(con, arg, progress):
    """批次抽參數：arg = {"ids": [...]} 或 {"cat": id}；已經有參數的論文略過（除非 redo）。"""
    a = json.loads(arg or "{}")
    ids = a.get("ids") or []
    if a.get("cat"):
        ids = [r["paper_id"] for r in con.execute("SELECT paper_id FROM paper_categories WHERE category_id=?", (a["cat"],))]
    if a.get("all"):
        ids = [r["id"] for r in con.execute("SELECT id FROM papers ORDER BY id")]
    if not a.get("redo"):
        have = {r["paper_id"] for r in con.execute("SELECT DISTINCT paper_id FROM paper_params")}
        ids = [i for i in ids if i not in have]
    done = skipped = failed = 0
    errs = []
    for i, pid in enumerate(ids):
        progress(f"{i + 1}/{len(ids)} 篇（完成 {done}、略過 {skipped}、失敗 {failed}）")
        try:
            found = extract(con, pid)
        except RuntimeError as e:
            if "沒有可讀的全文" in str(e):
                skipped += 1
                continue
            if "還沒設定" in str(e) or "無法沿用" in str(e):
                raise
            failed += 1; errs.append(f"#{pid}：{e}")
            continue
        except (ValueError, KeyError, IndexError) as e:
            failed += 1; errs.append(f"#{pid}：{e}")
            continue
        with db.tx() as c:
            apply_extracted(c, pid, found)
        done += 1
    return f"處理 {len(ids)} 篇：抽到參數 {done}、沒有全文 {skipped}、失敗 {failed}" + (f"（{'；'.join(errs[:3])}）" if errs else "")


# ------------------------------------------------------------------ 比較表
def compare(con, ids: list[int]) -> dict:
    ds = defs(con, compare_only=True)
    papers = []
    for pid in ids:
        p = con.execute("SELECT id, citekey, title, year, venue FROM papers WHERE id=?", (pid,)).fetchone()
        if p:
            papers.append(dict(p) | {"params": paper_params(con, pid)})
    used = [d for d in ds if any(d["key"] in p["params"] for p in papers)]
    return {"defs": ds, "used": [d["key"] for d in used], "papers": papers}


def compare_csv(con, ids: list[int]) -> str:
    t = compare(con, ids)
    cols = [d for d in t["defs"] if d["key"] in t["used"]]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["citekey", "year", "title"] + [f"{d['name']} {d['symbol']}".strip() + (f" [{d['unit']}]" if d["unit"] else "") for d in cols])
    for p in t["papers"]:
        w.writerow([p["citekey"], p["year"] or "", p["title"]] + [p["params"].get(d["key"], {}).get("value", "") for d in cols])
    return "﻿" + buf.getvalue()


def library_ranges(con) -> dict:
    """論文庫實際抽到的數值範圍（每個參數）：{key: {min, max, n}}。"""
    out = {}
    for r in con.execute("SELECT key, MIN(num) lo, MAX(num) hi, COUNT(*) n FROM paper_params WHERE num IS NOT NULL GROUP BY key"):
        out[r["key"]] = {"min": r["lo"], "max": r["hi"], "n": r["n"]}
    return out


def interpret(con, ids: list[int]) -> str:
    """請 AI 解讀比較表（Markdown）。"""
    c = ai.config(con)
    t = compare(con, ids)
    cols = [d for d in t["defs"] if d["key"] in t["used"]]
    if not cols:
        raise RuntimeError("這幾篇還沒有參數，請先抽取或填寫")
    lines = ["| 論文 | " + " | ".join(f"{d['name']}（{d['unit'] or '—'}）" for d in cols) + " |"]
    for p in t["papers"]:
        lines.append(f"| [{p['citekey']}] | " + " | ".join(p["params"].get(d["key"], {}).get("value", "—") for d in cols) + " |")
    mp = "\n".join(f"- {m['magnon']} ↔ {m['cqed']}" for m in (dict(r) for r in con.execute("SELECT magnon, cqed FROM param_map ORDER BY sort LIMIT 12")))
    system = ai.REVIEW_SYSTEM + ai._lab(c)
    user = (f"以下是幾篇論文的參數比較表（頻率、速率都是 ω/2π，線寬是 HWHM）：\n\n" + "\n".join(lines) +
            f"\n\nmagnon 與 cQED 的對應關係：\n{mp}\n\n請寫：\n## 一句話比較\n## 各篇落在哪個耦合區間（算出 C = g²/(κ_m κ_c) 或 g 與 κ 的比）\n"
            "## 數值差異的可能原因（樣品、架設、溫度）\n## 換成 cQED 的語言怎麼描述\n## 對我們實驗的啟示\n表格裡沒有的數值不要編造。")
    return ai.chat(con, system, user, 2500)
