import sys
import os
import warnings
import multiprocessing
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

from config import RESOURCE_DIR, APP_VERSION

warnings.filterwarnings("ignore", category=UserWarning)

if __name__ == "__main__":
    multiprocessing.freeze_support()   # HFSS 任務在獨立程序執行 (打包成 exe 時也需要)
    if "--self-test" in sys.argv:      # 打包檢查：套件都在、介面開得起來、獨立程序可以啟動 (不需要 AEDT)
        from selftest import run
        sys.exit(run())
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"ansys.hfss.automation.tool.{APP_VERSION}")
    except Exception:
        pass

    from ui_main import MainWindow
    app = QApplication(sys.argv)
    icon_path = os.path.join(RESOURCE_DIR, "icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
