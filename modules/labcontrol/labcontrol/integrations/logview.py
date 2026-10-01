"""logview 整合介面（骨架）。

logview 可以用三種深度接入，彼此不衝突，可以逐步做：

A. 檔案層（v0.1 已可用）
   量測輸出維持與舊 save_labber.py 相同的 Labber 結構，logview 不用改就能讀。
   另有 labcontrol 原生 HDF5（自我描述），logview 可呼叫 labcontrol.data.readers.open_dataset(path)
   取得統一的 Dataset，新增儀器/通道不用改讀檔程式。

B. 即時層（v0.4）
   logview 實作下方 Viewer 介面，用 attach_viewer(bus, viewer) 訂閱量測事件，量測中即時更新。
   - 同一個 Python 程序（logview 也是 PyQt）：直接 attach，搭配 apps.qt.bridge 轉到 GUI 執行緒。
   - 不同程序：v1.0 的 Instrument Server 會把同一組事件經由網路（ZeroMQ / WebSocket）發佈。

C. 指令層（v0.4+）
   原生檔的 metadata 保存完整的實驗設定與儀器 snapshot，
   logview 可以提供「用這個檔案的設定重新量測」→ 呼叫 Experiment.from_metadata(...)。

目前這裡提供 Viewer 協定、事件轉接器，以及「量測完成後自動用 logview 開檔」的 LogViewLauncher。
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence

from ..core.events import EventBus
from ..data.dataset import Dataset, PointRecord

log = logging.getLogger(__name__)


class Viewer(Protocol):
    def on_run_started(self, dataset: Dataset) -> None: ...
    def on_point(self, dataset: Dataset, record: PointRecord) -> None: ...
    def on_shot(self, index: int, shot: Dict[str, Any]) -> None: ...
    def on_run_finished(self, dataset: Optional[Dataset], status: str) -> None: ...


def attach_viewer(bus: EventBus, viewer: Viewer) -> List[int]:
    """把 EventBus 事件轉成 Viewer 方法呼叫；回傳訂閱 token（detach 用）。"""
    state: Dict[str, Optional[Dataset]] = {"ds": None}

    def started(topic, p):
        state["ds"] = p["dataset"]
        viewer.on_run_started(p["dataset"])

    def committed(topic, p):
        if state["ds"] is not None:
            viewer.on_point(state["ds"], p["record"])

    def shot(topic, p):
        viewer.on_shot(p["index"], p["shot"])

    def finished(topic, p):
        viewer.on_run_finished(p.get("dataset"), p.get("status", ""))

    return [bus.subscribe("run.started", started), bus.subscribe("point.committed", committed),
            bus.subscribe("point.shot", shot), bus.subscribe("run.finished", finished)]


def detach_viewer(bus: EventBus, tokens: Sequence[int]) -> None:
    for t in tokens:
        bus.unsubscribe(t)


class LogViewLauncher:
    """匯出完成後用 logview 開檔。LAB/settings.yaml：
        logview:
          command: ["C:/LocalEnvs/xxx/python.exe", "C:/tools/logview/main.py", "{path}"]
    TODO：確認 logview 的啟動方式與參數後調整。
    """

    def __init__(self, command: Optional[Sequence[str]] = None) -> None:
        if command is None:
            from ..settings import setting
            command = setting("logview.command", []) or []
        self.command = list(command)

    def open(self, path: str | Path) -> bool:
        if not self.command:
            return False
        cmd = [c.replace("{path}", str(path)) for c in self.command]
        try:
            subprocess.Popen(cmd)
            return True
        except Exception:  # noqa: BLE001
            log.exception("無法啟動 logview：%s", cmd)
            return False


class LogViewBridge:
    """即時層範例實作（TODO：依 logview 的 API 填入）。"""

    def __init__(self, logview_window: Any = None) -> None:
        self.win = logview_window

    def on_run_started(self, dataset: Dataset) -> None:
        log.info("logview: 新量測 %s", dataset.summary())

    def on_point(self, dataset: Dataset, record: PointRecord) -> None:
        pass  # 例：self.win.append_trace(record.setpoints, record.data)

    def on_shot(self, index: int, shot: Dict[str, Any]) -> None:
        pass

    def on_run_finished(self, dataset: Optional[Dataset], status: str) -> None:
        pass
