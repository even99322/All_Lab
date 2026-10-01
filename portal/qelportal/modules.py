"""模塊清單、預設權限與發佈版本。

每個模塊各自發佈、各自更新：上傳的 zip 裡必須有 ``module.json``（id、version 與這裡一致）。
桌面大程式依這裡的「最新版本」各自更新每個模塊；NAS 上的服務（論文庫、Hub、大程式本身）由更新代理更新。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# kind：web＝網頁（論文庫）；desktop＝桌面程式（大程式下載安裝）；library＝給其他模塊用的套件；
#       service＝NAS 上的服務（只顯示狀態，由更新代理更新）
# access：True＝要站長開放；預設值 default 給新使用者（站長可在管理頁改預設）
BUILTIN: List[Dict[str, Any]] = [
    {"id": "paperlib", "name": "論文模塊", "kind": "web", "access": True, "default": True, "icon": "book",
     "description": "論文庫：上傳、分類、閱讀、標註、標籤對應論文。"},
    {"id": "labcontrol", "name": "量測模塊", "kind": "desktop", "access": True, "default": False, "icon": "gauge",
     "description": "Lab Control：儀器控制、流程圖量測、遠端量測節點（會控制硬體，預設不開放）。",
     "platforms": ["windows", "macos"]},
    {"id": "lablogviewer", "name": "數據讀取模擬模塊", "kind": "desktop", "access": True, "default": True,
     "icon": "chart", "description": "LabLogViewer：讀 Labber / HDF5 數據、分析、擬合、3D。",
     "platforms": ["windows", "macos"]},
    {"id": "labcomm", "name": "通信模塊", "kind": "library", "access": False, "icon": "link",
     "description": "所有模塊之間的通信（大程式會自動安裝與更新）。"},
    {"id": "launcher", "name": "QEL Lab 大程式（桌面）", "kind": "desktop", "access": False, "icon": "home",
     "description": "Windows / macOS 的大程式本體：登入、安裝與更新各模塊。", "platforms": ["windows", "macos"]},
    {"id": "monitor", "name": "QEL Lab 監控程式", "kind": "desktop", "access": "owner", "icon": "pulse",
     "description": "監控大程式網站與 NAS 服務、管理更新（站長用）。", "platforms": ["windows", "macos"]},
    {"id": "portal", "name": "大程式網站", "kind": "service", "access": False, "icon": "server",
     "description": "NAS 上的大程式伺服器（本網站）。"},
    {"id": "labhub", "name": "量測中繼站（Lab Control Hub）", "kind": "service", "access": False, "icon": "relay",
     "description": "量測指令與即時資料中繼、量測檔存放。"},
    {"id": "agent", "name": "更新代理", "kind": "service", "access": False, "icon": "wrench",
     "description": "在 NAS 上停止、備份、更新、重建各服務。"},
]
BY_ID = {m["id"]: m for m in BUILTIN}
ACCESS_MODULES = [m["id"] for m in BUILTIN if m["access"] is True]
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")
VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}([a-z0-9.+-]*)$", re.I)


def parse_version(v: str) -> Tuple:
    """1.0.10 > 1.0.9；1.01 視為 1.0.1（與讀檔模塊的規則相同）。"""
    parts = re.findall(r"\d+|[a-z]+", str(v).lower())
    nums: List[Any] = []
    for p in parts:
        nums.append((0, int(p)) if p.isdigit() else (-1, p))
    return tuple(nums)


def find_manifest(z: zipfile.ZipFile) -> Tuple[str, Dict[str, Any]]:
    """在 zip 第一層或第二層找 module.json。"""
    cands = [n for n in z.namelist() if n.endswith("module.json") and n.count("/") <= 1
             and not n.startswith("__MACOSX")]
    if not cands:
        raise ValueError("zip 裡找不到 module.json（模塊根目錄必須有 module.json）")
    name = sorted(cands, key=lambda n: n.count("/"))[0]
    try:
        d = json.loads(z.read(name).decode("utf-8-sig"))
    except ValueError as e:
        raise ValueError(f"module.json 格式錯誤：{e}") from None
    if not isinstance(d, dict):
        raise ValueError("module.json 必須是物件")
    return name, d


def inspect_release(path: Path, module_id: str, version: str) -> Dict[str, Any]:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError("不是 zip 檔") from None
    with z:
        for m in z.infolist():
            n = m.filename
            if n.startswith("/") or ".." in Path(n).parts or re.match(r"^[A-Za-z]:", n):
                raise ValueError(f"zip 內容不安全：{n}")
        bad = z.testzip()
        if bad:
            raise ValueError(f"zip 損壞：{bad}")
        _, man = find_manifest(z)
    if man.get("id") != module_id:
        raise ValueError(f"module.json 的 id 是 {man.get('id')!r}，不是 {module_id!r}")
    if str(man.get("version")) != version:
        raise ValueError(f"module.json 的版本是 {man.get('version')!r}，不是 {version!r}")
    return man


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def latest(versions: List[str]) -> Optional[str]:
    return max(versions, key=parse_version) if versions else None


def manifest_bytes(z: bytes) -> Dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(z)) as zz:
        return find_manifest(zz)[1]
