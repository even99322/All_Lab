"""EventBus：核心與前端之間唯一的溝通管道。

核心（引擎、儀器）只 publish 事件，不知道誰在聽；
前端（PyQt、Flask、logview、CLI）只 subscribe，不直接碰引擎內部狀態。
這樣核心不需要 import PyQt6，也能同時接多個前端。

主題命名慣例（topic）：
    log                    一般訊息          payload: level, message
    run.state              引擎狀態變化      payload: state, previous
    run.started            開始量測          payload: dataset (Dataset, 只含結構)
    run.progress           進度              payload: index, total, elapsed, eta, setpoints
    point.shot             單次量測結果      payload: index, shot, n_shots
    point.committed        一個點確定寫入    payload: record
    point.rollback         回溯              payload: index
    run.finished           量測結束          payload: dataset, status
    instrument.connected   儀器連線          payload: name, idn
    instrument.error       儀器錯誤          payload: name, error
"""
from __future__ import annotations

import fnmatch
import itertools
import logging
import threading
from typing import Any, Callable, Dict, Tuple

log = logging.getLogger(__name__)
bus_log = logging.getLogger("labcontrol.bus")  # bus.log() 的訊息；已有前端顯示時可關閉 propagate

Callback = Callable[[str, Dict[str, Any]], None]


class EventBus:
    """執行緒安全的 publish/subscribe。

    - callback 在 *publish 的那條執行緒* 上被同步呼叫。
      GUI 端請透過 apps.qt.bridge.QtEventBridge 轉成 Qt signal（自動跨執行緒排隊）。
    - 訂閱者丟出的例外會被記錄但不會中斷量測。
    - pattern 支援萬用字元，例如 "run.*"、"*"。
    """

    def __init__(self) -> None:
        self._subs: Dict[int, Tuple[str, Callback]] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def subscribe(self, pattern: str, callback: Callback) -> int:
        with self._lock:
            token = next(self._ids)
            self._subs[token] = (pattern, callback)
            return token

    def unsubscribe(self, token: int) -> None:
        with self._lock:
            self._subs.pop(token, None)

    def publish(self, topic: str, **payload: Any) -> None:
        with self._lock:
            targets = [cb for pat, cb in self._subs.values() if fnmatch.fnmatchcase(topic, pat)]
        for cb in targets:
            try:
                cb(topic, payload)
            except Exception:  # noqa: BLE001 - 訂閱者錯誤不應影響量測
                log.exception("EventBus subscriber failed on topic %s", topic)

    # 方便的 log 快捷方式
    def log(self, message: str, level: str = "info") -> None:
        getattr(bus_log, level if level in ("debug", "info", "warning", "error") else "info")(message)
        self.publish("log", level=level, message=message)
