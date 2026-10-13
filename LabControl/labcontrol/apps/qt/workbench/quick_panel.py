"""流程圖左邊的「常改的數值」（0.0.31）：方案裡改過兩次以上的欄位會列在這裡，直接調（滾輪調位數），不用每次雙擊節點。

記錄在方案的 quick（「方塊id.欄位」→ 改過幾次；-1 = 使用者從面板移除，不再列出），所以跟著專案範本一起同步。
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt

from ....core.units import parse_quantity, split_unit
from .dialogs import decimals, dspin, ispin
from .doc import QUICK_MIN, WorkbenchDoc

FIELD_LABEL = {"value": "值", "start": "起點", "stop": "終點", "step": "步進", "points": "點數", "settle": "等待",
               "seconds": "等待"}


class QuickPanel(QtWidgets.QFrame):
    def __init__(self, doc: WorkbenchDoc, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.setObjectName("quickPanel")
        self.setMinimumWidth(170)
        self.setMaximumWidth(260)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(4)
        head = QtWidgets.QLabel("常改的數值")
        head.setStyleSheet("font-weight:600;")
        head.setToolTip("同一個欄位改過兩次以上就會出現在這裡，可以直接調整（滾輪 / ↑↓ 調游標那一位）。\n"
                        "右鍵可以從這裡移除。")
        v.addWidget(head)
        self.form = QtWidgets.QFormLayout()
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setVerticalSpacing(3)
        v.addLayout(self.form)
        self.empty = QtWidgets.QLabel("同一個數值改兩次後會出現在這裡")
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color:palette(placeholder-text); font-size:11px;")
        v.addWidget(self.empty)
        v.addStretch(1)
        self.rows: List[Tuple[str, QtWidgets.QWidget]] = []
        self._keys: List[str] = []
        doc.changed.connect(self._changed)
        self.rebuild()

    # ---- 列出 ----------------------------------------------------------------------------
    def entries(self) -> List[str]:
        q = self.doc.scheme.quick
        keys = [k for k, n in q.items() if n >= QUICK_MIN and self._resolve(k) is not None]
        order = {b.id: i for i, b in enumerate(self.doc.scheme.flat())}
        return sorted(keys, key=lambda k: (order.get(k.split(".", 1)[0], 999), k))

    def _resolve(self, key: str) -> Optional[Tuple[Any, str, Optional[str]]]:
        """→ (方塊, 欄位, settings 的鍵或 None)"""
        parts = key.split(".")
        b = self.doc.node(parts[0])
        if b is None:
            return None
        if len(parts) == 3 and parts[1] == "settings":
            return (b, "settings", parts[2]) if parts[2] in (b.settings or {}) else None
        return (b, parts[1], None) if len(parts) == 2 and hasattr(b, parts[1]) else None

    def _changed(self, origin: object) -> None:
        if origin is self:
            return
        if self.entries() != self._keys:
            self.rebuild()
        else:
            self._load_values()

    def rebuild(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.rows = []
        self._keys = self.entries()
        for key in self._keys:
            b, f, sk = self._resolve(key)
            w = self._editor(key, b, f, sk)
            lab = QtWidgets.QLabel(self._label(b, f, sk))
            lab.setToolTip(key)
            lab.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            lab.customContextMenuRequested.connect(lambda _p, k=key: self._menu(k))
            w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            w.customContextMenuRequested.connect(lambda _p, k=key: self._menu(k))
            self.form.addRow(lab, w)
            self.rows.append((key, w))
        self.empty.setVisible(not self._keys)
        self._load_values()

    def _label(self, b, f: str, sk: Optional[str]) -> str:
        name = b.title or (b.target if b.kind == "set" else b.instrument if b.kind == "measure" else b.kind)
        if sk:
            m = self.doc.catalog.measurer(b.instrument)
            fl = m.field_label(sk) if m and hasattr(m, "field_label") else sk
            return f"{name} · {fl}"
        return f"{name} · {FIELD_LABEL.get(f, f)}"

    def _unit(self, b, f: str, sk: Optional[str]) -> str:
        if sk:
            m = self.doc.catalog.measurer(b.instrument)
            fld = m.field(sk) if m else None
            return getattr(fld, "unit", "") or ""
        if f in ("settle", "seconds"):
            return "s"
        return "" if f == "points" else (b.unit or "")

    def _editor(self, key: str, b, f: str, sk: Optional[str]) -> QtWidgets.QWidget:
        unit = self._unit(b, f, sk)
        if f == "points" or (sk and not unit):
            w = ispin(-10_000_000, 10_000_000)
            w.valueChanged.connect(lambda v, k=key: self._set(k, int(v)))
        else:
            w = dspin(-1e12, 1e12, decimals(unit) if unit else 6, unit)
            w.valueChanged.connect(lambda v, k=key: self._set(k, float(v)))
        w.setObjectName(f"quick:{key}")
        w.setMinimumWidth(90)
        return w

    def _load_values(self) -> None:
        for key, w in self.rows:
            r = self._resolve(key)
            if r is None:
                continue
            b, f, sk = r
            raw = (b.settings or {}).get(sk) if sk else getattr(b, f, None)
            w.blockSignals(True)
            try:
                if isinstance(w, QtWidgets.QSpinBox):
                    w.setValue(int(float(str(raw).split()[0])) if raw not in (None, "") else 0)
                else:
                    unit = self._unit(b, f, sk)
                    if sk and isinstance(raw, str):
                        w.setValue(parse_quantity(raw, unit) / (split_unit(unit)[1] or 1.0))
                    else:
                        w.setValue(float(raw) if raw is not None else 0.0)
            except (TypeError, ValueError, IndexError):
                pass
            finally:
                w.blockSignals(False)

    # ---- 修改 ----------------------------------------------------------------------------
    def _set(self, key: str, value: Any) -> None:
        r = self._resolve(key)
        if r is None:
            return
        b0, f, sk = r
        unit = self._unit(b0, f, sk)

        def fn(s) -> None:
            b = s.graph.nodes.get(b0.id)
            if b is None:
                return
            if sk:
                b.settings = dict(b.settings or {})
                b.settings[sk] = f"{value:.9g} {unit}".strip() if isinstance(value, float) and unit else value
            else:
                setattr(b, f, value)
        self.doc.mutate(fn, origin=self)

    def _menu(self, key: str) -> None:
        m = QtWidgets.QMenu(self)
        m.addAction("從這裡移除", lambda: self.doc.mutate(lambda s: s.quick.__setitem__(key, -1)))
        m.exec(self.cursor().pos())

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802
        return QtCore.QSize(210, 200)
