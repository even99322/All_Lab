#!/bin/bash
# 在一般電腦上執行（不用 Docker）。第一次會建立虛擬環境並安裝套件。
cd "$(dirname "$0")"
DATA="${PAPERLIB_DATA:-$PWD/data}"
if [ ! -d .venv ]; then python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; fi
echo "資料夾：$DATA    網址：http://$(hostname -I 2>/dev/null | awk '{print $1}'):8080"
PAPERLIB_DATA="$DATA" .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
