"""0.0.24：電磁鐵組——配對的 DC 收在電磁鐵組底下（儀器伺服器、儀器參數列表），平時只看到電磁鐵組；
GS820 的兩個通道也能配成一組；「＋ 電磁鐵組…」建立。"""
from __future__ import annotations

import time

import pytest
import yaml

from labcontrol.core.grouping import default_groups
from labcontrol.core.station import Station
from labcontrol.remote.groups import all_names, build_groups, nest_members

LAB = {
    "DC1": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::A1::0::INSTR"},
    "DC2": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::A2::0::INSTR"},
    "DC5": {"driver": "yokogawa.gs820", "address": "USB0::0x0B21::0x002C::91R712667::0::INSTR"},
    "DC6": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::A6::0::INSTR"},
    "magnet_A": {"driver": "virtual.interleaved_pair", "group": "magnet", "sources": ["DC1", "DC2"]},
    "magnet_C": {"driver": "virtual.interleaved_pair", "group": "magnet", "sources": ["DC5.ch1", "DC5.ch2"]},
}


def _entries():
    return [{"name": n, "opts": dict(o), "in_file": True} for n, o in LAB.items()]


def test_members_nest_under_magnets():
    g = build_groups(_entries(), [], [], "ME")[0]
    top = [i["name"] for i in g["items"]]
    assert top == ["DC6", "magnet_A", "magnet_C"]                    # 平時只看到電磁鐵組（和沒配對的 DC6）
    by = {i["name"]: i for i in g["items"]}
    assert [m["name"] for m in by["magnet_A"]["members"]] == ["DC1", "DC2"]
    assert [m["name"] for m in by["magnet_C"]["members"]] == ["DC5"]    # GS820 兩個通道 → 一台
    assert set(all_names(g["items"])) == set(LAB)


def test_nest_never_takes_a_member_twice():
    items = [{"name": "DC5", "options": {}}, {"name": "m1", "options": {"sources": ["DC5.ch1", "DC6"]}},
             {"name": "m2", "options": {"sources": ["DC5.ch2", "DC7"]}}]
    nest_members(items)
    assert [i["name"] for i in items] == ["m1", "m2"]
    assert [m["name"] for m in items[0]["members"]] == ["DC5"] and items[1]["members"] == []


def test_remote_node_magnets_from_node_status():
    """別台量測節點的電磁鐵組（Hub 登錄沒有虛擬儀器）從節點狀態補上，DC 收在底下。"""
    reg = [{"key": "usb:0b21:0039:a1", "entries": [{"host": "PC-1", "name": "DC1", "configured": True,
                                                     "connected": True, "online": True}]},
           {"key": "usb:0b21:0039:a2", "entries": [{"host": "PC-1", "name": "DC2", "configured": True,
                                                     "online": True}]}]
    nodes = [{"name": "LAB-1", "host": "PC-1", "online": True, "instruments": [
        {"name": "magnet_A", "virtual": True, "connected": True, "driver": "virtual.interleaved_pair",
         "options": {"driver": "virtual.interleaved_pair", "sources": ["DC1", "DC2"]}}]}]
    g = next(x for x in build_groups([], reg, nodes, "ME") if x.get("node") == "LAB-1")
    assert [i["name"] for i in g["items"]] == ["magnet_A"]
    assert [m["name"] for m in g["items"][0]["members"]] == ["DC1", "DC2"]
    assert g["items"][0]["connected"] and g["items"][0]["members"][0]["connected"]


def test_gs820_channel_pair_works_and_is_a_live_group():
    st = Station({"simulate": True, "_lab_plugins": False, "sim_time_scale": 0.0,
                  "instruments": {k: v for k, v in LAB.items() if k in ("DC5", "magnet_C")}})
    st.connect(["magnet_C"])
    assert st.instruments["DC5"].connected
    st.parameter("magnet_C.level").set(1e-4)
    a = st.parameter("DC5.ch1.level").get()
    b = st.parameter("DC5.ch2.level").get()
    assert (a + b) / 2 == pytest.approx(1e-4, abs=1e-6)
    groups = default_groups(st)
    assert groups == [{"name": "magnet_C", "members": ["DC5.ch1.level", "DC5.ch2.level"], "virtual": "magnet_C"}]


# ---- GUI -----------------------------------------------------------------------------------------
def _pump(app, cond, s=10.0):
    t = time.time()
    while time.time() - t < s:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_magnet_dialog_and_server_tree(tmp_path, monkeypatch):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.server import InstrumentServerWindow
    from labcontrol.apps.qt.server.magnet_dialog import MagnetDialog, source_outputs

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "instruments.yaml"
    conf = {k: v for k, v in LAB.items() if k != "magnet_C"}
    conf["DC5"] = dict(LAB["DC5"], node="LAB-2")
    path.write_text(yaml.safe_dump({"instruments": conf}), encoding="utf-8")
    st = Station.from_file(path, simulate=True)
    assert [r for r, _t in source_outputs(st)] == ["DC1", "DC2", "DC5.ch1", "DC5.ch2", "DC6"]

    dlg = MagnetDialog(st, path)
    assert dlg.name.text() == "magnet_B"                          # magnet_A 已經有了
    assert dlg.a.currentData() == "DC5.ch1" and dlg.b.currentData() == "DC5.ch2"   # 沒配過的前兩個
    assert dlg.label.text() == "磁鐵 B（DC5 ch1+ch2）"
    dlg.b.setCurrentIndex(dlg.b.findData("DC1"))
    assert "已經在 magnet_A" in dlg.msg.text() and not dlg.ok.isEnabled()
    dlg.b.setCurrentIndex(dlg.b.findData("DC5.ch2"))
    dlg.accept()
    opts = dlg.result_options
    assert opts["sources"] == ["DC5.ch1", "DC5.ch2"] and opts["group"] == "magnet" and opts["node"] == "LAB-2"
    assert opts["driver"] == "virtual.interleaved_pair" and opts["source"]["resolution"] == pytest.approx(1e-6)

    w = InstrumentServerWindow(st)
    def fake_exec(self):
        self.result_name, self.result_options = "magnet_B", dict(opts)
        return QtWidgets.QDialog.DialogCode.Accepted
    monkeypatch.setattr(MagnetDialog, "exec", fake_exec)
    errors = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *a: errors.append(a[2]))
    w.add_magnet()
    assert not errors and "magnet_B" in st.instruments
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))["instruments"]["magnet_B"]
    assert saved["sources"] == ["DC5.ch1", "DC5.ch2"]
    g = w.tree.topLevelItem(0)
    top = {g.child(i).text(0): g.child(i) for i in range(g.childCount())}
    assert set(top) == {"DC6", "magnet_A", "magnet_B"}
    mb = top["magnet_B"]
    assert [mb.child(i).text(0) for i in range(mb.childCount())] == ["DC5"] and not mb.isExpanded()
    assert "成員 0/1" in mb.text(5)
    # 成員可以單獨連線
    w.tree.setCurrentItem(mb.child(0))
    assert w.selected() == "DC5" and w.a_conn.isEnabled()
    w.connect_selected()
    assert _pump(app, lambda: st.instruments["DC5"].connected)
    assert not st.instruments["magnet_B"].connected                # 只連了 DC5
    # 展開狀態在重新整理後保留
    w.refresh()
    g = w.tree.topLevelItem(0)
    mb = next(g.child(i) for i in range(g.childCount()) if g.child(i).text(0) == "magnet_B")
    assert mb.isExpanded() and "成員 1/1" in mb.text(5)
    # 整組連線
    w.connect_group(g.data(0, 0x0102))
    assert _pump(app, lambda: all(i.connected for i in st.instruments.values()))
    w.close()


def test_channel_panel_hides_member_sources(station):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.workbench.channels import ChannelPanel
    from labcontrol.apps.qt.workbench.doc import WorkbenchDoc
    from labcontrol.scheme import Scheme
    from labcontrol.scheme.catalog import build_catalog

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    members = {str(r).split(".")[0] for i in station.instruments.values() for r in (i.options.get("sources") or [])}
    assert members
    panel = ChannelPanel(WorkbenchDoc(Scheme("t"), build_catalog(station)), station)
    panel.only_conn.setChecked(False)
    panel.only_common.setChecked(True)
    visible = {panel.tree.topLevelItem(i).text(0) for i in range(panel.tree.topLevelItemCount())
               if not panel.tree.topLevelItem(i).isHidden()}
    assert visible
    labels = {n: station.instruments[n].options.get("label", n) for n in members}
    assert not set(labels.values()) & visible                      # 配對的 DC 平時不列
    panel.only_common.setChecked(False)
    visible = {panel.tree.topLevelItem(i).text(0) for i in range(panel.tree.topLevelItemCount())
               if not panel.tree.topLevelItem(i).isHidden()}
    assert set(labels.values()) <= visible                         # 取消「只顯示常用」仍可單獨設定
    panel.close()
