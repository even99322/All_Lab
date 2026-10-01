"""監控程式的畫面（PySide6）。網路動作都在背景執行緒，畫面不會卡住。"""
from __future__ import annotations

import time
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from labcomm.errors import CommError

from . import __version__
from .core import SERVICE_NAMES, Alert, Monitor, MonitorConfig, Snapshot, read_module_zip

OK, WARN, BAD, OFF = "#16a34a", "#d97706", "#dc2626", "#9ca3af"
STEP_ICON = {"pending": "○", "running": "◔", "ok": "✔", "fail": "✖", "skip": "–", "warn": "⚠"}


# ---- 背景工作 --------------------------------------------------------------------
class _Signals(QtCore.QObject):
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    progress = QtCore.Signal(object)


class _Task(QtCore.QRunnable):
    def __init__(self, fn: Callable[..., Any], with_progress: bool) -> None:
        super().__init__()
        self.fn, self.with_progress = fn, with_progress
        self.s = _Signals()

    def run(self) -> None:
        try:
            r = self.fn(self.s.progress.emit) if self.with_progress else self.fn()
            self.s.done.emit(r)
        except CommError as e:
            self.s.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.s.failed.emit(f"{type(e).__name__}: {e}")


_keep: List[_Task] = []


def run_bg(fn, on_done=None, on_fail=None, on_progress=None) -> None:
    t = _Task(fn, on_progress is not None)
    _keep.append(t)

    def cleanup(*_):
        if t in _keep:
            _keep.remove(t)
    if on_done:
        t.s.done.connect(on_done)
    if on_fail:
        t.s.failed.connect(on_fail)
    if on_progress:
        t.s.progress.connect(on_progress)
    t.s.done.connect(cleanup)
    t.s.failed.connect(cleanup)
    QtCore.QThreadPool.globalInstance().start(t)


def dot(color: str) -> str:
    return f'<span style="color:{color};font-size:16px">●</span>'


def ago(ts: Optional[float]) -> str:
    if not ts:
        return "—"
    s = time.time() - ts
    if s < 60:
        return "剛剛"
    if s < 3600:
        return f"{int(s // 60)} 分鐘前"
    if s < 86400:
        return f"{int(s // 3600)} 小時前"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


# ---- 登入 ------------------------------------------------------------------------
class LoginDialog(QtWidgets.QDialog):
    def __init__(self, mon: Monitor, parent=None) -> None:
        super().__init__(parent)
        self.mon = mon
        self.setWindowTitle("登入 QEL Lab 監控程式")
        self.setMinimumWidth(420)
        f = QtWidgets.QFormLayout(self)
        self.url = QtWidgets.QLineEdit(mon.cfg.portal_url)
        self.agent_url = QtWidgets.QLineEdit(mon.cfg.agent_url)
        self.agent_url.setPlaceholderText("空白＝大程式網站同一台主機的 8767")
        self.user = QtWidgets.QLineEdit(mon.cfg.username)
        self.pw = QtWidgets.QLineEdit()
        self.pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.code = QtWidgets.QLineEdit()
        self.code.setPlaceholderText("有開兩步驟驗證才需要")
        self.remember = QtWidgets.QCheckBox("記住登入")
        self.remember.setChecked(mon.cfg.remember)
        self.err = QtWidgets.QLabel()
        self.err.setStyleSheet(f"color:{BAD}")
        self.err.setWordWrap(True)
        f.addRow("大程式網址", self.url)
        f.addRow("更新代理網址", self.agent_url)
        f.addRow("帳號（站長）", self.user)
        f.addRow("密碼", self.pw)
        f.addRow("驗證碼", self.code)
        f.addRow("", self.remember)
        f.addRow(self.err)
        bb = QtWidgets.QDialogButtonBox()
        self.ok = bb.addButton("登入", QtWidgets.QDialogButtonBox.AcceptRole)
        emerg = bb.addButton("緊急模式…", QtWidgets.QDialogButtonBox.ActionRole)
        bb.addButton("取消", QtWidgets.QDialogButtonBox.RejectRole)
        self.ok.clicked.connect(self._login)
        emerg.clicked.connect(self._emergency)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        (self.pw if self.user.text() else self.user).setFocus()

    def _apply_urls(self) -> None:
        self.mon.cfg.portal_url = self.url.text().strip()
        self.mon.cfg.agent_url = self.agent_url.text().strip()
        self.mon.cfg.remember = self.remember.isChecked()
        from labcomm import PortalClient
        self.mon.portal = PortalClient(self.mon.cfg.portal_url, "", timeout=15, client_name="qel-monitor")

    def _login(self) -> None:
        self._apply_urls()
        self.ok.setEnabled(False)
        self.err.setText("登入中…")
        u, p, c = self.user.text().strip(), self.pw.text(), self.code.text().strip()
        run_bg(lambda: self.mon.login(u, p, c), self._done, self._fail)

    def _done(self, r) -> None:
        self.ok.setEnabled(True)
        if r.get("need_2fa"):
            self.err.setText("請輸入驗證 App 上的 6 位數字")
            self.code.setFocus()
            return
        self.mon.cfg.save()
        self.accept()

    def _fail(self, msg: str) -> None:
        self.ok.setEnabled(True)
        self.err.setText(msg)

    def _emergency(self) -> None:
        tok, ok = QtWidgets.QInputDialog.getText(self, "緊急模式", "大程式網站壞掉時，用更新代理的緊急 token（docker-compose.yml 的 "
                                                 "QEL_AGENT_TOKEN）登入，只能管理服務：", QtWidgets.QLineEdit.Password)
        if not ok or not tok:
            return
        self._apply_urls()
        run_bg(lambda: self.mon.use_emergency(tok), lambda _: self.accept(), self._fail)


# ---- 總覽 ------------------------------------------------------------------------
class TrafficBars(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.series: List[Dict[str, Any]] = []
        self.setMinimumHeight(70)

    def set_series(self, s: List[Dict[str, Any]]) -> None:
        self.series = s or []
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QtGui.QPainter(self)
        n = len(self.series)
        if not n:
            p.setPen(QtGui.QColor(OFF))
            p.drawText(self.rect(), Qt.AlignCenter, "沒有流量資料")
            return
        mx = max(1, max(x["n"] for x in self.series))
        w = self.width() / n
        for i, x in enumerate(self.series):
            hgt = (self.height() - 4) * x["n"] / mx
            p.fillRect(QtCore.QRectF(i * w, self.height() - hgt, max(1.0, w - 1), hgt),
                       QtGui.QColor(BAD if x.get("err") else "#1c6dd0"))


class Overview(QtWidgets.QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        v = QtWidgets.QVBoxLayout(self)
        self.cards = QtWidgets.QGridLayout()
        self.card_labels: Dict[str, QtWidgets.QLabel] = {}
        for i, sid in enumerate(("portal", "paperlib", "labhub", "agent")):
            box = QtWidgets.QGroupBox(SERVICE_NAMES[sid])
            lab = QtWidgets.QLabel("—")
            lab.setTextFormat(Qt.RichText)
            lab.setWordWrap(True)
            lab.setMinimumHeight(70)
            QtWidgets.QVBoxLayout(box).addWidget(lab)
            self.card_labels[sid] = lab
            self.cards.addWidget(box, i // 2, i % 2)
        v.addLayout(self.cards)
        v.addWidget(QtWidgets.QLabel("<b>異常</b>"))
        self.alerts = QtWidgets.QListWidget()
        self.alerts.setMaximumHeight(130)
        v.addWidget(self.alerts)
        v.addWidget(QtWidgets.QLabel("<b>最近 60 分鐘的請求</b>（紅色＝有錯誤）"))
        self.bars = TrafficBars()
        v.addWidget(self.bars)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)
        row = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton("開啟大程式網站")
        b.clicked.connect(lambda: self.win.open_web("/#/"))
        b2 = QtWidgets.QPushButton("網頁的系統狀態")
        b2.clicked.connect(lambda: self.win.open_web("/#/admin/status"))
        row.addWidget(b)
        row.addWidget(b2)
        row.addStretch()
        v.addLayout(row)
        v.addStretch()

    def show_snapshot(self, s: Snapshot) -> None:
        for r in s.service_rows():
            color = OK if r["online"] else (OFF if r["online"] is None else BAD)
            lines = [f"{dot(color)} <b>{'正常' if r['online'] else '未知' if r['online'] is None else '離線'}</b>"
                     + (f"　v{r['version']}" if r["version"] else "")]
            if r["ms"] is not None:
                lines.append(f"回應 {r['ms']} ms")
            if r["container"]:
                lines.append(f"容器：{r['container']}")
            if r["code_version"] and r["version"] and r["code_version"] != r["version"]:
                lines.append(f'<span style="color:{WARN}">程式 v{r["code_version"]}（尚未重新啟動）</span>')
            if r["error"] and not r["online"]:
                lines.append(f'<span style="color:{BAD}">{r["error"][:160]}</span>')
            if r["job"]:
                lines.append(f'<span style="color:{WARN}">進行中：{r["job"]["title"]}</span>')
            self.card_labels[r["id"]].setText("<br>".join(lines))
        self.alerts.clear()
        for a in s.alerts:
            it = QtWidgets.QListWidgetItem(("⛔ " if a.level == "bad" else "⚠ ") + a.message)
            it.setForeground(QtGui.QColor(BAD if a.level == "bad" else WARN))
            self.alerts.addItem(it)
        if not s.alerts:
            self.alerts.addItem("一切正常")
        t = (s.health or {}).get("traffic") or {}
        self.bars.set_series(t.get("series") or [])
        p = (s.health or {}).get("portal") or {}
        bits = []
        if t:
            bits.append(f"{t.get('requests', 0)} 次請求、平均 {t.get('avg_ms') or '—'} ms、{t.get('errors', 0)} 個錯誤")
            bits.append("線上：" + ("、".join(t.get("online") or []) or "—"))
        if p.get("disk_free_gb") is not None:
            bits.append(f"NAS 剩 {p['disk_free_gb']} / {p['disk_total_gb']} GB")
        if p.get("sessions") is not None:
            bits.append(f"{p['sessions']} 個登入、{p.get('datasets', 0)} 筆數據、{p.get('tags', 0)} 個標籤")
        self.summary.setText("　·　".join(bits))


# ---- 服務更新 ----------------------------------------------------------------------
class DropZip(QtWidgets.QLabel):
    dropped = QtCore.Signal(str)

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(56)
        self.setStyleSheet("border:2px dashed #9ca3af;border-radius:8px;padding:8px;")

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if any(u.toLocalFile().lower().endswith(".zip") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(".zip"):
                self.dropped.emit(u.toLocalFile())
                return

    def mousePressEvent(self, e) -> None:  # noqa: N802
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "選擇 zip", "", "zip (*.zip)")
        if p:
            self.dropped.emit(p)


class Services(QtWidgets.QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.zip: Optional[str] = None
        v = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("服務："))
        self.svc = QtWidgets.QComboBox()
        for sid, name in SERVICE_NAMES.items():
            self.svc.addItem(name, sid)
        self.svc.currentIndexChanged.connect(self._fill_backups)
        row.addWidget(self.svc)
        row.addStretch()
        v.addLayout(row)
        self.drop = DropZip("把新版 zip 拖到這裡（或按一下選檔）")
        self.drop.dropped.connect(self._picked)
        v.addWidget(self.drop)
        btns = QtWidgets.QHBoxLayout()
        self.b_update = QtWidgets.QPushButton("⬆ 更新")
        self.b_update.setEnabled(False)
        self.b_update.clicked.connect(self._update)
        btns.addWidget(self.b_update)
        for action, label in (("restart", "重新啟動"), ("rebuild", "重建"), ("full_rebuild", "完全重建"),
                              ("stop", "停止"), ("start", "啟動")):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(lambda _=False, a=action: self._action(a))
            btns.addWidget(b)
        btns.addStretch()
        v.addLayout(btns)
        v.addWidget(QtWidgets.QLabel("<b>備份</b>（每次更新前自動建立，保留 20 份）"))
        self.backups = QtWidgets.QListWidget()
        self.backups.setMaximumHeight(110)
        v.addWidget(self.backups)
        rb = QtWidgets.QPushButton("回到選取的備份")
        rb.clicked.connect(self._rollback)
        v.addWidget(rb, alignment=Qt.AlignLeft)
        v.addWidget(QtWidgets.QLabel("<b>進度</b>"))
        self.steps = QtWidgets.QLabel()
        self.steps.setTextFormat(Qt.RichText)
        v.addWidget(self.steps)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)
        self._snap: Optional[Snapshot] = None

    def show_snapshot(self, s: Snapshot) -> None:
        self._snap = s
        self._fill_backups()

    def _fill_backups(self) -> None:
        self.backups.clear()
        sid = self.svc.currentData()
        for r in (self._snap.service_rows() if self._snap else []):
            if r["id"] == sid:
                for b in r["backups"]:
                    it = QtWidgets.QListWidgetItem(f"v{b['version']}　{ago(b['time'])}　{b['name']}")
                    it.setData(Qt.UserRole, b["name"])
                    self.backups.addItem(it)

    def _picked(self, path: str) -> None:
        self.zip = path
        self.drop.setText(f"新版：{Path(path).name}")
        self.b_update.setEnabled(True)

    def _busy(self, title: str) -> None:
        self.log.clear()
        self.steps.setText(f"{title}…")

    def _progress(self, j: Dict[str, Any]) -> None:
        self.steps.setText("　".join(f"{STEP_ICON.get(s['status'], '•')} {s['title']}" for s in j.get("steps", [])))
        for line in j.get("lines", []):
            self.log.appendPlainText(time.strftime("%H:%M:%S ", time.localtime(line["time"])) + line["msg"])

    def _finished(self, j: Dict[str, Any]) -> None:
        if j.get("ok"):
            self.win.notify("完成", j.get("result", ""))
        else:
            QtWidgets.QMessageBox.warning(self, "失敗", j.get("result", "失敗"))
        self.win.refresh()

    def _failed(self, msg: str) -> None:
        self.log.appendPlainText("❌ " + msg)
        QtWidgets.QMessageBox.warning(self, "失敗", msg)

    def _update(self) -> None:
        sid, name = self.svc.currentData(), self.svc.currentText()
        if not self.zip or QtWidgets.QMessageBox.question(
                self, "更新", f"用 {Path(self.zip).name} 更新「{name}」？\n更新時服務會暫停幾秒，失敗會自動換回舊版。") \
                != QtWidgets.QMessageBox.Yes:
            return
        self._busy(f"更新 {name}")
        z = Path(self.zip)
        run_bg(lambda emit: self.win.mon.update_service(sid, z, emit), self._finished, self._failed, self._progress)

    def _action(self, action: str) -> None:
        sid, name = self.svc.currentData(), self.svc.currentText()
        label = {"restart": "重新啟動", "rebuild": "重建", "full_rebuild": "完全重建", "stop": "停止", "start": "啟動"}[action]
        if QtWidgets.QMessageBox.question(self, label, f"{label}「{name}」？") != QtWidgets.QMessageBox.Yes:
            return
        self._busy(f"{label} {name}")
        run_bg(lambda emit: self.win.mon.service_action(sid, action, emit), self._finished, self._failed,
               self._progress)

    def _rollback(self) -> None:
        it = self.backups.currentItem()
        if it is None:
            return
        sid, name = self.svc.currentData(), self.svc.currentText()
        b = it.data(Qt.UserRole)
        if QtWidgets.QMessageBox.question(self, "回到備份", f"把「{name}」換回 {b}？") != QtWidgets.QMessageBox.Yes:
            return
        self._busy(f"回到備份 {name}")
        run_bg(lambda emit: self.win.mon.service_action(sid, "rollback", emit, backup=b), self._finished,
               self._failed, self._progress)


# ---- 模塊發佈 ----------------------------------------------------------------------
class Releases(QtWidgets.QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.zip: Optional[str] = None
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(QtWidgets.QLabel("桌面模塊（量測、讀檔、通信、大程式本體、監控程式）的新版發佈到大程式；"
                                     "每台電腦的大程式會各自提示更新，不影響其他模塊。"))
        self.drop = DropZip("把模塊 zip 拖到這裡（zip 裡要有 module.json）")
        self.drop.dropped.connect(self._picked)
        v.addWidget(self.drop)
        self.info = QtWidgets.QLabel()
        v.addWidget(self.info)
        row = QtWidgets.QHBoxLayout()
        self.notes = QtWidgets.QLineEdit()
        self.notes.setPlaceholderText("版本說明")
        row.addWidget(self.notes, 1)
        self.b_pub = QtWidgets.QPushButton("發佈")
        self.b_pub.setEnabled(False)
        self.b_pub.clicked.connect(self._publish)
        row.addWidget(self.b_pub)
        v.addLayout(row)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["模塊", "代號", "類型", "最新版本"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        v.addWidget(self.table, 1)

    def load(self) -> None:
        if self.win.mon.user is None:
            return
        run_bg(self.win.mon.portal.modules, self._show, lambda m: self.info.setText(m))

    def _show(self, mods: List[Dict[str, Any]]) -> None:
        mods = [m for m in mods if m["kind"] in ("desktop", "library")]
        self.table.setRowCount(len(mods))
        kinds = {"desktop": "桌面程式", "library": "套件", "web": "網頁", "service": "NAS 服務"}
        for i, m in enumerate(mods):
            for j, val in enumerate((m["name"], m["id"], kinds.get(m["kind"], m["kind"]), m.get("latest") or "—")):
                self.table.setItem(i, j, QtWidgets.QTableWidgetItem(str(val)))

    def _picked(self, path: str) -> None:
        try:
            man = read_module_zip(Path(path))
        except Exception as e:  # noqa: BLE001
            self.info.setText(f'<span style="color:{BAD}">{e}</span>')
            self.b_pub.setEnabled(False)
            return
        self.zip = path
        self.info.setText(f"<b>{man.get('name', man['id'])}</b>（{man['id']}）v{man['version']}")
        self.drop.setText(Path(path).name)
        self.b_pub.setEnabled(True)

    def _publish(self) -> None:
        z, notes = Path(self.zip), self.notes.text()
        self.b_pub.setEnabled(False)
        run_bg(lambda: self.win.mon.publish_module(z, notes),
               lambda r: (self.win.notify("已發佈", f"{r['module']} v{r['version']}"), self.load(),
                          self.info.setText(f"已發佈 {r['module']} v{r['version']}")),
               lambda m: (self.info.setText(f'<span style="color:{BAD}">{m}</span>'), self.b_pub.setEnabled(True)))


# ---- 使用者 ------------------------------------------------------------------------
class Users(QtWidgets.QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        v = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("勾選＝開放。帳號、註冊審核、密碼在論文庫管理。"))
        top.addStretch()
        b = QtWidgets.QPushButton("在網頁管理")
        b.clicked.connect(lambda: self.win.open_web("/#/admin/users"))
        top.addWidget(b)
        v.addLayout(top)
        self.table = QtWidgets.QTableWidget()
        v.addWidget(self.table, 1)
        self.table.itemChanged.connect(self._changed)
        self._loading = False
        self.modules: List[Dict[str, Any]] = []

    def load(self) -> None:
        if self.win.mon.user is None:
            return
        run_bg(lambda: self.win.mon.portal.api("GET", "/admin/users"), self._show,
               lambda m: QtWidgets.QMessageBox.warning(self, "讀取失敗", m))

    def _show(self, r: Dict[str, Any]) -> None:
        self._loading = True
        self.modules = r["modules"]
        cols = ["使用者"] + [m["name"] for m in self.modules] + ["可登入大程式", "最後使用"]
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setRowCount(len(r["users"]))
        for i, u in enumerate(r["users"]):
            it = QtWidgets.QTableWidgetItem(f"{u['display_name']}（{u['username']}）" + ("　站長" if u["owner"] else ""))
            it.setFlags(Qt.ItemIsEnabled)
            it.setData(Qt.UserRole, u["username"])
            self.table.setItem(i, 0, it)
            for j, m in enumerate(self.modules):
                c = QtWidgets.QTableWidgetItem()
                c.setFlags(Qt.ItemIsEnabled | (Qt.ItemIsUserCheckable if not u["owner"] else Qt.NoItemFlags))
                c.setCheckState(Qt.Checked if u["access"].get(m["id"]) else Qt.Unchecked)
                c.setData(Qt.UserRole, ("access", m["id"]))
                self.table.setItem(i, 1 + j, c)
            c = QtWidgets.QTableWidgetItem()
            c.setFlags(Qt.ItemIsEnabled | (Qt.ItemIsUserCheckable if not u["owner"] else Qt.NoItemFlags))
            c.setCheckState(Qt.Unchecked if u["blocked"] else Qt.Checked)
            c.setData(Qt.UserRole, ("blocked", None))
            self.table.setItem(i, 1 + len(self.modules), c)
            self.table.setItem(i, 2 + len(self.modules), QtWidgets.QTableWidgetItem(ago(u.get("last_seen"))))
        self.table.resizeColumnsToContents()
        self._loading = False

    def _changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._loading or item.column() == 0:
            return
        kind = item.data(Qt.UserRole)
        if not kind:
            return
        name = self.table.item(item.row(), 0).data(Qt.UserRole)
        on = item.checkState() == Qt.Checked
        body = {"access": {kind[1]: on}} if kind[0] == "access" else {"blocked": not on}
        run_bg(lambda: self.win.mon.portal.api("PUT", f"/admin/users/{name}", body),
               lambda _: self.win.statusBar().showMessage(f"已更新 {name}", 4000),
               lambda m: (QtWidgets.QMessageBox.warning(self, "失敗", m), self.load()))


# ---- 日誌 ------------------------------------------------------------------------
class Logs(QtWidgets.QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        v = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.svc = QtWidgets.QComboBox()
        for sid, name in SERVICE_NAMES.items():
            self.svc.addItem(name, sid)
        self.tail = QtWidgets.QSpinBox()
        self.tail.setRange(20, 5000)
        self.tail.setValue(300)
        b = QtWidgets.QPushButton("讀取容器日誌")
        b.clicked.connect(self.load)
        for w in (QtWidgets.QLabel("服務："), self.svc, QtWidgets.QLabel("最後"), self.tail, QtWidgets.QLabel("行"), b):
            row.addWidget(w)
        row.addStretch()
        v.addLayout(row)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        v.addWidget(self.text, 1)

    def load(self) -> None:
        sid, n = self.svc.currentData(), self.tail.value()
        self.text.setPlainText("讀取中…")
        run_bg(lambda: self.win.mon.agent.logs(sid, n), self._show, lambda m: self.text.setPlainText(m))

    def _show(self, text: str) -> None:
        self.text.setPlainText(text)
        self.text.moveCursor(QtGui.QTextCursor.End)


# ---- 主視窗 ------------------------------------------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, mon: Monitor) -> None:
        super().__init__()
        self.mon = mon
        self.setWindowTitle(f"QEL Lab 監控程式 v{__version__}")
        self.resize(980, 720)
        self.tabs = QtWidgets.QTabWidget()
        self.overview = Overview(self)
        self.services = Services(self)
        self.releases = Releases(self)
        self.users = Users(self)
        self.logs = Logs(self)
        self.tabs.addTab(self.overview, "總覽")
        self.tabs.addTab(self.services, "服務更新")
        self.tabs.addTab(self.releases, "模塊發佈")
        self.tabs.addTab(self.users, "使用者")
        self.tabs.addTab(self.logs, "日誌")
        self.tabs.currentChanged.connect(self._tab)
        self.setCentralWidget(self.tabs)
        tb = self.addToolBar("main")
        tb.setMovable(False)
        tb.addAction("重新整理", self.refresh)
        tb.addAction("登出", self._logout)
        self.who = QtWidgets.QLabel()
        sp = QtWidgets.QWidget()
        sp.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        tb.addWidget(sp)
        tb.addWidget(self.who)
        self.tray: Optional[QtWidgets.QSystemTrayIcon] = None
        if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QtWidgets.QSystemTrayIcon(self.windowIcon() if not self.windowIcon().isNull()
                                                  else self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon), self)
            self.tray.setToolTip("QEL Lab 監控程式")
            self.tray.show()
        self._known_alerts: set = set()
        self._first = True
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(max(5, mon.cfg.refresh_s) * 1000)
        self._update_who()
        self.refresh()

    def _update_who(self) -> None:
        u = self.mon.user
        self.who.setText(f"{u['display_name']}（站長）　" if u else "緊急模式（只連更新代理）　")
        for i in (2, 3):
            self.tabs.setTabEnabled(i, u is not None)

    def _tab(self, i: int) -> None:
        w = self.tabs.widget(i)
        if w is self.releases:
            self.releases.load()
        elif w is self.users:
            self.users.load()
        elif w is self.logs and not self.logs.text.toPlainText():
            self.logs.load()

    def refresh(self) -> None:
        run_bg(self.mon.snapshot, self.show_snapshot, lambda m: self.statusBar().showMessage(m, 8000))

    def show_snapshot(self, s: Snapshot) -> None:
        self.overview.show_snapshot(s)
        self.services.show_snapshot(s)
        self.statusBar().showMessage(f"更新於 {time.strftime('%H:%M:%S')}" +
                                     (f"　·　{len(s.alerts)} 個異常" if s.alerts else "　·　一切正常"))
        keys = {a.key() for a in s.alerts}
        new = [a for a in s.alerts if a.key() not in self._known_alerts]
        if new and not self._first and self.mon.cfg.notify:
            self.notify("QEL Lab 異常", "\n".join(a.message for a in new), bad=True)
        recovered = self._known_alerts - keys
        if recovered and not self._first and self.mon.cfg.notify:
            self.notify("QEL Lab 已恢復", f"{len(recovered)} 個異常已解除")
        self._known_alerts = keys
        self._first = False

    def notify(self, title: str, msg: str, bad: bool = False) -> None:
        if self.tray is not None:
            self.tray.showMessage(title, msg, QtWidgets.QSystemTrayIcon.Critical if bad
                                  else QtWidgets.QSystemTrayIcon.Information, 8000)
        self.statusBar().showMessage(f"{title}：{msg}", 10000)

    def open_web(self, path: str) -> None:
        if self.mon.user is None:
            webbrowser.open(self.mon.portal.base + path)
            return
        run_bg(lambda: self.mon.open_portal_url(path), webbrowser.open,
               lambda m: webbrowser.open(self.mon.portal.base + path))

    def _logout(self) -> None:
        self.mon.logout()
        self.mon.cfg.save()
        self.close()
