"""v3.9：「執行步驟」清單——目前做到哪、還剩哪些步驟、各花多久、預估還要多久。

工作程序送來：
    ('plan', [[id, 文字], …])        這次任務的所有小步驟
    ('step', id, 'run'|'done'|'fail', 說明)
    ('progress', 0–1 或 None, 說明)   目前小步驟的進度（AEDT Simulation Monitor 或 2D 熱圖第幾點）
預估：每個小步驟上次花的時間（記在設定檔 step_durations，指數平均）；沒有紀錄的用預設值。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

DEFAULT_S = {"connect": 20, "BUILD": 90, "YIG": 30, "SIM_S:setup": 10, "SIM_S:solve": 1200, "SIM_S:export": 20,
             "SIM_S:yig": 300, "SIM_FIELDS:solve": 600, "SIM_FIELDS:plots": 30, "EXPORT_GIF": 180, "CLOSE": 30,
             "MAP2D:setup": 15, "MAP2D:solve": 3600, "MAP2D:export": 30}
ICON = {"wait": "⏳ 待執行", "run": "▶ 執行中", "done": "✅ 完成", "fail": "❌ 失敗", "skip": "⏭ 沒有執行"}


def fmt_s(sec: float) -> str:
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class StepPanel(QTreeWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHeaderLabels(["步驟", "狀態", "耗時 / 預估", "目前"])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        hh = self.header()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.setMaximumHeight(170)
        self.setMinimumHeight(110)
        self.history: Dict[str, float] = {}      # id → 秒（指數平均）
        self.rows: Dict[str, dict] = {}
        self.order: List[str] = []
        self.frac: Optional[float] = None

    # ---- 訊息 ----
    def set_plan(self, items) -> None:
        self.clear()
        self.rows, self.order, self.frac = {}, [], None
        for sid, label in items:
            self._add(sid, label)

    def _add(self, sid: str, label: str) -> dict:
        it = QTreeWidgetItem([label or sid, ICON["wait"], "", ""])
        self.addTopLevelItem(it)
        row = {"item": it, "status": "wait", "t0": None, "t": 0.0, "label": label or sid}
        self.rows[sid] = row
        self.order.append(sid)
        self._est(sid)
        return row

    def expected(self, sid: str) -> float:
        if sid in self.history:
            return self.history[sid]
        return DEFAULT_S.get(sid, DEFAULT_S.get(sid.split(":")[0], 60))

    def _est(self, sid: str) -> None:
        r = self.rows[sid]
        known = sid in self.history
        if r["status"] == "wait":
            r["item"].setText(2, f"約 {fmt_s(self.expected(sid))}" if known else "")

    def set_step(self, sid: str, status: str, info: str = "") -> None:
        r = self.rows.get(sid) or self._add(sid, info or sid)
        now = time.time()
        if status == "run":
            r["t0"] = now
            self.frac = None
            r["item"].setText(3, "")
            self.scrollToItem(r["item"])
            self.setCurrentItem(r["item"])
        elif r["t0"] is not None:
            r["t"] = now - r["t0"]
            if status == "done" and r["t"] > 0.5:
                old = self.history.get(sid)
                self.history[sid] = r["t"] if old is None else 0.5 * old + 0.5 * r["t"]
            r["item"].setText(2, fmt_s(r["t"]))
            if status == "done":
                r["item"].setText(3, info or "")
            elif info:
                r["item"].setText(3, info)
        r["status"] = status
        r["item"].setText(1, ICON.get(status, status))
        bold = status == "run"
        f = r["item"].font(0)
        f.setBold(bold)
        for c in range(4):
            r["item"].setFont(c, f)

    def set_progress(self, frac, text: str = "") -> None:
        self.frac = None if frac is None else max(0.0, min(1.0, float(frac)))
        cur = self.current()
        if cur:
            self.rows[cur]["item"].setText(3, text)

    def finish(self, success: bool) -> None:
        for sid in self.order:
            r = self.rows[sid]
            if r["status"] == "run":
                self.set_step(sid, "done" if success else "fail")
            elif r["status"] == "wait":
                r["status"] = "skip"
                r["item"].setText(1, ICON["skip"])

    # ---- 查詢 ----
    def current(self) -> Optional[str]:
        return next((sid for sid in self.order if self.rows[sid]["status"] == "run"), None)

    def tick(self) -> None:
        """每秒更新執行中那一列的時間。"""
        cur = self.current()
        if cur and self.rows[cur]["t0"]:
            el = time.time() - self.rows[cur]["t0"]
            exp = self.history.get(cur)
            self.rows[cur]["item"].setText(2, fmt_s(el) + (f" / 約 {fmt_s(exp)}" if exp else ""))

    def overall(self) -> Optional[float]:
        """整體進度 0–1（依預估時間加權）；沒有計畫 → None。"""
        if not self.order:
            return None
        total = sum(self.expected(s) for s in self.order)
        done = 0.0
        for sid in self.order:
            r, exp = self.rows[sid], self.expected(sid)
            if r["status"] in ("done", "fail", "skip"):
                done += exp
            elif r["status"] == "run":
                if self.frac is not None:
                    done += exp * self.frac
                elif r["t0"]:
                    done += exp * min(0.95, (time.time() - r["t0"]) / exp)
        return max(0.0, min(1.0, done / total)) if total > 0 else None

    def remaining(self) -> Optional[float]:
        """預估還要幾秒（只在剩下的步驟都有上次紀錄時才給）。"""
        left = 0.0
        for sid in self.order:
            r = self.rows[sid]
            if r["status"] == "wait":
                if sid not in self.history:
                    return None
                left += self.history[sid]
            elif r["status"] == "run":
                exp = self.history.get(sid)
                if exp is None and self.frac is None:
                    return None
                el = time.time() - (r["t0"] or time.time())
                if self.frac:
                    left += el * (1 - self.frac) / max(self.frac, 1e-3)
                else:
                    left += max(0.0, exp - el)
        return left

    def summary(self) -> str:
        n = len(self.order)
        k = sum(1 for s in self.order if self.rows[s]["status"] in ("done", "fail", "skip"))
        cur = self.current()
        txt = f"{k}/{n}" + (f"　{self.rows[cur]['label']}" if cur else "")
        rem = self.remaining()
        if rem is not None and cur:
            txt += f"　預估還要 {fmt_s(rem)}"
        return txt
