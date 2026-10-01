"""3D Surface window in its own process.

GPU drivers are the most common source of native crashes. The 3D window
therefore runs in a separate LabLogViewer process: if it crashes or hangs,
only the 3D window closes and the Browser, Viewer and analysis windows keep
working (the Viewer offers to reopen it).

Parent  (Viewer)          ThreeDProcessHost: starts ``main.py --three-d-window``,
                          talks JSON lines over a QLocalSocket, reports crashes.
Child   (3D process)      run_child(): a hidden Viewer that owns the 3D window
                          (same controls, renderer, export and annotation code).

The child never writes shared records (stars, tags, marks, sessions,
settings ...); it may write only ``three_d_states.json`` (its own 3D view per
Data). Settings are changed in the main process and pushed to the child.
Set ``LABLOGVIEWER_3D_PROCESS=0`` to keep 3D in the main process.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

CHILD_FLAG = "--three-d-window"
STATE_FILE = "three_d_states.json"


def process_mode_enabled() -> bool:
    value = os.environ.get("LABLOGVIEWER_3D_PROCESS", "auto").strip().lower()
    if value in {"0", "off", "false", "no"}:
        return False
    if value in {"1", "on", "true", "yes"}:
        return True
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.platformName() in {"offscreen", "minimal"} or "PYTEST_CURRENT_TEST" in os.environ:
        return False
    return True


# macOS / Linux keep a local channel as a socket file in the temporary folder, and such
# paths may be at most about 104 bytes; a long temporary folder made listen() fail.
SOCKET_PATH_LIMIT = 100


def channel_name(name: str) -> str:
    """A local channel name that always fits: the short name normally, or a full path in
    /tmp when the temporary folder's path is too long (Windows uses named pipes: no limit)."""
    if os.name == "nt":
        return name
    from PySide6.QtCore import QDir

    if len(os.fsencode(os.path.join(QDir.tempPath(), name))) <= SOCKET_PATH_LIMIT:
        return name
    return os.path.join("/tmp", name)


def child_command(arguments: list[str]) -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):                 # packaged app: the executable is the entry point
        return sys.executable, list(arguments)
    main = Path(__file__).resolve().parents[2] / "main.py"
    return sys.executable, [str(main), *arguments]


class _Channel(QObject):
    """Newline-delimited JSON messages over a local socket."""

    def __init__(self, socket: QLocalSocket, on_message, parent=None):
        super().__init__(parent)
        self.socket = socket
        self._buffer = b""
        self._on_message = on_message
        socket.readyRead.connect(self._read)

    def send(self, message: dict) -> None:
        if self.socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self.socket.write((json.dumps(message) + "\n").encode("utf-8"))
            self.socket.flush()

    def _read(self) -> None:
        self._buffer += bytes(self.socket.readAll())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                message = json.loads(line.decode("utf-8"))
            except ValueError:
                continue
            if isinstance(message, dict):
                self._on_message(message)


# -- main process ----------------------------------------------------------------
class ThreeDProcessHost(QObject):
    """Owns one isolated 3D process for one Viewer."""

    crashed = Signal(str)
    message = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process: QProcess | None = None
        self.server: QLocalServer | None = None
        self.channel: _Channel | None = None
        self._queue: list[dict] = []
        self._stopping = False
        self._init_file: str | None = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning

    def show(self, path: str, state: dict | None) -> None:
        if self.is_running():
            self.send({"cmd": "open", "path": path, "state": state})
            self.send({"cmd": "raise"})
            return
        self._start(path, state)

    def send(self, message: dict) -> None:
        if self.channel is not None:
            self.channel.send(message)
        elif self.is_running():
            self._queue.append(message)

    def shutdown(self) -> None:
        if not self.is_running():
            return
        self._stopping = True
        self.send({"cmd": "quit"})
        if not self.process.waitForFinished(2500):
            self.process.kill()
            self.process.waitForFinished(1000)

    def _start(self, path: str, state: dict | None) -> None:
        self._stopping = False
        name = channel_name(f"lablogviewer-3d-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        QLocalServer.removeServer(name)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)   # this user only
        self.server.newConnection.connect(self._connected)
        if not self.server.listen(name):
            self.crashed.emit(f"Could not start the 3D process channel: {self.server.errorString()}")
            return
        handle, self._init_file = tempfile.mkstemp(prefix="lablogviewer-3d-", suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"path": path, "state": state}, stream)
        program, arguments = child_command([CHILD_FLAG, "--server", name, "--init", self._init_file])
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedChannels)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.process.start(program, arguments)

    def _connected(self) -> None:
        socket = self.server.nextPendingConnection()
        if socket is None:
            return
        self.channel = _Channel(socket, self.message.emit, self)
        for message in self._queue:
            self.channel.send(message)
        self._queue.clear()

    def _error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.crashed.emit("The 3D process could not be started.")

    def _finished(self, code: int, status) -> None:
        crashed = status == QProcess.ExitStatus.CrashExit or code != 0
        self.channel = None
        self._queue.clear()
        if self.server is not None:
            self.server.close()
            self.server = None
        if self._init_file:
            Path(self._init_file).unlink(missing_ok=True)
            self._init_file = None
        if crashed and not self._stopping:
            self.crashed.emit(f"exit code {code}" if status != QProcess.ExitStatus.CrashExit else "crash")


# -- 3D process ------------------------------------------------------------------
class _ThreeDStates:
    """Last 3D view per Data, written only by 3D processes (read-merge-write)."""

    def __init__(self):
        from app.core.external_state import default_state_path

        self.path = default_state_path(STATE_FILE)

    def get(self, key: str) -> dict | None:
        from app.core.external_state import load_json_state

        data = load_json_state(self.path, {}).value
        value = data.get(key) if isinstance(data, dict) else None
        return value if isinstance(value, dict) else None

    def put(self, key: str, state: dict) -> None:
        from app.core.external_state import atomic_write_json, load_json_state

        data = load_json_state(self.path, {}).value
        data = data if isinstance(data, dict) else {}
        data[key] = state
        atomic_write_json(self.path, data)


def run_child(argv: list[str]) -> int:
    """Entry point of the isolated 3D process (``main.py --three-d-window``)."""
    import argparse

    from PySide6.QtWidgets import QApplication

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--server", required=True)
    parser.add_argument("--init", required=True)
    options, _unknown = parser.parse_known_args(argv)
    os.environ["LABLOGVIEWER_3D_PROCESS"] = "0"          # the 3D window lives here, in-process

    app = QApplication(sys.argv[:1])
    app.setApplicationName("LabLogViewer")
    from app.gui.glass_scrollbars import install_glass_scrollbars

    install_glass_scrollbars(app)
    from app.core.data_location import data_root
    from app.core.external_state import restrict_writes

    data_root()
    restrict_writes({STATE_FILE})
    from app.localization import initialize_localization
    from app.theme import initialize_theme

    localizer = initialize_localization(app)
    theme = initialize_theme(app, localizer.store)
    from app.core.watchdog import install_watchdog

    install_watchdog(app, "3d")
    from app.gui.effects import install_effects

    install_effects(app)

    try:
        init = json.loads(Path(options.init).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return 2
    from app.gui.main_window import MainWindow

    states = _ThreeDStates()
    viewer = MainWindow()
    socket = QLocalSocket()
    channel = _Channel(socket, lambda message: handle(message))
    viewer.settings_opener = lambda page: channel.send({"cmd": "show_settings", "page": page})
    viewer.network_opener = lambda: channel.send({"cmd": "show_network"})
    current = {"key": None}

    def save_state() -> None:
        state = viewer._current_viewer_display_state() if viewer.experiment is not None else None
        if current["key"] and isinstance(state, dict) and isinstance(state.get("three_d"), dict):
            states.put(current["key"], state["three_d"])

    def open_path(path: str, fallback: dict | None) -> None:
        if viewer.experiment is not None:
            save_state()
        viewer.open_file(path)
        if viewer.experiment is None:
            return
        current["key"] = str(Path(path).resolve())
        state = states.get(current["key"]) or fallback
        if isinstance(state, dict):
            viewer._restore_display_state({"mode": "3d", "three_d": state})
        window = viewer.open_3d_window()
        if window is not None and not getattr(window, "_child_hooked", False):
            window._child_hooked = True
            window.closed.connect(lambda: (save_state(), QTimer.singleShot(0, app.quit)))

    def reload_settings() -> None:
        from app.settings.store import SettingsStore

        store = localizer.store
        store._payload = SettingsStore(store.path)._payload
        theme.set_mode(store.appearance())
        theme.set_scientific_plot_appearance(store.scientific_plot_appearance())
        theme.set_export_plot_background(store.export_plot_background())
        theme.set_glass_material(*store.glass_material(), final=True)
        localizer.set_language(store.language())
        from app.gui.app_icon import apply_app_icon

        apply_app_icon(store)
        if viewer._three_d_window is not None:
            viewer._three_d_window.refresh_export_style()

    def handle(message: dict) -> None:
        command = message.get("cmd")
        window = viewer._three_d_window
        if command == "open" and isinstance(message.get("path"), str):
            open_path(message["path"], message.get("state"))
        elif command == "raise" and window is not None:
            window.showNormal()
            window.raise_()
            window.activateWindow()
        elif command == "settings":
            reload_settings()
        elif command == "network" and isinstance(message.get("status"), dict):
            from app.gui.network_panel import set_relayed_status

            set_relayed_status(message["status"])
        elif command == "grab" and window is not None and isinstance(message.get("path"), str):
            window.grab().save(message["path"])              # diagnostics / tests
            channel.send({"cmd": "grabbed", "path": message["path"]})
        elif command == "quit":
            if window is not None:
                window.close()
            app.quit()

    socket.disconnected.connect(app.quit)                 # main process gone -> close
    socket.connectToServer(options.server)
    if not socket.waitForConnected(5000):
        return 3
    app.setQuitOnLastWindowClosed(False)
    open_path(init.get("path", ""), init.get("state"))
    if viewer._three_d_window is None:
        return 4
    return app.exec()
