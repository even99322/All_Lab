import datetime as dt
import importlib.util
from pathlib import Path

import numpy as np

import labcontrol.data.writers.labber_export_script as les
from labcontrol.analysis.qc import evaluate_trace, iq_quadrant_classes
from labcontrol.data.naming import day_folder, stale_date_in_filename, unique_filename
from labcontrol.measure import Experiment

from .conftest import experiment_cfg


def test_labber_spec_matches_legacy_layout(station, tmp_path):
    exp = Experiment(station, experiment_cfg(stop=158.602))
    out = exp.plan_output(root=tmp_path)
    ds = exp.create_runner(out).run()
    spec = les.build_labber_spec(str(out.raw_path))
    names = [c["name"] for c in spec["step_channels"]]
    assert names[0] == "Average Current" and spec["step_channels"][0]["unit"] == "mA"
    np.testing.assert_allclose(spec["entry_axes"][0][1], ds.setpoints("Average Current") * 1e3)
    for legacy in ("S21 - Enabled", "Output power", "IF bandwidth", "Average", "# of averages",
                   "Start frequency", "Stop frequency", "# of points"):
        assert legacy in names
    assert spec["log_channels"][0]["name"] == "VNA - S21" and spec["log_channels"][0]["complex"]
    assert "VNA" in spec["instrument_config"]
    assert spec["channel_data"][0]["data"].shape == (5, 101)


def test_labber_script_is_standalone():
    src = Path(les.__file__).read_text(encoding="utf-8")
    assert "labcontrol" not in [l.split()[1].split(".")[0] for l in src.splitlines()
                               if l.startswith(("import ", "from "))]


def test_naming(tmp_path):
    now = dt.datetime(2026, 9, 29)
    assert day_folder("/r", now=now).as_posix() == "/r/2026/09/Data_0929"
    assert stale_date_in_filename("0901 RSMEP_13.hdf5", now) == ("0901", "0929", "0929 RSMEP_13.hdf5")
    assert stale_date_in_filename("0929 x.hdf5", now) is None
    (tmp_path / "a.hdf5").touch()
    assert unique_filename(tmp_path, "a.hdf5", "underscore") == "a_1.hdf5"
    (tmp_path / "a_1.hdf5").touch()
    assert unique_filename(tmp_path, "a_1.hdf5", "自動遞增 (底線)") == "a_2.hdf5"
    assert unique_filename(tmp_path, "a.hdf5", "paren") == "a (2).hdf5"
    assert unique_filename(tmp_path, "a.hdf5", "overwrite") == "a.hdf5"


def test_qc_functions():
    f = np.linspace(-1, 1, 401)
    tr = 1e-3 * (1 - 0.999 / (1 + 2j * f / 0.01))
    s = evaluate_trace(tr, mag_thresh=-70, phase_thresh=50, slope_thresh=5)
    assert s.mag_pass and s.min_mag < -100
    cls = iq_quadrant_classes(tr, radius=1e-3)
    assert set(cls["class"]) <= {"major", "other", "deep_lone", "deep_group", "out"}


def test_example_plugin_loads(station):
    spec = importlib.util.find_spec("labcontrol")
    assert spec is not None
    from labcontrol.core.registry import DRIVERS, HOOKS, load_plugin_paths

    root = Path(__file__).resolve().parents[1] / "labcontrol" / "defaults" / "plugins"
    load_plugin_paths([root])
    assert "dip_edge_stop" in HOOKS.names()
    # 底線開頭的範本 / 範例不會自動載入；直接匯入確認語法與註冊正確
    for f in ("_driver_template.py", "_example_keithley2400.py"):
        sp = importlib.util.spec_from_file_location(f"t_{f[1:-3]}", root / f)
        mod = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(mod)
    assert "keithley.k2400" in DRIVERS.names()
    assert "mylab.my_instrument" in DRIVERS.names()
