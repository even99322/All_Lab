"""極小的 HTTP 框架（標準函式庫）：路由、JSON、cookie、檔案串流、錯誤處理。"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import urllib.parse
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("qelportal")

MAX_JSON = 8 * 1024 * 1024
TRUST_PROXY = os.environ.get("QEL_TRUST_PROXY", "1") == "1"


class HTTPError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class Response:
    def __init__(self, body: Any = b"", status: int = 200, ctype: str = "application/octet-stream",
                 headers: Optional[List[Tuple[str, str]]] = None, file: Optional[Path] = None,
                 stream: Optional[Iterable[bytes]] = None, length: Optional[int] = None) -> None:
        self.body = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.status, self.ctype = status, ctype
        self.headers: List[Tuple[str, str]] = list(headers or [])
        self.file, self.stream, self.length = file, stream, length

    @classmethod
    def json(cls, data: Any, status: int = 200) -> "Response":
        return cls(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"), status,
                   "application/json; charset=utf-8", [("Cache-Control", "no-store")])

    @classmethod
    def html(cls, text: str, status: int = 200) -> "Response":
        return cls(text.encode("utf-8"), status, "text/html; charset=utf-8", [("Cache-Control", "no-cache")])

    @classmethod
    def redirect(cls, url: str, headers: Optional[List[Tuple[str, str]]] = None) -> "Response":
        return cls(b"", 302, "text/plain", [("Location", url), *(headers or [])])

    @classmethod
    def download(cls, path: Path, name: Optional[str] = None, ctype: str = "application/octet-stream") -> "Response":
        fn = urllib.parse.quote(name or path.name)
        return cls(status=200, ctype=ctype, file=path,
                   headers=[("Content-Disposition", f"attachment; filename*=UTF-8''{fn}")])

    def cookie(self, name: str, value: str, max_age: int, domain: str = "", secure: bool = False,
               http_only: bool = True) -> "Response":
        parts = [f"{name}={urllib.parse.quote(value)}", "Path=/", f"Max-Age={max_age}", "SameSite=Lax"]
        if http_only:
            parts.append("HttpOnly")
        if domain:
            parts.append(f"Domain={domain}")
        if secure:
            parts.append("Secure")
        self.headers.append(("Set-Cookie", "; ".join(parts)))
        return self


class Request:
    def __init__(self, h: "BaseHandler", method: str) -> None:
        self.h = h
        self.method = method
        sp = urllib.parse.urlsplit(h.path)
        self.path = sp.path
        self.raw_query = sp.query
        self.query: Dict[str, str] = {k: v[-1] for k, v in urllib.parse.parse_qs(sp.query, keep_blank_values=True).items()}
        self.headers = h.headers
        c = cookies.SimpleCookie()
        try:
            c.load(h.headers.get("Cookie", ""))
        except cookies.CookieError:
            pass
        self.cookies = {k: urllib.parse.unquote(m.value) for k, m in c.items()}
        peer = h.client_address[0] if h.client_address else ""
        xff = h.headers.get("X-Forwarded-For", "")
        self.ip = xff.split(",")[0].strip() if (TRUST_PROXY and xff) else peer
        self.https = h.headers.get("X-Forwarded-Proto", "").lower() == "https"
        self._body: Optional[bytes] = None
        self.session: Optional[Dict[str, Any]] = None       # 由 auth 填入
        self.started = time.time()

    @property
    def length(self) -> int:
        try:
            return int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return 0

    def body(self, limit: int = MAX_JSON) -> bytes:
        if self._body is None:
            n = self.length
            if n > limit:
                raise HTTPError(413, "資料太大")
            self._body = self.h.rfile.read(n) if n else b""
        return self._body

    def save_body(self, dest: Path, limit: int) -> int:
        """大檔案（模塊發佈 zip）直接串流寫到檔案，不放進記憶體。"""
        n = self.length
        if n <= 0:
            raise HTTPError(411, "需要 Content-Length")
        if n > limit:
            raise HTTPError(413, f"檔案太大（上限 {limit >> 20} MB）")
        self.consumed = True
        left = n
        with open(dest, "wb") as f:
            while left:
                chunk = self.h.rfile.read(min(1 << 20, left))
                if not chunk:
                    raise HTTPError(400, "上傳中斷")
                f.write(chunk)
                left -= len(chunk)
        return n

    def json(self) -> Any:
        raw = self.body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise HTTPError(400, "JSON 格式錯誤") from None

    def qint(self, name: str, default: int, lo: int = 0, hi: int = 10 ** 9) -> int:
        try:
            return max(lo, min(hi, int(float(self.query.get(name, default)))))
        except ValueError:
            return default

    def qfloat(self, name: str, default: float, lo: float = 0.0, hi: float = 1e9) -> float:
        try:
            return max(lo, min(hi, float(self.query.get(name, default))))
        except ValueError:
            return default

    @property
    def host_base(self) -> str:
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "localhost"
        return ("https" if self.https else "http") + "://" + host.split(",")[0].strip()


Route = Tuple[str, "re.Pattern[str]", Callable[..., Any], Dict[str, Any]]


class BaseHandler(BaseHTTPRequestHandler):
    routes: List[Route] = []
    server_version = "QELPortal"
    protocol_version = "HTTP/1.1"
    app: Any = None                      # Portal（由 server 設定）

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
        log.debug("%s - %s", self.client_address[0], fmt % args)

    def _send(self, r: Response, req: Optional[Request]) -> None:
        self.send_response(r.status)
        self.send_header("Content-Type", r.ctype)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if r.ctype.startswith("text/html"):
            self.send_header("X-Frame-Options", "SAMEORIGIN")
        for k, v in r.headers:
            self.send_header(k, v)
        if r.file is not None:
            size = r.file.stat().st_size
            self.send_header("Content-Length", str(size))
            self.end_headers()
            if self.command != "HEAD":
                with open(r.file, "rb") as f:
                    shutil.copyfileobj(f, self.wfile, 1 << 20)
        elif r.stream is not None:
            if r.length is not None:
                self.send_header("Content-Length", str(r.length))
                self.end_headers()
                for chunk in r.stream:
                    self.wfile.write(chunk)
            else:
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for chunk in r.stream:
                    if chunk:
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
        else:
            self.send_header("Content-Length", str(len(r.body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(r.body)

    def _dispatch(self, method: str) -> None:
        req = Request(self, method)
        status = 500
        try:
            r = self.app.handle(req, self.routes)
            if not isinstance(r, Response):
                r = Response.json(r if r is not None else {"ok": True})
            status = r.status
            # 沒讀完的 body 要讀掉，否則 keep-alive 的下一個請求會錯位
            if req._body is None and req.length and req.length < 64 * 1024 * 1024 and not getattr(req, "consumed", False):
                try:
                    req.body(limit=64 * 1024 * 1024)
                except HTTPError:
                    self.close_connection = True
            self._send(r, req)
        except HTTPError as e:
            status = e.code
            self.close_connection = True
            self._safe_send(Response.json({"ok": False, "error": str(e)}, e.code), req)
        except (BrokenPipeError, ConnectionResetError):
            status = 499
        except Exception as e:  # noqa: BLE001
            log.exception("處理 %s %s 失敗", method, req.path)
            self.close_connection = True
            self._safe_send(Response.json({"ok": False, "error": f"伺服器錯誤：{type(e).__name__}: {e}"}, 500), req)
        finally:
            try:
                self.app.record(req, status)
            except Exception:  # noqa: BLE001
                pass

    def _safe_send(self, r: Response, req: Request) -> None:
        try:
            self._send(r, req)
        except OSError:
            pass

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


def make_router() -> Tuple[List[Route], Callable[..., Callable]]:
    routes: List[Route] = []

    def route(method: str, pattern: str, **opts: Any) -> Callable:
        def deco(fn: Callable) -> Callable:
            routes.append((method, re.compile(pattern), fn, opts))
            return fn
        return deco
    return routes, route
