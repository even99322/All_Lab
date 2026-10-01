"""背景執行（網路請求不卡住畫面），結果回到 GUI 執行緒。

只用一個常駐在 GUI 執行緒的轉送物件（不再每次建立 QObject），背景執行緒結束時不會碰到已刪除的 Qt 物件。
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable, Optional

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal


class _Dispatcher(QObject):
    call = pyqtSignal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._call)

    def _call(self, fn: Callable[[Any], None], arg: Any) -> None:
        try:
            fn(arg)
        except RuntimeError as e:          # 視窗已關閉
            if "has been deleted" not in str(e):
                traceback.print_exc()
        except Exception:  # noqa: BLE001
            traceback.print_exc()


_dispatcher: Optional[_Dispatcher] = None


def _get() -> Optional[_Dispatcher]:
    global _dispatcher
    if _dispatcher is None and QCoreApplication.instance() is not None \
            and threading.current_thread() is threading.main_thread():
        _dispatcher = _Dispatcher()
    return _dispatcher


def run_bg(fn: Callable[[], Any], on_ok: Optional[Callable[[Any], None]] = None,
           on_err: Optional[Callable[[str], None]] = None) -> None:
    disp = _get()

    def run():
        try:
            r = fn()
        except Exception as e:  # noqa: BLE001
            if on_err is not None and disp is not None:
                disp.call.emit(on_err, str(e) or type(e).__name__)
            return
        if on_ok is not None and disp is not None:
            disp.call.emit(on_ok, r)
    threading.Thread(target=run, daemon=True).start()
