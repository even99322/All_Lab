"""Modeless general application settings."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from shiboken6 import isValid
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from app import __version__
from app.core import data_location
from app.localization import LocalizationManager, get_localization_manager
from app.settings.debug_info import DebugPage
from app.theme import ThemeManager, get_theme_manager


class SettingsDialog(QDialog):
    PAGES = ("general", "appearance", "personal", "three_d", "debug", "about")

    def __init__(self, localizer: LocalizationManager | None = None, parent=None,
                 theme_manager: ThemeManager | None = None):
        super().__init__(parent)
        self.localizer = localizer or get_localization_manager()
        self.theme_manager = theme_manager or get_theme_manager()
        self.setWindowTitle(self.localizer.text("settings.title"))
        self.resize(620, 390)
        root = QHBoxLayout(self)
        self.sections = QListWidget()
        self.sections.setMaximumWidth(180)
        self.sections.addItem(QListWidgetItem(self.localizer.text("settings.general")))
        root.addWidget(self.sections)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)

        general = QWidget()
        layout = QVBoxLayout(general)
        layout.addWidget(QLabel(self.localizer.text("settings.language")))
        self.english = QRadioButton(self.localizer.text("language.english"))
        self.traditional_chinese = QRadioButton(self.localizer.text("language.traditional_chinese"))
        self.language_group = QButtonGroup(self)
        self.language_group.setExclusive(True)
        self.language_group.addButton(self.english)
        self.language_group.addButton(self.traditional_chinese)
        layout.addWidget(self.english)
        layout.addWidget(self.traditional_chinese)
        layout.addSpacing(10)
        layout.addWidget(self._build_data_folder_group())
        layout.addWidget(self._build_tag_root_group())
        layout.addLayout(self._build_auto_refresh_row())
        layout.addStretch(1)
        self.pages.addWidget(general)

        self.pages.addWidget(self._build_appearance_page())
        from app.gui.personal_page import PersonalPage

        self.personal_page = PersonalPage(self.localizer, self.theme_manager, self)
        self.pages.addWidget(self.personal_page)
        self.pages.addWidget(self._build_three_d_page())

        self.debug_page = DebugPage(self.localizer, self)
        self.pages.addWidget(self.debug_page)
        about = QWidget()
        about_layout = QVBoxLayout(about)
        self.about_content = QLabel()
        self.about_content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.about_content.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.about_content.setWordWrap(True)
        about_layout.addWidget(self.about_content)
        licenses_row = QHBoxLayout()
        self.license_button = QPushButton(self.localizer.text("license.open"))
        self.license_button.setObjectName("aboutLicenses")
        self.license_button.clicked.connect(self._open_licenses)
        licenses_row.addWidget(self.license_button)
        self.whats_new_button = QPushButton(self.localizer.text("whatsnew.open"))
        self.whats_new_button.setObjectName("aboutWhatsNew")
        self.whats_new_button.clicked.connect(self._open_whats_new)
        licenses_row.addWidget(self.whats_new_button)
        licenses_row.addStretch(1)
        about_layout.addLayout(licenses_row)
        about_layout.addStretch(1)
        self.pages.addWidget(about)

        self.sections.addItem(QListWidgetItem(self.localizer.text("settings.appearance")))
        self.sections.addItem(QListWidgetItem(self.localizer.text("settings.personal")))
        self.sections.addItem(QListWidgetItem(self.localizer.text("settings.three_d")))
        self.sections.addItem(QListWidgetItem(self.localizer.text("settings.debug")))
        self.sections.addItem(QListWidgetItem(self.localizer.text("settings.about")))
        self.sections.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.sections.currentRowChanged.connect(self._page_changed)
        self.sections.setCurrentRow(0)
        self.english.setChecked(self.localizer.language == "en")
        self.traditional_chinese.setChecked(self.localizer.language == "zh_TW")
        self.english.toggled.connect(self._english_toggled)
        self.traditional_chinese.toggled.connect(self._chinese_toggled)
        self.appearance_combo.currentIndexChanged.connect(self._appearance_selected)
        if self.theme_manager is not None:
            self.theme_manager.mode_changed.connect(self._sync_appearance)
            self._sync_appearance(self.theme_manager.mode)
        else:
            self.appearance_combo.setEnabled(False)
        self.localizer.language_changed.connect(self._retranslate)
        self.localizer.bind(self)
        self._retranslate(self.localizer.language)

    def _open_licenses(self) -> None:
        from app.gui.license_dialog import LicenseDialog

        dialog = LicenseDialog(self.localizer, self)
        dialog.licenses_changed.connect(self.personal_page._licenses_changed)
        dialog.exec()

    def _build_appearance_page(self) -> QWidget:
        """Every appearance choice: theme, scientific/export plots and glass."""
        from app.gui.glass import GlassSlider, optical_glass_available

        page = QWidget()
        layout = QVBoxLayout(page)
        self.appearance_label = QLabel(self.localizer.text("settings.appearance"))
        layout.addWidget(self.appearance_label)
        theme_form = QFormLayout()
        self.appearance_combo = QComboBox()
        self.appearance_combo.setObjectName("settingsAppearanceSelector")
        for key, mode in (("theme.light", "light"), ("theme.dark", "dark"), ("theme.system", "system")):
            self.appearance_combo.addItem(self.localizer.text(key), mode)
        self.theme_label = QLabel(self.localizer.text("settings.theme"))
        theme_form.addRow(self.theme_label, self.appearance_combo)
        layout.addLayout(theme_form)

        self.advanced_group = QGroupBox(self.localizer.text("settings.advanced"))
        advanced = QFormLayout(self.advanced_group)
        self.scientific_plot_combo = QComboBox()
        self.scientific_plot_combo.setObjectName("settingsScientificPlotSelector")
        self.export_plot_combo = QComboBox()
        self.export_plot_combo.setObjectName("settingsExportBackgroundSelector")
        for combo in (self.scientific_plot_combo, self.export_plot_combo):
            for key, appearance in (("settings.white_background", "white"),
                                    ("settings.dark_background", "dark")):
                combo.addItem(self.localizer.text(key), appearance)
        self.scientific_plot_label = QLabel(self.localizer.text("settings.scientific_plot_appearance"))
        self.export_plot_label = QLabel(self.localizer.text("settings.export_plot_background"))
        advanced.addRow(self.scientific_plot_label, self.scientific_plot_combo)
        advanced.addRow(self.export_plot_label, self.export_plot_combo)
        layout.addWidget(self.advanced_group)

        self.glass_group = QGroupBox(self.localizer.text("settings.glass"))
        glass = QFormLayout(self.glass_group)
        self.glass_sliders = {}
        self.glass_labels = {}
        available = optical_glass_available()
        for key, text in (("thickness", "browser.glass_thickness"), ("frost", "browser.glass_frost")):
            slider = GlassSlider()
            slider.setObjectName(f"settingsGlass{key.title()}Slider")
            slider.setMinimumWidth(180)
            slider.setEnabled(available)
            slider.setToolTip(self.localizer.text(
                f"browser.glass_{key}_tip" if available else "browser.glass_unavailable"))
            slider.valueChanged.connect(lambda _value: self._glass_changed(final=False))
            slider.released.connect(lambda _value: self._glass_changed(final=True))
            label = QLabel(self.localizer.text(text))
            glass.addRow(label, slider)
            self.glass_sliders[key] = slider
            self.glass_labels[key] = label
        layout.addWidget(self.glass_group)
        layout.addWidget(self._build_app_icon_group())
        layout.addStretch(1)

        self.scientific_plot_combo.currentIndexChanged.connect(
            lambda index: self.theme_manager.set_scientific_plot_appearance(
                self.scientific_plot_combo.itemData(index)) if self.theme_manager is not None else None)
        self.export_plot_combo.currentIndexChanged.connect(
            lambda index: self.theme_manager.set_export_plot_background(
                self.export_plot_combo.itemData(index)) if self.theme_manager is not None else None)
        if self.theme_manager is not None:
            self.theme_manager.scientific_plot_appearance_changed.connect(self._sync_plot_appearance)
            self.theme_manager.export_plot_background_changed.connect(self._sync_plot_appearance)
            self.theme_manager.glass_material_changed.connect(self._sync_glass)
            self._sync_plot_appearance()
            self._sync_glass(*self.theme_manager.glass_material, True)
        else:
            for widget in (self.scientific_plot_combo, self.export_plot_combo, *self.glass_sliders.values()):
                widget.setEnabled(False)
        return page

    def _open_whats_new(self) -> None:
        from app import __version__
        from app.gui.whats_new import WhatsNewDialog

        self.whats_new_dialog = WhatsNewDialog(self.localizer, __version__, parent=self)
        self.whats_new_dialog.show()

    def _build_auto_refresh_row(self):
        """How often the Browser checks the opened database for new / changed files."""
        from PySide6.QtWidgets import QHBoxLayout

        row = QHBoxLayout()
        self.auto_refresh_label = QLabel(self.localizer.text("settings.auto_refresh"))
        self.auto_refresh_combo = QComboBox()
        self.auto_refresh_combo.setObjectName("settingsAutoRefresh")
        self.auto_refresh_combo.setToolTip(self.localizer.text("settings.auto_refresh_tip"))
        self._fill_auto_refresh_combo()
        store = getattr(self.localizer, "store", None)
        if store is not None and hasattr(store, "auto_refresh_seconds"):
            self.auto_refresh_combo.setCurrentIndex(self.auto_refresh_combo.findData(store.auto_refresh_seconds()))
            self.auto_refresh_combo.currentIndexChanged.connect(self._auto_refresh_chosen)
        else:
            self.auto_refresh_combo.setEnabled(False)
        row.addWidget(self.auto_refresh_label)
        row.addWidget(self.auto_refresh_combo)
        row.addStretch(1)
        return row

    def _fill_auto_refresh_combo(self) -> None:
        current = self.auto_refresh_combo.currentData()
        blocked = self.auto_refresh_combo.blockSignals(True)
        self.auto_refresh_combo.clear()
        for seconds in (0, 1, 2, 5):
            label = (self.localizer.text("settings.auto_refresh_off") if seconds == 0
                     else self.localizer.text("settings.auto_refresh_n").format(n=seconds))
            self.auto_refresh_combo.addItem(label, seconds)
        if current is not None:
            self.auto_refresh_combo.setCurrentIndex(self.auto_refresh_combo.findData(current))
        self.auto_refresh_combo.blockSignals(blocked)

    def _auto_refresh_chosen(self, index: int) -> None:
        from app.gui.browser_window import BrowserWindow

        self.localizer.store.set_auto_refresh_seconds(self.auto_refresh_combo.itemData(index))
        for window in QApplication.topLevelWidgets():
            if isinstance(window, BrowserWindow):
                window.auto_refresher.apply_setting()

    def _build_app_icon_group(self) -> QGroupBox:
        """Four application icons to choose from (no licence needed)."""
        from PySide6.QtCore import QSize
        from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QToolButton

        from app.gui import app_icon

        self.app_icon_group = QGroupBox(self.localizer.text("settings.app_icon"))
        self.app_icon_group.setObjectName("settingsAppIcon")
        row = QHBoxLayout(self.app_icon_group)
        self.app_icon_buttons = QButtonGroup(self)
        self.app_icon_buttons.setExclusive(True)
        store = getattr(self.localizer, "store", None)
        current = store.app_icon() if store is not None and hasattr(store, "app_icon") else app_icon.DEFAULT
        for number in range(1, app_icon.COUNT + 1):
            button = QToolButton()
            button.setObjectName(f"settingsAppIcon{number}")
            button.setCheckable(True)
            button.setIcon(app_icon.app_icon(number))
            button.setIconSize(QSize(64, 64))
            button.setAutoRaise(True)
            button.setToolTip(self.localizer.text("settings.app_icon_n").format(n=number))
            button.setChecked(number == current)
            self.app_icon_buttons.addButton(button, number)
            row.addWidget(button)
        row.addStretch(1)
        if store is None or not hasattr(store, "set_app_icon"):
            self.app_icon_group.setEnabled(False)
        else:
            self.app_icon_buttons.idClicked.connect(self._app_icon_chosen)
        return self.app_icon_group

    def _app_icon_chosen(self, number: int) -> None:
        from app.gui.app_icon import apply_app_icon

        store = self.localizer.store
        store.set_app_icon(number)
        apply_app_icon(store)

    def _build_three_d_page(self) -> QWidget:
        """3D Surface window settings: display performance and export style."""
        page = QWidget()
        layout = QVBoxLayout(page)
        self.three_d_title = QLabel(self.localizer.text("settings.three_d"))
        layout.addWidget(self.three_d_title)
        form = QFormLayout()
        self.three_d_profile_combo = QComboBox()
        self.three_d_profile_combo.setObjectName("settingsThreeDProfile")
        for profile in ("balanced", "quality", "economy"):
            self.three_d_profile_combo.addItem(self.localizer.text(f"settings.profile_{profile}"), profile)
        self.three_d_profile_combo.setToolTip(self.localizer.text("settings.three_d_profile_tip"))
        self.three_d_profile_label = QLabel(self.localizer.text("settings.three_d_profile"))
        form.addRow(self.three_d_profile_label, self.three_d_profile_combo)
        self.three_d_export_combo = QComboBox()
        self.three_d_export_combo.setObjectName("settingsThreeDExportStyle")
        for style in ("publication", "screen"):
            self.three_d_export_combo.addItem(self.localizer.text(f"settings.export_style_{style}"), style)
        self.three_d_export_combo.setToolTip(self.localizer.text("settings.three_d_export_style_tip"))
        self.three_d_export_label = QLabel(self.localizer.text("settings.three_d_export_style"))
        form.addRow(self.three_d_export_label, self.three_d_export_combo)
        layout.addLayout(form)
        layout.addStretch(1)
        store = getattr(self.localizer, "store", None)
        for combo, getter, setter in (
            (self.three_d_profile_combo, "three_d_profile", "set_three_d_profile"),
            (self.three_d_export_combo, "three_d_export_style", "set_three_d_export_style"),
        ):
            if store is not None and hasattr(store, getter):
                combo.setCurrentIndex(combo.findData(getattr(store, getter)()))
                combo.currentIndexChanged.connect(
                    lambda index, combo=combo, setter=getattr(store, setter): setter(combo.itemData(index)))
            else:
                combo.setEnabled(False)
        return page

    def _build_tag_root_group(self) -> QGroupBox:
        """The largest data folder that Retrieve by Tags can search (besides the open database)."""
        text = self.localizer.text
        group = QGroupBox(text("settings.tag_root"))
        group.setObjectName("settingsTagRoot")
        box = QVBoxLayout(group)
        note = QLabel(text("settings.tag_root_note"))
        note.setWordWrap(True)
        box.addWidget(note)
        row = QHBoxLayout()
        self.tag_root_path = QLabel()
        self.tag_root_path.setObjectName("settingsTagRootPath")
        self.tag_root_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.tag_root_path.setWordWrap(True)
        row.addWidget(self.tag_root_path, 1)
        browse = QPushButton(text("settings.tag_root_browse"))
        browse.clicked.connect(self._choose_tag_root)
        clear = QPushButton(text("settings.tag_root_clear"))
        clear.clicked.connect(lambda: self.set_tag_root(""))
        row.addWidget(browse)
        row.addWidget(clear)
        box.addLayout(row)
        self._show_tag_root()
        return group

    def _show_tag_root(self) -> None:
        root = self.localizer.store.tag_search_root()
        self.tag_root_path.setText(root or self.localizer.text("settings.tag_root_none"))

    def _choose_tag_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.localizer.text("settings.tag_root"),
                                                  self.localizer.store.tag_search_root())
        if folder:
            self.set_tag_root(folder)

    def set_tag_root(self, folder: str) -> None:
        self.localizer.store.set_tag_search_root(folder)
        self._show_tag_root()

    def _build_data_folder_group(self) -> QGroupBox:
        """Where everything kept outside HDF5 lives; movable with a folder browser."""
        self.data_folder_group = QGroupBox(self.localizer.text("settings.data_folder"))
        self.data_folder_group.setObjectName("settingsDataFolder")
        box = QVBoxLayout(self.data_folder_group)
        self.data_folder_path = QLabel()
        self.data_folder_path.setObjectName("settingsDataFolderPath")
        self.data_folder_path.setWordWrap(True)
        self.data_folder_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.data_folder_note = QLabel()
        self.data_folder_note.setWordWrap(True)
        box.addWidget(self.data_folder_path)
        box.addWidget(self.data_folder_note)
        row = QHBoxLayout()
        self.data_folder_browse = QPushButton(self.localizer.text("settings.data_folder_browse"))
        self.data_folder_open = QPushButton(self.localizer.text("settings.data_folder_open"))
        self.data_folder_default = QPushButton(self.localizer.text("settings.data_folder_default"))
        self.data_folder_browse.clicked.connect(self._browse_data_folder)
        self.data_folder_open.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_location.data_root()))))
        self.data_folder_default.clicked.connect(
            lambda: self._move_data_folder(data_location.default_data_root()))
        for button in (self.data_folder_browse, self.data_folder_open, self.data_folder_default):
            row.addWidget(button)
        row.addStretch(1)
        box.addLayout(row)
        self._sync_data_folder()
        return self.data_folder_group

    def _accept_cloud_risk(self, service: str) -> bool:
        """Cloud folders are not allowed unless the user accepts the risk explicitly."""
        from PySide6.QtWidgets import QCheckBox

        text = self.localizer.text
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(text("settings.data_folder"))
        box.setText(text("settings.cloud_blocked").format(service=service))
        consent = QCheckBox(text("settings.cloud_consent"))
        box.setCheckBox(consent)
        move = box.addButton(text("settings.cloud_move_anyway"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        move.setEnabled(False)
        consent.toggled.connect(move.setEnabled)
        box.exec()
        return box.clickedButton() is move and consent.isChecked()

    def _sync_data_folder(self) -> None:
        text = self.localizer.text
        current = data_location.data_root()
        self.data_folder_path.setText(f"{text('settings.data_folder_current')}: {current}")
        pending = data_location.pending_move()
        notice = data_location.startup_notice()
        service = data_location.cloud_service(current)
        if pending is not None:
            self.data_folder_note.setText(f"{text('settings.data_folder_pending')}: {pending}")
        elif service is not None:
            self.data_folder_note.setText(text("settings.cloud_current").format(service=service))
        elif notice:
            self.data_folder_note.setText(notice)
        else:
            self.data_folder_note.setText(text("settings.data_folder_tip"))
        self.data_folder_default.setEnabled(
            (pending or current).resolve() != data_location.default_data_root().resolve())

    def _browse_data_folder(self) -> None:
        start = str(data_location.data_root().parent)
        chosen = QFileDialog.getExistingDirectory(self, self.localizer.text("settings.data_folder_choose"), start)
        if chosen:
            self._move_data_folder(data_location.normalize_choice(chosen))

    def _move_data_folder(self, target) -> None:
        text = self.localizer.text
        try:
            try:
                result = data_location.request_move(target)
            except data_location.CloudLocationError as cloud:
                if not self._accept_cloud_risk(cloud.service):
                    return
                result = data_location.request_move(target, allow_cloud=True)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, text("settings.data_folder"), str(error))
            return
        if result == "occupied":
            answer = QMessageBox.question(
                self, text("settings.data_folder"), text("settings.data_folder_occupied").format(path=target))
            if answer != QMessageBox.StandardButton.Yes:
                return
            data_location.use_existing(target)
        self._sync_data_folder()
        if result == "same":
            return
        answer = QMessageBox.question(self, text("settings.data_folder"), text("settings.data_folder_restart"))
        if answer == QMessageBox.StandardButton.Yes:
            QApplication.closeAllWindows()
            QApplication.quit()

    def _page_changed(self, index: int) -> None:
        # The Personal page needs room for the colour list and the wheel.
        if self.PAGES[index] == "personal" and (self.width() < 880 or self.height() < 560):
            self.resize(max(self.width(), 880), max(self.height(), 560))

    def show_page(self, name: str) -> None:
        self.sections.setCurrentRow(self.PAGES.index(name))

    def _glass_changed(self, *, final: bool) -> None:
        if self.theme_manager is not None:
            self.theme_manager.set_glass_material(
                self.glass_sliders["thickness"].value() / 100.0,
                self.glass_sliders["frost"].value() / 100.0, final=final,
            )

    def _sync_glass(self, thickness: float, frost: float, _final: bool) -> None:
        for key, value in (("thickness", thickness), ("frost", frost)):
            slider = self.glass_sliders[key]
            if not slider.isSliderDown() and slider.value() != round(value * 100):
                slider.blockSignals(True)
                slider.setValue(round(value * 100))
                slider.blockSignals(False)
                slider.update()

    def _sync_plot_appearance(self, *_args) -> None:
        for combo, value in ((self.scientific_plot_combo, self.theme_manager.scientific_plot_appearance),
                             (self.export_plot_combo, self.theme_manager.export_plot_background)):
            index = combo.findData(value)
            if index >= 0 and index != combo.currentIndex():
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)

    def _english_toggled(self, checked: bool) -> None:
        if checked:
            self.localizer.set_language("en")

    def _chinese_toggled(self, checked: bool) -> None:
        if checked:
            self.localizer.set_language("zh_TW")

    def _retranslate(self, _language: str) -> None:
        for row, page in enumerate(self.PAGES):
            self.sections.item(row).setText(self.localizer.text(f"settings.{page}"))
        self.three_d_title.setText(self.localizer.text("settings.three_d"))
        self.english.setText(self.localizer.text("language.english"))
        self.traditional_chinese.setText(self.localizer.text("language.traditional_chinese"))
        self.appearance_label.setText(self.localizer.text("settings.appearance"))
        self.theme_label.setText(self.localizer.text("settings.theme"))
        self.three_d_profile_label.setText(self.localizer.text("settings.three_d_profile"))
        self.three_d_profile_combo.setToolTip(self.localizer.text("settings.three_d_profile_tip"))
        for index in range(self.three_d_profile_combo.count()):
            self.three_d_profile_combo.setItemText(index, self.localizer.text(
                f"settings.profile_{self.three_d_profile_combo.itemData(index)}"))
        self.advanced_group.setTitle(self.localizer.text("settings.advanced"))
        self.scientific_plot_label.setText(self.localizer.text("settings.scientific_plot_appearance"))
        self.export_plot_label.setText(self.localizer.text("settings.export_plot_background"))
        self.three_d_export_label.setText(self.localizer.text("settings.three_d_export_style"))
        self.three_d_export_combo.setToolTip(self.localizer.text("settings.three_d_export_style_tip"))
        for index in range(self.three_d_export_combo.count()):
            self.three_d_export_combo.setItemText(index, self.localizer.text(
                f"settings.export_style_{self.three_d_export_combo.itemData(index)}"))
        self.glass_group.setTitle(self.localizer.text("settings.glass"))
        self.app_icon_group.setTitle(self.localizer.text("settings.app_icon"))
        self.auto_refresh_label.setText(self.localizer.text("settings.auto_refresh"))
        self.whats_new_button.setText(self.localizer.text("whatsnew.open"))
        self.auto_refresh_combo.setToolTip(self.localizer.text("settings.auto_refresh_tip"))
        self._fill_auto_refresh_combo()
        for key in ("thickness", "frost"):
            self.glass_labels[key].setText(self.localizer.text(f"browser.glass_{key}"))
        theme_keys = {"light": "theme.light", "dark": "theme.dark", "system": "theme.system"}
        plot_keys = {"white": "settings.white_background", "dark": "settings.dark_background"}
        for combo, keys in ((self.appearance_combo, theme_keys), (self.scientific_plot_combo, plot_keys),
                            (self.export_plot_combo, plot_keys)):
            for index in range(combo.count()):
                combo.setItemText(index, self.localizer.text(keys[combo.itemData(index)]))
        if self.localizer.language == "en":
            self.traditional_chinese.setText("中文（繁體）「不好好練一下英文」")
            self.about_content.setText(
                f"LabLogViewer\nVersion v{__version__}\n\n"
                "Scientific Data Visualization for Labber HDF5\n\n"
                "Developed by\n戦わずに恋をする\n\n"
                "Co-developed by\n張譯文\n\n© 2026 戦わずに恋をする"
            )
        else:
            self.english.setText("English「按下去我欣賞你」")
            self.about_content.setText(
                f"LabLogViewer\n版本 v{__version__}\n\n"
                "Labber HDF5 科學資料視覺化\n\n"
                "開發者\n戦わずに恋をする\n\n"
                "共同開發者\n張譯文\n\n© 2026 戦わずに恋をする"
            )
        self.data_folder_group.setTitle(self.localizer.text("settings.data_folder"))
        self.data_folder_browse.setText(self.localizer.text("settings.data_folder_browse"))
        self.data_folder_open.setText(self.localizer.text("settings.data_folder_open"))
        self.data_folder_default.setText(self.localizer.text("settings.data_folder_default"))
        self._sync_data_folder()
        self.debug_page.retranslate()

    def _appearance_selected(self, index: int) -> None:
        if self.theme_manager is not None:
            mode = self.appearance_combo.itemData(index)
            if mode:
                self.theme_manager.set_mode(str(mode))

    def _sync_appearance(self, mode: str) -> None:
        index = self.appearance_combo.findData(mode)
        if index >= 0 and index != self.appearance_combo.currentIndex():
            self.appearance_combo.blockSignals(True)
            self.appearance_combo.setCurrentIndex(index)
            self.appearance_combo.blockSignals(False)


_shared_dialog: SettingsDialog | None = None


def show_settings(page: str | None = None, parent=None, localizer: LocalizationManager | None = None,
                  theme_manager: ThemeManager | None = None) -> SettingsDialog:
    """Open the one application-wide Settings dialog from any window."""
    global _shared_dialog
    localizer = localizer or get_localization_manager()
    theme_manager = theme_manager or get_theme_manager()
    dialog = _shared_dialog
    reusable = (dialog is not None and isValid(dialog)
                and dialog.localizer is localizer and dialog.theme_manager is theme_manager)
    if reusable and parent is not None and dialog.parent() is not parent:
        dialog.setParent(parent, dialog.windowFlags())      # stay above the window it was opened from
    if not reusable:
        dialog = SettingsDialog(localizer, parent, theme_manager)
        dialog.setModal(False)
        dialog.destroyed.connect(lambda *_args: _forget(dialog))
        _shared_dialog = dialog
    if page:
        dialog.show_page(page)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


def _forget(dialog) -> None:
    global _shared_dialog
    if _shared_dialog is dialog:
        _shared_dialog = None


def install_settings_menu(window, localizer: LocalizationManager | None = None,
                          theme_manager: ThemeManager | None = None, opener=None, network_opener=None):
    """Top menus "Network Workspace" and "Settings" that open their window directly.

    ``opener(page)`` / ``network_opener()`` replace the local windows (the
    isolated 3D process asks the main process, which owns settings and the
    network session).
    """
    from app.gui.top_menus import install_top_menus

    def settings() -> None:
        if opener is not None:
            opener(None)
        else:
            show_settings(None, window, localizer, theme_manager)

    def network() -> None:
        if network_opener is not None:
            network_opener()
        else:
            from app.gui.network_panel import open_network_panel

            open_network_panel(window)

    from app.gui.help_window import install_f1, open_help

    install_f1(window)                                  # F1: the help page for this window
    return install_top_menus(window, settings=settings, network=network, help=lambda: open_help(window))
