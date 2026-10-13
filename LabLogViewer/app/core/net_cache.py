"""Local copies of data files that live on a network drive (v2.1.6).

Reading HDF5 straight from a NAS share is many small reads, each one a network round trip; over the VPN that
makes previews, trace switching and plotting crawl. Copying the file once (large sequential reads) and reading
the copy is much faster. So:

* ``ensure_local(path)`` copies a file on a network drive into the cache (Browser preview, opening a Viewer);
* ``read_path(path)`` is what HDF5Reader opens: the cached copy while it is still identical to the original
  (same size and modification time), otherwise the original. Writes (Star, Tags, Comment …) always go to the
  original file; the copy is then out of date and is simply not used any more.
* A file that is still growing (measurement running) is never cached.
* ``busy()`` marks foreground work (a preview, a Viewer opening); background metadata scanning waits for it
  (``wait_idle``) so it does not take the bandwidth the user is waiting for.

Off with LABLOGVIEWER_NET_CACHE=0; LABLOGVIEWER_NET_CACHE=force treats every folder as a network drive (tests).
Size limit: LABLOGVIEWER_NET_CACHE_GB (default 8); the least recently used copies go first.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Iterator

CHUNK = 8 << 20
PREVIEW_MAX_BYTES = 768 << 20          # the Browser preview copies files up to this size
VIEWER_MAX_BYTES = 16 << 30            # a Viewer copies files up to this size (with a progress dialog)


class Cancelled(Exception):
    pass


def mode() -> str:
    return os.environ.get("LABLOGVIEWER_NET_CACHE", "").strip().lower()


def enabled() -> bool:
    return mode() not in ("0", "off", "false", "no")


def cache_dir() -> Path:
    env = os.environ.get("LABLOGVIEWER_NET_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "LabLogViewer"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "LabLogViewer"
    else:
        base = Path.home() / ".cache" / "lablogviewer"
    return base / "netcache"


def limit_bytes() -> int:
    try:
        return int(float(os.environ.get("LABLOGVIEWER_NET_CACHE_GB", "8")) * (1 << 30))
    except ValueError:
        return 8 << 30


# ---- which paths are on a network drive -------------------------------------------------------
_mounts: tuple[float, list[str]] = (0.0, [])
_NET_TYPES = {"smbfs", "afpfs", "nfs", "nfs4", "cifs", "smb3", "smb2", "webdav", "davfs", "fuse.sshfs", "9p"}


def _network_mounts() -> list[str]:
    """Mount points of network file systems (macOS / Linux), refreshed every minute."""
    global _mounts
    stamp, points = _mounts
    if time.monotonic() - stamp < 60:
        return points
    points = []
    try:
        if sys.platform.startswith("linux") and Path("/proc/mounts").is_file():
            for line in Path("/proc/mounts").read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[2].lower() in _NET_TYPES:
                    points.append(parts[1].replace("\\040", " "))
        elif sys.platform == "darwin":
            out = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                m = re.match(r".+? on (.+) \(([^,)]+)", line)
                if m and m.group(2).strip().lower() in _NET_TYPES:
                    points.append(m.group(1))
    except (OSError, subprocess.SubprocessError):
        pass
    points.sort(key=len, reverse=True)
    _mounts = (time.monotonic(), points)
    return points


def is_network_path(path: str | os.PathLike) -> bool:
    if mode() == "force":
        return True
    text = os.path.abspath(os.fspath(path))
    if sys.platform == "win32":
        if text.startswith(("\\\\", "//")):
            return True
        drive = os.path.splitdrive(text)[0]
        if not drive:
            return False
        try:
            import ctypes

            return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4     # DRIVE_REMOTE
        except Exception:
            return False
    return any(text == p or text.startswith(p.rstrip("/") + "/") for p in _network_mounts())


# ---- the cache ------------------------------------------------------------------------------------
def _key(path: str | os.PathLike) -> str:
    text = os.path.abspath(os.fspath(path))
    if sys.platform == "win32":
        text = text.lower()
    digest = hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()[:20]
    name = re.sub(r"[^\w.\-]+", "_", Path(text).name)[-80:]
    return f"{digest}_{name}"


def _files(path) -> tuple[Path, Path]:
    key = _key(path)
    folder = cache_dir()
    return folder / key, folder / (key + ".json")


def _stamp(path) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return int(st.st_size), int(st.st_mtime_ns)


def fresh_copy(path, stamp: tuple[int, int] | None = None) -> Path | None:
    """The cached copy of ``path`` if it is identical to the original (same size and time), else None."""
    if not enabled():
        return None
    local, info = _files(path)
    if not info.is_file() or not local.is_file():
        return None
    try:
        meta = json.loads(info.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    stamp = stamp or _stamp(path)
    if stamp is None or [meta.get("size"), meta.get("mtime_ns")] != list(stamp):
        return None
    if local.stat().st_size != stamp[0]:
        return None
    return local


def read_path(path) -> Path:
    """What to open for reading: the fresh cached copy, else the original."""
    local = fresh_copy(path)
    if local is None:
        return Path(path)
    try:
        os.utime(_files(path)[1])                        # recently used (for the size limit)
    except OSError:
        pass
    return local


def needs_copy(path, max_bytes: int = VIEWER_MAX_BYTES) -> bool:
    """True when ``ensure_local`` would copy (network drive, not cached yet, not too big)."""
    if not enabled() or not is_network_path(path):
        return False
    stamp = _stamp(path)
    return stamp is not None and 0 < stamp[0] <= max_bytes and fresh_copy(path, stamp) is None


_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()


def _lock_for(path) -> threading.Lock:
    with _key_locks_guard:
        return _key_locks.setdefault(_key(path), threading.Lock())


def ensure_local(path, progress: Callable[[int, int], None] | None = None,
                 cancel: Callable[[], bool] | None = None, max_bytes: int = VIEWER_MAX_BYTES) -> Path:
    """Copy ``path`` into the cache when it is on a network drive; returns what to read (copy or original).

    Raises Cancelled when ``cancel()`` becomes true. Any copy problem → the original is read directly."""
    if not enabled() or not is_network_path(path):
        return Path(path)
    stamp = _stamp(path)
    if stamp is None or stamp[0] <= 0 or stamp[0] > max_bytes:
        return Path(path)
    lock = _lock_for(path)
    while not lock.acquire(timeout=0.1):               # the same file is being copied by another worker
        if cancel is not None and cancel():
            raise Cancelled()
    try:
        local = fresh_copy(path, stamp)
        if local is not None:
            return read_path(path)
        try:
            return _copy(Path(path), stamp, progress, cancel)
        except Cancelled:
            raise
        except OSError:
            return Path(path)
    finally:
        lock.release()


def _copy(source: Path, stamp: tuple[int, int], progress, cancel) -> Path:
    local, info = _files(source)
    local.parent.mkdir(parents=True, exist_ok=True)
    evict(keep=stamp[0])
    tmp = local.with_name(f".{local.name}.{uuid.uuid4().hex[:8]}.part")
    total, done = stamp[0], 0
    try:
        with busy(), open(source, "rb") as src, open(tmp, "wb") as dst:
            while True:
                if cancel is not None and cancel():
                    raise Cancelled()
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                dst.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        if _stamp(source) != stamp or done != total:     # still being written: do not keep a copy
            tmp.unlink(missing_ok=True)
            return source
        os.replace(tmp, local)
        info.write_text(json.dumps({"source": str(source), "size": stamp[0], "mtime_ns": stamp[1],
                                    "copied": time.time()}), encoding="utf-8")
        return local
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def evict(keep: int = 0) -> None:
    """Remove the least recently used copies until the cache (plus ``keep`` new bytes) fits the limit."""
    folder = cache_dir()
    if not folder.is_dir():
        return
    entries = []
    total = 0
    for info in folder.glob("*.json"):
        local = info.with_suffix("")
        try:
            size = local.stat().st_size if local.is_file() else 0
            used = info.stat().st_mtime
        except OSError:
            continue
        entries.append((used, size, local, info))
        total += size
    for stale in folder.glob(".*.part"):                 # left by a crash (older than an hour)
        with contextlib.suppress(OSError):
            if time.time() - stale.stat().st_mtime > 3600:
                stale.unlink()
    limit = limit_bytes()
    for used, size, local, info in sorted(entries, key=lambda e: e[0]):
        if total + keep <= limit:
            break
        try:
            local.unlink(missing_ok=True)                  # Windows: a copy open in a Viewer stays
            info.unlink(missing_ok=True)
            total -= size
        except OSError:
            continue


def clear() -> None:
    folder = cache_dir()
    for f in list(folder.glob("*")) if folder.is_dir() else []:
        with contextlib.suppress(OSError):
            f.unlink()


def usage() -> tuple[int, int]:
    """(files, bytes) in the cache."""
    folder = cache_dir()
    files = [f for f in folder.glob("*") if f.is_file() and f.suffix != ".json" and not f.name.startswith(".")] \
        if folder.is_dir() else []
    return len(files), sum(f.stat().st_size for f in files)


# ---- foreground / background ----------------------------------------------------------------------
_busy = 0
_busy_lock = threading.Lock()
_idle = threading.Event()
_idle.set()


@contextlib.contextmanager
def busy() -> Iterator[None]:
    """Foreground network reading (preview, Viewer): background scanning waits meanwhile."""
    global _busy
    with _busy_lock:
        _busy += 1
        _idle.clear()
    try:
        yield
    finally:
        with _busy_lock:
            _busy -= 1
            if _busy == 0:
                _idle.set()


def wait_idle(cancel: Callable[[], bool] | None = None, max_wait: float = 120.0) -> None:
    """Background work: wait while foreground reading is going on (at most ``max_wait`` seconds)."""
    deadline = time.monotonic() + max_wait
    while not _idle.wait(0.2):
        if (cancel is not None and cancel()) or time.monotonic() > deadline:
            return
