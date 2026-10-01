"""整合測試：真的啟動論文庫（paperlib）與量測中繼站（Lab Control Hub），再接上大程式伺服器。"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "comm", ROOT / "portal", ROOT / "agent", ROOT / "monitor", ROOT / "launcher"):
    sys.path.insert(0, str(p))

HUB_TOKEN = "test-hub-token"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 40) -> None:
    t0 = time.time()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.time() - t0 < timeout:
        try:
            opener.open(url, timeout=2).read()
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise RuntimeError(f"{url} 沒有啟動")


def pl_call(base, method, path, body=None, cookie=""):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    h = {"Content-Type": "application/json", "X-PL": "1"}
    if cookie:
        h["Cookie"] = f"plsession={cookie}"
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers=h)
    r = opener.open(req, timeout=20)
    sc = r.headers.get_all("Set-Cookie") or []
    data = json.loads(r.read().decode() or "null")
    ck = ""
    for c in sc:
        if c.startswith("plsession="):
            ck = c.split(";", 1)[0].split("=", 1)[1]
    return data, ck


@pytest.fixture(scope="session")
def paperlib_server(tmp_path_factory):
    data = tmp_path_factory.mktemp("pldata")
    port = free_port()
    env = dict(os.environ, PAPERLIB_DATA=str(data), PAPERLIB_JOBS="0", TZ="Asia/Taipei")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
                             "--proxy-headers", "--forwarded-allow-ips", "*", "--log-level", "warning"],
                            cwd=str(ROOT / "modules" / "paperlib"), env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        wait_http(base + "/api/site")
        # 站長 boss、成員 amy、唯讀 vic
        _, boss = pl_call(base, "POST", "/api/auth/setup", {"username": "boss", "password": "bosspass1",
                                                             "display_name": "老闆"})
        pl_call(base, "PATCH", "/api/users/1", {"owner": True}, boss)
        pl_call(base, "POST", "/api/users", {"username": "amy", "password": "amypass12", "display_name": "Amy",
                                             "role": "member"}, boss)
        pl_call(base, "POST", "/api/users", {"username": "vic", "password": "vicpass12", "display_name": "Vic",
                                             "role": "viewer"}, boss)
        # 論文：直接寫資料庫（上傳 PDF 太慢）
        con = sqlite3.connect(str(data / "library.db"))
        now = "2026-10-01T00:00:00+00:00"
        for pid, title, year, tag in ((1, "Bound states in the continuum for magnons", 2024, "BIC"),
                                      (2, "Cavity magnonics review", 2022, None),
                                      (3, "YIG mirror coupling", 2025, "Mirror")):
            con.execute("INSERT INTO papers(id, citekey, title, authors, year, added_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                        (pid, f"k{pid}", title, json.dumps(["Wang, A."]), year, now, now))
            if tag:
                con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag,))
                tid = con.execute("SELECT id FROM tags WHERE name=?", (tag,)).fetchone()[0]
                con.execute("INSERT INTO paper_tags(paper_id, tag_id) VALUES(?,?)", (pid, tid))
        con.commit()
        con.close()
        pl_call(base, "POST", "/api/admin/reindex", {}, boss)          # 全文索引
        yield base
    finally:
        proc.terminate()
        proc.wait(10)


@pytest.fixture(scope="session")
def hub_server(tmp_path_factory):
    data = tmp_path_factory.mktemp("hubdata")
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "labhub", "--host", "127.0.0.1", "--port", str(port),
                             "--data", str(data), "--token", HUB_TOKEN],
                            cwd=str(ROOT / "modules" / "labcontrol"), env=dict(os.environ, LABHUB_LOG="WARNING"))
    base = f"http://127.0.0.1:{port}"
    try:
        wait_http(base + "/api/ping")
        yield base
    finally:
        proc.terminate()
        proc.wait(10)


@pytest.fixture()
def portal(tmp_path, paperlib_server, hub_server, monkeypatch):
    from qelportal.config import PortalConfig
    from qelportal.server import make_server

    shares = tmp_path / "shares"
    (shares / "VNA_data" / "2026").mkdir(parents=True)
    cfg = PortalConfig(data_dir=tmp_path / "portal", port=free_port(), host="127.0.0.1",
                       paperlib_url=paperlib_server, paperlib_public_url="http://nas.local:8080",
                       hub_url=hub_server, hub_token=HUB_TOKEN, recheck_s=600,
                       data_roots={"//nas/ccuqel": str(shares)})
    srv, app = make_server(cfg)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{cfg.port}"
    wait_http(base + "/api/v1/ping")
    yield {"base": base, "app": app, "cfg": cfg, "shares": shares, "paperlib": paperlib_server, "hub": hub_server}
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def qel_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QEL_HOME", str(tmp_path / "qelhome"))
    monkeypatch.delenv("QEL_TOKEN", raising=False)
    monkeypatch.delenv("QEL_PORTAL_URL", raising=False)
    return tmp_path / "qelhome"
