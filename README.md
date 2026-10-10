# All_Lab

實驗室程式的打包用 repo（GitHub Actions 在雲端的 Windows / Mac 打包）。

| 資料夾 | 程式 | 打包 | 產生 |
|---|---|---|---|
| `LabControl/` | Lab Control（量測） | 推 tag `labcontrol-v<版本>` 或 Actions → Build Lab Control → Run workflow | `LabControl_v<版本>_windows.exe`、`LabControl_v<版本>_macos-arm64.dmg`、`..._macos-x86_64.dmg` |

產生的檔案在該次執行的 **Artifacts**（下載後解壓），拖進平台網頁「控制台」→「全部發佈」。

注意：這個 repo 是公開的，**不要放 token、密碼**。Hub token 在各電腦的「設定 ▾ → Hub 連線設定…」或 `LAB/settings.yaml` 的 `remote.token` 填。
