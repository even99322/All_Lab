"""1.0.6：工具列換行、背景／去背開關、即時數據圖表排版、手機／平板看數據。"""
from __future__ import annotations

import os

import pytest

from tests.test_0031 import _pump


@pytest.fixture
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets

    if os.environ.get("QT_QPA_PLATFORM") != "offscreen" and not os.environ.get("DISPLAY") and os.name != "nt":
        pytest.skip("需要 Qt 顯示環境")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(qapp, tmp_path):
    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.core.station import Station
    from tests.conftest import SIM_LAB

    st = Station(SIM_LAB)
    w = LabControlWindow(st, None, root=str(tmp_path / "data"))
    yield w
    w.doc.dirty = False
    w.close()
    st.close()


def test_toolbar_wraps_instead_of_hiding_the_right_side(window):
    w = window
    names = [tb.objectName() for tb in w._toolbars]
    assert names == ["toolbar_windows", "toolbar_scheme", "toolbar_run", "toolbar_app"]
    w.show()
    w.resize(2600, 700)
    _pump(0.1)
    assert not any(w.toolBarBreak(tb) for tb in w._toolbars)
    w.resize(900, 700)
    _pump(0.1)
    assert any(w.toolBarBreak(tb) for tb in w._toolbars[1:])
    for tb in w._toolbars:                                   # every group fits on its own row: nothing in »
        assert tb.geometry().right() <= w.width()
    app = w._toolbars[-1]
    assert app.isVisible() and app.geometry().width() > 0
    w.resize(2600, 700)
    _pump(0.1)
    assert not any(w.toolBarBreak(tb) for tb in w._toolbars)


def _bg_scheme(st):
    from labcontrol.scheme import background as bg, build_catalog
    from labcontrol.scheme.templates import load_template

    cat = build_catalog(st)
    s = load_template("02_2d")
    src = bg.candidates(cat)[0].ref
    for b in s.flat():
        if b.kind == "set":
            b.target = src
    s.graph = None
    s.background["sets"] = [{"target": src, "value": 50, "unit": "mA"}]
    s.ensure_graph()
    return s, cat


def test_background_switch_expands_to_background_and_debg(tmp_path):
    from labcontrol.core.station import Station
    from labcontrol.measure.experiment import Experiment
    from labcontrol.scheme import background as bg
    from labcontrol.scheme.compile import compile_scheme
    from tests.conftest import SIM_LAB

    st = Station(SIM_LAB)
    try:
        s, cat = _bg_scheme(st)
        for b in s.flat():                                    # 小一點，跑得快
            if b.is_loop:
                b.start, b.stop, b.step = 100, 100.5, 0.5
        r = compile_scheme(s, cat)
        assert r.ok, [i.message for i in r.issues]
        assert r.config["debg"]["suffix"] == "_debg" and len(r.post) == 1
        post = r.post[0].config
        assert post["output"]["file_name"] == "2D_bg.nc"
        assert post["setup"]["VNA1"] == r.config["setup"]["VNA1"]            # VNA 設定與主量測相同
        assert abs(post["setup"][f"{bg.config(s)['sets'][0]['target']}.level"] - 0.05) < 1e-12
        exp = Experiment(st, r.config)
        out = exp.plan_output(root=str(tmp_path))
        ds = exp.create_runner(out).run()
        names = sorted(p.name for p in exp.export(ds, out))
        assert names == ["2D.nc", "2D_bg.nc", "2D_debg.nc"]
        # 沒有勾選電源 → 錯誤指向「背景＋去背」方塊
        s.background["sets"] = []
        r = compile_scheme(s, cat)
        assert not r.ok and any(i.block_id == bg.BG_ID and "勾選" in i.message for i in r.issues)
    finally:
        st.close()


def test_more_than_four_sources_and_conflict_with_explicit_nodes():
    from labcontrol.core.station import Station
    from labcontrol.scheme import background as bg
    from labcontrol.scheme.compile import compile_scheme
    from labcontrol.scheme.model import Block
    from tests.conftest import SIM_LAB

    st = Station(SIM_LAB)
    try:
        s, cat = _bg_scheme(st)
        refs = [t.ref for t in bg.candidates(cat)]
        assert len(refs) >= 5
        s.background["sets"] = [{"target": r, "value": 1.5 * k, "unit": "mA"} for k, r in enumerate(refs)]
        r = compile_scheme(s, cat)
        assert r.ok, [i.message for i in r.issues]
        setup = r.post[0].config["setup"]
        assert all(f"{ref}.level" in setup for ref in refs if "." not in ref)
        # 流程圖裡又放了去背節點 → 二擇一
        s.graph.add(Block("debg"), (0, 900))
        tail = s.graph.chain_tail("start")
        s.graph.append_after(tail, list(s.graph.nodes)[-1])
        s.sync_from_graph()
        r = compile_scheme(s, cat)
        assert any(i.block_id == bg.BG_ID and "開關" in i.message for i in r.issues) and not r.ok
    finally:
        st.close()


def test_old_explicit_background_nodes_are_folded_into_the_switch():
    from labcontrol.core.station import Station
    from labcontrol.scheme import background as bg, build_catalog
    from labcontrol.scheme.compile import compile_scheme
    from labcontrol.scheme.model import Block, Scheme
    from tests.conftest import SIM_LAB

    st = Station(SIM_LAB)
    try:
        cat = build_catalog(st)
        src = bg.candidates(cat)[0].ref
        s = Scheme(name="d", blocks=[
            Block("set", target=src, mode="sweep", start=0, stop=1, step=1, unit="mA",
                  children=[Block("measure", instrument="VNA1", traces=["S21"])]),
            Block("save", file_name="d.nc"),
            Block("set", target=src, value=7, unit="mA", settle=0.3),
            Block("measure", instrument="VNA1", traces=["S21"]),
            Block("save", file_name="mybg.nc"), Block("debg", suffix="_x")])
        s.ensure_graph()
        before = compile_scheme(s, cat)
        assert bg.adopt_explicit(s)
        assert [b.kind for b in s.blocks] == ["set", "save"]
        c = bg.config(s)
        assert c["enabled"] and c["sets"] == [{"target": src, "value": 7, "unit": "mA"}]
        assert c["settle"] == 0.3 and c["file_name"] == "mybg.nc" and c["suffix"] == "_x"
        after = compile_scheme(s, cat)
        assert after.ok and after.post[0].config["setup"] == before.post[0].config["setup"]
        assert after.config["debg"] == before.config["debg"]
        assert len(s.to_dict()["graph"]["nodes"]) == 3                 # 迴圈、量測、Data
        assert bg.config(type(s).from_dict(s.to_dict())) == c            # 存檔再讀回來一樣
    finally:
        st.close()


def test_switch_and_panel_in_the_window(window):
    from PyQt6 import QtCore

    from labcontrol.scheme import background as bg

    w = window
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.workbench.graph_view import FlowView

    toggle = w.findChild(QtWidgets.QToolButton, "backgroundToggle")
    assert toggle is not None
    doc = w.doc
    toggle.setChecked(False)
    _pump(0.05)
    assert not bg.enabled(doc.scheme)
    toggle.setChecked(True)
    _pump(0.05)
    assert bg.enabled(doc.scheme) and doc.selected == bg.BG_ID
    panel = w.files.data_panel
    assert panel.bg_box.isVisibleTo(panel) and panel.bg_table.rowCount() >= len(bg.candidates(doc.catalog))
    # 勾第二台電源、設電流
    item = panel.bg_table.item(1, 0)
    ref = item.data(QtCore.Qt.ItemDataRole.UserRole)
    item.setCheckState(QtCore.Qt.CheckState.Checked)
    _pump(0.05)
    assert any(st["target"] == ref for st in bg.config(doc.scheme)["sets"])
    sp = panel._bg_spins[ref]
    sp.setValue(12.5)
    sp.editingFinished.emit()
    _pump(0.05)
    assert next(st for st in bg.config(doc.scheme)["sets"] if st["target"] == ref)["value"] == 12.5
    assert doc.selected == bg.BG_ID and panel.bg_box.isVisibleTo(panel)
    view = w.findChildren(FlowView)[0]
    assert view.bg_item is not None and "12.5" in view.bg_item.sub
    toggle.setChecked(False)
    _pump(0.05)
    assert not bg.enabled(doc.scheme) and view.bg_item is None


def test_diagrams_overlay_and_side_by_side_together(window):
    from PyQt6 import QtCore

    from labcontrol.apps.qt.live.vna_panel import DROP_AFTER, DROP_BEFORE, DROP_CENTER, _UIState, normalize_groups

    assert normalize_groups([["db", "phase", "lin", "iq"], ["db", "re"]]) == [["db", "phase"], ["lin"], ["iq"], ["re"]]
    w = window
    w.popout_live()
    vna = w.live.vna
    vna.set_compact(False)
    sp = vna.trace()
    K = lambda f: f"{sp}:{f}"  # noqa: E731
    vna.overlay.setChecked(False)
    vna.set_views(["db", "phase", "lin"])
    p = vna.plots
    assert p.groups == [[K("db")], [K("phase")], [K("lin")]]
    msgs = []
    vna.message.connect(msgs.append)
    # trace 名稱拖到 dB 圖中間 → 疊在一起；其他圖照樣並排
    assert p.move_trace(K("phase"), K("db"), DROP_CENTER)
    assert p.groups == [[K("db"), K("phase")], [K("lin")]] and vna.groups == p.groups
    cv = p.cell_for(K("phase")).canvas
    assert cv.series == [(K("db"), "L"), (K("phase"), "R")]
    # 整張圖拖到另一張的左邊 / 上面 → 放在它前面
    both = f"{K('db')}+{K('phase')}"
    assert p.move_cell(K("lin"), both, DROP_BEFORE)
    assert p.groups == [[K("lin")], [K("db"), K("phase")]]
    grid = p.grid
    assert grid.getItemPosition(grid.indexOf(p.cells[K("lin")]))[:2] == (0, 0)
    # 第三種單位疊不上去；Polar 只能跟 Polar / Smith 疊
    assert not p.move_cell(K("lin"), both, DROP_CENTER) and "兩種單位" in msgs[-1]
    p.add_trace(sp, "iq")
    assert [K("iq")] in p.groups and not p.move_trace(K("iq"), K("lin"), DROP_CENTER)
    p.add_trace(sp, "smith")                                    # Smith 疊在 Polar 那張
    assert [K("iq"), K("smith")] in p.groups and p.cell_for(K("smith")).canvas.smith_items
    p.remove_trace(K("smith"))
    # 一條 trace 拉出來放旁邊、右鍵分開
    assert p.move_trace(K("db"), K("lin"), DROP_AFTER)
    assert p.groups[:3] == [[K("lin")], [K("db")], [K("phase")]]
    p.move_trace(K("phase"), K("db"), DROP_CENTER)
    p.split_out(K("phase"))
    assert [K("phase")] in p.groups and [K("db")] in p.groups
    # 版面：上下 / 左右
    vna.layout_box.setCurrentIndex(vna.layout_box.findData("cols"))
    cols = [grid.getItemPosition(grid.indexOf(p.cell_for(g[0])))[:2] for g in p.groups]
    assert cols == [(0, i) for i in range(len(p.groups))]
    vna.layout_box.setCurrentIndex(vna.layout_box.findData("rows"))
    rows = [grid.getItemPosition(grid.indexOf(p.cell_for(g[0])))[:2] for g in p.groups]
    assert rows == [(i, 0) for i in range(len(p.groups))]
    # 分組與版面記在 live_ui.json，下次打開一樣
    saved = _UIState().load()
    assert saved["tgroups"] == p.groups and saved["layout"] == "rows"
    # 「全部疊圖」：能疊的疊在一張，第三種單位與 Polar 另開
    vna.overlay.setChecked(True)
    assert len(p.groups) == 3 and [K("iq")] in p.groups and len(p.groups[0]) == 2
    vna.overlay.setChecked(False)
    assert all(len(g) == 1 for g in p.groups)
    # 拖放：放在圖的中間 = 疊圖區，邊緣 = 放旁邊
    cell = p.cells[K("db")]
    cell.resize(400, 300)
    assert cell.zone_at(QtCore.QPoint(200, 150))[0] == DROP_CENTER
    assert cell.zone_at(QtCore.QPoint(10, 150)) == (DROP_BEFORE, "left")
    assert cell.zone_at(QtCore.QPoint(200, 290)) == (DROP_AFTER, "bottom")
    vna.layout_box.setCurrentIndex(vna.layout_box.findData("auto"))       # live_ui.json 是共用的：還原
    vna.set_views(["db"])
    w.live_window.close()


# ---- 手機 / 平板看數據（Hub /m/data）----------------------------------------------------
from tests.test_104 import _get, _hub, _share, llvcore, nc_files  # noqa: E402,F401


def test_npz_without_numpy_matches_numpy():
    import io

    import numpy as np

    from labhub.data_routes import read_npz

    buf = io.BytesIO()
    meta = np.frombuffer('{"format": 1, "kind": "2d", "x_name": "電流"}'.encode(), dtype=np.uint8)
    z = np.arange(12, dtype=np.float32).reshape(3, 4)
    z[1, 2] = np.nan
    np.savez_compressed(buf, x=np.linspace(0, 1, 4), y=np.array([1.0, 2, 3], dtype=">f8"), z=z, meta=meta)
    m, arrays = read_npz(buf.getvalue())
    assert m["x_name"] == "電流"
    assert arrays["z"][1] == (3, 4) and list(arrays["y"][2]) == [1.0, 2.0, 3.0]          # big-endian 也讀得懂
    got = np.array(arrays["z"][2], dtype=np.float32).reshape(3, 4)
    assert np.array_equal(np.isnan(got), np.isnan(z)) and np.allclose(got[~np.isnan(z)], z[~np.isnan(z)])


def test_phone_data_browser_api(tmp_path, nc_files, llvcore):
    import base64
    import json
    import urllib.error
    import urllib.request

    import numpy as np

    from labhub import indexer
    from labhub.index_codec import decode_arrays

    share, data = _share(tmp_path, nc_files), tmp_path / "data"
    indexer.ensure_key(data, share / ".qel")
    indexer.Indexer(share, data).pass_once()
    srv, _state, base = _hub(data)
    auth = {"Cookie": "labhub_token=tok"}
    try:
        assert _get(base + "/api/data/list")[0] == 401
        code, body, _ = _get(base + "/api/data/list", auth)
        d = json.loads(body)
        assert code == 200 and [f["name"] for f in d["folders"]] == ["proj"] and d["folders"][0]["files"] == 2
        d = json.loads(_get(base + "/api/data/list?dir=proj", auth)[1])
        assert [f["name"] for f in d["folders"]] == ["Data_1014"] and not d["files"]
        d = json.loads(_get(base + "/api/data/list?dir=proj/Data_1014", auth)[1])
        names = sorted(f["name"] for f in d["files"])
        assert names == sorted(f.name for f in nc_files) and all(f["preview"] for f in d["files"])
        assert _get(base + "/api/data/list?dir=nope", auth)[0] == 404
        assert _get(base + "/api/data/list?dir=../x", auth)[0] == 400
        d = json.loads(_get(base + "/api/data/search?q=158600", auth)[1])
        assert [f["name"] for f in d["files"]] == [nc_files[0].name]
        assert _get(base + "/api/data/search?q=", auth)[0] == 400
        rel = f"proj/Data_1014/{nc_files[0].name}"
        code, body, _ = _get(base + "/api/data/preview?path=" + urllib.request.quote(rel), auth)
        p = json.loads(body)["preview"]
        meta, arrays = decode_arrays(indexer.preview_path(rel, data).read_bytes())
        assert code == 200 and p["kind"] == meta["kind"] and p["x_name"] == meta["x_name"]
        assert np.allclose(np.array(p["x"], dtype=float), arrays["x"], equal_nan=True)
        if p["kind"] == "2d":
            zz = np.frombuffer(base64.b64decode(p["z"]), dtype="<f4").reshape(p["ny"], p["nx"])
            assert np.allclose(zz, arrays["z"], equal_nan=True)
        else:
            assert np.allclose(np.array(p["y"], dtype=float), arrays["y"], equal_nan=True)
        assert isinstance(p["metadata"], list) and p["metadata"]
        assert _get(base + "/api/data/preview?path=proj/none.nc", auth)[0] == 404

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        try:
            urllib.request.build_opener(NoRedirect).open(base + "/m/data", timeout=5)
            raise AssertionError("should redirect")
        except urllib.error.HTTPError as e:
            assert e.code == 303 and e.headers["Location"].startswith("/login?next=/m/data")
        code, body, _ = _get(base + "/m/data", auth)
        assert code == 200 and b"/api/data/list" in body and b"apple-touch-icon" in body
        assert b'href="/data"' in _get(base + "/m", auth)[1]                        # 量測查看頁有連過去
    finally:
        srv.shutdown()
