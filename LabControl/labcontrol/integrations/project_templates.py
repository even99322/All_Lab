"""專案範本（0.0.31）：每個論文庫專案、每個範本（單張、2D…）一份方案，存在 Hub，所有電腦共用。

    load(project_id, template_key) → 方案 dict 或 None（Hub 連不到時用本機快取）
    save(project_id, template_key, scheme_dict)   先寫本機快取，再送到 Hub；送不出去記成待送，下次再送

Hub：GET /api/platform/project-templates/<專案>、PUT /api/platform/project-templates/<專案>/<範本>
本機快取：LAB/project_templates/<專案>/<範本>.json（{"scheme", "time", "pending"}）
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from ..paths import lab_path

log = logging.getLogger(__name__)
_KEY = re.compile(r"[A-Za-z0-9_.-]{1,80}")
_lock = threading.Lock()


def valid(project_id: str, key: str) -> bool:
    return bool(_KEY.fullmatch(str(project_id or "")) and _KEY.fullmatch(str(key or "")))


def _cache_file(project_id: str, key: str) -> Path:
    return lab_path("project_templates", str(project_id), f"{key}.json")


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("scheme"), dict) else None
    except (OSError, ValueError):
        return None


def _write(path: Path, rec: Dict[str, Any]) -> None:
    import uuid

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _hub():
    from .platform import cached_hub

    return cached_hub()


def load(project_id: str, key: str) -> Optional[Dict[str, Any]]:
    """這個專案的這個範本（方案 dict）；沒有 → None。Hub 與本機快取取比較新的。"""
    if not valid(project_id, key):
        return None
    local = _read(_cache_file(project_id, key))
    remote = None
    try:
        r = _hub().get(f"/api/platform/project-templates/{project_id}")
        remote = ((r or {}).get("templates") or {}).get(key)
    except Exception as e:  # noqa: BLE001 — 連不到 Hub：用快取
        log.debug("讀專案範本失敗：%s", e)
    if local and local.get("pending") and (not remote or float(local.get("time", 0)) >= float(remote.get("time", 0))):
        _push_async(project_id, key)                 # 上次沒送出去的：比 Hub 新，送上去
        return local["scheme"]
    if remote and isinstance(remote.get("scheme"), dict):
        with _lock:
            _write(_cache_file(project_id, key), {"scheme": remote["scheme"], "time": remote.get("time", time.time())})
        return remote["scheme"]
    return local["scheme"] if local else None


def save(project_id: str, key: str, scheme: Dict[str, Any], push: bool = True) -> bool:
    """寫本機快取並送到 Hub（push=False 只寫快取、背景送）。回傳是否已送到 Hub。"""
    if not valid(project_id, key):
        return False
    f = _cache_file(project_id, key)
    with _lock:
        _write(f, {"scheme": scheme, "time": time.time(), "pending": True})
    if not push:
        _push_async(project_id, key)
        return False
    return _push(project_id, key)


def _push(project_id: str, key: str) -> bool:
    f = _cache_file(project_id, key)
    rec = _read(f)
    if not rec:
        return False
    try:
        from ..remote.hub import user_name

        _hub()._req("PUT", f"/api/platform/project-templates/{project_id}/{key}",
                    {"scheme": rec["scheme"], "by": user_name()})
    except Exception as e:  # noqa: BLE001
        log.debug("送專案範本到 Hub 失敗（之後再送）：%s", e)
        return False
    with _lock:
        cur = _read(f)
        if cur and cur.get("time") == rec.get("time"):     # 送的期間沒有又改過
            cur.pop("pending", None)
            _write(f, cur)
    return True


def _push_async(project_id: str, key: str) -> None:
    threading.Thread(target=_push, args=(project_id, key), daemon=True, name="project-template-push").start()


def pending() -> int:
    base = lab_path("project_templates")
    return sum(1 for f in base.glob("*/*.json") if (_read(f) or {}).get("pending")) if base.is_dir() else 0
