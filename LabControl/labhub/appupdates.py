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


def public_file(name: str) -> bool:
    """/get/<app>/… 不用登入可以下載的檔：安裝檔與功能更新包（Python zip）。"""
    return bool(file_platform(name)) or name.lower().endswith(".zip")
