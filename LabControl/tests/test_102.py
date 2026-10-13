"""1.0.2：分檔依外圈「第幾次」（重複掃描 → p1、p2）、Data 方塊命名規則、背景量測＋去背（*_debg）、
2D 範本換成背景＋去背、熱圖重新打開時補回、即時 VNA 最低點置中。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import SIM_LAB
from tests.test_0031 import _pump

VS = {"start_freq": "5.0198 GHz", "stop_freq": "5.0298 GHz", "points": 11}


def _station():
    from labcontrol import Station

    st = Station(SIM_LAB)
    st.sim_time_scale = 0.0
    return st


def _run(scheme, st, root):
    from labcontrol.scheme import build_catalog
    from labcontrol.scheme.compile import compile_scheme

    r = compile_scheme(scheme, build_catalog(st))
    assert r.ok, [i.message for i in r.issues]
    exp = r.experiment(st)
    out = exp.plan_output(root=str(root))
    ds = exp.create_runner(out).run()
    return r, exp, out, ds, exp.export(ds, out)


def _split_scheme(outer_kw, save_kw=None, tail=()):
    from labcontrol.scheme.model import Block, Scheme

    outer = Block("set", target="pair", mode="sweep", unit="mA", **outer_kw)
    inner = Block("set", target="DC5.ch1", mode="sweep", start=0, stop=2, step=1, unit="mA")
    inner.children = [Block("measure", instrument="VNA1", traces=["S21"], settings=dict(VS))]
    outer.children = [inner, Block("save", file_name="data.nc", formats=["netcdf"], **(save_kw or {}))]
    return Scheme("t102", blocks=[outer, *tail])


def test_format_value_and_labels():
    from labcontrol.data.dataset import AxisSpec
    from labcontrol.measure.segments import format_value, segment_label

    assert format_value(100.35) == "100350" and format_value(100.35, "plain") == "100.35"
    assert format_value(100.35, "p") == "100p35" and format_value(-1.5) == "m1500"
    a = AxisSpec("I", "pair.level", "A", np.array([0.10035, 0.10036]), "mA", 1e3)
    assert segment_label([a], ["I"], (1,), by_index=True, style="nodot") == (1, "100360mA")
    rep = AxisSpec("I", "pair.level", "A", np.full(3, 0.1474), "mA", 1e3)
    assert segment_label([rep], ["I"], (2,), by_index=True) == (2, "p3")


def test_repeat_loop_splits_per_iteration(tmp_path):
    st = _station()
    try:
        _r, _e, _o, _ds, paths = _run(_split_scheme(dict(start=158.6, stop=158.6, points=3)), st, tmp_path)
        assert sorted(p.name for p in paths) == ["data_p1.nc", "data_p2.nc", "data_p3.nc"]
        import xarray as xr

        assert all(dict(xr.open_dataset(p).sizes)["DC5_ch1"] == 3 for p in paths)
    finally:
        st.close()


def test_value_loop_names_and_custom_rule(tmp_path):
    st = _station()
    try:
        _r, _e, _o, _ds, paths = _run(_split_scheme(dict(start=158.6, stop=158.61, step=0.01)), st, tmp_path / "a")
        assert sorted(p.name for p in paths) == ["data_158600mA.nc", "data_158610mA.nc"]
        s = _split_scheme(dict(start=158.6, stop=158.61, step=0.01),
                          save_kw={"name_pattern": "{stem}_{n:02d}_{label}", "label_style": "plain"})
        _r, _e, _o, _ds, paths = _run(s, st, tmp_path / "b")
        assert sorted(p.name for p in paths) == ["data_01_158.6mA.nc", "data_02_158.61mA.nc"]
    finally:
        st.close()


def test_background_stage_and_debackground(tmp_path):
    from labcontrol.scheme.model import Block

    tail = (Block("set", target="pair", value=158.7, unit="mA"),
            Block("measure", instrument="VNA1", traces=["S21"], settings=dict(VS)),
            Block("save", formats=["netcdf"]), Block("debg"))
    st = _station()
    try:
        r, exp, out, ds, paths = _run(_split_scheme(dict(start=158.6, stop=158.61, step=0.01), tail=tail), st,
                                      tmp_path)
        assert r.n_files == 5 and "背景量測" in r.est_breakdown
        names = sorted(p.name for p in paths)
        assert names == ["data_158600mA.nc", "data_158600mA_debg.nc", "data_158610mA.nc", "data_158610mA_debg.nc",
                         "data_bg.nc"]
        import xarray as xr

        raw = xr.open_dataset(out.folder / "data_158600mA.nc")
        deb = xr.open_dataset(out.folder / "data_158600mA_debg.nc")
        bg = xr.open_dataset(out.folder / "data_bg.nc")

        def cplx(d):
            v = d["S21"]
            return (v.values if np.iscomplexobj(v.values) else v.values).astype(complex)
        assert np.allclose(cplx(deb), cplx(raw) / cplx(bg).reshape(-1)[None, :], rtol=1e-5)
    finally:
        st.close()


def test_stopped_run_does_not_measure_background(tmp_path):
    from labcontrol.measure.experiment import Experiment
    from labcontrol.scheme import build_catalog
    from labcontrol.scheme.compile import compile_scheme
    from labcontrol.scheme.model import Block

    tail = (Block("set", target="pair", value=158.7, unit="mA"),
            Block("measure", instrument="VNA1", traces=["S21"], settings=dict(VS)), Block("save"), Block("debg"))
    st = _station()
    try:
        r = compile_scheme(_split_scheme(dict(start=158.6, stop=158.61, step=0.01), tail=tail), build_catalog(st))
        exp = Experiment(st, r.config)
        out = exp.plan_output(root=str(tmp_path))
        ds = exp.create_runner(out).run()
        del ds.records[3:]                                    # 像中途停止
        names = sorted(p.name for p in exp.export(ds, out))
        assert not any("_debg" in n or "_bg" in n for n in names)
    finally:
        st.close()


def test_compile_errors_for_debg():
    from labcontrol.scheme import build_catalog
    from labcontrol.scheme.compile import compile_scheme
    from labcontrol.scheme.model import Block

    st = _station()
    try:
        cat = build_catalog(st)
        r = compile_scheme(_split_scheme(dict(start=158.6, stop=158.61, step=0.01), tail=(Block("debg"),)), cat)
        assert not r.ok and any("去背需要背景" in i.message for i in r.issues)
        loop = Block("set", target="DC5.ch1", mode="sweep", start=0, stop=1, step=1, unit="mA",
                     children=[Block("measure", instrument="VNA1", traces=["S21"], settings=dict(VS))])
        r = compile_scheme(_split_scheme(dict(start=158.6, stop=158.61, step=0.01), tail=(loop, Block("save"))), cat)
        assert not r.ok and any("不能再有迴圈" in i.message for i in r.issues)
    finally:
        st.close()


def test_debackground_interpolates_background():
    from labcontrol.analysis.debackground import debackground_dataset
    from labcontrol.data.dataset import AxisSpec, ChannelSpec, Dataset, PointRecord

    f = np.linspace(1.0, 2.0, 11)
    ch = ChannelSpec("S21", vector=True, complex=True, x_values=f)
    main = Dataset("m", [AxisSpec("I", "x", "A", np.array([0.0]))], [ch])
    main.add(PointRecord(0, {"I": 0.0}, [{"S21": np.full(11, 2 + 0j)}], 0))
    fb = np.linspace(1.0, 2.0, 21)
    bg = Dataset("b", [AxisSpec("Repeat", "", "", np.array([0.0]))],
                 [ChannelSpec("S21", vector=True, complex=True, x_values=fb)])
    bg.add(PointRecord(0, {"Repeat": 0.0}, [{"S21": fb + 0j}], 0))
    out = debackground_dataset(main, bg)
    assert np.allclose(out.records[0].data["S21"], 2 / f)
    assert out.metadata["debackground"]["channels"] == ["S21"]


def test_2d_template_has_background_and_debg(tmp_path):
    from labcontrol import paths
    from labcontrol.scheme.compile import split_stages
    from labcontrol.scheme.templates import load_template

    s = load_template("02_2d")
    main, stages, debg = split_stages(s)
    assert s.template_rev >= 2 and len(stages) == 1 and debg is not None
    assert [b.kind for b in stages[0]] == ["set", "measure", "save"]
    # LAB/templates 裡比較舊的 2D → 換成新的（舊的留在 舊範本_rev0/）
    home = tmp_path / "LAB"
    (home / "templates").mkdir(parents=True)
    (home / "templates" / "02_2d.scheme.yaml").write_text("scheme: 1\nname: 2D\nblocks: []\n", encoding="utf-8")
    paths._upgrade_templates(home)
    assert "template_rev: 2" in (home / "templates" / "02_2d.scheme.yaml").read_text(encoding="utf-8")
    assert (home / "templates" / "舊範本_rev0" / "02_2d.scheme.yaml").is_file()


@pytest.fixture
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets

    if os.environ.get("QT_QPA_PLATFORM") != "offscreen" and not os.environ.get("DISPLAY") and os.name != "nt":
        pytest.skip("需要 Qt 顯示環境")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_data_panel_naming(qapp, tmp_path):
    from labcontrol.apps.qt.workbench import LabControlWindow

    st = _station()
    try:
        w = LabControlWindow(st, None, root=str(tmp_path / "data"))
        s = _split_scheme(dict(start=158.6, stop=158.62, step=0.01))
        s.output["file_name"] = "data.nc"
        w.doc.replace_scheme(s)
        save = next(b for b in w.doc.scheme.flat() if b.kind == "save")
        w.doc.select(save.id)
        _pump(0.1)
        panel = w.files.data_panel
        assert w.files.currentWidget() is panel and panel.save_box.isHidden() is False
        text = panel.preview.toPlainText()
        assert "data_158600mA.nc" in text and "data_158610mA.nc" in text
        panel.style.setCurrentIndex(panel.style.findData("plain"))
        assert next(b for b in w.doc.scheme.flat() if b.kind == "save").label_style == "plain"
        assert "data_158.61mA.nc" in panel.preview.toPlainText()
        panel.pattern.setText("{stem}_{n}")
        panel.pattern.editingFinished.emit()
        assert "data_2.nc" in panel.preview.toPlainText()
        w.doc.dirty = False
        w.close()
    finally:
        st.close()


def test_monitor_refills_heatmap(qapp, tmp_path):
    from labcontrol.apps.qt.workbench.monitor_panel import MonitorPanel
    from labcontrol.scheme import build_catalog
    from labcontrol.scheme.compile import compile_scheme

    st = _station()
    try:
        r = compile_scheme(_split_scheme(dict(start=158.6, stop=158.61, step=0.01)), build_catalog(st))
        exp = r.experiment(st)
        panel = MonitorPanel(st)
        panel.show()
        panel.run(exp, exp.plan_output(root=str(tmp_path)))
        assert _pump(20, lambda: not panel.busy)
        _pump(0.3)
        panel.heat[:] = np.nan                              # 像關掉時沒畫到的點
        panel.b_img.setChecked(False)
        panel.b_img.setChecked(True)
        assert np.isfinite(panel.heat).all()
        panel.close()
    finally:
        st.close()


def test_vna_minimum_to_center(qapp, monkeypatch):
    from labcontrol.apps.qt.live import vna_panel

    calls = []

    class Fake:
        def set(self, ref, v):
            calls.append((ref, v))
            return v

    panel = vna_panel.VNAPanel.__new__(vna_panel.VNAPanel)
    x = np.linspace(5.0e9, 5.01e9, 101)
    z = 1 - 0.9 / (1 + ((x - 5.0072e9) / 2e5) ** 2)
    panel.last = ("S21", x, z.astype(complex), "Hz")
    assert abs(panel.minimum_frequency() - 5.0072e9) < 1.1e5
    msgs = []
    panel.backend, panel.busy = Fake(), False
    panel.params = {"center_freq": {"ref": "VNA1.center_freq"}, "span": {"ref": "VNA1.span"}}
    panel.message = type("S", (), {"emit": staticmethod(msgs.append)})()
    panel.read_params = lambda: None
    panel.read_trace = lambda: None
    panel.cont = type("C", (), {"isChecked": staticmethod(lambda: True)})()
    monkeypatch.setattr(vna_panel, "run_bg", lambda work, ok, fail: ok(work()))
    panel.center_on_minimum()
    assert calls[0][0] == "VNA1.center_freq" and abs(calls[0][1] - 5.0072e9) < 1.1e5
    assert calls[1] == ("VNA1.span", pytest.approx(1e7)) and "最低點" in msgs[-1]
