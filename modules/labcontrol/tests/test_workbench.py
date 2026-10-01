"""主視窗（四格工作台）的互動測試：用真的滑鼠事件拉線、拖曳節點、設定面板。"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("QT_QPA_PLATFORM") and os.name != "nt" and not os.environ.get("DISPLAY"),
    reason="需要 Qt 顯示環境（可設 QT_QPA_PLATFORM=offscreen）")


@pytest.fixture
def win(station):
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.scheme import Scheme

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = LabControlWindow(station, Scheme("t"))
    w.resize(1500, 950)
    w.show()
    app.processEvents()
    yield w
    w.doc.dirty = False
    if w.monitor.runner is not None:
        w.monitor.runner.stop()
        w.monitor.runner.wait(20)
    w.close()
    app.processEvents()


def _drag(view, a, b):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    vp = view.viewport()
    pa, pb = view.mapFromScene(a), view.mapFromScene(b)
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pa)
    for k in range(1, 6):
        QTest.mouseMove(vp, pa + (pb - pa) * k / 5)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pb)


def test_build_scheme_by_mouse(win, monkeypatch):
    from labcontrol.apps.qt.workbench import dialogs
    from labcontrol.scheme.graph import BODY, NEXT, START

    monkeypatch.setattr(dialogs, "edit_block", lambda *a, **k: None)   # 不開對話框
    doc, view = win.doc, win.flow.view
    loop = doc.add_node({"kind": "set", "target": "pair", "mode": "sweep"}, pos=(0, 150))
    meas = doc.add_node({"kind": "measure", "instrument": "VNA1"}, pos=(320, 150))
    assert set(doc.graph.loose()) == {loop.id, meas.id}
    view.rebuild()
    # 開始 → 掃描節點（從「開始」的輸出點拉到節點上）
    _drag(view, view.items_by_id[START].ports[NEXT].scene_center(),
          view.items_by_id[loop.id].sceneBoundingRect().center())
    # 掃描節點「每一點 ⟲」 → 量測
    _drag(view, view.items_by_id[loop.id].ports[BODY].scene_center(),
          view.items_by_id[meas.id].sceneBoundingRect().center())
    assert doc.graph.links[(START, NEXT)] == loop.id and doc.graph.links[(loop.id, BODY)] == meas.id
    assert doc.result.ok, doc.result.issues
    assert len(doc.result.loops) == 1
    # 拖動節點：位置寫回方案
    it = view.items_by_id[meas.id]
    before = doc.graph.pos[meas.id]
    _drag(view, it.sceneBoundingRect().center(), it.sceneBoundingRect().center() + type(it.pos())(60, 40))
    assert doc.graph.pos[meas.id] != before
    # 復原
    doc.undo()
    assert doc.graph.pos[meas.id] == before


def test_channel_panel_and_file_panel(win, monkeypatch):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.workbench.channels import ROLE

    edits = []
    win.channels.edit_requested.disconnect()
    win.channels.edit_requested.connect(edits.append)
    tree = win.channels.tree
    # 找到「VNA1 S21（量測）」與 pair 的輸出值，加到流程
    rows = {}
    for i in range(tree.topLevelItemCount()):
        g = tree.topLevelItem(i)
        for j in range(g.childCount()):
            info = g.child(j).data(0, ROLE) or {}
            spec = info.get("spec") or {}
            rows[(spec.get("kind"), spec.get("target") or spec.get("instrument"), spec.get("trace"))] = g.child(j)
    tree.setCurrentItem(rows[("set", "pair", None)])
    win.channels.add_selected()
    tree.setCurrentItem(rows[("measure", "VNA1", "S21")])
    win.channels.add_selected()
    assert len(edits) == 1        # Step 節點加入後會請流程圖開設定視窗
    doc = win.doc
    loop = next(b for b in doc.graph.nodes.values() if b.kind == "set")
    assert loop.is_loop and doc.graph.to_blocks()[0].children[0].kind == "measure"   # 量測自動接進迴圈
    # 檔案設置寫回方案 output
    fp = win.files
    fp.fname.setText("my run.hdf5")
    fp.fname.editingFinished.emit()
    fp.f_hdf5.setChecked(True)
    fp.tags.setText("A, B")
    fp.tags.editingFinished.emit()
    assert doc.scheme.output["file_name"] == "my run.hdf5"
    assert doc.scheme.output["formats"] == ["labber", "hdf5"] and doc.scheme.output["tags"] == ["A", "B"]
    fp.delay.setValue(0.25)
    assert doc.result.config["sweep"][-1]["settle"] >= 0.25
    QtWidgets.QApplication.processEvents()


def test_step_dialog_center_span_and_log(win):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.workbench.dialogs import StepDialog

    doc = win.doc
    b = doc.add_node({"kind": "set", "target": "pair", "mode": "sweep"}, after="start")
    dlg = StepDialog(win, doc, doc.node(b.id))
    dlg.r_cs.setChecked(True)
    dlg.a.setValue(158.61)
    dlg.bb.setValue(0.02)
    dlg.r_pts.setChecked(True)
    dlg.points.setValue(5)
    dlg.alternate.setChecked(True)
    dlg.after.setCurrentIndex(dlg.after.findData("stay"))
    nb = dlg.result_block()
    assert nb.start == pytest.approx(158.60) and nb.stop == pytest.approx(158.62) and nb.points == 5
    assert nb.range_mode == "centerspan" and nb.alternate and nb.after == "stay"
    dlg.interp.setCurrentIndex(1)       # 對數
    assert dlg._values() is not None
    dlg.r_single.setChecked(True)
    dlg.value.setValue(158.6)
    assert dlg.result_block().mode == "fixed"
    dlg.reject()
    QtWidgets.QApplication.processEvents()


def test_run_from_window(win, tmp_path):
    import time

    from PyQt6 import QtWidgets

    from labcontrol.scheme import Block, Scheme

    s = Scheme("run", [Block("set", target="pair", mode="sweep", start=158.60, stop=158.602, step=0.0005, unit="mA",
                             children=[Block("measure", instrument="VNA1", traces=["S21"],
                                             settings={"points": 101})])],
               output={"formats": ["hdf5"], "file_name": "w.hdf5", "root": str(tmp_path)})
    win.doc.replace_scheme(s)
    assert win.doc.result.ok
    win.start_measurement()
    t0 = time.time()
    while win.monitor.busy and time.time() - t0 < 30:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)
    for _ in range(50):
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)
    assert not win.monitor.busy
    assert win.monitor.progress.value() == win.monitor.progress.maximum() == 5
    assert list(tmp_path.rglob("w.h5"))                 # 原生 HDF5 匯出
    assert list(tmp_path.rglob("_raw/w_*.lm.h5"))       # 逐點原始檔


def test_server_from_main_window_updates_channels(win):
    from PyQt6 import QtWidgets

    srv = win.open_server()
    assert srv.isVisible()
    n0 = win.channels.tree.topLevelItemCount()
    win.station.add("DC9", "yokogawa.gs200", address="X", source={"limits": [-0.1, 0.1]})
    QtWidgets.QApplication.processEvents()
    assert win.channels.tree.topLevelItemCount() == n0 + 1
    assert win.doc.catalog.target("DC9") is not None
    win.station.remove("DC9")
    QtWidgets.QApplication.processEvents()
    assert win.doc.catalog.target("DC9") is None


def test_light_dark_theme_switch(win, tmp_path, monkeypatch):
    """0.0.6：淺色 / 深色即時切換（主視窗 + 儀器伺服器同時開著），並寫回 settings.yaml。"""
    from PyQt6 import QtGui, QtWidgets

    from labcontrol.apps.qt import theme
    from labcontrol.apps.qt.server import InstrumentServerWindow
    from labcontrol.settings import settings

    app = QtWidgets.QApplication.instance()
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("# 我的註解\nlabber: {user: ME}\n", encoding="utf-8")
    monkeypatch.setattr(settings(), "path", cfg)
    srv = InstrumentServerWindow(win.station)
    srv.show()
    try:
        # 登記的元件會在切換時更新
        seen = []
        lab = QtWidgets.QLabel()
        theme.on_change(lab, lambda w: seen.append(theme.is_dark()))
        assert theme.set_mode("dark") is True
        app.processEvents()
        assert theme.is_dark() and seen[-1] is True
        assert app.palette().color(QtGui.QPalette.ColorRole.Base).lightness() < 60
        assert win.flow.view.backgroundBrush() is not None
        img = win.flow.view.grab().toImage()
        assert img.pixelColor(5, 5).lightness() < 80          # 流程圖背景變暗
        mon = win.monitor.plot.backgroundBrush().color()
        assert mon.lightness() < 60
        assert "🌙" not in win.act_theme.text() and srv.act_theme.text() == "☀"
        text = cfg.read_text(encoding="utf-8")
        assert "# 我的註解" in text and "theme: dark" in text and "user: ME" in text   # 保留使用者內容
        assert settings().get("app.theme") == "dark"

        theme.toggle()
        app.processEvents()
        assert not theme.is_dark() and seen[-1] is False
        assert app.palette().color(QtGui.QPalette.ColorRole.Base).lightness() > 200
        assert win.flow.view.grab().toImage().pixelColor(5, 5).lightness() > 200
        assert "theme: light" in cfg.read_text(encoding="utf-8")

        theme.apply(mode="bogus")              # 設定錯誤 → 淺色，不當掉
        assert theme.mode() == "light"
        theme.apply(mode="system")             # 跟隨系統（offscreen 沒有深色）
        assert theme.mode() == "system"
        del lab
        app.processEvents()
        theme.set_mode("light", save=False)
    finally:
        srv.close()
        theme.set_mode("light", save=False)


def test_theme_tables_complete():
    from labcontrol.apps.qt import theme

    assert set(theme.LIGHT) == set(theme.DARK)
    assert "theme" in open(__import__("labcontrol.paths", fromlist=["x"]).DEFAULTS_DIR / "settings.yaml",
                           encoding="utf-8").read()
