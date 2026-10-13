"""主視窗的版本檢查、更新推播與程式內更新（0.0.27）。

* 推播：背景一直等 Hub 的 /get/watch（長輪詢）；站長在控制台一發佈新版，幾秒內右下角就跳出通知
  「Lab Control x.y.z 可以更新」（工作列也會閃），狀態列出現「⬆ 更新到 x.y.z」。
* 版本檢查：「設定 ▾ → 版本檢查…」顯示目前版本、Hub 最新版本、更新方式與更新內容，可以直接更新。
* 更新：功能更新包（約 1 MB）或整個安裝檔，程式自己下載、換上、用同樣的參數重開；量測進行中不更新。
* 開啟成功後回報 selfupdate.mark_good()（新版開不起來時 main.py 會自動退回上一版）。
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

from PyQt6 import QtCore, QtWidgets

from ... import __version__, selfupdate
from ...settings import setting, settings
from .worker import run_bg

CHECK_EVERY_MS = 30 * 60 * 1000
PLAN_TEXT = {"code": "功能更新包（只下載程式碼，約 {mb}，幾秒後重開）",
             "installer": "整個程式（{mb}；這次的新版需要新的執行環境）",
             "python": "這是用 Python 執行的 Lab Control：請用 Lab APP / git 更新，或改用打包版",
             "wait": "新版需要新的執行環境，站長還沒有上傳這個作業系統的安裝檔；上傳後就能更新"}


def _mb(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    return f"{n / 1e6:.1f} MB" if n < 20e6 else f"{n / 1e6:.0f} MB"


def plan_text(c: Optional[Dict[str, Any]]) -> str:
    if not c or not c.get("plan"):
        return "已經是最新版"
    t = c.get("target") or {}
    return PLAN_TEXT[c["plan"]].format(mb=_mb(t.get("size")))


class UpdateToast(QtWidgets.QFrame):
    """主視窗右下角的更新通知（推播）。"""

    def __init__(self, ctl: "UpdateController", parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.setObjectName("updateToast")
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setStyleSheet("#updateToast { background: palette(window); border: 1px solid palette(highlight);"
                           " border-radius: 10px; }")
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 10)
        self.title = QtWidgets.QLabel("")
        self.title.setStyleSheet("font-weight: 700; font-size: 14px;")
        self.body = QtWidgets.QLabel("")
        self.body.setWordWrap(True)
        self.body.setMaximumWidth(380)
        row = QtWidgets.QHBoxLayout()
        self.b_now = QtWidgets.QPushButton("立即更新")
        self.b_now.setProperty("primary", True)
        b_more = QtWidgets.QPushButton("更新內容…")
        b_later = QtWidgets.QPushButton("稍後")
        self.b_now.clicked.connect(lambda: (self.hide(), ctl.update_now(confirm=False)))
        b_more.clicked.connect(lambda: (self.hide(), ctl.show_dialog()))
        b_later.clicked.connect(self.hide)
        row.addWidget(self.b_now)
        row.addWidget(b_more)
        row.addStretch(1)
        row.addWidget(b_later)
        v.addWidget(self.title)
        v.addWidget(self.body)
        v.addLayout(row)
        self.hide()

    def show_for(self, c: Dict[str, Any]) -> None:
        t = c.get("target") or {}
        self.title.setText(f"⬆ Lab Control {t.get('version')} 可以更新（目前 {c.get('current', __version__)}）")
        notes = [ln.strip("-•* ").strip() for ln in (c.get("notes") or "").splitlines() if ln.strip()]
        self.body.setText("\n".join(f"・{n}" for n in notes[:4]) + ("\n…" if len(notes) > 4 else "") +
                          ("\n" if notes else "") + plan_text(c))
        self.b_now.setVisible(c.get("plan") in ("code", "installer"))
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()

    def place(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        sb = p.statusBar().height() if hasattr(p, "statusBar") else 0
        self.move(max(8, p.width() - self.width() - 16), max(8, p.height() - self.height() - sb - 16))


class VersionDialog(QtWidgets.QDialog):
    """「設定 ▾ → 版本檢查…」"""

    def __init__(self, ctl: "UpdateController", parent=None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("版本檢查")
        self.resize(560, 460)
        v = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        r = selfupdate.running()
        cur = r["version"] + ("（功能更新包）" if r["code_root"] else "")
        self.cur = QtWidgets.QLabel(cur)
        self.cur.setStyleSheet("font-weight: 700;")
        form.addRow("目前版本", self.cur)
        if r["frozen"]:
            form.addRow("程式本體", QtWidgets.QLabel(f"{r['base']}（{r['platform']} {r['arch']}，執行環境 {r['requirements']}）"))
        else:
            form.addRow("執行方式", QtWidgets.QLabel("Python（開發 / Lab APP）"))
        self.hub = QtWidgets.QLabel("檢查中…")
        self.latest = QtWidgets.QLabel("—")
        self.latest.setStyleSheet("font-weight: 700;")
        self.how = QtWidgets.QLabel("")
        self.how.setWordWrap(True)
        form.addRow("Hub", self.hub)
        form.addRow("最新版本", self.latest)
        form.addRow("更新方式", self.how)
        v.addLayout(form)
        v.addWidget(QtWidgets.QLabel("更新內容"))
        self.notes = QtWidgets.QTextBrowser()
        v.addWidget(self.notes, 1)
        self.auto = QtWidgets.QCheckBox("有新版時通知我（推播）")
        self.auto.setChecked(bool(setting("app.check_updates", True)))
        self.auto.toggled.connect(ctl.set_enabled)
        v.addWidget(self.auto)
        row = QtWidgets.QHBoxLayout()
        self.b_check = QtWidgets.QPushButton("重新檢查")
        self.b_update = QtWidgets.QPushButton("立即更新")
        self.b_update.setProperty("primary", True)
        b_close = QtWidgets.QPushButton("關閉")
        self.b_check.clicked.connect(self.refresh)
        self.b_update.clicked.connect(lambda: (self.accept(), ctl.update_now(confirm=False)))
        b_close.clicked.connect(self.reject)
        row.addWidget(self.b_check)
        row.addStretch(1)
        row.addWidget(self.b_update)
        row.addWidget(b_close)
        v.addLayout(row)
        self.b_update.setEnabled(False)
        if ctl.result is not None:
            self.show_result(ctl.result)
        self.refresh()

    def refresh(self) -> None:
        self.b_check.setEnabled(False)
        self.hub.setText("檢查中…")

        def ok(c):
            self.b_check.setEnabled(True)
            self.ctl._found(c, quiet=True)
            self.show_result(c)

        def fail(m):
            self.b_check.setEnabled(True)
            self.hub.setText(f"連不到 Hub：{m}")
        run_bg(selfupdate.check, ok, fail)

    def show_result(self, c: Optional[Dict[str, Any]]) -> None:
        if c is None:
            self.hub.setText("連不到 Hub（設定 ▾ → Hub 連線設定…）")
            self.latest.setText("—")
            self.how.setText("")
            self.b_update.setEnabled(False)
            return
        self.hub.setText(c["hub"])
        self.latest.setText(str(c.get("version") or "Hub 上還沒有 Lab Control"))
        self.how.setText(plan_text(c))
        notes = (c.get("notes") or "").strip()
        self.notes.setMarkdown(notes or "（沒有寫更新內容）")
        self.b_update.setEnabled(c.get("plan") in ("code", "installer") and not self.ctl.busy)


class UpdateController(QtCore.QObject):
    found = QtCore.pyqtSignal(object)
    _progress = QtCore.pyqtSignal(int, int)

    def __init__(self, window) -> None:
        super().__init__(window)
        self.w = window
        self.result: Optional[Dict[str, Any]] = None
        self.busy = False
        self._announced: set = set()
        self._stop = threading.Event()
        self.button = QtWidgets.QPushButton("")
        self.button.setProperty("primary", True)
        self.button.setVisible(False)
        self.button.clicked.connect(self.show_dialog)
        self.toast = UpdateToast(self, window)
        self.found.connect(lambda c: self._found(c, quiet=False))
        self._progress.connect(self._show_progress)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(lambda: self.check(quiet=True))
        self._first = QtCore.QTimer(self)
        self._first.setSingleShot(True)
        self._first.timeout.connect(lambda: self.check(quiet=False))
        QtCore.QTimer.singleShot(1500, selfupdate.mark_good)      # 主視窗開得起來：這個版本可以用
        if setting("app.check_updates", True) and not os.environ.get("LABCONTROL_NO_UPDATE_CHECK"):
            self.start()

    # ---- 推播 ----------------------------------------------------------------------
    def start(self) -> None:
        self._first.start(5000)
        self.timer.start(CHECK_EVERY_MS)
        self._stop.clear()
        threading.Thread(target=self._watch_loop, name="update-watch", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.timer.stop()
        self._first.stop()

    def set_enabled(self, on: bool) -> None:
        settings().set("app.check_updates", bool(on))
        self.stop()
        if on:
            self.start()

    def _watch_loop(self) -> None:
        backoff = 10.0
        while not self._stop.is_set():
            ver = (self.result or {}).get("version") if (self.result or {}).get("plan") else None
            try:
                c = selfupdate.watch(ver or __version__, wait=60)
                backoff = 10.0
            except Exception:  # noqa: BLE001 - 連不到 Hub：等一下再試
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 600.0)
                continue
            if c is not None and not self._stop.is_set():
                try:
                    self.found.emit(c)
                except RuntimeError:                              # 視窗已關
                    return
                self._stop.wait(2)

    # ---- 檢查 ----------------------------------------------------------------------
    def check(self, quiet: bool = True) -> None:
        if self.busy:
            return
        run_bg(selfupdate.check, lambda c: self._found(c, quiet), lambda _m: None)

    def _found(self, c: Optional[Dict[str, Any]], quiet: bool) -> None:
        if c is None:
            return
        self.result = c
        if not c.get("plan"):
            self.button.setVisible(False)
            return
        t = c.get("target") or {}
        self.button.setText(f"⬆ 更新到 {t.get('version')}" if c["plan"] in ("code", "installer") else
                            f"有新版 {t.get('version')}")
        self.button.setToolTip(plan_text(c))
        self.button.setVisible(True)
        key = (t.get("version"), c.get("plan"))
        if not quiet and key not in self._announced:              # 每個版本只跳一次通知
            self._announced.add(key)
            self.toast.show_for(c)
            QtWidgets.QApplication.alert(self.w, 0)

    def show_dialog(self) -> None:
        VersionDialog(self, self.w).exec()

    # ---- 更新 ----------------------------------------------------------------------
    def update_now(self, confirm: bool = True) -> None:
        c = self.result
        if c is None or self.busy:
            return
        if c.get("plan") not in ("code", "installer"):
            QtWidgets.QMessageBox.information(self.w, "更新", plan_text(c))
            return
        if getattr(self.w.monitor, "busy", False):
            QtWidgets.QMessageBox.information(self.w, "更新", "量測進行中：量完再更新。")
            return
        t = c["target"]
        if confirm:
            q = QtWidgets.QMessageBox.question(self.w, "更新 Lab Control",
                                               f"更新到 {t['version']}？\n{plan_text(c)}\n\n會關閉這個視窗並自動重新開啟。")
            if q != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        if hasattr(self.w, "_confirm_discard") and not self.w._confirm_discard():
            return
        self.busy = True
        self.button.setEnabled(False)
        self.button.setVisible(True)
        self.button.setText("準備更新…")

        def ok(_what):
            self.w.statusBar().showMessage(f"⬆ 已更新到 {t['version']}，重新開啟…")
            self.w.doc.dirty = False
            self.w._closing_for_update = True
            self.stop()
            # 0.0.31：關主視窗（停掉節點、伺服器、即時數據…）再結束；卡住時 15 秒後強制結束，
            # 安裝程式才不會一直等（以前 QApplication.quit 不經過 closeEvent，背景執行緒讓程式留著）
            QtCore.QTimer.singleShot(300, lambda: selfupdate.exit_for_update(self._close_and_quit))

        def fail(m):
            self.busy = False
            self.button.setEnabled(True)
            self.button.setText(f"⬆ 更新到 {t['version']}")
            QtWidgets.QMessageBox.warning(self.w, "更新失敗", f"{m}\n\n目前的版本照常使用。")
        run_bg(lambda: selfupdate.apply(c, lambda d, n: self._progress.emit(d, n)), ok, fail)

    def _close_and_quit(self) -> None:
        try:
            self.w.close()
        finally:
            QtWidgets.QApplication.quit()

    def _show_progress(self, done: int, total: int) -> None:
        pct = f"{done * 100 // total}%" if total else _mb(done)
        self.button.setText(f"下載新版 {pct}")


def install_resize_hook(window, ctl: UpdateController) -> None:
    """主視窗改變大小時，通知跟著移到右下角。"""
    class _F(QtCore.QObject):
        def eventFilter(self, obj, ev):  # noqa: N802
            if ev.type() == QtCore.QEvent.Type.Resize and ctl.toast.isVisible():
                ctl.toast.place()
            return False
    f = _F(window)
    window.installEventFilter(f)
    ctl._resize_filter = f

