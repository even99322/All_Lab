# .nc 與 Labber .hdf5 互轉
> 量測程式（Lab Control）的 .nc 轉成 Labber 讀得到的 .hdf5，或把 Labber 的 .hdf5 轉成 .nc。可以一次轉很多個。

## 最短流程
1. **選檔案。** 瀏覽器裡選一個或多個數據（按住 Ctrl／⌘ 或 Shift 多選），按右鍵 → **轉成 Labber .hdf5⋯** 或 **轉成量測檔 .nc⋯**。也可以從 **檔案 → 轉檔** 選；Viewer 的 **檔案 → 轉檔** 會先帶入目前開著的檔案。
2. **選存到哪裡。** 預設存在原檔所在的資料夾，或指定一個資料夾。檔名跟原檔一樣、只換副檔名；已經有同名檔案時自動加 _2、_3⋯，**不會覆蓋任何檔案**。
3. **按「轉檔」。** 下方會逐一列出結果：✔ 轉好的檔案位置、✘ 失敗原因、⚠ 需要注意的地方。轉好的檔案如果在目前的資料庫裡，清單會自動更新。 ![](convert_dialog.png)

## 轉成 Labber .hdf5
- **寫檔方式**：
  - **自動**（預設）：電腦上找得到 Labber 的 Python 環境就用 **Labber API**（跟量測程式匯出 Labber 檔同一個方法），找不到就用 **內建**。
  - **Labber API**：一定用 Labber 自己的程式寫檔。要指定 **Labber Python**（預設 `C:\LocalEnvs\Labber環境_venv\Scripts\python.exe`，跟量測程式的設定相同）。
  - **內建**：不需要 Labber，照 Labber API 做出來的檔案結構寫（Channels、Step list、Log list、Data、Traces、Tags、Instrument config）。
- **掃描軸的順序**：Labber 的 Step list 第一個是最內圈（跑得最快）。轉檔時會把 .nc 的內圈軸排在前面，2D／3D 掃描在 Labber 裡每一格的位置都正確。
- **VNA 等曲線**（例如 S21 對 Frequency）存成 Labber 的 trace（複數就是複數）；頻率軸用起點＋間距記錄。頻率間距不固定時會出現 ⚠：Labber 只能顯示等間距的軸，讀檔程式仍會讀到正確的頻率。
- 儀器設定（量測時的快照）寫到 **Instrument config**。

## 轉成 .nc
- 產生量測程式的 NetCDF 格式（labcontrol-nc/1）：每個掃描軸一個座標、曲線的頻率軸自己一個座標，複數保持複數。量測程式、讀檔程式、Python 的 `xarray.open_dataset(檔案, engine="h5netcdf")` 都打得開。
- Labber 的儀器設定與固定的 step channel 放在檔案的 `labcontrol` 屬性（snapshot）裡，讀檔程式的中繼資料看得到。
- 沒跑完的 1D 量測只轉有量到的點（會出現 ⚠）；沒跑完的多維量測無法判斷每一點的位置，會顯示失敗原因。

## 做不出來時
- **找不到 Labber Python**：選了「Labber API」但路徑不對、或那個 Python 不能 `import Labber`。改成「自動」或「內建」，或在 **Labber Python** 選正確的 python.exe。
- **打不開**：檔案不是讀檔程式認得的格式，或還在寫入中。
