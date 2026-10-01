"""新增儀器（= Labber Instrument Server 的 Add）：選驅動、名稱、位址；可掃描 VISA 並用 *IDN? 辨識。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt

from ....core.capabilities import Source
from ....core.registry import DRIVERS
from ....core.station import Station
from ....core.units import parse_quantity
from ....settings import setting
from .. import theme
from ..worker import run_bg

VIRTUAL_PAIR = "virtual.interleaved_pair"


def driver_info(name: str) -> Dict[str, Any]:
    cls = DRIVERS.get(name)
    doc = (cls.__doc__ or "").strip().splitlines()
    is_source = issubclass(cls, Source) or getattr(cls, "sim_driver", None) == "sim.current_source" or \
        name.startswith("virtual.")
    return {"name": name, "cls": cls, "doc": doc[0] if doc else "", "source": is_source,
            "virtual": name.startswith("virtual."), "idn": getattr(cls, "IDN_PATTERN", None) or ""}


def selectable_drivers() -> List[str]:
    return [n for n in DRIVERS.names() if not n.startswith("sim.") and n != "rs.vna"]


class AddInstrumentDialog(QtWidgets.QDialog):
    def __init__(self, station: Station, parent=None, preset: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(parent)
        self.station = station
        self.setWindowTitle("新增儀器")
        self.resize(760, 560)
        lay = QtWidgets.QHBoxLayout(self)

        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("<b>驅動</b>"))
        self.drivers = QtWidgets.QListWidget()
        for n in selectable_drivers():
            info = driver_info(n)
            it = QtWidgets.QListWidgetItem(n)
            it.setToolTip(info["doc"])
            it.setData(Qt.ItemDataRole.UserRole, n)
            self.drivers.addItem(it)
        left.addWidget(self.drivers, 1)
        self.doc = QtWidgets.QLabel()
        self.doc.setWordWrap(True)
        self.doc.setStyleSheet("color:palette(text);")
        left.addWidget(self.doc)
        lay.addLayout(left, 2)

        right = QtWidgets.QVBoxLayout()
        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit()
        self.label = QtWidgets.QLineEdit()
        self.address = QtWidgets.QComboBox()
        self.address.setEditable(True)
        self.address.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.address.lineEdit().setPlaceholderText("例如 USB0::0x0B21::0x0039::90ZC38697::0::INSTR、TCPIP0::192.168.1.11::INSTR")
        scan_row = QtWidgets.QHBoxLayout()
        self.b_scan = QtWidgets.QPushButton("掃描 VISA 資源")
        self.b_probe = QtWidgets.QPushButton("連線測試（*IDN?）")
        scan_row.addWidget(self.b_scan)
        scan_row.addWidget(self.b_probe)
        self.timeout = QtWidgets.QSpinBox()
        self.timeout.setRange(100, 600000)
        self.timeout.setSuffix(" ms")
        self.timeout.setValue(int(setting("server.default_timeout_ms", 10000)))
        form.addRow("名稱", self.name)
        form.addRow("說明", self.label)
        form.addRow("位址", self.address)
        form.addRow("", scan_row)
        form.addRow("逾時", self.timeout)
        right.addLayout(form)

        self.src_box = QtWidgets.QGroupBox("安全設定（電源）")
        sf = QtWidgets.QFormLayout(self.src_box)
        ns = setting("server.new_source", {}) or {}
        lim = ns.get("limits") or [-0.2, 0.2]
        self.lo = QtWidgets.QLineEdit(f"{lim[0] * 1e3:g} mA")
        self.hi = QtWidgets.QLineEdit(f"{lim[1] * 1e3:g} mA")
        self.rate = QtWidgets.QLineEdit(f"{float(ns.get('ramp_rate', 5e-4)) * 1e3:g} mA/s")
        self.jump = QtWidgets.QLineEdit(f"{float(ns.get('max_jump', 5e-5)) * 1e3:g} mA")
        sf.addRow("下限", self.lo)
        sf.addRow("上限", self.hi)
        sf.addRow("斜坡速率", self.rate)
        sf.addRow("單次跳動上限", self.jump)
        right.addWidget(self.src_box)

        self.pair_box = QtWidgets.QGroupBox("電磁鐵組（一對電供交錯步進）")
        pf = QtWidgets.QFormLayout(self.pair_box)
        self.s1, self.s2 = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        srcs = [n for n, i in station.instruments.items() if isinstance(i, Source) or Station._sole_source(i) is not None]
        for c in (self.s1, self.s2):
            c.addItems(srcs)
        if len(srcs) > 1:
            self.s2.setCurrentIndex(1)
        self.magnet = QtWidgets.QCheckBox("列在「電磁鐵組」")
        self.magnet.setChecked(True)
        pf.addRow("第一台（先前進）", self.s1)
        pf.addRow("第二台", self.s2)
        pf.addRow("", self.magnet)
        right.addWidget(self.pair_box)
        self.save = QtWidgets.QCheckBox("寫入 instruments.yaml（下次開啟仍在；原檔會先備份）")
        self.save.setChecked(True)
        right.addWidget(self.save)
        self.msg = QtWidgets.QLabel()
        self.msg.setWordWrap(True)
        right.addWidget(self.msg)
        right.addStretch()
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("新增")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)
        lay.addLayout(right, 3)

        self.drivers.currentItemChanged.connect(lambda *_: self._on_driver())
        self.b_scan.clicked.connect(self.scan)
        self.b_probe.clicked.connect(self.probe)
        self.address.currentIndexChanged.connect(self._on_address_pick)
        self.drivers.setCurrentRow(0)
        if preset:
            self.apply_preset(preset)

    # ---- UI ------------------------------------------------------------
    def driver(self) -> str:
        it = self.drivers.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else ""

    def select_driver(self, name: str) -> None:
        for i in range(self.drivers.count()):
            if self.drivers.item(i).data(Qt.ItemDataRole.UserRole) == name:
                self.drivers.setCurrentRow(i)
                return

    def apply_preset(self, p: Dict[str, Any]) -> None:
        if p.get("driver"):
            self.select_driver(p["driver"])
        if p.get("address"):
            self.address.setEditText(p["address"])
        if p.get("name"):
            self.name.setText(p["name"])
        if p.get("label"):
            self.label.setText(p["label"])

    def _on_driver(self) -> None:
        d = self.driver()
        if not d:
            return
        info = driver_info(d)
        self.doc.setText(f"<b>{d}</b><br>{info['doc']}" + (f"<br><span style='color:{theme.c('muted')}'>IDN 樣式：{info['idn']}</span>"
                                                          if info["idn"] else ""))
        pair = d == VIRTUAL_PAIR
        self.pair_box.setVisible(pair)
        self.src_box.setVisible(info["source"])
        for w in (self.address, self.b_scan, self.b_probe, self.timeout):
            w.setEnabled(not info["virtual"])
        if not self.name.text().strip() or self.name.property("auto"):
            base = {"yokogawa": "DC", "rs": "VNA", "zi": "SHFQC", "virtual": "magnet_"}.get(d.split(".")[0], "INST")
            self.name.setText(self._free_name(base))
            self.name.setProperty("auto", True)

    def _free_name(self, base: str) -> str:
        i = 1
        while f"{base}{i}" in self.station.instruments or (base.endswith("_") and f"{base}{chr(64 + i)}" in
                                                            self.station.instruments):
            i += 1
        return f"{base}{chr(64 + i)}" if base.endswith("_") else f"{base}{i}"

    def scan(self) -> None:
        from ....diagnostics import scan_resources

        self.msg.setText("掃描中（對每個資源送 *IDN?，唯讀）…")
        self.b_scan.setEnabled(False)

        def done(res):
            self.b_scan.setEnabled(True)
            used = {str(i.options.get("address", "")) for i in self.station.instruments.values()}
            self.address.clear()
            for r in res:
                tag = "（已在清單）" if r.address in used else ""
                text = f"{r.address}　{r.idn or r.error}{tag}"
                self.address.addItem(text, {"address": r.address, "suggested": r.suggested})
            self.msg.setText(f"找到 {len(res)} 個 VISA 資源" if res else "沒有找到 VISA 資源")

        def fail(m):
            self.b_scan.setEnabled(True)
            self.msg.setText(f"<span style='color:{theme.c('err')}'>❌ {m}</span><br>請確認已安裝 NI-VISA / Keysight IO Libraries，"
                             "或在 settings.yaml 設 server.visa_backend: \"@py\"（需 pip install pyvisa-py）。")
        run_bg(lambda: scan_resources(identify=True), done, fail)

    def _on_address_pick(self, i: int) -> None:
        data = self.address.itemData(i)
        if not data:
            return
        self.address.setEditText(data["address"])
        if data["suggested"]:
            self.select_driver(data["suggested"][0])
            self.msg.setText(f"依 *IDN? 建議驅動：{', '.join(data['suggested'])}")

    def _addr(self) -> str:
        t = self.address.currentText().strip()
        return t.split("　")[0].strip()

    def probe(self) -> None:
        addr = self._addr()
        if not addr:
            return
        from ....core.transport import VisaTransport

        self.msg.setText(f"連線測試 {addr} …")
        pat = driver_info(self.driver())["idn"]

        def fn():
            t = VisaTransport(addr, timeout_ms=self.timeout.value(),
                              backend=str(setting("server.visa_backend", "") or ""))
            try:
                return t.query("*IDN?")
            finally:
                t.close()

        def done(idn):
            ok = not pat or re.search(pat, idn, re.I)
            self.msg.setText(f"✔ *IDN? = {idn}" + ("" if ok else
                             f"<br><span style='color:{theme.c('warn')}'>⚠ 與 {self.driver()} 預期的型號不同</span>"))
        run_bg(fn, done, lambda m: self.msg.setText(f"<span style='color:{theme.c('err')}'>❌ {m}</span>"))

    # ---- 結果 ------------------------------------------------------------
    def options(self) -> Dict[str, Any]:
        d = self.driver()
        info = driver_info(d)
        opts: Dict[str, Any] = {"driver": d}
        if self.label.text().strip():
            opts["label"] = self.label.text().strip()
        if not info["virtual"]:
            addr = self._addr()
            if not addr:
                raise ValueError("請填位址")
            opts["address"] = addr
            opts["timeout_ms"] = self.timeout.value()
        if d == VIRTUAL_PAIR:
            a, b = self.s1.currentText(), self.s2.currentText()
            if not a or a == b:
                raise ValueError("請選兩台不同的電源")
            opts["sources"] = [a, b]
            if self.magnet.isChecked():
                opts["group"] = "magnet"
            opts["display_unit"] = setting("editor.new_blocks.dc_set.unit", "mA")
        if info["source"]:
            lo, hi = parse_quantity(self.lo.text(), "A"), parse_quantity(self.hi.text(), "A")
            if lo >= hi:
                raise ValueError("下限必須小於上限")
            opts["source"] = {"limits": [lo, hi], "ramp_rate": parse_quantity(self.rate.text(), "A/s"),
                              "max_jump": parse_quantity(self.jump.text(), "A")}
        return opts

    def _accept(self) -> None:
        name = self.name.text().strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
            self.msg.setText(f"<span style='color:{theme.c('err')}'>名稱只能用英數字與底線，且不能以數字開頭</span>")
            return
        if name in self.station.instruments:
            self.msg.setText(f"<span style='color:{theme.c('err')}'>{name} 已存在</span>")
            return
        try:
            self.result_name, self.result_options = name, self.options()
        except Exception as e:  # noqa: BLE001
            self.msg.setText(f"<span style='color:{theme.c('err')}'>{e}</span>")
            return
        self.accept()
