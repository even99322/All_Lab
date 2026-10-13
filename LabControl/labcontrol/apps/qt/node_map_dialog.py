"""「🔁 量測電腦對應…」（0.0.32）：在程式裡設定儀器接在哪台量測電腦、每台的電磁鐵組 / VNA 順序。

* 上面「每台的儀器（依序）」→ settings.yaml ``lab_nodes``：方案用 LAB-1 的 magnet_A、magnet_B、VNA1，
  在 LAB-2 執行時依「第幾個」換成 magnet_C、magnet_D、VNA2。
* 下面「儀器接在哪台」→ 這台 instruments.yaml 每台儀器的 ``node:``（保留註解與排版；存檔前留 .bak）。
  電磁鐵組不用寫（跟著它的第一台電源）。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from PyQt6 import QtWidgets

from ...core.config import node_yaml, set_instrument_node
from ...core.nodes import pins
from ...scheme.remap import MAGNET, VNA, role_lists

DEFAULT_LAB_NODES: List[Dict[str, Any]] = [
    {"node": "LAB-1", "magnet": ["magnet_A", "magnet_B"], "vna": ["VNA1"]},
    {"node": "LAB-2", "magnet": ["magnet_C", "magnet_D"], "vna": ["VNA2"]},
]
FOLLOW = "（跟著電源）"
NONE = "（不指定）"


def _split(text: str) -> List[str]:
    return [x.strip() for x in str(text or "").replace("，", ",").replace("、", ",").split(",") if x.strip()]


class NodeMapDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, instruments_path: Optional[Path] = None,
                 lab_nodes: Optional[List[Dict[str, Any]]] = None, self_node: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("量測電腦對應")
        self.resize(760, 620)
        self.path = Path(instruments_path) if instruments_path else None
        self.text = ""
        self.instruments: Dict[str, Dict[str, Any]] = {}
        if self.path is not None and self.path.is_file():
            self.text = self.path.read_text(encoding="utf-8")
            try:
                self.instruments = dict((yaml.safe_load(self.text) or {}).get("instruments") or {})
            except yaml.YAMLError:
                self.instruments = {}
        self.changed_nodes: Dict[str, str] = {}

        v = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "方案裡的電磁鐵組與 VNA 會依「在哪台量測電腦執行」自動換成那台的：同一台的第幾個對到另一台的第幾個。")
        intro.setWordWrap(True)
        v.addWidget(intro)

        # 1.0.1：這台電腦是哪台量測電腦（電腦名稱和表裡的名稱不一樣時，在這裡指定）
        from ...core.nodes import my_names

        me_row = QtWidgets.QHBoxLayout()
        me_row.addWidget(QtWidgets.QLabel("這台電腦是："))
        self.self_combo = QtWidgets.QComboBox()
        self.self_combo.setObjectName("labNodeSelf")
        self.self_combo.setEditable(True)
        me_row.addWidget(self.self_combo, 1)
        names = sorted(n for n in my_names() if n)
        self.self_hint = QtWidgets.QLabel(f"（電腦 / 節點名稱：{'、'.join(names) or '—'}）")
        self.self_hint.setProperty("role", "muted")
        me_row.addWidget(self.self_hint)
        v.addLayout(me_row)
        self._self_node = str(self_node or "")

        box = QtWidgets.QGroupBox("每台量測電腦的儀器（依序，逗號分隔）")
        bl = QtWidgets.QVBoxLayout(box)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setObjectName("labNodesTable")
        self.table.setHorizontalHeaderLabels(["量測電腦", "電磁鐵組", "VNA"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        bl.addWidget(self.table)
        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("＋ 量測電腦")
        add.clicked.connect(lambda: self._add_row({"node": f"LAB-{self.table.rowCount() + 1}"}))
        rm = QtWidgets.QPushButton("移除")
        rm.clicked.connect(self._remove_row)
        reset = QtWidgets.QPushButton("還原預設")
        reset.clicked.connect(lambda: self._fill(DEFAULT_LAB_NODES))
        row.addWidget(add)
        row.addWidget(rm)
        row.addWidget(reset)
        row.addStretch(1)
        bl.addLayout(row)
        found = role_lists(self.instruments)
        names = sorted({n for d in found.values() for n in d[MAGNET]})
        vnas = sorted({n for d in found.values() for n in d[VNA]})
        self.hint = QtWidgets.QLabel(f"這台 instruments.yaml 的電磁鐵組：{'、'.join(names) or '（沒有）'}；"
                                     f"VNA：{'、'.join(vnas) or '（沒有）'}。別台的名稱也可以直接打。")
        self.hint.setWordWrap(True)
        bl.addWidget(self.hint)
        v.addWidget(box, 2)

        box2 = QtWidgets.QGroupBox("儀器接在哪台（這台 instruments.yaml 的 node:）")
        b2 = QtWidgets.QVBoxLayout(box2)
        self.inst_table = QtWidgets.QTableWidget(0, 3)
        self.inst_table.setObjectName("instrumentNodesTable")
        self.inst_table.setHorizontalHeaderLabels(["儀器", "說明", "接在"])
        self.inst_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.inst_table.verticalHeader().setVisible(False)
        b2.addWidget(self.inst_table)
        note = QtWidgets.QLabel("量測電腦只載入「接在這台」或沒指定的儀器；客戶端電腦全部列出。電磁鐵組跟著它的第一台電源。")
        note.setWordWrap(True)
        b2.addWidget(note)
        v.addWidget(box2, 3)

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._fill(lab_nodes if lab_nodes else DEFAULT_LAB_NODES)
        self._fill_instruments()
        self._fill_self()
        self.table.itemChanged.connect(lambda _it: self._fill_self())

    AUTO = "自動（依電腦 / 量測節點名稱）"
    CLIENT = "不是量測電腦（客戶端）"

    def _fill_self(self) -> None:
        cur = self.self_combo.currentText() if self.self_combo.count() else (self._self_node or self.AUTO)
        if cur in ("", None):
            cur = self.AUTO
        self.self_combo.blockSignals(True)
        self.self_combo.clear()
        self.self_combo.addItems([self.AUTO] + [e["node"] for e in self.lab_nodes()] + [self.CLIENT])
        if self.self_combo.findText(cur) < 0:
            self.self_combo.addItem(cur)
        self.self_combo.setCurrentText(cur)
        self.self_combo.blockSignals(False)

    def self_node(self) -> str:
        """settings lab_node_self："" = 自動；"-" = 客戶端；其他 = 量測電腦名稱。"""
        t = self.self_combo.currentText().strip()
        return "" if t in ("", self.AUTO) else "-" if t == self.CLIENT else t

    # ---- 每台的儀器 -------------------------------------------------------------------------------
    def _add_row(self, entry: Dict[str, Any]) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(str(entry.get("node") or "")))
        self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(", ".join(entry.get("magnet") or [])))
        self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(", ".join(entry.get("vna") or [])))

    def _remove_row(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def _fill(self, entries: List[Dict[str, Any]]) -> None:
        self.table.setRowCount(0)
        for e in entries or []:
            if isinstance(e, dict):
                self._add_row(e)

    def lab_nodes(self) -> List[Dict[str, Any]]:
        out = []
        for r in range(self.table.rowCount()):
            node = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            if not node:
                continue
            out.append({"node": node,
                        "magnet": _split(self.table.item(r, 1).text() if self.table.item(r, 1) else ""),
                        "vna": _split(self.table.item(r, 2).text() if self.table.item(r, 2) else "")})
        return out

    def node_names(self) -> List[str]:
        names = [e["node"] for e in self.lab_nodes()]
        for opts in self.instruments.values():
            for n in pins(opts if isinstance(opts, dict) else {}):
                if n not in names:
                    names.append(n)
        return names

    # ---- 儀器接在哪台 -----------------------------------------------------------------------------
    def _fill_instruments(self) -> None:
        nodes = self.node_names()
        choices = [NONE] + nodes + ([", ".join(nodes[:2])] if len(nodes) >= 2 else [])
        self.inst_table.setRowCount(0)
        self.combos: Dict[str, QtWidgets.QComboBox] = {}
        for name, opts in self.instruments.items():
            if str(name).startswith("_") or not isinstance(opts, dict):
                continue
            r = self.inst_table.rowCount()
            self.inst_table.insertRow(r)
            self.inst_table.setItem(r, 0, QtWidgets.QTableWidgetItem(str(name)))
            desc = str(opts.get("label") or opts.get("driver") or "")
            if opts.get("enabled") is False:
                desc += "（停用）"
            self.inst_table.setItem(r, 1, QtWidgets.QTableWidgetItem(desc))
            combo = QtWidgets.QComboBox()
            combo.setEditable(True)
            current = pins(opts)
            items = list(choices)
            if opts.get("sources") and not current:
                items = [FOLLOW] + [c for c in items if c != NONE]
            combo.addItems(items)
            text = ", ".join(current) if current else (FOLLOW if opts.get("sources") else NONE)
            if combo.findText(text) < 0:
                combo.addItem(text)
            combo.setCurrentText(text)
            combo.setProperty("original", text)
            self.inst_table.setCellWidget(r, 2, combo)
            self.combos[str(name)] = combo
        self.inst_table.resizeColumnsToContents()
        self.inst_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)

    def node_changes(self) -> Dict[str, List[str]]:
        """{儀器: [量測電腦…]}（只有改過的；空清單 = 不指定）。"""
        out: Dict[str, List[str]] = {}
        for name, combo in self.combos.items():
            text = combo.currentText().strip()
            if text == str(combo.property("original")):
                continue
            out[name] = [] if text in (NONE, FOLLOW, "") else _split(text)
        return out

    def apply_instruments(self) -> List[str]:
        """把改過的 node: 寫進 instruments.yaml（先留 .bak）；回傳改了哪些儀器。"""
        changes = self.node_changes()
        if not changes or self.path is None:
            return []
        text = self.text
        for name, nodes in changes.items():
            text = set_instrument_node(text, name, nodes)
        if text != self.text:
            shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
            self.path.write_text(text, encoding="utf-8")
            self.text = text
        return sorted(changes)


def edit_node_map(parent, instruments_path: Path) -> Optional[Dict[str, Any]]:
    """開對話框；按確定 → 存 settings.yaml lab_nodes 與 instruments.yaml node:。回傳 {lab_nodes, changed} 或 None。"""
    from ...settings import settings

    st = settings()
    cur = str(st.get("lab_node_self", "") or "")
    dlg = NodeMapDialog(parent, instruments_path, st.get("lab_nodes", []),
                        NodeMapDialog.CLIENT if cur == "-" else cur)
    if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return None
    nodes = dlg.lab_nodes()
    st.set("lab_nodes", nodes)
    st.set("lab_node_self", dlg.self_node())
    try:
        st.save()
    except Exception as e:  # noqa: BLE001
        QtWidgets.QMessageBox.warning(parent, "量測電腦對應", f"無法寫入 settings.yaml：{e}")
    try:
        changed = dlg.apply_instruments()
    except Exception as e:  # noqa: BLE001
        QtWidgets.QMessageBox.warning(parent, "量測電腦對應", f"無法寫入 instruments.yaml：{e}")
        changed = []
    return {"lab_nodes": nodes, "self_node": dlg.self_node(), "changed": changed, "node_yaml": {k: node_yaml(v) for k, v in dlg.node_changes().items()}}


__all__ = ["NodeMapDialog", "edit_node_map", "DEFAULT_LAB_NODES"]
