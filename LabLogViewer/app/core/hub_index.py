"""The NAS index (v1.0.3): file information and previews worked out on the NAS by the lab hub.

The hub's indexer (Lab Control ``labhub/indexer.py``) parses every file of the lab share "數據集散地" with this
program's own parser and stores the result. When a database inside the share is opened, the Browser asks the hub
for the whole folder at once instead of opening thousands of files over VPN:

* ``records_for(root)``   → {relative path: LogEntry fields} for the files below ``root`` (one request);
* ``fetch_preview(path)`` → the default Quick Preview of one file (a small .npz), or None.

Access: the share's ``.qel/index_key`` (whoever can read the share can read its index), sent as
``X-QEL-Share-Key``. Hubs tried: the server the path points at (``\\\\192.168.50.2\\…`` → 192.168.50.2), then
``hub_update.hub_urls()``. ``LABLOGVIEWER_HUB_INDEX=0`` turns all of this off. Every failure just returns
None: the Browser then reads the files itself as before.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PureWindowsPath
from typing import Any

from app.core import lab_share

MTIME_TOLERANCE = 2.0           # seconds: the share's time over SMB and the NAS's own time may differ a little
TREE_TIMEOUT = 20.0
PREVIEW_TIMEOUT = 8.0
_CACHE_SECONDS = 60.0

_lock = threading.Lock()
_tree_cache: dict[str, tuple[float, dict[str, dict] | None]] = {}
_good_base: dict[str, str] = {}             # share root → hub that answered
_down_until: dict[str, float] = {}          # share root → no hub answered: do not wait for one again until then
DOWN_SECONDS = 60.0


def enabled() -> bool:
    return os.environ.get("LABLOGVIEWER_HUB_INDEX", "1").strip().lower() not in ("0", "off", "false", "no")


def _parts(path: str) -> list[str]:
    text = str(path)
    if "\\" in text or (len(text) > 1 and text[1] == ":"):
        return list(PureWindowsPath(text).parts)
    return list(Path(text).parts)


def split_share(path: str | os.PathLike) -> tuple[str, str] | None:
    """(share root, path inside it with /) when ``path`` lies in the lab share "數據集散地"; else None."""
    text = os.fspath(path)
    for place in (p for p in os.environ.get("LABLOGVIEWER_LAB_DATA", "").split(";") if p.strip()):
        place = place.strip().rstrip("\\/")
        if text.lower().startswith(place.lower()) and (len(text) == len(place) or text[len(place)] in "\\/"):
            rest = text[len(place):].strip("\\/").replace("\\", "/")
            return place, rest
    parts = _parts(text)
    for i, part in enumerate(parts):
        if part == lab_share.FOLDER:
            root = parts[:i + 1]
            rest = "/".join(p for p in parts[i + 1:] if p not in ("\\", "/"))
            if "\\" in text or (len(text) > 1 and text[1] == ":"):
                return str(PureWindowsPath(*root)), rest
            return str(Path(*root)), rest
    return None


def _host_of(share_root: str) -> str | None:
    text = share_root.replace("/", "\\")
    if text.startswith("\\\\"):
        return text[2:].split("\\", 1)[0] or None
    return None


def _hub_urls() -> list[str]:
    try:
        from app.core.hub_update import hub_urls
    except ImportError:                          # the hub's own copy of the parser (labhub/llvcore) has no updater
        env = os.environ.get("QEL_HUB") or ""
        urls = [u.strip().rstrip("/") for u in env.split(",") if u.strip()] or \
            [f"http://{host}:8765" for host in lab_share.HOSTS]
        return [u if "://" in u else "http://" + u for u in urls]
    return list(hub_urls())


def hub_bases(share_root: str) -> list[str]:
    bases = _hub_urls()
    host = _host_of(share_root)
    if host:
        own = [b for b in bases if urllib.parse.urlsplit(b).hostname == host] or [f"http://{host}:8765"]
        bases = own + [b for b in bases if b not in own]
    good = _good_base.get(share_root)
    if good in bases:
        bases.remove(good)
        bases.insert(0, good)
    return bases


def read_key(share_root: str) -> str | None:
    try:
        key = (Path(share_root) / ".qel" / "index_key").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return key or None


def _get(url: str, key: str, timeout: float) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # lab network / VPN: no proxy
    request = urllib.request.Request(url, headers={"X-QEL-Share-Key": key, "Accept-Encoding": "gzip",
                                                   "User-Agent": "LabLogViewer"})
    with opener.open(request, timeout=timeout) as response:
        body = response.read()
        if (response.headers.get("Content-Encoding") or "").lower() == "gzip":
            body = gzip.decompress(body)
        return body


def _ask(share_root: str, path_and_query: str, timeout: float) -> bytes | None:
    key = read_key(share_root)
    if not key or time.monotonic() < _down_until.get(share_root, 0.0):
        return None
    for base in hub_bases(share_root):
        try:
            body = _get(base + path_and_query, key, timeout)
        except urllib.error.HTTPError as error:
            if error.code in (401, 404):
                _good_base[share_root] = base       # the hub answered: the index just has no such thing
                return None
            continue
        except (OSError, ValueError):
            continue
        _good_base[share_root] = base
        return body
    _down_until[share_root] = time.monotonic() + DOWN_SECONDS     # no hub: read the files directly for a while
    return None


def records_for(root_path: str | os.PathLike) -> dict[str, dict] | None:
    """{path relative to ``root_path`` (/): record} for every indexed file below it; None = no index."""
    if not enabled():
        return None
    split = split_share(root_path)
    if split is None:
        return None
    share_root, rel = split
    cache_key = os.fspath(root_path)
    with _lock:
        hit = _tree_cache.get(cache_key)
        if hit is not None and time.monotonic() - hit[0] < _CACHE_SECONDS:
            return hit[1]
    body = _ask(share_root, "/api/index/tree?root=" + urllib.parse.quote(rel), TREE_TIMEOUT)
    records = None
    if body is not None:
        try:
            tree = json.loads(body.decode("utf-8"))
            records = {e["rel"]: e for e in tree.get("entries", []) if isinstance(e, dict) and e.get("rel")}
        except (ValueError, TypeError, KeyError):
            records = None
    with _lock:
        _tree_cache[cache_key] = (time.monotonic(), records)
    return records


def forget(root_path: str | os.PathLike | None = None) -> None:
    with _lock:
        if root_path is None:
            _tree_cache.clear()
            _down_until.clear()
        else:
            _tree_cache.pop(os.fspath(root_path), None)


def matches(record: dict, size: int, mtime: float) -> bool:
    try:
        return int(record.get("size")) == int(size) and abs(float(record.get("mtime")) - float(mtime)) <= MTIME_TOLERANCE
    except (TypeError, ValueError):
        return False


def entry_fields(record: dict, root: Path, relative: str, stat) -> dict:
    """LogEntry keyword arguments from a hub record (the size / time are this computer's own)."""
    path = root / relative
    tags = record.get("file_tags")
    return {
        "absolute_path": str(path), "relative_path": relative, "file_name": path.name,
        "log_name": record.get("log_name") or path.stem, "status": "ok", "error_message": None,
        "size_bytes": stat.st_size, "mtime": stat.st_mtime, "creation_time": record.get("creation_time"),
        "sweep_dimension": record.get("sweep_dimension") or "", "metadata_complete": True,
        "file_tags": list(tags) if isinstance(tags, list) else ([] if tags is None else None),
        "file_star": bool(record.get("file_star")) if record.get("file_star") is not None else False,
    }


# ---- previews ------------------------------------------------------------------------------------
def fetch_preview(path: str | os.PathLike, size: int, mtime: float):
    """The NAS's default Quick Preview of one file, or None (not indexed yet, changed since, no hub …)."""
    if not enabled():
        return None
    split = split_share(path)
    if split is None:
        return None
    share_root, rel = split
    query = urllib.parse.urlencode({"path": rel, "size": int(size), "mtime": repr(float(mtime))})
    body = _ask(share_root, "/api/index/preview?" + query, PREVIEW_TIMEOUT)
    if body is None:
        return None
    try:
        return decode_preview(body)
    except Exception:
        return None


def decode_preview(blob: bytes):
    """A preview .npz from the hub (Lab Control labhub/index_codec.py) → QuickPreviewData."""
    import numpy as np

    from app.core.data_model import AcquisitionStatus, Grid2DData, Slice1DData
    from app.core.quick_preview import QuickPreviewData

    with np.load(io.BytesIO(blob), allow_pickle=False) as npz:
        meta: dict[str, Any] = json.loads(bytes(npz["meta"]).decode("utf-8"))
        arrays = {name: np.asarray(npz[name]) for name in npz.files if name != "meta"}
    if int(meta.get("format", 0)) != 1:
        raise ValueError("unknown preview format")
    acq = meta.get("acquisition")
    acquisition = AcquisitionStatus(**acq) if isinstance(acq, dict) else None
    if meta.get("kind") == "2d":
        data = Grid2DData(
            x_values=arrays["x"].astype(float), y_values=arrays["y"].astype(float),
            z_values=arrays["z"].astype(float),
            x_name=meta.get("x_name") or "", x_unit=meta.get("x_unit"), y_name=meta.get("y_name") or "",
            y_unit=meta.get("y_unit"), z_name=meta.get("z_name") or "", z_unit=meta.get("z_unit"),
            transform=meta.get("transform") or "raw", fixed_dims=meta.get("fixed_dims") or {},
            acquisition=acquisition, operation_label=meta.get("operation_label") or "",
            display_unit=meta.get("display_unit"),
        )
        kind = "2d"
    else:
        data = Slice1DData(
            x_values=arrays["x"].astype(float), y_values=arrays["y"].astype(float),
            x_name=meta.get("x_name") or "", x_unit=meta.get("x_unit"), z_name=meta.get("z_name") or "",
            z_unit=meta.get("z_unit"), transform=meta.get("transform") or "raw",
            fixed_dims=meta.get("fixed_dims") or {},
        )
        kind = "1d"
    preview = QuickPreviewData(
        kind, meta.get("log_name") or "", meta.get("channel_name") or "", data, acquisition=acquisition,
        colormap=meta.get("colormap") or "LabLog BWR", z_min=meta.get("z_min"), z_max=meta.get("z_max"),
        metadata=meta.get("metadata"),
    )
    preview.from_index = True
    return preview
