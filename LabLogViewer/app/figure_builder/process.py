"""Run the Scientific Figure Builder in its own process (a freeze or crash there
never stops the rest of LabLogViewer), like the 3D window.

Browser > Processing > Scientific Figure Builder... starts ``main.py --figure-builder``.
The child follows the theme / language in settings.json while it runs and
writes no LabLogViewer state (only the files the user saves or exports).
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QObject, QProcess, Signal

from app.gui.three_d_process import child_command


class FigureBuilderLauncher(QObject):
    crashed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.processes: list[QProcess] = []

    def open(self, dxf_path: str | None = None) -> QProcess:
        program, arguments = child_command(["--figure-builder", *([dxf_path] if dxf_path else [])])
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedChannels)
        process.finished.connect(lambda code, status, p=process: self._finished(p, code, status))
        process.start(program, arguments)
        self.processes.append(process)
        return process

    def running(self) -> int:
        return sum(1 for p in self.processes if p.state() != QProcess.ProcessState.NotRunning)

    def _finished(self, process: QProcess, code: int, status) -> None:
        if process in self.processes:
            self.processes.remove(process)
        if status == QProcess.ExitStatus.CrashExit or code not in (0, 2):
            self.crashed.emit(f"exit code {code}")
        process.deleteLater()

    def shutdown(self) -> None:
        for process in list(self.processes):
            process.terminate()
            if not process.waitForFinished(2000):
                process.kill()


def run_child(argv: list[str]) -> int:
    """Entry point of the Figure Builder process (``main.py --figure-builder [file.dxf]``)."""
    from PySide6.QtCore import QFileSystemWatcher, QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    app.setApplicationName("LabLogViewer")
    from app.gui.glass_scrollbars import install_glass_scrollbars

    install_glass_scrollbars(app)
    from app.core.data_location import data_root
    from app.core.external_state import restrict_writes

    data_root()
    restrict_writes(set())                         # never writes LabLogViewer records
    from app.localization import initialize_localization
    from app.theme import initialize_theme

    localizer = initialize_localization(app)
    theme = initialize_theme(app, localizer.store)
    from app.core.watchdog import install_watchdog

    install_watchdog(app, "figure")
    try:
        from app.gui.effects import install_effects

        install_effects(app)
    except Exception:
        pass
    from app.figure_builder.window import FigureBuilderWindow

    window = FigureBuilderWindow(localizer)
    files = [a for a in argv if a.lower().endswith((".dxf", ".llvfig")) and os.path.isfile(a)]
    if files:
        (window.open_project if files[0].lower().endswith(".llvfig") else window.load_dxf)(files[0])
    from app.gui.help_window import install_f1

    install_f1(window)
    window.show()

    def reload_settings() -> None:
        from app.settings.store import SettingsStore

        store = localizer.store
        try:
            store._payload = SettingsStore(store.path)._payload
            theme.set_mode(store.appearance())
            localizer.set_language(store.language())
        except Exception:
            pass
        if str(store.path) not in watcher.files() and store.path.exists():
            watcher.addPath(str(store.path))        # atomic saves replace the file

    watcher = QFileSystemWatcher()
    if localizer.store.path.exists():
        watcher.addPath(str(localizer.store.path))
    debounce = QTimer(singleShot=True, interval=250, timeout=reload_settings)
    watcher.fileChanged.connect(lambda _p: debounce.start())
    app.setQuitOnLastWindowClosed(True)
    return app.exec()
