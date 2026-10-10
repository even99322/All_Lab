"""測試用：寫一個 Labber 格式（Step list / Step config / Instrument config / Data）的小檔，不需要 Labber。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np


def write_labber_like(path: str | Path, steps: Sequence[Tuple[str, str, Sequence[float]]],
                      instruments: Dict[str, Dict[str, Any]], tags: Sequence[str] = (),
                      step_config: bool = True) -> Path:
    import h5py

    path = Path(path)
    s = h5py.string_dtype()
    with h5py.File(path, "w") as f:
        f.attrs["Tags"] = ", ".join(tags)
        f.attrs["comment"] = "fake"
        sl = np.array([(n.encode(),) for n, _, _ in steps], dtype=[("channel_name", "S64")])
        f.create_dataset("Step list", data=sl)
        f.create_dataset("Log list", data=np.array([(b"VNA - S21",)], dtype=[("channel_name", "S64")]))
        ch = np.array([(n.encode(), u.encode()) for n, u, _ in steps], dtype=[("name", "S64"), ("unitPhys", "S16")])
        f.create_dataset("Channels", data=ch)
        if step_config:
            dt = [("range_type", "i4"), ("step_type", "i4"), ("single", "f8"), ("start", "f8"), ("stop", "f8"),
                  ("center", "f8"), ("span", "f8"), ("step", "f8"), ("n_pts", "i4")]
            for n, _, vals in steps:
                vals = list(vals)
                if len(vals) == 1:
                    row = (0, 1, vals[0], 0, 0, 0, 0, 0, 1)
                else:
                    row = (1, 1, 0, vals[0], vals[-1], 0, 0, 0, len(vals))
                f.create_dataset(f"Step config/{n}/Step items", data=np.array([row], dtype=dt))
        for name, cfg in instruments.items():
            g = f.create_group(f"Instrument config/{name}")
            for k, v in cfg.items():
                g.attrs[k] = v
        grids = np.meshgrid(*[np.asarray(v, float) for _, _, v in steps], indexing="ij") if steps else []
        n = int(np.prod([len(v) for _, _, v in steps])) if steps else 1
        data = np.stack([g.reshape(1, -1) for g in grids], axis=1) if steps else np.zeros((1, 0, 1))
        f.create_dataset("Data/Data", data=data.reshape(1, len(steps), n))
        f.create_dataset("Data/Channel names", data=np.array([(n.encode(), b"") for n, _, _ in steps],
                                                             dtype=[("name", "S64"), ("info", "S8")]))
        del s
    return path
