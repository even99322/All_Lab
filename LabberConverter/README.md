# Labber Converter / Labber 轉換工具

版本 0.4.2：
- **NAS 比較不會失敗**：網路磁碟（SMB）常常不支援 HDF5 檔案鎖，以前會被當成「有人在寫」或 I/O 錯誤；現在自動改用不上鎖的方式開（仍檢查來源穩定）。
  可恢復的網路錯誤（含 Windows 的「網路名稱無法使用」等）最多試 5 次、等待 0.5 → 1 → 2 → 4 秒。
- **Database 備份不再堆在 Database.hdf5 旁邊**：放到隱藏資料夾 `.labber-converter-backups/`，只留最新 5 份；以前留在旁邊的 `Database.hdf5.*.bak` 會自動搬進去。
- **「Supported input: labcontrol-nc/1 with layout=grid…」**：
  - Lab Control 的非規則掃描（`layout=point`，例如電流異步）只有一個掃描量時可以轉了（依量測順序、記錄實際設定值）；
  - 單張曲線（沒有掃描軸）轉成一筆資料；
  - 不是量測的檔（例如 Lab Control 自己的 HDF5、其他程式的 .hdf5）改成「不轉換」，不算失敗；
  - 兩個以上掃描量的檔仍不支援，但錯誤會寫出是哪幾個量。
- **「同步既有資料庫分類」可以重建分類**：按下時會問要不要移除「沒有任何檔案在用」的舊分類（例如以前匯入留下、資料夾裡已經沒有的專案名稱）。

版本 0.4.1：修正選取 `10/Data_1013` 這類月份資料夾時，多產生一層 `Data_1013/10` 與分類 `10/1013/10` 的問題。重匯入舊版重複月份路徑時會修正輸出副本，來源不變。保留 0.4.0 的液態玻璃、暫存、續轉及資料庫變動檢查。

版本 0.4.1：修正選取 `10/Data_1013` 這類月份資料夾時，多產生一層 `Data_1013/10` 與分類 `10/1013/10` 的問題。重匯入舊版重複月份路徑時會修正輸出副本，來源不變。保留 0.4.0 的液態玻璃、暫存、續轉及資料庫變動檢查。

把 `labcontrol-nc/1` 的 NetCDF 量測資料轉成 Labber 1.8.6 形式的 HDF5 data log，並建立或合併 `Database.hdf5`。這是一個獨立工具，不修改 LabLogViewer。

## 開啟與操作

- macOS：開啟 `dist/Labber Converter 0.4.1.app`。在這個工作區也可以雙擊 `Launch.command`。
- Windows 使用者：取得單檔版 `LabberConverter_v<版本>_windows.exe`（GitHub 發佈 `labberconverter-v<版本>`，由 `.github/workflows/labberconverter.yml` 打包並在 exe 裡實際轉一個檔檢查）後直接雙擊，操作方式和 Mac 相同；不需安裝 Python，也不需附帶其他程式元件資料夾。目前這台 Mac 尚未產出或驗證 Windows EXE。
- 在 Windows 建置一次：將原始碼放到已安裝 Python 3.10 以上的 Windows 電腦，雙擊 `Build Windows.bat`。完成後只要傳送 `dist/Labber Converter.exe` 給使用者即可。`Install Windows.bat` 和 `Launch.bat` 是原始碼開發者的另一種啟動方式，不是 EXE 使用者必須執行的步驟。
- 手動啟動：`python -m pip install -r requirements.txt`，再執行 `python app.py`。

1. 按「加入檔案」可一次選取多個 `.nc`、ZIP 或既有 Labber `.hdf5/.h5`。
2. 按「加入資料夾」加入一個資料夾，也可以繼續加入其他資料夾。資料夾內的檔案會遞迴處理。可直接拖曳多個檔案或資料夾到視窗。
3. 瀏覽並選擇「輸出資料庫主資料夾」。要建立獨立資料庫，可按「建立新資料庫…」，在選取視窗新增並選擇母資料夾，程式會立即建立並檢查 `Database.hdf5`，不需要先加入輸入資料。直接轉換到新的母資料夾時也會先建立 Database。匯入既有資料庫時，選包含 `Database.hdf5` 的母資料夾。
4. 專案前綴可留白。新轉換 NC 依範本將 `Data_1021` 對應為 `10/1021`；額外來源子資料夾會同時保留在實際路徑與 Project 分類。填入 `My project` 時，分類為 `My project/10/1021`。原始 NC 的 `project` 保留在 `/Conversion`。既有 Labber 檔案保留量測資料與原本分類語意，必要時補上對應日期或來源子資料夾。
5. 按「開始轉換」。背景轉換時介面仍可操作，停止按鈕會在目前檔案完成後停止後續工作。
6. 在原廠 Labber 的 **Open Database** 選擇含 `Database.hdf5` 的最上層母資料夾，再按 **Reload Database**。`2026`、`10`、`Data_1021` 不會各自建立 Database，不能直接當成獨立資料庫開啟。完成紀錄會顯示已確認的 Database 完整路徑。

右上可切換繁體中文 / English、淺色 / 深色 / 跟隨系統。玻璃材質面板可調整厚度與霧度；折射演算法和色盤沿用本工作區 LabLogViewer。小視窗使用捲動，控制項不壓縮文字。

## 暫存、來源檢查與續轉

- **本機暫存與傳回驗證**預設開啟。檢查來源穩定後建立本機副本，在本機完成轉換與逐點數值驗證，再傳送至輸出資料夾內的隱藏暫存檔。比對 SHA-256 校驗值後才發佈正式檔名，發佈後再次讀回驗證。ZIP 在同批次內可共用已驗證的本機副本。關閉此選項時直接讀取來源，轉換工作仍在本機暫存區完成。
- 可恢復的逾時、連線中斷、stale handle 或 I/O 讀回錯誤，每項 I/O 操作最多嘗試 **5 次**，間隔加倍（0.5、1、2、4 秒）；權限不足、格式不支援、來源變動不會自動重試。重試過程與來源、輸出階段會出現在介面和報告。本機暫存需要額外空間，正常完成、失敗或取消後清除；強制終止程序時，作業系統暫存目錄可能殘留工作目錄。
- 來源先觀察 **1 秒**，比對大小、修改／變更時間與檔案識別。HDF5 另檢查寫入鎖與明確的 running/writing/acquiring 狀態。偵測到變動、寫入鎖或量測中標記時，直接拒絕來源複製與輸出發佈，提示停止量測後重試。複製後及發佈前再檢查一次；來源永遠只讀取。
- `complete=False` 本身不代表仍有程序正在寫入，已中止的部分量測仍可轉換。若量測程式既不提供執行狀態、不遵守檔案鎖，又會長時間停止寫入，單靠檔案觀察無法完全辨認其生命週期；這類來源應先在量測端停止後再轉換。
- 每筆保存進度至母資料夾的 `.labber-converter-progress.json`。要接續批次，加入相同輸入、選擇相同母資料夾，勾選 **續轉：驗證並略過已完成項目**。程式核對來源識別、來源與輸出 SHA-256、資料夾及專案設定；一致才略過轉換。輸出遺失、內容或設定改變時重新處理，遇到同名仍會詢問三選一。續轉預設關閉，方便刻意重新匯入。
- 進度在正式檔發佈前先記錄，能辨識「已發佈但來不及寫完成紀錄」的中斷。來自同一台電腦且確認程序已結束的工具鎖可自動回收；無法驗證或其他電腦的鎖不會自動刪除。取消可中止來源暫存與後續項目；正式輸出發佈開始後會先完成該筆。作業系統仍在等待單次網路 I/O 時，重試次數限制不會縮短系統本身的逾時時間。

## 多人修改資料庫

更新 Database 前核對檔案識別、時間、大小與 SHA-256；發現變動或其他寫入鎖時，重新讀取並合併，最多嘗試三輪。保留其他人的分類與額外節點，更新前備份，發佈後再次檢查分類。持續衝突時保留已驗證的量測輸出並標記 **分類待同步**；之後勾選續轉，只補做分類同步，不重轉或加編號。

這是對既有原廠檔案的變動檢查；工具鎖只約束本工具，原廠 Labber 不一定配合。最後一次檢查與檔案取代之間仍有極短競爭窗口，不能宣稱與任意外部寫入者完全互斥。

本版所有新增的逾時、寫入鎖、傳輸損壞、續轉和多人改寫測試均使用本機沙盒合成資料，沒有連線、讀寫或轉換真正 NAS 上的量測資料。

## 同名檔案：取代／不要轉換／同時保留

批次轉換遇到同一輸出位置已有相同檔名時，會逐筆跳出視窗，列出來源、既有輸出位置，以及同時保留時預計使用的新檔名。

- **取代**：新檔完成轉換與數值驗證、來源成功關閉後，才更新指定輸出檔。一般分類更新失敗時嘗試還原原檔；持續多人改寫則保留驗證完成的新輸出並標記待同步。還原失敗時保留暫存備份並在錯誤中指出位置。
- **不要轉換**：保留現有檔案，跳過這筆，繼續後面的資料。關閉彈窗或按 Escape 也會跳過此筆。
- **同時保留**：保留現有檔案，以「原始檔名＋底線編號」另存新檔；掃描同資料夾的匹配編號，取最大值加 1。例如已有 `foo.hdf5`、`foo_1.hdf5`、`foo_3.hdf5`，新檔使用 `foo_4.hdf5`。其他名稱如 `foo_other_99.hdf5` 不參與計算。

原始量測檔名中的數字保留，例如來源叫 `CPAEP_1.nc`，重複輸出會命名為 `CPAEP_1_1.hdf5`，再重複時為 `CPAEP_1_2.hdf5`。另存時也更新輸出的 `log_name`，讓 Labber 內可辨識新名稱。

完成、跳過與失敗會分別計數並寫入報告。命令列沒有彈窗，預設同時保留，也可指定 `--on-conflict skip` 或 `--on-conflict replace`。

## 這批 Data_1020 的結果

| 來源 | 實際已取得的掃描點 | 每個點的頻率數 | 電流單位 |
|---|---:|---:|---|
| 1020 X2 CPAEP_1.nc | 367 / 規劃 761 | 5001 | mA |
| 1020 X2 CPAEP_2.nc | 372 / 規劃 761 | 5001 | mA |

兩份來源都標示尚未完成。工具只去除尾端未量測配置，保留量測中的缺值、所有已取得資料、複數實部/虛部、時間戳與完成狀態。`retained` 是手動保留旗標，並非刪除其他量測點的條件；它會另存而不拿來篩選資料。完整規劃電流軸、原始設定和儀器 snapshot 另存於 `/Conversion`。

輸出例：

```text
輸出根目錄/
├── Database.hdf5
├── 2026/
│   └── 10/
│       └── Data_1020/
│           ├── 1020 X2 CPAEP_1.hdf5
│           └── 1020 X2 CPAEP_2.hdf5
└── conversion_report_日期時間.json
```

不要只更改 `.nc` 的副檔名。NC 本身雖使用 HDF5 容器，Labber 仍要求 Channels、Step list、Data、Traces 等內部結構。

## 原有資料庫及新日期

參考資料 `Data/PRL RSMEP best data` 的主資料夾同時放置 `Database.hdf5` 與 `2026`；量測檔位於 `2026/08/Data_0828`、`Data_0831` 等資料夾。檔內 `/Tags` 的 Project 分別為 `08/0828`、`08/0831`，Database 的 `/Labels` 則登記 `Project/08/0828`、`Project/08/0831`。本版照這個對應處理新檔。

輸入已有 `年/月/資料子資料夾` 時會保留該結構，不會重複堆疊 `2026/10`。輸入只有 `Data_1021` 時會補上年與月。一般來源 `X/subfolder/a.nc` 會放到 `年/月/Data_MMDD/X/subfolder/a.hdf5`，Project 同步為 `月/MMDD/X/subfolder`。範本也有直接位於月份下的檔案；這類原生路徑會保留。新建資料庫與匯入既有資料庫遵守相同規則。

0.4.1 會辨識「月份 / Data_MMDD」本身已是日期結構，例如選取 `10` 資料夾並包含 `Data_1013`，輸出為 `2026/10/Data_1013/a.hdf5`，分類為 `10/1013`，不再把 `10` 當額外來源母資料夾。若日期資料夾下仍有其他自訂子資料夾，結果會顯示原廠掃描位置警告；保留實體自訂層級不等同原廠 Log Browser 會遞迴掃描。原廠相容的標準路徑應讓量測檔直接位於 `年/月/Data_MMDD`，不能只靠 Database 中有分類就認定檔案會被掃描。

`Database.hdf5/Labels` 是分類名稱清單，沒有逐檔白名單。新日期的資料夾與標籤會加入同一份主 Database，原有分類及額外節點保留。來源只讀取；既有原生 HDF5 的輸出副本可能調整分類與新增來源紀錄，量測數值不變。重新載入後仍不出現，應檢查是否開到正確的母資料夾，以及分類、日期篩選和檔案格式。

「同步既有資料庫分類」會掃描輸出根目錄的 Labber 檔案，將 `/Tags` 的 Project、User、Tags 合併到 `/Labels`。原分類和額外節點保留（選「重建」時改成只留現有檔案的分類）；變更前在 `.labber-converter-backups/` 建立 `Database.hdf5.時間.bak`（只留最新 5 份）。它不轉換 NC，也不修改量測檔。資料庫損壞時直接報錯，不以空資料庫覆蓋。

## 支援範圍與驗證

- 本版支援已提供的 `schema=labcontrol-nc/1`、`layout=grid`，一個掃描軸搭配均勻取樣的曲線軸，可有多個實數或複數曲線通道。
- 未支援的標量訊號、額外維度或非均勻曲線軸會明確報錯，不偷偷忽略、插值或改變資料。
- 輸入只讀取；ZIP 成員不解壓到來源或輸出路徑；同名輸出依彈窗選擇取代、跳過或使用下一個底線編號。
- 每個 NC 輸出都會重新開啟，逐點比對掃描座標、複數實部/虛部、時間戳與保留旗標。HDF5 使用舊版相容的存檔方式、原始 compound/enum 定義與 gzip 壓縮。
- 這兩份實際資料已用本工作區獨立的 LabLogViewer 讀取器解析，得到 `(5001, 367)` 與 `(5001, 372)`，頻率 Hz、電流 mA。
- **這台電腦沒有 Labber 原廠程式，尚未在原廠 Log Browser 實測。** 格式和數值驗證不等同原廠 UI 驗證；請用上述輸出資料庫在目標 Labber 版本做最後確認。輸出報告也記錄 `native_labber_ui_verified=false`。

官方手冊：[Keysight Labber User Manual](https://www.keysight.com/us/en/assets/9018-18184/user-manuals/9018-18184.pdf)，§8.2.4、附錄 A3（自訂 data log / Python API）。這類輸出用於 Log Browser / Log Viewer，不是可重新執行的硬體量測配置。

## Command line / English quick start

```bash
python converter.py convert input.zip file.nc folder -o output_database
python converter.py convert input.zip -o existing_database --project "My project"
python converter.py convert input.zip -o output_database --resume
python converter.py index existing_database
python -m pytest tests -q
```

Launch the app, select one or more files/folders/ZIPs, choose an output database root, and press **Convert data**. **Create a new database…** initializes `Database.hdf5` in a chosen root before importing any data. The root contains both Database and the year/month/data folders. Open this root in Labber; child folders are not standalone databases. Source folder paths and corresponding Project labels follow the supplied native reference, for example `2026/10/Data_1021/a.hdf5` and `Project/10/1021`.

A filename conflict opens a **Replace / Skip this file / Keep both** dialog. Keep both uses the highest existing matching suffix plus 1 and updates the new log name. Replacement is published after conversion and validation; source runs retain their original acquisition status. Use **Reload Database** after importing. The target Labber UI still needs a native compatibility check; all exported NC numerical values are verified automatically.

Local caching is on by default. Source stability, explicit active flags and HDF5 writer locks are checked before copying; changing sources are refused. Conversion is local, transferred files are checksum-verified before and after publication, and recoverable I/O operations get up to three attempts. Turn on **Resume verified completed files** to match the progress journal, source/output hashes and conversion options before skipping completed work. Database changes are re-read and merged; persistent contention retains the verified output with labels pending for a later resume. These checks cannot provide atomic exclusion against an external writer that ignores this tool's lock.

The UI and exact light/dark palettes match the existing LabLogViewer design. The liquid-glass optical model includes Snell refraction, chromatic dispersion, Fresnel highlights and adjustable frost. See `THIRD_PARTY.txt` for the PyGlass MIT notice.
