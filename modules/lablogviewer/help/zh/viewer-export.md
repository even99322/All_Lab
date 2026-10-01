# 複製、儲存與匯出
> 把圖複製或存成 PNG／SVG，把數值匯出成 CSV／NPZ，或把整個掃描做成 GIF／MP4 動畫。

## 最短流程
1. **複製圖。** 在圖上按 **⌘C**（Windows：Ctrl+C）或右鍵 → **複製圖表**，直接貼到簡報或文件。多窗格時 **複製所有窗格**：Mac 為 Control+Shift+C、Windows 為 Ctrl+Shift+C。 ![](export_menu.png)
2. **存成圖片。** Viewer 工具列的 **匯出** 按鈕（分享圖示）→ **另存圖表⋯**，選 PNG 或 SVG（向量）。多窗格時另有 **另存作用中窗格⋯** 與 **另存所有窗格⋯**。
3. **匯出數值。** **匯出** → **匯出資料⋯**，依序選 **範圍**、**表示方式**、**範圍（X）**、**格式**，再選目的地，按 **匯出**。 ![](export_data.png)
4. **匯出動畫。** **匯出** → **匯出動畫⋯**：選 MP4 或 GIF、要包含的 trace（全部／選取／顯示中）、播放速度與解析度，按確定後選檔名。 ![](export_animation.png)

## 匯出資料的選項代表什麼
| 選項 | 意義 |
|---|---|
| **範圍**：Active Trace／Selected Traces／Visible Traces／Full Data | 匯出目前 trace、選取的 trace、畫面上顯示的 trace，或整份資料 |
| **表示方式**：Raw Data | 原始數值（複數保留實部與虛部），不受轉換、dB、公式影響 |
| **表示方式**：Current Transform | 套用目前的轉換（Magnitude、Phase、dB…）後的值 |
| **表示方式**：Displayed Data | 與畫面完全相同（包含公式） |
| **範圍（X）**：Full Range／Visible X Range | 全部 X，或只有目前看得到的 X 範圍 |
| **格式**：CSV／NPZ | CSV 是長表格（每列一個點，方便 Excel）；NPZ 保留陣列結構，給 Python（`numpy.load`） |

- 2D 資料一律匯出完整的有效網格。
- 檔案內附中繼資料（來源檔、通道、轉換、範圍），日後可以追溯數值是怎麼來的。

## 動畫
- **MP4**：播放速度 15–200 trace/秒，解析度可選目前大小、720p、1080p。
- **GIF**：每幀固定 10 ms（GIF 格式可靠的最小間隔），所以總長度 = trace 數 × 10 ms。
- 動畫至少需要兩條 trace；匯出在背景進行，可以取消。

## 做不出來時先檢查
- **匯出的數值和畫面不一樣**：確認 **表示方式**——Raw Data 不含 dB 與公式。
- **CSV 在 Excel 打開很慢**：資料很大時改用 NPZ，或只匯出 Visible X Range。
- **「匯出動畫至少需要兩條 Trace」**：只有一條 trace 的資料無法做動畫。
- **「此安裝不支援 MP4 編碼」**：缺少影片編碼器，改用 GIF。
- **⌘C 沒反應**：先點一下圖，讓圖取得焦點。
