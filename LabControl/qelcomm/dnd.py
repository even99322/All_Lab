"""模塊之間拖放數據檔的共用格式（不依賴 Qt）。

拖出（量測模塊的檔案清單、讀檔模塊、平台網頁）一律帶：

  * ``text/uri-list``：本機檔案（file://…）；網頁拖出時是數據目錄網址 ``http://<Hub>/api/platform/catalog/<id>``
  * ``application/x-qel-data``：JSON ``{"items": [{path?, catalog_id?, gateway?, name, node?, rel?, scheme?}]}``
  * 網頁另外帶 ``application/x-qel-catalog``：``{"id", "hub"}``

接收端用 :func:`resolve` 把這些變成本機檔案路徑（目錄項目會自動從 Hub 下載到快取資料夾）。
"""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

MIME = "application/x-qel-data"
MIME_WEB = "application/x-qel-catalog"
_CAT_RX = re.compile(r"^(https?://[^/]+)/api/platform/catalog/([0-9a-f]{16})/?$")
_FILE_RX = re.compile(r"^(https?://[^/]+)(/api/files/.+)$")


def payload(items: Iterable[Dict[str, Any]]) -> bytes:
    return json.dumps({"items": list(items)}, ensure_ascii=False).encode("utf-8")


def parse_payload(data: Optional[bytes]) -> List[Dict[str, Any]]:
    if not data:
        return []
    try:
        obj = json.loads(bytes(data).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    if isinstance(obj, dict) and "id" in obj and "items" not in obj:          # 網頁的 x-qel-catalog
        return [{"catalog_id": obj["id"], "gateway": obj.get("hub", "")}]
    return [x for x in (obj.get("items") if isinstance(obj, dict) else []) or [] if isinstance(x, dict)]


def ref_from_url(url: str) -> Optional[Dict[str, Any]]:
    """網址 → 目錄項目參考 {gateway, catalog_id} 或 {gateway, url}（/api/files/…）；本機檔案 → {path}。"""
    url = (url or "").strip()
    m = _CAT_RX.match(url)
    if m:
        return {"gateway": m.group(1), "catalog_id": m.group(2)}
    m = _FILE_RX.match(url)
    if m:
        return {"gateway": m.group(1), "url": m.group(2)}
    if url.startswith("file:"):
        p = urllib.parse.urlsplit(url)
        path = urllib.parse.unquote(p.path)
        if re.match(r"^/[A-Za-z]:/", path):                     # file:///C:/… → C:/…
            path = path[1:]
        return {"path": path}
    if url and "://" not in url:
        return {"path": url}
    return None


def cache_dir() -> Path:
    from .session import qel_home

    d = qel_home() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve(urls: Iterable[str] = (), data: Optional[bytes] = None, session: Any = None,
            dest: Optional[Path] = None) -> List[Path]:
    """拖進來的東西 → 本機檔案。需要從 Hub 下載的項目用 session（qelcomm.Session）下載到 dest（預設快取）。"""
    refs = parse_payload(data)
    if not refs:
        refs = [r for r in (ref_from_url(u) for u in urls) if r]
    out: List[Path] = []
    for r in refs:
        p = r.get("path")
        if p and Path(p).is_file():
            out.append(Path(p))
            continue
        if session is None or not (r.get("catalog_id") or r.get("url")):
            continue
        sub = (dest or cache_dir()) / (r.get("catalog_id") or "files")
        sub.mkdir(parents=True, exist_ok=True)
        if r.get("catalog_id"):
            item = session.item(r["catalog_id"])
            if not item.get("exists"):
                raise FileNotFoundError(f"{item.get('name')} 只在量測電腦 {item.get('node')} 上，Hub 沒有這個檔案")
            target = sub / item["name"]
            out.append(target if target.exists() and target.stat().st_size == item.get("size") else
                       session.download(item, sub))
        else:
            out.append(session.download(r["url"], sub))
    return out
