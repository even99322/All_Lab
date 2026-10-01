#!/bin/sh
# Linux / macOS 版（開發測試用；Windows 請用 build_exe.bat）
cd "$(dirname "$0")/../.." || exit 1
python3 -m pip install --upgrade pyinstaller PyQt6 pyyaml
python3 -m PyInstaller --noconfirm --clean --onefile --windowed --name LabControlMonitor \
  --add-data "assets/icon.ico:." --add-data "labhub:labhub_src" \
  --exclude-module numpy --exclude-module scipy --exclude-module matplotlib --exclude-module pyqtgraph \
  --exclude-module labcontrol monitor_main.py
