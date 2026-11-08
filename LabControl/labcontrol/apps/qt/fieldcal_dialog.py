"""1.0.31：磁場校正視窗——用 YIG 共振（電流 × 頻率的 S 參數圖）反推電磁鐵的「電流 → 磁場」。

流程：選電磁鐵組 → 選 Lab Control 量的 .nc（電磁鐵電流掃描 × VNA 頻率）→ 自動找每個電流的共振頻率 →
B = f / γ' − B_a 對電流擬合（離群點自動排除；在下面的圖點一下可以手動排除 / 加回）→ 儲存到 instruments.yaml。
存好之後電磁鐵組多一個「磁場」參數，量測流程可以直接設定 / 掃描磁場（mT）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PyQt6 import QtCore, QtWidgets

from ...core import fieldcal, labfile
from ...core.capabilities import Source
from ...settings import setting


def magnets(station) -> List[str]:
    """可以校正的電源：電磁鐵組在前，其他電源在後。"""
    out = [n for n, i in station.instruments.items() if isinstance(i, Source) and i.options.get("group") == "magnet"]
    out += [n for n, i in station.instruments.items() if isinstance(i, Source) and n not in out]
    return out


def save_calibration(station, path: Optional[Path], name: str, cal: Optional[Dict[str, Any]]) -> None:
    """寫入 instruments.yaml（cal = None → 清除）並更新正在用的儀器（不用重新連線）。"""
    if path is not None and Path(path).exists() and any(n == name for n, _ in labfile.configured(path)):
        if cal is None:
            labfile.update_instrument(path, name, {}, remove=("field_cal",))
        else:
            labfile.update_instrument(path, name, {"field_cal": cal})
    inst = station.instruments.get(name)
    if inst is not None:
        if cal is None:
            inst.options.pop("field_cal", None)
        else:
            inst.options["field_cal"] = cal
        fieldcal.attach(inst)
    station.bus.publish("station.changed", action="fieldcal", name=name)


class FieldCalDialog(QtWidgets.QDialog):
    def __init__(self, station, parent=None, magnet: str = "", path: Optional[str] = None,
                 config_path: Optional[Path] = None) -> None:
        super().__init__(parent)
        import pyqtgraph as pg

        self.station = station
        self.config_path = config_path if config_path is not None else _default_config(station)
        self.data: Optional[Dict[str, Any]] = None
        self.found: Optional[Dict[str, Any]] = None
        self.result: Optional[Dict[str, Any]] = None
        self.exclude: Optional[np.ndarray] = None
        self.setWindowTitle("磁場校正（YIG 共振）")
        self.resize(1180, 760)
        lay = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QWidget()
        left.setMaximumWidth(400)
        form = QtWidgets.QFormLayout(left)
        lay.addWidget(left)

        self.magnet = QtWidgets.QComboBox()
        self.magnet.setObjectName("magnet")
        self.magnet.addItems(magnets(station))
        if magnet:
            self.magnet.setCurrentText(magnet)
        self.magnet.currentTextChanged.connect(self._show_current_cal)
        form.addRow("電磁鐵組", self.magnet)
        self.current = QtWidgets.QLabel("")
        self.current.setWordWrap(True)
        form.addRow("目前的校正", self.current)

        row = QtWidgets.QHBoxLayout()
        self.file = QtWidgets.QLineEdit()
        self.file.setPlaceholderText("電磁鐵電流 × 頻率的量測檔（.nc）")
        self.file.editingFinished.connect(lambda: self.load_file(self.file.text()))
        b = QtWidgets.QPushButton("選檔…")
        b.clicked.connect(self.pick_file)
        row.addWidget(self.file, 1)
        row.addWidget(b)
        form.addRow("量測檔", row)
        self.trace = QtWidgets.QComboBox()
        self.axis = QtWidgets.QComboBox()
        self.trace.currentTextChanged.connect(lambda _t: self._reload())
        self.axis.currentTextChanged.connect(lambda _t: self._reload())
        form.addRow("資料", self.trace)
        form.addRow("電流軸", self.axis)

        def dspin(lo, hi, dec, val, suffix=""):
            w = QtWidgets.QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setDecimals(dec)
            w.setValue(val)
            w.setKeyboardTracking(False)
            if suffix:
                w.setSuffix(" " + suffix)
            w.valueChanged.connect(lambda _v: self.refit())
            return w

        self.gamma = dspin(1, 100, 4, float(setting("fieldcal.gamma_GHz_per_T", 28.0)), "GHz/T")
        self.gamma.setToolTip("γ/2π：YIG（g ≈ 2.00）約 28.0 GHz/T（= 2.80 MHz/Oe）")
        form.addRow("γ/2π", self.gamma)
        self.offset = dspin(-1000, 1000, 3, 0.0, "mT")
        self.offset.setToolTip("異向性等效場 B_a：f = γ'(B + B_a)；不知道就填 0")
        form.addRow("異向性場 B_a", self.offset)
        self.mode = QtWidgets.QComboBox()
        for k, t in (("auto", "自動判斷"), ("dip", "谷（吸收）"), ("peak", "峰")):
            self.mode.addItem(t, k)
        self.mode.currentIndexChanged.connect(lambda _i: self.refind())
        form.addRow("共振是", self.mode)
        fr = QtWidgets.QHBoxLayout()
        self.fmin = dspin(0, 1000, 4, 0.0, "GHz")
        self.fmax = dspin(0, 1000, 4, 0.0, "GHz")
        for w in (self.fmin, self.fmax):
            w.valueChanged.disconnect()
            w.valueChanged.connect(lambda _v: self.refind())
            w.setSpecialValueText("全部")
            fr.addWidget(w)
        form.addRow("頻率範圍", fr)
        self.order = QtWidgets.QSpinBox()
        self.order.setRange(1, 3)
        self.order.setValue(1)
        self.order.valueChanged.connect(lambda _v: self.refit())
        form.addRow("擬合階數", self.order)
        self.clip = dspin(0, 20, 1, 3.0, "σ")
        self.clip.setSpecialValueText("不排除")
        form.addRow("自動排除離群", self.clip)
        self.min_depth = dspin(0, 60, 1, 1.0, "dB")
        self.min_depth.setToolTip("共振比背景深 / 高不到這麼多 dB 的列不用（沒有共振的電流）")
        form.addRow("最小共振深度", self.min_depth)
        self.summary = QtWidgets.QLabel("選一個量測檔")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(self.summary)
        btns = QtWidgets.QHBoxLayout()
        self.b_save = QtWidgets.QPushButton("儲存校正")
        self.b_save.setProperty("primary", True)
        self.b_save.setEnabled(False)
        self.b_save.clicked.connect(self.save)
        self.b_clear = QtWidgets.QPushButton("清除校正")
        self.b_clear.clicked.connect(self.clear)
        close = QtWidgets.QPushButton("關閉")
        close.clicked.connect(self.reject)
        btns.addWidget(self.b_save)
        btns.addWidget(self.b_clear)
        btns.addStretch(1)
        btns.addWidget(close)
        form.addRow(btns)
        form.addRow(QtWidgets.QLabel("下圖點一下可以手動排除 / 加回那個點。"))

        plots = pg.GraphicsLayoutWidget()
        lay.addWidget(plots, 1)
        self.p_map = plots.addPlot(row=0, col=0, title="|S| (dB) 與找到的共振")
        self.p_map.setLabel("bottom", "電流", units="A")
        self.p_map.setLabel("left", "頻率", units="Hz")
        self.img = pg.ImageItem()
        self.p_map.addItem(self.img)
        self.sc_map = pg.ScatterPlotItem(size=6)
        self.p_map.addItem(self.sc_map)
        self.fit_map = pg.PlotCurveItem(pen=pg.mkPen("#ffd43b", width=2))
        self.p_map.addItem(self.fit_map)
        self.p_fit = plots.addPlot(row=1, col=0, title="磁場 B = f/γ' − B_a")
        self.p_fit.setLabel("bottom", "電流", units="A")
        self.p_fit.setLabel("left", "磁場", units="T")
        self.sc_fit = pg.ScatterPlotItem(size=8)
        self.sc_fit.sigClicked.connect(self._toggle_point)
        self.p_fit.addItem(self.sc_fit)
        self.curve = pg.PlotCurveItem(pen=pg.mkPen("#1971c2", width=2))
        self.p_fit.addItem(self.curve)
        self._show_current_cal()
        if path:
            self.load_file(path)

    # ---- 檔案 ----
    def pick_file(self) -> None:
        from ...data.naming import data_root

        start = str(Path(self.file.text()).parent) if self.file.text() else str(data_root())
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "選量測檔", start, "Lab Control 量測檔 (*.nc)")
        if p:
            self.load_file(p)

    def load_file(self, path: str) -> None:
        path = str(path or "").strip()
        if not path:
            return
        self.file.setText(path)
        try:
            info = fieldcal.scan_nc(path)
        except Exception as e:  # noqa: BLE001
            self.summary.setText(f"❌ 讀不到檔案：{e}")
            return
        if not info["freq"] or not info["axes"] or not info["traces"]:
            self.summary.setText("❌ 這個檔不是「電流 × 頻率」的圖（要有一個掃描軸和 VNA 的頻率軸）")
            return
        for w in (self.trace, self.axis):
            w.blockSignals(True)
            w.clear()
        self.trace.addItems(info["traces"])
        mag = self.magnet.currentText()
        best = 0
        for k, a in enumerate(info["axes"]):
            self.axis.addItem(f"{a['long_name']}（{a['units']}）", a["name"])
            if fieldcal.magnet_of_target(a["target"]) == mag:
                best = k
        self.axis.setCurrentIndex(best)
        tr = next((k for k, t in enumerate(info["traces"]) if "S21" in t), 0)
        self.trace.setCurrentIndex(tr)
        for w in (self.trace, self.axis):
            w.blockSignals(False)
        target_mag = fieldcal.magnet_of_target(info["axes"][best]["target"])
        if target_mag and target_mag in magnets(self.station):
            self.magnet.setCurrentText(target_mag)
        self._reload()

    def _reload(self) -> None:
        if not self.file.text() or not self.trace.currentText() or self.axis.currentData() is None:
            return
        try:
            self.data = fieldcal.load_nc(self.file.text(), self.trace.currentText(), self.axis.currentData())
        except Exception as e:  # noqa: BLE001
            self.summary.setText(f"❌ {e}")
            self.data = None
            return
        self.exclude = np.zeros(len(self.data["current"]), bool)
        self._draw_map()
        self.refind()

    # ---- 找共振 / 擬合 ----
    def refind(self) -> None:
        if self.data is None:
            return
        fmin = self.fmin.value() * 1e9 or None
        fmax = self.fmax.value() * 1e9 or None
        try:
            self.found = fieldcal.find_resonances(self.data["freq"], self.data["data"], self.mode.currentData(), fmin, fmax)
        except Exception as e:  # noqa: BLE001
            self.summary.setText(f"❌ {e}")
            self.found = None
            return
        self.refit()

    def refit(self) -> None:
        if self.data is None or self.found is None:
            return
        try:
            self.result = fieldcal.fit(self.data["current"], self.found["f"], self.gamma.value() * 1e9,
                                       self.offset.value() * 1e-3, self.order.value(), self.clip.value(),
                                       self.min_depth.value(), self.found["depth"], self.exclude)
        except Exception as e:  # noqa: BLE001
            self.result = None
            self.b_save.setEnabled(False)
            self.summary.setText(f"❌ {e}")
            self._draw_points()
            return
        r = self.result
        cal = fieldcal.FieldCalibration({"coef": r["coef"], "range": r["range"]})
        self.summary.setText(
            f"<b>{cal.describe()}</b><br>用了 {r['n']} / {len(r['used'])} 點（共振是{'谷' if self.found['mode'] == 'dip' else '峰'}）"
            f"　殘差 {r['rms'] * 1e3:.3g} mT（{r['rms'] * 1e4:.3g} Oe）　R² = {r['r2']:.6f}<br>"
            f"電流範圍 {r['range'][0] * 1e3:.4g} ~ {r['range'][1] * 1e3:.4g} mA"
            f" → 磁場 {cal.field(r['range'][0]) * 1e3:.4g} ~ {cal.field(r['range'][1]) * 1e3:.4g} mT")
        self.b_save.setEnabled(True)
        self._draw_points()

    def _toggle_point(self, _item, points) -> None:
        if self.exclude is None or not len(points):
            return
        k = int(points[0].data())
        self.exclude[k] = not self.exclude[k]
        self.refit()

    # ---- 畫圖 ----
    def _draw_map(self) -> None:
        import pyqtgraph as pg

        d = self.data
        z = fieldcal.to_db(d["data"])
        cur, fq = d["current"], d["freq"]
        self.img.setImage(np.nan_to_num(z, nan=float(np.nanmedian(z))), autoLevels=True)
        dx = (cur[-1] - cur[0]) / max(len(cur) - 1, 1) if len(cur) > 1 else 1e-3
        dy = (fq[-1] - fq[0]) / max(len(fq) - 1, 1) if len(fq) > 1 else 1.0
        self.img.setRect(QtCore.QRectF(cur[0] - dx / 2, fq[0] - dy / 2, dx * len(cur), dy * len(fq)))
        self.img.setColorMap(pg.colormap.get("viridis"))
        self.p_map.autoRange()

    def _draw_points(self) -> None:
        import pyqtgraph as pg

        if self.data is None or self.found is None:
            return
        cur, fr = self.data["current"], self.found["f"]
        used = self.result["used"] if self.result else np.zeros(len(cur), bool)
        ok = np.isfinite(fr)
        brushes = [pg.mkBrush("#40c057" if used[k] else "#fa5252") for k in range(len(cur))]
        self.sc_map.setData(cur[ok], fr[ok], brush=[b for b, o in zip(brushes, ok) if o], pen=None)
        g = self.gamma.value() * 1e9
        bf = fr / g - self.offset.value() * 1e-3
        spots = [{"pos": (cur[k], bf[k]), "brush": brushes[k], "data": k} for k in range(len(cur)) if ok[k]]
        self.sc_fit.setData(spots)
        if self.result:
            x = np.linspace(cur.min(), cur.max(), 200)
            y = np.polyval(self.result["coef"][::-1], x)
            self.curve.setData(x, y)
            self.fit_map.setData(x, (y + self.offset.value() * 1e-3) * g)
        else:
            self.curve.setData([], [])
            self.fit_map.setData([], [])

    # ---- 存 ----
    def _show_current_cal(self, *_a) -> None:
        inst = self.station.instruments.get(self.magnet.currentText())
        cal = fieldcal.load(inst.options) if inst is not None else None
        if cal is None:
            self.current.setText("（還沒有校正）")
        else:
            d = cal.d
            self.current.setText(f"{cal.describe()}<br><span style='color:gray'>{d.get('time', '')}　"
                                 f"{Path(str(d.get('file', ''))).name}</span>")
        self.b_clear.setEnabled(cal is not None)

    def save(self) -> None:
        if not self.result or self.data is None:
            return
        name = self.magnet.currentText()
        try:
            from ...remote.hub import user_name
            by = user_name()
        except Exception:  # noqa: BLE001
            by = ""
        cal = fieldcal.calibration_dict(self.result, self.gamma.value() * 1e9, self.offset.value() * 1e-3,
                                        {"file": self.file.text(), "trace": self.trace.currentText(),
                                         "axis": self.axis.currentData(), "resonance": self.found["mode"], "by": by})
        try:
            save_calibration(self.station, self.config_path, name, cal)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "儲存失敗", str(e))
            return
        self._show_current_cal()
        QtWidgets.QMessageBox.information(
            self, "磁場校正", f"已儲存 {name} 的磁場校正。\n量測流程裡「{name} 磁場」可以直接設定 / 掃描（mT）。")

    def clear(self) -> None:
        name = self.magnet.currentText()
        if QtWidgets.QMessageBox.question(self, "清除校正", f"清除 {name} 的磁場校正？") != \
                QtWidgets.QMessageBox.StandardButton.Yes:
            return
        save_calibration(self.station, self.config_path, name, None)
        self._show_current_cal()


def _default_config(station) -> Optional[Path]:
    src = (getattr(station, "config", {}) or {}).get("_source_path")
    if src:
        return Path(src)
    try:
        from ...paths import lab_path

        return lab_path("instruments.yaml")
    except Exception:  # noqa: BLE001
        return None
