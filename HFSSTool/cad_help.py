"""v3.9.5：DXF 編輯器的說明視窗。

    ① 功能介紹：工具、滑鼠、節點、指令列、鎖點、標註、修改、MS ⇄ CPW 轉換
    ② MS 與 CPW：結構、場分布、阻抗由什麼決定、各自的寄生模態、怎麼選；剖面示意圖＋即時試算（Z0、εeff、λg）
    ③ 繪製注意事項：Port、線寬 / 間隙、轉角、上地、過孔、網格、YIG 擺放、存檔
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import (QDialog, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QTabWidget,
                             QVBoxLayout, QWidget)

import tline
from mode_help import THEME, _browser, _font, apply_theme, is_dark_palette

# ======================================================================
# ① 功能介紹
# ======================================================================
HTML_FEATURES = """
<h3>DXF 編輯器：畫 HFSS 建模用的板子</h3>
<p>畫好、存檔後，主視窗的 DXF 路徑會換成這個檔，按「建立 3D 模型」就會照圖層建模。單位是 <b>mm</b>，Y 軸向上。
操作方式參考 AutoCAD：可以用滑鼠點，也可以在下方指令列打座標。</p>

<h4>工具（上面第二排）</h4>
<table>
<tr><th>工具</th><th>按鍵</th><th>怎麼用</th></tr>
<tr><td>選取</td><td>F1 / V</td><td>點圖形選取；拖白色方塊 = 移動節點；拖整個圖形 = 移動；拖橘色菱形 = 把線段彎成圓弧；雙擊線段 = 加節點</td></tr>
<tr><td>平移</td><td>F2 / H</td><td>拖曳移動畫面（任何工具下按住<b>空白鍵</b>或<b>滑鼠中鍵</b>也可以）</td></tr>
<tr><td>微波線</td><td>P</td><td>點節點畫<b>中心線</b>，線寬、轉角圓角、CPW 間隙在左邊「新畫的微波線」設定；雙擊或 Enter 結束</td></tr>
<tr><td>三點弧</td><td>A</td><td>起點 → 弧上一點 → 終點，畫一段圓弧微波線</td></tr>
<tr><td>曲線</td><td>B</td><td>點幾個錨點畫平滑的 Bézier 微波線，之後可以拖藍色控制點（按住 Alt 可以折角）</td></tr>
<tr><td>多邊形 / 曲線多邊形 / 矩形</td><td>G / K / R</td><td>畫在左邊「多邊形、矩形、圓畫在」選的圖層</td></tr>
<tr><td>圓（孔）</td><td>C</td><td>圓心 → 半徑；圖層 h（螺絲孔）或 v（金屬過孔）</td></tr>
<tr><td>標註</td><td>D</td><td>第一點 → 第二點 → 標註線的位置。「自動」：往上下拉 = 水平距離、往左右拉 = 垂直距離；也可以選「對齊（實際距離）」。存成 DXF 標註（圖層 dim，HFSS 不匯入）</td></tr>
<tr><td>量距離</td><td>M</td><td>點兩點：顯示距離、ΔX、ΔY、角度（不存檔，Esc 清掉）</td></tr>
<tr><td>框選放大</td><td>Z</td><td>拖一個框放大；點一下 = 放大一級。F = 全部顯示</td></tr>
</table>

<h4>滑鼠與鍵盤</h4>
<ul>
<li><b>滾輪</b>：以游標為中心縮放。<b>中鍵 / 空白鍵＋左鍵</b>：平移。<b>右鍵</b>：畫圖中 = 結束；其他 = 選單（刪除、複製、移動、旋轉、鏡像、改圖層、設圓角…）。</li>
<li><b>Shift</b>：從上一點只能 0 / 45 / 90°。<b>Alt</b>：暫時不吸附（格線、板邊、鎖點）。</li>
<li><b>方向鍵</b>：選取的節點（或整個圖形）移動一格格線；Shift ×10、Alt ÷10。</li>
<li><b>Delete</b>：刪除選取的圖形 / 標註；畫圖中 = 刪掉最後一點。<b>Esc</b>：取消畫到一半的、清掉量測、取消選取。</li>
<li><b>Ctrl+Z / Ctrl+Y</b>：復原 / 重做。<b>Ctrl+S</b>：存檔。</li>
</ul>

<h4>節點座標（點節點就能打數字）</h4>
<p>先選圖形，再點它的節點（白色方塊，選到的會有紅框），左邊「選取的節點」可以直接改：
<b>X / Y</b>（絕對座標）、<b>距上一點</b>（長度）、<b>角度</b>（從上一點量，逆時針為正）。◀ ▶ 換上一個 / 下一個節點。
右邊的節點表格也能改 X、Y、圓角 R、弧角度。</p>

<h4>指令列（最下面，跟 AutoCAD 一樣）</h4>
<table>
<tr><th>打什麼</th><th>意思</th></tr>
<tr><td><code>12.5,3</code></td><td>絕對座標</td></tr>
<tr><td><code>@5,0</code></td><td>相對上一點（ΔX, ΔY）</td></tr>
<tr><td><code>@10&lt;90</code></td><td>極座標：從上一點往 90° 走 10 mm</td></tr>
<tr><td><code>7.5</code></td><td>直接打長度：沿著游標的方向走 7.5 mm（畫圓時是半徑）</td></tr>
<tr><td>（空白）Enter</td><td>結束這條線</td></tr>
<tr><td><code>L</code> <code>A</code> <code>REC</code> <code>C</code> <code>POL</code> <code>B</code></td><td>微波線、三點弧、矩形、圓、多邊形、曲線</td></tr>
<tr><td><code>DIM</code> / <code>DAL</code> / <code>DI</code></td><td>標註（自動水平 / 垂直）/ 對齊標註 / 量距離</td></tr>
<tr><td><code>M 1,2</code> <code>CO 5,0</code> <code>RO 90</code> <code>MI X</code></td><td>移動、複製到、旋轉（繞圖形中心）、鏡像（X 左右 / Y 上下）；不帶數字會再問</td></tr>
<tr><td><code>Z</code> <code>U</code> <code>REDO</code> <code>E</code></td><td>全部顯示、復原、重做、刪除</td></tr>
<tr><td><code>OS</code> <code>OR</code> <code>GRID 0.05</code></td><td>物件鎖點開關、正交開關、格線間距</td></tr>
</table>
<p class="muted">選了某個節點時打座標（例如 <code>@0,0.5</code>）會直接移動那個節點。</p>

<h4>吸附與鎖點</h4>
<ul>
<li><b>吸附格線</b>（上面那排）：點到的位置對齊格線；<b>板邊</b>永遠優先吸附（Wave Port 要在板邊）。</li>
<li><b>物件鎖點 F3</b>：游標靠近時鎖上黃色記號 —— □ 端點 / 節點、△ 線段中點、○ 圓心、◇ 圓的四分點、✕ 微波線 / 上地輪廓的角（量線寬、間隙用）。</li>
<li><b>正交 F8</b>：畫圖、拖節點時只能水平或垂直。</li>
<li><b>顯示線段長度</b>：選取微波線時每一段標出長度，右邊也顯示中心線總長（算共振長度很方便）。</li>
</ul>

<h4>修改</h4>
<p>上面第三排或右鍵選單：<b>移動…、複製到…</b>（打 ΔX, ΔY）、<b>旋轉…</b>（繞圖形中心，逆時針為正）、<b>左右 / 上下鏡像</b>、
<b>置中到板子</b>。圓弧、圓角、Bézier 旋轉後形狀不變；鏡像時圓弧方向會跟著反過來。</p>

<h4>⇄ MS / CPW 自動轉換</h4>
<p>依主視窗的板材（εr、介電層厚度 h）與目標阻抗（預設 50 Ω）算線寬 / 間隙，切換建模方式：板材圖層 b ⇄ db、
CPW 開「自動產生上地」、轉 MS 時拿掉上地與過孔；所有微波線套用新的線寬（CPW 還有間隙），可以選「保持線寬算間隙」或「保持間隙算線寬」。
CPW 可以順便沿微波線兩側加一排過孔（直徑、間距、離間隙邊多遠）。公式說明見「② MS 與 CPW」。</p>

<h4>圖層</h4>
<table>
<tr><th>建模方式</th><th>圖層</th></tr>
<tr><td>MS（微帶線）</td><td><b>b</b> 板材（下面整片是地）、<b>g</b> 訊號線、<b>h</b> 螺絲孔</td></tr>
<tr><td>CPW（接地共面波導）</td><td><b>db</b> 下板材（下面整片是地）、<b>ub</b> 上板材（頂層地）、<b>g</b> 訊號線、<b>h</b> 螺絲孔、<b>v</b> 金屬過孔</td></tr>
<tr><td>不匯入 HFSS</td><td><b>g_path</b> 微波線中心線（給編輯器重新打開用）、<b>dim</b> 標註、其他不認得的圖層（原樣保留）</td></tr>
</table>
<p>左下角會一直檢查：缺圖層、Wave Port 數、上地有沒有被地包住的槽、圓角太小……，<span class="ok">✔ 可以給 HFSS 建模</span> 才放心建。</p>
"""

# ======================================================================
# ② MS 與 CPW
# ======================================================================
HTML_MS_CPW = """
<h3>微帶線（MS）與接地共面波導（CPW）</h3>
<p>兩種都是平面傳輸線，傳的是<b>準 TEM 模</b>：電場、磁場幾乎都在跟走線垂直的剖面裡，一部分在板材、一部分在空氣，
所以波速由「有效介電常數」εeff 決定（1 &lt; εeff &lt; εr），導波波長 λg = c / (f·√εeff)。
這個工具的 CPW 底下還有一整片地（db），正式名稱是<b>接地共面波導（GCPW / conductor-backed CPW）</b>。</p>

<h4>微帶線 MS</h4>
<ul>
<li><b>結構</b>：上面一條訊號線（寬 W），隔著介電層（厚 h、εr）底下整片是地。</li>
<li><b>電場</b>：從訊號線往下到地，邊緣有一部分繞到空氣裡（fringing）。<b>磁場</b>：繞著訊號線轉圈 —— 在訊號線<b>正上方是水平的</b>（垂直於走線方向），往上大約 W 到幾個 h 的距離就弱掉。</li>
<li><b>阻抗由 W / h 與 εr 決定</b>：線越寬、板越薄 → 電容越大 → Z0 越低。例：RO4003（εr 3.55）、h = 0.813 mm，50 Ω ≈ 1.83 mm；FR4 1.6 mm ≈ 3 mm。</li>
<li><b>優點</b>：最簡單、只有兩層、不用過孔。<b>缺點</b>：50 Ω 的線寬被板厚綁住（厚板 → 線很寬）；元件接地要打孔到底；開路端、轉角會輻射；頻率高時色散比較明顯。</li>
<li><b>寄生</b>：板子很厚或線很寬時（W、h 接近板材裡的半波長），會出現高階模、表面波。</li>
</ul>

<h4>接地共面波導 CPW（GCPW）</h4>
<ul>
<li><b>結構</b>：中間一條訊號線（寬 W），兩側隔著間隙 G 是<b>頂層地</b>（ub），底下還有一整片地（db）；上下兩層地要用<b>過孔</b>連起來。</li>
<li><b>電場</b>：主要從中心導體橫跨兩個間隙到旁邊的地，少部分往下到底層地。<b>磁場</b>：繞著中心導體轉，最強在導體邊緣與間隙；中心導體<b>正上方是水平的</b>、間隙上方有<b>垂直分量</b>；離板面高度超過 (W + 2G) 左右就弱很多（比 MS 集中）。</li>
<li><b>阻抗主要由 W / (W + 2G) 決定</b>，板厚 h 影響比較小 → 同一塊板子可以做很細的 50 Ω 線（例：RO4003 0.813 mm、W = 1 mm 時 G ≈ 0.14 mm）。間隙越大 → Z0 越高。</li>
<li><b>優點</b>：場集中在間隙、對旁邊的東西干擾小；元件可以直接接到旁邊的地；尺寸可以縮到接探針 / 晶片；色散小。<b>缺點</b>：間隙小、加工精度要求高；需要過孔與對稱的地。</li>
<li><b>寄生模態</b>（畫圖要注意的原因）：
  <ul>
  <li><b>槽線模（slotline）</b>：兩側的地電位不一樣時，兩個間隙變成兩條槽線。不對稱、轉角、T 型接頭都會激發 → 兩側地要等電位：<b>過孔牆</b>（或晶片上的 air bridge）。</li>
  <li><b>平行板模 / 板子共振</b>：頂層地和底層地之間是一個平行板波導，頻率到了會在整塊板子共振 → 過孔牆的間距要 <b>&lt; λg / 10</b>（常用 λg / 20），而且要靠近間隙邊。</li>
  </ul></li>
</ul>

<h4>怎麼選</h4>
<table>
<tr><th></th><th>MS</th><th>CPW（GCPW）</th></tr>
<tr><td>阻抗由誰決定</td><td>W / h、εr</td><td>W / (W + 2G)（h 影響小）</td></tr>
<tr><td>場集中在</td><td>訊號線下面的介電層</td><td>兩個間隙</td></tr>
<tr><td>接地</td><td>要打孔到底層</td><td>直接接旁邊的頂層地</td></tr>
<tr><td>需要過孔</td><td>不用</td><td>要（等電位、壓掉寄生模態）</td></tr>
<tr><td>加工</td><td>簡單</td><td>間隙要準</td></tr>
<tr><td>跟 YIG 球</td><td>磁場散得比較開，球放高一點也還耦合得到</td><td>磁場集中，耦合對高度、左右位置很敏感；同樣高度通常比較弱、但比較乾淨</td></tr>
</table>

<h4>這個工具用的公式</h4>
<ul>
<li><b>MS</b>：Hammerstad–Jensen。εeff = (εr+1)/2 + (εr−1)/2 · (1 + 12h/W)<sup>−1/2</sup>；
  W/h ≤ 1：Z0 = 60/√εeff · ln(8h/W + W/4h)；W/h &gt; 1：Z0 = 120π / √εeff / (W/h + 1.393 + 0.667 ln(W/h + 1.444))。</li>
<li><b>GCPW</b>：保角映射。k = W/(W+2G)、k₃ = tanh(πW/4h) / tanh(π(W+2G)/4h)，
  q = [K(k′)/K(k)]·[K(k₃)/K(k₃′)]，εeff = (1 + εr·q)/(1 + q)，Z0 = 60π / √εeff / [K(k)/K(k′) + K(k₃)/K(k₃′)]（K = 第一類完全橢圓積分）。</li>
<li>都忽略銅厚、頂層地當作無限寬；誤差約幾 %。<b>最後以 HFSS 的 Port 阻抗（Zpi）為準</b>，需要的話微調線寬 / 間隙。</li>
</ul>
<p class="muted">參考：Pozar, Microwave Engineering 3.8；Wadell, Transmission Line Design Handbook 3.4；Simons, Coplanar Waveguide Circuits, Components, and Systems ch. 2。</p>
"""

# ======================================================================
# ③ 繪製注意事項
# ======================================================================
HTML_TIPS = """
<h3>繪製時要注意的細節</h3>

<h4>1. Wave Port（最常出問題）</h4>
<ul>
<li><b>微波線的端點要剛好在板邊</b>（端點會自動吸附板邊，在板邊的端點有綠色圓圈）。HFSS 只在這些地方建 Wave Port；左下角會顯示找到幾個。</li>
<li>端點附近要有一段<b>垂直板邊的直線</b>（至少 2–3 個線寬），不要在板邊轉彎或斜著出去：Port 是一個平面，場要在那裡已經是乾淨的傳輸線模。</li>
<li>Port 的大小：MS 寬 ≈ 2·max(5W, 3h) + W、高 6h；CPW 寬 ≈ 3(W + 2G)、高 5h（主視窗的 Port 縮放基數會再乘上去）。
  <b>Port 兩側要留這麼多空間</b>：別的走線、板角、另一個 Port 不能擠在裡面；Port 不要放在板子的角落。</li>
<li>CPW 的 Port 兩側要有頂層地（自動上地會處理；手畫 ub 時要記得）。</li>
<li>Port 尺寸大於板材裡的半波長時會多出高階模（日誌會警告並建議縮放）。</li>
</ul>

<h4>2. 線寬、間隙、阻抗</h4>
<ul>
<li>先用「⇄ MS / CPW 自動轉換」算 50 Ω 的線寬 / 間隙，再畫；主視窗的介電質材料、Die 厚、εr 覆寫要先填對（轉換視窗會顯示讀到的值）。</li>
<li>CPW 的間隙不要小到加工做不到（一般 PCB ≥ 0.1–0.15 mm），也不要大到超過板厚太多（地離太遠，變得像 MS）。</li>
<li>同一條線中途改線寬 = 阻抗不連續；需要的話用漸變段（多邊形畫在 g）。</li>
</ul>

<h4>3. 轉角與曲線</h4>
<ul>
<li>直角轉彎會反射、輻射：用<b>圓角</b>（節點表格「圓角 R」或右鍵「設圓角」），半徑 ≥ 2–3 個線寬比較好；<b>半徑至少要 W/2</b>，否則轉角內側會重疊（左下角會警告）。</li>
<li>圓弧（三點弧、拖橘色菱形）存成 DXF 真正的圓弧；Bézier 曲線存檔時切成細小直線段（網格會比較多）。</li>
<li>CPW 轉彎時兩側間隙的路徑長不一樣 → 容易激發槽線模：轉角附近多放幾個過孔，或半徑放大。</li>
</ul>

<h4>4. CPW 的自動上地</h4>
<ul>
<li>勾「自動產生上地」時，上地 = 板材 −（每條微波線＋兩側間隙 G）；<b>不要再手畫 ub</b>（轉換時會拿掉）。</li>
<li><b>開路端</b>：停在板子中間的微波線末端會自動離地一個間隙 G。想要末端接地（短路 stub）的話，目前要關掉自動上地、自己畫 ub。</li>
<li><b>耦合間隙</b>（例如共振腔：兩段線頭對頭）：勾「端對端的微波線之間也挖空」，間隙兩側的槽會連起來，地不會伸進間隙。條件：兩端面對面、距離 ≤ 3 ×（W + 2G）。</li>
<li>左下角出現「上地有被地包住的槽」：表示有槽沒有連到板邊（例如線沒碰到板邊、或間隙太大沒被連起來）。存檔時會切成幾塊、HFSS 合併回去，形狀是對的，但要確定這是你要的。</li>
</ul>

<h4>5. 過孔（v）與螺絲孔（h）</h4>
<ul>
<li><b>v 金屬過孔</b>：把頂層地接到底層地。過孔牆間距 &lt; λg / 10（常用 λg / 20，試算在「② MS 與 CPW」），離間隙邊 0.1–0.3 mm、不要掉進間隙或碰到訊號線。</li>
<li>Port 附近的過孔不要放在 Port 平面上（自動加的會離板邊至少半個間距）。</li>
<li><b>h 螺絲孔</b>：整塊板子挖穿（MS / CPW 都可以），放在離走線遠的地方。</li>
<li>過孔很多時網格會變多；模擬時可以先只放走線附近的。</li>
</ul>

<h4>6. 精度與網格</h4>
<ul>
<li>用<b>鎖點、指令列打座標、節點座標</b>畫，不要只靠目測：差 1 µm 的細縫在 HFSS 會變成一片極細的網格（又慢又吃記憶體），也可能讓地沒被切開。</li>
<li>格線間距可以調（例如 0.05 mm）；需要任意位置時按住 Alt。</li>
<li>最小的特徵（間隙、耦合間隙）決定網格大小；先確定需要這麼細。</li>
</ul>

<h4>7. 板子大小與 YIG</h4>
<ul>
<li>走線離板子側邊至少 3–5 個 h（MS）或 3 ×（W + 2G）（CPW），邊界不會影響阻抗。</li>
<li>YIG 球的 Kittel 模要被<b>垂直於偏置</b>的微波磁場激發：偏置 +Z（垂直板面）→ 球放在<b>訊號線正上方</b>（那裡的磁場是水平的）；偏置在板面內 → 也可以放在間隙上方（有垂直分量）。</li>
<li>球越貼近金屬耦合越強、共振也會被金屬鏡像往上推（見 YIG 參數說明）。</li>
</ul>

<h4>8. 存檔</h4>
<ul>
<li>存檔後主視窗的 DXF 路徑會換成這個檔；<b>改完要重新「建立 3D 模型」</b>，舊的 HFSS 模型不會自己更新。</li>
<li>路徑最好全英文（HFSS 專案路徑必須全英文）。</li>
<li>也可以在 AutoCAD 改：圖層名稱照上面的規範；編輯器產生的輪廓（帶 QELHFSS 標記）重新打開時會依中心線重算。</li>
<li>標註（dim）、中心線（g_path）不會匯入 HFSS。</li>
</ul>
"""


# ======================================================================
# 剖面示意圖
# ======================================================================
class CrossSection(QWidget):
    """MS 與 GCPW 的剖面：導體、介電層、電場（實線箭頭）與磁場（虛線圈）示意。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(290)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = THEME.get("name") == "dark"
        p.fillRect(self.rect(), QColor(THEME["BG"]))
        w = self.width()
        half = w / 2
        self._panel(p, QRectF(10, 8, half - 20, self.height() - 16), "MS（微帶線）", cpw=False, dark=dark)
        self._panel(p, QRectF(half + 10, 8, half - 20, self.height() - 16), "CPW（接地共面波導，GCPW）", cpw=True, dark=dark)
        p.end()

    def _panel(self, p: QPainter, r: QRectF, title: str, cpw: bool, dark: bool) -> None:
        text = QColor(THEME["TEXT"])
        copper = QColor("#D4A017")
        sub = QColor("#4E7D3A" if dark else "#B9D8A6")
        efield = QColor("#64B5F6" if dark else "#1565C0")
        hfield = QColor("#FF8A65" if dark else "#D84315")
        p.setFont(_font(10, True))
        p.setPen(text)
        p.drawText(QRectF(r.left(), r.top(), r.width(), 22), Qt.AlignmentFlag.AlignHCenter, title)
        cx = r.center().x()
        top = r.top() + 30 + (r.height() - 30) * 0.42          # 板面
        h = (r.height() - 30) * 0.28                            # 介電層厚
        t = 5.0                                                 # 銅厚（像素）
        left, right = r.left() + 10, r.right() - 10
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(sub))
        p.drawRect(QRectF(left, top, right - left, h))
        p.setBrush(QBrush(copper))
        p.drawRect(QRectF(left, top + h, right - left, t))      # 底層地
        W = r.width() * (0.16 if not cpw else 0.13)
        G = r.width() * 0.09
        sig = QRectF(cx - W / 2, top - t, W, t)
        p.drawRect(sig)
        grounds = []
        if cpw:
            grounds = [QRectF(left, top - t, cx - W / 2 - G - left, t), QRectF(cx + W / 2 + G, top - t, right - (cx + W / 2 + G), t)]
            for g in grounds:
                p.drawRect(g)
            # 過孔
            p.setBrush(QBrush(copper.darker(115)))
            for x in (cx - W / 2 - G - 22, cx + W / 2 + G + 16):
                p.drawRect(QRectF(x, top, 6, h))
        # 電場
        pen = QPen(efield, 1.6)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        def arrow(a: QPointF, b: QPointF) -> None:
            p.drawLine(a, b)
            dx, dy = b.x() - a.x(), b.y() - a.y()
            n = math.hypot(dx, dy) or 1
            ux, uy = dx / n, dy / n
            s = 6
            poly = QPolygonF([b, QPointF(b.x() - ux * s - uy * s * 0.5, b.y() - uy * s + ux * s * 0.5),
                              QPointF(b.x() - ux * s + uy * s * 0.5, b.y() - uy * s - ux * s * 0.5)])
            p.setBrush(QBrush(efield))
            p.drawPolygon(poly)
            p.setBrush(Qt.BrushStyle.NoBrush)

        if not cpw:
            for k in range(5):                                   # 往下到地
                x = cx - W / 2 + W * (k + 0.5) / 5
                arrow(QPointF(x, top + 2), QPointF(x, top + h - 4))
            for sgn in (-1, 1):                                  # 邊緣繞出去
                path = QPainterPath(QPointF(cx + sgn * W / 2, top - t / 2))
                path.cubicTo(QPointF(cx + sgn * (W / 2 + 25), top - 22), QPointF(cx + sgn * (W / 2 + 45), top + 10),
                             QPointF(cx + sgn * (W / 2 + 40), top + h - 4))
                p.drawPath(path)
                arrow(path.pointAtPercent(0.96), path.pointAtPercent(1.0))
        else:
            for sgn in (-1, 1):                                  # 跨過間隙（空氣裡往上拱、板材裡往下拱）
                a = QPointF(cx + sgn * W / 2, top - t / 2)
                b = QPointF(cx + sgn * (W / 2 + G), top - t / 2)
                arrow(QPointF(a.x() + sgn * 2, a.y()), QPointF(b.x() - sgn * 2, b.y()))
                for lift in (10, 22):
                    for down in (-1, 1):
                        path = QPainterPath(a)
                        path.cubicTo(QPointF(a.x(), a.y() + down * lift), QPointF(b.x(), b.y() + down * lift), b)
                        p.drawPath(path)
                        arrow(path.pointAtPercent(0.9), path.pointAtPercent(1.0))
            for k in (-1, 0, 1):                                 # 少部分往下到底層地
                x = cx + k * W / 4
                arrow(QPointF(x, top + 2), QPointF(x, top + h - 4))
        # 磁場：繞著訊號線
        pen = QPen(hfield, 1.4, Qt.PenStyle.DashLine)
        p.setPen(pen)
        loops = ((W * 0.75, 16), (W * 1.15, 30), (W * 1.6, 46)) if not cpw else ((W * 0.62, 9), (W * 0.85, 17))
        for rx, ry in loops:
            p.drawEllipse(QPointF(cx, top - t / 2), rx, ry)
        # 標示
        p.setFont(_font(8.5))
        p.setPen(text)
        p.drawText(QPointF(cx - 5, top - t - (52 if not cpw else 34)), "W")
        p.drawText(QPointF(right - 40, top + h / 2 + 4), "h, εr")
        if cpw:
            p.drawText(QPointF(cx + W / 2 + G / 2 - 4, top - t - 30), "G")
            p.drawText(QPointF(left + 4, top - t - 6), "頂層地 ub")
            p.drawText(QPointF(cx + W / 2 + G + 18, top + h - 8), "過孔 v")
        p.drawText(QPointF(left + 4, top + h + t + 14), "底層地（db / b）")
        p.setPen(efield)
        p.drawText(QPointF(left + 4, r.bottom() - 18), "→ 電場")
        p.setPen(hfield)
        p.drawText(QPointF(left + 70, r.bottom() - 18), "- - 磁場（在訊號線正上方是水平的）")


# ======================================================================
# 試算
# ======================================================================
class Calculator(QGroupBox):
    def __init__(self, sub: dict, w: float, g: float, parent=None) -> None:
        super().__init__("試算（跟 MS / CPW 自動轉換同一套公式）", parent)
        f = QFormLayout()

        def spin(lo, hi, val, dec, suffix):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(dec)
            s.setValue(val)
            s.setSuffix(suffix)
            s.valueChanged.connect(lambda _v: self.recalc())
            return s
        er = sub.get("er") or tline.material_er(sub.get("material", "")) or 3.55
        self.sp_er = spin(1.0, 200, float(er), 3, "")
        self.sp_h = spin(0.001, 100, float(sub.get("h") or 0.813), 4, " mm")
        self.sp_w = spin(0.001, 1000, float(w), 4, " mm")
        self.sp_g = spin(0.001, 1000, float(g), 4, " mm")
        self.sp_f = spin(0.01, 1000, 5.0, 3, " GHz")
        row = QHBoxLayout()
        for label, sp in (("εr", self.sp_er), ("h", self.sp_h), ("W", self.sp_w), ("G", self.sp_g), ("f", self.sp_f)):
            row.addWidget(QLabel(label))
            row.addWidget(sp)
        f.addRow(row)
        self.out = QLabel("")
        self.out.setTextFormat(Qt.TextFormat.RichText)
        self.out.setWordWrap(True)
        f.addRow(self.out)
        self.setLayout(f)
        self.recalc()

    def recalc(self) -> dict:
        er, h, w, g, fr = (s.value() for s in (self.sp_er, self.sp_h, self.sp_w, self.sp_g, self.sp_f))
        ms_e, ms_z = tline.ms_eeff(w, h, er), tline.ms_z0(w, h, er)
        cp_e, cp_z = tline.gcpw_eeff_z0(w, g, h, er)
        lg_ms, lg_cp = tline.guided_mm(fr, ms_e), tline.guided_mm(fr, cp_e)
        w50 = tline.ms_width(50, h, er)
        g50 = tline.gcpw_gap(50, w, h, er)
        res = {"ms_z": ms_z, "ms_e": ms_e, "cp_z": cp_z, "cp_e": cp_e, "lg_ms": lg_ms, "lg_cp": lg_cp, "w50": w50, "g50": g50}
        g50_txt = f"{g50:.4f} mm" if g50 else "做不到（線太寬或太窄）"
        self.out.setText(
            f"<table cellpadding='4'><tr><th></th><th>Z0</th><th>εeff</th><th>λg</th><th>λg/2</th><th>λg/4</th><th>50 Ω</th></tr>"
            f"<tr><td><b>MS</b>（W）</td><td>{ms_z:.1f} Ω</td><td>{ms_e:.3f}</td><td>{lg_ms:.2f} mm</td>"
            f"<td>{lg_ms / 2:.2f} mm</td><td>{lg_ms / 4:.2f} mm</td><td>W = {w50:.4f} mm</td></tr>"
            f"<tr><td><b>CPW</b>（W、G）</td><td>{cp_z:.1f} Ω</td><td>{cp_e:.3f}</td><td>{lg_cp:.2f} mm</td>"
            f"<td>{lg_cp / 2:.2f} mm</td><td>{lg_cp / 4:.2f} mm</td><td>這個 W 的 G = {g50_txt}</td></tr></table>"
            f"<span style='color:gray'>CPW 過孔牆間距建議 ≤ λg/20 = {lg_cp / 20:.2f} mm（至少 &lt; λg/10 = {lg_cp / 10:.2f} mm）。"
            f"半波長共振腔長度 ≈ λg/2（開路端的邊緣效應會讓實際共振略低，以 HFSS 為準）。</span>")
        return res


class CadHelpDialog(QDialog):
    def __init__(self, parent=None, substrate: dict | None = None, width: float = 1.0, gap: float = 0.2) -> None:
        super().__init__(parent)
        self.setWindowTitle("DXF 編輯器說明 — 功能、MS 與 CPW、繪製注意事項")
        self.resize(1100, 820)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        apply_theme(is_dark_palette(parent))
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self.tabs.addTab(_browser(HTML_FEATURES), "① 功能介紹")
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(CrossSection(), 0)
        self.calc = Calculator(substrate or {}, width, gap)
        v.addWidget(self.calc, 0)
        v.addWidget(_browser(HTML_MS_CPW), 1)
        self.tabs.addTab(w, "② MS 與 CPW")
        self.tabs.addTab(_browser(HTML_TIPS), "③ 繪製注意事項")
