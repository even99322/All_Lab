"""1.0.1：知道方案在哪台量測電腦執行時，別台的電磁鐵組 / VNA 一律換成這台的（就算清單裡看得到別台的 VNA1）；
「這台電腦是」設定。"""
from __future__ import annotations

from tests.test_0031 import LAB2


def _all_station():
    from labcontrol import Station

    inst = {k: dict(v, address="X") for k, v in LAB2.items()}
    return inst, Station({"simulate": True, "instruments": inst, "_node_strict": False})


def test_node_aware_remap_even_when_other_pc_instruments_are_listed():
    from labcontrol.scheme import build_catalog
    from labcontrol.scheme.remap import current_node, remap_scheme, role_lists
    from labcontrol.scheme.templates import load_template
    from labcontrol.settings import setting

    inst, st = _all_station()
    cat = build_catalog(st)
    roles = role_lists(inst, setting("lab_nodes"))
    s = load_template("05_two_layer_one_file")
    assert remap_scheme(s, cat, roles) == {}                                   # 不知道在哪台：VNA1 看得到就不換
    m = remap_scheme(s, cat, roles, "lab-2")
    assert m == {"magnet_A": "magnet_C", "magnet_B": "magnet_D", "VNA1": "VNA2"}
    assert remap_scheme(s, cat, roles, "LAB-1") == {"magnet_C": "magnet_A", "magnet_D": "magnet_B", "VNA2": "VNA1"}
    assert remap_scheme(s, cat, roles, "LAB-1") == {}
    assert current_node(roles, {"desktop-x", "lab-2"}) == "LAB-2" and current_node(roles, {"pc"}) is None
    st.close()


def test_lab_node_self_setting_is_one_of_my_names(monkeypatch):
    from labcontrol.core import nodes
    from labcontrol.settings import settings

    s = settings()
    old = s.get("lab_node_self", "")
    try:
        s.set("lab_node_self", "LAB-2")
        assert "lab-2" in nodes.my_names()
    finally:
        s.set("lab_node_self", old)


def test_window_uses_target_node(tmp_path, monkeypatch):
    import os

    import pytest

    pytest.importorskip("PyQt6")
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen" and not os.environ.get("DISPLAY") and os.name != "nt":
        pytest.skip("需要 Qt 顯示環境")
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.scheme.templates import load_template
    from labcontrol.settings import settings

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    inst, st = _all_station()
    st.config["instruments"] = inst
    s = settings()
    old = s.get("lab_node_self", "")
    s.set("lab_node_self", "LAB-2")
    try:
        w = LabControlWindow(st, None, root=str(tmp_path / "data"))
        assert w.doc.run_node == "LAB-2"
        w.doc.replace_scheme(load_template("06_flux"))
        assert [b.instrument for b in w.doc.scheme.flat() if b.kind == "measure"] == ["VNA2"]
        assert [b.target for b in w.doc.scheme.flat() if b.kind == "set"] == ["magnet_C"]
        w.doc.dirty = False
        w.close()
    finally:
        s.set("lab_node_self", old)
        st.close()


def test_dialog_self_node(tmp_path):
    import os

    import pytest

    pytest.importorskip("PyQt6")
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen" and not os.environ.get("DISPLAY") and os.name != "nt":
        pytest.skip("需要 Qt 顯示環境")
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.node_map_dialog import NodeMapDialog

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    f = tmp_path / "instruments.yaml"
    f.write_text("instruments:\n  VNA2:\n    driver: rs.zna\n", encoding="utf-8")
    dlg = NodeMapDialog(None, f, None, "")
    assert dlg.self_node() == ""
    dlg.self_combo.setCurrentText("LAB-2")
    assert dlg.self_node() == "LAB-2"
    dlg.self_combo.setCurrentText(NodeMapDialog.CLIENT)
    assert dlg.self_node() == "-"
    dlg.close()


def test_export_thread_opens_reader_on_gui_thread(monkeypatch):
    import os
    import threading

    import pytest

    pytest.importorskip("PyQt6")
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen" and not os.environ.get("DISPLAY") and os.name != "nt":
        pytest.skip("需要 Qt 顯示環境")
    from PyQt6 import QtWidgets

    from labcontrol import Station
    from labcontrol.apps.qt.workbench import monitor_panel
    from tests.conftest import SIM_LAB
    from tests.test_0031 import _pump

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    st = Station(SIM_LAB)
    panel = monitor_panel.MonitorPanel(st)
    seen = []
    monkeypatch.setattr(monitor_panel, "open_in_lablogviewer",
                        lambda station, paths, split: seen.append((threading.current_thread() is threading.main_thread(),
                                                                   paths, split)))
    t = threading.Thread(target=lambda: panel.open_reader.emit(["a.nc"], False))
    t.start()
    t.join()
    assert _pump(2.0, lambda: bool(seen))
    assert seen[0] == (True, ["a.nc"], False)
    panel.close()
    st.close()
