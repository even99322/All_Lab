"""Data Transfer window (licence feature "migrate"; Settings > About > Licences...).

Moves history from an old LabLogViewer data folder into the one this program uses,
matching measurement files by content (app/core/migrate.py). The Viewers are closed
first; afterwards LabLogViewer restarts, so no record still held in memory can
overwrite the transferred data."""

from __future__ import annotations

import locale
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from app import __version__
from app.core import migrate as core

TEXT = {
    "en": {
        "title": "Data Transfer", "lang": "中文",
        "intro": "Moves your stars, Tags, comments, Marks, views and YIG sessions from an old LabLogViewer "
                 "data folder into a new one. Files are matched by their content, so records follow files "
                 "that were moved. Measurement files and the old folder are only read.",
        "close_first": "Migrating closes the Viewer windows; LabLogViewer then restarts by itself.",
        "source": "1. Old data folder", "target": "2. New data folder (the LabLogViewer you will use)",
        "roots": "3. Where the measurement files are now (the folder you open as the database)",
        "browse": "Choose…", "default": "This computer's data folder", "add": "Add…", "remove": "Remove",
        "compare": "Compare", "migrate": "Migrate", "undo": "Undo last migration",
        "cancel": "Stop", "report": "Open report",
        "prefer_old": "When both folders have a record for the same file, use the old one",
        "keep_unmatched": "Keep records whose file was not found (under the old path)",
        "settings": "Settings", "settings_fill": "Fill in only what the new folder is missing",
        "settings_old": "Use the old settings", "settings_skip": "Do not copy settings",
        "col_old": "Old path", "col_records": "Records", "col_match": "Match", "col_new": "New path", "col_note": "Note",
        "unchanged": "unchanged", "exact": "same content", "guess": "same name and size",
        "missing": "not found",
        "counts": "{exact} same content · {guess} name and size (check and tick) · {manual} chosen by hand · "
                  "{unchanged} unchanged · {missing} not found",
        "no_source": "Choose the old data folder.", "no_target": "Choose the new data folder.",
        "same": "The old and the new data folder are the same folder.",
        "records": "Records in the old folder: {items}", "none": "no records",
        "confirm": "The data folder is backed up first; you can undo.\n\nThe Viewer windows close now and LabLogViewer restarts after the transfer. Continue?",
        "restarting": "Done. LabLogViewer restarts now so that every window shows the transferred records.",
        "done": "Done. {files} files re-linked.\n{added}\n\nConflicts: {conflicts}. Skipped: {skipped}.\n"
                "The report and the backup are in:\n{backup}",
        "undo_confirm": "Put the new data folder back as it was before the migration of {name}?\n"
                        "Changes made after that migration are lost.",
        "undone": "Undone.", "no_backup": "There is no migration to undo.",
        "working": "Comparing… {done} / {total}", "error": "Could not migrate",
        "manual": "Choose the new file by hand…", "manual_clear": "Undo manual choice", "manual_status": "chosen by hand",
        "manual_title": "Pair these two files", "manual_pick": "Choose the file these records belong to now",
        "manual_old": "Old file", "manual_new": "New file", "manual_result": "Comparison",
        "same content": "Same content: every record can follow.",
        "same layout": "Same measurement layout (channels and sweep sizes); the values differ. "
                       "Every record can follow; check that it is the measurement you mean.",
        "different": "The files differ too much.",
        "manual_kinds": "Records to move:",
        "disclaimer": "Disclaimer: these files are clearly different ({reason}). The records will be attached "
                      "to a different measurement, which can mislead you or others later. Only stars, Tags and "
                      "comments may follow; Marks, views, 3D views and YIG sessions depend on the data's channels "
                      "and axes and stay with the old path. This pairing is your own decision and your own "
                      "responsibility; the report marks it as chosen by hand.",
        "accept": "I understand, and I take responsibility for this pairing",
        "blocked": "depends on the data's channels and axes",
        "select_row": "Select a row in the list first.", "ok": "Pair them", "cancel_dialog": "Cancel",
    },
    "zh": {
        "title": "資料轉移", "lang": "English",
        "intro": "把舊的 LabLogViewer 資料夾裡的星號、Tags、註解、Mark、視圖與 YIG 工作階段轉到新的資料夾。"
                 "以檔案內容比對，所以檔案搬家後紀錄仍會跟著檔案。量測檔與舊資料夾只會被讀取。",
        "close_first": "轉移時會關閉 Viewer 視窗，完成後 LabLogViewer 會自動重新開啟。",
        "source": "1. 舊的資料夾", "target": "2. 新的資料夾（之後要使用的 LabLogViewer）",
        "roots": "3. 量測檔現在所在的位置（在程式裡開啟為資料庫的那個資料夾）",
        "browse": "選擇⋯", "default": "這台電腦的資料夾", "add": "加入⋯", "remove": "移除",
        "compare": "比對", "migrate": "開始轉移", "undo": "復原上一次轉移",
        "cancel": "停止", "report": "開啟報告",
        "prefer_old": "兩邊都有同一個檔案的紀錄時，使用舊的",
        "keep_unmatched": "找不到檔案的紀錄也保留（留在舊路徑下）",
        "settings": "設定", "settings_fill": "只補新資料夾缺少的設定",
        "settings_old": "使用舊的設定", "settings_skip": "不複製設定",
        "col_old": "舊路徑", "col_records": "紀錄", "col_match": "比對結果", "col_new": "新路徑", "col_note": "備註",
        "unchanged": "未變動", "exact": "內容相同", "guess": "檔名與大小相同",
        "missing": "找不到",
        "counts": "{exact} 個內容相同 · {guess} 個檔名與大小相同（請確認後勾選） · {manual} 個手動指定 · "
                  "{unchanged} 個未變動 · {missing} 個找不到",
        "no_source": "請選擇舊的資料夾。", "no_target": "請選擇新的資料夾。",
        "same": "新舊資料夾是同一個資料夾。",
        "records": "舊資料夾中的紀錄：{items}", "none": "沒有紀錄",
        "confirm": "會先備份資料夾，之後可以復原。\n\n現在會關閉 Viewer 視窗，轉移完成後 LabLogViewer 會重新開啟。要繼續嗎？",
        "restarting": "完成。LabLogViewer 現在重新開啟，讓每個視窗顯示轉移後的紀錄。",
        "done": "完成。重新對應了 {files} 個檔案。\n{added}\n\n衝突：{conflicts}。略過：{skipped}。\n"
                "報告與備份在：\n{backup}",
        "undo_confirm": "把新的資料夾還原成 {name} 那次轉移之前的樣子？\n那次轉移之後的變更會遺失。",
        "undone": "已復原。", "no_backup": "沒有可以復原的轉移。",
        "working": "比對中⋯ {done} / {total}", "error": "無法轉移",
        "manual": "手動指定新檔案⋯", "manual_clear": "取消手動指定", "manual_status": "手動指定",
        "manual_title": "手動對應兩個檔案", "manual_pick": "選擇這些紀錄現在所屬的檔案",
        "manual_old": "舊檔案", "manual_new": "新檔案", "manual_result": "比對結果",
        "same content": "內容相同：所有紀錄都可以轉移。",
        "same layout": "量測結構相同（通道與掃描大小一致），但數值不同。所有紀錄都可以轉移；請確認這是你要的那筆量測。",
        "different": "兩個檔案差異過大。",
        "manual_kinds": "要轉移的紀錄：",
        "disclaimer": "免責聲明：這兩個檔案明顯不同（{reason}）。轉移後，這些紀錄會掛在另一筆量測上，"
                      "日後可能讓你或其他人誤解。只能轉移星號、Tags 與註解；Mark、視圖、3D 視圖與 YIG 工作階段"
                      "依賴資料的通道與座標軸，會留在舊路徑下。這個對應是你自行決定的，結果由你自行負責；"
                      "報告中會標記為手動指定。",
        "accept": "我了解，並對這個對應自行負責",
        "blocked": "依賴資料的通道與座標軸",
        "select_row": "請先在清單中選一列。", "ok": "確定對應", "cancel_dialog": "取消",
    },
}
RECORD_ZH = {
    "view": "視圖", "marks": "Mark", "overlays": "疊圖", "named views": "具名視圖", "axis presets": "座標軸預設",
    "comment": "註解", "3D view": "3D 視圖", "star": "星號", "tags": "Tags", "YIG session": "YIG 工作階段",
}
STATUS_COLORS = {"exact": "#34C759", "guess": "#FF9F0A", "unchanged": "#8E8E93", "missing": "#FF3B30",
                 "manual": "#0A84FF"}
SIMILARITY_SHORT = {"en": {"same content": "same content", "same layout": "same layout", "different": "different"},
                    "zh": {"same content": "內容相同", "same layout": "結構相同", "different": "差異過大"}}
REASON_ZH = {
    "identical content (SHA-256)": "內容完全相同（SHA-256）",
    "same channels and sweep sizes; the measured values differ": "通道與掃描大小相同，量測數值不同",
    "different channels": "通道不同", "different data sets": "資料集不同", "different sweep sizes": "掃描大小不同",
    "different layout": "結構不同", "one of the files cannot be read as HDF5": "其中一個檔案無法以 HDF5 讀取",
    "the old file no longer exists, so only its fingerprint could be compared, and the content differs":
        "舊檔案已不存在，只能比對指紋，而內容不同",
    "the old file no longer exists and has no fingerprint; nothing could be compared":
        "舊檔案已不存在，也沒有指紋，無法比對",
}


class ManualDialog(QDialog):
    """Shows how alike two files are; for clearly different files a disclaimer must be
    accepted and only stars, Tags and comments can be chosen."""

    def __init__(self, window: "MigratorWindow", pair: core.Pair, new_path: str, similarity: str, reason: str):
        super().__init__(window)
        t = window.t
        self.setWindowTitle(t("manual_title"))
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        for key, value in (("manual_old", pair.old.identity), ("manual_new", new_path)):
            title = QLabel(t(key))
            title.setStyleSheet("font-weight: 700;")
            layout.addWidget(title)
            label = QLabel(str(value))
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
        shown_reason = window.reason(reason)
        result = QLabel(f"<b>{t('manual_result')}</b>: {t(similarity)} ({shown_reason})")
        result.setWordWrap(True)
        result.setStyleSheet(f"color: {'#FF3B30' if similarity == 'different' else '#34C759'};")
        layout.addWidget(result)
        layout.addWidget(QLabel(t("manual_kinds")))
        self.kind_boxes: dict[str, QCheckBox] = {}
        for kind in sorted(pair.old.records):
            allowed = similarity != "different" or kind in core.SAFE_KINDS
            box = QCheckBox(window.record(kind) if allowed else f"{window.record(kind)} — {t('blocked')}")
            box.setChecked(allowed)
            box.setEnabled(allowed)
            self.kind_boxes[kind] = box
            layout.addWidget(box)
        self.accept_box = None
        if similarity == "different":
            warning = QLabel(t("disclaimer", reason=shown_reason))
            warning.setWordWrap(True)
            warning.setObjectName("migratorDisclaimer")
            warning.setStyleSheet("QLabel { border: 1px solid #FF9F0A; border-radius: 8px; padding: 10px; }")
            layout.addWidget(warning)
            self.accept_box = QCheckBox(t("accept"))
            self.accept_box.setObjectName("migratorAccept")
            layout.addWidget(self.accept_box)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(t("ok"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("cancel_dialog"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for box in (*self.kind_boxes.values(), *([self.accept_box] if self.accept_box else [])):
            box.toggled.connect(self._update)
        self._update()

    def kinds(self) -> set[str]:
        return {kind for kind, box in self.kind_boxes.items() if box.isChecked() and box.isEnabled()}

    def ok_enabled(self) -> bool:
        return self.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()

    def _update(self) -> None:
        ok = bool(self.kinds()) and (self.accept_box is None or self.accept_box.isChecked())
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)


class CompareWorker(QThread):
    progress = Signal(int, int)
    finished_plan = Signal(object)
    failed = Signal(str)

    def __init__(self, source, target, roots):
        super().__init__()
        self.source, self.target, self.roots = source, target, roots
        self.stopped = False

    def run(self):
        try:
            plan = core.compare(self.source, self.target, self.roots, progress=self.progress.emit,
                                cancel=lambda: self.stopped)
            self.finished_plan.emit(plan)
        except Exception as error:                 # shown to the user, nothing was written
            self.failed.emit(str(error))


def restart_program() -> None:
    """Start LabLogViewer again and quit this one (records in memory are not written)."""
    from PySide6.QtCore import QProcess

    from app.core.external_state import restrict_writes
    from app.gui.three_d_process import child_command

    restrict_writes({"session_lifecycle.json"})      # nothing may overwrite the transferred records
    program, arguments = child_command([])
    QProcess.startDetached(program, arguments)
    QApplication.instance().quit()


def close_viewers() -> None:
    """Close the Viewers (they save their Marks and views first)."""
    from app.gui.main_window import MainWindow

    for window in QApplication.topLevelWidgets():
        if isinstance(window, MainWindow) and window.isVisible() and not getattr(window, "_network_mirror", False):
            window.close()
    QApplication.processEvents()


class MigratorWindow(QMainWindow):
    def __init__(self, lang: str | None = None, target: str | None = None, roots: list[str] | None = None,
                 restart=restart_program):
        super().__init__()
        self.lang = lang or ("zh" if (locale.getlocale()[0] or "").startswith("zh") else "en")
        self.restart = restart
        self.plan: core.Plan | None = None
        self.worker: CompareWorker | None = None
        self.last_report: core.Report | None = None
        self.resize(1080, 760)
        central = QWidget()
        layout = QVBoxLayout(central)
        top = QHBoxLayout()
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        top.addWidget(self.intro, 1)
        self.lang_button = QPushButton()
        self.lang_button.clicked.connect(self.toggle_language)
        top.addWidget(self.lang_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        self.warning = QLabel()
        self.warning.setStyleSheet("color: #FF9F0A; font-weight: 600;")
        layout.addWidget(self.warning)

        self.source_box = QGroupBox()
        row = QHBoxLayout(self.source_box)
        self.source_edit = QLineEdit()
        self.source_edit.setObjectName("migratorSource")
        self.source_edit.editingFinished.connect(self.describe_source)
        self.source_browse = QPushButton()
        self.source_browse.clicked.connect(lambda: self.choose(self.source_edit, self.describe_source))
        row.addWidget(self.source_edit, 1)
        row.addWidget(self.source_browse)
        self.source_info = QLabel()
        self.source_info.setWordWrap(True)
        source_column = QVBoxLayout()
        source_column.addWidget(self.source_box)
        source_column.addWidget(self.source_info)
        layout.addLayout(source_column)

        self.target_box = QGroupBox()
        row = QHBoxLayout(self.target_box)
        self.target_edit = QLineEdit()
        self.target_edit.setObjectName("migratorTarget")
        self.target_browse = QPushButton()
        self.target_browse.clicked.connect(lambda: self.choose(self.target_edit))
        self.target_default = QPushButton()
        self.target_default.clicked.connect(lambda: self.target_edit.setText(str(core.default_data_folder())))
        row.addWidget(self.target_edit, 1)
        row.addWidget(self.target_browse)
        row.addWidget(self.target_default)
        if target is None:
            from app.core.data_location import data_root

            target = str(data_root())
        self.target_edit.setText(str(target))          # always this program's data folder
        self.target_edit.setReadOnly(True)
        self.target_browse.hide()
        self.target_default.hide()
        layout.addWidget(self.target_box)

        self.roots_box = QGroupBox()
        row = QHBoxLayout(self.roots_box)
        self.roots = QListWidget()
        self.roots.setObjectName("migratorRoots")
        self.roots.setMaximumHeight(70)
        row.addWidget(self.roots, 1)
        buttons = QVBoxLayout()
        self.add_root = QPushButton()
        self.add_root.clicked.connect(self.choose_root)
        self.remove_root = QPushButton()
        self.remove_root.clicked.connect(lambda: [self.roots.takeItem(self.roots.row(i)) for i in self.roots.selectedItems()])
        buttons.addWidget(self.add_root)
        buttons.addWidget(self.remove_root)
        row.addLayout(buttons)
        layout.addWidget(self.roots_box)

        actions = QHBoxLayout()
        self.compare_button = QPushButton()
        self.compare_button.setObjectName("migratorCompare")
        self.compare_button.clicked.connect(self.compare)
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self.stop)
        self.cancel_button.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        actions.addWidget(self.compare_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.progress, 1)
        layout.addLayout(actions)

        self.table = QTreeWidget()
        self.table.setObjectName("migratorTable")
        self.table.setColumnCount(5)
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)      # keep the file name visible
        self.table.itemChanged.connect(self._ticked)
        self.table.itemDoubleClicked.connect(lambda item, _column: self.choose_manual(item))
        layout.addWidget(self.table, 1)
        counts_row = QHBoxLayout()
        self.counts = QLabel()
        counts_row.addWidget(self.counts, 1)
        self.manual_button = QPushButton()
        self.manual_button.setObjectName("migratorManual")
        self.manual_button.clicked.connect(lambda: self.choose_manual(self.table.currentItem()))
        self.manual_clear = QPushButton()
        self.manual_clear.clicked.connect(self.clear_manual)
        counts_row.addWidget(self.manual_button)
        counts_row.addWidget(self.manual_clear)
        layout.addLayout(counts_row)

        options = QHBoxLayout()
        self.prefer_old = QCheckBox()
        self.keep_unmatched = QCheckBox()
        self.keep_unmatched.setChecked(True)
        self.settings_label = QLabel()
        self.settings_mode = QComboBox()
        option_column = QVBoxLayout()
        option_column.addWidget(self.prefer_old)
        option_column.addWidget(self.keep_unmatched)
        settings_row = QHBoxLayout()
        settings_row.addWidget(self.settings_label)
        settings_row.addWidget(self.settings_mode)
        settings_row.addStretch(1)
        option_column.addLayout(settings_row)
        options.addLayout(option_column, 1)
        self.undo_button = QPushButton()
        self.undo_button.clicked.connect(self.undo)
        self.report_button = QPushButton()
        self.report_button.clicked.connect(self.open_report)
        self.report_button.setEnabled(False)
        self.migrate_button = QPushButton()
        self.migrate_button.setObjectName("migratorMigrate")
        self.migrate_button.setEnabled(False)
        self.migrate_button.clicked.connect(self.migrate)
        self.migrate_button.setStyleSheet("QPushButton { font-weight: 700; padding: 6px 18px; }")
        for button in (self.undo_button, self.report_button, self.migrate_button):
            options.addWidget(button, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(options)
        self.setCentralWidget(central)
        for root in roots or []:
            self.roots.addItem(root)
        self.retranslate()

    # -- text --------------------------------------------------------------------------------------
    def t(self, key: str, **values) -> str:
        return TEXT[self.lang][key].format(**values)

    def record(self, label: str) -> str:
        return RECORD_ZH.get(label, label) if self.lang == "zh" else label

    def reason(self, text: str) -> str:
        if self.lang != "zh":
            return text
        if text in REASON_ZH:
            return REASON_ZH[text]
        return "、".join(REASON_ZH.get(part, part) for part in text.split(", "))

    def toggle_language(self) -> None:
        self.lang = "en" if self.lang == "zh" else "zh"
        self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(f"{self.t('title')} {__version__}")
        self.lang_button.setText(self.t("lang"))
        self.intro.setText(self.t("intro"))
        self.warning.setText(self.t("close_first"))
        self.source_box.setTitle(self.t("source"))
        self.target_box.setTitle(self.t("target"))
        self.roots_box.setTitle(self.t("roots"))
        for button, key in ((self.source_browse, "browse"), (self.target_browse, "browse"),
                            (self.target_default, "default"), (self.add_root, "add"), (self.remove_root, "remove"),
                            (self.compare_button, "compare"), (self.cancel_button, "cancel"),
                            (self.migrate_button, "migrate"), (self.undo_button, "undo"),
                            (self.report_button, "report"), (self.manual_button, "manual"),
                            (self.manual_clear, "manual_clear")):
            button.setText(self.t(key))
        self.prefer_old.setText(self.t("prefer_old"))
        self.keep_unmatched.setText(self.t("keep_unmatched"))
        self.settings_label.setText(self.t("settings"))
        current = self.settings_mode.currentIndex()
        self.settings_mode.clear()
        for key in ("settings_fill", "settings_old", "settings_skip"):
            self.settings_mode.addItem(self.t(key))
        self.settings_mode.setCurrentIndex(max(0, current))
        self.table.setHeaderLabels([self.t(k) for k in ("col_old", "col_records", "col_match", "col_new", "col_note")])
        self.describe_source()
        if self.plan is not None:
            self.show_plan(self.plan)

    # -- choosing ----------------------------------------------------------------------------------
    def choose(self, edit: QLineEdit, then=None) -> None:
        path = QFileDialog.getExistingDirectory(self, "", edit.text() or str(Path.home()))
        if path:
            edit.setText(path)
            if then is not None:
                then()

    def choose_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "", str(Path.home()))
        if path and not self.roots.findItems(path, Qt.MatchFlag.MatchExactly):
            self.roots.addItem(path)

    def describe_source(self) -> None:
        text = self.source_edit.text().strip()
        if not text:
            self.source_info.setText("")
            return
        try:
            counts = core.summary(core.open_folder(text))
        except core.MigrationError as error:
            self.source_info.setText(str(error))
            return
        items = ", ".join(f"{self.record(k)} {v}" for k, v in sorted(counts.items())) or self.t("none")
        self.source_info.setText(self.t("records", items=items))

    def folders(self):
        source, target = self.source_edit.text().strip(), self.target_edit.text().strip()
        if not source:
            raise core.MigrationError(self.t("no_source"))
        if not target:
            raise core.MigrationError(self.t("no_target"))
        if Path(source).expanduser().resolve() == Path(target).expanduser().resolve():
            raise core.MigrationError(self.t("same"))
        return core.open_folder(source), core.open_folder(target, create=True)

    # -- compare -----------------------------------------------------------------------------------
    def compare(self) -> None:
        try:
            source, target = self.folders()
        except core.MigrationError as error:
            QMessageBox.warning(self, self.t("title"), str(error))
            return
        roots = [self.roots.item(i).text() for i in range(self.roots.count())]
        self.worker = CompareWorker(source, target, roots)
        self.worker.progress.connect(self._progress)
        self.worker.finished_plan.connect(self._compared)
        self.worker.failed.connect(lambda message: QMessageBox.warning(self, self.t("title"), message))
        self.worker.finished.connect(lambda: (self.compare_button.setEnabled(True), self.cancel_button.setEnabled(False)))
        self.compare_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.migrate_button.setEnabled(False)
        self.worker.start()

    def stop(self) -> None:
        if self.worker is not None:
            self.worker.stopped = True

    def _progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.progress.setFormat(self.t("working", done=done, total=total))

    def _compared(self, plan: core.Plan) -> None:
        self.plan = plan
        self.show_plan(plan)
        self.migrate_button.setEnabled(bool(plan.pairs) or True)

    def show_plan(self, plan: core.Plan) -> None:
        self.table.blockSignals(True)
        self.table.clear()
        for pair in plan.pairs:
            records = sorted(pair.old.records if pair.kinds is None else pair.kinds)
            status = (f"{self.t('manual_status')} · {SIMILARITY_SHORT[self.lang][pair.similarity]}"
                      if pair.status == "manual" else self.t(pair.status))
            item = QTreeWidgetItem([pair.old.identity, ", ".join(self.record(r) for r in records), status,
                                    pair.new_path or "", self.reason(pair.note)])
            item.setData(0, Qt.ItemDataRole.UserRole, pair)
            item.setForeground(2, QColor(STATUS_COLORS[pair.status]))
            if pair.status in ("exact", "guess", "manual"):
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked if pair.selected else Qt.CheckState.Unchecked)
            item.setToolTip(0, pair.old.identity)
            item.setToolTip(3, pair.new_path or "")
            self.table.addTopLevelItem(item)
        self.table.blockSignals(False)
        counts = {k: 0 for k in ("exact", "guess", "manual", "unchanged", "missing")} | plan.counts()
        self.table.resizeColumnToContents(2)
        self.counts.setText(self.t("counts", **counts))

    def _ticked(self, item: QTreeWidgetItem, column: int) -> None:
        pair = item.data(0, Qt.ItemDataRole.UserRole)
        if pair is not None and column == 0:
            pair.selected = item.checkState(0) == Qt.CheckState.Checked

    # -- manual matches ------------------------------------------------------------------------------
    def choose_manual(self, item, new_path: str | None = None, dialog_answer=None) -> bool:
        """Pair the selected old file with a file picked by hand (``new_path`` / ``dialog_answer``
        stand in for the file and confirmation dialogs in tests)."""
        pair = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if pair is None:
            QMessageBox.information(self, self.t("title"), self.t("select_row"))
            return False
        if new_path is None:
            start = self.roots.item(0).text() if self.roots.count() else str(Path.home())
            new_path, _ = QFileDialog.getOpenFileName(self, self.t("manual_pick"), start, "HDF5 (*.hdf5 *.h5)")
            if not new_path:
                return False
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            similarity, reason = core.compare_files(pair.old, new_path)
        except core.MigrationError as error:
            QMessageBox.warning(self, self.t("title"), str(error))
            return False
        finally:
            QApplication.restoreOverrideCursor()
        dialog = ManualDialog(self, pair, new_path, similarity, reason)
        self.manual_dialog = dialog
        accepted = dialog_answer(dialog) if dialog_answer is not None else dialog.exec() == QDialog.DialogCode.Accepted
        if not accepted:
            return False
        core.set_manual(pair, new_path, dialog.kinds())
        self.show_plan(self.plan)
        return True

    def clear_manual(self) -> None:
        item = self.table.currentItem()
        pair = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if pair is not None:
            core.clear_manual(pair)
            self.show_plan(self.plan)

    # -- migrate / undo ----------------------------------------------------------------------------
    def options(self) -> core.Options:
        return core.Options(prefer_old=self.prefer_old.isChecked(), keep_unmatched=self.keep_unmatched.isChecked(),
                            settings=("fill", "old", "skip")[self.settings_mode.currentIndex()])

    def migrate(self, confirmed: bool = False) -> core.Report | None:
        if self.plan is None:
            return None
        if not confirmed and QMessageBox.question(self, self.t("title"), self.t("confirm")) \
                != QMessageBox.StandardButton.Yes:
            return None
        close_viewers()
        try:
            report = core.migrate(self.plan, self.options())
        except (core.MigrationError, OSError) as error:
            QMessageBox.critical(self, self.t("error"), str(error))
            return None
        self.last_report = report
        self.report_button.setEnabled(True)
        added = "\n".join(f"  {k}: {v}" for k, v in sorted(report.added.items()))
        if not confirmed:
            QMessageBox.information(self, self.t("title"), self.t(
                "done", files=report.moved_files, added=added, conflicts=len(report.conflicts),
                skipped=len(report.skipped), backup=report.backup) + "\n\n" + self.t("restarting"))
        if self.restart is not None:
            self.restart()
        return report

    def undo(self, confirmed: bool = False) -> bool:
        try:
            _source, target = self.folders()
        except core.MigrationError as error:
            QMessageBox.warning(self, self.t("title"), str(error))
            return False
        backups = core.list_backups(target)
        if not backups:
            QMessageBox.information(self, self.t("title"), self.t("no_backup"))
            return False
        if not confirmed and QMessageBox.question(self, self.t("title"), self.t("undo_confirm", name=backups[0].name)) \
                != QMessageBox.StandardButton.Yes:
            return False
        close_viewers()
        core.undo(target, backups[0])
        if not confirmed:
            QMessageBox.information(self, self.t("title"), self.t("undone") + "\n\n" + self.t("restarting"))
        if self.restart is not None:
            self.restart()
        return True

    def open_report(self) -> None:
        if self.last_report is None:
            return
        path = str(self.last_report.backup / "report.md")
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif os.name == "nt":
            os.startfile(path)                     # noqa: S606 - opens the report the user asked for
        else:
            subprocess.Popen(["xdg-open", path])


def open_migrate(localizer, parent=None) -> MigratorWindow:
    """The Data Transfer window, in the program's language, databases open in the Browser as default."""
    from app.gui.browser_window import BrowserWindow

    roots = []
    for window in QApplication.topLevelWidgets():
        if isinstance(window, BrowserWindow) and getattr(window, "scanner_root", None):
            roots.append(str(window.scanner_root))
    lang = "zh" if str(localizer.language).startswith("zh") else "en"
    window = MigratorWindow(lang, roots=sorted(set(roots)))
    window.show()
    window.raise_()
    return window
