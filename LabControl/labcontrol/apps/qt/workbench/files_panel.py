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
from typing import Any, Callable, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from ....core.units import parse_quantity
from ....data.naming import (DEFAULT_PATTERN, project_folder_path, data_root, day_folder, folder_pattern, list_project_folders,
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
    #: 0.0.31：論文庫專案要換了（舊的專案 dict 或 None）／已經換好（新的）——專案範本同步用
    project_changing = QtCore.pyqtSignal(object)
    project_changed = QtCore.pyqtSignal(object)
    def __init__(self, doc: WorkbenchDoc, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self._loading = False
        #: 0.0.31：在量測電腦（節點）上執行時：(節點名稱, 節點回報的存檔資訊 {root, pattern, sep, folders} 或 None)
        self.remote: Optional[tuple] = None
        self.addTab(self._file_tab(), "檔案")
        self.addTab(self._run_tab(), "執行")
        self.addTab(self._check_tab(), "檢查與估時")
        from .data_panel import DataPanel

        self.data_panel = DataPanel(doc)              # 1.0.2：點 Data / 去背方塊 → 在這裡設定
        self.addTab(self.data_panel, "Data 方塊")
        doc.selection_changed.connect(self._show_data_tab)
        doc.changed.connect(self._on_changed)
        theme.on_change(self, lambda w: w._refresh_derived(), call_now=False)
        self.load()
        self.reload_projects()

    def _show_data_tab(self, nid) -> None:
        from ....scheme.background import BG_ID

        b = self.doc.node(nid) if nid else None
        if nid == BG_ID or (b is not None and b.kind in ("save", "debg")):
            self.setCurrentWidget(self.data_panel)

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
        self.lab_project.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.lab_project.setMinimumContentsLength(10)
        proj_refresh = QtWidgets.QToolButton()
        proj_refresh.setText("⟳")
        proj_refresh.setToolTip("重新讀取論文庫的專案")
        proj_refresh.clicked.connect(lambda: self.reload_projects(force=True))
        # 1.0.10：子專案——選的專案底下還有子專案 → 右邊多一個選單（放不下就換到下一行），可以一層一層選下去
        from ..flow_layout import FlowLayout

        self.proj_flow = QtWidgets.QWidget()
        self.proj_flow_layout = FlowLayout(self.proj_flow)
        self.proj_flow_layout.addWidget(self.lab_project)
        self.sub_projects: list = []                  # 子專案的下拉選單（第 2 層以後）
        self._proj_items: list = []
        self.b_subproject = QtWidgets.QToolButton()
        self.b_subproject.setText("＋子")
        self.b_subproject.setToolTip("在目前選的專案底下建立子專案（建在論文庫；也可以在論文庫的專案專區建立）")
        self.b_subproject.clicked.connect(self._new_subproject)
        proj_row = QtWidgets.QHBoxLayout()
        proj_row.addWidget(self.proj_flow, 1)
        proj_row.addWidget(self.b_subproject, 0, Qt.AlignmentFlag.AlignTop)
        proj_row.addWidget(proj_refresh, 0, Qt.AlignmentFlag.AlignTop)
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
        self._browse_btn = browse
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
        self.root.editingFinished.connect(lambda: self.remote is None and (
            self._out("root", self.root.text().strip()), self._fill_folders()))
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
        self.repeat = ispin(1, 400)
        self.repeat.setObjectName("repeatShots")
        self.repeat.setToolTip("每個點量幾次。> 1：每次都保留，量完開「QC 挑選」視窗依 Mag / Phase / IQ 挑每點要用的那一筆\n"
                               "（手動步進時用「保留此點所有暫存」也一樣）")
        self.dip = QtWidgets.QCheckBox("吸收峰型態變化（1 ⇌ 2 個）自動暫停")
        f.addRow("每點量測前等待", self.delay)
        f.addRow("移到起點速率", self.approach)
        f.addRow("結束後停靠速率", self.park_rate)
        f.addRow("量測錯誤", self.on_error)
        f.addRow("重試次數", self.retries)
        f.addRow("每點重複量測", self.repeat)
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
        self.repeat.valueChanged.connect(lambda v: self._run("repeat_shots", int(v) if v > 1 else None))
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
            if key == "root" and self.remote is not None:    # 在量測電腦上執行：根目錄是那台的，不動這台的設定
                continue
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

    @staticmethod
    def _project_label(p: dict) -> str:
        label = p["name"] + (f"（{p['short_name']}）" if p.get("short_name") and p["short_name"] != p["name"] else "")
        if p.get("data"):
            label += f" · {p['data']} 筆數據"
        return label

    def _children(self) -> dict:
        """上層 id → 子專案（依原本的順序）；上層不存在 → 當成最上層。"""
        ids = {str(p["id"]) for p in self._proj_items}
        kids: dict = {}
        for p in self._proj_items:
            par = str(p.get("parent") or "")
            kids.setdefault(par if par in ids and par != str(p["id"]) else "", []).append(p)
        return kids

    def _chain(self, pid: str) -> list:
        """[最上層, …, 這個專案] 的 id。"""
        by = {str(p["id"]): p for p in self._proj_items}
        out, cur, seen = [], str(pid or ""), set()
        while cur and cur in by and cur not in seen:
            seen.add(cur)
            out.insert(0, cur)
            par = str(by[cur].get("parent") or "")
            cur = par if par in by else ""
        if not out and pid:
            cur = self.current_project()
            ids = [str(x) for x in (cur.get("path_ids") or [])] if str(cur.get("id")) == str(pid) else []
            out = ids or [str(pid)]
        return out

    def _ref(self, p: dict) -> dict:
        return {"id": str(p["id"]), "name": p["name"]}

    def _fill_projects(self, items: list) -> None:
        self._proj_items = [dict(p, id=str(p["id"])) for p in (items or [])]
        kids = self._children()
        cur = self.current_project()
        chain = self._chain(cur.get("id", "")) if cur.get("id") else []
        cb = self.lab_project
        cb.blockSignals(True)
        cb.clear()
        cb.addItem("（不指定專案）", None)
        seen = False
        for p in kids.get("", []):
            n = len(kids.get(p["id"], []))
            cb.addItem(self._project_label(p) + (f"　▸ {n} 個子專案" if n else ""), self._ref(p))
            if p.get("color"):
                cb.setItemData(cb.count() - 1, QtGui.QColor(p["color"]), Qt.ItemDataRole.DecorationRole)
            seen |= bool(chain) and p["id"] == chain[0]
        if cur.get("id") and not seen:   # 沒登入或專案讀不到：保留方案裡記的
            top = {"id": str(cur["id"]), "name": cur.get("name", "")}
            cb.addItem(f"{' / '.join(cur.get('path') or [cur.get('name') or cur['id']])}（方案裡記的）", top)
            chain = [str(cur["id"])]
        cb.addItem("＋ 新專案…", NEW_PROJECT)
        idx = 0
        for i in range(cb.count()):
            d = cb.itemData(i)
            if isinstance(d, dict) and chain and d["id"] == chain[0]:
                idx = i
        cb.setCurrentIndex(idx)
        cb.blockSignals(False)
        self._build_sub_combos(chain if idx else [])

    def _build_sub_combos(self, chain: list) -> None:
        """第 2 層以後的選單：選到的專案有子專案 → 右邊加一個（第一項 =「只選上層」）。"""
        for c in self.sub_projects:
            self.proj_flow_layout.removeWidget(c)
            c.hide()
            c.deleteLater()
        self.sub_projects = []
        kids = self._children()
        by = {p["id"]: p for p in self._proj_items}
        for level, pid in enumerate(chain):
            children = kids.get(pid, [])
            if pid not in by or not children:          # 沒有子專案：不多一個選單
                break
            c = QtWidgets.QComboBox()
            c.setObjectName(f"subProject{level + 1}")
            c.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
            c.setToolTip(f"「{by[pid]['name']}」底下的子專案（第 {level + 2} 層）")
            c.addItem(f"（只選「{by[pid]['name']}」）", self._ref(by[pid]))
            for ch in children:
                n = len(kids.get(ch["id"], []))
                c.addItem(self._project_label(ch) + (f"　▸ {n}" if n else ""), self._ref(ch))
                if ch.get("color"):
                    c.setItemData(c.count() - 1, QtGui.QColor(ch["color"]), Qt.ItemDataRole.DecorationRole)
            c.addItem("＋ 新子專案…", ("new", pid))
            nxt = chain[level + 1] if level + 1 < len(chain) else ""
            c.setCurrentIndex(0)
            for i in range(c.count()):
                d = c.itemData(i)
                if isinstance(d, dict) and nxt and d["id"] == nxt:
                    c.setCurrentIndex(i)
            c.activated.connect(lambda _i, c=c: self._sub_chosen(c))
            self.proj_flow_layout.addWidget(c)
            c.show()
            self.sub_projects.append(c)
            if not nxt:
                break
        self.proj_flow.updateGeometry()

    def reload_projects(self, force: bool = False) -> None:
        from ....integrations import platform as plat

        self._fill_projects(plat.PROJECTS.items)
        # 0.0.31：沒有登入也查得到（Hub 用論文庫的服務帳號查）

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
        self._select_project(d if isinstance(d, dict) else None)

    def _sub_chosen(self, c: QtWidgets.QComboBox) -> None:
        d = c.currentData()
        if isinstance(d, tuple) and d and d[0] == "new":
            p = self._new_project_dialog(parent=d[1])
            if p is None:
                self._fill_projects(self._project_items())
                return
            d = {"id": str(p["id"]), "name": p["name"]}
        if isinstance(d, dict):
            self._select_project(d)

    def _new_subproject(self) -> None:
        cur = self.current_project()
        if not cur.get("id"):
            QtWidgets.QMessageBox.information(self, "子專案", "先選一個專案，子專案會建在它底下。")
            return
        p = self._new_project_dialog(parent=str(cur["id"]))
        if p is not None:
            self._select_project({"id": str(p["id"]), "name": p["name"]})

    def _project_value(self, d: dict) -> dict:
        """方案裡記的專案：{"id", "name"}；子專案再加上層的名稱與 id（path / path_ids）。"""
        chain = self._chain(d["id"])
        by = {p["id"]: p for p in self._proj_items}
        out = {"id": str(d["id"]), "name": d["name"]}
        if len(chain) > 1 and all(x in by for x in chain):
            out["path"] = [by[x]["name"] for x in chain]
            out["path_ids"] = list(chain)
        return out

    def _select_project(self, d: Optional[dict]) -> None:
        d = self._project_value(d) if d else None
        old = self.current_project() or None
        if (old or {}).get("id") == ((d or {}).get("id")) and (old or {}).get("path") == (d or {}).get("path"):
            self._build_sub_combos(self._chain(d["id"]) if d else [])
            return                                      # 選了同一個專案
        self.project_changing.emit(old)
        folder = remembered_folder(d["id"]) if d else ""

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
        self.project_changed.emit(d)

    # ---- 在量測電腦上執行（0.0.31）------------------------------------------------
    def set_remote(self, name: Optional[str], info: Optional[dict] = None) -> None:
        """執行位置換到量測電腦 name（None = 這台電腦）：存檔位置改成顯示那台電腦的資料根目錄與專案資料夾。"""
        self.remote = (name, dict(info) if isinstance(info, dict) else None) if name else None
        remote = self.remote is not None
        self.root.setEnabled(not remote)
        self._browse_btn.setEnabled(not remote)
        if remote:
            root = (self.remote[1] or {}).get("root")
            self.root.setToolTip(f"在量測電腦 {name} 上執行：存到那台電腦自己的資料根目錄（它的 settings.yaml data.root）")
            self.root.setPlaceholderText(f"量測電腦 {name}：" + (root or "它的 data.root（預設 桌面\\數據集散地）"))
        else:
            self.root.setToolTip("")
            self.root.setPlaceholderText(str(data_root()))
        self.root.setText("" if remote else self.doc.scheme.output.get("root", ""))
        self._fill_folders()
        self._refresh_derived()

    def _remote_folder_text(self, now) -> str:
        """量測電腦上的存檔資料夾（用那台的路徑格式顯示）。"""
        name, info = self.remote
        info = info or {}
        o = self.doc.scheme.output
        pattern = o.get("folder_pattern") or info.get("pattern") or DEFAULT_PATTERN
        from ....data.naming import _fmt_tokens

        sub = pattern.format(**_fmt_tokens(now), project=project_folder(o))
        root = info.get("root") or "<data.root>"
        sep = info.get("sep") or ("\\" if "\\" in root or ":" in root[:3] else "/")
        return f"（量測電腦 {name}）" + root.rstrip("\\/") + sep + sub.replace("/", sep)

    # ---- 專案資料夾（0.0.30）-----------------------------------------------------
    def _root(self):
        return data_root(self.doc.scheme.output.get("root") or None)

    def _fill_folders(self) -> None:
        o = self.doc.scheme.output
        cb = self.proj_folder
        cb.blockSignals(True)
        cb.clear()
        remote_info = (self.remote[1] or {}) if self.remote else None
        folders = list(remote_info.get("folders") or []) if remote_info is not None else \
            list_project_folders(self._root())
        for name in folders:
            cb.addItem(name)
        cb.setEditText(o.get("project_folder") or "")
        auto = project_folder({k: v for k, v in o.items() if k != "project_folder"})
        exists = auto in folders if remote_info is not None else (self._root() / auto).is_dir()
        cb.lineEdit().setPlaceholderText(f"自動：{auto}" + ("（已有這個資料夾）" if exists else "（會建立）"))
        uses = "{project}" in (o.get("folder_pattern") or (remote_info or {}).get("pattern") or folder_pattern())
        cb.setEnabled(uses)
        if not uses:
            cb.lineEdit().setPlaceholderText("資料夾規則沒有 {project}：不分專案資料夾")
        cb.blockSignals(False)

    def _folder_chosen(self) -> None:
        text = self.proj_folder.currentText().strip()
        name = project_folder_path(text) if text else ""         # 1.0.10：可以用 / 分層（子專案）
        if name != text:
            self.proj_folder.setEditText(name)
        if name == (self.doc.scheme.output.get("project_folder") or ""):
            return
        self._out("project_folder", name or None)
        remember_folder(self.current_project().get("id", ""), name)
        self._fill_folders()

    def _open_folder(self) -> None:
        o = self.doc.scheme.output
        if self.remote is not None:
            QtWidgets.QMessageBox.information(self, "存檔資料夾", "量測在別台電腦上執行，存到：\n"
                                              + self._remote_folder_text(_dt.datetime.now()))
            return
        folder = day_folder(self._root(), folder_pattern(o.get("folder_pattern")), _dt.datetime.now(),
                            project_folder(o))
        while not folder.is_dir() and folder != folder.parent:       # 還沒建立：打開最近一層已經有的
            folder = folder.parent
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))

    def _project_items(self) -> list:
        from ....integrations import platform as plat

        return plat.PROJECTS.items or list(getattr(self, "_proj_items", []))

    def _new_project_dialog(self, parent: str = ""):
        """在論文庫建立新專案（需要登入，且論文庫允許你建立專案）；parent = 上層專案 id → 子專案。"""
        from ....integrations import platform as plat

        s = plat.current_session()
        if s is None:
            QtWidgets.QMessageBox.information(self, "新專案", "請先登入量子工程平台（選單「平台」→ 登入）。")
            return None
        by = {p["id"]: p for p in self._proj_items}
        dlg = NewProjectDialog(self, parent_name=" / ".join(by[x]["name"] for x in self._chain(parent) if x in by)
                               if parent else "")
        if not dlg.exec():
            return None
        try:
            p = s.create_project(**dlg.values(), **({"parent": parent} if parent else {}))
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
            if self.remote is None:
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
            self.repeat.setValue(max(1, int(run.get("repeat_shots", 1) or 1)))
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
            if self.remote is not None:
                folder = self._remote_folder_text(now)
            else:
                folder = day_folder(data_root(s.output.get("root") or None),
                                    folder_pattern(s.output.get("folder_pattern")), now, project_folder(s.output))
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

    def __init__(self, parent=None, parent_name: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(f"在「{parent_name}」底下新增子專案" if parent_name else "在論文庫新增專案")
        self.setMinimumWidth(380)
        f = QtWidgets.QFormLayout(self)
        if parent_name:
            f.addRow("上層專案", QtWidgets.QLabel(parent_name))
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
