"""Browser-only drag target and background sender for measurement transfer."""

from __future__ import annotations

from pathlib import Path
import tempfile
from uuid import uuid4

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QLabel, QTreeWidget
from PySide6.QtCore import QMimeData

from app.core.measurement_transfer import serialize_measurement
from app.core.measurement_transport import send_measurement


MIME_TYPE = "application/x-lablogviewer-measurement-drag"


class TransferEvents(QObject):
    received = Signal(str)


class MeasurementList(QTreeWidget):
    """Qt starts startDrag only after QApplication.startDragDistance()."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_drag_token: str | None = None
        self.setDragEnabled(True)

    def startDrag(self, supported_actions) -> None:
        item = self.currentItem()
        if item is None or item.data(0, Qt.UserRole) is None:
            return
        token = uuid4().hex
        self.active_drag_token = token
        mime = QMimeData()
        mime.setData(MIME_TYPE, token.encode("ascii"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)
        self.active_drag_token = None


class MeasurementDropTarget(QLabel):
    dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__("Drop a measurement here to send", parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(32)
        self.setFrameShape(QLabel.StyledPanel)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        token = bytes(event.mimeData().data(MIME_TYPE)).decode("ascii", errors="ignore")
        if token:
            self.dropped.emit(token)
            event.acceptProposedAction()


class MeasurementSendWorker(QThread):
    stage = Signal(str)
    progress = Signal(int, int)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, source: str, host: str, port: int,
                 database_name: str | None, folder_name: str | None, parent=None):
        super().__init__(parent)
        self.source = source
        self.host = host
        self.port = port
        self.database_name = database_name
        self.folder_name = folder_name

    def run(self) -> None:
        try:
            self.stage.emit("Preparing measurement...")
            with tempfile.TemporaryDirectory(prefix="lablog-transfer-") as temporary:
                archive = Path(temporary) / "measurement.llvmeasure"
                serialize_measurement(self.source, archive, database_name=self.database_name,
                                      folder_name=self.folder_name)
                self.stage.emit(f"Connecting to {self.host}:{self.port}...")
                send_measurement(self.host, self.port, archive,
                                 lambda done, total: self.progress.emit(done, total))
            self.succeeded.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
