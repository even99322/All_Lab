"""更新的介面（v3.3），跟量測程式一樣：右下角通知、狀態列「⬆ 更新到 x」、版本視窗、推播。邏輯在 updater.py。

HFSS_NO_UPDATE_CHECK=1 → 不檢查（測試、自我檢查用）。
"""
from __future__ import annotations

import os
import threading
import time
import webbrowser
from typing import Dict, Optional

from PyQt6.QtCore import QEvent, QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QProgressDialog, QPushButton, QToolButton, QVBoxLayout)

import updater

NOTE_LINES = 5


def _notes(app: Dict) -> str:
    text = str(app.get("notes") or "").strip()
    lines = [ln.strip().lstrip("-•* ").strip() for ln in text.splitlines() if ln.strip()]
    shown = "".join(f"<br>• {ln}" for ln in lines[:NOTE_LINES])
    more = f"<br>…還有 {len(lines) - NOTE_LINES} 項" if len(lines) > NOTE_LINES else ""
    return shown + more


class UpdateToast(QFrame):
    """主視窗右下角的通知。"""

    def __init__(self, ctl: "UpdateController") -> None:
        super().__init__(ctl.win)
        self.ctl = ctl
        self.setObjectName("updToast")
        self.setStyleSheet("#updToast { background: palette(window); border: 1px solid palette(highlight); "
                           "border-radius: 10px; }")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 10)
        top = QHBoxLayout()
        self.title = QLabel("")
        self.title.setStyleSheet("font-weight: 700; font-size: 14px;")
        top.addWidget(self.title, 1)
        x = QToolButton()
        x.setText("✕")
        x.setAutoRaise(True)
        x.clicked.connect(self.hide)
        top.addWidget(x)
        v.addLayout(top)
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setMaximumWidth(380)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.body)
        row = QHBoxLayout()
        row.addStretch(1)
        later = QPushButton("稍後")
        later.clicked.connect(self.hide)
        self.go = QPushButton("立即更新")
        self.go.setDefault(True)
        self.go.clicked.connect(lambda: (self.hide(), self.ctl.update_now()))
        row.addWidget(later)
        row.addWidget(self.go)
        v.addLayout(row)
        self.hide()
        ctl.win.installEventFilter(self)

    def show_for(self, app: Dict) -> None:
        self.title.setText(f"HFSS 自動化工具 {app.get('version')} 可以更新")
        self.body.setText(f"目前 {updater.current_version()}{_notes(app)}")
        self.go.setText("立即更新" if updater.can_self_update() else "下載")
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()

    def place(self) -> None:
        w = self.ctl.win
        sb = w.statusBar().height() if w.statusBar() else 0
        self.move(w.width() - self.width() - 16, w.height() - self.height() - 16 - sb)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.ctl.win and ev.type() == QEvent.Type.Resize and self.isVisible():
            self.place()
        return False


class UpdateController(QObject):
    found = pyqtSignal(object, str)          # (app, hub)：背景執行緒 → 介面
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(str, str)          # (錯誤訊息 / "", 檔案)

    def __init__(self, win) -> None:
        super().__init__(win)
        self.win = win
        self.app: Optional[Dict] = None
        self.hub = ""
        self.last_check: Optional[float] = None
        self.last_error = ""
        self._stop = threading.Event()
        self.button = QToolButton()
        self.button.setAutoRaise(True)
        self.button.clicked.connect(self.show_dialog)
        self._set_button()
        if win.statusBar():
            win.statusBar().addPermanentWidget(self.button)
        self.toast = UpdateToast(self)
        self.found.connect(self._on_found)
        self.disabled = bool(os.environ.get("HFSS_NO_UPDATE_CHECK"))
        if not self.disabled:
            QTimer.singleShot(1500, self.start)

    # ---- 背景：開啟時檢查 → 長輪詢 ------------------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._loop, name="hfss-update", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            r = updater.check()
            self.last_check = time.time()
            if r is None:
                self.last_error = "連不到平台（Hub）"
                self._stop.wait(300)
                continue
            self.last_error = ""
            self.found.emit(r["app"], r["hub"])
            ver = max(updater.current_version(), str((r["app"] or {}).get("version") or "0"), key=updater.vkey)
            while not self._stop.is_set():            # 推播：站長一發佈就回來
                try:
                    w = updater.watch(r["hub"], ver, 60)
                except Exception:  # noqa: BLE001 - 網路斷了：回到外面重新找 Hub
                    self._stop.wait(30)
                    break
                if w.get("changed") and w.get("app"):
                    self.found.emit(w["app"], r["hub"])
                    ver = str(w["app"].get("version") or ver)

    def _on_found(self, app, hub: str) -> None:
        self.hub = hub
        newer = updater.is_newer(app)
        was = updater.is_newer(self.app) and str((self.app or {}).get("version")) == str((app or {}).get("version"))
        self.app = app
        self._set_button()
        if newer and not was and str(app.get("version")) not in updater.load_conf().get("skipped", []):
            self.toast.show_for(app)
            try:
                QApplication.alert(self.win)
            except Exception:  # noqa: BLE001
                pass

    def _set_button(self) -> None:
        if updater.is_newer(self.app):
            self.button.setText(f"⬆ 更新到 {self.app.get('version')}")
            self.button.setStyleSheet("font-weight: 700; color: #1E88E5;")
            self.button.setToolTip("有新版：點一下看更新內容並更新")
        else:
            self.button.setText(f"v{updater.current_version()}")
            self.button.setStyleSheet("")
            self.button.setToolTip("版本檢查")

    # ---- 版本視窗 ---------------------------------------------------------------------------------
    def show_dialog(self) -> None:
        dlg = QDialog(self.win)
        dlg.setWindowTitle("版本檢查")
        dlg.setMinimumWidth(460)
        v = QVBoxLayout(dlg)
        form = QFormLayout()
        lbl_latest = QLabel("檢查中…")
        hub_edit = QLineEdit(updater.load_conf().get("hub_url", ""))
        hub_edit.setPlaceholderText("不填 = 192.168.50.2:8765、VPN 100.114.33.20:8765")
        form.addRow("目前版本", QLabel(updater.current_version()))
        form.addRow("平台上最新", lbl_latest)
        form.addRow("平台網址", hub_edit)
        v.addLayout(form)
        notes = QLabel("")
        notes.setWordWrap(True)
        notes.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(notes)
        btns = QDialogButtonBox()
        b_check = btns.addButton("重新檢查", QDialogButtonBox.ButtonRole.ActionRole)
        b_update = btns.addButton("立即更新", QDialogButtonBox.ButtonRole.AcceptRole)
        btns.addButton("關閉", QDialogButtonBox.ButtonRole.RejectRole)
        v.addWidget(btns)
        b_update.setEnabled(False)

        def show(r) -> None:
            if r is None:
                lbl_latest.setText("<span style='color:#E53935'>連不到平台（Hub）：確認在實驗室網路或開著 VPN</span>")
                return
            app = r["app"]
            if app is None or not app.get("version"):
                lbl_latest.setText("平台上還沒有這個程式（站長要先在控制台發佈）")
                return
            self._on_found(app, r["hub"])
            newer = updater.is_newer(app)
            lbl_latest.setText(f"<b>{app['version']}</b>　{'⬆ 可以更新' if newer else '✔ 已經是最新版'}"
                               f"<br><span style='color:gray'>{r['hub']}</span>")
            notes.setText(f"<b>{app['version']} 的更新內容</b>{_notes(app)}" if app.get("notes") else "")
            b_update.setEnabled(newer)
            b_update.setText("立即更新" if updater.can_self_update() else "下載")

        def recheck() -> None:
            updater.save_conf(hub_url=hub_edit.text().strip())
            lbl_latest.setText("檢查中…")
            QApplication.processEvents()
            show(updater.check(timeout=4))

        b_check.clicked.connect(recheck)
        b_update.clicked.connect(lambda: (dlg.accept(), self.update_now()))
        btns.rejected.connect(dlg.reject)
        QTimer.singleShot(50, recheck)
        dlg.exec()

    # ---- 更新 -----------------------------------------------------------------------------------
    def update_now(self) -> None:
        app = self.app
        if not updater.is_newer(app):
            return
        f = updater.pick_file(app, self.hub)
        if f is None:
            QMessageBox.information(self.win, "更新", "平台上還沒有這一版的 Windows exe。")
            return
        if not updater.can_self_update():
            webbrowser.open(f["url"])
            QMessageBox.information(self.win, "更新", "這是 Python 原始碼版，已經在瀏覽器下載新的 exe；之後改用 exe 就會自動更新。")
            return
        busy = getattr(self.win, "_is_running", lambda: False)()
        if busy:
            QMessageBox.information(self.win, "更新", "HFSS 任務還在執行，等它結束再更新。")
            return
        dst = updater.new_exe_path()
        prog = QProgressDialog(f"下載 {app['version']}…", "取消", 0, 100, self.win)
        prog.setWindowTitle("更新")
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)
        prog.setValue(0)
        state = {"cancel": False}
        prog.canceled.connect(lambda: state.update(cancel=True))
        def on_progress(got: int, total: int) -> None:
            if total:
                prog.setValue(int(got * 100 / total))
        self.progress.connect(on_progress)

        def work() -> None:
            try:
                updater.download(f["url"], dst, f.get("sha256", ""), progress=self.progress.emit,
                                 cancelled=lambda: state["cancel"])
                self.finished.emit("", dst)
            except updater.UpdateError as e:
                self.finished.emit(str(e), "")

        def done(err: str, path: str) -> None:
            for sig, slot in ((self.finished, done), (self.progress, on_progress)):
                try:
                    sig.disconnect(slot)
                except TypeError:
                    pass
            prog.close()
            if err:
                if err != "已取消":
                    QMessageBox.warning(self.win, "更新", f"{err}\n\n也可以到平台的「模塊下載」手動下載。")
                return
            try:
                updater.install(path)
            except (OSError, updater.UpdateError) as e:
                QMessageBox.warning(self.win, "更新", f"沒辦法換上新版：{e}")
                return
            self.stop()
            self.win.close()
            QApplication.quit()

        self.finished.connect(done)
        threading.Thread(target=work, name="hfss-download", daemon=True).start()
