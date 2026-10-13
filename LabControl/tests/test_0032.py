"""0.0.32：量測電腦對應（settings lab_nodes）、在程式裡改 instruments.yaml 的 node:、舊檔依位址補上 node:。"""
from __future__ import annotations

import os

import pytest
import yaml

from tests.test_0031 import LAB2, _pump

OLD_FILE = """# 註解留著
instruments:
  DC1:
    driver: yokogawa.gs200
    label: DC1 (90ZC38697)
    address: USB0::0x0B21::0x0039::90ZC38697::0::INSTR
  DC5:
    driver: yokogawa.gs200
    address: USB0::0x0B21::0x0039::NEWSERIAL::0::INSTR   # LAB-2 的電源
  VNA1:
    driver: rs.zna
    address: TCPIP0::192.168.1.11::INSTR
  VNA2:
    driver: rs.zna
    address: TCPIP0::192.168.1.12::INSTR
    enabled: false
  SHFQC1:
    driver: zi.shfqc
  magnet_B:
    driver: virtual.interleaved_pair
    group: magnet
    sources: [DC1, DC2]
"""


def test_old_file_gets_nodes_by_address():
    from labcontrol.core.config import migrate_instrument_nodes

    new = migrate_instrument_nodes(OLD_FILE)
    d = yaml.safe_load(new)["instruments"]
    assert d["DC1"]["node"] == "LAB-1" and d["VNA1"]["node"] == "LAB-1" and d["VNA2"]["node"] == "LAB-2"
    assert d["SHFQC1"]["node"] == ["LAB-1", "LAB-2"]
    assert "node" not in d["DC5"] and "node" not in d["magnet_B"]       # 不認得的、電磁鐵組：不動
    assert "# 註解留著" in new and "# LAB-2 的電源" in new
    assert migrate_instrument_nodes(new) == new                           # 已經有 node：不再動


def test_migration_runs_once(tmp_path, monkeypatch):
    from labcontrol import paths

    (tmp_path / "instruments.yaml").write_text(OLD_FILE, encoding="utf-8")
    paths._migrate_nodes(tmp_path)
    text = (tmp_path / "instruments.yaml").read_text(encoding="utf-8")
    assert "node: LAB-1" in text and (tmp_path / "instruments.yaml.bak-0.0.32").is_file()
    (tmp_path / "instruments.yaml").write_text(OLD_FILE, encoding="utf-8")
    paths._migrate_nodes(tmp_path)                                        # 做過一次就不再做
    assert "node:" not in (tmp_path / "instruments.yaml").read_text(encoding="utf-8")


def test_set_instrument_node():
    from labcontrol.core.config import set_instrument_node

    t = set_instrument_node(OLD_FILE, "DC5", "LAB-2")
    assert yaml.safe_load(t)["instruments"]["DC5"]["node"] == "LAB-2"
    t = set_instrument_node(t, "DC5", ["LAB-1", "LAB-2"])
    assert yaml.safe_load(t)["instruments"]["DC5"]["node"] == ["LAB-1", "LAB-2"]
    t = set_instrument_node(t, "DC5", [])
    assert "node" not in yaml.safe_load(t)["instruments"]["DC5"] and "# LAB-2 的電源" in t


def test_lab_nodes_setting_maps_without_the_other_pcs_instruments():
    """LAB-2 的 instruments.yaml 只有自己的儀器：靠 settings 的 lab_nodes 還是能把 LAB-1 的方案換過來（反之亦然）。"""
    from labcontrol import Station
    from labcontrol.scheme import build_catalog
    from labcontrol.scheme.remap import remap_scheme, role_lists
    from labcontrol.scheme.templates import load_template
    from labcontrol.settings import setting

    lab_nodes = setting("lab_nodes")
    assert lab_nodes[0] == {"node": "LAB-1", "magnet": ["magnet_A", "magnet_B"], "vna": ["VNA1"]}
    own = {k: dict(v, address="X") for k, v in LAB2.items()
           if k in ("DC5", "DC6", "DC7", "DC8", "VNA2", "magnet_C", "magnet_D")}
    assert "LAB-1" not in role_lists(own)
    roles = role_lists(own, lab_nodes)
    assert roles["LAB-1"]["magnet"] == ["magnet_A", "magnet_B"]
    st = Station({"simulate": True, "instruments": own})
    s = load_template("05_two_layer_one_file")
    assert remap_scheme(s, build_catalog(st), roles) == {"magnet_A": "magnet_C", "magnet_B": "magnet_D",
                                                         "VNA1": "VNA2"}
    st.close()
    # 設定決定順序：LAB-2 的第 1 組改成 magnet_D
    roles = role_lists(own, [{"node": "LAB-2", "magnet": ["magnet_D", "magnet_C"]}] + lab_nodes[:1])
    assert roles["LAB-2"]["magnet"][:2] == ["magnet_D", "magnet_C"]


@pytest.fixture
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets

    if os.environ.get("QT_QPA_PLATFORM") != "offscreen" and not os.environ.get("DISPLAY") and os.name != "nt":
        pytest.skip("需要 Qt 顯示環境")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_node_map_dialog_edits_both(qapp, tmp_path):
    from labcontrol.apps.qt.node_map_dialog import FOLLOW, NodeMapDialog

    f = tmp_path / "instruments.yaml"
    f.write_text(OLD_FILE, encoding="utf-8")
    dlg = NodeMapDialog(None, f, None)
    assert [e["node"] for e in dlg.lab_nodes()] == ["LAB-1", "LAB-2"]
    assert dlg.combos["magnet_B"].currentText() == FOLLOW
    dlg.table.item(1, 1).setText("magnet_C、magnet_D")
    assert dlg.lab_nodes()[1]["magnet"] == ["magnet_C", "magnet_D"]
    dlg.combos["DC5"].setCurrentText("LAB-2")
    dlg.combos["VNA1"].setCurrentText("LAB-1, LAB-2")
    assert dlg.apply_instruments() == ["DC5", "VNA1"]
    d = yaml.safe_load(f.read_text(encoding="utf-8"))["instruments"]
    assert d["DC5"]["node"] == "LAB-2" and d["VNA1"]["node"] == ["LAB-1", "LAB-2"]
    assert (tmp_path / "instruments.yaml.bak").is_file()
    dlg.close()


def test_window_node_map_action(qapp, tmp_path, monkeypatch):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt import node_map_dialog
    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.core.station import Station
    from tests.conftest import SIM_LAB

    st = Station(SIM_LAB)
    w = LabControlWindow(st, None, root=str(tmp_path / "data"))
    assert any("量測電腦對應" in t for tb in w.findChildren(QtWidgets.QToolBar) for t in [a.text() for a in tb.actions()])
    monkeypatch.setattr(node_map_dialog, "edit_node_map",
                        lambda parent, path: {"lab_nodes": [{"node": "LAB-9", "magnet": ["mX"], "vna": []}],
                                              "changed": [], "node_yaml": {}})
    w.edit_node_map()
    _pump(0.1)
    assert w.doc.roles["LAB-9"]["magnet"][0] == "mX"
    w.doc.dirty = False
    w.close()
    st.close()
