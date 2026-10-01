"""同一台電腦上模塊之間的直接傳遞（不經網路、不需要登入）。

每個開著的模塊開一個只聽 127.0.0.1 的小埠，把埠號和一次性的密語寫到
``<QEL_HOME>/run/<模塊>.json``（只有本人可讀）。其他模塊讀這個檔就能送動作過去：

    # 量測模塊
    ep = local.LocalEndpoint("labcontrol", "0.0.13", handler)
    ep.start()
    # 讀檔模塊
    local.send("labcontrol", actions.APPLY_SCHEME, {"path": "D:/data/x.hdf5"})

一次連線一個請求：送一行 JSON ``{"secret","action","payload","from"}``，回一行 JSON
``{"ok": true, "result": ...}`` 或 ``{"ok": false, "error": "..."}``。

handler 在背景執行緒被呼叫；有畫面的模塊應該在 handler 裡發 Qt signal 交給主執行緒，
然後立刻回傳（例如 ``{"accepted": True}``）。
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import qel_home
from .errors import CommError

Handler = Callable[[str, Dict[str, Any]], Any]
MAX_LINE = 4 * 1024 * 1024


def run_dir() -> Path:
    return qel_home() / "run"


def _info_path(module_id: str) -> Path:
    safe = "".join(c for c in module_id if c.isalnum() or c in "-_") or "module"
    return run_dir() / f"{safe}.json"


def _read_line(sock: socket.socket) -> bytes:
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > MAX_LINE:
            raise CommError("訊息太大")
    return buf.split(b"\n", 1)[0]


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    endpoint: "LocalEndpoint"


class _RequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        ep: LocalEndpoint = self.server.endpoint  # type: ignore[attr-defined]
        self.request.settimeout(30)
        try:
            msg = json.loads(_read_line(self.request).decode("utf-8") or "{}")
            if not hmac.compare_digest(str(msg.get("secret", "")).encode(), ep.secret.encode()):
                reply: Dict[str, Any] = {"ok": False, "error": "密語不正確"}
            else:
                action = str(msg.get("action") or "")
                payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
                if action == "ping":
                    reply = {"ok": True, "result": {"module": ep.module_id, "version": ep.version, "pid": os.getpid()}}
                elif ep.actions and action not in ep.actions:
                    reply = {"ok": False, "error": f"{ep.module_id} 不支援動作 {action}"}
                else:
                    reply = {"ok": True, "result": ep.handler(action, payload)}
        except Exception as e:  # noqa: BLE001
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        try:
            self.request.sendall(json.dumps(reply, ensure_ascii=False, default=str).encode("utf-8") + b"\n")
        except OSError:
            pass


class LocalEndpoint:
    def __init__(self, module_id: str, version: str, handler: Handler, actions: Optional[List[str]] = None) -> None:
        self.module_id = module_id
        self.version = version
        self.handler = handler
        self.actions = list(actions or [])
        self.secret = secrets.token_urlsafe(24)
        self._server: Optional[_Server] = None
        self._thread: Optional[threading.Thread] = None
        self.port = 0

    def start(self) -> "LocalEndpoint":
        srv = _Server(("127.0.0.1", 0), _RequestHandler)
        srv.endpoint = self
        self._server = srv
        self.port = srv.server_address[1]
        self._thread = threading.Thread(target=srv.serve_forever, name=f"labcomm-local-{self.module_id}",
                                        daemon=True)
        self._thread.start()
        p = _info_path(self.module_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps({"module": self.module_id, "version": self.version, "port": self.port,
                           "pid": os.getpid(), "secret": self.secret, "actions": self.actions,
                           "started": time.time()}, ensure_ascii=False)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        if sys.platform != "win32":
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        p = _info_path(self.module_id)
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("pid") == os.getpid():
                p.unlink()
        except (OSError, ValueError):
            pass


def endpoint_info(module_id: str) -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(_info_path(module_id).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("port") else None
    except (OSError, ValueError):
        return None


def send(module_id: str, action: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 10.0,
         sender: str = "") -> Any:
    """送動作給本機開著的模塊；模塊沒開時丟 CommError。"""
    info = endpoint_info(module_id)
    if info is None:
        raise CommError(f"{module_id} 沒有開著")
    msg = {"secret": info.get("secret", ""), "action": action, "payload": payload or {}, "from": sender}
    try:
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=timeout) as s:
            s.sendall(json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n")
            reply = json.loads(_read_line(s).decode("utf-8") or "{}")
    except (OSError, ValueError) as e:
        raise CommError(f"{module_id} 沒有回應（可能已經關閉）：{e}") from None
    if not reply.get("ok"):
        raise CommError(str(reply.get("error") or "失敗"))
    return reply.get("result")


def deliver(module_id: str, action: str, payload: Optional[Dict[str, Any]] = None, sender: str = "") -> str:
    """把動作交給某個模塊：開著就直接送；沒開就請大程式（launcher）開啟它再轉過去。

    回傳 ``"sent"`` 或 ``"launching"``；兩者都不行時丟 CommError（訊息可直接顯示）。
    """
    try:
        send(module_id, action, payload, sender=sender)
        return "sent"
    except CommError as e:
        if endpoint_info(module_id) is not None and "沒有回應" not in str(e):
            raise                                    # 模塊開著但拒絕（例如不支援、檔案不對）
    try:
        send("launcher", "launch", {"module": module_id, "action": action, "payload": payload or {}},
             timeout=15, sender=sender)
        return "launching"
    except CommError:
        raise CommError(f"{module_id} 沒有開著，也連不到 QEL Lab 大程式；請先從大程式開啟 {module_id}") from None


def is_running(module_id: str, timeout: float = 1.5) -> bool:
    try:
        send(module_id, "ping", timeout=timeout)
        return True
    except CommError:
        return False


def running_modules() -> List[Dict[str, Any]]:
    out = []
    d = run_dir()
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json")):
        info = endpoint_info(p.stem)
        if info and is_running(p.stem):
            out.append({k: v for k, v in info.items() if k != "secret"})
    return out
