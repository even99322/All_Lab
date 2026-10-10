"""用讀檔程式（LabLogViewer）開檔或開資料夾（0.0.26 起不經過啟動器，直接開這台電腦裝好的 LabLogViewer）。

找 LabLogViewer 的順序：settings.yaml ``app.reader_path`` → 環境變數 LABLOGVIEWER →
  Windows：%LOCALAPPDATA%\\Programs\\LabLogViewer\\LabLogViewer.exe（安裝程式的預設位置）、Program Files；
  macOS：/Applications/LabLogViewer.app、~/Applications/LabLogViewer.app；
  Linux：PATH 上的 lablogviewer。
LabLogViewer 的命令列：一個檔案 → 開 Viewer；一個資料夾 → 當資料庫開。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from ..settings import setting


class ReaderMissing(RuntimeError):
    pass


def candidates() -> List[Path]:
    out: List[Path] = []
    for v in (setting("app.reader_path", "") or "", os.environ.get("LABLOGVIEWER", "")):
        if v:
            out.append(Path(str(v)).expanduser())
    if sys.platform == "win32":
        for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                out.append(Path(base) / ("Programs" if base == os.environ.get("LOCALAPPDATA") else "") /
                           "LabLogViewer" / "LabLogViewer.exe")
    elif sys.platform == "darwin":
        out += [Path("/Applications/LabLogViewer.app"), Path.home() / "Applications" / "LabLogViewer.app"]
    else:
        w = shutil.which("lablogviewer") or shutil.which("LabLogViewer")
        if w:
            out.append(Path(w))
    return out


def find_reader() -> Optional[Path]:
    for p in candidates():
        if p.exists():
            return p
    return None


def download_page() -> str:
    try:
        from ..remote.hub import hub_urls

        urls = hub_urls()
        return (urls[0] + "/get") if urls else ""
    except Exception:  # noqa: BLE001
        return ""


def open_path(path: str | Path) -> Path:
    """用 LabLogViewer 開 ``path``（檔案或資料夾）。找不到 LabLogViewer 丟 ReaderMissing（訊息含下載頁）。"""
    exe = find_reader()
    if exe is None:
        page = download_page()
        raise ReaderMissing("這台電腦沒有找到讀檔程式 LabLogViewer" +
                            (f"：請到 {page} 下載安裝" if page else "") +
                            "（裝在別的位置：settings.yaml 設 app.reader_path）")
    path = str(Path(path))
    if sys.platform == "darwin" and exe.suffix == ".app":
        cmd = ["open", "-n", "-a", str(exe), "--args", path]
    else:
        cmd = [str(exe), path]
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200          # 不跟著 Lab Control 一起關
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)
    return exe
