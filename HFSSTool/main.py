import sys
import os
# ezdxf 的 C 加速模組在打包好的 exe 裡會讓程式直接結束（執行環境 DLL 版本不合）：用純 Python 版（DXF 都很小，沒差）
os.environ.setdefault("EZDXF_DISABLE_C_EXT", "1")
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
    if "--test-update" in sys.argv:    # 打包檢查（CI）：跟按「立即更新」一樣執行安裝程式，然後結束
        import updater
        updater.install(sys.argv[sys.argv.index("--test-update") + 1])
        sys.exit(0)
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
    if "--updated-from" in sys.argv:          # v3.3：自動更新完重開
        window.statusBar().showMessage(f"已更新到 {APP_VERSION}", 15000)
    try:
        import updater
        updater.cleanup_downloads()           # v3.6.4：上次更新下載的安裝程式
    except Exception:
        pass
    sys.exit(app.exec())
