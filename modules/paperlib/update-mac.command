#!/bin/bash
# LAB-QEL論文庫：從這台 Mac 一鍵更新 NAS 上的網站（在 Finder 雙擊執行）
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
CFG="$HOME/.paperlib-update"
fail() { printf '\n\033[1;31m✗ %s\033[0m\n' "$1"; read -r -p "按 Enter 關閉"; exit 1; }
echo "LAB-QEL論文庫 一鍵更新"
[ -f "$ROOT/app/main.py" ] || fail "請把這個檔案放在解壓縮後的 paperlib 資料夾裡執行。"
VER="$(grep -m1 -o 'VERSION = "[^"]*"' app/config.py | cut -d'"' -f2)"
echo "  要上傳的版本：$VER"
HOST=""; PORT=22; USER_=""; DPATH=/volume1/docker/paperlib
[ -f "$CFG" ] && . "$CFG"
ASK=1
if [ -n "$HOST" ]; then
  echo "  NAS：$USER_@$HOST:$PORT  專案資料夾：$DPATH"
  read -r -p "  直接按 Enter 使用這組設定，輸入 c 修改：" a; [ "$a" = "c" ] || ASK=0
fi
if [ $ASK = 1 ]; then
  read -r -p "  NAS 的 IP 或名稱${HOST:+（Enter＝$HOST）}：" v; HOST="${v:-$HOST}"
  read -r -p "  SSH 埠（Enter＝$PORT）：" v; PORT="${v:-$PORT}"
  read -r -p "  DSM 管理員帳號${USER_:+（Enter＝$USER_）}：" v; USER_="${v:-$USER_}"
  read -r -p "  NAS 上的專案資料夾（Enter＝$DPATH）：" v; DPATH="${v:-$DPATH}"
  [ -n "$HOST" ] && [ -n "$USER_" ] || fail "NAS 位址和帳號不能空白。"
  printf 'HOST=%q\nPORT=%q\nUSER_=%q\nDPATH=%q\n' "$HOST" "$PORT" "$USER_" "$DPATH" > "$CFG"
fi
printf '\n\033[1;36m▶ 1/2 上傳到 NAS（請輸入 DSM 密碼，輸入時畫面不會顯示）\033[0m\n'
COPYFILE_DISABLE=1 tar -czf - --exclude=./data --exclude=./import --exclude=./.venv --exclude=__pycache__ --exclude='*.pyc' --exclude=.DS_Store -C "$ROOT" . \
  | ssh -p "$PORT" "$USER_@$HOST" 'rm -rf /tmp/plu && mkdir -p /tmp/plu && tar -xzf - -C /tmp/plu && echo "  ✓ 已上傳"' \
  || fail "上傳失敗。請確認 DSM 已開啟 SSH（控制台 → 終端機和 SNMP）、IP／帳號／密碼正確。"
printf '\n\033[1;36m▶ 2/2 在 NAS 上停止、備份、重建、啟動（約 1–3 分鐘；密碼可能會問兩次）\033[0m\n'
ssh -t -p "$PORT" "$USER_@$HOST" "sudo bash /tmp/plu/update.sh /tmp/plu '$DPATH'" || fail "NAS 上的更新沒有完成，請看上面的訊息。"
printf '\n\033[1;32m✓ 全部完成。瀏覽器開 http://%s:8080 按 ⌘+Shift+R，頭像選單應顯示「程式版本 %s」。\033[0m\n' "$HOST" "$VER"
read -r -p "按 Enter 關閉"
