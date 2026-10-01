"""左下：儀器參數列表（對應 Labber Measurement Editor 左側的 Channels）。

  * 列出 instruments.yaml 裡所有儀器的可用參數：電磁鐵組、電源電流、儀器參數、量測通道。
  * 拖到流程圖 → 建立節點（電源 / 參數 → Step 節點，量測通道 → 量測節點）；雙擊或「加到流程」也可以。
  * 「取值」讀儀器目前的值；「設定值…」立刻寫入儀器（電源超過單次跳動上限會自動走斜坡）。
  * 「自動更新」定時讀取已連線儀器的電流。讀寫都在背景執行緒，不會卡住畫面。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import QObject, Qt, pyqtSignal

from ....core.station import Station
from ....core.units import parse_quantity, split_unit
from ....scheme.compile import target_param_ref
from ....settings import setting
from .doc import WorkbenchDoc
from .. import theme
from .graph_view import MIME, kind_color
from ....remote.backend import LocalBackend
from ..worker import start_thread

ROLE = Qt.ItemDataRole.UserRole + 1


class _Sig(QObject):
    value = pyqtSignal(str, object, str)      # ref, 值（SI）, 錯誤訊息
    done = pyqtSignal(str)                    # 訊息
    conn = pyqtSignal(object)                 # 已連線的儀器名稱 set（遠端節點在背景查詢）


class ChannelTree(QtWidgets.QTreeWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderLabels(["名稱", "目前值", "用途"])
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DragOnly)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        h = self.header()
        h.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

    def mimeData(self, items: List[QtWidgets.QTreeWidgetItem]) -> QtCore.QMimeData:
        md = QtCore.QMimeData()
        info = items[0].data(0, ROLE) if items else None
        if info and info.get("spec"):
            md.setData(MIME, QtCore.QByteArray(json.dumps(info["spec"]).encode("utf-8")))
            md.setText(items[0].text(0))
        return md

    def mimeTypes(self) -> List[str]:
        return [MIME]


class ChannelPanel(QtWidgets.QWidget):
    edit_requested = pyqtSignal(str)          # 新增 Step 節點後請流程圖開啟設定視窗

    def __init__(self, doc: WorkbenchDoc, station: Station, parent=None, backend=None) -> None:
        super().__init__(parent)
        self.doc, self.station = doc, station
        self.backend = backend or LocalBackend(station)
        self.sig = _Sig()
        self.sig.value.connect(self._on_value)
        self.sig.done.connect(self._on_done)
        self.sig.conn.connect(self._on_conn)
        self.items: Dict[str, List[QtWidgets.QTreeWidgetItem]] = {}
        self._polling = False
        self._conn_busy = False
        self._conn: set = self._local_connected()

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        top = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("搜尋參數…")
        self.search.setClearButtonEnabled(True)
        self.only_common = QtWidgets.QCheckBox("只顯示常用")
        self.only_common.setChecked(True)
        self.only_conn = QtWidgets.QCheckBox("只顯示已連線")
        self.only_conn.setToolTip("只列出已連線儀器的參數（取消勾選可看到全部，拖進流程圖編輯方案）")
        self.only_conn.setChecked(bool(setting("editor.channels.only_connected", True)))
        top.addWidget(self.search, 1)
        top.addWidget(self.only_conn)
        top.addWidget(self.only_common)
        lay.addLayout(top)
        self.tree = ChannelTree()
        lay.addWidget(self.tree, 1)
        btns = QtWidgets.QHBoxLayout()
        self.b_add = QtWidgets.QPushButton("加到流程")
        self.b_get = QtWidgets.QPushButton("取值")
        self.b_set = QtWidgets.QPushButton("設定值…")
        self.b_conn = QtWidgets.QPushButton("連線儀器")
        self.auto = QtWidgets.QCheckBox("自動更新")
        self.auto.setChecked(bool(setting("monitor.auto_poll", True)))
        for w in (self.b_add, self.b_get, self.b_set, self.b_conn):
            btns.addWidget(w)
        btns.addStretch()
        btns.addWidget(self.auto)
        lay.addLayout(btns)
        self.msg = QtWidgets.QLabel()
        self.msg.setStyleSheet("color:palette(text); font-size:11px;")
        lay.addWidget(self.msg)

        self.search.textChanged.connect(self._filter)
        self.only_common.toggled.connect(self._filter)
        self.only_conn.toggled.connect(self._filter)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self.add_selected())
        self.tree.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.b_add.clicked.connect(self.add_selected)
        self.b_get.clicked.connect(self.get_selected)
        self.b_set.clicked.connect(self.set_selected)
        self.b_conn.clicked.connect(self.connect_all)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(int(float(setting("monitor.poll_interval_s", 2.0)) * 1000))
        self.conn_timer = QtCore.QTimer(self)
        self.conn_timer.timeout.connect(self.update_connected)
        self.conn_timer.start(1500)
        self.rebuild()
        theme.on_change(self, lambda w: w.rebuild(), call_now=False)

    # ---- 建立列表 -----------------------------------------------------------
    def rebuild(self) -> None:
        cat = self.doc.catalog
        st = self.station
        self.tree.clear()
        self.items.clear()
        bold = QtGui.QFont()
        bold.setBold(True)

        def group(text: str, sub: str = "") -> QtWidgets.QTreeWidgetItem:
            g = QtWidgets.QTreeWidgetItem([text, "", sub])
            g.setFont(0, bold)
            g.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(g)
            return g

        def row(parent, name: str, use: str, info: Dict[str, Any], color: str = "") -> QtWidgets.QTreeWidgetItem:
            it = QtWidgets.QTreeWidgetItem([name, "—", use])
            it.setData(0, ROLE, info)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if info.get("spec"):
                flags |= Qt.ItemFlag.ItemIsDragEnabled
                it.setToolTip(0, "拖到流程圖，或雙擊加到流程")
            else:
                it.setForeground(0, QtGui.QBrush(theme.qc("muted")))
            it.setFlags(flags)
            if color:
                pm = QtGui.QPixmap(10, 10)
                pm.fill(QtGui.QColor(color))
                it.setIcon(0, QtGui.QIcon(pm))
            parent.addChild(it)
            if info.get("ref"):
                self.items.setdefault(info["ref"], []).append(it)
            return it

        magnets = [t for t in cat.targets if t.group == "magnet"]
        if magnets:
            g = group("電磁鐵組", "一對電供控制一組電磁鐵")
            for t in magnets:
                vi = st.instruments.get(t.instrument)
                needs = list((vi.options.get("sources") if vi is not None else None) or [t.instrument])
                row(g, t.label, "掃描 / 設定", {"ref": target_param_ref(t), "unit": t.unit, "common": True,
                                               "spec": {"kind": "set", "target": t.ref, "mode": "sweep"},
                                               "settable": True, "needs": needs}, kind_color("dc").name())
            g.setExpanded(True)
        for name, inst in st.instruments.items():
            if inst.options.get("group") == "magnet":
                continue
            label = inst.options.get("label", name)
            g = group(label, getattr(inst, "driver_name", "") or type(inst).__name__)
            g.setData(0, ROLE, {"needs": list(inst.options.get("sources") or [name])})
            known = set()
            for t in cat.targets:
                if t.instrument != name:
                    continue
                if t.group in ("source", "magnet"):
                    ref = target_param_ref(t)
                    row(g, f"{t.short} 輸出值", "掃描 / 設定", {"ref": ref, "unit": t.unit, "common": True,
                                                            "spec": {"kind": "set", "target": t.ref, "mode": "sweep"},
                                                            "settable": True}, kind_color("dc").name())
                else:
                    ref = t.ref
                    row(g, t.param_label or t.label, "掃描 / 設定",
                        {"ref": ref, "unit": t.unit, "common": t.common,
                         "spec": {"kind": "set", "target": t.ref}, "settable": True}, kind_color("param").name())
                known.add(ref)
            m = cat.measurer(name)
            if m is not None:
                for tr in m.traces:
                    row(g, f"{tr}（量測）", "量測", {"ref": None, "common": True,
                                                  "spec": {"kind": "measure", "instrument": name, "trace": tr}},
                        kind_color(m.kind or "measure").name())
            # 其他參數（開關、列舉、唯讀）：只能取值 / 設定值，不能拖進流程
            nodes = [("", inst)] + [(k, ch) for k, ch in inst.channels.items()]
            for ck, node in nodes:
                for k, p in node.parameters.items():
                    ref = f"{name}.{ck}.{k}" if ck else f"{name}.{k}"
                    if ref in known or k == "level" or k.startswith("interleave_") or not p.gettable:
                        continue
                    lab = (p.spec.label if p.spec is not None and p.spec.label else k)
                    if ck and len(inst.channels) > 1:
                        lab = f"{ck} {lab}"
                    sp = p.spec
                    row(g, lab, "設定值" if p.settable else "唯讀",
                        {"ref": ref, "unit": (sp.shown_unit if sp else p.unit), "common": False,
                         "settable": p.settable, "kind": sp.kind if sp else "float",
                         "choices": sp.choices if sp else None})
            g.setExpanded(True)
        self._filter()
        self._update_buttons()

    # ---- 已連線的儀器 ---------------------------------------------------------------
    def _local_connected(self) -> set:
        return {n for n, i in self.station.instruments.items() if i.connected}

    def update_connected(self) -> None:
        """更新「已連線」清單（遠端節點在背景查詢，不卡畫面）。"""
        if not self.only_conn.isChecked() or not self.isVisible():
            return
        if not getattr(self.backend, "remote", False):
            self._on_conn(self._local_connected())
            return
        if self._conn_busy:
            return
        self._conn_busy = True

        def run():
            try:
                self.sig.conn.emit({i["name"] for i in self.backend.instruments() if i.get("connected")})
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._conn_busy = False
        start_thread(run)

    def _on_conn(self, names) -> None:
        names = set(names)
        if names != self._conn:
            self._conn = names
            self._filter()

    def _filter(self) -> None:
        q = self.search.text().strip().lower()
        only = self.only_common.isChecked()
        oc = self.only_conn.isChecked()
        conn = self._conn if getattr(self.backend, "remote", False) else self._local_connected()
        self._conn = conn
        shown = 0
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            g_needs = (g.data(0, ROLE) or {}).get("needs")
            any_vis = False
            for j in range(g.childCount()):
                it = g.child(j)
                info = it.data(0, ROLE) or {}
                needs = info.get("needs") or g_needs or []
                vis = (not only or info.get("common", False)) and \
                      (not q or q in it.text(0).lower() or q in g.text(0).lower()) and \
                      (not oc or all(n in conn for n in needs))
                it.setHidden(not vis)
                any_vis |= vis
            g.setHidden(not any_vis)
            shown += any_vis
        if oc and not shown and self.tree.topLevelItemCount():
            self.msg.setText("沒有已連線的儀器：按「連線儀器」，或取消「只顯示已連線」看全部參數")
        elif self.msg.text().startswith("沒有已連線的儀器"):
            self.msg.setText("")

    def _current(self) -> Optional[Dict[str, Any]]:
        it = self.tree.currentItem()
        return it.data(0, ROLE) if it is not None else None

    def _update_buttons(self) -> None:
        info = self._current() or {}
        self.b_add.setEnabled(bool(info.get("spec")))
        self.b_get.setEnabled(bool(info.get("ref")))
        self.b_set.setEnabled(bool(info.get("ref")) and bool(info.get("settable")))

    # ---- 動作 ------------------------------------------------------------
    def add_selected(self) -> None:
        info = self._current()
        if not info or not info.get("spec"):
            return
        blk = self.doc.append_to_flow(info["spec"])
        if info["spec"]["kind"] == "set":
            self.edit_requested.emit(blk.id)

    def _fmt(self, info: Dict[str, Any], v: Any) -> str:
        if isinstance(v, bool) or v is None or isinstance(v, str):
            return "—" if v is None else str(v)
        unit = info.get("unit") or ""
        scale = split_unit(unit)[1] if unit else 1.0
        try:
            return f"{float(v) / (scale or 1.0):.7g} {unit}".strip()
        except (TypeError, ValueError):
            return str(v)

    def _bg(self, fn, done_msg: str = "") -> None:
        def run():
            try:
                fn()
                if done_msg:
                    self.sig.done.emit(done_msg)
            except Exception as e:  # noqa: BLE001
                self.sig.done.emit(f"❌ {e}")
        start_thread(run)

    def get_selected(self) -> None:
        info = self._current()
        if info and info.get("ref"):
            self.read(info["ref"], connect=True)

    def read(self, ref: str, connect: bool = False) -> None:
        def fn():
            try:
                for r, (v, err) in self.backend.get([ref], connect).items():
                    self.sig.value.emit(r, v, err)
            except Exception as e:  # noqa: BLE001
                self.sig.value.emit(ref, None, str(e))
        self._bg(fn)

    def set_selected(self) -> None:
        info = self._current()
        if not info or not info.get("ref") or not info.get("settable"):
            return
        ref, unit = info["ref"], info.get("unit") or ""
        cur = self.items.get(ref, [None])[0]
        cur_text = cur.text(1) if cur is not None else ""
        if info.get("choices"):
            val, ok = QtWidgets.QInputDialog.getItem(self, "設定值", f"{ref}", list(info["choices"]), 0, False)
        elif info.get("kind") == "bool":
            val, ok = QtWidgets.QInputDialog.getItem(self, "設定值", f"{ref}", ["ON", "OFF"], 0, False)
        else:
            val, ok = QtWidgets.QInputDialog.getText(
                self, "設定值（立刻寫入儀器）",
                f"{ref}\n目前：{cur_text}\n輸入新值（可帶單位；不帶單位 = {unit or 'SI'}）：")
        if not ok or not str(val).strip():
            return
        try:
            if info.get("choices") or info.get("kind") in ("bool", "str", "enum"):
                v: Any = val
            else:
                v = parse_quantity(val, unit or "")
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "設定值", f"無法解析：{e}")
            return

        def fn():
            self.sig.value.emit(ref, self.backend.set(ref, v), "")
        self.msg.setText(f"設定 {ref} …（電源超過單次跳動上限時會以斜坡前進）")
        self._bg(fn, f"✔ 已設定 {ref}")

    def connect_all(self) -> None:
        def fn():
            self.backend.connect_all()
        self.msg.setText("連線所有儀器（唯讀，不改變輸出）…")
        self._bg(fn, "✔ 已連線所有儀器")

    def _poll(self) -> None:
        if not self.auto.isChecked() or self._polling or not self.isVisible():
            return
        cands = []
        for ref, items in self.items.items():
            info = items[0].data(0, ROLE) or {}
            if info.get("spec", {}).get("kind") == "set" and ref.endswith(".level"):
                cands.append(ref)
        if not cands:
            return
        self._polling = True

        def run():
            try:
                refs = [r for r in cands if self.backend.connected(r.split(".")[0])]
                if refs:
                    for r, (v, err) in self.backend.get(refs).items():
                        self.sig.value.emit(r, v, err)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._polling = False
        start_thread(run)

    # ---- 回呼（GUI 執行緒）----------------------------------------------------
    def _on_value(self, ref: str, v: Any, err: str) -> None:
        for it in self.items.get(ref, []):
            info = it.data(0, ROLE) or {}
            it.setText(1, "錯誤" if err else self._fmt(info, v))
            it.setToolTip(1, err or "")
            it.setForeground(1, QtGui.QBrush(theme.qc("err" if err else "text")))
        if err:
            self.msg.setText(f"❌ {ref}：{err}")

    def _on_done(self, msg: str) -> None:
        self.msg.setText(msg)
        self.update_connected()
        if msg.startswith("✔ 已連線"):
            for ref in list(self.items):
                if ref.endswith(".level"):
                    self.read(ref)
