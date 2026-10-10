"""Hub 控制代理：更新網站（每一步即時回報）、失敗自動換回、完全重建、重新啟動、停止 / 啟動、回到備份。
Docker 用 tests/fake_docker.py 模擬（容器 = python -m labhub 子程序，程式碼掛載 = hub_dir/labhub）。"""
import io
import shutil
import threading
import time
import zipfile
from pathlib import Path

import pytest

from labhub import __version__
from labhub.agent import AgentConfig, make_agent_server
from labmonitor.client import AgentClient, HubClient, HubError
from tests.fake_docker import FakeDocker

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "tk"


def _zip_of(pkg: Path, version=None, break_server=False, prefix="LabControlHub/labhub") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in sorted(pkg.rglob("*")):
            rel = p.relative_to(pkg)
            if not p.is_file() or "__pycache__" in rel.parts:
                continue
            data = p.read_bytes()
            if version and rel.as_posix() == "__init__.py":
                data = data.replace(f'__version__ = "{__version__}"'.encode(), f'__version__ = "{version}"'.encode())
            if break_server and rel.as_posix() == "server.py":
                data += b"\nraise RuntimeError('broken on purpose')\n"
            z.writestr(f"{prefix}/{rel.as_posix()}", data)
        z.writestr("LabControlHub/docker-compose.yml", "services: {}\n")
    return buf.getvalue()


@pytest.fixture
def env(tmp_path):
    hub_dir = tmp_path / "LabControlHub"
    shutil.copytree(ROOT / "labhub", hub_dir / "labhub", ignore=shutil.ignore_patterns("__pycache__"))
    (hub_dir / "data").mkdir()
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    fd = FakeDocker(hub_dir, "labcontrol-hub", port, TOKEN)
    cfg = AgentConfig(hub_dir, "labcontrol-hub", f"http://127.0.0.1:{port}", fd.url, TOKEN, verify_s=20)
    srv, agent = make_agent_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ac = AgentClient(f"http://127.0.0.1:{srv.server_address[1]}", TOKEN)
    yield ac, fd, HubClient(f"http://127.0.0.1:{port}", TOKEN), hub_dir, agent
    srv.shutdown()
    fd.close()


def _run(ac, action, **kw):
    j = ac.submit(action, **kw)
    after, seen = 0, []
    while True:
        v = ac.job(j["id"], after, wait=10)
        after = v["n_lines"]
        seen += [l["msg"] for l in v["lines"]]
        if v["done"]:
            return v, seen


def test_start_update_and_verify(env):
    ac, fd, hub, hub_dir, agent = env
    st = ac.status()
    assert st["container"]["status"] == "created" and not st["hub_online"] and st["code_version"] == __version__
    v, _ = _run(ac, "start")
    assert v["ok"] and hub.ping()["version"] == __version__

    v, lines = _run(ac, "update", data=_zip_of(ROOT / "labhub", version="9.9.9"))
    assert v["ok"], lines
    assert [s["key"] for s in v["steps"]] == ["check", "stop", "backup", "replace", "rebuild", "start", "verify"]
    assert all(s["status"] == "ok" for s in v["steps"])
    assert hub.ping()["version"] == "9.9.9" and "Hub 已更新到 v9.9.9" in v["result"]
    assert any("▶ 停止" in l for l in lines) and any("✔ 確認版本" in l for l in lines)       # 每一步都有即時紀錄
    assert ("POST", "/containers/create?name=labcontrol-hub") in fd.calls                    # 重建容器
    bk = ac.status()["backups"]
    assert bk and bk[0]["version"] == __version__
    assert (hub_dir / "labhub" / "server.py").exists() and not list(hub_dir.glob(".update/*"))


def test_broken_update_rolls_back(env):
    ac, fd, hub, hub_dir, agent = env
    _run(ac, "start")
    v, lines = _run(ac, "update", data=_zip_of(ROOT / "labhub", version="9.9.10", break_server=True))
    assert not v["ok"] and "已換回" in v["result"]
    verify = next(s for s in v["steps"] if s["key"] == "verify")
    assert verify["status"] == "fail"
    assert next(s for s in v["steps"] if s["key"] == "restore_verify")["status"] == "ok"
    assert hub.ping()["version"] == __version__                 # 舊版回來了


def test_bad_zip_is_rejected_before_stopping(env):
    ac, fd, hub, hub_dir, agent = env
    _run(ac, "start")
    v, _ = _run(ac, "update", data=b"not a zip")
    assert not v["ok"] and v["steps"][0]["status"] == "fail" and v["steps"][1]["status"] == "skip"
    assert hub.ping()["version"] == __version__                 # 沒有停止 Hub


def test_full_rebuild_restart_stop_start_rollback(env):
    ac, fd, hub, hub_dir, agent = env
    _run(ac, "start")
    v, lines = _run(ac, "full_rebuild")
    assert v["ok"] and fd.pulls == 1 and any("Image is up to date" in l for l in lines)
    v, _ = _run(ac, "stop")
    assert v["ok"] and not ac.status()["hub_online"]
    with pytest.raises(HubError):
        hub.ping()
    v, _ = _run(ac, "start")
    assert v["ok"] and hub.ping()
    v, _ = _run(ac, "restart")
    assert v["ok"]
    _run(ac, "update", data=_zip_of(ROOT / "labhub", version="9.9.9"))
    name = ac.status()["backups"][0]["name"]
    v, _ = _run(ac, "rollback", backup=name)
    assert v["ok"] and hub.ping()["version"] == __version__


def test_one_job_at_a_time(env):
    ac, fd, hub, hub_dir, agent = env
    ac.submit("start")
    with pytest.raises(HubError, match="409"):
        ac.submit("restart")


def test_console_page_served(env):
    ac = env[0]
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor())
    page = opener.open(ac.url + f"/?token={TOKEN}").read().decode()
    assert "Hub 控制台" in page and "更新網站" in page and "完全重建" in page


def test_monitor_console_update_flow(env, tmp_path, monkeypatch):
    """Monitor 的 Hub 控制台：選 zip → 更新網站，每一步即時顯示；停止 / 啟動；回到備份。"""
    import os

    if not os.environ.get("QT_QPA_PLATFORM") and os.name != "nt" and not os.environ.get("DISPLAY"):
        pytest.skip("需要 Qt 顯示環境")
    from PyQt6 import QtWidgets

    from labmonitor.console import HubConsole, zip_version

    ac, fd, hub, hub_dir, agent = env
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def pump(cond, s=60):
        t = time.time()
        while time.time() - t < s:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.03)
        return False
    w = HubConsole(lambda: ac.url, lambda: TOKEN)
    monkeypatch.setattr(w, "confirm", lambda text: True)
    w.show()
    assert pump(lambda: w.status.get("agent_version"))
    assert w.info["state"].text().startswith("已建立") and not w.b_update.isEnabled()
    w.run_action("start")
    assert pump(lambda: w.job_id is None and w.status.get("hub_online") and "✅" in w.msg.text())
    z = tmp_path / "LabControlHub_v9.9.9.zip"
    z.write_bytes(_zip_of(ROOT / "labhub", version="9.9.9"))
    assert zip_version(z.read_bytes()) == "9.9.9"
    bad = tmp_path / "x.zip"
    with zipfile.ZipFile(bad, "w") as zz:
        zz.writestr("readme.txt", "x")
    w.load_zip(str(bad))
    assert "不是 Hub 的 zip" in w.msg.text() and w.zip_data is None
    w.load_zip(str(z))
    assert "v9.9.9" in w.drop.label.text() and w.b_update.isEnabled()
    w.update_site()
    assert pump(lambda: w.job_id is None and w.status.get("hub_version") == "9.9.9")
    assert [p.text.text() for p in w.pills] == ["檢查 zip", "停止", "備份", "換程式", "重建", "啟動", "確認版本"]
    assert all(p.icon.text() == "✔" for p in w.pills)
    log = w.log.toPlainText()
    assert "停止" in log and "確認版本" in log and "Hub 已更新到 v9.9.9" in w.msg.text()
    assert w.backups.topLevelItemCount() >= 1
    # 回到備份（更新前的版本）
    w.backups.setCurrentItem(w.backups.topLevelItem(0))
    w.rollback()
    assert pump(lambda: w.job_id is None and w.status.get("hub_version") == __version__)
    w.run_action("stop")
    assert pump(lambda: w.job_id is None and w.info["state"].text() == "已停止")
    w.close()
