"""Interfaces > Measurement: open Lab Control (the lab's measurement program), v2.1.3.

* A Data entry is selected: Lab Control applies the settings of that file to its measurement plan
  (it asks first, and the change can be undone there).
* Lab Control already open on this computer: the request goes to it over a local connection
  (QLocalServer "qel-labcontrol-<user>"; Lab Control 0.0.28 and newer) and its window comes to the front.
* Not open: it is started — the program Lab Control recorded in ~/.qel/labcontrol/app.json the last time
  it ran, else the usual install places (Windows: %LOCALAPPDATA%\\Programs\\Lab Control\\LabControl.exe,
  macOS: /Applications/Lab Control.app). LABCONTROL_EXE overrides.
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
from pathlib import Path

from app.interfaces.base import BaseInterface, InterfaceContext, InterfaceResult

NOT_FOUND = ("Lab Control was not found on this computer. Install it from the platform's download page "
             "(http://192.168.50.2:8765/get), open it once, then try again.\n\n"
             "找不到 Lab Control：請從平台的下載頁安裝並開啟一次，再試一次。")


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "qel-labcontrol-" + "".join(c for c in user if c.isalnum() or c in "-_")[:40]


def send(message: dict, timeout_ms: int = 1500) -> bool:
    """Hand the request to an open Lab Control; False when none is listening."""
    try:
        from PySide6.QtNetwork import QLocalSocket
    except ImportError:
        return False
    sock = QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    ok = sock.waitForReadyRead(timeout_ms) and b'"ok"' in bytes(sock.readAll())
    sock.disconnectFromServer()
    return ok


def find_labcontrol() -> list[str] | None:
    """The command that starts Lab Control, or None."""
    env = os.environ.get("LABCONTROL_EXE")
    if env and Path(env).exists():
        return [env]
    record = Path.home() / ".qel" / "labcontrol" / "app.json"
    try:
        info = json.loads(record.read_text(encoding="utf-8"))
        exe = str(info.get("exe") or "")
        if exe and Path(exe).exists():
            return [exe, *[str(a) for a in info.get("args") or []]]
    except (OSError, ValueError):
        pass
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Programs" / "Lab Control"
        for name in ("LabControl.exe",):
            if (base / name).is_file():
                return [str(base / name)]
    if sys.platform == "darwin":
        for app in (Path("/Applications/Lab Control.app"), Path.home() / "Applications" / "Lab Control.app"):
            if app.is_dir():
                return ["open", "-a", str(app), "--args"]
    return None


def _detached() -> dict:
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return kw


class MeasurementInterface(BaseInterface):
    def is_available(self) -> bool:
        return True

    def launch(self, context: InterfaceContext) -> InterfaceResult:
        path = context.selected_log_path
        path = Path(path) if path is not None and Path(path).is_file() else None
        message = {"cmd": "apply", "path": str(path)} if path else {"cmd": "show"}
        if send(message):
            return InterfaceResult.launched()
        command = find_labcontrol()
        if command is None:
            return InterfaceResult.failed(NOT_FOUND)
        if path is not None:
            command = [*command, "--apply", str(path)]
        try:
            subprocess.Popen(command, **_detached())
        except OSError as error:
            return InterfaceResult.failed(f"Lab Control could not be started: {error}")
        return InterfaceResult.launched()
