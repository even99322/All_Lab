"""量測（Lab Control）、讀檔（LabLogViewer）兩個獨立程式的版本資訊與推播（0.0.27）。

    GET /get/info                      → {apps: [app_info(...), ...]}（下載頁、程式內版本檢查）
    GET /get/watch?app=&version=&wait= → 長輪詢：Hub 發佈了比 version 新的版本就立刻回應（程式內推播）
    GET /get/feed/<app>.json           → LabLogViewer 的更新 feed 格式（version、date、items、downloads）

app_info：
    downloads  各作業系統的安裝檔（.exe / .dmg，整個程式）
    code       「功能更新包」：最新的 Python zip（只有程式碼，約 1 MB）。打包版的 Lab Control 如果執行環境相同
               （requirements 指紋一樣），只下載這個 zip 就能更新功能、幾秒內重開，不用換掉整個 .exe。
    version    最新版本（安裝檔或功能更新包，取新的）
"""
from __future__ import annotations

import hashlib
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .platform import APPS, file_platform

_REQ_CACHE: Dict[Tuple[str, float], Dict[str, str]] = {}


def requirements_fingerprint(text: str) -> str:
    """requirements.txt 的指紋：去掉註解、空白、大小寫，排序後的 sha256 前 16 碼。只看套件與版本限制。"""
    lines = []
    for line in str(text or "").splitlines():
        line = line.split("#", 1)[0].strip().lower().replace(" ", "")
        if line:
            lines.append(line)
    return hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()[:16]


def zip_runtime(path: Path) -> Dict[str, str]:
    """Python zip 的執行環境：{requirements: 指紋, python: "3.12"}（快取：檔案沒變就不再打開）。"""
    key = (str(path), path.stat().st_mtime)
    if key in _REQ_CACHE:
        return _REQ_CACHE[key]
    out = {"requirements": "", "python": ""}
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.startswith("__MACOSX/")]

            def shallow(name: str) -> Optional[str]:
                cands = sorted((n for n in names if n.rsplit("/", 1)[-1] == name), key=lambda n: n.count("/"))
                return cands[0] if cands and cands[0].count("/") <= 1 else None
            req = shallow("requirements.txt")
            if req:
                out["requirements"] = requirements_fingerprint(z.read(req).decode("utf-8", "replace"))
            pv = shallow(".python-version")
            if pv:
                out["python"] = z.read(pv).decode("utf-8", "replace").strip()
    except (OSError, zipfile.BadZipFile):
        pass
    _REQ_CACHE[key] = out
    return out


def _pv(v: str):
    from .server import parse_version

    return parse_version(v or "0")


def app_info(plat, mid: str) -> Dict[str, Any]:
    mods = plat.modules()
    m = mods.get(mid) or {}
    notes = {v["version"]: v.get("notes", "") for v in m.get("versions") or []}
    times = {v["version"]: v.get("time") for v in m.get("versions") or []}
    dl = [dict(f, url=f"/get/{mid}/{f['version']}/{_q(f['name'])}", notes=notes.get(f["version"], ""))
          for f in m.get("downloads") or []]
    code = None
    for v in m.get("versions") or []:                      # 新到舊：最新的完整 Python zip（不是安裝檔）
        z = [f for f in v["files"] if f["name"].lower().endswith(".zip") and not f.get("platform")]
        if z:
            f = z[0]
            rt = zip_runtime(plat.modules_dir / mid / v["version"] / f["name"])
            code = {"name": f["name"], "version": v["version"], "size": f["size"], "sha256": f.get("sha256", ""),
                    "url": f"/get/{mid}/{v['version']}/{_q(f['name'])}", "notes": v.get("notes", ""),
                    "time": v.get("time"), **rt}
            break
    vers = [f["version"] for f in dl] + ([code["version"]] if code else [])
    latest = max(vers, key=_pv, default=None)
    return {"id": mid, "name": m.get("name", mid), "app": m.get("app", mid), "description": m.get("description", ""),
            "downloads": dl, "code": code, "version": latest,
            "installer_version": max((f["version"] for f in dl), key=_pv, default=None),
            "notes": notes.get(latest or "", ""), "time": times.get(latest or "")}


def all_apps(plat) -> List[Dict[str, Any]]:
    return [app_info(plat, mid) for mid in APPS]


def watch(plat, mid: str, version: str, wait: float) -> Dict[str, Any]:
    """等到 Hub 上有比 version 新的 mid（或 wait 秒到），回傳 {changed, app}。"""
    deadline = time.monotonic() + max(0.0, min(float(wait or 0), 120.0))
    while True:
        with plat.release_cond:
            seq = plat.release_seq
        info = app_info(plat, mid)
        if info["version"] and _pv(info["version"]) > _pv(version):
            return {"changed": True, "app": info}
        left = deadline - time.monotonic()
        if left <= 0:
            return {"changed": False, "app": info}
        with plat.release_cond:
            if plat.release_seq == seq:
                plat.release_cond.wait(timeout=left)


def lablogviewer_feed(plat, mid: str = "lablogviewer") -> Dict[str, Any]:
    """LabLogViewer 的 latest.json 格式（app/core/update_feed.py）＋ 這次的安裝檔清單。"""
    info = app_info(plat, mid)
    ver = info.get("installer_version") or info.get("version") or "0"
    notes = next((f.get("notes") for f in info["downloads"] if f["version"] == ver), "") or info.get("notes") or ""
    items = []
    for line in notes.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if line and not line.startswith("#"):
            items.append({"category": "improved", "zh": line, "en": line})
    t = next((f for f in info["downloads"] if f["version"] == ver), None)
    date = time.strftime("%Y-%m-%d", time.localtime(info.get("time") or time.time())) if t else ""
    return {"version": ver, "date": date, "items": items[:200],
            "downloads": [f for f in info["downloads"] if f["version"] == ver]}


def _q(name: str) -> str:
    import urllib.parse

    return urllib.parse.quote(name)


# ---- Windows 一鍵安裝（0.0.28）-------------------------------------------------------------------------
SETUP_APPS = ("labcontrol", "lablogviewer")


def is_setup_name(name: str) -> bool:
    n = str(name).lower()
    return n.endswith(".exe") and "setup" in n


def setup_info(plat) -> Optional[Dict[str, Any]]:
    """最新的 QELSetup_v<版本>_windows.exe（下載頁的「Windows 一鍵安裝」）；沒有 → None。"""
    info = app_info(plat, "setup")
    win = [f for f in info["downloads"] if f.get("platform") == "windows"]
    win.sort(key=lambda f: _pv(f["version"]), reverse=True)
    return win[0] if win else None


def _plan_file(plat, mid: str, platform: str, arch: str) -> Optional[Dict[str, Any]]:
    m = plat.modules().get(mid) or {}
    files = [dict(f, version=v["version"], url=f"/get/{mid}/{v['version']}/{_q(f['name'])}")
             for v in m.get("versions") or [] for f in v["files"] if f.get("platform") == platform]
    if platform == "windows":
        files = [f for f in files if is_setup_name(f["name"])]
    else:                                                  # macOS：.dmg；同晶片 > universal > 沒寫
        files = [f for f in files if f["name"].lower().endswith(".dmg")
                 and (f.get("arch") or "") in (arch, "universal2", "")]
    if not files:
        return None
    best = max(_pv(f["version"]) for f in files)
    files = [f for f in files if _pv(f["version"]) == best]
    files.sort(key=lambda f: 0 if (f.get("arch") or "") == arch else 1 if f.get("arch") == "universal2" else 2)
    return files[0]


def setup_plan(plat, platform: str = "windows", arch: str = "x86_64") -> str:
    """一鍵安裝讀的清單（純文字，一行一個程式）：
        app|版本|檔名|大小|sha256|網址路徑
    Windows：每個程式最新的安裝程式（*setup*.exe，Inno Setup，能用 /SILENT 安裝）；
    macOS（?platform=macos&arch=arm64|x86_64）：最新的 .dmg（同晶片優先），給 /get/mac 安裝腳本。"""
    platform = "macos" if str(platform).lower().startswith("mac") else "windows"
    arch = "arm64" if str(arch).lower() in ("arm64", "aarch64") else "x86_64"
    lines = [f"# QEL setup plan v1 ({platform} {arch}): app|version|file|size|sha256|path"]
    for mid in SETUP_APPS:
        f = _plan_file(plat, mid, platform, arch)
        if f is not None:
            lines.append("|".join([mid, f["version"], f["name"], str(f.get("size", 0)), f.get("sha256", ""),
                                   f["url"]]))
    return "\n".join(lines) + "\n"


MAC_SCRIPT = r"""#!/bin/bash
# 量子工程平台 Mac 安裝（從實驗室 NAS）：在「終端機」貼上
#     curl -fsSL __BASE__/get/mac | bash
# 下載量測（Lab Control）與讀檔（LabLogViewer）這台 Mac 晶片的最新版、檢查 sha256、裝到「應用程式」。
# 用 curl 下載不會被標成「從網路下載」，所以不會出現「已損毀，無法打開」。只裝一個：QEL_APPS=labcontrol
set -euo pipefail
main() {
  local BASE="__BASE__" APPS="${QEL_APPS:-labcontrol lablogviewer}" ARCH DEST TMP PLAN
  ARCH=$(uname -m); [ "$ARCH" = "arm64" ] || ARCH="x86_64"
  DEST="/Applications"; [ -w "$DEST" ] || DEST="$HOME/Applications"; mkdir -p "$DEST"
  TMP=$(mktemp -d); trap "rm -rf '$TMP'" EXIT
  echo "量子工程平台：$BASE（$ARCH）→ $DEST"
  PLAN=$(curl -fsSL "$BASE/get/setup.txt?platform=macos&arch=$ARCH")
  local n=0 app ver name size sha path mnt src dst
  while IFS='|' read -r app ver name size sha path; do
    case "$app" in ""|\#*) continue ;; esac
    case " $APPS " in *" $app "*) ;; *) continue ;; esac
    echo "⬇ $name"
    curl -fL --progress-bar "$BASE$path" -o "$TMP/$name"
    echo "$sha  $TMP/$name" | shasum -a 256 -c - >/dev/null || { echo "✖ $name 檢查碼不符"; exit 1; }
    mnt=$(mktemp -d)
    hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$mnt" "$TMP/$name" >/dev/null
    src=$(ls -d "$mnt"/*.app | head -1)
    dst="$DEST/$(basename "$src")"
    osascript -e "quit app \"$(basename "$src" .app)\"" >/dev/null 2>&1 || true
    rm -rf "$dst"
    ditto "$src" "$dst"
    hdiutil detach "$mnt" -quiet || true
    xattr -dr com.apple.quarantine "$dst" 2>/dev/null || true
    echo "✔ $dst（$ver）"
    n=$((n + 1))
  done <<< "$PLAN"
  [ "$n" -gt 0 ] || { echo "✖ 平台上還沒有這台 Mac（$ARCH）的安裝檔"; exit 1; }
  echo "完成。之後兩個程式都會自己收到更新。"
}
main "$@"
"""


def mac_script(base: str) -> str:
    return MAC_SCRIPT.replace("__BASE__", base.rstrip("/"))


def public_file(name: str) -> bool:
    """/get/<app>/… 不用登入可以下載的檔：安裝檔與功能更新包（Python zip）。"""
    return bool(file_platform(name)) or name.lower().endswith(".zip")
