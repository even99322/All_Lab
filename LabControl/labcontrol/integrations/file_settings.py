"""從數據檔讀回量測設定，套用到量測方案（讀檔模塊 / 平台把數據檔拖到量測模塊時用）。

支援：

* Lab Control 原生檔（.lm.h5 / .hdf5，attrs["metadata"]）：裡面有完整的量測方案 → 直接開成方案。
* Lab Control 匯出的 Labber 檔：0.0.13 起在檔案根目錄加了 attrs["labcontrol"]（同樣的 metadata）→ 直接開成方案。
* 其他 Labber 檔（舊的 sweep_main、Labber Measurement）：讀 Step list / Step config / Instrument config，
  把 VNA 設定（頻率、點數、功率、IF 頻寬、平均）與掃描範圍套到目前方案裡名稱對得上的方塊。
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..scheme.model import Block, Scheme

#: Labber VNA 驅動的設定名稱 → 量測方塊 settings 的鍵與顯示單位
VNA_KEYS: List[Tuple[Tuple[str, ...], str, str]] = [
    (("Start frequency", "Start Frequency", "Frequency start"), "start_freq", "GHz"),
    (("Stop frequency", "Stop Frequency", "Frequency stop"), "stop_freq", "GHz"),
    (("# of points", "Number of points", "Points"), "points", ""),
    (("Output power", "Power", "Source power"), "power", "dBm"),
    (("IF bandwidth", "IF Bandwidth", "IF BW"), "if_bw", "kHz"),
    (("# of averages", "Number of averages", "Averages"), "averages", ""),
]
SCALE = {"": 1.0, "A": 1.0, "mA": 1e-3, "uA": 1e-6, "µA": 1e-6, "V": 1.0, "mV": 1e-3, "Hz": 1.0, "kHz": 1e3,
         "MHz": 1e6, "GHz": 1e9, "dBm": 1.0, "dB": 1.0, "T": 1.0, "mT": 1e-3, "s": 1.0, "ms": 1e-3}


@dataclass
class StepInfo:
    channel: str
    unit: str = ""
    start: Optional[float] = None
    stop: Optional[float] = None
    points: int = 1
    single: Optional[float] = None

    @property
    def is_sweep(self) -> bool:
        return self.points > 1 and self.start is not None and self.stop is not None


@dataclass
class FileSettings:
    path: Path
    source: str = "unknown"                 # labcontrol | labber | unknown
    scheme: Optional[Dict[str, Any]] = None
    steps: List[StepInfo] = field(default_factory=list)
    instruments: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    comment: str = ""

    def summary(self) -> str:
        if self.scheme:
            return f"Lab Control 量測方案「{self.scheme.get('name', '')}」"
        sw = [s for s in self.steps if s.is_sweep]
        parts = [f"{s.channel} {_fmt(s.start)}→{_fmt(s.stop)} {s.unit}（{s.points} 點）" for s in sw]
        return "Labber：" + ("；".join(parts) if parts else "沒有掃描") + \
            (f"；儀器 {len(self.instruments)} 台" if self.instruments else "")


def _fmt(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.6g}"


def _s(v: Any) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)


def _py(v: Any) -> Any:
    try:
        import numpy as np

        if isinstance(v, np.generic):
            return v.item()
        if isinstance(v, np.ndarray):
            return v.tolist()
    except ImportError:  # pragma: no cover
        pass
    return _s(v) if isinstance(v, (bytes, bytearray)) else v


def _meta_scheme(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sch = (meta.get("experiment") or {}).get("scheme")
    return sch if isinstance(sch, dict) and ("blocks" in sch or "graph" in sch) else None


def read_file_settings(path: str | Path) -> FileSettings:
    import h5py

    path = Path(path)
    fs = FileSettings(path)
    with h5py.File(path, "r") as f:
        for key in ("labcontrol", "metadata"):
            if key in f.attrs:
                try:
                    meta = json.loads(_s(f.attrs[key]) or "{}")
                except ValueError:
                    continue
                sch = _meta_scheme(meta)
                if sch:
                    fs.source, fs.scheme = "labcontrol", sch
                    out = (meta.get("experiment") or {}).get("output") or {}
                    tags = out.get("tags") or next((e.get("tags") for e in out.get("export") or []
                                                    if isinstance(e, dict) and e.get("tags")), [])
                    fs.tags = [str(t) for t in tags or []]
                    break
        if "Step list" in f:
            if fs.source == "unknown":
                fs.source = "labber"
            fs.steps = _labber_steps(f)
            fs.instruments = _labber_instruments(f)
            if "Tags" in f.attrs:
                fs.tags = fs.tags or [t.strip() for t in re.split(r"[,;]", _s(f.attrs["Tags"])) if t.strip()]
            fs.comment = _s(f.attrs.get("comment", ""))
    return fs


def _labber_steps(f) -> List[StepInfo]:
    import numpy as np

    rows = f["Step list"][()]
    names = [_s(r["channel_name"]) for r in rows] if rows.dtype.names else [_s(r) for r in rows]
    units: Dict[str, str] = {}
    if "Channels" in f and f["Channels"].dtype.names:
        for r in f["Channels"][()]:
            nm = _s(r["name"])
            for k in ("unitPhys", "unit", "unit_phys"):
                if k in f["Channels"].dtype.names:
                    units[nm] = _s(r[k])
                    break
    out: List[StepInfo] = []
    for n in names:
        st = StepInfo(n, units.get(n, ""))
        grp = f.get(f"Step config/{n}/Step items")
        if grp is not None and grp.dtype.names:
            items = grp[()]
            fields = grp.dtype.names
            vals: List[float] = []
            for it in items:
                rt = int(it["range_type"]) if "range_type" in fields else 1
                if rt == 0:
                    st.single = float(it["single"])
                    vals.append(st.single)
                    continue
                if rt == 2 and "center" in fields:
                    a = float(it["center"]) - float(it["span"]) / 2
                    b = float(it["center"]) + float(it["span"]) / 2
                else:
                    a, b = float(it["start"]), float(it["stop"])
                npts = int(it["n_pts"]) if "n_pts" in fields else 0
                if (not npts or ("step_type" in fields and int(it["step_type"]) == 0)) and "step" in fields \
                        and float(it["step"]):
                    npts = int(round(abs(b - a) / abs(float(it["step"])))) + 1
                vals += list(np.linspace(a, b, max(npts, 1)))
            if vals:
                st.start, st.stop, st.points = float(vals[0]), float(vals[-1]), len(vals)
        if st.start is None and "Data" in f and "Channel names" in f["Data"]:
            st = _from_data(f, st)
        out.append(st)
    return out


def _from_data(f, st: StepInfo) -> StepInfo:
    import numpy as np

    try:
        cn = [_s(r[0] if isinstance(r, (tuple, np.void)) else r) for r in f["Data/Channel names"][()]]
        i = cn.index(st.channel)
        d = f["Data/Data"][:, i, ...]
        u = np.unique(np.round(np.asarray(d, float).ravel(), 15))
        u = u[np.isfinite(u)]
        if u.size:
            st.start, st.stop, st.points = float(u[0]), float(u[-1]), int(u.size)
    except (KeyError, ValueError, IndexError):
        pass
    return st


def _labber_instruments(f) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    g = f.get("Instrument config")
    if g is None:
        return out
    for name in g:
        cfg = {k: _py(v) for k, v in g[name].attrs.items()}
        if hasattr(g[name], "keys"):
            for sub in g[name]:
                d = g[name][sub]
                if hasattr(d, "shape") and d.shape == ():
                    cfg[sub] = _py(d[()])
        out[name] = cfg
    return out


# ---- 套用 --------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _with_unit(v: float, unit: str) -> Any:
    if not unit:
        return int(v) if float(v).is_integer() else v
    return f"{v / SCALE.get(unit, 1.0):.10g} {unit}"


def _vna_config(fs: FileSettings) -> Optional[Dict[str, Any]]:
    for name, cfg in fs.instruments.items():
        if any(k in cfg for keys, _, _ in VNA_KEYS for k in keys[:1]) or re.search(r"vna|znb|zna|network", name, re.I):
            return cfg
    return None


def apply_to_scheme(scheme: Scheme, fs: FileSettings) -> List[str]:
    """把 Labber 檔的設定套到 scheme（原地修改）；回傳改了哪些東西（給使用者看）。"""
    changes: List[str] = []
    vna = _vna_config(fs)
    # 流程圖方案：方塊存在 graph.nodes（scheme.blocks 是由圖產生的副本）
    blocks = list(scheme.graph.nodes.values()) if scheme.graph is not None else list(_walk(scheme.blocks))
    if vna:
        for b in blocks:
            if b.kind != "measure" or not re.search(r"vna", b.instrument, re.I):
                continue
            for keys, key, unit in VNA_KEYS:
                v = next((vna[k] for k in keys if k in vna), None)
                if v is None:
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(fv):
                    continue
                new = _with_unit(fv, unit)
                if b.settings.get(key) != new:
                    b.settings[key] = new
                    changes.append(f"{b.instrument} {key} = {new}")
    for st in fs.steps:
        if not st.is_sweep:
            continue
        for b in blocks:
            if not b.is_loop:
                continue
            names = {_norm(b.axis_name), _norm(b.target), _norm(b.title)} - {""}
            if _norm(st.channel) not in names and not any(n and n in _norm(st.channel) for n in names):
                continue
            k = SCALE.get(b.unit, 1.0) if st.unit in ("", "A", "V", "Hz", "T", "s") else 1.0
            b.start, b.stop = round(st.start / k, 12), round(st.stop / k, 12)
            b.points, b.step = st.points, None
            changes.append(f"{b.axis_name or b.target}：{b.start:g} → {b.stop:g} {b.unit}（{st.points} 點）")
            break
    if scheme.graph is not None and changes:
        scheme.blocks = scheme.graph.to_blocks()
    return changes


def _walk(blocks: List[Block]):
    for b in blocks:
        yield b
        yield from _walk(b.children)


def scheme_from_file(fs: FileSettings) -> Optional[Scheme]:
    if not fs.scheme:
        return None
    return Scheme.from_dict(fs.scheme)
