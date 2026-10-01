"""數據載入執行緒：讀檔與 dB 計算都在背景完成"""
import traceback
from PyQt6.QtCore import QThread, pyqtSignal

from ..core.data_io import load_dataset


class DataLoadWorker(QThread):
    loaded = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            self.loaded.emit(load_dataset(self.path))
        except Exception as e:
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")
