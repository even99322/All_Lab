"""Folder-oriented database browser with a lightweight Quick Preview."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
from time import perf_counter

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QInputDialog, QMainWindow, QMenu,
    QMessageBox, QProgressDialog, QPushButton, QSplitter, QStackedWidget,
    QLineEdit, QProgressBar, QSpinBox, QStatusBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QPlainTextEdit, QWidget,
)

from app import __version__
from app.palette import STATUS
from app.core.comment_store import CommentStore
from app.core.axis_preset_store import AxisPresetStore
from app.core.mark_store import MarkStore
from app.core.named_view_store import NamedViewStore
from app.core.overlay_store import OverlayStore
from app.core.transform_store import TransformStore
from app.core.data_rename import DataRenameError, rename_hdf5_data
from app.core.labber_comment import NativeCommentError, read_native_comment, write_native_comment
from app.core.viewer_display_state_store import ViewerDisplayStateStore
from app.core.session_store import SessionStore
from app.core.session_lifecycle import SessionLifecycleStore, StartupRecovery
from app.core.database_index_store import DatabaseIndexStore
from app.core.database_scanner import DatabaseScanResult, DatabaseScanner, LogEntry
from app.core.data_identity import stable_data_identity
from app.core.external_state import default_state_path
from app.core.measurement_transport import MeasurementReceiver
from app.core.star_store import StarStore
from app.core.tag_store import TagStore, is_flux_named, session_group_id
from app.core.tag_query import query_entries
from app.gui.database_scan_worker import DatabaseScanWorker
from app.gui.glass import GlassSlider, GlassToolSurface, optical_glass_available
from app.gui.main_window import MainWindow
from app.gui.measurement_transfer_ui import MeasurementDropTarget, MeasurementList, MeasurementSendWorker, TransferEvents
from app.gui.plot_2d_widget import DEFAULT_COLORMAP, Plot2DWidget
from app.gui.plot_widget import Plot1DWidget
from app.gui.quick_preview_worker import QuickPreviewWorker
from app.gui.recovery_report import RecoveryReportDialog
from app.gui.tag_assignment_dialog import TagAssignmentDialog
from app.gui.tag_management_dialog import TagManagementDialog
from app.gui.tag_query_dialog import TagQueryDialog
from app.icons import icon, make_icon_only
from app.interfaces import BaseInterface, InterfaceContext, InterfaceStatus
from app.interfaces.measurement.interface import MeasurementInterface
from app.interfaces.online_paper_library import open_online_paper_library
from app.interfaces.time_domain.interface import TimeDomainInterface
from app.localization import get_localization_manager
from app.settings.dialog import show_settings
from app.theme import APPEARANCE_BUTTON_SIZE, TOOLBAR_ICON_SIZE, ThemeManager, get_theme_manager

logger = logging.getLogger(__name__)

STAR_STATE_ROLE = Qt.UserRole + 1


class BrowserWindow(QMainWindow):
    def __init__(self, star_store: StarStore | None = None,
                 tag_store: TagStore | None = None,
                 comment_store: CommentStore | None = None,
                 viewer_display_state_store: ViewerDisplayStateStore | None = None,
                 session_store: SessionStore | None = None,
                 session_lifecycle: SessionLifecycleStore | None = None,
                 startup_recovery: StartupRecovery | None = None,
                 theme_manager: ThemeManager | None = None):
        super().__init__()
        self.localizer = get_localization_manager()
        self.theme_manager = theme_manager or get_theme_manager(QApplication.instance(), self.localizer.store)
        self.measurement_interface = MeasurementInterface()
        self.time_domain_interface = TimeDomainInterface()
        self._settings_dialog = None
        self.setWindowTitle(f"LabLogViewer v{__version__} — Database Browser")
        self.resize(1320, 760)
        self.scanner_root: str | None = None
        self.scan_result: DatabaseScanResult | None = None
        self.star_store = star_store or StarStore()
        if tag_store is not None:
            self.tag_store = tag_store
        elif star_store is not None:
            self.tag_store = TagStore(self.star_store.storage_path.with_name("tags.json"), legacy_paths=[])
        else:
            self.tag_store = TagStore()
        if comment_store is not None:
            self.comment_store = comment_store
        elif star_store is not None:
            self.comment_store = CommentStore(self.star_store.storage_path.with_name("comments.json"))
        else:
            self.comment_store = CommentStore()
        if viewer_display_state_store is not None:
            self.viewer_display_state_store = viewer_display_state_store
        elif star_store is not None:
            self.viewer_display_state_store = ViewerDisplayStateStore(
                self.star_store.storage_path.with_name("viewer_display_states.json")
            )
        else:
            self.viewer_display_state_store = ViewerDisplayStateStore()
        if session_store is not None:
            self.session_store = session_store
        elif star_store is not None:
            self.session_store = SessionStore(self.star_store.storage_path.with_name("session.json"))
        else:
            self.session_store = SessionStore()
        self.session_lifecycle = session_lifecycle
        self.startup_recovery = startup_recovery or StartupRecovery()
        if self.session_store.requires_safe_recovery:
            self.startup_recovery = StartupRecovery(
                safe_recovery=True,
                previous_status=self.startup_recovery.previous_status,
                last_operation=self.startup_recovery.last_operation,
                last_operation_time=self.startup_recovery.last_operation_time,
                last_checkpoint=self.startup_recovery.last_checkpoint,
                last_exception=self.startup_recovery.last_exception,
                database_path=self.startup_recovery.database_path,
                lifecycle_corrupt=self.startup_recovery.lifecycle_corrupt,
                session_corrupt=self.session_store.recovered_from_corruption,
                session_state_unavailable=True,
            )
        self._safe_recovery = self.startup_recovery.safe_recovery
        self._recovery_report_dialog: RecoveryReportDialog | None = None
        self._recovery_report_shown = False
        if star_store is not None:
            self.database_index_store = DatabaseIndexStore(self.star_store.storage_path.with_name("database_index.json"))
        else:
            self.database_index_store = DatabaseIndexStore()
        state_directory = self.star_store.storage_path.parent
        self.transform_store = TransformStore(state_directory / "transforms.json")
        self.axis_preset_store = AxisPresetStore(state_directory / "axis_presets.json")
        self.overlay_store = OverlayStore(state_directory / "overlays.json")
        self.mark_store = MarkStore(state_directory / "marks.json")
        self.named_view_store = NamedViewStore(state_directory / "named_views.json")
        self._viewers: list[MainWindow] = []
        self._debackground_dialog: DeBackgroundDialog | None = None
        self._pending_generated_open: str | None = None
        self._pending_selection_relative_path: str | None = None
        self._debackground_refresh_pending = False
        self.last_viewer_open_timing: dict[str, float] | None = None
        self._scan_worker: DatabaseScanWorker | None = None
        self._enrichment_worker: DatabaseScanWorker | None = None
        self._progress_dialog: QProgressDialog | None = None
        self._preview_worker: QuickPreviewWorker | None = None
        self._preview_workers: set[QuickPreviewWorker] = set()
        self._preview_data = None
        self._transfer_events = TransferEvents(self)
        self._transfer_events.received.connect(self._on_measurement_received)
        self._measurement_receiver: MeasurementReceiver | None = None
        self._send_workers: set[MeasurementSendWorker] = set()
        self._comment_source: str | None = None
        self._comment_database_id: str | None = None
        self._comment_relative_path: str | None = None
        self._comment_loading = False
        self._comment_dirty = False
        self._comment_native_text = ""
        self._comment_external_text = ""
        self._comment_expanded = True
        self._comment_ratio = 0.05
        self._comment_timer = QTimer(self)
        self._comment_timer.setSingleShot(True)
        self._comment_timer.setInterval(450)
        self._comment_timer.timeout.connect(self._flush_browser_comment)
        self._folder_items: dict[tuple[str, ...], QTreeWidgetItem] = {}
        self._active_query: tuple[frozenset[str], str] | None = None
        self.disk_folders: set[tuple[str, ...]] = set()      # folders on disk, empty ones too
        self._spare_viewer: MainWindow | None = None
        QTimer.singleShot(0, self._schedule_prewarm)
        from app.gui.auto_refresh import AutoRefresher

        self.auto_refresher = AutoRefresher(self, self.localizer.store)
        self._restoring_session = False
        self._pending_session: dict | None = None
        self._session_timer = QTimer(self)
        self._session_timer.setSingleShot(True)
        self._session_timer.setInterval(700)
        self._session_timer.timeout.connect(self._flush_session)
        self._build_menu()
        self._build_central_widget()
        from app.gui.annotation import AnnotationSession
        self.annotation = AnnotationSession(
            self, self.annotation_button, regions=lambda: [([self.preview_stack], False)],
            localizer=self.localizer,
        )
        self.localizer.bind(self)
        if self.theme_manager is not None:
            self._sync_appearance_controls(self.theme_manager.mode)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("No database open. File → Open Database to begin.")

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()
        # Keep the Settings menu as an ordinary top-level Cocoa menu.  Qt's
        # TextHeuristicRole may otherwise relocate a Settings/Preferences
        # menu into the application menu on macOS.
        menu_bar.setNativeMenuBar(True)
        file_menu = menu_bar.addMenu("&File")
        self.file_menu = file_menu
        open_action = QAction("&Open Database...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_database_dialog)
        file_menu.addAction(open_action)
        self.reload_action = QAction("&Reload Database", self)
        self.reload_action.setShortcut("Ctrl+R")
        self.reload_action.setEnabled(False)
        self.reload_action.triggered.connect(self.reload_database)
        file_menu.addAction(self.reload_action)
        self.new_folder_action = QAction("&New Folder...", self)
        self.new_folder_action.setObjectName("browserNewFolder")
        self.new_folder_action.setShortcut("Ctrl+Shift+N")
        self.new_folder_action.setEnabled(False)
        self.new_folder_action.triggered.connect(lambda _checked=False: self.new_folder())
        file_menu.addAction(self.new_folder_action)
        file_menu.addSeparator()
        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        tags_menu = menu_bar.addMenu("&Tags")
        self.tags_menu = tags_menu
        manage_tags_action = QAction("&Manage Tags...", self)
        manage_tags_action.triggered.connect(self._open_tag_manager)
        tags_menu.addAction(manage_tags_action)
        retrieve_action = QAction("&Retrieve by Tags...", self)
        retrieve_action.triggered.connect(self._open_tag_query)
        tags_menu.addAction(retrieve_action)
        processing_menu = menu_bar.addMenu("&Processing")
        self.processing_menu = processing_menu
        self.debackground_action = QAction("De-background...", self)
        self.debackground_action.triggered.connect(self._open_debackground)
        processing_menu.addAction(self.debackground_action)
        self.figure_builder_action = QAction("Scientific Figure Builder...", self)
        self.figure_builder_action.setObjectName("figureBuilderAction")
        self.figure_builder_action.triggered.connect(self._open_figure_builder)
        processing_menu.addAction(self.figure_builder_action)
        self.figure_builder_action.setText(self.localizer.text("fig.menu"))
        self.localizer.language_changed.connect(
            lambda _language: self.figure_builder_action.setText(self.localizer.text("fig.menu")))
        interfaces_menu = menu_bar.addMenu("Interfaces")
        interfaces_menu.menuAction().setMenuRole(QAction.MenuRole.NoRole)
        self.interfaces_menu = interfaces_menu
        self.measurement_action = QAction("Measurement", self)
        self.measurement_action.triggered.connect(self._launch_measurement_interface)
        interfaces_menu.addAction(self.measurement_action)
        self.time_domain_action = QAction("Time Domain", self)
        self.time_domain_action.triggered.connect(self._launch_time_domain_interface)
        interfaces_menu.addAction(self.time_domain_action)
        self.online_paper_library_action = QAction("Online Paper Library", self)
        self.online_paper_library_action.triggered.connect(self._launch_online_paper_library)
        interfaces_menu.addAction(self.online_paper_library_action)
        # Network Workspace and Settings open their windows directly (no sub-items).
        from app.settings.dialog import install_settings_menu

        settings_menu = install_settings_menu(self, self.localizer, self.theme_manager,
                                              opener=lambda page: self._open_settings(page))
        self.settings_menu = settings_menu
        self.settings_action = settings_menu.direct_action
        if self.theme_manager is not None:
            self.theme_manager.mode_changed.connect(self._sync_appearance_controls)
            self.theme_manager.theme_changed.connect(
                lambda _theme: self._update_appearance_button()
                if hasattr(self, "appearance_toggle_button") else None
            )
            self.theme_manager.scientific_plot_appearance_changed.connect(
                self._sync_plot_appearance_controls
            )
            self.theme_manager.export_plot_background_changed.connect(
                self._sync_plot_appearance_controls
            )
            self._sync_appearance_controls(self.theme_manager.mode)
            self._sync_plot_appearance_controls(self.theme_manager.scientific_plot_appearance)
            self._sync_plot_appearance_controls(self.theme_manager.export_plot_background)

    def _build_interface_context(self) -> InterfaceContext:
        selected = self._selected_data()[1]
        database_path = Path(self.scanner_root) if self.scanner_root else None
        if selected is not None:
            selected_path = Path(selected.absolute_path)
            selected_folder = selected_path.parent
        else:
            selected_path = None
            folder_item = self.folder_tree.currentItem() if hasattr(self, "folder_tree") else None
            folder_parts = folder_item.data(0, Qt.UserRole) if folder_item is not None else ()
            selected_folder = database_path.joinpath(*folder_parts) if database_path is not None else None
        sweep_information = None
        if selected is not None and selected.metadata_complete and selected.sweep_dimension:
            sweep_information = {"summary": selected.sweep_dimension}
        return InterfaceContext(
            database_path=database_path,
            database_name=database_path.name if database_path is not None else None,
            database_identity=(
                self.scan_result.database_id if self.scan_result is not None
                else stable_data_identity(database_path) if database_path is not None else None
            ),
            selected_folder=selected_folder,
            selected_log_name=selected.log_name if selected is not None else None,
            selected_log_id=None,
            selected_log_path=selected_path,
            selected_channel=None,
            selected_dimensions=None,
            sweep_information=sweep_information,
            metadata=None,
            instrument_metadata=None,
        )

    def _launch_interface(self, interface: BaseInterface, unavailable_key: str) -> None:
        try:
            result = interface.launch(self._build_interface_context())
        except Exception:
            logger.exception("Interface launch failed")
            QMessageBox.warning(
                self, self.localizer.text("menu.interfaces"),
                self.localizer.text("interfaces.launch_failed"),
            )
            return
        if result.status is InterfaceStatus.UNAVAILABLE:
            QMessageBox.information(
                self, self.localizer.text("menu.interfaces"), self.localizer.text(unavailable_key),
            )
        elif result.status is InterfaceStatus.ERROR:
            QMessageBox.warning(
                self, self.localizer.text("menu.interfaces"),
                result.message or self.localizer.text("interfaces.launch_failed"),
            )

    def _launch_measurement_interface(self, _checked: bool = False) -> None:
        self._launch_interface(self.measurement_interface, "interfaces.measurement_unconfigured")

    def _launch_time_domain_interface(self, _checked: bool = False) -> None:
        self._launch_interface(self.time_domain_interface, "interfaces.time_domain_unconfigured")

    def _launch_online_paper_library(self, _checked: bool = False) -> None:
        from app.interfaces import qel

        if qel.show_related_papers(self):       # QEL Lab：依選取數據的標籤列出論文
            return
        if not open_online_paper_library():
            QMessageBox.warning(
                self, self.localizer.text("menu.interfaces"),
                self.localizer.text("interfaces.paper_library_failed"),
            )

    def _open_settings(self, page: str | None = None) -> None:
        self._settings_dialog = show_settings(page, self, self.localizer, self.theme_manager)

    def _set_appearance(self, mode: str) -> None:
        if self.theme_manager is not None:
            self.theme_manager.set_mode(mode)

    APPEARANCE_CYCLE = ("light", "dark", "system")

    def _build_glass_controls(self, row: QHBoxLayout) -> None:
        """Thickness and Frost dials for the toolbar glass (persisted on release)."""
        self.glass_controls = QWidget()
        self.glass_controls.setObjectName("glassControls")
        layout = QHBoxLayout(self.glass_controls)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)
        thickness, frost = (self.theme_manager.glass_material
                            if self.theme_manager is not None else (0.5, 0.25))
        available = optical_glass_available() and not self._safe_recovery
        self.glass_sliders = {}
        for index, (key, value, tip) in enumerate((
            ("thickness", thickness, "Glass thickness: refraction depth and rim highlight"),
            ("frost", frost, "Glass frost: blur and milky haze"),
        )):
            if index:
                layout.addSpacing(6)
            label = QLabel("Thickness" if key == "thickness" else "Frost")
            label.setObjectName("glassDialLabel")
            slider = GlassSlider()
            slider.setObjectName(f"glass{key.title()}Slider")
            slider.setValue(round(value * 100))
            slider.setEnabled(available)
            unavailable = "Glass effect is off (Safe Recovery or LABLOGVIEWER_DISABLE_GLASS)"
            for widget in (label, slider):
                widget.setToolTip(tip if available else unavailable)
            slider.setAccessibleName(tip)
            slider.valueChanged.connect(lambda _value: self._on_glass_dial_changed(final=False))
            slider.released.connect(lambda _value: self._on_glass_dial_changed(final=True))
            layout.addWidget(label)
            layout.addWidget(slider)
            self.glass_sliders[key] = slider
        row.addWidget(self.glass_controls)
        if self.theme_manager is not None:
            self.theme_manager.glass_material_changed.connect(self._sync_glass_dials)

    def _sync_glass_dials(self, thickness: float, frost: float, _final: bool) -> None:
        for key, value in (("thickness", thickness), ("frost", frost)):
            slider = self.glass_sliders[key]
            if not slider.isSliderDown() and slider.value() != round(value * 100):
                slider.blockSignals(True)
                slider.setValue(round(value * 100))
                slider.blockSignals(False)
                slider.update()

    def _on_glass_dial_changed(self, *, final: bool) -> None:
        if self.theme_manager is None:
            return
        self.theme_manager.set_glass_material(
            self.glass_sliders["thickness"].value() / 100.0,
            self.glass_sliders["frost"].value() / 100.0,
            final=final,
        )

    def _cycle_appearance(self) -> None:
        current = self.theme_manager.mode if self.theme_manager is not None else "system"
        cycle = self.APPEARANCE_CYCLE
        next_mode = cycle[(cycle.index(current) + 1) % len(cycle)] if current in cycle else cycle[0]
        self._set_appearance(next_mode)
        self._update_appearance_button()

    def _update_appearance_button(self) -> None:
        button = self.appearance_toggle_button
        mode = self.theme_manager.mode if self.theme_manager is not None else "system"
        button.setProperty("appearanceMode", mode)
        button.setIcon(icon(f"theme_{mode}"))
        label = f"{self.localizer.text('settings.appearance')}: {self.localizer.text(f'theme.{mode}')}"
        if mode == "system" and self.theme_manager is not None:
            label += f" ({self.localizer.text(f'theme.{self.theme_manager.resolved_theme}')})"
        button.setToolTip(label)
        button.setAccessibleName(label)

    def _sync_appearance_controls(self, mode: str) -> None:
        if hasattr(self, "appearance_toggle_button"):
            self._update_appearance_button()
        for action_mode, action in getattr(self, "appearance_actions", {}).items():
            action.setChecked(action_mode == mode)

    def _sync_plot_appearance_controls(self, _appearance: str) -> None:
        if self.theme_manager is None:
            return
        plot_appearance = self.theme_manager.scientific_plot_appearance
        export_appearance = self.theme_manager.export_plot_background
        for appearance, action in getattr(self, "scientific_plot_actions", {}).items():
            action.setChecked(appearance == plot_appearance)
        for appearance, action in getattr(self, "export_plot_actions", {}).items():
            action.setChecked(appearance == export_appearance)

    def _build_central_widget(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(6, 6, 6, 6)
        # Network Workspace (Host / Client) sits at the very top of the Browser.
        from app.gui.network_panel import NetworkStatusBar

        self.network_bar = NetworkStatusBar(central)
        outer.addWidget(self.network_bar)
        # One compact top row whose three segments track the workspace columns,
        # so Star/Tag sit directly above "Data in Folder".
        self.browser_glass_toolbar = GlassToolSurface(central)
        self.browser_glass_toolbar.setObjectName("browserGlassToolbar")
        self.browser_glass_toolbar.set_optical_glass_enabled(not self._safe_recovery)
        toolbar = QHBoxLayout(self.browser_glass_toolbar)
        # A little vertical room lets each glass capsule show its rim and shadow.
        toolbar.setContentsMargins(6, 3, 6, 3)
        toolbar.setSpacing(0)
        self._toolbar_segments = []
        for _index in range(3):
            segment = QWidget()
            segment_layout = QHBoxLayout(segment)
            segment_layout.setContentsMargins(0, 0, 6, 0)
            segment_layout.setSpacing(4)
            toolbar.addWidget(segment)
            self._toolbar_segments.append(segment_layout)
        database_row, data_row, view_row = self._toolbar_segments

        self.open_button = make_icon_only(QToolButton(), "open", "Open Database...", TOOLBAR_ICON_SIZE, flat=True)
        self.open_button.clicked.connect(self.open_database_dialog)
        database_row.addWidget(self.open_button)
        self.reload_button = make_icon_only(QToolButton(), "reload", "Reload Database", TOOLBAR_ICON_SIZE, flat=True)
        self.reload_button.setEnabled(False)
        self.reload_button.clicked.connect(self.reload_database)
        database_row.addWidget(self.reload_button)
        database_row.addStretch(1)

        self.star_button = make_icon_only(
            QToolButton(), "star", self.localizer.text("browser.star"), TOOLBAR_ICON_SIZE, flat=True
        )
        self.star_button.setProperty("_lv_content_value", True)
        self.localizer.language_changed.connect(lambda _language: self._sync_star_button())
        self.star_button.setEnabled(False)
        self.star_button.clicked.connect(self._toggle_selected_star)
        data_row.addWidget(self.star_button)
        self.tag_button = make_icon_only(QToolButton(), "tag", "Tag...", TOOLBAR_ICON_SIZE, flat=True)
        self.tag_button.setEnabled(False)
        self.tag_button.clicked.connect(self._edit_selected_tags)
        data_row.addWidget(self.tag_button)
        self.retrieve_button = make_icon_only(
            QToolButton(), "filter", "Retrieve by Tags...", TOOLBAR_ICON_SIZE, flat=True
        )
        self.retrieve_button.setEnabled(False)
        self.retrieve_button.clicked.connect(self._open_tag_query)
        data_row.addWidget(self.retrieve_button)
        self.clear_query_button = make_icon_only(
            QToolButton(), "back", "Back to Folder", TOOLBAR_ICON_SIZE, flat=True
        )
        self.clear_query_button.setEnabled(False)
        self.clear_query_button.clicked.connect(self._clear_query)
        data_row.addWidget(self.clear_query_button)
        data_row.addStretch(1)

        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.addStretch(1)
        self._build_glass_controls(view_row)
        view_row.addSpacing(16)
        self.appearance_toggle_button = QToolButton()
        self.appearance_toggle_button.setObjectName("appearanceToggle")
        self.appearance_toggle_button.setFixedSize(APPEARANCE_BUTTON_SIZE, APPEARANCE_BUTTON_SIZE)
        self.appearance_toggle_button.setIconSize(QSize(24, 24))
        self.appearance_toggle_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.appearance_toggle_button.clicked.connect(self._cycle_appearance)
        self.localizer.language_changed.connect(lambda _language: self._update_appearance_button())
        view_row.addWidget(self.appearance_toggle_button)
        self.debackground_action.setIcon(icon("debackground"))
        self.debackground_button = QToolButton()
        self.debackground_button.setDefaultAction(self.debackground_action)
        make_icon_only(self.debackground_button, "debackground", self.debackground_action.text(),
                       TOOLBAR_ICON_SIZE, flat=True)
        view_row.addWidget(self.debackground_button)
        # Annotation sits at the top right in every window.
        from app.gui.annotation import make_annotation_button
        self.annotation_button = make_annotation_button(TOOLBAR_ICON_SIZE)
        view_row.addWidget(self.annotation_button)
        self._update_appearance_button()
        outer.addWidget(self.browser_glass_toolbar)
        self._browser_toolbar = toolbar

        workspace = QSplitter(Qt.Horizontal)
        left = QSplitter(Qt.Vertical)
        folder_group = QGroupBox("Folders")
        folder_layout = QVBoxLayout(folder_group)
        folder_layout.setContentsMargins(4, 4, 4, 4)
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.itemSelectionChanged.connect(self._on_folder_selection_changed)
        self.folder_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.folder_tree.customContextMenuRequested.connect(self._on_folder_context_menu)
        self.folder_tree.itemExpanded.connect(self._update_folder_icon)
        self.folder_tree.itemCollapsed.connect(self._update_folder_icon)
        folder_layout.addWidget(self.folder_tree)
        left.addWidget(folder_group)
        tags_group = QGroupBox("Tags of Selected Data")
        tags_layout = QVBoxLayout(tags_group)
        tags_layout.setContentsMargins(4, 4, 4, 4)
        self.selected_log_label = QLabel("No Data selected")
        self.selected_log_label.setWordWrap(True)
        tags_layout.addWidget(self.selected_log_label)
        self.tags_tree = QTreeWidget()
        self.tags_tree.setHeaderHidden(True)
        self.tags_tree.itemDoubleClicked.connect(self._on_tag_tree_double_clicked)
        tags_layout.addWidget(self.tags_tree)
        self.edit_tags_button = QPushButton("Edit Tags...")
        self.edit_tags_button.setEnabled(False)
        self.edit_tags_button.clicked.connect(self._edit_selected_tags)
        tags_layout.addWidget(self.edit_tags_button)
        left.addWidget(tags_group)
        left.setSizes([520, 230])
        workspace.addWidget(left)

        self.data_group = QGroupBox("Data in Folder")
        data_layout = QVBoxLayout(self.data_group)
        data_layout.setContentsMargins(4, 4, 4, 4)
        self.query_status_label = QLabel()
        self.query_status_label.setWordWrap(True)
        self.query_status_label.hide()
        data_layout.addWidget(self.query_status_label)
        self.data_list = MeasurementList()
        self.data_list.setColumnCount(5)
        self.data_list.setHeaderLabels(["Star", "Log name", "Date created", "Sweep dimension", "Tags"])
        self.data_list.setRootIsDecorated(False)
        self.data_list.setAlternatingRowColors(True)
        self.data_list.itemSelectionChanged.connect(self._on_data_selection_changed)
        self.data_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.data_list.itemClicked.connect(self._on_data_item_clicked)
        self.data_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.data_list.customContextMenuRequested.connect(self._on_context_menu)
        data_layout.addWidget(self.data_list)
        self.no_results_label = QLabel("No matching data.")
        self.no_results_label.setAlignment(Qt.AlignCenter)
        self.no_results_label.hide()
        data_layout.addWidget(self.no_results_label)
        workspace.addWidget(self.data_group)
        self.tree = self.data_list

        preview_group = QGroupBox("Quick Preview")
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(4, 4, 4, 4)
        transfer_row = QHBoxLayout()
        transfer_row.addWidget(QLabel("Receiver"))
        self.transfer_host = QLineEdit("127.0.0.1")
        self.transfer_host.setPlaceholderText("IP / host")
        self.transfer_host.setToolTip("IP address or host name of the receiving LabLogViewer")
        self.transfer_host.setMaximumWidth(145)
        transfer_row.addWidget(self.transfer_host)
        self.transfer_port = QSpinBox()
        self.transfer_port.setRange(1, 65535)
        self.transfer_port.setValue(53117)
        self.transfer_port.setToolTip("Receiver TCP port")
        transfer_row.addWidget(self.transfer_port)
        self.transfer_listen = QPushButton("Receive")
        self.transfer_listen.setCheckable(True)
        self.transfer_listen.setToolTip("Listen for measurements from another LabLogViewer")
        self.transfer_listen.toggled.connect(self._toggle_measurement_receiver)
        transfer_row.addWidget(self.transfer_listen)
        self.transfer_status = QLabel("Not connected")
        transfer_row.addWidget(self.transfer_status, 1)
        preview_layout.addLayout(transfer_row)
        self.transfer_drop_target = MeasurementDropTarget()
        self.transfer_drop_target.dropped.connect(self._send_dropped_measurement)
        preview_layout.addWidget(self.transfer_drop_target)
        self.transfer_progress = QProgressBar()
        self.transfer_progress.setRange(0, 100)
        self.transfer_progress.hide()
        preview_layout.addWidget(self.transfer_progress)
        self.preview_status = QLabel("Select a data entry to preview it.")
        self.preview_status.setWordWrap(True)
        preview_layout.addWidget(self.preview_status)
        self.selected_tags_label = QLabel("Tags: none")
        self.selected_tags_label.setWordWrap(True)
        preview_layout.addWidget(self.selected_tags_label)
        self.preview_stack = QStackedWidget()
        self.preview_empty = QLabel("No preview")
        self.preview_empty.setAlignment(Qt.AlignCenter)
        self.preview_1d = Plot1DWidget()
        self.preview_2d = Plot2DWidget()
        self.preview_stack.addWidget(self.preview_empty)
        self.preview_stack.addWidget(self.preview_1d)
        self.preview_stack.addWidget(self.preview_2d)
        preview_layout.addWidget(self.preview_stack, 1)
        self.preview_comment_splitter = QSplitter(Qt.Vertical)
        self.preview_comment_splitter.setObjectName("browserPreviewCommentSplitter")
        self.preview_comment_splitter.addWidget(preview_group)

        self.comment_panel = QWidget(self.preview_comment_splitter)
        comment_layout = QVBoxLayout(self.comment_panel)
        comment_layout.setContentsMargins(4, 2, 4, 4)
        comment_layout.setSpacing(2)
        comment_header = QHBoxLayout()
        self.comment_toggle_button = QToolButton(self.comment_panel)
        self.comment_toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.comment_toggle_button.clicked.connect(self._toggle_comment_panel)
        comment_header.addWidget(self.comment_toggle_button)
        comment_header.addStretch(1)
        comment_layout.addLayout(comment_header)
        self.comment_editor = QPlainTextEdit(self.comment_panel)
        self.comment_editor.setPlaceholderText("Comment for the selected Data")
        self.comment_editor.setEnabled(False)
        self.comment_editor.textChanged.connect(self._schedule_browser_comment_save)
        comment_layout.addWidget(self.comment_editor, 1)
        self.preview_comment_splitter.addWidget(self.comment_panel)
        self.preview_comment_splitter.setStretchFactor(0, 1)
        self.preview_comment_splitter.setStretchFactor(1, 0)
        self.preview_comment_splitter.splitterMoved.connect(self._on_comment_splitter_moved)
        workspace.addWidget(self.preview_comment_splitter)
        workspace.setStretchFactor(0, 1)
        workspace.setStretchFactor(1, 2)
        workspace.setStretchFactor(2, 3)
        workspace.setSizes([220, 480, 700])
        workspace.splitterMoved.connect(lambda *_args: self._schedule_session_save())
        workspace.splitterMoved.connect(lambda *_args: self._align_toolbar_segments())
        outer.addWidget(workspace, 1)
        self.browser_splitter = workspace
        self.setCentralWidget(central)
        self._apply_comment_panel_size(default=True)
        self._sync_selected_tags()

    # ---- selected Data Comment -------------------------------------------

    def _comment_header_height(self) -> int:
        return max(24, self.comment_toggle_button.sizeHint().height() + 4)

    def _comment_total_height(self) -> int:
        return max(sum(self.preview_comment_splitter.sizes()), self.preview_comment_splitter.height(), 1)

    def _apply_comment_panel_size(self, *, default: bool = False) -> None:
        """Keep the Browser note compact: 5% by default, capped at 15%."""
        if not hasattr(self, "preview_comment_splitter"):
            return
        total = self._comment_total_height()
        header = self._comment_header_height()
        if default:
            self._comment_ratio = 0.05
        maximum = max(header, int(total * 0.15))
        if self._comment_expanded:
            desired = max(header, int(total * max(0.05, min(0.15, self._comment_ratio))))
            desired = min(desired, maximum)
            self.comment_editor.show()
            self.comment_panel.setMaximumHeight(maximum)
            self.comment_toggle_button.setText("Comment")
            self.comment_toggle_button.setArrowType(Qt.DownArrow)
        else:
            desired = header
            self.comment_editor.hide()
            self.comment_panel.setMaximumHeight(header)
            self.comment_toggle_button.setText("Comment")
            self.comment_toggle_button.setArrowType(Qt.RightArrow)
        self.preview_comment_splitter.setSizes([max(total - desired, 1), desired])

    def _on_comment_splitter_moved(self, _position: int, _index: int) -> None:
        if not self._comment_expanded:
            self._apply_comment_panel_size()
            return
        total = self._comment_total_height()
        comment_height = self.preview_comment_splitter.sizes()[-1]
        self._comment_ratio = max(0.05, min(0.15, comment_height / total))
        if comment_height > max(self._comment_header_height(), int(total * 0.15)):
            self._apply_comment_panel_size()
        self._schedule_session_save()

    def _toggle_comment_panel(self) -> None:
        self._comment_expanded = not self._comment_expanded
        self._apply_comment_panel_size()
        self._schedule_session_save()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "preview_comment_splitter"):
            QTimer.singleShot(0, self._apply_comment_panel_size)
        if hasattr(self, "browser_splitter"):
            QTimer.singleShot(0, self._align_toolbar_segments)

    def _align_toolbar_segments(self) -> None:
        sizes = self.browser_splitter.sizes()
        handle = self.browser_splitter.handleWidth()
        for index, size in enumerate(sizes[:len(self._toolbar_segments)]):
            width = size + (handle if index < len(sizes) - 1 else 0)
            self._browser_toolbar.setStretch(index, max(1, width))

    def _schedule_browser_comment_save(self) -> None:
        if not self._comment_loading and self._comment_source:
            self._comment_dirty = True
            self._comment_timer.start()

    def _load_browser_comment(self, entry: LogEntry | None) -> None:
        self._flush_browser_comment()
        self._comment_source = entry.absolute_path if entry is not None else None
        self._comment_database_id = self._entry_database(entry) if entry is not None and self.scan_result else None
        self._comment_relative_path = entry.relative_path if entry is not None else None
        self._comment_loading = True
        try:
            if entry is None:
                self.comment_editor.clear()
                self.comment_editor.setEnabled(False)
                self.comment_editor.setToolTip("")
                return
            native = read_native_comment(entry.absolute_path)
            external = self.comment_store.get_for_source(
                entry.absolute_path, database_id=self._comment_database_id,
                relative_path=self._comment_relative_path,
            )
            self._comment_native_text = native.text
            self._comment_external_text = external
            # Native Labber text is authoritative when it exists. A distinct
            # pre-v0.13D external note is deliberately retained, never merged
            # or discarded; the tooltip makes that conservative policy clear.
            text = native.text if native.text else external
            self.comment_editor.setPlainText(text)
            self.comment_editor.setEnabled(True)
            if native.text and external and external != native.text:
                self.comment_editor.setToolTip(
                    "A distinct legacy LabLogViewer note is preserved externally. "
                    "Edits update the native Labber Comment when supported."
                )
            elif not native.supported:
                self.comment_editor.setToolTip(
                    f"Native Labber Comment is unavailable; this note is stored externally. {native.reason or ''}".strip()
                )
            else:
                self.comment_editor.setToolTip("Edits are saved to the confirmed native Labber Comment attribute.")
        finally:
            self._comment_loading = False
            self._comment_dirty = False

    def _flush_browser_comment(self) -> None:
        if self._comment_timer.isActive():
            self._comment_timer.stop()
        if not self._comment_source or self._comment_loading or not self._comment_dirty:
            return
        text = self.comment_editor.toPlainText()
        try:
            write_native_comment(self._comment_source, text)
            # A v0.13B note that was the only displayed Comment is migrated
            # with the user's intentional edit. Distinct native/external text
            # is never overwritten implicitly.
            if not self._comment_external_text or self._comment_external_text == self._comment_native_text:
                self.comment_store.set_for_source(
                    self._comment_source, text, database_id=self._comment_database_id,
                    relative_path=self._comment_relative_path,
                )
            self.statusBar().showMessage("Saved native Labber Comment.", 2500)
        except NativeCommentError as error:
            # Unsupported or failed native writes retain the typed note in the
            # established external store instead of losing it.
            self.comment_store.set_for_source(
                self._comment_source, text, database_id=self._comment_database_id,
                relative_path=self._comment_relative_path,
            )
            self.statusBar().showMessage(f"Saved external Comment ({error}).", 5000)
        finally:
            self._comment_dirty = False

    def _restore_comment_panel_state(self, state: object) -> None:
        if not isinstance(state, dict):
            return
        self._comment_expanded = bool(state.get("expanded", True))
        ratio = state.get("ratio", 0.05)
        try:
            self._comment_ratio = max(0.05, min(0.15, float(ratio)))
        except (TypeError, ValueError):
            self._comment_ratio = 0.05
        self._apply_comment_panel_size()

    def open_database_dialog(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open Database Folder")
        if path:
            self.open_database(path)

    def open_database(self, root_path: str) -> None:
        self.scanner_root = str(Path(root_path).expanduser().resolve())
        self._record_operation("Database opening started")
        self._start_scan(self.scanner_root)
        self._schedule_session_save()

    def _record_operation(self, label: str, **details) -> None:
        if self.session_lifecycle is not None:
            self.session_lifecycle.record_operation(label, **details)

    def _show_recovery_report(self) -> None:
        if not self._safe_recovery or self._recovery_report_shown:
            return
        self._recovery_report_shown = True
        self._recovery_report_dialog = RecoveryReportDialog(self.startup_recovery, self)
        self._recovery_report_dialog.show()
        self._recovery_report_dialog.raise_()

    def reload_database(self) -> None:
        if self.scanner_root:
            self._start_scan(self.scanner_root)

    def _start_scan(self, root_path: str) -> None:
        self.open_button.setEnabled(False)
        self.reload_button.setEnabled(False)
        self.reload_action.setEnabled(False)
        self._progress_dialog = QProgressDialog("Scanning HDF5 files...", None, 0, 0, self)
        self._progress_dialog.setWindowModality(Qt.WindowModal)
        self._progress_dialog.setMinimumDuration(300)
        self._progress_dialog.setWindowTitle("Opening Database")
        self._scan_worker = DatabaseScanWorker(
            root_path, self.database_index_store, fast_listing=True, parent=self,
        )
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.finished_scan.connect(self._on_scan_finished)
        self._scan_worker.failed.connect(self._on_scan_failed)
        self._scan_worker.start()

    def _start_metadata_enrichment(self, root_path: str) -> None:
        """Parse only uncached entries after the Browser is already usable."""
        if self._enrichment_worker is not None and self._enrichment_worker.isRunning():
            return
        worker = DatabaseScanWorker(root_path, self.database_index_store, parent=self)
        self._enrichment_worker = worker
        worker.finished_scan.connect(self._on_metadata_enriched)
        worker.failed.connect(lambda message: self.statusBar().showMessage(f"Metadata enrichment failed: {message}"))
        worker.start()

    # -- new folder --------------------------------------------------------------------------------
    def _on_folder_context_menu(self, position) -> None:
        if self.scan_result is None:
            return
        item = self.folder_tree.itemAt(position)
        parts = tuple(item.data(0, Qt.UserRole) or ()) if item is not None else ()
        menu = QMenu(self)
        action = menu.addAction(self.localizer.text("browser.new_folder"))
        action.triggered.connect(lambda _checked=False, parts=parts: self.new_folder(parts))
        menu.exec(self.folder_tree.viewport().mapToGlobal(position))

    def new_folder(self, parent_parts: tuple[str, ...] | None = None, name: str | None = None) -> Path | None:
        """Create a folder in the opened database (inside the selected folder by default).
        ``name`` skips the question (tests). Measurement files are never touched."""
        if self.scan_result is None:
            return None
        if parent_parts is None:
            item = self.folder_tree.currentItem()
            parent_parts = tuple(item.data(0, Qt.UserRole) or ()) if item is not None else ()
        parent = Path(self.scan_result.root_path).joinpath(*parent_parts)
        text = self.localizer.text
        interactive = name is None
        if name is None:
            where = "/".join(parent_parts) or Path(self.scan_result.root_path).name
            name, ok = QInputDialog.getText(self, text("browser.new_folder_title"),
                                            text("browser.new_folder_prompt").format(parent=where))
            if not ok:
                return None
        name = str(name).strip()
        problem = None
        if not name or name in (".", ".."):
            problem = text("browser.new_folder_empty")
        elif any(ch in name for ch in '/\\:*?"<>|') or name.startswith(".") or name.endswith((" ", ".")):
            problem = text("browser.new_folder_bad")
        elif (parent / name).exists():
            if interactive:
                from app.gui.save_target import ask_keep_both

                other = ask_keep_both(self, parent / name)
                if other is None:
                    return None
                name = other.name
            else:
                problem = text("browser.new_folder_exists").format(name=name)
        if problem is None:
            try:
                (parent / name).mkdir()
            except OSError as error:
                problem = text("browser.new_folder_failed").format(reason=error.strerror or str(error))
        if problem is not None:
            QMessageBox.warning(self, text("browser.new_folder_title"), problem)
            return None
        created = tuple(parent_parts) + (name,)
        self.disk_folders.add(created)
        self.apply_auto_refresh(self.scan_result, rebuild_tree=True, new_entries=[])
        item = self._folder_items.get(created)
        if item is not None:
            self.folder_tree.setCurrentItem(item)
        self._record_operation("Folder created", folder="/".join(created))
        self.statusBar().showMessage(text("browser.new_folder_done").format(name="/".join(created)), 4000)
        return parent / name

    # -- auto refresh (app/gui/auto_refresh.py) -------------------------------------------------
    def apply_auto_refresh(self, result: DatabaseScanResult, *, rebuild_tree: bool,
                           new_entries: list[LogEntry]) -> None:
        """Show what auto refresh found without losing the selection or the scroll position."""
        selected = self._selected_data()[1]
        selected_path = selected.relative_path if selected is not None else None
        current_folder = self.folder_tree.currentItem()
        folder_parts = tuple(current_folder.data(0, Qt.UserRole) or ()) if current_folder is not None else ()
        scroll = self.data_list.verticalScrollBar().value()
        self.scan_result = result
        if new_entries:
            self.tag_store.initialize_entries(result.database_id, new_entries)    # automatic Tags
        if rebuild_tree:
            tree_scroll = self.folder_tree.verticalScrollBar().value()
            blocked = self.folder_tree.blockSignals(True)
            try:
                self._populate_folder_tree(result)
                folder_item = self._folder_items.get(folder_parts)
                if folder_item is not None:
                    self.folder_tree.setCurrentItem(folder_item)
            finally:
                self.folder_tree.blockSignals(blocked)
            self.folder_tree.verticalScrollBar().setValue(tree_scroll)
        if self._active_query is not None:
            return                                         # a Tag search result stays as it is
        blocked = self.data_list.blockSignals(True)
        try:
            self._populate_data_list(folder_parts)
            reselected = None
            if selected_path:
                for row in range(self.data_list.topLevelItemCount()):
                    item = self.data_list.topLevelItem(row)
                    entry = item.data(0, Qt.UserRole)
                    if entry is not None and entry.relative_path == selected_path:
                        self.data_list.setCurrentItem(item)
                        reselected = entry
                        break
        finally:
            self.data_list.blockSignals(blocked)
        self.data_list.verticalScrollBar().setValue(scroll)
        if selected is not None and reselected is not None and (
                reselected.size_bytes, reselected.mtime) != (selected.size_bytes, selected.mtime):
            self._on_data_selection_changed()              # the selected file changed: refresh its preview

    def auto_refresh_message(self, added: int, changed: int, removed: int) -> str:
        return self.localizer.text("browser.auto_refresh_done").format(added=added, changed=changed, removed=removed)

    def _on_metadata_enriched(self, result: DatabaseScanResult) -> None:
        if self.scanner_root != str(Path(result.root_path).expanduser().resolve()):
            return
        selected = self._selected_data()[1]
        selected_relative_path = selected.relative_path if selected is not None else None
        current_folder = self.folder_tree.currentItem()
        folder_parts = current_folder.data(0, Qt.UserRole) if current_folder is not None else ()
        self.scan_result = result
        self._populate_folder_tree(result)
        folder_item = self._folder_items.get(tuple(folder_parts or ()))
        if folder_item is not None:
            self.folder_tree.setCurrentItem(folder_item)
        self._populate_data_list(tuple(folder_parts or ()))
        if selected_relative_path:
            for row in range(self.data_list.topLevelItemCount()):
                item = self.data_list.topLevelItem(row)
                entry = item.data(0, Qt.UserRole)
                if entry is not None and entry.relative_path == selected_relative_path:
                    self.data_list.setCurrentItem(item)
                    break
        self.statusBar().showMessage(f"Database metadata ready — {result.n_ok} logs")

    def _on_scan_progress(self, done: int, total: int) -> None:
        dialog = self._progress_dialog
        if dialog is not None:
            try:
                if total > 0:
                    dialog.setRange(0, total)
                    dialog.setValue(done)
                dialog.setLabelText(f"Scanning HDF5 files...\n{done} / {total}")
            except RuntimeError:
                # Closing the Browser may delete the modal dialog while a
                # queued worker progress signal is still in flight.
                return

    def _on_scan_finished(self, result: DatabaseScanResult) -> None:
        active_query = self._active_query
        from app.core.change_watch import folders_on_disk

        self.disk_folders = folders_on_disk(result.root_path)     # empty folders are shown too
        self.scan_result = result
        self._populate_folder_tree(result)
        if self._pending_selection_relative_path is not None:
            self._select_relative_path(self._pending_selection_relative_path)
            self._pending_selection_relative_path = None
        self.open_button.setEnabled(True)
        self.reload_button.setEnabled(True)
        self.reload_action.setEnabled(True)
        self.new_folder_action.setEnabled(True)
        self.retrieve_button.setEnabled(True)
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None
        self.statusBar().showMessage(
            f"Database: {result.root_path}  —  {result.n_ok} logs"
            + (f"  ({result.n_error} could not be parsed)" if result.n_error else "")
        )
        if any(not entry.metadata_complete for entry in result.entries):
            self.statusBar().showMessage(
                f"Database usable — enriching metadata for {sum(not entry.metadata_complete for entry in result.entries)} files..."
            )
            self._start_metadata_enrichment(result.root_path)
        if active_query is not None:
            self._run_tag_query(set(active_query[0]), active_query[1], record=False)
        if self._pending_session is not None:
            self._restore_after_scan(self._pending_session)
            self._pending_session = None
        elif self._safe_recovery:
            self._show_recovery_report()
        if self.session_lifecycle is not None:
            self.session_lifecycle.record_checkpoint(
                "Database loaded successfully", database_path=result.root_path
            )
        if self._debackground_refresh_pending:
            self._debackground_refresh_pending = False
            QTimer.singleShot(0, self.reload_database)
        else:
            self._open_pending_generated_if_ready(result)
        self._schedule_session_save()

    def _on_scan_failed(self, message: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None
        self.open_button.setEnabled(True)
        self.reload_button.setEnabled(self.scanner_root is not None)
        self.reload_action.setEnabled(self.scanner_root is not None)
        QMessageBox.critical(self, "Scan failed", f"Could not scan this folder:\n\n{message}")
        self._show_recovery_report()

    def _populate_folder_tree(self, result: DatabaseScanResult) -> None:
        self.tag_store.initialize_entries(result.database_id, result.entries)
        self.folder_tree.clear()
        self.data_list.clear()
        self._folder_items = {}
        root = QTreeWidgetItem([Path(result.root_path).name or result.root_path])
        root.setData(0, Qt.UserRole, ())
        self.folder_tree.addTopLevelItem(root)
        self._folder_items[()] = root
        folders: set[tuple[str, ...]] = set()
        for parts in [entry.folder_parts for entry in result.entries] + list(self.disk_folders):
            for length in range(1, len(parts) + 1):
                folders.add(tuple(parts[:length]))
        for parts in sorted(folders, key=lambda value: tuple(p.lower() for p in value)):
            item = QTreeWidgetItem([parts[-1]])
            item.setData(0, Qt.UserRole, parts)
            self._folder_items[parts[:-1]].addChild(item)
            self._folder_items[parts] = item
        self.folder_tree.expandAll()
        for item in self._folder_items.values():
            self._update_folder_icon(item)
        self.folder_tree.setCurrentItem(root)
        self._populate_data_list(())

    @staticmethod
    def _update_folder_icon(item: QTreeWidgetItem) -> None:
        opened = item.isExpanded() and item.childCount() > 0
        item.setIcon(0, icon("folder_open" if opened else "folder"))

    def _on_folder_selection_changed(self) -> None:
        item = self.folder_tree.currentItem()
        if item is not None:
            self._active_query = None
            self.clear_query_button.setEnabled(False)
            self._populate_data_list(item.data(0, Qt.UserRole) or ())
            self._schedule_session_save()

    def _populate_data_list(self, folder_parts: tuple[str, ...]) -> None:
        if self.scan_result is None:
            self._display_entries([], query_mode=False)
            return
        entries = [entry for entry in self.scan_result.entries if entry.folder_parts == folder_parts]
        entries.sort(key=lambda value: value.log_name.lower())
        self._display_entries(entries, query_mode=False)

    def _select_relative_path(self, relative_path: str) -> bool:
        if self.scan_result is None:
            return False
        entry = next((item for item in self.scan_result.entries
                      if item.relative_path == relative_path), None)
        if entry is None:
            return False
        folder_item = self._folder_items.get(entry.folder_parts)
        previous = self.folder_tree.blockSignals(True)
        try:
            if folder_item is not None:
                self.folder_tree.setCurrentItem(folder_item)
        finally:
            self.folder_tree.blockSignals(previous)
        self._populate_data_list(entry.folder_parts)
        for row in range(self.data_list.topLevelItemCount()):
            item = self.data_list.topLevelItem(row)
            candidate = item.data(0, Qt.UserRole)
            if candidate is not None and candidate.relative_path == relative_path:
                self.data_list.setCurrentItem(item)
                return True
        return False

    def _display_entries(self, entries: list[LogEntry], *, query_mode: bool) -> None:
        self.data_list.clear()
        self._clear_preview("Select a data entry to preview it.")
        self.data_group.setTitle("Query Results" if query_mode else "Data in Folder")
        self.query_status_label.setVisible(query_mode)
        self.no_results_label.setVisible(query_mode and not entries)
        headers = ["Star", "Log name", "Date created", "Sweep dimension", "Tags"]
        if query_mode:
            headers = ["Star", "Log name", "Source folder", "Date created", "Tags", "Sweep dimension"]
        self.data_list.setColumnCount(len(headers))
        self.data_list.setHeaderLabels(headers)
        if self.scan_result is None:
            return
        for entry in entries:
            starred = self.star_store.is_starred(self._entry_database(entry), entry.relative_path)
            created = entry.creation_time if entry.creation_time is not None else entry.mtime
            date_text = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M") if created else ""
            if query_mode:
                values = [
                    "", entry.log_name,
                    getattr(entry, "source_folder", "") or Path(entry.relative_path).parent.as_posix(), date_text,
                    self._tag_text(entry), entry.sweep_dimension,
                ]
            else:
                values = [
                    "", entry.log_name, date_text,
                    entry.sweep_dimension, self._tag_text(entry),
                ]
            item = QTreeWidgetItem(values)
            item.setToolTip(4, self._tag_text(entry) or "No Tags")
            if query_mode:
                item.setToolTip(2, entry.relative_path)
            item.setData(0, Qt.UserRole, entry)
            self._set_star_cell(item, starred)
            item.setTextAlignment(0, Qt.AlignCenter)
            if entry.status == "error":
                for column in range(len(headers)):
                    item.setForeground(column, QColor(STATUS["unavailable_row"]))
                item.setToolTip(1, entry.error_message or "Could not parse this file")
            self.data_list.addTopLevelItem(item)
        for column in range(len(headers)):
            self.data_list.resizeColumnToContents(column)
        self._sync_star_button()
        self._sync_tag_button()
        self._sync_selected_tags()

    def _selected_data(self) -> tuple[QTreeWidgetItem | None, LogEntry | None]:
        item = self.data_list.currentItem()
        return item, (item.data(0, Qt.UserRole) if item is not None else None)

    def _toggle_measurement_receiver(self, enabled: bool) -> None:
        if not enabled:
            if self._measurement_receiver is not None:
                self._measurement_receiver.stop()
                self._measurement_receiver = None
            self.transfer_port.setEnabled(True)
            self.transfer_status.setText("Not connected")
            return
        receiver = MeasurementReceiver(default_state_path("received"),
                                       lambda path: self._transfer_events.received.emit(str(path)))
        try:
            port = receiver.start(port=self.transfer_port.value())
        except OSError as exc:
            self.transfer_status.setText(f"Receive failed: {exc}")
            self.transfer_listen.blockSignals(True)
            self.transfer_listen.setChecked(False)
            self.transfer_listen.blockSignals(False)
            return
        self._measurement_receiver = receiver
        self.transfer_port.setEnabled(False)
        self.transfer_status.setText(f"Listening on port {port}")

    def _on_measurement_received(self, path: str) -> None:
        self.transfer_status.setText(f"Received: {Path(path).name}")
        self.statusBar().showMessage(f"Measurement stored externally: {path}", 10000)

    def _send_dropped_measurement(self, token: str) -> None:
        if token != self.data_list.active_drag_token:
            return
        _item, entry = self._selected_data()
        if entry is None or entry.status != "ok" or not Path(entry.absolute_path).is_file():
            self.transfer_status.setText("Source measurement unavailable")
            return
        host = self.transfer_host.text().strip()
        if not host or any(char.isspace() for char in host):
            self.transfer_status.setText("Invalid receiver host")
            return
        worker = MeasurementSendWorker(
            entry.absolute_path, host, self.transfer_port.value(),
            Path(self.scanner_root).name if self.scanner_root else None,
            "/".join(entry.folder_parts), self,
        )
        self._send_workers.add(worker)
        self.transfer_progress.setValue(0)
        self.transfer_progress.show()
        worker.stage.connect(self.transfer_status.setText)
        worker.progress.connect(lambda done, total: self._update_transfer_progress(done, total))
        worker.succeeded.connect(lambda: self._finish_measurement_send(True, "Transfer completed"))
        worker.failed.connect(lambda message: self._finish_measurement_send(False, message))
        worker.finished.connect(lambda: self._send_workers.discard(worker))
        worker.start()

    def _update_transfer_progress(self, done: int, total: int) -> None:
        self.transfer_progress.setValue(int(done * 100 / total))
        self.transfer_status.setText(f"Transferring {done:,} / {total:,} bytes")

    def _finish_measurement_send(self, success: bool, message: str) -> None:
        self.transfer_progress.hide()
        self.transfer_status.setText(message if success else f"Transfer failed: {message}")
        self.statusBar().showMessage(self.transfer_status.text(), 10000)

    def _on_data_selection_changed(self) -> None:
        self._sync_star_button()
        self._sync_tag_button()
        _, entry = self._selected_data()
        self._sync_selected_tags()
        self._load_browser_comment(entry)
        if entry is None:
            self._clear_preview("Select a data entry to preview it.")
        elif entry.status != "ok":
            self._clear_preview(entry.error_message or "Preview unavailable.")
        else:
            self._start_preview(entry)
        self._schedule_session_save()

    def _start_preview(self, entry: LogEntry) -> None:
        self._clear_preview(f"Loading {entry.log_name}...")
        worker = QuickPreviewWorker(
            entry.absolute_path,
            self.viewer_display_state_store.get(entry.absolute_path), parent=self,
        )
        self._preview_worker = worker
        self._preview_workers.add(worker)
        worker.preview_ready.connect(self._on_preview_ready)
        worker.preview_failed.connect(self._on_preview_failed)
        worker.finished.connect(self._on_preview_worker_finished)
        worker.start()

    def _on_preview_worker_finished(self) -> None:
        worker = self.sender()
        if isinstance(worker, QuickPreviewWorker):
            self._preview_workers.discard(worker)

    def _on_preview_ready(self, worker: QuickPreviewWorker, preview) -> None:
        if worker is not self._preview_worker:
            return
        self._preview_data = preview
        status = f"{preview.log_name}  ·  {preview.channel_name}"
        if preview.acquisition is not None and preview.acquisition.is_partial:
            status += (
                f"  ·  Partial acquisition: {preview.acquisition.acquired_entries} / "
                f"{preview.acquisition.nominal_entries} sweeps"
            )
        if preview.restored:
            status += "  ·  Restored last Viewer display"
        self.preview_status.setText(status)
        if preview.kind == "1d":
            trace = preview.data
            self.preview_1d.plot(
                trace.x_values, trace.y_values,
                x_label=f"{trace.x_name} [{trace.x_unit or '-'}]",
                y_label=f"{trace.z_name} [{trace.transform.replace('_', ' ')}]",
                title=preview.log_name, name=trace.z_name,
            )
            self.preview_stack.setCurrentWidget(self.preview_1d)
        else:
            self.preview_2d.plot(
                preview.data, colormap=preview.colormap,
                z_min=preview.z_min, z_max=preview.z_max,
            )
            self.preview_stack.setCurrentWidget(self.preview_2d)

    def _on_preview_failed(self, worker: QuickPreviewWorker, message: str) -> None:
        if worker is self._preview_worker:
            self._clear_preview(message)

    def _clear_preview(self, message: str) -> None:
        self._preview_data = None
        self.preview_status.setText(message)
        self.preview_stack.setCurrentWidget(self.preview_empty)

    def _on_data_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        entry = item.data(0, Qt.UserRole)
        if entry is not None:
            self._toggle_star(item, entry)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        entry = item.data(0, Qt.UserRole)
        if entry is not None:
            self._open_viewer_for(entry)

    def _open_debackground(self) -> None:
        from app.gui.debackground_dialog import DeBackgroundDialog

        if self._debackground_dialog is not None:
            try:
                if self._debackground_dialog.isVisible():
                    self._debackground_dialog.showNormal()
                    self._debackground_dialog.raise_()
                    self._debackground_dialog.activateWindow()
                    return
            except RuntimeError:
                self._debackground_dialog = None
        _item, selected = self._selected_data()
        target = selected if selected is not None and selected.status == "ok" else None
        dialog = DeBackgroundDialog(self, target)
        dialog.operation_journal = self._record_operation
        self._debackground_dialog = dialog
        dialog.show()

    def _on_debackground_completed(self, output_path: str) -> None:
        if not self.scanner_root:
            return
        try:
            Path(output_path).resolve().relative_to(Path(self.scanner_root).resolve())
        except ValueError:
            return
        self.statusBar().showMessage(f"De-background created: {Path(output_path).name}; refreshing Database…", 6000)
        if self._scan_worker is not None and self._scan_worker.isRunning():
            self._debackground_refresh_pending = True
        else:
            self.reload_database()

    def open_generated_output(self, output_path: str) -> None:
        output = Path(output_path).expanduser().resolve()
        if not output.is_file():
            QMessageBox.warning(self, "Output unavailable", f"The generated file is no longer available:\n\n{output}")
            return
        if self.scan_result is not None:
            entry = next(
                (value for value in self.scan_result.entries
                 if Path(value.absolute_path).resolve() == output and value.status == "ok"),
                None,
            )
            if entry is not None:
                self._open_viewer_for(entry)
                return
        if self.scanner_root is not None:
            try:
                output.relative_to(Path(self.scanner_root).resolve())
            except ValueError:
                pass
            else:
                self._pending_generated_open = str(output)
                if self._scan_worker is None or not self._scan_worker.isRunning():
                    self.reload_database()
                self.statusBar().showMessage("Waiting for Database refresh before opening generated Data…", 5000)
                return
        entry = DatabaseScanner._scan_one(output.parent, output)
        self._open_viewer_for(entry)

    def _open_pending_generated_if_ready(self, result: DatabaseScanResult) -> None:
        pending = self._pending_generated_open
        if pending is None:
            return
        output = Path(pending).resolve()
        entry = next(
            (value for value in result.entries
             if Path(value.absolute_path).resolve() == output and value.status == "ok"),
            None,
        )
        self._pending_generated_open = None
        if entry is not None:
            self._open_viewer_for(entry)
        else:
            QMessageBox.warning(
                self, "Generated Data not indexed",
                f"The Database refresh did not find the generated log:\n\n{output}",
            )

    # -- a Viewer built ahead of time -------------------------------------------------------------
    # Building a Viewer (controls, plots, 3D page ...) takes about a second. One is built
    # while the program is idle and handed out on the next open, so opening feels instant;
    # the next one is built again afterwards. LABLOGVIEWER_NO_PREWARM=1 turns this off.
    PREWARM_DELAY_MS = 1500

    def _viewer_arguments(self) -> dict:
        return dict(transform_store=self.transform_store, axis_preset_store=self.axis_preset_store,
                    overlay_store=self.overlay_store, mark_store=self.mark_store,
                    viewer_display_state_store=self.viewer_display_state_store,
                    comment_store=self.comment_store, named_view_store=self.named_view_store,
                    theme_manager=self.theme_manager)

    @staticmethod
    def prewarm_enabled() -> bool:
        import os

        if os.environ.get("LABLOGVIEWER_NO_PREWARM", "") == "1":
            return False
        return "PYTEST_CURRENT_TEST" not in os.environ or os.environ.get("LABLOGVIEWER_PREWARM") == "1"

    def _schedule_prewarm(self) -> None:
        if self.prewarm_enabled():
            QTimer.singleShot(self.PREWARM_DELAY_MS, self._prewarm_viewer)

    def _prewarm_viewer(self) -> None:
        from shiboken6 import isValid

        if not self.isVisible() or (self._spare_viewer is not None and isValid(self._spare_viewer)):
            return
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            QTimer.singleShot(500, self._prewarm_viewer)         # the user is busy: a bit later
            return
        self._spare_viewer = MainWindow(**self._viewer_arguments())

    def _take_viewer(self, comment_context) -> MainWindow:
        from shiboken6 import isValid

        viewer, self._spare_viewer = self._spare_viewer, None
        if viewer is None or not isValid(viewer):
            viewer = MainWindow(**self._viewer_arguments(), comment_context=comment_context)
        else:
            viewer._comment_context = comment_context
        self._schedule_prewarm()
        return viewer

    def _open_viewer_for(self, entry: LogEntry, session_state: dict | None = None) -> MainWindow | None:
        if entry.status == "error":
            QMessageBox.warning(self, "Cannot open log", f"This file could not be parsed:\n\n{entry.error_message}")
            return None
        started = perf_counter()
        viewer = self._take_viewer(self._comment_context_for_entry(entry))
        constructed = perf_counter()
        viewer.setWindowTitle(f"LabLogViewer v{__version__} — {entry.log_name}")
        viewer.operation_journal = self._record_operation
        viewer.show()
        viewer.open_file(entry.absolute_path)
        loaded = perf_counter()
        if session_state is not None:
            viewer.restore_session_state(session_state)
        restored = perf_counter()
        # T_open stops only after Qt has processed the first real plot paint.
        # Kept as diagnostic state, not a user-facing status-message stream.
        QApplication.processEvents()
        painted = perf_counter()
        self.last_viewer_open_timing = {
            "viewer_construction_ms": (constructed - started) * 1000.0,
            "data_model_and_initial_plot_ms": (loaded - constructed) * 1000.0,
            "session_restore_ms": (restored - loaded) * 1000.0,
            "first_usable_frame_ms": (painted - restored) * 1000.0,
            "total_open_ms": (painted - started) * 1000.0,
        }
        logger.debug("Viewer open timing for %s: %s", entry.absolute_path, self.last_viewer_open_timing)
        self._viewers.append(viewer)
        viewer.workspace_state_changed.connect(self._schedule_session_save)
        viewer.destroyed.connect(lambda *_args: self._schedule_session_save())
        self._schedule_session_save()
        return viewer

    def _comment_context_for_entry(self, entry: LogEntry) -> tuple[str, str] | None:
        if self.scan_result is None:
            return None
        try:
            relative = Path(entry.absolute_path).resolve().relative_to(
                Path(self.scan_result.root_path).resolve()
            ).as_posix()
        except ValueError:
            return None
        return self.scan_result.database_id, relative

    def _on_context_menu(self, pos) -> None:
        item = self.data_list.itemAt(pos)
        if item is None:
            return
        self.data_list.setCurrentItem(item)
        entry = item.data(0, Qt.UserRole)
        if entry is None or self.scan_result is None:
            return
        menu = QMenu(self)
        starred = self.star_store.is_starred(self._entry_database(entry), entry.relative_path)
        action = menu.addAction("Unstar" if starred else "Star")
        action.triggered.connect(lambda: self._toggle_star(item, entry))
        tag_action = menu.addAction("Edit Tags...")
        tag_action.triggered.connect(lambda: self._edit_tags(item, entry))
        menu.addSeparator()
        rename_action = menu.addAction("Rename File...")
        rename_action.triggered.connect(lambda: self._rename_entry(entry))
        if entry.status == "ok":
            open_action = menu.addAction("Open")
            open_action.triggered.connect(lambda: self._open_viewer_for(entry))
        menu.exec(self.data_list.viewport().mapToGlobal(pos))

    def _rename_entry(self, entry: LogEntry, new_stem: str | None = None) -> bool:
        if self.scan_result is None or self.scanner_root is None:
            return False
        if new_stem is None:
            new_stem, accepted = QInputDialog.getText(
                self, "Rename Data File", "New filename (extension is kept):",
                text=Path(entry.absolute_path).stem,
            )
            if not accepted:
                return False
            source = Path(entry.absolute_path)
            stem = str(new_stem).strip()
            if stem.lower().endswith(source.suffix.lower()):
                stem = stem[:-len(source.suffix)].rstrip()
            wanted = source.with_name(f"{stem}{source.suffix}")
            if stem and stem != source.stem and wanted.exists():
                from app.gui.save_target import ask_keep_both

                other = ask_keep_both(self, wanted)
                if other is None:
                    return False
                new_stem = other.stem
        source_identity = stable_data_identity(entry.absolute_path)
        if any(
            viewer.isVisible() and viewer.experiment is not None
            and viewer.experiment.data_identity == source_identity
            for viewer in self._viewers
        ):
            QMessageBox.information(
                self, "Close Viewer First",
                "Close this Data's Viewer before renaming it so its file handle and saved state stay in sync.",
            )
            return False
        if any(worker is not None and worker.isRunning()
               for worker in (self._scan_worker, self._enrichment_worker)):
            QMessageBox.information(
                self, "Database Busy", "Wait for Database loading to finish before renaming a file."
            )
            return False
        if any(
            getattr(worker, "isRunning", lambda: False)()
            and stable_data_identity(worker.path) == source_identity
            for worker in tuple(self._preview_workers)
        ):
            QMessageBox.information(
                self, "Preview Busy", "Wait for this Data's Quick Preview to finish, then try again."
            )
            return False
        if self._debackground_dialog is not None and self._debackground_dialog.is_processing:
            QMessageBox.information(
                self, "Processing Busy", "Wait for De-background processing to finish before renaming files."
            )
            return False

        self._flush_browser_comment()
        for viewer in tuple(self._viewers):
            if viewer.isVisible():
                viewer._flush_display_state()
                viewer._persist_all_marks()
        stores = (
            self.star_store, self.tag_store, self.comment_store,
            self.viewer_display_state_store, self.axis_preset_store,
            self.overlay_store, self.mark_store, self.named_view_store,
            self.session_store, self.database_index_store,
        )
        try:
            destination = rename_hdf5_data(
                entry.absolute_path,
                new_stem,
                database_id=self._entry_database(entry),
                old_relative_path=entry.relative_path,
                state_stores=stores,
            )
        except (DataRenameError, OSError, RuntimeError, ValueError) as error:
            QMessageBox.warning(self, "Rename Failed", str(error))
            return False
        if destination == Path(entry.absolute_path):
            return False
        self._pending_selection_relative_path = destination.relative_to(
            Path(self.scan_result.root_path)
        ).as_posix()
        self._comment_source = None
        self._comment_dirty = False
        self.reload_database()
        self.statusBar().showMessage(f"Renamed Data file to {destination.name}")
        return True

    def _open_figure_builder(self) -> None:
        """Open the Scientific Figure Builder in its own process (a freeze there does not stop this window)."""
        from app.figure_builder.process import FigureBuilderLauncher

        if getattr(self, "_figure_launcher", None) is None:
            self._figure_launcher = FigureBuilderLauncher(self)
            self._figure_launcher.crashed.connect(self._figure_builder_crashed)
        self._figure_launcher.open()

    def _figure_builder_crashed(self, reason: str) -> None:
        QMessageBox.warning(self, self.localizer.text("fig.title"),
                            self.localizer.text("fig.crashed").format(reason=reason))

    def _sync_star_button(self) -> None:
        _, entry = self._selected_data()
        self.star_button.setEnabled(entry is not None and self.scan_result is not None)
        if entry is None or self.scan_result is None:
            self._set_star_button_state(False)
            return
        starred = self.star_store.is_starred(self._entry_database(entry), entry.relative_path)
        self._set_star_button_state(starred)

    def _set_star_button_state(self, starred: bool) -> None:
        text = self.localizer.text("browser.unstar" if starred else "browser.star")
        make_icon_only(self.star_button, "star_filled" if starred else "star", text, TOOLBAR_ICON_SIZE)

    def _set_star_cell(self, item: QTreeWidgetItem, starred: bool) -> None:
        item.setData(0, STAR_STATE_ROLE, bool(starred))
        item.setIcon(0, icon("star_filled" if starred else "star"))
        item.setToolTip(0, self.localizer.text(
            "browser.star_cell_starred" if starred else "browser.star_cell_unstarred"
        ))

    def _toggle_selected_star(self) -> None:
        item, entry = self._selected_data()
        if item is not None and entry is not None:
            self._toggle_star(item, entry)

    def _tag_text(self, entry: LogEntry) -> str:
        if self.scan_result is None:
            return ""
        assigned = self.tag_store.tags_for(self._entry_database(entry), entry.relative_path)
        grouped = self.tag_store.tags_by_category()
        return "; ".join(
            f"{self.localizer.text('tag.category.' + category.lower().replace(' ', '_'))}: "
            f"{', '.join(name for name in grouped[category] if name in assigned)}"
            for category in self.tag_store.categories()
            if any(name in assigned for name in grouped[category])
        )

    def _sync_selected_tags(self) -> None:
        self.tags_tree.clear()
        _, entry = self._selected_data()
        if entry is None or self.scan_result is None:
            self.selected_log_label.setText("No Data selected")
            self.selected_tags_label.setText("Tags: none")
            self.tags_tree.addTopLevelItem(QTreeWidgetItem(["No tags"]))
            self.edit_tags_button.setEnabled(False)
            return
        self.selected_log_label.setText(entry.log_name)
        assigned = self.tag_store.tags_for(self._entry_database(entry), entry.relative_path)
        grouped = self.tag_store.tags_by_category()
        ordered = [name for name in self.tag_store.list_tags() if name in assigned]
        if not ordered:
            self.tags_tree.addTopLevelItem(QTreeWidgetItem(["No tags"]))
        for category in self.tag_store.categories():
            category_tags = [name for name in grouped[category] if name in assigned]
            if not category_tags:
                continue
            category_key = "tag.category." + category.lower().replace(" ", "_")
            category_label = self.localizer.text(category_key)
            category_item = QTreeWidgetItem([category_label])
            category_item.setExpanded(True)
            self.tags_tree.addTopLevelItem(category_item)
            for name in category_tags:
                item = QTreeWidgetItem([name])
                item.setToolTip(0, f'{category} Tag assigned to "{entry.log_name}"')
                category_item.addChild(item)
        summary = self._tag_text(entry)
        self.selected_tags_label.setText(f"Tags: {summary if summary else 'none'}")
        self.edit_tags_button.setEnabled(True)

    def _sync_tag_button(self) -> None:
        _, entry = self._selected_data()
        self.tag_button.setEnabled(entry is not None and self.scan_result is not None)
        self.edit_tags_button.setEnabled(entry is not None and self.scan_result is not None)

    def _edit_selected_tags(self) -> None:
        item, entry = self._selected_data()
        if item is not None and entry is not None:
            self._edit_tags(item, entry)

    def _edit_tags(self, item: QTreeWidgetItem, entry: LogEntry) -> None:
        if self.scan_result is None:
            return
        database_id = self._entry_database(entry)
        assigned = self.tag_store.tags_for(database_id, entry.relative_path)
        dialog = TagAssignmentDialog(entry.log_name, self.tag_store, assigned, self)
        accepted = dialog.exec() == TagAssignmentDialog.Accepted
        if accepted:
            for name in dialog.pending_tags:
                categories = getattr(dialog, "pending_tag_categories", {})
                self.tag_store.create_tag(name, categories.get(name, "Other"))
            self.tag_store.set_tags(
                database_id, entry.relative_path, dialog.selected_tags(),
                group_id=session_group_id(entry.relative_path),
                flux_default=is_flux_named(entry.relative_path, entry.log_name),
            )
        self._refresh_tag_views()

    def _open_tag_manager(self) -> None:
        manager = TagManagementDialog(self.tag_store, self)
        manager.exec()
        if self._active_query is not None:
            tags, mode = self._active_query
            renamed = {manager.renamed_tags.get(name, name) for name in tags}
            if manager.deleted_tags & set(tags):
                self._clear_query()
                return
            self._active_query = (frozenset(renamed), mode)
        self._refresh_tag_views()

    def _master_root(self) -> str:
        from app.settings.store import SettingsStore

        store = getattr(self.localizer, "store", None) or SettingsStore()
        root = store.tag_search_root()
        return root if root and Path(root).is_dir() else ""

    def _open_tag_query(self) -> None:
        if self.scan_result is None:
            return
        dialog = TagQueryDialog(self.tag_store, self, master_root=self._master_root())
        if self._active_query is not None:
            dialog.set_query(set(self._active_query[0]), self._active_query[1])
            dialog.set_scope(self._active_query[2] if len(self._active_query) > 2 else "database")
        if dialog.exec() == TagQueryDialog.Accepted:
            self._run_tag_query(dialog.selected_tags(), dialog.query_mode(), scope=dialog.scope())

    def _entry_database(self, entry) -> str | None:
        """The database an entry belongs to (results from the largest data folder keep their own)."""
        own = getattr(entry, "database_id", "")
        if own:
            return own
        return self.scan_result.database_id if self.scan_result is not None else None

    def _run_tag_query(self, tags: set[str], mode: str, *, record: bool = True, scope: str = "database") -> None:
        if self.scan_result is None or not tags:
            return
        available = set(self.tag_store.list_tags())
        if not tags <= available:
            self._clear_query()
            return
        mode = mode.upper()
        master_root = self._master_root() if scope == "master" else ""
        if master_root:
            from app.core.master_search import search

            matches = search(self.tag_store, master_root, tags, mode, self.database_index_store)
        else:
            scope = "database"
            matches = query_entries(
                self.scan_result.entries, tags, mode,
                lambda entry: self.tag_store.tags_for(self.scan_result.database_id, entry.relative_path),
            )
            matches.sort(key=lambda entry: (
                tuple(part.lower() for part in entry.folder_parts), entry.log_name.lower(),
                entry.relative_path.lower(),
            ))
        self._active_query = (frozenset(tags), mode, scope)
        if record:
            self.tag_store.record_query(tags, mode)
        ordered_tags = [name for name in self.tag_store.list_tags() if name in tags]
        count = len(matches)
        self.query_status_label.setText(
            f"{count} result{'s' if count != 1 else ''} · {mode}: " + f" {mode} ".join(ordered_tags)
        )
        self.clear_query_button.setEnabled(True)
        self._display_entries(matches, query_mode=True)

    def _clear_query(self) -> None:
        self._active_query = None
        self.clear_query_button.setEnabled(False)
        item = self.folder_tree.currentItem()
        folder_parts = item.data(0, Qt.UserRole) if item is not None else ()
        self._populate_data_list(folder_parts or ())

    def _refresh_tag_views(self) -> None:
        if self._active_query is not None:
            tags, mode, scope = self._active_query
            self._run_tag_query(set(tags), mode, record=False, scope=scope)
            return
        for row in range(self.data_list.topLevelItemCount()):
            row_item = self.data_list.topLevelItem(row)
            row_entry = row_item.data(0, Qt.UserRole)
            if row_entry is not None:
                text = self._tag_text(row_entry)
                row_item.setText(4, text)
                row_item.setToolTip(4, text or "No Tags")
        self.data_list.resizeColumnToContents(4)
        self._sync_selected_tags()

    def _on_tag_tree_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if self._selected_data()[1] is not None:
            self._edit_selected_tags()

    def _toggle_star(self, item: QTreeWidgetItem, entry: LogEntry) -> None:
        if self.scan_result is None:
            return
        new_state = self.star_store.toggle(self._entry_database(entry), entry.relative_path)
        self._set_star_cell(item, new_state)
        self._sync_star_button()

    @staticmethod
    def _log_label(entry: LogEntry, starred: bool) -> str:
        return f"{'★' if starred else '☆'} {entry.log_name}"

    # ---- v0.13C application session --------------------------------------

    def _schedule_session_save(self, *_args) -> None:
        if not self._restoring_session:
            try:
                self._session_timer.start()
            except RuntimeError:
                # A child Viewer can be deleted after Browser teardown starts.
                pass

    @staticmethod
    def _geometry_state(window: QMainWindow) -> dict[str, object]:
        return {
            "geometry": bytes(window.saveGeometry()).hex(),
            "maximized": bool(window.isMaximized()),
        }

    def _restore_geometry(self, state: object) -> None:
        if not isinstance(state, dict):
            return
        encoded = state.get("geometry")
        if isinstance(encoded, str):
            try:
                from PySide6.QtCore import QByteArray
                self.restoreGeometry(QByteArray(bytes.fromhex(encoded)))
            except (TypeError, ValueError):
                pass
        frame = self.frameGeometry()
        screens = QGuiApplication.screens()
        if screens and not any(screen.availableGeometry().intersects(frame) for screen in screens):
            available = QGuiApplication.primaryScreen().availableGeometry()
            self.move(available.center() - self.rect().center())
        if state.get("maximized") is True:
            self.showMaximized()

    def session_state(self) -> dict:
        """Capture browser and visible Viewer ownership, never domain-store copies."""
        selected = self._selected_data()[1]
        folder = self.folder_tree.currentItem()
        query = None
        if self._active_query is not None:
            query = {"tags": sorted(self._active_query[0]), "mode": self._active_query[1],
                     "scope": self._active_query[2]}
        viewers = []
        for viewer in list(self._viewers):
            try:
                state = viewer.session_state() if viewer.isVisible() else None
            except RuntimeError:
                state = None
            if state is not None:
                viewers.append(state)
        return {
            "database_path": self.scanner_root,
            "browser": {
                "selected_relative_path": selected.relative_path if selected else None,
                "folder": list(folder.data(0, Qt.UserRole) or ()) if folder is not None else [],
                "query": query,
                "splitter": self.browser_splitter.sizes(),
                "comment_panel": {
                    "expanded": self._comment_expanded,
                    "ratio": self._comment_ratio,
                },
                "window": self._geometry_state(self),
            },
            "viewers": viewers,
        }

    def _flush_session(self) -> None:
        if self._session_timer.isActive():
            self._session_timer.stop()
        if self._restoring_session:
            return
        # Opening and immediately closing a blank Browser must not erase a
        # recoverable workspace from an earlier database session.
        if self.scanner_root is None and self.scan_result is None:
            return
        self.session_store.set(self.session_state())

    def restore_previous_session(self) -> bool:
        """Begin a guarded asynchronous restore of the last valid database."""
        state = self.session_store.get()
        path = state.get("database_path") if isinstance(state, dict) else None
        if (not isinstance(path, str) or not Path(path).is_dir()) and self._safe_recovery:
            path = self.startup_recovery.database_path
        if not isinstance(path, str) or not Path(path).is_dir():
            if isinstance(path, str) and path:
                self.statusBar().showMessage("Previous database is unavailable. Open a folder to continue.")
            self._show_recovery_report()
            return False
        if self._safe_recovery and self.session_store.requires_safe_recovery:
            state = {"database_path": path, "browser": {}}
        self._restoring_session = True
        self._pending_session = state
        self._restore_geometry(state.get("browser", {}).get("window"))
        self.open_database(path)
        return True

    def _restore_after_scan(self, state: dict) -> None:
        """Restore valid pieces only after the database scan has populated entries."""
        self._restoring_session = True
        try:
            browser = state.get("browser", {})
            if not isinstance(browser, dict) or self.scan_result is None:
                return
            splitter = browser.get("splitter")
            if isinstance(splitter, list) and len(splitter) == self.browser_splitter.count() and sum(splitter) > 0:
                self.browser_splitter.setSizes(splitter)
                self._align_toolbar_segments()
            self._restore_comment_panel_state(browser.get("comment_panel"))
            selected_path = browser.get("selected_relative_path")
            selected = next(
                (entry for entry in self.scan_result.entries if entry.relative_path == selected_path), None
            )
            if selected is not None:
                folder_item = self._folder_items.get(selected.folder_parts)
                if folder_item is not None:
                    self.folder_tree.setCurrentItem(folder_item)
                self._populate_data_list(selected.folder_parts)
            query = browser.get("query")
            if isinstance(query, dict) and isinstance(query.get("tags"), list):
                tags = {tag for tag in query["tags"] if isinstance(tag, str)}
                mode = query.get("mode")
                if tags and isinstance(mode, str):
                    scope = query.get("scope") if query.get("scope") in ("database", "master") else "database"
                    self._run_tag_query(tags, mode, record=False, scope=scope)
            if selected is not None:
                for row in range(self.data_list.topLevelItemCount()):
                    item = self.data_list.topLevelItem(row)
                    entry = item.data(0, Qt.UserRole)
                    if entry is not None and entry.relative_path == selected.relative_path:
                        self.data_list.setCurrentItem(item)
                        break
            if not self._safe_recovery:
                for viewer_state in state.get("viewers", []):
                    if not isinstance(viewer_state, dict):
                        continue
                    source_path = viewer_state.get("source_path")
                    entry = next(
                        (candidate for candidate in self.scan_result.entries
                         if stable_data_identity(candidate.absolute_path) == source_path
                         and candidate.status == "ok"), None
                    )
                    if entry is not None and Path(entry.absolute_path).is_file():
                        self._open_viewer_for(entry, viewer_state)
        finally:
            self._restoring_session = False
        if self._safe_recovery:
            self.statusBar().showMessage(
                "Safe Recovery Mode: Database and Browser context restored; previous Viewers were skipped."
            )
            self._show_recovery_report()
        else:
            self.statusBar().showMessage("Restored previous workspace.")
        self._schedule_session_save()

    def closeEvent(self, event) -> None:
        if self._spare_viewer is not None:
            from shiboken6 import isValid

            if isValid(self._spare_viewer):
                self._spare_viewer.deleteLater()
            self._spare_viewer = None
        active_send = next((worker for worker in self._send_workers if worker.isRunning()), None)
        if active_send is not None:
            event.ignore()
            self.transfer_status.setText("Finishing measurement transfer before closing...")
            active_send.finished.connect(self.close, Qt.SingleShotConnection)
            return
        if self._measurement_receiver is not None:
            self._measurement_receiver.stop()
            self._measurement_receiver = None
        for worker in list(self._send_workers):
            if worker.isRunning():
                worker.wait(30000)
        dialog = self._debackground_dialog
        if dialog is not None and dialog.is_processing:
            event.ignore()
            dialog.processing_stopped.connect(self.close, Qt.SingleShotConnection)
            dialog.cancel_for_shutdown()
            return
        self._flush_browser_comment()
        if self._enrichment_worker is not None and self._enrichment_worker.isRunning():
            self._enrichment_worker.wait(30000)
        for viewer in list(self._viewers):
            try:
                viewer.metadata_dialog.flush_comment()
                viewer._flush_display_state()
            except RuntimeError:
                continue
        self._flush_session()
        for worker in list(self._preview_workers):
            if worker.isRunning():
                worker.wait(30000)
            worker.preview_ready.disconnect(self._on_preview_ready)
            worker.preview_failed.disconnect(self._on_preview_failed)
        super().closeEvent(event)
