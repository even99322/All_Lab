"""1.0.7：每點重複量測＋QC 挑選視窗（移植自電磁鐵異步量測工具 v3）。"""
from __future__ import annotations

import os

import numpy as np
import pytest

from tests.test_0031 import _pump
from tests.test_102 import VS, _station


def _scheme(points=3):
    from labcontrol.scheme.model import Block, Scheme

    loop = Block("set", target="pair", mode="sweep", start=158.6, stop=158.62, points=points, unit="mA")
    loop.children = [Block("measure", instrument="VNA1", traces=["S21"], settings=dict(VS))]
    s = Scheme("qc", blocks=[loop, Block("save", file_name="qc.nc", formats=["netcdf", "hdf5"])])
    s.run["repeat_shots"] = 4
    return s


def _dataset(st, root):
    from labcontrol.scheme import build_catalog
    from labcontrol.scheme.compile import compile_scheme

    r = compile_scheme(_scheme(), build_catalog(st))
    assert r.ok, [i.message for i in r.issues]
    exp = r.experiment(st)
    out = exp.plan_output(root=str(root))
    ds = exp.create_runner(out).run()
    return exp, out, ds


def test_repeat_shots_keeps_every_shot(tmp_path):
    from labcontrol.measure.runner import RunOptions

    assert RunOptions.from_config({"repeat_shots": "3"}).repeat_shots == 3
    assert RunOptions.from_config({"repeat_shots": 0}).repeat_shots == 1
    st = _station()
    try:
        exp, out, ds = _dataset(st, tmp_path)
        assert len(ds) == 3 and all(r.retained and len(r.shots) == 4 and r.selected == 3 for r in ds.records)
        assert exp.needs_review(ds)
        ds.records[1].select(0)                                   # QC 挑了第 1 筆
        paths = exp.export(ds, out)
        import xarray as xr

        nc = next(p for p in paths if p.suffix == ".nc")
        x = xr.open_dataset(nc)
        got = np.asarray(x["S21"].values)
        want = np.asarray(ds.records[1].shots[0]["S21"])
        assert np.allclose(got.reshape(3, -1)[1], want) and bool(x["retained"].values.all())
    finally:
        st.close()


@pytest.fixture
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets

    if os.environ.get("QT_QPA_PLATFORM") != "offscreen" and not os.environ.get("DISPLAY") and os.name != "nt":
        pytest.skip("需要 Qt 顯示環境")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_qc_window_selects_stars_and_applies(qapp, tmp_path):
    from labcontrol.apps.qt.workbench.qc_window import QCWindow, point_label

    st = _station()
    try:
        _exp, _out, ds = _dataset(st, tmp_path)
    finally:
        st.close()
    w = QCWindow(ds, final=True)
    assert w.points.count() == 3 and w.traces.count() == 4
    assert "pair" in point_label(ds, ds.records[0]) or "#1" in point_label(ds, ds.records[0])
    # 選第二點的 Tr 002、打星號
    w.points.setCurrentRow(1)
    row = w.mapping.index(1)
    w.traces.setCurrentRow(row)
    w.toggle_star()
    assert 1 in w.stars[id(ds.records[1])] and w.traces.item(0).text().startswith("⭐")       # 置頂
    x, y = w.curve_mag.getData()
    assert len(x) == 11 and np.isfinite(y).all()
    assert len(w.scatter.data) == 11
    w.use_last_everywhere()
    assert all(w.selection[id(r)] == 3 for r in ds.records)
    w.pick_best_everywhere()
    assert w.selection[id(ds.records[1])] == 1                    # ⭐ 優先
    got = []
    w.done.connect(got.append)
    w.finish()
    assert got == [ds] and ds.records[1].selected == 1 and ds.records[1].notes["qc_star"] == [1]


def test_closing_the_window_still_saves(qapp, tmp_path):
    from labcontrol.apps.qt.workbench.qc_window import QCWindow

    st = _station()
    try:
        _exp, _out, ds = _dataset(st, tmp_path)
    finally:
        st.close()
    w = QCWindow(ds, final=True)
    got = []
    w.done.connect(got.append)
    w.show()
    w.close()
    assert got == [ds]


def test_monitor_opens_qc_before_export(qapp, tmp_path, monkeypatch):
    from labcontrol.apps.qt.workbench import monitor_panel
    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.core.station import Station
    from tests.conftest import SIM_LAB

    st = Station(SIM_LAB)
    st.sim_time_scale = 0.0
    win = LabControlWindow(st, None, root=str(tmp_path / "data"))
    try:
        mon = win.measure_window.findChild(monitor_panel.MonitorPanel) if hasattr(win, "measure_window") else None
        mon = mon or win.findChild(monitor_panel.MonitorPanel)
        assert mon is not None
        exp, out, ds = _dataset(st, tmp_path)
        exported = []
        monkeypatch.setattr(mon, "_export", lambda e, d, o: exported.append(d))
        monkeypatch.setattr(monitor_panel, "start_thread", lambda fn, name="": fn())
        mon.exp, mon.output, mon._own_run_id = exp, out, "run-x"
        mon._on_finished({"dataset": ds, "status": "finished", "run_id": "run-x"})
        assert not exported and mon._qc is not None and mon._qc.final
        mon._qc.finish()
        assert exported == [ds]
    finally:
        win.doc.dirty = False
        win.close()
        st.close()


# ---- Hub 網頁：登入、共用導覽、量測設置（節點指令）--------------------------------------------
from tests.test_remote import TOKEN, hubsrv  # noqa: E402,F401
from tests.test_instruments_remote import NET_LAB  # noqa: E402


def test_node_commands_for_the_setup_page(hubsrv, tmp_path, monkeypatch):
    from labcontrol import Station
    from labcontrol.remote import NodeService, RemoteNode

    hub = hubsrv[0]
    cfg = tmp_path / "instruments.yaml"
    import yaml

    cfg.write_text(yaml.safe_dump(NET_LAB, allow_unicode=True), encoding="utf-8")
    st = Station.from_file(cfg)
    assert st.config_path is not None and st.simulate
    n = NodeService(st, hub, name="QEL-PC").start()
    try:
        rn = RemoteNode(hub, "QEL-PC")
        cat = rn.call("catalog", timeout=20)
        assert any(t["ref"] == "pair" for t in cat["targets"]) and cat["measurers"][0]["ref"] == "VNA1"
        assert any(f["key"] == "start_freq" for f in cat["measurers"][0]["fields"])
        tm = rn.call("templates", timeout=20)
        two = next(t for t in tm if t["key"] == "02_2d")
        assert two["outline"][0]["kind"] == "set" and two["scheme"]["background"]["enabled"]
        chk = rn.call("check_scheme", scheme=_scheme().to_dict(), timeout=20)
        assert chk["ok"] and chk["points"] == 3 and [o["kind"] for o in chk["outline"]] == ["set", "measure", "save"]
        bad = rn.call("check_scheme", scheme=two["scheme"], timeout=20)
        assert not bad["ok"] and bad["issues"]                         # 範本的 magnet_A 這台沒有
        f = rn.call("instruments_file", timeout=20)
        assert "VNA1" in f["text"] and "VNA1" in f["loaded"]
        # 裝置 token 不能自稱站長 → 改設定檔被拒
        try:
            rn.call("save_instruments", text=f["text"], timeout=20)
            raise AssertionError("should refuse")
        except Exception as e:  # noqa: BLE001
            assert "站長" in str(e)
        # 站長（網頁登入）→ 存檔、留 .bak
        new = f["text"].replace("labber_name: VNA", "labber_name: VNA-X")
        assert n.cmd_save_instruments({"user": "boss", "owner": True}, new)["saved"]
        assert "VNA-X" in cfg.read_text(encoding="utf-8") and cfg.with_suffix(".yaml.bak").is_file()
        try:
            n.cmd_save_instruments({"user": "boss", "owner": True}, "instruments: [1, 2")
            raise AssertionError("should refuse")
        except Exception as e:  # noqa: BLE001
            assert "YAML" in str(e)
        res = n.cmd_save_lab_nodes({"user": "boss", "owner": True},
                                   [{"node": "LAB-1", "magnet": ["pair"], "vna": ["VNA1"]}, {"node": ""}])
        assert res["lab_nodes"] == [{"node": "LAB-1", "magnet": ["pair"], "vna": ["VNA1"]}]
    finally:
        n.stop()
        st.close()


def test_web_pages_need_login_and_share_the_nav(hubsrv):
    import urllib.error
    import urllib.request

    from tests.test_104 import _get

    state = hubsrv[1]
    base = hubsrv[0].url if hasattr(hubsrv[0], "url") else hubsrv[0].base

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)

    def location(path, cookie=None):
        req = urllib.request.Request(base + path, headers={"Cookie": cookie} if cookie else {})
        try:
            opener.open(req, timeout=5)
            return None
        except urllib.error.HTTPError as e:
            return e.headers.get("Location")

    assert location("/").startswith("/login")                      # 一律先登入
    assert location("/get").startswith("/login?next=/get")
    assert location("/data").startswith("/login") and location("/setup").startswith("/login")
    assert _get(base + "/get/info")[0] == 200                       # 程式用的照舊不用登入
    cookie = f"labhub_token={TOKEN}"
    assert location("/", cookie) == "/portal" and location("/nodes", cookie) == "/setup"
    for path, key in (("/portal", "home"), ("/data", "data"), ("/live", "live"), ("/setup", "setup"),
                      ("/m", "live"), ("/m/data", "data")):
        code, body, _ = _get(base + path, {"Cookie": cookie})
        text = body.decode("utf-8")
        assert code == 200 and 'class="qel-nav"' in text and "<!--QEL-NAV-->" not in text, path
        assert f'class="i on" data-v="{key}"' in text, path
        for label in ("模塊下載", "數據查看", "量測查看", "量測設置"):
            assert label in text
        assert "控制台" not in text.split("qel-nav")[1].split("</div>")[0]   # 裝置 token 不是站長
    code, body, _ = _get(base + "/data", {"Cookie": cookie})
    assert b"lablogviewer://open?share=" in body
    assert state is not None
