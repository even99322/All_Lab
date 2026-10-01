"""數據檔 ↔ 量測設置。

量測模塊存檔時把「量測方案」和「標籤、來源」寫進數據檔的根屬性：

    qel/scheme   量測方案（JSON，與 Lab Control 的 *.scheme.yaml 內容相同）
    qel/meta     {"tags": [...], "source": {...}, "dataset_id": ..., "labcomm": 版本}

讀檔模塊把數據檔拖到量測模塊時，量測模塊用 ``extract_scheme`` 讀回方案並套用。
舊的數據檔沒有 ``qel/scheme`` 時，依序嘗試：

1. Lab Control 原生 HDF5（``*.lm.h5``，metadata 裡本來就有方案）；
2. 同一天資料夾 ``_raw/`` 裡對應的原生檔；
3. 都沒有 → ``None``（畫面上提示「這個檔案沒有量測設置」）。

讀寫 HDF5 需要 h5py（量測與讀檔模塊本來就有）；沒有 h5py 時這些函式回傳 None。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import __version__

SCHEME_ATTR = "qel/scheme"
META_ATTR = "qel/meta"
DATA_SUFFIXES = (".hdf5", ".h5", ".hdf")


def _h5py():
    try:
        import h5py  # noqa: WPS433
        return h5py
    except ImportError:
        return None


def _text(v: Any) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if hasattr(v, "tolist"):
        v = v.tolist()
        if isinstance(v, bytes):
            return v.decode("utf-8", "replace")
    return str(v)


def is_data_file(path: "str | Path") -> bool:
    return str(path).lower().endswith(DATA_SUFFIXES)


# ---- 寫入 -----------------------------------------------------------------------
def embed(path: "str | Path", scheme: Optional[dict] = None, meta: Optional[dict] = None) -> bool:
    """把方案與標籤寫進數據檔根屬性（覆蓋舊值）。成功回傳 True。"""
    h5py = _h5py()
    if h5py is None:
        return False
    try:
        with h5py.File(str(path), "r+") as f:
            if scheme is not None:
                f.attrs[SCHEME_ATTR] = json.dumps(scheme, ensure_ascii=False, default=str)
            if meta is not None:
                old = {}
                if META_ATTR in f.attrs:
                    try:
                        old = json.loads(_text(f.attrs[META_ATTR]))
                    except ValueError:
                        old = {}
                old.update(meta)
                old["labcomm"] = __version__
                f.attrs[META_ATTR] = json.dumps(old, ensure_ascii=False, default=str)
        return True
    except OSError:
        return False


# ---- 讀出 -----------------------------------------------------------------------
def _scheme_from_open(f) -> Optional[dict]:
    if SCHEME_ATTR in f.attrs:
        try:
            d = json.loads(_text(f.attrs[SCHEME_ATTR]))
            if isinstance(d, dict):
                return d
        except ValueError:
            pass
    if "metadata" in f.attrs:                    # Lab Control 原生 HDF5
        try:
            meta = json.loads(_text(f.attrs["metadata"]))
            sch = ((meta or {}).get("experiment") or {}).get("scheme")
            if isinstance(sch, dict):
                return sch
        except ValueError:
            pass
    return None


def _raw_candidates(path: Path) -> List[Path]:
    raw = path.parent / "_raw"
    if not raw.is_dir():
        return []
    stem = path.stem
    out = []
    for p in raw.glob("*.lm.h5"):
        base = p.name[: -len(".lm.h5")]
        prefix = base.rsplit("_", 1)[0] if "_" in base else base       # <stem>_<HHMMSS>
        if stem == prefix or stem.startswith(prefix + "_"):
            out.append(p)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def extract_scheme(path: "str | Path") -> Optional[dict]:
    """讀回數據檔的量測方案（找不到回傳 None）。"""
    h5py = _h5py()
    if h5py is None:
        return None
    path = Path(path)
    try:
        with h5py.File(str(path), "r") as f:
            s = _scheme_from_open(f)
            if s is not None:
                return s
    except OSError:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    for cand in _raw_candidates(path):
        if mtime is not None and cand.stat().st_mtime > mtime + 24 * 3600:
            continue
        try:
            with h5py.File(str(cand), "r") as f:
                s = _scheme_from_open(f)
                if s is not None:
                    return s
        except OSError:
            continue
    return None


def read_meta(path: "str | Path") -> Dict[str, Any]:
    """qel/meta + Labber 的 /Tags（Project、Tags、User）。"""
    h5py = _h5py()
    out: Dict[str, Any] = {"tags": []}
    if h5py is None:
        return out
    try:
        with h5py.File(str(path), "r") as f:
            if META_ATTR in f.attrs:
                try:
                    out.update(json.loads(_text(f.attrs[META_ATTR])))
                except ValueError:
                    pass
            if "Tags" in f:
                a = f["Tags"].attrs
                labber_tags = [_text(t) for t in (a.get("Tags") if a.get("Tags") is not None else [])]
                out["tags"] = list(dict.fromkeys([*out.get("tags", []), *labber_tags]))
                for k in ("Project", "User"):
                    if a.get(k) is not None:
                        v = a.get(k)
                        v = v[0] if hasattr(v, "__len__") and not isinstance(v, (str, bytes)) and len(v) else v
                        out[k.lower()] = _text(v)
    except OSError:
        pass
    return out


def fingerprint(path: "str | Path", head: int = 4 << 20, tail: int = 1 << 20) -> str:
    """快速內容指紋（大小 + 開頭 4 MB + 結尾 1 MB）：檔案搬家、改名後仍認得是同一筆數據。"""
    p = Path(path)
    size = p.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with open(p, "rb") as f:
        h.update(f.read(head))
        if size > head:
            f.seek(max(head, size - tail))
            h.update(f.read(tail))
    return "qfp1:" + h.hexdigest()[:40]


# ---- 顯示用 ----------------------------------------------------------------------
def scheme_summary(scheme: Optional[dict]) -> List[str]:
    """把方案整理成幾行人看得懂的說明（給讀檔模塊、大程式網頁顯示）。"""
    if not scheme:
        return ["（沒有量測設置）"]
    lines = [f"方案：{scheme.get('name', '未命名')}"]
    nodes: Iterable[dict] = []
    g = scheme.get("graph")
    if isinstance(g, dict):
        nodes = g.get("nodes") or []
    elif isinstance(scheme.get("blocks"), list):
        nodes = scheme["blocks"]
    for n in nodes:
        if not isinstance(n, dict):
            continue
        kind = n.get("kind") or n.get("type") or ""
        title = n.get("title") or n.get("target") or n.get("name") or kind
        p = n.get("params") if isinstance(n.get("params"), dict) else n
        if p.get("mode") == "sweep" or "start" in p:
            unit = p.get("unit", "")
            step = p.get("step", p.get("points", ""))
            lines.append(f"• {title}：{p.get('start')} → {p.get('stop')} {unit}（步進 {step}）")
        elif "value" in p:
            lines.append(f"• {title}：{p.get('value')} {p.get('unit', '')}".rstrip())
        elif kind == "measure":
            lines.append(f"• 量測 {p.get('instrument') or title}：{'、'.join(map(str, p.get('traces') or []))}".rstrip("："))
        elif kind == "save":
            lines.append(f"• 存檔 {p.get('file_name') or ''}".rstrip())
        elif kind == "wait":
            lines.append(f"• 等待 {p.get('seconds', '')} s")
        elif kind:
            lines.append(f"• {title}")
    out = scheme.get("output") or {}
    if out.get("tags"):
        lines.append("標籤：" + "、".join(map(str, out["tags"])))
    return lines


def local_path_or_none(payload: Dict[str, Any]) -> Optional[Path]:
    p = payload.get("path")
    if not p:
        return None
    p = Path(os.path.expanduser(str(p)))
    return p if p.exists() else None
