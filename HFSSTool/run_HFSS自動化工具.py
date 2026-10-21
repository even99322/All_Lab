"""在獨立 CMD 視窗啟動 HFSS 自動化工具。

Python 直譯器的尋找順序：
  1. 環境變數 HFSS_VENV_PYTHON
  2. C:\\LocalEnvs\\HFSS_venv\\Scripts\\python.exe (舊版預設)
  3. 執行本啟動器的 Python
"""
import subprocess
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
target_script = os.path.join(current_dir, "main.py")   # 舊版指向已過時的單檔版 HFSS自動化工具.py

candidates = [os.environ.get("HFSS_VENV_PYTHON"), r"C:\LocalEnvs\HFSS_venv\Scripts\python.exe", sys.executable]
venv_python = next(p for p in candidates if p and os.path.exists(p))

print(f"🚀 正在開啟獨立 CMD 視窗執行程式... ({venv_python})")
cmd_args = ["cmd.exe", "/k", venv_python, target_script] + sys.argv[1:]

try:
    subprocess.Popen(cmd_args, cwd=current_dir, creationflags=subprocess.CREATE_NEW_CONSOLE)
except Exception as e:
    print(f"\n❌ 啟動獨立視窗時發生錯誤: {e}")
    input("\n請按 Enter 鍵退出...")
