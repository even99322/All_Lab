"""分檔：流程圖中 Data 方塊放在外圈迴圈裡 → 外圈每走一個值就存一個檔。

實驗 YAML：
    output:
      split_by: [磁鐵 A 電流]          # 外層軸名稱（必須是最外面的連續幾層）

- 量測中：外圈值一換，上一段就在背景匯出（不擋量測）。
- 含手動保留點的段落留到 QC 後，由 Experiment.export() 一併匯出。
- 回溯跨回已匯出的段落 → 該段重新量完後以同檔名覆寫。
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

import numpy as np

from ..data.dataset import AxisSpec, Dataset, PointRecord
from .hooks import Hook

if TYPE_CHECKING:  # pragma: no cover
    from .experiment import Experiment, OutputPlan

log = logging.getLogger(__name__)
Key = Tuple[float, ...]


def segment_key(split_by: List[str], rec: PointRecord) -> Key:
    return tuple(float(rec.setpoints[a]) for a in split_by)


def segment_dataset(ds: Dataset, split_by: List[str], key: Key) -> Dataset:
    axes = [a for a in ds.axes if a.name not in split_by]
    info = {}
    for name, v in zip(split_by, key):
        a = next(x for x in ds.axes if x.name == name)
        info[name] = {"value": v, "unit": a.unit, "display_unit": a.display_unit,
                      "display_scale": a.display_scale, "target": a.target}
    seg = Dataset(ds.name, axes, ds.channels, {**ds.metadata, "segment": info})
    for r in ds.records:
        if segment_key(split_by, r) == key:
            seg.add(r)
    return seg


def segment_label(axes: Sequence[AxisSpec], split_by: List[str], key: Key) -> Tuple[int, str]:
    """(外圈索引, 檔名用標籤) 例如 (3, '52mA')。"""
    parts, index = [], 0
    for name, v in zip(split_by, key):
        a = next(x for x in axes if x.name == name)
        i = int(np.argmin(np.abs(np.asarray(a.values) - v)))
        index = index * len(a.values) + i
        parts.append(f"{v * a.display_scale:.6g}{a.display_unit}")
    return index, "_".join(parts)


class SegmentExportHook(Hook):
    """由 Experiment 自動加入，不需寫在 hooks 設定裡。"""

    registered_name = "segment_export"

    def __init__(self, experiment: "Experiment", output: "OutputPlan") -> None:
        super().__init__(enabled=True)
        self.exp = experiment
        self.output = output
        self.split_by = list(output.split_by or experiment.split_by)
        self.current: Optional[Key] = None
        self._q: "queue.Queue[Optional[Tuple[Key, Dataset]]]" = queue.Queue()
        self._closed = False
        self._worker = threading.Thread(target=self._work, name="segment-export", daemon=True)
        self._worker.start()

    def on_run_start(self, ctx) -> None:
        self.current = None

    def after_point(self, ctx, record):
        key = segment_key(self.split_by, record)
        if self.current is not None and key != self.current and ctx.dataset is not None:
            seg = segment_dataset(ctx.dataset, self.split_by, self.current)
            if seg.retained:
                ctx.log(f"段落 {self.current} 含手動保留點，QC 後再匯出")
            else:
                self._q.put((self.current, seg))
        self.current = key
        return None

    def on_rollback(self, ctx, index: int) -> None:
        sp = self.exp.plan.setpoints(index)
        key = tuple(float(sp[a]) for a in self.split_by)
        if key in self.output.exported:
            self.output.dirty.add(key)
        self.current = key

    def on_run_end(self, ctx) -> None:
        self.finish(wait=False)   # 最後一段交給 Experiment.export()（可能還要 QC）

    def finish(self, wait: bool = True, timeout: Optional[float] = None) -> None:
        """不再接受新段落；wait=True 時等背景匯出全部完成。"""
        if not self._closed:
            self._closed = True
            self._q.put(None)
        if wait:
            self._worker.join(timeout)

    def _work(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            key, seg = item
            try:
                self.exp.export_segment(seg, self.output, key)
            except Exception as e:  # noqa: BLE001
                self.exp.station.bus.log(f"❌ 段落匯出失敗 {key}：{e}", "error")

