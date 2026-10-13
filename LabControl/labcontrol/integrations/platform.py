"""量子工程平台整合（不依賴 Qt）。

* 登入：沿用啟動器的登入（環境變數 QEL_GATEWAY / QEL_TOKEN 或 ~/.qel/session.json，見 qelcomm）。
* 數據目錄：本機量測存好的檔，背景上傳到平台（帶標籤、量測者、方案名稱），讀檔模塊與網頁就能直接用。
  量測節點的檔案原本就會上傳到 Hub，這裡只處理「這台電腦自己量的」。
* 標籤：所有模塊共用的標籤（含論文數、論文庫連結）。

量測節點（remote.node）仍然用裝置 token（remote.token）連 Hub，不受使用者登入過期影響。
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..settings import setting

log = logging.getLogger(__name__)

_session_cache: Dict[str, Any] = {"at": 0.0, "s": None}


def available() -> bool:
    try:
        import qelcomm  # noqa: F401
        return True
    except ImportError:
        return False


def current_session(refresh: bool = False):
    """目前的平台登入（qelcomm.Session）或 None。平台功能關閉 / 沒裝 qelcomm / 沒登入 → None。"""
    if not setting("platform.enabled", True) or not available():
        return None
    now = time.time()
    if not refresh and now - _session_cache["at"] < 30:
        return _session_cache["s"]
    from qelcomm import Session

    s = Session.current()
    _session_cache.update(at=now, s=s)
    return s


def set_session(s) -> None:
    _session_cache.update(at=time.time(), s=s)


def node_name() -> str:
    from ..remote.hub import default_node_name

    return default_node_name()


class TagCache:
    """平台標籤（20 秒快取；失敗時沿用上次的結果）。"""

    def __init__(self) -> None:
        self.tags: List[Dict[str, Any]] = []
        self.at = 0.0
        self.error = ""

    def get(self, force: bool = False) -> List[Dict[str, Any]]:
        s = current_session()
        if s is None:
            return self.tags
        if force or time.time() - self.at > 20:
            try:
                self.tags = s.tags()
                self.at = time.time()
                self.error = ""
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
        return self.tags

    def names(self) -> List[str]:
        return [t["name"] for t in self.get()]


TAGS = TagCache()


class ProjectCache:
    """論文庫的專案（20 秒快取）。"""

    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []
        self.at = 0.0
        self.error = ""

    def get(self, force: bool = False) -> List[Dict[str, Any]]:
        if not (force or time.time() - self.at > 20):
            return self.items
        s = current_session()
        try:
            if s is not None:
                self.items = s.projects(refresh=force)
            else:
                # 0.0.31：沒有登入平台 → 問 Hub（裝置 token）；Hub 用論文庫的服務帳號查
                self.items = hub_projects(force)
            self.at = time.time()
            self.error = ""
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            if s is not None:                      # 個人登入失效：改問 Hub
                try:
                    self.items = hub_projects(force)
                    self.at = time.time()
                    self.error = ""
                except Exception:  # noqa: BLE001
                    pass
        return self.items

    def find(self, pid: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self.items if p["id"] == str(pid)), None)


PROJECTS = ProjectCache()

_hub_cache: Dict[str, Any] = {"hub": None, "at": 0.0}


def cached_hub():
    """連得到的 Lab Control Hub（5 分鐘內重用）；連不到 → HubError。"""
    from ..remote.hub import find_hub

    if _hub_cache["hub"] is None or time.time() - _hub_cache["at"] > 300:
        _hub_cache["hub"] = find_hub()
        _hub_cache["at"] = time.time()
    return _hub_cache["hub"]


def hub_projects(force: bool = False) -> List[Dict[str, Any]]:
    """Hub 的專案清單（裝置 token；Hub 沒有使用者登入時用論文庫服務帳號查）。"""
    r = cached_hub().get("/api/platform/projects", **({"refresh": "1"} if force else {}))
    items = r.get("projects") if isinstance(r, dict) else r
    return [dict(p, id=str(p.get("id"))) for p in (items or []) if isinstance(p, dict) and p.get("id") is not None]

_suspend = {"n": 0}
_suspend_lock = threading.Lock()


class suspend_uploads:  # noqa: N801 - 當作 with 區塊使用
    """量測節點匯出時暫停這台電腦的平台上傳（節點自己會上傳到 Hub 並登記）。"""

    def __enter__(self):
        with _suspend_lock:
            _suspend["n"] += 1

    def __exit__(self, *a):
        with _suspend_lock:
            _suspend["n"] -= 1


class Uploader:
    """本機量測存檔（data.exported 事件）→ 背景上傳到平台數據目錄。"""

    def __init__(self, bus) -> None:
        self.bus = bus
        self.q: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self.done: List[Dict[str, Any]] = []
        self.tok = bus.subscribe("data.exported", self._on_export)
        self.th = threading.Thread(target=self._loop, daemon=True, name="platform-upload")
        self.th.start()

    def close(self) -> None:
        try:
            self.bus.unsubscribe(self.tok)
        except Exception:  # noqa: BLE001
            pass
        self.q.put(None)

    def _on_export(self, _topic: str, p: Dict[str, Any]) -> None:
        if _suspend["n"] or not setting("platform.upload_local_runs", True):
            return
        if current_session() is None:
            return
        self.q.put(dict(p))

    def _loop(self) -> None:
        while True:
            job = self.q.get()
            if job is None:
                return
            try:
                self.done.append(self.upload(job))
            except Exception as e:  # noqa: BLE001
                log.warning("上傳到平台失敗：%s", e)
                try:
                    self.bus.log(f"⚠ 上傳 {Path(job.get('path', '')).name} 到平台失敗：{e}（檔案仍在這台電腦）",
                                 "warning")
                except Exception:  # noqa: BLE001
                    pass

    def upload(self, job: Dict[str, Any]) -> Dict[str, Any]:
        s = current_session()
        p = Path(job["path"])
        from ..data.naming import data_root

        root = data_root() if setting("data.root", "") else p.parent
        try:
            rel = p.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = p.name
        meta = {k: job[k] for k in ("writer", "segment") if job.get(k) is not None}
        meta["segment"] = str(meta["segment"]) if "segment" in meta else None
        meta = {k: v for k, v in meta.items() if v is not None}
        res = s.upload(node_name(), rel, p, tags=list(job.get("tags") or []), run_id=str(job.get("run_id") or ""),
                       scheme=str(job.get("scheme") or ""), meta=meta, project=str(job.get("project") or ""))
        try:
            self.bus.publish("platform.uploaded", path=str(p), catalog_id=res.get("catalog_id"))
        except Exception:  # noqa: BLE001
            pass
        self.bus.log(f"📤 已上傳到平台數據目錄：{p.name}" + (f"（{', '.join(job['tags'])}）" if job.get("tags") else ""))
        return res


def fetch_catalog_file(catalog_id: str) -> Path:
    """平台數據目錄的項目 → 下載到本機快取，回傳路徑。"""
    from qelcomm import dnd

    s = current_session()
    if s is None:
        raise RuntimeError("沒有登入量子工程平台")
    files = dnd.resolve([], dnd.payload([{"catalog_id": catalog_id}]), s)
    if not files:
        raise RuntimeError("下載失敗")
    return files[0]
