#!/bin/bash
# QEL Lab 大程式（macOS）：雙擊執行。第一次會建立 Python 環境（需要 Python 3.12，python.org 或 Homebrew）。
cd "$(dirname "$0")"
VENV="$HOME/QELLab/envs/_launcher"
if [ ! -x "$VENV/bin/python" ]; then
  echo "第一次執行：建立 Python 環境…"
  PY=$(command -v python3.12 || command -v python3)
  "$PY" -m venv "$VENV" && "$VENV/bin/python" -m pip install --disable-pip-version-check -r requirements.txt
fi
exec "$VENV/bin/python" main.py
