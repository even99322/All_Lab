"""Keep the Windows shortcuts in step with the app icon chosen in Settings.

A Windows shortcut (.lnk) keeps its own icon, so changing the app icon inside the program
does not change the Desktop / Start-menu shortcuts the installer made. This module rewrites
the icon of those shortcuts (only ones that start this program and are named LabLogViewer)
and can create a fresh Desktop shortcut. Shortcuts the user pinned or made by hand elsewhere
are left alone. Nothing here runs on macOS / Linux.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.gui import app_icon

SHORTCUT_NAME = "LabLogViewer.lnk"
_NO_WINDOW = 0x08000000


def is_supported() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False) or os.environ.get("LABLOGVIEWER_FORCE_SHORTCUTS"))


def icon_file(number: int) -> Path:
    """The .ico of one icon design: installed beside the program, else inside the icons folder."""
    if int(number) == app_icon.CUSTOM:                    # the imported picture
        custom = app_icon.custom_icon_path().with_suffix(".ico")
        if custom.is_file():
            return custom
    number = number if 1 <= int(number) <= app_icon.COUNT else app_icon.DEFAULT
    beside = Path(sys.executable).parent / "shortcut_icons" / f"app_icon_{number}.ico"
    return beside if beside.exists() else app_icon.icon_path(number).with_suffix(".ico")


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _powershell(script: str) -> str:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or f"PowerShell exited with {result.returncode}")
    return result.stdout


def shortcut_folders() -> list[Path]:
    """Where the installer puts its shortcuts: the Desktop and the Start-menu group."""
    folders: list[Path] = []
    appdata = os.environ.get("APPDATA")
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        folders.append(Path(userprofile) / "Desktop")
        folders.append(Path(userprofile) / "OneDrive" / "Desktop")
    if appdata:
        folders.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "LabLogViewer")
        folders.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return folders


def update_shortcut_icons(number: int, folders: list[Path] | None = None, program: str | None = None) -> list[str]:
    """Point the installer-made shortcuts at icon ``number``; returns the shortcuts changed."""
    program = str(Path(program or sys.executable))
    icon = icon_file(number)
    if not icon.exists():
        return []
    targets = [folder / SHORTCUT_NAME for folder in (folders if folders is not None else shortcut_folders())
               if (folder / SHORTCUT_NAME).is_file()]
    changed: list[str] = []
    for link in targets:
        script = (
            f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_quote(str(link))});"
            f"if($s.TargetPath -ieq {_quote(program)}){{$s.IconLocation={_quote(str(icon) + ',0')};$s.Save();'changed'}}"
        )
        try:
            if "changed" in _powershell(script):
                changed.append(str(link))
        except (OSError, subprocess.SubprocessError):
            continue
    if changed:
        refresh_shell()
    return changed


def create_desktop_shortcut(number: int) -> str:
    """Create (or replace) the Desktop shortcut with icon ``number``; returns its path."""
    program = Path(sys.executable)
    link = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / SHORTCUT_NAME
    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_quote(str(link))});"
        f"$s.TargetPath={_quote(str(program))};$s.WorkingDirectory={_quote(str(program.parent))};"
        f"$s.IconLocation={_quote(str(icon_file(number)) + ',0')};$s.Save()"
    )
    _powershell(script)
    refresh_shell()
    return str(link)


def refresh_shell() -> None:
    """Tell Explorer that icons changed (SHCNE_ASSOCCHANGED), so it redraws without a restart."""
    try:
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except (AttributeError, OSError):
        pass
