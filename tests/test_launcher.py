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


def _fake_python_tarball(tmp_path):
    """假的可攜版 Python：python/bin/python3 轉呼叫目前的 Python（Linux 測試用）。"""
    import tarfile
    src = tmp_path / "pysrc" / "python" / "bin"
    src.mkdir(parents=True)
    exe = src / "python3"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    exe.chmod(0o755)
    tgz = tmp_path / "python.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(tmp_path / "pysrc" / "python", arcname="python")
    return tgz


@pytest.mark.skipif(sys.platform == "win32", reason="假 Python 用 shell script")
def test_portable_python_concurrent_and_repair(qel_home, tmp_path, monkeypatch):
    """好幾個模塊同時安裝：只下載一次、彼此不刪掉對方的檔案；壞掉的 python 資料夾會自動重裝。"""
    import threading
    from qellauncher import core
    tgz = _fake_python_tarball(tmp_path)
    monkeypatch.setenv("QEL_PYTHON_URL", tgz.as_uri())
    downloads = []
    import urllib.request
    orig = urllib.request.urlopen

    def counting(url, *a, **k):
        downloads.append(url)
        return orig(url, *a, **k)
    monkeypatch.setattr(urllib.request, "urlopen", counting)
    (qel_home / "downloads").mkdir(parents=True, exist_ok=True)
    # 上次失敗留下的半套 python（沒有完成標記）
    (qel_home / "python" / "bin").mkdir(parents=True)
    (qel_home / "python" / "bin" / "python3").write_text("broken")
    results, errors = [], []

    def go():
        try:
            results.append(core.ensure_portable_python(qel_home))
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    ts = [threading.Thread(target=go) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(120)
    assert errors == [] and len(set(results)) == 1 and len(downloads) == 1
    assert (qel_home / "python" / core.OK_MARK).exists()
    assert list((qel_home / "downloads").iterdir()) == [] and not list(qel_home.glob(".python.*"))
    assert not (qel_home / "python.lock").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="假 Python 用 shell script")
def test_half_built_env_is_rebuilt(qel_home, tmp_path, monkeypatch):
    """上次建到一半的環境（有 python、沒有 pip）不會被沿用，會重建。打包版（frozen）用可攜版 Python。"""
    from qellauncher import core
    monkeypatch.setenv("QEL_PYTHON_URL", _fake_python_tarball(tmp_path).as_uri())
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    store = core.ModuleStore(qel_home)
    z = tmp_path / "m.zip"
    z.write_bytes(fake_module("1.0.0"))
    store.install_zip("lablogviewer", "1.0.0", z)
    env = qel_home / "envs" / "lablogviewer" / "bin"
    env.mkdir(parents=True)
    (env / "python").write_text("#!/bin/sh\nexit 1\n")
    (env / "python").chmod(0o755)
    py = store.prepare_env("lablogviewer", "1.0.0")
    assert core._env_healthy(py)
    assert (qel_home / "python" / core.OK_MARK).exists()


def test_child_python_runs_in_utf8():
    """中文 Windows 預設 cp950：pip 讀含「—」的 requirements.txt 會 UnicodeDecodeError，子程序一律 UTF-8。"""
    from qellauncher import core
    r = core._run([sys.executable, "-c", "import sys, locale; print(sys.flags.utf8_mode, locale.getpreferredencoding(False));"
                   "sys.stdout.flush(); sys.stdout.buffer.write(b'\\xff\\xfe')"], 30)
    assert r.returncode == 0 and r.stdout.splitlines()[0].replace("-", "").upper() == "1 UTF8"


def test_cards_not_squeezed(qel_home, monkeypatch):
    """卡片高度放得下換行後的文字（Windows 放大 125%／150% 時文字不會疊在一起）。"""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    from qellauncher.window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = MainWindow(Launcher(PortalClient("http://127.0.0.1:9"), ModuleStore(qel_home)))
    mods = [{"id": "paperlib", "name": "論文模塊", "kind": "web", "allowed": True, "description": "論文庫：上傳、分類、閱讀、標註。"},
            {"id": "labcontrol", "name": "量測模塊", "kind": "desktop", "allowed": True, "latest": "0.0.13",
             "description": "Lab Control：儀器控制、流程圖量測、遠端量測節點（實驗控制硬體，預設不開放）。"},
            {"id": "lablogviewer", "name": "數據讀取模擬模塊", "kind": "desktop", "allowed": True, "latest": "1.0.4",
             "description": "LabLogViewer：讀 Labber / HDF5 數據、分析、擬合、3D。"}]
    w.stack.setCurrentWidget(w.main_page)
    w._show_modules(mods)
    w.cards["lablogviewer"]._progress("解壓縮 Python", 0.55)
    w.show()
    for width in (1160, 800, 620):
        w.resize(width, 700)
        for _ in range(20):
            app.processEvents()
        for mid, card in w.cards.items():
            for lab in card.findChildren(QtWidgets.QLabel):
                if lab.isVisible() and lab.wordWrap():
                    assert lab.height() >= lab.heightForWidth(lab.width()), (width, mid, lab.text())
        cards = list(w.cards.values())
        assert not any(a.geometry().intersects(b.geometry()) for a in cards for b in cards if a is not b)
    w.close()
