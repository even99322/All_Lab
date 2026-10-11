"""Updates from the Quantum Engineering Platform hub (v2.1.2): push notification and one-click install.

The lab's hub (NAS, ``http://192.168.50.2:8765``, VPN ``http://100.114.33.20:8765``; override with the
environment variable ``QEL_HUB``, comma separated) publishes LabLogViewer installers:

    GET /get/feed/lablogviewer.json   {"version", "date", "items": [...], "downloads": [{name, platform, arch,
                                       size, sha256, url}]}  (the same feed format as update_feed.py, plus downloads)
    GET /get/watch?app=lablogviewer&version=2.1.1&wait=60
                                      long poll: answers as soon as a newer version is published (push)

``HubWatcher`` (app/gui/hub_updates.py) keeps that long poll open and adds an "update" notice (bell and pet)
the moment the hub has a newer version. The update preview then offers "Update now", which downloads the
installer for this computer and installs it without a browser:

* Windows: the Inno Setup installer runs with /SILENT (same install folder, no questions), then LabLogViewer
  starts again;
* macOS: the new LabLogViewer.app from the .dmg replaces the running one, then it starts again.

Downloads are checked against the hub's size and sha256. Only the packaged app (PyInstaller) installs; running
from Python only notifies.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from app.core.app_update import version_key
from app.core.update_feed import UpdateInfo, _parse_feed

APP_ID = "lablogviewer"
DEFAULT_HUBS = ("http://192.168.50.2:8765", "http://100.114.33.20:8765")
FROZEN = bool(getattr(sys, "frozen", False))


class HubUpdateError(RuntimeError):
    pass


def hub_urls() -> list[str]:
    env = os.environ.get("QEL_HUB") or os.environ.get("QEL_GATEWAY") or ""
    urls = [u.strip().rstrip("/") for u in env.split(",") if u.strip()] or list(DEFAULT_HUBS)
    return [u if "://" in u else "http://" + u for u in urls]


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))        # lab network / VPN, no proxy


def this_platform() -> str:
    return "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"


def this_arch() -> str:
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"


def pick_download(downloads: list[dict], plat: str | None = None, arch: str | None = None) -> dict | None:
    """The installer for this computer (Mac: same chip, then universal, then the other chip)."""
    plat, arch = plat or this_platform(), arch or this_arch()
    mine = [d for d in downloads or [] if d.get("platform") == plat]
    mine.sort(key=lambda d: 0 if (d.get("arch") or "") == arch else 1 if (d.get("arch") or "") in ("universal2", "")
              else 2)
    return mine[0] if mine else None


def _with_download(data: dict, base: str) -> tuple[UpdateInfo | None, dict | None]:
    info = _parse_feed(data)
    dl = pick_download(data.get("downloads") or [])
    if dl is not None:
        dl = dict(dl, url=base + dl["url"] if str(dl.get("url", "")).startswith("/") else dl["url"], hub=base)
    return info, dl


def read_hub_feed(timeout: float = 6.0, bases: list[str] | None = None) -> tuple[UpdateInfo | None, dict | None]:
    """(feed, installer for this computer). Raises HubUpdateError when no hub answers."""
    last = None
    for base in bases or hub_urls():
        try:
            with _opener().open(base + f"/get/feed/{APP_ID}.json", timeout=timeout) as response:
                data = json.loads(response.read(1_000_000).decode("utf-8"))
        except Exception as error:                      # try the next address (lab network / VPN)
            last = error
            continue
        return _with_download(data, base)
    raise HubUpdateError(f"The hub cannot be reached ({last}).")


def watch(version: str, wait: float = 60.0, bases: list[str] | None = None):
    """Long poll: (feed, installer) as soon as the hub has a version newer than ``version``; None on timeout."""
    last = None
    for base in bases or hub_urls():
        url = f"{base}/get/watch?app={APP_ID}&version={urllib.parse.quote(version)}&wait={int(wait)}"
        try:
            with _opener().open(url, timeout=wait + 15) as response:
                result = json.loads(response.read(2_000_000).decode("utf-8"))
        except Exception as error:
            last = error
            continue
        if not result.get("changed"):
            return None
        return read_hub_feed(bases=[base])
    raise HubUpdateError(f"The hub cannot be reached ({last}).")


def notify(info: UpdateInfo | None, download: dict | None, current_version: str) -> dict | None:
    """Add the "update" notice (bell, pet) for a newer hub version, with what "Update now" needs."""
    from app.core import notifications
    from app.core.update_feed import newer_info

    info = newer_info(info, current_version, notifications.skipped_versions())
    if info is None:
        return None
    details = info.details()
    if download is not None:
        details["install"] = {k: download.get(k) for k in ("name", "url", "size", "sha256", "platform", "arch", "hub")}
    return notifications.add("update", {"version": info.version, "n": len(info.items)}, details=details,
                             key=f"update:{info.version}")


def can_install(details: dict | None) -> bool:
    install = (details or {}).get("install") or {}
    return FROZEN and bool(install.get("url")) and install.get("platform") == this_platform()


# ---- installing -------------------------------------------------------------------------------
def download(install: dict, progress: Callable[[int, int], None] | None = None) -> Path:
    folder = Path(tempfile.mkdtemp(prefix="lablogviewer_update_"))
    name = Path(urllib.parse.unquote(str(install.get("name") or "LabLogViewer-update"))).name
    target = folder / name
    expected = int(install.get("size") or 0)
    done = 0
    digest = hashlib.sha256()
    with _opener().open(install["url"], timeout=60) as response, target.open("wb") as out:
        total = expected or int(response.headers.get("Content-Length") or 0)
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done, total)
    if expected and done != expected:
        raise HubUpdateError(f"The download is incomplete ({done} of {expected} bytes).")
    if install.get("sha256") and digest.hexdigest() != install["sha256"]:
        raise HubUpdateError("The downloaded file is damaged (its checksum does not match).")
    return target


def _detached() -> dict:
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return kw


def current_bundle() -> Path | None:
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def install_windows(setup: Path, app_exe: Path | None = None) -> Path:
    """Run the Inno Setup installer silently after this program quits, then start LabLogViewer again."""
    app_exe = app_exe or Path(sys.executable).resolve()
    script = setup.with_name("install_update.cmd")
    # v2.1.3: wait for this LabLogViewer to exit ("timeout" cannot run without a console: ping waits instead)
    script.write_text(
        "@echo off\r\n"
        f"set PID={os.getpid()}\r\n"
        ":wait\r\n"
        'tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)\r\n'
        f'"{setup}" /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /NOCANCEL\r\n'
        f'start "" "{app_exe}"\r\n', encoding="utf-8")
    subprocess.Popen(["cmd.exe", "/c", str(script)], **_detached())
    return script


def install_macos(dmg: Path) -> Path:
    """Replace the running LabLogViewer.app with the one in the .dmg (the old one is restored on failure)."""
    bundle = current_bundle()
    if bundle is None:
        raise HubUpdateError("The running LabLogViewer.app was not found.")
    mount = Path(tempfile.mkdtemp(prefix="lablogviewer_dmg_"))
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount), str(dmg)],
                   check=True, capture_output=True)
    try:
        apps = sorted(mount.glob("*.app"))
        if not apps:
            raise HubUpdateError("The disk image has no app.")
        old = bundle.with_name(bundle.name + ".old")
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        bundle.rename(old)
        try:
            subprocess.run(["ditto", str(apps[0]), str(bundle)], check=True, capture_output=True)
        except Exception:
            shutil.rmtree(bundle, ignore_errors=True)
            old.rename(bundle)
            raise
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(bundle)], capture_output=True)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount), "-quiet"], capture_output=True)
    subprocess.Popen(["/bin/sh", "-c", f'sleep 2; open -n "{bundle}"'], **_detached())
    return bundle


def install(install_info: dict, progress: Callable[[int, int], None] | None = None) -> str:
    """Download and install; the caller quits LabLogViewer right after (Windows: the installer waits for it)."""
    if not FROZEN:
        raise HubUpdateError("LabLogViewer runs from Python here: update it with git / Lab APP instead.")
    path = download(install_info, progress)
    if this_platform() == "windows":
        install_windows(path)
        return "windows"
    if this_platform() == "macos":
        install_macos(path)
        return "macos"
    raise HubUpdateError("Updating is supported on Windows and macOS.")


def newer(info: UpdateInfo | None, current_version: str) -> bool:
    return info is not None and version_key(info.version) > version_key(current_version)

