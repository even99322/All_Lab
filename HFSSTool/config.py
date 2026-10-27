import os
import sys
import json
import shutil

# =================================================================
# 🌟 路徑設定
# =================================================================
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 程式附帶的檔案 (icon.ico)：打包成單一 exe 時解壓在 sys._MEIPASS
RESOURCE_DIR = getattr(sys, "_MEIPASS", SCRIPT_DIR)

# 實驗室統一設定資料夾：%USERPROFILE%\LAB\HFSS
SETTINGS_DIR = os.path.join(os.path.expanduser("~"), "LAB", "HFSS")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "hfss_settings.json")
LEGACY_SETTINGS_FILE = os.path.join(SCRIPT_DIR, "hfss_settings.json")   # v3.0 以前放在程式旁邊

DEFAULT_LOCAL_ROOT = r"C:\Temp\HFSS_Local"

APP_VERSION = "v3.9.5"

# AEDT 版本：之後升級 (例如 2026.2 / 2027.1) 只要在 UI 下拉選單改，或改這裡的預設值即可
DEFAULT_AEDT_VERSION = "2026.1"
SUPPORTED_AEDT_VERSIONS = ["2026.1", "2025.2"]

# PyAEDT 1.5.0 起才完整支援 AEDT 2026 R1
MIN_PYAEDT_FOR_2026R1 = (1, 5, 0)

# 固定的 Setup / Sweep 名稱 (場型任務會重用 S 參數的 Setup 網格)
S_SETUP_NAME = "Setup_Broadband"
S_SWEEP_NAME = "Sweep_Broadband"
FIELD_SWEEP_NAME = "Sweep_Fields"
FIELD_SETUP_NAME = "Setup_For_Fields"   # 僅在「不沿用 S 參數網格」時使用


class ConfigManager:
    @staticmethod
    def _migrate_legacy():
        """第一次使用 v3.1 時，把舊版放在程式資料夾的設定檔複製到 LAB\\HFSS。"""
        if not os.path.exists(SETTINGS_FILE) and os.path.exists(LEGACY_SETTINGS_FILE):
            try:
                os.makedirs(SETTINGS_DIR, exist_ok=True)
                shutil.copy2(LEGACY_SETTINGS_FILE, SETTINGS_FILE)
            except Exception as e:
                print(f"遷移舊設定檔失敗: {e}")

    @staticmethod
    def load_settings():
        """讀取 JSON 設定檔"""
        ConfigManager._migrate_legacy()
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"讀取設定檔失敗: {e}")
        return {}

    @staticmethod
    def save_settings(settings_dict):
        """儲存 JSON 設定檔"""
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            tmp = SETTINGS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(settings_dict, f, indent=4, ensure_ascii=False)
            os.replace(tmp, SETTINGS_FILE)      # 先寫暫存檔再取代，避免寫到一半當機把設定檔弄壞
        except Exception as e:
            print(f"儲存設定檔失敗: {e}")
