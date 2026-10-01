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
