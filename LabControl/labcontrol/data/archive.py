"""專題歸檔（0.0.31）：把量測數據（一個或多個檔、或整個資料夾）複製到 NAS「數據集散地/<專案>/」，保留日期資料夾結構。

    plan(sources, project_folder, dest_root, data_root) → [(來源檔, 目的檔)]
    copy(plan, progress)                                 → {copied, skipped, bytes, errors}

目的地：NAS 的 \\\\192.168.50.2\\ccuqel\\數據集散地（實驗室網路）或 \\\\100.114.33.20\\ccuqel\\數據集散地（VPN）；
Mac：/Volumes/ccuqel/數據集散地。環境變數 LABCONTROL_ARCHIVE_ROOT 可以指定（測試、其他位置）。
來源在這台的數據集散地裡 → 目的地保留「<專案>/年/月/Data_MMDD/檔名」；在別的地方 → <專案>/<來源資料夾名稱>/…
已經有一樣的檔（大小、修改時間相同）→ 跳過；同名但不同 → 加 _2、_3。
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

FOLDER = "數據集散地"
SHARE = "ccuqel"
HOSTS = ("192.168.50.2", "100.114.33.20")
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def nas_candidates() -> List[str]:
    env = os.environ.get("LABCONTROL_ARCHIVE_ROOT", "")
    if env.strip():
        return [p.strip() for p in env.split(";") if p.strip()]
    if sys.platform == "win32":
        return [f"\\\\{h}\\{SHARE}\\{FOLDER}" for h in HOSTS]
    if sys.platform == "darwin":
        return [f"/Volumes/{SHARE}/{FOLDER}", f"/Volumes/{SHARE}-1/{FOLDER}"]
    return [f"/mnt/{SHARE}/{FOLDER}", str(Path.home() / SHARE / FOLDER)]


def _reachable(path: str, timeout: float) -> bool:
    res: Dict[str, bool] = {}
    t = threading.Thread(target=lambda: res.__setitem__("ok", os.path.isdir(path)), daemon=True)
    t.start()
    t.join(timeout)
    return bool(res.get("ok"))


def find_nas(timeout: float = 5.0) -> Optional[str]:
    """第一個連得到的 NAS 數據集散地（實驗室網路優先，再 VPN）。"""
    for p in nas_candidates():
        if _reachable(p, timeout):
            return p
    return None


def _files(src: Path) -> Iterable[Path]:
    if src.is_file():
        yield src
        return
    for dp, dns, fns in os.walk(src):
        dns[:] = sorted(d for d in dns if not d.startswith("."))
        for f in sorted(fns):
            if f not in SKIP_NAMES and not f.startswith("."):
                yield Path(dp) / f


def _rel_in_root(path: Path, data_root: Optional[Path]) -> Optional[Path]:
    """path 在這台的數據集散地裡 → 去掉最前面的專案資料夾後的相對路徑（年/月/Data_MMDD/檔名）。"""
    if data_root is None:
        return None
    try:
        rel = path.resolve().relative_to(Path(data_root).resolve())
    except (ValueError, OSError):
        return None
    parts = rel.parts
    return Path(*parts[1:]) if len(parts) > 1 else Path(*parts)


def plan(sources: List[Path], project_folder: str, dest_root: Path,
         data_root: Optional[Path] = None) -> List[Tuple[Path, Path]]:
    dest = Path(dest_root) / project_folder
    out: List[Tuple[Path, Path]] = []
    seen = set()
    for src in sources:
        src = Path(src)
        base = src.parent if src.is_file() else src.parent
        for f in _files(src):
            rel = _rel_in_root(f, data_root)
            if rel is None:                              # 不在數據集散地：<來源資料夾名稱>/…
                rel = Path(src.name) / f.relative_to(src) if src.is_dir() else Path(base.name) / f.name
            d = dest / rel
            if d in seen:
                continue
            seen.add(d)
            out.append((f, d))
    return out


def _same(a: Path, b: Path) -> bool:
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    return sa.st_size == sb.st_size and abs(sa.st_mtime - sb.st_mtime) < 2.1


def _free_name(d: Path) -> Path:
    n = 2
    while True:
        c = d.with_name(f"{d.stem}_{n}{d.suffix}")
        if not c.exists():
            return c
        n += 1


def copy(items: List[Tuple[Path, Path]], progress: Optional[Callable[[int, int, str], None]] = None,
         stop: Optional[threading.Event] = None) -> Dict[str, object]:
    """複製；progress(done_bytes, total_bytes, 目前的檔名)。"""
    total = sum(s.stat().st_size for s, _ in items if s.exists())
    done = copied = skipped = 0
    errors: List[str] = []
    t0 = time.monotonic()
    for src, dst in items:
        if stop is not None and stop.is_set():
            break
        try:
            size = src.stat().st_size
            if dst.exists():
                if _same(src, dst):
                    skipped += 1
                    done += size
                    if progress:
                        progress(done, total, src.name)
                    continue
                dst = _free_name(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(f".{dst.name}.part")
            with open(src, "rb") as fi, open(tmp, "wb") as fo:
                while True:
                    chunk = fi.read(4 << 20)
                    if not chunk:
                        break
                    fo.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total, src.name)
            shutil.copystat(src, tmp)
            os.replace(tmp, dst)
            copied += 1
        except OSError as e:
            errors.append(f"{src.name}：{e}")
    return {"copied": copied, "skipped": skipped, "bytes": done, "errors": errors,
            "seconds": time.monotonic() - t0}
