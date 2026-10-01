"""遠端量測：節點 ↔ Lab Control Hub（這裡在本機開一個真的 Hub 伺服器）↔ 控制端。"""
import os
import threading
import time
import urllib.request

import pytest

from labcontrol.core.events import EventBus
from labcontrol.remote import Hub, HubError, LiveFeed, NodeService, RemoteNode, auto_update_nodes, find_hub, list_nodes
from labcontrol.remote.versioning import is_newer, parse_version
from labcontrol.scheme import Block, Scheme
from labhub.server import Config, make_server

TOKEN = "test-token"


def _scheme(stop=158.602):
    return Scheme("遠端測試", [Block("set", target="pair", mode="sweep", start=158.60, stop=stop, step=0.0005,
                                     unit="mA", children=[Block("measure", instrument="VNA1", traces=["S21"],
                                                                settings={"points": 101})])],
                  output={"formats": ["hdf5"], "file_name": "remote.hdf5"})


@pytest.fixture
def hubsrv(tmp_path, monkeypatch):
    """本機 Hub 伺服器 + 指向它的 settings。回傳 (Hub client, HubState, releases 資料夾)。"""
    from labcontrol.settings import settings

    monkeypatch.setenv("LABHUB_NO_RESTART", "1")         # 測試中不讓 Hub 重新執行自己
    rel = tmp_path / "Releases"
    (rel / "LabControl").mkdir(parents=True)
    cfg = Config(tmp_path / "hubdata", releases_dir=rel, token=TOKEN)
    srv, state = make_server(cfg, "127.0.0.1", 0)
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    th.start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    s = settings()
    old = s.get("remote")
    s.set("remote.hub_urls", [url])
    s.set("remote.token", TOKEN)
    s.set("remote.heartbeat_s", 0.2)
    s.set("remote.live_interval_s", 0.05)
    s.set("remote.command_wait_s", 2)
    s.set("remote.download_root", str(tmp_path / "downloads"))
    yield Hub(url, TOKEN), state, rel
    s.set("remote", old)
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def hub(hubsrv):
    return hubsrv[0]


@pytest.fixture
def node(station, hub):
    n = NodeService(station, hub, name="QEL-PC").start()
    yield n
    n.stop()


def _wait(cond, t=15):
    t0 = time.time()
    while time.time() - t0 < t:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_versions():
    assert is_newer("0.0.5", "0.0.4") and is_newer("0.0.4a", "0.0.4") and is_newer("v0.1.0", "0.0.9b")
    assert not is_newer("0.0.4", "0.0.4") and parse_version("v1.2.3c") == (1, 2, 3, "c")


def test_find_hub_and_token(hubsrv, monkeypatch):
    from labcontrol.settings import settings

    hub, state, _ = hubsrv
    assert find_hub().url == hub.url
    settings().set("remote.token", "wrong")
    with pytest.raises(HubError, match="token"):
        find_hub()
    with pytest.raises(HubError, match="拒絕"):
        Hub(hub.url, "wrong").state()
    settings().set("remote.hub_urls", ["127.0.0.1:1"])          # 沒寫 http:// 也可以；連不到 → 清楚的錯誤
    with pytest.raises(HubError, match="連不到"):
        find_hub()


def test_dashboard_login_and_devices(hubsrv):
    hub, state, _ = hubsrv
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor())
    r = opener.open(hub.url + "/")                                  # 沒登入 → 登入頁
    assert "登入" in r.read().decode() and r.geturl().endswith("/login")
    r = opener.open(hub.url + "/login", data=f"token={TOKEN}".encode())
    page = r.read().decode()
    assert "Lab Control Hub" in page and "量測節點" in page          # cookie 登入後看到監控頁
    hub.heartbeat({"id": "alice@PC-01", "host": "PC-01", "user": "alice", "version": "0.0.7", "role": "client"})
    s = hub.state()
    d = next(d for d in s["devices"] if d["id"] == "alice@PC-01")
    assert d["online"] and d["role"] == "client" and d["ip"] == "127.0.0.1"
    state.save(force=True)                                          # Hub 重開後記得有哪些電腦
    assert "alice@PC-01" in (state.cfg.data_dir / "state.json").read_text(encoding="utf-8")


def test_node_listing_and_commands(node, hub, hubsrv):
    assert _wait(lambda: any(n["online"] for n in list_nodes(hub)))
    st = list_nodes(hub)[0]
    assert st["name"] == "QEL-PC" and st["state"] == "idle" and {"DC3", "VNA1"} <= {i["name"] for i in st["instruments"]}
    s = hub.state()
    assert any(d.get("role") == "node" and d.get("node") == "QEL-PC" for d in s["devices"])   # 網頁「所有電腦」
    rn = RemoteNode(hub, "QEL-PC")
    t0 = time.perf_counter()
    assert rn.call("ping")["name"] == "QEL-PC"
    assert time.perf_counter() - t0 < 1.0                          # long-poll：指令立即送達
    got = rn.call("get", refs=["DC3.level", "nope.level"], connect=True)
    assert got["DC3.level"]["value"] == pytest.approx(0.1586) and got["nope.level"]["error"]
    assert rn.call("set", ref="DC3.level", value="158.61 mA") == pytest.approx(0.15861)
    with pytest.raises(Exception):
        rn.call("stop")                                   # 沒有量測可停
    mirror = rn.mirror_station()                          # 控制端用節點的儀器清單建模擬鏡像
    assert mirror.simulate and "VNA1" in mirror.instruments
    # 同名節點不能在另一個程式上線
    other = NodeService(node.station, hub, name="QEL-PC")
    import labcontrol.remote.node as node_mod
    old = node_mod.hostname
    node_mod.hostname = lambda: "OTHER-PC"
    try:
        with pytest.raises(HubError, match="已經在"):
            other.start()
    finally:
        node_mod.hostname = old


def test_remote_run_live_feed_and_files(node, hub, tmp_path):
    rn = RemoteNode(hub, "QEL-PC")
    bus = EventBus()
    seen = []
    bus.subscribe("*", lambda t, p: seen.append((t, p)))
    feed = LiveFeed(hub, "QEL-PC", bus)
    sch = _scheme()
    sch.output["root"] = str(tmp_path / "data")
    res = rn.call("run_scheme", timeout=20, scheme=sch.to_dict())
    assert res["run_id"] and res["file_name"] == "remote.hdf5"
    assert _wait(lambda: (feed.poll(wait=1) or True) and any(t == "remote.files" for t, _ in seen), 30)
    topics = [t for t, _ in seen]
    assert "run.started" in topics and topics.count("point.shot") >= 5 and "run.finished" in topics
    started = next(p for t, p in seen if t == "run.started")
    assert started["dataset"].axes[0].name and len(started["dataset"].plan) == 5
    shot = next(p for t, p in seen if t == "point.shot")
    assert shot["shot"]["S21"].size == 101 and shot["shot"]["S21"].dtype.kind == "c"
    assert list((tmp_path / "data").rglob("remote.h5"))          # 節點自己存檔
    files = next(p for t, p in seen if t == "remote.files")["files"]
    rels = [f["rel"] for f in files]
    assert any(r.endswith("remote.h5") and "/" in r for r in rels)   # 保留資料夾結構（相對資料根目錄）
    assert any("_raw/" in r for r in rels)                           # 原始檔也上傳
    listed = hub.files("QEL-PC")
    assert {f["rel"] for f in listed} >= set(rels)
    dst = hub.download(files[0]["url"], tmp_path / "dl" / "x.h5")    # 控制端下載
    src = next((tmp_path / "data").rglob(files[0]["rel"].split("/")[-1]))
    assert dst.read_bytes() == src.read_bytes()
    assert _wait(lambda: ((rn.status() or {}).get("run") or {}).get("status") == "finished", 5)
    st = rn.status()
    assert "@" in st["run"]["by"]
    assert any("已上傳" in (l.get("message") or "") for l in st["log"])     # 網頁顯示節點訊息


def test_late_joiner_and_hub_restart_reset(node, hub, tmp_path):
    """中途加入的控制端從這次量測的開頭開始；序號比 Hub 新（Hub 重開過）時重新同步。"""
    rn = RemoteNode(hub, "QEL-PC")
    sch = _scheme()
    sch.output["root"] = str(tmp_path / "data")
    rn.call("run_scheme", scheme=sch.to_dict())
    assert _wait(lambda: (rn.status() or {}).get("run", {}) and rn.status()["run"].get("status") == "finished", 30)
    bus = EventBus()
    seen = []
    bus.subscribe("*", lambda t, p: seen.append(t))
    feed = LiveFeed(hub, "QEL-PC", bus)
    feed.last_seq = 10 ** 6                       # 模擬 Hub 重開（序號重來）
    feed.poll()
    feed.poll()
    assert seen[0] == "run.started" and "run.finished" in seen and seen.count("point.shot") == 5


def test_remote_stop(node, hub, station, tmp_path):
    rn = RemoteNode(hub, "QEL-PC")
    sch = _scheme(stop=158.70)                           # 201 點：夠長
    sch.output["root"] = str(tmp_path / "data")
    rn.call("run_scheme", scheme=sch.to_dict())
    with pytest.raises(Exception):
        rn.call("run_scheme", scheme=sch.to_dict())      # 同時只能一個量測
    assert _wait(lambda: (rn.status() or {}).get("state") == "running", 10)
    rn.call("stop")
    assert _wait(lambda: (rn.status() or {}).get("state") in ("aborted", "finished", "idle"), 20)


def test_update_via_hub(node, hubsrv, tmp_path, monkeypatch):
    import labcontrol
    from labcontrol.remote import updater

    hub, state, rel = hubsrv
    calls = []
    monkeypatch.setattr(updater, "perform_update", lambda v, h, log=print: calls.append((v, h.url)) or "test")
    restarted = []
    node.on_restart = lambda: restarted.append(True)
    rn = RemoteNode(hub, "QEL-PC")
    r = rn.call("update", version="9.9.9")
    assert r["accepted"] is False and "找不到" in r["reason"]           # Hub 上沒有這個版本
    (rel / "LabControl" / "v9.9.9").mkdir()
    (rel / "LabControl" / "v9.9.9" / "main.py").write_text("")
    monkeypatch.setattr("labcontrol.remote.client.__version__", "9.9.9")   # 控制端比較新 → 自動請節點更新
    res = auto_update_nodes(hub)
    assert res["QEL-PC"]["accepted"] is True
    assert _wait(lambda: restarted, 5) and calls == [("9.9.9", hub.url)]
    assert rn.call("update", version=labcontrol.__version__)["accepted"] is False   # 不會「更新」到同版


def test_install_from_hub(hubsrv, tmp_path, monkeypatch):
    from labcontrol.remote import updater

    hub, state, rel = hubsrv
    src = rel / "LabControl" / "v0.9.0"
    (src / "labcontrol").mkdir(parents=True)
    (src / "main.py").write_text("print('hi')")
    (src / "labcontrol" / "__init__.py").write_text("")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("x")
    (src / "requirements.txt").write_text((updater.APP_ROOT / "requirements.txt").read_text(encoding="utf-8"),
                                          encoding="utf-8")
    assert hub.releases() == ["0.9.0"]
    dst = updater.install_from_hub("0.9.0", hub, log=lambda m: None)
    assert (dst / "main.py").exists() and (dst / "labcontrol" / "__init__.py").exists() and (dst / ".complete").exists()
    assert not (dst / ".git").exists()                                   # 不帶 git 資料
    assert updater._python_for(dst, lambda m: None) == __import__("sys").executable     # 套件相同 → 沿用
    popen = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: popen.append((a, k)))
    monkeypatch.setattr(updater, "labapp_executable", lambda: None)
    assert updater.perform_update("0.9.0", hub, log=lambda m: None) == "Hub 下載"
    assert popen and popen[0][1]["cwd"] == str(dst)


@pytest.mark.skipif(not os.environ.get("QT_QPA_PLATFORM") and os.name != "nt" and not os.environ.get("DISPLAY"),
                    reason="需要 Qt 顯示環境")
def test_gui_run_on_node(hubsrv, tmp_path, monkeypatch):
    """A 電腦開量測節點；B 電腦選「執行於：節點」→ 開始 → B 的即時監控顯示節點的量測，結束後自動下載資料檔。"""
    from PyQt6 import QtWidgets

    from labcontrol import Station
    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.remote import save_node_state
    from tests.conftest import SIM_LAB

    hub = hubsrv[0]
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def pump(s):
        t = time.time()
        while time.time() - t < s:
            app.processEvents()
            time.sleep(0.02)
    A = LabControlWindow(Station(SIM_LAB), Scheme("A"))
    A.act_node.setChecked(True)
    assert _wait(lambda: (pump(0.1) or True) and A.remote.node is not None, 10)
    save_node_state(auto_start=False)
    sch = _scheme(stop=158.603)
    sch.output["root"] = str(tmp_path / "data")
    B = LabControlWindow(Station(SIM_LAB), sch)
    assert _wait(lambda: (pump(0.2) or True) and B.target.count() > 1, 10)
    assert A.target.count() == 1                       # 自己這台不會列出自己的節點
    assert "Hub：✔" in B.remote_status.text()
    B.target.setCurrentIndex(1)
    assert _wait(lambda: (pump(0.2) or True) and B.remote_target is not None, 10)
    assert B.doc.result.ok
    B.start_measurement()
    assert _wait(lambda: (pump(0.2) or True) and B.monitor.progress.value() == 7, 20)
    assert _wait(lambda: (pump(0.2) or True) and list((tmp_path / "downloads").rglob("remote.h5")), 15)
    devs = hub.state()["devices"]
    assert len([d for d in devs if d["online"]]) >= 1 and any(d.get("role") == "node" for d in devs)
    for w in (B, A):
        w.doc.dirty = False
        w.close()
    A.act_node.setChecked(False)
    pump(0.3)
