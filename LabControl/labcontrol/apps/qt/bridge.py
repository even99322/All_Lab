"""EventBus → Qt signal。核心在量測執行緒 publish，Qt 會自動把 signal 排進 GUI 執行緒。

    bridge = QtEventBridge(station.bus)
    bridge.event.connect(lambda topic, payload: ...)
    bridge.on("point.shot", self.update_plot)     # 只接特定主題
"""
from __future__ import annotations

from typing import Any, Callable, Dict

from PyQt6.QtCore import QObject, pyqtSignal

from ...core.events import EventBus


class QtEventBridge(QObject):
    event = pyqtSignal(str, object)

    def __init__(self, bus: EventBus, pattern: str = "*", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bus = bus
        self._handlers: Dict[str, list] = {}
        self._token = bus.subscribe(pattern, self._forward)
        self.event.connect(self._dispatch)
        token = self._token
        self.destroyed.connect(lambda *_a: bus.unsubscribe(token))     # 1.0.10：元件刪掉後不再轉送（避免當機）

    def _forward(self, topic: str, payload: Dict[str, Any]) -> None:
        try:
            self.event.emit(topic, payload)
        except RuntimeError:                       # Qt 物件已經刪除
            self.bus.unsubscribe(self._token)

    def on(self, topic: str, fn: Callable[[Dict[str, Any]], None]) -> None:
        self._handlers.setdefault(topic, []).append(fn)

    def _dispatch(self, topic: str, payload: Dict[str, Any]) -> None:
        for fn in self._handlers.get(topic, []):
            fn(payload)

    def close(self) -> None:
        self.bus.unsubscribe(self._token)
