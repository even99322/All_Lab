"""量測「卡住」偵測：一段時間沒有任何進度（點、斜坡、狀態變化）就把所有執行緒的堆疊存到 LAB/logs。

量測中、準備中才計時；暫停、手動步進等待使用者時不算卡住。
門檻（settings.yaml run_defaults.stall_warn_s，預設 120 s）會自動放寬到「預估每點時間 × 5」以上。
存檔後在量測訊息顯示路徑，把那個檔案傳給開發者就能看出卡在哪一行（儀器沒回應、等鎖、網路…）。
"""
from __future__ import annotations

import datetime as _dt
import logging
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)
ACTIVITY = ("run.progress", "run.ramp", "point.shot", "point.committed", "run.state", "run.hook", "manual.shots",
            "manual.waiting", "run.manual")


def dump_threads(path: Path, title: str = "") -> Path:
    frames = sys._current_frames()
    names = {t.ident: t.name for t in threading.enumerate()}
    lines = [f"# {title}", f"# {_dt.datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    for ident, frame in frames.items():
        lines.append(f"--- 執行緒 {names.get(ident, '?')} ({ident}) ---")
        lines.extend(x.rstrip("\n") for x in traceback.format_stack(frame))
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class StallWatchdog:
    def __init__(self, runner, threshold_s: Optional[float] = None, point_s: float = 0.0) -> None:
        from ..settings import setting

        self.runner = runner
        base = float(threshold_s if threshold_s is not None else setting("run_defaults.stall_warn_s", 120))
        self.threshold = max(base, 5.0 * float(point_s or 0.0)) if base > 0 else 0.0
        self.last = time.monotonic()
        self.dumps = 0
        self._stop = threading.Event()
        self._tok = runner.bus.subscribe("*", self._on_event)

    def _on_event(self, topic: str, p: Dict[str, Any]) -> None:
        if topic in ACTIVITY and p.get("run_id", self.runner.run_id) == self.runner.run_id:
            self.last = time.monotonic()

    def start(self) -> "StallWatchdog":
        if self.threshold > 0:
            threading.Thread(target=self._loop, daemon=True, name=f"watchdog-{self.runner.run_id}").start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.runner.bus.unsubscribe(self._tok)

    def _loop(self) -> None:
        from ..paths import lab_path

        next_at = self.threshold
        while not self._stop.wait(min(5.0, self.threshold / 4)):
            r = self.runner
            st = getattr(r.state, "value", str(r.state))
            if st not in ("preparing", "running") or getattr(r, "manual", False):
                self.last = time.monotonic()
                next_at = self.threshold
                continue
            idle = time.monotonic() - self.last
            if idle < next_at:
                continue
            safe = "".join(c if c.isalnum() else "_" for c in r.run_id)
            path = lab_path("logs", f"stall_{safe}_{_dt.datetime.now():%H%M%S}.txt")
            try:
                dump_threads(path, f"量測 {r.run_id}：{idle:.0f} s 沒有進度（第 {r.index + 1} 點，狀態 {st}）")
                msg = (f"⚠ 量測已 {idle:.0f} 秒沒有進度（第 {r.index + 1} 點）。已把程式狀態存到 {path}，"
                       "請把這個檔傳給開發者")
            except OSError as e:
                msg = f"⚠ 量測已 {idle:.0f} 秒沒有進度（無法存檔：{e}）"
            log.warning(msg)
            r.bus.log(msg, "warning")
            self.dumps += 1
            next_at = idle + self.threshold * 2        # 之後間隔拉長，不洗版
