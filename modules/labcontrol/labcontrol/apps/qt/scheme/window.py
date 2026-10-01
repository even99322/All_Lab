"""量測方案編輯器主視窗。

    python main.py                     （Lab Control 主程式，等同下面不帶參數）
    python -m labcontrol.apps.qt.scheme [方案.yaml] [--lab 其他 instruments.yaml] [--sim]

版面：
    左  方塊庫（拖曳或雙擊）
    中  流程圖（照手繪草圖的畫法）
    右  屬性（選取方塊的設定；沒選取 = 方案設定）
    下  Labber 式參數表 ｜ 檢查與估時 ｜ YAML
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from PyQt6 import QtGui, QtWidgets
from PyQt6.QtCore import Qt

from .... import APP_NAME, __version__
from ....core.station import Station
from ....paths import lab_home, lab_path, open_folder
from ....scheme import TEMPLATES, Scheme, build_catalog, format_duration
from ....scheme.templates import default_template
from .doc import SchemeDoc
from .flowchart import FlowchartView, Palette
from .inspector import Inspector
from .tables import IssuesPanel, ParamTables, YamlView


class SchemeEditorWindow(QtWidgets.QMainWindow):
    def __init__(self, station: Station, scheme: Optional[Scheme] = None, root: Optional[str] = None) -> None:
        super().__init__()
        self.station = station
        self.root = root
        self.monitor = None
        self.doc = SchemeDoc(scheme or default_template() or Scheme("新量測方案"), build_catalog(station), self)
        self.resize(1480, 940)

        self.palette_ = Palette(self.doc)
        self.flow = FlowchartView(self.doc)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(self.flow)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background:#f8f9fb; border:none; }")
        self.inspector = Inspector(self.doc)

        top = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        pal_box = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(pal_box)
        pl.setContentsMargins(6, 4, 0, 4)
        hint = QtWidgets.QLabel("<b>方塊庫</b><br><span style='color:#868e96'>拖到流程圖，或雙擊加在選取方塊後</span>")
        hint.setWordWrap(True)
        pl.addWidget(hint)
        pl.addWidget(self.palette_, 1)
        top.addWidget(pal_box)
        top.addWidget(scroll)
        top.addWidget(self.inspector)
        top.setStretchFactor(1, 1)
        top.setSizes([230, 820, 380])

        self.tabs = QtWidgets.QTabWidget()
        self.tables = ParamTables(self.doc)
        self.issues = IssuesPanel(self.doc)
        self.yaml = YamlView(self.doc)
        self.tabs.addTab(self.tables, "Labber 式參數表")
        self.tabs.addTab(self.issues, "檢查與估時")
        self.tabs.addTab(self.yaml, "YAML")

        main = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        main.addWidget(top)
        main.addWidget(self.tabs)
        main.setSizes([600, 320])
        self.setCentralWidget(main)
        self._toolbar()
        self.status = QtWidgets.QLabel()
        self.statusBar().addPermanentWidget(self.status, 1)
        self.doc.changed.connect(lambda _o: self._refresh())
        self._refresh()

    # ---- 工具列 -------------------------------------------------------------
    def _toolbar(self) -> None:
        tb = self.addToolBar("main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        tb.addAction("新方案", self.new_scheme).setShortcut("Ctrl+N")
        tmpl = QtWidgets.QToolButton()
        tmpl.setText("範本 ▾")
        tmpl.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(tmpl)
        menu.aboutToShow.connect(lambda: self._fill_templates(menu))   # 每次打開重新讀 LAB/templates
        self._fill_templates(menu)
        tmpl.setMenu(menu)
        tb.addWidget(tmpl)
        tb.addAction("開啟…", self.open_scheme).setShortcut("Ctrl+O")
        tb.addAction("儲存", self.save_scheme).setShortcut("Ctrl+S")
        tb.addAction("另存…", lambda: self.save_scheme(ask=True))
        tb.addSeparator()
        tb.addAction("復原", self.doc.undo).setShortcut("Ctrl+Z")
        tb.addAction("重做", self.doc.redo).setShortcut("Ctrl+Y")
        tb.addSeparator()
        self.act_run = tb.addAction("▶ 執行量測", self.run)
        self.act_run.setShortcut("F5")
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        mode = QtWidgets.QLabel("  模擬模式  " if self.station.simulate else "  實機  ")
        mode.setStyleSheet("background:%s; color:white; border-radius:4px; padding:2px 6px; font-weight:600;"
                           % ("#f08c00" if self.station.simulate else "#2b8a3e"))
        tb.addWidget(mode)
        lab = QtWidgets.QToolButton()
        lab.setText("設定 ▾")
        lab.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QtWidgets.QMenu(lab)
        m.addAction("開啟設定資料夾（LAB）", lambda: open_folder(lab_home()))
        m.addAction("編輯 settings.yaml", lambda: open_folder(lab_path("settings.yaml")))
        m.addAction("編輯 instruments.yaml", lambda: open_folder(lab_path("instruments.yaml")))
        m.addSeparator()
        m.addAction("重新載入設定與儀器清單", self.reload_lab)
        m.addSeparator()
        m.addAction(f"關於 {APP_NAME}", self.about)
        lab.setMenu(m)
        tb.addWidget(lab)

    def _fill_templates(self, menu: QtWidgets.QMenu) -> None:
        menu.clear()
        items = TEMPLATES.items()
        for key, (label, fn) in items:
            menu.addAction(label, lambda fn=fn: self._load_scheme(fn(), None))
        if not items:
            menu.addAction("（LAB/templates 沒有範本）").setEnabled(False)
        menu.addSeparator()
        menu.addAction("把目前方案存成範本…", self.save_as_template)
        menu.addAction("開啟範本資料夾", lambda: open_folder(lab_path("templates")))

    def save_as_template(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "存成範本", "範本檔名（英數字）：", text="my_template")
        if ok and name.strip():
            path = lab_path("templates", f"{name.strip()}.scheme.yaml")
            path.parent.mkdir(parents=True, exist_ok=True)
            self.doc.scheme.save(path)

    def reload_lab(self) -> None:
        """重新讀 settings.yaml；instruments.yaml 的變更（新儀器、上下限、參數覆寫）需重新開啟程式。"""
        from ....settings import reload

        reload()
        self.doc.catalog = build_catalog(self.station)
        self.doc._after_change(None)
        QtWidgets.QMessageBox.information(self, "已重新載入", "settings.yaml 已重新載入。\n"
                                          "instruments.yaml 的變更請重新開啟 Lab Control 生效。")

    def about(self) -> None:
        QtWidgets.QMessageBox.about(self, APP_NAME, f"<b>{APP_NAME}</b> {__version__}<br>"
                                    f"設定資料夾：{lab_home()}<br>"
                                    f"{'模擬模式' if self.station.simulate else '實機模式'}")

    def _refresh(self) -> None:
        r = self.doc.result
        name = self.doc.scheme.name + (" *" if self.doc.dirty else "")
        self.setWindowTitle(f"{APP_NAME} {__version__} — {name}")
        self.act_run.setEnabled(r.ok)
        err = sum(1 for i in r.issues if i.level == "error")
        warn = sum(1 for i in r.issues if i.level == "warning")
        state = f"<span style='color:#e03131'>✖ {err} 個錯誤</span>" if err else "<span style='color:#2b8a3e'>✔ 可以執行</span>"
        self.status.setText(f"{state}{f'　⚠ {warn} 個警告' if warn else ''}　·　{r.total_points:,} 點　·　"
                            f"預估 {format_duration(r.est_seconds)}　·　"
                            f"{(str(r.n_files) + ' 個檔') if r.n_files else '只寫 raw 檔'}"
                            + (f"　·　{self.doc.path}" if self.doc.path else ""))

    # ---- 檔案 ---------------------------------------------------------------
    def _confirm_discard(self) -> bool:
        if not self.doc.dirty:
            return True
        r = QtWidgets.QMessageBox.question(self, "未儲存", "目前方案尚未儲存，確定要放棄修改？")
        return r == QtWidgets.QMessageBox.StandardButton.Yes

    def _load_scheme(self, scheme: Scheme, path: Optional[str]) -> None:
        if self._confirm_discard():
            self.doc.replace_scheme(scheme, path)

    def new_scheme(self) -> None:
        self._load_scheme(Scheme("新量測方案"), None)

    def open_scheme(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "開啟方案", str(lab_path("schemes")),
                                                        "量測方案 (*.yaml *.yml)")
        if path:
            try:
                self._load_scheme(Scheme.load(path), path)
            except Exception as e:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(self, "開啟失敗", str(e))

    def save_scheme(self, ask: bool = False) -> None:
        path = self.doc.path
        if ask or not path:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "儲存方案",
                                                            str(lab_path("schemes", f"{self.doc.scheme.name}.scheme.yaml")),
                                                            "量測方案 (*.yaml)")
            if not path:
                return
        self.doc.scheme.save(path)
        self.doc.path = path
        self.doc.dirty = False
        self._refresh()

    # ---- 執行 ---------------------------------------------------------------
    def run(self) -> None:
        r = self.doc.result
        if not r.ok:
            self.tabs.setCurrentWidget(self.issues)
            return
        from ..monitor import MonitorWindow

        exp = r.experiment(self.station)
        if self.monitor is not None and self.monitor.runner is not None and self.monitor.runner.is_active:
            QtWidgets.QMessageBox.information(self, "量測中", "目前還有量測在進行")
            return
        self.monitor = MonitorWindow(self.station, exp, root=self.root)
        self.monitor.show()
        self.monitor.raise_()

    def closeEvent(self, ev: QtGui.QCloseEvent) -> None:
        if self.monitor is not None and self.monitor.runner is not None and self.monitor.runner.is_active:
            QtWidgets.QMessageBox.warning(self, "量測中", "請先在監控視窗中斷量測")
            ev.ignore()
            return
        if not self._confirm_discard():
            ev.ignore()
            return
        ev.accept()


def main(argv=None) -> int:
    from ...app import main as app_main

    return app_main(argv)
