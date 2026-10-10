"""記憶體中的量測資料模型，所有 writer / reader / 前端共用。

一個 Dataset =
    axes       掃描軸（例如 Average Current）
    channels   每個點量到的東西（例如 S21 向量、某台電表的數值）
    records    依序的點；每個點可以有多個 shot（手動模式保留的多筆 trace），selected 指向最後採用的那筆
    metadata   儀器 snapshot、程序設定、執行參數…（寫入檔案，未來可「用這個檔案的設定重跑」）
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass
class AxisSpec:
    name: str
    target: str
    unit: str                     # SI 單位（內部）
    values: np.ndarray
    display_unit: str = ""        # 匯出/顯示單位（例如 mA）
    display_scale: float = 1.0    # SI → 顯示單位的倍率（例如 1e3）

    def to_display(self, v):
        return np.asarray(v) * self.display_scale


@dataclass
class ChannelSpec:
    name: str
    unit: str = ""
    vector: bool = False
    complex: bool = False
    x_name: str = ""
    x_unit: str = ""
    x_values: Optional[np.ndarray] = None
    export_name: Optional[str] = None   # 例如 Labber 的 "VNA - S21"
    source: str = ""                    # 來源儀器 ref
    labber_group: str = ""              # Labber「Instrument config」下的群組名（舊檔為 "VNA"）
    labber_settings: List[list] = field(default_factory=list)


@dataclass
class PointRecord:
    index: int
    setpoints: Dict[str, float]
    shots: List[Dict[str, Any]]
    selected: int = -1
    retained: bool = False
    timestamp: float = field(default_factory=time.time)
    notes: Dict[str, Any] = field(default_factory=dict)

    @property
    def data(self) -> Dict[str, Any]:
        return self.shots[self.selected]

    def select(self, i: int) -> None:
        if not -len(self.shots) <= i < len(self.shots):
            raise IndexError(i)
        self.selected = i


class Dataset:
    def __init__(self, name: str, axes: List[AxisSpec], channels: List[ChannelSpec],
                 metadata: Optional[Dict[str, Any]] = None) -> None:
        self.name = name
        self.axes = axes
        self.channels = channels
        self.metadata: Dict[str, Any] = dict(metadata or {})
        self.records: List[PointRecord] = []

    # ---- 結構 -------------------------------------------------------------
    @property
    def shape(self) -> tuple:
        return tuple(len(a.values) for a in self.axes)

    @property
    def planned_points(self) -> int:
        return int(np.prod(self.shape)) if self.axes else 1

    def channel(self, name: str) -> ChannelSpec:
        for c in self.channels:
            if c.name == name:
                return c
        raise KeyError(name)

    # ---- 資料 -------------------------------------------------------------
    def add(self, rec: PointRecord) -> None:
        self.records.append(rec)

    def truncate(self, n: int) -> None:
        del self.records[n:]

    def __len__(self) -> int:
        return len(self.records)

    @property
    def retained(self) -> List[PointRecord]:
        return [r for r in self.records if r.retained]

    def setpoints(self, axis: Optional[str] = None) -> np.ndarray:
        if axis is not None:
            return np.array([r.setpoints[axis] for r in self.records])
        names = [a.name for a in self.axes]
        return np.array([[r.setpoints[n] for n in names] for r in self.records]).reshape(len(self.records), len(names))

    def column(self, channel: str) -> np.ndarray:
        """所有點「採用中」的 shot 堆疊成陣列：scalar → (N,)，vector → (N, n_x)。"""
        if not self.records:
            return np.array([])
        return np.stack([np.asarray(r.data[channel]) for r in self.records])

    def summary(self) -> str:
        ax = ", ".join(f"{a.name}[{len(a.values)}]" for a in self.axes)
        ch = ", ".join(c.name for c in self.channels)
        return f"Dataset '{self.name}': axes={ax}; channels={ch}; points={len(self)}/{self.planned_points}"
