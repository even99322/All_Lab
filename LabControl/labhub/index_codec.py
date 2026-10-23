"""數據索引的預覽格式（1.0.4）：讀檔程式的 QuickPreviewData ↔ 一個小的 .npz。

NAS 上的索引程式（labhub/indexer.py）把每個檔的預設預覽存成這個格式；讀檔程式（app/core/hub_index.py）讀回來
直接顯示，不必隔著 VPN 開 HDF5。內容：

    meta  JSON（kind、log_name、channel、名稱 / 單位、transform、colormap、範圍、acquisition、metadata 區塊）
    x, y  座標；z（2D）
2D 最多 PREVIEW_X × PREVIEW_Y 點、1D 最多 PREVIEW_1D 點：縮小時每一格保留「離中位數最遠」的值，窄的 dip / peak 不會被抹掉。
需要 numpy（只有索引程式與讀檔程式用；Hub 本身只轉送 bytes）。
"""
from __future__ import annotations

import io
import json
import math
from typing import Any, Dict, Tuple

import numpy as np

FORMAT = 1
PREVIEW_X = 800
PREVIEW_Y = 400
PREVIEW_1D = 4000


def _reduce(z: np.ndarray, coords: np.ndarray, axis: int, limit: int, extra=None):
    """沿 axis 縮成最多 limit 格；每格取離整體中位數最遠的值，座標取那一格的平均。
    extra（1.0.23）：同形狀的複數原始資料，取「同一格同一個點」（跟 z 選的點一致）；有給就多回傳一個。"""
    n = z.shape[axis]
    k = int(math.ceil(n / max(1, limit)))
    if k <= 1:
        return (z, coords) if extra is None else (z, coords, extra)
    m = int(math.ceil(n / k))
    pad = m * k - n
    zz = np.moveaxis(z, axis, -1).astype(np.float64, copy=False)
    if pad:
        zz = np.concatenate([zz, np.full(zz.shape[:-1] + (pad,), np.nan)], axis=-1)
    blocks = zz.reshape(zz.shape[:-1] + (m, k))
    finite = blocks[np.isfinite(blocks)]
    center = float(np.median(finite)) if finite.size else 0.0
    dev = np.where(np.isfinite(blocks), np.abs(blocks - center), -1.0)
    pick = np.argmax(dev, axis=-1)
    out = np.take_along_axis(blocks, pick[..., None], axis=-1)[..., 0]
    c = np.asarray(coords, dtype=np.float64)
    if pad:
        c = np.concatenate([c, np.full(pad, np.nan)])
    with np.errstate(invalid="ignore"):
        cc = np.nanmean(c.reshape(m, k), axis=1)
    if extra is None:
        return np.moveaxis(out, -1, axis), cc
    ee = np.moveaxis(np.asarray(extra, dtype=np.complex128), axis, -1)
    if pad:
        ee = np.concatenate([ee, np.full(ee.shape[:-1] + (pad,), np.nan + 0j)], axis=-1)
    eo = np.take_along_axis(ee.reshape(ee.shape[:-1] + (m, k)), pick[..., None], axis=-1)[..., 0]
    return np.moveaxis(out, -1, axis), cc, np.moveaxis(eo, -1, axis)


def _real(values: Any) -> np.ndarray:
    a = np.asarray(values)
    if np.iscomplexobj(a):
        a = np.abs(a)
    return a.astype(np.float64, copy=False)


def _json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    if isinstance(obj, np.ndarray):
        return _json(obj.tolist())
    return str(obj)


def encode_preview(preview: Any, raw: Any = None) -> bytes:
    """QuickPreviewData → bytes。raw（1.0.23）：2D 複數原始資料（跟 z 同形狀）→ 另存 zre / zim，網頁可以換成相位、實部、虛部…"""
    data = preview.data
    acq = getattr(preview, "acquisition", None)
    meta: Dict[str, Any] = {
        "format": FORMAT, "kind": preview.kind, "log_name": preview.log_name, "channel_name": preview.channel_name,
        "colormap": preview.colormap, "z_min": preview.z_min, "z_max": preview.z_max,
        "metadata": preview.metadata,
        "acquisition": None if acq is None else {
            "channel_name": acq.channel_name, "nominal_entries": acq.nominal_entries,
            "acquired_entries": acq.acquired_entries, "state": acq.state, "reason": acq.reason},
        "x_name": data.x_name, "x_unit": data.x_unit, "z_name": data.z_name, "z_unit": data.z_unit,
        "transform": data.transform, "fixed_dims": getattr(data, "fixed_dims", {}) or {},
    }
    arrays: Dict[str, np.ndarray] = {}
    if preview.kind == "2d":
        z = _real(data.z_values)
        x = np.asarray(data.x_values, dtype=np.float64)
        y = np.asarray(data.y_values, dtype=np.float64)
        cz = None
        if raw is not None:
            cz = np.asarray(raw)
            if cz.shape != z.shape or not np.iscomplexobj(cz):
                cz = None
        if cz is None:
            z, x = _reduce(z, x, 1, PREVIEW_X)
            z, y = _reduce(z, y, 0, PREVIEW_Y)
        else:
            z, x, cz = _reduce(z, x, 1, PREVIEW_X, cz)
            z, y, cz = _reduce(z, y, 0, PREVIEW_Y, cz)
            arrays["zre"], arrays["zim"] = cz.real.astype(np.float32), cz.imag.astype(np.float32)
            meta["complex"] = True
        meta.update(y_name=data.y_name, y_unit=data.y_unit, operation_label=getattr(data, "operation_label", ""),
                    display_unit=getattr(data, "display_unit", None), shape=list(np.asarray(data.z_values).shape))
        arrays.update(x=x.astype(np.float64), y=y.astype(np.float64), z=z.astype(np.float32))
    else:
        y = _real(data.y_values)
        x = np.asarray(data.x_values, dtype=np.float64)
        y, x = _reduce(y, x, 0, PREVIEW_1D)
        meta["shape"] = [int(np.asarray(data.y_values).size)]
        arrays.update(x=x, y=y.astype(np.float32))
    arrays = {k: np.ascontiguousarray(v) for k, v in arrays.items()}   # 1.0.21：轉置過的資料存成 C 順序（Hub 讀得最快）
    arrays["meta"] = np.frombuffer(json.dumps(_json(meta), ensure_ascii=False).encode("utf-8"), dtype=np.uint8)
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    return buf.getvalue()


def decode_arrays(blob: bytes) -> Tuple[Dict[str, Any], Dict[str, np.ndarray]]:
    """bytes → (meta, {x, y, z})；不需要讀檔程式的類別（測試、其他工具用）。"""
    with np.load(io.BytesIO(blob), allow_pickle=False) as npz:
        meta = json.loads(bytes(npz["meta"]).decode("utf-8"))
        arrays = {k: npz[k] for k in npz.files if k != "meta"}
    if int(meta.get("format", 0)) != FORMAT:
        raise ValueError(f"預覽格式 {meta.get('format')} 不支援")
    return meta, arrays
