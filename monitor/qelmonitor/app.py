"""監控程式進入點。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtGui, QtWidgets

from .core import Monitor, MonitorConfig
from .window import LoginDialog, MainWindow

ICON = Path(__file__).resolve().parent / "icon.svg"


def main(argv=None) -> int:
    app = QtWidgets.QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("QEL Lab 監控程式")
    app.setQuitOnLastWindowClosed(True)
    if ICON.exists():
        app.setWindowIcon(QtGui.QIcon(str(ICON)))
    cfg = MonitorConfig.load()
    mon = Monitor(cfg)
    if not mon.resume():
        dlg = LoginDialog(mon)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return 0
    win = MainWindow(mon)
    win.show()
    return app.exec()
