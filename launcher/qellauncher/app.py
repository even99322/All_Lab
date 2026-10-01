"""大程式（桌面）進入點。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtGui, QtWidgets

from labcomm import PortalClient
from labcomm.config import load_config

from . import __version__
from .core import Launcher
from .window import MainWindow

ICON = Path(__file__).resolve().parent / "icon.svg"


def main(argv=None) -> int:
    app = QtWidgets.QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("QEL Lab")
    if ICON.exists():
        app.setWindowIcon(QtGui.QIcon(str(ICON)))
    cfg = load_config()
    client = PortalClient(cfg.portal_url, cfg.token, timeout=15, client_name=f"qel-launcher/{__version__}")
    win = MainWindow(Launcher(client))
    win.show()
    win.start()
    return app.exec()
