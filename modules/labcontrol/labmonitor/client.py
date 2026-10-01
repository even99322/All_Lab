"""Lab Control Hub 的 HTTP client（標準函式庫 urllib；略過系統 proxy）。"""
from __future__ import annotations

import io
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class HubError(RuntimeError):
    pass


def normalize_url(u: str, port: int = 8765) -> str:
    u = str(u).strip().rstrip("/")
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    if not re.search(r":\d+$", urllib.parse.urlsplit(u).netloc):
        u += f":{port}"
    return u


def me() -> str:
    import getpass

    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return f"{user}@{socket.gethostname()}（Monitor）"


class HubClient:
    def __init__(self, url: str, token: str = "", timeout: float = 15.0) -> None:
        self.url = normalize_url(url)
        self.token = token
        self.timeout = timeout

    def _req(self, method: str, path: str, body: Any = None, raw: Optional[bytes] = None,
             timeout: Optional[float] = None, ctype: str = "application/json") -> Any:
        data = raw if raw is not None else (json.dumps(body, ensure_ascii=False).encode("utf-8")
                                            if body is not None else None)
        req = urllib.request.Request(self.url + path, data=data, method=method)
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
            raise HubError(f"{e.code}：{msg}") from None
        except (urllib.error.URLError, OSError) as e:
            raise HubError(f"連不到 Hub {self.url}：{getattr(e, 'reason', e)}") from None

    # ---- 讀 ----------------------------------------------------------------
    def ping(self) -> Dict[str, Any]:
        return self._req("GET", "/api/ping", timeout=5)

    def state(self) -> Dict[str, Any]:
        return self._req("GET", "/api/state")

    def instruments(self) -> List[Dict[str, Any]]:
        return list(self._req("GET", "/api/instruments").get("instruments") or [])

    def hub_info(self) -> Dict[str, Any]:
        return self._req("GET", "/api/hub")

    def files(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(self._req("GET", f"/api/files?limit={limit}").get("files") or [])

    # ---- 指令 ----------------------------------------------------------------
    def command(self, node: str, cmd: str, args: Optional[Dict[str, Any]] = None, wait: float = 30.0) -> Any:
        """送指令到節點並等回覆；失敗丟 HubError。"""
        body = {"cmd": cmd, "args": args or {}, "from": {"user": me()}}
        r = self._req("POST", f"/api/nodes/{urllib.parse.quote(node)}/commands?wait={wait:g}", body,
                      timeout=wait + self.timeout)
        rep = r.get("reply") or {}
        if not rep.get("ok"):
            raise HubError(rep.get("error") or f"{cmd} 失敗")
        return rep.get("result")

    def claim(self, key: str, host: str) -> Dict[str, Any]:
        return self._req("POST", "/api/instruments/claim", {"key": key, "host": host, "by": me()})

    def release(self, key: str) -> Dict[str, Any]:
        return self._req("POST", "/api/instruments/release", {"key": key})

    # ---- Hub 更新 ----------------------------------------------------------------
    def hub_update(self, version: Optional[str] = None) -> Dict[str, Any]:
        return self._req("POST", "/api/hub/update", {"version": version} if version else {})

    def hub_install_dir(self, pkg_dir: Path) -> Dict[str, Any]:
        return self._req("POST", "/api/hub/install", raw=zip_package(pkg_dir), ctype="application/zip", timeout=120)

    def hub_restart(self) -> Dict[str, Any]:
        return self._req("POST", "/api/hub/restart", {})


def zip_package(pkg_dir: Path) -> bytes:
    """把 labhub 資料夾打包成 zip（內容 labhub/...）。"""
    pkg_dir = Path(pkg_dir)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pkg_dir.rglob("*")):
            rel = p.relative_to(pkg_dir)
            if p.is_file() and "__pycache__" not in rel.parts and p.suffix != ".pyc":
                z.write(p, ("labhub" / rel).as_posix())
    return buf.getvalue()


def bundled_hub() -> Optional[Path]:
    """這個程式內附的 labhub 原始碼（exe：_MEIPASS/labhub_src；原始碼執行：repo 的 labhub/）。"""
    import sys

    base = getattr(sys, "_MEIPASS", None)
    if base and (Path(base) / "labhub_src" / "server.py").exists():
        return Path(base) / "labhub_src"
    p = Path(__file__).resolve().parents[1] / "labhub"
    return p if (p / "server.py").exists() else None


def package_version(pkg_dir: Optional[Path]) -> Optional[str]:
    if pkg_dir is None:
        return None
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)', (Path(pkg_dir) / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1) if m else None


def parse_version(v: str):
    m = re.match(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?([a-z]*)", str(v or ""), re.I)
    if not m:
        return (0, 0, 0, "")
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0), (m.group(4) or "").lower())


def find(urls: List[str], token: str) -> HubClient:
    errors = []
    for u in urls:
        c = HubClient(u, token)
        try:
            p = c.ping()
        except HubError as e:
            errors.append(str(e))
            continue
        if p.get("server") != "labhub":
            errors.append(f"{c.url} 不是 Lab Control Hub")
        elif not p.get("auth"):
            errors.append(f"{c.url}：token 不正確")
        else:
            return c
    raise HubError("；".join(errors) or "沒有設定 Hub 網址")


class AgentClient(HubClient):
    """Hub 控制代理（NAS 上的 labcontrol-hub-agent，預設 port 8766）。"""

    def __init__(self, url: str, token: str = "", timeout: float = 15.0) -> None:
        super().__init__(normalize_url(url, 8766), token, timeout)

    def status(self) -> Dict[str, Any]:
        return self._req("GET", "/api/agent")

    def submit(self, action: str, data: Optional[bytes] = None, backup: str = "", by: str = "") -> Dict[str, Any]:
        q = urllib.parse.urlencode({"action": action, "backup": backup, "by": by or me()})
        if data is not None:
            return self._req("POST", f"/api/agent/jobs?{q}", raw=data, ctype="application/zip", timeout=180)
        return self._req("POST", f"/api/agent/jobs?{q}", {})

    def job(self, jid: str, after: int = 0, wait: float = 20.0) -> Dict[str, Any]:
        return self._req("GET", f"/api/agent/jobs/{jid}?after={after}&wait={wait:g}", timeout=wait + self.timeout)


def agent_url_for(hub_url: str, port: int = 8766) -> str:
    """Hub 網址 → 同一台主機的控制代理網址。"""
    u = urllib.parse.urlsplit(normalize_url(hub_url))
    return f"{u.scheme}://{u.hostname}:{port}"
