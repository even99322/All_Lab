"""Settings > Shortcuts (v2.1.6): change the Viewer's single-key shortcuts."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QKeySequenceEdit, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from app.core import shortcuts


class ShortcutsPage(QWidget):
    def __init__(self, localizer, parent=None) -> None:
        super().__init__(parent)
        self.localizer = localizer
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.form = QFormLayout()
        layout.addLayout(self.form)
        self.edits: dict[str, QKeySequenceEdit] = {}
        self.labels: dict[str, QLabel] = {}
        for action, _default in shortcuts.ACTIONS:
            edit = QKeySequenceEdit()
            edit.setObjectName(f"shortcut_{action}")
            edit.setMaximumSequenceLength(1)
            edit.setClearButtonEnabled(True)
            edit.editingFinished.connect(lambda a=action: self._edited(a))
            edit.keySequenceChanged.connect(lambda _seq, a=action: self._cleared(a))
            off = QToolButton()
            off.setText("×")
            off.setToolTip("Turn this shortcut off")
            off.clicked.connect(lambda _c=False, a=action: self._turn_off(a))
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(edit, 1)
            row_layout.addWidget(off)
            label = QLabel()
            self.form.addRow(label, row)
            self.edits[action] = edit
            self.labels[action] = label
        self.warning = QLabel()
        self.warning.setObjectName("shortcutConflicts")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #d9822b;")
        layout.addWidget(self.warning)
        buttons = QHBoxLayout()
        self.reset_button = QPushButton()
        self.reset_button.setObjectName("shortcutsReset")
        self.reset_button.clicked.connect(self._reset)
        buttons.addWidget(self.reset_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        self.retranslate()
        self.load()

    def retranslate(self) -> None:
        text = self.localizer.text
        self.title.setText(text("shortcut.page_hint"))
        for action, label in self.labels.items():
            label.setText(text(f"shortcut.{action}"))
        self.reset_button.setText(text("shortcut.reset"))
        self._show_conflicts()

    def load(self) -> None:
        for action, key in shortcuts.bindings().items():
            with QSignalBlocker(self.edits[action]):
                self.edits[action].setKeySequence(QKeySequence(key))
        self._show_conflicts()

    def _edited(self, action: str) -> None:
        seq = self.edits[action].keySequence().toString()
        if seq != shortcuts.key_for(action):
            shortcuts.set_key(action, seq)
        self._show_conflicts()

    def _cleared(self, action: str) -> None:
        if self.edits[action].keySequence().isEmpty() and shortcuts.key_for(action):
            shortcuts.set_key(action, "")
            self._show_conflicts()

    def _turn_off(self, action: str) -> None:
        with QSignalBlocker(self.edits[action]):
            self.edits[action].clear()
        shortcuts.set_key(action, "")
        self._show_conflicts()

    def _reset(self) -> None:
        shortcuts.reset()
        self.load()

    def _show_conflicts(self) -> None:
        text = self.localizer.text
        clashes = shortcuts.conflicts()
        if not clashes:
            self.warning.setText("")
            return
        rows = [f"{key.upper()}: " + ", ".join(text(f"shortcut.{a}") for a in actions)
                for key, actions in clashes.items()]
        self.warning.setText(text("shortcut.conflict") + "\n" + "\n".join(rows))
