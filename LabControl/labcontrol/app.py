"""Lab Control 主程式（main.py 呼叫這裡）。

    python main.py                 開啟量測方案編輯器（儀器清單 = LAB/instruments.yaml）
    python main.py 方案.yaml       直接開啟某個方案
    python main.py --sim           模擬模式（沒有接儀器也能操作）
    python main.py --lab 其他.yaml 使用另一份儀器清單
    LabControl.exe --self-test     打包版自我檢查（打包腳本會自動跑）

第一次啟動會建立 LAB 資料夾（預設 C:\\Users\\even9\\LAB）並放入預設設定檔。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import APP_NAME, __version__
from .paths import ensure_lab_home, lab_home, lab_path
from .settings import setting

# Windows 工作列 / 開始功能表的程式 ID（1.0.3 起固定，不帶版本號；安裝程式的捷徑用同一個）
APP_USER_MODEL_ID = "CCUQEL.LabControl"

ASSETS = Path(__file__).resolve().parent.parent / "assets"
ICON_FILES = ("icon.ico", "icon.png")


def icon_path() -> Optional[Path]:
    for name in ICON_FILES:
        for base in (ASSETS, Path(__file__).with_name("assets")):
            if (base / name).exists():
                return base / name
    return None


def _setup_logging() -> None:
    logdir = lab_path("logs")
    logdir.mkdir(parents=True, exist_ok=True)
    h = logging.FileHandler(logdir / "labcontrol.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    lg = logging.getLogger("labcontrol")
    lg.addHandler(h)
    lg.setLevel(logging.INFO)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in args:                  # 打包版自我檢查（不開視窗、不碰 LAB 資料夾）
        from .self_test import run as self_test

        return self_test()
    from .selfupdate import cleanup_after_update

    cleanup_after_update()                     # 上次程式內更新留下的舊執行檔
    ap = argparse.ArgumentParser(prog="Lab Control", description=f"{APP_NAME} {__version__}")
    ap.add_argument("scheme", nargs="?", help="要開啟的方案檔（.scheme.yaml）")
    ap.add_argument("--lab", help="儀器清單（預設 LAB/instruments.yaml）")
    ap.add_argument("--sim", action="store_true", help="模擬模式（覆寫 settings.yaml app.simulate）")
    ap.add_argument("--root", help="覆寫資料根目錄（預設 settings.yaml data.root）")
    ap.add_argument("--server", action="store_true", help="只開儀器伺服器（連線、直接控制、驅動測試）")
    ap.add_argument("--node", action="store_true", help="開啟後立即以量測節點上線（其他電腦可透過 NAS 控制）")
    ap.add_argument("--target", help="開啟後「執行於」選這個量測節點（例如 LAB-A）")
    ap.add_argument("--minimized", action="store_true", help="視窗縮小開啟（量測電腦開機自動開啟時用）")
    ap.add_argument("--theme", choices=["light", "dark", "system"], help="外觀（覆寫 settings.yaml app.theme，不寫檔）")
    ap.add_argument("--apply", metavar="數據檔", help="套用這個數據檔（.nc / .hdf5）的量測設定；Lab Control 已經開著就交給它")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    a = ap.parse_args(argv)
    if a.scheme and Path(a.scheme).suffix.lower() in (".nc", ".hdf5", ".h5") and not a.apply:
        a.apply, a.scheme = a.scheme, None        # 「用 Lab Control 開啟」數據檔 = 套用它的設定

    created = ensure_lab_home()
    _setup_logging()
    try:
        from .autostart import refresh as _refresh_autostart

        _refresh_autostart()                   # 改用安裝程式裝之後，開機自動開啟跟著換到新位置
    except Exception:  # noqa: BLE001
        pass

    from PyQt6 import QtCore, QtGui, QtWidgets

    from .core.station import Station
    from .scheme import Scheme

    if sys.platform == "win32":   # 工作列顯示自己的 icon，而不是 python.exe 的
        try:
            import ctypes
            # 1.0.3：固定的 ID（以前帶版本號 → 每次更新 Windows 都當成另一個程式，釘選在工作列的就失效）
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:  # noqa: BLE001
            pass

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    from .apps.qt import theme
    theme.apply(app, a.theme)
    ic = icon_path()
    if ic:
        app.setWindowIcon(QtGui.QIcon(str(ic)))
    if not (a.server or a.node or a.minimized):
        from .apps.qt.local_link import forward

        if forward(a.apply):                    # 已經開著：交給它（視窗跳到前面），這裡結束
            return 0

    try:
        st = Station.from_lab(a.lab, simulate=True if a.sim else None)
    except Exception as e:  # noqa: BLE001 — 設定檔錯誤：告訴使用者去哪裡改
        QtWidgets.QMessageBox.critical(None, APP_NAME, f"讀取儀器清單失敗：\n{e}\n\n設定資料夾：{lab_home()}")
        return 1

    auto = [n for n in (setting("server.auto_connect", []) or []) if n in st.instruments]
    if auto:   # 開啟時自動（唯讀）連線；失敗只記錄，不擋住程式
        from .apps.qt.worker import run_bg
        run_bg(lambda: st.connect(auto), None, lambda m: logging.getLogger("labcontrol").warning("自動連線失敗：%s", m))

    if a.server:
        from .apps.qt.server import InstrumentServerWindow

        w = InstrumentServerWindow(st)
        w.show()
    else:
        from .apps.qt.workbench import LabControlWindow

        scheme = Scheme.load(a.scheme) if a.scheme else None
        w = LabControlWindow(st, scheme, root=a.root)
        if a.scheme:
            w.doc.path = str(Path(a.scheme))
        w.showMinimized() if a.minimized else w.show()
        if a.node or setting("remote.node_enabled", False):
            w.act_node.setChecked(True)
        if a.target:
            w.want_target(a.target)
        from .apps.qt.local_link import LocalLink

        w.local_link = LocalLink(w.handle_local_message, w)    # 讀檔程式「介面 → 量測」、--apply
        w.local_link.start()
        if a.apply:
            QtCore.QTimer.singleShot(600, lambda: w.handle_local_message({"cmd": "apply", "path": a.apply}))
    if created:
        w.statusBar().showMessage(f"已建立設定資料夾 {lab_home()}（{len(created)} 個預設檔）", 15000)
    rc = app.exec()
    from . import selfupdate

    if selfupdate.EXITING:                 # 0.0.31：要換新版：清理卡住也一定在 15 秒內結束
        selfupdate.hard_exit_after(15.0)
    if hasattr(w, "_shutdown_remote"):
        w._shutdown_remote()       # 節點離線（更新重啟時也會走這裡）
    from .measure.runner import Runner
    Runner.stop_all()
    st.close()
    if selfupdate.EXITING:                 # 0.0.31：要換新版：不等背景執行緒（安裝程式在等這個程式結束）
        flush_streams()
        logging.shutdown()
        os._exit(rc or 0)
    return rc


def flush_streams() -> None:
    """1.0.9：視窗版（PyInstaller --windowed）沒有主控台，sys.stdout / sys.stderr 是 None——更新重啟時不能因此當掉。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    sys.exit(main())
