"""Experiment：把一份實驗 YAML 組裝成可執行的 Runner，並處理輸出路徑與匯出。

前端（CLI / Qt / web）的標準流程：
    exp = Experiment.from_file("LAB/experiments/dc_vna_sweep.yaml", station)
    out = exp.plan_output()                    # 今日資料夾、檔名、raw 路徑
    runner = exp.create_runner(out)
    runner.start()  (或 runner.run())
    ... 量測結束 ...
    if exp.needs_review(ds):  → 前端開 QC 視窗讓使用者挑選 → exp.export(ds, out)
    else:                     → exp.export(ds, out)
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import load_config
from ..core.errors import ConfigError
from ..core.registry import PROCEDURES, WRITERS
from ..core.station import Station
from ..data.dataset import Dataset
from ..data.naming import day_folder, stale_date_in_filename, unique_filename
from ..data.writers.hdf5_native import HDF5Writer
from ..settings import setting
from .hooks import build_hook
from .runner import RunOptions, Runner
from .segments import SegmentExportHook, segment_dataset, segment_key, segment_label
from .sweep import SweepPlan


@dataclass
class OutputPlan:
    folder: Path
    file_name: str
    collision: str = "underscore"
    raw_path: Optional[Path] = None
    exporters: List[Dict[str, Any]] = field(default_factory=list)
    date_warning: Optional[Tuple[str, str, str]] = None   # (檔名日期, 今天, 建議檔名)；由前端決定是否採用
    split_pattern: str = "{stem}_{index:03d}_{label}"     # 分檔檔名；label 例如 52mA
    exported: Dict[tuple, List[Path]] = field(default_factory=dict)   # 已匯出的段落 → 檔案
    dirty: set = field(default_factory=set)                           # 回溯後需重新匯出的段落
    split_by: List[str] = field(default_factory=list)                 # 這個輸出的分檔軸（最外面幾層）
    extras: List["OutputPlan"] = field(default_factory=list)          # 其他 Data 方塊（output.extra）


class Experiment:
    def __init__(self, station: Station, config: Dict[str, Any]) -> None:
        self.station = station
        self.config = copy.deepcopy(config)
        self.name = self.config.get("name", "experiment")
        proc_cfg = dict(self.config.get("procedure") or {})
        ptype = proc_cfg.pop("type", None)
        if not ptype:
            raise ConfigError("experiment 缺少 procedure.type")
        self.procedure = PROCEDURES.get(ptype)(station, proc_cfg, setup=self.config.get("setup"))
        self.plan = SweepPlan.from_config(self.config.get("sweep") or [], snake=bool(self.config.get("snake", False)))
        self.options = RunOptions.from_config(self.config.get("run"))
        self.hook_configs = list(self.config.get("hooks") or [])
        self.output_cfg = dict(self.config.get("output") or {})
        self.split_by: List[str] = list(self.output_cfg.get("split_by") or [])
        self.extra_cfg: List[Dict[str, Any]] = [dict(e) for e in self.output_cfg.get("extra") or []]
        for by in [self.split_by] + [list(e.get("split_by") or []) for e in self.extra_cfg]:
            outer = [a.name for a in self.plan.axes[:len(by)]]
            if by and outer != by:
                raise ConfigError(f"output.split_by 必須是最外層的連續掃描軸（依序 {outer}），目前是 {by}")
        self._segment_hooks: Dict[int, SegmentExportHook] = {}

    @classmethod
    def from_file(cls, path: str | Path, station: Station) -> "Experiment":
        return cls(station, load_config(path))

    # ---- 輸出 -------------------------------------------------------------------
    def plan_output(self, now: Optional[_dt.datetime] = None, file_name: Optional[str] = None,
                    root: Optional[str] = None) -> OutputPlan:
        now = now or _dt.datetime.now()
        o = self.output_cfg
        # 優先順序：呼叫參數 > 實驗 output: > settings.yaml data:
        root = root or o.get("root") or setting("data.root", "") or "./data"
        folder = day_folder(root, o.get("folder_pattern") or setting("data.folder_pattern", "{yyyy}/{mm}/Data_{mmdd}"),
                            now)
        fname = file_name or o.get("file_name") or f"{self.name}.hdf5"
        raw = None
        if o.get("raw", setting("data.raw", True)):
            raw = folder / setting("data.raw_folder", "_raw") / f"{Path(fname).stem}_{now:%H%M%S}.lm.h5"
        exporters = [dict(e) for e in o.get("export", [])]
        pattern = o.get("split_pattern") or setting("data.split_pattern", "{stem}_{index:03d}_{label}")
        plan = OutputPlan(folder=folder, file_name=fname,
                          collision=o.get("collision") or setting("data.collision", "underscore"),
                          raw_path=raw, exporters=exporters, date_warning=stale_date_in_filename(fname, now),
                          split_pattern=pattern, split_by=list(self.split_by))
        for e in self.extra_cfg:
            plan.extras.append(OutputPlan(folder=folder, file_name=e.get("file_name") or f"{Path(fname).stem}_2.hdf5",
                                          collision=e.get("collision") or plan.collision,
                                          exporters=[dict(x) for x in e.get("export", [])], split_pattern=pattern,
                                          split_by=list(e.get("split_by") or [])))
        if plan.date_warning:
            old, today, suggest = plan.date_warning
            self.station.bus.log(f"📝 檔名日期 {old} 不是今天 {today}（建議：{suggest}）", "warning")
        return plan

    # ---- 執行 -------------------------------------------------------------------
    def create_runner(self, output: Optional[OutputPlan] = None, **option_overrides: Any) -> Runner:
        opts = dataclasses.replace(self.options, **option_overrides)
        writers = [HDF5Writer(output.raw_path)] if (output and output.raw_path) else []
        meta = {"experiment": {k: v for k, v in self.config.items() if not k.startswith("_")}}
        if output:
            meta["output"] = {"folder": str(output.folder), "file_name": output.file_name}
        hooks = [build_hook(h) for h in self.hook_configs]
        self._segment_hooks = {}
        if output is not None:
            for plan in [output, *output.extras]:
                if plan.split_by and plan.exporters:
                    h = SegmentExportHook(self, plan)
                    self._segment_hooks[id(plan)] = h
                    hooks.append(h)
        return Runner(self.station, self.procedure, self.plan, options=opts, hooks=hooks, writers=writers,
                      name=self.name, metadata=meta)

    @staticmethod
    def needs_review(dataset: Optional[Dataset]) -> bool:
        return bool(dataset and dataset.retained)

    def export(self, dataset: Dataset, output: OutputPlan) -> List[Path]:
        """依 output.export 設定匯出（同名檔在此時才決定遞增編號，與舊版相同）。"""
        if not len(dataset):
            self.station.bus.log("⚠️ 無有效量測數據，取消存檔", "warning")
            return []
        output.folder.mkdir(parents=True, exist_ok=True)
        paths: List[Path] = []
        for plan in [output, *output.extras]:
            paths.extend(self._export_plan(dataset, plan))
        return paths

    def _export_plan(self, dataset: Dataset, output: OutputPlan) -> List[Path]:
        if output.split_by:
            h = self._segment_hooks.get(id(output))
            if h is not None:
                h.finish()                          # 等背景段落匯出做完
            paths: List[Path] = []
            keys = list(dict.fromkeys(segment_key(output.split_by, r) for r in dataset.records))
            for key in keys:
                if key in output.exported and key not in output.dirty:
                    paths.extend(output.exported[key])
                    continue
                paths.extend(self.export_segment(segment_dataset(dataset, output.split_by, key), output, key))
            return paths
        paths = []
        for e in output.exporters:
            e = dict(e)
            cls = WRITERS.get(e.pop("type"))
            writer = cls(**e)
            stem, ext = Path(output.file_name).stem, Path(output.file_name).suffix
            name = output.file_name if ext == writer.extension else stem + writer.extension
            name = unique_filename(output.folder, name, output.collision)
            path = writer.export(dataset, output.folder / name)
            self.station.bus.log(f"✅ 已存檔：{path}")
            self.station.bus.publish("data.exported", path=str(path), writer=cls.registered_name,
                                     **self._export_info(e))
            paths.append(path)
        return paths

    def _export_info(self, exporter: Dict[str, Any]) -> Dict[str, Any]:
        """data.exported 事件附帶的資訊：方案（寫進數據檔，之後可以拖回來套用）與標籤（QEL Lab 共用標籤）。"""
        return {"name": self.name, "scheme": self.config.get("scheme"), "tags": list(exporter.get("tags") or [])}

    def export_segment(self, seg: Dataset, output: OutputPlan, key: tuple) -> List[Path]:
        """匯出一個段落（外圈某個值）。已匯出過的段落覆寫同一個檔名。"""
        output.folder.mkdir(parents=True, exist_ok=True)
        index, label = segment_label([a.spec() for a in self.plan.axes], output.split_by or self.split_by, key)
        stem = Path(output.file_name).stem
        base = output.split_pattern.format(stem=stem, index=index + 1, label=label)
        old = {p.suffix: p for p in output.exported.get(key, [])}
        paths: List[Path] = []
        for e in output.exporters:
            e = dict(e)
            cls = WRITERS.get(e.pop("type"))
            writer = cls(**e)
            path = old.get(writer.extension) or output.folder / unique_filename(
                output.folder, base + writer.extension, output.collision)
            writer.export(seg, path)
            self.station.bus.log(f"✅ 已存檔（{label}）：{path.name}")
            self.station.bus.publish("data.exported", path=str(path), writer=cls.registered_name, segment=key,
                                     **self._export_info(e))
            paths.append(path)
        output.exported[key] = paths
        output.dirty.discard(key)
        return paths
