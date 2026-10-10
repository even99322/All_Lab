"""EventBus 事件 ↔ JSON（節點把量測事件寫到 NAS，控制端還原後交給同一個監控畫面）。

複數 / 實數陣列用 float32 + base64 壓縮（501 點的 S21 約 5 KB）。
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

import numpy as np

from ..data.dataset import AxisSpec, ChannelSpec
from ..measure.sweep import Axis, SweepPlan

FORWARD = ("run.state", "run.progress", "run.started", "point.shot", "point.committed", "point.rollback",
           "manual.shots", "run.finished", "run.ramp", "run.hook", "log")


def enc_array(a: Any) -> Any:
    arr = np.asarray(a)
    if arr.ndim == 0:
        v = arr.item()
        return {"c": [v.real, v.imag]} if isinstance(v, complex) else v
    if np.iscomplexobj(arr):
        return {"re": _b64(arr.real), "im": _b64(arr.imag), "n": int(arr.size)}
    return {"f": _b64(arr), "n": int(arr.size)}


def dec_array(d: Any) -> Any:
    if isinstance(d, dict):
        if "re" in d:
            return _unb64(d["re"]) + 1j * _unb64(d["im"])
        if "f" in d:
            return _unb64(d["f"])
        if "c" in d:
            return complex(d["c"][0], d["c"][1])
    return d


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype=np.float32).tobytes()).decode("ascii")


def _unb64(s: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype=np.float32).astype(float)


# ---------------------------------------------------------------------------
def encode_event(topic: str, p: Dict[str, Any], runner=None) -> Optional[Dict[str, Any]]:
    if topic not in FORWARD:
        return None
    if topic == "run.started":
        ds = p["dataset"]
        plan = runner.plan if runner is not None else None
        return {"t": topic, "run_id": p.get("run_id"), "name": ds.name,
                "axes": [{"name": a.name, "target": a.target, "unit": a.unit, "values": np.asarray(a.values).tolist(),
                          "display_unit": a.display_unit, "display_scale": a.display_scale,
                          "alternate": bool(getattr(plan.axes[i], "alternate", False)) if plan else False}
                         for i, a in enumerate(ds.axes)],
                "snake": bool(plan.snake) if plan else False,
                "channels": [{"name": c.name, "unit": c.unit, "vector": c.vector, "complex": c.complex,
                              "x_name": c.x_name, "x_unit": c.x_unit,
                              "x_values": enc_array(c.x_values) if c.x_values is not None else None}
                             for c in ds.channels]}
    if topic == "point.shot":
        return {"t": topic, "index": p["index"], "setpoints": p.get("setpoints", {}),
                "shot": {k: enc_array(v) for k, v in p["shot"].items()}}
    if topic == "point.committed":
        rec = p.get("record")
        return {"t": topic, "index": getattr(rec, "index", p.get("index"))}
    if topic == "run.finished":
        ds = p.get("dataset")
        return {"t": topic, "run_id": p.get("run_id"), "status": p.get("status"), "error": p.get("error"),
                "summary": ds.summary() if ds is not None else "", "n": len(ds) if ds is not None else 0}
    out = {"t": topic}
    for k, v in p.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, dict):
            out[k] = {str(kk): (vv if isinstance(vv, (str, int, float, bool)) or vv is None else str(vv))
                      for kk, vv in v.items()}
        else:
            out[k] = str(v)
    return out


class RemoteDataset:
    """控制端用的輕量 Dataset（只有監控畫面需要的欄位）。"""

    def __init__(self, ev: Dict[str, Any]) -> None:
        self.name = ev["name"]
        self.axes = [AxisSpec(a["name"], a["target"], a["unit"], np.asarray(a["values"], float), a["display_unit"],
                              a["display_scale"]) for a in ev["axes"]]
        self.channels = [ChannelSpec(c["name"], c["unit"], c["vector"], c["complex"], c["x_name"], c["x_unit"],
                                     dec_array(c["x_values"]) if c["x_values"] is not None else None)
                         for c in ev["channels"]]
        self.retained: list = []
        self._n = 0
        self._summary = ""
        self.plan = SweepPlan([Axis(a["target"] or a["name"], np.asarray(a["values"], float), a["name"],
                                    alternate=a.get("alternate", False)) for a in ev["axes"]], snake=ev.get("snake", False))

    def __len__(self) -> int:
        return self._n

    def summary(self) -> str:
        return self._summary


def decode_event(ev: Dict[str, Any], ds: Optional[RemoteDataset]) -> tuple:
    """→ (topic, payload, dataset)。run.started 會產生新的 RemoteDataset。"""
    t = ev["t"]
    p = {k: v for k, v in ev.items() if k != "t"}
    if t == "run.started":
        ds = RemoteDataset(ev)
        return t, {"run_id": ev.get("run_id"), "dataset": ds}, ds
    if t == "point.shot":
        p["shot"] = {k: dec_array(v) for k, v in ev["shot"].items()}
    elif t == "run.finished":
        if ds is not None:
            ds._n, ds._summary = ev.get("n", 0), ev.get("summary", "")
        p["dataset"] = ds
    return t, p, ds
