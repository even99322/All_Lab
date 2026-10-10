"""labcontrol 原生 HDF5 格式（schema "labcontrol/1"），自我描述、可逐點寫入。

檔案結構：
    /                         attrs: schema, name, created, complete, status, metadata(JSON)
    /axes/<k>                 掃描軸數值（SI）  attrs: name, target, unit, display_unit, display_scale
    /setpoints                (N, n_axes)  每一點實際設定值（SI）
    /timestamps               (N,)
    /selected                 (N,)  每點採用的 shot 編號
    /retained                 (N,)  是否為手動保留點
    /notes                    (N,)  JSON 字串（hooks 的註記，例如 dip_count）
    /channels/<ch>/data       (N, n_x) 或 (N,)  每點採用的 shot
    /channels/<ch>/x          向量通道的 x 軸
    /channels/<ch>            attrs: name, unit, vector, complex, x_name, x_unit, export_name, source,
                                     labber_group, labber_settings(JSON)
    /shots/<i>/<ch>           保留點的所有 shot（QC 用）

Labber 匯出、logview、分析腳本都讀這個格式，所以新增儀器/通道不用改讀檔程式。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ...core.registry import register_writer
from ..dataset import Dataset, PointRecord
from .base import Writer

SCHEMA = "labcontrol/1"


def _key(name: str) -> str:
    return re.sub(r"[/\\]", "_", name)


def _json_default(o: Any):
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, complex):
        return [o.real, o.imag]
    return str(o)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=_json_default)


@register_writer("hdf5")
class HDF5Writer(Writer):
    extension = ".h5"

    def __init__(self, path: Optional[str | Path] = None, flush_every: int = 1) -> None:
        self.path = Path(path) if path else None
        self.flush_every = max(1, int(flush_every))
        self._f = None
        self._n = 0

    # ---- 串流 ---------------------------------------------------------------
    def open(self, dataset: Dataset) -> None:
        import h5py

        if self.path is None:
            raise ValueError("HDF5Writer 串流模式需要 path")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = h5py.File(self.path, "w")
        self._init_file(self._f, dataset, resizable=True)
        self._n = 0
        self._f.flush()

    def write_point(self, dataset: Dataset, record: PointRecord) -> None:
        if self._f is None:
            return
        f = self._f
        n = len(dataset)
        i = n - 1
        self._resize(f, n)
        self._write_row(f, dataset, i, record)
        self._n = n
        if n % self.flush_every == 0:
            f.flush()

    def truncate(self, dataset: Dataset, n: int) -> None:
        if self._f is None:
            return
        self._resize(self._f, n)
        if "shots" in self._f:
            for k in list(self._f["shots"].keys()):
                if int(k) >= n:
                    del self._f["shots"][k]
        self._n = n
        self._f.flush()

    def close(self, dataset: Optional[Dataset], status: str = "") -> None:
        if self._f is None:
            return
        try:
            if dataset is not None:
                # 回存 QC 可能修改過的 selected
                self._resize(self._f, len(dataset))
                for i, r in enumerate(dataset.records):
                    self._f["selected"][i] = r.selected if r.selected >= 0 else len(r.shots) + r.selected
            self._f.attrs["complete"] = status == "finished"
            self._f.attrs["status"] = status
        finally:
            self._f.close()
            self._f = None

    # ---- 一次寫出 -------------------------------------------------------------
    def export(self, dataset: Dataset, path: str | Path) -> Path:
        import h5py

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with h5py.File(path, "w") as f:
            self._init_file(f, dataset, resizable=False, n=len(dataset))
            for i, r in enumerate(dataset.records):
                self._write_row(f, dataset, i, r)
            f.attrs["complete"] = True
            f.attrs["status"] = "exported"
        return path

    # ---- 內部 -----------------------------------------------------------------
    def _init_file(self, f, ds: Dataset, resizable: bool, n: int = 0) -> None:
        maxshape = (lambda *s: (None, *s)) if resizable else (lambda *s: (n, *s))
        f.attrs["schema"] = SCHEMA
        f.attrs["name"] = ds.name
        f.attrs["created"] = ds.metadata.get("created", "")
        f.attrs["metadata"] = dumps(ds.metadata)
        f.attrs["complete"] = False
        g = f.create_group("axes")
        for k, a in enumerate(ds.axes):
            d = g.create_dataset(str(k), data=np.asarray(a.values, dtype=float))
            d.attrs.update(name=a.name, target=a.target, unit=a.unit,
                           display_unit=a.display_unit, display_scale=a.display_scale)
        na = len(ds.axes)
        f.create_dataset("setpoints", shape=(n, na), maxshape=maxshape(na), dtype=float)
        f.create_dataset("timestamps", shape=(n,), maxshape=maxshape(), dtype=float)
        f.create_dataset("selected", shape=(n,), maxshape=maxshape(), dtype=int)
        f.create_dataset("retained", shape=(n,), maxshape=maxshape(), dtype=bool)
        import h5py

        f.create_dataset("notes", shape=(n,), maxshape=maxshape(), dtype=h5py.string_dtype())
        cg = f.create_group("channels")
        for c in ds.channels:
            g = cg.create_group(_key(c.name))
            g.attrs.update(name=c.name, unit=c.unit, vector=c.vector, complex=c.complex,
                           x_name=c.x_name, x_unit=c.x_unit, export_name=c.export_name or "",
                           source=c.source, labber_group=getattr(c, "labber_group", "") or "",
                           labber_settings=dumps(c.labber_settings))
            dtype = complex if c.complex else float
            if c.vector:
                nx = len(c.x_values) if c.x_values is not None else 0
                g.create_dataset("x", data=np.asarray(c.x_values if c.x_values is not None else [], dtype=float))
                g.create_dataset("data", shape=(n, nx), maxshape=maxshape(nx), dtype=dtype,
                                 chunks=(1, max(nx, 1)) if resizable else None)
            else:
                g.create_dataset("data", shape=(n,), maxshape=maxshape(), dtype=dtype)

    @staticmethod
    def _resize(f, n: int) -> None:
        for name in ("setpoints", "timestamps", "selected", "retained", "notes"):
            f[name].resize(n, axis=0)
        for g in f["channels"].values():
            g["data"].resize(n, axis=0)

    @staticmethod
    def _write_row(f, ds: Dataset, i: int, r: PointRecord) -> None:
        f["setpoints"][i] = [r.setpoints[a.name] for a in ds.axes]
        f["timestamps"][i] = r.timestamp
        f["selected"][i] = r.selected if r.selected >= 0 else len(r.shots) + r.selected
        f["retained"][i] = r.retained
        f["notes"][i] = dumps(r.notes)
        data = r.data
        for c in ds.channels:
            f["channels"][_key(c.name)]["data"][i] = np.asarray(data[c.name])
        if r.retained and len(r.shots) > 1:
            sg = f.require_group("shots").require_group(str(i))
            for c in ds.channels:
                k = _key(c.name)
                if k in sg:
                    del sg[k]
                sg.create_dataset(k, data=np.stack([np.asarray(s[c.name]) for s in r.shots]))
