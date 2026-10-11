"""0.0.28：檔名打完直接按「▶ 開始量測」/ F5 也算數；預設不另存 _raw/；流程圖顯示 VNA 設定；滾輪調位數。"""
from __future__ import annotations

import time

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtTest import QTest


def _pump(sec=0.3):
    t = time.time() + sec
    while time.time() < t:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)


@pytest.fixture
def win(tmp_path):
    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.core.station import Station
    from labcontrol.scheme.templates import TEMPLATES

    st = Station.from_lab(simulate=True)
    st.sim_time_scale = 0.0
    w = LabControlWindow(st, None, root=str(tmp_path))
    w.show()
    w._load_scheme(TEMPLATES[list(TEMPLATES)[0]][1](), None)
    _pump(0.2)
    yield w
    if w.monitor.runner is not None:
        w.monitor.runner.stop()
        w.monitor.runner.wait(30)
    w.doc.dirty = False
    w.close()
    st.close()


def test_typed_file_name_counts_without_enter(win, tmp_path):
    fname = win.files.fname
    fname.setFocus()
    fname.selectAll()
    QTest.keyClicks(fname, "my_run")                  # 打字，不按 Enter、焦點不離開
    assert win.doc.scheme.output.get("file_name") != "my_run"
    win.start_measurement()                            # 工具列按鈕 / F5 不會拿走焦點
    assert win.doc.scheme.output.get("file_name") == "my_run"
    t = time.time()
    while win.monitor.busy and time.time() - t < 60:
        _pump(0.05)
    _pump(0.5)
    names = [p.name for p in tmp_path.rglob("*") if p.is_file()]
    assert any(n.startswith("my_run") for n in names), names
    assert not any("aaa" in n for n in names)
    assert not list(tmp_path.rglob("_raw"))           # 預設不另存 _raw/


def test_file_name_is_committed_while_typing(win):
    fname = win.files.fname
    fname.setFocus()
    fname.selectAll()
    QTest.keyClicks(fname, "abc")
    _pump(0.6)                                         # 停手 0.4 秒就寫進方案
    assert win.doc.scheme.output.get("file_name") == "abc"
    assert fname.text() == "abc"


def test_raw_backup_setting(monkeypatch):
    from labcontrol.measure import experiment

    assert experiment.raw_backup() is False
    monkeypatch.setattr(experiment, "setting", lambda k, d=None: True if k == "data.raw_backup" else d)
    assert experiment.raw_backup() is True


def test_flow_chart_shows_vna_settings():
    from labcontrol.apps.qt.workbench.graph_view import measure_details

    lines = measure_details({"start_freq": "5.019800000 GHz", "stop_freq": "5.0298 GHz", "points": 501,
                             "if_bw": "10 kHz", "averages": 4, "power": "-10 dBm"}, "vna")
    assert lines == ["5.0198 GHz → 5.0298 GHz", "501 點 · IF 10 kHz", "平均 4 · 功率 -10 dBm"]
    assert measure_details({"points": 101, "averages": 0}, "vna") == ["⚠ 頻率：用儀器目前值", "101 點", "平均 關"]
    lines = measure_details({}, "vna")
    assert lines[0] == "⚠ 頻率：用儀器目前值" and lines[-1] == "⚠ 點數、IF、平均：用儀器目前值"


def test_flow_node_is_taller_for_vna(win):
    from labcontrol.apps.qt.workbench.graph_view import H

    items = list(win.flow.view.items_by_id.values())
    vna = [i for i in items if i.color_key in ("vna", "measure")]
    assert vna and vna[0].h > H and len(vna[0].sub.split("\n")) >= 3


def test_digit_step():
    from labcontrol.apps.qt.digit_edit import digit_step

    assert digit_step("5.0248", 4, 1) == ("5.0348", 4)          # 游標左邊那一位（0.01）
    assert digit_step("5.0248", 6, -1) == ("5.0247", 6)
    assert digit_step("9.999", 1, 1) == ("10.999", 2)            # 進位，游標跟著同一位
    assert digit_step("-10", 3, 1) == ("-9", 2)
    assert digit_step("5.02 GHz", 4, 3) == ("5.05 GHz", 4)
    assert digit_step("on", 1, 1) is None and digit_step("1e9", 1, 1) is None


def test_wheel_line_edit_and_spinbox(qtbot=None):
    from labcontrol.apps.qt.digit_edit import DigitSpinBox, WheelLineEdit

    e = WheelLineEdit("5.0248")
    e.show()
    got = []
    e.stepped.connect(lambda: got.append(e.text()))
    e.setFocus()
    e.setCursorPosition(3)                                         # 5.0|248 → 動 0.0
    QTest.keyClick(e, QtCore.Qt.Key.Key_Up)
    assert e.text() == "5.1248" and got == ["5.1248"]

    def wheel(w, dy, pos):
        ev = QtGui.QWheelEvent(QtCore.QPointF(pos), QtCore.QPointF(w.mapToGlobal(pos)), QtCore.QPoint(0, 0),
                               QtCore.QPoint(0, dy), QtCore.Qt.MouseButton.NoButton,
                               QtCore.Qt.KeyboardModifier.NoModifier, QtCore.Qt.ScrollPhase.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(w, ev)
    e.setCursorPosition(6)
    wheel(e, -120, e.rect().center())
    assert e.text() != "5.1248" and len(got) == 2
    s = DigitSpinBox()
    s.setDecimals(4)
    s.setRange(0, 100)
    s.setSuffix(" GHz")
    s.setValue(5.0248)
    s.show()
    s.setFocus()
    s.lineEdit().setCursorPosition(4)                              # 5.02|48 → 0.01
    s.stepBy(1)
    assert s.value() == pytest.approx(5.0348)
    e.close()
    s.close()
