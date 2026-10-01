"""量測節點自我更新。

方法（settings.yaml remote.update.method）：
  hub     從 Lab Control Hub 下載 v<版本>.zip（Hub 提供 Lab APP 的 Releases 資料夾）解壓到 LAB/app/v<版本>/，
          套件與目前相同就沿用目前的 Python，不同就建立新的虛擬環境（有 uv 用 uv），然後啟動新版（預設）
  labapp  呼叫 Lab APP 安裝並啟動指定版本（命令在 remote.update.command；命令列參數尚未驗證）
  auto    找得到 Lab APP 就用 labapp，否則用 hub
呼叫端（節點）在新版啟動後結束自己。量測中不會呼叫到這裡（節點會等量測結束）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List

from ..paths import lab_path
from ..settings import setting
from .versioning import labapp_executable, launch_update

APP_ROOT = Path(__file__).resolve().parents[2]


def _detached_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": 0x00000008 | 0x00000200}   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return {"start_new_session": True}


def relaunch_args() -> List[str]:
    """用同樣的參數啟動新版（main.py 或 python -m labcontrol）。"""
    argv0 = Path(sys.argv[0]).name if sys.argv else ""
    if argv0 == "__main__.py":
        return ["-m", "labcontrol", *sys.argv[1:]]
    return ["main.py", *sys.argv[1:]]


def _python_for(dst: Path, log: Callable[[str], None]) -> str:
    cur_req = (APP_ROOT / "requirements.txt")
    new_req = dst / "requirements.txt"
    same = cur_req.exists() and new_req.exists() and cur_req.read_text(encoding="utf-8") == \
        new_req.read_text(encoding="utf-8")
    if same or not new_req.exists():
        log("套件相同，沿用目前的 Python 環境")
        return sys.executable
    venv = lab_path("app", f"venv-{dst.name}")
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if py.exists():
        return str(py)
    uv = shutil.which("uv")
    log(f"建立新的 Python 環境（套件有變動）：{venv}")
    if uv:
        pyver = (dst / ".python-version").read_text().strip() if (dst / ".python-version").exists() else ""
        subprocess.run([uv, "venv", str(venv)] + (["--python", pyver] if pyver else []), check=True)
        subprocess.run([uv, "pip", "install", "--python", str(py), "-r", str(new_req)], check=True)
    else:
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        subprocess.run([str(py), "-m", "pip", "install", "-r", str(new_req)], check=True)
    return str(py)


def install_from_hub(version: str, hub, log: Callable[[str], None] = print) -> Path:
    """從 Hub 下載並解壓到 LAB/app/v<版本>/（已完整下載過就直接用）。"""
    import zipfile

    v = str(version).lstrip("vV")
    dst = lab_path("app", f"v{v}")
    if (dst / ".complete").exists() and (dst / "main.py").exists():
        return dst
    zpath = lab_path("app", f"_download_v{v}.zip")
    log(f"從 Hub 下載 v{v}…")
    hub.download(f"/api/releases/v{v}.zip", zpath)
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    with zipfile.ZipFile(zpath) as z:
        root = dst.resolve()
        for m in z.infolist():
            target = (dst / m.filename).resolve()
            if root not in target.parents and target != root:
                raise RuntimeError(f"發佈檔內容不安全：{m.filename}")
        z.extractall(dst)
    if not (dst / "main.py").exists():
        raise RuntimeError(f"v{v} 的發佈檔沒有 main.py")
    (dst / ".complete").write_text(v, encoding="utf-8")
    try:
        zpath.unlink()
    except OSError:
        pass
    return dst


def perform_update(version: str, hub, log: Callable[[str], None] = print) -> str:
    """安裝並啟動新版，回傳使用的方法。失敗丟例外（目前的程式繼續執行）。"""
    method = str(setting("remote.update.method", "hub"))
    exe = labapp_executable()
    if method == "labapp" or (method == "auto" and exe is not None and exe.exists()):
        cmd = launch_update(version)
        log(f"⬆ 已請 Lab APP 安裝並啟動 {version}：{' '.join(cmd)}")
        return "Lab APP"
    dst = install_from_hub(version, hub, log)
    py = _python_for(dst, log)
    args = relaunch_args()
    subprocess.Popen([py, *args], cwd=str(dst), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, **_detached_kwargs())
    log(f"⬆ 已啟動 {dst / args[0] if args[0] == 'main.py' else dst}")
    return "Hub 下載"
