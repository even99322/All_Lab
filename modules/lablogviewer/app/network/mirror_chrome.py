"""Banner on Client mirror windows: who is followed, quality, progress, Leave."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QToolBar, QWidget

LEVEL_COLORS = {"excellent": "#2e9e48", "good": "#7cb342", "fair": "#f0a202", "unusable": "#d32f2f",
                "unknown": "#9e9e9e"}


class MirrorBanner(QWidget):
    def __init__(self, space, parent=None):
        super().__init__(parent)
        self.space = space
        self.setObjectName("networkMirrorBanner")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 2, 8, 2)
        self.dot = QLabel("●")
        self.label = QLabel()
        self.quality = QLabel()
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setTextVisible(True)
        self.progress.hide()
        self.leave = QPushButton(space.text("net.leave"))
        self.leave.setProperty("networkAllowed", True)       # the only input a Client mirror takes
        self.leave.clicked.connect(space.disconnect)
        for widget in (self.dot, self.label):
            row.addWidget(widget)
        row.addStretch(1)
        row.addWidget(self.progress)
        row.addWidget(self.quality)
        row.addWidget(self.leave)
        space.changed.connect(self.refresh)
        space.progress.connect(self._progress)
        self.refresh()

    def refresh(self) -> None:
        client = self.space.client
        host = client.host_info.get("host_name", "") if client else ""
        session = client.host_info.get("session_name", "") if client else ""
        source = self.space.text("net.source_local" if self.space.data_source == "local" else "net.source_host")
        self.label.setText(self.space.text("net.following").format(host=host, session=session) + f"  ·  {source}")
        quality = self.space.client_quality
        self.dot.setStyleSheet(f"color: {LEVEL_COLORS.get(quality.level, '#9e9e9e')};")
        self.quality.setText(f"{self.space.text('net.level_' + quality.level)}  {quality.text()}")

    def _progress(self, label: str, done: int, total: int) -> None:
        if total <= 0:
            self.progress.hide()
            return
        self.progress.show()
        self.progress.setMaximum(100)
        self.progress.setValue(int(100 * done / total))
        self.progress.setFormat(f"{label} %p%  ({total / 1e6:.1f} MB)")


def install_mirror_banner(window, space) -> MirrorBanner:
    bar = QToolBar(window)
    bar.setObjectName("networkMirrorBar")
    bar.setMovable(False)
    banner = MirrorBanner(space, bar)
    bar.addWidget(banner)
    window.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
    window.insertToolBarBreak(bar)
    window.network_banner = banner
    return banner
