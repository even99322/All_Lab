"""這台電腦上的程式找到開著的 Lab Control（0.0.28）。

* 開啟時：
  - 寫 ~/.qel/labcontrol/app.json（{exe, args, version, pid}），讓讀檔程式知道怎麼啟動 Lab Control；
  - 在本機開一個 QLocalServer（名稱 qel-labcontrol-<使用者>）。
* 讀檔程式（LabLogViewer）「介面 → 量測」或 `LabControl --apply 檔案`：
  - Lab Control 已經開著 → 送 {"cmd": "apply", "path": …} 給它（視窗跳到最前面，詢問後套用設定）；
  - 沒開 → 啟動 Lab Control，開好後套用。

訊息是一行 JSON：{"cmd": "show"} 或 {"cmd": "apply", "path": "<數據檔>"}；回覆 {"ok": true}。
只接受本機連線（QLocalServer = Windows named pipe / Unix socket）。
"""
from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PyQt6 import QtCore, QtNetwork

from ... import __version__


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return "qel-labcontrol-" + "".join(c for c in user if c.isalnum() or c in "-_")[:40]


def app_file() -> Path:
    return Path.home() / ".qel" / "labcontrol" / "app.json"


def launch_command() -> List[str]:
    """重新啟動這個 Lab Control 的命令（打包版 = 執行檔；Python 版 = python main.py）。"""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    main = Path(__file__).resolve().parents[3] / "main.py"
    return [sys.executable, str(main)]


def write_app_file() -> None:
    cmd = launch_command()
    try:
        app_file().parent.mkdir(parents=True, exist_ok=True)
        tmp = app_file().with_suffix(".tmp")
        tmp.write_text(json.dumps({"exe": cmd[0], "args": cmd[1:], "version": __version__, "pid": os.getpid(),
                                   "server": server_name()}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(app_file())
    except OSError:
        pass


def send(message: Dict[str, Any], timeout_ms: int = 1500) -> bool:
    """送給開著的 Lab Control；沒有開著 → False。"""
    sock = QtNetwork.QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    ok = sock.waitForReadyRead(timeout_ms) and b'"ok"' in bytes(sock.readAll())
    sock.disconnectFromServer()
    return ok


class LocalLink(QtCore.QObject):
    """主視窗開著時的本機服務。handler(message) 在 GUI 執行緒執行。"""

    def __init__(self, handler: Callable[[Dict[str, Any]], None], parent=None) -> None:
        super().__init__(parent)
        self.handler = handler
        self.server = QtNetwork.QLocalServer(self)
        self.server.setSocketOptions(QtNetwork.QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._accept)
        self._buffers: Dict[int, bytes] = {}

    def start(self) -> bool:
        name = server_name()
        # 先問：Windows 的 named pipe、Unix 的 UserAccessOption 都允許同名再 listen（會搶走連線）
        if send({"cmd": "ping"}, 400):
            return False                              # 另一個 Lab Control 已經在服務
        if not self.server.listen(name):
            QtNetwork.QLocalServer.removeServer(name)     # 上次當掉留下的
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
        line = buf.split(b"\n", 1)[0]
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            msg = {}
        sock.write(b'{"ok": true}\n')
        sock.flush()
        if isinstance(msg, dict) and msg.get("cmd") not in (None, "ping"):
            QtCore.QTimer.singleShot(0, lambda m=msg: self.handler(m))


def forward(apply_path: Optional[str]) -> bool:
    """`LabControl --apply 檔案`（QApplication 建好之後呼叫）：已經有 Lab Control 開著 → 交給它（True），
    這個程序就不用再開視窗。"""
    msg = {"cmd": "apply", "path": str(Path(apply_path).resolve())} if apply_path else {"cmd": "show"}
    return send(msg)
