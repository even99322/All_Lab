"""全平台共用的標籤。

所有模塊用同一批標籤：量測模塊寫進數據檔（Labber Tags）、讀檔模塊用來分類與搜尋、論文模塊用來連到論文。
標籤清單以大程式伺服器為準；連不上時用本機快取（``<QEL_HOME>/cache/tags.json``），
再沒有就用這裡的預設值（與讀檔模塊原本的預設標籤相同）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import qel_home

# 與讀檔模塊（LabLogViewer）原本的分類相同，另加「論文主題」給論文庫的標籤
CATEGORIES = ("Project", "Level", "Board Design", "Data Analysis", "Measurement", "Paper Topic", "Other")
CATEGORY_NAMES = {"Project": "專案", "Level": "階段", "Board Design": "板子設計", "Data Analysis": "數據分析",
                  "Measurement": "量測", "Paper Topic": "論文主題", "Other": "其他"}
SINGLE_SELECT = frozenset({"Project", "Level"})
DEFAULT_TAGS: Dict[str, tuple] = {
    "Project": ("LRCPAEP", "BIC", "CM", "RSMEP"),
    "Level": ("LA", "LR"),
    "Board Design": ("Mirror",),
    "Data Analysis": ("Flux", "BG", "De-background"),
    "Measurement": ("VNA Sweep",),
    "Paper Topic": (),
    "Other": ("Good Data", "Best Data", "Debug", "singleYIG", "doubleYIG"),
}

_KNOWN = {
    "lrcpaep/ccep": "LRCPAEP", "lrcpaep": "LRCPAEP",
    "best-data": "Best Data", "best data": "Best Data",
    "good-data": "Good Data", "good data": "Good Data",
    "debug": "Debug", "debg": "De-background", "de-background": "De-background",
    "debackground": "De-background", "de background": "De-background",
    "vna sweep": "VNA Sweep", "vna-sweep": "VNA Sweep",
}
_CASE_FIXED = ("BIC", "CM", "RSMEP", "LA", "LR", "Mirror", "Flux", "BG", "singleYIG", "doubleYIG")
_SPACES = re.compile(r"\s+")


def canonical(name: Any, aliases: Optional[Dict[str, str]] = None) -> str:
    """標籤名稱正規化：去空白、已知別名（大小寫、底線、連字號）統一成同一個寫法。"""
    if not isinstance(name, str):
        return ""
    n = _SPACES.sub(" ", name.strip())
    if not n:
        return ""
    folded = n.casefold().replace("_", "-")
    if aliases:
        for k, v in aliases.items():
            if k.casefold().replace("_", "-") == folded:
                return v
    if folded in _KNOWN:
        return _KNOWN[folded]
    if folded.replace("-", " ") in _KNOWN:
        return _KNOWN[folded.replace("-", " ")]
    for c in _CASE_FIXED:
        if n.casefold() == c.casefold():
            return c
    return n


def default_category(name: str) -> str:
    for cat, names in DEFAULT_TAGS.items():
        if name in names:
            return cat
    return "Other"


def default_taxonomy() -> Dict[str, Any]:
    tags = [{"name": t, "category": c, "color": "", "description": "", "aliases": [], "papers": []}
            for c in CATEGORIES for t in DEFAULT_TAGS.get(c, ())]
    return {"categories": [{"key": c, "name": CATEGORY_NAMES[c], "single": c in SINGLE_SELECT} for c in CATEGORIES],
            "tags": tags, "source": "default"}


def _cache_path() -> Path:
    return qel_home() / "cache" / "tags.json"


def save_cache(taxonomy: Dict[str, Any]) -> None:
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(taxonomy, cached_at=time.time()), ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def load_cache() -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(_cache_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("tags"), list) else None
    except (OSError, ValueError):
        return None


def shared_taxonomy(client=None) -> Dict[str, Any]:
    """大程式 → 本機快取 → 預設值。成功連線時順便更新快取。"""
    if client is not None:
        try:
            t = client.tags()
            t["source"] = "portal"
            save_cache(t)
            return t
        except Exception:  # noqa: BLE001 - 離線時用快取
            pass
    cached = load_cache()
    if cached:
        cached["source"] = "cache"
        return cached
    return default_taxonomy()


def tag_names(taxonomy: Dict[str, Any]) -> List[str]:
    return [t["name"] for t in taxonomy.get("tags", []) if t.get("name")]


def alias_map(taxonomy: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for t in taxonomy.get("tags", []):
        for a in t.get("aliases") or []:
            out[a] = t["name"]
    return out


def normalize_list(names: Iterable[Any], taxonomy: Optional[Dict[str, Any]] = None) -> List[str]:
    """正規化並去重（保留順序）。"""
    al = alias_map(taxonomy) if taxonomy else None
    out: List[str] = []
    seen = set()
    for n in names:
        c = canonical(n, al)
        if c and c.casefold() not in seen:
            seen.add(c.casefold())
            out.append(c)
    return out


def by_category(taxonomy: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for t in taxonomy.get("tags", []):
        out.setdefault(t.get("category") or "Other", []).append(t)
    return out
