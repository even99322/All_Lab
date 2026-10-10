"""0.0.8：跨電腦的儀器登錄、共用儀器「拉取群組」、遠端連線 / 控制、全部連線 / 斷線、卡住偵測、Hub 自我更新。"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from labcontrol import Station
from labcontrol.remote import Hub, HubError, NodeService, RemoteNode
from labcontrol.remote.hub import hostname
from labcontrol.remote.instruments import instrument_key
from tests.test_remote import TOKEN, _scheme, _wait, hubsrv  # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parents[1]
NET_LAB = {
    "simulate": True,
    "instruments": {
        "DC3": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::9017D5818::0::INSTR",
                "source": {"limits": [-0.2, 0.2], "ramp_rate": 5e-3, "max_jump": 5e-5, "resolution": 1e-6},
                "sim": {"initial_level": 0.1586}},
        "DC4": {"driver": "yokogawa.gs200", "address": "USB0::0x0B21::0x0039::9017D5816::0::INSTR",
                "source": {"limits": [-0.2, 0.2], "ramp_rate": 5e-3, "max_jump": 5e-5, "resolution": 1e-6},
                "sim": {"initial_level": 0.1586}},
        "VNA1": {"driver": "rs.zna", "address": "TCPIP0::192.168.1.11::INSTR", "labber_name": "VNA"},
        "pair": {"driver": "virtual.interleaved_pair", "sources": ["DC3", "DC4"],
                 "source": {"limits": [-0.2, 0.2], "ramp_rate": 5e-3, "max_jump": 5e-5, "resolution": 1e-6}},
    },
}


def test_instrument_keys():
    assert instrument_key("TCPIP0::192.168.1.11::INSTR") == "net:192.168.1.11"
    assert instrument_key("TCPIP::192.168.1.11::hislip0::INSTR") == "net:192.168.1.11"   # 同一台
    assert instrument_key("TCPIP0::10.0.0.5::5025::SOCKET") == "net:10.0.0.5:5025"
    assert instrument_key("USB0::0x0B21::0x0039::90ZC38697::0::INSTR") == "usb:0b21:0039:90zc38697"
    assert instrument_key("GPIB0::5::INSTR", host="PC1") == "gpib@pc1:0:5"
    assert instrument_key("", {"sources": ["DC1", "DC2"]}) is None
    assert instrument_key("", {"device": "DEV12345"}) == "zi:dev12345"


@pytest.fixture
def netnode(hubsrv):
    hub = hubsrv[0]
    st = Station(NET_LAB)
    n = NodeService(st, hub, name="QEL-PC").start()
    yield n, st, hub
    n.stop()


def _other_pc(hub, connected=True):
    """第二台電腦（控制端）也設定了同一台網路 VNA。"""
    hub.heartbeat({"id": "bob@OTHER-PC", "host": "OTHER-PC", "user": "bob", "version": "0.0.8", "role": "client",
                   "instruments": [{"name": "ZNA", "key": "net:192.168.1.11", "address": "TCPIP0::192.168.1.11::INSTR",
                                    "driver": "rs.zna", "connected": connected, "lease": None}]})


def test_registry_shared_claim_and_guard(netnode):
    node, st, hub = netnode
    rn = RemoteNode(hub, "QEL-PC")
    assert rn.call("connect_all") == {"DC3": "", "DC4": "", "VNA1": "", "pair": ""}
    _other_pc(hub)
    def vna_ready():
        r = next((r for r in hub.instruments_registry() if r["key"] == "net:192.168.1.11"), None)
        return r and r["shared"] and hostname() in r["connected_on"]
    assert _wait(vna_ready, 5)
    reg = {r["key"]: r for r in hub.instruments_registry()}
    vna = reg["net:192.168.1.11"]
    assert {e["host"] for e in vna["entries"]} == {hostname(), "OTHER-PC"}
    me = next(e for e in vna["entries"] if e["host"] == hostname())
    assert me["node"] == "QEL-PC" and me["name"] == "VNA1" and me["connected"]
    assert not reg["usb:0b21:0039:9017d5818"]["shared"]            # USB 只接在一台
    assert "net:192.168.1.11" in hub.state()["shared"]

    # 拉到另一台電腦 → 這個節點收到 release，中斷 VNA1
    r = hub.claim_instrument("net:192.168.1.11", "OTHER-PC")
    assert r["owner"] == "OTHER-PC" and r["released"] == ["QEL-PC"]
    assert _wait(lambda: not st.instruments["VNA1"].connected, 5)
    assert _wait(lambda: node.owners.get("net:192.168.1.11") == "OTHER-PC", 5)
    # 歸別台 → 全部連線會略過它（說明原因），單獨連線被拒絕
    res = rn.call("connect_all")
    assert res["DC3"] == "" and "OTHER-PC" in res["VNA1"] and not st.instruments["VNA1"].connected
    assert "目前歸 OTHER-PC" in rn.call("connect", names=["VNA1"])["VNA1"]
    assert "目前歸 OTHER-PC" in rn.call("get", refs=["VNA1.power"], connect=True)["VNA1.power"]["error"]
    # 拉回這台 → 可以連線
    hub.claim_instrument("net:192.168.1.11", hostname())
    assert _wait(lambda: node.owners.get("net:192.168.1.11") == hostname(), 5)
    assert rn.call("connect", names=["VNA1"]) == {"VNA1": ""} and st.instruments["VNA1"].connected
    # 量測使用中不能被拉走
    with st.lease("run-x", ["VNA1"]):
        node._post_status()
        with pytest.raises(HubError, match="量測中使用"):
            _claim_from(hub, "OTHER-PC-2", "net:192.168.1.11")
    res = rn.call("disconnect_all")
    assert all(v == "" for v in res.values()) and not any(i.connected for i in st.instruments.values())


def _claim_from(hub, host, key):
    """另一台電腦要拉取正在這台量測中的儀器。"""
    hub.heartbeat({"id": f"x@{host}", "host": host, "user": "x", "role": "client",
                   "instruments": [{"name": "V", "key": key, "connected": False}]})
    return hub.claim_instrument(key, host)


def test_remote_describe_get_set_and_scan(netnode, monkeypatch):
    node, st, hub = netnode
    rn = RemoteNode(hub, "QEL-PC")
    d = rn.call("describe", name="VNA1")
    params = {p["name"]: p for g in d["groups"] for p in g["params"]}
    assert params["start_freq"]["display_unit"] == "GHz" and params["start_freq"]["settable"]
    assert params["sweep_time"]["settable"] and params["sweep_time_auto"]["kind"] == "bool"   # 0.0.10：可控制掃描速度
    with pytest.raises(HubError, match="尚未連線"):
        rn.call("get_all", name="VNA1")
    rn.call("connect", names=["VNA1"])
    vals = rn.call("get_all", name="VNA1")
    assert vals["VNA1.points"]["value"] and "VNA1.power" in vals
    assert rn.call("set", ref="VNA1.power", value="-20 dBm") == pytest.approx(-20)
    dc = rn.call("describe", name="DC3")
    assert any(g["source"] for g in dc["groups"])
    # 掃描：VISA 資源（這裡用假的）回報到 Hub 的儀器登錄
    from labcontrol import diagnostics
    from labcontrol.diagnostics import Resource

    fake = [Resource("USB0::0x0B21::0x0039::9017D5818::0::INSTR"), Resource("TCPIP0::192.168.1.99::INSTR")]
    fake[0].idn, fake[1].idn = "YOKOGAWA,GS210,9017D5818,2.02", "Keysight,N5222B,MY123,1.0"
    monkeypatch.setattr(diagnostics, "scan_resources", lambda identify=True, **k: fake)
    items = rn.call("scan", timeout=30)
    assert {i["configured_as"] for i in items} == {"DC3", None}
    assert _wait(lambda: any(r["key"] == "net:192.168.1.99" for r in hub.instruments_registry()), 5)
    new = next(r for r in hub.instruments_registry() if r["key"] == "net:192.168.1.99")
    assert new["model"] == "N5222B" and new["entries"][0]["detected"] and not new["entries"][0]["configured"]


def test_node_never_blocks_measurement_on_slow_hub(netnode, tmp_path, monkeypatch):
    """Hub 很慢（VPN 逾時）時，節點上的量測不能被拖住（0.0.7 的卡住原因之一）。"""
    node, st, hub = netnode
    orig = node.hub.write_batch
    calls = []

    def slow(name, events):
        calls.append(len(events))
        time.sleep(1.5)
        return orig(name, events)
    monkeypatch.setattr(node.hub, "write_batch", slow)
    monkeypatch.setattr(node.hub, "write_head", lambda name, head: time.sleep(1.5))
    rn = RemoteNode(hub, "QEL-PC")
    sch = _scheme(stop=158.605)                       # 11 點
    sch.output["root"] = str(tmp_path / "data")
    t0 = time.time()
    rn.call("run_scheme", scheme=sch.to_dict())
    from labcontrol.measure.runner import Runner

    assert _wait(lambda: node._last_runner._done.is_set(), 20)
    took = time.time() - t0
    assert took < 4.0, f"量測被 Hub 拖慢：{took:.1f} s"
    assert _wait(lambda: sum(calls) > 11 and not node._out, 20)     # 資料之後仍全部送到


def test_stall_watchdog_dumps_threads(tmp_path, monkeypatch):
    from labcontrol.core.events import EventBus
    from labcontrol.measure import watchdog

    monkeypatch.setattr("labcontrol.paths.lab_path", lambda *p: tmp_path.joinpath(*p))

    class FakeRunner:
        run_id, index, manual = "r1", 3, False
        bus = EventBus()

        class state:
            value = "running"
    msgs = []
    FakeRunner.bus.subscribe("log", lambda t, p: msgs.append(p["message"]))
    dog = watchdog.StallWatchdog(FakeRunner(), threshold_s=0.3).start()
    try:
        assert _wait(lambda: dog.dumps >= 1, 5)
    finally:
        dog.stop()
    f = next(tmp_path.joinpath("logs").glob("stall_r1_*.txt"))
    text = f.read_text(encoding="utf-8")
    assert "沒有進度" in text and "MainThread" in text and "第 4 點" in msgs[0]


def test_hub_self_update_from_release_and_zip(tmp_path):
    """真的啟動一個 Hub 程序：從發佈資料夾更新 → 重新執行成新版；再用 zip 安裝（Monitor 內附版本）。"""
    import shutil

    from labmonitor.client import HubClient, bundled_hub, zip_package

    app = tmp_path / "app"
    shutil.copytree(ROOT / "labhub", app / "labhub", ignore=shutil.ignore_patterns("__pycache__"))
    rel = tmp_path / "rel" / "LabControl" / "v9.9.9"
    shutil.copytree(ROOT / "labhub", rel / "labhub", ignore=shutil.ignore_patterns("__pycache__"))
    (rel / "main.py").write_text("")
    init = rel / "labhub" / "__init__.py"
    init.write_text(init.read_text(encoding="utf-8").replace(
        f'__version__ = "{__import__("labhub").__version__}"', '__version__ = "9.9.9"'), encoding="utf-8")
    port = 18000 + os.getpid() % 1000
    env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUB_")}
    p = subprocess.Popen([sys.executable, "-S", "-m", "labhub", "--port", str(port), "--data", "../data",
                          "--releases", str(tmp_path / "rel"), "--token", "tk"], cwd=app, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    c = HubClient(f"http://127.0.0.1:{port}", "tk")
    try:
        assert _wait(lambda: _alive(c), 10)
        info = c.hub_info()
        assert info["update_available"] and info["latest_release"] == "9.9.9"
        assert c.hub_update()["installed"] == "9.9.9"
        assert _wait(lambda: _alive(c) and c.ping()["version"] == "9.9.9", 15)
        assert c.hub_info()["booted"] and json.loads((tmp_path / "data/hub_app/current.json").read_text())["healthy"]
        # zip 安裝（內附版本比較舊 → 開機時選較新的 9.9.9 → 仍是 9.9.9；這裡只驗證安裝流程）
        z = zip_package(bundled_hub())
        r = c._req("POST", "/api/hub/install", raw=z, ctype="application/zip")
        assert r["ok"] and r["installed"] == __import__("labhub").__version__
        assert _wait(lambda: _alive(c), 15)
    finally:
        p.terminate()
        p.wait(5)


def _alive(c):
    try:
        c.ping()
        return True
    except Exception:  # noqa: BLE001
        return False


def test_boot_falls_back_after_failed_starts(tmp_path, monkeypatch):
    from labhub import selfupdate

    data = tmp_path / "d"
    pkg = tmp_path / "v9" / "labhub"
    pkg.mkdir(parents=True)
    (pkg / "server.py").write_text("")
    selfupdate.write_current(data, {"version": "9.9.9", "path": str(tmp_path / "v9"), "attempts": 2, "healthy": False})
    monkeypatch.delenv("LABHUB_BOOTED", raising=False)
    execs = []
    monkeypatch.setattr(os, "execve", lambda *a: execs.append(a))
    selfupdate.boot(["--data", str(data)])
    assert not execs and (data / "hub_app" / "current.failed.json").exists()      # 退回內建版本
    selfupdate.write_current(data, {"version": "9.9.9", "path": str(tmp_path / "v9"), "attempts": 0, "healthy": True})
    monkeypatch.chdir(tmp_path)
    selfupdate.boot(["--data", "d"])
    assert execs and execs[0][2]["LABHUB_BOOTED"] == "1" and str(data) in execs[0][1]   # 相對路徑改成絕對


@pytest.mark.skipif(not os.environ.get("QT_QPA_PLATFORM") and os.name != "nt" and not os.environ.get("DISPLAY"),
                    reason="需要 Qt 顯示環境")
def test_monitor_window(netnode):
    from PyQt6 import QtWidgets

    from labmonitor.instrument_dialog import RemoteInstrumentDialog
    from labmonitor.window import MonitorWindow

    node, st, hub = netnode
    _other_pc(hub, connected=False)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def pump(s):
        t = time.time()
        while time.time() - t < s:
            app.processEvents()
            time.sleep(0.02)
    w = MonitorWindow({"hub_urls": [hub.url], "token": TOKEN, "refresh_s": 0.5}, persist=False)
    w.show()
    assert _wait(lambda: (pump(0.1) or True) and w.nodes.topLevelItemCount() == 1, 10)
    assert w.nodes.topLevelItem(0).text(0) == "QEL-PC" and w.nodes.topLevelItem(0).childCount() == 4
    assert _wait(lambda: (pump(0.1) or True) and w.insts.topLevelItemCount() >= 3, 10)
    vna = next(w.insts.topLevelItem(i) for i in range(w.insts.topLevelItemCount())
               if w.insts.topLevelItem(i).text(3) == "net:192.168.1.11")
    assert "共用" in vna.text(6) and vna.childCount() == 2
    # 選節點 → 全部連線
    w.nodes.setCurrentItem(w.nodes.topLevelItem(0))
    w.node_action("connect_all")
    assert _wait(lambda: (pump(0.1) or True) and st.instruments["DC3"].connected, 10)
    # 遠端控制視窗：讀全部、寫入 DC3 電流
    dlg = RemoteInstrumentDialog(w.client, "QEL-PC", "DC3", w)
    dlg.show()
    assert _wait(lambda: (pump(0.1) or True) and "DC3.ch1.level" in dlg.rows and "已讀取" in dlg.msg.text(), 10)
    row = dlg.rows["DC3.ch1.level"]
    row.w.setText("158.62")
    row._mark()
    dlg.write_dirty()
    assert _wait(lambda: (pump(0.1) or True) and abs(st.parameter("DC3.level").cache - 0.15862) < 1e-9, 15)
    # Hub 控制台分頁：這裡沒有控制代理 → 顯示第一次設定的說明
    w.tabs.setCurrentWidget(w.console)
    assert _wait(lambda: (pump(0.1) or True) and "連不到控制代理" in w.console.sub.text() and "第一次使用" in w.console.msg.text(), 15)
    dlg.close()
    w.close()
    pump(0.2)
