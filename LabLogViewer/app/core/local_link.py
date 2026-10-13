"""Other programs on this computer reach an open LabLogViewer (v2.1.6).

* While the Browser is open: a QLocalServer named ``qel-lablogviewer-<user>`` and the record
  ``~/.qel/lablogviewer/app.json`` ({exe, args, version, pid}) so Lab Control knows how to start LabLogViewer.
* Lab Control sends ``{"cmd": "open", "path": "<data file>"}`` when a measurement has finished: the open
  LabLogViewer shows that file in a Viewer. Not open → Lab Control starts LabLogViewer with the file.
* ``LabLogViewer <file>`` while another LabLogViewer is open: the file goes to that one (no second program).

Messages are one JSON line; the answer is ``{"ok": true}``. Only local connections (named pipe / Unix socket).
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "qel-lablogviewer-" + "".join(c for c in user if c.isalnum() or c in "-_")[:40]


def app_file() -> Path:
    return Path.home() / ".qel" / "lablogviewer" / "app.json"


def launch_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve().parents[2] / "main.py")]


def write_app_file() -> None:
    from app import __version__

    cmd = launch_command()
    try:
        app_file().parent.mkdir(parents=True, exist_ok=True)
        tmp = app_file().with_suffix(".tmp")
        tmp.write_text(json.dumps({"exe": cmd[0], "args": cmd[1:], "version": __version__, "pid": os.getpid(),
                                   "server": server_name()}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(app_file())
    except OSError:
        pass


def send(message: dict, timeout_ms: int = 1500) -> bool:
    """Hand a request to the open LabLogViewer; False when none is listening."""
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


class LocalLink(QObject):
    """Local service while the Browser is open; handler(message) runs on the GUI thread."""

    def __init__(self, handler: Callable[[dict[str, Any]], None], parent=None) -> None:
        super().__init__(parent)
        self.handler = handler
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._accept)
        self._buffers: dict[int, bytes] = {}

    def start(self) -> bool:
        name = server_name()
        if send({"cmd": "ping"}, 400):
            return False                              # another LabLogViewer already serves
        if not self.server.listen(name):
            QLocalServer.removeServer(name)           # left over from a crash
            if not self.server.listen(name):
                return False
        write_app_file()
        return True

    def stop(self) -> None:
        self.server.close()

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(sock.deleteLater)

    def _read(self, sock) -> None:
        buf = self._buffers.get(id(sock), b"") + bytes(sock.readAll())
        if b"\n" not in buf:
            self._buffers[id(sock)] = buf[-65536:]
            return
        self._buffers.pop(id(sock), None)
        try:
            msg = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        except ValueError:
            msg = {}
        sock.write(b'{"ok": true}\n')
        sock.flush()
        if isinstance(msg, dict) and msg.get("cmd") not in (None, "ping"):
            QTimer.singleShot(0, lambda m=msg: self.handler(m))
