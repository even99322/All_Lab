"""Writer 介面。

一個 writer 可以支援兩種用法（都可選擇性實作）：
  串流：open → write_point（每點）→ truncate（回溯）→ close        由 Runner 呼叫，當機不丟資料
  匯出：export(dataset, path) → 實際寫出的路徑                      量測結束 / QC 挑選後呼叫
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..dataset import Dataset, PointRecord


class Writer:
    #: 預設副檔名
    extension = ".h5"

    def open(self, dataset: Dataset) -> None: ...
    def write_point(self, dataset: Dataset, record: PointRecord) -> None: ...
    def truncate(self, dataset: Dataset, n: int) -> None: ...
    def close(self, dataset: Optional[Dataset], status: str = "") -> None: ...

    def export(self, dataset: Dataset, path: str | Path) -> Path:
        raise NotImplementedError(f"{type(self).__name__} 不支援 export")
