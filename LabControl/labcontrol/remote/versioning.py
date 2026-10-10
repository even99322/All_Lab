"""版本比較與透過 Lab APP 更新。

版本字串：X.Y.Z 加上可選的修正字母（0.0.1a < 0.0.1b < 0.0.2）；前面可以有 v。
更新：呼叫 Lab APP 安裝並啟動指定版本（命令在 settings.yaml remote.update.command），然後結束目前的程式。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from ..settings import setting


def parse_version(v: str) -> Tuple:
    m = re.match(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?([a-z]*)", str(v or ""), re.I)
    if not m:
        return (0, 0, 0, "")
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0), (m.group(4) or "").lower())


def is_newer(a: str, b: str) -> bool:
    """a 是否比 b 新。"""
    return parse_version(a) > parse_version(b)


def labapp_executable() -> Optional[Path]:
    explicit = str(setting("remote.update.labapp", "") or "").strip()
    if explicit:
        return Path(os.path.expandvars(os.path.expanduser(explicit)))
    cands = []
    if os.name == "nt":
        la = os.environ.get("LOCALAPPDATA", "")
        if la:
            cands.append(Path(la) / "LabApps" / "LabApp" / "LabApp.exe")
    elif sys.platform == "darwin":
        cands += [Path.home() / "Applications" / "Lab APP.app" / "Contents" / "MacOS" / "Lab APP",
                  Path("/Applications/Lab APP.app/Contents/MacOS/Lab APP")]
    for c in cands:
        if c.exists():
            return c
    return cands[0] if cands else None


def update_command(version: str) -> List[str]:
    exe = labapp_executable()
    app = str(setting("remote.update.app_name", "LabControl"))
    tpl = setting("remote.update.command", ["{labapp}", "--app", "{app}", "--version", "v{version}"]) or []
    v = str(version).lstrip("vV")
    return [str(x).format(labapp=str(exe or ""), app=app, version=v) for x in tpl]


def launch_update(version: str) -> List[str]:
    """啟動 Lab APP 安裝新版（與目前程式脫離，目前程式隨後應結束）。回傳實際執行的命令。"""
    cmd = update_command(version)
    if not cmd or not cmd[0]:
        raise RuntimeError("沒有設定 Lab APP 路徑（settings.yaml remote.update.labapp）")
    if cmd[0].lower().endswith((".exe", "lab app")) and not Path(cmd[0]).exists():
        raise RuntimeError(f"找不到 Lab APP：{cmd[0]}（可在 settings.yaml remote.update.labapp 指定）")
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **kw)
    return cmd
