"""監控程式：核心（登入、狀態、異常、發佈）與畫面（offscreen）。"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from conftest import free_port
from fake_docker import FakeDocker
from labcomm.errors import CommError
from qelagent.agent import Agent, Service
from qelagent.docker import Docker
from qelagent.server import Auth, make_server
from qelmonitor.core import AgentClient, Monitor, MonitorConfig, read_module_zip

EMERGENCY = "monitor-emergency"


@pytest.fixture()
def agent(portal, tmp_path):
    """更新代理：用大程式驗證站長 token；「容器」是一個會一直睡的子程序。"""
    fd = FakeDocker()
    fd.add("qel-portal", [sys.executable, "-c", "import time; time.sleep(600)"], str(tmp_path), dict(os.environ)).start()
    (tmp_path / "stack" / "portal" / "qelportal").mkdir(parents=True)
    from qelportal import __version__ as portal_version
    (tmp_path / "stack" / "portal" / "qelportal" / "__init__.py").write_text(f'__version__ = "{portal_version}"\n')
    svcs = [Service("portal", "大程式網站", "qel-portal", tmp_path / "stack" / "portal", "qelportal",
                    portal["base"] + "/api/v1/ping")]
    ag = Agent(svcs, Docker(fd.url), tmp_path / "stack" / "backups")
    port = free_port()
    srv = make_server(ag, Auth(EMERGENCY, portal["base"]), "127.0.0.1", port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    fd.close()


def mon_for(portal, agent_url, tmp_path):
    cfg = MonitorConfig(portal_url=portal["base"], agent_url=agent_url)
    return Monitor(cfg)


def test_config_roundtrip(tmp_path):
    p = tmp_path / "c.json"
    c = MonitorConfig(portal_url="http://nas:8090, http://vpn:8090", token="t", remember=False)
    c.save(p)
    d = json.loads(p.read_text())
    assert d["token"] == ""                                 # 沒勾記住就不存 token
    assert MonitorConfig.load(p).portal_url == "http://nas:8090, http://vpn:8090"
    assert c.agent_urls() == ["http://nas:8767", "http://vpn:8767"]
    assert MonitorConfig(agent_url="10.0.0.5:9000").agent_urls() == ["http://10.0.0.5:9000"]


def test_only_owner_can_use_monitor(portal, agent, tmp_path):
    m = mon_for(portal, agent, tmp_path)
    with pytest.raises(CommError, match="只給站長"):
        m.login("amy", "amypass12")
    r = m.login("boss", "bosspass1")
    assert r["ok"] and m.user["manager"]
    # 記住的 token 可以直接繼續
    m2 = Monitor(MonitorConfig(portal_url=portal["base"], agent_url=agent, token=m.cfg.token))
    assert m2.resume()


def test_snapshot_and_alerts(portal, agent, tmp_path):
    m = mon_for(portal, agent, tmp_path)
    m.login("boss", "bosspass1")
    s = m.snapshot()
    assert s.health["paperlib"]["online"] and s.agent["services"][0]["id"] == "portal"
    rows = {r["id"]: r for r in s.service_rows()}
    assert rows["portal"]["online"] and rows["portal"]["container"] == "running"
    assert rows["paperlib"]["version"] == "1.5.1" and rows["agent"]["online"]
    assert s.alerts == []
    m.cfg.disk_warn_gb = 1e9
    assert any("剩餘空間" in a.message for a in m.snapshot().alerts)
    m.cfg.disk_warn_gb = 5
    bad = Monitor(MonitorConfig(portal_url=portal["base"], agent_url="http://127.0.0.1:9"))
    bad.login("boss", "bosspass1")
    s = bad.snapshot()
    assert s.agent is None and any(a.service == "agent" for a in s.alerts)


def test_emergency_mode(portal, agent, tmp_path):
    m = mon_for(portal, agent, tmp_path)
    with pytest.raises(CommError):
        m.use_emergency("wrong")
    m.use_emergency(EMERGENCY)
    s = m.snapshot()
    assert s.agent and s.health is None and "緊急" in s.health_error
    assert not any(a.service == "portal" and a.level == "bad" for a in s.alerts)


def test_publish_module_and_sso(portal, agent, tmp_path):
    m = mon_for(portal, agent, tmp_path)
    m.login("boss", "bosspass1")
    z = tmp_path / "lablogviewer.zip"
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("LabLogViewer/module.json", json.dumps({"id": "lablogviewer", "version": "1.0.4", "name": "讀檔"}))
        zz.writestr("LabLogViewer/main.py", "print(1)\n")
    assert read_module_zip(z)["version"] == "1.0.4"
    r = m.publish_module(z, "測試")
    assert r["module"] == "lablogviewer" and r["version"] == "1.0.4"
    assert {x["id"]: x for x in m.portal.modules()}["lablogviewer"]["latest"] == "1.0.4"
    url = m.open_portal_url("/#/admin/users")
    assert url.startswith(portal["base"] + "/sso?ticket=") and "next=%2F%23%2Fadmin%2Fusers" in url
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zz:
        zz.writestr("x.txt", "x")
    with pytest.raises(ValueError, match="module.json"):
        read_module_zip(bad)


def test_agent_logs_through_monitor(portal, agent, tmp_path):
    m = mon_for(portal, agent, tmp_path)
    m.login("boss", "bosspass1")
    assert "ready" in m.agent.logs("portal", 50)
    j = m.service_action("portal", "restart", lambda j: None)
    assert j["ok"], j


def test_window_offscreen(portal, agent, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    from qelmonitor.window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    m = mon_for(portal, agent, tmp_path)
    m.login("boss", "bosspass1")
    w = MainWindow(m)
    w.timer.stop()
    s = m.snapshot()
    w.show_snapshot(s)
    assert "正常" in w.overview.card_labels["paperlib"].text()
    assert w.overview.alerts.item(0).text() == "一切正常"
    w.users._show(m.portal.api("GET", "/admin/users"))
    assert w.users.table.rowCount() == 3
    w.releases._show(m.portal.modules())
    assert w.releases.table.rowCount() >= 4
    w.services.show_snapshot(s)
    w.services._progress({"steps": [{"title": "停止", "status": "ok"}, {"title": "備份", "status": "running"}],
                          "lines": [{"time": 0, "msg": "▶ 備份"}]})
    assert "✔ 停止" in w.services.steps.text()
    w.close()
