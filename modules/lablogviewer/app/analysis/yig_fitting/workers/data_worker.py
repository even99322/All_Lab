"""Background snapshot of LabLogViewer-owned data for analysis."""

import traceback

from PySide6.QtCore import QThread, Signal

from ..core.data_io import dataset_from_experiment


class DataLoadWorker(QThread):
    loaded = Signal(dict)
    failed = Signal(str)

    def __init__(self, experiment, parent=None):
        super().__init__(parent)
        self.experiment = experiment

    def run(self):
        try:
            self.loaded.emit(dataset_from_experiment(self.experiment))
        except Exception as error:
            self.failed.emit(f"{error}\n\n{traceback.format_exc()}")
