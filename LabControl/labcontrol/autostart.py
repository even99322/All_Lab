"""量測電腦：登入電腦時自動開啟 Lab Control，並以量測節點上線（「設定 ▾ → 量測電腦：開機自動開啟」）。

- Windows：``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` 的 ``LabControl``（不需要系統管理員）；
- macOS：``~/Library/LaunchAgents/tw.edu.ccu.qel.labcontrol.plist``；
- Linux：``~/.config/autostart/labcontrol.desktop``。

開啟時帶 ``--node --minimized``：視窗縮小，量測節點在背景上線。
打開時會順便移除舊的「量子工程平台」啟動器的自動開啟（0.0.26 起不再使用啟動器）。
"""
from __future__ import annotations

import os
import plistlib
import shlex
import sys
from pathlib import Path
from typing import List

NAME = "LabControl"
LABEL = "tw.edu.ccu.qel.labcontrol"
OLD = ("QELPlatform", "tw.edu.ccu.qel.platform", "qelplatform.desktop")     # 舊啟動器
ARGS = ["--node", "--minimized"]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def command() -> List[str]:
    """開啟 Lab Control 的指令（打包版 = 執行檔本身；原始碼 = python main.py）。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, *ARGS]
    py = Path(sys.executable)
    if os.name == "nt" and py.name.lower() == "python.exe" and (py.parent / "pythonw.exe").exists():
        py = py.parent / "pythonw.exe"                 # 不要多一個黑色視窗
    return [str(py), str(Path(__file__).resolve().parents[1] / "main.py"), *ARGS]


def _mac_plist(label: str = LABEL) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _linux_desktop(name: str = "labcontrol.desktop") -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / name


def is_enabled() -> bool:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, NAME)
            return True
        except OSError:
            return False
    return (_mac_plist() if sys.platform == "darwin" else _linux_desktop()).exists()


def remove_old_launcher() -> bool:
    """移除舊啟動器（QELPlatform）的自動開啟；回傳是否真的移除了。"""
    removed = False
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, OLD[0])
                removed = True
        except OSError:
            pass
        return removed
    p = _mac_plist(OLD[1]) if sys.platform == "darwin" else _linux_desktop(OLD[2])
    if p.exists():
        p.unlink()
        removed = True
    return removed


def set_enabled(on: bool) -> None:
    cmd = command()
    if on:
        remove_old_launcher()
    if sys.platform == "win32":
        import subprocess
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
            else:
                try:
                    winreg.DeleteValue(k, NAME)
                except OSError:
                    pass
        return
    if sys.platform == "darwin":
        p = _mac_plist()
        if on:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(plistlib.dumps({"Label": LABEL, "ProgramArguments": cmd, "RunAtLoad": True,
                                          "ProcessType": "Interactive"}))
        elif p.exists():
            p.unlink()
        return
    p = _linux_desktop()
    if on:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[Desktop Entry]\nType=Application\nName=Lab Control\n"
                     f"Exec={' '.join(shlex.quote(c) for c in cmd)}\nX-GNOME-Autostart-enabled=true\n",
                     encoding="utf-8")
    elif p.exists():
        p.unlink()


def refresh() -> bool:
    """開著「開機自動開啟」而程式換了位置（0.0.28 起用安裝程式裝到 %LOCALAPPDATA%\\Programs\\Lab Control）
    → 自動開啟改成指向現在這個程式。回傳是否有改。"""
    if not getattr(sys, "frozen", False) or not is_enabled():
        return False
    try:
        if sys.platform == "win32":
            import subprocess
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                current = str(winreg.QueryValueEx(k, NAME)[0])
            if current == subprocess.list2cmdline(command()):
                return False
        elif sys.platform == "darwin":
            if plistlib.loads(_mac_plist().read_bytes()).get("ProgramArguments") == command():
                return False
        else:
            return False
        set_enabled(True)
        return True
    except Exception:  # noqa: BLE001
        return False
