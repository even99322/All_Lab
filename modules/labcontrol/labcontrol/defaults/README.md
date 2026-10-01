# Lab Control 設定資料夾（LAB）

這個資料夾由 Lab Control 第一次啟動時建立。**所有設定都在這裡**，程式升級不會覆寫你改過的檔案。

| 檔案 / 資料夾 | 內容 |
|---|---|
| `settings.yaml` | App 設定（存檔位置、Labber、預設值、單位、規則、估時…）。沒寫的項目自動使用預設值。 |
| `instruments.yaml` | 儀器清單（位址、型號、安全限制、參數覆寫、電磁鐵組）。 |
| `templates/` | 範本方案（編輯器「範本」選單列出這裡的 `*.scheme.yaml`）。 |
| `schemes/` | 你存的量測方案。 |
| `experiments/` | YAML 實驗設定（命令列 `python -m labcontrol run` 用）。 |
| `plugins/` | 自訂 driver / hook（丟 `.py` 重開即載入；底線開頭的檔案是範本，不會載入）。 |
| `logs/` | 錯誤記錄。 |

- 想恢復某個預設檔：把它刪掉（或改名），重新開啟 Lab Control 就會補回預設版本。
- 改 `settings.yaml` 後可在編輯器「設定 ▾ → 重新載入設定」；改 `instruments.yaml` 需重新開啟程式。
- 新增儀器的寫法：程式資料夾內 `docs/DRIVER_GUIDE.md`，或參考 `plugins/_driver_template.py`。
