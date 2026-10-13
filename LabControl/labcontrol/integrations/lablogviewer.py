"""量完自動用讀檔程式（LabLogViewer）開啟新數據（0.0.31）。

* LabLogViewer 開著（2.1.6 以上）→ 透過本機連線 qel-lablogviewer-<使用者> 送 {"cmd": "open", "path": …}；
* 沒開 → 啟動它並開這個檔：它上次開啟時記在 ~/.qel/lablogviewer/app.json 的程式，否則一般的安裝位置
  （Windows：%LOCALAPPDATA%\\Programs\\LabLogViewer\\LabLogViewer.exe；macOS：/Applications/LabLogViewer.app）。
* settings.yaml lablogviewer.open_after_measure: false 可以關掉；分檔量測（雙層分檔，一個外圈值一個檔）不開。
"""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from ..settings import setting


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return "qel-lablogviewer-" + "".join(c for c in user if c.isalnum() or c in "-_")[:40]


def send(message: dict, timeout_ms: int = 1500) -> bool:
    try:
        from PyQt6 import QtNetwork
    except ImportError:
        return False
    sock = QtNetwork.QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    ok = sock.waitForReadyRead(timeout_ms) and b'"ok"' in bytes(sock.readAll())
    sock.disconnectFromServer()
    return ok


def find_command() -> Optional[List[str]]:
    env = os.environ.get("LABLOGVIEWER_EXE")
    if env and Path(env).exists():
        return [env]
    try:
        info = json.loads((Path.home() / ".qel" / "lablogviewer" / "app.json").read_text(encoding="utf-8"))
        exe = str(info.get("exe") or "")
        if exe and Path(exe).exists():
            return [exe, *[str(a) for a in info.get("args") or []]]
    except (OSError, ValueError):
        pass
    if sys.platform == "win32":
        p = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Programs" / "LabLogViewer" \
            / "LabLogViewer.exe"
        if p.is_file():
            return [str(p)]
    if sys.platform == "darwin":
        for app in (Path("/Applications/LabLogViewer.app"), Path.home() / "Applications" / "LabLogViewer.app"):
            if app.is_dir():
                return ["open", "-a", str(app), "--args"]
    return None


def pick(paths: Sequence[Path]) -> Optional[Path]:
    """存了幾種格式時開哪一個：.nc 優先，再原生 HDF5。"""
    ps = [Path(p) for p in paths if Path(p).is_file()]
    for suf in (".nc", ".h5", ".hdf5"):
        for p in ps:
            if p.name.lower().endswith(suf) and not p.name.lower().endswith(".lm.h5"):
                return p
    return ps[0] if ps else None


def open_file(path: Path) -> str:
    """→ "sent"（交給開著的讀檔程式）/ "started"（啟動讀檔程式）/ "missing"（找不到讀檔程式）。"""
    path = Path(path).resolve()
    if send({"cmd": "open", "path": str(path)}):
        return "sent"
    cmd = find_command()
    if cmd is None:
        return "missing"
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200
    else:
        kw["start_new_session"] = True
    subprocess.Popen([*cmd, str(path)], **kw)
    return "started"


def enabled() -> bool:
    return bool(setting("lablogviewer.open_after_measure", True))


def after_measure(paths: Sequence[Path], split: bool) -> Optional[str]:
    """量完存好檔之後呼叫：分檔量測或關掉時不開。回傳 open_file 的結果或 None。"""
    if split or not enabled():
        return None
    p = pick(paths)
    return open_file(p) if p is not None else None
