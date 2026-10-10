"""發佈中心：把各模塊的新版 zip 丟進來，自動判斷是哪個模塊、哪一版，一鍵全部更新。

| zip | 做什麼 |
|---|---|
| Hub（LabControlHub_v*.zip） | 控制代理更新 NAS 上的 Hub 網站（停止 → 備份 → 換程式 → 重建 → 啟動 → 確認） |
| 論文庫（paperlib 的資料夾 zip） | 控制代理更新論文庫網站（備份 → 換程式 → 建置映像 → 重建 → 啟動 → 確認；data/ 不動） |
| 量測模塊、讀檔模塊、Monitor、啟動器 | 發佈到平台：各電腦的啟動器自動更新；量測節點也從 Hub 自動更新 |

順序：論文庫 → 各模塊 → Hub（Hub 會重新啟動，所以放最後）。模塊發佈需要站長登入（「平台（站長）」分頁）。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from labhub.packages import PackageError, identify

from .bg import run_bg
from .client import AgentClient

ORDER = {"paperlib": 0, "hub": 9}             # 其他（模塊）= 5
COLS = ["檔案", "判斷為", "新版本", "目前版本", "狀態"]
EXTS = (".zip", ".exe", ".msi", ".dmg", ".pkg")      # zip ＋ 啟動器的 Windows / macOS 安裝檔


class DropZone(QtWidgets.QFrame):
    files = QtCore.pyqtSignal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setProperty("role", "card")
        self.setMinimumHeight(90)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(16, 12, 16, 12)
        lab = QtWidgets.QLabel("<b>把新版 zip 拖到這裡（可以一次多個）</b><br>"
                               "Hub、論文庫、量測模塊、讀檔模塊、Monitor、啟動器都可以，程式會自己判斷。不用解壓縮。<br>"
                               "啟動器的 Windows .exe、macOS .dmg 也丟這裡：平台網頁會出現對應的下載按鈕。")
        lab.setWordWrap(True)
        h.addWidget(lab, 1)
        b = QtWidgets.QPushButton("選擇檔案…")
        b.clicked.connect(self._browse)
        h.addWidget(b)

    def _browse(self) -> None:
        ps, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "選擇新版 zip 或安裝檔", str(Path.home() / "Downloads"),
                                                       "發佈檔 (*.zip *.exe *.msi *.dmg *.pkg)")
        if ps:
            self.files.emit(ps)

    def dragEnterEvent(self, ev) -> None:  # noqa: N802
        if ev.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(EXTS) for u in ev.mimeData().urls()):
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:  # noqa: N802
        self.files.emit([u.toLocalFile() for u in ev.mimeData().urls() if u.toLocalFile().lower().endswith(EXTS)])


class PublishTab(QtWidgets.QWidget):
    def __init__(self, agent_url: Callable[[], str], token: Callable[[], str], platform, parent=None) -> None:
        super().__init__(parent)
        self.agent_url, self.token, self.platform = agent_url, token, platform
        self.items: List[Dict[str, Any]] = []
        self.running = False
        self.current: Dict[str, Optional[str]] = {}
        self.published: Dict[str, set] = {}          # 模塊 → {"版本/檔名"}（同一版補上 .exe / .dmg 用）
        v = QtWidgets.QVBoxLayout(self)
        head = QtWidgets.QHBoxLayout()
        t = QtWidgets.QLabel("發佈中心")
        t.setProperty("role", "h1")
        head.addWidget(t)
        head.addStretch(1)
        self.who = QtWidgets.QLabel("")
        self.who.setProperty("role", "muted")
        head.addWidget(self.who)
        v.addLayout(head)
        self.drop = DropZone()
        self.drop.files.connect(self.add_files)
        v.addWidget(self.drop)
        self.table = QtWidgets.QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for i, w in ((1, 190), (2, 90), (3, 90)):
            self.table.setColumnWidth(i, w)
        self.table.currentCellChanged.connect(lambda r, *_: self._show_notes(r))
        v.addWidget(self.table, 2)
        nl = QtWidgets.QLabel("發佈說明（從 zip 裡的版本說明自動帶入，可以修改；啟動器與平台網頁會顯示）")
        nl.setProperty("role", "muted")
        v.addWidget(nl)
        self.notes = QtWidgets.QPlainTextEdit()
        self.notes.setMaximumHeight(90)
        self.notes.textChanged.connect(self._save_notes)
        v.addWidget(self.notes)
        row = QtWidgets.QHBoxLayout()
        self.b_remove = QtWidgets.QPushButton("移除選取")
        self.b_remove.clicked.connect(self.remove_selected)
        self.b_clear = QtWidgets.QPushButton("清除已完成")
        self.b_clear.clicked.connect(self.clear_done)
        self.b_go = QtWidgets.QPushButton("▶ 全部發佈")
        self.b_go.setProperty("primary", True)
        self.b_go.clicked.connect(self.publish_all)
        row.addWidget(self.b_remove)
        row.addWidget(self.b_clear)
        row.addStretch(1)
        row.addWidget(self.b_go)
        v.addLayout(row)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        v.addWidget(self.log)
        self._sync_buttons()
        QtCore.QTimer.singleShot(500, self.refresh_current)

    # ---- 加入 --------------------------------------------------------------
    def add_files(self, paths: List[str]) -> None:
        for p in paths:
            path = Path(p)
            try:
                info = identify(path, path.name)
            except (PackageError, OSError) as e:
                self.say(f"❌ {path.name}：{e}")
                continue
            key = (info["target"], info.get("platform", ""), _arch(path.name) if info.get("platform") else "")
            self.items = [x for x in self.items if not (_key(x) == key and x["status"] == "待發佈")]
            info.update(path=str(path), status="待發佈", done=False)
            self.items.append(info)
            self.say(f"＋ {path.name} → {info['name']} v{info['version']}")
        self.items.sort(key=_order)
        self.render()
        self.refresh_current()

    def remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            if 0 <= r < len(self.items) and self.items[r]["status"] != "發佈中…":
                del self.items[r]
        self.render()

    def clear_done(self) -> None:
        self.items = [x for x in self.items if not x["done"]]
        self.render()

    # ---- 顯示 --------------------------------------------------------------
    def render(self) -> None:
        self.table.setRowCount(len(self.items))
        for r, x in enumerate(self.items):
            cur = self.current.get(x["target"])
            plat = bool(x.get("platform"))
            vals = [Path(x["path"]).name, x["name"], x["version"], cur or "—", x["status"]]
            for c, val in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(str(val))
                if c == 4:
                    color = {"✔": "#51cf66", "❌": "#ff6b6b"}.get(str(val)[:1])
                    if color:
                        it.setForeground(QtGui.QColor(color))
                if c == 2 and cur and not plat and _older(x["version"], cur):
                    it.setForeground(QtGui.QColor("#ffa94d"))
                    it.setToolTip("比目前的版本舊")
                self.table.setItem(r, c, it)
        self._sync_buttons()

    def _show_notes(self, row: int) -> None:
        self.notes.blockSignals(True)
        self.notes.setPlainText(self.items[row].get("notes", "") if 0 <= row < len(self.items) else "")
        self.notes.blockSignals(False)

    def _save_notes(self) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self.items):
            self.items[r]["notes"] = self.notes.toPlainText()

    def _sync_buttons(self) -> None:
        pending = [x for x in self.items if not x["done"]]
        self.b_go.setEnabled(bool(pending) and not self.running)
        self.b_go.setText(f"▶ 全部發佈（{len(pending)}）" if pending else "▶ 全部發佈")
        s = getattr(self.platform, "session", None)
        self.who.setText(f"站長：{s.username}" if s else "模塊發佈需要站長登入（「平台（站長）」分頁）")

    def say(self, msg: str) -> None:
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {msg}")

    def agent(self) -> Optional[AgentClient]:
        u = self.agent_url()
        return AgentClient(u, self.token(), timeout=15) if u else None

    def refresh_current(self) -> None:
        """目前線上的版本：Hub / 論文庫問控制代理，模塊問平台。"""
        ag, s = self.agent(), getattr(self.platform, "session", None)

        def work():
            out: Dict[str, Optional[str]] = {}
            if ag is not None:
                try:
                    st = ag.status()
                    out["hub"] = st.get("hub_version")
                    out["paperlib"] = (st.get("paperlib") or {}).get("version")
                except Exception:  # noqa: BLE001
                    pass
            if s is not None:
                try:
                    for m in s.modules():
                        out[m["id"]] = m.get("latest")
                        self.published[m["id"]] = {f"{v['version']}/{f['name']}" for v in m.get("versions") or []
                                                   for f in v.get("files") or []}
                except Exception:  # noqa: BLE001
                    pass
            return out

        def ok(cur):
            self.current.update({k: v for k, v in cur.items() if v})
            self.render()
        run_bg(work, ok, lambda _m: None)

    # ---- 發佈 --------------------------------------------------------------
    def publish_all(self) -> None:
        pending = [x for x in self.items if not x["done"]]
        if not pending or self.running:
            return
        for x in pending:                          # 線上已經是這一版：不用再發佈
            if x.get("platform"):                  # 安裝檔：那一版已經有同名檔才略過
                if f"{x['version']}/{Path(x['path']).name}" in self.published.get(x["target"], set()):
                    x["status"], x["done"] = "✔ 線上已有這個檔（略過）", True
            elif self.current.get(x["target"]) == x["version"]:
                x["status"], x["done"] = "✔ 線上已是這一版（略過）", True
        pending = [x for x in pending if not x["done"]]
        self.render()
        if not pending:
            return
        needs_owner = any(x["kind"] == "module" for x in pending)
        if needs_owner and getattr(self.platform, "session", None) is None:
            self.platform.login_or_out()
            if getattr(self.platform, "session", None) is None:
                self.say("❌ 模塊發佈需要站長登入")
                return
        olds = [x for x in pending if not x.get("platform") and self.current.get(x["target"])
                and _older(x["version"], self.current[x["target"]])]
        if olds and QtWidgets.QMessageBox.question(
                self, "發佈中心", "這些比目前的版本舊，確定要發佈（退回舊版）？\n" +
                "\n".join(f"・{x['name']} v{x['version']}（目前 v{self.current[x['target']]}）" for x in olds)) \
                != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        pending.sort(key=_order)
        self.running = True
        self._sync_buttons()
        self._next(pending)

    def _next(self, queue: List[Dict[str, Any]]) -> None:
        if not queue:
            self.running = False
            self.say("全部完成")
            self._sync_buttons()
            QtCore.QTimer.singleShot(2000, self.refresh_current)
            return
        x = queue.pop(0)
        self._active = x
        x["status"] = "發佈中…"
        self.render()

        def done(ok: bool, msg: str) -> None:
            x["status"] = ("✔ " if ok else "❌ ") + msg
            x["done"] = ok
            self.say(f"{'✔' if ok else '❌'} {x['name']} v{x['version']}：{msg}")
            if ok and not x.get("platform"):
                self.current[x["target"]] = x["version"]
            if ok:
                self.published.setdefault(x["target"], set()).add(f"{x['version']}/{Path(x['path']).name}")
            self.render()
            self._next(queue)

        if x["kind"] == "web":
            self._web(x, done)
        else:
            self._module(x, done)

    def _module(self, x: Dict[str, Any], done: Callable[[bool, str], None]) -> None:
        s = self.platform.session
        msg = ("已發佈；平台網頁的「下載」按鈕會給這個檔" if x.get("platform") else
               "已發佈；各電腦的啟動器會自動更新")
        run_bg(lambda: s.publish_module(x["target"], x["version"], x["path"], x.get("notes", "")),
               lambda _r: done(True, msg), lambda m: done(False, m))

    def _web(self, x: Dict[str, Any], done: Callable[[bool, str], None]) -> None:
        ag = self.agent()
        if ag is None:
            done(False, "沒有控制代理網址（「⚙ 連線設定」）")
            return
        action = "update" if x["target"] == "hub" else "paperlib_update"
        data = Path(x["path"]).read_bytes()

        def run():
            j = ag.submit(action, data=data)
            after = 0
            while True:
                v = ag.job(j["id"], after, wait=10)
                after = v.get("n_lines", after)
                cur = next((st for st in v.get("steps", []) if st["status"] == "running"), None)
                if cur is not None:
                    QtCore.QMetaObject.invokeMethod(self, "_set_status", Qt.ConnectionType.QueuedConnection,
                                                    QtCore.Q_ARG(str, x["target"]),
                                                    QtCore.Q_ARG(str, f"{cur['title']}… {cur.get('msg', '')}"))
                if v.get("done"):
                    return v

        def ok(v):
            done(bool(v.get("ok")), v.get("result", ""))
        run_bg(run, ok, lambda m: done(False, m))

    @QtCore.pyqtSlot(str, str)
    def _set_status(self, target: str, text: str) -> None:
        x = getattr(self, "_active", None)
        if x is not None and x["target"] == target and not x["done"] and not x["status"].startswith(("✔", "❌")):
            x["status"] = text
            self.render()

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        self._sync_buttons()
        self.refresh_current()


def _arch(name: str) -> str:
    from labhub.platform import file_arch

    return file_arch(name)


def _key(x: Dict[str, Any]) -> tuple:
    return (x["target"], x.get("platform", ""), _arch(Path(x["path"]).name) if x.get("platform") else "")


def _order(x: Dict[str, Any]) -> tuple:
    """論文庫 → 模塊 → 安裝檔（同一版的 zip 先發佈）→ Hub。"""
    return (x["done"], ORDER.get(x["target"], 5), 1 if x.get("platform") else 0)


def _older(a: str, b: str) -> bool:
    from labhub.server import parse_version

    return parse_version(a) < parse_version(b)
