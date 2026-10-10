"""平台監控（站長）：網站狀態、使用者與模塊權限、模塊發佈與更新、操作紀錄。

用站長的論文庫帳號登入（qelcomm）；權限資料都在 Hub（NAS 中繼站），論文庫帳號的停用 / 啟用會寫回論文庫。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from .bg import run_bg


def _card(title: str) -> tuple:
    f = QtWidgets.QFrame()
    f.setProperty("role", "card")
    v = QtWidgets.QVBoxLayout(f)
    v.setContentsMargins(12, 8, 12, 8)
    t = QtWidgets.QLabel(title)
    t.setProperty("role", "muted")
    val = QtWidgets.QLabel("—")
    val.setProperty("role", "big")
    sub = QtWidgets.QLabel("")
    sub.setProperty("role", "muted")
    v.addWidget(t)
    v.addWidget(val)
    v.addWidget(sub)
    return f, val, sub


class PublishDialog(QtWidgets.QDialog):
    def __init__(self, module: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"發佈 {module.get('name', module['id'])} 新版")
        f = QtWidgets.QFormLayout(self)
        latest = module.get("latest") or "0.0.0"
        parts = [int(x) for x in latest.split(".") if x.isdigit()] or [0, 0, 0]
        parts[-1] += 1
        self.version = QtWidgets.QLineEdit(".".join(map(str, parts)))
        self.file = QtWidgets.QLineEdit()
        b = QtWidgets.QPushButton("選擇…")
        b.clicked.connect(self._browse)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.file, 1)
        row.addWidget(b)
        self.notes = QtWidgets.QPlainTextEdit()
        self.notes.setMaximumHeight(80)
        f.addRow("版本（純數字）", self.version)
        f.addRow("檔案（.zip）", row)
        f.addRow("說明", self.notes)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _browse(self) -> None:
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "選擇發佈檔", "", "壓縮檔 (*.zip);;所有檔案 (*)")
        if p:
            self.file.setText(p)


class PlatformTab(QtWidgets.QWidget):
    """Monitor 的「平台（站長）」分頁。"""

    def __init__(self, gateway_fn, parent=None) -> None:
        super().__init__(parent)
        self.gateway_fn = gateway_fn            # () → Hub 網址（Monitor 目前連的那台）
        self.session = None
        self.modules: List[Dict[str, Any]] = []
        self.user_mods: List[Dict[str, Any]] = []
        self._loading = False
        v = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.who = QtWidgets.QLabel("未登入")
        self.who.setProperty("role", "h2")
        top.addWidget(self.who)
        top.addStretch(1)
        self.b_login = QtWidgets.QPushButton("用站長帳號登入…")
        self.b_login.clicked.connect(self.login_or_out)
        b = QtWidgets.QPushButton("⟳ 重新整理")
        b.clicked.connect(self.refresh)
        b2 = QtWidgets.QPushButton("🌐 平台網頁")
        b2.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(self._gw() + "/portal#/admin")))
        for x in (self.b_login, b, b2):
            top.addWidget(x)
        v.addLayout(top)
        cards = QtWidgets.QHBoxLayout()
        self.c_pl = _card("論文庫")
        self.c_hub = _card("Hub")
        self.c_nodes = _card("量測節點（線上 / 全部）")
        self.c_data = _card("數據目錄")
        self.c_users = _card("登入中的使用者")
        for c in (self.c_pl, self.c_hub, self.c_nodes, self.c_data, self.c_users):
            cards.addWidget(c[0])
        v.addLayout(cards)
        split = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        ub = QtWidgets.QWidget()
        ul = QtWidgets.QVBoxLayout(ub)
        ul.setContentsMargins(0, 0, 0, 0)
        h = QtWidgets.QLabel("使用者與模塊權限（勾選 = 可以使用；「帳號」取消勾選 = 停用論文庫帳號並登出）")
        h.setProperty("role", "muted")
        ul.addWidget(h)
        self.users = QtWidgets.QTableWidget()
        self.users.verticalHeader().setVisible(False)
        self.users.itemChanged.connect(self._user_changed)
        ul.addWidget(self.users, 1)
        split.addWidget(ub)
        mb = QtWidgets.QWidget()
        ml = QtWidgets.QVBoxLayout(mb)
        ml.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        h = QtWidgets.QLabel("模塊發佈（啟動器依這裡的最新版單獨安裝 / 更新各模塊）")
        h.setProperty("role", "muted")
        row.addWidget(h, 1)
        self.b_pub = QtWidgets.QPushButton("發佈新版…")
        self.b_pub.clicked.connect(self.publish)
        row.addWidget(self.b_pub)
        ml.addLayout(row)
        self.mods = QtWidgets.QTreeWidget()
        self.mods.setHeaderLabels(["模塊", "代號", "類型", "最新版", "版本數", "最近發佈"])
        self.mods.setRootIsDecorated(False)
        for i, w in enumerate((160, 110, 70, 90, 70, 200)):
            self.mods.setColumnWidth(i, w)
        ml.addWidget(self.mods, 1)
        split.addWidget(mb)
        ab = QtWidgets.QWidget()
        al = QtWidgets.QVBoxLayout(ab)
        al.setContentsMargins(0, 0, 0, 0)
        h = QtWidgets.QLabel("最近的操作")
        h.setProperty("role", "muted")
        al.addWidget(h)
        self.audit = QtWidgets.QTreeWidget()
        self.audit.setHeaderLabels(["時間", "使用者", "動作", "內容"])
        self.audit.setRootIsDecorated(False)
        self.audit.setColumnWidth(0, 150)
        al.addWidget(self.audit, 1)
        split.addWidget(ab)
        split.setSizes([300, 200, 160])
        v.addWidget(split, 1)
        self.msg = QtWidgets.QLabel("")
        self.msg.setProperty("role", "muted")
        v.addWidget(self.msg)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(lambda: self.refresh(quiet=True))
        self.timer.start(15000)
        self._try_saved_login()

    def _gw(self) -> str:
        return (self.session.gateway if self.session else self.gateway_fn() or "").rstrip("/")

    # ---- 登入 --------------------------------------------------------------
    def _try_saved_login(self) -> None:
        try:
            from qelcomm import Session
        except ImportError:
            self.who.setText("沒有 qelcomm 通信模塊")
            self.b_login.setEnabled(False)
            return
        s = Session.current()
        if s is None:
            self._set_session(None)
            return

        def ok(_r):
            if self.session is not None:                # 這段時間已經用站長帳號登入了
                return
            self._set_session(s if s.is_owner else None)
            if not s.is_owner:
                self.msg.setText(f"目前登入的 {s.username} 不是站長；請用站長帳號登入")
        run_bg(s.me, ok, lambda _m: None if self.session is not None else self._set_session(None))

    def login_or_out(self) -> None:
        if self.session is not None:
            s, self.session = self.session, None
            run_bg(s.logout)
            self._set_session(None)
            return
        from qelcomm import Session

        d = QtWidgets.QDialog(self)
        d.setWindowTitle("站長登入")
        f = QtWidgets.QFormLayout(d)
        gw = QtWidgets.QLineEdit(self._gw())
        u = QtWidgets.QLineEdit()
        p = QtWidgets.QLineEdit()
        p.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        c = QtWidgets.QLineEdit()
        c.setPlaceholderText("兩步驟驗證碼（有開才需要）")
        msg = QtWidgets.QLabel("")
        f.addRow("Hub", gw)
        f.addRow("帳號", u)
        f.addRow("密碼", p)
        f.addRow("驗證碼", c)
        f.addRow(msg)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        f.addRow(bb)
        bb.rejected.connect(d.reject)

        def go():
            msg.setText("登入中…")

            def ok(s):
                if not s.is_owner:
                    msg.setText("這個帳號不是站長")
                    run_bg(s.logout)
                    return
                self._set_session(s)
                d.accept()
            run_bg(lambda: Session.login(gw.text().strip(), u.text().strip(), p.text(), c.text().strip(),
                                         save=False, client="Monitor"),
                   ok, lambda m: msg.setText("請輸入兩步驟驗證碼" if m == "need_2fa" else f"❌ {m}"))
        bb.accepted.connect(go)
        d.exec()

    def _set_session(self, s) -> None:
        self.session = s
        on = s is not None
        self.b_login.setText("登出" if on else "用站長帳號登入…")
        self.who.setText(f"站長：{s.user.get('display_name') or s.username}" if on else "未登入（只顯示公開狀態）")
        self.users.setEnabled(on)
        self.b_pub.setEnabled(on)
        self.refresh()

    # ---- 資料 --------------------------------------------------------------
    def refresh(self, quiet: bool = False) -> None:
        if self._loading or not self.isVisible() and quiet:
            return
        s = self.session
        if s is None:
            self._fill_status(None)
            return
        self._loading = True

        def work():
            return {"status": s.status(), "users": s.request("GET", "/api/platform/users"), "modules": s.modules(),
                    "audit": s.request("GET", "/api/platform/audit?limit=60")["audit"]}

        def ok(d):
            self._loading = False
            self._fill_status(d["status"])
            self._fill_users(d["users"])
            self._fill_modules(d["modules"])
            self._fill_audit(d["audit"])
            self.msg.setText(f"更新於 {time.strftime('%H:%M:%S')}")

        def err(m):
            self._loading = False
            self.msg.setText(f"❌ {m}")
        run_bg(work, ok, err)

    def _fill_status(self, st: Optional[Dict[str, Any]]) -> None:
        if st is None:
            for c in (self.c_pl, self.c_hub, self.c_nodes, self.c_data, self.c_users):
                c[1].setText("—")
                c[2].setText("")
            return
        pl = st.get("paperlib") or {}
        self.c_pl[1].setText("● 連線" if pl.get("online") else ("● 連不到" if pl.get("configured") else "未設定"))
        self.c_pl[1].setStyleSheet("color:#51cf66" if pl.get("online") else "color:#ff6b6b")
        self.c_pl[2].setText(f"{pl.get('name') or ''} {pl.get('version') or ''}　{pl.get('url') or ''}".strip())
        hub = st.get("hub") or {}
        self.c_hub[1].setText(str(hub.get("version") or ""))
        up = float(hub.get("uptime") or 0)
        self.c_hub[2].setText(f"已執行 {up / 3600:.1f} 小時" if up else "")
        nodes = st.get("nodes") or []
        self.c_nodes[1].setText(f"{sum(1 for n in nodes if n.get('online'))} / {len(nodes)}")
        self.c_nodes[2].setText(f"電腦 {st.get('devices', 0)} 台在線")
        self.c_data[1].setText(str(st.get("catalog", 0)))
        self.c_data[2].setText("筆")
        self.c_users[1].setText(str(st.get("users_logged_in", 0)))

    def _fill_users(self, r: Dict[str, Any]) -> None:
        self.user_mods = r["modules"]
        users = r["users"]
        cols = ["使用者", "帳號"] + [m["name"] for m in self.user_mods]
        self.users.blockSignals(True)
        self.users.clear()
        self.users.setColumnCount(len(cols))
        self.users.setHorizontalHeaderLabels(cols)
        self.users.setRowCount(len(users))
        for i, u in enumerate(users):
            name = QtWidgets.QTableWidgetItem(f"{u.get('display_name') or u['username']}（{u['username']}）"
                                              + ("　★站長" if u.get("owner") else ""))
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.users.setItem(i, 0, name)
            acc = QtWidgets.QTableWidgetItem("啟用")
            acc.setData(Qt.ItemDataRole.UserRole, ("user", u["id"], u["username"]))
            fl = Qt.ItemFlag.ItemIsEnabled | (Qt.ItemFlag.NoItemFlags if u.get("owner") else Qt.ItemFlag.ItemIsUserCheckable)
            acc.setFlags(fl)
            acc.setCheckState(Qt.CheckState.Unchecked if u.get("disabled") else Qt.CheckState.Checked)
            self.users.setItem(i, 1, acc)
            for j, m in enumerate(self.user_mods):
                it = QtWidgets.QTableWidgetItem("")
                it.setData(Qt.ItemDataRole.UserRole, ("perm", u["username"], m["id"]))
                editable = not u.get("owner") and not u.get("disabled")
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | (Qt.ItemFlag.ItemIsUserCheckable if editable
                                                         else Qt.ItemFlag.NoItemFlags))
                it.setCheckState(Qt.CheckState.Checked if u["modules"].get(m["id"], True) else Qt.CheckState.Unchecked)
                self.users.setItem(i, 2 + j, it)
        self.users.resizeColumnsToContents()
        self.users.blockSignals(False)

    def _user_changed(self, it: QtWidgets.QTableWidgetItem) -> None:
        tag = it.data(Qt.ItemDataRole.UserRole)
        s = self.session
        if not tag or s is None:
            return
        on = it.checkState() == Qt.CheckState.Checked

        def revert(m):
            self.users.blockSignals(True)
            it.setCheckState(Qt.CheckState.Unchecked if on else Qt.CheckState.Checked)
            self.users.blockSignals(False)
            self.msg.setText(f"❌ {m}")
        if tag[0] == "user":
            run_bg(lambda: s.request("PATCH", f"/api/platform/users/{tag[1]}", {"enabled": on}),
                   lambda _r: (self.msg.setText(f"{tag[2]}：{'已啟用' if on else '已停用並登出'}"), self.refresh()),
                   revert)
        else:
            run_bg(lambda: s.request("PUT", "/api/platform/perms", {"username": tag[1], "module": tag[2],
                                                                   "allowed": on}),
                   lambda _r: self.msg.setText(f"{tag[1]} 的 {tag[2]}：{'開放' if on else '關閉'}"), revert)

    def _fill_modules(self, mods: List[Dict[str, Any]]) -> None:
        self.modules = mods
        cur = self.mods.currentItem().data(0, Qt.ItemDataRole.UserRole) if self.mods.currentItem() else None
        self.mods.clear()
        for m in mods:
            v = (m.get("versions") or [{}])[0] if m.get("versions") else {}
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(v["time"])) if v.get("time") else ""
            it = QtWidgets.QTreeWidgetItem([m.get("name", ""), m["id"], "網頁" if m.get("kind") == "web" else "桌面",
                                            m.get("latest") or "—", str(len(m.get("versions") or [])),
                                            f"{when} {v.get('by') or ''}".strip()])
            it.setData(0, Qt.ItemDataRole.UserRole, m["id"])
            self.mods.addTopLevelItem(it)
            if m["id"] == cur:
                self.mods.setCurrentItem(it)

    def _fill_audit(self, rows: List[Dict[str, Any]]) -> None:
        self.audit.clear()
        for a in rows:
            self.audit.addTopLevelItem(QtWidgets.QTreeWidgetItem(
                [time.strftime("%m-%d %H:%M:%S", time.localtime(a["time"])), a.get("user") or "",
                 a.get("action") or "", a.get("detail") or ""]))

    def publish(self) -> None:
        s = self.session
        it = self.mods.currentItem()
        if s is None or it is None:
            self.msg.setText("先選一個模塊")
            return
        m = next(x for x in self.modules if x["id"] == it.data(0, Qt.ItemDataRole.UserRole))
        if m.get("kind") == "web":
            self.msg.setText("網頁模塊不用發佈")
            return
        d = PublishDialog(m, self)
        if not d.exec():
            return
        path, ver, notes = d.file.text().strip(), d.version.text().strip(), d.notes.toPlainText().strip()
        if not Path(path).is_file():
            self.msg.setText("找不到檔案")
            return
        self.msg.setText(f"上傳 {Path(path).name}…")
        run_bg(lambda: s.publish_module(m["id"], ver, path, notes),
               lambda _r: (self.msg.setText(f"✔ 已發佈 {m['name']} {ver}：各電腦的啟動器會顯示「更新」"), self.refresh()),
               lambda e: self.msg.setText(f"❌ {e}"))
