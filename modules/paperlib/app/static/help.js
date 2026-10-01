// 使用說明頁：列出網站所有功能，附截圖。內容寫在 SECTIONS，用簡單的標記：
//   「- 」清單、「  - 」第二層、「1. 」步驟、「> 」提示、**粗體**、`程式字`、[文字](#/路徑) 站內連結
import { h, icon, S, isAdmin, can, esc } from './app.js';

const IMG = (name) => `/static/help/${name}.webp`;

const GROUPS = [
  ['start', '快速上手'],
  ['organize', '整理論文'],
  ['read', '閱讀與標註'],
  ['find', '找論文'],
  ['research', '研究工具'],
  ['team', '協作'],
  ['account', '帳號、裝置與管理'],
];

const SECTIONS = [
  // ───────────────────────────── 快速上手
  { id: 'quick', g: 'start', icon: 'spark', title: '五分鐘上手', body: `
1. **找論文**：左側選單點分類，或在頂列搜尋框輸入關鍵字後按 Enter。
2. **開始讀**：點論文卡片打開。左邊是 PDF，右邊是重點、標註、圖卡、關聯、書目、檔案六個分頁。
3. **做記號**：選取文字後點色盤劃線（黃＝重點、藍＝方法／公式、綠＝參數、紅＝疑問），或按上方的筆形按鈕直接手寫。
4. **標進度**：論文頁右上的下拉選單把狀態改成「待讀／閱讀中／已閱讀」，只有你看得到。
5. **上傳**：右上「上傳」一次拖入多個 PDF，系統會自動抓書目、判斷重複。
> 不知道某個功能在哪？在這頁上方的搜尋框輸入關鍵字，例如「翻譯」「並排」「參數」。` },
  { id: 'devices', g: 'start', icon: 'phone', title: '各裝置怎麼用', body: `
- **Windows／Mac**：直接開網址。點論文會在獨立的「閱讀桌」視窗開啟，可以把論文庫和閱讀桌放在兩個螢幕。
- **iPhone／iPad**：用 Safari 開 https 網址 →「分享」→「加入主畫面」，之後從主畫面全螢幕開啟。頭像選單的「加到主畫面」有圖解。
- **Android**：用 Chrome 開 https 網址 → 頭像選單「安裝成 App」。
- **手機閱讀**：下方分頁列在「閱讀、重點、標註、圖卡、關聯、書目、檔案」之間切換；選取文字後會跳出劃線色盤與翻譯按鈕。` },

  // ───────────────────────────── 整理論文
  { id: 'home', g: 'organize', icon: 'grid', title: '論文群總覽（首頁）', go: ['#/', '回首頁'], img: [['home', '首頁：最近開啟、提醒、各分類卡片']], body: `
由上而下：置頂論文 → **最近開啟**（你最近打開的 12 篇，附上次讀到的頁數）→ 全站必讀進度 → 指派給你的論文 → 你要報告的組會 → 未歸檔待讀 → 新論文追蹤 → 各分類卡片（最新 4 篇縮圖）→ 標籤 → 最近動態。` },
  { id: 'upload', g: 'organize', icon: 'upload', title: '上傳論文', body: `
1. 按右上「上傳」，一次選或拖入多個 PDF。
2. 系統自動：
  - 用檔案內容判斷重複，重複的直接略過（OCR 過的檔案也認得出原始掃描檔）。
  - 抽出 DOI、arXiv 編號、標題、年份。
  - DOI 和既有論文相同時，建議附加為「其他版本」；看起來是補充資料或審稿意見時，建議附加到對應的正文。
  - 沒有文字層的掃描檔，入庫後自動 OCR。
3. 確認清單後按「確認入庫」，可以一併指定分類與標籤；沒指定分類的會進「未歸檔待讀」。
- 已在論文庫的論文，可以在論文頁「檔案」分頁加入附件（補充資料、審稿意見、其他版本）。` },
  { id: 'cats', g: 'organize', icon: 'folder', title: '分類、資料夾與未歸檔', img: [['category', '分類頁：上方是資料夾列，論文依資料夾分段']], body: `
- **分類**＝架設類型大類，一篇論文可以屬於多個分類。分類分成幾個群組，決定側欄與首頁的分段。
- **資料夾**：分類頁上方的資料夾列按「＋新資料夾」建立；資料夾右邊「⋯」可以改名、排序、刪除。
  - 把論文卡片**拖到資料夾**上就移過去；勾選多篇可以一起拖，或按「移到資料夾」。
  - 刪除資料夾不會刪除論文，論文會回到「不在資料夾」。
- **未歸檔待讀**：還沒分類的論文。卡片上的「歸到〇〇」是系統依內容給的建議，按一下就歸檔；也可以勾選多篇按「套用建議」。
- **批次操作**：列表右上「選取」後勾多篇，可以一起加入分類、移到資料夾、加標籤、改我的狀態、指派、設為必讀、比較參數。每個列表也有「BibTeX」按鈕匯出目前的列表。` },
  { id: 'tags', g: 'organize', icon: 'tag', title: '標籤', go: ['#/tags', '看全部標籤'], body: `
- 跨分類的自由註記，例如「P1」「推甄 Ref」「組會報告」。外觀是 \`#名稱\` 加左側色條，和分類的圓角膠囊不同。
- 在論文的「重點」分頁輸入即可新增。「標籤」頁列出所有標籤與論文預覽。
- 「管理 → 標籤」可以設定顏色、說明、順序，也可以改名（同名會合併）或刪除。` },
  { id: 'pin', g: 'organize', icon: 'pin', title: '置頂', body: `
論文卡片的「⋯」選單：
- 在分類頁按是「在此分類置頂」，排在該分類最前面。
- 在其他列表按是「全站置頂」，出現在首頁的「置頂論文」。` },
  { id: 'status', g: 'organize', icon: 'bookmark', title: '閱讀狀態（私人）', go: ['#/todo', '我的待讀'], body: `
- 「未標記、待讀、閱讀中、已閱讀」是**每個人自己的**狀態，別人看不到。
- 在論文頁右上的下拉選單修改，或在列表勾選多篇後按「我的狀態」。
- 左側「我的待讀」「閱讀中」、列表上方的狀態篩選，都依你自己的狀態。` },
  { id: 'versions', g: 'organize', icon: 'merge', title: '預印本與重複論文', go: ['#/versions', '打開'], body: `
- **自動偵測正式發表**：每週檢查只有 arXiv 編號的論文，找到正式版就更新 DOI、期刊、年份（保留 arXiv 編號）；不確定的列出來等人確認。單篇可以在「書目」分頁按「檢查是否已正式發表」。
- **重複論文**：同一 DOI、同一 arXiv、預印本與正式版、或同作者且標題幾乎相同的，會列在左側「預印本與重複論文」。
- **合併**（需要「管理」權限）：選要保留的那篇按「合併」。另一篇的 PDF 變成「其他版本」，標註、討論、圖卡、參數、關聯、分類、標籤、閱讀狀態、指派、組會、入門路徑全部搬過去。不是重複的按「不是重複」。` },

  // ───────────────────────────── 閱讀與標註
  { id: 'reader', g: 'read', icon: 'book', title: 'PDF 閱讀器', img: [['paper', '論文頁：左邊 PDF，右邊六個分頁']], body: `
- 論文頁左邊是 PDF，右邊有**重點、標註、圖卡、關聯、書目、檔案**六個分頁。
- **從上次的頁數繼續**：打開論文會跳到上次讀到的頁（記在各自的裝置上）。
- **縮放論文**：手機用兩指捏合、電腦按住 Ctrl（Mac 也可以用觸控板捏合）再滾滑鼠滾輪，只會放大縮小 PDF，網頁的按鈕與側欄不會跟著變大。也可以用工具列的 −／＋ 與「符合寬度」。
- **收起右側欄**：標題列右邊的側欄按鈕把右側欄收起來，PDF 放到最大；再按一次展開。
- 上方工具列：切換檔案、頁碼、縮放、符合寬度、框選圖卡、手寫、開新分頁。
- **書目**分頁：編輯標題、作者、年份、期刊、DOI、arXiv、citekey、摘要；「用 DOI／arXiv 補齊」從 Crossref 或 arXiv 抓資料。
- **檔案**分頁：正文、補充資料等多個檔案切換閱讀；掃描檔有「OCR 辨識文字」；可下載原檔或帶劃線與筆記的 PDF。` },
  { id: 'annotate', g: 'read', icon: 'marker', title: '劃線、筆記與討論', img: [['annotations', '「標註」分頁：劃線、筆記、手寫與討論串']], body: `
- **個人模式與全域筆記**：「標註」分頁上方（手寫工具列也有）的「全域筆記」開關，**預設關閉**。
  - 關閉（個人模式）：PDF 上和標註列表只顯示你自己的劃線、筆記、手寫；你新增的都是私人，只有你看得到。
  - 開啟（全域筆記）：顯示所有人的公開標註，你新增的標註會公開，可以討論、@提及別人。
  - 開關記在各自的裝置上，所有論文一起套用；單則標註可以在「⋯」改成公開或私人。
- **劃線**：選取文字後點色盤。黃＝重點、藍＝方法／公式、綠＝參數／數據、紅＝疑問。
- **劃線＋筆記**：劃線後直接寫筆記。**頁面筆記、整篇筆記**在「標註」分頁新增。
- **私人筆記**：勾「私人」後只有自己看得到（個人模式下新增的預設就是私人）。
- **討論**：每則標註底下都能回覆，用 \`@名字\` 提及成員，對方會收到通知。
- 點標註會跳到 PDF 上的位置。只有建立者本人或管理員能修改、刪除。` },
  { id: 'ink', g: 'read', icon: 'draw', title: '手寫', img: [['ink', '手寫工具列：筆、螢光筆、橡皮擦、七種顏色']], body: `
- 閱讀器上方的**筆形按鈕**開始手寫，再按一次結束並儲存。
- 工具：筆、螢光筆（半透明、較粗）、橡皮擦（劃過筆跡就擦掉）；七種顏色與三種粗細，上次用的會記住。
- 「復原」一筆一筆退回。
- 平板可以勾「**只用觸控筆**」：只有 Apple Pencil 等觸控筆會畫，手指照常捲動與縮放。
- 同一頁的筆畫存成一則「手寫」標註，出現在「標註」分頁（附縮圖），可以加文字、設私人、回覆討論。
- 只能擦自己的筆跡（管理員可以擦任何人的）。下載「帶劃線與筆記的 PDF」時手寫會一起寫進去。
- **即時儲存**：每畫一筆就存，不用按「完成」。沒網路或伺服器沒回應時，筆跡先存在這台裝置上（工具列顯示「⚠ N 筆待上傳」），連上後自動補送；重新打開論文也會先顯示還沒上傳的筆跡。看到「✓ 已儲存」代表都已存到伺服器。` },
  { id: 'notebook', g: 'read', icon: 'book', title: '筆記頁（空白手寫筆記）', img: [['notebook', '論文旁邊開一本筆記頁：方格、橫線、點陣或空白']], body: `
- 論文閱讀器上方的「**筆記頁**」按鈕：在論文旁邊開一本空白筆記，直接手寫推導、畫圖。每篇論文每個人各一本。
  - **單篇時**：自動改成並排，一側是論文、一側是筆記頁（視窗寬就左右，高就上下）。
  - **已經並排時**：筆記頁開在這篇論文的另一側。
  - 再按一次「筆記頁」或按筆記頁左上的 ×，就關掉筆記頁；原本是單篇的會回到單篇。
- 工具：筆、螢光筆、橡皮擦、捲動（手指翻頁用）、七種顏色、三種粗細、復原；平板可勾「只用觸控筆」。
- 頁面樣式：橫線、方格、點陣、空白；「新增一頁」加在最後面。右上的下載鈕把整本筆記存成 PDF。
- 筆記頁的手寫也會列在論文的「標註」分頁（標「筆記頁 N」），點了會打開那一頁。
- 公開或私人跟「全域筆記」開關一致（預設只有你看得到）。` },
  { id: 'mynotes', g: 'read', icon: 'pen', title: '我的筆記（所有筆記集中看）', go: ['#/notes', '打開我的筆記'], img: [['notes', '我的筆記：筆記頁整本列出，標註與手寫依論文分組']], body: `
- 左側選單「**我的筆記**」：所有論文裡的筆記頁、手寫、劃線、文字筆記都集中在這一頁。
- **筆記頁**以整本列出（第一頁縮圖、頁數、最後修改），點了會在論文旁邊打開。
- **標註與手寫**依論文分組、最近修改的在前；點頁碼或右邊的箭頭，會打開論文並跳到那一則（在閱讀桌裡也可以）。
- 上方可以切換「只有我的／含所有人公開的」，依類型篩選（筆記頁、手寫、劃線、文字筆記），也可以搜尋筆記內容、劃線文字或論文標題。
- 「匯出 Markdown」把目前列出的劃線與文字筆記存成一個 .md 檔，可以貼到 Obsidian、Notion。` },
  { id: 'translate', g: 'read', icon: 'globe', title: '翻譯', body: `
- 在 PDF 選取文字 → 按「翻譯」，右下角出現譯文卡片：「複製」或「劃線並存譯文」（原文劃線、譯文存成標註）。
- 「書目」分頁有「翻譯摘要」，可以存成整篇筆記。
- 行尾斷字（mag-non）會自動接回；同一段文字第二次翻譯直接讀快取，不會重複計費。
> 翻譯服務由管理員在「管理 → 網站、翻譯與 AI」設定。` },
  { id: 'keypoints', g: 'read', icon: 'list', title: '重點欄與 AI 預填', body: `
- 每篇論文有一組共用欄位，預設是「重點、架設／平台、關鍵參數、可萃取特徵／觀測量、與我們實驗的關係、備註」。打字後自動儲存，欄位名稱可以自訂。
- **AI 預填**：「重點」分頁按「AI 預填」，AI 讀全文後給建議，你勾選、修改後才套用。AI 填、還沒人改過的欄位標「AI」。
- **問這篇**：針對單篇論文向 AI 提問。
- 「重點」分頁最下方還有「參數」區塊，見〈參數比較與對照表〉。` },
  { id: 'desk', g: 'read', icon: 'stack', title: '閱讀桌（兩篇並排）', img: [['desk', '並排：寬螢幕左右排，分頁標出「左／右」']], body: `
- **電腦**：點論文會在獨立的「閱讀桌」視窗打開，上方分頁列切換已開的論文（最多 8 篇）。頭像選單可以關掉「獨立視窗」；按住 Ctrl（Mac 用 ⌘）點論文會用瀏覽器新分頁開。
- **手機**：論文頁「⋯ → 加入閱讀桌」。
- **分頁／並排**：「分頁」一次看一篇；「並排」固定兩篇同時看。
  - 視窗寬比高大時**左右**並排，高比寬大時**上下**並排；轉手機方向或拉視窗大小會自動切換。
  - 分頁上標出哪篇在「左／右」（或「上／下」）。
  - **換掉其中一邊**：先點那一邊的論文（藍框表示選中），再點上方分頁的另一篇。
- 切換或換出再換回來，**頁數、筆記分頁、右側欄狀態都會保留**。
- 閱讀桌上方的側欄按鈕一次收起所有論文的右側欄，並排時 PDF 更寬。` },
  { id: 'figures', g: 'read', icon: 'crop', title: '圖表剪貼簿（圖卡）', go: ['#/figures', '打開圖表剪貼簿'], img: [['figures', '圖表剪貼簿：依類型篩選，勾選後下載或做成投影片']], body: `
- **框選**：閱讀器上方的「框選」圖示 → 在 PDF 上拖出範圍 → 確認後存成圖卡。手機也能用手指拖，按 Esc 取消。
- 伺服器用高解析度重新裁切，放大或放進投影片都清楚。
- 自動抓「FIG.」「Table」開頭的文字當圖說；類型分成圖、公式、表格。公式可以「轉成 LaTeX」。
- 圖卡可以複製圖片、下載 PNG、複製 LaTeX、點圖跳回原文位置。
- 左側「圖表剪貼簿」列出全部圖卡，勾選幾張可以「下載 ZIP」或「做成投影片」。` },

  // ───────────────────────────── 找論文
  { id: 'search', g: 'find', icon: 'search', title: '搜尋與語意搜尋', img: [['search', '語意搜尋：用一句描述找意思接近的論文']], body: `
在頂列搜尋框輸入後按 Enter，結果頁上方切換兩種模式（會記住上次用的）：
- **關鍵字**：搜尋標題、作者、期刊、全文、重點欄、公開筆記。多個詞用空白分隔，結果必須全部符合；\`EP\`、\`SM\` 這類縮寫以整個字比對。
- **語意**：用描述找意思接近的論文，例如「用 YIG 做非互易耦合的」。卡片上的「相關：…」是共同的關鍵詞。中文描述需要先設定 AI 或向量模型。` },
  { id: 'links', g: 'find', icon: 'graph', title: '關聯、相似論文與關聯圖', go: ['#/graph', '打開關聯圖'], img: [['graph', '關聯圖：分群版面，點論文凸顯相連的論文']], body: `
- **手動關聯**：「關聯」分頁搜尋另一篇論文，選方向與關係（延伸自、使用其方法／公式、實驗驗證其理論、對照／比較…，也可以自己打）。
- **自動引用**：系統讀參考文獻比對論文庫，自動建立「引用」關聯。手動刪除的不會再被加回來。
- **相似論文**：「關聯」分頁最下方列出最相似的幾篇與共同關鍵詞，可直接「建立關聯」。
- **關聯圖**：分群版面（每個分類一框）與時間軸版面（橫軸年份）。節點越大被引用越多；點論文只凸顯相連的論文，右側列出引用誰、被誰引用。可搜尋、篩選分類、「以這篇為中心」。` },
  { id: 'feeds', g: 'find', icon: 'rss', title: '新論文追蹤', go: ['#/feeds', '打開新論文追蹤'], img: [['feeds', '新論文追蹤：找到的論文先列在「待看」']], body: `
- 每天在設定的時間自動檢查（預設 7:00），也可以按「立即檢查」。
- **arXiv 追蹤**：填關鍵字，可限制 arXiv 分類（例如 \`quant-ph\`）。
- **期刊 RSS**：內建 PRL、PRB、PRA、PR Applied、PRX Quantum、Nature Physics、Nature Communications、npj QI，也可以貼其他 RSS。
- 找到的論文先列在「待看」，按「加入論文庫」才建立（arXiv 的會一併下載 PDF）；不要的按「略過」，已在論文庫的會自動標示。
- 每個追蹤可以設定「加入時自動加的標籤」。` },
  { id: 'jsearch', g: 'find', icon: 'search', title: '期刊搜尋', go: ['#/jsearch', '打開期刊搜尋'], img: [['jsearch', '期刊搜尋：論文庫關鍵字依篇數排序，可選交集或聯集']], body: `
在指定期刊裡依關鍵字與時間範圍找論文。
- **期刊**：Physical Review 系列、Nature 系列、Science 與 Science Advances、APL、NJP、Optica 等；每組可以全選或清除，有「管理」權限的人可以用 ISSN 新增期刊。
- **關鍵字**：從論文庫標題與摘要抽出的單字與片語，**依出現在幾篇論文排序**，長條與數字是篇數；也可以自己輸入。
- **交集／聯集**：交集＝所有勾選的字都要出現；聯集＝出現任一個就算。還可以填「排除」的字。
- **時間範圍**：近 1 個月到 5 年，或自訂起訖日期。
- 結果標出命中的關鍵字與被引用次數，論文庫已有的會打勾。勾選後「加入論文庫」。
- **存成自動追蹤**：把目前的條件存成追蹤，之後每天自動檢查。
> 建議管理員設定免費的 OpenAlex 金鑰（管理 → 維護與備份），搜尋會比較完整；沒有金鑰時改用 Crossref。` },

  // ───────────────────────────── 研究工具
  { id: 'params', g: 'research', icon: 'table', title: '參數比較與對照表', go: ['#/params', '打開參數表'], img: [['params', '比較表：自動算協同度與耦合區間'], ['glossary', '名稱對照：各種寫法、單位與慣例陷阱']], body: `
四個分頁：
- **比較表**：加入要比較的論文（或在列表勾選後按「比較參數」）。每列一個參數、每欄一篇論文；滑鼠移到格子上看原文寫法與頁碼，有權限的人點格子就能改。有 g、κ_m、κ_c 時自動算**協同度 C = g²/(κ_m κ_c)** 與**耦合區間**。可以匯出 CSV、請 AI 解讀整張表。
- **名稱對照**：約 30 個 magnon 常用參數，列出其他寫法（g、g_m、g_mc、g_eff、J…）、統一單位、定義、慣例與陷阱（HWHM／FWHM、rad/s 與 Hz、Oe 與 mT）。
- **預估值**：各平台的典型數量級（YIG＋3D 腔、平面共振器、開放波導、超導 qubit…），並和論文庫實測值對照。
- **qubit／cavity 對照**：把 magnon 的說法對應到 cQED 與波導 QED 的語言，例如 κ_m ↔ γ₁ = 1/T₁、耗散耦合 ↔ 關聯衰減 Γ₁₂、多點耦合 ↔ 巨原子。
- **論文頁**：「重點」分頁最下方的「參數」區塊可以手動填，或「AI 抽取」讓 AI 讀全文找參數、統一單位後給你勾選。` },
  { id: 'ai', g: 'research', icon: 'spark', title: '問論文庫與 AI 綜述', go: ['#/ask', '問論文庫'], body: `
- **問論文庫**：AI 先在論文庫搜尋相關論文再回答，用 \`[citekey]\` 標出處，點了會跳到該論文；可以限定分類。
- **AI 綜述**（管理員或有「管理＋AI」權限）：
  1. 逐篇填寫重點欄（已經有人寫的欄位不覆蓋）。
  2. 分類綜述：一句話總結、研究脈絡、參數比較表、主要結果、未解問題、和我們實驗的關係、建議閱讀順序。
  3. 整個論文庫總覽。
  - 也可以「一鍵」三步依序在背景執行，綜述可以下載成 Markdown。
> AI 產生的內容請對照原文確認，尤其是數值。` },
  { id: 'slides', g: 'research', icon: 'slides', title: '組會投影片（.pptx）', body: `
- **整場組會**：「組會／閱讀清單」每場右上角「投影片」。
- **單篇論文**：論文頁「⋯ → 產生報告投影片」。**圖卡**：圖表剪貼簿勾選後「做成投影片」。
- 內容來源可選「直接用重點欄」（立即、免費）或「AI 精簡成要點」。
- 每篇：標題頁 → 這篇在做什麼 → 關鍵參數表 → 圖卡 → 可萃取特徵與和我們的關係 → 疑問與討論（紅色「疑問」標註）。
- 產生 16:9 的 PowerPoint，可以用 PowerPoint、Keynote、Google 簡報開啟再修改；完整重點欄放在講者備忘稿。` },

  // ───────────────────────────── 協作
  { id: 'assign', g: 'team', icon: 'at', title: '指派論文', go: ['#/assigned', '指派給我'], body: `
- 論文頁「⋯ → 指派給成員」、卡片「⋯ → 指派給…」，或勾選多篇後按「指派」。可以一次選多位成員並附說明。
- 被指派的人收到通知，論文自動列入他的「待讀」與「指派給我」。
- 他改成「已閱讀」時，指派的人會收到通知，論文頁他的名字旁打 ✓。
- 指派只公開「有沒有讀完」，其他閱讀狀態仍是私人的。` },
  { id: 'required', g: 'team', icon: 'flag', title: '全站必讀', go: ['#/required', '全站必讀'], body: `
- 有「管理」權限的人可以把論文設為必讀，全體成員會收到通知。
- 左側「全站必讀」顯示你讀了幾篇，首頁有進度條，卡片上有紅色「必讀」標記。
- 管理員看得到每位成員的進度，點名字可以看他還差哪幾篇；一般成員只看得到自己的。` },
  { id: 'meetings', g: 'team', icon: 'calendar', title: '組會與閱讀清單', go: ['#/meetings', '打開組會'], body: `
- 新增組會（日期、標題、備註），再加入論文與報告人，也可以只寫自由主題。
- 論文頁「⋯ → 排進組會」可以直接排入。
- 被排到的人收到通知，首頁提醒「你接下來要報告」；論文頁標題下方顯示組會日期與報告人。` },
  { id: 'paths', g: 'team', icon: 'route', title: '新人入門路徑', go: ['#/paths', '打開入門路徑'], img: [['paths', '入門路徑：每篇的閱讀目標與成員進度']], body: `
- **建立**（需要「指派」權限）：「新增路徑」→ 加入論文並排順序，每篇寫「讀這篇要看懂什麼」。有 AI 時可以按「起草」自動挑 4–8 篇、由淺入深排序。
- **加入**：成員自己「加入這條路徑」，或由學長姐「安排成員」。加入後論文自動列入「待讀」。
- **進度**：依每個人的「已閱讀」狀態計算，路徑成員的進度對大家公開。
- 首頁提醒「下一篇」；論文頁顯示「路徑 第 N/M 步」與這一步的閱讀目標。` },
  { id: 'notify', g: 'team', icon: 'bell', title: '通知', body: `
右上角鈴鐺的紅點代表有未讀通知。會通知的事件：
- 有人 @你、回覆你參與的討論
- 有人指派論文給你、你指派的論文被讀完
- 有論文被設為全站必讀、你被排進組會報告、有人幫你安排入門路徑
- 點通知直接跳到對應的論文或標註。在「我的帳號」填 Email 可以訂閱每週摘要。` },
  { id: 'dashboard', g: 'team', icon: 'chart', title: '實驗室動態看板', go: ['#/dashboard', '打開看板'], img: [['dashboard', '實驗室動態：新增、標註、成員貢獻與閱讀進度']], body: `
- 可以看 30 天、90 天或一年：新增論文、公開標註、討論、圖卡、關聯的數量與增減。
- 每月新增論文與標註、發表年份分布、各分類論文數、成員貢獻、最常被打開的論文、熱門討論。
- 閱讀進度：全站必讀完成率、指派完成率、各入門路徑的成員進度。
- **隱私**：只統計公開內容；開啟次數只統計總數，不顯示誰看了哪篇。` },

  // ───────────────────────────── 帳號、裝置與管理
  { id: 'app', g: 'account', icon: 'offline', title: '安裝成 App 與離線閱讀', img: [['phone', '手機：安裝成 App 後全螢幕開啟']], body: `
- **安裝**：頭像選單「安裝成 App」（Android、電腦的 Chrome／Edge）或「加到主畫面」（iPhone／iPad，有圖解）。
- **需要 https 網址**：用 \`http://100.x.x.x:8080\` 開啟時仍可使用網站，但不能安裝與離線。
- **離線閱讀**：看過的頁面會留在裝置上；讀過的最近 20 篇 PDF 在背景完整下載。論文頁「⋯ → 離線保存」會把這篇的 PDF、附件、標註、圖卡、參數存起來，不會被擠掉。
- 離線時可以閱讀，但新增或修改無法儲存。頭像選單「離線閱讀與儲存空間」可以查看或清除。
- 登出會清掉這個帳號在裝置上的快取。` },
  { id: 'export', g: 'account', icon: 'download', title: '匯出與 Zotero', body: `
- **BibTeX**：每個列表都有「BibTeX」按鈕；論文頁選單「複製 BibTeX」。
- **帶劃線與筆記的 PDF**：劃線、筆記、手寫寫進 PDF，最後附一頁重點欄與筆記摘要（只含公開筆記和你自己的私人筆記）。
- **Markdown 筆記**：論文頁「⋯ → 匯出筆記（Markdown）」，適合貼到 Obsidian、Notion。
- **CSV、全部 BibTeX、資料庫備份**：「管理 → 維護與備份」。
- **Zotero 雙向同步**：管理員在「管理 → Zotero」設定。` },
  { id: 'theme', g: 'account', icon: 'moon', title: '外觀', body: `
- 右上角半圓按鈕選「跟隨系統、淺色、深色」。
- 「PDF 也用深色」會把 PDF 頁面反相，適合晚上閱讀。設定記在各自的裝置上。` },
  { id: 'myaccount', g: 'account', icon: 'user', title: '我的帳號與安全', body: `
右上角頭像 →「我的帳號」：
- 修改顯示名稱（別人用 \`@這個名字\` 提到你）、填 Email 並選擇是否訂閱每週摘要。
- **兩步驟驗證**：用 Google Authenticator、Microsoft Authenticator、1Password 等 App。
- 登出其他裝置、查看自己的權限；頭像選單也可以「變更密碼」。
- 頭像選單的「程式版本」顯示目前伺服器的版本。
> 同一帳號 15 分鐘內錯 8 次會鎖 15 分鐘。忘記密碼或手機遺失請找管理員重設。` },
  { id: 'register', g: 'account', icon: 'user', title: '註冊與站長', body: `
- **申請帳號**：登入頁按「還沒有帳號？申請註冊」，填用戶名稱、帳號、密碼（至少 8 字元）、Email，可以附一段說明（例如你是誰、哪個實驗室）。送出後等站長審核，通過會寄信通知。
- 審核中的帳號還不能登入，登入時會顯示「申請還在等站長審核」。
- **站長**：網站的擁有者，擁有管理員的全部權限，另外負責審核註冊。有站長之後，只有站長能指定或取消站長，其他管理員也不能修改站長的帳號。
- 還沒有站長時，管理員可以在「管理 → 使用者與權限」對任一帳號按「設為站長」；在那之前由管理員代為審核註冊。
- 最後一位站長不能取消自己，要先把另一位設為站長（交接）。
- **Email**：還沒填 Email 的帳號每次登入會提醒填寫；填了才收得到通知信與每週摘要。` },
  { id: 'perms', g: 'account', icon: 'shield', title: '角色與權限', body: `
三種角色：**管理員、成員、唯讀訪客**，每個帳號都可以逐項加開或收回權限。
- **成員預設可以**：上傳、編輯書目與重點欄、分類與標籤、劃線筆記與討論、圖卡、關聯、翻譯、AI、指派與組會、入門路徑。
- **唯讀訪客**：閱讀與翻譯，以及自己的閱讀狀態。
- **只有管理員**：刪除論文、管理分類與標籤清單、全站必讀、修改參數參考表、合併重複論文、帳號、網站設定、備份、Zotero。
- **限制項「只能修改自己上傳的論文」**：適合學弟妹或外部合作者，其他論文只能閱讀、劃線、翻譯。
- 標註與關聯只有建立者本人或管理員能修改、刪除。刪除的論文與檔案會移到回收區（\`data/trash/\`），不會直接消失。` },
  { id: 'admin', g: 'account', icon: 'gear', title: '管理頁', admin: true, go: ['#/admin', '打開管理'], body: `
右上角頭像 →「管理」（有「管理」權限但不是管理員的人，只看得到「分類」「標籤」）：
- **使用者與權限**：新增帳號、角色、逐項權限、重設密碼、重設兩步驟驗證、停用帳號。
- **分類／標籤**：新增、改名、顏色、群組、說明、排序、刪除。
- **網站、翻譯與 AI**：網站名稱與網址、翻譯服務、AI 服務、實驗室背景、語意搜尋的向量模型。按「儲存並測試」可以看錯誤訊息。
- **通知與摘要**：SMTP、Webhook（Discord、Slack）、每週摘要時間，可以預覽與寄測試信。
- **Zotero**：Library ID、API 金鑰、收藏夾、自動同步。
- **維護與備份**：資料庫備份、匯出、重建索引、重新分析引用、OCR 全部掃描檔、預印本自動檢查、AI 批次抽取參數、新論文追蹤排程、OpenAlex 金鑰、背景工作。
- **動態紀錄**：最近 200 筆操作（含登入 IP）。` },
  { id: 'faq', g: 'account', icon: 'help', title: '常見問題', body: `
- **瀏覽器擋下閱讀桌視窗？** 允許這個網站的彈出視窗，或在頭像選單關掉「論文開在獨立的閱讀桌視窗」。
- **掃描檔選不到字、搜尋不到？** 在「檔案」分頁按「OCR 辨識文字」。中文論文請管理員把辨識語言設成 \`eng+chi_tra\`。
- **翻譯或 AI 沒有反應？** 請管理員到「管理 → 網站、翻譯與 AI」按「儲存並測試」看錯誤訊息。
- **自動引用太雜？** 關聯圖取消勾選「自動引用」；個別錯誤的關聯在「關聯」分頁刪除，不會再被加回來。
- **手機沒有「安裝」、離線不能用？** 網址必須是 https；iPhone 用 Safari 的「分享 → 加入主畫面」，Android 用 Chrome。
- **框選圖卡時頁面一直捲動？** 先按「框選」圖示（按鈕變藍）再拖。
- **語意搜尋找不到中文描述的論文？** 需要設定 AI 或向量模型，或直接用英文描述。
- **更新後看起來沒變？** 頭像選單看「程式版本」；版本對但畫面舊，按 Ctrl＋F5 或用無痕視窗開一次。
- **怎麼更新網站、看網站狀況？**（管理員）在電腦上雙擊新版裡的 \`console-windows.bat\`（Mac 用 \`console-mac.command\`）打開控制台：可以監控網站、看錯誤紀錄與容器日誌、備份、重新啟動，選擇新版 zip 就能一鍵更新。也可以用 \`update-windows.bat\` 或 SSH 執行 \`update.sh\`。` },
];

// ───────────────────────────── 簡易標記 → DOM
function inline(t) {
  return esc(t)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/`(.+?)`/g, '<code>$1</code>')
    .replace(/\[(.+?)\]\((#\/[^)\s]*)\)/g, '<a href="$2">$1</a>');
}
function render(src) {
  const out = h('div', { class: 'help-body' });
  let list = null, sub = null, ol = null;
  const html = (tag, t, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; e.innerHTML = inline(t); return e; };
  for (const raw of src.trim().split('\n')) {
    const line = raw.replace(/\s+$/, '');
    if (!line) continue;
    let m;
    if ((m = line.match(/^ {2}- (.*)/))) {
      const host = (list || ol)?.lastElementChild;
      if (!host) { out.append(html('p', m[1])); continue; }
      if (!sub || sub.parentElement !== host) { sub = document.createElement('ul'); host.append(sub); }
      sub.append(html('li', m[1]));
    } else if ((m = line.match(/^- (.*)/))) {
      if (!list) { list = document.createElement('ul'); out.append(list); }
      ol = null; sub = null; list.append(html('li', m[1]));
    } else if ((m = line.match(/^\d+\. (.*)/))) {
      if (!ol) { ol = document.createElement('ol'); out.append(ol); }
      list = null; sub = null; ol.append(html('li', m[1]));
    } else if ((m = line.match(/^> (.*)/))) {
      list = ol = sub = null; const e = html('div', m[1], 'help-tip'); e.prepend(h('span', { class: 'help-tip-ic' }, icon('info'))); out.append(e);
    } else {
      list = ol = sub = null; out.append(html('p', line));
    }
  }
  return out;
}

function lightbox(src, cap) {
  const close = () => { box.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  const box = h('div', { class: 'help-lightbox', onclick: close },
    h('img', { src, alt: cap }), h('div', { class: 'help-lightbox-cap' }, cap));
  document.addEventListener('keydown', onKey);
  document.body.append(box);
}

const plain = (s) => `${s.title} ${s.body}`.replace(/[*`>#\[\]()]/g, ' ').toLowerCase();

// view：顯示的容器；focus：要捲到的段落 id；base：段落連結的前綴（#/help 或 #/admin/help）
export function viewHelp(view, focus = '', base = '#/help') {
  const embedded = base !== '#/help';
  view.classList.add(...(embedded ? ['help-page', 'embedded'] : ['page', 'help-page']));
  const secEls = new Map();
  const tocLinks = new Map();
  const visible = SECTIONS.filter((s) => !s.admin || isAdmin() || can('manage'));

  const card = (s) => {
    const el = h('section', { class: 'help-sec', id: `help-${s.id}`, 'data-id': s.id },
      h('div', { class: 'help-sec-head' },
        h('span', { class: 'help-sec-ic' }, icon(s.icon || 'info')),
        h('h2', {}, s.title),
        s.admin ? h('span', { class: 'pill' }, '管理員') : null,
        s.go ? h('a', { class: 'btn small ghost help-go', href: s.go[0] }, s.go[1], ' →') : null),
      render(s.body),
      s.img ? h('div', { class: `help-shots n${s.img.length}` }, s.img.map(([name, cap]) =>
        h('figure', { class: `help-shot ${name.startsWith('phone') ? 'narrow' : ''}` },
          h('button', { class: 'help-shot-btn', 'aria-label': `放大：${cap}`, onclick: () => lightbox(IMG(name), cap) },
            h('img', { src: IMG(name), alt: cap, loading: 'lazy', decoding: 'async', onerror: (e) => e.target.closest('figure').remove() })),
          h('figcaption', {}, cap)))) : null);
    secEls.set(s.id, el);
    return el;
  };

  const jump = (id) => {
    const el = secEls.get(id); if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    history.replaceState(null, '', `${base}/${id}`);
    mark(id);
  };
  const mark = (id) => tocLinks.forEach((a, k) => a.classList.toggle('on', k === id));

  const toc = h('nav', { class: 'help-toc', 'aria-label': '目錄' },
    GROUPS.map(([g, label]) => {
      const items = visible.filter((s) => s.g === g);
      if (!items.length) return null;
      return h('div', { class: 'help-toc-grp', 'data-g': g },
        h('div', { class: 'help-toc-title' }, label),
        items.map((s) => { const a = h('a', { href: `${base}/${s.id}`, 'data-id': s.id, onclick: (e) => { e.preventDefault(); jump(s.id); } }, s.title); tocLinks.set(s.id, a); return a; }));
    }));

  const content = h('div', { class: 'help-content' },
    GROUPS.map(([g, label]) => {
      const items = visible.filter((s) => s.g === g);
      if (!items.length) return null;
      return h('div', { class: 'help-grp', 'data-g': g }, h('h3', { class: 'help-grp-title' }, label), items.map(card));
    }));
  const none = h('div', { class: 'empty hidden' }, h('p', {}, '找不到相關的說明，換個關鍵字試試。'));

  const filter = (q) => {
    q = q.trim().toLowerCase();
    const words = q.split(/\s+/).filter(Boolean);
    let n = 0;
    for (const s of visible) {
      const ok = !words.length || words.every((w) => plain(s).includes(w));
      secEls.get(s.id).classList.toggle('hidden', !ok);
      tocLinks.get(s.id).classList.toggle('hidden', !ok);
      if (ok) n++;
    }
    content.querySelectorAll('.help-grp').forEach((g) => g.classList.toggle('hidden', !g.querySelector('.help-sec:not(.hidden)')));
    toc.querySelectorAll('.help-toc-grp').forEach((g) => g.classList.toggle('hidden', !g.querySelector('a:not(.hidden)')));
    none.classList.toggle('hidden', n > 0);
  };
  const search = h('input', { type: 'search', class: 'help-search', placeholder: '搜尋說明，例如：翻譯、並排、參數、離線', 'aria-label': '搜尋說明', oninput: (e) => filter(e.target.value) });

  const header = h('div', { class: 'help-head' },
    h('div', {},
      embedded ? null : h('h1', {}, '使用說明'),
      h('p', { class: 'muted' }, `${S.site.name} 的所有功能與操作方式。程式版本 ${S.site.version || ''}`)),
    h('div', { class: 'help-search-wrap' }, icon('search'), search));

  view.replaceChildren(header, h('div', { class: 'help-layout' }, toc, h('div', {}, none, content)));

  // 捲動時標出目錄上目前的段落
  const io = new IntersectionObserver((ents) => {
    const top = ents.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
    if (top) mark(top.target.dataset.id);
  }, { rootMargin: '-10% 0px -70% 0px' });
  secEls.forEach((el) => io.observe(el));

  if (focus && secEls.has(focus)) requestAnimationFrame(() => { secEls.get(focus).scrollIntoView({ block: 'start' }); mark(focus); });
  return () => io.disconnect();
}
