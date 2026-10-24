"""YIG 參數說明視窗（v3.6.3）：每個參數影響什麼、即時試算（共振、線寬、掃頻點夠不夠、網格大小）、常見問題。

試算用的是 yig.py 同一套公式（Kittel、線寬、四面體估計），跟主視窗與建模時一致。
共振曲線是「示意」：用 Lorentzian 表示 FMR 吸收，深度不代表實際耦合強度。
"""
from __future__ import annotations

import math

import numpy as np
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (QDialog, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                             QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

import yig
from mode_help import _browser, _set_html, _themed, apply_theme, is_dark_palette, THEME

HTML_PARAMS = """
<h3>每個參數影響什麼</h3>
<table width="100%">
<tr><th width="16%">參數</th><th width="34%">影響</th><th width="30%">調大會怎樣</th><th width="20%">建議</th></tr>

<tr><td colspan="4"><b>幾何</b></td></tr>
<tr><td>直徑</td><td>球裡的自旋數 ∝ 體積 → <b>耦合強度</b>（約 ∝ √體積）；也決定網格大小、記憶體</td>
<td>耦合變強、共振更深；但會出現更多高階靜磁模（多個小共振），網格變多</td><td>跟實驗用的球一樣（例如 0.4、1.0、1.2 mm）</td></tr>
<tr><td>高度：球底離波導頂面</td><td>球離訊號線越遠，微波磁場越弱 → <b>耦合變弱</b>。最低 0 = 球底貼在波導頂面</td>
<td>耦合快速變弱（共振變淺、變窄）</td><td>照實驗架設（球通常貼著或隔一層薄膠帶，0–0.1 mm）</td></tr>
<tr><td>球心 Z</td><td>跟「高度」是同一件事，用絕對座標表示：球心 Z = 波導頂面 + 高度 + 半徑</td><td>同上</td><td>想對齊 AEDT 座標時用這個填</td></tr>
<tr><td>位置 X / Y</td><td>球在板子上的位置 → 那裡的微波磁場有多強、方向對不對</td><td>—</td>
<td>放在訊號線正上方（磁場最強、方向水平）；用 3D 預覽點頂點可以直接放到那一點上方</td></tr>
<tr><td>球體網格邊長</td><td>球裡四面體的大小 → <b>準確度與記憶體</b>（四面體數 ∝ 1/邊長³）</td>
<td>變粗：省記憶體、算得快；太粗共振頻率會偏</td><td>空白 = 自動（直徑 / 8）。估計超過 4 萬個會變紅</td></tr>

<tr><td colspan="4"><b>材料</b></td></tr>
<tr><td>4πMs</td><td>飽和磁化。① 外加場換成 HFSS 內部場時要扣 Ms/3（去磁）② 耦合強度約 ∝ √Ms</td>
<td>內部場變小（偏置不夠時會 ≤ 0、沒飽和）；耦合變強。球的 Kittel 頻率本身跟 Ms 無關</td><td>單晶球：室溫 0.180 T、10 K 約 0.248 T（見「文獻參數」）</td></tr>
<tr><td>ΔH</td><td>鐵磁共振線寬（損耗）→ <b>共振寬度與 Q 值</b>：線寬 ≈ γ·μ0ΔH</td>
<td>共振變寬、變淺；需要的掃頻點變少</td><td>室溫 0.27 Oe @ 9.4 GHz（α ≈ 4×10⁻⁵）；量到的總線寬約 0.3–0.5 Oe</td></tr>
<tr><td>ΔH 頻率</td><td>ΔH 是在哪個頻率量的；AEDT 用它換成阻尼係數，讓線寬隨頻率變</td><td>—</td><td>照材料規格（常見 9.4 GHz）；空白 = AEDT 預設</td></tr>
<tr><td>εr、tanδ</td><td>球的介電性質：高頻的介電共振、少量背景損耗</td><td>介電共振往低頻移；背景損耗變大</td><td>YIG εr ≈ 15、tanδ ≈ 2×10⁻⁴</td></tr>
<tr><td>Landé g</td><td>Kittel 頻率 f = g × 13.996 GHz/T × μ0H<sub>外加</sub>；v3.7 起也寫進 HFSS 材料（HFSS 預設 2.0）</td><td>共振頻率等比例變高（g 差 0.005 → 7 GHz 差約 17 MHz）</td><td>室溫 2.005、10 K 約 2.010</td></tr>

<tr><td colspan="4"><b>偏置磁場</b></td></tr>
<tr><td>輸入方式 / 數值</td><td>決定 <b>共振頻率</b>（Kittel）。HFSS 的 Bias 是球體內部場；選「外加磁場」時程式自動扣 Ms/3</td>
<td>共振頻率往上（約 2.8 MHz / Oe、28 MHz / mT）</td><td>讓 Kittel 落在你的掃頻範圍裡；內部場要 &gt; 0</td></tr>
<tr><td>方向</td><td>能激發 Kittel 模的是 <b>垂直於偏置</b> 的微波磁場</td><td>—</td>
<td>+Z（垂直板面）配訊號線正上方的水平磁場最常用</td></tr>

<tr><td colspan="4"><b>求解</b></td></tr>
<tr><td>Kittel 加入 Adaptive</td><td>網格在共振頻率被檢查收斂 → 共振頻率與深度比較準</td><td>—</td><td>建議勾；Adaptive 用 Multi-Frequencies（比 Broadband 省記憶體）</td></tr>
<tr><td>細掃範圍 ±</td><td>Kittel 附近另外掃的範圍（存成 *_kittel.csv）</td><td>範圍變大、同樣點數下點距變大</td><td>線寬的 20–100 倍（共振可能因網格 / 靜磁模偏移幾 MHz）</td></tr>
<tr><td>細掃點數</td><td>點距 = 2 × 範圍 / (點數 − 1)</td><td>點越密越看得清共振；Discrete 時間 ∝ 點數</td><td>線寬內至少 5 點</td></tr>
<tr><td>方式 / 誤差容許</td><td>Interpolating：算少數頻點再擬合；Discrete：每點都解（最準最慢）</td><td>容許越小越準、越慢</td>
<td>Interpolating 0.1%；有疑問用 Discrete 小範圍驗證。有 YIG 時 AEDT 不接受 Fast</td></tr>
</table>
"""

HTML_FAQ = """
<h3>常見問題</h3>
<h4>求解時 Out of memory / Insufficient memory</h4>
<p>先看 AEDT 訊息裡 <code>Seed Refinement Estimates for body YIG_Sphere</code>：四面體太多就是球體網格太細。
把「球體網格邊長」留空（自動 = 直徑 / 8）或改大，按「🧲 建立 / 更新 YIG 球」。另外 Adaptive 改用 Multi-Frequencies、
降低 Max Passes 也能省很多記憶體。</p>
<h4>S 參數上看不到共振</h4>
<ul>
<li>主掃頻點距（通常幾 MHz）比線寬（零點幾 MHz）大很多 → 一定看不到，請看 <code>*_kittel.csv</code> 的細掃結果。</li>
<li>Kittel 不在掃頻範圍內（記錄會警告）→ 調偏置或範圍。</li>
<li>球離訊號線太遠或不在訊號線上方 → 耦合太弱，降低「高度」、用 3D 預覽放到訊號線正上方。</li>
<li>偏置方向跟微波磁場平行 → 激發不到，換方向。</li></ul>
<h4>共振頻率跟 Kittel 試算差幾 MHz</h4>
<p>正常：網格不夠細、球不是完美球面（曲面元素）、還有其他靜磁模。細掃範圍要留餘裕（± 線寬的幾十倍）。</p>
<h4>建立掃頻失敗（InsertFrequencySweep）</h4>
<p>有 YIG（偏置 ferrite）時 AEDT 不接受 Fast 掃頻，工具會自動改 Interpolating；其他情況也會依序退回 Interpolating → Discrete。</p>
<h4>Port 有「additional propagating mode」訊息</h4>
<p>Wave Port 太大，多了會傳播的高階模態：用主控制面板建議的 Port 縮放，或把模態數設成 2。不影響 YIG 設定本身。</p>
"""


HTML_LIT = """
<h3>文獻參數：YIG 單晶球要填什麼</h3>
<p class="muted">YIG 分頁「材料」→「套用」可以一鍵填入。HFSS 的鐵氧體模型（Polder 張量）只用得到 4πMs、g、ΔH（與量測頻率）、εr、tanδ；
沒有交換作用、也沒有晶格異向性。</p>
<table width="100%">
<tr><th width="17%">HFSS 參數</th><th width="17%">室溫 300 K</th><th width="17%">低溫 ≈10 K</th><th>根據</th></tr>
<tr><td>4πMs（μ0Ms）</td><td><b>0.180 T</b></td><td><b>0.248 T</b></td>
<td>Maier-Flaig 2017：μ0Ms(300 K) = 180.0 ± 0.8 mT；Bloch 律擬合 μ0M0 = 249.5 mT（Fig. 3a，10 K 約 248 mT）。
Serga 2010 的 1750 G（0.175 T）是 LPE 薄膜的常用值。</td></tr>
<tr><td>Landé g</td><td><b>2.005</b></td><td><b>2.010</b></td>
<td>Maier-Flaig 2017：g(290 K) = 2.0054，降溫到 10 K 變成約 2.010（至少 +0.2%）。Serga 2010 用 γ = 2.8 MHz/Oe（g ≈ 2.00）。</td></tr>
<tr><td>ΔH（半高全寬）</td><td><b>0.27 Oe @ 9.4 GHz</b></td><td><b>≈0.5 Oe @ 工作頻率</b></td>
<td>室溫 Gilbert 阻尼 α ≈ 4×10⁻⁵（Maier-Flaig 2017；Qin 2018 引的塊材值 ≈3×10⁻⁵）：Δf = 2αf → 9.4 GHz 時 0.75 MHz = 0.27 Oe。
另外有不隨頻率變的非均勻線寬 Δf₀ ≈ 0.2–1 MHz（表面凹洞散射；室溫約 0.3 MHz），HFSS 沒辦法單獨表示；要對齊量測就直接填量到的線寬。
Serga 2010：塊材 YIG FMR 線寬一般 &lt; 0.5 Oe。<br>
低溫（&lt;100 K）線寬由稀土雜質主導、<b>不是</b> Gilbert 型（不跟頻率成正比）：9.6 GHz 時約 1.4 MHz（Fig. 4b，5–10 K），
在 30 K 附近有峰值（約 3 MHz）→ 「ΔH 頻率」填你的工作頻率，HFSS 才不會把它按頻率縮放錯。</td></tr>
<tr><td>εr、tanδ</td><td>15、2×10⁻⁴</td><td>15、2×10⁻⁴</td><td>這幾篇論文都沒有量介電性質；這是 YIG 常用值。</td></tr>
<tr><td>偏置（HFSS Bias）</td><td colspan="2">內部場 H<sub>i</sub> = H<sub>0</sub> − Ms/3（球的去磁因子 1/3）</td>
<td>Walker 1958：H<sub>i</sub> = H<sub>0</sub> − 4πM<sub>0</sub>N<sub>z</sub>。工具選「外加磁場」時自動扣掉；HFSS 的 Bias 是內部場。</td></tr>
</table>

<h4>共振頻率（工具的 Kittel 試算）</h4>
<p>球的均勻模（Walker 的 (110) 模）：<b>f = (γ/2π) μ0 (H<sub>0</sub> + H<sub>ani</sub>)</b>，跟 Ms 無關（Maier-Flaig 2017 式 2；Walker 1958）。
γ/2π = g × 13.996 GHz/T → g = 2.005 時 28.07 GHz/T（2.807 MHz/Oe）。</p>
<ul>
<li><b>異向性 H<sub>ani</sub></b>：HFSS 沒有這一項。Maier-Flaig 的球在室溫是 γμ0H<sub>ani</sub>/2π = 68.5 MHz（約 2.4 mT），
而且跟球的晶軸方向有關（低溫最多約 6 mT）。要對齊實驗就把它加進「外加磁場」。</li>
<li><b>其他靜磁模（Walker 模）</b>：例如 (440) 模 f = (γ/2π) μ0 (H<sub>0</sub> + H<sub>ani</sub> + Ms/9)，比 Kittel 高約 Ms/9（室溫 ≈ 560 MHz）。
微波磁場不均勻（球跟訊號線差不多大、或球不在正中央）時會被激發 → S 參數上會多出好幾個小共振，這是正常的（Walker 1958；Maier-Flaig 2017 註 1）。</li>
<li><b>Ms 可以用兩個模的頻差反推</b>：μ0Ms = 9 (2π/γ) (f<sub>440</sub> − f<sub>110</sub>)（Maier-Flaig 2017 式 4），不受磁場校正誤差影響。</li>
</ul>

<h4>哪些參數 HFSS 不需要</h4>
<ul>
<li><b>交換常數</b>（Serga 2010：α<sub>ex</sub> = q/4π = 3×10⁻¹² cm²）：只影響微米以下波長的自旋波。毫米球的模是靜磁模，交換可以忽略
（Walker 1958；Herring &amp; Kittel 1951）。</li>
<li><b>薄膜值</b>（Qin 2018：40 nm PLD 膜 Ms = 115 kA/m、α = 3.5×10⁻⁴）：是奈米薄膜，<b>不要</b>拿來設定單晶球。</li>
<li>Barker 2016（原子尺度熱自旋動力學：光學 / 聲學磁子、Curie 溫度）、Altland（場論教科書）：理論背景，沒有 HFSS 要填的數字。</li>
</ul>
<p class="muted">參考：H. Maier-Flaig et al., PRB 95, 214423 (2017)；A. A. Serga et al., J. Phys. D 43, 264002 (2010)；
L. R. Walker, J. Appl. Phys. 29, 318 (1958)；C. Herring &amp; C. Kittel, Phys. Rev. 81, 869 (1951)；H. Qin et al., PRB 98, 224422 (2018)；
J. Barker &amp; G. E. W. Bauer, PRL 117, 217201 (2016)；A. Altland &amp; B. Simons, Condensed Matter Field Theory.</p>
"""


class _Plot(QWidget):
    """兩張圖：共振示意（含掃頻點）與 Kittel 頻率對偏置。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        import matplotlib
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        fonts = ["Microsoft JhengHei", "Microsoft YaHei", "PingFang TC", "Noto Sans CJK TC", "Noto Sans CJK JP"]
        matplotlib.rcParams["font.sans-serif"] = fonts + [f for f in matplotlib.rcParams["font.sans-serif"] if f not in fonts]
        matplotlib.rcParams["axes.unicode_minus"] = False
        self.fig = Figure(figsize=(8, 3.0), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.fig)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)
        self.ax1, self.ax2 = self.fig.subplots(1, 2, gridspec_kw={"width_ratios": [3, 2]})

    def draw(self, r: dict, dark: bool):
        bg, fg, grid = ("#1F2124", "#E8ECEF", "#383D43") if dark else ("#FFFFFF", "#263238", "#E3E8EB")
        acc, pt, warn = ("#64B5F6", "#FFA24C", "#FF6B6B") if dark else ("#1565C0", "#E65100", "#C62828")
        self.fig.set_facecolor(bg)
        for ax in (self.ax1, self.ax2):
            ax.clear()
            ax.set_facecolor(bg)
            ax.tick_params(colors=fg, labelsize=8)
            for s in ax.spines.values():
                s.set_color(grid)
            ax.grid(True, color=grid, lw=0.6)
        ax = self.ax1
        if r["ok"]:
            fk, lw, span = r["f_k"] * 1e3, max(r["lw"], 1e-4), r["span"]
            x = np.linspace(-span, span, 1500)
            y = -12 / (1 + (2 * x / lw) ** 2)                       # 示意：深度固定 12 dB
            ax.plot(x, y, color=acc, lw=1.6, label="共振（示意）")
            n = max(2, int(r["pts"]))
            xs = np.linspace(-span, span, n)
            near = xs[np.abs(xs) <= max(3 * lw, span / 60)]
            ax.plot(near, -12 / (1 + (2 * near / lw) ** 2), "o", ms=3.5, color=pt, label=f"掃頻點（間距 {r['step']:.3g} MHz）")
            ax.axvspan(-lw / 2, lw / 2, color=acc, alpha=0.12)
            win = max(4 * lw, 6 * r["step"])
            ax.set_xlim(-win, win)
            ax.set_ylim(-14, 1.5)
            ax.set_xlabel(f"頻率 − Kittel {fk / 1e3:.4f} GHz (MHz)", color=fg, fontsize=8.5)
            ax.set_ylabel("|S21| (dB，示意)", color=fg, fontsize=8.5)
            ax.set_title(f"線寬 {r['lw']:.3g} MHz 內有 {r['ppl']:.1f} 個點", color=warn if r["ppl"] < 5 else fg, fontsize=9.5)
            leg = ax.legend(fontsize=7.5, loc="lower right", facecolor=bg, edgecolor=grid)
            for t in leg.get_texts():
                t.set_color(fg)
        ax = self.ax2
        if r["ok"]:
            b = np.linspace(0, max(500, r["b_ext"] * 1.6), 200)
            ax.plot(b, yig.gamma_ghz_per_t(r["g"]) * b * 1e-3, color=acc, lw=1.6)
            ax.axvspan(0, r["ms_mt"] / 3, color=warn, alpha=0.12)
            ax.plot([r["b_ext"]], [r["f_k"]], "o", color=pt, ms=7)
            ax.annotate(f"{r['f_k']:.3f} GHz", (r["b_ext"], r["f_k"]), textcoords="offset points", xytext=(6, -12),
                        color=fg, fontsize=8)
            ax.text(r["ms_mt"] / 6, ax.get_ylim()[1] * 0.92 if ax.get_ylim()[1] else 1, "未飽和", color=warn,
                    fontsize=7.5, ha="center", va="top")
            ax.set_xlabel("外加磁場 μ0H (mT)", color=fg, fontsize=8.5)
            ax.set_ylabel("Kittel 頻率 (GHz)", color=fg, fontsize=8.5)
            ax.set_title("偏置 → 共振頻率", color=fg, fontsize=9.5)
        self.canvas.draw_idle()


class YigHelpDialog(QDialog):
    """main：主視窗（用來讀目前的 YIG 參數、把試算的值套回去）；None 時用預設值。"""

    FIELDS = (  # key, 標籤, 最小, 最大, 小數, 單位
        ("bias", "外加磁場 μ0H", 0.0, 5000.0, 2, " mT"),
        ("ms", "4πMs", 0.001, 2.0, 4, " T"),
        ("g", "Landé g", 1.0, 3.0, 3, ""),
        ("dh", "ΔH", 0.0, 100.0, 3, " Oe"),
        ("d", "直徑", 0.01, 20.0, 3, " mm"),
        ("mesh", "網格邊長（0 = 自動）", 0.0, 5.0, 4, " mm"),
        ("span", "細掃範圍 ±", 0.1, 5000.0, 2, " MHz"),
    )

    def __init__(self, main=None):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("YIG 參數說明 — 每個參數影響什麼")
        self.resize(1060, 820)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self._dark = is_dark_palette(main)
        apply_theme(self._dark)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)
        tabs.addTab(_browser(HTML_PARAMS), "① 參數影響")

        w = QWidget()
        v = QVBoxLayout(w)
        box = QGroupBox("試算（改數字馬上更新；不會改到主視窗，按「套用到 YIG 分頁」才會）")
        gl = QGridLayout(box)
        self.spins = {}
        for i, (key, label, lo, hi, dec, unit) in enumerate(self.FIELDS):
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setDecimals(dec)
            sp.setSuffix(unit)
            sp.setKeyboardTracking(False)
            sp.valueChanged.connect(self.recalc)
            self.spins[key] = sp
            gl.addWidget(QLabel(label + ":"), i // 4, (i % 4) * 2)
            gl.addWidget(sp, i // 4, (i % 4) * 2 + 1)
        self.sp_pts = QSpinBox()
        self.sp_pts.setRange(2, 100001)
        self.sp_pts.setKeyboardTracking(False)
        self.sp_pts.valueChanged.connect(self.recalc)
        gl.addWidget(QLabel("細掃點數:"), 1, 6)
        gl.addWidget(self.sp_pts, 1, 7)
        v.addWidget(box)
        self.out = QTextBrowser()
        self.out.setMaximumHeight(210)
        v.addWidget(self.out)
        try:
            self.plot = _Plot()
            v.addWidget(self.plot, 1)
        except Exception as e:  # noqa: BLE001 - 沒有 matplotlib 也能看文字
            self.plot = None
            v.addWidget(QLabel(f"（畫不了圖：{e}）"))
        row = QHBoxLayout()
        b = QPushButton("↺ 讀主視窗目前的值")
        b.clicked.connect(self.load_from_main)
        row.addWidget(b)
        self.btn_apply = QPushButton("✔ 套用到 YIG 分頁")
        self.btn_apply.clicked.connect(self.apply_to_main)
        self.btn_apply.setEnabled(main is not None)
        row.addWidget(self.btn_apply)
        row.addStretch(1)
        v.addLayout(row)
        tabs.addTab(w, "② 即時試算")
        tabs.addTab(_browser(HTML_FAQ), "③ 常見問題")
        tabs.addTab(_browser(HTML_LIT), "④ 文獻參數")
        self.tabs = tabs

        close = QPushButton("關閉")
        close.clicked.connect(self.close)
        r = QHBoxLayout()
        r.addStretch()
        r.addWidget(close)
        lay.addLayout(r)
        self.load_from_main()

    # ---- 跟主視窗交換數值 ----
    def _main_values(self) -> dict:
        vals = {"bias": 250.0, "ms": 0.180, "g": 2.005, "dh": 0.27, "d": 1.2, "mesh": 0.0, "span": 50.0, "pts": 1001}
        m = self.main
        if m is None:
            return vals

        def f(edit, key):
            try:
                txt = edit.text().strip()
                vals[key] = float(txt) if txt else vals[key]
            except (ValueError, AttributeError):
                pass
        f(m.edit_yig_ms, "ms"); f(m.edit_yig_g, "g"); f(m.edit_yig_dh, "dh"); f(m.edit_yig_d, "d")
        f(m.edit_yig_span, "span"); f(m.edit_yig_pts, "pts")
        vals["mesh"] = 0.0
        f(m.edit_yig_mesh, "mesh")
        try:
            v = float(m.edit_yig_bias.text())
            if m.combo_yig_bmode.currentIndex() == 0:
                vals["bias"] = v
            else:                                   # 內部場 A/m → 外加 mT
                vals["bias"] = yig.bias_fields(v, "internal_Apm", vals["ms"], vals["g"])["B_ext_mT"]
        except (ValueError, AttributeError):
            pass
        return vals

    def load_from_main(self):
        vals = self._main_values()
        for k, sp in self.spins.items():
            sp.blockSignals(True)
            sp.setValue(vals[k])
            sp.blockSignals(False)
        self.sp_pts.blockSignals(True)
        self.sp_pts.setValue(int(vals["pts"]))
        self.sp_pts.blockSignals(False)
        self.recalc()

    def apply_to_main(self):
        m = self.main
        if m is None:
            return
        s = {k: sp.value() for k, sp in self.spins.items()}
        fmt = lambda x: f"{x:.6g}"
        m.combo_yig_bmode.setCurrentIndex(0)
        m.edit_yig_bias.setText(fmt(s["bias"]))
        m.edit_yig_ms.setText(fmt(s["ms"])); m.edit_yig_g.setText(fmt(s["g"])); m.edit_yig_dh.setText(fmt(s["dh"]))
        m.edit_yig_d.setText(fmt(s["d"]))
        m.edit_yig_mesh.setText("" if s["mesh"] <= 0 else fmt(s["mesh"]))
        m.edit_yig_span.setText(fmt(s["span"])); m.edit_yig_pts.setText(str(self.sp_pts.value()))
        self.btn_apply.setText("✔ 已套用")

    # ---- 計算 ----
    def results(self) -> dict:
        s = {k: sp.value() for k, sp in self.spins.items()}
        pts = self.sp_pts.value()
        fld = yig.bias_fields(s["bias"], "external_mT", s["ms"], s["g"])
        lw = yig.linewidth_mhz(s["dh"], s["g"])
        step = 2 * s["span"] / max(1, pts - 1)
        edge = s["mesh"] or yig.auto_mesh_mm(s["d"])
        return {"ok": True, "f_k": fld["f_kittel"], "H_int": fld["H_int"], "b_ext": s["bias"], "ms_mt": s["ms"] * 1e3,
                "g": s["g"], "lw": lw, "q": (fld["f_kittel"] * 1e3 / lw) if lw > 0 else math.inf, "step": step,
                "ppl": (lw / step) if step > 0 else 0, "span": s["span"], "pts": pts, "edge": edge,
                "auto_edge": not s["mesh"], "tets": yig.mesh_estimate(s["d"], edge), "d": s["d"],
                "span_lw": (s["span"] / lw) if lw > 0 else math.inf}

    def recalc(self, *_):
        r = self.results()
        t = THEME
        warn = lambda ok, txt: f"<span class='{'ok' if ok else 'warn'}'>{'✔' if ok else '⚠'} {txt}</span>"
        rows = [
            ("HFSS Bias（球體內部場）", f"{r['H_int']:,.0f} A/m",
             warn(r["H_int"] > 0, "飽和" if r["H_int"] > 0 else f"偏置不夠：外加至少要 > Ms/3 = {r['ms_mt'] / 3:.1f} mT")),
            ("Kittel 共振頻率", f"{r['f_k']:.4f} GHz", "= g × 13.996 GHz/T × μ0H<sub>外加</sub>（球的 Kittel 跟 Ms 無關）"),
            ("FMR 線寬（半高全寬）", f"{r['lw']:.3g} MHz", f"Q<sub>FMR</sub> ≈ {r['q']:,.0f}"),
            ("細掃點距", f"{r['step']:.3g} MHz",
             warn(r["ppl"] >= 5, f"線寬內 {r['ppl']:.1f} 個點" + ("" if r["ppl"] >= 5 else "（建議 ≥ 5：加點數或縮小範圍）"))),
            ("細掃範圍", f"± {r['span']:g} MHz",
             warn(r["span_lw"] >= 20, f"= 線寬的 {r['span_lw']:.0f} 倍" + ("" if r["span_lw"] >= 20 else "（共振可能偏移幾 MHz，建議 ≥ 20 倍）"))),
            ("球體網格", f"{r['edge']:g} mm{'（自動）' if r['auto_edge'] else ''}",
             warn(r["tets"] <= yig.MESH_WARN_TETS, f"約 {r['tets']:,} 個四面體"
                  + ("" if r["tets"] <= yig.MESH_WARN_TETS else f"（可能記憶體不夠，建議 ≥ {yig.mesh_for_tets(r['d'], 15000):g} mm）"))),
        ]
        html = "<table width='100%'>" + "".join(
            f"<tr><td width='24%'><b>{a}</b></td><td width='18%'>{b}</td><td>{c}</td></tr>" for a, b, c in rows) + "</table>"
        _set_html(self.out, html)
        if self.plot is not None:
            self.plot.draw(r, self._dark)
        self.btn_apply.setText("✔ 套用到 YIG 分頁")
        return r

    def changeEvent(self, e):
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange, QEvent.Type.StyleChange):
            dark = is_dark_palette(self)
            if dark != self._dark:
                self._dark = dark
                apply_theme(dark)
                for tb in self.findChildren(QTextBrowser):
                    if hasattr(tb, "_raw_html"):
                        tb.setHtml(_themed(tb._raw_html))
                self.recalc()
        super().changeEvent(e)
