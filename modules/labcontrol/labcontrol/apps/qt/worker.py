"""在背景執行緒做儀器通訊，結果回到 GUI 執行緒（避免 VISA 逾時時畫面卡住）。

    run_bg(fn, on_ok=lambda result: ..., on_err=lambda msg: ...)
    start_thread(fn)          # 不需要結果的背景工作

規則：Qt 前端的背景執行緒一律用這兩個函式啟動，不要直接 ``threading.Thread``。
背景工作的 closure 常常抓著視窗（``self``）；如果視窗在工作進行中被關閉，最後一個參考會在背景執行緒釋放，
Qt 物件就會在錯的執行緒被刪除（Qt 不允許，可能當機）。``start_thread`` 會把 closure 送回 GUI 執行緒再釋放。
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable, List, Optional

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal



class _Reaper(QObject):
    """在 GUI 執行緒接收背景工作用完的物件並釋放。"""
    drop = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.drop.connect(self._drop)

    def _drop(self, _obj: Any) -> None:     # 參數離開這個函式時在 GUI 執行緒被釋放
        pass


_reaper: Optional[_Reaper] = None


def _get_reaper() -> Optional[_Reaper]:
    global _reaper
    if _reaper is None and QCoreApplication.instance() is not None \
            and threading.current_thread() is threading.main_thread():
        _reaper = _Reaper()
    return _reaper


def start_thread(fn: Callable[[], Any], name: Optional[str] = None) -> threading.Thread:
    """在背景執行 fn；fn（及它抓住的視窗等 Qt 物件）在 GUI 執行緒釋放。須在 GUI 執行緒呼叫。"""
    reaper = _get_reaper()
    box: List[Callable[[], Any]] = [fn]
    del fn

    def run(box=box, reaper=reaper) -> None:
        f = box.pop()
        try:
            f()
        finally:
            if reaper is not None:
                try:
                    reaper.drop.emit(f)       # 排進 GUI 執行緒的事件持有最後一個參考
                except RuntimeError:          # 程式結束中
                    pass
            del f

    t = threading.Thread(target=run, daemon=True, name=name)
    t.start()
    return t


class _Dispatcher(QObject):
    """常駐在 GUI 執行緒的轉送器：背景工作完成後把 (callback, 結果) 送回 GUI 執行緒執行。

    0.0.12 以前每次 run_bg 建一個 QObject（_Relay），背景執行緒結束時對它 emit；它偶爾會在 emit 前後被回收，
    造成「wrapped C/C++ object of type _Relay has been deleted」與隨機 segfault。現在只有一個常駐物件。
    """
    call = pyqtSignal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._call)

    def _call(self, fn: Callable[[Any], None], arg: Any) -> None:
        try:
            fn(arg)
        except RuntimeError as e:                 # 視窗已關閉（底層 Qt 物件已刪除）：忽略
            if "has been deleted" not in str(e):
                traceback.print_exc()
        except Exception:  # noqa: BLE001
            traceback.print_exc()


_dispatcher: Optional[_Dispatcher] = None


def _get_dispatcher() -> Optional[_Dispatcher]:
    global _dispatcher
    if _dispatcher is None and QCoreApplication.instance() is not None \
            and threading.current_thread() is threading.main_thread():
        _dispatcher = _Dispatcher()
    return _dispatcher


def run_bg(fn: Callable[[], Any], on_ok: Optional[Callable[[Any], None]] = None,
           on_err: Optional[Callable[[str], None]] = None) -> threading.Thread:
    """在背景執行 fn；完成後在 GUI 執行緒呼叫 on_ok(結果) 或 on_err(訊息)。須在 GUI 執行緒呼叫。"""
    disp = _get_dispatcher()

    def run():
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001
            if on_err is not None and disp is not None:
                disp.call.emit(on_err, f"{type(e).__name__}: {e}" if str(e) else traceback.format_exc(limit=1))
            return
        if on_ok is not None and disp is not None:
            disp.call.emit(on_ok, res)

    return start_thread(run)
