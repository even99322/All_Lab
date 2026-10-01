"""啟動大程式伺服器：``python -m qelportal``（docker-compose.yml 的 portal 服務）。"""
from __future__ import annotations

import argparse
import logging
import mimetypes
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

from . import __version__
from . import api
from .app import Portal
from .config import PortalConfig
from .httpd import BaseHandler, HTTPError, Request, Response, Server

STATIC = Path(__file__).resolve().parent / "static"
log = logging.getLogger("qelportal")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")


def _static(req: Request) -> Response:
    path = req.path
    if path in ("/", "/index.html"):
        return Response.html((STATIC / "index.html").read_text(encoding="utf-8"))
    if path == "/sw.js" or path == "/manifest.webmanifest":
        p = STATIC / path.lstrip("/")
    elif path.startswith("/static/"):
        p = (STATIC / path[len("/static/"):]).resolve()
        if STATIC.resolve() not in p.parents:
            raise HTTPError(404, "找不到")
    else:
        raise HTTPError(404, "找不到")
    if not p.is_file():
        raise HTTPError(404, "找不到")
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype.endswith(("javascript", "json", "manifest+json")):
        ctype += "; charset=utf-8"
    cache = "no-cache" if p.suffix in (".js", ".css", ".html", ".webmanifest") else "public, max-age=86400"
    return Response(p.read_bytes(), 200, ctype, [("Cache-Control", cache)])


class PortalHandler(BaseHandler):
    routes = api.routes
    server_version = f"QELPortal/{__version__}"


class _App(Portal):
    def handle(self, req, routes):  # noqa: D401
        r = super().handle(req, routes)
        if r is None:
            if req.method != "GET":
                raise HTTPError(405, "不支援")
            return _static(req)
        return r


def make_server(cfg: PortalConfig, paperlib=None) -> Tuple[Server, Portal]:
    app = _App(cfg, paperlib)
    api.seed(app)
    handler = type("Handler", (PortalHandler,), {"app": app})
    srv = Server((cfg.host, cfg.port), handler)
    return srv, app


def _housekeeping(app: Portal) -> None:
    while True:
        time.sleep(3600)
        try:
            now = time.time()
            with app.store.tx() as c:
                c.execute("DELETE FROM sessions WHERE expires < ?", (now,))
                c.execute("DELETE FROM tickets WHERE expires < ?", (now,))
        except Exception:  # noqa: BLE001
            log.exception("清理失敗")


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="qelportal", description="QEL Lab 大程式伺服器")
    ap.add_argument("--port", type=int)
    ap.add_argument("--data")
    args = ap.parse_args(argv)
    cfg = PortalConfig.from_env()
    if args.port:
        cfg.port = args.port
    if args.data:
        cfg.data_dir = Path(args.data)
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    srv, app = make_server(cfg)
    threading.Thread(target=_housekeeping, args=(app,), daemon=True).start()
    log.info("QEL Lab 大程式 v%s：http://%s:%d/（論文庫 %s、Hub %s）", __version__, cfg.host, cfg.port,
             cfg.paperlib_url, cfg.hub_url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
