"""安裝 / 解除安裝：開機（登入）自動在背景啟動。不需要管理員權限。

Windows：把 exe 複製到 %LOCALAPPDATA%\\QELNetMeter\\QELNetMeter.exe，
         登錄 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\QELNetMeter = "<exe>" --agent
macOS  ：~/Library/LaunchAgents/tw.qel.netmeter.plist（RunAtLoad，執行 App 裡的程式 --agent）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import APP_ID
from .config import data_dir

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PLIST = Path.home() / "Library" / "LaunchAgents" / "tw.qel.netmeter.plist"
LABEL = "tw.qel.netmeter"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _onefile() -> bool:
    meipass = getattr(sys, "_MEIPASS", "")
    return bool(meipass) and Path(meipass).resolve() != Path(sys.executable).resolve().parent / "_internal" \
        and Path(meipass).resolve() != Path(sys.executable).resolve().parent


def installed_exe() -> Path:
    if sys.platform == "win32" and frozen() and _onefile():
        return data_dir() / f"{APP_ID}.exe"
    return Path(sys.executable)


def agent_command(exe: Optional[Path] = None) -> List[str]:
    if frozen():
        return [str(exe or installed_exe()), "--agent"]
    return [sys.executable, "-m", "netmeter", "--agent"]


def copy_self() -> Path:
    """1.0.1 起 Windows 由安裝程式裝好（資料夾版）：直接用自己。只有舊的單一 exe 版（_MEIPASS 在暫存資料夾）才複製。"""
    if sys.platform != "win32" or not frozen() or not _onefile():
        return Path(sys.executable)
    src, dst = Path(sys.executable).resolve(), installed_exe()
    if dst.exists() and src == dst.resolve():
        return dst
    for i in range(20):                                   # 舊的背景程式剛被叫結束，檔案可能還鎖著
        try:
            tmp = dst.with_suffix(".new")
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            return dst
        except PermissionError:
            time.sleep(0.5)
    raise PermissionError(f"無法更新 {dst}（舊版還在執行？）")


def enable_autostart(exe: Optional[Path] = None) -> str:
    cmd = agent_command(exe)
    if sys.platform == "win32":
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, APP_ID, 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
        return "登入 Windows 時自動啟動"
    if sys.platform == "darwin":
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        args = "".join(f"<string>{a}</string>" for a in cmd)
        PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{LABEL}</string>
<key>ProgramArguments</key><array>{args}</array>
<key>RunAtLoad</key><true/>
<key>ProcessType</key><string>Background</string>
</dict></plist>
""", encoding="utf-8")
        return "登入 macOS 時自動啟動"
    return "（這個系統不支援自動啟動）"


def disable_autostart() -> None:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, APP_ID)
        except OSError:
            pass
    elif sys.platform == "darwin":
        subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
        try:
            PLIST.unlink()
        except OSError:
            pass


def autostart_enabled() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, APP_ID)
            return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return PLIST.exists()
    return False


def start_agent(exe: Optional[Path] = None) -> None:
    """另開一個背景程式（不會跟著這個視窗關掉）。"""
    cmd = agent_command(exe)
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **kw)
