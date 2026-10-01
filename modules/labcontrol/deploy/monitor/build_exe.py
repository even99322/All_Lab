"""產生 LabControlMonitor.exe（build_exe.bat 會呼叫這個檔；也可以直接 python deploy\\monitor\\build_exe.py）。

所有輸出同時寫到 deploy\\monitor\\build_log.txt，失敗時把這個檔傳給開發者。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = Path(__file__).resolve().with_name("build_log.txt")


def run(cmd, log):
    log.write("\n$ " + " ".join(cmd) + "\n")
    log.flush()
    print("\n>>> " + " ".join(cmd), flush=True)
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    for line in p.stdout:
        print(line, end="", flush=True)
        log.write(line)
    p.wait()
    log.flush()
    return p.returncode


def main() -> int:
    with open(LOG, "w", encoding="utf-8") as log:
        log.write(f"Python {sys.version}\n{sys.executable}\n{ROOT}\n")
        print(f"Python {sys.version.split()[0]}  ({sys.executable})")
        if sys.version_info < (3, 9):
            print("需要 Python 3.9 以上")
            return 1
        if run([sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller", "PyQt6", "pyyaml"], log):
            print("\n[錯誤] 安裝 PyInstaller / PyQt6 失敗（網路？權限？）。詳細訊息：" + str(LOG))
            return 1
        sep = os.pathsep                      # Windows ';'，其他 ':'
        icon = ROOT / "assets" / "icon.ico"
        cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
               "--name", "LabControlMonitor",
               "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
               "--add-data", f"{icon}{sep}.",
               "--add-data", f"{ROOT / 'labhub'}{sep}labhub_src",
               "--exclude-module", "numpy", "--exclude-module", "scipy", "--exclude-module", "matplotlib",
               "--exclude-module", "pyqtgraph", "--exclude-module", "labcontrol",
               str(ROOT / "monitor_main.py")]
        if icon.exists():
            cmd[cmd.index("--name"):cmd.index("--name")] = ["--icon", str(icon)]
        if run(cmd, log):
            print("\n[錯誤] PyInstaller 失敗。請把這個檔傳給開發者：" + str(LOG))
            return 1
        exe = ROOT / "dist" / ("LabControlMonitor.exe" if os.name == "nt" else "LabControlMonitor")
        if not exe.exists():
            print("\n[錯誤] 沒有產生 exe。請把這個檔傳給開發者：" + str(LOG))
            return 1
        msg = f"\n完成：{exe}（{exe.stat().st_size / 1e6:.0f} MB）"
        print(msg)
        log.write(msg + "\n")
        if os.name == "nt":
            subprocess.run(["explorer", "/select,", str(exe)])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(f"\n[錯誤] {type(e).__name__}: {e}\n詳細訊息：{LOG}")
        sys.exit(1)
