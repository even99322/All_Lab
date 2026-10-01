"""Licences dialog: this computer's machine code, installed licences, install / remove."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)


class LicenseDialog(QDialog):
    licenses_changed = Signal()

    def __init__(self, localizer, parent=None):
        super().__init__(parent)
        from app._guard.machine import machine_code

        self.localizer = localizer
        text = localizer.text
        self.setObjectName("licenseDialog")
        self.setWindowTitle(text("license.title"))
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        intro = QLabel(text("license.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(QLabel(text("license.machine_code")))
        self.code = QLineEdit(machine_code())
        self.code.setObjectName("machineCode")
        self.code.setReadOnly(True)
        row.addWidget(self.code, 1)
        copy = QPushButton(text("license.copy"))
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.code.text()))
        row.addWidget(copy)
        layout.addLayout(row)
        # Which LabLogViewer this is and whether it can check licences: a licence generated
        # for another copy (or a copy without the public key) is recognised at a glance.
        from pathlib import Path

        from app import __version__
        from app._guard import license as licenses

        installed, short = licenses.key_status()
        self.program_folder = str(Path(__file__).resolve().parents[2])
        self.key_line = QLabel(text("license.program").format(version=__version__, folder=self.program_folder)
                               + "\n" + (text("license.key_ok").format(key=short) if installed
                                         else text("license.key_missing")))
        self.key_line.setObjectName("licenseKeyStatus")
        self.key_line.setWordWrap(True)
        self.key_line.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if not installed:
            self.key_line.setStyleSheet("color: #FF9F0A;")
        layout.addWidget(self.key_line)
        self.table = QTreeWidget()
        self.table.setObjectName("licenseTable")
        self.table.setRootIsDecorated(False)
        self.table.setHeaderLabels([text("license.holder"), text("license.features"),
                                    text("license.expires"), text("license.status")])
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 190)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        install = QPushButton(text("license.install"))
        install.clicked.connect(self._choose_file)
        self.remove_button = QPushButton(text("license.remove"))
        self.remove_button.clicked.connect(self._remove)
        self.relink_button = QPushButton(text("relink.open"))
        self.relink_button.setObjectName("openRelink")
        self.relink_button.clicked.connect(self._open_relink)
        self.migrate_button = QPushButton(text("migrate.open"))
        self.migrate_button.setObjectName("openMigrate")
        self.migrate_button.clicked.connect(self._open_migrate)
        for button in (install, self.remove_button, self.relink_button, self.migrate_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    def _feature_names(self, features) -> str:
        return ", ".join(self.localizer.text(f"license.feature_{f}") for f in features)

    def refresh(self) -> None:
        from datetime import date

        from app._guard import gate, license as licenses

        text = self.localizer.text
        self.table.clear()
        for path, found, reason in licenses.all_installed():
            if found is None:
                item = QTreeWidgetItem([path.stem[:12], "", "", text(f"license.error_{reason}")])
            else:
                left = found.days_left(date.today())
                item = QTreeWidgetItem([found.holder, self._feature_names(found.features),
                                        found.expires.isoformat(), text("license.valid_days").format(days=left)])
            item.setData(0, 256, str(path))
            self.table.addTopLevelItem(item)
        self.remove_button.setEnabled(self.table.topLevelItemCount() > 0)
        # The re-link tool is only offered to holders of a licence with that feature.
        self.relink_button.setVisible(gate.relink_allowed() is not None)
        self.migrate_button.setVisible(gate.migrate_allowed() is not None)

    def install_from(self, path: str) -> str | None:
        """Install ``path``; returns an error text or None."""
        from app._guard import gate, license as licenses

        try:
            licenses.install(path)
        except licenses.LicenseError as error:
            message = self.localizer.text(f"license.error_{error.reason}")
            if error.reason == "no_key":
                message += "\n\n" + self.localizer.text("license.key_where").format(folder=self.program_folder)
            return message
        except OSError as error:
            return str(error)
        gate.invalidate()
        self.refresh()
        self.licenses_changed.emit()
        return None

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.localizer.text("license.install"), "",
                                              "LabLogViewer licence (*.llvkey)")
        if not path:
            return
        error = self.install_from(path)
        if error:
            QMessageBox.warning(self, self.localizer.text("license.title"), error)
        else:
            QMessageBox.information(self, self.localizer.text("license.title"),
                                    self.localizer.text("license.installed"))

    def _remove(self) -> None:
        from app._guard import gate, license as licenses

        item = self.table.currentItem()
        if item is None:
            return
        licenses.remove(item.data(0, 256))
        gate.invalidate()
        self.refresh()
        self.licenses_changed.emit()

    def _open_migrate(self) -> None:
        from app.gui.migrate_dialog import open_migrate

        self.migrate_window = open_migrate(self.localizer, self)

    def _open_relink(self) -> None:
        from app.gui.relink_dialog import open_relink

        open_relink(self.localizer, self)


def open_licenses(localizer, parent=None) -> LicenseDialog:
    dialog = LicenseDialog(localizer, parent)
    dialog.show()
    return dialog
