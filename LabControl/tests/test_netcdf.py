"""0.0.20：量測存成 xarray .nc（取代 Labber）。"""
from __future__ import annotations

import json

import numpy as np
import pytest

from labcontrol.data.dataset import AxisSpec, ChannelSpec, Dataset, PointRecord
from labcontrol.data.readers import open_dataset
from labcontrol.data.writers.netcdf import NetCDFWriter, open_nc
from labcontrol.measure import Experiment

from .conftest import experiment_cfg

xr = pytest.importorskip("xarray")


def _ds(n_points=None, jitter=False):
    ax = [AxisSpec("magnet A", "magnet.level", "A", np.linspace(0.1, 0.15, 3), "mA", 1e3),
          AxisSpec("power", "vna.power", "dBm", np.array([-10.0, 0.0]), "dBm", 1.0)]
    f = np.linspace(6e9, 7e9, 5)
    ch = [ChannelSpec("S21", "", True, True, "Frequency", "Hz", f, export_name="VNA - S21", source="VNA1"),
          ChannelSpec("I avg", "A")]
    ds = Dataset("t", ax, ch, {"created": "2026-10-09", "experiment": {"output": {"lab_project": {"id": "7", "name": "YIG"}},
                                                                       "scheme": {"name": "t"}}})
    k = 0
    for a in ax[0].values:
        for p in ax[1].values:
            if n_points is not None and k >= n_points:
                break
            sp = {"magnet A": a + (1e-5 * k if jitter else 0), "power": p}
            ds.add(PointRecord(k, sp, [{"S21": np.exp(1j * f / 1e9) * (k + 1), "I avg": k * 1e-3}], timestamp=1000 + k))
            k += 1
    return ds, f


def test_grid_layout_units_complex_and_partial(tmp_path):
    ds, f = _ds(n_points=5)
    p = NetCDFWriter(user="QEL", tags=["a", "b"], comment="hi").export(ds, tmp_path / "r.nc")
    x = xr.open_dataset(p, engine="h5netcdf")
    assert dict(x.sizes) == {"magnet_A": 3, "power": 2, "Frequency": 5}
    assert x["magnet_A"].attrs["units"] == "mA" and list(x["magnet_A"].values) == [100.0, 125.0, 150.0]
    s = x["S21"].sel(magnet_A=125, power=0.0)                       # 第 4 點（k=3）
    np.testing.assert_allclose(s.values, np.exp(1j * f / 1e9) * 4)
    assert np.isnan(x["I_avg"].sel(magnet_A=150, power=0.0))        # 沒量到 → NaN
    assert x.attrs["tags"] == "a, b" and x.attrs["project"] == "YIG" and x.attrs["project_id"] == "7"
    assert x.attrs["complete"] == 0 and x.attrs["layout"] == "grid"
    assert json.loads(x.attrs["labcontrol"])["experiment"]["scheme"]["name"] == "t"
    assert x["S21"].attrs["long_name"] == "VNA - S21"
    # 讀回 Dataset（Lab Control 自己的讀檔介面）
    back = open_dataset(p)
    assert len(back) == 5 and [a.name for a in back.axes] == ["magnet A", "power"]
    np.testing.assert_allclose(back.column("S21"), ds.column("S21"))
    np.testing.assert_allclose(back.setpoints("magnet A"), ds.setpoints("magnet A"))
    assert open_nc(p).attrs["labcontrol_meta"]["created"] == "2026-10-09"


def test_point_layout_when_not_on_grid(tmp_path):
    ds, f = _ds(jitter=True)
    p = NetCDFWriter().export(ds, tmp_path / "j.nc")
    x = xr.open_dataset(p, engine="h5netcdf")
    assert x.attrs["layout"] == "point" and x.sizes["point"] == 6
    np.testing.assert_allclose(x["magnet_A"].values, ds.setpoints("magnet A") * 1e3)
    back = open_dataset(p)
    np.testing.assert_allclose(back.column("I avg"), ds.column("I avg"))


def test_experiment_exports_nc_and_apply_settings(station, tmp_path):
    cfg = experiment_cfg(stop=158.602)
    cfg["output"]["export"] = [{"type": "netcdf", "tags": ["T"], "user": "me"}]
    cfg["output"]["file_name"] = "run.nc"
    exp = Experiment(station, cfg)
    out = exp.plan_output(root=tmp_path)
    ds = exp.create_runner(out).run()
    paths = exp.export(ds, out)
    assert paths[0].suffix == ".nc"
    x = xr.open_dataset(paths[0], engine="h5netcdf")
    assert x["S21"].dims == ("Average_Current", "Frequency") and x["S21"].shape == (len(ds), 101)
    assert x.attrs["tags"] == "T" and x.attrs["complete"] == 1
    import h5py

    with h5py.File(paths[0], "r") as f:         # 拖回量測模塊：h5py 讀得到完整的量測設定
        meta = json.loads(f.attrs["labcontrol"])
    assert meta["experiment"]["name"] == "test"
