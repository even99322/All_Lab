"""更新代理的 HTTP API（port 8767）。

權限：``Authorization: Bearer <token>``，兩種 token 都可以：
* 大程式的登入 token（站長）：代理會問大程式 ``/api/v1/me`` 確認是站長；
* 緊急 token ``QEL_AGENT_TOKEN``：大程式本身壞掉、需要靠代理救回來時用（寫在 docker-compose.yml）。

API::

    GET  /api/ping                                   {server, version}（不需要 token）
    GET  /api/agent                                  所有服務的容器狀態、版本、備份、進行中的工作
    GET  /api/agent/services/<id>/logs?tail=200      容器日誌
    POST /api/agent/services/<id>/jobs?action=…      開始工作（update 的 body 是 zip；rollback 加 &backup=檔名）
    GET  /api/agent/jobs/<jid>?after=N&wait=s        工作進度（long-poll）
"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import DEFAULT_PORT, __version__
from .agent import Agent, AgentError, load_services
from .docker import Docker

log = logging.getLogger("qelagent")
MAX_ZIP = 1024 << 20


class Auth:
    def __init__(self, emergency: str, portal_url: str, cache_s: float = 60.0) -> None:
        self.emergency = emergency
        self.portal_url = portal_url.rstrip("/")
        self.cache_s = cache_s
        self._cache: Dict[str, Tuple[float, Optional[str]]] = {}
        self._lock = threading.Lock()

    def check(self, token: str) -> Optional[str]:
        """回傳操作者名稱；沒有權限回傳 None。"""
        if not token:
            return None
        if self.emergency and hmac.compare_digest(token.encode(), self.emergency.encode()):
            return "緊急 token"
        if not self.portal_url:
            return None
        now = time.time()
        with self._lock:
            hit = self._cache.get(token)
            if hit and now - hit[0] < self.cache_s:
                return hit[1]
        who: Optional[str] = None
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(self.portal_url + "/api/v1/me", headers={"Authorization": f"Bearer {token}"})
            with opener.open(req, timeout=8) as r:
                me = json.loads(r.read().decode("utf-8"))
            if me.get("manager"):
                who = f"{me.get('display_name') or me.get('username')}（{me.get('username')}）"
        except (urllib.error.URLError, OSError, ValueError):
            who = None
        with self._lock:
            self._cache[token] = (now, who)
            if len(self._cache) > 500:
                self._cache.clear()
        return who


class Handler(BaseHTTPRequestHandler):
    server_version = f"QELAgent/{__version__}"
    agent: Agent = None  # type: ignore[assignment]
    auth: Auth = None  # type: ignore[assignment]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
        log.debug("%s - %s", self.client_address[0], fmt % args)

    def _json(self, data: Any, code: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _who(self) -> str:
        a = self.headers.get("Authorization", "")
        tok = a[7:].strip() if a.lower().startswith("bearer ") else ""
        who = self.auth.check(tok)
        if who is None:
            raise AgentError(401, "需要站長登入（或緊急 token）")
        return who

    def _route(self, method: str) -> None:
        sp = urllib.parse.urlsplit(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(sp.query).items()}
        path = sp.path
        try:
            if method == "GET" and path == "/api/ping":
                return self._json({"ok": True, "server": "qel-agent", "version": __version__, "time": time.time()})
            who = self._who()
            if method == "GET" and path == "/api/agent":
                return self._json(self.agent.status())
            m = re.fullmatch(r"/api/agent/services/([a-z0-9_-]+)/logs", path)
            if method == "GET" and m:
                return self._json({"service": m.group(1), "text": self.agent.logs(m.group(1), int(q.get("tail", 200)))})
            m = re.fullmatch(r"/api/agent/services/([a-z0-9_-]+)/jobs", path)
            if method == "POST" and m:
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_ZIP:
                    raise AgentError(413, "zip 太大")
                data = self.rfile.read(n) if n else None
                job = self.agent.submit(m.group(1), q.get("action", ""), who, data, q.get("backup", ""))
                return self._json(job.view())
            m = re.fullmatch(r"/api/agent/jobs/([0-9a-f]+)", path)
            if method == "GET" and m:
                job = self.agent.job(m.group(1))
                after = int(q.get("after", 0))
                wait = min(30.0, float(q.get("wait", 0)))
                if wait:
                    job.wait(after, wait)
                return self._json(job.view(after))
            raise AgentError(404, f"找不到 {method} {path}")
        except AgentError as e:
            self._json({"ok": False, "error": str(e)}, e.code)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            log.exception("處理 %s %s 失敗", method, path)
            try:
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            except OSError:
                pass

    def do_GET(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")


def make_server(agent: Agent, auth: Auth, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    h = type("AgentHandler", (Handler,), {"agent": agent, "auth": auth})
    srv = ThreadingHTTPServer((host, port), h)
    srv.daemon_threads = True
    return srv


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="qelagent", description="QEL Lab 更新代理")
    ap.add_argument("--port", type=int, default=int(os.environ.get("QEL_AGENT_PORT", DEFAULT_PORT)))
    args = ap.parse_args(argv)
    logging.basicConfig(level=os.environ.get("QEL_LOG", "INFO"), format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    stack = Path(os.environ.get("QEL_STACK_DIR", "/stack"))
    sfile = os.environ.get("QEL_AGENT_SERVICES")
    services = load_services(stack, Path(sfile) if sfile else None)
    agent = Agent(services, Docker(os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")),
                  Path(os.environ.get("QEL_BACKUP_DIR", str(stack / "backups"))),
                  verify_s=float(os.environ.get("QEL_AGENT_VERIFY_S", 90)))
    auth = Auth(os.environ.get("QEL_AGENT_TOKEN", ""), os.environ.get("QEL_PORTAL_URL", "http://qel-portal:8090"))
    if not auth.emergency:
        log.warning("沒有設定 QEL_AGENT_TOKEN：大程式網站壞掉時無法用緊急 token 登入代理")
    srv = make_server(agent, auth, port=args.port)
    log.info("QEL Lab 更新代理 v%s：port %d，管理 %s", __version__, args.port, "、".join(s.id for s in services))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
