"""右邊：檔案設置（檔名、資料夾、格式、量測者、標籤、註解）。0.0.20 起存成 xarray .nc，不再輸出 Labber。

分頁：
  檔案        論文庫專案（可新增）、檔名、資料夾、同名處理、格式、量測者、標籤與註解、分檔結果、實際存檔路徑預覽
  執行        每點量測前等待、移動 / 停靠速率、錯誤處理、自動暫停
  檢查與估時   錯誤與警告（點一下跳到節點）、總點數、預估時間
沒填的欄位一律使用 settings.yaml 的預設值（欄位裡灰色字）。
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Callable

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from ....core.units import parse_quantity
from ....data.naming import (data_root, day_folder, folder_pattern, list_project_folders,
                             project_folder, project_folder_name, remember_folder, remembered_folder,
                             stale_date_in_filename)
from ....scheme.compile import file_settings, format_duration
from ....settings import setting
from .. import theme
from ..worker import run_bg
from .dialogs import dspin, ispin
from .doc import WorkbenchDoc

NEW_PROJECT = "__new__"
COLLISIONS = [("underscore", "建立新檔（加 _2、_3…）"), ("paren", "建立新檔（加 (2)、(3)…）"), ("overwrite", "覆寫")]


from ....measure.experiment import raw_backup  # noqa: E402


class FilePanel(QtWidgets.QTabWidget):
    def __init__(self, doc: WorkbenchDoc, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self._loading = False
        self.addTab(self._file_tab(), "檔案")
        self.addTab(self._run_tab(), "執行")
        self.addTab(self._check_tab(), "檢查與估時")
        doc.changed.connect(self._on_changed)
        theme.on_change(self, lambda w: w._refresh_derived(), call_now=False)
        self.load()
        self.reload_projects()

    # ---- 檔案 -----------------------------------------------------------
    def _file_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)
        outer.setContentsMargins(6, 6, 6, 6)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)
        self.lab_project = QtWidgets.QComboBox()
        self.lab_project.setToolTip("論文庫裡的專案：數據上傳後歸到這個專案（讀檔模塊可以直接開整個專案），\n"
                                    "標籤會記到專案的數據標籤；資料夾規則可以用 {project}")
        self.lab_project.setMinimumContentsLength(16)
        proj_refresh = QtWidgets.QToolButton()
        proj_refresh.setText("⟳")
        proj_refresh.setToolTip("重新讀取論文庫的專案")
        proj_refresh.clicked.connect(lambda: self.reload_projects(force=True))
        proj_row = QtWidgets.QHBoxLayout()
        proj_row.addWidget(self.lab_project, 1)
        proj_row.addWidget(proj_refresh)
        # 0.0.30：專案資料夾（資料根目錄底下，每個專案一個）：自動用專案名稱建立，或選已經有的資料夾
        self.proj_folder = QtWidgets.QComboBox()
        self.proj_folder.setObjectName("projectFolder")
        self.proj_folder.setEditable(True)
        self.proj_folder.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.proj_folder.setToolTip("資料根目錄底下這個專案的資料夾（資料夾規則裡的 {project}）。\n"
                                    "空白 = 自動用論文庫專案的名稱（沒有就建立）；也可以從清單選已經有的資料夾，\n"
                                    "或打一個新名稱。每個專案選過的資料夾這台電腦會記住。")
        folder_open = QtWidgets.QToolButton()
        folder_open.setText("📂")
        folder_open.setToolTip("在檔案總管打開存檔的資料夾")
        folder_open.clicked.connect(self._open_folder)
        folder_row = QtWidgets.QHBoxLayout()
        folder_row.addWidget(self.proj_folder, 1)
        folder_row.addWidget(folder_open)
        self.fname = QtWidgets.QLineEdit()
        self.date_hint = QtWidgets.QPushButton()
        self.date_hint.setFlat(True)
        theme.on_change(self.date_hint, lambda b: b.setStyleSheet(f"color:{theme.c('warn')}; text-align:left;"))
        self.date_hint.setVisible(False)
        self.root = QtWidgets.QLineEdit()
        browse = QtWidgets.QToolButton()
        browse.setText("…")
        self.pattern = QtWidgets.QLineEdit()
        self.collision = QtWidgets.QComboBox()
        for k, v in COLLISIONS:
            self.collision.addItem(v, k)
        self.f_nc = QtWidgets.QCheckBox("NetCDF（xarray .nc）")
        self.f_nc.setToolTip("用 xarray 開：xr.open_dataset(檔名, engine=\"h5netcdf\")；軸、單位、標籤、量測設定都在檔案裡")
        self.f_hdf5 = QtWidgets.QCheckBox("HDF5（原生）")
        fmts = QtWidgets.QHBoxLayout()
        fmts.addWidget(self.f_nc)
        fmts.addWidget(self.f_hdf5)
        fmts.addStretch()
        self.project = QtWidgets.QLineEdit()
        self.user = QtWidgets.QLineEdit()
        self.tags = QtWidgets.QLineEdit()
        self.comment = QtWidgets.QPlainTextEdit()
        self.comment.setMaximumHeight(52)
        self.comment.setPlaceholderText("註解（寫進檔案；儀器設定會自動存，不用寫在這裡）")
        root_row = QtWidgets.QHBoxLayout()
        root_row.addWidget(self.root, 1)
        root_row.addWidget(browse)
        r = 0
        for label, widget in (("論文庫專案", proj_row), ("專案資料夾", folder_row), ("檔名", self.fname),
                              ("", self.date_hint), ("資料根目錄", root_row),
                              ("資料夾規則", self.pattern), ("同名檔案", self.collision), ("格式", fmts)):
            grid.addWidget(QtWidgets.QLabel(label), r, 0)
            if isinstance(widget, QtWidgets.QLayout):
                grid.addLayout(widget, r, 1)
            else:
                grid.addWidget(widget, r, 1)
            r += 1
        lab = QtWidgets.QLabel("檔案資訊")
        lab.setStyleSheet("font-weight:600; color:palette(text); margin-top:4px;")
        grid.addWidget(lab, r, 0)
        r += 1
        tag_row = QtWidgets.QHBoxLayout()
        tag_row.addWidget(self.tags, 1)
        self.tag_btn = QtWidgets.QPushButton("🏷 標籤與論文")
        self.tag_btn.setToolTip("從量子工程平台共用的標籤挑選，並看每個標籤在論文庫裡的論文")
        self.tag_btn.clicked.connect(self._pick_tags)
        tag_row.addWidget(self.tag_btn)
        self.tags.setToolTip("所有模塊共用的標籤（量子工程平台）；也寫進檔案，上傳到數據目錄時一起登記")
        self.project.hide()                 # Labber 的 Project（MM/MMDD）：不再使用，專案改用上面的論文庫專案
        for label, widget in (("量測者", self.user), ("標籤", tag_row),
                              ("註解", self.comment)):
            grid.addWidget(QtWidgets.QLabel(label), r, 0)
            if isinstance(widget, QtWidgets.QLayout):
                grid.addLayout(widget, r, 1)
            else:
                grid.addWidget(widget, r, 1)
            r += 1
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)
        self.where = QtWidgets.QLabel()
        self.where.setWordWrap(True)
        self.where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.where.setStyleSheet(theme.tint("ok"))
        outer.addWidget(self.where)
        outer.addStretch()

        self.lab_project.activated.connect(self._project_chosen)
        self.proj_folder.activated.connect(lambda _i: self._folder_chosen())
        self.proj_folder.lineEdit().editingFinished.connect(self._folder_chosen)
        self.fname.editingFinished.connect(self._commit_fname)
        # 0.0.28：打字時就記下檔名（之前只有按 Enter / 換到別的欄位才記：直接按「▶ 開始量測」或 F5
        # 時，工具列按鈕不會拿走焦點，打好的檔名沒有套用，存成舊的 aaa_1）
        self._fname_timer = QtCore.QTimer(self)
        self._fname_timer.setSingleShot(True)
        self._fname_timer.timeout.connect(self._commit_fname)
        self.fname.textEdited.connect(lambda _t: self._fname_timer.start(400))
        self.date_hint.clicked.connect(self._fix_date)
        self.root.editingFinished.connect(lambda: (self._out("root", self.root.text().strip()), self._fill_folders()))
        browse.clicked.connect(self._browse)
        self.pattern.editingFinished.connect(lambda: (self._out("folder_pattern", self.pattern.text().strip()),
                                                      self._fill_folders()))
        self.collision.currentIndexChanged.connect(lambda _i: self._out("collision", self.collision.currentData()))
        self.f_nc.toggled.connect(lambda _v: self._formats())
        self.f_hdf5.toggled.connect(lambda _v: self._formats())
        self.project.editingFinished.connect(lambda: self._out("project", self.project.text().strip()))
        self.user.editingFinished.connect(lambda: self._out("user", self.user.text().strip() or None))
        self.tags.editingFinished.connect(self._tags)
        self.comment.textChanged.connect(lambda: self._comment_timer.start(600))
        self._comment_timer = QtCore.QTimer(self)
        self._comment_timer.setSingleShot(True)
        self._comment_timer.timeout.connect(lambda: self._out("comment", self.comment.toPlainText().strip()))
        return w

    # ---- 執行 -----------------------------------------------------------
    def _run_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        unit = setting("editor.new_blocks.dc_set.unit", "mA")
        self.rate_unit = f"{unit}/s"
        self.delay = dspin(0, 1e6, 3, "s")
        self.approach = dspin(0, 1e6, 4, self.rate_unit)
        self.park_rate = dspin(0, 1e6, 4, self.rate_unit)
        self.on_error = QtWidgets.QComboBox()
        self.on_error.addItem("重試後自動暫停（等人處理）", "pause")
        self.on_error.addItem("重試後中斷量測", "stop")
        self.retries = ispin(0, 20)
        self.dip = QtWidgets.QCheckBox("吸收峰型態變化（1 ⇌ 2 個）自動暫停")
        f.addRow("每點量測前等待", self.delay)
        f.addRow("移到起點速率", self.approach)
        f.addRow("結束後停靠速率", self.park_rate)
        f.addRow("量測錯誤", self.on_error)
        f.addRow("重試次數", self.retries)
        f.addRow("自動判斷", self.dip)
        note = QtWidgets.QLabel("數值 0 = 使用儀器的斜坡速率。各軸「掃完後回到第一點 / 停在最後」在該掃描節點設定。"
                                "沒改過的欄位使用 settings.yaml 的 run_defaults。")
        note.setWordWrap(True)
        note.setStyleSheet("color:palette(placeholder-text); font-size:11px;")
        f.addRow(note)
        self.delay.valueChanged.connect(lambda v: self._run("point_delay", v or None))
        self.approach.valueChanged.connect(lambda v: self._run("approach_rate", f"{v:g} {self.rate_unit}" if v else None))
        self.park_rate.valueChanged.connect(lambda v: self._run("park_rate", f"{v:g} {self.rate_unit}" if v else None))
        self.on_error.currentIndexChanged.connect(lambda _i: self._run("on_error", self.on_error.currentData()))
        self.retries.valueChanged.connect(lambda v: self._run("retries", int(v)))
        self.dip.toggled.connect(self._dip)
        return w

    # ---- 檢查 -----------------------------------------------------------
    def _check_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        self.est = QtWidgets.QLabel()
        self.est.setWordWrap(True)
        v.addWidget(self.est)
        self.issue_list = QtWidgets.QListWidget()
        self.issue_list.itemClicked.connect(self._issue_clicked)
        v.addWidget(self.issue_list, 1)
        return w

    # ---- 寫回方案 ---------------------------------------------------------
    def _mutate(self, fn: Callable) -> None:
        if not self._loading:
            self.doc.mutate(fn, origin=self)

    def _commit_fname(self) -> None:
        self._fname_timer.stop()
        name = self.fname.text().strip()
        if name != (self.doc.scheme.output.get("file_name") or ""):
            self._out("file_name", name)

    def commit_edits(self) -> None:
        """開始量測、存方案之前：把正在打、還沒按 Enter 的欄位都寫進方案。"""
        out = self.doc.scheme.output
        self._commit_fname()
        for key, widget in (("root", self.root), ("folder_pattern", self.pattern), ("project", self.project)):
            if widget.text().strip() != (out.get(key) or ""):
                self._out(key, widget.text().strip())
        if self.proj_folder.currentText().strip() != (out.get("project_folder") or ""):
            self._folder_chosen()
        if self.user.text().strip() != (out.get("user") or ""):
            self._out("user", self.user.text().strip() or None)
        current = ", ".join(out["tags"]) if out.get("tags") is not None else ""
        if self.tags.text().strip() != current:
            self._tags()
        if self._comment_timer.isActive() or self.comment.toPlainText().strip() != (out.get("comment") or ""):
            self._comment_timer.stop()
            self._out("comment", self.comment.toPlainText().strip())

    def _out(self, key: str, value: Any) -> None:
        def fn(s):
            if value in (None, "") and key != "user":
                s.output.pop(key, None)
            elif value is None:
                s.output.pop(key, None)
            else:
                s.output[key] = value
        self._mutate(fn)

    def _formats(self) -> None:
        f = [k for k, cb in (("netcdf", self.f_nc), ("hdf5", self.f_hdf5)) if cb.isChecked()]
        self._out("formats", f)

    def _tags(self) -> None:
        text = self.tags.text().strip()
        self._out("tags", [t.strip() for t in text.split(",") if t.strip()] if text else None)

    def current_tags(self) -> list:
        return [t.strip() for t in self.tags.text().split(",") if t.strip()]

    def set_tags(self, tags: list) -> None:
        self.tags.setText(", ".join(tags))
        self._tags()

    def _pick_tags(self) -> None:
        from ..platform_ui import TagPickerDialog

        d = TagPickerDialog(self.current_tags(), self, project=self.doc.scheme.output.get("lab_project"))
        if d.exec():
            self.set_tags(d.selected)

    # ---- 論文庫專案 -----------------------------------------------------------
    def current_project(self) -> dict:
        return dict(self.doc.scheme.output.get("lab_project") or {})

    def _fill_projects(self, items: list) -> None:
        cur = self.current_project()
        cb = self.lab_project
        cb.blockSignals(True)
        cb.clear()
        cb.addItem("（不指定專案）", None)
        seen = False
        for p in items:
            label = p["name"] + (f"（{p['short_name']}）" if p.get("short_name") and p["short_name"] != p["name"] else "")
            if p.get("data"):
                label += f" · {p['data']} 筆數據"
            cb.addItem(label, {"id": str(p["id"]), "name": p["name"]})
            if p.get("color"):
                cb.setItemData(cb.count() - 1, QtGui.QColor(p["color"]), Qt.ItemDataRole.DecorationRole)
            seen |= str(p["id"]) == str(cur.get("id", ""))
        if cur.get("id") and not seen:   # 沒登入或專案讀不到：保留方案裡記的
            cb.addItem(f"{cur.get('name') or cur['id']}（方案裡記的）", {"id": str(cur["id"]), "name": cur.get("name", "")})
        cb.addItem("＋ 新專案…", NEW_PROJECT)
        idx = 0
        for i in range(cb.count()):
            d = cb.itemData(i)
            if isinstance(d, dict) and d["id"] == str(cur.get("id", "")):
                idx = i
        cb.setCurrentIndex(idx)
        cb.blockSignals(False)

    def reload_projects(self, force: bool = False) -> None:
        from ....integrations import platform as plat

        self._fill_projects(plat.PROJECTS.items)
        if plat.current_session() is None:
            self.lab_project.setToolTip("登入量子工程平台後可以選論文庫的專案")
            return

        def ok(items: list) -> None:
            self._fill_projects(items)
            if plat.PROJECTS.error:
                self.lab_project.setToolTip(f"讀不到論文庫的專案：{plat.PROJECTS.error}")
        run_bg(lambda: plat.PROJECTS.get(force=force), ok, lambda m: None)

    def _project_chosen(self, _i: int) -> None:
        d = self.lab_project.currentData()
        if d == NEW_PROJECT:
            p = self._new_project_dialog()
            if p is None:
                self._fill_projects(self._project_items())
                return
            d = {"id": str(p["id"]), "name": p["name"]}
        folder = remembered_folder(d["id"]) if isinstance(d, dict) else ""

        def fn(s):
            if d:
                s.output["lab_project"] = d
            else:
                s.output.pop("lab_project", None)
            if folder:                                  # 這台電腦上次替這個專案選的資料夾
                s.output["project_folder"] = folder
            else:
                s.output.pop("project_folder", None)
        self._mutate(fn)
        self._fill_projects(self._project_items())
        self._fill_folders()

    # ---- 專案資料夾（0.0.30）-----------------------------------------------------
    def _root(self):
        return data_root(self.doc.scheme.output.get("root") or None)

    def _fill_folders(self) -> None:
        o = self.doc.scheme.output
        cb = self.proj_folder
        cb.blockSignals(True)
        cb.clear()
        for name in list_project_folders(self._root()):
            cb.addItem(name)
        cb.setEditText(o.get("project_folder") or "")
        auto = project_folder({k: v for k, v in o.items() if k != "project_folder"})
        exists = (self._root() / auto).is_dir()
        cb.lineEdit().setPlaceholderText(f"自動：{auto}" + ("（已有這個資料夾）" if exists else "（會建立）"))
        uses = "{project}" in folder_pattern(o.get("folder_pattern"))
        cb.setEnabled(uses)
        if not uses:
            cb.lineEdit().setPlaceholderText("資料夾規則沒有 {project}：不分專案資料夾")
        cb.blockSignals(False)

    def _folder_chosen(self) -> None:
        text = self.proj_folder.currentText().strip()
        name = project_folder_name(text) if text else ""
        if name != text:
            self.proj_folder.setEditText(name)
        if name == (self.doc.scheme.output.get("project_folder") or ""):
            return
        self._out("project_folder", name or None)
        remember_folder(self.current_project().get("id", ""), name)
        self._fill_folders()

    def _open_folder(self) -> None:
        o = self.doc.scheme.output
        folder = day_folder(self._root(), folder_pattern(o.get("folder_pattern")), _dt.datetime.now(),
                            project_folder(o))
        while not folder.is_dir() and folder != folder.parent:       # 還沒建立：打開最近一層已經有的
            folder = folder.parent
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))

    @staticmethod
    def _project_items() -> list:
        from ....integrations import platform as plat

        return plat.PROJECTS.items

    def _new_project_dialog(self):
        """在論文庫建立新專案（需要登入，且論文庫允許你建立專案）。"""
        from ....integrations import platform as plat

        s = plat.current_session()
        if s is None:
            QtWidgets.QMessageBox.information(self, "新專案", "請先登入量子工程平台（選單「平台」→ 登入）。")
            return None
        dlg = NewProjectDialog(self)
        if not dlg.exec():
            return None
        try:
            p = s.create_project(**dlg.values())
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "新專案", f"建立失敗：{e}")
            return None
        plat.PROJECTS.items = [x for x in plat.PROJECTS.items if x["id"] != str(p["id"])] + [p]
        plat.PROJECTS.at = 0.0
        return p

    def _run(self, key: str, value: Any) -> None:
        def fn(s):
            if value is None:
                s.run.pop(key, None)
            else:
                s.run[key] = value
        self._mutate(fn)

    def _dip(self, on: bool) -> None:
        def fn(s):
            s.hooks = [h for h in s.hooks if h.get("type") != "dip_shape_pause"]
            if on:
                s.hooks.append({"type": "dip_shape_pause", "enabled": True})
        self._mutate(fn)

    def _browse(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "資料根目錄", str(data_root(self.root.text() or None)))
        if d:
            self.root.setText(d)
            self._out("root", d)
            self._fill_folders()

    def _fix_date(self) -> None:
        w = stale_date_in_filename(self.fname.text())
        if w:
            self.fname.setText(w[2])
            self._out("file_name", w[2])

    # ---- 讀取 -----------------------------------------------------------
    def _on_changed(self, origin: object) -> None:
        if origin is self:
            self._refresh_derived()
        else:
            self.load()

    def load(self) -> None:
        self._loading = True
        try:
            s = self.doc.scheme
            o = s.output
            fs = file_settings(s, self.doc.result.save)
            if not (self.fname.hasFocus() and self._fname_timer.isActive()):    # 正在打的字不要被蓋掉
                self.fname.setText(o.get("file_name", ""))
            self.fname.setPlaceholderText(fs["file_name"])
            self.root.setText(o.get("root", ""))
            self.root.setPlaceholderText(str(data_root()))
            self.pattern.setText(o.get("folder_pattern", ""))
            self.pattern.setPlaceholderText(folder_pattern())
            self._fill_folders()
            self.collision.setCurrentIndex(max(0, self.collision.findData(fs["collision"])))
            self.f_nc.setChecked("netcdf" in fs["formats"])
            self.f_hdf5.setChecked("hdf5" in fs["formats"])
            self._fill_projects(self._project_items())
            self.project.setText(o.get("project", ""))
            self.project.setPlaceholderText(f"{setting('labber.project', 'auto')}（auto = MM/MMDD）")
            self.user.setText(o.get("user") or "")
            self.user.setPlaceholderText(str(setting("data.user", None) or setting("labber.user", "")))
            self.tags.setText(", ".join(o["tags"]) if o.get("tags") is not None else "")
            self.tags.setPlaceholderText(", ".join(fs["tags"]) + "（逗號分隔）")
            if self.comment.toPlainText().strip() != o.get("comment", ""):
                self.comment.setPlainText(o.get("comment", ""))
            run = {**(setting("run_defaults", {}) or {}), **s.run}
            self.delay.setValue(_num(run.get("point_delay"), "s"))
            self.approach.setValue(_num(s.run.get("approach_rate"), self.rate_unit))
            self.park_rate.setValue(_num(s.run.get("park_rate"), self.rate_unit))
            self.approach.setSpecialValueText(f"預設（{run.get('approach_rate') or '儀器速率'}）")
            self.park_rate.setSpecialValueText(f"預設（{run.get('park_rate') or '儀器速率'}）")
            self.on_error.setCurrentIndex(max(0, self.on_error.findData(run.get("on_error", "pause"))))
            self.retries.setValue(int(run.get("retries", 1)))
            self.dip.setChecked(any(h.get("type") == "dip_shape_pause" and h.get("enabled", True) for h in s.hooks))
        finally:
            self._loading = False
        self._refresh_derived()

    def _refresh_derived(self) -> None:
        s, r = self.doc.scheme, self.doc.result
        fs = file_settings(s, r.save)
        now = _dt.datetime.now()
        w = stale_date_in_filename(fs["file_name"], now)
        self.date_hint.setVisible(bool(w))
        if w:
            self.date_hint.setText(f"⚠ 檔名日期 {w[0]} 不是今天 → 按這裡改成「{w[2]}」")
        try:
            folder = day_folder(data_root(s.output.get("root") or None), folder_pattern(s.output.get("folder_pattern")),
                                now, project_folder(s.output))
        except Exception as e:  # noqa: BLE001
            folder = Path(f"（資料夾規則錯誤：{e}）")
        if not fs["formats"]:
            split = "⚠ 沒有選格式：量完不會存檔"
        elif r.split_depth:
            names = "、".join(lp.axis_name for lp in r.loops[:r.split_depth])
            ex = setting("data.split_pattern", "{stem}_{index:03d}_{label}")
            split = f"每個 <b>{names}</b> 值存一個檔，共 <b>{r.n_files}</b> 個（檔名規則 {ex}）"
        else:
            split = "整個量測存成 <b>一個檔</b>" + ("（多維 .nc）" if len(r.loops) > 1 else "")
        for e in getattr(r, "extras", []) or []:
            names = "、".join(lp.axis_name for lp in r.loops[:e["depth"]])
            split += (f"<br>＋ 額外 Data：<b>{e['file_name']}</b>（{'+'.join(e['formats']) or '未選格式'}），"
                      + (f"每個 {names} 值一個檔" if e["depth"] else "整個量測一個檔"))
        self.where.setText(f"<b>存到</b> {folder}<br><b>檔名</b> {fs['file_name']}"
                           f"（{dict(COLLISIONS).get(fs['collision'], fs['collision'])}）<br>{split}"
                           + ("<br>" + theme.span("原始資料另存 _raw/（settings.yaml data.raw_backup）", "muted")
                              if raw_backup() else ""))
        # 檢查
        self.issue_list.clear()
        for i in r.issues:
            icon = {"error": "✖", "warning": "⚠", "info": "ℹ"}[i.level]
            it = QtWidgets.QListWidgetItem(f"{icon} {i.message}")
            it.setForeground(QtGui.QBrush(theme.qc({"error": "err", "warning": "warn", "info": "accent"}[i.level])))
            it.setData(Qt.ItemDataRole.UserRole, i.block_id)
            self.issue_list.addItem(it)
        parts = "、".join(f"{k} {format_duration(v)}" for k, v in r.est_breakdown.items() if v)
        err = sum(1 for i in r.issues if i.level == "error")
        self.est.setText(f"{theme.span(f'✖ 有 {err} 個錯誤', 'err') if err else theme.span('✔ 可以執行', 'ok')}"
                         f"　·　<b>{r.total_points:,}</b> 點　·　預估 <b>{format_duration(r.est_seconds)}</b>"
                         + (f"<br>{theme.span(parts, 'muted')}" if parts else ""))
        self.setTabText(2, f"檢查與估時{f'（{err}）' if err else ''}")

    def _issue_clicked(self, it: QtWidgets.QListWidgetItem) -> None:
        bid = it.data(Qt.ItemDataRole.UserRole)
        if bid:
            self.doc.select(bid)


class NewProjectDialog(QtWidgets.QDialog):
    """在論文庫新增專案：名稱、簡稱、說明、顏色。"""

    COLORS = ["#4f7cff", "#16a34a", "#d97706", "#dc2626", "#9333ea", "#0891b2", "#64748b"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("在論文庫新增專案")
        self.setMinimumWidth(380)
        f = QtWidgets.QFormLayout(self)
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText("例如 YIG mirror coupling")
        self.short = QtWidgets.QLineEdit()
        self.short.setPlaceholderText("簡稱（可空白）")
        self.desc = QtWidgets.QPlainTextEdit()
        self.desc.setMaximumHeight(70)
        self.color = QtWidgets.QComboBox()
        for c in self.COLORS:
            pm = QtGui.QPixmap(14, 14)
            pm.fill(QtGui.QColor(c))
            self.color.addItem(QtGui.QIcon(pm), c, c)
        f.addRow("名稱", self.name)
        f.addRow("簡稱", self.short)
        f.addRow("說明", self.desc)
        f.addRow("顏色", self.color)
        note = QtWidgets.QLabel("專案建立在論文庫裡（論文、數據標籤、量測數據都歸在這個專案）。")
        note.setWordWrap(True)
        note.setStyleSheet("color:palette(placeholder-text); font-size:11px;")
        f.addRow(note)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("建立")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _ok(self) -> None:
        if not self.name.text().strip():
            self.name.setFocus()
            return
        self.accept()

    def values(self) -> dict:
        return {"name": self.name.text().strip(), "short_name": self.short.text().strip(),
                "description": self.desc.toPlainText().strip(), "color": self.color.currentData() or ""}


def _num(v: Any, unit: str) -> float:
    if v in (None, ""):
        return 0.0
    try:
        from ....core.units import split_unit

        return float(parse_quantity(v, unit)) / (split_unit(unit)[1] or 1.0)
    except Exception:  # noqa: BLE001
        return 0.0
