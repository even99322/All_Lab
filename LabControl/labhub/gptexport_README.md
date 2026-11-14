# CCU Magnonics & Non-Hermitian Physics — 論文庫分類與匯出規範

> **README 版本：1.1** ｜ **制定日期：2026-11-14** ｜ **1.1 修訂：2026-11-14（A–D 改用論文庫的關聯性定義，見第 10 節）**  
> **用途：**供 Claude 整理／匯出實驗室論文庫，以及 ChatGPT 後續匯入、檢索、比較與引用。  
> **研究範圍：**YIG Magnonics、Cavity/ Waveguide Magnonics、Non-Hermitian Physics、CPA/CPAEP、CLS、Coherent/Dissipative Coupling、PIM、MFC，以及頻域／時域量測。

---

## 0. 核心原則（務必遵守）

1. **一篇研究工作，一個穩定的 `Paper ID`。**格式 `MAG-0001`、`MAG-0002`……；ID 一旦分配，不因重新分類、改名或更新 PDF 而改動。
2. **分類是多維度，不是把論文硬塞到單一資料夾。**每篇必須有 **1 個主要主題**，可有**多個次級標籤**；另外獨立記錄優先度、閱讀狀態、論文類型及證據品質。
3. **優先度不等於論文品質、引用數、發表期刊或閱讀進度。**A–D 代表**與本實驗室的映射類型**（第 3.1 節，與論文庫的「關聯性 A/B/C/D」同一套定義）；尚無足夠資訊時保持空白、列為待評估，不得擅自給 D。
4. **可追溯性優先。**論文主張、公式、實驗數字應盡可能附原始頁碼、式號、圖號；讀不到全文就不能宣稱已核對全文。
5. **不可杜撰。**未知 DOI、作者、公式、參數、研究結果、頁碼一律留空或標示 `待核對`。不得以摘要推測論文的所有公式和實驗條件。
6. **原始資料與 AI 註解分離。**原始 PDF 不被改寫；AI 整理出的摘要、分類、比較結果是獨立資料，可供人工修正。
7. **不以公開資料覆寫實驗室機密。**未公開手稿、實驗筆記、數據與私人註記須另行標記權限，僅在使用者明確授權的範圍內匯出。

---

## 1. 主要主題（Primary Topic）：八選一

`主要主題` 只能填下表**完全一致的英文值**。同時涉及多個領域時，依論文**主要研究問題與核心創新**決定單一主分類，其他概念放入次級標籤。

| 代碼 | `主要主題`（固定值） | 收錄標準 |
|---|---|---|
| FND | `Fundamentals` | Magnonics、YIG 材料性質、自旋波、Kittel/Walker modes 等基礎理論及教科書式文獻 |
| CAV | `Cavity Magnonics` | 腔光子–磁振子強耦合、CMP、cavity input-output、cavity-based hybrid systems |
| WAV | `Waveguide Magnonics` | 波導媒介相互作用、波導中的集體效應、CLS、GSE、傳播相位和邊界條件 |
| NH | `Non-Hermitian Physics` | 非厄米 Hamiltonian、EP/EL、非互易或非厄米模態與光譜特徵 |
| CPA | `CPA / CPAEP` | CPA、scattering zero/pole、CPA exceptional point、generic/non-generic CPAEP |
| CPL | `Coupling Mechanisms` | Coherent/dissipative、waveguide/mirror-mediated coupling、耦合符號、相位及機制比較 |
| DRV | `Driven Magnonics` | Pump-induced magnon、parametric/nonlinear effects、Magnonic Frequency Comb、driven dynamics |
| EXP | `Experimental Methods` | VNA、S11/S21、SHFQC、脈衝、time-domain、FFT、資料擬合及設備方法 |

**主題衝突處理：**

- 主要貢獻是 **CPA/CPAEP 形成條件或其散射判據** → `CPA / CPAEP`，即使使用了波導或非厄米模型。
- 主要貢獻是 **EP/EL 的一般性理論**，不是 CPA → `Non-Hermitian Physics`。
- 主要貢獻是 **波導介導的集體耦合、CLS 或 GSE** → `Waveguide Magnonics`。
- 主要貢獻是 **耦合機制差異本身**（相干 vs. 耗散、相位、傳播或反射邊界）→ `Coupling Mechanisms`。
- 主要貢獻是 **泵浦、非線性與頻率梳** → `Driven Magnonics`。
- 主要貢獻是 **腔–磁振子系統** → `Cavity Magnonics`。
- 論文主要是 **量測、儀器、擬合／分析方法** → `Experimental Methods`。
- 主要是 **基礎背景與模式定義** → `Fundamentals`。
- 若光看摘要仍有歧義：依題目／摘要暫分類，並於「重要限制 / 待確認」寫 `主分類待全文核對`，不可假裝已讀全文。

**不要使用** `EP`、`Magnonics`、`YIG` 等籠統標籤代替「主要主題」；它們只能是次級標籤。

## 2. 次級標籤（Secondary Tags）：可複選

`次級標籤` 以 **半形分號 `;`** 分隔，例如：

`CPAEP; Non-generic EP; Dissipative coupling; Scattering zero; VNA-S21`

以**下列標準寫法**儲存，避免同一概念出現同義異拼。建議按照「物理現象 → 機制 → 模型 → 量測」排序。只標註論文**明確討論**的內容，而不是所有可能關聯的概念。

| 類別 | 允許的標準標籤 | 特別注意 |
|---|---|---|
| 物理模式 | `YIG`; `Kittel mode`; `Walker mode`; `Spin wave` | 如涉及 Walker (2,2,0)，在研究備註寫明模式索引 |
| 腔與波導 | `CMP`; `CLS`; `GSE`; `Waveguide-mediated coupling`; `Mirror-mediated coupling` | 區分系統架構與耦合機制 |
| 非厄米與散射 | `Non-Hermitian Hamiltonian`; `EP`; `EL`; `CPA`; `CPAEP`; `Generic EP`; `Non-generic EP`; `Scattering zero`; `Scattering pole`; `Reflectionless state` | 不可將 Hamiltonian EP、scattering zero EP、pole EP 自動視為同一概念 |
| 耦合與驅動 | `Coherent coupling`; `Dissipative coupling`; `PIM`; `MFC`; `Nonlinear dynamics`; `Phase control` | 必要時記錄複數耦合的相位慣例 |
| 理論工具 | `Input-output theory`; `Scattering matrix`; `Effective Hamiltonian`; `Transfer matrix` | 理論形式不同時不可直接將參數對照 |
| 實驗技術 | `VNA-S21`; `VNA-S11`; `Time-domain`; `SHFQC`; `Ringdown`; `IQ mixing`; `FFT`; `Curve fitting` | 要分清原始量測與數據後處理 |

**新標籤規則：**發現上表沒有的概念時，可以新增至 `proposed_tags.csv`（`Paper ID,建議標籤,原因`）；**先不要**直接在主資料表中創造近義標籤。經人工確認後才升版 README 與標籤字典。

## 3. 重要度（`優先 A-D`）與閱讀狀態

### 3.1 A/B/C/D 的判斷（1.1：與論文庫「關聯性」同一套定義）

| 分級 | 定義 | 典型情境 |
|---|---|---|
| **A — Direct experimental relevance／直接實驗相關** | 直接研究 YIG／magnon microwave system，與現有 YIG＋電磁鐵＋VNA／SHFQC 能力高度對應，可直接重現、比較或作近期實驗依據 | 可在本實驗室架構上直接重現或比較的 CPAEP、CLS、耦合實驗 |
| **B — Transferable experimental method／實驗方法可移植** | 平台可不同，但 PCB／CPW／waveguide／mirror／pump-probe／S-parameter／time-domain 等裝置或量測方法可移植 | 其他平台的波導／鏡面架構、S 參數或時域量測方法 |
| **C — Transferable theory／理論模型可移植** | 平台不同或偏理論，但 input-output、coupled-mode、S-matrix、Green function、Lindblad、EP/PT、collective coupling、waveguide QED 等模型可映射到實驗室系統 | EP／CPA 散射理論、waveguide QED 集體耦合模型 |
| **D — Background／一般背景參考** | 主要為背景或相鄰領域，目前沒有明確可移植的實驗方法或理論模型 | 一般背景、相鄰領域 |

- A/B/C/D 是「與實驗室的映射類型」，**不是**論文品質分數，也不是 Core／Related 的重要性排序。
- 匯出工具直接帶入論文庫的「關聯性」欄位（人工指定或 AI 評級）；AI 評級在 `與本實驗室關聯` 註明「AI 評級」與信心，人工評級註明「人工」。
- **分級必須反映實際內容，不可僅依關鍵字命中**。
- **缺乏足夠資訊 → 留空**，另外在 `classification_warnings.csv` 記錄 `PRIORITY_PENDING`。
- 若論文在新的實驗計畫中重要性提升，允許 A–D 改級，但**不更換 Paper ID**，並記錄修改理由與日期。

### 3.2 閱讀狀態：與 A–D 分開記錄

允許值：

- `未讀`：只有書目與檔案資訊；尚未閱讀內容。
- `摘要已讀`：已讀摘要／頁面資訊，可做**初步**分類。
- `全文已讀`：已閱全文，尚未完成深入整理。
- `深度解析`：已完成模型、關鍵證據與實驗／理論整理。
- `待核對`：有內容矛盾、解析失敗或缺少關鍵頁面，待人工確認。

（1.1：論文庫的閱讀狀態是每位成員各自的，匯出工具一律填 `未讀`；實際閱讀與解析進度請由後續整理更新。）

`論文類型` 允許：`Review`、`Theory`、`Experiment`、`Theory+Experiment`、`Methods`、`Preprint`、`Other`。出版狀態（例如 preprint／peer reviewed）應另外在來源備註標示，不應以 `Preprint` 隱藏論文本身的理論或實驗屬性；若欄位只能存一值，優先選研究性質，於備註記錄出版狀態。

## 4. Paper ID、重複文獻與版本規則

1. 首先查重：**正規化 DOI**（移除 `https://doi.org/`、`doi:`、多餘空白；比較時忽略字母大小寫）；再核對 arXiv ID、標題、作者和年份。
2. **同一研究工作**的 arXiv 版、accepted manuscript、期刊正式版通常併為**一筆**；保留所有可辨識的版本來源，預設以 final published version 作為主要引用。若差異涉及實質修訂，保留版本註記與受影響的結論。
3. 同題但內容不同的獨立文章、正式 sequel 或不同實驗結果，應分配**不同 Paper ID**，用備註建立關聯。
4. DOI 相同不得重複分配 ID；無 DOI 的資料先以 arXiv ID，再以標題＋作者＋年份查重。
5. ID 按現有清單的最大編號依序增加；**不按年份排序重新編號**，刪除的 ID 不重用。若尚無既有 ID，從 `MAG-0001` 開始。
6. 若判定疑似重複但無法確定，不可自動刪除：在 `classification_warnings.csv` 標記 `POSSIBLE_DUPLICATE` 並列出候選 ID。
7. 除 `Paper ID` 以外，盡量保留原始 `DOI`、`arXiv / URL`、`source_path`、`sha256`，方便跨系統對應。

**PDF 命名：**`MAG-0001__short-title.pdf`，例如 `MAG-0001__non-generic-cpaep.pdf`。`short-title` 使用小寫英文字母、數字、連字號；去掉特殊字元。若須儲存多版本，使用 `MAG-0001__v1-arxiv.pdf` / `MAG-0001__published.pdf`，並於 manifest 註明版本，總索引仍維持一筆。

## 5. 匯出資料夾規格（Claude 應依此產出）

```text
CCU_Magnonics_Library_Export/
├── README.md                         # 本規範，請原封不動保留
├── metadata/
│   ├── papers.csv                    # 必要：一篇一列，主索引
│   ├── notes.csv                     # 建議：逐篇解析，可多列
│   ├── evidence.csv                  # 建議：跨論文證據，可多列
│   ├── manifest.json                 # 必要：版本、來源、檔案路徑、校驗資訊
│   ├── classification_warnings.csv  # 必要：問題與待核對事項，可只有表頭
│   └── proposed_tags.csv            # 可選：待審核的新標籤
├── papers/                           # 可存取且獲准匯出的原始 PDF
│   ├── MAG-0001__short-title.pdf
│   └── ...
└── notes/                            # 可選：每篇較完整的研究筆記
    ├── MAG-0001.md
    └── ...
```

### 5.1 `papers.csv` 欄位（**務必與現有 Excel「論文總索引」一致**）

下列 24 欄的**欄位名稱與順序不可自行更動**：

```csv
Paper ID,優先 A-D,閱讀狀態,主要主題,次級標籤,論文標題 Title,第一作者,出版年,期刊 / 來源,DOI,arXiv / URL,PDF 檔名,論文類型,研究問題,物理系統,核心模型 / Hamiltonian,耦合機制,CPA / EP 條件,量測方法,關鍵式 / 圖,與本實驗室關聯,重要限制 / 待確認,原文定位,更新日期
```

- 每篇**恰好一列**，`Paper ID` 唯一。
- `主要主題` 必填（真的無法判定時留空並警告）；`次級標籤` 使用 `;` 分隔。
- `優先 A-D` 只能是 `A`、`B`、`C`、`D` 或空白。
- `出版年`：4 位年份；`更新日期`：`YYYY-MM-DD`。
- `PDF 檔名`：相對路徑，例 `papers/MAG-0001__short-title.pdf`；無 PDF 留空。
- `關鍵式 / 圖`、`原文定位`：以 PDF 所示 **頁碼／公式／圖號** 記錄，例 `PDF p. 5, Eq. (7); Fig. 2`；如果印刷頁碼與 PDF 頁碼不同，寫清楚慣例。
- CSV 使用 `UTF-8 with BOM (utf-8-sig)`，標準 CSV 雙引號跳脫；不要用逗號、空格或換行拆成多篇。
- 缺資料用**空欄位**，不要填 `None`、`N/A`、`null`、`unknown` 作為假資訊。需要注意的缺漏記錄於警告檔。

### 5.2 `notes.csv` 欄位（對應 Excel「逐篇解析」）

```csv
Paper ID,研究問題與創新,系統架構/器件,重要假設,Hamiltonian/方程式,主要推導步驟,定義/單位慣例,實驗裝置與頻段,主要結果,必讀 Figure/Table,與 CPAEP/CLS/PIM 的關聯,尚未解決問題,證據來源（頁、式、圖）,人工核對狀態,更新日期
```

- 一篇可多列（例如理論推導、實驗結論與限制分開），**每一列都要帶 `Paper ID`**。
- `人工核對狀態` 建議使用 `未核對`、`部分核對`、`已核對`、`待人工確認`。
- 只看摘要時，不能將 Hamiltonian、推導或實驗參數寫得像已經全文查驗；應留空並留下核對事項。

### 5.3 `evidence.csv` 欄位（對應 Excel「跨論文證據」）

```csv
證據 ID,研究命題 / 比較問題,Paper ID,主題,結論／公式,原文定義與假設,參數值,單位與頻率慣例,原文頁/式/圖,來源 DOI / URL,相反或支持的文獻 ID,可直接比較？,目前判斷,需再驗證的點,最後更新
```

- 每個獨立證據一列，`證據 ID` 使用 `EVD-000001`、`EVD-000002`……，保持穩定。
- `可直接比較？`：`是`、`否`、`條件式`、`待核對`，不可把不同系統、損耗慣例的數值直接合併。
- 預設**先產出有根據的少量證據**，不為了填滿表格而編寫假資料。

### 5.4 `manifest.json` 必要資訊

每份匯出應提供以下結構（示例中的示意值**不可當成真實論文資訊**）：

```json
{
  "schema_version": "1.1",
  "exported_at": "2026-11-14",
  "producer": "Claude",
  "source_system": "請填實際論文庫來源，例如 Zotero / NAS / local folder",
  "paper_count": 1,
  "pdf_count": 1,
  "papers": [
    {
      "paper_id": "MAG-0001",
      "doi_normalized": "",
      "arxiv_id": "",
      "pdf_relative_path": "papers/MAG-0001__short-title.pdf",
      "sha256": "",
      "source_path": "",
      "source_url": "",
      "source_version": "",
      "content_access": "full_text",
      "export_permission": "authorized"
    }
  ]
}
```

- 上例僅示範 JSON 欄位（不代表實際收錄一篇論文）；真正匯出時**必須**讓 `paper_count`、`pdf_count` 與實際資料相符（零篇時 `papers` 應為空陣列）。
- `sha256` 只記錄**實際計算**出的雜湊；無法計算則留空，不可捏造。
- `content_access` 可填 `full_text`、`abstract_only`、`metadata_only`、`unreadable`。
- `export_permission`：`authorized` 或 `metadata_only`；若無權限附 PDF，保留書目資料但不匯出 PDF。
- `source_path` 可以保存原始相對路徑；避免寫入帳號密碼、存取 token、未授權個資或不必要的私人絕對路徑。

### 5.5 `classification_warnings.csv`（必要）

```csv
Paper ID,warning_code,severity,description,action_required
```

建議 `warning_code`：`PRIORITY_PENDING`、`POSSIBLE_DUPLICATE`、`PDF_MISSING`、`PDF_UNREADABLE`、`DOI_MISSING`、`METADATA_CONFLICT`、`PRIMARY_TOPIC_UNCERTAIN`、`EVIDENCE_NOT_VERIFIED`、`RESTRICTED_SOURCE`。`severity` 使用 `info`、`warning`、`error`。沒有警告則保留表頭、零筆資料。

## 6. 物理內容整理時的特別規範

**這是實驗室研究資料庫，不是一般文摘工具。**需注意：

1. **頻率慣例：**分清 `f` 和 `ω = 2πf`；g、κ、γ、α、β 等可能以 Hz、MHz、rad/s 或 `2π × MHz` 表示，記錄原文單位。
2. **損耗定義：**full linewidth、half linewidth、amplitude decay rate、energy decay rate 可能不同；參數不可憑字母名稱直接相等。
3. **EP 類型：**明確區分 Hamiltonian eigenvalue EP、scattering-matrix EP、zero/pole coalescence、CPAEP 及 non-generic 判準。不能因論文寫 `EP` 就推定研究內容相同。
4. **散射觀測：**區分 `S11`、`S21`、反射／透射、scattering zero、resonance pole 與 coherent perfect absorption；記下埠數與輸入相位條件。
5. **耦合機制：**分辨 coherent、dissipative、complex coupling、waveguide-mediated、mirror-mediated；有外加相位時需標相位定義與耦合點的相對位置。
6. **實驗架構：**記錄 YIG mode（Kittel/Walker）、cavity/waveguide、偏壓磁場、泵浦、掃頻區間、儀器、時域／頻域，以及數據是否已擬合。
7. **引用可信度：**AI 自行解釋需標示 `AI interpretation`，原文可證實的內容標示 `Verified from PDF`；未核對的結論標示 `Needs verification`。
8. **不同文獻比較：**先列模型、假設、邊界條件和符號慣例，再比較 EP 條件或參數。不要只對照表面形式相似的公式。

## 7. Claude 匯出執行流程

Claude 在有權讀取論文庫的情況下，建議依序執行：

1. **Inventory：**列出可存取的 PDF、原始檔名、來源資料夾、書目資訊；不要先移動或刪除原始論文。
2. **Identity & Dedupe：**按 DOI → arXiv → 標題／作者／年份去重；為每項研究工作分配或重用穩定 `Paper ID`。
3. **Metadata：**擷取真實標題、作者、年份、期刊、DOI、連結；對有爭議的書目先註記，不能猜測。
4. **Classify：**依本 README 設定一個主題、多個標準標籤、A–D（若有依據）、閱讀狀態、論文類型。
5. **Analyze：**只有在實際讀到內文時才填 Hamiltonian、CPA/EP 條件、參數、圖／式證據；若文獻太多，可先完成書目與分類，深度解析稍後分批。
6. **Export：**輸出 `papers.csv`、`notes.csv`、`evidence.csv`、`manifest.json`、`classification_warnings.csv`、可授權 PDF 與選用 Markdown 筆記。
7. **Validate：**檢查 ID 唯一性、主題／標籤字彙、相對路徑、檔案存在、年份格式、DOI 去重、manifest 筆數與 CSV 一致。
8. **Report：**寫明總論文數、PDF 筆數、A/B/C/D／未評級數、主題分布、重複候選、缺漏或無法解析筆數；不要宣稱尚未執行的驗證完成。

### 匯出完成的驗收條件

- [ ] `papers.csv` 有 24 個正確表頭；每篇一列，所有 `Paper ID` 唯一。
- [ ] 全部 `主要主題` 落在第 1 節指定的 8 個值內（無法分類的已記錄警告）。
- [ ] 全部 `次級標籤` 使用受控字彙；新增標籤提案另外列出。
- [ ] 所有優先度是 `A/B/C/D/空白`；未讀論文不被誤標成 D。
- [ ] PDF 只包含有權匯出的檔案；所有相對路徑能在匯出資料夾中開啟。
- [ ] `manifest.json` 筆數與實際資料一致；不能計算的 SHA-256 保持空白。
- [ ] `notes.csv` 與 `evidence.csv` 不包含無法回溯的編造推導或數據。
- [ ] 不確定內容已列入 `classification_warnings.csv`。
- [ ] 原始論文庫沒有因本次匯出被刪除、搬動或覆寫。

---

## 8. 可以直接交給 Claude 的工作指令

> 請依照本 `README.md`，將我提供且授權存取的實驗室論文庫整理為 `CCU_Magnonics_Library_Export/`。保留原始檔案，不改動來源資料夾。先建立書目總索引，按照 8 大主題、受控標籤、A–D 關聯度與獨立的閱讀狀態分類；用 DOI／arXiv／標題作者年份去重，為每篇研究工作指派固定 `MAG-xxxx` ID。**不要因 PDF 未讀而擅自列為 D；不要推測未核對的實驗數據、Hamiltonian、EP 條件或頁碼。**依 README 精確輸出 `papers.csv`、`notes.csv`、`evidence.csv`、`manifest.json`、`classification_warnings.csv` 和可合法匯出的 PDF。完成後檢查表頭、路徑、筆數與去重狀態，並附分類統計及待核對問題列表。如工具無法直接存取檔案或匯出 PDF，明確指出障礙並產出可行的部分結果，不得假裝完成。

## 9. 後續匯入 ChatGPT 的方式

1. 優先帶回 `metadata/papers.csv`、`manifest.json`、`classification_warnings.csv`，確認去重與分類品質。
2. 再依研究需求帶入 `notes.csv`、`evidence.csv` 與 A 類的**核心 PDF**。ChatGPT 的專案檔案數量有限，不需一次上傳所有 PDF。
3. 可將這些 CSV 整合回既有 `Magnonics_Research_Library_Starter.xlsx` 的「論文總索引」「逐篇解析」「跨論文證據」三張工作表；欄位名稱已為此對齊。
4. 日後更新採**增量匯出**：沿用既有 ID、保留修訂日期，新增／變更記錄應可辨識，不要每次重建編號。

**本 README 為資料庫的分類與交換契約。任何類別、標籤或欄位名稱調整，必須升版、記錄變更，確保 Claude 與 ChatGPT 使用同一套定義。**

## 10. 變更紀錄

| 版本 | 日期 | 變更 |
|---|---|---|
| 1.0 | 2026-11-14 | 初版 |
| 1.1 | 2026-11-14 | 第 0、3.1 節：A–D 改用論文庫「關聯性 A/B/C/D」的定義（A 直接實驗相關、B 實驗方法可移植、C 理論模型可移植、D 背景）；第 3.2 節：匯出工具的閱讀狀態一律填 `未讀`；manifest `schema_version` 改為 `1.1` |
