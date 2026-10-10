"""xarray / NetCDF4 匯出（.nc，0.0.20 起取代 Labber）。

讀法（任何電腦、任何分析腳本，一行）::

    import xarray as xr
    ds = xr.open_dataset("run.nc", engine="h5netcdf")
    ds["S21"].sel(magnet_A=120, method="nearest").plot()       # 用軸的名字切片、畫圖
    abs(ds["S21"]).plot()                                      # 2D 圖，軸名、刻度、單位自動帶上
    ds.attrs["tags"], ds.attrs["project"]                      # 標籤、論文庫專案…

檔案結構（xarray.Dataset）：

- 座標：每個掃描軸一個（名稱 = 軸名稱，數值用顯示單位，例如 mA；``attrs: units, long_name, target, si_scale``）；
  向量通道的 x 軸也是座標（例如 ``frequency``，Hz）。
- 變數：每個量測通道一個，維度 = (掃描軸…, x 軸)；複數直接存成複數（需要 ``engine="h5netcdf"``）。
  沒量到的點是 NaN。另外 ``timestamp``（每點時間，Unix 秒）、``retained``（手動保留點）。
- 點不在格子上（非規則掃描）時，改成一維 ``point`` 維度，各軸的實際設定值當成沿 ``point`` 的座標。
- 全域屬性：``schema``、``name``、``created``、``user``、``tags``（逗號分隔）、``project``（論文庫專案名稱）、
  ``project_id``、``comment``、``complete``，以及 ``labcontrol``（JSON：完整量測方案與儀器設定，
  拖回量測模塊可以套用同樣的設定）。

檔案本身是 HDF5（h5netcdf），h5py 也讀得到；沒有 Labber 依賴。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ...core.registry import register_writer
from ..dataset import Dataset
from .base import Writer
from .hdf5_native import dumps

SCHEMA = "labcontrol-nc/1"


def var_name(name: str, used: Optional[set] = None) -> str:
    """通道 / 軸名稱 → xarray 變數名（英數與底線；重複時加 _2、_3）。"""
    n = re.sub(r"[^0-9A-Za-z_]+", "_", str(name or "")).strip("_") or "var"
    if n[0].isdigit():
        n = "v_" + n
    if used is not None:
        base, k = n, 2
        while n in used:
            n, k = f"{base}_{k}", k + 1
        used.add(n)
    return n


def _grid_index(ds: Dataset) -> Optional[List[Tuple[int, ...]]]:
    """每個點落在掃描格子的哪一格；有點不在格子上（或兩點同一格）→ None（改用 point 維度）。"""
    if not ds.axes:
        return [()] * len(ds.records)
    idx: List[Tuple[int, ...]] = []
    seen = set()
    for r in ds.records:
        cell = []
        for a in ds.axes:
            vals = np.asarray(a.values, dtype=float)
            v = float(r.setpoints[a.name])
            k = int(np.argmin(np.abs(vals - v)))
            tol = 1e-9 * max(1.0, abs(v)) + (np.min(np.abs(np.diff(vals))) * 1e-6 if len(vals) > 1 else 0)
            if abs(vals[k] - v) > tol:
                return None
            cell.append(k)
        t = tuple(cell)
        if t in seen:
            return None
        seen.add(t)
        idx.append(t)
    return idx


@register_writer("netcdf")
class NetCDFWriter(Writer):
    extension = ".nc"

    def __init__(self, user: str = "", tags: Optional[Sequence[str]] = None, comment: str = "",
                 project: str = "", **_ignored: Any) -> None:
        self.user = user or ""
        self.tags = [str(t) for t in (tags or [])]
        self.comment = comment or ""
        self.project = project or ""

    def to_xarray(self, ds: Dataset):
        """Dataset → xarray.Dataset（也給分析程式直接用，不必先存檔）。"""
        import xarray as xr

        used: set = set()
        grid = _grid_index(ds)
        coords: Dict[str, Any] = {}
        axis_dims: List[str] = []
        for a in ds.axes:
            d = var_name(a.name, used)
            axis_dims.append(d)
            coords[d] = (a.to_display(np.asarray(a.values, dtype=float)),
                         {"units": a.display_unit or a.unit, "long_name": a.name, "target": a.target,
                          "si_units": a.unit, "si_scale": float(a.display_scale)})
        n = len(ds.records)
        data_vars: Dict[str, Any] = {}
        if grid is not None:
            shape = tuple(len(a.values) for a in ds.axes)
            base_dims = tuple(axis_dims)
            out_coords = {d: (d, v, attrs) for d, (v, attrs) in coords.items()}

            def place(values: np.ndarray, fill: Any, dtype: Any) -> np.ndarray:
                tail = values.shape[1:] if values.ndim > 1 else ()
                arr = np.full(shape + tail, fill, dtype=dtype)
                for i, cell in enumerate(grid):
                    arr[cell] = values[i]
                return arr
        else:
            base_dims = ("point",)
            out_coords = {"point": ("point", np.arange(n), {"long_name": "量測點順序"})}
            sp = ds.setpoints() if n else np.zeros((0, len(ds.axes)))
            for k, (d, (_v, attrs)) in enumerate(coords.items()):
                out_coords[d] = ("point", sp[:, k] * ds.axes[k].display_scale if n else np.zeros(0), attrs)

            def place(values: np.ndarray, fill: Any, dtype: Any) -> np.ndarray:
                return np.asarray(values, dtype=dtype)

        x_dims: Dict[Tuple[str, bytes], str] = {}
        for c in ds.channels:
            name = var_name(c.name, used)
            col = ds.column(c.name) if n else np.zeros((0,) + ((len(c.x_values),) if c.vector and c.x_values is not None else ()))
            dtype = complex if c.complex else float
            col = np.asarray(col, dtype=dtype)
            dims = base_dims
            if c.vector:
                xv = np.asarray(c.x_values if c.x_values is not None else np.arange(col.shape[-1] if col.ndim > 1 else 0),
                                dtype=float)
                key = (c.x_name or "x", xv.tobytes())
                if key not in x_dims:
                    xd = var_name(c.x_name or "x", used)
                    x_dims[key] = xd
                    out_coords[xd] = (xd, xv, {"units": c.x_unit, "long_name": c.x_name or "x"})
                dims = base_dims + (x_dims[key],)
                if n == 0:
                    col = col.reshape((0, len(xv)))
            fill = np.nan + 0j if c.complex else np.nan
            data_vars[name] = (dims, place(col, fill, dtype),
                               {"units": c.unit, "long_name": c.export_name or c.name, "source": c.source,
                                "channel": c.name})
        if n:
            data_vars["timestamp"] = (base_dims, place(np.array([r.timestamp for r in ds.records]), np.nan, float),
                                      {"units": "s", "long_name": "量測時間（Unix 秒）"})
            data_vars["retained"] = (base_dims, place(np.array([bool(r.retained) for r in ds.records]), False, bool),
                                     {"long_name": "手動保留點"})
        md = ds.metadata or {}
        out = (md.get("experiment") or {}).get("output") or {}
        lab_project = out.get("lab_project") or {}
        attrs = {"schema": SCHEMA, "name": ds.name, "created": str(md.get("created", "")),
                 "user": self.user, "tags": ", ".join(self.tags), "comment": self.comment,
                 "project": self.project or str(lab_project.get("name") or ""),
                 "project_id": str(lab_project.get("id") or ""),
                 "points": n, "planned_points": int(ds.planned_points),
                 "complete": int(n >= ds.planned_points),
                 "layout": "grid" if grid is not None else "point",
                 "labcontrol": dumps(md)}
        return xr.Dataset(data_vars=data_vars, coords=out_coords, attrs=attrs)

    def export(self, dataset: Dataset, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        x = self.to_xarray(dataset)
        has_complex = any(np.iscomplexobj(v.values) for v in x.data_vars.values())
        tmp = path.with_name(path.name + ".part")
        import warnings

        with warnings.catch_warnings():       # 複數：h5netcdf 會提醒「不是標準 NetCDF」，xarray / h5py 都讀得到
            warnings.simplefilter("ignore", UserWarning)
            x.to_netcdf(tmp, engine="h5netcdf", invalid_netcdf=has_complex)
        tmp.replace(path)
        return path


def open_nc(path: str | Path):
    """讀 Lab Control 的 .nc（等同 xr.open_dataset(path, engine="h5netcdf")，並把 labcontrol 屬性解開）。"""
    import xarray as xr

    ds = xr.open_dataset(path, engine="h5netcdf")
    try:
        ds.attrs["labcontrol_meta"] = json.loads(ds.attrs.get("labcontrol") or "{}")
    except ValueError:
        pass
    return ds
