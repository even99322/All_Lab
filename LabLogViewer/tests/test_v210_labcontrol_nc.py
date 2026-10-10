"""
tests/test_v210_labcontrol_nc.py

v2.1.0: Lab Control xarray/NetCDF (.nc, schema "labcontrol-nc/1") files open
through the normal HDF5Reader -> AutoDetector -> Experiment pipeline, via the
Labber-shaped virtual view in app/core/labcontrol_nc.py.

The fixture files are written here with xarray + h5netcdf exactly the way
Lab Control's NetCDF writer lays them out (sweep coordinates carry a "target"
attribute, logged variables a "channel" attribute, complex data stored
natively).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

xr = pytest.importorskip("xarray")
pytest.importorskip("h5netcdf")

from app.core.hdf5_reader import HDF5Reader  # noqa: E402
from app.core.labber_parser import AutoDetector, load_experiment  # noqa: E402

FREQ = np.linspace(5.0e9, 5.1e9, 11)
CURRENT = np.array([150.0, 151.0, 152.0])        # mA (display units)
POWER = np.array([-20.0, -10.0])


def _axis(values, name, unit, target, scale):
    return (name.replace(" ", "_"), values,
            {"units": unit, "long_name": name, "target": target, "si_units": "A" if unit == "mA" else unit,
             "si_scale": scale})


def _write(path: Path, *, partial: bool = False, point: bool = False) -> Path:
    meta = {"created": "2026-10-09T21:00:00", "snapshot": {"VNA1": {"driver": "rs.zna", "power": -10}},
            "experiment": {"name": "2D sweep"}}
    attrs = {"schema": "labcontrol-nc/1", "name": "2D sweep", "created": "2026-10-09T21:00:00", "user": "QEL",
             "tags": "YIG, mirror", "comment": "hello", "project": "YIG mirror coupling", "project_id": "1",
             "layout": "point" if point else "grid", "complete": 0 if partial else 1,
             "labcontrol": json.dumps(meta)}
    rng = np.arange(len(CURRENT) * len(POWER) * len(FREQ)).reshape(len(CURRENT), len(POWER), len(FREQ))
    s21 = (rng + 1.0) * np.exp(1j * rng / 10.0)
    iavg = np.arange(len(CURRENT) * len(POWER), dtype=float).reshape(len(CURRENT), len(POWER)) * 1e-3
    ts = 1_791_500_000.0 + np.arange(len(CURRENT) * len(POWER), dtype=float).reshape(iavg.shape)
    if partial:
        s21[2, 1, :] = np.nan
        iavg[2, 1] = np.nan
        ts[2, 1] = np.nan
    if point:
        n = 4
        cur = np.array([150.0, 150.3, 151.7, 152.0])
        pw = np.array([-20.0, -20.0, -10.0, -10.0])
        ds = xr.Dataset(
            {"S21": (("point", "Frequency"), s21.reshape(-1, len(FREQ))[:n],
                     {"units": "", "long_name": "VNA - S21", "channel": "S21", "source": "VNA1"}),
             "timestamp": (("point",), ts.reshape(-1)[:n], {"units": "s"})},
            coords={"point": ("point", np.arange(n), {"long_name": "order"}),
                    "Average_Current": ("point", cur, {"units": "mA", "long_name": "Average Current",
                                                       "target": "pair.level", "si_scale": 1e3}),
                    "power": ("point", pw, {"units": "dBm", "long_name": "power", "target": "VNA1.power",
                                            "si_scale": 1.0}),
                    "Frequency": ("Frequency", FREQ, {"units": "Hz", "long_name": "Frequency"})},
            attrs=attrs)
    else:
        cur = _axis(CURRENT, "Average Current", "mA", "pair.level", 1e3)
        pw = _axis(POWER, "power", "dBm", "VNA1.power", 1.0)
        dims = (cur[0], pw[0])
        ds = xr.Dataset(
            {"S21": (dims + ("Frequency",), s21, {"units": "", "long_name": "VNA - S21", "channel": "S21",
                                                  "source": "VNA1"}),
             "I_avg": (dims, iavg, {"units": "A", "long_name": "I avg", "channel": "I avg", "source": "DMM"}),
             "timestamp": (dims, ts, {"units": "s"}),
             "retained": (dims, np.zeros(iavg.shape, dtype=bool), {})},
            coords={cur[0]: (cur[0], cur[1], cur[2]), pw[0]: (pw[0], pw[1], pw[2]),
                    "Frequency": ("Frequency", FREQ, {"units": "Hz", "long_name": "Frequency"})},
            attrs=attrs)
    ds.to_netcdf(path, engine="h5netcdf", invalid_netcdf=True)
    return path


@pytest.fixture
def grid_file(tmp_path):
    return _write(tmp_path / "run.nc")


def test_reader_presents_labber_view(grid_file):
    with HDF5Reader(grid_file) as reader:
        assert reader.is_labcontrol_nc
        for path in ("/Channels", "/Step list", "/Log list", "/Data/Data", "/Traces/VNA - S21"):
            assert reader.kind_of(path) == "dataset", path
        assert reader.get_attrs("/Data")["Step dimensions"] == [3, 2]
        assert reader.read("/Traces/VNA - S21").shape == (len(FREQ), 2, 6)
        assert any(node.path == "/Data/Data" for node in reader.tree())


def test_experiment_axes_channels_tags(grid_file):
    exp = load_experiment(str(grid_file))
    try:
        assert [a.channel.name for a in exp.step_axes] == ["Average Current", "power"]
        np.testing.assert_allclose(exp.step_axes[0].values, CURRENT)
        np.testing.assert_allclose(exp.step_axes[1].values, POWER)
        assert exp.step_axes[0].channel.unit == "mA"
        vt = exp.vector_traces["VNA - S21"]
        np.testing.assert_allclose(vt.x_values, FREQ)
        assert vt.complex and vt.x_unit == "Hz"
        assert exp.project == "YIG mirror coupling" and exp.tags == ["YIG", "mirror"] and exp.user == "QEL"
        assert exp.comment == "hello" and exp.log_name
        assert exp.instrument_config["VNA1"]["power"] == -10
        assert exp.get_channel("I avg").unit == "A"
    finally:
        exp.close()


def test_values_match_written_data(grid_file):
    exp = load_experiment(str(grid_file))
    try:
        rng = np.arange(3 * 2 * len(FREQ)).reshape(3, 2, len(FREQ))
        expected = (rng + 1.0) * np.exp(1j * rng / 10.0)
        dims, arr = exp.get_full_nd_array("VNA - S21")
        assert [d.name for d in dims][1:] == ["Average Current", "power"]
        # arr: (Frequency, current, power)
        np.testing.assert_allclose(arr[:, 1, 0], expected[1, 0, :])
        np.testing.assert_allclose(arr[:, 2, 1], expected[2, 1, :])
        scalar = exp.get_data("I avg")
        np.testing.assert_allclose(scalar, np.arange(6) * 1e-3)
        grid = exp.get_2d_data("Average Current", "power", "I avg")
        assert grid.z_values.shape == (2, 3)
        assert grid.z_values[1, 2] == pytest.approx(5e-3)            # current 152, power -10
    finally:
        exp.close()


def test_partial_measurement_keeps_nan(tmp_path):
    exp = load_experiment(str(_write(tmp_path / "partial.nc", partial=True)))
    try:
        _dims, arr = exp.get_full_nd_array("VNA - S21", transform="magnitude")
        assert np.isnan(arr[:, 2, 1]).all() and np.isfinite(arr[:, 0, 0]).all()
    finally:
        exp.close()


def test_point_layout(tmp_path):
    exp = load_experiment(str(_write(tmp_path / "pts.nc", point=True)))
    try:
        assert [a.channel.name for a in exp.step_axes] == ["Point"]
        np.testing.assert_allclose(exp.get_data("Average Current"), [150.0, 150.3, 151.7, 152.0])
        assert exp.get_data("VNA - S21").shape == (len(FREQ), 4)
    finally:
        exp.close()


def test_database_scanner_lists_nc(tmp_path):
    from app.core.database_scanner import HDF5_EXTENSIONS

    assert ".nc" in HDF5_EXTENSIONS
    _write(tmp_path / "a.nc")
    with HDF5Reader(tmp_path / "a.nc") as reader:
        exp = AutoDetector.detect_and_parse(reader)
        assert exp.total_dimensions >= 3


# ---- GUI: the viewer and the database browser open .nc files ------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_main_window_opens_nc(qapp, grid_file):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    try:
        win.open_file(str(grid_file))
        assert win.experiment is not None and win.experiment.vector_traces
        y_items = [win.y_combo.itemText(i) for i in range(win.y_combo.count())]
        assert "VNA - S21" in y_items
        curve = win.plot_widget._curve
        assert curve is not None
        xdata, ydata = curve.getData()
        assert len(xdata) == len(FREQ) and np.isfinite(ydata).all()
    finally:
        win.close()


def test_browser_lists_nc_files(qapp, tmp_path):
    from PySide6.QtCore import QEventLoop, QTimer

    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    db = tmp_path / "db" / "YIG mirror coupling" / "LAB-A"
    db.mkdir(parents=True)
    _write(db / "a.nc")
    _write(db / "b.nc", partial=True)
    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    loop = QEventLoop()
    win.open_database(str(tmp_path / "db"))
    win._scan_worker.finished_scan.connect(loop.quit)
    win._scan_worker.failed.connect(loop.quit)
    QTimer.singleShot(15000, loop.quit)
    loop.exec()
    try:
        entries = {e.file_name: e for e in win.scan_result.entries}
        assert set(entries) == {"a.nc", "b.nc"}
        assert all(e.status == "ok" for e in entries.values()), [e.error_message for e in entries.values()]
    finally:
        win.close()


# ---- 2.1.0 on 2.0 (2026-10-10 base): tags / star / comment inside .nc, De-background ---------------
def test_native_tags_star_comment_live_in_nc_root_attrs(grid_file):
    from app.core.labber_comment import read_native_comment, write_native_comment
    from app.core.labber_star import read_native_star, write_native_star
    from app.core.labber_tags import read_native_tags, write_native_tags

    assert read_native_tags(grid_file) == ["YIG", "mirror"]
    assert read_native_star(grid_file) is None
    assert write_native_tags(grid_file, ["YIG", "coupling"], ["mirror"]) == ["YIG", "coupling"]
    assert write_native_star(grid_file, True) is True
    assert write_native_comment(grid_file, "edited").text == "edited"
    ds = xr.open_dataset(grid_file, engine="h5netcdf")         # still a clean NetCDF dataset
    try:
        assert ds.attrs["tags"] == "YIG, coupling" and int(ds.attrs["star"]) == 1 and ds.attrs["comment"] == "edited"
        assert "S21" in ds.data_vars
    finally:
        ds.close()
    exp = load_experiment(str(grid_file))
    try:
        assert exp.tags == ["YIG", "coupling"] and exp.comment == "edited"
    finally:
        exp.close()
    with HDF5Reader(grid_file) as reader:
        assert reader.get_attrs("/")["star"] is True
    assert write_native_star(grid_file, False) is False


def test_scanner_reads_star_and_tags_from_nc(tmp_path):
    from app.core.database_scanner import DatabaseScanner
    from app.core.labber_star import write_native_star

    path = _write(tmp_path / "s.nc")
    write_native_star(path, True)
    result = DatabaseScanner.scan(tmp_path)
    entry = next(e for e in result.entries if e.file_name == "s.nc")
    assert entry.status == "ok" and entry.file_star is True and entry.file_tags == ["YIG", "mirror"]


def test_debackground_nc_gives_nc(tmp_path):
    from app.core.debackground import process_debackground

    target = _write(tmp_path / "target.nc")
    bg = 2.0 * np.exp(0.5j) * np.ones(len(FREQ))               # one trace, no sweep (a Lab Control single shot)
    xr.Dataset({"S21": (("Frequency",), bg, {"units": "", "long_name": "VNA - S21", "channel": "S21"})},
               coords={"Frequency": ("Frequency", FREQ, {"units": "Hz", "long_name": "Frequency"})},
               attrs={"schema": "labcontrol-nc/1", "name": "bg", "layout": "grid", "complete": 1}
               ).to_netcdf(tmp_path / "background.nc", engine="h5netcdf", invalid_netcdf=True)
    out = process_debackground(target, tmp_path / "background.nc", "VNA - S21")
    assert out.name == "target_debg.nc"
    rng = np.arange(3 * 2 * len(FREQ)).reshape(3, 2, len(FREQ))
    expected = (rng + 1.0) * np.exp(1j * rng / 10.0) / bg
    ds = xr.open_dataset(out, engine="h5netcdf")
    try:
        assert ds["S21"].dims == ("Average_Current", "power", "Frequency")
        np.testing.assert_allclose(ds["S21"].values, expected, rtol=2e-3)     # float16-quantized like Labber debg
    finally:
        ds.close()
    exp = load_experiment(str(out))
    try:
        assert exp.vector_traces["VNA - S21"].complex
    finally:
        exp.close()
