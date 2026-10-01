"""Labber 匯出：先寫 labcontrol 原生檔到暫存，再用 Labber 的 Python 3.8 環境轉檔。

與舊 SaveThread 相同的做法（主程式可用新版 Python，Labber API 留在 3.8 venv）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from ...core.errors import ConfigError
from ...core.registry import register_writer
from ..dataset import Dataset
from .base import Writer
from .hdf5_native import HDF5Writer

SCRIPT = Path(__file__).with_name("labber_export_script.py")


@register_writer("labber")
class LabberWriter(Writer):
    extension = ".hdf5"

    def __init__(self, python38: Optional[str] = None, project: Optional[str] = None, user: Optional[str] = None,
                 tags: Optional[List[str]] = None, script: Optional[str] = None,
                 timeout_s: Optional[float] = None, comment: str = "") -> None:
        from ...settings import setting   # 沒指定的值 → settings.yaml 的 labber:

        self.python38 = (python38 or os.environ.get("LABCONTROL_LABBER_PYTHON") or setting("labber.python38", "")
                         or sys.executable)
        self.project = project or setting("labber.project", "auto")
        self.user = user if user is not None else setting("labber.user", "")
        self.tags = list(tags if tags is not None else (setting("labber.tags", []) or []))
        self.comment = comment or ""
        self.script = Path(script) if script else SCRIPT
        self.timeout_s = float(timeout_s if timeout_s is not None else setting("labber.timeout_s", 600))

    def export(self, dataset: Dataset, path: str | Path) -> Path:
        path = Path(path)
        if not Path(self.python38).exists() and self.python38 != sys.executable:
            raise ConfigError(f"找不到 Labber Python：{self.python38}")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".lm.h5", prefix="labber_tmp_")
        os.close(fd)
        try:
            HDF5Writer().export(dataset, tmp)
            cmd = [self.python38, str(self.script), tmp, str(path), "--project", self.project, "--user", self.user]
            for t in self.tags:
                cmd += ["--tag", t]
            if self.comment:
                cmd += ["--comment", self.comment]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_s)
            if res.returncode != 0 or "SUCCESS" not in res.stdout:
                raise RuntimeError(f"Labber 轉檔失敗：\n{res.stdout}\n{res.stderr}")
            return path
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
