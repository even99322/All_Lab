#!/bin/bash
# LAB-QEL論文庫 控制台（Mac）：第一次執行會建立 Python 環境並安裝 paramiko
cd "$(dirname "$0")" || exit 1
ENV="$HOME/.config/paperlib-console/venv"
if [ ! -x "$ENV/bin/python" ]; then
  command -v python3 >/dev/null || { echo "需要 Python 3.10 以上：https://www.python.org/downloads/"; read -r -p "按 Enter 關閉"; exit 1; }
  echo "第一次執行：準備控制台（約 1 分鐘）…"
  python3 -m venv "$ENV" || { read -r -p "建立環境失敗，按 Enter 關閉"; exit 1; }
fi
"$ENV/bin/python" -c "import tkinter" 2>/dev/null || { echo "這個 Python 沒有 tkinter，請改用 python.org 的安裝檔安裝 Python 後再試（並刪除 $ENV）"; read -r -p "按 Enter 關閉"; exit 1; }
"$ENV/bin/python" -c "import paramiko" 2>/dev/null || "$ENV/bin/python" -m pip install -q --disable-pip-version-check paramiko
exec "$ENV/bin/python" tools/console/paperlib_console.py
