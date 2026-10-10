#!/bin/sh
# 沒有 Docker 時：在常開的 Linux / macOS / NAS（有 Python 3.8+）上執行 Lab Control Hub
cd "$(dirname "$0")"
exec python3 -m labhub --port 8765 --data "$PWD/data" ${LABHUB_RELEASES:+--releases "$LABHUB_RELEASES"}
