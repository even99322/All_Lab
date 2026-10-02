"""監控程式的核心（沒有畫面，方便測試）：連大程式與更新代理、整理狀態、判斷異常、更新服務、發佈模塊。"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from labcomm import PortalClient
from labcomm.config import split_urls
from labcomm.errors import CommError, NotLoggedIn, Unreachable

SERVICE_NAMES = {"portal": "大程式網站", "paperlib": "論文庫", "labhub": "量測中繼站", "agent": "更新代理"}


def config_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "QELMonitor"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "QELMonitor"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "qel-monitor"


@dataclass
class MonitorConfig:
    portal_url: str = "http://192.168.50.2:8090, http://100.114.33.20:8090"
    agent_url: str = ""                 # 空白＝大程式網站同一台主機的 8767
    username: str = ""
    token: str = ""                     # 大程式登入 token（記住登入時）
    remember: bool = True
    refresh_s: int = 10
    notify: bool = True
    disk_warn_gb: float = 5.0
    error_warn_per_hour: int = 20

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "MonitorConfig":
        p = path or config_dir() / "config.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Optional[Path] = None) -> None:
        p = path or config_dir() / "config.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        d = asdict(self)
        if not self.remember:
            d["token"] = ""
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        os.replace(tmp, p)

    def agent_urls(self, portal_base: str = "") -> List[str]:
        if self.agent_url.strip():
            return split_urls(self.agent_url)
        bases = [portal_base] if portal_base else split_urls(self.portal_url)
        out = []
        for b in bases:
            u = urllib.parse.urlsplit(b)
            out.append(f"{u.scheme}://{u.hostname}:8767")
        return out


class AgentClient:
    """更新代理（NAS port 8767）。token：大程式的站長 token，或緊急 token。"""

    def __init__(self, urls: List[str], token: str, timeout: float = 15.0) -> None:
        self.urls = urls
        self.base = urls[0] if urls else ""
        self.token = token
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _req(self, method: str, path: str, data: Optional[bytes] = None, timeout: Optional[float] = None) -> Any:
        last: Optional[BaseException] = None
        for base in [self.base] + [u for u in self.urls if u != self.base]:
            req = urllib.request.Request(base + path, data=data, method=method,
                                         headers={"Authorization": f"Bearer {self.token}"} if self.token else {})
            try:
                with self._opener.open(req, timeout=timeout or self.timeout) as r:
                    self.base = base
                    return json.loads(r.read().decode("utf-8") or "null")
            except urllib.error.HTTPError as e:
                try:
                    msg = json.loads(e.read().decode("utf-8")).get("error") or e.reason
                except Exception:  # noqa: BLE001
                    msg = e.reason
                if e.code == 401:
                    raise NotLoggedIn(f"更新代理：{msg}", 401) from None
                raise CommError(f"更新代理：{msg}", e.code) from None
            except (urllib.error.URLError, OSError) as e:
                last = e
        raise Unreachable(f"連不到更新代理（{', '.join(self.urls)}）：{getattr(last, 'reason', last)}")

    def ping(self) -> Dict[str, Any]:
        return self._req("GET", "/api/ping", timeout=5)

    def status(self) -> Dict[str, Any]:
        return self._req("GET", "/api/agent")

    def logs(self, service: str, tail: int = 300) -> str:
        return self._req("GET", f"/api/agent/services/{service}/logs?tail={int(tail)}")["text"]

    def submit(self, service: str, action: str, zip_bytes: Optional[bytes] = None, backup: str = "") -> Dict[str, Any]:
        q = urllib.parse.urlencode({"action": action, "backup": backup})
        return self._req("POST", f"/api/agent/services/{service}/jobs?{q}", zip_bytes or b"", timeout=600)

    def job(self, job_id: str, after: int = 0, wait: float = 10) -> Dict[str, Any]:
        return self._req("GET", f"/api/agent/jobs/{job_id}?after={after}&wait={wait}", timeout=wait + 15)

    def follow(self, job_id: str, on_update: Callable[[Dict[str, Any]], None], timeout: float = 900) -> Dict[str, Any]:
        """一直讀到工作結束；代理自己重新啟動時會短暫連不到，繼續等。"""
        after, t0 = 0, time.time()
        lines: List[Dict[str, Any]] = []
        while time.time() - t0 < timeout:
            try:
                j = self.job(job_id, after, 10)
            except Unreachable:
                time.sleep(2)
                continue
            except CommError as e:
                if e.status == 404 and lines:           # 代理更新自己後重新啟動 → 工作紀錄消失
                    return {"id": job_id, "done": True, "ok": True, "result": "代理已重新啟動", "lines": lines}
                raise
            lines += j["lines"]
            after = j["n_lines"]
            j["all_lines"] = lines
            on_update(j)
            if j["done"]:
                return j
        raise CommError("等太久，工作還沒結束（可以稍後在監控程式查看）")


def read_module_zip(path: Path) -> Dict[str, Any]:
    """讀出模塊 zip 的 module.json（第一層或第二層）。"""
    with zipfile.ZipFile(path) as z:
        cands = sorted((n for n in z.namelist() if n.rsplit("/", 1)[-1] == "module.json" and n.count("/") <= 1),
                       key=lambda n: n.count("/"))
        if not cands:
            raise ValueError("zip 裡找不到 module.json")
        d = json.loads(z.read(cands[0]).decode("utf-8-sig"))
    if not d.get("id") or not d.get("version"):
        raise ValueError("module.json 需要 id 與 version")
    return d


@dataclass
class Alert:
    level: str          # warn | bad
    service: str
    message: str

    def key(self) -> str:
        return f"{self.level}:{self.service}:{self.message}"


@dataclass
class Snapshot:
    time: float
    health: Optional[Dict[str, Any]] = None
    health_error: str = ""
    agent: Optional[Dict[str, Any]] = None
    agent_error: str = ""
    alerts: List[Alert] = field(default_factory=list)

    def service_rows(self) -> List[Dict[str, Any]]:
        """總覽卡片：每個服務一列（合併網站健康檢查與代理的容器狀態）。"""
        h = self.health or {}
        ag = {s["id"]: s for s in (self.agent or {}).get("services", [])}
        rows = []
        for sid in ("portal", "paperlib", "labhub", "agent"):
            hs = h.get(sid) or {}
            a = ag.get(sid) or {}
            online = hs.get("online") if hs else (a.get("online") if a else None)
            if sid == "portal":
                online = self.health is not None
            if sid == "agent":
                online = self.agent is not None
            rows.append({"id": sid, "name": SERVICE_NAMES[sid], "online": online,
                         "version": hs.get("version") or a.get("running_version") or a.get("code_version"),
                         "code_version": a.get("code_version"), "ms": hs.get("ms"),
                         "container": (a.get("container") or {}).get("status"),
                         "error": hs.get("error") or (a.get("container") or {}).get("error") or "",
                         "backups": a.get("backups") or [], "job": a.get("job")})
        return rows


class Monitor:
    def __init__(self, cfg: MonitorConfig, emergency_token: str = "") -> None:
        self.cfg = cfg
        self.portal = PortalClient(cfg.portal_url, cfg.token, timeout=15, client_name="qel-monitor")
        self.emergency = emergency_token
        self.agent = AgentClient(cfg.agent_urls(), emergency_token or cfg.token)
        self.user: Optional[Dict[str, Any]] = None

    # ---- 登入 ------------------------------------------------------------------
    def login(self, username: str, password: str, code: str = "") -> Dict[str, Any]:
        r = self.portal.login(username, password, code)
        if r.get("need_2fa"):
            return r
        me = self.portal.me()
        if not me.get("manager"):
            self.portal.logout()
            raise CommError("監控程式只給站長使用")
        self.user = me
        self.cfg.username = username
        self.cfg.token = self.portal.token
        self.agent = AgentClient(self.cfg.agent_urls(self.portal.base), self.portal.token)
        return {"ok": True, "user": me}

    def resume(self) -> bool:
        """用記住的 token 繼續登入。"""
        if not self.cfg.token:
            return False
        try:
            me = self.portal.me()
        except CommError:
            return False
        if not me.get("manager"):
            return False
        self.user = me
        self.agent = AgentClient(self.cfg.agent_urls(self.portal.base), self.portal.token)
        return True

    def use_emergency(self, token: str) -> None:
        """大程式網站壞掉：只連更新代理。"""
        self.emergency = token
        self.agent = AgentClient(self.cfg.agent_urls(), token)
        self.agent.status()

    def logout(self) -> None:
        try:
            self.portal.logout()
        except CommError:
            pass
        self.user = None
        self.cfg.token = ""

    # ---- 狀態 ------------------------------------------------------------------
    def snapshot(self) -> Snapshot:
        s = Snapshot(time=time.time())
        if self.user is not None:
            try:
                s.health = self.portal.health()
            except CommError as e:
                s.health_error = str(e)
        else:
            s.health_error = "沒有登入大程式（緊急模式）" if self.emergency else "沒有登入"
        try:
            s.agent = self.agent.status()
        except CommError as e:
            s.agent_error = str(e)
        s.alerts = self.alerts(s)
        return s

    def alerts(self, s: Snapshot) -> List[Alert]:
        out: List[Alert] = []
        if s.health is None and self.user is not None:
            out.append(Alert("bad", "portal", f"大程式網站沒有回應：{s.health_error}"))
        h = s.health or {}
        for sid in ("paperlib", "labhub"):
            if h and not (h.get(sid) or {}).get("online"):
                out.append(Alert("bad", sid, f"{SERVICE_NAMES[sid]}離線：{(h.get(sid) or {}).get('error') or ''}".rstrip("：")))
        if s.agent is None:
            out.append(Alert("warn", "agent", s.agent_error or "連不到更新代理"))
        for a in (s.agent or {}).get("services", []):
            c = a.get("container") or {}
            if c.get("status") not in (None, "running", "unknown") and not a.get("self"):
                out.append(Alert("bad", a["id"], f"{a['name']}的容器狀態：{c.get('status')}"))
            if a.get("code_version") and a.get("running_version") and a["code_version"] != a["running_version"] \
                    and not a.get("self"):
                out.append(Alert("warn", a["id"], f"{a['name']}的程式是 v{a['code_version']}，但執行中的是 "
                                                  f"v{a['running_version']}（需要重新啟動）"))
        p = h.get("portal") or {}
        if p.get("disk_free_gb") is not None and p["disk_free_gb"] < self.cfg.disk_warn_gb:
            out.append(Alert("warn", "portal", f"NAS 剩餘空間只有 {p['disk_free_gb']} GB"))
        t = h.get("traffic") or {}
        if t.get("errors", 0) >= self.cfg.error_warn_per_hour:
            out.append(Alert("warn", "portal", f"最近一小時有 {t['errors']} 個伺服器錯誤"))
        return out

    # ---- 動作 ------------------------------------------------------------------
    def update_service(self, service: str, zip_path: Path, on_update: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
        job = self.agent.submit(service, "update", Path(zip_path).read_bytes())
        on_update(job)
        return self.agent.follow(job["id"], on_update)

    def service_action(self, service: str, action: str, on_update: Callable[[Dict[str, Any]], None],
                       backup: str = "") -> Dict[str, Any]:
        job = self.agent.submit(service, action, backup=backup)
        on_update(job)
        return self.agent.follow(job["id"], on_update)

    def publish_module(self, zip_path: Path, notes: str = "") -> Dict[str, Any]:
        man = read_module_zip(Path(zip_path))
        return self.portal.publish_release(man["id"], str(man["version"]), Path(zip_path).read_bytes(), notes)

    def open_portal_url(self, next_path: str = "/#/admin") -> str:
        """一次性登入網址：瀏覽器打開就是登入狀態（不會把 token 放在網址上）。"""
        t = self.portal.api("POST", "/sso/ticket")
        return f"{self.portal.base}{t['url']}&next={urllib.parse.quote(next_path, safe='')}"
