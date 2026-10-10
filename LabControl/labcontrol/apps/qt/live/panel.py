"""即時監控分頁：電源群組（左）＋ VNA（右：上參數、下曲線）。本機或「執行於」的量測節點都用同一個畫面。

可以「⧉ 獨立視窗」拿出來單獨看；關閉獨立視窗會放回主視窗分頁。
"""
from __future__ import annotations

from typing import Optional

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal

from ..worker import run_bg
from .dc_panel import DCGroupsPanel
from .vna_panel import VNAPanel


class LivePanel(QtWidgets.QWidget):
    message = pyqtSignal(str)
    popout_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.backend = None
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(6)
        head = QtWidgets.QHBoxLayout()
        self.where = QtWidgets.QLabel("本機")
        self.where.setProperty("role", "h2")
        head.addWidget(self.where)
        self.banner = QtWidgets.QLabel("")
        self.banner.setProperty("role", "muted")
        head.addWidget(self.banner, 1)
        self.b_conn = QtWidgets.QPushButton("全部連線")
        self.b_conn.setToolTip("連線這台（或執行節點）群組內的所有儀器（唯讀，不改變輸出）")
        self.b_conn.clicked.connect(self.connect_all)
        self.b_reload = QtWidgets.QPushButton("重新讀取")
        self.b_reload.setToolTip("重新讀取電源與 VNA 清單")
        self.b_reload.clicked.connect(self.reload)
        self.b_pop = QtWidgets.QPushButton("⧉ 獨立視窗")
        self.b_pop.clicked.connect(self.popout_requested.emit)
        for b in (self.b_conn, self.b_reload, self.b_pop):
            head.addWidget(b)
        v.addLayout(head)
        self.split = split = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        self.dc = DCGroupsPanel()
        self.vna = VNAPanel()
        dcw = QtWidgets.QWidget()
        dl = QtWidgets.QVBoxLayout(dcw)
        dl.setContentsMargins(0, 0, 4, 0)
        t = QtWidgets.QLabel("電源群組")
        t.setProperty("role", "muted")
        dl.addWidget(t)
        dl.addWidget(self.dc, 1)
        vw = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(vw)
        vl.setContentsMargins(4, 0, 0, 0)
        t = QtWidgets.QLabel("VNA")
        t.setProperty("role", "muted")
        self.vna_title = t
        vl.addWidget(t)
        vl.addWidget(self.vna, 1)
        self.dcw, self.vw = dcw, vw
        split.addWidget(dcw)
        split.addWidget(vw)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 6)
        split.setSizes([420, 600])
        split.setChildrenCollapsible(False)
        v.addWidget(split, 1)
        self.msg = QtWidgets.QLabel("")
        self.msg.setProperty("role", "muted")
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        for w in (self.dc, self.vna):
            w.message.connect(self._msg)
        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.timeout.connect(self._maybe_reload)
        self.refresh_timer.start(5000)
        self._last_conn: Optional[frozenset] = None

    def set_compact(self, on: bool) -> None:
        """嵌在主視窗：只顯示 VNA 的 dB 曲線與「量測 / 循環」；電源群組、VNA 參數等在「⧉ 獨立視窗」。"""
        self.compact = bool(on)
        self.dcw.setVisible(not on)
        self.vna_title.setVisible(not on)
        self.vna.set_compact(on)
        self.b_conn.setVisible(not on)

    def _msg(self, m: str) -> None:
        self.msg.setText(m)
        self.message.emit(m)

    def set_backend(self, backend, key: str, label: str) -> None:
        self.backend = backend
        self.where.setText(label)
        self._last_conn = None
        self.dc.set_backend(backend, key)
        self.vna.set_backend(backend)

    def reload(self) -> None:
        self.dc.reload()
        self.vna.reload()

    def _maybe_reload(self) -> None:
        """儀器連線 / 斷線後自動更新清單（只顯示已連線的電源與 VNA）。"""
        be = self.backend
        if be is None or not self.isVisible():
            return

        def ok(names):
            names = frozenset(names)
            if self._last_conn is not None and names != self._last_conn:
                self.reload()
            self._last_conn = names
        run_bg(lambda: [i["name"] for i in be.instruments() if i.get("connected")], ok, lambda _m: None)

    def connect_all(self) -> None:
        be = self.backend
        if be is None:
            return
        self._msg("連線所有儀器（唯讀）…")

        def ok(res):
            bad = {k: v for k, v in (res or {}).items() if v}
            self._msg(f"✔ 連線完成 {len(res or {}) - len(bad)}/{len(res or {})}" +
                      ("　" + "；".join(f"{k}：{v}" for k, v in list(bad.items())[:3]) if bad else ""))
            self.reload()
        run_bg(be.connect_all, ok, lambda m: self._msg(f"❌ 連線失敗：{m}"))

    def set_busy(self, busy: bool) -> None:
        self.banner.setText("量測進行中：即時控制暫停（數值照常更新）" if busy else "")
        self.dc.set_busy(busy)
        self.vna.set_busy(busy)
        self.b_conn.setEnabled(not busy)


class LiveWindow(QtWidgets.QMainWindow):
    """獨立的即時監控視窗；關閉時把面板還回去。"""

    closed = pyqtSignal()

    def __init__(self, panel: LivePanel, title: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.resize(1200, 760)
        self.panel = panel
        self.setCentralWidget(panel)
        panel.b_pop.setVisible(False)
        panel.set_compact(False)
        panel.show()

    def closeEvent(self, ev) -> None:
        w = self.takeCentralWidget()
        if w is not None:
            w.setParent(None)
        self.panel.b_pop.setVisible(True)
        self.panel.set_compact(True)
        self.closed.emit()
        super().closeEvent(ev)
