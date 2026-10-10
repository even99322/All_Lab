"""
S 參數擬合 GUI —— 程式進入點
執行：python main.py

結構：
    fitapp/core/        純運算（無 Qt）：data_io, formula, fitting, batch, report, settings
    fitapp/workers/     背景工作：data_worker (QThread 讀檔)、fit_engine (子行程擬合)
    fitapp/ui/          介面元件：main_window, plots, param_table, batch_panel
    fitapp/controller.py        單次擬合與共用流程
    fitapp/batch_controller.py  連續擬合
    fitapp/session.py           操作設定保存 / 還原

設定檔：config/fit_gui_session.json（程式資料夾不可寫入時放在 ~/.fit_gui/config/）
公式庫：config/formula_library.json + config/formulas/
閃退記錄：config/logs/fit_gui_crash.log
擬合結果：使用者指定的輸出資料夾（預設為數據檔旁的 fit_results/）
"""
import os
import sys
import time
import signal
import threading
import traceback
import faulthandler
import multiprocessing as mp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_ctrl = None
_fault_file = None


def _log_path():
    from fitapp.core.paths import sub_dir
    return os.path.join(sub_dir("logs"), "fit_gui_crash.log")


def _emergency_save():
    if _ctrl is None:
        return
    for fn in (lambda: _ctrl.session.save(force=True), lambda: _ctrl.batch.autosave(force=True)):
        try:
            fn()
        except Exception:
            pass


def install_crash_handlers():
    global _fault_file
    log = _log_path()
    # 1) 原生層級崩潰（segfault 等）→ 寫出 traceback
    try:
        _fault_file = open(log, "a", encoding="utf-8")
        faulthandler.enable(_fault_file)
    except OSError:
        faulthandler.enable()

    def write_log(text):
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{text}\n")
        except OSError:
            pass

    # 2) 未捕捉的 Python 例外：PyQt6 預設會直接結束程式，這裡改成記錄 + 保存 + 提示
    def excepthook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        sys.__stderr__.write(text)
        write_log(text)
        _emergency_save()
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is not None:
                QMessageBox.critical(None, "未預期的錯誤",
                                     f"{value}\n\n設定與連續擬合結果已自動保存。\n"
                                     f"詳細內容已寫入：\n{log}\n\n{text[-1500:]}")
        except Exception:
            pass

    sys.excepthook = excepthook
    threading.excepthook = lambda a: excepthook(a.exc_type, a.exc_value, a.exc_traceback)

    # 3) 被系統終止 / Ctrl+C
    def on_signal(signum, frame):
        _emergency_save()
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.quit()

    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None),
                getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            try:
                signal.signal(sig, on_signal)
            except (ValueError, OSError):
                pass


def main():
    global _ctrl
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from fitapp.ui.main_window import MainWindow
    from fitapp.controller import FitController

    install_crash_handlers()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    _ctrl = FitController(win)
    app.aboutToQuit.connect(_ctrl.shutdown)

    # 讓 Python 有機會處理訊號（Qt 事件迴圈內預設收不到 Ctrl+C）
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(300)

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    mp.freeze_support()  # 打包成 exe 時子行程需要
    main()
