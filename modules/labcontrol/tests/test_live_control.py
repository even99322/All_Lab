"""0.0.9：即時監控（電源群組 / VNA）本機與遠端共用同一組後端；共用儀器未歸屬時不可連線；遠端新增儀器設定。"""
import time

import numpy as np
import pytest

from labcontrol import Station
from labcontrol.core import grouping
from labcontrol.remote import HubError, NodeService, RemoteNode
from labcontrol.remote.backend import LocalBackend, RemoteBackend
from labcontrol.remote.hub import hostname
from tests.test_instruments_remote import NET_LAB, _other_pc
from tests.test_remote import _wait, hubsrv  # noqa: F401 - fixture


def _local():
    st = Station(NET_LAB)
    st.sim_time_scale = 0.01
    return st


def test_list_sources_and_default_groups():
    st = _local()
    b = LocalBackend(st)
    assert b.sources()["sources"] == []                     # 只列已連線的
    b.connect_all()
    info = b.sources()
    refs = [s["ref"] for s in info["sources"]]
    assert refs == ["DC3.ch1.level", "DC4.ch1.level"]
    assert info["sources"][0]["resolution"] == pytest.approx(1e-6)
    assert info["groups"] == [{"name": "pair", "members": ["DC3.ch1.level", "DC4.ch1.level"], "virtual": "pair"}]


def test_group_ramp_fine_step_and_output():
    st = _local()
    b = LocalBackend(st)
    b.connect(["DC3", "DC4"])
    refs = ["DC3.ch1.level", "DC4.ch1.level"]
    st.parameter("DC4.ch1.level").set(0.1587)
    jid = b.group_ramp(refs, 0.1590, rate=5e-3)
    assert _wait(lambda: b.ramp_status(jid)["done"], 10)
    lv = {r: v for r, (v, _e) in b.levels(refs).items()}
    assert lv["DC3.ch1.level"] == pytest.approx(0.1590) and lv["DC4.ch1.level"] == pytest.approx(0.1590)
    # 微調：+ 動較低的那台一個最小步進；- 動較高的
    r = b.fine_step(refs, +1)
    assert sorted(r.values()) == pytest.approx([0.1590, 0.159001])
    r = b.fine_step(refs, -1)
    assert list(r.values()) == pytest.approx([0.1590, 0.1590])
    with pytest.raises(Exception, match="兩台"):
        b.fine_step(refs[:1], +1)
    assert b.output(refs, True) == {"DC3.ch1.level": True, "DC4.ch1.level": True}
    # 超過上限拒絕
    with pytest.raises(Exception):
        b.group_ramp(refs, 0.5)
    # 中止
    jid = b.group_ramp(refs, 0.0, rate=1e-4)
    time.sleep(0.05)
    b.group_stop(jid)
    assert _wait(lambda: b.ramp_status(jid)["done"], 5)
    assert b.ramp_status(jid)["stopped"]


def test_local_vna_trace():
    st = _local()
    b = LocalBackend(st)
    b.connect(["VNA1"])
    v = b.vnas()
    assert v and v[0]["name"] == "VNA1"
    tr = v[0]["traces"][0]
    x, z, unit = b.vna_trace("VNA1", tr)
    assert len(x) == len(z) > 10 and np.iscomplexobj(z) and unit == "Hz"


@pytest.fixture
def node(hubsrv):
    hub = hubsrv[0]
    st = Station(NET_LAB)
    st.sim_time_scale = 0.01
    n = NodeService(st, hub, name="QEL-PC").start()
    yield n, st, hub
    n.stop()


def test_remote_backend_controls_node(node):
    """B 電腦直接命令量測節點 A：連線、電源群組、微調、輸出、VNA 曲線。"""
    n, st, hub = node
    b = RemoteBackend(RemoteNode(hub, "QEL-PC"))
    assert b.connect(["DC3", "DC4", "VNA1"]) == {"DC3": "", "DC4": "", "VNA1": ""}
    assert st.instruments["DC3"].connected
    info = b.sources()
    assert [s["ref"] for s in info["sources"]] == ["DC3.ch1.level", "DC4.ch1.level"]
    refs = ["DC3.ch1.level", "DC4.ch1.level"]
    jid = b.group_ramp(refs, 0.1592, rate=5e-3)
    assert _wait(lambda: b.ramp_status(jid)["done"], 15)
    assert st.parameter("DC3.ch1.level").get() == pytest.approx(0.1592)
    r = b.fine_step(refs, +1)
    assert sorted(r.values()) == pytest.approx([0.1592, 0.159201])
    assert b.output(refs, True) == {"DC3.ch1.level": True, "DC4.ch1.level": True}
    vn = b.vnas()
    x, z, unit = b.vna_trace("VNA1", vn[0]["traces"][0])
    assert len(x) == len(z) > 10 and np.iscomplexobj(z)
    assert b.disconnect_all()
    assert not st.instruments["VNA1"].connected


def test_shared_unowned_instrument_needs_group(node):
    n, st, hub = node
    rn = RemoteNode(hub, "QEL-PC")
    n._post_status()
    _other_pc(hub, connected=False)
    n._post_status()
    assert "net:192.168.1.11" in n.shared
    res = rn.call("connect_all")
    assert res["DC3"] == "" and "尚未歸屬" in res["VNA1"]
    hub.claim_instrument("net:192.168.1.11", hostname())
    n._post_status()
    assert rn.call("connect", names=["VNA1"]) == {"VNA1": ""}


def test_add_instrument_remotely(node, tmp_path):
    """儀器伺服器把儀器移到一台還沒有它設定的節點：補設定（寫入那台的 instruments.yaml）。"""
    n, st, hub = node
    rn = RemoteNode(hub, "QEL-PC")
    opts = {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::90ZC0001::0::INSTR",
            "source": {"limits": [-0.1, 0.1], "ramp_rate": 1e-3, "max_jump": 5e-5, "resolution": 1e-6}}
    st.config["_source_path"] = str(tmp_path / "instruments.yaml")
    (tmp_path / "instruments.yaml").write_text("instruments: {}\n", encoding="utf-8")
    assert rn.call("add_instrument", name="DC9", options=opts) == {"added": True, "name": "DC9"}
    assert "DC9" in st.instruments
    assert "DC9" in (tmp_path / "instruments.yaml").read_text(encoding="utf-8")
    assert rn.call("add_instrument", name="DC9", options=opts)["added"] is False
    rep = next(i for i in n.status()["instruments"] if i["name"] == "DC9")
    assert rep["options"]["driver"] == "yokogawa.gs200" and rep["options"]["source"]["limits"] == [-0.1, 0.1]
    with pytest.raises(HubError):
        rn.call("add_instrument", name="bad.name", options=opts)


# ---- 儀器伺服器群組 -----------------------------------------------------------------
from labcontrol.remote.groups import UNASSIGNED, build_groups, move_instrument  # noqa: E402


def _reg(key, entries, shared=False, owner=None):
    return {"key": key, "network": key.startswith("net:"), "shared": shared, "owner": owner, "idn": "",
            "connected_on": [e["host"] for e in entries if e.get("connected")], "entries": entries}


def _e(host, name, configured=True, connected=False, node=None, options=None):
    return {"host": host, "node": node, "online": True, "name": name, "configured": configured,
            "detected": True, "connected": connected, "driver": "rs.zna", "address": "TCPIP0::1.2.3.4::INSTR",
            "options": options}


def test_build_groups_rules():
    local = [{"name": "DC1", "opts": {"driver": "yokogawa.gs200", "address": "USB0::1::2::S1::0::INSTR"}},
             {"name": "VNA1", "opts": {"driver": "rs.zna", "address": "TCPIP0::1.2.3.4::INSTR"}},
             {"name": "magnet", "opts": {"driver": "virtual.interleaved_pair", "sources": ["DC1"]}}]
    nodes = [{"name": "A", "host": "PC-A", "online": True}, {"name": "ME", "host": "ME", "online": True}]
    reg = [_reg("net:1.2.3.4", [_e("ME", "VNA1"), _e("PC-A", "ZNA", node="A")], shared=True),
           _reg("usb:0001:0002:s9", [_e("PC-A", "DC7")]),
           _reg("net:9.9.9.9", [_e("PC-A", None, configured=False)])]
    g = build_groups(local, reg, nodes, "ME")
    ids = [x["id"] for x in g]
    assert ids == ["me", "pc-a", UNASSIGNED]
    assert g[0]["title"] == "這台電腦（量測節點 ME）" and g[1]["title"] == "量測節點 A · PC-A"
    assert [i["name"] for i in g[0]["items"]] == ["DC1", "magnet"]     # VNA1 共用未歸屬 → 未歸屬
    assert [i["name"] for i in g[1]["items"]] == ["DC7"]
    un = {i["key"]: i for i in g[2]["items"]}
    assert un["net:1.2.3.4"]["local"] and un["net:1.2.3.4"]["shared"]
    assert un["net:9.9.9.9"]["detected_only"]
    # 歸到 PC-A：顯示在 A 的群組，用 A 的名稱
    g = build_groups(local, reg, nodes, "ME", owners={"net:1.2.3.4": "PC-A"})
    assert [i["name"] for i in g[1]["items"]] == ["DC7", "ZNA"]
    assert all(i["key"] != "net:1.2.3.4" for i in g[0]["items"] + g[2]["items"])
    # 歸這台
    g = build_groups(local, reg, nodes, "ME", owners={"net:1.2.3.4": "ME"})
    assert "VNA1" in [i["name"] for i in g[0]["items"]]


def test_move_instrument_logic():
    calls = []
    kw = dict(me="ME", claim=lambda k, h: calls.append(("claim", k, h)), release=lambda k: calls.append(("rel", k)),
              add_local=lambda n, o: calls.append(("local", n, o["driver"])),
              add_remote=lambda node, n, o: calls.append(("remote", node, n)))
    vna = {"key": "net:1.2.3.4", "name": "VNA1", "network": True, "host": "ME",
           "options": {"driver": "rs.zna", "address": "TCPIP0::1.2.3.4::INSTR", "enabled": True}}
    a = {"id": "pc-a", "host": "PC-A", "node": "A", "online": True, "title": "量測節點 A · PC-A"}
    msg = move_instrument(vna, a, target_has=lambda k: False, **kw)
    assert calls == [("remote", "A", "VNA1"), ("claim", "net:1.2.3.4", "PC-A")] and "instruments.yaml" in msg
    calls.clear()
    move_instrument(vna, {"id": UNASSIGNED, "title": "未歸屬", "host": None}, target_has=lambda k: True, **kw)
    assert calls == [("rel", "net:1.2.3.4")]
    with pytest.raises(RuntimeError, match="USB"):
        move_instrument({"key": "usb:1", "name": "DC1", "network": False, "host": "ME"}, a,
                        target_has=lambda k: True, **kw)
    with pytest.raises(RuntimeError, match="沒有在線"):
        move_instrument(vna, dict(a, online=False), target_has=lambda k: False, **kw)


def test_server_window_groups_remote_node(hubsrv, monkeypatch):
    """儀器伺服器：顯示別台量測節點的群組，從這台直接命令它全部連線、把共用 VNA 移過去。"""
    from PyQt6 import QtWidgets

    import labcontrol.remote.instruments as ri
    import labcontrol.remote.node as rnode
    from labcontrol.apps.qt.server import InstrumentServerWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    hub = hubsrv[0]
    monkeypatch.setattr(rnode, "hostname", lambda: "NODE-A")
    monkeypatch.setattr(ri, "hostname", lambda: "NODE-A")
    nst = Station(NET_LAB)
    node = NodeService(nst, hub, name="A").start()
    try:
        local = Station({"simulate": True, "instruments": {
            "ZNA": {"driver": "rs.zna", "address": "TCPIP0::192.168.1.11::INSTR"}}})

        class FakeRemote:
            def __init__(self):
                self.hub = hub
                self.owners, self.shared = {}, set()
                self.nodes = []

            def refresh(self):
                s = hub.state()
                self.nodes, self.owners, self.shared = s["nodes"], s["owners"], set(s["shared"])
        rm = FakeRemote()
        hub.heartbeat({"id": f"me@{hostname()}", "host": hostname(), "user": "me", "role": "client",
                       "instruments": [{"name": "ZNA", "key": "net:192.168.1.11", "connected": False,
                                        "options": {"driver": "rs.zna", "address": "TCPIP0::192.168.1.11::INSTR"}}]})
        node._post_status()
        rm.refresh()
        w = InstrumentServerWindow(local)
        w.remote = rm
        w.registry = hub.instruments_registry()
        w.refresh()

        def pump(cond, s=10):
            t = time.time()
            while time.time() - t < s:
                app.processEvents()
                if cond():
                    return True
                time.sleep(0.02)
            return False
        titles = [w.tree.topLevelItem(i).text(0) for i in range(w.tree.topLevelItemCount())]
        assert titles[0].startswith("這台電腦") and titles[1] == "量測節點 A · NODE-A" and titles[-1] == "未歸屬"
        ga = w.tree.topLevelItem(1)
        assert {ga.child(i).text(0) for i in range(ga.childCount())} == {"DC3", "DC4"}
        un = w.tree.topLevelItem(w.tree.topLevelItemCount() - 1)
        vna = next(un.child(i).data(0, 0x0101) for i in range(un.childCount())
                   if un.child(i).data(0, 0x0101)["key"] == "net:192.168.1.11")
        # 全部連線（別台節點）：共用 VNA 未歸屬，不在它的群組
        w.connect_group(ga.data(0, 0x0102))
        assert pump(lambda: nst.instruments["DC3"].connected and nst.instruments["DC4"].connected)
        # 把共用 VNA 移到 A：A 已有設定（VNA1），直接歸屬
        w.move_to(vna, ga.data(0, 0x0102))
        assert pump(lambda: hub.state()["owners"].get("net:192.168.1.11") == "NODE-A")
        node._post_status()
        rm.refresh()
        w.registry = hub.instruments_registry()
        w.refresh()
        ga = w.tree.topLevelItem(1)
        assert "VNA1" in {ga.child(i).text(0) for i in range(ga.childCount())}
        w.connect_group(ga.data(0, 0x0102))
        assert pump(lambda: nst.instruments["VNA1"].connected)
        w.close()
    finally:
        node.stop()
