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
