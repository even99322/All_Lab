"""大程式（桌面）的畫面：登入 → 模塊卡片（安裝 / 更新 / 開啟），把數據檔拖到卡片上直接交給模塊。"""
from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from labcomm import actions, handoff, local
from labcomm.config import load_config, split_urls
from labcomm.errors import CommError, NotLoggedIn

from . import __version__
from .core import Launcher, parse_version

ACCENT, MUTED, BAD = "#1c6dd0", "#6b7785", "#dc2626"
DROP_ACTION = {"labcontrol": (actions.APPLY_SCHEME, "拖數據檔到這裡：套用它的量測設置"),
               "lablogviewer": (actions.OPEN_FILE, "拖數據檔到這裡：開啟")}


class _Sig(QtCore.QObject):
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    progress = QtCore.Signal(str, float)


class _Task(QtCore.QRunnable):
    def __init__(self, fn, progress: bool) -> None:
        super().__init__()
        self.fn, self.with_progress, self.s = fn, progress, _Sig()

    def run(self) -> None:
        try:
            self.s.done.emit(self.fn(self.s.progress.emit) if self.with_progress else self.fn())
        except CommError as e:
            self.s.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.s.failed.emit(f"{type(e).__name__}: {e}")


_alive: List[_Task] = []


def run_bg(fn, done=None, failed=None, progress=None) -> None:
    t = _Task(fn, progress is not None)
    _alive.append(t)
    t.s.done.connect(lambda *_: _alive.remove(t) if t in _alive else None)
    t.s.failed.connect(lambda *_: _alive.remove(t) if t in _alive else None)
    if done:
        t.s.done.connect(done)
    if failed:
        t.s.failed.connect(failed)
    if progress:
        t.s.progress.connect(progress)
    QtCore.QThreadPool.globalInstance().start(t)


# ---- 登入 ------------------------------------------------------------------------
class LoginPage(QtWidgets.QWidget):
    logged_in = QtCore.Signal()

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        outer = QtWidgets.QVBoxLayout(self)
        outer.addStretch()
        box = QtWidgets.QFrame()
        box.setObjectName("card")
        box.setMaximumWidth(420)
        f = QtWidgets.QFormLayout(box)
        title = QtWidgets.QLabel("<h2>QEL Lab</h2>用論文庫的帳號登入")
        f.addRow(title)
        cfg = load_config()
        self.url = QtWidgets.QLineEdit(", ".join(cfg.portal_url))
        self.user = QtWidgets.QLineEdit((cfg.user or {}).get("username", ""))
        self.pw = QtWidgets.QLineEdit()
        self.pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.code = QtWidgets.QLineEdit()
        self.code.setPlaceholderText("有開兩步驟驗證才需要")
        self.code.hide()
        self.err = QtWidgets.QLabel()
        self.err.setWordWrap(True)
        self.err.setStyleSheet(f"color:{BAD}")
        f.addRow("大程式網址", self.url)
        f.addRow("帳號", self.user)
        f.addRow("密碼", self.pw)
        f.addRow("驗證碼", self.code)
        f.addRow(self.err)
        self.btn = QtWidgets.QPushButton("登入")
        self.btn.setDefault(True)
        self.btn.clicked.connect(self._login)
        self.pw.returnPressed.connect(self._login)
        reg = QtWidgets.QPushButton("申請帳號（網頁）")
        reg.setFlat(True)
        reg.clicked.connect(lambda: webbrowser.open(split_urls(self.url.text())[0] + "/#/register"))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(reg)
        row.addStretch()
        row.addWidget(self.btn)
        f.addRow(row)
        h = QtWidgets.QHBoxLayout()
        h.addStretch()
        h.addWidget(box)
        h.addStretch()
        outer.addLayout(h)
        outer.addStretch()

    def _login(self) -> None:
        from labcomm import PortalClient
        self.win.launcher.client = PortalClient(self.url.text(), "", timeout=15, client_name=f"qel-launcher/{__version__}")
        self.btn.setEnabled(False)
        self.err.setText("登入中…")
        u, p, c = self.user.text().strip(), self.pw.text(), self.code.text().strip()
        run_bg(lambda: self.win.launcher.login(u, p, c), self._done, self._fail)

    def _done(self, r) -> None:
        self.btn.setEnabled(True)
        if r.get("need_2fa"):
            self.code.show()
            self.code.setFocus()
            self.err.setText("請輸入驗證 App 上的 6 位數字")
            return
        self.err.clear()
        self.pw.clear()
        self.logged_in.emit()

    def _fail(self, msg: str) -> None:
        self.btn.setEnabled(True)
        self.err.setText(msg)


# ---- 模塊卡片 ----------------------------------------------------------------------
class ModuleCard(QtWidgets.QFrame):
    def __init__(self, win: "MainWindow", m: Dict[str, Any]) -> None:
        super().__init__()
        self.win, self.m = win, m
        self.setObjectName("card")
        self.setMinimumWidth(280)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        v = QtWidgets.QVBoxLayout(self)
        t = QtWidgets.QLabel(f"<b style='font-size:15px'>{m['name']}</b>")
        v.addWidget(t)
        d = QtWidgets.QLabel(m.get("description", ""))
        d.setWordWrap(True)
        d.setStyleSheet(f"color:{MUTED}")
        v.addWidget(d)
        self.info = QtWidgets.QLabel()
        self.info.setStyleSheet(f"color:{MUTED};font-size:12px")
        v.addWidget(self.info)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        self.bar.hide()
        v.addWidget(self.bar)
        self.row = QtWidgets.QHBoxLayout()
        v.addLayout(self.row)
        self.drop_hint: Optional[QtWidgets.QLabel] = None
        if m["id"] in DROP_ACTION and m.get("allowed"):
            self.setAcceptDrops(True)
            self.drop_hint = QtWidgets.QLabel(DROP_ACTION[m["id"]][1])
            self.drop_hint.setStyleSheet(f"color:{MUTED};font-size:12px;border:1px dashed {MUTED};border-radius:6px;padding:4px")
            v.addWidget(self.drop_hint)
        self.render()

    def render(self) -> None:
        m = self.m
        while self.row.count():
            w = self.row.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not m.get("allowed"):
            self.info.setText("站長還沒有開放給你")
            self.setEnabled(False)
            return
        if m["kind"] == "web":
            self.info.setText("網頁模塊")
            self._btn("開啟", lambda: self.win.open_web((m.get("url") or "") + "/"), primary=True)
            self.row.addStretch()
            return
        inst, latest = m.get("installed"), m.get("latest")
        bits = [f"已安裝 v{inst}" if inst else "尚未安裝", f"最新 v{latest}" if latest else "尚未發佈"]
        if m.get("running"):
            bits.append("執行中")
        self.info.setText("　·　".join(bits))
        if inst:
            self._btn("開啟", self.open, primary=True)
            if m.get("update"):
                self._btn(f"更新到 v{latest}", self.install)
            menu_btn = QtWidgets.QToolButton()
            menu_btn.setText("⋯")
            menu_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            menu = QtWidgets.QMenu(menu_btn)
            menu.addAction("換回上一版", self.rollback)
            menu.addAction("看記錄檔", lambda: self.win.open_path(self.win.launcher.store.home / "logs" / f"{m['id']}.log"))
            menu.addAction("移除", self.uninstall)
            menu_btn.setMenu(menu)
            self.row.addWidget(menu_btn)
        elif latest:
            self._btn("安裝", self.install, primary=True)
        self.row.addStretch()

    def _btn(self, text: str, fn: Callable, primary: bool = False) -> None:
        b = QtWidgets.QPushButton(text)
        if primary:
            b.setObjectName("primary")
        b.clicked.connect(fn)
        self.row.addWidget(b)

    def _progress(self, msg: str, frac: float) -> None:
        self.bar.show()
        self.bar.setValue(int(frac * 100))
        self.bar.setFormat(f"{msg} %p%")

    def install(self) -> None:
        mid = self.m["id"]
        self.setEnabled(False)
        run_bg(lambda emit: (self.win.launcher.ensure_labcomm(emit), self.win.launcher.install_latest(mid, emit))[1],
               lambda v: (self.bar.hide(), self.setEnabled(True), self.win.toast(f"{self.m['name']} v{v} 已安裝"),
                          self.win.reload()),
               lambda e: (self.bar.hide(), self.setEnabled(True), self.win.error(e)), self._progress)

    def open(self) -> None:
        try:
            self.win.launcher.launch(self.m["id"])
            self.win.toast(f"開啟 {self.m['name']}")
            QtCore.QTimer.singleShot(1500, self.win.reload_local)
        except CommError as e:
            self.win.error(str(e))

    def rollback(self) -> None:
        v = self.win.launcher.store.rollback(self.m["id"])
        self.win.toast(f"已換回 v{v}" if v else "沒有更早的版本")
        self.win.reload()

    def uninstall(self) -> None:
        if QtWidgets.QMessageBox.question(self, "移除", f"移除 {self.m['name']}？（數據與設定檔不會刪除）") \
                == QtWidgets.QMessageBox.Yes:
            self.win.launcher.store.uninstall(self.m["id"])
            self.win.reload()

    # ---- 拖放：數據檔交給模塊 -------------------------------------------------------
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if any(handoff.is_data_file(u.toLocalFile()) for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self.setStyleSheet(f"#card {{ border:2px solid {ACCENT}; }}")

    def dragLeaveEvent(self, e) -> None:  # noqa: N802
        self.setStyleSheet("")

    def dropEvent(self, e) -> None:  # noqa: N802
        self.setStyleSheet("")
        files = [u.toLocalFile() for u in e.mimeData().urls() if handoff.is_data_file(u.toLocalFile())]
        if not files:
            return
        e.acceptProposedAction()
        self.win.hand_to(self.m["id"], DROP_ACTION[self.m["id"]][0], {"path": files[0]})


# ---- 主視窗 ------------------------------------------------------------------------
STYLE = f"""
QWidget {{ font-size: 14px; }}
#card {{ background: palette(base); border: 1px solid palette(mid); border-radius: 10px; padding: 6px; }}
QPushButton#primary {{ background: {ACCENT}; color: white; border: none; border-radius: 6px; padding: 6px 14px; }}
QPushButton#primary:disabled {{ background: palette(mid); }}
"""


class MainWindow(QtWidgets.QMainWindow):
    remote_action = QtCore.Signal(str, str, dict)       # 來自本機其他模塊或其他電腦：(模塊, 動作, payload)
    events_changed = QtCore.Signal(str)

    def __init__(self, launcher: Launcher) -> None:
        super().__init__()
        self.launcher = launcher
        self.setWindowTitle(f"QEL Lab 大程式 v{__version__}")
        self.resize(940, 640)
        self.setStyleSheet(STYLE)
        self.stack = QtWidgets.QStackedWidget()
        self.setCentralWidget(self.stack)
        self.login_page = LoginPage(self)
        self.login_page.logged_in.connect(self.show_main)
        self.stack.addWidget(self.login_page)
        self.main_page = QtWidgets.QWidget()
        mv = QtWidgets.QVBoxLayout(self.main_page)
        head = QtWidgets.QHBoxLayout()
        self.hello = QtWidgets.QLabel()
        head.addWidget(self.hello)
        head.addStretch()
        for text, path in (("網頁版", "/#/"), ("數據", "/#/data"), ("共用標籤", "/#/tags")):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(lambda _=False, p=path: self.open_web(p))
            head.addWidget(b)
        self.admin_btn = QtWidgets.QPushButton("管理")
        self.admin_btn.clicked.connect(lambda: self.open_web("/#/admin"))
        head.addWidget(self.admin_btn)
        out = QtWidgets.QPushButton("登出")
        out.clicked.connect(self.logout)
        head.addWidget(out)
        mv.addLayout(head)
        self.banner = QtWidgets.QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(f"background:#fff7e6;color:#8a5a00;border-radius:6px;padding:8px")
        self.banner.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.banner.linkActivated.connect(self._banner_link)
        self.banner.hide()
        mv.addWidget(self.banner)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        self.grid_host = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.grid_host)
        self.grid.setAlignment(Qt.AlignTop)
        scroll.setWidget(self.grid_host)
        mv.addWidget(scroll, 1)
        self.stack.addWidget(self.main_page)
        self.cards: Dict[str, ModuleCard] = {}
        self.remote_action.connect(self._remote)
        self.events_changed.connect(lambda _t: self.reload())
        self._endpoint: Optional[local.LocalEndpoint] = None
        self._stop = threading.Event()

    # ---- 頁面 --------------------------------------------------------------------
    def start(self) -> None:
        if self.launcher.resume():
            self.show_main()
        else:
            self.stack.setCurrentWidget(self.login_page)

    def show_main(self) -> None:
        u = self.launcher.user or {}
        self.hello.setText(f"<b>{u.get('display_name', '')}</b>" + ("（站長）" if u.get("manager") else ""))
        self.admin_btn.setVisible(bool(u.get("manager")))
        self.stack.setCurrentWidget(self.main_page)
        self._start_services()
        self.reload()
        run_bg(lambda: self.launcher.ensure_labcomm(), None, lambda e: None)

    def reload(self) -> None:
        run_bg(self.launcher.refresh, self._show_modules, self._refresh_failed)

    def reload_local(self) -> None:
        for c in self.cards.values():
            c.m["running"] = self.launcher.running(c.m["id"])
            c.render()

    def _refresh_failed(self, msg: str) -> None:
        if "登入" in msg:
            self.stack.setCurrentWidget(self.login_page)
        self.error(msg)

    def _show_modules(self, mods: List[Dict[str, Any]]) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.cards.clear()
        shown = [m for m in mods if m["kind"] in ("desktop", "web") and m["id"] != "launcher"]
        for i, m in enumerate(shown):
            card = ModuleCard(self, m)
            self.cards[m["id"]] = card
            self.grid.addWidget(card, i // 2, i % 2)
        me = next((m for m in mods if m["id"] == "launcher"), None)
        if me and me.get("latest") and parse_version(me["latest"]) > parse_version(__version__):
            self.banner.setText(f"大程式有新版 v{me['latest']}（目前 v{__version__}）。<a href='update'>立即更新</a>")
            self.banner.show()
        else:
            self.banner.hide()

    def _banner_link(self, link: str) -> None:
        if link == "update":
            self.banner.setText("下載中…")
            run_bg(lambda emit: self.launcher.install_latest("launcher", emit),
                   lambda v: (self.banner.setText(f"已下載 v{v}，關閉再打開大程式就會使用新版。")),
                   lambda e: self.banner.setText(f"更新失敗：{e}"),
                   lambda msg, f: self.banner.setText(f"{msg} {int(f * 100)}%"))

    # ---- 背景：本機傳遞、跨電腦事件 -----------------------------------------------------
    def _start_services(self) -> None:
        if self._endpoint is None:
            def handler(action: str, payload: Dict[str, Any]) -> Any:
                if action == actions.LAUNCH:
                    self.remote_action.emit(str(payload.get("module")), str(payload.get("action") or ""),
                                            dict(payload.get("payload") or {}))
                    return {"accepted": True}
                raise CommError(f"大程式不支援 {action}")
            try:
                self._endpoint = local.LocalEndpoint("launcher", __version__, handler, [actions.LAUNCH]).start()
            except OSError:
                self._endpoint = None
        if not getattr(self, "_listener", None):
            self._listener = threading.Thread(target=self._listen, daemon=True, name="qel-events")
            self._listener.start()

    def _listen(self) -> None:
        after = -1
        while not self._stop.is_set():
            if self.launcher.user is None:
                self._stop.wait(3)
                continue
            try:
                r = self.launcher.client.events(after=after, wait=25, topics=["handoff", "modules", "access"])
            except NotLoggedIn:
                self._stop.wait(10)
                continue
            except CommError:
                self._stop.wait(5)
                continue
            after = r["last"]
            for ev in r["events"]:
                if ev["topic"] == "handoff":
                    d = ev["data"] or {}
                    self.remote_action.emit(str(d.get("module")), str(d.get("action") or ""), dict(d.get("payload") or {}))
                else:
                    self.events_changed.emit(ev["topic"])

    def _remote(self, module: str, action: str, payload: Dict[str, Any]) -> None:
        known = {m["id"] for m in self.launcher.modules}
        if not module or (known and module not in known):
            return
        if self.launcher.store.installed(module) is None:
            self.error(f"收到要給 {module} 的動作，但這台電腦還沒有安裝 {module}")
            return
        self.toast(f"開啟 {module}…")
        run_bg(lambda: self.launcher.deliver_after_launch(module, action, payload) if action
               else self.launcher.launch(module), lambda _: self.reload_local(), self.error)

    def hand_to(self, module: str, action: str, payload: Dict[str, Any]) -> None:
        """卡片收到拖進來的數據檔。"""
        if self.launcher.store.installed(module) is None:
            self.error("請先安裝這個模塊")
            return
        self._remote(module, action, payload)

    # ---- 其他 ------------------------------------------------------------------
    def open_web(self, target: str) -> None:
        """網頁一律經一次性登入票開啟（瀏覽器直接是登入狀態）。target：/#/… 或論文庫網址。"""
        import urllib.parse
        base = self.launcher.client.base

        def go() -> str:
            t = self.launcher.client.api("POST", "/sso/ticket")
            return f"{base}{t['url']}&next={urllib.parse.quote(target, safe='')}"
        run_bg(go, webbrowser.open, lambda e: webbrowser.open(target if target.startswith("http") else base + target))

    def open_path(self, p: Path) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p)))

    def toast(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 6000)

    def error(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 12000)
        QtWidgets.QMessageBox.warning(self, "QEL Lab", msg)

    def logout(self) -> None:
        self.launcher.logout()
        self.stack.setCurrentWidget(self.login_page)

    def closeEvent(self, e) -> None:  # noqa: N802
        self._stop.set()
        if self._endpoint is not None:
            self._endpoint.stop()
        super().closeEvent(e)
