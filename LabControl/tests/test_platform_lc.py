"""Lab Control × 量子工程平台：上傳到數據目錄（帶標籤）、拖放數據檔套用設定、接收「套用設定」、標籤挑選。"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from labhub.server import Config, make_server
from tests import fake_paperlib


@pytest.fixture
def gw(tmp_path, monkeypatch):
    psrv, pstate, purl = fake_paperlib.start()
    cfg = Config(tmp_path / "hub", token="dev", paperlib_url=purl, paperlib_public="https://papers.example")
    srv, state = make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    from qelcomm import Session

    s = Session.login(url, "alice", "alicepw", save=False)
    monkeypatch.setenv("QEL_GATEWAY", url)
    monkeypatch.setenv("QEL_TOKEN", s.token)
    yield url, state, s
    srv.shutdown()
    srv.server_close()
    psrv.shutdown()
    psrv.server_close()


def _wait(cond, t=10, app=None):
    t0 = time.time()
    while time.time() - t0 < t:
        if app is not None:
            app.processEvents()
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_uploader_registers_local_runs(gw, tmp_path):
    from labcontrol.core.events import EventBus
    from labcontrol.integrations import platform as plat

    url, state, s = gw
    bus = EventBus()
    up = plat.Uploader(bus)
    f = tmp_path / "run.hdf5"
    f.write_bytes(b"data")
    bus.publish("data.exported", path=str(f), writer="labber", tags=["transmon"], scheme="2D")
    assert _wait(lambda: up.done)
    item = s.item(up.done[0]["catalog_id"])
    assert item["tags"] == ["transmon"] and item["user"] == "alice" and item["scheme"] == "2D"
    with plat.suspend_uploads():                         # 量測節點自己會上傳 → 不重複
        bus.publish("data.exported", path=str(f), writer="hdf5")
    time.sleep(0.3)
    assert len(up.done) == 1
    up.close()


def test_experiment_output_tags():
    from labcontrol.measure.experiment import Experiment

    e = Experiment.__new__(Experiment)
    e.config = {"output": {"export": [{"type": "labber", "tags": ["a", "b"]}, {"type": "hdf5"}],
                           "extra": [{"export": [{"type": "labber", "tags": ["b", "c"]}]}]}}
    assert e.output_tags() == ["a", "b", "c"]


@pytest.fixture
def win(station):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.paths import DEFAULTS_DIR
    from labcontrol.scheme import Scheme

    app = QtWidgets.QApplication.instance()
    w = LabControlWindow(station, Scheme.load(DEFAULTS_DIR / "templates" / "02_2d.scheme.yaml"))
    w.show()
    app.processEvents()
    yield w, app
    w.doc.dirty = False
    w.close()
    app.processEvents()


def _labber(tmp_path):
    from labcontrol.testing.fake_labber import write_labber_like

    return write_labber_like(tmp_path / "old.hdf5", [("Average Current", "A", np.linspace(0.11, 0.12, 11))],
                             {"VNA": {"Start frequency": 6.0e9, "# of points": 301}}, tags=["JPA"])


def test_drop_labber_file_applies_settings(win, tmp_path, monkeypatch):
    from PyQt6 import QtCore, QtWidgets

    w, app = win
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
    p = _labber(tmp_path)
    md = QtCore.QMimeData()
    md.setUrls([QtCore.QUrl.fromLocalFile(str(p))])
    assert w.platform.accepts(md)
    bad = QtCore.QMimeData()
    bad.setUrls([QtCore.QUrl.fromLocalFile(str(tmp_path / "x.png"))])
    assert not w.platform.accepts(bad)
    w.platform.drop(md)
    assert _wait(lambda: w.doc.scheme.blocks[0].points == 11, app=app)
    meas = w.doc.scheme.blocks[0].children[0]
    assert meas.settings["start_freq"] == "6 GHz" and meas.settings["points"] == 301
    assert w.doc.scheme.output.get("tags") == ["JPA"]
    w.doc.undo()                                          # 可以復原
    assert w.doc.scheme.blocks[0].points != 11


def test_apply_settings_message_from_platform(gw, win, tmp_path, monkeypatch):
    from PyQt6 import QtWidgets

    from labcontrol.scheme import Scheme
    from labcontrol.testing.fake_labber import write_labber_like
    from labcontrol.data.writers.labber import embed_metadata
    from labcontrol.paths import DEFAULTS_DIR

    url, state, s = gw
    w, app = win
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
    # 別人量的檔（裡面有完整方案）在數據目錄 → 網頁按「套用設定到量測模塊」
    sch = Scheme.load(DEFAULTS_DIR / "templates" / "03_2d_async.scheme.yaml")
    f = write_labber_like(tmp_path / "lc.hdf5", [("Average Current", "A", [0.1, 0.2])], {})
    embed_metadata(f, {"experiment": {"scheme": sch.to_dict()}})
    up = s.upload("LAB-1", "x/lc.hdf5", f, tags=["transmon"])
    w.platform.start_listener()
    time.sleep(0.6)
    s.send("labcontrol", "apply_settings", from_module="portal", catalog_id=up["catalog_id"], name="lc.hdf5")
    assert _wait(lambda: w.doc.scheme.name == sch.name, 15, app)
    w.platform.stop_listener()


def test_tag_picker_shows_platform_tags_and_papers(gw, win):
    from labcontrol.apps.qt.platform_ui import TagPickerDialog

    w, app = win
    d = TagPickerDialog(["sample-X"], w)
    assert _wait(lambda: d.tree.topLevelItemCount() >= 2, app=app)
    names = []
    for i in range(d.tree.topLevelItemCount()):
        cat = d.tree.topLevelItem(i)
        names += [cat.child(j).text(0) for j in range(cat.childCount())]
    assert {"transmon", "JPA", "sample-X"} <= set(names)
    assert _wait(lambda: "sample-X" in d.papers.toHtml() or "論文庫" in d.papers.toHtml(), app=app)
    # 勾 transmon → 右邊列出論文（可以點擊到論文庫）
    cat = next(d.tree.topLevelItem(i) for i in range(d.tree.topLevelItemCount())
               if any(d.tree.topLevelItem(i).child(j).text(0) == "transmon"
                      for j in range(d.tree.topLevelItem(i).childCount())))
    it = next(cat.child(j) for j in range(cat.childCount()) if cat.child(j).text(0) == "transmon")
    from PyQt6.QtCore import Qt
    it.setCheckState(0, Qt.CheckState.Checked)
    assert d.selected == ["sample-X", "transmon"]
    assert _wait(lambda: "papers.example/#/p/11" in d.papers.toHtml(), app=app)
    d.close()
    w.files.set_tags(d.selected)
    assert w.doc.scheme.output["tags"] == ["sample-X", "transmon"]


def test_recent_data_drag_out_and_open_in_viewer(gw, win, tmp_path):
    from qelcomm import Session, dnd

    url, state, s = gw
    w, app = win
    f = tmp_path / "new_run.hdf5"
    f.write_bytes(b"123")
    w.station.bus.publish("data.exported", path=str(f), writer="hdf5", tags=["transmon"], scheme="t")
    lst = w.platform.recent.list
    assert _wait(lambda: lst.count() == 1 and lst.item(0).text().startswith("☁"), 10, app)   # 也上傳到平台了
    md = lst.mimeData([lst.item(0)])
    assert md.hasUrls() and Path(md.urls()[0].toLocalFile()) == f
    item = dnd.parse_payload(bytes(md.data(dnd.MIME)))[0]
    assert item["catalog_id"] and item["gateway"] == url
    # 「用讀檔程式開啟」→ 0.0.26 起直接開這台電腦的 LabLogViewer（不經過啟動器）
    from labcontrol.integrations import reader

    opened = []
    orig = reader.open_path
    reader.open_path = lambda p: opened.append(Path(p)) or Path("LabLogViewer.exe")
    try:
        lst.setCurrentRow(0)
        w.platform.recent._open()
        assert opened == [f]
    finally:
        reader.open_path = orig


def test_monitor_platform_tab(gw, tmp_path):
    from PyQt6 import QtWidgets
    from PyQt6.QtCore import Qt

    from labmonitor.platform_tab import PlatformTab
    from qelcomm import Session

    url, state, _ = gw
    app = QtWidgets.QApplication.instance()
    tab = PlatformTab(lambda: url)
    tab.show()
    owner = Session.login(url, "admin", "adminpw", save=False)
    z = tmp_path / "LabControl_v0.0.13.zip"
    z.write_bytes(b"zip")
    owner.publish_module("labcontrol", "0.0.13", z)
    tab._set_session(owner)
    assert _wait(lambda: tab.users.rowCount() == 4 and tab.mods.topLevelItemCount() >= 5, 10, app)
    assert tab.c_pl[1].text() == "● 連線" and tab.c_hub[1].text()
    row = next(i for i in range(4) if "alice" in tab.users.item(i, 0).text())
    col = 2 + [m["id"] for m in tab.user_mods].index("labcontrol")
    tab.users.item(row, col).setCheckState(Qt.CheckState.Unchecked)            # 關掉 alice 的量測模塊
    assert _wait(lambda: state.platform.perms_table().get("alice", {}).get("labcontrol") is False, 10, app), tab.msg.text()
    lc = next(tab.mods.topLevelItem(i) for i in range(tab.mods.topLevelItemCount())
              if tab.mods.topLevelItem(i).text(1) == "labcontrol")
    assert lc.text(3) == "0.0.13"
    tab.close()


def test_project_choice_new_project_and_project_tags(gw, win, monkeypatch):
    """檔案設置：選論文庫專案、在量測模塊新增專案、標籤記到專案、資料夾規則 {project}、上傳歸到專案。"""
    from PyQt6.QtCore import Qt

    from labcontrol.apps.qt.platform_ui import TagPickerDialog
    from labcontrol.apps.qt.workbench import files_panel as fp
    from labcontrol.integrations import platform as plat

    url, state, s = gw
    w, app = win
    plat.PROJECTS.items, plat.PROJECTS.at = [], 0.0
    cb = w.files.lab_project
    w.files.reload_projects(force=True)
    assert _wait(lambda: cb.findText("YIG mirror coupling", Qt.MatchFlag.MatchStartsWith) >= 0, app=app)
    assert cb.itemData(0) is None and cb.itemData(cb.count() - 1) == fp.NEW_PROJECT
    # 選既有專案 → 方案記 lab_project；資料夾規則可以用 {project}
    cb.setCurrentIndex(cb.findText("YIG mirror coupling", Qt.MatchFlag.MatchStartsWith))
    w.files._project_chosen(cb.currentIndex())
    assert w.doc.scheme.output["lab_project"] == {"id": "1", "name": "YIG mirror coupling"}
    w.files.pattern.setText("{project}/{yyyy}")
    w.files.pattern.editingFinished.emit()
    assert "YIG mirror coupling" in w.files.where.text()
    # 在量測模塊新增專案（建在論文庫）
    monkeypatch.setattr(fp.NewProjectDialog, "exec", lambda self: True)
    monkeypatch.setattr(fp.NewProjectDialog, "values",
                        lambda self: {"name": "Magnon JPA", "short_name": "MJ", "description": "", "color": ""})
    cb.setCurrentIndex(cb.count() - 1)
    w.files._project_chosen(cb.currentIndex())
    lp = w.doc.scheme.output["lab_project"]
    assert lp["name"] == "Magnon JPA" and lp["id"]
    assert any(p["name"] == "Magnon JPA" for p in s.projects(refresh=True))
    assert cb.currentData() == lp
    # 標籤挑選：最上面是專案的標籤；選了的標籤記到專案
    s.add_project_tags(lp["id"], ["magnon"])
    d = TagPickerDialog([], w, project=lp)
    assert _wait(lambda: d.tree.topLevelItemCount() and "magnon" in
                 [d.tree.topLevelItem(0).child(j).text(0) for j in range(d.tree.topLevelItem(0).childCount())], app=app)
    assert d.tree.topLevelItem(0).text(0).startswith(TagPickerDialog.PROJECT_CAT)
    d.new.setText("readout-chain")
    d._create()
    for i in range(d.tree.topLevelItemCount()):
        cat = d.tree.topLevelItem(i)
        for j in range(cat.childCount()):
            if cat.child(j).text(0) == "transmon":
                cat.child(j).setCheckState(0, Qt.CheckState.Checked)
    assert "transmon" in d.selected and "readout-chain" in d.selected
    d._accept()
    assert _wait(lambda: {"magnon", "transmon", "readout-chain"} <= set(s.project(lp["id"])["tags"]), app=app)
    # 量測存檔 → 上傳時歸到專案
    up = w.platform.uploader
    n0 = len(up.done)
    import tempfile
    f = Path(tempfile.mkdtemp()) / "run.hdf5"
    f.write_bytes(b"123")
    w.station.bus.publish("data.exported", path=str(f), writer="hdf5", tags=["transmon"], project=lp["id"])
    assert _wait(lambda: len(up.done) > n0, app=app)
    assert s.item(up.done[-1]["catalog_id"])["project"] == lp["id"]
    assert [x["name"] for x in s.catalog(project=lp["id"])["items"]] == ["run.hdf5"]
