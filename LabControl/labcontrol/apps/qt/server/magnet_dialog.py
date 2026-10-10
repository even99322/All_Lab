"""「＋ 電磁鐵組…」：把兩個電源輸出配成一組電磁鐵（virtual.interleaved_pair）。

可以配的：單通道電源（GS200 / GS210 → DC1）或多通道電源的通道（GS820 → DC5.ch1、DC5.ch2）。
寫入 instruments.yaml::

    magnet_C:
      driver: virtual.interleaved_pair
      group: magnet
      label: 磁鐵 C（DC5 ch1+ch2）
      sources: [DC5.ch1, DC5.ch2]
      display_unit: mA
      source: {limits: [...], ramp_rate: ..., max_jump: ..., resolution: ...}   # 取自第一個電源

配好之後，儀器伺服器把這兩個電源收在電磁鐵組底下（展開可以單獨連線 / 控制），即時監控的電源群組也以它為一組。
"""
from __future__ import annotations

import math
import string
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt6 import QtWidgets

from ....core import labfile
from ....core.capabilities import Source
from ....core.station import Station


def source_outputs(station: Station) -> List[Tuple[str, str]]:
    """可以配對的電源輸出 [(ref, 顯示文字)]：單通道 → 名稱；多通道 → 名稱.chN。虛擬儀器不列。"""
    out = []
    for n, inst in station.instruments.items():
        if inst.options.get("sources"):
            continue
        label = inst.options.get("label") or ""
        chans = [(ck, ch) for ck, ch in getattr(inst, "channels", {}).items() if isinstance(ch, Source)]
        if len(chans) > 1:
            for ck, _ch in chans:
                out.append((f"{n}.{ck}", f"{n} {ck}" + (f"　{label}" if label else "")))
        elif isinstance(inst, Source) or chans:
            out.append((n, n + (f"　{label}" if label else "")))
    return out


def used_refs(station: Station, conf: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """已經配進某個電磁鐵組的電源 {ref: 電磁鐵組名稱}（ref 與 sources 寫法相同）。"""
    out: Dict[str, str] = {}
    names = dict(conf)
    for n, inst in station.instruments.items():
        names.setdefault(n, dict(inst.options))
    for n, o in names.items():
        for r in (o or {}).get("sources") or []:
            out[str(r)] = n
    return out


def _fin(v: Any) -> Optional[float]:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def source_block(station: Station, ref: str) -> Dict[str, Any]:
    """電磁鐵組的 source: 安全設定，取自第一個電源（上下限、斜坡速率、單次跳動上限、解析度）。"""
    try:
        src = station.source(ref)
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[str, Any] = {}
    lim = getattr(src, "limits", None)
    lo, hi = _fin(getattr(lim, "lo", None)), _fin(getattr(lim, "hi", None))
    if lo is not None and hi is not None:
        out["limits"] = [lo, hi]
    rp = getattr(src, "ramp_policy", None)
    for key, val in (("ramp_rate", getattr(rp, "rate", None)), ("max_jump", getattr(rp, "max_jump", None)),
                     ("resolution", getattr(src, "resolution", None))):
        v = _fin(val)
        if v:
            out[key] = v
    return out


def next_name(existing) -> Tuple[str, str]:
    for ch in string.ascii_uppercase:
        if f"magnet_{ch}" not in existing:
            return f"magnet_{ch}", ch
    return f"magnet_{len(existing)}", str(len(existing))


class MagnetDialog(QtWidgets.QDialog):
    def __init__(self, station: Station, config_path: Path, parent=None, preset: Optional[List[str]] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新增電磁鐵組")
        self.station = station
        try:
            conf = dict(labfile.configured(config_path))
        except Exception:  # noqa: BLE001
            conf = {}
        self.existing = set(conf) | set(station.instruments)
        self.used = used_refs(station, conf)
        self.outputs = source_outputs(station)
        self.result_name = ""
        self.result_options: Dict[str, Any] = {}

        form = QtWidgets.QFormLayout(self)
        name, letter = next_name(self.existing)
        self.letter = letter
        self.name = QtWidgets.QLineEdit(name)
        self.label = QtWidgets.QLineEdit("")
        self.a = QtWidgets.QComboBox()
        self.b = QtWidgets.QComboBox()
        for cb in (self.a, self.b):
            for ref, text in self.outputs:
                cb.addItem(text + (f"　（已在 {self.used[ref]}）" if ref in self.used else ""), ref)
            cb.currentIndexChanged.connect(self._sync)
        free = [i for i, (ref, _t) in enumerate(self.outputs) if ref not in self.used]
        if preset:
            for cb, ref in zip((self.a, self.b), preset):
                i = cb.findData(ref)
                if i >= 0:
                    cb.setCurrentIndex(i)
        elif len(free) >= 2:
            self.a.setCurrentIndex(free[0])
            self.b.setCurrentIndex(free[1])
        self.unit = QtWidgets.QComboBox()
        self.unit.addItems(["mA", "A", "uA"])
        form.addRow("名稱", self.name)
        form.addRow("說明", self.label)
        form.addRow("電源 1（先前進）", self.a)
        form.addRow("電源 2", self.b)
        form.addRow("顯示單位", self.unit)
        note = QtWidgets.QLabel("兩個電源交錯步進控制一組電磁鐵（輸出值 = 兩台平均）。\n"
                                "安全設定（上下限、斜坡速率）取自電源 1，之後可以在 instruments.yaml 改。\n"
                                "配對後兩個電源收在電磁鐵組底下，展開仍可以單獨連線、控制。")
        note.setProperty("role", "muted")
        form.addRow(note)
        self.msg = QtWidgets.QLabel("")
        self.msg.setWordWrap(True)
        form.addRow(self.msg)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.ok = bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        form.addRow(bb)
        self._label_auto = True
        self.label.textEdited.connect(lambda _t: setattr(self, "_label_auto", False))
        self.name.textChanged.connect(self._sync)
        self._sync()

    def _pair_text(self) -> str:
        a, b = self.a.currentData() or "", self.b.currentData() or ""
        if a.split(".")[0] == b.split(".")[0] and "." in a and "." in b:
            return f"{a.split('.')[0]} {a.split('.')[1]}+{b.split('.')[1]}"
        return f"{a}+{b}"

    def _sync(self, *_a) -> None:
        if not hasattr(self, "msg"):
            return
        if getattr(self, "_label_auto", True):
            self.label.setText(f"磁鐵 {self.letter}（{self._pair_text()}）")
        err = self.problem()
        self.msg.setText(err or "")
        if hasattr(self, "ok"):
            self.ok.setEnabled(not err)

    def problem(self) -> str:
        if not self.outputs:
            return "沒有可以配對的電源：請先新增電源（GS200 / GS210 / GS820）。"
        a, b = self.a.currentData(), self.b.currentData()
        n = self.name.text().strip()
        if not n or "." in n or n in self.existing:
            return f"名稱「{n}」不能用（空白、含「.」或已經有這個名稱）。"
        if a == b:
            return "兩個電源要不同。"
        busy = [r for r in (a, b) if r in self.used]
        if busy:
            return "、".join(f"{r} 已經在 {self.used[r]}" for r in busy) + "：一個電源只能在一組電磁鐵裡。"
        return ""

    def accept(self) -> None:
        if self.problem():
            return
        a, b = self.a.currentData(), self.b.currentData()
        opts: Dict[str, Any] = {"driver": "virtual.interleaved_pair", "group": "magnet",
                                "label": self.label.text().strip() or self.name.text().strip(),
                                "sources": [a, b], "display_unit": self.unit.currentText()}
        block = source_block(self.station, a)
        if block:
            opts["source"] = block
        node = self.station.instruments[a.split(".")[0]].options.get("node")
        if node:
            opts["node"] = node
        self.result_name = self.name.text().strip()
        self.result_options = opts
        super().accept()
