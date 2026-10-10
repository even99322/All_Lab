"""量子工程平台的畫面：登入、標籤挑選（含論文庫專案的標籤與相關論文連結）、平台選單、拖放數據檔套用設定、接收其他模塊的指令。"""
from __future__ import annotations

import html
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal

from ...integrations import platform as plat
from ...settings import setting, settings
from .worker import run_bg

log = logging.getLogger(__name__)
DATA_EXT = (".nc", ".hdf5", ".h5", ".lm.h5")


# ---- 登入 ---------------------------------------------------------------------
class PlatformLoginDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, gateway: str = "", username: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("登入量子工程平台")
        self.session = None
        self.setMinimumWidth(380)
        v = QtWidgets.QVBoxLayout(self)
        info = QtWidgets.QLabel("用論文庫的帳號密碼登入（登入一次，下次開啟會記得）。")
        info.setWordWrap(True)
        v.addWidget(info)
        f = QtWidgets.QFormLayout()
        if not gateway:
            from ...remote.hub import hub_urls

            gateway = (hub_urls() or [""])[0]
        self.gw = QtWidgets.QLineEdit(gateway)
        self.user = QtWidgets.QLineEdit(username)
        self.pw = QtWidgets.QLineEdit()
        self.pw.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.code = QtWidgets.QLineEdit()
        f.addRow("中繼站（Hub）", self.gw)
        f.addRow("帳號", self.user)
        f.addRow("密碼", self.pw)
        f.addRow("驗證碼", self.code)
        self._code_row = f.rowCount() - 1
        f.setRowVisible(self._code_row, False)
        self.form = f
        v.addLayout(f)
        self.msg = QtWidgets.QLabel("")
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("登入")
        bb.accepted.connect(self.do_login)
        bb.rejected.connect(self.reject)
        self.ok_btn = bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        v.addWidget(bb)

    def do_login(self) -> None:
        from qelcomm import Session

        gw, u, pw, code = self.gw.text().strip(), self.user.text().strip(), self.pw.text(), self.code.text().strip()
        if not (gw and u and pw):
            self.msg.setText("請填中繼站、帳號與密碼")
            return
        self.ok_btn.setEnabled(False)
        self.msg.setText("登入中…")

        def ok(s):
            self.session = s
            plat.set_session(s)
            self.accept()

        def err(m):
            self.ok_btn.setEnabled(True)
            if m == "need_2fa":
                self.form.setRowVisible(self._code_row, True)
                self.msg.setText("請輸入兩步驟驗證碼")
            else:
                self.msg.setText(f"❌ {m}")
        run_bg(lambda: Session.login(gw, u, pw, code), ok, err)


# ---- 標籤（所有模塊共用）＋ 相關論文 -------------------------------------------------------
class TagPickerDialog(QtWidgets.QDialog):
    """勾選標籤；右邊顯示被選的標籤在論文庫裡的論文（可以點擊開啟）。

    有指定論文庫專案時，最上面列出「這個專案的標籤」；按確定後，選了但專案還沒有的標籤會加到專案的數據標籤。
    """

    PROJECT_CAT = "📁 專案"

    def __init__(self, selected: List[str], parent=None, project: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(parent)
        self.project = dict(project or {}) if (project or {}).get("id") else None
        p = plat.PROJECTS.find(self.project["id"]) if self.project else None
        self.project_tags: List[str] = list((p or {}).get("tags") or [])
        self.setWindowTitle("標籤與論文" + (f"（專案：{self.project.get('name') or self.project['id']}）"
                                          if self.project else ""))
        self.resize(820, 520)
        self.selected = list(dict.fromkeys(selected))
        h = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()
        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText("搜尋標籤…")
        self.filter.textChanged.connect(self._filter)
        left.addWidget(self.filter)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["標籤", "論文", "數據"])
        self.tree.setColumnWidth(0, 200)
        self.tree.itemChanged.connect(self._changed)
        self.tree.currentItemChanged.connect(lambda cur, _p: self._show_papers(cur))
        left.addWidget(self.tree, 1)
        row = QtWidgets.QHBoxLayout()
        self.new = QtWidgets.QLineEdit()
        self.new.setPlaceholderText("新標籤")
        self.new_cat = QtWidgets.QLineEdit()
        self.new_cat.setPlaceholderText("分類")
        self.new_cat.setMaximumWidth(100)
        b = QtWidgets.QPushButton("建立")
        b.clicked.connect(self._create)
        row.addWidget(self.new, 1)
        row.addWidget(self.new_cat)
        row.addWidget(b)
        left.addLayout(row)
        h.addLayout(left, 1)
        right = QtWidgets.QVBoxLayout()
        self.chosen = QtWidgets.QLabel("")
        self.chosen.setWordWrap(True)
        right.addWidget(self.chosen)
        self.papers = QtWidgets.QTextBrowser()
        self.papers.setOpenExternalLinks(True)
        right.addWidget(self.papers, 1)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        self.add_to_project = QtWidgets.QCheckBox("把選的標籤記到這個專案")
        self.add_to_project.setChecked(True)
        self.add_to_project.setVisible(self.project is not None)
        right.addWidget(self.add_to_project)
        right.addWidget(bb)
        h.addLayout(right, 1)
        self.session = plat.current_session()
        if self.session is None:
            self.papers.setHtml("<p>沒有登入量子工程平台：只能手動輸入標籤。<br>"
                                "登入後可以從共用標籤挑選，並看到每個標籤在論文庫裡的論文。</p>")
        self._fill(plat.TAGS.tags)
        if self.session is not None:
            run_bg(lambda: plat.TAGS.get(force=True), self._fill, lambda m: self.papers.setHtml(f"❌ {html.escape(m)}"))
            if self.project is not None:
                run_bg(lambda: self.session.project(self.project["id"]), self._got_project, lambda m: None)
        self._update_chosen()

    def _got_project(self, d: Dict[str, Any]) -> None:
        self.project_tags = list(d.get("tags") or [])
        self._fill(plat.TAGS.tags)

    def _accept(self) -> None:
        """確定：選了但專案還沒有的標籤加到專案（背景做，失敗只寫 log）。"""
        if self.project is not None and self.session is not None and self.add_to_project.isChecked():
            new = [t for t in self.selected if t not in self.project_tags]
            if new:
                s, pid = self.session, self.project["id"]
                run_bg(lambda: s.add_project_tags(pid, new), lambda _r: plat.PROJECTS.get(force=True),
                       lambda m: log.warning("標籤加到專案失敗：%s", m))
        self.accept()

    def _fill(self, tags: List[Dict[str, Any]]) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        cats: Dict[str, QtWidgets.QTreeWidgetItem] = {}
        by_name = {t["name"]: t for t in tags}
        rows = []
        if self.project is not None:
            pname = self.project.get("name") or self.project["id"]
            cats[self.PROJECT_CAT] = QtWidgets.QTreeWidgetItem(self.tree, [f"{self.PROJECT_CAT}：{pname}"])
            cats[self.PROJECT_CAT].setFlags(Qt.ItemFlag.ItemIsEnabled)
            cats[self.PROJECT_CAT].setExpanded(True)
            if not self.project_tags:
                hint = QtWidgets.QTreeWidgetItem(cats[self.PROJECT_CAT], ["（這個專案還沒有標籤：勾選下面的標籤或建立新標籤）"])
                hint.setFlags(Qt.ItemFlag.ItemIsEnabled)
            rows += [dict(by_name.get(n, {"name": n}), category=self.PROJECT_CAT) for n in self.project_tags]
        rows += tags
        names = set(by_name) | set(self.project_tags)
        rows += [{"name": n, "category": "（這次方案）"} for n in self.selected if n not in names]
        for t in rows:
            c = t.get("category") or "（未分類）"
            if c not in cats:
                cats[c] = QtWidgets.QTreeWidgetItem(self.tree, [c])
                cats[c].setFlags(Qt.ItemFlag.ItemIsEnabled)
                cats[c].setExpanded(True)
            it = QtWidgets.QTreeWidgetItem(cats[c], [t["name"], str(t.get("papers", "")), str(t.get("data", ""))])
            it.setData(0, Qt.ItemDataRole.UserRole, t)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Checked if t["name"] in self.selected else Qt.CheckState.Unchecked)
            if t.get("color"):
                it.setForeground(0, QtGui.QBrush(QtGui.QColor(t["color"])))
        self.tree.blockSignals(False)

    def _filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            cat = self.tree.topLevelItem(i)
            any_vis = False
            for j in range(cat.childCount()):
                ch = cat.child(j)
                vis = not text or text in ch.text(0).lower()
                ch.setHidden(not vis)
                any_vis |= vis
            cat.setHidden(not any_vis)

    def _changed(self, it: QtWidgets.QTreeWidgetItem, _col: int) -> None:
        t = it.data(0, Qt.ItemDataRole.UserRole)
        if not t:
            return
        if it.checkState(0) == Qt.CheckState.Checked:
            if t["name"] not in self.selected:
                self.selected.append(t["name"])
        else:
            self.selected = [x for x in self.selected if x != t["name"]]
        # 同一個標籤可能同時出現在「專案」與原本的分類：一起打勾
        self.tree.blockSignals(True)
        state = it.checkState(0)
        for other in self.tree.findItems(t["name"], Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive, 0):
            if other is not it and other.data(0, Qt.ItemDataRole.UserRole):
                other.setCheckState(0, state)
        self.tree.blockSignals(False)
        self._update_chosen()

    def _update_chosen(self) -> None:
        self.chosen.setText("已選：" + ("、".join(self.selected) if self.selected else "（無）"))
        if self.session is not None and self.selected:
            self._load_papers(self.selected)

    def _show_papers(self, cur) -> None:
        t = cur.data(0, Qt.ItemDataRole.UserRole) if cur else None
        if t and self.session is not None:
            self._load_papers([t["name"]])

    def _load_papers(self, names: List[str]) -> None:
        s = self.session
        self.papers.setHtml("<p>載入相關論文…</p>")

        def ok(r):
            parts = []
            for t in r["tags"]:
                link = f' <a href="{html.escape(t["url"])}">在論文庫開啟 →</a>' if t.get("url") else ""
                parts.append(f"<h4>#{html.escape(t['name'])}（{len(t['papers'])}）{link}</h4>")
                for p in t["papers"][:15]:
                    au = ", ".join((p.get("authors") or [])[:3])
                    parts.append(f'<p><a href="{html.escape(p["url"])}">{html.escape(p["title"] or "")}</a><br>'
                                 f'<small>{html.escape(au)}{" · " + str(p["year"]) if p.get("year") else ""}</small></p>')
                if not t["papers"]:
                    parts.append("<p><small>論文庫裡還沒有這個標籤的論文</small></p>")
            self.papers.setHtml("".join(parts))
        run_bg(lambda: s.papers(names, 30), ok, lambda m: self.papers.setHtml(f"❌ {html.escape(m)}"))

    def _create(self) -> None:
        name = self.new.text().strip()
        if not name:
            return
        if name not in self.selected:
            self.selected.append(name)
        if self.session is None:
            self._fill(plat.TAGS.tags)
            self._update_chosen()
            return
        cat = self.new_cat.text().strip()
        if self.project is not None and self.add_to_project.isChecked() and name not in self.project_tags:
            self.project_tags.append(name)
            s, pid = self.session, self.project["id"]
            run_bg(lambda: s.add_project_tags(pid, [name]), lambda _r: None,
                   lambda m: log.warning("標籤加到專案失敗：%s", m))

        def ok(_r):
            self.new.clear()
            self._fill(plat.TAGS.get(force=True))
            self._update_chosen()
        run_bg(lambda: self.session.create_tag(name, cat), ok,
               lambda m: QtWidgets.QMessageBox.warning(self, "建立標籤", m))


# ---- 剛存好的數據（可以拖到讀檔模塊）-------------------------------------------------------
class RecentList(QtWidgets.QListWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setDragEnabled(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)

    def mimeData(self, items):  # noqa: N802
        from qelcomm import qt as qelqt

        s = plat.current_session()
        out = []
        for it in items:
            d = dict(it.data(Qt.ItemDataRole.UserRole))
            if s is not None and d.get("catalog_id"):
                d["gateway"] = s.gateway
            out.append(d)
        return qelqt.mime_for(out)


class RecentData(QtWidgets.QWidget):
    """這次開啟後存好的數據檔：拖到讀檔模塊（LabLogViewer）就能開；也可以叫讀檔模塊開啟。"""

    open_requested = pyqtSignal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        hint = QtWidgets.QLabel("剛存好的數據：拖到讀檔模塊就能開（☁ = 已在平台數據目錄）")
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.list = RecentList()
        self.list.itemDoubleClicked.connect(lambda it: self.open_requested.emit(dict(it.data(Qt.ItemDataRole.UserRole))))
        v.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton("用讀檔程式開啟")
        b.clicked.connect(self._open)
        f = QtWidgets.QPushButton("打開資料夾")
        f.clicked.connect(self._folder)
        row.addWidget(b)
        row.addWidget(f)
        row.addStretch(1)
        v.addLayout(row)

    def add(self, path: str, catalog_id: str = "") -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            d = it.data(Qt.ItemDataRole.UserRole)
            if d["path"] == path:
                if catalog_id:
                    d["catalog_id"] = catalog_id
                    it.setData(Qt.ItemDataRole.UserRole, d)
                    it.setText("☁ " + Path(path).name)
                return
        if catalog_id:
            return
        it = QtWidgets.QListWidgetItem(Path(path).name)
        it.setToolTip(path)
        it.setData(Qt.ItemDataRole.UserRole, {"path": path, "name": Path(path).name})
        self.list.insertItem(0, it)
        while self.list.count() > 30:
            self.list.takeItem(self.list.count() - 1)

    def _current(self) -> Optional[Dict[str, Any]]:
        it = self.list.currentItem() or (self.list.item(0) if self.list.count() else None)
        return dict(it.data(Qt.ItemDataRole.UserRole)) if it else None

    def _open(self) -> None:
        d = self._current()
        if d:
            self.open_requested.emit(d)

    def _folder(self) -> None:
        d = self._current()
        if d:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(d["path"]).parent)))


# ---- 平台控制（主視窗用）------------------------------------------------------------------
class PlatformController(QtCore.QObject):
    """主視窗的平台功能：選單、上傳、拖放套用設定、接收「套用設定」指令。"""

    message = pyqtSignal(str)
    _remote = pyqtSignal(object)
    _exported = pyqtSignal(str, str)

    def __init__(self, window) -> None:
        super().__init__(window)
        self.w = window
        self.uploader = plat.Uploader(window.station.bus) if plat.available() else None
        self.recent = RecentData()
        self.recent.open_requested.connect(self.open_in_viewer)
        self._exported.connect(self.recent.add)
        bus = window.station.bus
        self._toks = [bus.subscribe("data.exported", lambda _t, p: self._exported.emit(str(p.get("path")), "")),
                      bus.subscribe("platform.uploaded",
                                    lambda _t, p: self._exported.emit(str(p.get("path")), str(p.get("catalog_id") or "")))]
        self.listener = None
        self._remote.connect(self.handle_message)
        self.label = QtWidgets.QLabel("")
        self.label.setProperty("role", "muted")
        self.refresh_label()
        if setting("platform.listen", True):
            self.start_listener()
        QtCore.QTimer.singleShot(300, self.handle_startup_open)

    # ---- 選單 --------------------------------------------------------------
    def menu(self, parent) -> QtWidgets.QMenu:
        m = QtWidgets.QMenu("量子工程平台", parent)
        m.aboutToShow.connect(lambda: self._fill_menu(m))
        return m

    def _fill_menu(self, m: QtWidgets.QMenu) -> None:
        m.clear()
        s = plat.current_session()
        if not plat.available():
            m.addAction("（沒有安裝 qelcomm 通信模塊）").setEnabled(False)
            return
        if s is None:
            m.addAction("登入（論文庫帳號）…", self.login)
        else:
            m.addAction(f"已登入：{s.user.get('display_name') or s.username or '（平台）'}").setEnabled(False)
            m.addAction("開啟平台網頁", lambda: self._open(s.gateway + "/portal"))
            m.addAction("數據目錄（網頁）", lambda: self._open(s.gateway + "/portal#/data"))
            m.addAction("標籤與論文…", self.pick_tags)
            proj = (self.w.doc.scheme.output or {}).get("lab_project") or {}
            if proj.get("id"):
                name = proj.get("name") or proj["id"]
                m.addAction(f"用讀檔程式開啟專案「{name}」的所有數據", lambda: self.open_project_in_viewer(proj))
                p = plat.PROJECTS.find(proj["id"])
                if p and p.get("url"):
                    m.addAction(f"在論文庫開啟專案「{name}」", lambda u=p["url"]: self._open(u))
            m.addAction("登出", self.logout)
        m.addSeparator()
        a = m.addAction("本機量測存檔後上傳到數據目錄")
        a.setCheckable(True)
        a.setChecked(bool(setting("platform.upload_local_runs", True)))
        a.toggled.connect(lambda v: settings().set("platform.upload_local_runs", bool(v)))
        m.addAction("套用數據檔的設定…", self.choose_file)

    @staticmethod
    def _open(url: str) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def refresh_label(self) -> None:
        s = plat.current_session(refresh=True)
        self.label.setText(f"平台：{s.user.get('display_name') or s.username}" if s and s.user else
                           ("平台：已登入" if s else "平台：未登入"))

    def login(self) -> None:
        d = PlatformLoginDialog(self.w)
        if d.exec() and d.session is not None:
            self.refresh_label()
            self.start_listener()
            self.w.files.reload_projects(force=True)
            self.message.emit(f"✔ 已登入量子工程平台（{d.session.username}）")

    def logout(self) -> None:
        s = plat.current_session()
        if s is not None:
            run_bg(s.logout)
        plat.set_session(None)
        self.stop_listener()
        self.refresh_label()
        plat.PROJECTS.items, plat.PROJECTS.at = [], 0.0
        self.w.files.reload_projects()

    def pick_tags(self) -> None:
        cur = list((self.w.doc.scheme.output or {}).get("tags") or [])
        d = TagPickerDialog(cur, self.w, project=(self.w.doc.scheme.output or {}).get("lab_project"))
        if d.exec():
            self.w.files.set_tags(d.selected)

    # ---- 套用數據檔的設定 ------------------------------------------------------
    def choose_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self.w, "選數據檔（套用它的量測設定）", "",
                                                        "量測數據 (*.nc *.hdf5 *.h5)")
        if path:
            self.apply_file(Path(path))

    @staticmethod
    def accepts(md: QtCore.QMimeData) -> bool:
        try:
            from qelcomm import qt as qelqt
        except ImportError:
            return bool(md.hasUrls())
        if not qelqt.accepts(md):
            return False
        if md.hasUrls():
            for u in md.urls():
                s = u.toString()
                if u.isLocalFile() and not u.toLocalFile().lower().endswith(DATA_EXT):
                    return False
                if not u.isLocalFile() and "/api/" not in s:
                    return False
        return True

    def drop(self, md: QtCore.QMimeData) -> None:
        from qelcomm import qt as qelqt

        self.message.emit("讀取拖進來的數據檔…")
        run_bg(lambda: qelqt.files_from(md, plat.current_session()),
               lambda files: self.apply_file(files[0]) if files else self.message.emit("❌ 沒有可用的數據檔"),
               lambda m: QtWidgets.QMessageBox.warning(self.w, "套用設定", m))

    def apply_file(self, path: Path) -> None:
        from ...integrations.file_settings import apply_to_scheme, read_file_settings, scheme_from_file

        try:
            fs = read_file_settings(path)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self.w, "套用設定", f"讀不出 {Path(path).name} 的設定：{e}")
            return
        if fs.scheme:
            sch = scheme_from_file(fs)
            q = QtWidgets.QMessageBox.question(
                self.w, "套用設定", f"{Path(path).name}\n\n這個檔裡有完整的{fs.summary()}。\n開成目前的方案？")
            if q == QtWidgets.QMessageBox.StandardButton.Yes:
                self.w._load_scheme(sch, None)
                self.message.emit(f"✔ 已套用 {Path(path).name} 的量測方案")
            return
        if fs.source != "labber":
            QtWidgets.QMessageBox.information(self.w, "套用設定", f"{Path(path).name} 不是 Lab Control 或 Labber 量測檔")
            return
        preview = self.w.doc.scheme.copy()
        changes = apply_to_scheme(preview, fs)
        tags_note = f"\n\n檔案標籤：{', '.join(fs.tags)}（一併加到這次的標籤）" if fs.tags else ""
        if not changes and not fs.tags:
            QtWidgets.QMessageBox.information(self.w, "套用設定", f"{fs.summary()}\n\n目前方案裡沒有對得上的方塊"
                                              "（VNA 量測方塊、軸名稱相同的掃描）。")
            return
        q = QtWidgets.QMessageBox.question(self.w, "套用設定", f"{fs.summary()}\n\n會修改：\n・" +
                                           ("\n・".join(changes) or "（無）") + tags_note)
        if q != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        def fn(s):
            apply_to_scheme(s, fs)
            if fs.tags:
                cur = list((s.output or {}).get("tags") or [])
                s.output = dict(s.output or {}, tags=list(dict.fromkeys(cur + fs.tags)))
        self.w.doc.mutate(fn)
        self.message.emit(f"✔ 已套用 {Path(path).name} 的設定（{len(changes)} 項；可以復原）")

    # ---- 其他模塊 / 網頁送來的指令 -----------------------------------------------------
    def start_listener(self) -> None:
        self.stop_listener()
        s = plat.current_session()
        if s is None:
            return
        from qelcomm import Listener

        self.listener = Listener(s, "labcontrol", lambda m: self._remote.emit(m), lambda e: log.info("平台訊息：%s", e))
        self.listener.start()

    def stop_listener(self) -> None:
        if self.listener is not None:
            self.listener.stop()
            self.listener = None

    def handle_message(self, m: Dict[str, Any]) -> None:
        """平台網頁送來的指令（0.0.26 起由量測程式處理，取代啟動器）：
        套用設定、用讀檔程式開數據（open_in）、用讀檔程式開整個專案（open_project）。"""
        import socket

        if m.get("host") and str(m["host"]).lower() != socket.gethostname().lower():
            return                                            # 指定給別台電腦
        if m.get("type") == "open_in" and m.get("module", "lablogviewer") == "lablogviewer":
            self.open_in_viewer(m)
            return
        if m.get("type") == "open_project" and m.get("project"):
            self.open_project_in_viewer({"id": m["project"], "name": m.get("name", "")})
            return
        if m.get("type") in ("apply_settings", "open", "open_in") and m.get("catalog_id"):
            self.message.emit(f"收到「套用設定」：{m.get('name', '')}（來自 {m.get('from', {}).get('module') or '平台'}）")
            run_bg(lambda: plat.fetch_catalog_file(m["catalog_id"]), self.apply_file,
                   lambda e: QtWidgets.QMessageBox.warning(self.w, "套用設定", e))
        elif m.get("type") == "apply_settings" and m.get("path"):
            self.apply_file(Path(m["path"]))

    def handle_startup_open(self) -> None:
        raw = os.environ.pop("QEL_OPEN", "")
        if not raw:
            return
        try:
            self.handle_message(json.loads(raw))
        except ValueError:
            pass

    def open_in_viewer(self, d: Dict[str, Any]) -> None:
        """用讀檔程式（LabLogViewer）開這個檔：本機有就直接開；只有平台數據目錄的編號就先下載到快取。"""
        from ...integrations.reader import ReaderMissing, open_path

        def opened(path) -> None:
            try:
                open_path(path)
                self.message.emit(f"已用讀檔程式開啟 {Path(path).name}")
            except ReaderMissing as e:
                QtWidgets.QMessageBox.information(self.w, "讀檔程式", str(e))
            except OSError as e:
                self.message.emit(f"❌ 開啟讀檔程式失敗：{e}")

        path = d.get("path")
        if path and Path(path).exists():
            opened(path)
            return
        if d.get("catalog_id") and plat.current_session() is not None:
            self.message.emit(f"下載 {d.get('name') or d['catalog_id']}…")
            run_bg(lambda: plat.fetch_catalog_file(d["catalog_id"]), opened, lambda m: self.message.emit(f"❌ {m}"))
            return
        self.message.emit("找不到這個檔案（沒有登入平台時只能開本機的檔）")

    def open_project_in_viewer(self, proj: Dict[str, Any]) -> None:
        """用讀檔程式開整個專案：NAS 的專案資料夾看得到就直接開，看不到就同步到本機（只下載新的）再開。"""
        s = plat.current_session()
        if s is None:
            self.message.emit("請先登入量子工程平台（平台 ▾ → 登入）")
            return
        label = proj.get("name") or proj["id"]
        self.message.emit(f"準備專案「{label}」的數據…")

        def work():
            shares = [x for x in str(s.me().get("projects_share") or "").split(",") if x.strip()]
            folder = s.project_folder(proj["id"], shares)
            if folder is not None:
                return folder
            dest = Path(str(setting("platform.projects_dir", "") or (Path.home() / "QELData" / "projects"))).expanduser()
            return s.sync_project(proj["id"], dest)

        def ok(folder) -> None:
            from ...integrations.reader import ReaderMissing, open_path

            try:
                open_path(folder)
                self.message.emit(f"📂 已用讀檔程式開啟專案「{label}」：{folder}")
            except ReaderMissing as e:
                QtWidgets.QMessageBox.information(self.w, "讀檔程式", f"{e}\n\n專案數據在：{folder}")
        run_bg(work, ok, lambda m: self.message.emit(f"❌ 開啟專案失敗：{m}"))

    def close(self) -> None:
        for t in getattr(self, "_toks", []):
            try:
                self.w.station.bus.unsubscribe(t)
            except Exception:  # noqa: BLE001
                pass
        self.stop_listener()
        if self.uploader is not None:
            self.uploader.close()
