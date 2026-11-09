"""1.0.27：儀器 / 電磁鐵組改名、隱藏，量測電腦之間同步儀器清單。"""
from __future__ import annotations

from pathlib import Path

import yaml

from labcontrol.core import instedit, labfile
from labcontrol.core.station import Station
from labcontrol.remote.groups import build_groups, filter_hidden
from labcontrol.remote.instsync import SYNC_KEY, InstrumentSync, plan_sync
import pytest


@pytest.fixture(autouse=True)
def _isolated_rename(tmp_path, monkeypatch):
    """1.0.33：改名會一起改 LAB 資料夾裡的方案 / 範本——測試改到自己的暫存資料夾，不動共用的測試 LAB。"""
    from labcontrol.core import rename_refs

    orig = rename_refs.propagate
    monkeypatch.setattr(rename_refs, "propagate", lambda old, new, root=None, push_projects=False:
                        orig(old, new, root=tmp_path / "_lab", push_projects=False))

U1 = "USB0::0x0B21::0x0039::91AB00001::INSTR"
U2 = "USB0::0x0B21::0x0039::91AB00002::INSTR"
U8 = "USB0::0x0B21::0x0039::91AB00008::INSTR"

LAB1 = {
    "DC1": {"driver": "yokogawa.gs200", "address": U1},
    "DC2": {"driver": "yokogawa.gs200", "address": U2},
    "magnet_A": {"driver": "virtual.interleaved_pair", "group": "magnet", "sources": ["DC1", "DC2"]},
    "VNA1": {"driver": "rs.zna", "address": "TCPIP0::192.0.2.11::INSTR"},
}


def _write(path: Path, inst, extra=None) -> Path:
    data = dict(extra or {})
    data["instruments"] = inst
    path.write_text("# 註解要保留\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_labfile_rename_keeps_order_comments_and_sources(tmp_path):
    p = _write(tmp_path / "instruments.yaml", LAB1)
    touched = labfile.rename_instrument(p, "DC1", "DC9")
    text = p.read_text(encoding="utf-8")
    assert "# 註解要保留" in text
    conf = labfile.configured(p)
    assert [n for n, _ in conf] == ["DC9", "DC2", "magnet_A", "VNA1"]
    assert dict(conf)["magnet_A"]["sources"] == ["DC9", "DC2"] and touched == ["magnet_A"]


def test_rename_instrument_reloads_station_and_lab_nodes(tmp_path, monkeypatch):
    from labcontrol.settings import settings

    st_ = settings()
    st_.set("lab_nodes", [{"node": "LAB-1", "magnet": ["magnet_A"], "vna": ["VNA1"]}])
    p = _write(tmp_path / "instruments.yaml", LAB1)
    st = Station.from_file(p, simulate=True)
    res = instedit.rename_instrument(st, p, "magnet_A", "magA")
    assert "magA" in st.instruments and "magnet_A" not in st.instruments
    assert res["name"] == "magA"
    from labcontrol.settings import setting
    assert setting("lab_nodes")[0]["magnet"] == ["magA"]
    instedit.rename_instrument(st, p, "DC1", "DC9")
    assert st.instruments["magA"].options["sources"] == ["DC9", "DC2"] and "DC9" in st.instruments
    import pytest
    from labcontrol.core.errors import ConfigError
    for bad in ("DC2", "1abc", "a.b", "has space", ""):
        with pytest.raises(ConfigError):
            instedit.rename_instrument(st, p, "DC9", bad)


def test_rename_refuses_connected(tmp_path):
    import pytest

    from labcontrol.core.errors import ConfigError

    p = _write(tmp_path / "instruments.yaml", LAB1)
    st = Station.from_file(p, simulate=True)
    st.instruments["DC1"].connect()
    with pytest.raises(ConfigError, match="中斷連線"):
        instedit.rename_instrument(st, p, "DC1", "DC9")


def test_hidden_flag_and_group_filter(tmp_path):
    p = _write(tmp_path / "instruments.yaml", LAB1)
    st = Station.from_file(p, simulate=True)
    instedit.set_hidden(st, p, "VNA1", True)
    instedit.set_hidden(st, p, "DC2", True)
    assert dict(labfile.configured(p))["VNA1"]["hidden"] is True and st.instruments["VNA1"].options["hidden"]
    local = [{"name": n, "opts": o, "in_file": True} for n, o in labfile.configured(p)]
    g = build_groups(local, [], [], "ME")[0]
    kept, n = filter_hidden(g["items"])
    assert [i["name"] for i in kept] == ["magnet_A"] and n == 2
    assert [m["name"] for m in kept[0]["members"]] == ["DC1"]
    instedit.set_hidden(st, p, "VNA1", False)
    assert dict(labfile.configured(p))["VNA1"]["hidden"] is False


def test_plan_sync_usb_pinned_network_loaded():
    plan = plan_sync({}, "LAB-1", "PC-1", LAB1, me_names={"lab-2"})
    add = plan["add"]
    assert add["DC1"]["node"] == "LAB-1" and add["DC1"][SYNC_KEY] == "LAB-1"
    assert add["magnet_A"]["node"] == "LAB-1"
    assert "node" not in add["VNA1"] and add["VNA1"][SYNC_KEY] == "LAB-1"      # 網路儀器兩台都載入


def test_plan_sync_rename_remove_hidden_and_conflicts():
    local = {n: o for n, o in plan_sync({}, "LAB-1", "PC-1", LAB1)["add"].items()}
    local["DC5"] = {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::91AB00005::INSTR"}
    local["DC1"]["hidden"] = True                                              # 這台自己隱藏的
    owner = {"DC9": dict(LAB1["DC1"]), "DC2": dict(LAB1["DC2"]),
             "magA": {**LAB1["magnet_A"], "sources": ["DC9", "DC2"]},
             "DC5": {"driver": "yokogawa.gs200", "address": U8}}               # 同名但不同台
    plan = plan_sync(local, "LAB-1", "PC-1", owner)
    assert plan["rename"] == {"DC1": "DC9", "magnet_A": "magA"}
    assert plan["remove"] == ["VNA1"]
    assert "DC9" not in plan["replace"]                                        # 改名沿用原設定（含這台的隱藏）
    assert ("DC5", "這台已經有另一台叫 DC5 的儀器") in plan["skipped"] and "DC5" not in plan["add"]
    owner["DC2"]["hidden"] = True                                              # 那台隱藏 → 跟著
    plan = plan_sync(local, "LAB-1", "PC-1", owner)
    assert plan["replace"]["DC2"]["hidden"] is True


def test_plan_sync_skips_existing_and_own_entries():
    local = {"VNA1x": {"driver": "rs.zna", "address": "TCPIP0::192.0.2.11::INSTR"},     # 同一台、別的名字
             "DC1": {"driver": "yokogawa.gs200", "address": U1, "node": "LAB-1"}}     # 共用設定檔的寫法
    plan = plan_sync(local, "LAB-1", "PC-1", LAB1)
    assert "VNA1" not in plan["add"] and "DC1" not in plan["add"] and not plan["skipped"]
    # 那台從別台同步來的不會再轉送；指定給別台的不算那台的
    owner = {"X1": {**LAB1["DC1"], SYNC_KEY: "LAB-3", "node": "LAB-3"},
             "X2": {**LAB1["DC2"], "node": "LAB-3"}}
    assert plan_sync({}, "LAB-1", "PC-1", owner)["add"] == {}


def test_plan_sync_handover_to_this_pc():
    local = plan_sync({}, "LAB-1", "PC-1", LAB1, me_names={"lab-2"})["add"]
    owner = dict(LAB1)
    owner["DC2"] = {**LAB1["DC2"], "node": "LAB-2"}                            # LAB-1 把 DC2 拖到 LAB-2
    plan = plan_sync(local, "LAB-1", "PC-1", owner, me_names={"lab-2"})
    assert plan["replace"]["DC2"]["node"] == "LAB-2" and SYNC_KEY not in plan["replace"]["DC2"]
    assert "DC2" not in plan["remove"]


class _Hub:
    def __init__(self, texts, nodes):
        self.texts, self._nodes, self.put = texts, nodes, {}

    def nodes(self):
        return self._nodes

    def instruments(self, name):
        return self.texts.get(name, "")

    def put_instruments(self, name, text):
        self.put[name] = text

    def claim_instrument(self, key, host, by=""):
        self.claims = getattr(self, "claims", []) + [(key, host)]


class _Node:
    def __init__(self, station, path, hub, name="LAB-2"):
        self.station, self._p, self.hub, self.name = station, path, hub, name

    def _instruments_path(self):
        return self._p

    def write_instruments(self):
        self.hub.put_instruments(self.name, self._p.read_text(encoding="utf-8"))


def test_instrument_sync_end_to_end(tmp_path):
    p2 = _write(tmp_path / "lab2.yaml", {"DC5": {"driver": "yokogawa.gs200",
                                                 "address": "USB0::0x0B21::0x0039::91AB00005::INSTR"}},
                {"_node_names": ["LAB-2"], "_node_strict": True})
    st = Station.from_file(p2, simulate=True)
    lab1_text = yaml.safe_dump({"instruments": LAB1}, allow_unicode=True, sort_keys=False)
    hub = _Hub({"LAB-1": lab1_text}, [{"name": "LAB-1", "host": "PC-1", "online": False},
                                       {"name": "LAB-2", "host": "PC-2", "online": True}])
    sync = InstrumentSync(_Node(st, p2, hub))
    sync.publish_if_changed()
    assert "LAB-2" in hub.put
    res = sync.apply("LAB-1", "PC-1", yaml.safe_load(lab1_text)["instruments"])
    conf = dict(labfile.configured(p2))
    assert set(res["add"]) == {"DC1", "DC2", "magnet_A", "VNA1"}
    assert conf["DC1"]["node"] == "LAB-1" and "DC1" not in st.instruments          # USB：只列出、不載入
    assert "VNA1" in st.instruments                                              # 網路儀器：這台也載入
    assert st.foreign.get("magnet_A") == "LAB-1"
    assert hub.claims == [("net:192.0.2.11", "PC-1")]                           # 網路儀器先歸原本那台
    # LAB-1 改名 + 移除 VNA1
    lab1 = dict(LAB1)
    lab1["DC9"] = lab1.pop("DC1")
    lab1["magnet_A"] = {**LAB1["magnet_A"], "sources": ["DC9", "DC2"]}
    lab1.pop("VNA1")
    res = sync.apply("LAB-1", "PC-1", lab1)
    conf = dict(labfile.configured(p2))
    assert "DC9" in conf and "DC1" not in conf and "VNA1" not in conf and "VNA1" not in st.instruments
    assert conf["magnet_A"]["sources"] == ["DC9", "DC2"]
    # 這台的 DC5 不受影響
    assert "DC5" in st.instruments and SYNC_KEY not in conf["DC5"]


def _pump(app, cond, timeout=5.0):
    import time

    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_server_window_rename_and_hide(tmp_path, monkeypatch):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.server import InstrumentServerWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    p = _write(tmp_path / "instruments.yaml", LAB1)
    st = Station.from_file(p, simulate=True)
    w = InstrumentServerWindow(st)

    def find(name):
        g = w.tree.topLevelItem(0)
        for i in range(g.childCount()):
            it = g.child(i)
            if it.text(0) == name:
                return it
            for j in range(it.childCount()):
                if it.child(j).text(0) == name:
                    return it.child(j)
        return None

    # 電磁鐵組改名
    w.tree.setCurrentItem(find("magnet_A"))
    assert w.a_rename.isEnabled() and w.a_hide.isEnabled()
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", lambda *a, **k: ("magA", True))
    w.rename_selected()
    assert _pump(app, lambda: "magA" in st.instruments)
    assert "magA" in dict(labfile.configured(p))
    # 連線中的 DC1：問要不要中斷，再改名
    st.instruments["DC1"].connect()
    w.refresh()
    w.tree.setCurrentItem(find("DC1"))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", lambda *a, **k: ("DC9", True))
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
    w.rename_selected()
    assert _pump(app, lambda: "DC9" in st.instruments)
    assert st.instruments["magA"].options["sources"] == ["DC9", "DC2"]
    # 隱藏 VNA1 → 不顯示；勾「顯示隱藏的儀器」→ 再出現
    w.refresh()
    w.tree.setCurrentItem(find("VNA1"))
    w.toggle_hidden()
    assert _pump(app, lambda: find("VNA1") is None)
    assert "隱藏 1 台" in w.tree.topLevelItem(0).text(1)
    w.show_hidden.setChecked(True)
    it = find("VNA1")
    assert it is not None and "隱藏" in it.text(4)
    w.tree.setCurrentItem(it)
    assert w.a_hide.text() == "取消隱藏"
    w.toggle_hidden()
    assert _pump(app, lambda: dict(labfile.configured(p))["VNA1"]["hidden"] is False)
    w.close()
