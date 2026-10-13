"""0.0.31：斜坡預設 10 mA/s、專案範本（Hub 共用）、6 個範本、常改的數值、儀器依量測電腦對應與改名、專題歸檔。"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from labcontrol.settings import settings

TOKEN = "t0031"


def _pump(sec=0.3, until=None):
    from PyQt6 import QtWidgets

    t = time.time() + sec
    while time.time() < t:
        QtWidgets.QApplication.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until is not None else True


@pytest.fixture
def hubsrv(tmp_path):
    from labhub.server import Config, make_server
    from labcontrol.integrations import platform as plat

    cfg = Config(tmp_path / "hubdata", token=TOKEN)
    srv, state = make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    s = settings()
    old = s.get("remote")
    s.set("remote.hub_urls", [url])
    s.set("remote.token", TOKEN)
    plat._hub_cache.update(hub=None, at=0.0)
    yield url, state, srv
    s.set("remote", old)
    plat._hub_cache.update(hub=None, at=0.0)
    srv.shutdown()
    srv.server_close()


# ---- 1. 斜坡預設 10 mA/s ---------------------------------------------------------------------------
def test_default_ramp_is_10_mA_per_s(tmp_path):
    from labcontrol import settings as st_mod
    from labcontrol.core.config import migrate_instruments_text, source_settings

    defaults = st_mod._load_yaml(st_mod.DEFAULTS_DIR / "instruments.yaml")
    assert "5.0e-4" not in (st_mod.DEFAULTS_DIR / "instruments.yaml").read_text(encoding="utf-8")
    assert defaults["_yoko_defaults"]["source"]["ramp_rate"] == pytest.approx(1e-2)
    assert source_settings({"source": {"ramp_rate": 5e-4}})["ramp_policy"].rate == pytest.approx(1e-2)  # 舊預設
    assert source_settings({"source": {"ramp_rate": 2e-3}})["ramp_policy"].rate == pytest.approx(2e-3)  # 自己改的
    text = "a:\n  source:\n    ramp_rate: 5.0e-4   # x\n    max_jump: 5.0e-5\nb:\n  source: {ramp_rate: 2e-3}\n"
    new = migrate_instruments_text(text)
    assert "ramp_rate: 1.0e-2" in new and "5.0e-5" in new and "2e-3" in new
    user = tmp_path / "settings.yaml"
    user.write_text("run_defaults: {approach_rate: 0.5 mA/s, park_rate: 1 mA/s}\n", encoding="utf-8")
    s = st_mod.Settings.load(user)
    assert s.get("run_defaults.approach_rate") == "10 mA/s" and s.get("run_defaults.park_rate") == "10 mA/s"
    from labcontrol.paths import _migrate_files

    lab = tmp_path / "LAB"
    lab.mkdir()
    (lab / "instruments.yaml").write_text(text, encoding="utf-8")
    _migrate_files(lab)
    assert "ramp_rate: 1.0e-2" in (lab / "instruments.yaml").read_text(encoding="utf-8")
    assert (lab / "instruments.yaml.bak-0.0.31").read_text(encoding="utf-8") == text


def test_old_templates_move_aside(tmp_path):
    from labcontrol.paths import ensure_lab_home

    lab = tmp_path / "LAB"
    (lab / "templates").mkdir(parents=True)
    (lab / "templates" / "04_n_layer.scheme.yaml").write_text("scheme: 1\nname: old\nblocks: []\n", encoding="utf-8")
    (lab / "templates" / "mine.scheme.yaml").write_text("scheme: 1\nname: mine\nblocks: []\n", encoding="utf-8")
    ensure_lab_home(lab)
    names = sorted(p.name for p in (lab / "templates").glob("*.scheme.yaml"))
    assert "04_n_layer.scheme.yaml" not in names and "mine.scheme.yaml" in names
    assert "06_flux.scheme.yaml" in names and (lab / "templates" / "舊範本_0.0.30" / "04_n_layer.scheme.yaml").exists()


# ---- 2. 專案範本 -------------------------------------------------------------------------------------
def test_project_templates_sync_through_hub(hubsrv, tmp_path):
    from labcontrol.integrations import project_templates as pt
    from labcontrol.scheme.templates import load_template

    url, state, srv = hubsrv
    s = load_template("02_2d")
    s.name = "專案 7 的 2D"
    assert pt.save("7", "02_2d", s.to_dict()) is True
    f = Path(state.platform.data_dir) / "project_templates" / "7" / "02_2d.json"
    assert f.exists() and not pt.pending()
    assert pt.load("7", "02_2d")["name"] == "專案 7 的 2D"
    assert pt.load("7", "01_single_shot") is None and pt.load("../x", "02_2d") is None
    from labcontrol.integrations import platform as plat

    plat._hub_cache.update(hub=None, at=0.0)
    s0 = settings().get("remote.hub_urls")
    settings().set("remote.hub_urls", ["http://127.0.0.1:9"])          # 連不到 Hub：先存本機，標成待送
    s.name = "離線改的"
    assert pt.save("7", "02_2d", s.to_dict()) is False and pt.pending() == 1
    assert pt.load("7", "02_2d")["name"] == "離線改的"
    settings().set("remote.hub_urls", s0)
    plat._hub_cache.update(hub=None, at=0.0)
    assert pt.load("7", "02_2d")["name"] == "離線改的"                   # 本機比較新 → 送上去
    t0 = time.time()
    while pt.pending() and time.time() - t0 < 5:
        time.sleep(0.05)
    assert not pt.pending() and "離線改的" in f.read_text(encoding="utf-8")


@pytest.fixture
def win(tmp_path, monkeypatch):
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets

    if os.environ.get("QT_QPA_PLATFORM") != "offscreen" and not os.environ.get("DISPLAY") and os.name != "nt":
        pytest.skip("需要 Qt 顯示環境")
    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.core.station import Station
    from tests.conftest import SIM_LAB

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    st = Station(SIM_LAB)
    st.sim_time_scale = 0.0
    w = LabControlWindow(st, None, root=str(tmp_path / "data"))
    w.show()
    _pump(0.2)
    yield w
    w.doc.dirty = False
    w.close()
    st.close()


def test_window_project_templates(win, hubsrv):
    from labcontrol.integrations import project_templates as pt

    sync = win.project_sync
    win.doc.mutate(lambda s: s.output.__setitem__("lab_project", {"id": "11", "name": "P11"}))
    sync.load_template("02_2d")
    assert _pump(5, lambda: not sync.loading and win.doc.scheme.template == "02_2d"
                 and pt.load("11", "02_2d") is not None)
    assert win.doc.scheme.output["lab_project"]["id"] == "11"
    # 改一個值 → 停手後寫回專案 11
    b = next(x for x in win.doc.scheme.flat() if x.kind == "set")
    win.doc.mutate(lambda s: setattr(s.graph.nodes[b.id], "stop", 133.0))
    sync.timer.setInterval(10)
    sync.timer.start()
    assert _pump(5, lambda: not win.doc.dirty and "133" in str(pt.load("11", "02_2d")))
    # 換到專案 12（還沒有）：沿用目前設定並存一份；改專案 12 的值不影響 11
    win.files.project_changing.emit({"id": "11", "name": "P11"})
    win.doc.mutate(lambda s: s.output.__setitem__("lab_project", {"id": "12", "name": "P12"}))
    win.files.project_changed.emit({"id": "12", "name": "P12"})
    assert _pump(5, lambda: not sync.loading and pt.load("12", "02_2d") is not None)
    b = next(x for x in win.doc.scheme.flat() if x.kind == "set")
    win.doc.mutate(lambda s: setattr(s.graph.nodes[b.id], "stop", 144.0))
    sync.flush(background=False)
    assert "144" in str(pt.load("12", "02_2d")) and "144" not in str(pt.load("11", "02_2d"))
    # 回到專案 11：載入它自己的版本
    win.files.project_changing.emit({"id": "12", "name": "P12"})
    win.doc.mutate(lambda s: s.output.__setitem__("lab_project", {"id": "11", "name": "P11"}))
    win.files.project_changed.emit({"id": "11", "name": "P11"})
    assert _pump(5, lambda: next(x for x in win.doc.scheme.flat() if x.kind == "set").stop == 133.0)


# ---- 3. 常改的數值 -------------------------------------------------------------------------------------
def test_quick_panel_lists_values_changed_twice(win):
    from labcontrol.scheme.templates import load_template

    win.doc.replace_scheme(load_template("02_2d"))
    q = win.flow.quick
    b = next(x for x in win.doc.scheme.flat() if x.kind == "set")
    m = next(x for x in win.doc.scheme.flat() if x.kind == "measure")
    win.doc.mutate(lambda s: setattr(s.graph.nodes[b.id], "start", 101.0))
    assert not q.rows                                                      # 一次：還不列
    win.doc.mutate(lambda s: setattr(s.graph.nodes[b.id], "start", 102.0))
    for v in ("5.02 GHz", "5.021 GHz"):
        win.doc.mutate(lambda s, v=v: s.graph.nodes[m.id].settings.__setitem__("start_freq", v))
    keys = [k for k, _ in q.rows]
    assert f"{b.id}.start" in keys and f"{m.id}.settings.start_freq" in keys
    w = dict(q.rows)[f"{b.id}.start"]
    assert w.value() == pytest.approx(102.0)
    w.setValue(110.0)                                                      # 從面板改 → 方塊跟著改
    assert win.doc.node(b.id).start == pytest.approx(110.0)
    wf = dict(q.rows)[f"{m.id}.settings.start_freq"]
    assert wf.value() == pytest.approx(5.021)
    wf.setValue(5.03)
    from labcontrol.core.units import parse_quantity

    assert parse_quantity(win.doc.node(m.id).settings["start_freq"], "Hz") == pytest.approx(5.03e9)
    win.doc.mutate(lambda s: s.quick.__setitem__(f"{b.id}.start", -1))   # 移除 → 不再列
    assert f"{b.id}.start" not in [k for k, _ in q.rows]
    assert "quick" in win.doc.scheme.to_dict()


# ---- 4. 儀器依量測電腦對應、改名 -------------------------------------------------------------------------
LAB2 = {
    "DC1": {"driver": "yokogawa.gs200", "node": "LAB-1"}, "DC2": {"driver": "yokogawa.gs200", "node": "LAB-1"},
    "DC3": {"driver": "yokogawa.gs200", "node": "LAB-1"}, "DC4": {"driver": "yokogawa.gs200", "node": "LAB-1"},
    "DC5": {"driver": "yokogawa.gs200", "node": "LAB-2"}, "DC6": {"driver": "yokogawa.gs200", "node": "LAB-2"},
    "DC7": {"driver": "yokogawa.gs200", "node": "LAB-2"}, "DC8": {"driver": "yokogawa.gs200", "node": "LAB-2"},
    "VNA1": {"driver": "rs.zna", "node": "LAB-1"}, "VNA2": {"driver": "rs.zna", "node": "LAB-2"},
    "magnet_A": {"driver": "virtual.interleaved_pair", "group": "magnet", "sources": ["DC3", "DC4"]},
    "magnet_B": {"driver": "virtual.interleaved_pair", "group": "magnet", "sources": ["DC1", "DC2"]},
    "magnet_C": {"driver": "virtual.interleaved_pair", "group": "magnet", "sources": ["DC5", "DC6"]},
    "magnet_D": {"driver": "virtual.interleaved_pair", "group": "magnet", "sources": ["DC7", "DC8"]},
}


def test_role_lists_and_remap():
    from labcontrol import Station
    from labcontrol.scheme import build_catalog
    from labcontrol.scheme.remap import remap_scheme, role_lists
    from labcontrol.scheme.templates import load_template

    roles = role_lists(LAB2)
    assert roles["LAB-1"] == {"magnet": ["magnet_A", "magnet_B"], "vna": ["VNA1"]}
    assert roles["LAB-2"] == {"magnet": ["magnet_C", "magnet_D"], "vna": ["VNA2"]}
    lab2 = {k: dict(v, address="X") for k, v in LAB2.items() if k in ("DC5", "DC6", "DC7", "DC8", "VNA2",
                                                                     "magnet_C", "magnet_D")}
    st = Station({"simulate": True, "instruments": lab2})
    cat = build_catalog(st)
    s = load_template("05_two_layer_one_file")                     # magnet_A × magnet_B → VNA1
    m = remap_scheme(s, cat, roles)
    assert m == {"magnet_A": "magnet_C", "magnet_B": "magnet_D", "VNA1": "VNA2"}
    tg = [b.target for b in s.flat() if b.kind == "set"]
    assert tg == ["magnet_C", "magnet_D"] and [b.instrument for b in s.flat() if b.kind == "measure"] == ["VNA2"]
    assert remap_scheme(s, cat, roles) == {}                        # 已經是這台的：不動
    st.close()


def test_rename_label_keeps_comments():
    from labcontrol.core.config import set_instrument_label

    text = ("instruments:\n  magnet_A:   # 磁鐵\n    driver: virtual.interleaved_pair\n"
            "    label: 磁鐵 A（DC3+DC4）   # 顯示\n    sources: [DC3, DC4]\n  magnet_B:\n    driver: x\n")
    new = set_instrument_label(text, "magnet_A", "左邊線圈")
    assert 'label: "左邊線圈"   # 顯示' in new and "# 磁鐵" in new
    new = set_instrument_label(new, "magnet_B", "右邊")
    import yaml

    d = yaml.safe_load(new)["instruments"]
    assert d["magnet_A"]["label"] == "左邊線圈" and d["magnet_B"]["label"] == "右邊" and d["magnet_B"]["driver"] == "x"


def test_window_rename_magnet_group(win, monkeypatch):
    from PyQt6 import QtWidgets

    from labcontrol.paths import lab_path

    f = lab_path("instruments.yaml")
    f.parent.mkdir(parents=True, exist_ok=True)
    old = f.read_text(encoding="utf-8") if f.exists() else None
    f.write_text("instruments:\n  pair:\n    driver: virtual.interleaved_pair\n    label: 磁鐵 A\n",
                 encoding="utf-8")
    try:
        monkeypatch.setattr(QtWidgets.QInputDialog, "getText", lambda *a, **k: ("主線圈", True))
        win.rename_magnet_group("pair")
        assert win.doc.catalog.target("pair").label == "主線圈"
        assert "主線圈" in f.read_text(encoding="utf-8")
    finally:
        if old is None:
            f.unlink()
        else:
            f.write_text(old, encoding="utf-8")


# ---- 5. 專題歸檔 -----------------------------------------------------------------------------------------
def test_archive_plan_and_copy(tmp_path):
    from labcontrol.data import archive

    root = tmp_path / "數據集散地"
    day = root / "未分專案" / "2026" / "10" / "Data_1013"
    day.mkdir(parents=True)
    (day / "a.nc").write_bytes(b"A" * 1000)
    (day / "b.nc").write_bytes(b"B" * 10)
    (day / ".DS_Store").write_bytes(b"x")
    other = tmp_path / "elsewhere" / "run1"
    other.mkdir(parents=True)
    (other / "c.hdf5").write_bytes(b"C")
    nas = tmp_path / "nas"
    items = archive.plan([day, other / "c.hdf5"], "YIG", nas, root)
    dst = sorted(str(d.relative_to(nas)).replace("\\", "/") for _, d in items)
    assert dst == ["YIG/2026/10/Data_1013/a.nc", "YIG/2026/10/Data_1013/b.nc", "YIG/run1/c.hdf5"]
    r = archive.copy(items)
    assert r["copied"] == 3 and not r["errors"] and (nas / "YIG/2026/10/Data_1013/a.nc").read_bytes() == b"A" * 1000
    r = archive.copy(items)                                         # 第二次：一樣的檔跳過
    assert r["copied"] == 0 and r["skipped"] == 3
    (day / "b.nc").write_bytes(b"changed")
    r = archive.copy(archive.plan([day / "b.nc"], "YIG", nas, root))
    assert r["copied"] == 1 and (nas / "YIG/2026/10/Data_1013/b_2.nc").read_bytes() == b"changed"


def test_archive_dialog(win, tmp_path, monkeypatch):
    from labcontrol.apps.qt.archive_dialog import ArchiveDialog

    monkeypatch.setenv("LABCONTROL_ARCHIVE_ROOT", str(tmp_path / "nas"))
    (tmp_path / "nas").mkdir()
    src = tmp_path / "run"
    src.mkdir()
    (src / "x.nc").write_bytes(b"x" * 100)
    d = ArchiveDialog(win, project={"id": "3", "name": "YIG mirror"}, sources=[src])
    assert _pump(5, lambda: d.dest.text() == str(tmp_path / "nas"))
    assert d.project.currentText() == "YIG mirror" and d.b_go.isEnabled()
    d.start()
    assert _pump(5, lambda: (tmp_path / "nas" / "YIG mirror" / "run" / "x.nc").exists() and "完成" in d.where.text())
    d.close()


# ---- 6. 兩個程式連動 ----------------------------------------------------------------------------------
def test_drop_data_anywhere_applies_settings(win, tmp_path, monkeypatch):
    from PyQt6 import QtCore, QtGui, QtWidgets

    f = tmp_path / "x.nc"
    f.write_bytes(b"\x89HDF")
    got = []
    monkeypatch.setattr(win.platform, "drop", lambda md: got.append([u.toLocalFile() for u in md.urls()]))
    md = QtCore.QMimeData()
    md.setUrls([QtCore.QUrl.fromLocalFile(str(f))])
    for target in (win.files.fname, win.flow.view.viewport(), win.measure_window):
        pos = QtCore.QPointF(5, 5)
        ev = QtGui.QDropEvent(pos, QtCore.Qt.DropAction.CopyAction, md, QtCore.Qt.MouseButton.NoButton,
                              QtCore.Qt.KeyboardModifier.NoModifier)
        assert win._drop_filter.eventFilter(target, ev) is True          # 攔下來（Qt 送進來時就是這條路）
    assert got == [[str(f)]] * 3 and win.files.fname.text() != str(f)
    md2 = QtCore.QMimeData()
    md2.setUrls([QtCore.QUrl.fromLocalFile(str(tmp_path / "notes.txt"))])
    ev = QtGui.QDropEvent(QtCore.QPointF(5, 5), QtCore.Qt.DropAction.CopyAction, md2, QtCore.Qt.MouseButton.NoButton,
                          QtCore.Qt.KeyboardModifier.NoModifier)
    assert win._drop_filter.eventFilter(win.flow.view.viewport(), ev) is False
    assert len(got) == 3                                                   # 不是數據檔：不攔


def test_open_new_data_in_lablogviewer(win, tmp_path, monkeypatch):
    from PyQt6 import QtNetwork

    from labcontrol.integrations import lablogviewer as llv

    monkeypatch.setattr(llv, "server_name", lambda: "qel-lablogviewer-test0031")
    nc, h5 = tmp_path / "a.nc", tmp_path / "a.h5"
    nc.write_bytes(b"x")
    h5.write_bytes(b"x")
    assert llv.pick([h5, nc]) == nc
    launched = []
    monkeypatch.setattr(llv, "find_command", lambda: None)
    monkeypatch.setattr(llv, "enabled", lambda: True)
    assert llv.after_measure([nc], split=False) == "missing"
    assert llv.after_measure([nc], split=True) is None                     # 雙層分檔：不開
    srv = QtNetwork.QLocalServer()
    QtNetwork.QLocalServer.removeServer("qel-lablogviewer-test0031")
    assert srv.listen("qel-lablogviewer-test0031")
    msgs = []

    def accept():
        s = srv.nextPendingConnection()

        def read():
            msgs.append(bytes(s.readAll()))
            s.write(b'{"ok": true}\n')
            s.flush()
        s.readyRead.connect(read)
    srv.newConnection.connect(accept)
    res = {}
    t = threading.Thread(target=lambda: res.setdefault("r", llv.after_measure([h5, nc], split=False)))
    t.start()
    _pump(3, lambda: not t.is_alive())
    assert res.get("r") == "sent" and str(nc.resolve()) in msgs[0].decode("utf-8")
    srv.close()
    monkeypatch.setattr(llv, "find_command", lambda: ["llv-test"])
    monkeypatch.setattr(llv.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    assert llv.open_file(nc) == "started" and launched == [["llv-test", str(nc.resolve())]]
