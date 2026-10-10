"""Companion panel (opened from the pet's hover bar). Follows the app theme (standard widgets).

Tabs, unlocked by the pet's level (scientific functions are never locked):
  Quick actions (Lv.1; Lv.40 dock: reorder + overview; Lv.50: presets) | Backpack (Lv.1; drag-to-pet Lv.10)
  Recent research (Lv.20) | Notifications (Lv.30) | Dashboard (Lv.50)
The EXP bar is in Settings > Pet, not next to the pet.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPushButton, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from app.pet import actions, backpack, journal, progress, registry, store

UNLOCK = {"recent": 20, "notes": 30, "dashboard": 50}
KIND_ICON = {"measurement": "◈", "image": "▣", "text": "≣", "file": "▢", "analysis": "∿", "export": "⇪",
             "debackground": "BG", "operation": "✎"}
NOTE_ICON = {"info": "ℹ", "success": "✓", "warning": "⚠", "error": "⛔"}
_open_viewers = []


def open_measurement(path: str) -> None:
    from app.gui.main_window import MainWindow

    window = MainWindow()
    _open_viewers.append(window)
    window.show()
    window.open_file(path)


class CompanionPanel(QFrame):
    action_requested = Signal(str)
    changed = Signal()

    def __init__(self, text, tracker=None, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.text = text
        self.tracker = tracker
        self.setObjectName("petPanel")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumSize(460, 360)
        root = QVBoxLayout(self)
        self.title = QLabel(); f = self.title.font(); f.setBold(True); f.setPointSize(f.pointSize() + 2); self.title.setFont(f)
        root.addWidget(self.title)
        self.tabs = QTabWidget(); self.tabs.setObjectName("petTabs")
        root.addWidget(self.tabs, 1)
        self._build_quick(); self._build_backpack(); self._build_recent(); self._build_notes(); self._build_dashboard()
        self.slot_buttons: list[QToolButton] = []

    def t(self, key: str, default: str = "") -> str:
        return self.text(key, default or key)

    # -- tabs ------------------------------------------------------------------------------------
    def _build_quick(self):
        w = QWidget(); v = QVBoxLayout(w)
        self.dock = QLabel(); self.dock.setWordWrap(True); self.dock.setObjectName("petDock")
        v.addWidget(self.dock)
        self.quick_title = QLabel(); qf = self.quick_title.font(); qf.setBold(True); self.quick_title.setFont(qf)
        v.addWidget(self.quick_title)
        self.grid_host = QWidget(); self.grid = QGridLayout(self.grid_host); self.grid.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.grid_host)
        self.quick_hint = QLabel(); self.quick_hint.setWordWrap(True)
        v.addWidget(self.quick_hint)
        row = QHBoxLayout()
        self.preset_save = QPushButton(); self.preset_save.clicked.connect(self._save_preset)
        self.preset_load = QToolButton(); self.preset_load.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        row.addWidget(self.preset_save); row.addWidget(self.preset_load); row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        self.tabs.addTab(w, "")

    def _build_backpack(self):
        w = QWidget(); v = QVBoxLayout(w)
        self.bag_info = QLabel(); self.bag_info.setWordWrap(True)
        self.bag_list = QListWidget(); self.bag_list.setObjectName("petBag")
        self.bag_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.bag_list.itemDoubleClicked.connect(lambda _i: self._bag_open())
        v.addWidget(self.bag_info); v.addWidget(self.bag_list, 1)
        grid = QGridLayout()
        self.bag_buttons = {}
        for i, (key, fn) in enumerate((("add_current", self._bag_add_current), ("open", self._bag_open), ("copy", self._bag_copy),
                                       ("copy_all", self._bag_copy_all), ("export", self._bag_export), ("remove", self._bag_remove),
                                       ("clear", self._bag_clear))):
            b = QPushButton(); b.clicked.connect(fn); b.setObjectName(f"petBag_{key}")
            self.bag_buttons[key] = b
            grid.addWidget(b, i // 4, i % 4)
        v.addLayout(grid)
        self.tabs.addTab(w, "")

    def _build_recent(self):
        w = QWidget(); v = QVBoxLayout(w)
        self.recent_list = QListWidget(); self.recent_list.setObjectName("petRecent")
        self.recent_list.itemDoubleClicked.connect(lambda item: self._recent_open(item))
        row = QHBoxLayout()
        self.recent_open_btn = QPushButton(); self.recent_open_btn.clicked.connect(lambda: self._recent_open(self.recent_list.currentItem()))
        self.recent_pin_btn = QPushButton(); self.recent_pin_btn.clicked.connect(self._recent_pin)
        row.addWidget(self.recent_open_btn); row.addWidget(self.recent_pin_btn); row.addStretch(1)
        v.addWidget(self.recent_list, 1); v.addLayout(row)
        self.tabs.addTab(w, "")

    def _build_notes(self):
        w = QWidget(); v = QVBoxLayout(w)
        self.notes_list = QListWidget(); self.notes_list.setObjectName("petNotes")
        row = QHBoxLayout()
        self.notes_read = QPushButton(); self.notes_read.clicked.connect(lambda: (journal.mark_all_read(), self.refresh()))
        self.notes_clear = QPushButton(); self.notes_clear.clicked.connect(lambda: (journal.clear_notes(), self.refresh()))
        row.addWidget(self.notes_read); row.addWidget(self.notes_clear); row.addStretch(1)
        v.addWidget(self.notes_list, 1); v.addLayout(row)
        self.tabs.addTab(w, "")

    def _build_dashboard(self):
        w = QWidget(); v = QVBoxLayout(w)
        self.dash = QLabel(); self.dash.setTextFormat(Qt.TextFormat.RichText); self.dash.setWordWrap(True)
        self.dash.setObjectName("petDashboard"); self.dash.setAlignment(Qt.AlignmentFlag.AlignTop)
        v.addWidget(self.dash, 1)
        self.tabs.addTab(w, "")

    # -- refresh ---------------------------------------------------------------------------------
    def refresh(self) -> None:
        state = store.load()
        info = registry.get(state["current"])
        if info is None:
            return
        level = store.level_of(state, info.id)
        self.level = level
        self.title.setText(f"{self.t(info.name_key)} · Lv.{level}")
        names = (("pet.tab.quick", 1), ("pet.tab.backpack", 1), ("pet.tab.recent", UNLOCK["recent"]),
                 ("pet.tab.notes", UNLOCK["notes"]), ("pet.tab.dashboard", UNLOCK["dashboard"]))
        unread = journal.unread_count()
        for i, (key, lv) in enumerate(names):
            label = self.t(key)
            if key == "pet.tab.notes" and unread and level >= lv:
                label += f" ({unread})"
            self.tabs.setTabText(i, label if level >= lv else f"🔒 {label}")
            self.tabs.setTabEnabled(i, level >= lv)
            self.tabs.setTabToolTip(i, "" if level >= lv else self.t("pet.panel.unlock_at").format(level=lv))
        self._refresh_quick(state, level)
        self._refresh_backpack(level)
        self._refresh_recent()
        self._refresh_notes()
        self._refresh_dashboard(state, info, level)

    def _refresh_quick(self, state, level):
        _bag, slots = progress.capacity(level)
        dock = level >= 40
        self.dock.setVisible(dock)
        if dock:
            recent = journal.load()["recent"][:3]
            links = ", ".join(r["title"] for r in recent) or "—"
            self.dock.setText(self.t("pet.dock.summary").format(bag=len(backpack.items()), cap=progress.capacity(level)[0],
                                                                 unread=journal.unread_count(), recent=links))
        self.quick_title.setText(self.t("pet.panel.quick").format(n=slots))
        for b in self.slot_buttons:
            b.setParent(None)
        self.slot_buttons = []
        chosen = state["quick_actions"][:slots]
        for i in range(slots):
            action = actions.get(chosen[i]) if i < len(chosen) else None
            b = QToolButton(); b.setObjectName(f"petSlot{i}")
            b.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            b.setMinimumWidth(150)
            b.setText(f"{action.glyph}  {self.t(action.label_key)}" if action else self.t("pet.panel.empty"))
            if action:
                b.clicked.connect(lambda _=False, a=action.id: (self.hide(), self.action_requested.emit(a)))
            else:
                b.clicked.connect(b.showMenu)
            b.setMenu(self._slot_menu(i, slots, dock))
            self.grid.addWidget(b, i // 2, i % 2)
            self.slot_buttons.append(b)
        self.quick_hint.setText(self.t("pet.panel.quick_hint") + (" " + self.t("pet.dock.reorder") if dock else ""))
        presets_on = level >= 50
        self.preset_save.setVisible(presets_on); self.preset_load.setVisible(presets_on)
        if presets_on:
            self.preset_save.setText(self.t("pet.preset.save"))
            self.preset_load.setText(self.t("pet.preset.load"))
            menu = QMenu(self)
            for name, quick in journal.presets().items():
                a = menu.addAction(name)
                a.triggered.connect(lambda _=False, q=quick: self._apply_preset(q))
            if not journal.presets():
                menu.addAction(self.t("pet.preset.none")).setEnabled(False)
            self.preset_load.setMenu(menu)

    def _slot_menu(self, index: int, slots: int, dock: bool) -> QMenu:
        menu = QMenu(self)
        for action in actions.ACTIONS:
            a = menu.addAction(f"{action.glyph}  {self.t(action.label_key)}")
            a.triggered.connect(lambda _=False, i=index, aid=action.id: self._set_slot(i, aid))
        menu.addSeparator()
        if dock:
            left = menu.addAction(self.t("pet.dock.left")); left.setEnabled(index > 0)
            left.triggered.connect(lambda _=False, i=index: self._move_slot(i, -1))
            right = menu.addAction(self.t("pet.dock.right")); right.setEnabled(index < slots - 1)
            right.triggered.connect(lambda _=False, i=index: self._move_slot(i, 1))
        clear = menu.addAction(self.t("pet.panel.clear"))
        clear.triggered.connect(lambda _=False, i=index: self._set_slot(i, None))
        return menu

    def _quick_list(self, state) -> list[str]:
        _bag, slots = progress.capacity(store.level_of(state, state["current"]))
        quick = list(state["quick_actions"])
        return (quick + [""] * slots)[:max(slots, len(quick))]

    def _set_slot(self, index: int, action_id: str | None) -> None:
        state = store.load()
        quick = self._quick_list(state)
        quick[index] = action_id or ""
        state["quick_actions"] = quick
        store.save(state)
        self.refresh(); self.changed.emit()

    def _move_slot(self, index: int, step: int) -> None:
        state = store.load()
        quick = self._quick_list(state)
        j = index + step
        if 0 <= j < len(quick):
            quick[index], quick[j] = quick[j], quick[index]
            state["quick_actions"] = quick
            store.save(state)
        self.refresh(); self.changed.emit()

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, self.t("pet.preset.save"), self.t("pet.preset.name"))
        if ok and name.strip():
            journal.save_preset(name.strip()[:40], store.load()["quick_actions"])
            self.refresh()

    def _apply_preset(self, quick) -> None:
        state = store.load()
        state["quick_actions"] = list(quick)
        store.save(state)
        self.refresh(); self.changed.emit()

    # -- backpack --------------------------------------------------------------------------------
    def _refresh_backpack(self, level):
        cap = progress.capacity(level)[0]
        values = backpack.items()
        hint = self.t("pet.bag.drag") if level >= 10 else self.t("pet.bag.drag_locked")
        self.bag_info.setText(self.t("pet.bag.count").format(n=len(values), cap=cap) + "  " + hint)
        self.bag_list.clear()
        for item in values:
            li = QListWidgetItem(f"{KIND_ICON.get(item['kind'], '▢')}  {item['title']}")
            li.setData(Qt.ItemDataRole.UserRole, item["id"])
            li.setToolTip(item.get("path") or item.get("text", "")[:300] or item["title"])
            self.bag_list.addItem(li)
        for key, b in self.bag_buttons.items():
            b.setText(self.t(f"pet.bag.{key}"))

    def _selected_bag(self) -> list[dict]:
        ids = {i.data(Qt.ItemDataRole.UserRole) for i in self.bag_list.selectedItems()}
        return [it for it in backpack.items() if it["id"] in ids]

    def _bag_add_current(self) -> None:
        viewer = self.tracker.viewer() if self.tracker is not None else None
        path = getattr(getattr(viewer, "experiment", None), "source_path", None)
        if not path:
            QMessageBox.information(self, self.t("pet.tab.backpack"), self.t("pet.action.need_viewer"))
            return
        try:
            backpack.add_path(str(path), progress.capacity(self.level)[0])
        except backpack.BackpackFull:
            QMessageBox.information(self, self.t("pet.tab.backpack"), self.t("pet.drop.full"))
        except OSError:
            QMessageBox.information(self, self.t("pet.tab.backpack"), self.t("pet.drop.failed"))
        self.refresh()

    def _bag_open(self) -> None:
        for item in self._selected_bag()[:3]:
            if item["kind"] == "measurement":
                if Path(item["path"]).exists():
                    open_measurement(item["path"])
                else:
                    QMessageBox.information(self, item["title"], self.t("pet.bag.missing"))
            elif item["kind"] == "text":
                QMessageBox.information(self, item["title"], item.get("text", ""))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(backpack.payload_path(item))))

    @staticmethod
    def _as_text(item: dict) -> str:
        if item["kind"] == "text":
            return item.get("text", "")
        return item.get("path") or str(backpack.payload_path(item) or item["title"])

    def _bag_copy(self) -> None:
        sel = self._selected_bag()
        if len(sel) == 1 and sel[0]["kind"] == "image":
            from PySide6.QtGui import QImage

            QGuiApplication.clipboard().setImage(QImage(str(backpack.payload_path(sel[0]))))
        elif sel:
            QGuiApplication.clipboard().setText("\n".join(self._as_text(i) for i in sel))

    def _bag_copy_all(self) -> None:
        QGuiApplication.clipboard().setText("\n".join(self._as_text(i) for i in backpack.items()))

    def _bag_export(self) -> None:
        sel = self._selected_bag()
        if not sel:
            return
        target = QFileDialog.getExistingDirectory(self, self.t("pet.bag.export"))
        if target:
            n = backpack.export([i["id"] for i in sel], target)
            QMessageBox.information(self, self.t("pet.tab.backpack"), self.t("pet.bag.exported").format(n=n))

    def _bag_remove(self) -> None:
        for item in self._selected_bag():
            backpack.remove(item["id"])
        self.refresh()

    def _bag_clear(self) -> None:
        if backpack.items() and QMessageBox.question(self, self.t("pet.tab.backpack"), self.t("pet.bag.clear_confirm")) \
                == QMessageBox.StandardButton.Yes:
            backpack.clear()
            self.refresh()

    # -- recent / notifications / dashboard -------------------------------------------------------
    def _refresh_recent(self):
        self.recent_list.clear()
        for i, r in enumerate(journal.load()["recent"]):
            pin = "📌 " if r.get("pinned") else ""
            when = time.strftime("%m-%d %H:%M", time.localtime(r.get("time", 0)))
            li = QListWidgetItem(f"{pin}{KIND_ICON.get(r['kind'], '•')}  {r['title']}    {when}")
            li.setData(Qt.ItemDataRole.UserRole, i)
            li.setToolTip(r.get("path", ""))
            self.recent_list.addItem(li)
        self.recent_open_btn.setText(self.t("pet.recent.open"))
        self.recent_pin_btn.setText(self.t("pet.recent.pin"))

    def _recent_open(self, item) -> None:
        if item is None:
            return
        r = journal.load()["recent"][item.data(Qt.ItemDataRole.UserRole)]
        path = r.get("path", "")
        if path.lower().endswith((".hdf5", ".h5", ".hdf", ".nc")) and Path(path).exists():
            open_measurement(path)
        elif Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            QMessageBox.information(self, r.get("title", ""), self.t("pet.bag.missing"))

    def _recent_pin(self) -> None:
        item = self.recent_list.currentItem()
        if item is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        r = journal.load()["recent"][index]
        journal.set_pinned(index, not r.get("pinned"))
        self.refresh()

    def _refresh_notes(self):
        self.notes_list.clear()
        for n in journal.load()["notes"]:
            when = time.strftime("%m-%d %H:%M", time.localtime(n.get("time", 0)))
            li = QListWidgetItem(f"{NOTE_ICON.get(n['level'], '•')}  {n['text']}    {when}")
            if not n.get("read"):
                f = li.font(); f.setBold(True); li.setFont(f)
            self.notes_list.addItem(li)
        self.notes_read.setText(self.t("pet.notes.read"))
        self.notes_clear.setText(self.t("pet.notes.clear"))

    def _refresh_dashboard(self, state, info, level):
        s = journal.summary()
        rows = (("measurement", "pet.dash.measurements"), ("operation", "pet.dash.operations"),
                ("analysis", "pet.dash.analysis"), ("debackground", "pet.dash.debackground"), ("export", "pet.dash.exports"))

        def col(counts):
            return "".join(f"<tr><td>{self.t(label)}</td><td align='right'><b>{counts.get(k, 0)}</b></td></tr>" for k, label in rows)
        exp = int(state["exp"].get(info.id, 0))
        self.dash.setText(
            f"<table width='100%'><tr><td valign='top'><b>{self.t('pet.dash.today')}</b><table>{col(s['today'])}</table></td>"
            f"<td valign='top'><b>{self.t('pet.dash.month')}</b><table>{col(s['month'])}"
            f"<tr><td>{self.t('pet.dash.days')}</td><td align='right'><b>{s['days_month']}</b></td></tr></table></td>"
            f"<td valign='top'><b>{self.t('pet.dash.total')}</b><table>{col(s['total'])}"
            f"<tr><td>{self.t('pet.dash.days')}</td><td align='right'><b>{s['days_total']}</b></td></tr></table></td></tr></table>"
            f"<p>{self.t('pet.dash.exp').format(level=level, exp=exp, streak=state.get('streak', 0))}</p>")

    def popup_near(self, pet_window) -> None:
        self.refresh()
        self.adjustSize()
        geo = pet_window.frameGeometry()
        screen = pet_window.screen().availableGeometry()
        x = min(max(screen.left(), geo.center().x() - self.width() // 2), screen.right() - self.width())
        y = geo.top() - self.height() - 6
        if y < screen.top():
            y = geo.bottom() + 6
        self.move(x, y)
        self.show()
