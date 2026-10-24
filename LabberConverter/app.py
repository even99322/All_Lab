"""Bilingual desktop UI for Labber Converter."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from dataclasses import dataclass, field
from threading import Event

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QThread, QTimer, QUrl, Signal, QStandardPaths
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QIcon, QImage,
                          QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow,
                               QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy,
                               QSlider, QVBoxLayout, QWidget, QAbstractItemView, QScrollArea,
                               QLayout, QDialog, QCheckBox, QMessageBox)

from converter import VERSION, convert_batch, reindex, initialize_database
from glass_material import GlassMaterial, GlassRenderer, LIGHT_STYLE, DARK_STYLE, paint_glass, _pixels
from palette import APP_DARK, APP_LIGHT

STRINGS = {
    "title": ("Labber 轉換工具", "Labber Converter"),
    "subtitle": ("把新資料帶回熟悉的 Labber。", "Bring new data back to Labber."),
    "input": ("輸入資料", "Input data"),
    "input_hint": ("支援 .nc、ZIP、既有 Labber 檔案與資料夾", "Add .nc, ZIP, existing Labber files, or folders"),
    "add_files": ("加入檔案", "Add files"), "add_folder": ("加入資料夾", "Add folder"),
    "remove": ("移除所選", "Remove selected"), "clear": ("清空", "Clear"),
    "drop": ("拖曳資料到這裡", "Drop your data here"),
    "drop_hint": ("可一次加入多個檔案、ZIP 或資料夾", "Add multiple files, ZIP archives, or folders at once"),
    "output": ("輸出資料庫主資料夾", "Output database root"),
    "output_hint": ("Database.hdf5 和資料子資料夾會放在此層。", "Database.hdf5 and the data folders belong at this level."),
    "output_placeholder": ("選擇輸出資料夾…", "Choose an output folder…"),
    "browse": ("瀏覽…", "Browse…"), "project": ("專案前綴（選填）", "Project prefix (optional)"),
    "new_database": ("建立新資料庫…", "Create a new database…"),
    "new_database_title": ("選擇或新增資料庫主資料夾", "Choose or create the database root folder"),
    "initializing": ("正在建立並檢查 Database.hdf5…", "Creating and checking Database.hdf5…"),
    "database_ready": ("資料庫已就緒：{path}", "Database ready: {path}"),
    "database_file": ("已確認資料庫檔案：{path}", "Verified database file: {path}"),
    "root_placeholder": ("主資料夾", "Database root"),
    "tree_layout": ("{root}/\n├─ Database.hdf5\n└─ 2026/\n   └─ 10/\n      └─ Data_1021/\n         └─ a.hdf5", "{root}/\n├─ Database.hdf5\n└─ 2026/\n   └─ 10/\n      └─ Data_1021/\n         └─ a.hdf5"),
    "project_placeholder": ("留白依月／日期分類", "Leave blank for month/date labels"),
    "local_cache": ("本機暫存與傳回驗證", "Local cache and verified transfer"),
    "local_cache_tip": ("先檢查來源是否穩定，再複製至本機轉換；傳回後重新核對校驗值。可恢復的 I/O 錯誤最多嘗試 3 次。",
                        "Check source stability before caching locally. Convert locally and verify the transferred output. Recoverable I/O operations get at most 3 attempts."),
    "resume": ("續轉：驗證並略過已完成項目", "Resume verified completed files"),
    "resume_tip": ("核對來源、輸出校驗值與轉換設定。關閉時仍會保存進度，但重新匯入會保留同名檔案三選一。",
                   "Match source/output checksums and conversion settings. Progress is always saved; turn this off to import again using the filename conflict dialog."),
    "phase_checking": ("檢查來源是否仍在寫入 · {name}", "Checking source stability · {name}"),
    "phase_caching": ("建立本機暫存 · {name}", "Caching locally · {name}"),
    "phase_converting": ("本機轉換與驗證 · {name}", "Converting and validating locally · {name}"),
    "phase_transferring": ("傳回並驗證輸出 · {name}", "Transferring and verifying output · {name}"),
    "phase_retry": ("I/O 暫時失敗，重試 {attempt}/{total} · {name}", "Temporary I/O failure; retry {attempt}/{total} · {name}"),
    "resumed_file": ("↷ {name} · 來源與輸出驗證一致，已續轉略過", "↷ {name} · source/output verified; resumed without conversion"),
    "pending_file": ("◷ {name} · 輸出已驗證；分類待同步，請使用續轉", "◷ {name} · output verified; labels pending, use Resume"),
    "source_active": ("來源仍在寫入或標示量測中，已拒絕處理；請停止量測後重試。", "Source is changing or reports an active measurement. Processing refused; retry after measurement stops."),
    "native_scan_path_warning": ("  注意：日期資料夾下仍有額外子資料夾，原廠 Labber 可能不會掃描此位置。", "  Warning: an extra folder remains below the date folder; native Labber may not scan this location."),
    "layout": ("自動整理成 Labber 資料庫", "Organized for Labber automatically"),
    "layout_hint": ("結構範例；日期依來源資料決定。\n保留來源子資料夾，並同步 Labber 分類。\n請在 Labber 開啟最上層主資料夾。",
                    "Example layout; dates follow the source.\nKeeps source folders and syncs Labber labels.\nOpen the top-level database root in Labber."),
    "convert": ("開始轉換", "Convert data"), "cancel": ("停止後續轉換", "Stop after this file"),
    "open_output": ("開啟輸出資料夾", "Open output folder"),
    "index": ("同步既有資料庫分類", "Sync existing database labels"),
    "index_tip": ("掃描輸出資料夾內既有的 Labber 檔案，合併分類，並備份原清單。",
                  "Scan existing Labber files in the output folder, merge labels, and back up the original database."),
    "material": ("玻璃材質", "Glass material"), "thickness": ("厚度", "Thickness"),
    "frost": ("霧度", "Frost"), "ready": ("準備就緒", "Ready"),
    "queue": ("{count} 個輸入項目 · 資料夾會遞迴掃描", "{count} inputs · folders are scanned recursively"),
    "results": ("轉換紀錄", "Activity"),
    "result_empty": ("轉換進度與結果會顯示在這裡。", "Progress and results will appear here."),
    "working": ("正在轉換 {done}/{total} · {name}", "Converting {done}/{total} · {name}"),
    "scanning": ("正在掃描輸入資料…", "Scanning inputs…"),
    "indexing": ("正在同步資料庫分類…", "Syncing database labels…"),
    "done": ("完成 {ok} · 續轉略過 {resumed} · 跳過 {skipped} · 待同步 {pending} · 失敗 {errors}", "Done {ok} · resumed {resumed} · skipped {skipped} · pending {pending} · failed {errors}"),
    "stopped": ("已停止 · 完成 {ok} · 續轉 {resumed} · 待同步 {pending} · 失敗 {errors}", "Stopped · done {ok} · resumed {resumed} · pending {pending} · failed {errors}"),
    "conflict_title": ("輸出檔案已存在", "Output file already exists"),
    "conflict_message": ("這個位置已有相同檔名，請選擇如何處理。", "A file with this name already exists. Choose what to do."),
    "conflict_output": ("既有檔案", "Existing output"),
    "conflict_source": ("正在匯入", "Incoming source"),
    "conflict_keep_name": ("同時保留的新檔名：{name}", "Keep both as: {name}"),
    "conflict_hint": ("取代會等新檔轉換與驗證完成後才更新原檔。\n同時保留會使用同資料夾的最大底線編號加 1。",
                      "Replace updates the old file after conversion and validation.\nKeep both uses the highest existing suffix plus 1."),
    "replace": ("取代", "Replace"), "skip": ("不要轉換", "Skip this file"),
    "keep_both": ("同時保留", "Keep both"),
    "skipped_file": ("↷ {name} · 已跳過", "↷ {name} · skipped"),
    "not_convertible": ("↷ {name} · 不轉換：{why}", "↷ {name} · not converted: {why}"),
    "rebuild_title": ("同步資料庫分類", "Sync database labels"),
    "rebuild_ask": ("要不要順便移除「沒有任何檔案在用」的舊分類？\n\n例如以前匯入時留下、現在資料夾裡已經沒有的專案名稱（像 mirror）。"
                    "移除前會先備份 Database.hdf5。\n\n是：重建分類（只留現有檔案的分類）\n否：只加入新分類",
                    "Also remove labels that no file uses any more?\n\nFor example a project name left from an earlier import."
                    " Database.hdf5 is backed up first.\n\nYes: rebuild labels from the files\nNo: only add new labels"),
    "removed_labels": ("已移除沒有檔案的分類：{labels}", "Removed unused labels: {labels}"),
    "same_file": ("  來源和輸出是同一檔案，已保留原檔。", "  Source and output are the same file; the original was kept."),
    "replaced_file": ("  已取代同名輸出檔案。", "  Replaced the existing output."),
    "renamed_file": ("  已同時保留，使用新檔名。", "  Kept both with the new filename."),
    "index_done": ("已掃描 {files} 個檔案，資料庫共有 {labels} 個分類。", "Scanned {files} files; the database now has {labels} labels."),
    "need_input": ("請先加入檔案或資料夾。", "Add files or folders first."),
    "need_output": ("請先選擇輸出資料夾。", "Choose an output folder first."),
    "failure": ("處理失敗，請查看下方說明。", "Processing failed; see the details below."),
    "finishing": ("正在完成目前檔案，之後停止。", "Finishing the current file, then stopping."),
    "reload": ("在 Labber 選擇此輸出根目錄，並執行 Reload Database。",
               "Open this output root in Labber, then use Reload Database."),
    "ok_file": ("✓ {name} · {points} 個電流點 × {freq} 個頻率點", "✓ {name} · {points} sweep points × {freq} trace points"),
    "copied": ("✓ {name} · 已複製既有 Labber 檔案", "✓ {name} · existing Labber file copied"),
    "incomplete": ("  原量測未完成；已保留 {points}/{planned} 個點，沒有補造資料。",
                   "  Source run was incomplete; kept {points}/{planned} points without filling missing data."),
    "report": ("完整報告：{path}", "Full report: {path}"),
    "light": ("淺色", "Light"), "dark": ("深色", "Dark"), "system": ("跟隨系統", "System"),
    "theme_tip": ("主題配色", "Appearance"), "language_tip": ("介面語言", "Language"),
    "file_filter": ("量測資料 (*.nc *.zip *.hdf5 *.h5);;所有檔案 (*)", "Measurement data (*.nc *.zip *.hdf5 *.h5);;All files (*)"),
}


def load_settings(path):
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data,dict) else {}
    except (OSError, ValueError):
        return {}


class Worker(QObject):
    progress = Signal(int, int, str)
    result = Signal(object)
    failed = Signal(str)
    finished = Signal()
    conflict = Signal(object)
    activity = Signal(object)

    def __init__(self, inputs, output, project, index_only=False, initialize_only=False, local_cache=True, resume=False,
                 rebuild=False):
        super().__init__()
        self.rebuild = rebuild
        self.inputs, self.output, self.project, self.index_only = inputs, output, project, index_only
        self.initialize_only=initialize_only
        self.local_cache,self.resume=local_cache,resume

    def run(self):
        try:
            result = initialize_database(self.output) if self.initialize_only else reindex(self.output, self.rebuild) if self.index_only else convert_batch(
                self.inputs,self.output,project=self.project,
                progress=lambda a,b,c:self.progress.emit(a,b,c),
                cancel=lambda:QThread.currentThread().isInterruptionRequested(),
                on_conflict=self.ask_conflict,local_cache=self.local_cache,resume=self.resume,
                activity=self.activity.emit)
            self.result.emit(result)
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
        finally:
            self.finished.emit()

    def ask_conflict(self, source, destination, suggestion):
        request=ConflictRequest(source.label, destination, suggestion)
        self.conflict.emit(request)
        while not request.ready.wait(.1):
            if QThread.currentThread().isInterruptionRequested():
                return "cancel"
        return request.action


@dataclass
class ConflictRequest:
    source: str
    destination: Path
    suggestion: Path
    action: str = "skip"
    ready: Event = field(default_factory=Event)


class ConflictDialog(QDialog):
    def __init__(self, owner, request):
        super().__init__(owner)
        self.setObjectName("conflictDialog")
        self.setWindowTitle(owner.tr("conflict_title"))
        self.setMinimumWidth(560)
        self.resize(640,360)
        self.action="skip"  # Closing the window / Escape leaves the existing file intact.
        layout=QVBoxLayout(self)
        layout.setContentsMargins(26,24,26,24)
        layout.setSpacing(14)
        def label(value, name=""):
            widget=QLabel(value)
            widget.setTextFormat(Qt.TextFormat.PlainText)
            widget.setWordWrap(True)
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if name:widget.setObjectName(name)
            layout.addWidget(widget)
            return widget
        label(owner.tr("conflict_title"),"section")
        label(owner.tr("conflict_message"))
        label(owner.tr("conflict_output")+"\n"+str(request.destination),"tree")
        label(owner.tr("conflict_source")+"\n"+request.source,"secondary")
        label(owner.tr("conflict_keep_name",name=request.suggestion.name),"smallTitle")
        label(owner.tr("conflict_hint"),"secondary")
        buttons=QHBoxLayout()
        self.action_buttons={}
        for action,key in [("replace","replace"),("skip","skip"),("keep","keep_both")]:
            button=QPushButton(owner.tr(key))
            button.setMinimumHeight(40)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False,choice=action:self.choose(choice))
            self.action_buttons[action]=button
            buttons.addWidget(button)
        self.action_buttons["skip"].setDefault(True)
        self.action_buttons["skip"].setFocus()
        layout.addLayout(buttons)

    def choose(self, action):
        self.action=action
        self.accept()


class GlassCapsule(QWidget):
    """Uses the same Snell/dispersion/Fresnel kernel as the existing app."""
    def __init__(self, owner, widgets):
        super().__init__()
        self.owner=owner
        self.renderer=GlassRenderer()
        self.cache_key=None
        self.image=None
        self.setMinimumHeight(54)
        layout=QHBoxLayout(self)
        layout.setContentsMargins(14,8,14,8)
        layout.setSpacing(6)
        for widget in widgets:
            widget.setProperty("capsule",True)
            layout.addWidget(widget)

    def paintEvent(self,event):
        painter=QPainter(self)
        rect=QRect(5,5,max(4,self.width()-10),max(4,self.height()-10))
        key=(self.size(),self.owner.size(),self.owner.dark,self.owner.thickness,self.owner.frost,self.pos())
        if key!=self.cache_key:
            self.cache_key=key
            backdrop=self.owner.backdrop()
            point=self.mapTo(self.owner,rect.topLeft())
            try:
                self.image=self.renderer.refract(_pixels(backdrop),QRect(point,rect.size()),
                    GlassMaterial(thickness=self.owner.thickness,frost=self.owner.frost),1.,
                    edge_shade=0. if self.owner.dark else .16)
            except (ValueError,IndexError):
                self.image=None
        paint_glass(painter,QRectF(rect),self.image,DARK_STYLE if self.owner.dark else LIGHT_STYLE)


class LiquidButton(QPushButton):
    """Real refracted glass for both enabled and disabled action buttons."""
    def __init__(self, owner, primary=False):
        super().__init__()
        self.owner,self.primary=owner,primary
        self.renderer=GlassRenderer()
        self.cache_key=None
        self.image=None
        self.hovered=False
        self.setMinimumHeight(48)

    def enterEvent(self,event):
        self.hovered=True;self.update()
        super().enterEvent(event)

    def leaveEvent(self,event):
        self.hovered=False;self.update()
        super().leaveEvent(event)

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect=QRect(4,4,max(4,self.width()-8),max(4,self.height()-8))
        point=self.mapTo(self.owner,rect.topLeft())
        key=(self.size(),self.owner.size(),point,self.owner.dark,self.owner.thickness,self.owner.frost)
        if self.cache_key!=key:
            self.cache_key=key
            try:
                self.image=self.renderer.refract(_pixels(self.owner.backdrop()),QRect(point,rect.size()),
                    GlassMaterial(thickness=self.owner.thickness,frost=self.owner.frost),1.,
                    edge_shade=0. if self.owner.dark else .16)
            except (ValueError,IndexError):
                self.image=None
        paint_glass(painter,QRectF(rect),self.image,DARK_STYLE if self.owner.dark else LIGHT_STYLE)
        shape=QPainterPath();shape.addRoundedRect(QRectF(rect),rect.height()/2,rect.height()/2)
        if self.primary:
            tint=QColor(self.owner.colors.accent)
            tint.setAlpha(66 if self.isEnabled() else 20)
            painter.fillPath(shape,tint)
        if self.isDown() or self.hovered and self.isEnabled():
            painter.fillPath(shape,QColor(255,255,255,35 if not self.isDown() else 16))
        if self.hasFocus():
            painter.setPen(QPen(QColor(self.owner.colors.accent),1.5))
            painter.drawRoundedRect(QRectF(rect).adjusted(1,1,-1,-1),rect.height()/2,rect.height()/2)
        font=QFont(self.font())
        if self.primary:font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(self.owner.colors.text if self.isEnabled() else self.owner.colors.secondary))
        painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,self.text())


class MainWindow(QMainWindow):
    def __init__(self, settings_path=None):
        super().__init__()
        default_settings=(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))/"settings.json"
                          if getattr(sys,"frozen",False) else Path(__file__).parent/"state/settings.json")
        self.settings_path=Path(settings_path or os.environ.get("LABBER_CONVERTER_SETTINGS",default_settings))
        self.settings=load_settings(self.settings_path)
        self.lang="en" if self.settings.get("language")=="en" else "zh"
        self.mode=self.settings.get("theme","light")
        self.thickness=float(self.settings.get("thickness",.5))
        self.frost=float(self.settings.get("frost",.25))
        self.dark=False
        self.thread=None
        self.worker=None
        self.last_report=None
        self.inputs=[]
        self.bindings=[]
        self._backdrop=None
        self._backdrop_key=None
        self.resize(1100,900)
        self.setMinimumSize(920,740)
        self.setAcceptDrops(True)
        self._build()
        self._translate()
        self._theme()
        QApplication.instance().styleHints().colorSchemeChanged.connect(self.system_theme_changed)

    def tr(self,key,**values):
        return STRINGS[key][self.lang=="en"].format(**values)

    def label(self,key,kind=""):
        widget=QLabel()
        widget.setWordWrap(True)
        if kind:widget.setObjectName(kind)
        self.bindings.append((widget,key))
        return widget

    def button(self,key,callback,kind=""):
        widget=LiquidButton(self,primary=kind=="primary") if kind=="primary" or key=="open_output" else QPushButton()
        if kind:widget.setObjectName(kind)
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        widget.clicked.connect(callback)
        self.bindings.append((widget,key))
        return widget

    def _build(self):
        central=QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        main=QVBoxLayout(central)
        main.setContentsMargins(30,24,30,22)
        main.setSpacing(19)
        header=QHBoxLayout()
        title_box=QVBoxLayout()
        title_box.setSpacing(4)
        title_box.addWidget(self.label("title","title"))
        title_box.addWidget(self.label("subtitle","secondary"))
        header.addLayout(title_box)
        header.addStretch()
        self.theme_combo=QComboBox()
        for mode in ["light","dark","system"]:self.theme_combo.addItem("",mode)
        self.theme_combo.setCurrentIndex(max(0,self.theme_combo.findData(self.mode)))
        self.theme_combo.currentIndexChanged.connect(self.change_theme)
        self.language_combo=QComboBox()
        self.language_combo.addItem("繁體中文","zh")
        self.language_combo.addItem("English","en")
        self.language_combo.setCurrentIndex(self.lang=="en")
        self.language_combo.currentIndexChanged.connect(self.change_language)
        self.material_button=self.button("material",self.show_material)
        header.addWidget(GlassCapsule(self,[self.material_button,self.theme_combo,self.language_combo]))
        main.addLayout(header)
        body=QHBoxLayout()
        body.setSpacing(22)
        left=QVBoxLayout()
        left.setSpacing(9)
        left.addWidget(self.label("input","section"))
        left.addWidget(self.label("input_hint","secondary"))
        tools=QHBoxLayout()
        self.files_button=self.button("add_files",self.pick_files)
        self.folder_button=self.button("add_folder",self.pick_folder)
        self.remove_button=self.button("remove",self.remove_selected)
        tools.addWidget(GlassCapsule(self,[self.files_button,self.folder_button]))
        tools.addStretch()
        tools.addWidget(self.remove_button)
        left.addLayout(tools)
        self.input_frame=QFrame()
        self.input_frame.setObjectName("inputFrame")
        stack=QVBoxLayout(self.input_frame)
        stack.setContentsMargins(8,8,8,8)
        self.input_list=QListWidget()
        self.input_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.input_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.input_list.setWordWrap(True)
        self.input_list.setAcceptDrops(False)
        self.empty=QWidget()
        empty_layout=QVBoxLayout(self.empty)
        empty_layout.addStretch()
        arrow=QLabel("↓")
        arrow.setObjectName("dropArrow")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(arrow)
        for key,kind in [("drop","dropTitle"),("drop_hint","secondary")]:
            label=self.label(key,kind)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(label)
        empty_layout.addStretch()
        stack.addWidget(self.input_list)
        stack.addWidget(self.empty)
        left.addWidget(self.input_frame,1)
        self.count_label=QLabel()
        self.count_label.setObjectName("secondary")
        left.addWidget(self.count_label)
        body.addLayout(left,3)
        right_widget=QWidget()
        right=QVBoxLayout(right_widget)
        right.setContentsMargins(1,0,7,5)
        right.setSpacing(11)
        right.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        right.addWidget(self.label("output","section"))
        right.addWidget(self.label("output_hint","secondary"))
        self.output_edit=QLineEdit(self.settings.get("output",""))
        self.output_edit.setClearButtonEnabled(True)
        self.output_edit.setMinimumHeight(40)
        self.output_edit.textChanged.connect(self.update_buttons)
        self.output_edit.textChanged.connect(self.update_database_preview)
        output_row=QHBoxLayout()
        output_row.addWidget(self.output_edit,1)
        self.browse_button=self.button("browse",self.pick_output)
        self.browse_button.setMinimumHeight(40)
        output_row.addWidget(self.browse_button)
        right.addLayout(output_row)
        self.new_database_button=self.button("new_database",self.pick_new_database)
        right.addWidget(self.new_database_button)
        right.addWidget(self.label("project"))
        self.project_edit=QLineEdit()
        self.project_edit.setMinimumHeight(40)
        right.addWidget(self.project_edit)
        self.local_cache_check=QCheckBox()
        self.local_cache_check.setChecked(bool(self.settings.get("local_cache",True)))
        self.bindings.append((self.local_cache_check,"local_cache"))
        right.addWidget(self.local_cache_check)
        self.resume_check=QCheckBox()
        self.resume_check.setChecked(bool(self.settings.get("resume",False)))
        self.bindings.append((self.resume_check,"resume"))
        right.addWidget(self.resume_check)
        right.addSpacing(5)
        right.addWidget(self.label("layout","smallTitle"))
        self.database_tree=QLabel()
        self.database_tree.setObjectName("tree")
        self.database_tree.setWordWrap(True)
        self.database_tree.setMinimumHeight(125)
        right.addWidget(self.database_tree)
        right.addWidget(self.label("layout_hint","secondary"))
        self.index_button=self.button("index",lambda:self.start(index_only=True))
        self.index_button.setMinimumHeight(38)
        right.addWidget(self.index_button)
        right.addStretch()
        self.material_popup=QFrame(self,Qt.WindowType.Popup)
        self.material_popup.setObjectName("materialPopup")
        popup_layout=QVBoxLayout(self.material_popup)
        popup_layout.setContentsMargins(20,18,20,18)
        popup_layout.addWidget(self.label("material","smallTitle"))
        controls=QGridLayout()
        controls.setVerticalSpacing(15)
        self.sliders=[]
        for row,(key,value) in enumerate([("thickness",self.thickness),("frost",self.frost)]):
            controls.addWidget(self.label(key,"secondary"),row,0)
            slider=QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0,100)
            slider.setValue(round(value*100))
            slider.setMinimumWidth(180)
            slider.setMinimumHeight(24)
            slider.valueChanged.connect(self.change_material)
            controls.addWidget(slider,row,1)
            self.sliders.append(slider)
        popup_layout.addLayout(controls)
        right_scroll=QScrollArea()
        right_scroll.setObjectName("rightScroll")
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setWidget(right_widget)
        body.addWidget(right_scroll,2)
        main.addLayout(body,1)
        status_row=QHBoxLayout()
        self.status=QLabel()
        self.status.setObjectName("status")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        status_row.addWidget(self.status,1)
        self.cancel_button=self.button("cancel",self.cancel)
        self.cancel_button.hide()
        status_row.addWidget(self.cancel_button)
        self.convert_button=self.button("convert",lambda:self.start(),"primary")
        self.convert_button.setMinimumWidth(170)
        status_row.addWidget(self.convert_button)
        main.addLayout(status_row)
        self.progress=QProgressBar()
        self.progress.setRange(0,100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        main.addWidget(self.progress)
        result_head=QHBoxLayout()
        result_head.addWidget(self.label("results","smallTitle"))
        result_head.addStretch()
        self.open_button=self.button("open_output",self.open_output)
        result_head.addWidget(self.open_button)
        main.addLayout(result_head)
        self.log=QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(110)
        self.log.setMaximumHeight(160)
        main.addWidget(self.log)
        footer=QHBoxLayout()
        footer.addWidget(self.label("reload","secondary"),1)
        version=QLabel(f"v{VERSION}")
        version.setObjectName("secondary")
        footer.addWidget(version)
        main.addLayout(footer)
        self.refresh_inputs()

    def save_settings(self):
        self.settings.update(language=self.lang,theme=self.mode,output=self.output_edit.text(),
                             thickness=self.thickness,frost=self.frost,
                             local_cache=self.local_cache_check.isChecked(),resume=self.resume_check.isChecked())
        try:
            self.settings_path.parent.mkdir(parents=True,exist_ok=True)
            self.settings_path.write_text(json.dumps(self.settings,ensure_ascii=False,indent=2),encoding="utf-8")
        except OSError:
            pass

    def change_language(self):
        self.lang=self.language_combo.currentData()
        self._translate()
        self.save_settings()

    def _translate(self):
        for widget,key in self.bindings:widget.setText(self.tr(key))
        self.setWindowTitle(self.tr("title"))
        self.output_edit.setPlaceholderText(self.tr("output_placeholder"))
        self.project_edit.setPlaceholderText(self.tr("project_placeholder"))
        self.log.setPlaceholderText(self.tr("result_empty"))
        self.index_button.setToolTip(self.tr("index_tip"))
        self.local_cache_check.setToolTip(self.tr("local_cache_tip"))
        self.resume_check.setToolTip(self.tr("resume_tip"))
        self.theme_combo.setToolTip(self.tr("theme_tip"))
        self.language_combo.setToolTip(self.tr("language_tip"))
        for index in range(self.theme_combo.count()):
            self.theme_combo.setItemText(index,self.tr(self.theme_combo.itemData(index)))
        self.count_label.setText(self.tr("queue",count=len(self.inputs)))
        self.update_database_preview()
        if not self.thread:self.status.setText(self.tr("ready"))
        if self.last_report:self.show_report(self.last_report)

    def change_theme(self):
        self.mode=self.theme_combo.currentData()
        self._theme()
        self.save_settings()

    def system_theme_changed(self,*_args):
        if self.mode=="system":self._theme()

    def _theme(self):
        self.dark=self.mode=="dark" or (self.mode=="system" and QApplication.styleHints().colorScheme()==Qt.ColorScheme.Dark)
        c=APP_DARK if self.dark else APP_LIGHT
        self.colors=c
        self._backdrop_key=None
        body=("rgba(255,255,255,0.045)" if self.dark else "rgba(255,255,255,0.48)")
        edge=("rgba(255,255,255,0.16)" if self.dark else "rgba(15,23,32,0.16)")
        self.setStyleSheet(f'''
            QWidget {{ color:{c.text}; font-size:13px; background:transparent; }}
            QMainWindow {{ background:{c.window}; }}
            QDialog#conflictDialog {{ background:{c.panel}; }}
            QFrame#materialPopup {{ background:{c.panel}; border:1px solid {c.border}; border-radius:14px; }}
            QScrollArea#rightScroll, QScrollArea#rightScroll > QWidget > QWidget {{ background:transparent; }}
            QLabel#title {{ font-size:28px; font-weight:650; letter-spacing:-0.7px; }}
            QLabel#section {{ font-size:17px; font-weight:600; }}
            QLabel#smallTitle, QLabel#dropTitle {{ font-weight:600; }}
            QLabel#dropTitle {{ font-size:17px; }}
            QLabel#dropArrow {{ font-size:48px; font-weight:300; color:{c.accent}; }}
            QLabel#secondary {{ color:{c.secondary}; font-size:12px; }}
            QLabel#status {{ color:{c.accent}; }}
            QLabel#tree {{ background:{body}; border:1px solid {c.border}; border-radius:10px; padding:13px; font-size:12px; }}
            QFrame#inputFrame {{ background:{body}; border:1px solid {c.border}; border-radius:16px; }}
            QListWidget {{ border:0; outline:0; padding:4px; }}
            QListWidget::item {{ padding:12px 8px; margin:3px 0; border-radius:8px; }}
            QListWidget::item:selected {{ background:{c.selected}; }}
            QLineEdit, QPlainTextEdit {{ background:{c.input}; border:1px solid {c.border}; border-radius:9px; padding:9px; selection-background-color:{c.selected}; }}
            QLineEdit:focus {{ border:1px solid {c.accent}; }}
            QPlainTextEdit {{ background:{body}; font-size:12px; }}
            QPushButton {{ min-height:18px; border:1px solid {edge}; border-top-color:rgba(255,255,255,0.55); border-radius:15px; padding:8px 14px;
                background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {('#FFFFFF' if not self.dark else '#35424E')},stop:1 {('#E4EBF1' if not self.dark else '#202B35')}); }}
            QPushButton:hover {{ border-color:{c.accent}; }}
            QPushButton:pressed {{ background:{c.selected}; }}
            QPushButton[capsule="true"] {{ border:0; background:transparent; padding:6px 10px; }}
            QPushButton[capsule="true"]:hover {{ background:{body}; }}
            QPushButton#primary {{ background:{c.accent}; color:{('#0B0F14' if self.dark else '#FFFFFF')}; border-color:{c.accent}; font-weight:600; padding:11px 22px; border-radius:20px; }}
            QPushButton#primary:disabled {{ color:{c.secondary}; background:{body}; border-color:{c.border}; }}
            QPushButton:disabled {{ color:{c.secondary}; background:{body}; border-color:{c.border}; }}
            QComboBox {{ background:transparent; border:0; padding:5px 10px; min-width:70px; }}
            QComboBox QAbstractItemView {{ background:{c.panel}; border:1px solid {c.border}; padding:6px; selection-background-color:{c.selected}; }}
            QProgressBar {{ background:{c.border}; border:0; border-radius:3px; }}
            QProgressBar::chunk {{ background:{c.accent}; border-radius:3px; }}
            QSlider::groove:horizontal {{ background:{c.border}; height:3px; border-radius:1px; }}
            QSlider::sub-page:horizontal {{ background:{c.accent}; border-radius:1px; }}
            QSlider::handle:horizontal {{ background:white; border:1px solid {c.border}; width:15px; height:15px; margin:-7px 0; border-radius:8px; }}
            QCheckBox {{ spacing:8px; }}
            QCheckBox::indicator {{ width:15px; height:15px; }}
            QCheckBox::indicator:unchecked {{ border:1px solid {c.secondary}; border-radius:4px; background:{body}; }}
            QScrollBar:vertical {{ background:transparent; width:7px; }}
            QScrollBar::handle:vertical {{ background:{c.border}; min-height:24px; border-radius:3px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
            QToolTip {{ background:{c.panel}; color:{c.text}; border:1px solid {c.border}; padding:4px; }}
        ''')
        for capsule in [*self.findChildren(GlassCapsule),*self.findChildren(LiquidButton)]:
            capsule.cache_key=None
            capsule.update()
        self.update()

    def change_material(self):
        self.thickness,self.frost=[s.value()/100 for s in self.sliders]
        for capsule in [*self.findChildren(GlassCapsule),*self.findChildren(LiquidButton)]:capsule.update()
        self.save_settings()

    def show_material(self):
        self.material_popup.adjustSize()
        self.material_popup.move(self.material_button.mapToGlobal(self.material_button.rect().bottomLeft()))
        self.material_popup.show()

    def backdrop(self):
        key=(self.width(),self.height(),self.dark)
        if key!=self._backdrop_key:
            image=QImage(self.size(),QImage.Format.Format_RGBA8888)
            image.fill(QColor(self.colors.window))
            painter=QPainter(image)
            # Subtle in-app illumination; only this backdrop is refracted.
            for x,y,r,alpha in [(self.width()*.7,-80,600,23 if self.dark else 30),(-80,self.height()*.55,500,12 if self.dark else 15)]:
                glow=QRadialGradient(QPointF(x,y),r)
                color=QColor(self.colors.accent);color.setAlpha(alpha)
                glow.setColorAt(0,color);glow.setColorAt(1,QColor(0,0,0,0))
                painter.fillRect(image.rect(),glow)
            painter.end()
            self._backdrop=image;self._backdrop_key=key
        return self._backdrop

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.drawImage(0,0,self.backdrop())

    def resizeEvent(self,event):
        self._backdrop_key=None
        super().resizeEvent(event)

    def add_inputs(self,paths):
        for raw in paths:
            path=str(Path(raw).expanduser().resolve())
            if path not in self.inputs:self.inputs.append(path)
        self.refresh_inputs()

    def refresh_inputs(self):
        self.input_list.clear()
        for path in self.inputs:
            p=Path(path)
            self.input_list.addItem(f"{'▣' if p.is_dir() else '↳'}  {p.name}\n     {p.parent}")
            self.input_list.item(self.input_list.count()-1).setToolTip(path)
        self.input_list.setVisible(bool(self.inputs))
        self.empty.setVisible(not self.inputs)
        self.count_label.setText(self.tr("queue",count=len(self.inputs)))
        self.update_buttons()

    def remove_selected(self):
        indices={self.input_list.row(item) for item in self.input_list.selectedItems()}
        self.inputs=[p for i,p in enumerate(self.inputs) if i not in indices]
        self.refresh_inputs()

    def pick_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,self.tr("add_files"),"",self.tr("file_filter"))
        self.add_inputs(paths)

    def pick_folder(self):
        path=QFileDialog.getExistingDirectory(self,self.tr("add_folder"))
        if path:self.add_inputs([path])

    def pick_output(self):
        path=QFileDialog.getExistingDirectory(self,self.tr("output"),self.output_edit.text())
        if path:
            self.output_edit.setText(path)
            self.save_settings()

    def pick_new_database(self):
        path=QFileDialog.getExistingDirectory(self,self.tr("new_database_title"),self.output_edit.text())
        if path:
            self.output_edit.setText(path)
            self.start(initialize_only=True)

    def update_database_preview(self):
        if hasattr(self,"database_tree"):
            value=self.output_edit.text().strip()
            root=Path(value).name if value else self.tr("root_placeholder")
            self.database_tree.setText(self.tr("tree_layout",root=root))
            self.database_tree.setToolTip(str(Path(value)/"Database.hdf5") if value else "")

    def dragEnterEvent(self,event):
        if not self.thread and event.mimeData().hasUrls():event.acceptProposedAction()

    def dropEvent(self,event):
        if not self.thread:
            self.add_inputs([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
            event.acceptProposedAction()

    def update_buttons(self):
        if not hasattr(self,"convert_button"):return
        busy=self.thread is not None
        self.convert_button.setEnabled(not busy and bool(self.inputs) and bool(self.output_edit.text().strip()))
        self.index_button.setEnabled(not busy and bool(self.output_edit.text().strip()))
        self.remove_button.setEnabled(not busy and bool(self.inputs))
        self.open_button.setEnabled(bool(self.output_edit.text().strip()))
        for widget in [self.files_button,self.folder_button,self.browse_button,self.new_database_button,self.output_edit,self.project_edit,self.input_list,self.local_cache_check,self.resume_check]:widget.setEnabled(not busy)
        self.cancel_button.setVisible(busy)

    def start(self,index_only=False,initialize_only=False):
        if self.thread:return
        if not index_only and not initialize_only and not self.inputs:
            self.status.setText(self.tr("need_input"));return
        if not self.output_edit.text().strip():
            self.status.setText(self.tr("need_output"));return
        rebuild=False
        if index_only:
            box=QMessageBox(QMessageBox.Icon.Question,self.tr("rebuild_title"),self.tr("rebuild_ask"),
                            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No|QMessageBox.StandardButton.Cancel,self)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            answer=box.exec()
            if answer==QMessageBox.StandardButton.Cancel:return
            rebuild=answer==QMessageBox.StandardButton.Yes
        self.last_report=None
        self.log.clear()
        self.save_settings()
        self.progress.setRange(0,0)
        self.status.setText(self.tr("initializing" if initialize_only else "indexing" if index_only else "scanning"))
        self.thread=QThread(self)
        self.worker=Worker(list(self.inputs),self.output_edit.text().strip(),self.project_edit.text().strip(),index_only,initialize_only,
                           self.local_cache_check.isChecked(),self.resume_check.isChecked(),rebuild)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.activity.connect(self.on_activity)
        self.worker.conflict.connect(self.resolve_conflict)
        self.worker.result.connect(self.show_report)
        self.worker.failed.connect(self.on_failure)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.on_finished)
        self.thread.start()
        self.update_buttons()

    def on_progress(self,done,total,name):
        self.progress.setRange(0,total)
        self.progress.setValue(done)
        self.status.setText(self.tr("working",done=done,total=total,name=name))
        self.status.setToolTip(name)

    def on_activity(self,event):
        self.status.setText(self.tr("phase_"+event["stage"],**event))

    def resolve_conflict(self,request):
        try:
            if self.thread is None or self.thread.isInterruptionRequested():
                request.action="cancel"
                return
            dialog=ConflictDialog(self,request)
            dialog.exec()
            request.action=dialog.action
            dialog.deleteLater()
        finally:
            request.ready.set()

    def show_report(self,report):
        self.last_report=report
        if report.get("operation")=="initialize":
            message=self.tr("database_ready",path=report["database_root"])
            self.log.setPlainText(message+"\n"+self.tr("database_file",path=report["database_path"]))
            self.status.setText(message)
        elif "ok" not in report:
            message=self.tr("index_done",files=report["files"],labels=report["labels"])
            if report.get("removed_labels"):
                message+="\n"+self.tr("removed_labels",labels="、".join(report["removed_labels"]))
            self.log.setPlainText(message+"\n"+json.dumps(report["errors"],ensure_ascii=False,indent=2) if report["errors"] else message)
            self.status.setText(message)
        else:
            lines=[]
            for item in report["files"]:
                if item["status"]=="error":
                    lines.extend([f"✕ {item['source']}",f"  {item['error']}"])
                    if item.get("reason")=="source_active":lines.append(self.tr("source_active"))
                elif item["status"]=="resumed":
                    lines.append(self.tr("resumed_file",name=Path(item["output"]).name))
                elif item["status"]=="pending_sync":
                    lines.append(self.tr("pending_file",name=Path(item["output"]).name))
                elif item["status"]=="skipped" and item.get("reason")=="not_convertible":
                    lines.append(self.tr("not_convertible",name=Path(item["source"].split(" :: ")[-1]).name,why=item.get("error","")))
                elif item["status"]=="skipped":
                    lines.append(self.tr("skipped_file",name=Path(item["output"]).name))
                    if item.get("reason")=="source_is_destination":lines.append(self.tr("same_file"))
                else:
                    name=Path(item["output"]).name
                    if item.get("copied_existing_labber"):
                        lines.append(self.tr("copied",name=name))
                    else:
                        lines.append(self.tr("ok_file",name=name,points=item["points"],freq=" / ".join(map(str,item["trace_points"]))))
                        if not item["source_complete"]:
                            lines.append(self.tr("incomplete",points=item["points"],planned=item["planned_points"]))
                        for warning in item.get("warnings",[]):
                            if not warning.startswith("Source run is incomplete:"):lines.append("  "+warning)
                    if item.get("conflict_action")=="replace":lines.append(self.tr("replaced_file"))
                    if item.get("conflict_action")=="keep":lines.append(self.tr("renamed_file"))
                    if item.get("native_scan_path_warning"):lines.append(self.tr("native_scan_path_warning"))
            lines.extend(["",self.tr("report",path=report["report_path"])])
            if report.get("database_verified"):
                lines.append(self.tr("database_file",path=report["database_path"]))
            if report.get("database_error"):
                lines.append(report["database_error"])
            self.log.setPlainText("\n".join(lines))
            self.status.setText(self.tr("stopped" if report.get("cancelled") else "done",ok=report["ok"],skipped=report.get("skipped",0),errors=report["errors"],resumed=report.get("resumed",0),pending=report.get("pending_sync",0)))
        self.progress.setRange(0,100);self.progress.setValue(100)

    def on_failure(self,message):
        self.status.setText(self.tr("failure"))
        self.log.setPlainText(message)
        self.progress.setRange(0,100);self.progress.setValue(0)

    def on_finished(self):
        thread=self.thread
        self.thread=None;self.worker=None
        if thread:thread.deleteLater()
        self.update_buttons()

    def cancel(self):
        if self.thread:
            self.thread.requestInterruption()
            self.status.setText(self.tr("finishing"))

    def open_output(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.output_edit.text()).expanduser().resolve())))

    def closeEvent(self,event):
        if self.thread:
            self.cancel();event.ignore()
        else:
            self.save_settings();event.accept()


def smoke_conversion(folder):
    """0.4.2 (packaging check): convert one tiny synthetic labcontrol-nc file inside the packaged app."""
    try:
        import h5py
        import numpy as np
        src=Path(folder)/"smoke_src"/"Data_1022";src.mkdir(parents=True,exist_ok=True)
        with h5py.File(src/"smoke.nc","w") as file:
            file.attrs.update(schema="labcontrol-nc/1",layout="grid",created="2026-10-22T18:00:00",points=2,
                              planned_points=2,complete=1,project="",user="CI",tags="smoke",labcontrol="{}")
            axis=file.create_dataset("Current",data=[1.,2.]);axis.make_scale("Current")
            freq=file.create_dataset("Frequency",data=[1e9,1e9+1e3,1e9+2e3]);freq.make_scale("Frequency")
            trace=file.create_dataset("S21",data=np.ones((2,3),dtype=complex))
            trace.dims[0].attach_scale(axis);trace.dims[1].attach_scale(freq)
        report=convert_batch([src],Path(folder)/"smoke_out")
        return "conversion ok" if report["ok"]==1 and report["database_verified"] else f"conversion failed: {report['files']}"
    except Exception as error:  # noqa: BLE001
        return f"conversion failed: {type(error).__name__}: {error}"


def main():
    app=QApplication(sys.argv)
    app.setApplicationName("Labber Converter")
    app.setOrganizationName("LabTools")
    app.setStyle("Fusion")
    icon_path=Path(__file__).parent/"assets/icon.png"
    if icon_path.exists():app.setWindowIcon(QIcon(str(icon_path)))
    window=MainWindow()
    window.show()
    smoke=os.environ.get("LABBER_CONVERTER_SMOKE_DIR")
    if smoke:
        def smoke_capture():
            destination=Path(smoke)
            destination.mkdir(parents=True,exist_ok=True)
            window.grab().save(str(destination/"packaged_ui.png"))
            status=smoke_conversion(destination)
            (destination/"ready.txt").write_text(f"Labber Converter {VERSION}\n{status}",encoding="utf-8")
            window.close()
            app.quit()
        QTimer.singleShot(1500,smoke_capture)
    raise SystemExit(app.exec())


if __name__=="__main__":
    main()
