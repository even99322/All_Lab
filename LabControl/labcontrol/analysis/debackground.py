"""去背（1.0.2）：主數據的每一條 trace 除以背景 trace（複數相除），跟讀檔程式的「去背」相同。

    result = debackground_dataset(main, background)

* 只處理複數向量通道（例如 VNA 的 S21）；其他通道照抄。
* 背景取背景數據的第一個點；兩邊的頻率點不一樣時，把背景（實部、虛部）內插到主數據的頻率上。
* 背景為 0 或不是有限值的點 → 結果為 NaN（不會整個失敗）。
"""
from __future__ import annotations

import copy
from typing import Dict, Optional

import numpy as np

from ..core.errors import LabControlError
from ..data.dataset import Dataset, PointRecord


class DebackgroundError(LabControlError):
    pass


def _x(ch, n: int) -> Optional[np.ndarray]:
    x = getattr(ch, "x_values", None)
    if x is None:
        return None
    x = np.asarray(x, dtype=float).reshape(-1)
    return x if x.size == n else None


def background_traces(main: Dataset, background: Dataset) -> Dict[str, np.ndarray]:
    """{通道: 對齊主數據頻率的背景 trace}。"""
    if not len(background):
        raise DebackgroundError("背景數據是空的")
    rec = background.records[0].data
    out: Dict[str, np.ndarray] = {}
    for ch in main.channels:
        if not (ch.vector and ch.complex) or ch.name not in rec:
            continue
        bg = np.asarray(rec[ch.name], dtype=complex).reshape(-1)
        n = None
        for r in main.records:
            if ch.name in r.data:
                n = np.asarray(r.data[ch.name]).reshape(-1).size
                break
        if n is None:
            continue
        xm = _x(ch, n)
        xb = _x(_bgch(background, ch.name), bg.size)
        same = bg.size == n and (xm is None or xb is None or
                                 np.allclose(xm, xb, rtol=0, atol=max(1e-9 * float(np.abs(xm).max()), 1e-6)))
        if not same:
            if xm is None or xb is None:
                raise DebackgroundError(f"{ch.name}：背景 {bg.size} 點、主數據 {n} 點，又沒有頻率可以對齊")
            order = np.argsort(xb)
            bg = np.interp(xm, xb[order], bg.real[order]) + 1j * np.interp(xm, xb[order], bg.imag[order])
        out[ch.name] = bg
    if not out:
        raise DebackgroundError("背景與主數據沒有共同的複數 trace 通道（例如 S21）")
    return out


def _bgch(ds: Dataset, name: str):
    try:
        return ds.channel(name)
    except KeyError:
        return None


def debackground_dataset(main: Dataset, background: Dataset) -> Dataset:
    traces = background_traces(main, background)
    bad = {k: ~(np.isfinite(v) & (v != 0)) for k, v in traces.items()}
    safe = {k: np.where(bad[k], 1.0, v) for k, v in traces.items()}
    meta = copy.deepcopy(main.metadata)
    meta["debackground"] = {"background": background.name, "channels": sorted(traces),
                            "method": "complex division (target / background)"}
    out = Dataset(main.name, main.axes, main.channels, meta)
    for r in main.records:
        shots = []
        for shot in r.shots:
            new = dict(shot)
            for name, bg in safe.items():
                if name in shot:
                    v = np.asarray(shot[name], dtype=complex).reshape(-1) / bg
                    v[bad[name]] = np.nan
                    new[name] = v.reshape(np.shape(shot[name]))
            shots.append(new)
        out.add(PointRecord(index=r.index, setpoints=dict(r.setpoints), shots=shots, selected=r.selected,
                            retained=r.retained, timestamp=r.timestamp, notes=dict(r.notes)))
    return out
