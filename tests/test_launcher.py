"""桌面大程式：從大程式網站安裝、更新、換回、啟動模塊，並把動作傳給模塊（真的子程序）。"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest

from conftest import ROOT
from labcomm import PortalClient, local
from labcomm.errors import CommError
from qellauncher.core import Launcher, ModuleStore

FAKE_MAIN = '''
import json, os, sys, time
from labcomm import local
out = os.environ["FAKE_OUT"]
def handler(action, payload):
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps({"action": action, "payload": payload, "token": bool(os.environ.get("QEL_TOKEN")),
                            "version": os.environ.get("QEL_MODULE_VERSION")}) + "\\n")
    return {"accepted": True}
ep = local.LocalEndpoint("lablogviewer", os.environ.get("QEL_MODULE_VERSION", "?"), handler).start()
time.sleep(float(os.environ.get("FAKE_LIFE", "15")))
ep.stop()
'''


def fake_module(version: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("LabLogViewer/module.json", json.dumps({"id": "lablogviewer", "version": version, "kind": "desktop",
                                                           "entry": "main.py"}))
        z.writestr("LabLogViewer/main.py", FAKE_MAIN)
    return buf.getvalue()



@pytest.fixture()
def published(portal, tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import package
    boss = PortalClient(portal["base"])
    boss.login("boss", "bosspass1")
    z = package.build("labcomm", tmp_path / "dist")
    boss.publish_release("labcomm", "1.0.0", z.read_bytes())
    boss.publish_release("lablogviewer", "1.0.0", fake_module("1.0.0"))
    return boss


def make_launcher(portal, home):
    c = PortalClient(portal["base"], client_name="pytest-launcher")
    return Launcher(c, ModuleStore(home))


def test_install_launch_deliver(portal, published, qel_home, tmp_path, monkeypatch):
    out = tmp_path / "received.jsonl"
    monkeypatch.setenv("FAKE_OUT", str(out))
    L = make_launcher(portal, qel_home)
    with pytest.raises(CommError):
        L.login("amy", "nope-wrong")
    L.login("amy", "amypass12")
    assert json.loads((qel_home / "session.json").read_text())["user"]["username"] == "amy"
    mods = {m["id"]: m for m in L.refresh()}
    assert mods["lablogviewer"]["allowed"] and mods["lablogviewer"]["latest"] == "1.0.0"
    assert not mods["labcontrol"]["allowed"]
    with pytest.raises(CommError, match="站長"):
        L.install_latest("labcontrol")
    steps = []
    assert L.ensure_labcomm(lambda m, f: steps.append(m)) == "1.0.0"
    assert (qel_home / "modules" / "labcomm" / "1.0.0" / "labcomm" / "client.py").exists()
    L.install_latest("lablogviewer", lambda m, f: steps.append((m, round(f, 2))))
    assert steps[-1] == ("完成", 1.0)
    assert L.store.installed("lablogviewer") == "1.0.0"
    c = L.command("lablogviewer")
    assert c["env"]["QEL_TOKEN"] == L.client.token and c["env"]["QEL_MODULE_ID"] == "lablogviewer"
    assert str(qel_home / "modules" / "labcomm" / "1.0.0") in c["env"]["PYTHONPATH"].split(os.pathsep)
    assert Path(c["cmd"][0]).parent.parent == qel_home / "envs" / "lablogviewer"     # 模塊自己的環境
    # 開啟並把動作傳過去（模塊用安裝的 labcomm，不是測試的這份）
    L.deliver_after_launch("lablogviewer", "open_file", {"path": "/data/x.hdf5"})
    got = [json.loads(x) for x in out.read_text().splitlines()]
    assert got == [{"action": "open_file", "payload": {"path": "/data/x.hdf5"}, "token": True, "version": "1.0.0"}]
    # 另一個模塊（例如量測模塊）直接用 labcomm 送
    assert local.deliver("lablogviewer", "show_papers", {"tags": ["BIC"]}) == "sent"
    assert json.loads(out.read_text().splitlines()[-1])["action"] == "show_papers"
    L.procs["lablogviewer"].terminate()
    L.procs["lablogviewer"].wait(10)


def test_update_rollback_prune(portal, published, qel_home):
    L = make_launcher(portal, qel_home)
    L.login("amy", "amypass12")
    L.install_latest("lablogviewer")
    for v in ("1.0.1", "1.0.2"):
        published.publish_release("lablogviewer", v, fake_module(v))
        m = {x["id"]: x for x in L.refresh()}["lablogviewer"]
        assert m["update"] and m["installed"] != v
        L.install_latest("lablogviewer")
        assert L.store.installed("lablogviewer") == v
    assert L.store.versions("lablogviewer") == ["1.0.2", "1.0.1"]            # 只保留 2 個版本
    assert L.store.rollback("lablogviewer") == "1.0.1"
    assert L.store.installed("lablogviewer") == "1.0.1"
    assert L.store.rollback("lablogviewer") is None
    L.store.uninstall("lablogviewer")
    assert L.store.installed("lablogviewer") is None


def test_rejects_tampered_download(portal, published, qel_home, tmp_path):
    L = make_launcher(portal, qel_home)
    L.login("amy", "amypass12")
    z = tmp_path / "x.zip"
    z.write_bytes(fake_module("1.0.0"))
    with pytest.raises(CommError, match="校驗碼"):
        L.store.install_zip("lablogviewer", "1.0.0", z, sha256="0" * 64)
    with pytest.raises(CommError, match="不是 lablogviewer v9"):
        L.store.install_zip("lablogviewer", "9", z)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zz:
        zz.writestr("../evil.py", "x")
    with pytest.raises(CommError, match="不安全"):
        L.store.install_zip("lablogviewer", "1.0.0", bad)


def test_deliver_without_launcher_gives_clear_error(qel_home):
    with pytest.raises(CommError, match="大程式"):
        local.deliver("labcontrol", "apply_scheme", {"path": "/x.hdf5"})


def test_window_launch_request_from_other_module(portal, published, qel_home, tmp_path, monkeypatch):
    """讀檔模塊沒開時，量測模塊用 labcomm.local.deliver → 大程式開啟讀檔模塊再轉交。"""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    out = tmp_path / "received.jsonl"
    monkeypatch.setenv("FAKE_OUT", str(out))
    from PySide6 import QtWidgets
    from PySide6.QtTest import QTest
    from qellauncher.window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    L = make_launcher(portal, qel_home)
    L.login("amy", "amypass12")
    L.install_latest("lablogviewer")
    w = MainWindow(L)
    w.start()
    t0 = time.time()
    while not w.cards and time.time() - t0 < 15:
        QTest.qWait(100)
    assert "lablogviewer" in w.cards and "labcontrol" in w.cards and "paperlib" in w.cards
    assert not w.cards["labcontrol"].isEnabled()                      # 站長沒開放
    assert local.deliver("lablogviewer", "open_file", {"path": "/d/a.hdf5"}) == "launching"
    t0 = time.time()
    while not out.exists() and not w.errors and time.time() - t0 < 60:
        QTest.qWait(200)
    assert not w.errors, w.errors
    assert json.loads(out.read_text().splitlines()[0])["payload"] == {"path": "/d/a.hdf5"}
    L.procs["lablogviewer"].terminate()
    w.close()


def test_concurrent_installs_do_not_collide(portal, published, qel_home):
    """背景自動安裝通信模塊的同時使用者按「開啟」：同一個模塊同時只裝一次。"""
    import threading
    L = make_launcher(portal, qel_home)
    L.login("amy", "amypass12")
    L.refresh()
    errors = []

    def go():
        try:
            assert L.ensure_labcomm() == "1.0.0"
            L.install_latest("lablogviewer")
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    ts = [threading.Thread(target=go) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(120)
    assert errors == []
    assert L.store.installed("labcomm") == "1.0.0" and L.store.installed("lablogviewer") == "1.0.0"
    assert list((qel_home / "downloads").glob("*.zip")) == []
