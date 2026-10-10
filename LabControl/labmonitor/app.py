"""Lab Control Monitor 進入點（exe / python -m labmonitor）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _icon():
    from PyQt6 import QtGui

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    for p in (base / "icon.ico", base / "assets" / "icon.ico"):
        if p.exists():
            return QtGui.QIcon(str(p))
    return None


def main() -> int:
    from PyQt6 import QtWidgets

    from . import APP_NAME, __version__
    from .window import MonitorWindow

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"LabControlMonitor.{__version__}")
        except Exception:  # noqa: BLE001
            pass
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    from . import style

    style.apply(app, dark=True)
    ic = _icon()
    if ic is not None:
        app.setWindowIcon(ic)
    w = MonitorWindow()
    w.show()
    shot = os.environ.get("LABMONITOR_SCREENSHOT")          # 測試用：N 秒後截圖並結束（路徑;秒;分頁）
    if shot:
        from PyQt6 import QtCore

        parts = shot.split(";")
        path, sec = parts[0], float(parts[1]) if len(parts) > 1 else 5
        tab = int(parts[2]) if len(parts) > 2 else 0
        w.tabs.setCurrentIndex(tab)
        QtCore.QTimer.singleShot(int(sec * 1000), lambda: (w.grab().save(path), app.quit()))
    return app.exec()
