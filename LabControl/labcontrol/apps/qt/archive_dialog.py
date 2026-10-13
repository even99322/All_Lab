"""「檔案 → 專題歸檔…」（0.0.31）：選數據（檔案或整個資料夾）→ 選專案 → 複製到 NAS 數據集散地/<專案>/。"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional

from PyQt6 import QtCore, QtWidgets

from ...data import archive
from ...data.naming import data_root, project_folder_name
from .worker import run_bg


class ArchiveDialog(QtWidgets.QDialog):
    _progress = QtCore.pyqtSignal(int, int, str)

    def __init__(self, parent=None, project: Optional[dict] = None, sources: Optional[List[Path]] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("專題歸檔")
        self.resize(640, 460)
        self.sources: List[Path] = list(sources or [])
        self._stop = threading.Event()
        v = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel("把量測數據複製到實驗室 NAS 的「數據集散地／專案」資料夾，保留日期資料夾結構。"
                                 "原本的檔案不會動；NAS 上已經有一樣的檔會跳過。")
        intro.setWordWrap(True)
        v.addWidget(intro)
        row = QtWidgets.QHBoxLayout()
        b_files = QtWidgets.QPushButton("選數據檔…")
        b_files.clicked.connect(self._pick_files)
        b_dir = QtWidgets.QPushButton("選整個資料夾…")
        b_dir.clicked.connect(self._pick_dir)
        b_clear = QtWidgets.QPushButton("清除")
        b_clear.clicked.connect(lambda: (self.sources.clear(), self._refresh()))
        for b in (b_files, b_dir, b_clear):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.list = QtWidgets.QListWidget()
        v.addWidget(self.list, 1)
        form = QtWidgets.QFormLayout()
        self.project = QtWidgets.QComboBox()
        self.project.setEditable(True)
        self.project.setObjectName("archiveProject")
        form.addRow("專案", self.project)
        dest_row = QtWidgets.QHBoxLayout()
        self.dest = QtWidgets.QLineEdit()
        self.dest.setPlaceholderText("尋找 NAS 的數據集散地…")
        b_dest = QtWidgets.QToolButton()
        b_dest.setText("…")
        b_dest.clicked.connect(self._pick_dest)
        dest_row.addWidget(self.dest, 1)
        dest_row.addWidget(b_dest)
        form.addRow("NAS 數據集散地", dest_row)
        v.addLayout(form)
        self.where = QtWidgets.QLabel("")
        self.where.setWordWrap(True)
        self.where.setProperty("role", "muted")
        v.addWidget(self.where)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setVisible(False)
        v.addWidget(self.bar)
        bb = QtWidgets.QDialogButtonBox()
        self.b_go = bb.addButton("開始歸檔", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QtWidgets.QDialogButtonBox.StandardButton.Close)
        self.b_go.clicked.connect(self.start)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._progress.connect(self._show_progress)
        self.project.currentTextChanged.connect(lambda _t: self._refresh())
        self.dest.textChanged.connect(lambda _t: self._refresh())
        self._fill_projects(project)
        run_bg(lambda: archive.find_nas(), self._found_nas, lambda m: None)
        self._refresh()

    # ---- 選擇 ----------------------------------------------------------------------------
    def _fill_projects(self, cur: Optional[dict]) -> None:
        from ...integrations import platform as plat

        names = [p["name"] for p in plat.PROJECTS.items if p.get("name")]
        if cur and cur.get("name") and cur["name"] not in names:
            names.insert(0, cur["name"])
        self.project.addItems(names)
        if cur and cur.get("name"):
            self.project.setCurrentText(cur["name"])

        def ok(items):
            have = {self.project.itemText(i) for i in range(self.project.count())}
            for p in items or []:
                if p.get("name") and p["name"] not in have:
                    self.project.addItem(p["name"])
        run_bg(lambda: plat.PROJECTS.get(), ok, lambda m: None)

    def _found_nas(self, path: Optional[str]) -> None:
        if not self.dest.text().strip():
            if path:
                self.dest.setText(path)
            else:
                self.dest.setPlaceholderText("連不到 NAS（請連實驗室網路或 VPN），或按 … 選位置")

    def _pick_files(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "選數據檔", str(data_root()),
                                                          "量測數據 (*.nc *.hdf5 *.h5 *.lm.h5);;所有檔案 (*)")
        self.sources += [Path(f) for f in files if Path(f) not in self.sources]
        self._refresh()

    def _pick_dir(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "選資料夾", str(data_root()))
        if d and Path(d) not in self.sources:
            self.sources.append(Path(d))
        self._refresh()

    def _pick_dest(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "NAS 的數據集散地", self.dest.text() or "")
        if d:
            self.dest.setText(d)

    def items(self):
        if not self.sources or not self.dest.text().strip() or not self.project.currentText().strip():
            return []
        return archive.plan(self.sources, project_folder_name(self.project.currentText()),
                            Path(self.dest.text().strip()), data_root())

    def _refresh(self) -> None:
        self.list.clear()
        for s in self.sources:
            self.list.addItem(("📁 " if s.is_dir() else "📄 ") + str(s))
        it = self.items()
        if it:
            size = sum(s.stat().st_size for s, _ in it if s.exists())
            self.where.setText(f"{len(it)} 個檔、{size / 1e6:.1f} MB → {it[0][1].parent}"
                               + ("…" if len({d.parent for _, d in it}) > 1 else ""))
        else:
            self.where.setText("選數據、專案與 NAS 位置後開始。")
        self.b_go.setEnabled(bool(it))

    # ---- 複製 ----------------------------------------------------------------------------
    def start(self) -> None:
        items = self.items()
        if not items:
            return
        self.b_go.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 1000)

        def ok(r):
            self.bar.setValue(1000)
            msg = f"✔ 歸檔完成：複製 {r['copied']} 個、已存在跳過 {r['skipped']} 個"
            if r["errors"]:
                msg += f"；{len(r['errors'])} 個失敗：\n" + "\n".join(r["errors"][:10])
            self.where.setText(msg)
            self.b_go.setEnabled(True)

        def fail(m):
            self.where.setText(f"❌ 歸檔失敗：{m}")
            self.b_go.setEnabled(True)
        run_bg(lambda: archive.copy(items, lambda d, t, n: self._progress.emit(d, t, n), self._stop), ok, fail)

    def _show_progress(self, done: int, total: int, name: str) -> None:
        self.bar.setValue(int(done * 1000 / total) if total else 0)
        self.where.setText(f"複製中：{name}（{done / 1e6:.1f} / {total / 1e6:.1f} MB）")

    def reject(self) -> None:
        self._stop.set()
        super().reject()
