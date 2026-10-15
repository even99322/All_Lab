"""v1.0.7: Lab Control .nc ⇄ Labber .hdf5 conversion (File menu / Browser right-click)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import h5py
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DATA = Path(__file__).parent / "data"
NC = DATA / "labcontrol_sim_sweep.nc"
REPORT = Path(__file__).parent.parent / "sample_reports" / "0828_RSMEP_1.inspect.json"


def _two_axis_nc(path: Path) -> Path:
    """A 2-axis grid sweep (power outer, current inner) with a complex trace and a scalar channel."""
    from app.core.labber_convert import Axis, Chan, Sweep, write_nc

    power = np.array([-20.0, -15.0, -10.0])
    current = np.array([1.0, 1.5, 2.0, 2.5])
    freq = np.linspace(5e9, 5.01e9, 11)
    p, c, f = np.meshgrid(power, current, freq, indexing="ij")
    s21 = (p + 1j * c) * (1 + f / 1e10)                        # every cell distinct and checkable
    temp = (power[:, None] * 10 + current[None, :])
    sweep = Sweep("two axis", [Axis("Output power", "dBm", power), Axis("Average Current", "mA", current)],
                  [Chan("VNA - S21", "", s21, "VNA", "Frequency", "Hz", freq), Chan("Temperature", "K", temp)],
                  fixed=[("IF bandwidth", "Hz", 1000.0)], instrument_config={"VNA": {"IF bandwidth": 1000.0}},
                  project="YIG", user="QEL", tags=["a", "b"], comment="hello")
    return write_nc(sweep, path)


def _same(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and np.allclose(a, b, equal_nan=True)


def test_nc_to_labber_builtin_matches_real_labber_layout(tmp_path):
    from app.core import labber_convert as lc

    res = lc.convert_file(NC, "hdf5", engine="builtin")
    assert res.ok, res.error
    out = Path(res.output)
    try:
        assert out.suffix == ".hdf5" and out.parent == NC.parent and res.engine == "builtin"
        # every group / dataset / dtype a file written by the Labber API has (except saved Log Browser views)
        real = json.loads(REPORT.read_text(encoding="utf-8"))
        ours = {}
        with h5py.File(out, "r") as f:
            def seen(name, obj):
                ours["/" + name] = obj.dtype if hasattr(obj, "dtype") else None
            f.visititems(seen)
            for node in real["nodes"]:
                path = node["path"]
                if path.startswith(("/Views", "/Step config/", "/Instrument config/")) or "VNA - S21" in path:
                    continue
                assert path == "/" or path in ours, path
                if node["kind"] == "dataset" and node["dtype"] and node["dtype"].startswith("[("):
                    fields = [x.split("'")[1] for x in node["dtype"][2:-2].split("), (")]
                    assert list(ours[path].names) == fields, path
            for key in real["nodes"][0]["attrs"]:
                assert key in f.attrs, key
            for key in ("Completed", "Entries, last trace", "Step dimensions", "Step index"):
                assert key in f["Data"].attrs
            steps = [r[0].decode() if isinstance(r[0], bytes) else r[0] for r in f["Step list"][()]]
            assert steps[0] == "Step index API"
            assert list(f["Data"].attrs["Step dimensions"])[0] == 1
            assert f["Data/Data"].shape[0] == 1
            names = [k for k in f["Traces"] if not k.endswith(("_N", "_t0dt", "_xvalues")) and k != "Time stamp"]
            assert names and all(f["Traces"][k].ndim == 3 for k in names)
    finally:
        out.unlink()


def test_round_trip_nc_labber_nc(tmp_path):
    """.nc → Labber .hdf5 → .nc keeps the axes, the traces and the values — read back by LabLogViewer itself."""
    from app.core import labber_convert as lc

    src = tmp_path / "src.nc"
    import shutil

    shutil.copy(NC, src)
    a = lc.load_sweep(src)
    h = lc.convert_file(src, "hdf5", engine="builtin")
    assert h.ok, h.error
    b = lc.load_sweep(h.output)
    n = lc.convert_file(h.output, "nc")
    assert n.ok, n.error and n.output.endswith(".nc")
    c = lc.load_sweep(n.output)
    for other in (b, c):
        assert [x.name for x in other.axes] == [x.name for x in a.axes]
        assert all(_same(x.values, y.values) for x, y in zip(a.axes, other.axes))
        got = {ch.name: ch for ch in other.channels}
        for ch in a.channels:
            assert ch.name in got and _same(np.asarray(ch.data), np.asarray(got[ch.name].data)), ch.name
            if ch.vector:
                assert _same(ch.x, got[ch.name].x) and got[ch.name].x_name == ch.x_name


def test_two_axis_sweep_keeps_cells(tmp_path):
    """Labber runs the first Step-list channel fastest: the converted file must put each cell back in place."""
    from app.core import labber_convert as lc
    from app.core.labber_parser import load_experiment

    src = _two_axis_nc(tmp_path / "two.nc")
    a = lc.load_sweep(src)
    assert a.sizes == (3, 4) and a.project == "YIG" and a.tags == ["a", "b"]
    res = lc.convert_file(src, "hdf5", engine="builtin")
    assert res.ok, res.error
    with h5py.File(res.output, "r") as f:
        steps = [r[0] for r in f["Step list"][()]]
        steps = [s.decode() if isinstance(s, bytes) else s for s in steps]
        assert steps[:3] == ["Step index API", "Average Current", "Output power"]      # inner loop first
        assert list(f["Data"].attrs["Step dimensions"][:3]) == [1, 4, 3]
        cur = f["Data/Data"][0, steps.index("Average Current")]
        assert list(cur[:5]) == [1.0, 1.5, 2.0, 2.5, 1.0]                           # inner fastest
        assert "VNA" in f["Instrument config"] and "Step channels" in f["Instrument config"]   # .nc snapshot
        assert list(f["Tags"].attrs["Project"]) in (["YIG"], [b"YIG"])
    exp = load_experiment(res.output)
    try:
        assert exp.entry_order == "F"
        assert [ax.channel.name for ax in exp.step_axes] == ["Average Current", "Output power"]
        assert list(exp.step_axes[0].values) == [1.0, 1.5, 2.0, 2.5]
        assert list(exp.step_axes[1].values) == [-20.0, -15.0, -10.0]
        dims, arr = exp.get_full_nd_array("VNA - S21")                             # (freq, current, power)
        want = next(c for c in a.channels if c.name == "VNA - S21").data           # (power, current, freq)
        assert np.allclose(arr, np.transpose(want, (2, 1, 0)))
        grid = exp.get_2d_data("Average Current", "Output power", "Temperature")
        assert np.allclose(grid.z_values, next(c for c in a.channels if c.name == "Temperature").data)
    finally:
        exp.close()
    back = lc.convert_file(res.output, "nc")
    assert back.ok, back.error
    c = lc.load_sweep(back.output)
    assert [x.name for x in c.axes] == ["Output power", "Average Current"]
    for ch in a.channels:
        assert np.allclose(next(x for x in c.channels if x.name == ch.name).data, ch.data)
    assert c.instrument_config.get("Step channels", {}).get("IF bandwidth") == 1000.0


def test_nc_from_labber_opens_with_xarray(tmp_path):
    xr = pytest.importorskip("xarray")
    from app.core import labber_convert as lc

    src = _two_axis_nc(tmp_path / "two.nc")
    h = lc.convert_file(src, "hdf5", engine="builtin")
    n = lc.convert_file(h.output, "nc", folder=tmp_path / "out")
    assert Path(n.output).parent == tmp_path / "out"
    ds = xr.open_dataset(n.output, engine="h5netcdf")
    try:
        assert ds.attrs["schema"] == "labcontrol-nc/1" and ds.attrs["layout"] == "grid"
        v = ds["VNA___S21"]
        assert v.dims == ("Output_power", "Average_Current", "Frequency") and np.iscomplexobj(v.values)
        assert ds["Average_Current"].attrs["units"] == "mA" and ds["Average_Current"].attrs["target"]
    finally:
        ds.close()


def test_never_overwrites_and_reports_errors(tmp_path):
    from app.core import labber_convert as lc

    src = _two_axis_nc(tmp_path / "x.nc")
    first = lc.convert_file(src, "hdf5", engine="builtin")
    second = lc.convert_file(src, "hdf5", engine="builtin")
    assert Path(first.output).name == "x.hdf5" and Path(second.output).name == "x_2.hdf5"
    bad = tmp_path / "bad.nc"
    bad.write_bytes(b"not a file")
    res = lc.convert_files([bad, src], "hdf5", engine="builtin")
    assert not res[0].ok and res[0].error and res[1].ok
    missing = lc.convert_file(src, "hdf5", engine="api", labber_python=str(tmp_path / "nope.exe"))
    assert not missing.ok and "Labber Python" in missing.error


def test_api_engine_runs_the_script(tmp_path, monkeypatch):
    """The Labber-API route: the spec + script, run by 'the Labber Python' (a fake Labber module here)."""
    import sys

    from app.core import labber_convert as lc

    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "Labber.py").write_text(
        "import h5py, numpy as np\n"
        "calls = {}\n"
        "class _F:\n"
        "    def __init__(self, path, logs, steps):\n"
        "        self.path, self.logs, self.steps, self.entries = path, logs, steps, []\n"
        "    def setProject(self, p): pass\n"
        "    def setUser(self, u): pass\n"
        "    def setTags(self, t): pass\n"
        "    def setComment(self, c): pass\n"
        "    def addEntry(self, e): self.entries.append(dict(e))\n"
        "    def __del__(self):\n"
        "        with h5py.File(self.path, 'w') as f:\n"
        "            f.attrs['n'] = len(self.entries)\n"
        "            f.attrs['steps'] = ','.join(s['name'] for s in self.steps)\n"
        "            f.create_group('Traces')\n"
        "def createLogFile_ForData(path, logs, steps, use_database=False):\n"
        "    return _F(path, logs, steps)\n"
        "def getTraceDict(y, x0=0.0, x1=1.0):\n"
        "    return {'y': y, 't0': x0}\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(fake))
    src = _two_axis_nc(tmp_path / "two.nc")
    monkeypatch.setitem(lc._API_OK, sys.executable, True)
    res = lc.convert_file(src, "hdf5", engine="api", labber_python=sys.executable)
    assert res.ok, res.error
    assert res.engine == "api"
    with h5py.File(res.output, "r") as f:
        assert int(f.attrs["n"]) == 12 and f.attrs["steps"].startswith("Average Current,Output power")
        assert "VNA" in f["Instrument config"]
    assert not list(tmp_path.glob("*.part*"))


@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_convert_dialog_batch(qapp, tmp_path):
    from app.gui.convert_dialog import ConvertDialog

    a = _two_axis_nc(tmp_path / "a.nc")
    b = _two_axis_nc(tmp_path / "b.nc")
    d = ConvertDialog(None, "hdf5", [a, b, tmp_path / "skip.hdf5"])
    assert d.files() == [str(a), str(b)]                           # wrong type is left out
    d.engine_combo.setCurrentIndex(2)                              # built-in
    d.other_folder.setChecked(True)
    d.folder_edit.setText(str(tmp_path / "labber"))
    got = []
    d.converted.connect(got.append)
    d.start()
    assert d.wait()
    assert sorted(Path(p).name for p in got[0]) == ["a.hdf5", "b.hdf5"]
    assert all(Path(p).parent == tmp_path / "labber" for p in got[0])
    assert "✔" in d.log.toPlainText() and d.close_button.text() in ("Close", "關閉")
    back = ConvertDialog(None, "nc", got[0])
    assert not back.labber_box.isVisibleTo(back)
    back.start()
    assert back.wait()
    assert all(Path(r.output).suffix == ".nc" for r in back.results)
    d.close()
    back.close()


def test_menus_offer_conversion(qapp, tmp_path, monkeypatch):
    import shutil

    from PySide6.QtWidgets import QMessageBox

    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow
    from app.gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    w = MainWindow()
    try:
        names = [a.text() for a in w.convert_menu.actions()]
        assert any(".nc → Labber" in n for n in names) and any("→ Lab Control .nc" in n for n in names)
        w.open_file(str(NC))
        d = w.open_convert("hdf5")
        assert d is not None and d.files() == [str(NC)]
        d.close()
    finally:
        w.close()

    db = tmp_path / "db"
    db.mkdir()
    shutil.copy(NC, db / "one.nc")
    _two_axis_nc(db / "two.nc")
    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    try:
        win.open_database(str(db))
        for _ in range(200):
            qapp.processEvents()
            if win.scan_result is not None and win.data_list.topLevelItemCount() >= 2 and \
                    (win._scan_worker is None or not win._scan_worker.isRunning()):
                break
            import time

            time.sleep(0.02)
        items = []
        def walk(item):
            if item.data(0, 0x0100) is not None:
                items.append(item)
            for i in range(item.childCount()):
                walk(item.child(i))
        for i in range(win.data_list.topLevelItemCount()):
            walk(win.data_list.topLevelItem(i))
        assert len(items) >= 2
        for it in items:
            it.setSelected(True)
        shown = {}
        monkeypatch.setattr(win, "_exec_menu", lambda menu, _pos: shown.setdefault("menu", menu))
        win._on_context_menu(win.data_list.visualItemRect(items[0]).center())
        acts = {a.objectName(): a for a in shown["menu"].actions()}
        assert "browserConvertToLabber" in acts and "2 Files" in acts["browserConvertToLabber"].text()
        assert len(win.data_list.selectedItems()) >= 2                  # right-click kept the selection
        dialog = win.convert_selected("hdf5")
        assert dialog is not None and len(dialog.files()) == 2
        dialog.engine_combo.setCurrentIndex(2)
        dialog.same_folder.setChecked(True)                             # (remembered from the last run)
        dialog.start()
        assert dialog.wait()
        assert (db / "one.hdf5").exists() and (db / "two.hdf5").exists()
        dialog.close()
    finally:
        win.close()
