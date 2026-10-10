"""讀檔介面：logview 與分析程式透過 open_dataset(path) 取得統一的 Dataset。

新的檔案格式 = 新增一個 @register_reader 類別，讀檔端程式不用改。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from ...core.errors import LabControlError
from ...core.registry import READERS, register_reader
from ..dataset import AxisSpec, ChannelSpec, Dataset, PointRecord


class Reader:
    def can_read(self, path: Path) -> bool:
        raise NotImplementedError

    def read(self, path: Path) -> Dataset:
        raise NotImplementedError


def _s(v) -> str:
    return v.decode("utf-8") if isinstance(v, bytes) else str(v)


@register_reader("hdf5")
class NativeHDF5Reader(Reader):
    def can_read(self, path: Path) -> bool:
        try:
            import h5py

            with h5py.File(path, "r") as f:
                return _s(f.attrs.get("schema", "")).startswith(("labcontrol/", "labmaster/"))
        except Exception:  # noqa: BLE001
            return False

    def read(self, path: Path) -> Dataset:
        import h5py

        with h5py.File(path, "r") as f:
            axes = []
            for k in sorted(f["axes"].keys(), key=int):
                d = f["axes"][k]
                a = d.attrs
                axes.append(AxisSpec(_s(a["name"]), _s(a["target"]), _s(a["unit"]), d[:],
                                     _s(a["display_unit"]), float(a["display_scale"])))
            channels: List[ChannelSpec] = []
            for g in f["channels"].values():
                a = g.attrs
                channels.append(ChannelSpec(
                    name=_s(a["name"]), unit=_s(a["unit"]), vector=bool(a["vector"]), complex=bool(a["complex"]),
                    x_name=_s(a["x_name"]), x_unit=_s(a["x_unit"]),
                    x_values=g["x"][:] if "x" in g else None, export_name=_s(a["export_name"]) or None,
                    source=_s(a["source"]), labber_group=_s(a.get("labber_group", "")),
                    labber_settings=json.loads(_s(a.get("labber_settings", "[]"))),
                ))
            ds = Dataset(_s(f.attrs["name"]), axes, channels, json.loads(_s(f.attrs.get("metadata", "{}"))))
            n = f["setpoints"].shape[0]
            sp = f["setpoints"][:]
            data = {c.name: f["channels"][g_key]["data"][:] for c, g_key in zip(channels, f["channels"].keys())}
            shots_grp = f.get("shots")
            for i in range(n):
                sel = int(f["selected"][i])
                retained = bool(f["retained"][i])
                if shots_grp is not None and str(i) in shots_grp:
                    sg = shots_grp[str(i)]
                    keys = list(f["channels"].keys())
                    m = sg[keys[0]].shape[0]
                    shots = [{c.name: sg[k][j] for c, k in zip(channels, keys)} for j in range(m)]
                else:
                    shots = [{c.name: data[c.name][i] for c in channels}]
                    sel = 0
                notes = json.loads(_s(f["notes"][i]) or "{}")
                ds.add(PointRecord(index=i, setpoints={a.name: float(sp[i, j]) for j, a in enumerate(axes)},
                                   shots=shots, selected=sel, retained=retained,
                                   timestamp=float(f["timestamps"][i]), notes=notes))
        return ds


@register_reader("labber")
class LabberReader(Reader):
    """TODO(v0.4)：直接用 h5py 讀 Labber log（不需 Labber API），讓 logview 同時支援新舊檔。"""

    def can_read(self, path: Path) -> bool:
        try:
            import h5py

            with h5py.File(path, "r") as f:
                return "Data" in f and "Step list" in f
        except Exception:  # noqa: BLE001
            return False

    def read(self, path: Path) -> Dataset:
        raise NotImplementedError("LabberReader 尚未實作（見 docs/ROADMAP.md v0.4）")


def open_dataset(path: str | Path) -> Dataset:
    path = Path(path)
    for name in READERS.names():
        reader = READERS.get(name)()
        if reader.can_read(path):
            return reader.read(path)
    raise LabControlError(f"沒有 reader 能讀取 {path}")


@register_reader("netcdf")
class NetCDFReader(Reader):
    """Lab Control 的 xarray .nc（schema labcontrol-nc/…）→ Dataset（只含量到的點；每點一個 shot）。"""

    def can_read(self, path: Path) -> bool:
        try:
            import h5py

            with h5py.File(path, "r") as f:
                return _s(f.attrs.get("schema", "")).startswith("labcontrol-nc/")
        except Exception:  # noqa: BLE001
            return False

    def read(self, path: Path) -> Dataset:
        import numpy as np
        import xarray as xr

        with xr.open_dataset(path, engine="h5netcdf") as x:
            x = x.load()
        grid = x.attrs.get("layout", "grid") == "grid"
        axes: List[AxisSpec] = []
        axis_vars = []
        for name, c in x.coords.items():
            if "target" not in c.attrs:
                continue
            scale = float(c.attrs.get("si_scale", 1.0)) or 1.0
            vals = np.unique(np.asarray(c.values, dtype=float)) if not grid else np.asarray(c.values, dtype=float)
            axes.append(AxisSpec(str(c.attrs.get("long_name", name)), str(c.attrs.get("target", "")),
                                 str(c.attrs.get("si_units", "")), vals / scale, str(c.attrs.get("units", "")), scale))
            axis_vars.append(name)
        skip = {"timestamp", "retained"}
        channels: List[ChannelSpec] = []
        chan_vars = []
        for name, v in x.data_vars.items():
            if name in skip:
                continue
            extra = [d for d in v.dims if d not in axis_vars and d != "point"]
            xd = extra[0] if extra else None
            channels.append(ChannelSpec(
                name=str(v.attrs.get("channel", name)), unit=str(v.attrs.get("units", "")), vector=xd is not None,
                complex=np.iscomplexobj(v.values), x_name=str(x[xd].attrs.get("long_name", xd)) if xd else "",
                x_unit=str(x[xd].attrs.get("units", "")) if xd else "",
                x_values=np.asarray(x[xd].values, dtype=float) if xd else None,
                export_name=str(v.attrs.get("long_name", "")) or None, source=str(v.attrs.get("source", ""))))
            chan_vars.append(name)
        meta = json.loads(x.attrs.get("labcontrol") or "{}")
        ds = Dataset(str(x.attrs.get("name", Path(path).stem)), axes, channels, meta)
        ts = x["timestamp"] if "timestamp" in x else None
        if grid:
            shape = tuple(len(a.values) for a in axes)
            cells = [()] if not shape else list(np.ndindex(*shape))
            if ts is not None:
                cells = [c for c in cells if np.isfinite(ts.values[c])]
            cells.sort(key=lambda c: float(ts.values[c]) if ts is not None else 0.0)
        else:
            cells = [(i,) for i in range(x.sizes.get("point", 0))]
        for i, cell in enumerate(cells):
            if grid:
                sp = {a.name: float(a.values[k]) for a, k in zip(axes, cell)}
            else:
                sp = {a.name: float(x[av].values[cell[0]]) / a.display_scale for a, av in zip(axes, axis_vars)}
            shot = {c.name: np.asarray(x[cv].values[cell]) for c, cv in zip(channels, chan_vars)}
            ret = bool(x["retained"].values[cell]) if "retained" in x else False
            ds.add(PointRecord(index=i, setpoints=sp, shots=[shot], selected=0, retained=ret,
                               timestamp=float(ts.values[cell]) if ts is not None else 0.0))
        return ds
