"""1.0.31：「重複 N 次」流程方塊、Yokogawa 預設電流模式（見 test_drivers）。"""
import time

import pytest

from labcontrol.scheme import Block, Scheme, build_catalog, compile_scheme
from labcontrol.scheme.catalog import REPEAT
from tests.test_scheme import MAG_LAB, errors, mst  # noqa: F401


@pytest.fixture
def cat(mst):  # noqa: F811
    return build_catalog(mst)


def _scheme(n=3, settle=0.0, inner=False):
    meas = Block("measure", instrument="VNA1", traces=["S21"], settings={"points": 11})
    rep = Block("set", target=REPEAT, mode="sweep", start=1, stop=n, points=n, settle=settle)
    if inner:
        loop = Block("set", target="magnet_A", mode="sweep", start=50, stop=52, step=1, unit="mA", children=[meas])
        rep.children = [loop]
    else:
        rep.children = [meas]
    s = Scheme(name="rep", blocks=[rep, Block("save", formats=["netcdf"])])
    s.run["approach_rate"] = "50 mA/s"
    s.run["park"] = "none"
    return s


def test_repeat_is_in_catalog_and_compiles(cat):
    t = cat.target(REPEAT)
    assert t is not None and t.group == "repeat" and cat.targets[-1].ref == REPEAT
    res = compile_scheme(_scheme(4), cat)
    assert res.ok, errors(res)
    assert [lp.axis_name for lp in res.loops] == ["Repeat"] and res.total_points == 4
    assert res.config["sweep"][0] == {"target": "", "name": "Repeat", "values": [1, 2, 3, 4], "settle": 0.0}
    res = compile_scheme(_scheme(2, inner=True), cat)
    assert res.ok and [lp.n for lp in res.loops] == [2, 3] and res.total_points == 6
    # 存讀不變
    s2 = Scheme.from_dict(_scheme(5).to_dict())
    assert compile_scheme(s2, cat).loops[0].n == 5
    # 錯誤：0 次、當固定設定用
    bad = _scheme(1)
    bad.blocks[0].points, bad.blocks[0].stop = 0, 0
    assert any("重複次數" in m for m in errors(compile_scheme(bad, cat)))
    fixed = Scheme(blocks=[Block("set", target=REPEAT, mode="fixed", value=1),
                           Block("measure", instrument="VNA1", traces=["S21"]), Block("save")])
    assert any("迴圈" in m for m in errors(compile_scheme(fixed, cat)))


def test_repeat_runs_and_waits_between_repeats(mst, cat, tmp_path):  # noqa: F811
    res = compile_scheme(_scheme(3, settle=0.15), cat)
    exp = res.experiment(mst)
    out = exp.plan_output(root=tmp_path)
    t0 = time.monotonic()
    ds = exp.create_runner(out).run()
    dt = time.monotonic() - t0
    assert len(ds) == 3 and [r.setpoints["Repeat"] for r in ds.records] == [1, 2, 3]
    assert dt >= 0.3 - 0.02                                       # 第 2、3 次之前各等 0.15 s
    paths = exp.export(ds, out)
    import xarray as xr
    with xr.open_dataset(paths[0], engine="h5netcdf") as x:
        assert "Repeat" in x.dims and x.sizes["Repeat"] == 3


def test_repeat_dialog_and_new_block(mst, cat):  # noqa: F811
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.workbench.dialogs import RepeatDialog
    from labcontrol.apps.qt.workbench.doc import WorkbenchDoc
    from labcontrol.apps.qt.workbench.graph_view import describe

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    doc = WorkbenchDoc(Scheme(), cat)
    b = doc.new_block({"kind": "set", "target": REPEAT, "mode": "sweep"})
    assert b.is_loop and b.points == 3
    d = RepeatDialog(None, b)
    d.count.setValue(7)
    d.settle.setValue(2.5)
    nb = d.result_block()
    assert (nb.points, nb.stop, nb.settle, nb.mode) == (7, 7.0, 2.5, "sweep")
    title, sub, badge, _ = describe(doc, nb.id, nb)
    assert title == "重複" and "7 次" in sub and "2.5" in sub
