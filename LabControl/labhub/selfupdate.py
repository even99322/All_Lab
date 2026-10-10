"""Hub 自我更新。

新版的 labhub 程式放在 ``<data>/hub_app/v<版本>/labhub/``，``<data>/hub_app/current.json`` 記錄要用哪一版。
啟動時（``python -m labhub``，見 __main__.py）如果 current.json 的版本比內建的新，就切換到那一版執行；
新版連續兩次沒能正常啟動會自動退回內建版本（避免更新壞掉後 Hub 起不來）。

更新來源：
  * Lab APP 發佈資料夾（Hub 的 LABHUB_RELEASES，裡面的 LabControl/v<版本>/labhub/）；
  * Lab Control Monitor 上傳的 zip（exe 內附的 Hub 版本）。
更新後 Hub 以同樣的參數重新啟動自己（Docker、run_hub.bat、python -m labcontrol hub 都適用）。
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("labhub")
PKG = Path(__file__).resolve().parent


def _version_of(pkg_dir: Path) -> Optional[str]:
    try:
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)', (pkg_dir / "__init__.py").read_text(encoding="utf-8"))
        return m.group(1) if m else None
    except OSError:
        return None


def app_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "hub_app"


def current_file(data_dir: Path) -> Path:
    return app_dir(data_dir) / "current.json"


def read_current(data_dir: Path) -> Dict[str, Any]:
    try:
        return json.loads(current_file(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_current(data_dir: Path, data: Dict[str, Any]) -> None:
    f = current_file(data_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def _validate(pkg: Path) -> str:
    if not (pkg / "__init__.py").exists() or not (pkg / "server.py").exists():
        raise ValueError("不是 labhub 程式資料夾（缺少 __init__.py / server.py）")
    for f in pkg.rglob("*.py"):
        compile(f.read_text(encoding="utf-8"), str(f), "exec")      # 語法檢查（不寫 .pyc）
    v = _version_of(pkg)
    if not v:
        raise ValueError("讀不到 labhub 版本")
    return v


def install_dir(data_dir: Path, src_pkg: Path) -> str:
    """把 src_pkg（labhub 資料夾）安裝成下次啟動使用的版本，回傳版本。"""
    v = _validate(src_pkg)
    data_dir = Path(data_dir).resolve()
    dst_root = app_dir(data_dir) / f"v{v}"
    tmp = app_dir(data_dir) / f".install-{os.getpid()}-{int(time.time())}"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src_pkg, tmp / "labhub", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if dst_root.exists():
        shutil.rmtree(dst_root)
    os.replace(tmp, dst_root)
    write_current(data_dir, {"version": v, "path": str(dst_root), "installed": time.time(), "attempts": 0,
                             "healthy": False})
    log.info("已安裝 Hub v%s → %s（重新啟動後生效）", v, dst_root)
    return v


def install_zip(data_dir: Path, data: bytes) -> str:
    """zip 內含 labhub/（可在任一層資料夾下，例如 LabControlHub/labhub/）。"""
    with zipfile.ZipFile(io.BytesIO(data)) as z, tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        for m in z.infolist():
            t = (root / m.filename).resolve()
            if root not in t.parents and t != root:
                raise ValueError(f"zip 內容不安全：{m.filename}")
        z.extractall(root)
        pkgs = sorted((p.parent for p in root.rglob("server.py") if p.parent.name == "labhub"),
                      key=lambda p: len(p.parts))
        if not pkgs:
            raise ValueError("zip 裡找不到 labhub/ 資料夾")
        return install_dir(data_dir, pkgs[0])


def release_pkg(releases_dir: Optional[Path], app_name: str, version: str) -> Optional[Path]:
    if releases_dir is None:
        return None
    v = str(version).lstrip("vV")
    for base in (Path(releases_dir) / app_name, Path(releases_dir)):
        for n in (f"v{v}", v):
            p = base / n / "labhub"
            if (p / "server.py").exists():
                return p
    return None


def running_info() -> Dict[str, Any]:
    from . import __version__

    return {"version": __version__, "path": str(PKG), "booted": bool(os.environ.get("LABHUB_BOOTED"))}


def mark_healthy(data_dir: Path) -> None:
    """新版正常啟動後呼叫（清掉失敗計數）。"""
    from . import __version__

    cur = read_current(data_dir)
    if cur and cur.get("version") == __version__ and os.environ.get("LABHUB_BOOTED"):
        cur.update(healthy=True, attempts=0, started=time.time())
        write_current(data_dir, cur)


# ---- 重新啟動 -------------------------------------------------------------------
_restart_argv: List[str] = []


def set_restart_argv(argv: List[str]) -> None:
    _restart_argv[:] = list(argv)


def restart_later(delay: float = 1.0, before=None) -> None:
    """稍後以同樣參數重新啟動 Hub（re-exec；Docker 容器不會停止）。LABHUB_NO_RESTART=1 時不重新啟動（測試用）。"""
    if os.environ.get("LABHUB_NO_RESTART"):
        log.warning("LABHUB_NO_RESTART：略過重新啟動")
        return

    def go() -> None:
        try:
            if before is not None:
                before()
        except Exception:  # noqa: BLE001
            log.exception("重新啟動前的收尾失敗")
        env = dict(os.environ)
        env.pop("LABHUB_BOOTED", None)
        base = str(PKG.parent)
        env["PYTHONPATH"] = base + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        log.warning("Hub 重新啟動…")
        logging.shutdown()
        os.execve(sys.executable, [sys.executable, "-m", "labhub", *_restart_argv], env)
    threading.Timer(delay, go).start()


# ---- 啟動時選版本（__main__ 呼叫）--------------------------------------------------
def _data_from_argv(argv: List[str]) -> Path:
    for i, a in enumerate(argv):
        if a == "--data" and i + 1 < len(argv):
            return Path(argv[i + 1]).resolve()
        if a.startswith("--data="):
            return Path(a.split("=", 1)[1]).resolve()
    return Path(os.environ.get("LABHUB_DATA", "./labhub-data")).resolve()


def _absolute_paths(argv: List[str]) -> List[str]:
    """切換工作目錄前，把 --data / --releases 的相對路徑改成絕對路徑。"""
    out = list(argv)
    for i, a in enumerate(out):
        for opt in ("--data", "--releases"):
            if a == opt and i + 1 < len(out):
                out[i + 1] = str(Path(out[i + 1]).resolve())
            elif a.startswith(opt + "="):
                out[i] = f"{opt}={Path(a.split('=', 1)[1]).resolve()}"
    return out


def boot(argv: List[str]) -> None:
    """如果 data/hub_app 有比內建更新、且沒連續失敗的版本 → 切換過去執行（不回傳）。"""
    from . import __version__
    from .server import parse_version

    if os.environ.get("LABHUB_BOOTED"):
        return
    data = _data_from_argv(argv)
    cur = read_current(data)
    if not cur:
        return
    path = Path(cur.get("path", ""))
    if not (path / "labhub" / "server.py").exists():
        return
    if parse_version(cur.get("version", "0")) <= parse_version(__version__):
        return                                   # 內建的已經一樣或更新（例如手動換過檔案）
    if not cur.get("healthy") and int(cur.get("attempts", 0)) >= 2:
        print(f"Hub v{cur.get('version')} 連續兩次啟動失敗，改用內建 v{__version__}", file=sys.stderr, flush=True)
        os.replace(current_file(data), current_file(data).with_name("current.failed.json"))
        return
    cur["attempts"] = int(cur.get("attempts", 0)) + (0 if cur.get("healthy") else 1)
    write_current(data, cur)
    argv = _absolute_paths(argv)
    env = dict(os.environ, LABHUB_BOOTED="1", LABHUB_DATA=str(data))
    if env.get("LABHUB_RELEASES"):
        env["LABHUB_RELEASES"] = str(Path(env["LABHUB_RELEASES"]).resolve())
    env["PYTHONPATH"] = str(path) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    os.chdir(path)
    os.execve(sys.executable, [sys.executable, "-m", "labhub", *argv], env)
