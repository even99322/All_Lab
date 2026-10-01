"""連到 Lab Control Hub（NAS 上的中繼網站，見 labhub/ 與 docs/REMOTE.md）。

    hub = find_hub()                    # 依 settings.yaml remote.hub_urls 依序嘗試（內網、VPN）
    hub.nodes()                         # 所有節點（線上與否由 Hub 依它收到心跳的時間判斷，不受各電腦時鐘影響）
    hub.call("QEL-PC", "ping")          # 送指令並等回覆

所有連線都是這台電腦主動連 Hub（HTTP），不需要 SMB 共用資料夾、不需要 Windows 登入 NAS。
存取用 settings.yaml 的 remote.token（實驗室共用的 Hub token，不是 NAS 帳號密碼）。
只用標準函式庫 urllib，並略過系統 proxy（實驗室內網 / VPN 直連）。
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import re
import shutil
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..settings import setting

log = logging.getLogger(__name__)


class HubError(RuntimeError):
    pass


class HubConflict(HubError):
    pass


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_") or "node"


def hostname() -> str:
    return socket.gethostname()


def user_name() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return os.environ.get("USERNAME", "user")


def user_id() -> str:
    return f"{user_name()}@{hostname()}"


def default_node_name() -> str:
    return safe_name(setting("remote.node.name", "") or hostname())


# ---- JSON（本機小檔用，例如 LAB/node_state.json）-----------------------------------
def _default(o: Any) -> Any:
    try:
        import numpy as np

        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (np.floating, np.integer, np.bool_)):
            return o.item()
    except ImportError:  # pragma: no cover
        pass
    return str(o)


def dumps(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, default=_default).encode("utf-8")


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_bytes(dumps(data))
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ---- 設定 ------------------------------------------------------------------
def hub_urls() -> List[str]:
    env = os.environ.get("LAB_CONTROL_HUB")
    urls = [env] if env else list(setting("remote.hub_urls", []) or [])
    out = []
    for u in urls:
        u = str(u).strip().rstrip("/")
        if not u:
            continue
        if "://" not in u:
            u = "http://" + u
        if not re.search(r":\d+$", urllib.parse.urlsplit(u).netloc):
            u += f":{int(setting('remote.port', 8765))}"
        out.append(u)
    return out


def hub_token() -> str:
    return str(os.environ.get("LAB_CONTROL_HUB_TOKEN") or setting("remote.token", "") or "").strip()


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 不走系統 proxy


# ---------------------------------------------------------------------------
class Hub:
    def __init__(self, url: str, token: str = "", timeout: float = 10.0) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"Hub({self.url})"

    # ---- HTTP ------------------------------------------------------------
    def _req(self, method: str, path: str, body: Any = None, params: Optional[Dict[str, Any]] = None,
             timeout: Optional[float] = None, raw: Optional[bytes] = None, ctype: str = "application/json") -> Any:
        url = self.url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = raw if raw is not None else (dumps(body) if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", ctype)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with _OPENER.open(req, timeout=timeout or self.timeout) as r:
                payload = r.read()
                if r.headers.get_content_type() == "application/json":
                    return json.loads(payload.decode("utf-8"))
                return payload
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8")).get("error") or str(e)
            except Exception:  # noqa: BLE001
                msg = str(e)
            if e.code == 409:
                raise HubConflict(msg) from None
            if e.code == 401:
                raise HubError(f"Hub 拒絕存取：{msg}") from None
            raise HubError(f"Hub {e.code}：{msg}") from None
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise HubError(f"連不到 Hub {self.url}：{reason}") from None

    def get(self, path: str, **params: Any) -> Any:
        timeout = float(params.get("wait") or 0) + self.timeout
        return self._req("GET", path, params=params or None, timeout=timeout)

    def post(self, path: str, body: Any = None, **params: Any) -> Any:
        timeout = float(params.get("wait") or 0) + self.timeout
        return self._req("POST", path, body if body is not None else {}, params=params or None, timeout=timeout)

    @staticmethod
    def _n(name: str) -> str:
        return "/api/nodes/" + urllib.parse.quote(safe_name(name))

    # ---- 總覽 ------------------------------------------------------------
    def ping(self) -> Dict[str, Any]:
        return self._req("GET", "/api/ping", timeout=min(self.timeout, 4))

    def state(self) -> Dict[str, Any]:
        return self.get("/api/state")

    def nodes(self) -> List[Dict[str, Any]]:
        return list(self.state().get("nodes") or [])

    def node_status(self, name: str) -> Optional[Dict[str, Any]]:
        try:
            return self.get(self._n(name))
        except HubError as e:
            if "404" in str(e):
                return None
            raise

    def heartbeat(self, info: Dict[str, Any]) -> Dict[str, Any]:
        return self.post("/api/devices/heartbeat", info)

    @property
    def dashboard_url(self) -> str:
        return self.url + "/"

    # ---- 節點端 ------------------------------------------------------------
    def claim(self, name: str, info: Dict[str, Any]) -> Dict[str, Any]:
        return self.post(self._n(name) + "/claim", info)

    def put_status(self, name: str, st: Dict[str, Any]) -> Dict[str, Any]:
        """回報節點狀態；回覆含 owners（共用儀器歸屬：key → 電腦名稱）。"""
        return self.post(self._n(name) + "/status", st) or {}

    def offline(self, name: str) -> None:
        self.post(self._n(name) + "/offline")

    def put_instruments(self, name: str, text: str) -> None:
        self._req("PUT", self._n(name) + "/instruments", raw=text.encode("utf-8"), ctype="text/yaml; charset=utf-8")

    def instruments(self, name: str) -> str:
        r = self.get(self._n(name) + "/instruments")
        return r.decode("utf-8") if isinstance(r, bytes) else str(r)

    def take_commands(self, name: str, wait: float = 20.0) -> List[Dict[str, Any]]:
        return list(self.get(self._n(name) + "/commands", wait=wait).get("commands") or [])

    def reply(self, cid: str, data: Dict[str, Any]) -> None:
        self.post("/api/replies/" + urllib.parse.quote(cid), data)

    def write_head(self, name: str, head: Dict[str, Any]) -> Dict[str, Any]:
        return self.post(self._n(name) + "/live/head", head)

    def write_batch(self, name: str, events: List[Dict[str, Any]]) -> int:
        return int(self.post(self._n(name) + "/live/batch", {"events": events})["seq"])

    def upload(self, name: str, rel: str, path: Path) -> Dict[str, Any]:
        """上傳一個檔（串流，不整個讀進記憶體）。"""
        path = Path(path)
        url = f"{self.url}/api/files/{urllib.parse.quote(safe_name(name))}/" + \
            "/".join(urllib.parse.quote(p) for p in Path(rel).as_posix().split("/"))
        size = path.stat().st_size
        with open(path, "rb") as f:
            req = urllib.request.Request(url, data=f, method="PUT")
            req.add_header("Content-Length", str(size))
            req.add_header("Content-Type", "application/octet-stream")
            if self.token:
                req.add_header("Authorization", f"Bearer {self.token}")
            try:
                with _OPENER.open(req, timeout=max(60.0, size / 2e6)) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                raise HubError(f"上傳失敗 {e.code}：{e.read()[:200]!r}") from None
            except (urllib.error.URLError, OSError) as e:
                raise HubError(f"上傳失敗：{getattr(e, 'reason', e)}") from None

    def releases(self) -> List[str]:
        return list(self.get("/api/releases").get("versions") or [])

    # ---- 儀器登錄（哪台儀器掛在哪台電腦、共用儀器歸誰）----------------------------------
    def instruments_registry(self) -> List[Dict[str, Any]]:
        return list(self.get("/api/instruments").get("instruments") or [])

    def claim_instrument(self, key: str, host: str, by: str = "") -> Dict[str, Any]:
        """把（共用的）儀器拉到 host 的群組：其他電腦會中斷它。"""
        return self.post("/api/instruments/claim", {"key": key, "host": host, "by": by or user_id()})

    def release_instrument(self, key: str) -> Dict[str, Any]:
        return self.post("/api/instruments/release", {"key": key})

    # ---- 控制端 ------------------------------------------------------------
    def send(self, name: str, cmd: str, args: Optional[Dict[str, Any]] = None) -> str:
        from .. import __version__

        msg = {"cmd": cmd, "args": args or {}, "from": {"user": user_id(), "host": hostname(), "version": __version__}}
        return str(self.post(self._n(name) + "/commands", msg)["id"])

    def call(self, name: str, cmd: str, args: Optional[Dict[str, Any]] = None, timeout: float = 20.0) -> Dict[str, Any]:
        from .. import __version__

        msg = {"cmd": cmd, "args": args or {}, "from": {"user": user_id(), "host": hostname(), "version": __version__}}
        return dict(self.post(self._n(name) + "/commands", msg, wait=timeout).get("reply") or {})

    def live(self, name: str, after: int, run: Optional[str] = None, wait: float = 0.0) -> Dict[str, Any]:
        return self.get(self._n(name) + "/live", after=int(after), run=run, wait=wait)

    def files(self, node: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        return list(self.get("/api/files", node=node, limit=limit).get("files") or [])

    def download(self, url_path: str, dst: Path) -> Path:
        """下載 Hub 上的檔（url_path 例如 /api/files/QEL-PC/2026/…/a.hdf5）到 dst。"""
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(self.url + url_path)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:6]}.part")
        try:
            with _OPENER.open(req, timeout=120) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            os.replace(tmp, dst)
        except (urllib.error.URLError, OSError) as e:
            if tmp.exists():
                tmp.unlink()
            raise HubError(f"下載失敗 {url_path}：{getattr(e, 'reason', e)}") from None
        return dst


def find_hub() -> Hub:
    """依序嘗試 settings.yaml remote.hub_urls，回傳第一個連得到且 token 正確的 Hub。"""
    urls = hub_urls()
    if not urls:
        raise HubError("沒有設定 Hub 網址（settings.yaml remote.hub_urls）")
    token = hub_token()
    errors = []
    for u in urls:
        h = Hub(u, token, timeout=float(setting("remote.timeout_s", 10)))
        try:
            p = h.ping()
        except HubError as e:
            errors.append(str(e))
            continue
        if p.get("server") != "labhub":
            errors.append(f"{u} 不是 Lab Control Hub")
            continue
        if not p.get("auth"):
            errors.append(f"{u}：token 不正確或未設定（主視窗「設定 ▾ → Hub 連線設定…」）")
            continue
        return h
    msg = "找不到 Lab Control Hub：" + "；".join(errors)
    log.warning(msg)
    raise HubError(msg)
