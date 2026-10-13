"""The lab's shared data folder on the NAS, "數據集散地" (v2.1.5).

Windows:  \\\\192.168.50.2\\ccuqel\\數據集散地 (lab network), \\\\100.114.33.20\\ccuqel\\數據集散地 (VPN)
macOS:    /Volumes/ccuqel/數據集散地 once the share is mounted (Finder: smb://100.114.33.20/ccuqel)
Override: LABLOGVIEWER_LAB_DATA (one or more folders, separated by ";").

A share that cannot be reached can make a plain os.path.isdir hang for many seconds, so every place is
checked in its own thread with a time limit, and the first one that answers (in the order above) is used.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

FOLDER = "數據集散地"
SHARE = "ccuqel"
HOSTS = ("192.168.50.2", "100.114.33.20")          # lab network, VPN


def candidates() -> list[str]:
    env = os.environ.get("LABLOGVIEWER_LAB_DATA", "")
    if env.strip():
        return [p.strip() for p in env.split(";") if p.strip()]
    if sys.platform == "win32":
        return [f"\\\\{host}\\{SHARE}\\{FOLDER}" for host in HOSTS]
    if sys.platform == "darwin":
        return [f"/Volumes/{SHARE}/{FOLDER}", f"/Volumes/{SHARE}-1/{FOLDER}"]
    return [f"/mnt/{SHARE}/{FOLDER}", str(Path.home() / SHARE / FOLDER)]


def _reachable(path: str, timeout: float) -> bool:
    result: dict = {}

    def probe():
        try:
            result["ok"] = os.path.isdir(path)
        except OSError:
            result["ok"] = False
    worker = threading.Thread(target=probe, daemon=True)
    worker.start()
    worker.join(timeout)
    return bool(result.get("ok"))


def find(timeout: float = 6.0) -> str | None:
    """The first lab data folder that answers within ``timeout`` seconds (all are probed at once)."""
    places = candidates()
    found: dict[str, bool] = {}
    threads = []
    for place in places:
        thread = threading.Thread(target=lambda p=place: found.__setitem__(p, _reachable(p, timeout)), daemon=True)
        thread.start()
        threads.append(thread)
    end = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.0, end - time.monotonic()))
    for place in places:                                  # keep the preferred order
        if found.get(place):
            return place
    return None


def mount_macos(host: str = HOSTS[1]) -> bool:
    """macOS: ask Finder to mount smb://<host>/ccuqel (it asks for the NAS account the first time)."""
    if sys.platform != "darwin":
        return False
    try:
        subprocess.Popen(["open", f"smb://{host}/{SHARE}"])
        return True
    except OSError:
        return False


def local_lab_data() -> Path | None:
    """v2.1.6: a measurement computer keeps its data in Desktop\\數據集散地 (Lab Control's default)."""
    env = os.environ.get("LABLOGVIEWER_LOCAL_LAB_DATA")
    if env:
        p = Path(env).expanduser()
        return p if p.is_dir() else None
    for d in (Path.home() / "Desktop" / FOLDER, Path.home() / "OneDrive" / "Desktop" / FOLDER,
              Path.home() / "OneDrive" / "桌面" / FOLDER):
        if d.is_dir():
            return d
    return None


def default_folder(timeout: float = 3.0) -> str | None:
    """Where to open data by default: this computer's 數據集散地 (measurement computer), else the NAS one."""
    local = local_lab_data()
    if local is not None:
        return str(local)
    return find(timeout)


def lab_root_of(path: str | Path) -> Path | None:
    """The 數據集散地 folder that contains path (or is path), else None."""
    p = Path(path)
    for cand in [p, *p.parents]:
        if cand.name == FOLDER:
            return cand
    return None


def project_folders(root: str | Path) -> list[str]:
    """Project folders inside a 數據集散地 (not starting with . or _), sorted."""
    try:
        return sorted((d.name for d in Path(root).iterdir() if d.is_dir() and not d.name.startswith((".", "_"))),
                      key=str.casefold)
    except OSError:
        return []

