import subprocess
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
target_script = os.path.join(current_dir, "main.py")
venv_python = r"C:\LocalEnvs\fitting_venv\Scripts\python.exe"

print("🚀 正在開啟獨立 CMD 視窗執行程式...")

cmd_args = ["cmd.exe", "/k", venv_python, target_script] + sys.argv[1:]

try:
    subprocess.Popen(cmd_args, creationflags=subprocess.CREATE_NEW_CONSOLE)
except Exception as e:
    print(f"\n❌ 啟動獨立視窗時發生錯誤: {e}")
    input("\n請按 Enter 鍵退出...")
