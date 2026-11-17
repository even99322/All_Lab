"""1.0.11：同一次掃描量多個 S 參數（S11 + S31）共用 Frequency：X 只列一個 Frequency，每個通道都能畫。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

xr = pytest.importorskip("xarray")
pytest.importorskip("h5netcdf")

from app.core.channel_manager import ChannelManager  # noqa: E402
from app.core.labber_parser import load_experiment  # noqa: E402

CUR = np.array([40.0, 40.1, 40.2])
F1 = np.linspace(2.5e9, 4.5e9, 21)


def _write(path: Path, f31) -> Path:
    attrs = {"schema": "labcontrol-nc/1", "name": "two S", "created": "2026-11-17T10:00:00", "layout": "grid", "complete": 1}
    s11 = (np.arange(3 * 21).reshape(3, 21) + 1.0) * (1 + 0.5j)
    coords = {"Current": ("Current", CUR, {"units": "mA", "long_name": "Current", "target": "pair.level", "si_scale": 1e3}),
              "Frequency": ("Frequency", F1, {"units": "Hz", "long_name": "Frequency"})}
    d31 = "Frequency"
    if f31 is not F1:
        d31 = "Frequency_2"
        coords[d31] = (d31, f31, {"units": "Hz", "long_name": "Frequency"})
    ds = xr.Dataset({"S11": (("Current", "Frequency"), s11, {"units": "", "long_name": "VNA - S11", "channel": "S11"}),
                     "S31": (("Current", d31), s11 * 2, {"units": "", "long_name": "VNA - S31", "channel": "S31"}),
                     "timestamp": (("Current",), 1.79e9 + np.arange(3.0), {"units": "s"})},
                    coords=coords, attrs=attrs)
    ds.to_netcdf(path, engine="h5netcdf", invalid_netcdf=True)
    return path


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _cands(mgr, name, source):
    return [c for c in mgr.list_axis_candidates() if c.name == name and c.source == source]


def test_shared_frequency_listed_once_and_pairs_with_every_channel(tmp_path):
    exp = load_experiment(str(_write(tmp_path / "a.nc", F1)))
    try:
        mgr = ChannelManager(exp)
        xs = [c for c in mgr.list_x_axis_candidates() if c.source == "trace_axis"]
        assert [c.name for c in xs] == ["Frequency"]
        f = xs[0]
        for y in ("VNA - S11", "VNA - S31"):
            yc = _cands(mgr, y, "vector_channel")[0]
            assert mgr.axis_domains_compatible(f, yc)
            np.testing.assert_allclose(mgr.get_axis_data(f), F1)
            assert mgr.get_axis_data(yc, entry_index=1).shape == (21,)
        derived31 = [c for c in mgr.list_axis_candidates() if c.source == "derived" and c.base_channel == "VNA - S31"]
        assert derived31 and all(mgr.axis_domains_compatible(f, c) for c in derived31)
        # S31 對 S11（同一條頻率軸，逐點）也可以
        assert mgr.axis_domains_compatible(_cands(mgr, "VNA - S11", "vector_channel")[0],
                                           _cands(mgr, "VNA - S31", "vector_channel")[0])
    finally:
        exp.close()


def test_different_frequency_axes_stay_separate(tmp_path):
    exp = load_experiment(str(_write(tmp_path / "b.nc", np.linspace(5e9, 6e9, 21))))
    try:
        mgr = ChannelManager(exp)
        xs = [c for c in mgr.list_x_axis_candidates() if c.source == "trace_axis"]
        assert len(xs) == 2 and len({c.name for c in xs}) == 2
        s31 = _cands(mgr, "VNA - S31", "vector_channel")[0]
        own = next(c for c in xs if c.base_channel == "VNA - S31")
        other = next(c for c in xs if c.base_channel == "VNA - S11")
        assert mgr.axis_domains_compatible(own, s31) and not mgr.axis_domains_compatible(other, s31)
    finally:
        exp.close()


def test_main_window_plots_s31_against_frequency(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from app.gui.main_window import MainWindow

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a)))
    win = MainWindow()
    try:
        win.open_file(str(_write(tmp_path / "c.nc", F1)))
        x_items = [win.x_combo.itemText(i) for i in range(win.x_combo.count())]
        assert x_items.count("Frequency") == 1
        win.y_combo.setCurrentIndex(win.y_combo.findText("VNA - S31"))
        win.update_plot()
        assert not warned
        xdata, ydata = win.plot_widget._curve.getData()
        assert len(xdata) == len(F1) and np.isfinite(ydata).all()
        np.testing.assert_allclose(xdata, F1, rtol=1e-6)
    finally:
        win.close()
