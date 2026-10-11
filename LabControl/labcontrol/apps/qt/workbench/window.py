"""Lab Control 主視窗（仿 Labber Measurement Editor 的操作邏輯）。

    ┌──────────────────────────────┬─────────────────────────┐
    │ 量測方案（流程圖）             │ 檔案設置                  │
    │  節點自由拖動、拉線連接          │  檔名、資料夾、標籤、執行、    │
    ├──────────────────────────────┤  檢查與估時、已存的數據      │
    │ 儀器參數（Channels）           │                         │
    │  拖到流程圖建立節點、取值 / 設定值 │                         │
    └──────────────────────────────┴─────────────────────────┘

量測面板只有「左邊設計流程、右邊設置存檔」（0.0.25）：
  * 量測監控（進度、曲線、2D、暫停 / 停止）在按「▶ 開始量測」後以獨立視窗彈出；工具列「📈 量測監控」再開。
  * 即時數據（電源群組、VNA）用工具列「📡 即時數據」開獨立視窗。
  * 關閉這兩個視窗只是收起來，量測照常進行。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from .... import APP_NAME, __version__
from ....core.station import Station
from ....paths import lab_home, lab_path, open_folder
from ....scheme import TEMPLATES, Scheme, build_catalog
from ....scheme.templates import default_template
from .channels import ChannelPanel
from .doc import WorkbenchDoc
from .files_panel import FilePanel
from .. import theme
from .graph_view import FlowPanel
from .monitor_panel import MonitorPanel
from .remote_mixin import RemoteMixin


def _panel(title: str, body: QtWidgets.QWidget, extra: Optional[QtWidgets.QWidget] = None,
           subtitle: str = "") -> QtWidgets.QWidget:
    """四格版面的一格：卡片（圓角、細框），標題 + 灰色副標題（與 Lab APP 相同風格）。"""
    w = QtWidgets.QFrame()
    w.setObjectName("panel")
    theme.on_change(w, lambda w: w.setStyleSheet(
        f"#panel {{ background:{theme.c('panel')}; border:1px solid {theme.c('border')}; border-radius:10px; }}"))
    v = QtWidgets.QVBoxLayout(w)
    v.setContentsMargins(1, 1, 1, 1)
    v.setSpacing(0)
    head = QtWidgets.QHBoxLayout()
    head.setContentsMargins(14, 9, 10, 7)
    head.setSpacing(8)
    lab = QtWidgets.QLabel(title)
    theme.on_change(lab, lambda lab: lab.setStyleSheet(
        f"font-weight:600; font-size:15px; color:{theme.c('title')}; background:transparent;"))
    head.addWidget(lab)
    if subtitle:
        sub = QtWidgets.QLabel(subtitle)
        theme.on_change(sub, lambda x: x.setStyleSheet(f"color:{theme.c('muted')}; font-size:12px; background:transparent;"))
        head.addWidget(sub)
    head.addStretch()
    if extra is not None:
        head.addWidget(extra)
    hw = QtWidgets.QWidget()
    hw.setObjectName("panelhead")
    hw.setLayout(head)
    theme.on_change(hw, lambda hw: hw.setStyleSheet(
        f"#panelhead {{ background:transparent; border-bottom:1px solid {theme.c('border')}; }}"))
    v.addWidget(hw)
    v.addWidget(body, 1)
    return w


class ToolWindow(QtWidgets.QMainWindow):
    """主視窗的獨立視窗（即時數據、量測監控）：內容常駐，關閉只是收起來。"""

    def __init__(self, body: QtWidgets.QWidget, title: str, parent: QtWidgets.QWidget,
                 size: tuple = (1100, 760)) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(title)
        self.resize(*size)
        wrap = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(wrap)
        v.setContentsMargins(6, 4, 6, 4)
        v.addWidget(body)
        theme.on_change(wrap, lambda w: w.setStyleSheet(f"background:{theme.c('wrap')};"))
        self.setCentralWidget(wrap)
        self.body = body

    def open(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()


class LabControlWindow(RemoteMixin, QtWidgets.QMainWindow):
    def __init__(self, station: Station, scheme: Optional[Scheme] = None, root: Optional[str] = None) -> None:
        super().__init__()
        self.station = station
        self.root = root
        self.doc = WorkbenchDoc(scheme or default_template() or Scheme("新量測方案"), build_catalog(station), self)
        self.resize(1680, 1000)

        self.flow = FlowPanel(self.doc)
        self.local_channels = self.channels = ChannelPanel(self.doc, station)
        self.local_monitor = self.monitor = MonitorPanel(station)
        self.files = FilePanel(self.doc)
        self.channels.edit_requested.connect(self.flow.view.edit_node)
        self.monitor.start_requested.connect(self.start_measurement)
        self.monitor.finished.connect(lambda _s: self._refresh(preview=False))   # 保留結果畫面
        self.chan_stack = QtWidgets.QStackedWidget()      # 本機 / 遠端節點的儀器參數列表
        self.chan_stack.addWidget(self.local_channels)
        self.mon_stack = QtWidgets.QStackedWidget()       # 本機 / 遠端節點的即時監控
        self.mon_stack.addWidget(self.local_monitor)

        left = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        left.addWidget(_panel("量測方案", self.flow, subtitle="流程圖"))
        left.addWidget(_panel("儀器參數", self.chan_stack))
        left.setSizes([620, 360])
        # 即時數據（直接控制電源群組 / VNA）與量測監控：各自的獨立視窗（關閉 = 收起來）
        from ..live import LivePanel
        from ....remote.backend import LocalBackend
        from ....remote.hub import hostname

        self.live = LivePanel()
        self.live.set_backend(LocalBackend(station), f"local:{hostname()}", "本機")
        self.live.set_compact(False)
        self.live.b_pop.setVisible(False)
        self.live.message.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.live_window = ToolWindow(self.live, f"即時數據（本機）— {APP_NAME}", self, (1200, 760))
        self.measure_window = ToolWindow(self.mon_stack, f"量測監控 — {APP_NAME}", self, (1100, 800))
        self._was_busy = False
        self.busy_timer = QtCore.QTimer(self)
        self.busy_timer.timeout.connect(self._sync_busy)
        self.busy_timer.start(700)
        main = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(left)
        main.addWidget(_panel("檔案設置", self.files, subtitle="存檔 · 執行 · 檢查與估時"))
        main.setSizes([1060, 620])
        main.setHandleWidth(6)
        wrap = QtWidgets.QWidget()
        wl = QtWidgets.QVBoxLayout(wrap)
        wl.setContentsMargins(6, 4, 6, 4)
        wl.addWidget(main)
        theme.on_change(wrap, lambda w: w.setStyleSheet(f"background:{theme.c('wrap')};"))
        self.setCentralWidget(wrap)
        self._toolbar()
        self.status = QtWidgets.QLabel()
        self.statusBar().addPermanentWidget(self.status, 1)
        self.doc.changed.connect(lambda _o: self._refresh())
        self.server = None
        from ..bridge import QtEventBridge

        self.st_bridge = QtEventBridge(station.bus, "station.changed", parent=self)
        self.st_bridge.on("station.changed", lambda _p: self._instruments_changed())
        self._init_remote()
        from ..platform_ui import PlatformController

        self.platform = PlatformController(self)          # 量子工程平台：登入、標籤、上傳、拖放套用設定
        self.platform.message.connect(lambda m: self.statusBar().showMessage(m, 10000))
        self.statusBar().addPermanentWidget(self.platform.label)
        from ..updates import UpdateController, install_resize_hook

        self.updates = UpdateController(self)               # 版本檢查、更新推播、程式內更新
        self.statusBar().addPermanentWidget(self.updates.button)
        install_resize_hook(self, self.updates)
        self.files.addTab(self.platform.recent, "已存的數據")
        self.setAcceptDrops(True)
        self._refresh()

    # ---- 工具列 -------------------------------------------------------------
    def _toolbar(self) -> None:
        tb = self.addToolBar("main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        act = tb.addAction("🖧 儀器伺服器", self.open_server)
        act.setToolTip("連線 / 新增 / 直接控制儀器、驅動測試（Labber Instrument Server）")
        a = tb.addAction("📡 即時數據", self.popout_live)
        a.setToolTip("即時數據獨立視窗：電源群組（前往目標、記憶、微調）與 VNA（參數、曲線）")
        a = tb.addAction("📈 量測監控", self.show_measure_window)
        a.setToolTip("量測進度、曲線、2D 影像、暫停 / 停止（開始量測時會自動彈出）")
        self._remote_toolbar(tb)
        tb.addSeparator()
        tb.addAction("新方案", self.new_scheme).setShortcut("Ctrl+N")
        tmpl = QtWidgets.QToolButton()
        tmpl.setText("範本 ▾")
        tmpl.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(tmpl)
        menu.aboutToShow.connect(lambda: self._fill_templates(menu))
        tmpl.setMenu(menu)
        tb.addWidget(tmpl)
        tb.addAction("開啟…", self.open_scheme).setShortcut("Ctrl+O")
        tb.addAction("儲存", self.save_scheme).setShortcut("Ctrl+S")
        tb.addAction("另存…", lambda: self.save_scheme(ask=True))
        tb.addSeparator()
        tb.addAction("復原", self.doc.undo).setShortcut("Ctrl+Z")
        tb.addAction("重做", self.doc.redo).setShortcut("Ctrl+Y")
        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel(" 方案名稱 "))
        self.name = QtWidgets.QLineEdit()
        self.name.setMinimumWidth(190)
        self.name.editingFinished.connect(self._rename)
        tb.addWidget(self.name)
        tb.addSeparator()
        self._remote_target_widget(tb)
        # 開始量測：自己建立按鈕（主要動作的藍色樣式），不從工具列取回 Qt 內部建立的按鈕
        self.act_run = QtGui.QAction("▶ 開始量測", self)
        self.act_run.setShortcut("F5")
        self.act_run.triggered.connect(self.start_measurement)
        self.run_btn = QtWidgets.QToolButton()
        self.run_btn.setDefaultAction(self.act_run)
        self.run_btn.setProperty("primary", True)
        tb.addWidget(self.run_btn)

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        mode = QtWidgets.QLabel("  模擬模式  " if self.station.simulate else "  實機  ")
        mode.setStyleSheet("background:%s; color:white; border-radius:4px; padding:2px 6px; font-weight:600;"
                           % ("#f08c00" if self.station.simulate else "#2b8a3e"))
        tb.addWidget(mode)
        self.act_theme = theme.make_toggle_action(self)
        tb.addAction(self.act_theme)
        lab = QtWidgets.QToolButton()
        lab.setText("設定 ▾")
        lab.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QtWidgets.QMenu(lab)
        m.addAction("開啟設定資料夾（LAB）", lambda: open_folder(lab_home()))
        m.addAction("編輯 settings.yaml", lambda: open_folder(lab_path("settings.yaml")))
        m.addAction("編輯 instruments.yaml", lambda: open_folder(lab_path("instruments.yaml")))
        m.addSeparator()
        m.addAction("重新載入設定", self.reload_lab)
        m.addMenu(theme.make_menu(m))
        m.addSeparator()
        m.addAction("Lab Control Monitor（所有節點與儀器）…", self._open_monitor)
        m.addAction("開啟 Hub 監控網頁", self._open_hub_web)
        m.addAction("Hub 連線設定…", self._open_hub_settings)
        m.addAction("重新連線 Hub", lambda: self.remote.find())
        m.addAction("請所有較舊的節點更新", lambda: self.remote.auto_update())
        m.addSeparator()
        m.addAction("版本檢查…", lambda: self.updates.show_dialog())
        a = m.addAction("量測電腦：開機自動開啟並上線")
        a.setCheckable(True)
        a.setToolTip("登入這台電腦時自動開啟 Lab Control（視窗縮小）並以量測節點上線")
        from .... import autostart

        try:
            a.setChecked(autostart.is_enabled())
        except Exception:  # noqa: BLE001
            a.setEnabled(False)
        a.toggled.connect(self._set_autostart)
        m.addAction("下載頁（給新電腦裝量測 / 讀檔）", self._open_download_page)
        m.addSeparator()
        m.addAction(f"關於 {APP_NAME}", self.about)
        lab.setMenu(m)
        tb.addWidget(lab)
        pl = QtWidgets.QToolButton()
        pl.setText("平台 ▾")
        pl.setToolTip("量子工程平台：登入、標籤與論文、數據目錄")
        pl.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        pl.setMenu(self._platform_menu())
        tb.addWidget(pl)

    def _platform_menu(self) -> QtWidgets.QMenu:
        m = QtWidgets.QMenu(self)
        m.aboutToShow.connect(lambda: self.platform._fill_menu(m))
        return m

    # ---- 拖放數據檔 → 套用設定 ---------------------------------------------------
    def dragEnterEvent(self, ev: QtGui.QDragEnterEvent) -> None:  # noqa: N802
        if self.platform.accepts(ev.mimeData()):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev) -> None:  # noqa: N802
        if self.platform.accepts(ev.mimeData()):
            ev.acceptProposedAction()

    def dropEvent(self, ev: QtGui.QDropEvent) -> None:  # noqa: N802
        if self.platform.accepts(ev.mimeData()):
            ev.acceptProposedAction()
            self.platform.drop(ev.mimeData())

    def _fill_templates(self, menu: QtWidgets.QMenu) -> None:
        menu.clear()
        items = list(TEMPLATES.items())
        for key, (label, fn) in items:
            menu.addAction(label, lambda fn=fn: self._load_scheme(fn(), None))
        if not items:
            menu.addAction("（LAB/templates 沒有範本）").setEnabled(False)
        menu.addSeparator()
        menu.addAction("把目前方案存成範本…", self.save_as_template)
        menu.addAction("開啟範本資料夾", lambda: open_folder(lab_path("templates")))

    def _rename(self) -> None:
        name = self.name.text().strip()
        if name and name != self.doc.scheme.name:
            self.doc.mutate(lambda s: setattr(s, "name", name), origin=self)

    def _refresh(self, preview: bool = True) -> None:
        r = self.doc.result
        if not self.name.hasFocus():
            self.name.setText(self.doc.scheme.name)
        self.setWindowTitle(f"{APP_NAME} {__version__} — {self.doc.scheme.name}{' *' if self.doc.dirty else ''}")
        self.act_run.setEnabled(r.ok and not self.monitor.busy)
        self.monitor.b_start.setEnabled(r.ok and not self.monitor.busy)
        if preview:
            self.monitor.set_preview(r)
        err = sum(1 for i in r.issues if i.level == "error")
        self.status.setText((f"✖ {err} 個錯誤（見右邊「檢查與估時」）" if err else "✔ 可以執行")
                            + (f"　·　{self.doc.path}" if self.doc.path else "")
                            + f"　·　設定資料夾 {lab_home()}")

    # ---- 檔案 ---------------------------------------------------------------
    def _confirm_discard(self) -> bool:
        if not self.doc.dirty:
            return True
        r = QtWidgets.QMessageBox.question(self, "未儲存", "目前方案尚未儲存，確定要放棄修改？")
        return r == QtWidgets.QMessageBox.StandardButton.Yes

    def _load_scheme(self, scheme: Scheme, path: Optional[str]) -> None:
        if self._confirm_discard():
            self.doc.replace_scheme(scheme, path)
            self.flow.view.fit_all()

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
        self.files.commit_edits()
        path = self.doc.path
        if ask or not path:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "儲存方案", str(lab_path("schemes", f"{self.doc.scheme.name}.scheme.yaml")), "量測方案 (*.yaml)")
            if not path:
                return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.doc.scheme.save(path)
        self.doc.path = path
        self.doc.dirty = False
        self._refresh()

    def save_as_template(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "存成範本", "範本檔名（英數字）：", text="my_template")
        if ok and name.strip():
            path = lab_path("templates", f"{name.strip()}.scheme.yaml")
            path.parent.mkdir(parents=True, exist_ok=True)
            self.doc.scheme.save(path)

    def reload_lab(self) -> None:
        from ....settings import reload

        reload()
        theme.apply()                       # settings.yaml app.theme 可能改了
        self.doc.set_catalog(build_catalog(self.station))
        self.channels.rebuild()
        QtWidgets.QMessageBox.information(self, "已重新載入", "settings.yaml 已重新載入。\n"
                                          "instruments.yaml 的變更請重新開啟 Lab Control 生效。")

    def _set_autostart(self, on: bool) -> None:
        from .... import autostart

        try:
            autostart.set_enabled(on)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "開機自動開啟", f"設定失敗：{e}")
            return
        if on and not self.act_node.isChecked():
            self.act_node.setChecked(True)              # 量測電腦：現在也上線
        self.statusBar().showMessage("✔ 登入電腦時會自動開啟 Lab Control 並以量測節點上線" if on else
                                     "已關閉開機自動開啟", 10000)

    def _open_download_page(self) -> None:
        from ....integrations.reader import download_page

        url = download_page()
        if url:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def about(self) -> None:
        QtWidgets.QMessageBox.about(self, APP_NAME, f"<b>{APP_NAME}</b> {__version__}<br>"
                                    f"設定資料夾：{lab_home()}<br>"
                                    f"{'模擬模式' if self.station.simulate else '實機模式'}")

    # ---- 執行 ---------------------------------------------------------------
    def start_measurement(self) -> None:
        self.files.commit_edits()                    # 檔名等欄位打完沒按 Enter 也算數
        r = self.doc.result
        if not r.ok:
            self.files.setCurrentIndex(2)
            QtWidgets.QMessageBox.warning(self, "無法開始", "方案有錯誤，請看右邊「檢查與估時」。")
            return
        if self.monitor.busy:
            return
        if self.remote_target is not None:
            self._start_remote()
            return
        try:
            exp = r.experiment(self.station)
            out = exp.plan_output(root=self.root)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "無法開始", str(e))
            return
        target = out.folder / out.file_name
        if out.collision == "overwrite" and target.exists():
            q = QtWidgets.QMessageBox.question(self, "檔案已存在", f"{target}\n已存在，確定要覆寫？")
            if q != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self.monitor.run(exp, out)
        self.show_measure_tab()
        self._refresh(preview=False)

    def open_server(self):
        from ..server import InstrumentServerWindow

        if self.server is None:
            self.server = InstrumentServerWindow(self.station)
            self.server.remote = self.remote
            self.remote.registry_changed.connect(self.server.refresh)
        self.server.show()
        self.server.raise_()
        self.server.activateWindow()
        return self.server

    # ---- 即時數據 / 量測監控（獨立視窗）------------------------------------------------------
    def show_measure_window(self) -> None:
        self.measure_window.setWindowTitle(f"量測監控 — {self.doc.scheme.name} — {APP_NAME}")
        self.measure_window.open()

    show_measure_tab = show_measure_window          # 舊名稱（遠端量測開始時呼叫）

    def _sync_busy(self) -> None:
        busy = bool(self.monitor.busy)
        if busy != self._was_busy:
            self._was_busy = busy
            self.live.set_busy(busy)
            if busy:
                self.show_measure_window()

    def popout_live(self) -> None:
        """「📡 即時數據」：開啟（或叫到最前面）即時數據視窗。"""
        self.live_window.open()

    def set_live_backend(self, backend, key: str, label: str) -> None:
        self.live.set_backend(backend, key, label)
        self.live_window.setWindowTitle(f"即時數據（{label}）— {APP_NAME}")

    def _instruments_changed(self) -> None:
        """儀器伺服器新增 / 移除儀器後：重建儀器參數列表與方案檢查。"""
        self.local_channels.rebuild()
        if self.remote_target is None:
            self.doc.set_catalog(build_catalog(self.station))

    def handle_local_message(self, m: dict) -> None:
        """同一台電腦的其他程式（LabLogViewer「介面 → 量測」、`LabControl --apply 檔案`）送來的指令。"""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        QtWidgets.QApplication.alert(self, 2000)
        if m.get("cmd") == "apply" and m.get("path"):
            p = Path(str(m["path"]))
            if not p.is_file():
                QtWidgets.QMessageBox.warning(self, "套用設定", f"找不到數據檔：{p}")
                return
            if self.monitor.busy:
                QtWidgets.QMessageBox.information(self, "套用設定", "量測進行中：量完再套用設定。")
                return
            self.platform.apply_file(p)

    def closeEvent(self, ev: QtGui.QCloseEvent) -> None:
        if self.monitor.busy:
            q = QtWidgets.QMessageBox.question(self, "量測中", "量測進行中，確定要中斷並關閉？"
                                               "（電流會依設定斜坡回到停靠位置）")
            if q != QtWidgets.QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            self.monitor.runner.stop()
            self.monitor.runner.wait(60)
        if not self._confirm_discard():
            ev.ignore()
            return
        if getattr(self, "local_link", None) is not None:
            self.local_link.stop()
        self._shutdown_remote()
        self.updates.stop()
        self.platform.close()
        self.live_window.close()
        self.measure_window.close()
        self.live.vna.close_windows()
        self.monitor.close_bridge()
        self.st_bridge.close()
        if self.server is not None:
            self.server.close()
        ev.accept()
