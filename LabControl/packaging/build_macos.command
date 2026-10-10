#!/bin/bash
# Lab Control：在 macOS 上打包成 .dmg（在 Finder 雙擊；第一次可能要右鍵 →「打開」）
# 需要 Python 3.12：https://www.python.org/downloads/（建議用官網安裝檔）
set -e
cd "$(dirname "$0")/.."
PY=""
for c in python3.12 /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; assert sys.version_info >= (3, 10)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "找不到 Python 3.10 以上：請先到 https://www.python.org/downloads/ 安裝"; read -r -p "按 Enter 關閉"; exit 1
fi
echo "使用 Python：$("$PY" -c 'import sys,platform;print(sys.version.split()[0], platform.machine())')"
export PYTHONUTF8=1
[ -d .build-venv ] || "$PY" -m venv .build-venv
source .build-venv/bin/activate
echo "安裝套件（numpy、PyQt6、xarray、PyInstaller…約 400 MB，第一次要幾分鐘）…"
python -m pip install --disable-pip-version-check --upgrade pip
python -m pip install --disable-pip-version-check -r requirements.txt pyinstaller pillow pyvisa-py
python packaging/build_app.py
open dist
echo
echo "完成：把 dist 裡的 LabControl_v*_macos-*.dmg 拖進平台網頁的「控制台」→「全部發佈」。"
read -r -p "按 Enter 關閉"
