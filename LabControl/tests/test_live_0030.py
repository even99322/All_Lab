"""0.0.30：數據集散地＋專案資料夾、電流目標旋鈕（±200 mA、目前值、全域最大速率 10 mA/s）、
量測方塊的中心頻率 / span 與「套用儀器目前值」、即時數據的 x / y 軸運算。"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import numpy as np
import pytest

from labcontrol.core.axis_math import AxisMath, AxisMathError, compile_axis
from labcontrol.data import naming


def _pump(sec=0.3, until=None):
    from PyQt6 import QtWidgets

    t = time.time() + sec
    while time.time() < t:
        QtWidgets.QApplication.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until is not None else True


# ---- 存檔位置 -------------------------------------------------------------------------------
def test_default_root_is_desktop_lab_data_and_old_default_migrates(tmp_path, monkeypatch):
    from labcontrol import settings as st_mod

    defaults = st_mod._load_yaml(st_mod.DEFAULTS_DIR / st_mod.SETTINGS_FILE)
    assert defaults["data"]["root"] == "~/Desktop/數據集散地"
    assert defaults["data"]["folder_pattern"] == "{project}/{yyyy}/{mm}/Data_{mmdd}"
    assert defaults["live"]["max_ramp_mA_s"] == 10 and defaults["live"]["dial_range_mA"] == 200
    user = tmp_path / "settings.yaml"
    user.write_text("data: {root: C:/Users/QEL/Desktop/VNA_data, folder_pattern: '{yyyy}/{mm}/Data_{mmdd}'}\n",
                    encoding="utf-8")
    s = st_mod.Settings.load(user)
    assert s.get("data.root") == "~/Desktop/數據集散地"                      # 舊預設 → 新預設
    assert s.get("data.folder_pattern") == "{project}/{yyyy}/{mm}/Data_{mmdd}"
    user.write_text("data: {root: D:/my_data, folder_pattern: '{yyyy}'}\n", encoding="utf-8")
    s = st_mod.Settings.load(user)
    assert (s.get("data.root"), s.get("data.folder_pattern")) == ("D:/my_data", "{yyyy}")    # 自己改的不動
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert naming.data_root("~/Desktop/數據集散地") == Path(os.path.expanduser("~")) / "Desktop" / "數據集散地"


def test_project_folders(tmp_path, monkeypatch):
    now = dt.datetime(2026, 10, 12)
    assert naming.project_folder({}) == "未分專案"
    assert naming.project_folder({"lab_project": {"id": "3", "name": "YIG: mirror"}}) == "YIG_ mirror"
    assert naming.project_folder({"lab_project": {"id": "3", "name": "YIG"}, "project_folder": "舊的 YIG"}) == "舊的 YIG"
    assert naming.day_folder(tmp_path, now=now, project="YIG") == tmp_path / "YIG" / "2026" / "10" / "Data_1012"
    for d in ("YIG", "magnon", "_raw", ".git"):
        (tmp_path / d).mkdir()
    (tmp_path / "a.txt").write_text("x")
    assert naming.list_project_folders(tmp_path) == ["magnon", "YIG"]
    naming.remember_folder("7", "magnon")
    assert naming.remembered_folder("7") == "magnon" and naming.remembered_folder("8") == ""
    naming.remember_folder("7", "")
    assert naming.remembered_folder("7") == ""


@pytest.fixture
def win(tmp_path, monkeypatch):
    pytest.importorskip("PyQt6")
    lab = tmp_path / "LAB"                       # 自己的 LAB：不影響之後其他測試讀到的 instruments.yaml 等
    lab.mkdir()
    shared = Path(os.environ["LAB_CONTROL_HOME"]) / "settings.yaml"
    if shared.is_file():
        (lab / "settings.yaml").write_text(shared.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("LAB_CONTROL_HOME", str(lab))
    from PyQt6 import QtWidgets

    if os.environ.get("QT_QPA_PLATFORM") != "offscreen" and not os.environ.get("DISPLAY") and os.name != "nt":
        pytest.skip("需要 Qt 顯示環境（可設 QT_QPA_PLATFORM=offscreen）")
    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.core.station import Station
    from labcontrol.scheme.templates import TEMPLATES

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    st = Station.from_lab(simulate=True)
    st.sim_time_scale = 0.0
    w = LabControlWindow(st, None, root=str(tmp_path / "數據集散地"))
    w.show()
    w._load_scheme(TEMPLATES[list(TEMPLATES)[0]][1](), None)
    _pump(0.2)
    yield w
    w.doc.dirty = False
    w.close()
    st.close()


def test_files_panel_project_folder(win, tmp_path):
    root = tmp_path / "data root"
    (root / "YIG 舊資料").mkdir(parents=True)
    (root / "magnon").mkdir()
    fp = win.files
    fp.root.setText(str(root))
    fp.root.editingFinished.emit()
    cb = fp.proj_folder
    assert [cb.itemText(i) for i in range(cb.count())] == ["magnon", "YIG 舊資料"]
    assert cb.isEnabled() and "自動：未分專案" in cb.lineEdit().placeholderText()
    assert "未分專案" in fp.where.text()
    cb.setCurrentIndex(1)
    cb.activated.emit(1)
    assert win.doc.scheme.output["project_folder"] == "YIG 舊資料"
    assert "YIG 舊資料" in fp.where.text()
    exp = win.doc.result.experiment(win.station)
    out = exp.plan_output(now=dt.datetime(2026, 10, 12))
    assert out.folder == root / "YIG 舊資料" / "2026" / "10" / "Data_1012"
    cb.setEditText("")                                                  # 空白 = 自動
    fp._folder_chosen()
    assert "project_folder" not in win.doc.scheme.output
    fp.pattern.setText("{yyyy}/{mm}")
    fp.pattern.editingFinished.emit()
    assert not cb.isEnabled()                                           # 規則沒有 {project}


# ---- 電流目標旋鈕 ---------------------------------------------------------------------------------
def test_digit_dial(win):
    from PyQt6 import QtCore, QtGui, QtWidgets

    from labcontrol.apps.qt.live.digit_dial import DigitDial

    d = DigitDial("A", 0.2)
    d.show()
    got = []
    d.edited.connect(got.append)
    d.set_value(0.1)                                                    # 100.000 mA，不發出 edited
    assert [c.label.text() for c in d.cells] == list("100000") and d.sign.label.text() == "+" and got == []
    d.step(1, +1)                                                       # 十位 +1 → 110 mA
    assert d.value() == pytest.approx(0.110) and got == [pytest.approx(0.110)]
    d.step(2, +5)                                                       # 百位 +5 → 上限 200 mA
    assert d.value() == pytest.approx(0.2)
    assert not d.cells[0].up.isEnabled()
    d.step(None, -1)                                                    # 正負號
    assert d.value() == pytest.approx(-0.2) and d.sign.label.text() == "-"
    d.set_value(0.0123456)
    assert d.value() == pytest.approx(0.012346)                          # 1 µA 解析度
    cell = d.cells[-1]                                                  # 0.001 mA 那一位：滾輪
    pos = QtCore.QPointF(cell.rect().center())
    ev = QtGui.QWheelEvent(pos, QtCore.QPointF(cell.mapToGlobal(pos.toPoint())), QtCore.QPoint(0, 0),
                           QtCore.QPoint(0, 240), QtCore.Qt.MouseButton.NoButton,
                           QtCore.Qt.KeyboardModifier.NoModifier, QtCore.Qt.ScrollPhase.NoScrollPhase, False)
    QtWidgets.QApplication.sendEvent(cell, ev)
    assert d.value() == pytest.approx(0.012348)
    d.set_value(5.0)                                                    # 超出範圍 → 停在上限
    assert d.value() == pytest.approx(0.2)
    d.close()


def test_dc_dial_ramps_at_capped_rate(win, monkeypatch):
    dc = win.live.dc
    win.popout_live()
    win.live.connect_all()
    assert _pump(15, lambda: any(c.dial is not None for c in dc.cards))
    card = next(c for c in dc.cards if c.dial is not None)
    assert card.range_badge.text() == "Range: 0.2 A"
    assert _pump(5, lambda: card.now_value.text() not in ("—", ""))
    lv = dc.levels[card.members[0]]
    assert card.now_value.text() == f"{lv * 1e3:+.3f} mA"
    assert card.dial.value() == pytest.approx(lv, abs=1e-6)              # 沒在調：目標跟著目前值
    # 全域最大調整速率：預設 10 mA/s；儀器的 ramp_rate 比較慢時用儀器的
    assert dc.max_rate_mA_s() == pytest.approx(10.0)
    inst = min(float(dc.sources[m]["rate"]) for m in card.members)
    assert dc.capped_rate(card.members, None) == pytest.approx(min(inst, 10e-3))
    assert dc.capped_rate(card.members, 0.05) == pytest.approx(10e-3)    # 指定 50 mA/s → 10
    assert dc.capped_rate(card.members, 1e-4) == pytest.approx(1e-4)
    dc.max_rate.setText("0.2")
    dc.max_rate.editingFinished.emit()
    assert dc.max_rate_mA_s() == pytest.approx(0.2)
    assert json.loads(dc._prefs_file().read_text(encoding="utf-8"))["max_rate_mA_s"] == 0.2
    assert dc.capped_rate(card.members, None) == pytest.approx(min(inst, 2e-4))
    calls = []
    real = dc.backend.group_ramp
    monkeypatch.setattr(dc.backend, "group_ramp", lambda refs, t, r=None: calls.append((refs, t, r)) or real(refs, t, r))
    card._dial_timer.setInterval(50)
    card.dial.step(-3, +2)                                              # +0.002 mA
    target = card.dial.value()
    assert _pump(5, lambda: bool(calls))
    assert calls[0][1] == pytest.approx(target) and calls[0][2] == pytest.approx(min(inst, 2e-4))
    st = win.station
    assert _pump(10, lambda: abs(st.parameter(card.members[0]).get() - target) < 1e-9)
    dc.set_max_rate_mA_s(10.0)


# ---- 量測方塊：中心 / span、套用目前值 ---------------------------------------------------------------
def test_measure_dialog_center_span_and_current(win):
    from labcontrol.apps.qt.workbench.dialogs import MeasureDialog

    b = next(x for x in win.doc.graph.nodes.values() if x.kind == "measure")
    b.settings = {**(b.settings or {}), "start_freq": "5.0198 GHz", "stop_freq": "5.0298 GHz"}
    dlg = MeasureDialog(win, win.doc, b)
    assert dlg.freq_mode is not None and dlg.freq_mode.currentData() == "startstop"
    dlg.freq_mode.setCurrentIndex(1)                                    # → 中心 / span（換算）
    assert set(dlg._fields) >= {"center_freq", "span"} and "start_freq" not in dlg._fields
    assert dlg._fields["center_freq"][1].value() == pytest.approx(5.0248)
    assert dlg._fields["span"][1].value() == pytest.approx(10.0)
    s = dlg.result_block().settings
    assert s["center_freq"].endswith("GHz") and "start_freq" not in s and "stop_freq" not in s
    from labcontrol.core.units import parse_quantity

    assert parse_quantity(s["center_freq"], "Hz") == pytest.approx(5.0248e9)
    assert parse_quantity(s["span"], "Hz") == pytest.approx(10e6)
    dlg.freq_mode.setCurrentIndex(0)                                    # 換回來
    assert dlg._fields["start_freq"][1].value() == pytest.approx(5.0198)
    # 套用儀器目前值：讀模擬 VNA 現在的設定
    st = win.station
    st.ensure_connected([f"{b.instrument}.start_freq"])
    st.parameter(f"{b.instrument}.start_freq").set(4.9e9)
    st.parameter(f"{b.instrument}.points").set(777)
    dlg.apply_current()
    assert _pump(10, lambda: dlg.cur_note.text().startswith("✔"))
    assert dlg._fields["start_freq"][1].value() == pytest.approx(4.9)
    assert dlg._fields["points"][1].value() == 777
    dlg.freq_mode.setCurrentIndex(1)
    dlg.apply_current()
    assert _pump(10, lambda: "已填入" in dlg.cur_note.text() and dlg.b_current.isEnabled())
    assert dlg._fields["center_freq"][1].value() > 0
    dlg.close()
    from labcontrol.apps.qt.workbench.graph_view import measure_details

    assert measure_details({"center_freq": "5.0248 GHz", "span": "10 MHz"}, "vna")[0].startswith("中心 5.0248 GHz")


# ---- 軸運算 ----------------------------------------------------------------------------------------
def test_axis_math():
    x = np.array([1.0, 10.0, 100.0])
    y = np.array([1.0, 2.0, 3.0])
    assert np.allclose(compile_axis("y = y**2", "y")(x, y), [1, 4, 9])
    assert np.allclose(compile_axis("log10(x)", "x")(x, y), [0, 1, 2])
    assert np.allclose(compile_axis("np.log(x)", "x")(x, y), np.log(x))
    assert np.allclose(compile_axis("y - mean(y)", "y")(x, y), [-1, 0, 1])
    assert np.allclose(compile_axis("y^2", "y")(x, y), [1, 4, 9])
    assert compile_axis("", "y") is None and compile_axis("y", "y") is None and compile_axis("y = y", "y") is None
    for bad in ('__import__("os")', "x.__class__", "open('f')", "(1).real", "y if x else y", "9**9**9", "y +"):
        with pytest.raises(AxisMathError):
            compile_axis(bad, "y")
    m = AxisMath()
    m.set("x/1e9", "y**2")
    nx, ny = m.apply(x, y)
    assert np.allclose(nx, x / 1e9) and np.allclose(ny, y ** 2)
    with pytest.raises(AxisMathError):
        m.set("x", "bad(")
    assert m.y_text == "y**2"                                           # 寫錯：原本的不變


def test_live_vna_axis_math(win):
    win.popout_live()
    vna = win.live.vna
    vna.set_compact(False)
    x = np.linspace(5e9, 5.1e9, 11)
    z = np.full(11, 0.5 + 0j)
    vna.plots.set_views(["db"])
    vna.plots.update_data("S21", x, z, "Hz")
    cv = vna.plots.cells["db"].canvas
    xx, yy = cv.data["db"]
    assert np.allclose(yy, 20 * np.log10(0.5))
    vna.y_expr.setText("y**2")
    vna.x_expr.setText("x/1e9")
    vna._math_changed()
    xx, yy = cv.data["db"]
    assert np.allclose(yy, (20 * np.log10(0.5)) ** 2) and np.allclose(xx, x / 1e9)
    assert "y = y**2" in vna.plots.cells["db"].head.text()
    assert cv.plot.getAxis("left").labelText.startswith("y = y**2")
    from labcontrol.apps.qt.live.vna_panel import _UIState

    assert _UIState().load()["y_expr"] == "y**2"
    vna.y_expr.setText("nope(")
    vna._math_changed()
    assert vna.math_err.text().startswith("⚠") and np.allclose(cv.data["db"][1], (20 * np.log10(0.5)) ** 2)
    vna.y_expr.setText("")
    vna.x_expr.setText("")
    vna._math_changed()
    assert np.allclose(cv.data["db"][1], 20 * np.log10(0.5)) and cv.plot.getAxis("left").labelText == "振幅 (dB)"
