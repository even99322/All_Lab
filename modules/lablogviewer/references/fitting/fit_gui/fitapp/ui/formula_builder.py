"""公式產生器視窗：輸入理想模型 → 自動加 Fano 相位與環境誤差 → 產生公式 .py"""
import os
import tempfile

import numpy as np
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel, QLineEdit,
    QPlainTextEdit, QCheckBox, QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QSplitter, QTabWidget, QFileDialog, QMessageBox,
)

from ..core import codegen as cg
from ..core.formula import load_formula_module, param_names, guess_params, model_to_complex
from .plots import FitPlotWidget

EXAMPLES = {
    "單模（g = 0）": "S = 1 + kappa/(i(w - w_w) - (kappa + alpha))",
    "耦合雙模": "S = 1 + kappa/(i(w - w_w) - (kappa + alpha) + g^2/(i(w - w_d) - xi))",
    "多行（中間變數）": "D = w - w_r\nL = i*D - (kappa + gamma)/2\nS = 1 - kappa/2 / L",
}

C_NAME, C_UNIT, C_ROLE, C_P0, C_LO, C_HI = range(6)


class FormulaBuilderDialog(QDialog):
    saved = pyqtSignal(str, str, bool)   # (檔案路徑, 函式名稱, 是否在主視窗載入)

    def __init__(self, parent=None, trace_provider=None, start_dir=""):
        super().__init__(parent)
        self.setWindowTitle("公式產生器：理想模型 + Fano + 環境誤差")
        self.resize(1300, 900)
        self.trace_provider = trace_provider
        self.start_dir = start_dir
        self.code = ""
        self._memory = {}          # 參數表設定（依名稱記憶，重新解析時保留）
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self.regenerate)
        self._build()
        self.txt_expr.setPlainText(EXAMPLES["耦合雙模"])
        self.regenerate()

    # ------------------------------------------------------------------ 介面
    def _build(self):
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)

        left = QWidget()
        lv = QVBoxLayout(left)

        g1 = QGroupBox("① 理想模型")
        gl = QGridLayout(g1)
        self.txt_name = QLineEdit("S21_model")
        self.txt_fvar = QLineEdit("w")
        self.txt_fvar.setMaximumWidth(80)
        self.cmb_example = QComboBox()
        self.cmb_example.addItem("（插入範例）")
        self.cmb_example.addItems(list(EXAMPLES))
        self.txt_expr = QPlainTextEdit()
        self.txt_expr.setFont(mono)
        self.txt_expr.setMinimumHeight(80)
        self.txt_expr.setMaximumHeight(140)
        self.chk_i = QCheckBox("i 代表虛數單位")
        self.chk_i.setChecked(True)
        self.txt_bg = QLineEdit("1")
        self.txt_bg.setToolTip("Fano 相位只轉動「S − 背景」的部分；一般傳輸/反射的背景為 1")
        hint = QLabel("Python 語法；^ 會轉成 **、i(…) 視為 1j*(…)。可多行：前面寫中間變數，"
                      "最後一行寫 S = …。可用 sqrt、exp、sin、cos、log、abs、conj、pi。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        r = 0
        gl.addWidget(QLabel("函式名稱"), r, 0); gl.addWidget(self.txt_name, r, 1)
        gl.addWidget(QLabel("頻率變數"), r, 2); gl.addWidget(self.txt_fvar, r, 3); r += 1
        gl.addWidget(QLabel("S_ideal ="), r, 0); gl.addWidget(self.cmb_example, r, 1, 1, 3); r += 1
        gl.addWidget(self.txt_expr, r, 0, 1, 4); r += 1
        gl.addWidget(hint, r, 0, 1, 4); r += 1
        gl.addWidget(self.chk_i, r, 0, 1, 2)
        gl.addWidget(QLabel("背景項 B"), r, 2); gl.addWidget(self.txt_bg, r, 3)
        lv.addWidget(g1)

        g2 = QGroupBox("② 參數（初值 / 邊界留空 = 由數據自動估計）")
        pl = QVBoxLayout(g2)
        self.tbl = QTableWidget(0, 6)
        self.tbl.setHorizontalHeaderLabels(["參數", "單位", "類型", "初值", "下界", "上界"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl.setToolTip("類型：共振位置 → 取數據凹陷頻率、連續擬合時隨視窗移動；"
                            "線寬/耦合 → 由半高寬估計；其他 → 依單位給預設值")
        pl.addWidget(self.tbl)
        lv.addWidget(g2, 1)

        g3 = QGroupBox("③ 加上的修正")
        ol = QGridLayout(g3)
        self.chk_fano = QCheckBox("Fano 相位  S = B + (S_ideal − B)·e^{iθ_F}")
        self.chk_fano.setChecked(True)
        self.chk_env = QCheckBox("環境誤差  A·exp{ i[φ₀ − 2π(w − w_c)τ] }")
        self.chk_env.setChecked(True)
        self.cmb_ref = QComboBox()
        self.chk_conj = QCheckBox("取共軛（IQ 圖繞行方向相反時）")
        self.chk_abs = QCheckBox("同時產生 |S| 版本")
        self.chk_abs.setChecked(True)
        self.chk_si = QCheckBox("依單位換算成 Hz / s / rad（算式以 SI 單位書寫）")
        self.chk_si.setChecked(True)
        ol.addWidget(self.chk_fano, 0, 0, 1, 2)
        ol.addWidget(self.chk_env, 1, 0, 1, 2)
        ol.addWidget(QLabel("延遲參考 w_c"), 2, 0); ol.addWidget(self.cmb_ref, 2, 1)
        ol.addWidget(self.chk_conj, 3, 0, 1, 2)
        ol.addWidget(self.chk_abs, 4, 0, 1, 2)
        ol.addWidget(self.chk_si, 5, 0, 1, 2)
        lv.addWidget(g3)

        g4 = QGroupBox("④ 公式庫")
        ll = QGridLayout(g4)
        self.chk_lib = QCheckBox("儲存時加入公式庫（含公式圖與備註）")
        self.chk_lib.setChecked(True)
        self.txt_lib_title = QLineEdit()
        self.txt_lib_title.setPlaceholderText("標題（留空 = 函式名稱）")
        self.txt_lib_notes = QPlainTextEdit()
        self.txt_lib_notes.setPlaceholderText("備註：適用情況、參數意義…")
        self.txt_lib_notes.setMaximumHeight(60)
        ll.addWidget(self.chk_lib, 0, 0, 1, 2)
        ll.addWidget(QLabel("標題"), 1, 0); ll.addWidget(self.txt_lib_title, 1, 1)
        ll.addWidget(QLabel("備註"), 2, 0); ll.addWidget(self.txt_lib_notes, 2, 1)
        self.chk_lib.toggled.connect(self.txt_lib_title.setEnabled)
        self.chk_lib.toggled.connect(self.txt_lib_notes.setEnabled)
        lv.addWidget(g4)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        lv.addWidget(self.lbl_status)

        bl = QHBoxLayout()
        self.btn_open = QPushButton("開啟既有產生檔…")
        self.btn_preview = QPushButton("以目前數據預覽")
        self.btn_save = QPushButton("儲存 .py…")
        self.btn_save_load = QPushButton("儲存並載入")
        self.btn_save_load.setStyleSheet("font-weight: bold;")
        for b in (self.btn_open, self.btn_preview, self.btn_save, self.btn_save_load):
            bl.addWidget(b)
        lv.addLayout(bl)

        # 右側：程式碼 / 預覽
        self.tabs = QTabWidget()
        self.txt_code = QPlainTextEdit()
        self.txt_code.setReadOnly(True)
        self.txt_code.setFont(mono)
        self.txt_code.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.plot = FitPlotWidget()
        self.tabs.addTab(self.txt_code, "產生的程式碼")
        self.tabs.addTab(self.plot, "預覽（自動初值）")

        sp = QSplitter(Qt.Orientation.Horizontal)
        sp.addWidget(left)
        sp.addWidget(self.tabs)
        sp.setSizes([560, 740])
        QVBoxLayout(self).addWidget(sp)

        # 事件
        for x in (self.txt_name, self.txt_fvar, self.txt_bg):
            x.textChanged.connect(lambda _: self._timer.start())
        self.txt_expr.textChanged.connect(self._timer.start)
        for x in (self.chk_i, self.chk_fano, self.chk_env, self.chk_conj, self.chk_abs, self.chk_si):
            x.toggled.connect(lambda _: self._timer.start())
        self.cmb_ref.currentIndexChanged.connect(lambda _: self._timer.start())
        self.tbl.itemChanged.connect(lambda _: self._timer.start())
        self.cmb_example.activated.connect(self._insert_example)
        self.btn_open.clicked.connect(self.open_existing)
        self.btn_preview.clicked.connect(self.preview)
        self.btn_save.clicked.connect(lambda: self.save(load=False))
        self.btn_save_load.clicked.connect(lambda: self.save(load=True))
        self.btn_preview.setEnabled(self.trace_provider is not None)

    def _insert_example(self, i):
        if i > 0:
            self.txt_expr.setPlainText(EXAMPLES[self.cmb_example.itemText(i)])
        self.cmb_example.setCurrentIndex(0)

    # ------------------------------------------------------------------ 參數表
    def _table_state(self):
        out = {}
        for r in range(self.tbl.rowCount()):
            n = self.tbl.item(r, C_NAME).text()
            out[n] = dict(name=n,
                          unit=self.tbl.cellWidget(r, C_UNIT).currentText().strip(),
                          role=self.tbl.cellWidget(r, C_ROLE).currentData(),
                          p0=self.tbl.item(r, C_P0).text().strip(),
                          lo=self.tbl.item(r, C_LO).text().strip(),
                          hi=self.tbl.item(r, C_HI).text().strip())
        return out

    def _sync_table(self, params):
        self._memory.update(self._table_state())
        if [self.tbl.item(r, C_NAME).text() for r in range(self.tbl.rowCount())] == params:
            return
        self.tbl.blockSignals(True)
        self.tbl.setRowCount(len(params))
        for r, n in enumerate(params):
            u0, r0 = cg.default_param(n)
            m = self._memory.get(n, {})
            it = QTableWidgetItem(n)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.tbl.setItem(r, C_NAME, it)
            cu = QComboBox()
            cu.setEditable(True)
            cu.addItems(cg.UNIT_CHOICES)
            cu.setCurrentText(m.get("unit", u0))
            cu.currentTextChanged.connect(lambda _: self._timer.start())
            self.tbl.setCellWidget(r, C_UNIT, cu)
            cr = QComboBox()
            for k, v in cg.ROLES.items():
                cr.addItem(v, k)
            cr.setCurrentIndex(max(0, cr.findData(m.get("role", r0))))
            cr.currentIndexChanged.connect(lambda _: self._timer.start())
            self.tbl.setCellWidget(r, C_ROLE, cr)
            for c, key in ((C_P0, "p0"), (C_LO, "lo"), (C_HI, "hi")):
                self.tbl.setItem(r, c, QTableWidgetItem(m.get(key, "")))
        self.tbl.blockSignals(False)

    def _sync_ref(self, params):
        cur = self.cmb_ref.currentData()
        self.cmb_ref.blockSignals(True)
        self.cmb_ref.clear()
        self.cmb_ref.addItem("自動（共振位置平均，沒有則用視窗中心）", "auto")
        self.cmb_ref.addItem("擬合視窗中心", "window")
        self.cmb_ref.addItem("所有共振位置參數的平均", "mean_pos")
        state = self._table_state()
        for p in params:
            if state.get(p, {}).get("unit") in cg.FREQ_UNITS:
                self.cmb_ref.addItem(f"參數 {p}", p)
        i = self.cmb_ref.findData(cur)
        self.cmb_ref.setCurrentIndex(max(0, i))
        self.cmb_ref.blockSignals(False)

    # ------------------------------------------------------------------ 產生
    def spec(self):
        return dict(
            name=self.txt_name.text().strip(),
            freq_var=self.txt_fvar.text().strip() or "w",
            imag_i=self.chk_i.isChecked(),
            background=self.txt_bg.text().strip() or "1",
            expr=self.txt_expr.toPlainText(),
            params=list(self._table_state().values()),
            fano=self.chk_fano.isChecked(),
            env=self.chk_env.isChecked(),
            ref=self.cmb_ref.currentData() or "auto",
            conj=self.chk_conj.isChecked(),
            make_abs=self.chk_abs.isChecked(),
            si=self.chk_si.isChecked(),
        )

    def regenerate(self):
        self.code = ""
        try:
            parsed = cg.parse_model(self.txt_expr.toPlainText(),
                                    self.txt_fvar.text().strip() or "w", self.chk_i.isChecked())
            self._sync_table(parsed["params"])
            self._sync_ref(parsed["params"])
            spec = self.spec()
            code = cg.generate_code(spec)
            ok, msg = cg.validate(code, spec)
        except Exception as e:
            ok, msg, code = False, str(e), ""
        self.code = code if ok else ""
        self.txt_code.setPlainText(code or f"# {msg}")
        env = cg.env_names([self.tbl.item(r, 0).text() for r in range(self.tbl.rowCount())])
        extra = ""
        if ok and any(v != k for k, v in env.items()):
            extra = "（環境參數與模型參數同名，已改名為 " + \
                    ", ".join(v for k, v in env.items() if v != k) + "）"
        self.lbl_status.setText(msg + extra)
        self.lbl_status.setStyleSheet("color: #2a7a2a;" if ok else "color: #c0392b;")
        for b in (self.btn_save, self.btn_save_load):
            b.setEnabled(ok)
        self.btn_preview.setEnabled(ok and self.trace_provider is not None)
        return ok

    def preview(self):
        if not self.regenerate():
            return
        trace = self.trace_provider() if self.trace_provider else None
        if trace is None:
            QMessageBox.information(self, "沒有數據", "請先在主視窗載入數據並設定遮罩範圍")
            return
        f, s = trace
        name = self.txt_name.text().strip()
        tmp = os.path.join(tempfile.gettempdir(), f"_fb_preview_{os.getpid()}.py")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(self.code)
            mod, funcs = load_formula_module(tmp)
            fn = funcs[name]
            names = param_names(fn)
            g, _, warn = guess_params(mod, name, names, f, s)
            p0 = [g[n][0] for n in names]
            c, mag = model_to_complex(fn(f, *p0), len(f))
            self.plot.plot(f, s, c, mag, label="自動初值",
                           title="自動初值預覽：" + ", ".join(f"{n}={v:.4g}" for n, v in zip(names, p0)))
            self.tabs.setCurrentWidget(self.plot)
            if warn:
                QMessageBox.warning(self, "guess 警告", warn)
        except Exception as e:
            QMessageBox.critical(self, "預覽失敗", str(e))

    def save(self, load=False):
        if not self.regenerate():
            return
        name = self.txt_name.text().strip()
        path, _ = QFileDialog.getSaveFileName(self, "儲存公式檔",
                                              os.path.join(self.start_dir, f"formula_{name}.py"),
                                              "Python (*.py)")
        if not path:
            return
        if not path.endswith(".py"):
            path += ".py"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.code)
        self.start_dir = os.path.dirname(path)
        self.saved.emit(path, name, load)
        msg = f"已產生：\n{path}"
        if self.chk_lib.isChecked():
            msg += "\n\n已加入公式庫。"
        if load:
            msg += "\n已在主視窗載入。"
        QMessageBox.information(self, "已儲存", msg)

    def open_existing(self):
        path, _ = QFileDialog.getOpenFileName(self, "開啟公式產生器建立的 .py", self.start_dir, "Python (*.py)")
        if not path:
            return
        try:
            spec = cg.load_spec(path)
        except Exception as e:
            QMessageBox.critical(self, "讀取失敗", str(e))
            return
        if not spec:
            QMessageBox.warning(self, "無法讀取", "這個檔案不是公式產生器建立的（找不到設定紀錄）")
            return
        self.set_spec(spec)
        self.start_dir = os.path.dirname(path)

    def library_info(self):
        return dict(enabled=self.chk_lib.isChecked(),
                    title=self.txt_lib_title.text().strip(),
                    notes=self.txt_lib_notes.toPlainText().strip())

    def set_spec(self, spec):
        self._memory = {p["name"]: p for p in spec.get("params", [])}
        self.tbl.setRowCount(0)
        for w_, v in ((self.txt_name, spec.get("name", "S21_model")),
                      (self.txt_fvar, spec.get("freq_var", "w")),
                      (self.txt_bg, spec.get("background", "1"))):
            w_.setText(v)
        self.chk_i.setChecked(spec.get("imag_i", True))
        self.chk_fano.setChecked(spec.get("fano", True))
        self.chk_env.setChecked(spec.get("env", True))
        self.chk_conj.setChecked(spec.get("conj", False))
        self.chk_abs.setChecked(spec.get("make_abs", True))
        self.chk_si.setChecked(spec.get("si", True))
        self.txt_expr.setPlainText(spec.get("expr", ""))
        self.regenerate()
        i = self.cmb_ref.findData(spec.get("ref", "auto"))
        self.cmb_ref.setCurrentIndex(max(0, i))
        self.regenerate()
