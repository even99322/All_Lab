"""公式庫視窗：瀏覽 / 搜尋 / 載入 已加入的公式，顯示公式圖與備註"""
import os

from PyQt6.QtCore import Qt, QTimer, QUrl, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QListWidget, QListWidgetItem, QSplitter, QFileDialog, QMessageBox,
    QInputDialog, QCheckBox, QGroupBox,
)

from ..core.library import FormulaLibrary, latex_for_spec
from ..core import codegen as cg
from ..core.formula import load_formula_module, param_names, get_units
from .latex_view import FormulaImage, check_latex

ROLE_ID = Qt.ItemDataRole.UserRole


def auto_latex(path, func):
    """從公式產生器的設定紀錄自動產生 LaTeX；沒有紀錄時給佔位內容"""
    try:
        spec = cg.load_spec(path)
    except Exception:
        spec = None
    if spec and func in (spec.get("name"), spec.get("name", "") + "_abs"):
        try:
            return latex_for_spec(spec, func), spec
        except Exception:
            pass
    return r"S_{21} = \mathrm{" + func.replace("_", r"\_") + r"}(\omega_p)", spec


class FormulaLibraryDialog(QDialog):
    load_requested = pyqtSignal(str, str)      # (檔案, 函式)
    open_in_builder = pyqtSignal(dict)         # 公式產生器設定
    library_changed = pyqtSignal()

    def __init__(self, library=None, parent=None, current_provider=None):
        super().__init__(parent)
        self.setWindowTitle("公式庫")
        self.resize(1200, 760)
        self.lib = library or FormulaLibrary()
        self.current_provider = current_provider     # () -> (path, func) 或 None
        self._eid = None
        self._dirty = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(350)
        self._preview_timer.timeout.connect(self._update_preview)
        self._build()
        self.refresh_list()

    # ------------------------------------------------------------------ 介面
    def _build(self):
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)

        left = QWidget()
        lv = QVBoxLayout(left)
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("搜尋標題 / 函式 / 備註")
        self.lst = QListWidget()
        self.lst.setSpacing(2)
        self.lst.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.btn_add_cur = QPushButton("加入目前公式")
        self.btn_add_file = QPushButton("從檔案加入…")
        self.chk_copy = QCheckBox("加入時複製公式檔到公式庫資料夾")
        self.chk_copy.setChecked(bool(self.lib.options.get("copy_files", True)))
        self.btn_delete = QPushButton("刪除")
        self.btn_folder = QPushButton("開啟公式庫資料夾")
        lv.addWidget(self.txt_search)
        lv.addWidget(self.lst, 1)
        row = QHBoxLayout()
        row.addWidget(self.btn_add_cur)
        row.addWidget(self.btn_add_file)
        lv.addLayout(row)
        lv.addWidget(self.chk_copy)
        row2 = QHBoxLayout()
        row2.addWidget(self.btn_delete)
        row2.addWidget(self.btn_folder)
        lv.addLayout(row2)

        right = QWidget()
        rv = QVBoxLayout(right)
        top = QGridLayout()
        self.txt_title = QLineEdit()
        self.lbl_info = QLabel("")
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_info.setStyleSheet("color: gray;")
        top.addWidget(QLabel("標題"), 0, 0)
        top.addWidget(self.txt_title, 0, 1)
        top.addWidget(self.lbl_info, 1, 0, 1, 2)
        rv.addLayout(top)

        g_img = QGroupBox("公式")
        gi = QVBoxLayout(g_img)
        self.img = FormulaImage(fontsize=18, fit_width=True)
        self.img.setMinimumHeight(150)
        gi.addWidget(self.img)
        rv.addWidget(g_img, 3)

        g_notes = QGroupBox("備註")
        gn = QVBoxLayout(g_notes)
        self.txt_notes = QPlainTextEdit()
        self.txt_notes.setPlaceholderText("例如：適用條件、參數物理意義、量測批次、擬合技巧…")
        gn.addWidget(self.txt_notes)
        rv.addWidget(g_notes, 2)

        g_tex = QGroupBox("LaTeX（每行一個式子；修改後上方即時預覽）")
        gt = QVBoxLayout(g_tex)
        self.txt_latex = QPlainTextEdit()
        self.txt_latex.setFont(mono)
        self.txt_latex.setMaximumHeight(110)
        self.lbl_tex_err = QLabel("")
        self.lbl_tex_err.setStyleSheet("color: #c0392b;")
        row3 = QHBoxLayout()
        self.btn_auto_tex = QPushButton("由產生器設定重建 LaTeX")
        row3.addWidget(self.lbl_tex_err, 1)
        row3.addWidget(self.btn_auto_tex)
        gt.addWidget(self.txt_latex)
        gt.addLayout(row3)
        rv.addWidget(g_tex, 1)

        bottom = QHBoxLayout()
        self.btn_builder = QPushButton("在公式產生器中開啟")
        self.btn_save = QPushButton("儲存變更")
        self.btn_load = QPushButton("載入此公式")
        self.btn_load.setStyleSheet("font-weight: bold; padding: 6px 14px;")
        bottom.addWidget(self.btn_builder)
        bottom.addStretch()
        bottom.addWidget(self.btn_save)
        bottom.addWidget(self.btn_load)
        rv.addLayout(bottom)

        sp = QSplitter(Qt.Orientation.Horizontal)
        sp.addWidget(left)
        sp.addWidget(right)
        sp.setSizes([330, 870])
        QVBoxLayout(self).addWidget(sp)

        self.txt_search.textChanged.connect(lambda _: self.refresh_list())
        self.lst.currentItemChanged.connect(self._on_select)
        self.lst.itemDoubleClicked.connect(lambda _: self.load_selected())
        self.btn_add_cur.clicked.connect(self.add_current)
        self.btn_add_file.clicked.connect(self.add_from_file)
        self.chk_copy.toggled.connect(self._on_copy_toggled)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.lib.formula_dir)))
        self.txt_latex.textChanged.connect(self._on_edit_latex)
        self.txt_notes.textChanged.connect(self._mark_dirty)
        self.txt_title.textEdited.connect(self._mark_dirty)
        self.btn_auto_tex.clicked.connect(self.rebuild_latex)
        self.btn_save.clicked.connect(self.save_current)
        self.btn_load.clicked.connect(self.load_selected)
        self.btn_builder.clicked.connect(self.open_builder)
        self._set_editor_enabled(False)

    # ------------------------------------------------------------------ 清單
    def refresh_list(self, select_id=None):
        select_id = select_id or self._eid
        self.lst.blockSignals(True)
        self.lst.clear()
        for e in self.lib.search(self.txt_search.text()):
            it = QListWidgetItem(f"{e.get('title') or e['func']}\n    {e['func']}  ·  {os.path.basename(e['file'])}")
            it.setToolTip((e.get("notes") or "").strip() or e["func"])
            fm = self.lst.fontMetrics()
            it.setSizeHint(QSize(fm.horizontalAdvance(it.text().split("\n")[-1]) + 30,
                                 2 * fm.lineSpacing() + 8))
            it.setData(ROLE_ID, e["id"])
            if not os.path.exists(self.lib.resolve(e)):
                it.setForeground(Qt.GlobalColor.red)
                it.setToolTip("找不到公式檔")
            self.lst.addItem(it)
        self.lst.blockSignals(False)
        target = None
        for i in range(self.lst.count()):
            if self.lst.item(i).data(ROLE_ID) == select_id:
                target = i
        if target is None and self.lst.count():
            target = 0
        if target is not None:
            self.lst.setCurrentRow(target)
        else:
            self._show(None)

    def _on_select(self, cur, prev):
        if self._dirty and self._eid:
            self.save_current(quiet=True)
        self._show(cur.data(ROLE_ID) if cur else None)

    def _set_editor_enabled(self, on):
        for w in (self.txt_title, self.txt_notes, self.txt_latex, self.btn_save, self.btn_load,
                  self.btn_delete, self.btn_auto_tex, self.btn_builder):
            w.setEnabled(on)

    def _show(self, eid):
        self._eid = eid
        e = self.lib.get(eid) if eid else None
        self._set_editor_enabled(e is not None)
        for w in (self.txt_title, self.txt_notes, self.txt_latex):
            w.blockSignals(True)
        if e is None:
            self.txt_title.setText("")
            self.txt_notes.setPlainText("")
            self.txt_latex.setPlainText("")
            self.lbl_info.setText("公式庫是空的：可用「加入目前公式」或「從檔案加入…」，"
                                  "或在公式產生器儲存時勾選「加入公式庫」。")
            self.img.set_latex("")
        else:
            self.txt_title.setText(e.get("title", ""))
            self.txt_notes.setPlainText(e.get("notes", ""))
            self.txt_latex.setPlainText(e.get("latex", ""))
            path = self.lib.resolve(e)
            exists = os.path.exists(path)
            units = e.get("units") or {}
            params = "、".join(f"{k}[{v}]" if v else k for k, v in units.items())
            spec = self._spec_of(e) if exists else None
            self.lbl_info.setText(
                f"函式：{e['func']}　　檔案：{path}{'' if exists else '（找不到！）'}\n"
                f"參數：{params or '—'}\n"
                f"建立：{e.get('created', '')}　更新：{e.get('updated', '')}"
                + ("　　（由公式產生器建立）" if spec else ""))
            self.btn_load.setEnabled(exists)
            self.btn_builder.setEnabled(spec is not None)
            self.btn_auto_tex.setEnabled(spec is not None)
            self._update_preview()
        for w in (self.txt_title, self.txt_notes, self.txt_latex):
            w.blockSignals(False)
        self._dirty = False
        self.btn_save.setEnabled(False)

    def _spec_of(self, e):
        try:
            spec = cg.load_spec(self.lib.resolve(e))
        except Exception:
            return None
        if spec and e["func"] in (spec.get("name"), spec.get("name", "") + "_abs"):
            return spec
        return None

    # ------------------------------------------------------------------ 編輯
    def _mark_dirty(self, *_):
        if self._eid:
            self._dirty = True
            self.btn_save.setEnabled(True)

    def _on_edit_latex(self):
        self._mark_dirty()
        self._preview_timer.start()

    def _update_preview(self):
        err = self.img.set_latex(self.txt_latex.toPlainText())
        self.lbl_tex_err.setText(err if err and "無法" in err else "")

    def rebuild_latex(self):
        e = self.lib.get(self._eid)
        if e is None:
            return
        tex, _ = auto_latex(self.lib.resolve(e), e["func"])
        self.txt_latex.setPlainText(tex)

    def save_current(self, quiet=False):
        e = self.lib.get(self._eid)
        if e is None:
            return
        latex = self.txt_latex.toPlainText()
        err = check_latex(latex)
        if err and not quiet:
            if QMessageBox.question(self, "LaTeX 有誤", f"{err}\n\n仍要儲存嗎？") != \
                    QMessageBox.StandardButton.Yes:
                return
        self.lib.update(e["id"], title=self.txt_title.text().strip() or e["func"],
                        notes=self.txt_notes.toPlainText(), latex=latex)
        self._dirty = False
        self.btn_save.setEnabled(False)
        self.refresh_list(select_id=e["id"])
        self.library_changed.emit()

    def _on_copy_toggled(self, on):
        self.lib.options["copy_files"] = bool(on)
        self.lib.save()

    # ------------------------------------------------------------------ 新增 / 刪除 / 載入
    def add_entry(self, path, func, title=None, notes="", latex=None, source="file", select=True):
        """供外部（主視窗、公式產生器）呼叫；同檔同函式已存在時更新該筆"""
        try:
            _, funcs = load_formula_module(path)
            if func not in funcs:
                raise KeyError(f"檔案中沒有函式 {func}")
            mod, _ = load_formula_module(path)
            names = param_names(funcs[func])
            units = get_units(mod, func, names)
        except Exception as e:
            QMessageBox.critical(self, "無法加入", str(e))
            return None
        if latex is None:
            latex, _ = auto_latex(path, func)
        e = self.lib.upsert(path, func, title=title, latex=latex, notes=notes,
                            units=units, source=source)
        if select:
            self.txt_search.setText("")
            self.refresh_list(select_id=e["id"])
        self.library_changed.emit()
        return e

    def add_current(self):
        cur = self.current_provider() if self.current_provider else None
        if not cur:
            QMessageBox.information(self, "沒有公式", "主視窗目前沒有載入公式")
            return
        path, func = cur
        title, ok = QInputDialog.getText(self, "加入公式庫", "標題：", text=func)
        if ok:
            self.add_entry(path, func, title=title.strip() or func)

    def add_from_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "選擇公式檔", "", "Python (*.py)")
        if not path:
            return
        try:
            _, funcs = load_formula_module(path)
        except Exception as e:
            QMessageBox.critical(self, "讀取失敗", str(e))
            return
        if not funcs:
            QMessageBox.warning(self, "沒有函式", "檔案中找不到可擬合的函式")
            return
        items = ["（全部函式）"] + list(funcs)
        choice, ok = QInputDialog.getItem(self, "選擇函式", "要加入哪個函式？", items, 0, False)
        if not ok:
            return
        targets = list(funcs) if choice == items[0] else [choice]
        for f in targets:
            self.add_entry(path, f, select=(f == targets[-1]))

    def delete_selected(self):
        e = self.lib.get(self._eid)
        if e is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("刪除公式")
        box.setText(f"從公式庫刪除「{e.get('title') or e['func']}」？")
        chk = QCheckBox("一併刪除公式庫資料夾中的公式檔（沒有其他項目使用時）")
        box.setCheckBox(chk)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self.lib.remove(e["id"], delete_file=chk.isChecked())
        self._eid = None
        self._dirty = False
        self.refresh_list()
        self.library_changed.emit()

    def load_selected(self):
        e = self.lib.get(self._eid)
        if e is None:
            return
        if self._dirty:
            self.save_current(quiet=True)
        path = self.lib.resolve(e)
        if not os.path.exists(path):
            QMessageBox.critical(self, "找不到檔案", path)
            return
        self.load_requested.emit(path, e["func"])

    def open_builder(self):
        e = self.lib.get(self._eid)
        spec = self._spec_of(e) if e else None
        if spec:
            self.open_in_builder.emit(spec)

    def select_entry(self, path, func):
        e = self.lib.find(path, func) if path else None
        if e:
            self.txt_search.setText("")
            self.refresh_list(select_id=e["id"])
