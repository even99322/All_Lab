HFSS 自動化工具 v3.2 (AEDT 2026 R1)
====================================

■ 安裝 / 升級 (從 2025 R2 版升上來)
1. 升級 PyAEDT (AEDT 2026 R1 需要 PyAEDT >= 1.5.0)：
       C:\LocalEnvs\HFSS_venv\Scripts\python.exe -m pip install -U -r requirements.txt
   註：PyAEDT 1.x 起套件名稱回到 "pyaedt"，若舊環境裝的是 ansys-aedt-core，建議先 pip uninstall ansys-aedt-core 再安裝。
2. 執行 run_HFSS自動化工具.py (或 python main.py)。
   若 venv 不在 C:\LocalEnvs\HFSS_venv，可設定環境變數 HFSS_VENV_PYTHON 指向 python.exe。
3. 主畫面「AEDT 版本」預設 2026.1；仍可切回 2025.2。未來升級只要輸入新版本號。
4. 設定檔改存在 %USERPROFILE%\LAB\HFSS\hfss_settings.json (實驗室統一位置)。
   第一次啟動時，若程式資料夾旁有舊的 hfss_settings.json 會自動複製過去。

■ 專案資料夾結構 (v3.1 起)
  本地: C:\Temp\HFSS_Local\<專案名稱>\
          <專案名稱>.aedt / .aedtresults / S_para.csv / S_para.sNp / GIF\
  NAS : <NAS 路徑>\<專案名稱>\  (整個資料夾鏡像)
  開啟專案或一鍵流程時，若已有同名專案 (本地、NAS、或舊版位置) 會詢問：
    是 → 開啟已有檔案 (NAS / 舊位置的檔案會先複製到新的本地資料夾)
    否 → 輸入新的專案名稱
  專案名稱只能用英文、數字、底線、減號。

■ 建議流程
  「⚡ 一鍵全流程」：在同一次 AEDT 連線中依序 建模 → S參數 → 場型 (→ GIF → 關閉備份)。
  單獨按鈕仍保留，可只重跑其中一步。

■ 場型 GIF
  已取代舊的 export_gif.py (螢幕截圖 + 滑鼠 RPA)。2026 R1 是新版 GUI，截圖比對一定會失效。
  新做法全程走 AEDT API 匯出每個相位的影格，再用 Pillow 組成 GIF，並固定色階避免閃爍。
  AEDT 必須以 GUI 模式執行 (本工具預設即是)。

■ DXF 繪圖命名規範與模式說明 (同舊版)
DXF 繪圖命名規範
為了讓腳本能正確判斷並拉伸 3D 模型，你在 AutoCAD 或其他繪圖軟體輸出 DXF 時，必須嚴格遵守以下圖層名稱 (大小寫敏感)：

模式 1：傳統微帶線 (MS)
b (Board)：板材圖層 (將用於生成底層地平面與介電層)

g (Guide)：微波線圖層 (頂層訊號走線)

h (Hole)：螺絲孔圖層 (將會貫穿並挖空整個板子)

模式 2：共面波導 (CPW)
db (Down Board)：下板材圖層 (將用於生成底層地平面與介電層)

ub (Up Board)：上板材圖層 (頂層地平面)

g (Guide)：微波線圖層 (頂層訊號走線)

h (Hole)：螺絲孔圖層 (貫穿挖空)

v (Via)：金屬化過孔圖層 (將自動填入紫色的銅材，並貫穿導通上下地平面)

註 : 
dxf檔需使用 2013年版本儲存



1. Adaptive 模式 (網格自適應優化策略)
決定 HFSS 要根據「哪些頻率」來細化 3D 網格 (Mesh)：

🎯 Single (單頻優化) - 【最推薦、速度最快】

設定建議：填入掃頻範圍的「最高頻率」(如 9GHz)。

適用時機：一般傳輸線 (CPW/MS)、無強烈共振的被動元件。確保最高頻率(最短波長)的網格夠密，低頻自然精準。

📊 Multi-Frequencies (多頻點優化)

設定建議：填入頻帶的頭、中、尾 (如 2.5GHz, 5.5GHz, 9GHz)，或已知的共振點。

適用時機：雙頻/多頻天線、多工器、或當結構有局部強烈共振，擔心 Single 模式的網格會忽略低頻細節時使用。

🌌 Broadband (寬頻優化) - 【極度耗時，請謹慎使用】

設定建議：由 Sweep 範圍自動決定，軟體會在全頻段內自動尋找最難收斂的點。

適用時機：超寬頻 (UWB) 天線、高速數位電路訊號完整性 (SI) 分析、特性未知的複雜濾波器。

⚠️ 警告：若 Wave Port 尺寸過大激發高階模態，此模式極易陷入無限切網格的死迴圈。

2. Sweep 模式 (頻率響應掃描策略)
決定 HFSS 如何產出 2GHz ~ 9GHz 之間的連續 S 參數曲線：

🚀 Interpolating (插值掃頻) - 【最推薦、速度最快】

特點：利用數學多項式推導全頻段響應，只需解少數幾個頻率點。

適用時機：S 參數曲線平滑的結構 (如傳輸線、一般天線)。注意：可能會錯過極度狹窄的深谷 (High-Q Notch)。

⚡ Fast (快速掃頻)

特點：利用極點與零點 (Poles/Zeros) 模型進行推導。

適用時機：高 Q 值的共振腔或窄頻濾波器，能比 Interpolating 更準確地捕捉尖銳的頻率響應，且速度比 Discrete 快。

🧱 Discrete (離散掃頻) - 【絕對精準，但最耗時】

特點：針對你設定的「每一個頻率點」都暴力重新解一次 3D 矩陣。

適用時機：驗證強烈共振點的絕對精準度，或需要觀察「每一個頻率點」的 3D 電磁場分佈動畫時才使用。

====================================
v3.2 變更紀錄
====================================
【YIG 磁性球】(「YIG 磁性球」分頁)
- 建模時在訊號線上方建立 YIG 球 (位置：訊號線中央 + 偏移，或絕對座標；高度 = 走線頂面 + 間隙 + 半徑)
- 依 AEDT 2026.1 錄製腳本的語法建立 ferrite 材料 YIG_auto (4πMs、ΔH、ΔH 頻率、εr、tanδ)
- AssignMagneticBias 均勻偏置；外加磁場 (mT) 自動扣除球體去磁場 Ms/3，換算成 HFSS 的內部場 Bias (A/m)
- 球體網格：邊長上限 + 曲面元素
- Kittel 頻率自動加入 Adaptive 頻點；共振附近另外細掃，輸出 S_para_kittel.csv / .sNp
- 介面即時顯示內部場、Kittel 頻率、線寬、細掃間距 (間距大於線寬時警告)

====================================
v3.1 變更紀錄
====================================
【介面不再卡住】
- HFSS 任務改在獨立程序 (multiprocessing) 執行。舊版用 QThread，但 PyAEDT 求解期間會佔住 Python GIL，
  介面執行緒拿不到執行權而整個卡死；現在介面只透過 Queue 收日誌，求解中也能操作、捲動、看說明。
- 新增執行狀態列：目前步驟、忙碌指示、經過時間。
- 新增「🛑 中止」：先要求 AEDT 停止模擬，15 秒後強制結束工作程序。
- 關閉程式時的 NAS 同步改在背景執行，不會讓關閉動作卡住。

【設定檔與專案資料夾】
- 設定檔：%USERPROFILE%\LAB\HFSS\hfss_settings.json (寫入時先寫暫存檔再取代，避免損壞)。
- 每個專案一個資料夾，CSV / Touchstone / GIF 都放在專案資料夾內；NAS 鏡像整個資料夾。
- 同名專案檢查：詢問開啟已有檔案或改名；名稱不合法時要求重新命名。
- 「📂 開啟資料夾」按鈕；狀態列顯示目前專案。

【進階設定分頁 (新)】
- Adaptive：最少 Pass 數、基底函數階數、Lambda 細化與 Target、Direct / Iterative 求解器
- Sweep：插值誤差容許、插值最大求解數、強制被動性 / 因果性、掃頻存場
- Wave Port：模態數、參考阻抗、重正規化、De-embed、特性阻抗定義 (Zpi/Zpv/Zvi)
- 邊界：Radiation / PML、無限大地平面
- 材料：εr / tanδ 覆寫 (自動複製材料為 <名稱>_custom)
- GIF：幀數、視角、每幀時間、解析度
- 所有欄位都有滑鼠提示說明；可一鍵恢復預設值。

【模式說明視窗 (❓ 模式說明)】
- ① 整體流程圖  ② Adaptive 動畫 (網格細化、ΔS 收斂、Single/Multi/Broadband 頻點位置)
  ③ Sweep 動畫 (Discrete / Interpolating / Fast 的取樣方式與漏峰風險)  ④ 情境建議  ⑤ 參數速查
- 非強制視窗，可以邊看邊操作主畫面。
- 自動跟隨系統淺色 / 深色模式 (Windows 深色模式下文字、表格、動畫配色皆可清楚辨識)，切換主題時即時更新。

====================================
v3.0 變更紀錄
====================================
【2026 R1 / PyAEDT 1.x 相容性 (不改會直接出錯)】
- SolutionData.data_real() 已在 PyAEDT 1.0 移除 → 改用 get_expression_data()
- close_project(save_project=True) 參數已改名 → close_project(save=True)
- AEDT 版本改為可設定 (預設 2026.1)，並在啟動時檢查 PyAEDT 版本
- 取得 CPU 核心數改用 psutil：Windows 11 24H2 已移除 wmic，舊版會誤判成 4 核、安全上限只剩 2 核

【可能長期沒生效的設定 (修正後結果可能與舊版略有不同)】
- Setup 原本用 'OrderOfBasis'、'MaxRefinePerPass'、'Broadband'、'MultiFreqs' 等 key，
  與 AEDT 實際的 'BasisOrder'、'PercentRefinement'、'BroadBand'、'MultipleAdaptiveFreqsSetup' 不符。
  現改用 PyAEDT 官方方法設定，Mixed Order / 網格細化% / Broadband / 多頻點 都會真正套用。

【流程優化】
- 場型模擬沿用 S 參數 Setup 的收斂網格，只加算單點掃頻 → 不必再從頭跑一次 Adaptive
- 多個場型頻率合併為一個單點掃頻 (Sweep_Fields)
- S 參數掃頻建立時就關閉 SaveFields (舊版先存場再關，Discrete 掃頻時浪費大量硬碟)
- S 參數一次查詢取實部+虛部；並額外輸出 Touchstone (.sNp)
- Wave Port 偵測：頂點座標只向 AEDT 查一次 (舊版在每個面×每條線重複查詢)
- MS 建模：每個 DXF 圖層只匯入一次 (舊版 b、h 匯入兩次，其中一份直接刪掉)
- 一鍵全流程：多步驟共用同一次 AEDT 連線，只在結束時備份一次
- CLOSE：先關閉專案解除鎖定再備份 NAS，備份內容一致；robocopy 錯誤碼會回報

【穩定性】
- 建模前檢查 DXF 圖層與材料是否存在，缺少時明確報錯
- 重新建模會先清除舊模型與 Port，不再疊出重複物件
- 介電常數改從材料庫讀取 (舊版為寫死的對照表)
- 頻率欄位可輸入 9GHz / 9 GHz / 9000MHz；欄位格式錯誤會跳提示而不是整個程式閃退
- analyze_setup 失敗時會中止後續步驟並提示
- 啟動器改為執行 main.py (舊版指向過時的單檔版)
- plot.ipynb：修正語法錯誤與 dB→線性 轉換 (np.exp → 10**)

【移除】
- HFSS自動化工具.py (舊的單檔版，功能已被 main.py + ui_main.py + hfss_worker.py 取代)
- export_gif.py / run_test.py (RPA 版 GIF 匯出，已整合進主程式)
