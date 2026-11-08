"""1.0.31：磁場校正（YIG 共振反推電流 → 磁場）。"""
import numpy as np
import pytest
import yaml

from labcontrol.core import fieldcal, labfile
from labcontrol.core.station import Station

G = 28.0e9          # Hz/T
SLOPE = 4.0         # T/A（= 40 Oe/mA）
B0 = 0.02           # T（剩磁）


def _synthetic(tmp_path, n_i=41, n_f=401, noise=0.05, bad=(7,), units="mA", mode="dip"):
    """電磁鐵電流 × 頻率的 |S21|：每個電流在 f = γ(B0 + SLOPE·I) 有一個洛倫茲吸收谷。"""
    import xarray as xr

    cur = np.linspace(0.02, 0.06, n_i)                    # A
    fq = np.linspace(2e9, 10e9, n_f)
    rng = np.random.default_rng(0)
    z = np.zeros((n_i, n_f))
    fr_true = G * (B0 + SLOPE * cur)
    for k, f0 in enumerate(fr_true):
        if k in bad:
            f0 = 9e9                                       # 找錯 / 交叉的點
        lor = 1 / (1 + ((fq - f0) / 30e6) ** 2)
        z[k] = (-10 * lor if mode == "dip" else 10 * lor) - 3 + noise * rng.standard_normal(n_f)
    scale = 1e3 if units == "mA" else 1.0
    ds = xr.Dataset({"VNA_S21": (("magnet_A", "Frequency"), z, {"units": "dB"})},
                    coords={"magnet_A": ("magnet_A", cur * scale, {"units": units, "target": "magnet_A.level",
                                                                   "si_scale": scale, "long_name": "磁鐵 A 電流"}),
                            "Frequency": ("Frequency", fq, {"units": "Hz"})})
    p = tmp_path / "yig.nc"
    ds.to_netcdf(p, engine="h5netcdf")
    return p, cur, fr_true


def test_find_and_fit_recovers_calibration(tmp_path):
    p, cur, fr = _synthetic(tmp_path)
    info = fieldcal.scan_nc(p)
    assert info["freq"]["name"] == "Frequency" and info["axes"][0]["target"] == "magnet_A.level"
    d = fieldcal.load_nc(p, "VNA_S21", "magnet_A")
    np.testing.assert_allclose(d["current"], cur)
    found = fieldcal.find_resonances(d["freq"], d["data"])
    assert found["mode"] == "dip"
    good = np.arange(len(cur)) != 7
    assert np.max(np.abs(found["f"][good] - fr[good])) < 10e6       # 頻率格 20 MHz，內插後更準
    r = fieldcal.fit(d["current"], found["f"], G, 0.0, 1, 3.0)
    assert not r["used"][7] and r["n"] == len(cur) - 1               # 離群點自動排除
    assert r["coef"][1] == pytest.approx(SLOPE, rel=2e-3) and r["coef"][0] == pytest.approx(B0, abs=2e-4)
    assert r["rms"] < 3e-4
    assert r["r2"] > 0.9999
    # 手動排除
    ex = np.zeros(len(cur), bool)
    ex[0] = True
    assert not fieldcal.fit(d["current"], found["f"], G, exclude=ex)["used"][0]
    # 峰也找得到
    p2, _, _ = _synthetic(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, mode="peak", bad=())
    d2 = fieldcal.load_nc(p2, "VNA_S21", "magnet_A")
    assert fieldcal.find_resonances(d2["freq"], d2["data"])["mode"] == "peak"


def test_calibration_inverse_and_quadratic():
    c = fieldcal.FieldCalibration({"coef": [B0, SLOPE], "range": [0.0, 0.1]})
    assert c.field(0.04) == pytest.approx(B0 + SLOPE * 0.04)
    assert c.current(c.field(0.033)) == pytest.approx(0.033)
    q = fieldcal.FieldCalibration({"coef": [0.001, 0.2, -0.3], "range": [0.0, 0.1]})
    for i in (0.0, 0.02, 0.07, 0.1):
        assert q.current(q.field(i)) == pytest.approx(i, abs=1e-9)
    assert "Oe/A" in c.describe()
    with pytest.raises(Exception):
        fieldcal.FieldCalibration({"coef": [1.0]})


def _station(tmp_path, cal=None):
    inst = {f"DC{i}": {"driver": "yokogawa.gs200", "address": "X",
                       "source": {"limits": [-0.2, 0.2], "ramp_rate": 5, "max_jump": 1, "resolution": 1e-6}}
            for i in (1, 2)}
    inst["magnet_A"] = {"driver": "virtual.interleaved_pair", "group": "magnet", "sources": ["DC1", "DC2"],
                        "source": {"limits": [-0.2, 0.2], "ramp_rate": 5, "max_jump": 1}}
    if cal:
        inst["magnet_A"]["field_cal"] = cal
    p = tmp_path / "instruments.yaml"
    p.write_text(yaml.safe_dump({"simulate": True, "instruments": inst}), encoding="utf-8")
    return Station.from_file(p, simulate=True), p


def test_field_parameter_sets_current(tmp_path):
    st, p = _station(tmp_path, {"method": "yig", "coef": [B0, SLOPE], "range": [0.0, 0.1]})
    m = st.instruments["magnet_A"]
    assert "field" in m.parameters and m.parameters["field"].spec.display_unit == "mT"
    st.connect(["magnet_A"]) if hasattr(st, "connect") else m.connect()
    m.parameters["field"].set(B0 + SLOPE * 0.05)
    assert m.get_level() == pytest.approx(0.05, abs=2e-6)
    assert m.parameters["field"].get() == pytest.approx(B0 + SLOPE * 0.05, abs=1e-6)
    m.parameters["field"].set("150 mT")
    assert m.get_level() == pytest.approx((0.15 - B0) / SLOPE, abs=2e-6)
    from labcontrol.scheme import build_catalog
    t = build_catalog(st).target("magnet_A.field")
    assert t is not None and t.unit == "mT" and t.common and t.limits[1] == pytest.approx(B0 + SLOPE * 0.2)
    st.close()
    st2, _ = _station(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path)
    assert "field" not in st2.instruments["magnet_A"].parameters
    st2.close()


def test_dialog_finds_fits_and_saves(tmp_path):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.fieldcal_dialog import FieldCalDialog

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # noqa: F841
    nc, _, _ = _synthetic(tmp_path)
    st, p = _station(tmp_path)
    d = FieldCalDialog(st, None, path=str(nc), config_path=p)
    assert d.magnet.currentText() == "magnet_A" and d.result is not None
    assert d.result["coef"][1] == pytest.approx(SLOPE, rel=2e-3) and "Oe/A" in d.summary.text()
    from PyQt6 import QtWidgets as W
    W.QMessageBox.information = lambda *a, **k: None
    d.save()
    conf = dict(labfile.configured(p))
    cal = conf["magnet_A"]["field_cal"]
    assert cal["method"] == "yig" and cal["coef"][1] == pytest.approx(SLOPE, rel=2e-3) and cal["file"].endswith("yig.nc")
    assert "field" in st.instruments["magnet_A"].parameters
    W.QMessageBox.question = lambda *a, **k: W.QMessageBox.StandardButton.Yes
    d.clear()
    assert "field_cal" not in dict(labfile.configured(p))["magnet_A"]
    assert "field" not in st.instruments["magnet_A"].parameters
    d.close()
    st.close()
