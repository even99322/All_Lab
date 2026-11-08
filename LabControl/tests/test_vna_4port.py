"""1.0.28：4 port VNA 沒連線時，量測方塊也能選 S34、S13…（port 數記住 / 節點回報 / 手動輸入）。"""
from __future__ import annotations

import yaml

from labcontrol.core import sparams as sp
from labcontrol.core import vna_ports
from labcontrol.core.station import Station

VNA = {"driver": "rs.zna", "address": "TCPIP0::192.0.2.11::INSTR"}


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _own_ports_file(tmp_path, monkeypatch):
    """vna_ports.json 用這個測試自己的（不影響其他測試的 VNA1）。"""
    monkeypatch.setattr(vna_ports, "_path", lambda: tmp_path / "vna_ports.json")


def _station(tmp_path, **opts):
    p = tmp_path / "instruments.yaml"
    p.write_text(yaml.safe_dump({"instruments": {"VNA1": {**VNA, **opts}}}), encoding="utf-8")
    return Station.from_file(p, simulate=True)


def test_trace_list_defaults_without_info(tmp_path):
    st = _station(tmp_path)
    assert st.instruments["VNA1"].trace_list() == ["S21", "S11", "S12", "S22"]


def test_trace_list_from_ports_option(tmp_path):
    vna = _station(tmp_path, ports=4).instruments["VNA1"]
    tl = vna.trace_list()
    assert len(tl) == 16 and tl[0] == "S21" and {"S34", "S13", "S43"} <= set(tl)


def test_trace_list_remembers_detected_ports(tmp_path):
    vna_ports.remember_ports("VNA1", 4)
    vna = _station(tmp_path).instruments["VNA1"]
    assert vna.known_ports() == 4 and "S34" in vna.trace_list()
    vna_ports.remember_ports("VNA1", 2)                      # port 數變了 → 改成新的
    assert vna.known_ports() == 2 and "S34" not in vna.trace_list()


def test_node_ports_used_by_mirror(tmp_path):
    vna = _station(tmp_path, _node_ports=4).instruments["VNA1"]
    assert "S43" in vna.trace_list()


def test_instrument_report_has_ports_and_traces(tmp_path):
    from labcontrol.remote.instruments import instrument_report

    st = _station(tmp_path, ports=4)
    rec = next(r for r in instrument_report(st, "PC-1") if r["name"] == "VNA1")
    assert rec["ports"] == 4 and len(rec["traces"]) == 16 and "_node_ports" not in rec["options"]


def test_mirror_station_gets_node_ports(monkeypatch):
    from labcontrol.remote.client import RemoteNode

    text = yaml.safe_dump({"instruments": {"VNA1": dict(VNA)}})

    class H:
        def instruments(self, name):
            return text

        def node_status(self, name):
            return {"last_seen": 0, "instruments": [{"name": "VNA1", "ports": 4}]}
    st = RemoteNode(H(), "LAB-1").mirror_station()
    assert "S34" in st.instruments["VNA1"].trace_list()


def test_catalog_lists_all_sparams(tmp_path):
    from labcontrol.scheme import build_catalog

    cat = build_catalog(_station(tmp_path, ports=4))
    assert cat.measurer("VNA1").traces == sp.ordered(sp.sparam_names(4))


def test_measure_dialog_extra_trace(tmp_path):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.workbench.dialogs import MeasureDialog
    from labcontrol.scheme import build_catalog
    from labcontrol.scheme.model import Block

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841

    class Doc:
        catalog = build_catalog(_station(tmp_path))            # port 數未知：清單只有預設四個

    b = Block("measure", instrument="VNA1", traces=["S21", "S34"], settings={})
    d = MeasureDialog(None, Doc(), b)
    texts = [d.traces.item(i).text() for i in range(d.traces.count())]
    assert texts[-1] == "S34"                                  # 方案裡有的也列出（勾選）
    assert d.extra_row.isVisibleTo(d) and "port 數" in d.trace_note.text()
    d.extra_trace.setText("s13")
    d.add_extra_trace()
    d.extra_trace.setText("X9")
    d.add_extra_trace()
    assert "不是 S 參數" in d.trace_note.text()
    assert d.result_block().traces == ["S21", "S34", "S13"]
    d.close()

    class Doc4:
        catalog = build_catalog(_station(tmp_path, ports=4))
    d = MeasureDialog(None, Doc4(), Block("measure", instrument="VNA1", traces=["S21"], settings={}))
    assert d.traces.count() == 16 and "4 port" in d.trace_note.text()
    d.extra_trace.setText("S51")
    d.add_extra_trace()
    assert "超出" in d.trace_note.text()
    d.close()
