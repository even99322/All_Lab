"""1.0.21：網頁預覽讀得懂 Fortran 順序（轉置過）的 2D 預覽。"""
import io

import numpy as np


def test_read_npy_fortran_order():
    from labhub.data_routes import read_npy
    z = np.arange(12, dtype=np.float32).reshape(3, 4)
    for arr in (np.asfortranarray(z), z.T.copy().T, z):
        buf = io.BytesIO()
        np.save(buf, arr)
        kind, shape, a = read_npy(buf.getvalue())
        assert shape == (3, 4) and list(a) == list(z.ravel())
    col = np.asfortranarray(np.arange(5, dtype=np.float64).reshape(5, 1))
    buf = io.BytesIO()
    np.save(buf, col)
    assert list(read_npy(buf.getvalue())[2]) == [0, 1, 2, 3, 4]


def test_preview_json_with_transposed_z():
    import zipfile, json
    from labhub.data_routes import preview_json
    z = np.asfortranarray(np.arange(6, dtype=np.float32).reshape(2, 3))
    meta = np.frombuffer(json.dumps({"kind": "2d"}).encode(), dtype=np.uint8)
    buf = io.BytesIO()
    np.savez(buf, x=np.arange(3.0), y=np.arange(2.0), z=z, meta=meta)
    out = preview_json(buf.getvalue())
    import base64
    got = np.frombuffer(base64.b64decode(out["z"]), dtype=np.float32)
    assert out["ny"] == 2 and out["nx"] == 3 and list(got) == [0, 1, 2, 3, 4, 5]


def test_data_page_puts_frequency_on_y():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "labhub" / "static" / "mdata.html").read_text(encoding="utf-8")
    assert "function isFreq" in html and 'id="swapBtn"' in html and "transpose()" in html


def test_complex_preview_round_trip():
    """1.0.23：2D 預覽另存複數原始資料（跟 dB 選同一個點），API 要 complex=1 才送。"""
    import base64
    from types import SimpleNamespace
    from labhub.index_codec import _reduce, encode_preview
    from labhub.data_routes import preview_json

    rng = np.random.default_rng(1)
    cz = (rng.normal(size=(30, 3000)) + 1j * rng.normal(size=(30, 3000))) * 0.01 + 1
    cz[7, 1234] = 0.001 + 0j                                         # 窄的 dip
    db = 20 * np.log10(np.abs(cz))
    out, x, ce = _reduce(db, np.arange(3000.0), 1, 800, cz)
    assert np.allclose(20 * np.log10(np.abs(ce)), out)                # 同一個點
    data = SimpleNamespace(z_values=db, x_values=np.arange(3000.0), y_values=np.arange(30.0), x_name="f", x_unit="Hz",
                           y_name="I", y_unit="A", z_name="S21", z_unit="dB", transform="magnitude_db")
    pv = SimpleNamespace(kind="2d", log_name="t", channel_name="S21", colormap="LabLog BWR", z_min=None, z_max=None,
                         metadata=None, data=data)
    blob = encode_preview(pv, cz)
    plain = preview_json(blob)
    assert plain["complex"] and "zre" not in plain
    full = preview_json(blob, with_complex=True)
    zre = np.frombuffer(base64.b64decode(full["zre"]), dtype=np.float32)
    zim = np.frombuffer(base64.b64decode(full["zim"]), dtype=np.float32)
    zdb = np.frombuffer(base64.b64decode(full["z"]), dtype=np.float32)
    assert zre.size == zdb.size and np.allclose(20 * np.log10(np.hypot(zre, zim)), zdb, atol=1e-3)
    assert zdb.min() < -50                                            # dip 留著
    assert not preview_json(encode_preview(pv))["complex"]            # 沒有原始資料


def test_data_page_views_and_markers():
    """1.0.23：網頁數據查看有視圖、背景、色階、放大、截面預設 trace、標記。"""
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "labhub" / "static" / "mdata.html").read_text(encoding="utf-8")
    for needle in ('id="vTf"', 'id="vBg"', 'id="vCmap"', 'id="zoomBtn"', 'id="cutTrace"', 'id="cutBoth"', 'id="mkMode"',
                   'id="mkTable"', "complex=1", "function traceKind", "uphase"):
        assert needle in html, needle
