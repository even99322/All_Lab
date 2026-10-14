"""``lablogviewer://`` links (v1.0.5): the Hub's web page "數據查看" opens a file in this program.

    lablogviewer://open?share=<path inside 數據集散地, "/" separated>
    lablogviewer://open?path=<absolute path on this computer>

The share path is joined to the first lab data folder this computer can reach (lab network, then VPN;
see lab_share). Windows: the installer registers the scheme for the current user (and the program
re-registers it at start, so an older install also gets it); macOS: CFBundleURLTypes in the app bundle,
delivered as a QFileOpenEvent with the URL.
"""

from __future__ import annotations

import os
import sys
import threading
import urllib.parse
from pathlib import Path

SCHEME = "lablogviewer"


def is_link(text) -> bool:
    return isinstance(text, str) and text.lower().startswith(SCHEME + ":")


def parse(link: str) -> dict | None:
    """{"share": "a/b/c.nc"} or {"path": "..."}; None for anything else (only "open" is understood)."""
    if not is_link(link):
        return None
    parts = urllib.parse.urlsplit(link)
    action = (parts.netloc or parts.path.strip("/")).lower()
    if action != "open":
        return None
    query = urllib.parse.parse_qs(parts.query)
    share = (query.get("share") or [""])[0]
    if share:
        rel = [p for p in share.replace("\\", "/").split("/") if p not in ("", ".")]
        if not rel or any(p == ".." for p in rel):
            return None
        return {"share": "/".join(rel)}
    path = (query.get("path") or [""])[0]
    return {"path": path} if path else None


def _exists(path: str, timeout: float) -> bool:
    result: dict = {}

    def probe() -> None:
        try:
            result["ok"] = os.path.exists(path)
        except OSError:
            result["ok"] = False
    worker = threading.Thread(target=probe, daemon=True)
    worker.start()
    worker.join(timeout)
    return bool(result.get("ok"))


def resolve(link: str, timeout: float = 6.0) -> tuple[str | None, str]:
    """(local path, "") or (None, reason). Never waits longer than about ``timeout`` seconds per probe."""
    target = parse(link)
    if target is None:
        return None, f"Not a LabLogViewer link: {link}"
    if "path" in target:
        path = target["path"]
        return (path, "") if _exists(path, timeout) else (None, f"File not found: {path}")
    from app.core import lab_share

    root = lab_share.find(timeout)
    if root is None:
        return None, ("Cannot reach the lab data folder (數據集散地). Connect to the lab network or the VPN"
                      + (" and mount smb://100.114.33.20/ccuqel in Finder" if sys.platform == "darwin" else "")
                      + ", then click the link again.")
    path = os.path.join(root, *target["share"].split("/"))
    return (path, "") if _exists(path, timeout) else (None, f"File not found in 數據集散地: {target['share']}")


def register_windows(executable: str | None = None) -> bool:
    """HKCU\\Software\\Classes\\lablogviewer → this program (no administrator rights needed). True if written."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False
    exe = executable or (sys.executable if getattr(sys, "frozen", False) else "")
    if not exe:
        return False
    command = f'"{exe}" "%1"'
    base = rf"Software\Classes\{SCHEME}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as key:
            if winreg.QueryValueEx(key, "")[0] == command:
                return False                               # already points here
    except OSError:
        pass
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:LabLogViewer")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{exe}",0')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        return True
    except OSError:
        return False


def is_data_path(path: str) -> bool:
    return Path(path).suffix.lower() in (".hdf5", ".h5", ".nc")
