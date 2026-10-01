#!/bin/bash
# LAB-QEL論文庫：在 NAS 上更新並重建（停止舊容器 → 備份資料庫 → 換新程式 → 重建 → 啟動 → 確認版本）
#
# 用法（SSH 登入 NAS 後）：
#   已經用 File Station 把新程式蓋到專案資料夾：
#     sudo bash /volume1/docker/paperlib/update.sh
#   新程式放在別的資料夾（例如一鍵更新工具傳上來的 /tmp/plu）：
#     sudo bash /tmp/plu/update.sh /tmp/plu /volume1/docker/paperlib
#   加上 --clean 會完全不用快取重新建置（較慢，約 5–10 分鐘）：
#     sudo bash /volume1/docker/paperlib/update.sh --clean
#
# 不會動到 data/（論文、筆記、設定）與 import/。

export PATH="/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin:$PATH"
NAME=paperlib
PORT=8080

CLEAN=""
ARGS=()
for a in "$@"; do
  if [ "$a" = "--clean" ]; then CLEAN="--no-cache"; else ARGS+=("$a"); fi
done
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${ARGS[0]:-$HERE}"
DST="${ARGS[1]:-$HERE}"
SRC="$(cd "$SRC" 2>/dev/null && pwd)" || { echo "✗ 找不到新程式資料夾：${ARGS[0]}"; exit 1; }
mkdir -p "$DST" && DST="$(cd "$DST" && pwd)"

step() { printf '\n\033[1;36m▶ %s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '\n\033[1;31m✗ %s\033[0m\n' "$1"; exit 1; }

[ "$(id -u)" = "0" ] || die "需要管理員權限，請在指令前面加 sudo，例如：sudo bash $0"
command -v docker >/dev/null 2>&1 || die "找不到 docker。請確認已安裝 Container Manager。"
if docker compose version >/dev/null 2>&1; then DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then DC=(docker-compose)
else die "找不到 docker compose。"; fi

echo "LAB-QEL論文庫 更新工具"
echo "  專案資料夾：$DST"

# ── 解壓縮時多了一層資料夾（paperlib/paperlib/app…）
if [ "$SRC" = "$DST" ] && [ -f "$DST/paperlib/app/main.py" ]; then
  warn "發現多一層的 paperlib 資料夾，改用裡面的新程式"
  SRC="$DST/paperlib"
fi
[ -f "$SRC/app/main.py" ] && [ -f "$SRC/docker-compose.yml" ] || die "在 $SRC 找不到程式（app/main.py、docker-compose.yml）。"
NEWVER="$(grep -m1 -o 'VERSION = "[^"]*"' "$SRC/app/config.py" | cut -d'"' -f2)"
echo "  新版本：${NEWVER:-未知}"
OLDVER="$(curl -s --noproxy "*" -m 3 "http://127.0.0.1:$PORT/api/site" 2>/dev/null | grep -o '"version":"[^"]*"' | cut -d'"' -f4)"
echo "  目前執行中的版本：${OLDVER:-（沒有在執行或 1.7 以前）}"

# ── 1. 停止舊容器（不管是不是這個專案建立的）
step "1/5 停止舊的容器"
cd "$DST"
"${DC[@]}" -p "$NAME" down --remove-orphans >/dev/null 2>&1 || true
if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  docker stop "$NAME" >/dev/null 2>&1 || true
  docker rm -f "$NAME" >/dev/null 2>&1 || true
fi
if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then die "容器 $NAME 停不掉，請到 Container Manager →「容器」手動刪除後再執行一次。"; fi
OTHER="$(docker ps --format '{{.Names}} {{.Ports}}' | grep -E ":$PORT->" | cut -d' ' -f1)"
[ -n "$OTHER" ] && die "還有其他容器佔用 $PORT 埠：$OTHER。請先停止它（sudo docker stop $OTHER）再執行一次。"
ok "已停止"

# ── 2. 備份資料庫
step "2/5 備份資料庫"
mkdir -p "$DST/data/backups"
if [ -f "$DST/data/library.db" ]; then
  TS="$(date +%Y%m%d-%H%M%S)"
  B="$DST/data/backups/before-update-$TS"
  cp -p "$DST/data/library.db" "$B.db"
  for x in wal shm; do [ -f "$DST/data/library.db-$x" ] && cp -p "$DST/data/library.db-$x" "$B.db-$x"; done
  ok "已備份到 data/backups/before-update-$TS.db"
  # 只留最近 5 份更新前備份
  ls -1t "$DST"/data/backups/before-update-*.db 2>/dev/null | tail -n +6 | while read -r f; do rm -f "$f" "$f-wal" "$f-shm"; done
else
  warn "還沒有資料庫（第一次安裝），略過"
fi
mkdir -p "$DST/import"

# ── 3. 換上新程式（data/、import/ 不動）
step "3/5 換上新程式"
if [ "$SRC" = "$DST" ]; then
  ok "程式已經在專案資料夾裡"
else
  shopt -s dotglob nullglob
  for item in "$SRC"/*; do
    base="$(basename "$item")"
    case "$base" in data|import|.DS_Store|__MACOSX) continue ;; esac
    if [ "$base" = "docker-compose.yml" ] && [ -f "$DST/docker-compose.yml" ]; then
      cp -p "$DST/docker-compose.yml" "$DST/docker-compose.yml.bak"
      if grep -qE '^[[:space:]]+TUNNEL_TOKEN:' "$DST/docker-compose.yml"; then
        # 有自己設定的 Cloudflare Tunnel：保留原檔，只更新映像檔版本
        sed -i "s#image: paperlib:.*#image: paperlib:${NEWVER}#" "$DST/docker-compose.yml"
        warn "保留你修改過的 docker-compose.yml（Cloudflare Tunnel 設定），只更新版本號；原檔備份在 docker-compose.yml.bak"
        continue
      fi
    fi
    rm -rf "${DST:?}/$base"
    cp -a "$item" "$DST/$base"
  done
  shopt -u dotglob nullglob
  # 多一層資料夾的情況：搬完就把內層刪掉（裡面沒有資料庫才刪）
  if [ "$SRC" = "$DST/paperlib" ] && [ ! -e "$DST/paperlib/data/library.db" ]; then rm -rf "${DST:?}/paperlib"; fi
  find "$DST/app" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
  ok "已更新程式檔案"
fi
HAVE="$(grep -m1 -o 'VERSION = "[^"]*"' "$DST/app/config.py" | cut -d'"' -f2)"
[ "$HAVE" = "$NEWVER" ] || die "專案資料夾裡的版本是 $HAVE，不是 $NEWVER，檔案可能沒有複製成功。"

# ── 4. 重建映像檔
step "4/5 重建映像檔${CLEAN:+（不使用快取，約 5–10 分鐘）}"
cd "$DST"
"${DC[@]}" -p "$NAME" build $CLEAN || die "建置失敗。上面的訊息是錯誤原因（常見：NAS 連不到外網下載套件）。舊資料都還在，修好後再執行一次即可。"
ok "建置完成"

# ── 5. 啟動並確認版本
step "5/5 啟動"
"${DC[@]}" -p "$NAME" up -d || die "啟動失敗。"
printf '  等待網站啟動'
RUNVER=""
for i in $(seq 1 90); do
  RUNVER="$(curl -s --noproxy "*" -m 2 "http://127.0.0.1:$PORT/api/site" 2>/dev/null | grep -o '"version":"[^"]*"' | cut -d'"' -f4)"
  [ -n "$RUNVER" ] && break
  printf '.'; sleep 2
done
echo
if [ "$RUNVER" = "$NEWVER" ]; then
  docker image prune -f >/dev/null 2>&1 || true   # 清掉被取代的舊映像檔
  [ "$SRC" != "$DST" ] && case "$SRC" in /tmp/*) rm -rf "$SRC" ;; esac
  printf '\n\033[1;32m✓ 更新完成：目前版本 %s\033[0m\n' "$RUNVER"
  echo "  瀏覽器按 Ctrl+F5（手機把 App 關掉再開），頭像選單的「程式版本」應該顯示 $RUNVER。"
else
  echo "---- 最後 40 行日誌 ----"
  docker logs --tail 40 "$NAME" 2>&1
  die "網站沒有在 3 分鐘內回應新版本（目前：${RUNVER:-沒有回應}）。請把上面的日誌傳給維護的人。"
fi
