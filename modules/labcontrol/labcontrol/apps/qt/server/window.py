"""儀器伺服器（= Labber Instrument Server）。

  * 以「群組」顯示：每台量測節點（電腦）下轄的儀器 ＋「未歸屬」（共用但還沒歸屬、或只被偵測到的儀器）。
  * 拖曳儀器到別的群組 = 移動（共用網路儀器）：那台沒有設定會自動複製過去，其他電腦上連著的自動中斷。
  * 每個群組都有「全部連線 / 全部斷線」；別台節點的儀器也能直接連線、中斷、開控制視窗（經 Hub）。
  * 新增（掃描 VISA、*IDN? 辨識）、移除、啟用 / 停用 → 寫回這台的 instruments.yaml（保留註解、先備份）。
  * 與主視窗共用同一個 Station：同一台儀器只開一次 VISA，量測中的儀器自動變唯讀。
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal

from .... import APP_NAME, __version__
from ....core import labfile
from ....core.station import Station
from ....paths import lab_path, open_folder
from ....remote.groups import UNASSIGNED, build_groups, move_instrument
from ....remote.hub import hostname
from ....settings import setting
from .. import theme
from ..bridge import QtEventBridge
from ..worker import run_bg
from .config_window import InstrumentConfigWindow, status_of

ROLE = Qt.ItemDataRole.UserRole          # 本機儀器名稱（遠端 / 未歸屬為 None）
ITEM = Qt.ItemDataRole.UserRole + 1      # 儀器 dict
GROUP = Qt.ItemDataRole.UserRole + 2     # 群組 dict（群組列）
COLS = ["名稱", "狀態", "驅動", "位址", "說明", "*IDN? / 訊息"]


class _HubShim:
    """讓 labmonitor 的遠端儀器視窗用 Lab Control 的 Hub 連線。"""

    def __init__(self, hub) -> None:
        self.hub = hub

    def command(self, node: str, cmd: str, args: Optional[Dict[str, Any]] = None, wait: float = 30.0) -> Any:
        from ....remote.hub import HubError

        r = self.hub.call(node, cmd, args or {}, wait)
        if not r.get("ok"):
            raise HubError(r.get("error") or f"{cmd} 失敗")
        return r.get("result")


class GroupTree(QtWidgets.QTreeWidget):
    """群組樹：儀器可以拖到別的群組（放開時發出 moved，不直接搬動項目）。"""

    moved = pyqtSignal(object, object)       # 儀器 dict、目標群組 dict

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderLabels(COLS)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(False)
        self.setIndentation(16)

    @staticmethod
    def group_of(it: Optional[QtWidgets.QTreeWidgetItem]) -> Optional[QtWidgets.QTreeWidgetItem]:
        while it is not None and it.data(0, GROUP) is None:
            it = it.parent()
        return it

    def _target(self, ev) -> Optional[QtWidgets.QTreeWidgetItem]:
        src = self.currentItem()
        if src is None or src.data(0, ITEM) is None:
            return None
        g = self.group_of(self.itemAt(ev.position().toPoint()))
        if g is None or g is self.group_of(src):
            return None
        return g

    def dragMoveEvent(self, ev) -> None:
        if self._target(ev) is not None:
            ev.setDropAction(Qt.DropAction.MoveAction)
            ev.accept()
        else:
            ev.ignore()

    def dropEvent(self, ev) -> None:
        g = self._target(ev)
        src = self.currentItem()
        ev.setDropAction(Qt.DropAction.IgnoreAction)
        ev.ignore()                                # 不讓 Qt 自己搬項目：由 moved 處理後重新整理
        if g is not None and src is not None:
            self.moved.emit(src.data(0, ITEM), g.data(0, GROUP))


class InstrumentServerWindow(QtWidgets.QMainWindow):
    def __init__(self, station: Station, parent=None) -> None:
        super().__init__(parent)
        self.station = station
        self.remote = None                        # 主視窗設定（RemoteManager）；沒有 Hub 時只顯示這台
        self.registry: List[Dict[str, Any]] = []
        self._fetching = False
        self.cfg_windows: Dict[str, InstrumentConfigWindow] = {}
        self.remote_windows: Dict[str, QtWidgets.QWidget] = {}
        self.errors: Dict[str, str] = {}
        self._collapsed: set = set()
        self.setWindowTitle(f"儀器伺服器 — {APP_NAME} {__version__}")
        self.resize(1180, 680)

        tb = self.addToolBar("server")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        tb.addAction("＋ 新增儀器…", self.add_instrument)
        self.a_remove = tb.addAction("移除", self.remove_instrument)
        self.a_enable = tb.addAction("啟用 / 停用", self.toggle_enabled)
        tb.addSeparator()
        self.a_cfg = tb.addAction("控制視窗", self.open_config)
        self.a_conn = tb.addAction("▶ 連線", self.connect_selected)
        self.a_disc = tb.addAction("■ 中斷", self.disconnect_selected)
        self.a_move = tb.addAction("移到群組…", self.move_selected)
        self.a_move.setToolTip("共用的網路儀器：移到某台量測節點的群組（也可以直接拖曳到群組）")
        tb.addSeparator()
        self.a_test = tb.addAction("驅動測試…", self.test_selected)
        tb.addAction("全部測試…", self.test_all)
        tb.addSeparator()
        tb.addAction("掃描 VISA…", self.scan)
        tb.addAction("↻ 重新整理", self.reload)
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        mode = QtWidgets.QLabel("  模擬模式  " if station.simulate else "  實機  ")
        mode.setStyleSheet("background:%s; color:white; border-radius:4px; padding:2px 6px; font-weight:600;"
                           % ("#f59e0b" if station.simulate else "#22c55e"))
        tb.addWidget(mode)
        tb.addAction("開啟 instruments.yaml", lambda: open_folder(self.config_path))
        self.act_theme = theme.make_toggle_action(self)
        tb.addAction(self.act_theme)

        body = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(12, 10, 12, 8)
        lay.setSpacing(8)
        head = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("儀器伺服器")
        title.setProperty("role", "h1")
        head.addWidget(title)
        self.sub = QtWidgets.QLabel("")
        self.sub.setProperty("role", "muted")
        head.addWidget(self.sub, 1)
        lay.addLayout(head)

        split = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        self.tree = GroupTree()
        for i, w in enumerate((300, 110, 150, 280, 150)):
            self.tree.setColumnWidth(i, w)
        self.tree.itemDoubleClicked.connect(self._double)
        self.tree.currentItemChanged.connect(lambda *_: self._update_actions())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.moved.connect(self.move_to)
        self.tree.itemCollapsed.connect(lambda it: self._collapsed.add(self._gid(it)))
        self.tree.itemExpanded.connect(lambda it: self._collapsed.discard(self._gid(it)))
        split.addWidget(self.tree)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        split.addWidget(self.log)
        split.setSizes([500, 150])
        lay.addWidget(split, 1)
        self.setCentralWidget(body)
        hint = QtWidgets.QLabel("雙擊儀器開啟控制視窗。拖曳儀器到別的群組 = 移動（共用網路儀器）。"
                                "連線只讀取狀態，不會改變輸出；量測中的儀器只能讀取。")
        self.statusBar().addWidget(hint)

        self.bridge = QtEventBridge(station.bus, parent=self)
        self.bridge.on("instrument.connected", lambda p: self._event(p["name"], f"已連線：{p.get('idn', '')}"))
        self.bridge.on("instrument.closed", lambda p: self._event(p["name"], "已中斷連線"))
        self.bridge.on("instrument.error", lambda p: self._event(p["name"], f"錯誤：{p.get('error')}", error=True))
        self.bridge.on("station.lease", lambda p: self.refresh())
        self.bridge.on("station.changed", lambda p: self.refresh())
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.fetch_registry)
        self.timer.start(int(float(setting("remote.heartbeat_s", 2)) * 1500))
        self.refresh()
        theme.on_change(self, lambda w: w.refresh(), call_now=False)

    # ---- 資料 ------------------------------------------------------------
    @property
    def config_path(self) -> Path:
        return self.station.config_path or lab_path(setting("app.instruments_file", "instruments.yaml"))

    @property
    def hub(self):
        return getattr(self.remote, "hub", None) if self.remote is not None else None

    def entries(self) -> List[Dict]:
        """instruments.yaml 的全部儀器（含停用）＋ 只存在於 Station 的儀器。"""
        out = []
        seen = set()
        try:
            conf = labfile.configured(self.config_path)
        except Exception as e:  # noqa: BLE001
            conf = []
            self._log(f"❌ 讀取 {self.config_path} 失敗：{e}")
        for name, opts in conf:
            out.append({"name": name, "opts": opts, "in_file": True})
            seen.add(name)
        for name, inst in self.station.instruments.items():
            if name not in seen:
                out.append({"name": name, "opts": {"driver": getattr(inst, "config_driver", inst.driver_name),
                                                   **inst.options}, "in_file": False})
        for e in out:
            inst = self.station.instruments.get(e["name"])
            e["connected"] = bool(inst is not None and inst.connected)
            e["lease"] = self.station.lease_holder(e["name"]) if inst is not None else None
            e["idn"] = getattr(inst, "_idn", "") if inst is not None else ""
        return out

    def groups(self) -> List[Dict[str, Any]]:
        rm = self.remote
        nodes = list(getattr(rm, "nodes", []) or []) if rm is not None else []
        owners = dict(getattr(rm, "owners", {}) or {}) if rm is not None else {}
        shared = set(getattr(rm, "shared", set()) or ()) if rm is not None else set()
        return build_groups(self.entries(), self.registry if self.hub is not None else [], nodes, hostname(),
                            owners, shared)

    def reload(self) -> None:
        self.fetch_registry(force=True)
        self.refresh()

    def fetch_registry(self, force: bool = False) -> None:
        hub = self.hub
        if hub is None or self._fetching or (not force and not self.isVisible()):
            return
        self._fetching = True

        def ok(reg):
            self._fetching = False
            if reg != self.registry:
                self.registry = reg
                self.refresh()

        def fail(m):
            self._fetching = False
        run_bg(hub.instruments_registry, ok, fail)

    @staticmethod
    def _gid(it) -> str:
        g = it.data(0, GROUP) if it is not None else None
        return g["id"] if g else ""

    def refresh(self) -> None:
        cur = self.tree.currentItem()
        cur_key = None
        if cur is not None and cur.data(0, ITEM):
            d = cur.data(0, ITEM)
            cur_key = (d.get("host"), d.get("name"))
        self.tree.clear()
        groups = self.groups()
        remote_on = self.hub is not None
        n_items = 0
        for g in groups:
            if g["id"] == UNASSIGNED and not g["items"] and not remote_on:
                continue
            gi = QtWidgets.QTreeWidgetItem([g["title"], "", "", "", "", ""])
            gi.setData(0, GROUP, g)
            gi.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled)
            f = gi.font(0)
            f.setBold(True)
            f.setPointSizeF(f.pointSizeF() + 1)
            gi.setFont(0, f)
            n_conn = sum(1 for i in g["items"] if i.get("connected"))
            gi.setText(1, f"{n_conn}/{len(g['items'])} 已連線" if g["id"] != UNASSIGNED else f"{len(g['items'])} 台")
            gi.setForeground(1, QtGui.QBrush(theme.qc("muted")))
            if g["id"] == UNASSIGNED:
                gi.setForeground(0, QtGui.QBrush(QtGui.QColor(theme.c("warn"))))
                gi.setToolTip(0, "共用網路儀器（多台電腦都看得到）還沒歸屬、或只被掃描偵測到的儀器。\n"
                                 "拖到某台量測節點的群組後才能連線。")
            elif not g["local"] and not g["online"]:
                gi.setForeground(0, QtGui.QBrush(theme.qc("disabled")))
                gi.setText(1, gi.text(1) + "（離線）")
            self.tree.addTopLevelItem(gi)
            if g["id"] != UNASSIGNED and (g["local"] or g["node"]):
                self.tree.setItemWidget(gi, 2, self._group_buttons(g))
            for d in g["items"]:
                it = self._item(d)
                gi.addChild(it)
                n_items += 1
                if cur_key and (d.get("host"), d.get("name")) == cur_key:
                    self.tree.setCurrentItem(it)
            gi.setExpanded(g["id"] not in self._collapsed)
        me = hostname()
        self.sub.setText(f"{me}  ·  {n_items} 台儀器" + ("  ·  Hub 已連線" if remote_on else "  ·  只顯示這台（未連上 Hub）"))
        self._update_actions()

    def _group_buttons(self, g: Dict[str, Any]) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 1, 0, 1)
        h.setSpacing(4)
        b1 = QtWidgets.QPushButton("全部連線")
        b1.setProperty("primary", True)
        b2 = QtWidgets.QPushButton("全部斷線")
        for b in (b1, b2):
            b.setStyleSheet("padding:1px 8px;")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            h.addWidget(b)
        b1.setEnabled(bool(g["local"] or g["online"]))
        b2.setEnabled(bool(g["local"] or g["online"]))
        b1.clicked.connect(lambda: self.connect_group(g))
        b2.clicked.connect(lambda: self.disconnect_group(g))
        h.addStretch(1)
        return w

    def _item(self, d: Dict[str, Any]) -> QtWidgets.QTreeWidgetItem:
        name = d["name"]
        if d.get("local"):
            text, color = status_of(self.station, name)
            if name in self.errors and text == "未連線":
                text, color = "錯誤", theme.c("err")
            if not d.get("enabled", True):
                text, color = "停用", theme.c("disabled")
            msg = d.get("idn") if d.get("connected") else self.errors.get(name, "")
            if not d.get("in_file", True):
                msg = (msg + "　" if msg else "") + "（未寫入 instruments.yaml）"
        else:
            if d.get("lease"):
                text, color = "量測使用中", theme.c("accent")
            elif d.get("connected"):
                text, color = "已連線", theme.c("ok")
            elif d.get("detected_only"):
                text, color = "只偵測到", theme.c("warn")
            elif not d.get("configured"):
                text, color = "尚無設定", theme.c("warn")
            elif not d.get("online"):
                text, color = "離線", theme.c("disabled")
            else:
                text, color = "未連線", theme.c("muted")
            msg = d.get("idn") or ""
            if d.get("connected_on") and not d.get("connected"):
                msg = f"連線中：{', '.join(d['connected_on'])}　" + msg
        extra = []
        if d.get("shared"):
            extra.append("共用" + ("，已歸屬" if d.get("owned") else "，未歸屬"))
        if d.get("hosts") and (d.get("shared") or d.get("detected_only") or not d.get("configured")):
            extra.append("看得到：" + "、".join(d["hosts"]))
        label = d.get("label") or ""
        if extra:
            label = (label + "　" if label else "") + "（" + "；".join(extra) + "）"
        it = QtWidgets.QTreeWidgetItem([name, f"● {text}", str(d.get("driver") or ""), str(d.get("address") or "—"),
                                        label, msg or ""])
        it.setForeground(1, QtGui.QBrush(QtGui.QColor(color)))
        f = it.font(0)
        f.setBold(True)
        it.setFont(0, f)
        if text in ("停用", "未載入", "離線"):
            if text == "未載入":
                it.setText(5, msg or "不在目前的 Station（重新開啟 Lab Control 後載入，或按「啟用 / 停用」）")
            for c in range(len(COLS)):
                it.setForeground(c, QtGui.QBrush(theme.qc("disabled")))
        it.setData(0, ROLE, name if d.get("local") else None)
        it.setData(0, ITEM, d)
        it.setToolTip(5, it.text(5))
        it.setToolTip(0, (d.get("key") or "虛擬儀器") + (f"\n位於 {d.get('host')}" if d.get("host") else ""))
        it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
        return it

    def selected(self) -> Optional[str]:
        """選取的「本機」儀器名稱（本機才能新增 / 移除 / 測試）。"""
        it = self.tree.currentItem()
        return it.data(0, ROLE) if it else None

    def selected_item(self) -> Optional[Dict[str, Any]]:
        it = self.tree.currentItem()
        return it.data(0, ITEM) if it else None

    def selected_group(self) -> Optional[Dict[str, Any]]:
        g = GroupTree.group_of(self.tree.currentItem())
        return g.data(0, GROUP) if g is not None else None

    def _update_actions(self) -> None:
        n = self.selected()
        d = self.selected_item()
        g = self.selected_group()
        inst = self.station.instruments.get(n) if n else None
        leased = bool(n and self.station.lease_holder(n))
        remote_ok = bool(d and not d.get("local") and g and g.get("node") and g.get("online") and d.get("configured"))
        self.a_cfg.setEnabled(inst is not None or remote_ok)
        self.a_conn.setEnabled((inst is not None and not inst.connected) or (remote_ok and not d.get("connected")))
        self.a_disc.setEnabled((inst is not None and inst.connected and not leased)
                               or (remote_ok and d.get("connected") and not d.get("lease")))
        self.a_move.setEnabled(bool(d and d.get("network") and self.hub is not None))
        self.a_test.setEnabled(inst is not None and not leased)
        self.a_remove.setEnabled(bool(n) and not leased)
        self.a_enable.setEnabled(bool(n) and not leased)

    def _log(self, msg: str) -> None:
        self.log.appendPlainText(f"{_dt.datetime.now():%H:%M:%S}  {msg}")

    def _event(self, name: str, msg: str, error: bool = False) -> None:
        if error:
            self.errors[name] = msg
        else:
            self.errors.pop(name, None)
        self._log(f"[{name}] {msg}")
        self.refresh()

    # ---- 動作 ------------------------------------------------------------
    def _menu(self, pos) -> None:
        it = self.tree.itemAt(pos)
        if it is None:
            return
        m = QtWidgets.QMenu(self)
        g = it.data(0, GROUP)
        if g is not None:
            if g["id"] != UNASSIGNED and (g["local"] or g["online"]):
                m.addAction("全部連線", lambda: self.connect_group(g))
                m.addAction("全部斷線", lambda: self.disconnect_group(g))
            else:
                return
        else:
            for a in (self.a_cfg, self.a_conn, self.a_disc, None, self.a_test, None, self.a_enable, self.a_remove):
                if a is None:
                    m.addSeparator()
                elif a.isEnabled():
                    m.addAction(a)
            d = it.data(0, ITEM)
            if d and d.get("network") and self.hub is not None:
                sub = m.addMenu("移到群組")
                cur = GroupTree.group_of(it).data(0, GROUP)
                for tg in self.groups():
                    if tg["id"] == cur["id"]:
                        continue
                    a = sub.addAction(tg["title"], lambda tg=tg, d=d: self.move_to(d, tg))
                    a.setEnabled(tg["id"] == UNASSIGNED or tg["local"] or bool(tg["node"] and tg["online"]))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _double(self, it, _col) -> None:
        if it.data(0, ITEM) is not None:
            self.open_config()

    def _node_backend(self, node: str):
        from ....remote.backend import RemoteBackend
        from ....remote.client import RemoteNode

        return RemoteBackend(RemoteNode(self.hub, node))

    def open_config(self, name: Optional[str] = None) -> Optional[QtWidgets.QWidget]:
        name = name or self.selected()
        if not name:
            d, g = self.selected_item(), self.selected_group()
            if d and not d.get("local") and g and g.get("node") and self.hub is not None:
                return self.open_remote(g["node"], d["name"])
            return None
        if name not in self.station.instruments:
            return None
        w = self.cfg_windows.get(name)
        if w is None or not w.isVisible():
            w = InstrumentConfigWindow(self.station, name)
            self.cfg_windows[name] = w
        w.show()
        w.raise_()
        w.activateWindow()
        return w

    def open_remote(self, node: str, name: str) -> QtWidgets.QWidget:
        from labmonitor.instrument_dialog import RemoteInstrumentDialog

        k = f"{node}/{name}"
        w = self.remote_windows.get(k)
        if w is None or not w.isVisible():
            w = RemoteInstrumentDialog(_HubShim(self.hub), node, name, self)
            w.setModal(False)
            self.remote_windows[k] = w
        w.show()
        w.raise_()
        return w

    def connect_selected(self) -> None:
        n = self.selected()
        if n:
            self._connect([n])
            return
        d, g = self.selected_item(), self.selected_group()
        if d and g and g.get("node"):
            self._remote(g, "connect", [d["name"]])

    def disconnect_selected(self) -> None:
        n = self.selected()
        if n:
            run_bg(lambda: self.station.disconnect(n), lambda _r: self.refresh(), lambda m: self._log(f"❌ {m}"))
            return
        d, g = self.selected_item(), self.selected_group()
        if d and g and g.get("node"):
            self._remote(g, "disconnect", [d["name"]])

    def connect_all(self) -> None:
        self._connect(list(self.station.instruments))

    def connect_group(self, g: Dict[str, Any]) -> None:
        names = [i["name"] for i in g["items"] if i.get("configured", True) and i.get("enabled", True)]
        if g["local"]:
            self._connect([n for n in names if n in self.station.instruments])
        elif g.get("node"):
            self._remote(g, "connect", names)

    def disconnect_group(self, g: Dict[str, Any]) -> None:
        names = [i["name"] for i in g["items"]]
        if g["local"]:
            self._log(f"{g['title']}：全部中斷連線…")
            run_bg(lambda: self.station.disconnect_all([n for n in names if n in self.station.instruments]),
                   self._disc_done, lambda m: self._log(f"❌ {m}"))
        elif g.get("node"):
            self._remote(g, "disconnect", names)

    def _remote(self, g: Dict[str, Any], op: str, names: List[str]) -> None:
        if not names:
            return
        be = self._node_backend(g["node"])
        verb = "連線" if op == "connect" else "中斷"
        self._log(f"🛰 {g['title']}：{verb} {', '.join(names)}…")

        def done(res):
            bad = {k: v for k, v in (res or {}).items() if v}
            for k, v in bad.items():
                self._log(f"⚠ [{g['node']}/{k}] {v}")
            self._log(f"🛰 {g['title']}：{verb}完成 {len(names) - len(bad)}/{len(names)}")
            self.fetch_registry(force=True)
        run_bg(lambda: getattr(be, op)(names), done, lambda m: self._log(f"❌ {m}"))

    def _connect(self, names: List[str]) -> None:
        self._log(f"連線 {', '.join(names)}（唯讀）…")

        def fn():
            return {k: v for k, v in self.station.connect_all(names).items() if v}

        def done(bad):
            for n, e in bad.items():
                self.errors[n] = e
                self._log(f"❌ [{n}] {e}")
            ok = len(names) - len(bad)
            self._log(f"▶ 連線完成：{ok} 台成功" + (f"，{len(bad)} 台失敗" if bad else ""))
            self.refresh()
        run_bg(fn, done, lambda m: self._log(f"❌ {m}"))

    def _disc_done(self, res) -> None:
        bad = {k: v for k, v in res.items() if v}
        for k, v in bad.items():
            self._log(f"⚠ [{k}] {v}")
        self._log(f"■ 已中斷 {len(res) - len(bad)} 台" + (f"，{len(bad)} 台未中斷（量測使用中）" if bad else ""))
        self.refresh()

    def disconnect_all(self) -> None:
        self._log("全部中斷連線…")
        run_bg(self.station.disconnect_all, self._disc_done, lambda m: self._log(f"❌ {m}"))

    # ---- 移動（共用儀器歸屬）------------------------------------------------------------
    def move_selected(self) -> None:
        d = self.selected_item()
        if not d:
            return
        cur = self.selected_group()
        choices = [g for g in self.groups() if g["id"] != (cur or {}).get("id")
                   and (g["id"] == UNASSIGNED or g["local"] or (g["node"] and g["online"]))]
        if not choices:
            QtWidgets.QMessageBox.information(self, "移到群組", "沒有其他可用的群組（別台量測節點不在線）。")
            return
        title, ok = QtWidgets.QInputDialog.getItem(self, "移到群組", f"把 {d['name']} 移到：",
                                                   [g["title"] for g in choices], 0, False)
        if ok:
            self.move_to(d, next(g for g in choices if g["title"] == title))

    def move_to(self, d: Dict[str, Any], target: Dict[str, Any]) -> None:
        hub = self.hub
        if hub is None:
            QtWidgets.QMessageBox.information(self, "移動", "需要連上 Hub 才能移動儀器（歸屬記錄在 Hub）。")
            return
        me = hostname()
        tgt_host = str(target.get("host") or "").lower()
        reg = {r["key"]: r for r in self.registry}
        local_keys = {i["key"] for g in self.groups() if g["local"] for i in g["items"] if i.get("key")}
        local_keys |= {i["key"] for g in self.groups() if g["id"] == UNASSIGNED for i in g["items"]
                       if i.get("local") and i.get("key")}

        def target_has(key: str) -> bool:
            if tgt_host == me.lower():
                return key in local_keys
            return any(e.get("configured") and str(e.get("host", "")).lower() == tgt_host
                       for e in (reg.get(key) or {}).get("entries") or [])

        def add_local(name: str, opts: Dict[str, Any]) -> None:
            o = dict(opts)
            driver = o.pop("driver")
            if name not in self.station.instruments:
                self.station.add(name, driver, **dict(o))
                labfile.add_instrument(self.config_path, name, {"driver": driver, **o})

        def add_remote(node: str, name: str, opts: Dict[str, Any]) -> None:
            from ....remote.client import RemoteNode

            RemoteNode(hub, node).call("add_instrument", name=name, options=opts, timeout=60)

        def work():
            return move_instrument(d, target, me=me, claim=lambda k, h: hub.claim_instrument(k, h),
                                   release=hub.release_instrument, add_local=add_local, add_remote=add_remote,
                                   target_has=target_has)

        def ok(msg):
            self._log(f"⇄ {msg}")
            if self.remote is not None and hasattr(self.remote, "refresh"):
                self.remote.refresh()
            self.fetch_registry(force=True)
            self.refresh()

        def fail(m):
            self._log(f"❌ 移動失敗：{m}")
            QtWidgets.QMessageBox.warning(self, "移動失敗", m)
        self._log(f"⇄ 移動 {d['name']} → {target['title']}…")
        run_bg(work, ok, fail)

    def claim_selected(self) -> None:
        """（相容 0.0.8）把選取的共用儀器拉到這台 = 移到這台的群組。"""
        d = self.selected_item()
        if d:
            mine = next(g for g in self.groups() if g["local"])
            self.move_to(d, mine)

    def add_instrument(self, preset: Optional[dict] = None) -> None:
        from .add_dialog import AddInstrumentDialog

        dlg = AddInstrumentDialog(self.station, self, preset=preset)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        name, opts = dlg.result_name, dict(dlg.result_options)
        try:
            o = dict(opts)
            driver = o.pop("driver")
            self.station.add(name, driver, **o)
            if dlg.save.isChecked():
                labfile.add_instrument(self.config_path, name, opts)
                self._log(f"＋ 已新增 {name}（{driver}），寫入 {self.config_path}")
            else:
                self._log(f"＋ 已新增 {name}（{driver}），只在這次執行有效")
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "新增失敗", str(e))
        self.refresh()

    def remove_instrument(self) -> None:
        n = self.selected()
        if not n:
            return
        in_file = any(name == n for name, _ in labfile.configured(self.config_path))
        q = QtWidgets.QMessageBox.question(self, "移除儀器", f"移除 {n}？" + ("\n會從 instruments.yaml 刪除（原檔先備份）。"
                                                                          if in_file else ""))
        if q != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            if n in self.station.instruments:
                self.station.remove(n)
            if in_file:
                labfile.remove_instrument(self.config_path, n)
            self._log(f"－ 已移除 {n}")
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "移除失敗", str(e))
        self.refresh()

    def toggle_enabled(self) -> None:
        n = self.selected()
        if not n:
            return
        conf = dict(labfile.configured(self.config_path))
        if n not in conf:
            QtWidgets.QMessageBox.information(self, "啟用 / 停用", f"{n} 不在 instruments.yaml 裡。")
            return
        enabled = conf[n].get("enabled", True) is not False
        try:
            if enabled:
                if n in self.station.instruments:
                    self.station.remove(n)
                labfile.update_instrument(self.config_path, n, {"enabled": False})
                self._log(f"已停用 {n}（儀器清單保留，下次開啟不載入）")
            else:
                opts = dict(conf[n])
                opts.pop("enabled", None)
                driver = opts.pop("driver")
                self.station.add(n, driver, **opts)
                labfile.update_instrument(self.config_path, n, {}, remove=("enabled",))
                self._log(f"已啟用 {n}")
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "啟用 / 停用失敗", str(e))
        self.refresh()

    def test_selected(self) -> None:
        n = self.selected()
        if n and n in self.station.instruments:
            from .test_dialog import DriverTestDialog

            DriverTestDialog(self.station, [n], self).show()

    def test_all(self) -> None:
        from .test_dialog import DriverTestDialog

        names = [n for n in self.station.instruments if not self.station.lease_holder(n)]
        DriverTestDialog(self.station, names, self).show()

    def scan(self) -> None:
        from .scan_dialog import ScanDialog

        dlg = ScanDialog(self.station, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted and dlg.chosen:
            self.add_instrument(dlg.chosen)

    def closeEvent(self, ev) -> None:
        for w in list(self.cfg_windows.values()):
            w.close()
        self.bridge.close()
        super().closeEvent(ev)
