#!/usr/bin/env python3
"""
main.py — LabLogViewer application entry point.

v0.8: the Database Browser is now the primary window. Passing a
single HDF5 file path still opens that file directly in a standalone
Viewer window, unchanged from earlier versions, for convenience /
backward compatibility (e.g. "open with LabLogViewer" on one file).
Passing a folder path opens the Browser pre-loaded with that folder as
its database, skipping the "Open Database..." dialog.

Usage:
    python main.py                     # launch the Database Browser
    python main.py file.hdf5           # launch a single-file Viewer directly
    python main.py /path/to/folder     # launch the Browser, pre-scanning that folder
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow
from app.gui.browser_window import BrowserWindow
from app.localization import initialize_localization
from app.theme import initialize_theme
from app.core.data_location import data_root
from app.core.session_lifecycle import SessionLifecycleStore, install_exception_hooks


def _keep_matplotlib_cache() -> None:
    """Packaged builds: PyInstaller points Matplotlib at a new temporary folder on every
    start, so its font cache (several seconds) was rebuilt each time. Keep it in the
    user's cache folder instead, one folder per installed copy (its fonts' paths)."""
    if not getattr(sys, "frozen", False):
        return
    import hashlib
    import os

    home = Path.home()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "LabLogViewer" / "Cache"
    elif sys.platform == "darwin":
        base = home / "Library" / "Caches" / "LabLogViewer"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache") / "LabLogViewer"
    copy = hashlib.sha1(str(Path(sys.executable).resolve()).encode("utf-8")).hexdigest()[:10]
    folder = base / f"matplotlib-{copy}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(folder)
    except OSError:
        pass                                        # keep PyInstaller's temporary folder


def main() -> int:
    _keep_matplotlib_cache()
    if "--self-test" in sys.argv:
        # Check a (packaged) build on this computer: libraries, bundled files, windows,
        # helper process, licence check. See app/self_test.py.
        from app.self_test import run as run_self_test

        return run_self_test()
    if "--three-d-window" in sys.argv:
        # Isolated 3D Surface process started by a Viewer (see app/gui/three_d_process.py).
        from app.gui.three_d_process import run_child

        return run_child(sys.argv[1:])
    if "--figure-builder" in sys.argv:
        # Scientific Figure Builder in its own process (see app/figure_builder/process.py).
        from app.figure_builder.process import run_child as run_figure_builder

        return run_figure_builder(sys.argv[1:])
    app = QApplication(sys.argv)
    app.setApplicationName("LabLogViewer")
    from app.gui.glass_scrollbars import install_glass_scrollbars

    install_glass_scrollbars(app)                   # liquid-glass scroll bars over the content
    # Opens (moves / migrates, if needed) the data folder before any store reads it.
    data_root()
    # First start after an update: upgrade the records (with a backup) before anything reads them.
    from app import __version__
    from app.core.app_update import check as check_update

    update_result = check_update(data_root(), __version__)
    localizer = initialize_localization(app)
    theme_manager = initialize_theme(app, localizer.store)
    from app.core.cpu_threads import apply_cpu_threads
    apply_cpu_threads(localizer.store.cpu_threads())
    from app.gui.annotation import apply_personal_pens

    apply_personal_pens()
    from app._guard import gate
    from app.gui.personal_page import apply_everywhere

    # Personal colours unlock at the 10th data operation: apply them without a restart.
    gate.add_unlock_listener(lambda: apply_everywhere(theme_manager))
    from app.gui.plot_gestures import install_trackpad_gestures

    install_trackpad_gestures(app)                  # pinch = zoom, two-finger scroll = pan
    from app.gui.effects import install_effects

    install_effects(app)                            # button press spring, theme ripple origin
    lifecycle = SessionLifecycleStore()
    startup_recovery = lifecycle.begin()
    install_exception_hooks(lifecycle)
    lifecycle.enable_native_crash_log()
    from app.core.watchdog import install_watchdog

    install_watchdog(app, "main")
    app.aboutToQuit.connect(lifecycle.mark_clean)

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    # v1.0.5: lablogviewer://open?share=… from the Hub's web page "數據查看" → the file in 數據集散地
    from app.core import link_open

    link_open.register_windows()                    # installed program: the link opens this copy
    if link_open.is_link(arg):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QMessageBox

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            path, reason = link_open.resolve(arg)
        finally:
            QApplication.restoreOverrideCursor()
        if path is None:
            QMessageBox.warning(None, "LabLogViewer", reason)
            return 1
        arg = path
    # v2.1.6: another LabLogViewer is open → give it the file / folder (no second program)
    from app.core import local_link

    if arg and Path(arg).exists() and local_link.send({"cmd": "open", "path": str(Path(arg).resolve())}):
        return 0

    if arg and Path(arg).is_file():
        # Single-file mode: a data file (Lab Control after a measurement, a double click) opens only its Viewer,
        # also after an unclean exit (1.0.1; the Browser's Safe Recovery is for the Browser's session).
        window = MainWindow()
        window.operation_journal = lifecycle.record_operation
        window.show()
        window.open_file(arg)
    else:
        window = BrowserWindow(
            session_lifecycle=lifecycle,
            startup_recovery=startup_recovery,
        )
        window.show()
        # Desktop pet (Settings > Pet): owned by this process, closes with LabLogViewer.
        from app.pet.manager import start as start_pet
        from app.settings.dialog import show_settings

        start_pet(localizer, open_settings=lambda: show_settings("pet", window, localizer, theme_manager))
        if arg and Path(arg).is_dir():
            window.open_database(arg)
            if startup_recovery.safe_recovery:
                window._record_operation("Safe Recovery opened an explicitly selected Database")
        elif arg and Path(arg).is_file():
            QTimer.singleShot(0, window.restore_previous_session)
        elif not arg:
            # The Browser owns session recovery because it owns database and
            # Viewer window ownership.  Deferring one event turn lets native
            # window construction complete before a scan begins.
            QTimer.singleShot(0, window.restore_previous_session)

    # v2.1.6: Lab Control (and a second LabLogViewer) reach this one: {"cmd": "open", "path": ...}
    extra_viewers: list = []

    def handle_local(message: dict) -> None:
        path = message.get("path")
        if message.get("cmd") != "open" or not path:
            if message.get("cmd") == "show":
                window.showNormal(); window.raise_(); window.activateWindow()
            return
        if hasattr(window, "open_path"):
            window.open_path(str(path))
        else:                                       # single-file mode: one more Viewer
            viewer = MainWindow()
            viewer.show()
            viewer.open_file(str(path))
            extra_viewers.append(viewer)

    from app.gui.file_drop import install as install_file_drop

    # v2.1.6: drag data files / folders from Explorer or Finder onto any window to open them
    install_file_drop(app, lambda p: handle_local({"cmd": "open", "path": p}))
    link = local_link.LocalLink(handle_local, app)
    link.start()
    app.aboutToQuit.connect(link.stop)

    from app.core.warmup import start_background_warmup
    from app.gui.whats_new import after_start

    QTimer.singleShot(1500, start_background_warmup)   # Matplotlib + font cache, off the window's thread

    # Shown once the windows are up: What's New after an update, or a warning.
    QTimer.singleShot(600, lambda: setattr(window, "_whats_new", after_start(update_result, localizer, window)))
    return app.exec()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
