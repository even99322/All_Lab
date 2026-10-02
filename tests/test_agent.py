"""更新代理：真的把大程式網站（子程序）停止、備份、換程式、重建、啟動、確認，失敗自動換回。"""
from __future__ import annotations

import io
import re
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import ROOT, free_port, wait_http
from fake_docker import FakeDocker
from qelagent.agent import Agent, Service, load_services, read_version
from qelagent.docker import Docker, demux
from qelportal import __version__ as PORTAL_V
from qelagent.server import Auth, make_server

EMERGENCY = "emergency-token"


def portal_zip(version: str, broken: bool = False) -> bytes:
    """把目前的 qelportal 打包成指定版本（broken＝啟動就當掉）。"""
    buf = io.BytesIO()
    src = ROOT / "portal" / "qelportal"
    with zipfile.ZipFile(buf, "w") as z:
        for p in src.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                rel = "portal/qelportal/" + p.relative_to(src).as_posix()
                data = p.read_bytes()
                if p.name == "__init__.py" and p.parent == src:
                    data = re.sub(rb'__version__ = "[^"]+"', f'__version__ = "{version}"'.encode(), data)
                if broken and p.name == "__main__.py":
                    data = b"raise SystemExit(3)\n"
                z.writestr(rel, data)
        z.writestr("portal/module.json", json.dumps({"id": "portal", "version": version}))
    return buf.getvalue()


@pytest.fixture()
def stack(tmp_path):
    st = tmp_path / "stack"
    shutil.copytree(ROOT / "portal" / "qelportal", st / "portal" / "qelportal",
                    ignore=shutil.ignore_patterns("__pycache__"))
    port = free_port()
    fd = FakeDocker()
    env = {k: v for k, v in os.environ.items() if not k.startswith("QEL_")}
    env.update(QEL_PORTAL_DATA=str(tmp_path / "pdata"), QEL_PORTAL_PORT=str(port))
    fd.add("qel-portal", [sys.executable, "-m", "qelportal"], str(st / "portal"), env).start()
    (st / "agent" / "qelagent").mkdir(parents=True)
    shutil.copytree(ROOT / "agent" / "qelagent", st / "agent" / "qelagent", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    services = [Service("portal", "大程式網站", "qel-portal", st / "portal", "qelportal",
                        f"http://127.0.0.1:{port}/api/v1/ping"),
                Service("agent", "更新代理", "qel-agent", st / "agent", "qelagent", "", is_self=True)]
    exits = []
    agent = Agent(services, Docker(fd.url), st / "backups", verify_s=20, exit_fn=lambda: exits.append(1))
    auth = Auth(EMERGENCY, "")
    aport = free_port()
    srv = make_server(agent, auth, "127.0.0.1", aport)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{aport}"
    wait_http(f"http://127.0.0.1:{port}/api/v1/ping")
    yield {"base": base, "agent": agent, "fd": fd, "stack": st, "exits": exits, "srv": srv}
    srv.shutdown()
    fd.close()


def call(base, method, path, data=None, token=EMERGENCY):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    h = {"Authorization": f"Bearer {token}"} if token else {}
    r = opener.open(urllib.request.Request(base + path, data=data, method=method, headers=h), timeout=60)
    return json.loads(r.read().decode())


def run_job(base, sid, action, data=None, backup=""):
    j = call(base, "POST", f"/api/agent/services/{sid}/jobs?action={action}&backup={backup}", data)
    after = 0
    t0 = time.time()
    while time.time() - t0 < 90:
        j = call(base, "GET", f"/api/agent/jobs/{j['id']}?after={after}&wait=5")
        after = j["n_lines"]
        if j["done"]:
            return j
    raise AssertionError("工作沒有結束")


def online_version(stack_):
    svc = stack_["agent"].services["portal"]
    return (stack_["agent"].health(svc) or {}).get("version")


def test_auth(stack):
    with pytest.raises(urllib.error.HTTPError) as e:
        call(stack["base"], "GET", "/api/agent", token="")
    assert e.value.code == 401
    with pytest.raises(urllib.error.HTTPError):
        call(stack["base"], "GET", "/api/agent", token="wrong")
    assert call(stack["base"], "GET", "/api/ping", token="")["server"] == "qel-agent"


def test_status_and_logs(stack):
    st = call(stack["base"], "GET", "/api/agent")
    by = {s["id"]: s for s in st["services"]}
    assert by["portal"]["online"] and by["portal"]["running_version"] == PORTAL_V
    assert by["portal"]["code_version"] == PORTAL_V and by["portal"]["container"]["running"]
    assert by["agent"]["self"] and by["agent"]["code_version"] == "1.0.0"
    logs = call(stack["base"], "GET", "/api/agent/services/portal/logs?tail=10")["text"]
    assert "ready" in logs and "\x01" not in logs


def test_update_then_failed_update_rolls_back_then_rollback(stack):
    j = run_job(stack["base"], "portal", "update", portal_zip("9.0.1"))
    assert j["ok"], j["result"]
    assert [s["status"] for s in j["steps"]] == ["ok"] * 7
    assert online_version(stack) == "9.0.1" and read_version(stack["agent"].services["portal"]) == "9.0.1"
    assert json.loads((stack["stack"] / "portal" / "module.json").read_text())["version"] == "9.0.1"
    # 壞掉的新版：自動換回 1.0.1
    j = run_job(stack["base"], "portal", "update", portal_zip("9.0.2", broken=True))
    assert not j["ok"] and "已換回 v9.0.1" in j["result"]
    assert online_version(stack) == "9.0.1"
    # 回到最早的備份（目前的版本）
    backups = call(stack["base"], "GET", "/api/agent")["services"][0]["backups"]
    first = [b for b in backups if b["version"] == PORTAL_V][0]["name"]
    j = run_job(stack["base"], "portal", "rollback", backup=first)
    assert j["ok"], j["result"]
    assert online_version(stack) == PORTAL_V


def test_rejects_bad_zips(stack):
    j = run_job(stack["base"], "portal", "update", b"not a zip")
    assert not j["ok"] and "不是 zip" in j["result"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x/qelportal/__init__.py", "def broken(:\n")
    j = run_job(stack["base"], "portal", "update", buf.getvalue())
    assert not j["ok"] and ("SyntaxError" in j["result"] or "invalid syntax" in j["result"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("labhub/server.py", "x=1\n")
    j = run_job(stack["base"], "portal", "update", buf.getvalue())
    assert not j["ok"] and "找不到 qelportal/" in j["result"]
    assert online_version(stack) == PORTAL_V          # 都沒有動到正在跑的版本


def test_restart_stop_start(stack):
    assert run_job(stack["base"], "portal", "restart")["ok"]
    assert run_job(stack["base"], "portal", "stop")["ok"]
    assert online_version(stack) is None
    assert run_job(stack["base"], "portal", "start")["ok"]
    assert online_version(stack) == PORTAL_V


def test_self_update(stack):
    buf = io.BytesIO()
    src = ROOT / "agent" / "qelagent"
    with zipfile.ZipFile(buf, "w") as z:
        for p in src.rglob("*.py"):
            data = p.read_bytes()
            if p.name == "__init__.py":
                data = data.replace(b'__version__ = "1.0.0"', b'__version__ = "1.0.1"')
            z.writestr("qelagent/" + p.relative_to(src).as_posix(), data)
    j = run_job(stack["base"], "agent", "update", buf.getvalue())
    assert j["ok"], j["result"]
    assert read_version(stack["agent"].services["agent"]) == "1.0.1"
    time.sleep(3.5)
    assert stack["exits"] == [1]                       # 重新啟動自己（Docker restart policy 會再開起來）


def test_portal_token_auth(tmp_path):
    class Me(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            tok = self.headers.get("Authorization", "")[7:]
            body = {"boss": {"username": "boss", "display_name": "老闆", "manager": True},
                    "amy": {"username": "amy", "manager": False}}.get(tok)
            raw = json.dumps(body or {"error": "請先登入"}).encode()
            self.send_response(200 if body else 401)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Me)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    a = Auth("", f"http://127.0.0.1:{srv.server_address[1]}")
    assert a.check("boss") == "老闆（boss）"
    assert a.check("amy") is None and a.check("nobody") is None and a.check("") is None
    srv.shutdown()


def test_demux_and_service_overrides(tmp_path):
    raw = bytes([1, 0, 0, 0, 0, 0, 0, 3]) + b"ab\n" + bytes([2, 0, 0, 0, 0, 0, 0, 2]) + b"c\n"
    assert demux(raw) == "ab\nc\n"
    assert demux(b"plain tty text") == "plain tty text"
    f = tmp_path / "services.json"
    f.write_text(json.dumps([{"id": "paperlib", "container": "my-paperlib"},
                             {"id": "extra", "name": "其他", "container": "x", "dir": "extra", "code": "pkg"}]))
    svcs = {s.id: s for s in load_services(tmp_path / "stack", f)}
    assert svcs["paperlib"].container == "my-paperlib" and svcs["paperlib"].code == "app"
    assert svcs["extra"].dir == tmp_path / "stack" / "extra"
    assert set(svcs) == {"portal", "paperlib", "labhub", "agent", "extra"}
