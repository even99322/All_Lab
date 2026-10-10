"""0.0.22：儀器歸屬哪台量測電腦（instruments.yaml node:）＋ VNA 偵測 port（S31、S43…）。"""
from __future__ import annotations

import numpy as np
import pytest

from labcontrol.core import sparams as sp
from labcontrol.core.nodes import foreign_map, pins
from labcontrol.remote.groups import UNASSIGNED, build_groups, move_instrument
from tests.test_remote import hubsrv  # noqa: F401 - fixture

LAB = {
    "DC1": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::90ZC38697::0::INSTR", "node": "LAB-1"},
    "DC2": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::90ZC38696::0::INSTR", "node": "LAB-1"},
    "DC5": {"driver": "yokogawa.gs820", "address": "USB0::0x0B21::0x0040::91X000005::0::INSTR", "node": "LAB-2"},
    "magnet_B": {"driver": "virtual.interleaved_pair", "sources": ["DC1", "DC2"]},
    "VNA1": {"driver": "rs.zna", "address": "TCPIP0::192.168.1.11::INSTR", "node": "LAB-1"},
    "VNA2": {"driver": "rs.zna", "address": "TCPIP0::192.168.1.12::INSTR", "node": "LAB-2"},
    "SHFQC1": {"driver": "zi.shfqc", "device": "dev12345", "node": ["LAB-1", "LAB-2"]},
    "DMM": {"driver": "sim.dc"},
}


def _flat(items):
    """群組項目＋收在電磁鐵組底下的成員（0.0.24）。"""
    return [x for i in items for x in [i, *(i.get("members") or [])]]


def test_pins_parse():
    assert pins({"node": "LAB-1"}) == ["LAB-1"]
    assert pins({"node": "LAB-1, LAB-2"}) == ["LAB-1", "LAB-2"]
    assert pins({"node": ["LAB-1", "LAB-2"]}) == ["LAB-1", "LAB-2"]
    assert pins({}) == []


def test_foreign_map_per_pc():
    on2 = foreign_map(LAB, me={"lab-2", "pc-lab2"})
    assert set(on2) == {"DC1", "DC2", "magnet_B", "VNA1"}          # 虛擬儀器跟著電源
    assert on2["magnet_B"] == "LAB-1"
    on1 = foreign_map(LAB, me={"LAB-1"})
    assert set(on1) == {"DC5", "VNA2"}
    assert foreign_map(LAB, me={"client-pc"}) == {}                  # 客戶端：全部列出
    assert set(foreign_map(LAB, me={"client-pc"}, strict=True)) >= {"DC1", "DC5", "VNA1", "VNA2"}


def test_station_skips_foreign(monkeypatch):
    from labcontrol.core.station import Station

    cfg = {"simulate": True, "_lab_plugins": False, "_node_names": ["LAB-2"], "instruments": {
        "DC1": {"driver": "yokogawa.gs200", "address": "USB0::1::2::A::INSTR", "node": "LAB-1"},
        "DC2": {"driver": "yokogawa.gs200", "address": "USB0::1::2::B::INSTR", "node": "LAB-1"},
        "DC5": {"driver": "yokogawa.gs200", "address": "USB0::1::2::C::INSTR", "node": "LAB-2"},
        "magnet_B": {"driver": "virtual.interleaved_pair", "sources": ["DC1", "DC2"]},
    }}
    st = Station(cfg)
    assert set(st.instruments) == {"DC5"}
    assert st.foreign == {"DC1": "LAB-1", "DC2": "LAB-1", "magnet_B": "LAB-1"}
    with pytest.raises(Exception, match="LAB-1"):
        st.expand(["DC1.level"])


def _entry(name, opts, foreign=None, connected=False):
    return {"name": name, "opts": dict(opts), "in_file": True, "connected": connected, "foreign": foreign}


def test_groups_on_lab2_show_usb_under_lab1():
    on2 = foreign_map(LAB, me={"LAB-2"})
    local = [_entry(n, o, on2.get(n)) for n, o in LAB.items()]
    nodes = [{"name": "LAB-1", "host": "PC-LAB1", "online": True}, {"name": "LAB-2", "host": "PC-LAB2", "online": True}]
    # LAB-1 已回報它的 DC1（同一個 USB key，連線中）
    from labcontrol.remote.instruments import instrument_key

    k1 = instrument_key(LAB["DC1"]["address"], LAB["DC1"], "PC-LAB1")
    registry = [{"key": k1, "entries": [{"host": "PC-LAB1", "name": "DC1", "configured": True, "connected": True,
                                          "online": True, "options": LAB["DC1"]}]}]
    groups = build_groups(local, registry, nodes, "PC-LAB2")
    me, lab1 = groups[0], next(g for g in groups if g["node"] == "LAB-1")
    mine = {i["name"] for i in me["items"]}
    assert {"DC1", "DC2", "magnet_B", "VNA1"}.isdisjoint(mine)
    assert {"DC5", "VNA2", "DMM"} <= mine
    there = {i["name"]: i for i in _flat(lab1["items"])}
    assert {"DC1", "DC2", "magnet_B", "VNA1"} <= set(there)
    assert there["DC1"]["connected"] and there["DC1"]["pinned"] == "LAB-1"      # 用 LAB-1 的即時狀態
    assert there["DC2"]["status"].startswith("instruments.yaml")
    assert all(i["name"] != "DC1" for i in groups[-1]["items"])                  # 不在未歸屬


def test_stale_registry_entry_from_this_pc_does_not_pull_usb_back():
    """LAB-2 舊版曾回報 DC1（設定了但連不上）：仍顯示在 LAB-1。"""
    from labcontrol.remote.instruments import instrument_key

    on2 = foreign_map(LAB, me={"LAB-2"})
    local = [_entry(n, o, on2.get(n)) for n, o in LAB.items()]
    k1 = instrument_key(LAB["DC1"]["address"], LAB["DC1"], "x")
    registry = [{"key": k1, "entries": [
        {"host": "PC-LAB2", "name": "DC1", "configured": True, "connected": False, "options": LAB["DC1"]},
        {"host": "PC-LAB1", "name": "DC1", "configured": True, "connected": True, "options": LAB["DC1"]}]}]
    nodes = [{"name": "LAB-1", "host": "PC-LAB1", "online": True}]
    groups = build_groups(local, registry, nodes, "PC-LAB2")
    assert "DC1" not in {i["name"] for i in groups[0]["items"]}
    lab1 = next(g for g in groups if g["node"] == "LAB-1")
    assert [i for i in _flat(lab1["items"]) if i["name"] == "DC1"][0]["host"] == "PC-LAB1"


def test_usb_without_pins_prefers_connected_host():
    """沒寫 node 時，同一台 USB 設定在兩台：放在連線中的那台。"""
    from labcontrol.remote.instruments import instrument_key

    o = {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::X1::0::INSTR"}
    k = instrument_key(o["address"], o, "a")
    registry = [{"key": k, "entries": [
        {"host": "PC-A", "name": "DC9", "configured": True, "connected": False},
        {"host": "PC-B", "name": "DC9", "configured": True, "connected": True}]}]
    groups = build_groups([], registry, [{"name": "B", "host": "PC-B", "online": True}], "PC-C")
    b = next(g for g in groups if g["host"] == "PC-B")
    assert [i["name"] for i in b["items"]] == ["DC9"]


def test_move_usb_writes_node_pin():
    pinned = []
    common = dict(me="PC-LAB2", claim=lambda *a: None, release=lambda *a: None, add_local=lambda *a: None,
                  add_remote=lambda *a: None, target_has=lambda k: True, pin=lambda n, node: pinned.append((n, node)),
                  me_node="LAB-2")
    item = {"name": "DC3", "key": "usb:0b21:0039:x", "network": False, "local": True, "host": "PC-LAB2"}
    lab1 = {"id": "pc-lab1", "title": "量測節點 LAB-1", "host": "PC-LAB1", "node": "LAB-1", "online": False,
            "local": False}
    msg = move_instrument(item, lab1, **common)
    assert pinned == [("DC3", "LAB-1")] and "LAB-1" in msg
    back = {"name": "DC3", "key": "usb:0b21:0039:x", "network": False, "local": False, "pinned": "LAB-1",
            "local_name": "DC3"}
    mine = {"id": "pc-lab2", "title": "這台電腦", "host": "PC-LAB2", "node": "LAB-2", "local": True}
    move_instrument(back, mine, **common)
    assert pinned[-1] == ("DC3", "LAB-2")
    with pytest.raises(RuntimeError):
        move_instrument(item, {"id": UNASSIGNED, "title": "未歸屬"}, **common)


# ---- VNA port ------------------------------------------------------------------------------
def test_sparam_names():
    assert sp.sparam_names(2) == ["S11", "S12", "S21", "S22"]
    four = sp.sparam_names(4)
    assert len(four) == 16 and "S31" in four and "S43" in four
    assert sp.ports_of("S43") == (4, 3) and sp.ports_of("Trc1") is None
    assert sp.ports_from_idn("Rohde-Schwarz,ZNA26-4Port,1332,2.20") == 4
    assert sp.ordered(four, {"S43": ["Trc3"]})[:2] == ["S21", "S43"]


def test_zna_detects_ports_and_auto_creates_trace():
    from labcontrol.drivers.rohde_schwarz.vna import RohdeSchwarzZNA
    from labcontrol.testing.fake_scpi import FakeZNA

    t = FakeZNA()
    t.ports = 4
    vna = RohdeSchwarzZNA("VNA2", transport=t)
    vna.connect()
    assert vna.ports and vna.ports["ports"] == 4 and vna.ports["source"] == "INST:PORT:COUN?"
    assert vna.ports["traces"] == {"S21": ["Trc1"], "S11": ["Trc2"]}
    tl = vna.trace_list()
    assert tl[0] == "S21" and "S31" in tl and "S43" in tl and len(tl) == 16
    z = vna.acquire("S43")                                   # ZNA 上沒有 → 自動建立 LC_S43
    assert z.size == 501 and ("LC_S43", "S43") in t.catalog
    info = vna.detect_ports()
    assert "S43" in info["traces"]


def test_zna_port_count_falls_back_to_idn():
    from labcontrol.drivers.rohde_schwarz.vna import RohdeSchwarzZNA
    from labcontrol.testing.fake_scpi import FakeZNA

    t = FakeZNA()
    t.rules = [r for r in t.rules if "PORT" not in r[0].pattern]
    t.IDN = "Rohde-Schwarz,ZNA43-2Port,1,2.20"
    vna = RohdeSchwarzZNA("V", transport=t)
    vna.connect()
    assert vna.ports["ports"] == 2 and "IDN" in vna.ports["source"]
    assert vna.check_errors() == []                          # 不支援的指令錯誤已清掉
    v2 = RohdeSchwarzZNA("V2", transport=FakeZNA(), ports=3, detect_ports=False)
    v2.connect()
    assert v2.ports is None and v2.trace_list() == ["S21", "S11", "S12", "S22"]
    assert v2.detect_ports()["ports"] == 3


def test_sim_vna_ports_and_backend():
    from labcontrol.core.station import Station
    from labcontrol.remote.backend import LocalBackend

    st = Station({"simulate": True, "_lab_plugins": False, "instruments": {
        "VNA2": {"driver": "rs.zna", "sim": {"ports": 4, "sweep_time": 0}}}})
    be = LocalBackend(st)
    info = be.vna_ports("VNA2")
    assert info["ports"] == 4 and "S31" in info["sparams"]
    x, z, unit = be.vna_trace("VNA2", "S31", True)
    assert unit == "Hz" and np.isfinite(z).all()
    s11 = be.vna_trace("VNA2", "S11", True)[1]
    assert np.median(np.abs(s11)) > np.median(np.abs(z))     # 反射比穿透大
    assert [v for v in be.vnas() if v["name"] == "VNA2"][0]["ports"]["ports"] == 4


# ---- GUI -----------------------------------------------------------------------------------
def _pump(app, cond, s=10.0):
    import time

    t = time.time()
    while time.time() - t < s:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_vna_panel_detect_ports_builds_matrix(tmp_path, monkeypatch):
    from PyQt6 import QtWidgets

    monkeypatch.setenv("LAB_CONTROL_HOME", str(tmp_path))
    from labcontrol.apps.qt.live.vna_panel import VNAPanel
    from labcontrol.core.station import Station
    from labcontrol.remote.backend import LocalBackend

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    st = Station({"simulate": True, "_lab_plugins": False, "instruments": {
        "VNA2": {"driver": "rs.zna", "sim": {"ports": 4, "sweep_time": 0}}}})
    st.connect(["VNA2"])
    p = VNAPanel()
    msgs = []
    p.message.connect(msgs.append)
    p.set_backend(LocalBackend(st))
    assert _pump(app, lambda: len(p.sbtn) == 16)                 # 選 VNA 時自動偵測
    assert p.sgrid.itemAtPosition(2, 0).widget().text() == "S31"
    assert p.sgrid.itemAtPosition(3, 2).widget().text() == "S43"
    assert p.trace() == "S21" and p.sbtn["S21"].font().bold()
    assert "4 port" in p.ports_label.text()
    p.sbtn["S43"].click()
    assert _pump(app, lambda: p.last is not None and p.last[0] == "S43")
    st.instruments["VNA2"].options["ports"] = 2
    p.detect_ports()                                             # 按鈕：重新偵測
    assert _pump(app, lambda: len(p.sbtn) == 4)
    assert any("2 port" in m for m in msgs)
    assert p.trace() == "S21"                                    # S43 不存在了 → 回到 S21
    p.close()


def test_server_window_pins_usb_to_other_node(tmp_path, monkeypatch):
    from PyQt6 import QtWidgets

    import yaml

    monkeypatch.setenv("LAB_CONTROL_HOME", str(tmp_path))
    monkeypatch.setenv("LAB_CONTROL_NODE_NAME", "LAB-2")
    from labcontrol.apps.qt.server import InstrumentServerWindow
    from labcontrol.core.station import Station

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "instruments.yaml"
    path.write_text(yaml.safe_dump({"instruments": {
        "DC1": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::A1::0::INSTR"},
        "DC2": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::A2::0::INSTR"},
        "DC5": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::A5::0::INSTR"},
        "magnet_B": {"driver": "virtual.interleaved_pair", "sources": ["DC1", "DC2"]}}}), encoding="utf-8")
    st = Station.from_file(path, simulate=True)
    assert set(st.instruments) == {"DC1", "DC2", "DC5", "magnet_B"}           # 還沒寫 node：全部
    w = InstrumentServerWindow(st)
    w.remote = None

    class Rm:
        hub = None
        owners, shared = {}, set()
        nodes = [{"name": "LAB-1", "host": "PC-LAB1", "online": True}]
    w.remote = Rm()
    w.refresh()
    g = w.groups()
    lab1 = next(x for x in g if x.get("node") == "LAB-1")
    for n in ("DC1", "DC2"):
        item = next(i for i in _flat(g[0]["items"]) if i["name"] == n)
        w.move_to(item, lab1)
        assert _pump(app, lambda n=n: n not in st.instruments)
    assert set(st.instruments) == {"DC5"} and st.foreign.get("magnet_B") == "LAB-1"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))["instruments"]
    assert data["DC1"]["node"] == "LAB-1" and data["DC2"]["node"] == "LAB-1"
    g = w.groups()
    assert {i["name"] for i in g[0]["items"]} == {"DC5"}
    lab1 = next(x for x in g if x.get("node") == "LAB-1")
    assert {"DC1", "DC2", "magnet_B"} <= {i["name"] for i in _flat(lab1["items"])}
    # 拖回這台
    back = next(i for i in _flat(lab1["items"]) if i["name"] == "DC1")
    w.move_to(back, g[0])
    assert _pump(app, lambda: "DC1" in st.instruments)
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["instruments"]["DC1"]["node"] == "LAB-2"
    w.close()


# ---- 0.0.23：LAB-2 實機回報（0.0.22 拖曳後 DC1–4、magnet 仍在這台）-------------------------------
USER_LAB2 = {   # LAB-2 拖過 DC1、magnet_A、magnet_B 之後的 instruments.yaml（DC2–4 沒寫 node）
    "DC1": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::90ZC38697::0::INSTR", "node": "LAB-1"},
    "DC2": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::90ZC38696::0::INSTR"},
    "DC3": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::9017D5818::0::INSTR"},
    "DC4": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::9017D5816::0::INSTR"},
    "DC5": {"driver": "yokogawa.gs820", "address": "USB0::0x0B21::0x002C::91R712667::0::INSTR"},
    "DC6": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::9017D5817::0::INSTR"},
    "magnet_A": {"driver": "virtual.interleaved_pair", "sources": ["DC3", "DC4"], "node": "LAB-1"},
    "magnet_B": {"driver": "virtual.interleaved_pair", "sources": ["DC1", "DC2"], "node": "LAB-1"},
    "VNA2": {"driver": "rs.zna", "address": "TCPIP0::192.168.1.12::INSTR"},
}


def test_pinned_magnet_takes_its_sources_along():
    away = foreign_map(USER_LAB2, me={"lab-2"}, strict=True)
    assert set(away) == {"DC1", "DC2", "DC3", "DC4", "magnet_A", "magnet_B"}


def test_node_state_name_makes_pc_strict(tmp_path, monkeypatch):
    """工具列「🛰 量測節點」的名稱記在 LAB/node_state.json，沒有環境變數也算量測電腦。"""
    import json

    import yaml

    from labcontrol.core import nodes
    from labcontrol.core.station import Station

    monkeypatch.setenv("LAB_CONTROL_HOME", str(tmp_path))
    monkeypatch.delenv("LAB_CONTROL_NODE_NAME", raising=False)
    (tmp_path / "node_state.json").write_text(json.dumps({"name": "LAB-2", "auto_start": True}), encoding="utf-8")
    assert "lab-2" in nodes.my_names() and nodes.is_measure_pc()
    path = tmp_path / "instruments.yaml"
    path.write_text(yaml.safe_dump({"instruments": USER_LAB2}), encoding="utf-8")
    st = Station.from_file(path, simulate=True)
    assert set(st.instruments) == {"DC5", "DC6", "VNA2"}


def test_node_start_applies_ownership(tmp_path, monkeypatch, hubsrv):
    """量測節點上線（名稱只在節點裡）→ 別台的儀器立刻移出、不回報給 Hub。"""
    import yaml

    from labcontrol.core import nodes
    from labcontrol.core.station import Station
    from labcontrol.remote import NodeService

    monkeypatch.setenv("LAB_CONTROL_HOME", str(tmp_path))
    monkeypatch.delenv("LAB_CONTROL_NODE_NAME", raising=False)
    path = tmp_path / "instruments.yaml"
    path.write_text(yaml.safe_dump({"instruments": USER_LAB2}), encoding="utf-8")
    st = Station.from_file(path, simulate=True)
    assert "DC1" in st.instruments                       # 還不是量測電腦：全部
    node = NodeService(st, hubsrv[0], name="LAB-2").start()
    try:
        assert set(st.instruments) == {"DC5", "DC6", "VNA2"}
        reported = {i["name"] for i in node.hub.instruments_registry() for i in i.get("entries") or []}
        assert "DC1" not in reported
    finally:
        node.stop()
        nodes.register_running("LAB-2", False)


def test_mirror_station_only_has_node_instruments(hubsrv):
    import yaml

    from labcontrol.remote import RemoteNode

    hub = hubsrv[0]
    hub.put_instruments("LAB-2", yaml.safe_dump({"instruments": USER_LAB2}))
    st = RemoteNode(hub, "LAB-2").mirror_station()
    assert set(st.instruments) == {"DC5", "DC6", "VNA2"}
