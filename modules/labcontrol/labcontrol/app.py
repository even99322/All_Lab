"""Lab Control 主程式（main.py 呼叫這裡）。

    python main.py                 開啟量測方案編輯器（儀器清單 = LAB/instruments.yaml）
    python main.py 方案.yaml       直接開啟某個方案
    python main.py --sim           模擬模式（沒有接儀器也能操作）
    python main.py --lab 其他.yaml 使用另一份儀器清單

第一次啟動會建立 LAB 資料夾（預設 C:\\Users\\even9\\LAB）並放入預設設定檔。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import APP_NAME, __version__
from .paths import ensure_lab_home, lab_home, lab_path
from .settings import setting

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
    ap = argparse.ArgumentParser(prog="Lab Control", description=f"{APP_NAME} {__version__}")
    ap.add_argument("scheme", nargs="?", help="要開啟的方案檔（.scheme.yaml），或數據檔（.hdf5，套用它的量測設置）")
    ap.add_argument("--lab", help="儀器清單（預設 LAB/instruments.yaml）")
    ap.add_argument("--sim", action="store_true", help="模擬模式（覆寫 settings.yaml app.simulate）")
    ap.add_argument("--root", help="覆寫資料根目錄（預設 settings.yaml data.root）")
    ap.add_argument("--server", action="store_true", help="只開儀器伺服器（連線、直接控制、驅動測試）")
    ap.add_argument("--node", action="store_true", help="開啟後立即以量測節點上線（其他電腦可透過 NAS 控制）")
    ap.add_argument("--theme", choices=["light", "dark", "system"], help="外觀（覆寫 settings.yaml app.theme，不寫檔）")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    a = ap.parse_args(argv)

    created = ensure_lab_home()
    _setup_logging()

    from PyQt6 import QtGui, QtWidgets

    from .core.station import Station
    from .scheme import Scheme

    if sys.platform == "win32":   # 工作列顯示自己的 icon，而不是 python.exe 的
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"LabControl.{__version__}")
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

        from .integrations import qel

        scheme = None
        if a.scheme and qel.is_data_file(a.scheme):     # 數據檔：讀回當時的量測設置（QEL Lab）
            try:
                scheme = Scheme.from_dict(qel.scheme_from_file(a.scheme))
            except Exception as e:  # noqa: BLE001
                QtWidgets.QMessageBox.warning(None, APP_NAME, str(e))
        elif a.scheme:
            scheme = Scheme.load(a.scheme)
        w = LabControlWindow(st, scheme, root=a.root)
        if a.scheme and not qel.is_data_file(a.scheme):
            w.doc.path = str(Path(a.scheme))
        qel.attach_window(w, st, __version__)            # QEL Lab 大程式：拖數據檔套用設置、登錄數據、共用標籤
        w.show()
        if a.node or setting("remote.node_enabled", False):
            w.act_node.setChecked(True)
    if created:
        w.statusBar().showMessage(f"已建立設定資料夾 {lab_home()}（{len(created)} 個預設檔）", 15000)
    rc = app.exec()
    if hasattr(w, "_shutdown_remote"):
        w._shutdown_remote()       # 節點離線（更新重啟時也會走這裡）
    from .measure.runner import Runner
    Runner.stop_all()
    st.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
