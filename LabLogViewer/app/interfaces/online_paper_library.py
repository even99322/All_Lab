"""External default-browser launcher; no embedded browser or network client."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


ONLINE_PAPER_LIBRARY_URL = "http://100.114.33.20:8080/"


def open_online_paper_library(url_opener: Callable[[QUrl], bool] | None = None) -> bool:
    """Ask the OS to open the configured URL; opener injection supports safe tests."""
    opener = QDesktopServices.openUrl if url_opener is None else url_opener
    return bool(opener(QUrl(ONLINE_PAPER_LIBRARY_URL)))
