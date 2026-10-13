"""專案範本同步（0.0.31）。

* 範本選單：選了「2D」而且檔案設置選了論文庫專案 → 載入這個專案的「2D」（沒有就用內建範本）。
* 換論文庫專案：先把目前的設定存回舊專案，再載入新專案的同一個範本（新專案還沒有 → 沿用目前的設定並存成新專案的）。
* 方案改了（停手 1.5 秒）→ 自動寫回目前專案的這個範本（Hub 共用；連不到 Hub 先存本機、之後再送）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PyQt6 import QtCore

from ....integrations import project_templates as pt
from ....scheme import Scheme
from ..worker import run_bg


class ProjectTemplateSync(QtCore.QObject):
    def __init__(self, win) -> None:
        super().__init__(win)
        self.w = win
        self._busy = 0                              # 載入中：不要把剛載入的內容又存回去
        self.loading = 0                            # 正在從 Hub 讀專案範本
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.flush)
        win.doc.changed.connect(self._changed)
        win.files.project_changing.connect(self._project_changing)
        win.files.project_changed.connect(self._project_changed)

    # ---- 目前的專案 / 範本 ------------------------------------------------------------------
    @property
    def scheme(self) -> Scheme:
        return self.w.doc.scheme

    def project(self) -> Dict[str, Any]:
        p = self.scheme.output.get("lab_project")
        return dict(p) if isinstance(p, dict) and p.get("id") else {}

    def active(self) -> bool:
        return bool(self.scheme.template and self.project())

    # ---- 寫回 ------------------------------------------------------------------------------
    def _changed(self, _origin: object) -> None:
        if self._busy or not self.active():
            return
        self.timer.start()

    def flush(self, project: Optional[Dict[str, Any]] = None, background: bool = True) -> None:
        """把目前的方案存到 project（預設 = 目前選的專案）的這個範本。"""
        self.timer.stop()
        p = project or self.project()
        key = self.scheme.template
        if not (p.get("id") and key):
            return
        data = self.scheme.to_dict()
        doc = self.w.doc

        def ok(sent: bool) -> None:
            if doc.path is None:                    # 專案範本就是存檔的地方：不再問「要不要儲存」
                doc.dirty = False
                self.w._refresh(preview=False)
            self.w.statusBar().showMessage(
                f"✔ 已寫回專案「{p.get('name', p['id'])}」的範本「{self.scheme.name}」"
                + ("" if sent else "（Hub 連不到，先存在這台電腦，之後自動送出）"), 5000)
        if background:
            run_bg(lambda: pt.save(p["id"], key, data), ok, lambda m: None)
        else:
            ok(pt.save(p["id"], key, data, push=False))

    # ---- 載入 ------------------------------------------------------------------------------
    def load_template(self, key: str) -> None:
        """範本選單：有選專案 → 這個專案的版本；否則內建範本。"""
        from ....scheme.templates import load_template

        base = load_template(key)
        p = self.project()
        if self.active():
            self.flush(background=False)              # 換範本前先把目前的存回去
        if not p:
            self._apply(base)
            return

        self.loading += 1

        def ok(d: Optional[Dict[str, Any]]) -> None:
            self.loading -= 1
            if d and int(d.get("template_rev") or 0) < int(base.template_rev or 0):
                d = None                              # 1.0.2：內建範本改版了（例如 2D 加背景＋去背）→ 用新版
                self.w.statusBar().showMessage(f"📁 範本「{base.name}」有新版，這個專案改用新版（舊設定沒有沿用）", 8000)
            s = Scheme.from_dict(d) if d else base
            s.template = key
            s.output["lab_project"] = p
            self._apply(s)
            if not d:
                self.flush()                          # 這個專案第一次用這個範本：存一份
            else:
                self.w.statusBar().showMessage(f"📁 專案「{p.get('name')}」的範本「{s.name}」", 5000)
        run_bg(lambda: pt.load(p["id"], key), ok, lambda m: ok(None))

    def _apply(self, s: Scheme) -> None:
        self._busy += 1
        try:
            if self.w.doc.path is None:
                self.w.doc.dirty = False              # 專案範本已經同步過，不用問
            self.w._load_scheme(s, None)
        finally:
            self._busy -= 1

    def _project_changing(self, old: Optional[Dict[str, Any]]) -> None:
        if old and old.get("id") and self.scheme.template:
            self.flush(project=old, background=False)
        self.timer.stop()
        self._busy += 1                               # 換專案的那一下修改不要存（會存到新專案）
        self._switching = True

    def _project_changed(self, new: Optional[Dict[str, Any]]) -> None:
        if getattr(self, "_switching", False):
            self._switching = False
            self._busy -= 1
        self.timer.stop()
        key = self.scheme.template
        if not (key and new and new.get("id")):
            return

        self.loading += 1

        def ok(d: Optional[Dict[str, Any]]) -> None:
            self.loading -= 1
            if d:
                s = Scheme.from_dict(d)
                s.template = key
                s.output["lab_project"] = new
                self._apply(s)
                self.w.statusBar().showMessage(f"📁 已載入專案「{new.get('name')}」的範本「{s.name}」", 6000)
            else:
                self.flush()                          # 新專案還沒有：用目前的設定建立
        run_bg(lambda: pt.load(new["id"], key), ok, lambda m: ok(None))
