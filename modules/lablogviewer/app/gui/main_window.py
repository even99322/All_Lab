"""
app/gui/main_window.py — Phase 5A / Phase 6

Main application window. This module (and everything under app/gui/)
must NEVER import h5py. Data access goes exclusively through:

    app.core.labber_parser.load_experiment()   (returns an Experiment;
                                                  internally uses HDF5Reader,
                                                  which the GUI never touches)
    app.core.cache.CachedExperiment             (wraps Experiment.get_data()
                                                  and Experiment.get_2d_data()
                                                  with an LRU cache)
    app.core.channel_manager.ChannelManager     (channel listing / metadata)

Phase 5A scope: File > Open, Channel Browser, Experiment Summary, 1D
plot with X/Y axis selection and complex-data transform.

Phase 6 scope (this update): a Plot Mode selector (1D / 2D); in 2D
mode, X/Y/Z axis selection (X/Y candidates derived structurally from
ChannelManager.get_2d_axis_candidates(), never hardcoded to a channel
name), a full heatmap via Plot2DWidget, colormap selection, and
auto/manual color range. 1D mode is unchanged and still works exactly
as in Phase 5A.

Explicitly NOT in this phase: N-dimensional slice explorer, line
cuts, folder database, tags/favorites, Raw HDF5 Explorer Mode — all
deferred to later phases per the project plan.
"""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
import os
import sys
import weakref

import numpy as np

from PySide6.QtCore import (
    QByteArray, QEvent, QPoint, QRect, QSize, QSignalBlocker, QStandardPaths,
    QTimer, Qt, Signal,
)
from PySide6.QtGui import QAction, QGuiApplication, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStatusBar,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.palette import STATUS
from app.core.cache import CachedExperiment, LRUDataCache
from app.gui.glass import GlassToolBar
from app.core.axis_preset_store import AxisPreset, AxisPresetStore, AxisRef
from app.core.channel_manager import AxisCandidate, ChannelManager
from app.core.data_model import ChannelNotFound, Data2DError, Experiment, Grid2DData, SliceError
from app.core.data_table import DEFAULT_MAX_ROWS, build_1d_table
from app.core.hdf5_reader import HDF5ReadError
from app.core.labber_parser import UnsupportedLabberFormat, load_experiment
from app.core.mark_model import (
    CROSSHAIR,
    HORIZONTAL_LINE,
    MARK_TOOLS,
    MAX_MARKS,
    POINT_MARK,
    RANGE,
    TOOL_LABELS,
    VERTICAL_LINE,
    HalfPeakResult,
    MarkManager,
)
from app.core.mark_store import MarkStore
from app.core.overlay_store import OverlayStore, SavedOverlay
from app.core.local_analysis import (
    AROUND_MARK, AUTO_NEARBY, BETWEEN_MARKS, SELECTED_RANGE, VISIBLE_RANGE,
    AnalysisError, HALF_PEAK, LocalAnalyzer, OPERATIONS, PEAK, REGION_MODES, TROUGH,
)
from app.core.transform_store import TransformSpec, TransformStore
from app.core.viewer_display_state_store import ViewerDisplayStateStore
from app.core.comment_store import CommentStore
from app.core.named_view_store import NamedViewStore
from app.core.formula import FormulaError, apply_formulas, formula_terms, formula_to_display, parse_formula
from app.core.formula import _latex_bare as _latex_bare_root
from app.core.data_export import (
    ACTIVE_TRACE,
    CURRENT_TRANSFORM,
    DISPLAYED,
    FULL_DATA,
    FULL_RANGE,
    RAW,
    SELECTED_TRACES,
    VISIBLE_TRACES,
    VISIBLE_X_RANGE,
    ExportColumn,
    ExportDataset,
    complex_columns,
    long_grid_dataset,
    nd_dataset,
    safe_filename,
    write_csv,
    write_npz,
)
from app.core.animation_export import (
    ALL_TRACES, GIF, MP4, AnimationExportError, AnimationOptions,
    encoder_available, finite_global_range, gif_duration_seconds, GIF_FRAME_DURATION_SECONDS,
    gif_frame_ids, scope_trace_ids, target_frame_size,
)
from app.core.trace_overlay import COLOR_MODES, SEQUENTIAL
from app.plotting.complex_transform import VALID_TRANSFORMS, unwrap_phase

from app.gui.channel_browser import ChannelBrowser
from app.gui.plot_widget import Plot1DWidget
from app.gui.plot_2d_widget import Plot2DWidget, COLORMAPS, DEFAULT_COLORMAP
from app.gui.slice_widget import SliceExplorerWidget
from app.gui.linecut_widget import CutWindow
from app.gui.metadata_dialog import MetadataDialog
from app.gui.log_entries_widget import LogEntriesWidget
from app.gui.mark_overlay import MarkOverlay
from app.gui.mark_tool_icons import mark_tool_icon
from app.gui.plot_export import PaneRenderSurface, render_composite_image, write_png, write_raster_svg, write_svg
from app.gui.drag_share import DragShareController, DragShareManager
from app.gui.plot_interaction import PlotInteractionControls
from app.icons import icon, make_icon_only
from app.localization import get_localization_manager
from app.theme import TOOLBAR_ICON_SIZE, WHITE_PLOT, ThemeManager, get_theme_manager
from app.gui.data_export_dialog import DataExportDialog, ExportOptions
from app.gui.animation_export_dialog import AnimationExportDialog
from app.gui.animation_renderer import AnimationEncodeWorker, AnimationPane, AnimationRenderSession
from app.visualization3d.mapping import (
    GeometryType, TRANSFORM_LABELS_3D, mapping_values, prepare_point_cloud,
    prepare_trajectory_trace,
    prepare_waterfall_grid, transform_grid_3d,
)
from app.gui.multi_pane import (
    FOUR_PANES, ONE_PANE, PANE_LAYOUTS, THREE_PANES, TWO_SIDE, TWO_STACKED,
    PaneFrame, PaneState,
)
from app import __version__

TRANSFORM_LABELS = {
    "raw": "Raw (complex)",
    "real": "Real",
    "imag": "Imag",
    "magnitude": "Magnitude",
    "magnitude_db": "Magnitude (dB)",
    "phase_deg": "Phase (deg)",
    "phase_rad": "Phase (rad)",
}


class MainWindow(QMainWindow):
    workspace_state_changed = Signal()

    def __init__(self, transform_store: TransformStore | None = None,
                 axis_preset_store: AxisPresetStore | None = None,
                 overlay_store: OverlayStore | None = None,
                 mark_store: MarkStore | None = None,
                 viewer_display_state_store: ViewerDisplayStateStore | None = None,
                 comment_store: CommentStore | None = None,
                 named_view_store: NamedViewStore | None = None,
                 comment_context: tuple[str, str] | None = None,
                 theme_manager: ThemeManager | None = None):
        super().__init__()
        self.setWindowTitle(f"LabLogViewer v{__version__}")
        self.resize(1200, 780)

        self.experiment: Experiment | None = None
        self.cached: CachedExperiment | None = None
        self.mgr: ChannelManager | None = None
        self.transform_store = transform_store or TransformStore()
        self.axis_preset_store = axis_preset_store or AxisPresetStore()
        self.overlay_store = overlay_store or OverlayStore()
        self.mark_store = mark_store or MarkStore()
        self.viewer_display_state_store = viewer_display_state_store or ViewerDisplayStateStore()
        self.comment_store = comment_store or CommentStore()
        self.named_view_store = named_view_store or NamedViewStore()
        self._comment_context = comment_context
        self._restoring_session = False
        self._loading_file = False
        self._display_state_timer = QTimer(self)
        self._display_state_timer.setSingleShot(True)
        self._display_state_timer.setInterval(500)
        self._display_state_timer.timeout.connect(self._flush_display_state)
        # (same JSON file) but each Viewer's transform_combo only
        # reflects it at populate time — a Save in one Viewer becomes
        # visible in another after that Viewer next opens/reloads a file.
        self._axis_candidates: list[AxisCandidate] = []
        self._mark_managers = {0: MarkManager(), 1: MarkManager()}
        self._mark_overlays: dict[int, MarkOverlay] = {}
        self._range_starts: dict[int, float] = {}
        self._analysis_target_id: str | None = None
        self._analysis_preview_signature: tuple | None = None
        self._overlay_plot_signature: tuple | None = None
        self._preserve_overlay_view_once = False
        self._normal_transform_state: dict | None = None
        self._updating_multi_trace_panel = False
        self._pending_mark_tool: str | None = None
        self._pane_states: dict[int, PaneState] = {}
        self._pane_frames: dict[int, PaneFrame] = {}
        self._active_pane_id = 1
        self._multi_pane_loading = False
        self._syncing_pane_view = False
        self._multi_pane_maximized = False
        self._single_mark_managers = None
        self._single_mark_overlays = None
        self._trace_manager_requested = False
        self._pane_layout_root: QSplitter | None = None
        self._pane_splitters: list[QSplitter] = []
        self._cut_windows: dict[str, CutWindow] = {}
        self._node_antinode_window = None
        self._yig_fitting_windows: dict[str, QWidget] = {}
        self._three_d_window = None
        # Pointer / Drag to Share for the 3D window (its own toolbar toggle).
        self._three_d_share_mode = False
        self._context_export_pane_id: int | None = None
        self._plot_shortcuts: list[QShortcut] = []
        self._drag_share_controllers: list[DragShareController] = []
        self._drag_share_manager = DragShareManager()
        self.operation_journal = None
        self.localizer = get_localization_manager()
        self.theme_manager = theme_manager or get_theme_manager(
            QApplication.instance(), self.localizer.store
        )
        self._animation_jobs: list[dict[str, object]] = []
        # The lower Log Entries/Traces area is a true workspace splitter.  Its
        # ratio survives the widget's intentional reparenting for Multi-Pane.
        self._trace_area_ratio = 0.22
        # Menu actions retain their standard shortcut metadata, while this
        # intentionally inert owner prevents Qt from treating the entire
        # MainWindow (including text/table children) as a shortcut target.
        self._export_shortcut_owner = QWidget(self)
        self._export_shortcut_owner.hide()

        self._build_menu()
        self._build_application_toolbar()
        self._build_central_widget()
        self._register_export_surface(
            self.plot_widget.plot_widget, self.plot_widget.plot_widget.getPlotItem()
        )
        self._register_export_surface(self.plot_2d_widget.graphics_widget, self.plot_2d_widget.plot_item)
        self._register_export_surface(
            self.plot_widget_nd.plot_widget, self.plot_widget_nd.plot_widget.getPlotItem()
        )
        self._register_export_surface(self.plot_2d_widget_nd.graphics_widget, self.plot_2d_widget_nd.plot_item)
        self._cut_windows = {
            "x": CutWindow("x", self),
            "y": CutWindow("y", self),
        }
        self._initialize_mark_overlays()
        self._initialize_multi_pane()
        self._install_annotation()
        self.plot_interaction_controls.set_multi_pane_available(False)
        self._connect_display_state_signals()
        self._update_export_action_labels()
        self.localizer.bind(self)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("No file loaded. File → Open to begin.")

        # v0.9A: Up/Down = sequential Trace navigation, regardless of
        # which child widget technically has keyboard focus (spec's
        # explicit "不要要求使用者點擊一個很小的 hidden widget" requirement).
        # Installed at the QApplication level so it fires BEFORE a
        # focused child widget (e.g. a QComboBox, which also responds
        # to Up/Down itself) would otherwise consume the key first.
        # Guarded by isActiveWindow() so it only affects the currently
        # focused Viewer - independent Viewer windows never interfere
        # with each other's trace navigation.
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    # ---- UI construction --------------------------------------------------

    def _build_menu(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")

        self.open_action = QAction("&Open...", self)
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.setIcon(icon("open"))
        self.open_action.triggered.connect(self.open_file_dialog)
        file_menu.addAction(self.open_action)

        self.reload_action = QAction("&Reload", self)
        self.reload_action.setShortcut("Ctrl+R")
        self.reload_action.setIcon(icon("reload"))
        self.reload_action.triggered.connect(self._reload_current_file)
        file_menu.addAction(self.reload_action)

        file_menu.addSeparator()
        self.save_view_action = QAction("Save View Preset...", self)
        self.save_view_action.triggered.connect(self._save_named_view)
        file_menu.addAction(self.save_view_action)
        self.load_view_action = QAction("Manage View Presets...", self)
        self.load_view_action.triggered.connect(self._open_named_view_manager)
        file_menu.addAction(self.load_view_action)

        file_menu.addSeparator()
        self.copy_plot_action = QAction("Copy Plot", self._export_shortcut_owner)
        self.copy_plot_action.setShortcut(QKeySequence.StandardKey.Copy)
        self.copy_plot_action.setShortcutContext(Qt.WidgetShortcut)
        self.copy_plot_action.triggered.connect(self._copy_active_pane)
        self.copy_context_action = QAction("Copy Plot", self._export_shortcut_owner)
        self.copy_context_action.setShortcut(QKeySequence.StandardKey.Copy)
        self.copy_context_action.setShortcutContext(Qt.WidgetShortcut)
        self.copy_context_action.triggered.connect(self._copy_context_pane)
        self.copy_all_panes_action = QAction("Copy All Panes", self._export_shortcut_owner)
        self.copy_all_panes_action.setShortcut(
            QKeySequence("Meta+Shift+C" if sys.platform == "darwin" else "Ctrl+Shift+C")
        )
        self.copy_all_panes_action.setShortcutContext(Qt.WidgetShortcut)
        self.copy_all_panes_action.triggered.connect(self._copy_all_panes)
        self.save_plot_action = QAction("Save Plot As...", self._export_shortcut_owner)
        self.save_plot_action.triggered.connect(self._save_active_pane_dialog)
        self.save_context_action = QAction("Save Plot As...", self._export_shortcut_owner)
        self.save_context_action.triggered.connect(self._save_context_pane)
        self.save_all_panes_action = QAction("Save All Panes As...", self._export_shortcut_owner)
        self.save_all_panes_action.triggered.connect(self._save_all_panes_dialog)
        self.export_data_action = QAction("Export Data...", self)
        self.export_data_action.triggered.connect(self._export_active_pane_data)
        # A plot context menu has an explicit pointer pane, whereas File/Menu
        # commands intentionally use the Active Pane.  Both route through the
        # same export command implementation.
        self.export_data_context_action = QAction("Export Data...", self)
        self.export_data_context_action.triggered.connect(self._export_context_pane_data)
        self.export_menu = QMenu("&Export", file_menu)
        self.export_menu.addAction(self.copy_plot_action)
        self.export_menu.addAction(self.copy_all_panes_action)
        self.export_menu.addSeparator()
        self.export_menu.addAction(self.save_plot_action)
        self.export_menu.addAction(self.save_all_panes_action)
        self.export_menu.addSeparator()
        self.export_menu.addAction(self.export_data_action)
        self.export_menu.addSeparator()
        self.export_animation_action = QAction("Export Animation...", self)
        self.export_animation_action.triggered.connect(self._export_animation_dialog)
        self.export_menu.addAction(self.export_animation_action)
        self.export_menu.aboutToShow.connect(self._update_export_action_labels)
        file_menu.addMenu(self.export_menu)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menu.addMenu("&View")
        self.traces_action = QAction("&Traces", self)
        self.traces_action.setIcon(icon("trace"))
        self.traces_action.triggered.connect(self._show_trace_manager)
        view_menu.addAction(self.traces_action)
        self.metadata_action = QAction("&Metadata", self)
        self.metadata_action.triggered.connect(self._show_metadata_dialog)
        self.data_table_action = QAction("&Data Table", self)
        self.data_table_action.triggered.connect(self._show_data_table_dialog)
        self.show_x_cut_action = QAction("Show X Cut", self)
        self.show_x_cut_action.triggered.connect(lambda: self._show_cut_window("x"))
        self.show_y_cut_action = QAction("Show Y Cut", self)
        self.show_y_cut_action.triggered.connect(lambda: self._show_cut_window("y"))
        self.show_menu = QMenu("&Show", self)
        self.show_menu.addAction(self.metadata_action)
        self.show_menu.addAction(self.data_table_action)
        self.show_menu.addSeparator()
        self.show_menu.addAction(self.show_x_cut_action)
        self.show_menu.addAction(self.show_y_cut_action)
        view_menu.addMenu(self.show_menu)

        analysis_menu = menu.addMenu("&Analysis")
        self.node_antinode_action = QAction("Open Coarse Detector", self)
        self.node_antinode_action.setEnabled(False)
        self.node_antinode_action.setVisible(False)
        self.node_antinode_action.triggered.connect(self._open_coarse_in_analysis)
        self.yig_fitting_action = QAction("YIG Mirror Analysis...", self)
        self.yig_fitting_action.setEnabled(False)
        self.yig_fitting_action.triggered.connect(self._open_yig_fitting_window)
        analysis_menu.addAction(self.yig_fitting_action)
        self.surface_3d_action = QAction("3D Surface...", self)
        self.surface_3d_action.setEnabled(False)
        self.surface_3d_action.triggered.connect(self.open_3d_window)
        analysis_menu.addAction(self.surface_3d_action)
        self.analysis_menu = analysis_menu
        from app.settings.dialog import install_settings_menu
        self.settings_menu = install_settings_menu(self)

    def _build_application_toolbar(self) -> None:
        """Permanent application actions, separate from plot-local controls."""
        toolbar = GlassToolBar("Viewer", self)
        toolbar.setObjectName("viewerApplicationToolbar")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        toolbar.setIconSize(QSize(TOOLBAR_ICON_SIZE, TOOLBAR_ICON_SIZE))
        self.addToolBar(Qt.TopToolBarArea, toolbar)
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.reload_action)
        toolbar.addSeparator()
        toolbar.addAction(self.traces_action)
        self.show_tool_button = QToolButton(toolbar)
        make_icon_only(self.show_tool_button, "show_trace", "Show", TOOLBAR_ICON_SIZE)
        self.show_tool_button.setMenu(self.show_menu)
        self.show_tool_button.setPopupMode(QToolButton.InstantPopup)
        toolbar.addWidget(self.show_tool_button)
        self.export_tool_button = QToolButton(toolbar)
        make_icon_only(self.export_tool_button, "export", "Export", TOOLBAR_ICON_SIZE)
        self.export_tool_button.setMenu(self.export_menu)
        self.export_tool_button.setPopupMode(QToolButton.InstantPopup)
        toolbar.addWidget(self.export_tool_button)
        toolbar.addSeparator()
        self.analysis_tool_button = QToolButton(toolbar)
        make_icon_only(self.analysis_tool_button, "analysis", "Analysis", TOOLBAR_ICON_SIZE)
        self.analysis_tool_button.setMenu(self.analysis_menu)
        self.analysis_tool_button.setPopupMode(QToolButton.InstantPopup)
        toolbar.addWidget(self.analysis_tool_button)
        toolbar.addSeparator()
        self.plot_interaction_controls = PlotInteractionControls(toolbar, zoom=True)
        self.plot_interaction_controls.mode_changed.connect(self._set_drag_share_mode)
        from app.gui.plot_gestures import RectZoom

        self.rect_zoom = RectZoom(self)                  # rectangle Zoom tool (1D / 2D)
        self.plot_interaction_controls.zoom_changed.connect(self.rect_zoom.set_enabled)
        self.rect_zoom.user_zoomed = self._note_data_operation
        toolbar.addWidget(self.plot_interaction_controls)
        # Annotation sits at the top right in every window.
        from app.gui.annotation import make_annotation_button, toolbar_stretch
        toolbar.addWidget(toolbar_stretch())
        self.annotation_button = make_annotation_button(TOOLBAR_ICON_SIZE)
        toolbar.addWidget(self.annotation_button)
        self.application_toolbar = toolbar
        from app.gui.network_panel import install_network_bar

        install_network_bar(self)                      # Network Workspace status, first row

    # ---- per-data Quick Preview display state -------------------------------

    def _connect_display_state_signals(self) -> None:
        """Persist only meaningful display choices, never mouse/repaint noise."""
        widgets = (
            self.mode_combo, self.x_combo, self.y_combo, self.transform_combo,
            self.db_checkbox, self.unwrap_checkbox, self.z_combo_2d,
            self.x_combo_2d, self.y_combo_2d, self.transform_combo_2d,
            self.colormap_combo, self.auto_range_checkbox, self.zmin_spin, self.zmax_spin,
            self.x_combo_nd, self.y_combo_nd, self.z_combo_nd, self.transform_combo_nd,
            self.geometry_combo, self.phase_unwrap_axis_combo,
            self.surface_rendering_combo, self.surface_color_source_combo,
            self.surface_color_transform_combo, self.colormap_combo_nd,
            self.auto_range_checkbox_nd, self.zmin_spin_nd, self.zmax_spin_nd,
            self.surface_z_scale_slider, self.surface_z_auto_checkbox,
            self.surface_projection_combo, self.surface_opacity_slider,
            self.surface_b_transform_combo, self.surface_b_opacity_slider,
            self.surface_projection_checkbox, self.surface_reference_combo,
            self.surface_reference_value, self.surface_top_colorbar_checkbox,
            self.point_x_mapping_combo, self.point_y_mapping_combo,
            self.point_z_mapping_combo, self.point_color_mapping_combo,
            self.point_size_spin,
        )
        for widget in widgets:
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(self._schedule_display_state_save)
            elif isinstance(widget, (QDoubleSpinBox, QSpinBox, QSlider)):
                widget.valueChanged.connect(self._schedule_display_state_save)
            else:
                widget.currentIndexChanged.connect(self._schedule_display_state_save)
        self.log_entries.entry_selected.connect(lambda _entry: self._schedule_display_state_save())
        self.log_entries.selection_changed.connect(lambda: self.workspace_state_changed.emit())
        self.trace_color_combo.currentTextChanged.connect(lambda _text: self.workspace_state_changed.emit())

    def _schedule_display_state_save(self, *_args) -> None:
        if self.experiment is not None and not self._multi_pane_loading:
            state = self._current_viewer_display_state()
            if state is not None:
                # Browser Preview may be selected before the debounce expires;
                # stage the state in-process now and keep the write coalesced.
                self.viewer_display_state_store.stage(self.experiment.source_path, state)
            self._display_state_timer.start()
            if not self._restoring_session:
                self.workspace_state_changed.emit()

    def _three_d_state(self) -> dict | None:
        """The 3D controls' state (the inner "three_d" dict), or None if incomplete."""
        x_name = self.x_combo_nd.currentText()
        y_name = self.y_combo_nd.currentData()
        z_name = self.z_combo_nd.currentText()
        if not x_name or y_name is None or not z_name:
            return None
        return {
            "x": x_name,
            "y": str(y_name),
            "height_source": z_name,
            "geometry": self.geometry_combo.currentData() or "Surface",
            "height_transform": self.transform_combo_nd.currentData() or "raw",
            "unwrap_axis": int(self.phase_unwrap_axis_combo.currentData() or 0),
            "color_source": self.surface_color_source_combo.currentData(),
            "color_transform": self.surface_color_transform_combo.currentData() or "raw",
            "colormap": self.colormap_combo_nd.currentText() or DEFAULT_COLORMAP,
            "auto_color": self.auto_range_checkbox_nd.isChecked(),
            "color_minimum": self.zmin_spin_nd.value(),
            "color_maximum": self.zmax_spin_nd.value(),
            "rendering_policy": self.surface_rendering_combo.currentData() or "Auto",
            "z_scale": self.surface_z_scale_slider.value(),
            "z_auto": self.surface_z_auto_checkbox.isChecked(),
            "projection": self.surface_projection_combo.currentData() or "perspective",
            "opacity": self.surface_opacity_slider.value(),
            "surface_b_transform": self.surface_b_transform_combo.currentData() or "imag",
            "surface_b_opacity": self.surface_b_opacity_slider.value(),
            "bottom_projection": self.surface_projection_checkbox.isChecked(),
            "reference_mode": self.surface_reference_combo.currentData() or "off",
            "reference_value": self.surface_reference_value.value(),
            "top_colorbar": self.surface_top_colorbar_checkbox.isChecked(),
            "point_x": self.point_x_mapping_combo.currentData(),
            "point_y": self.point_y_mapping_combo.currentData(),
            "point_z": self.point_z_mapping_combo.currentData(),
            "point_color": self.point_color_mapping_combo.currentData(),
            "point_size": self.point_size_spin.value(),
            "camera": (self.nd_surface_renderer.camera_state().to_dict()
                       if self.nd_surface_renderer is not None else None),
        }

    def _current_viewer_display_state(self) -> dict | None:
        if self.experiment is None:
            return None
        mode = 2 if self._three_d_active() else self.mode_combo.currentIndex()
        if mode == 0:
            x_candidate = self.x_combo.currentData()
            y_candidate = self.y_combo.currentData()
            if x_candidate is None or y_candidate is None:
                return None
            transform = self._current_transform_spec().resolve_transform_key()
            pane_state = self._pane_states.get(self._active_pane_id)
            return {
                "mode": "1d",
                "one_d": {
                    "channel": y_candidate.base_channel,
                    "x_axis": x_candidate.name,
                    "transform": transform,
                    "db": self.db_checkbox.isChecked(),
                    "unwrap": self.unwrap_checkbox.isChecked(),
                    "trace_index": self.log_entries.current_row(),
                    "x_formula": pane_state.x_formula if pane_state else "",
                    "y_formula": pane_state.y_formula if pane_state else "",
                    "formula_enabled": bool(pane_state and pane_state.formula_enabled),
                },
            }
        if mode == 1:
            z_name = self.z_combo_2d.currentText()
            x_name = self.x_combo_2d.currentText()
            y_name = self.y_combo_2d.currentText()
            transform = self.transform_combo_2d.currentData()
            if not all(isinstance(value, str) and value for value in (z_name, x_name, y_name, transform)):
                return None
            return {
                "mode": "2d",
                "two_d": {
                    "z": z_name,
                    "x": x_name,
                    "y": y_name,
                    "transform": transform,
                    "colormap": self.colormap_combo.currentText() or DEFAULT_COLORMAP,
                    "auto_color": self.auto_range_checkbox.isChecked(),
                    # The spinboxes intentionally display a compact rounded
                    # value; the ColorBarItem owns the exact visible levels.
                    "minimum": self.plot_2d_widget._z_min if self.plot_2d_widget._z_min is not None else self.zmin_spin.value(),
                    "maximum": self.plot_2d_widget._z_max if self.plot_2d_widget._z_max is not None else self.zmax_spin.value(),
                },
            }
        if mode == 2:
            state = self._three_d_state()
            return {"mode": "3d", "three_d": state} if state is not None else None
        return None

    def _flush_display_state(self) -> None:
        if self._display_state_timer.isActive():
            self._display_state_timer.stop()
        if self.experiment is None:
            return
        state = self._current_viewer_display_state()
        if state is not None:
            self.viewer_display_state_store.stage(self.experiment.source_path, state)
            self.viewer_display_state_store.flush()

    # ---- v0.13C workspace session state -----------------------------------

    @staticmethod
    def _geometry_state(widget: QWidget) -> dict[str, object]:
        return {
            "geometry": bytes(widget.saveGeometry()).hex(),
            "maximized": bool(widget.isMaximized()),
        }

    @staticmethod
    def _restore_geometry(widget: QWidget, state: object) -> None:
        if not isinstance(state, dict):
            return
        encoded = state.get("geometry")
        if isinstance(encoded, str):
            try:
                widget.restoreGeometry(QByteArray(bytes.fromhex(encoded)))
            except (TypeError, ValueError):
                pass
        frame = widget.frameGeometry()
        screens = QGuiApplication.screens()
        if screens and not any(screen.availableGeometry().intersects(frame) for screen in screens):
            available = QGuiApplication.primaryScreen().availableGeometry()
            widget.move(available.center() - widget.rect().center())
        if state.get("maximized") is True:
            widget.showMaximized()

    @staticmethod
    def _pane_state_payload(state: PaneState) -> dict[str, object]:
        return {
            "plot_mode": state.plot_mode, "x_axis": state.x_axis, "y_axis": state.y_axis,
            "transform_name": state.transform_name, "db": state.db, "unwrap": state.unwrap,
            "show_data_points": state.show_data_points, "point_size_mode": state.point_size_mode,
            "manual_point_size": state.manual_point_size, "trace_index": state.trace_index,
            "z_name": state.z_name, "grid_x_name": state.grid_x_name,
            "grid_y_name": state.grid_y_name, "grid_transform": state.grid_transform,
            "colormap": state.colormap, "auto_color": state.auto_color,
            "color_min": state.color_min, "color_max": state.color_max,
            "x_range": list(state.x_range) if state.x_range is not None else None,
            "y_range": list(state.y_range) if state.y_range is not None else None,
            "x_formula": state.x_formula, "y_formula": state.y_formula,
            "formula_enabled": state.formula_enabled,
        }

    @staticmethod
    def _apply_pane_state_payload(state: PaneState, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        fields = (
            "plot_mode", "x_axis", "y_axis", "transform_name", "db", "unwrap",
            "show_data_points", "point_size_mode", "manual_point_size", "trace_index",
            "z_name", "grid_x_name", "grid_y_name", "grid_transform", "colormap",
            "auto_color", "color_min", "color_max",
        )
        for field in fields:
            if field in payload:
                setattr(state, field, payload[field])
        for field in ("x_formula", "y_formula"):
            value = payload.get(field, getattr(state, field))
            if isinstance(value, str):
                try:
                    parse_formula(value)
                except FormulaError:
                    setattr(state, field, "")
                    state.formula_enabled = False
                    continue
                setattr(state, field, value)
        state.formula_enabled = bool(payload.get("formula_enabled", state.formula_enabled)) and bool(
            state.x_formula or state.y_formula
        )
        for axis in ("x", "y"):
            value = payload.get(f"{axis}_range")
            if isinstance(value, list) and len(value) == 2:
                try:
                    bounds = (float(value[0]), float(value[1]))
                    if np.isfinite(bounds).all() and bounds[0] != bounds[1]:
                        setattr(state, f"{axis}_range", bounds)
                except (TypeError, ValueError):
                    pass

    def session_state(self) -> dict[str, object] | None:
        """Return semantic Viewer state; Marks and external domains stay separate."""
        if self.experiment is None:
            return None
        self._capture_active_pane_state()
        selection = self.log_entries.selection_state
        panes = {
            str(pane_id): self._pane_state_payload(state)
            for pane_id, state in self._pane_states.items()
        }
        return {
            "source_path": self.experiment.data_identity,
            "display": self._current_viewer_display_state(),
            "trace_selection": {
                "selected": list(selection.ordered_selection()),
                "visible": list(selection.visible_selection()),
                "active": selection.active_trace,
                "reference": selection.reference_trace,
                "color_mode": self.trace_color_combo.currentText() or SEQUENTIAL,
            },
            "pane": {
                "layout": self.pane_layout_combo.currentText(),
                "active": self._active_pane_id,
                "states": panes,
                "sizes": self._pane_splitter_sizes(),
                "multi_sizes": self.multi_pane_splitter.sizes(),
                "sync_trace": self.sync_trace_checkbox.isChecked(),
                "sync_x": self.sync_x_checkbox.isChecked(),
            },
            "splitters": {
                "main": self.main_splitter.sizes(), "right": self.right_splitter.sizes(),
                "plot_1d": self.plot_1d_splitter.sizes(), "plot_2d": self.plot_2d_splitter.sizes(),
                "trace_area_ratio": self._current_trace_area_ratio(),
            },
            "windows": {
                "viewer": self._geometry_state(self),
                "metadata": {"open": self.metadata_dialog.isVisible(),
                             **self._geometry_state(self.metadata_dialog)},
                "data_table": {"open": self.data_table_dialog.isVisible(),
                               **self._geometry_state(self.data_table_dialog)},
                "cuts": {
                    axis: {"open": window.isVisible(),
                           "follow": window.follow_main_plot_checkbox.isChecked(),
                           "always_on_top": window.always_on_top_checkbox.isChecked(),
                           "position": list(window._position) if window._position is not None else None,
                           "source_pane": window._source_pane,
                           **self._geometry_state(window)}
                    for axis, window in self._cut_windows.items()
                },
            },
        }

    def _restore_display_state(self, state: object) -> None:
        if not isinstance(state, dict):
            return
        mode = state.get("mode")
        if mode == "1d" and isinstance(state.get("one_d"), dict):
            one_d = state["one_d"]
            x_formula = one_d.get("x_formula", "")
            y_formula = one_d.get("y_formula", "")
            if not isinstance(x_formula, str) or not isinstance(y_formula, str):
                x_formula, y_formula = "", ""
            try:
                parse_formula(x_formula)
                parse_formula(y_formula)
            except FormulaError:
                x_formula, y_formula = "", ""
                self.formula_status_label.setText("Saved Formula was invalid; identity was restored.")
            pane_state = self._pane_states.get(self._active_pane_id)
            if pane_state is not None:
                pane_state.x_formula = x_formula
                pane_state.y_formula = y_formula
                pane_state.formula_enabled = bool(one_d.get("formula_enabled", False)) and bool(x_formula or y_formula)
            with QSignalBlocker(self.x_formula_edit), QSignalBlocker(self.y_formula_edit):
                self.x_formula_edit.setText(x_formula)
                self.y_formula_edit.setText(y_formula)
            self._refresh_formula_previews()
            self.mode_combo.setCurrentIndex(0)
            for combo, key in ((self.x_combo, "x_axis"), (self.y_combo, "channel")):
                value = one_d.get(key)
                if isinstance(value, str) and combo.findText(value) >= 0:
                    combo.setCurrentText(value)
            transform = one_d.get("transform")
            base = {
                "real": "Real", "imag": "Imag", "magnitude": "Magnitude",
                "magnitude_db": "Magnitude", "phase_deg": "Phase", "phase_rad": "Phase",
            }.get(transform)
            if base and self.transform_combo.findText(base) >= 0:
                self.transform_combo.setCurrentText(base)
            self.db_checkbox.setChecked(transform == "magnitude_db" or bool(one_d.get("db")))
            self.unwrap_checkbox.setChecked(bool(one_d.get("unwrap")))
            return
        if mode == "2d" and isinstance(state.get("two_d"), dict):
            two_d = state["two_d"]
            self.mode_combo.setCurrentIndex(1)
            for combo, key in ((self.z_combo_2d, "z"), (self.x_combo_2d, "x"),
                               (self.y_combo_2d, "y"), (self.colormap_combo, "colormap")):
                value = two_d.get(key)
                if isinstance(value, str) and combo.findText(value) >= 0:
                    combo.setCurrentText(value)
            index = self.transform_combo_2d.findData(two_d.get("transform"))
            if index >= 0:
                self.transform_combo_2d.setCurrentIndex(index)
            self.auto_range_checkbox.setChecked(bool(two_d.get("auto_color", True)))
            for spin, key in ((self.zmin_spin, "minimum"), (self.zmax_spin, "maximum")):
                try:
                    spin.setValue(float(two_d.get(key, spin.value())))
                except (TypeError, ValueError):
                    pass
            return
        if mode == "3d" and isinstance(state.get("three_d"), dict):
            three_d = state["three_d"]
            self._pending_3d_camera_state = three_d.get("camera")
            blocked = (
                self.mode_combo, self.x_combo_nd, self.y_combo_nd, self.z_combo_nd,
                self.transform_combo_nd, self.geometry_combo, self.phase_unwrap_axis_combo,
                self.surface_color_source_combo, self.surface_color_transform_combo,
                self.colormap_combo_nd, self.auto_range_checkbox_nd,
                self.zmin_spin_nd, self.zmax_spin_nd, self.surface_rendering_combo,
                self.surface_z_scale_slider, self.surface_z_auto_checkbox,
                self.surface_projection_combo, self.surface_opacity_slider,
                self.surface_b_transform_combo, self.surface_b_opacity_slider,
                self.surface_projection_checkbox, self.surface_reference_combo,
                self.surface_reference_value, self.surface_top_colorbar_checkbox,
                self.point_x_mapping_combo, self.point_y_mapping_combo,
                self.point_z_mapping_combo, self.point_color_mapping_combo,
                self.point_size_spin,
            )
            previous_signal_states = [(widget, widget.blockSignals(True)) for widget in blocked]
            try:
                self._show_3d_window()
                for combo, key, by_data in (
                    (self.x_combo_nd, "x", False),
                    (self.y_combo_nd, "y", True),
                    (self.z_combo_nd, "height_source", False),
                    (self.geometry_combo, "geometry", True),
                    (self.transform_combo_nd, "height_transform", True),
                    (self.phase_unwrap_axis_combo, "unwrap_axis", True),
                    (self.surface_color_source_combo, "color_source", True),
                    (self.surface_color_transform_combo, "color_transform", True),
                    (self.colormap_combo_nd, "colormap", False),
                    (self.surface_rendering_combo, "rendering_policy", True),
                    (self.surface_projection_combo, "projection", True),
                    (self.surface_b_transform_combo, "surface_b_transform", True),
                    (self.surface_reference_combo, "reference_mode", True),
                ):
                    value = three_d.get(key)
                    index = combo.findData(value) if by_data else combo.findText(value) if isinstance(value, str) else -1
                    if index >= 0:
                        combo.setCurrentIndex(index)
                self._populate_point_mappings()
                for combo, key in (
                    (self.point_x_mapping_combo, "point_x"),
                    (self.point_y_mapping_combo, "point_y"),
                    (self.point_z_mapping_combo, "point_z"),
                    (self.point_color_mapping_combo, "point_color"),
                ):
                    index = combo.findData(three_d.get(key))
                    if index >= 0:
                        combo.setCurrentIndex(index)
                self.auto_range_checkbox_nd.setChecked(bool(three_d.get("auto_color", True)))
                self.surface_z_auto_checkbox.setChecked(bool(three_d.get("z_auto", True)))
                self.surface_projection_checkbox.setChecked(bool(three_d.get("bottom_projection", False)))
                self.surface_top_colorbar_checkbox.setChecked(bool(three_d.get("top_colorbar", False)))
                self.surface_opacity_slider.setValue(int(three_d.get("opacity", 100)))
                self.surface_b_opacity_slider.setValue(int(three_d.get("surface_b_opacity", 55)))
                self.surface_z_scale_slider.setValue(int(three_d.get("z_scale", 10)))
                self.surface_reference_value.setValue(float(three_d.get("reference_value", 0.0)))
                self.point_size_spin.setValue(float(three_d.get("point_size", 0.018)))
                low = float(three_d.get("color_minimum", self.zmin_spin_nd.value()))
                high = float(three_d.get("color_maximum", self.zmax_spin_nd.value()))
                if np.isfinite([low, high]).all() and low < high:
                    self.zmin_spin_nd.setValue(low)
                    self.zmax_spin_nd.setValue(high)
            except (TypeError, ValueError, OverflowError):
                pass
            finally:
                for widget, previous in previous_signal_states:
                    widget.blockSignals(previous)
            self.zmin_spin_nd.setEnabled(not self.auto_range_checkbox_nd.isChecked())
            self.zmax_spin_nd.setEnabled(not self.auto_range_checkbox_nd.isChecked())
            self.surface_opacity_label.setText(f"{self.surface_opacity_slider.value()}%")
            self.surface_b_opacity_label.setText(f"{self.surface_b_opacity_slider.value()}%")
            self.surface_z_scale_label.setText(
                self.localizer.text("viewer.auto") if self.surface_z_auto_checkbox.isChecked()
                else f"{self.surface_z_scale_slider.value() / 10.0:.1f}×"
            )
            self._update_geometry_controls()
            self._rebuild_nd_plot()

    def restore_session_state(self, state: object) -> bool:
        """Restore one already-opened Viewer defensively and without write loops."""
        if self.experiment is None or not isinstance(state, dict):
            return False
        self._restoring_session = True
        try:
            self._restore_display_state(state.get("display"))
            pane = state.get("pane")
            if isinstance(pane, dict):
                layout = pane.get("layout")
                if isinstance(layout, str) and self.pane_layout_combo.findText(layout) >= 0:
                    self.pane_layout_combo.setCurrentText(layout)
                states = pane.get("states")
                if isinstance(states, dict):
                    for key, payload in states.items():
                        try:
                            pane_id = int(key)
                        except (TypeError, ValueError):
                            continue
                        if pane_id in self._pane_states:
                            self._apply_pane_state_payload(self._pane_states[pane_id], payload)
                active = pane.get("active")
                if isinstance(active, int) and active in self._pane_states:
                    self._activate_pane(active, capture=False)
                self.sync_trace_checkbox.setChecked(bool(pane.get("sync_trace", True)))
                self.sync_x_checkbox.setChecked(bool(pane.get("sync_x", False)))
                sizes = pane.get("sizes")
                if isinstance(sizes, list):
                    self._restore_pane_splitter_sizes(sizes)
                multi_sizes = pane.get("multi_sizes")
                if isinstance(multi_sizes, list) and len(multi_sizes) == self.multi_pane_splitter.count():
                    self.multi_pane_splitter.setSizes(multi_sizes)
                if not self.multi_pane_splitter.isHidden():
                    self._render_multi_panes()

            selection = state.get("trace_selection")
            if isinstance(selection, dict):
                self.log_entries.restore_selection(
                    selection.get("selected", []), active=selection.get("active"),
                    visible=selection.get("visible"), reference=selection.get("reference"),
                )
                color_mode = selection.get("color_mode")
                if isinstance(color_mode, str) and self.trace_color_combo.findText(color_mode) >= 0:
                    self.trace_color_combo.setCurrentText(color_mode)
            splitters = state.get("splitters")
            if isinstance(splitters, dict):
                for splitter, key in ((self.main_splitter, "main"), (self.right_splitter, "right"),
                                      (self.plot_1d_splitter, "plot_1d"), (self.plot_2d_splitter, "plot_2d")):
                    sizes = splitters.get(key)
                    if isinstance(sizes, list) and len(sizes) == splitter.count() and sum(sizes) > 0:
                        splitter.setSizes(sizes)
                ratio = splitters.get("trace_area_ratio")
                if isinstance(ratio, (int, float)):
                    self._restore_trace_area_ratio(float(ratio))
            windows = state.get("windows")
            cut_states = None
            if isinstance(windows, dict):
                self._restore_geometry(self, windows.get("viewer"))
                for dialog, key in ((self.metadata_dialog, "metadata"),
                                    (self.data_table_dialog, "data_table")):
                    window_state = windows.get(key)
                    if isinstance(window_state, dict):
                        self._restore_geometry(dialog, window_state)
                        if window_state.get("open") is True:
                            dialog.show()
                cut_states = windows.get("cuts")
            # Named View Presets store only scientific Cut/window state, not
            # full-session geometry. Accept that compact representation too.
            if not isinstance(cut_states, dict):
                cut_states = state.get("cuts")
            if isinstance(cut_states, dict):
                for axis, window in self._cut_windows.items():
                    cut_state = cut_states.get(axis)
                    if not isinstance(cut_state, dict):
                        continue
                    window.follow_main_plot_checkbox.setChecked(bool(cut_state.get("follow", True)))
                    window.always_on_top_checkbox.setChecked(bool(cut_state.get("always_on_top", False)))
                    if isinstance(windows, dict):
                        self._restore_geometry(window, cut_state)
                    if cut_state.get("open") is True:
                        self._show_cut_window(axis)
                    # Showing refreshes the live source grid. Apply the saved
                    # Cut coordinate afterward so the source's default
                    # crosshair does not overwrite the View/session position.
                    position = cut_state.get("position")
                    if isinstance(position, list) and len(position) == 2:
                        try:
                            window.set_position(float(position[0]), float(position[1]))
                        except (TypeError, ValueError):
                            pass
            analysis = state.get("analysis")
            if isinstance(analysis, dict):
                for combo, key in ((self.analysis_region_combo, "region"),
                                   (self.analysis_operation_combo, "operation")):
                    value = analysis.get(key)
                    if isinstance(value, str) and combo.findText(value) >= 0:
                        combo.setCurrentText(value)
                try:
                    self.analysis_window_spin.setValue(int(analysis.get("window", self.analysis_window_spin.value())))
                except (TypeError, ValueError):
                    pass
                target = analysis.get("target")
                self._analysis_target_id = target if isinstance(target, str) else None
            if isinstance(state.get("show_mark_values"), bool):
                self.show_mark_values_checkbox.setChecked(state["show_mark_values"])
            overlay_name = state.get("overlay_name")
            if isinstance(overlay_name, str):
                index = self.saved_overlay_combo.findText(overlay_name)
                if index >= 0:
                    self.saved_overlay_combo.setCurrentIndex(index)

            active = self._active_pane_id if self._active_pane_id in self._pane_states else 1
            if not self.multi_pane_splitter.isHidden():
                self._render_multi_panes()
            else:
                pane_state = self._pane_states.get(active)
                if pane_state is not None:
                    self._load_pane_controls(pane_state)
                    if pane_state.plot_mode == 0:
                        self.update_plot(preserve_view=True)
                        view_box = self.plot_widget.plot_widget.getViewBox()
                    elif pane_state.plot_mode == 1:
                        self._rebuild_2d_plot()
                        view_box = self.plot_2d_widget.view_box
                    else:
                        self._rebuild_nd_plot()
                        view_box = None
                    if view_box is not None and (pane_state.x_range is not None or pane_state.y_range is not None):
                        view_box.setRange(xRange=pane_state.x_range, yRange=pane_state.y_range, padding=0)
            self._apply_named_view_marks(state.get("marks"))
            if isinstance(analysis, dict):
                for combo, key in ((self.analysis_start_mark_combo, "start_mark"),
                                   (self.analysis_end_mark_combo, "end_mark")):
                    object_id = analysis.get(key)
                    index = combo.findData(object_id) if isinstance(object_id, str) else -1
                    if index >= 0:
                        combo.setCurrentIndex(index)
            display_state = state.get("display")
            if isinstance(display_state, dict) and display_state.get("mode") == "3d":
                self.open_3d_window()
            self._refresh_analysis_target()
            self._refresh_cut_windows()
            return True
        except Exception:
            return False
        finally:
            self._restoring_session = False

    def _apply_named_view_marks(self, payload: object, portable: bool = False) -> None:
        if not isinstance(payload, dict):
            return
        for pane_key, modes in payload.items():
            try:
                pane_id = int(pane_key)
            except (TypeError, ValueError):
                continue
            pane_state = self._pane_states.get(pane_id)
            if pane_state is None or not isinstance(modes, dict):
                continue
            for mode_key, entry in modes.items():
                try:
                    mode = int(mode_key)
                except (TypeError, ValueError):
                    continue
                manager = pane_state.mark_managers.get(mode)
                if manager is None or not manager.can_place or not isinstance(entry, dict):
                    continue
                own = self._portable_mark_context(manager.context_key) if portable else manager.context_key
                if entry.get("context") != self._mark_context_signature(own):
                    continue
                mark_state = entry.get("state")
                if not isinstance(mark_state, dict) or mark_state.get("mode") != manager.mode:
                    continue
                previous = manager.persistence_state()
                restored_count = manager.restore_persistence_state(mark_state)
                has_objects = bool(mark_state.get("marks") or mark_state.get("annotations"))
                if has_objects and restored_count == 0:
                    manager.restore_persistence_state(previous)
                    continue
                selected_id = entry.get("selected_id")
                manager.select_object(selected_id if isinstance(selected_id, str) else None)
                peaks = entry.get("half_peak")
                if isinstance(peaks, dict):
                    for object_id, values in peaks.items():
                        if not isinstance(object_id, str) or not isinstance(values, dict):
                            continue
                        try:
                            peak = HalfPeakResult(
                                extremum_x=float(values["extremum_x"]),
                                extremum_value=float(values["extremum_value"]),
                                baseline=float(values["baseline"]),
                                half_level=float(values["half_level"]),
                                left_crossing=float(values["left_crossing"]),
                                right_crossing=float(values["right_crossing"]),
                                width=float(values["width"]),
                                sample_index=int(values["sample_index"]),
                            )
                            manager.set_range_half_peak(object_id, peak)
                        except (KeyError, TypeError, ValueError, OverflowError):
                            continue
                if not self.multi_pane_splitter.isHidden():
                    frame = self._pane_frames.get(pane_id)
                    overlay = frame.mark_overlays.get(mode) if frame is not None else None
                elif pane_id == self._active_pane_id:
                    overlay = self._mark_overlays.get(mode)
                else:
                    overlay = None
                if overlay is not None:
                    self._sync_range_half_peak_visuals(manager, overlay)
                    overlay.render(manager.marks(), manager.annotations(), manager.selected_id,
                                   self.show_mark_values_checkbox.isChecked())
                self._persist_mark_manager(manager, pane_id)
        self._refresh_mark_ui()

    def _build_central_widget(self) -> None:
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(4, 4, 4, 4)

        self.main_splitter = QSplitter(Qt.Horizontal)
        splitter = self.main_splitter

        # right: mode selector + controls/plot stack, over experiment summary
        self.right_splitter = QSplitter(Qt.Vertical)
        right_splitter = self.right_splitter

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(4, 4, 4, 4)

        mode_row = QHBoxLayout()
        self.toggle_channels_button = QPushButton("\u25c0 Hide Controls")
        self.toggle_channels_button.setCheckable(True)
        self.toggle_channels_button.toggled.connect(self._on_toggle_channel_browser)
        mode_row.addWidget(self.toggle_channels_button)
        mode_row.addSpacing(12)
        mode_row.addWidget(QLabel("Plot Mode:"))
        self.mode_combo = QComboBox()
        # 3D Surface lives in its own window (Analysis > 3D Surface...).
        self.mode_combo.addItems([
            self.localizer.text("viewer.one_d"),
            self.localizer.text("viewer.two_d"),
        ])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_combo)
        self.maximize_plot_button = make_icon_only(QToolButton(), "maximize", "Maximize Plot")
        self.maximize_plot_button.setCheckable(True)
        self.maximize_plot_button.toggled.connect(self._on_maximize_plot_toggled)
        mode_row.addWidget(self.maximize_plot_button)
        mode_row.addSpacing(12)
        mode_row.addWidget(QLabel("Mark Tool:"))
        self.mark_tool_combo = QComboBox()
        self.mark_tool_combo.setMinimumWidth(155)
        self.mark_tool_combo.addItem("Choose Mark", userData=None)
        for tool in MARK_TOOLS:
            self.mark_tool_combo.addItem(mark_tool_icon(tool), TOOL_LABELS[tool], userData=tool)
        self.mark_tool_combo.currentIndexChanged.connect(self._on_mark_tool_changed)
        mode_row.addWidget(self.mark_tool_combo)
        self.add_mark_button = QPushButton("Add")
        self.add_mark_button.setCheckable(True)
        self.add_mark_button.setEnabled(False)
        self.add_mark_button.toggled.connect(self._on_add_mark_toggled)
        mode_row.addWidget(self.add_mark_button)
        self.delete_mark_button = QPushButton("Delete")
        self.delete_mark_button.setEnabled(False)
        self.delete_mark_button.clicked.connect(self._delete_selected_mark)
        mode_row.addWidget(self.delete_mark_button)
        self.clear_marks_button = QPushButton("Clear Tools")
        self.clear_marks_button.setEnabled(False)
        self.clear_marks_button.clicked.connect(self._clear_marks)
        mode_row.addWidget(self.clear_marks_button)
        mode_row.addStretch(1)
        central_layout.addLayout(mode_row)

        self.pane_controls_host = QWidget()
        self.pane_controls_host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        pane_controls = QHBoxLayout(self.pane_controls_host)
        pane_controls.setContentsMargins(0, 0, 0, 0)
        pane_controls.addWidget(QLabel("Pane Layout:"))
        self.pane_layout_combo = QComboBox()
        self.pane_layout_combo.addItems(PANE_LAYOUTS)
        self.pane_layout_combo.setMinimumWidth(180)
        pane_controls.addWidget(self.pane_layout_combo)
        self.sync_trace_checkbox = QCheckBox("Sync Trace")
        self.sync_trace_checkbox.setChecked(True)
        pane_controls.addWidget(self.sync_trace_checkbox)
        self.sync_x_checkbox = QCheckBox("Sync X Range")
        self.sync_x_checkbox.setChecked(False)
        pane_controls.addWidget(self.sync_x_checkbox)
        self.active_pane_label = QLabel("Active Pane: 1")
        self.active_pane_label.setStyleSheet("color: %s;" % STATUS["pane_label"])
        pane_controls.addWidget(self.active_pane_label)
        self.reset_layout_button = QPushButton("Reset Layout")
        self.reset_layout_button.clicked.connect(self._reset_pane_geometry)
        pane_controls.addWidget(self.reset_layout_button)
        pane_controls.addStretch(1)
        central_layout.addWidget(self.pane_controls_host)

        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self._build_1d_page())
        self.mode_stack.addWidget(self._build_2d_page())
        self._nd_plot_page = self._build_nd_page()
        top_layout.addWidget(self.mode_stack)

        self.multi_pane_splitter = QSplitter(Qt.Vertical)
        self.pane_grid_host = QWidget()
        self.pane_grid = QVBoxLayout(self.pane_grid_host)
        self.pane_grid.setContentsMargins(0, 0, 0, 0)
        self.pane_grid.setSpacing(0)
        self.multi_pane_splitter.addWidget(self.pane_grid_host)
        self.multi_pane_splitter.setStretchFactor(0, 4)
        self.multi_pane_splitter.hide()
        top_layout.addWidget(self.multi_pane_splitter)

        self.marks_widget = self._build_marks_widget()
        self.controls_panel = self._build_controls_panel()
        self.controls_panel.setMinimumWidth(260)
        self.controls_panel.setMaximumWidth(320)
        splitter.addWidget(self.controls_panel)
        self._channel_browser_last_width = 280

        right_splitter.addWidget(top)

        self.metadata_dialog = MetadataDialog(self.comment_store, self)
        self.summary_text = self.metadata_dialog.summary_text
        self.data_table_widget = self._build_data_table_widget()
        self.data_table_dialog = self._create_tool_dialog("Data Table", self.data_table_widget, 720, 460)
        right_splitter.setStretchFactor(0, 1)

        splitter.addWidget(right_splitter)
        # Plot side gets the large majority of horizontal space too;
        # channel browser defaults to a modest width and can be
        # dragged smaller/larger or hidden entirely via the toggle
        # button above.
        splitter.setSizes([280, 920])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        central_layout.addWidget(splitter)
        self.setCentralWidget(central)
        self._plot_layout_state: dict | None = None
        self.pane_layout_combo.currentTextChanged.connect(self._on_pane_layout_changed)
        self.sync_trace_checkbox.toggled.connect(self._on_sync_trace_toggled)
        self.sync_x_checkbox.toggled.connect(lambda _checked: self.workspace_state_changed.emit())
        for splitter in (self.main_splitter, self.right_splitter, self.plot_1d_splitter,
                         self.plot_2d_splitter, self.multi_pane_splitter):
            splitter.splitterMoved.connect(lambda *_args: self.workspace_state_changed.emit())
        self.plot_1d_splitter.splitterMoved.connect(lambda *_args: self._remember_trace_area_ratio())
        self.multi_pane_splitter.splitterMoved.connect(lambda *_args: self._remember_trace_area_ratio())

    def _create_tool_dialog(self, title: str, content: QWidget, width: int, height: int) -> QDialog:
        dialog = QDialog(self, Qt.Window)
        dialog.setWindowTitle(title)
        dialog.setModal(False)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(content)
        dialog.resize(width, height)
        return dialog

    def _show_metadata_dialog(self) -> None:
        self.metadata_dialog.show()
        self.metadata_dialog.raise_()
        self.metadata_dialog.activateWindow()
        self.workspace_state_changed.emit()

    def _show_data_table_dialog(self) -> None:
        self.data_table_dialog.show()
        self.data_table_dialog.raise_()
        self.data_table_dialog.activateWindow()
        self.workspace_state_changed.emit()

    def _open_node_antinode_window(self) -> None:
        """Compatibility entry point; coarse analysis now lives in the unified window."""
        self._open_coarse_in_analysis()

    def _open_coarse_in_analysis(self) -> None:
        self._open_yig_fitting_window()
        if self.experiment is None:
            return
        window = self._yig_fitting_windows.get(self.experiment.data_identity)
        if window is None:
            return
        window.tabs.setCurrentIndex(3)
        window.phase_panel.chk_coarse_enabled.setChecked(True)

    def _open_yig_fitting_window(self) -> None:
        if self.experiment is None:
            return
        self._journal_operation("YIG Mirror Analysis opened")
        identity = self.experiment.data_identity
        window = self._yig_fitting_windows.get(identity)
        if window is not None:
            window.show()
            window.raise_()
            window.activateWindow()
            return
        if not os.environ.get("MPLCONFIGDIR"):
            # Rebuildable font cache: kept out of the user data folder.
            app_cache = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.CacheLocation
            )
            if app_cache:
                cache_dir = Path(app_cache) / "matplotlib"
                try:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    os.environ["MPLCONFIGDIR"] = str(cache_dir)
                except OSError:
                    pass
        from app.gui.yig_fitting_window import YigMirrorFittingWindow

        window = YigMirrorFittingWindow(self.experiment, self)
        self._yig_fitting_windows[identity] = window
        window_ref = weakref.ref(window)
        window.destroyed.connect(
            lambda _obj=None, key=identity, ref=window_ref: self._forget_yig_fitting_window(key, ref)
        )
        window.show()
        window.raise_()
        return window

    def _journal_operation(self, label: str, **details) -> None:
        callback = self.operation_journal
        if callable(callback):
            callback(label, **details)

    # ---- screen annotation -----------------------------------------------------

    def _install_annotation(self) -> None:
        """Pen / laser overlay on the Viewer plots (display only, never saved)."""
        from app.gui.annotation import AnnotationSession, lock_widgets

        self.annotation = AnnotationSession(
            self, self.annotation_button,
            regions=lambda: [([self.mode_stack, self.multi_pane_splitter], False)],
            lock=lock_widgets(
                [self.mode_combo, self.pane_layout_combo, self.maximize_plot_button,
                 self.reset_layout_button],
                [self.multi_pane_splitter],
            ),
            localizer=self.localizer,
        )

    # ---- 3D Surface analysis window ------------------------------------------

    def _three_d_active(self) -> bool:
        window = getattr(self, "_three_d_window", None)
        return window is not None and window.isVisible()

    def _three_d_in_process(self) -> bool:
        from app.gui.three_d_process import process_mode_enabled

        return not process_mode_enabled()

    def _three_d_host(self):
        """The isolated 3D process for this Viewer (created on first use)."""
        if getattr(self, "_three_d_process", None) is None:
            from app.gui.three_d_process import ThreeDProcessHost
            from app.settings.store import add_settings_listener

            self._three_d_process = ThreeDProcessHost(self)
            self._three_d_process.crashed.connect(self._on_3d_process_crashed)
            self._three_d_process.message.connect(self._on_3d_process_message)
            add_settings_listener(lambda: self._three_d_process.send({"cmd": "settings"}))
            from app.gui.network_panel import status_summary
            from app.network.workspace import workspace

            relay = lambda: self._three_d_process.send({"cmd": "network", "status": status_summary()})
            workspace().changed.connect(relay)
            self._three_d_process.relay_network = relay
        return self._three_d_process

    def _open_3d_process(self) -> None:
        if self.experiment is None:
            return
        self._flush_display_state()
        state = self._three_d_state()
        host = self._three_d_host()
        host.show(str(self.experiment.source_path), state)
        host.relay_network()
        self._journal_operation("3D Surface window opened (separate process)")

    def _on_3d_process_message(self, message: dict) -> None:
        if message.get("cmd") == "show_network":
            from app.gui.network_panel import open_network_panel

            open_network_panel(self)
        elif message.get("cmd") == "show_settings":
            from app.settings.dialog import show_settings

            page = message.get("page")
            show_settings(page if page in ("general", "appearance", "three_d", "debug", "about") else None,
                          self, self.localizer, getattr(self, "theme_manager", None))

    def _on_3d_process_crashed(self, reason: str) -> None:
        self._journal_operation(f"3D Surface process ended unexpectedly ({reason})")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.localizer.text("three_d.crash_title"))
        box.setText(self.localizer.text("three_d.crash_text"))
        reopen = box.addButton(self.localizer.text("three_d.reopen"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.setModal(False)
        box.buttonClicked.connect(lambda button: self._open_3d_process() if button is reopen else None)
        box.show()

    def _show_3d_window(self):
        """Create (once) and show the 3D window without rebuilding the scene."""
        from app.gui.three_d_window import ThreeDAnalysisWindow

        if not self._three_d_in_process():
            # Restoring a saved 3D view: open it in the isolated 3D process once
            # the rest of the saved state has been applied.
            QTimer.singleShot(0, self._open_3d_process)
            return None

        if self._three_d_window is None:
            self._three_d_window = ThreeDAnalysisWindow(self, self._nd_controls_page, self._nd_plot_page)
            self._three_d_window.closed.connect(self._on_3d_window_closed)
            self.localizer.bind(self._three_d_window)
        self._three_d_window.update_title()
        self._three_d_window.refresh_export_style()
        self._three_d_window.show()
        self._three_d_window.raise_()
        self._three_d_window.activateWindow()
        return self._three_d_window

    def open_3d_window(self, *_args):
        if self.experiment is None:
            return None
        if not self._three_d_in_process():
            self._open_3d_process()
            return None
        window = self._show_3d_window()
        self._journal_operation("3D Surface window opened")
        self.nd_plot_stack.setCurrentIndex(2)
        self._rebuild_nd_plot()
        self._apply_surface_projection()
        return window

    def _on_3d_window_closed(self) -> None:
        renderer = getattr(self, "nd_surface_renderer", None)
        if renderer is not None and renderer.is_preparing:
            renderer.clear()
        self._journal_operation("3D Surface window closed")

    def three_d_export_style(self) -> str:
        store = getattr(self.localizer, "store", None)
        getter = getattr(store, "three_d_export_style", None)
        return getter() if callable(getter) else "publication"

    def _render_3d_publication(self, image_format: str = "png") -> bytes | None:
        from app.visualization3d.publication_style import render_renderer_publication

        try:
            return render_renderer_publication(
                self.nd_surface_renderer, image_format=image_format,
                plot_colors=self._current_export_plot_colors(),
            )
        except Exception as error:
            self.statusBar().showMessage(f"3D publication export failed: {error}", 6000)
            return None

    def _current_export_plot_colors(self):
        manager = getattr(self, "theme_manager", None) or get_theme_manager()
        from app.theme import WHITE_PLOT
        return manager.export_plot_colors if manager is not None else WHITE_PLOT

    def _render_3d_image_for_export(self) -> QImage | None:
        if not self._has_3d_scene():
            self.statusBar().showMessage("No 3D scene is available for export.", 4000)
            return None
        if self.three_d_export_style() == "publication":
            data = self._render_3d_publication("png")
            if data:
                image = QImage.fromData(data, "PNG")
                if not image.isNull():
                    return image
        return self._render_3d_export_image()

    def copy_3d_plot(self) -> None:
        image = self._render_3d_image_for_export()
        if image is not None:
            QGuiApplication.clipboard().setImage(image)
            target = self._three_d_window.statusBar() if self._three_d_window else self.statusBar()
            target.showMessage(self.localizer.text("viewer.copied_3d"), 3000)

    def save_3d_plot_dialog(self) -> None:
        if not self._has_3d_scene():
            return
        publication = self.three_d_export_style() == "publication"
        filters = ("PNG image (*.png);;PDF document (*.pdf);;SVG image (*.svg)" if publication
                   else "PNG image (*.png);;SVG image (*.svg)")
        path, selected = QFileDialog.getSaveFileName(
            self._three_d_window or self, self.localizer.text("viewer.save_3d"),
            f"{self._drag_share_filename(None, False)}.png", filters,
        )
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".pdf" if "PDF" in selected else ".svg" if "SVG" in selected else ".png")
        ok = False
        if publication:
            data = self._render_3d_publication(target.suffix.lower().lstrip("."))
            if data:
                try:
                    target.write_bytes(data)
                    ok = True
                except OSError:
                    ok = False
        else:
            image = self._render_3d_export_image()
            if image is not None:
                ok = (write_raster_svg(image, str(target)) if target.suffix.lower() == ".svg"
                      else write_png(image, str(target)))
        if not ok:
            QMessageBox.warning(self._three_d_window or self, self.localizer.text("viewer.save_3d"),
                                "The 3D plot could not be saved.")

    def _forget_yig_fitting_window(self, identity: str, window_ref) -> None:
        if self._yig_fitting_windows.get(identity) is window_ref():
            self._yig_fitting_windows.pop(identity, None)

    # ---- plot copy and image export ---------------------------------------

    def _is_multi_pane_export(self) -> bool:
        return (
            not self.multi_pane_splitter.isHidden()
            and self._pane_count(self.pane_layout_combo.currentText()) > 1
        )

    def _drag_share_all_panes(self) -> bool:
        return self._is_multi_pane_export() and self.plot_interaction_controls.scope == "all"

    def _set_drag_share_mode(self, enabled: bool) -> None:
        self.plot_interaction_controls.set_share_mode(enabled)
        self.plot_interaction_controls.set_multi_pane_available(self._is_multi_pane_export())

    def set_3d_share_mode(self, enabled: bool) -> None:
        """Pointer (False) or Drag to Share (True) in the 3D Surface window."""
        self._three_d_share_mode = bool(enabled)
        if self.nd_surface_renderer is not None:
            self.nd_surface_renderer._share_mode = self._three_d_share_mode

    def _update_export_action_labels(self) -> None:
        """Keep the native File menu honest about the current pane scope."""
        multi = self._is_multi_pane_export()
        self.copy_plot_action.setText("Copy Active Pane" if multi else "Copy Plot")
        self.save_plot_action.setText("Save Active Pane As..." if multi else "Save Plot As...")
        self.copy_all_panes_action.setEnabled(multi)
        self.save_all_panes_action.setEnabled(multi)
        self.export_animation_action.setEnabled(self.log_entries.row_count() > 1)

    def _register_export_surface(self, surface: QWidget, plot_item, pane_id: int | None = None) -> None:
        """Attach shared shortcuts and augment this plot's native menu."""
        # QAction keeps the command metadata and is shared by menus.  Qt's
        # shortcut listeners live on the actual plot canvases, so Copy in a
        # table or text editor remains that widget's native Copy operation.
        for action in (self.copy_plot_action, self.copy_all_panes_action):
            shortcut = QShortcut(action.shortcut(), surface)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action.trigger)
            self._plot_shortcuts.append(shortcut)
        surface.setContextMenuPolicy(Qt.CustomContextMenu)
        surface.customContextMenuRequested.connect(
            lambda point, widget=surface, item=plot_item, pane=pane_id:
            self._show_plot_context_menu(widget, item, point, pane)
        )
        self._drag_share_controllers.append(
            DragShareController(
                surface, pane_id, self._drag_share_manager,
                self._render_drag_share_payload,
                lambda: self.plot_interaction_controls.share_mode and self._drag_share_is_available(),
                self._drag_share_all_panes,
                self,
            )
        )

    def _show_plot_context_menu(self, surface: QWidget, plot_item, point: QPoint,
                                pane_id: int | None) -> None:
        """Augment pyqtgraph's standard menu for the right-clicked pane.

        The pyqtgraph navigation actions are reused, rather than reimplemented.
        A fresh wrapper menu only prevents repeated right-clicks from
        accumulating duplicate actions while retaining View All, axes, mouse
        mode and plot options.
        """
        menu = self._build_plot_context_menu(surface, plot_item, pane_id)
        menu.exec(surface.mapToGlobal(point))

    def _build_plot_context_menu(self, surface: QWidget, plot_item,
                                 pane_id: int | None) -> QMenu:
        """Build one ephemeral augmented menu for a plot surface.

        Kept separate from ``_show_plot_context_menu`` so the exact command
        composition can be regression tested without simulating a native menu
        event.
        """
        self._update_export_action_labels()
        self._context_export_pane_id = pane_id or self._active_pane_id
        menu = QMenu(surface)
        if self._is_multi_pane_export():
            self.copy_context_action.setText("Copy This Pane")
            self.save_context_action.setText("Save This Pane As...")
            menu.addAction(self.copy_context_action)
            menu.addAction(self.copy_all_panes_action)
            menu.addSeparator()
            menu.addAction(self.save_context_action)
            menu.addAction(self.save_all_panes_action)
        else:
            self.copy_context_action.setText("Copy Plot")
            self.save_context_action.setText("Save Plot As...")
            menu.addAction(self.copy_plot_action)
            menu.addAction(self.save_plot_action)
        menu.addSeparator()
        menu.addAction(self.export_data_context_action)

        # Preserve pyqtgraph's established navigation affordances.  Their
        # QAction instances (and their nested menus) remain pyqtgraph owned,
        # so we do not create competing axis/mouse commands.  LabLogViewer's
        # explicit Save Plot / Export Data commands replace only pyqtgraph's
        # ambiguous generic ``Export...`` action in this wrapper menu.
        view_box = plot_item.getViewBox() if plot_item is not None else None
        if view_box is not None:
            native_menu = view_box.getMenu(None)
            if native_menu is not None:
                menu.addSeparator()
                menu.addActions(native_menu.actions())
        if plot_item is not None:
            plot_options = plot_item.getMenu()
            if plot_options is not None and plot_options.actions():
                menu.addMenu(plot_options)
            scene = plot_item.scene()
            if scene is not None and getattr(scene, "contextMenu", None):
                menu.addActions([
                    action for action in scene.contextMenu
                    if action.text().replace("&", "") != "Export..."
                ])
        return menu

    def _copy_context_pane(self) -> None:
        self._copy_pane(self._context_export_pane_id or self._active_pane_id)

    def _save_context_pane(self) -> None:
        self._save_pane_dialog(self._context_export_pane_id or self._active_pane_id)

    def _single_export_surface(self) -> QWidget | None:
        if self.mode_combo.currentIndex() == 0:
            return self.plot_widget.plot_widget
        if self.mode_combo.currentIndex() == 1:
            return self.plot_2d_widget.graphics_widget
        if self._three_d_active():
            if self.nd_plot_stack.currentIndex() == 2:
                return None
            return (
                self.plot_2d_widget_nd.graphics_widget
                if self.nd_plot_stack.currentIndex() == 1
                else self.plot_widget_nd.plot_widget
            )
        return None

    def _single_export_controller(self):
        mode = self.mode_combo.currentIndex()
        if mode == 0:
            return self.plot_widget
        if mode == 1:
            return self.plot_2d_widget
        if mode == 2:
            if self.nd_plot_stack.currentIndex() == 1:
                return self.plot_2d_widget_nd
            if self.nd_plot_stack.currentIndex() == 0:
                return self.plot_widget_nd
        return None

    def _pane_export_controller(self, pane_id: int):
        frame = self._pane_frames.get(pane_id)
        state = self._pane_states.get(pane_id)
        if frame is None or state is None:
            return None
        return frame.plot_2d if state.plot_mode == 1 else frame.plot_1d

    def _plot_export_context(self, controller):
        manager = self.theme_manager
        if (controller is None or manager is None
                or manager.scientific_plot_appearance == manager.export_plot_background):
            return None
        temporary = getattr(controller, "temporary_scientific_plot_appearance", None)
        if not callable(temporary):
            return None
        return lambda: temporary(manager.export_plot_colors)

    def _export_background_color(self) -> str:
        if self.theme_manager is None:
            return WHITE_PLOT.background
        return self.theme_manager.export_plot_colors.background

    def _pane_export_surface(self, pane_id: int) -> QWidget | None:
        frame = self._pane_frames.get(pane_id)
        state = self._pane_states.get(pane_id)
        if frame is None or state is None:
            return None
        return frame.plot_2d.graphics_widget if state.plot_mode == 1 else frame.plot_1d.plot_widget

    @staticmethod
    def _safe_rect(rect: QRect) -> QRect:
        return QRect(rect.x(), rect.y(), max(1, rect.width()), max(1, rect.height()))

    def _virtual_multi_pane_rects(self, layout_name: str, sizes: list[list[int]], width: int, height: int) -> dict[int, QRect]:
        """Reconstruct logical pane bounds while an active pane is maximized."""
        full = QRect(0, 0, max(1, width), max(1, height))

        def split(rect: QRect, orientation: Qt.Orientation, values: list[int]) -> tuple[QRect, QRect]:
            first, second = (values + [1, 1])[:2]
            total = max(1, first + second)
            if orientation == Qt.Horizontal:
                first_width = max(1, round(rect.width() * first / total))
                return (
                    QRect(rect.x(), rect.y(), first_width, rect.height()),
                    QRect(rect.x() + first_width, rect.y(), max(1, rect.width() - first_width), rect.height()),
                )
            first_height = max(1, round(rect.height() * first / total))
            return (
                QRect(rect.x(), rect.y(), rect.width(), first_height),
                QRect(rect.x(), rect.y() + first_height, rect.width(), max(1, rect.height() - first_height)),
            )

        root = sizes[0] if sizes else [1, 1]
        if layout_name == TWO_SIDE:
            left, right = split(full, Qt.Horizontal, root)
            return {1: left, 2: right}
        if layout_name == TWO_STACKED:
            top, bottom = split(full, Qt.Vertical, root)
            return {1: top, 2: bottom}
        if layout_name == THREE_PANES:
            top, bottom = split(full, Qt.Vertical, root)
            left, right = split(bottom, Qt.Horizontal, sizes[1] if len(sizes) > 1 else [1, 1])
            return {1: top, 2: left, 3: right}
        top, bottom = split(full, Qt.Vertical, root)
        top_left, top_right = split(top, Qt.Horizontal, sizes[1] if len(sizes) > 1 else [1, 1])
        bottom_left, bottom_right = split(bottom, Qt.Horizontal, sizes[2] if len(sizes) > 2 else [1, 1])
        return {1: top_left, 2: top_right, 3: bottom_left, 4: bottom_right}

    def _all_pane_render_surfaces(self) -> tuple[list[PaneRenderSurface], QSize]:
        """Return plot-only pane surfaces and their visible or saved workspace layout."""
        host = self.pane_grid_host
        width, height = max(1, host.width()), max(1, host.height())
        count = self._pane_count(self.pane_layout_combo.currentText())
        if self._multi_pane_maximized and self._plot_layout_state is not None:
            layout_name = self._plot_layout_state.get("pane_layout", self.pane_layout_combo.currentText())
            rects = self._virtual_multi_pane_rects(
                layout_name, self._plot_layout_state.get("pane_sizes", []), width, height
            )
        else:
            rects = {}
            for pane_id in range(1, count + 1):
                surface = self._pane_export_surface(pane_id)
                if surface is None:
                    continue
                position = surface.mapTo(host, QPoint(0, 0))
                rects[pane_id] = self._safe_rect(QRect(position, surface.size()))
        surfaces = []
        for pane_id in range(1, count + 1):
            surface = self._pane_export_surface(pane_id)
            rect = rects.get(pane_id)
            if surface is not None and rect is not None:
                surfaces.append(PaneRenderSurface(
                    surface, self._safe_rect(rect),
                    appearance_context=self._plot_export_context(
                        self._pane_export_controller(pane_id)
                    ),
                ))
        return surfaces, QSize(width, height)

    def _pane_render_surfaces(self, pane_id: int | None = None, *, all_panes: bool = False) -> tuple[list[PaneRenderSurface], QSize]:
        if all_panes and self._is_multi_pane_export():
            return self._all_pane_render_surfaces()
        surface = self._pane_export_surface(pane_id or self._active_pane_id) if self._is_multi_pane_export() else self._single_export_surface()
        if surface is None:
            return [], QSize(1, 1)
        size = QSize(max(1, surface.width()), max(1, surface.height()))
        controller = (self._pane_export_controller(pane_id or self._active_pane_id)
                      if self._is_multi_pane_export() else self._single_export_controller())
        return [PaneRenderSurface(
            surface, QRect(0, 0, size.width(), size.height()),
            appearance_context=self._plot_export_context(controller),
        )], size

    def _drag_share_is_available(self) -> bool:
        """Marks own their placement drag; ordinary plot drags may share."""
        return self.experiment is not None and self._pending_mark_tool is None

    def _drag_share_filename(self, pane_id: int | None, all_panes: bool) -> str:
        """Build a useful destination filename from display-only plot context."""
        if self.experiment is None:
            return "LabLogViewer_plot"
        if all_panes:
            suffix = "AllPanes"
        elif self._is_multi_pane_export() and pane_id is not None:
            suffix = f"Pane{pane_id}"
        else:
            suffix = "Plot"
        channel = ""
        mode = self._export_mode_for_pane(pane_id or self._active_pane_id)
        if self._is_multi_pane_export() and pane_id in self._pane_states:
            state = self._pane_states[pane_id]
            if mode == 1:
                channel = state.z_name
            elif isinstance(state.y_axis, dict):
                channel = str(state.y_axis.get("name") or state.y_axis.get("base_channel") or "")
            else:
                channel = str(state.y_axis or "")
        elif mode == 1:
            channel = self.z_combo_2d.currentText()
        else:
            candidate = self.y_combo.currentData()
            channel = candidate.base_channel if candidate is not None else self.y_combo.currentText()
        # Keep the routing suffix even when a verbose channel display name
        # needs shortening for a desktop file destination.
        prefix = safe_filename("_".join(str(part) for part in (self.experiment.display_name, channel) if part))
        return safe_filename(f"{prefix[:100]}_{suffix}")

    def _has_3d_scene(self) -> bool:
        return (self._three_d_active()
                and self.nd_plot_stack.currentIndex() == 2
                and self.nd_surface_renderer is not None
                and (self.nd_surface_renderer._source_height_grid is not None
                     or self.nd_surface_renderer._point_cloud is not None))

    def _render_3d_export_image(self) -> QImage | None:
        try:
            if not self._has_3d_scene():
                raise ValueError("No 3D scientific view is available for export.")
            image = self.nd_surface_renderer.render_plot_image()
            if image.isNull():
                raise ValueError("The native 3D renderer produced no image.")
            return image
        except Exception as error:
            self.statusBar().showMessage(f"3D export failed: {error}", 5000)
            return None

    def _show_3d_export_menu(self, global_position: QPoint) -> None:
        menu = QMenu(self._three_d_window or self)
        menu.addAction(self.localizer.text("viewer.copy_3d"), self.copy_3d_plot)
        menu.addAction(self.localizer.text("viewer.save_3d"), self.save_3d_plot_dialog)
        menu.exec(global_position)

    def _start_3d_share_drag(self) -> None:
        if not self._drag_share_is_available():
            return
        image = self._render_3d_export_image()
        if image is None:
            return
        filename = self._drag_share_filename(None, False)
        try:
            self._drag_share_manager.start_drag(self.nd_surface_renderer, image, filename)
        except OSError as error:
            self.statusBar().showMessage(f"3D sharing failed: {error}", 5000)

    def _render_drag_share_payload(self, pane_id: int | None, all_panes: bool) -> tuple[object, str] | None:
        """Produce the one lazy graphical payload used by native drag/drop."""
        surfaces, size = self._pane_render_surfaces(pane_id, all_panes=all_panes)
        if not surfaces:
            self.statusBar().showMessage("No plot is available to share.", 3000)
            return None
        image = render_composite_image(surfaces, size, background=self._export_background_color())
        if image.isNull():
            self.statusBar().showMessage("The plot image could not be prepared for sharing.", 3000)
            return None
        self.statusBar().showMessage(
            "Sharing all panes." if all_panes else "Sharing this plot pane.", 2500
        )
        return image, self._drag_share_filename(pane_id, all_panes)

    def _copy_active_pane(self) -> None:
        self._copy_pane(self._active_pane_id)

    def _copy_pane(self, pane_id: int) -> None:
        surfaces, size = self._pane_render_surfaces(pane_id)
        if not surfaces:
            self.statusBar().showMessage("No plot is available to copy.", 3000)
            return
        # Copy and Save deliberately use the same composition renderer.  In
        # particular, direct QWidget.render() at a high-DPI clipboard scale
        # clipped pyqtgraph canvases on macOS while the saved PNG was correct.
        QGuiApplication.clipboard().setImage(render_composite_image(
            surfaces, size, background=self._export_background_color()
        ))
        self.statusBar().showMessage("Plot copied to clipboard.", 3000)

    def _copy_all_panes(self) -> None:
        surfaces, size = self._pane_render_surfaces(all_panes=True)
        if not surfaces:
            self.statusBar().showMessage("No pane workspace is available to copy.", 3000)
            return
        QGuiApplication.clipboard().setImage(render_composite_image(
            surfaces, size, background=self._export_background_color()
        ))
        self.statusBar().showMessage("All panes copied to clipboard.", 3000)

    def _save_active_pane_dialog(self) -> None:
        self._save_pane_dialog(self._active_pane_id)

    def _save_pane_dialog(self, pane_id: int) -> None:
        self._save_export_dialog(pane_id=pane_id, all_panes=False)

    def _save_all_panes_dialog(self) -> None:
        self._save_export_dialog(all_panes=True)

    def _save_export_dialog(self, *, pane_id: int | None = None, all_panes: bool = False) -> None:
        default_name = "all-panes.png" if all_panes else "plot.png"
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Save Plot Image", default_name, "PNG image (*.png);;SVG image (*.svg)"
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".svg" if "SVG" in selected_filter else ".png"
        if self._save_export(path, pane_id=pane_id, all_panes=all_panes):
            self.statusBar().showMessage(f"Saved plot image: {Path(path).name}", 4000)
        else:
            QMessageBox.warning(self, "Save Plot", "The plot image could not be saved.")

    def _save_export(self, path: str, *, pane_id: int | None = None, all_panes: bool = False) -> bool:
        """Destination-independent export seam used by actions, tests, and menus."""
        surfaces, size = self._pane_render_surfaces(pane_id, all_panes=all_panes)
        if not surfaces:
            return False
        target = Path(path)
        if target.suffix.lower() == ".svg":
            return write_svg(surfaces, size, target, background=self._export_background_color())
        return write_png(render_composite_image(
            surfaces, size, background=self._export_background_color()
        ), target)

    # ---- animation export -------------------------------------------------

    def _animation_trace_ids(self, options: AnimationOptions) -> tuple[int, ...]:
        return scope_trace_ids(
            options.trace_scope, self.log_entries.row_count(),
            self.trace_selection.ordered_selection(), self.trace_selection.visible_selection(),
        )

    def _animation_pane_states(self) -> list[tuple[int, PaneState]]:
        """Capture the current semantic pane state before rendering starts."""
        self._capture_active_pane_state()
        if self._is_multi_pane_export():
            return [(pane_id, self._pane_states[pane_id])
                    for pane_id in range(1, self._pane_count(self.pane_layout_combo.currentText()) + 1)]
        return [(1, self._pane_states[1])]

    def _animation_snapshot(self, options: AnimationOptions) -> tuple[tuple[int, ...], tuple[AnimationPane, ...], QSize]:
        """Copy transformed trace arrays so a later UI change cannot affect export."""
        if self.experiment is None or self.cached is None or self.mgr is None:
            raise AnimationExportError("Open compatible data before exporting an animation.")
        trace_ids = self._animation_trace_ids(options)
        if len(trace_ids) < 2:
            raise AnimationExportError(f"{options.trace_scope} needs at least two valid traces.")
        surfaces, size = self._pane_render_surfaces(all_panes=True)
        rects = {pane_id: surface.target for pane_id, surface in zip(
            range(1, len(surfaces) + 1), surfaces
        )}
        panes: list[AnimationPane] = []
        for pane_id, state in self._animation_pane_states():
            target = rects.get(pane_id, QRect(0, 0, size.width(), size.height()))
            if state.plot_mode == 1:
                if not (state.z_name and state.grid_x_name and state.grid_y_name):
                    raise AnimationExportError("One animation pane has no compatible 2D grid.")
                try:
                    source_grid = self.cached.get_2d_data(
                        state.grid_x_name, state.grid_y_name, state.z_name,
                        transform=state.grid_transform,
                    )
                except Exception as error:
                    raise AnimationExportError(f"Cannot snapshot 2D pane {pane_id}: {error}") from error
                vector_grid = (
                    state.z_name in self.experiment.vector_traces
                    and source_grid.z_values.ndim == 2
                    and source_grid.z_values.shape[0] == self.log_entries.row_count()
                )
                if not vector_grid:
                    raise AnimationExportError(
                        f"2D pane {pane_id} cannot map its sweep entries to grid rows unambiguously."
                    )
                # Keep the exact measured surface and its orientation; the
                # animation renderer must never rebuild it from a later Viewer
                # selection or synthesize a different grid.
                grid = Grid2DData(
                    x_values=np.asarray(source_grid.x_values, dtype=float).copy(),
                    y_values=np.asarray(source_grid.y_values, dtype=float).copy(),
                    z_values=np.asarray(source_grid.z_values, dtype=float).copy(),
                    x_name=source_grid.x_name, x_unit=source_grid.x_unit,
                    y_name=source_grid.y_name, y_unit=source_grid.y_unit,
                    z_name=source_grid.z_name, z_unit=source_grid.z_unit,
                    transform=source_grid.transform, fixed_dims=dict(source_grid.fixed_dims),
                    acquisition=source_grid.acquisition,
                )
                z_range = (
                    (float(state.color_min), float(state.color_max))
                    if not state.auto_color and np.isfinite(state.color_min) and np.isfinite(state.color_max)
                    and state.color_min != state.color_max
                    else finite_global_range((grid.z_values[list(trace_ids), :],))
                )
                panes.append(AnimationPane(
                    target=target, title=f"{grid.z_name}  ({grid.y_name} × {grid.x_name})",
                    kind="2d", grid=grid, colormap=state.colormap or DEFAULT_COLORMAP,
                    z_range=z_range, reveal_trace_ids=trace_ids,
                ))
                continue
            if state.plot_mode != 0:
                raise AnimationExportError("Animation supports only compatible 1D or 2D panes.")
            x_cand = self._candidate_from_ref(state.x_axis)
            y_cand = self._candidate_from_ref(state.y_axis)
            if x_cand is None or y_cand is None or x_cand.domain != "points" or y_cand.domain != "points":
                raise AnimationExportError("Animation requires point-domain 1D axes in every pane.")
            if not self.mgr.axis_domains_compatible(x_cand, y_cand):
                raise AnimationExportError("One animation pane has incompatible axes.")
            frames: dict[int, np.ndarray] = {}
            x_reference: np.ndarray | None = None
            for trace in trace_ids:
                x_values, y_values, _ = self._pane_plot_arrays(x_cand, y_cand, trace, state)
                x_values = np.asarray(x_values, dtype=float).copy()
                y_values = np.asarray(y_values, dtype=float).copy()
                if x_values.size != y_values.size:
                    raise AnimationExportError(
                        f"Trace {trace + 1} in the selected scope has mismatched X/Y sample counts."
                    )
                if x_reference is None:
                    x_reference = x_values
                elif x_reference.shape != x_values.shape or not np.allclose(x_reference, x_values, equal_nan=True):
                    raise AnimationExportError("Animation requires one stable X axis across selected traces.")
                frames[trace] = y_values
            valid_ids = tuple(trace for trace in trace_ids if trace in frames)
            if len(valid_ids) != len(trace_ids):
                missing = next(trace for trace in trace_ids if trace not in frames)
                raise AnimationExportError(
                    f"Trace {missing + 1} in the selected scope has no complete displayed frame."
                )
            if len(valid_ids) < 2 or x_reference is None:
                raise AnimationExportError("Fewer than two selected traces contain valid displayed samples.")
            if valid_ids != trace_ids:
                raise AnimationExportError("Animation trace scope changed while creating its snapshot.")
            x_range = finite_global_range((x_reference,))
            y_range = finite_global_range(frames.values())
            transform = self._pane_transform_spec(state)
            suffix = transform.name + (" (unwrapped)" if transform.unwrap else "")
            y_label = f"{y_cand.name} — {suffix}" if suffix else y_cand.name
            panes.append(AnimationPane(
                target=target, title=f"{y_cand.name} vs {x_cand.name}",
                x_values=x_reference, frames=frames,
                x_label=f"{x_cand.name} [{x_cand.unit or '-'}]",
                y_label=y_label,
                x_range=x_range, y_range=y_range,
            ))
        if not panes:
            raise AnimationExportError("No compatible plot panes are available for animation.")
        dynamic_panes = [pane for pane in panes if pane.kind == "1d"]
        common = tuple(trace for trace in trace_ids if all(trace in pane.frames for pane in dynamic_panes))
        if len(common) < 2:
            raise AnimationExportError("Animation panes do not share two valid trace identities.")
        return common, tuple(panes), size

    def _animation_overlay_lines(self, trace: int, options: AnimationOptions) -> tuple[str, ...]:
        if options.format == GIF:
            return ()
        lines: list[str] = []
        if options.show_trace_number:
            lines.append(f"Trace {trace + 1} / {self.log_entries.row_count()}")
        if options.show_sweep_parameter:
            values = self._trace_sweep_values(trace)
            if values:
                name, value = next(iter(values.items()))
                unit = next((axis.channel.unit for axis in self.experiment.step_axes
                             if axis.channel.name == name), None)
                try:
                    value_text = f"{float(value):.6g}"
                except (TypeError, ValueError):
                    value_text = str(value)
                lines.append(f"{name} = {value_text} {unit or ''}".rstrip())
        return tuple(lines)

    def _export_animation_dialog(self) -> None:
        if self.log_entries.row_count() <= 1:
            self.statusBar().showMessage("Animation export needs at least two traces.", 4000)
            return
        dialog = AnimationExportDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        options = dialog.options()
        if not encoder_available(options.format):
            QMessageBox.warning(self, "Export Animation", f"{options.format} encoding is unavailable in this installation.")
            return
        extension = ".gif" if options.format == GIF else ".mp4"
        default_name = safe_filename(f"{self.experiment.display_name if self.experiment else 'LabLogViewer'}_animation") + extension
        path, _ = QFileDialog.getSaveFileName(self, "Export Animation", default_name,
                                               "GIF animation (*.gif)" if options.format == GIF else "MP4 video (*.mp4)")
        if not path:
            return
        output_path = Path(path)
        if output_path.suffix != extension:
            output_path = output_path.with_suffix(extension)
        try:
            trace_ids, panes, size = self._animation_snapshot(options)
        except AnimationExportError as error:
            QMessageBox.warning(self, "Export Animation", str(error))
            return
        self._start_animation_export(output_path, options, trace_ids, panes, size)

    def _start_animation_export(self, path: Path, options: AnimationOptions,
                                trace_ids: tuple[int, ...], panes: tuple[AnimationPane, ...], size: QSize) -> None:
        frame_ids = gif_frame_ids(trace_ids) if options.format == GIF else trace_ids
        if options.format == GIF:
            target_size = target_frame_size(size.width(), size.height(), "1080p")
            duration = GIF_FRAME_DURATION_SECONDS
            fps = 100
        else:
            target_size = target_frame_size(size.width(), size.height(), options.resolution)
            duration, fps = 1 / options.traces_per_second, options.traces_per_second
        session = AnimationRenderSession(panes, size)
        worker = AnimationEncodeWorker(
            path, options.format, fps=fps, duration=duration,
            expected_frames=len(frame_ids),
        )
        progress = QProgressDialog("Preparing animation…", "Cancel", 0, len(frame_ids), self)
        progress.setWindowTitle("Export Animation")
        progress.setAutoClose(False)
        progress.setMinimumDuration(0)
        progress.show()
        worker.start()
        job: dict[str, object] = {"session": session, "worker": worker, "progress": progress,
                                  "frames": frame_ids, "index": 0, "options": options,
                                  "target_size": target_size, "timer": None}
        self._animation_jobs.append(job)
        timer = QTimer(self)
        job["timer"] = timer

        def cancel() -> None:
            worker.cancel()
            timer.stop()
            worker.finish()

        def complete() -> None:
            if not worker.finished.is_set():
                QTimer.singleShot(50, complete)
                return
            session.close()
            progress.close()
            if job in self._animation_jobs:
                self._animation_jobs.remove(job)
            if worker.error:
                QMessageBox.warning(self, "Export Animation", f"Animation encoding failed: {worker.error}")
            elif worker.cancelled.is_set():
                self.statusBar().showMessage("Animation export cancelled.", 4000)
            else:
                if options.format == GIF:
                    total_duration = gif_duration_seconds(len(frame_ids))
                    self.statusBar().showMessage(
                        f"GIF exported: {len(trace_ids)} traces, "
                        f"{worker.validated_frame_count or worker.frames_written} frames, "
                        f"10 ms/frame, {total_duration:.2f} s total", 8000,
                    )
                else:
                    self.statusBar().showMessage(f"Animation exported: {path.name}", 5000)

        def step() -> None:
            if worker.cancelled.is_set() or worker.error:
                timer.stop()
                worker.finish()
                complete()
                return
            index = int(job["index"])
            if index >= len(frame_ids):
                timer.stop()
                worker.finish()
                complete()
                return
            trace = frame_ids[index]
            try:
                image = session.render(trace, target_size=target_size,
                                       overlay_lines=self._animation_overlay_lines(trace, options))
            except Exception as error:
                worker.error = str(error)
                worker.cancel()
                timer.stop()
                worker.finish()
                complete()
                return
            if not worker.submit(image):
                return
            job["index"] = index + 1
            progress.setValue(index + 1)
            progress.setLabelText(f"Rendering frame {index + 1} / {len(frame_ids)}")

        progress.canceled.connect(cancel)
        timer.timeout.connect(step)
        timer.start(0)

    # ---- numerical scientific data export ---------------------------------

    def _export_active_pane_data(self) -> None:
        """File/menu export: the established command target is Active Pane."""
        self._open_data_export_dialog(self._active_pane_id)

    def _export_context_pane_data(self) -> None:
        """Plot context export: the pointer pane is the command target."""
        self._open_data_export_dialog(self._context_export_pane_id or self._active_pane_id)

    def _export_mode_for_pane(self, pane_id: int) -> int:
        if self._is_multi_pane_export() and pane_id in self._pane_states:
            return self._pane_states[pane_id].plot_mode
        return self.mode_combo.currentIndex()

    def _export_visible_x_range(self, pane_id: int, mode: int) -> tuple[float, float] | None:
        try:
            if self._is_multi_pane_export():
                frame = self._pane_frames.get(pane_id)
                if frame is None:
                    return None
                view_box = frame.plot_2d.view_box if mode == 1 else frame.plot_1d.plot_widget.getViewBox()
            elif mode == 1:
                view_box = self.plot_2d_widget.view_box
            else:
                view_box = self.plot_widget.plot_widget.getViewBox()
            values = view_box.viewRange()[0]
            return float(min(values)), float(max(values))
        except Exception:
            return None

    def _export_base_metadata(self, pane_id: int, options: ExportOptions) -> dict[str, object]:
        assert self.experiment is not None
        return {
            "application": "LabLogViewer",
            "application_version": __version__,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "data_identity": self.experiment.data_identity,
                "display_name": self.experiment.display_name,
                "path": str(self.experiment.source_path),
            },
            "pane_id": int(pane_id),
            "scope": options.scope,
            "representation": options.representation,
            "range": {"kind": options.range_kind},
            "partial_acquisition": False,
        }

    def _open_data_export_dialog(self, pane_id: int) -> None:
        if self.experiment is None or self.cached is None:
            self.statusBar().showMessage("Open a Labber log before exporting data.", 3000)
            return
        mode = self._export_mode_for_pane(pane_id)
        if mode not in (0, 1):
            QMessageBox.information(self, "Export Data", "The current N-D view has no export context yet.")
            return
        channel = self._export_channel_name(pane_id, mode) or "data"
        suggested = safe_filename(
            f"{self.experiment.display_name}_{channel}_{'2d' if mode == 1 else '1d'}"
        ) + ".csv"
        dialog = DataExportDialog(
            self, is_2d=mode == 1, default_path=suggested,
            visible_range_available=self._export_visible_x_range(pane_id, mode) is not None,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        options = dialog.options()
        if not options.destination:
            return
        target = Path(options.destination)
        if not target.suffix:
            target = target.with_suffix(f".{options.format}")
        if target.exists() and str(target) != dialog.confirmed_path:
            from app.gui.save_target import ask_existing

            chosen = ask_existing(self, target)
            if chosen is None:
                return
            target = chosen
        try:
            dataset = self._build_export_dataset(pane_id, mode, options)
            if options.format == "npz":
                write_npz(target, dataset)
            else:
                write_csv(target, dataset)
        except Exception as error:
            QMessageBox.warning(self, "Export Data", f"The data could not be exported:\n\n{error}")
            return
        self.statusBar().showMessage(f"Exported scientific data: {target.name}", 5000)

    def _export_channel_name(self, pane_id: int, mode: int) -> str | None:
        if self._is_multi_pane_export() and pane_id in self._pane_states:
            state = self._pane_states[pane_id]
            return state.z_name if mode == 1 else (
                self._candidate_from_ref(state.y_axis).base_channel if self._candidate_from_ref(state.y_axis) else None
            )
        if mode == 1:
            return self.z_combo_2d.currentText() or None
        candidate = self.y_combo.currentData()
        return candidate.base_channel if candidate is not None else None

    def _export_1d_context(self, pane_id: int) -> tuple[AxisCandidate, AxisCandidate, TransformSpec, int]:
        if self._is_multi_pane_export() and pane_id in self._pane_states:
            state = self._pane_states[pane_id]
            x_cand = self._candidate_from_ref(state.x_axis)
            y_cand = self._candidate_from_ref(state.y_axis)
            if x_cand is None or y_cand is None:
                raise ValueError("The selected pane has no compatible 1D axes.")
            active = self.log_entries.current_row() if self.sync_trace_checkbox.isChecked() else state.trace_index
            return x_cand, y_cand, self._pane_transform_spec(state), int(active)
        x_cand, y_cand = self.x_combo.currentData(), self.y_combo.currentData()
        if x_cand is None or y_cand is None:
            raise ValueError("Choose both a 1D X Axis and Y Axis before exporting.")
        return x_cand, y_cand, self._current_transform_spec(), self.log_entries.current_row()

    @staticmethod
    def _export_transform_key(candidate: AxisCandidate, spec: TransformSpec,
                              representation: str) -> str:
        if representation == RAW:
            return "raw"
        if candidate.is_complex:
            return spec.resolve_transform_key()
        if candidate.source == "derived":
            key = candidate.transform_key or "raw"
            if key == "magnitude" and spec.db:
                return "magnitude_db"
            return key
        return "raw"

    def _export_trace_indices(self, scope: str, active: int, points_domain: bool) -> tuple[int, ...]:
        if scope == FULL_DATA or not points_domain:
            return (int(active),)
        if scope == ACTIVE_TRACE:
            return (int(active),)
        if scope == SELECTED_TRACES:
            return self.trace_selection.ordered_selection() or (int(active),)
        if scope == VISIBLE_TRACES:
            return self.trace_selection.visible_selection() or (int(active),)
        return (int(active),)

    def _trace_sweep_values(self, trace: int) -> dict[str, object]:
        """Recorded per-entry step values make exported trace rows traceable."""
        if self.cached is None or self.experiment is None:
            return {}
        result: dict[str, object] = {}
        for axis in self.experiment.step_axes:
            try:
                values = np.asarray(self.cached.get_data(axis.channel.name, transform="raw")).reshape(-1)
                if 0 <= trace < values.size:
                    result[axis.channel.name] = values[trace].item() if isinstance(values[trace], np.generic) else values[trace]
            except Exception:
                continue
        return result

    def _export_1d_trace_arrays(self, pane_id: int, x_cand: AxisCandidate,
                                 y_cand: AxisCandidate, spec: TransformSpec,
                                 trace: int, representation: str) -> tuple[np.ndarray, np.ndarray, str]:
        if self.cached is None:
            raise ValueError("No cached experiment is available.")
        if self._is_multi_pane_export():
            state = self._pane_states[pane_id]
            x_values, displayed_values, displayed_transform = self._pane_plot_arrays(
                x_cand, y_cand, trace, state,
                apply_formula=representation == DISPLAYED,
            )
        else:
            x_values, displayed_values, displayed_transform, _ = self._plot_arrays_for_entry(
                x_cand, y_cand, trace, spec,
                apply_formula=representation == DISPLAYED,
            )
        if representation == DISPLAYED:
            return np.asarray(x_values), np.asarray(displayed_values), displayed_transform
        if y_cand.domain == "points":
            raw_values = np.asarray(
                self.cached.get_data(y_cand.base_channel, transform="raw", entry_slice=trace)
            ).reshape(-1)
        else:
            raw_values = np.asarray(self.cached.get_data(y_cand.base_channel, transform="raw")).reshape(-1)
        if representation == RAW:
            return np.asarray(x_values), raw_values, "raw"
        transform = self._export_transform_key(y_cand, spec, CURRENT_TRANSFORM)
        values = np.asarray(self.cached.get_data(
            y_cand.base_channel, transform=transform,
            entry_slice=trace if y_cand.domain == "points" else None,
        )).reshape(-1)
        if spec.unwrap and transform in ("phase_deg", "phase_rad"):
            values = unwrap_phase(values, unit="deg" if transform == "phase_deg" else "rad")
        return np.asarray(x_values), values, transform

    def _build_1d_trace_export(self, pane_id: int, options: ExportOptions) -> ExportDataset:
        assert self.experiment is not None
        x_cand, y_cand, spec, active = self._export_1d_context(pane_id)
        if not self.mgr.axis_domains_compatible(x_cand, y_cand):
            raise ValueError("The selected 1D axes are incompatible.")
        traces = self._export_trace_indices(options.scope, active, x_cand.domain == y_cand.domain == "points")
        visible = self._export_visible_x_range(pane_id, 0) if options.range_kind == VISIBLE_X_RANGE else None
        x_label = f"{x_cand.name}_{x_cand.unit}" if x_cand.unit else x_cand.name
        rows: list[ExportColumn] = []
        trace_arrays: dict[str, np.ndarray] = {}
        trace_metadata: list[dict[str, object]] = []
        pieces: list[tuple[int, np.ndarray, np.ndarray, str]] = []
        for trace in traces:
            x_values, values, transform = self._export_1d_trace_arrays(
                pane_id, x_cand, y_cand, spec, trace, options.representation
            )
            if visible is not None:
                mask = np.isfinite(x_values) & (x_values >= visible[0]) & (x_values <= visible[1])
                x_values, values = x_values[mask], values[mask]
            pieces.append((trace, x_values, values, transform))
            trace_arrays[f"trace_{trace + 1}_x"] = x_values
            trace_arrays[f"trace_{trace + 1}_values"] = values
            trace_metadata.append({
                "index": trace,
                "reference": trace == self.trace_selection.reference_trace,
                "sweep_values": self._trace_sweep_values(trace),
            })
        transform_name = pieces[0][3] if pieces else "raw"
        value_label = (
            y_cand.base_channel if options.representation == RAW
            else f"{y_cand.base_channel}_{TRANSFORM_LABELS.get(transform_name, transform_name)}"
        )
        multi = len(pieces) > 1
        for trace, x_values, values, _transform in pieces:
            count = x_values.size
            if multi:
                rows.append(ExportColumn("trace", "Trace", np.full(count, trace + 1, dtype=int)))
                rows.append(ExportColumn("reference", "Reference", np.full(count, trace == self.trace_selection.reference_trace, dtype=bool)))
                for name, value in self._trace_sweep_values(trace).items():
                    label = f"{name}_{next((axis.channel.unit for axis in self.experiment.step_axes if axis.channel.name == name), None) or ''}".rstrip("_")
                    rows.append(ExportColumn(f"sweep_{name}", label, np.full(count, value)))
            rows.append(ExportColumn("x", x_label, x_values))
            rows.extend(complex_columns(value_label, values))
        # Long-form CSV needs one physical column per concept. Concatenate
        # per-trace chunks rather than creating duplicate header names.
        if multi:
            ordered_keys: list[tuple[str, str]] = []
            for column in rows:
                identity = (column.key, column.label)
                if identity not in ordered_keys:
                    ordered_keys.append(identity)
            columns = []
            for key, label in ordered_keys:
                values = [column.values for column in rows if (column.key, column.label) == (key, label)]
                columns.append(ExportColumn(key, label, np.concatenate(values)))
        else:
            columns = rows
        metadata = self._export_base_metadata(pane_id, options)
        metadata.update({
            "mode": "1d",
            "channel": y_cand.base_channel,
            "x_axis": {"name": x_cand.name, "unit": x_cand.unit},
            "transform": transform_name,
            "unwrap": bool(spec.unwrap and options.representation != RAW),
            "traces": trace_metadata,
            "x_column_key": "x",
        })
        return ExportDataset(columns, trace_arrays, metadata)

    def _build_full_data_export(self, pane_id: int, options: ExportOptions) -> ExportDataset:
        assert self.experiment is not None and self.cached is not None
        x_cand, y_cand, spec, _active = self._export_1d_context(pane_id)
        transform = self._export_transform_key(y_cand, spec, options.representation)
        dimensions, values = self.cached.get_full_nd_array(y_cand.base_channel, transform=transform)
        values = np.asarray(values)
        if spec.unwrap and options.representation != RAW and transform in ("phase_deg", "phase_rad") and values.ndim:
            flattened = values.reshape(values.shape[0], -1)
            values = np.column_stack([
                unwrap_phase(flattened[:, index], unit="deg" if transform == "phase_deg" else "rad")
                for index in range(flattened.shape[1])
            ]).reshape(values.shape)
        visible = self._export_visible_x_range(pane_id, 0) if options.range_kind == VISIBLE_X_RANGE else None
        if visible is not None:
            matching = next((index for index, dimension in enumerate(dimensions)
                             if dimension.name == x_cand.name), None)
            if matching is not None:
                axis = np.asarray(dimensions[matching].values)
                mask = np.isfinite(axis) & (axis >= visible[0]) & (axis <= visible[1])
                values = np.take(values, np.flatnonzero(mask), axis=matching)
                dimension = dimensions[matching]
                from app.core.data_model import Dimension
                dimensions[matching] = Dimension(
                    dimension.name, dimension.unit, int(np.count_nonzero(mask)), axis[mask],
                    dimension.is_uniform, dimension.step,
                )
        metadata = self._export_base_metadata(pane_id, options)
        metadata.update({"channel": y_cand.base_channel, "transform": transform,
                         "unwrap": bool(spec.unwrap and options.representation != RAW)})
        return nd_dataset(
            dimensions=dimensions, values=values, value_name=y_cand.base_channel,
            value_unit=self._transformed_unit(transform, y_cand.unit), metadata=metadata,
        )

    def _build_2d_export(self, pane_id: int, options: ExportOptions) -> ExportDataset:
        assert self.cached is not None
        if self._is_multi_pane_export() and pane_id in self._pane_states:
            state = self._pane_states[pane_id]
            z_name, x_name, y_name = state.z_name, state.grid_x_name, state.grid_y_name
            transform = state.grid_transform
        else:
            z_name, x_name, y_name = self.z_combo_2d.currentText(), self.x_combo_2d.currentText(), self.y_combo_2d.currentText()
            transform = self.transform_combo_2d.currentData() or "raw"
        if not all((z_name, x_name, y_name)):
            raise ValueError("Choose valid 2D Z, X and Y axes before exporting.")
        grid_transform = "raw" if options.representation == RAW else transform
        grid = self.cached.get_2d_data(x_name, y_name, z_name, transform=grid_transform)
        x_values, z_values = np.asarray(grid.x_values), np.asarray(grid.z_values)
        visible = self._export_visible_x_range(pane_id, 1) if options.range_kind == VISIBLE_X_RANGE else None
        if visible is not None:
            mask = np.isfinite(x_values) & (x_values >= visible[0]) & (x_values <= visible[1])
            x_values, z_values = x_values[mask], z_values[:, mask]
        metadata = self._export_base_metadata(pane_id, options)
        metadata.update({"channel": z_name, "transform": grid_transform,
                         "unwrap": False, "color_range_influences_values": False,
                         "partial_acquisition": bool(grid.acquisition and grid.acquisition.is_partial)})
        return long_grid_dataset(
            x_values=x_values, y_values=grid.y_values, z_values=z_values,
            x_name=grid.x_name, x_unit=grid.x_unit, y_name=grid.y_name, y_unit=grid.y_unit,
            z_name=grid.z_name, z_unit=self._transformed_unit(grid_transform, grid.z_unit),
            metadata=metadata,
        )

    def _build_export_dataset(self, pane_id: int, mode: int, options: ExportOptions) -> ExportDataset:
        if mode == 1:
            return self._build_2d_export(pane_id, options)
        if options.scope == FULL_DATA:
            return self._build_full_data_export(pane_id, options)
        return self._build_1d_trace_export(pane_id, options)

    # ---- independent Cut windows -------------------------------------------

    def _cut_source_context(self) -> tuple[Plot2DWidget | None, str | None, int | None]:
        """Return one Viewer-owned Cut source and its display-only context."""
        display_name = self.experiment.log_name if self.experiment is not None else None
        if not self.multi_pane_splitter.isHidden():
            frame = self._pane_frames.get(self._active_pane_id)
            state = self._pane_states.get(self._active_pane_id)
            if frame is not None and state is not None and state.plot_mode == 1:
                pane = self._active_pane_id if self._pane_count(self.pane_layout_combo.currentText()) > 1 else None
                return (frame.plot_2d if frame.plot_2d._grid is not None else None), display_name, pane
            return None, display_name, None
        if self.mode_combo.currentIndex() == 1:
            return (self.plot_2d_widget if self.plot_2d_widget._grid is not None else None), display_name, None
        return None, display_name, None

    def _cut_source(self) -> Plot2DWidget | None:
        return self._cut_source_context()[0]

    def _refresh_cut_windows(self) -> None:
        source, display_name, pane_id = self._cut_source_context()
        for window in self._cut_windows.values():
            window.set_source_context(display_name if source is not None else None, pane_id)
        if source is None or source._grid is None:
            for window in self._cut_windows.values():
                window.set_grid(None)
            return
        position = source.get_last_hover_or_default()
        for window in self._cut_windows.values():
            if position is not None:
                window.update_source(source._grid, position[0], position[1], force_position=True)
            else:
                window.set_grid(source._grid)

    def _on_cut_source_hover(self, source: Plot2DWidget, x: float, y: float, _z: float) -> None:
        if source is not self._cut_source():
            return
        for window in self._cut_windows.values():
            window.update_source(source._grid, x, y)

    def _show_cut_window(self, cut_axis: str) -> None:
        self._refresh_cut_windows()
        window = self._cut_windows[cut_axis]
        window.show()
        window.raise_()
        window.activateWindow()
        self.workspace_state_changed.emit()

    def _clear_cut_windows(self) -> None:
        for window in self._cut_windows.values():
            window.set_grid(None)

    def _report_partial_acquisition(self, data) -> None:
        """Keep valid incomplete measurements visible without overstating them."""
        status = getattr(data, "acquisition", None)
        if status is not None and status.is_partial:
            self.statusBar().showMessage(
                f"Partial acquisition: {status.acquired_entries} / {status.nominal_entries} "
                f"sweeps shown ({status.reason})."
            )

    def _sync_annotation_cut_position(self, mode: int, annotation) -> None:
        """Route a 2D Mark Crosshair through the cached Cut position source."""
        if mode != 1 or getattr(annotation, "annotation_type", None) != CROSSHAIR:
            return
        source = self._cut_source()
        if source is not None:
            source.set_crosshair_position(annotation.x, annotation.y)

    def _reload_current_file(self) -> None:
        if self.experiment is not None:
            self.open_file(str(self.experiment.source_path))

    def reload_keeping_view(self) -> bool:
        """Read the open file again (it grew: a running measurement) and keep the view:
        display, traces, panes and zoom. A file that cannot be read right now (still being
        written) keeps the data already shown; auto refresh tries again later."""
        if self.experiment is None:
            return False
        path = str(self.experiment.source_path)
        state = self.session_state()
        try:
            from app.core.labber_parser import load_experiment

            fresh = load_experiment(path)
        except Exception:
            return False
        previous = self.experiment.data_identity
        self.show_experiment(fresh, previous_identity=previous, live=True)
        if isinstance(state, dict):
            self.restore_session_state(state)
        return True

    def _show_trace_manager(self) -> None:
        self._trace_manager_requested = True
        self._refresh_multi_trace_panel()
        self.multi_trace_panel.show()
        self.trace_management_splitter.setSizes([max(self.trace_management_splitter.sizes()[0], 520), 320])
        self._restore_trace_area_ratio(self._trace_area_ratio)

    def _trace_area_splitter(self) -> QSplitter:
        """The current parent of Log Entries, independent from pane splitters."""
        return (
            self.multi_pane_splitter
            if self.trace_management_splitter.parent() is self.multi_pane_splitter
            else self.plot_1d_splitter
        )

    def _current_trace_area_ratio(self) -> float:
        sizes = self._trace_area_splitter().sizes()
        if len(sizes) != 2 or sum(sizes) <= 0:
            return self._trace_area_ratio
        return max(0.0, min(0.95, float(sizes[1]) / float(sum(sizes))))

    def _remember_trace_area_ratio(self) -> None:
        """Remember only a non-collapsed choice; zero means deliberately hidden."""
        ratio = self._current_trace_area_ratio()
        if ratio > 0.01:
            self._trace_area_ratio = ratio

    def _restore_trace_area_ratio(self, ratio: float | None = None) -> None:
        """Restore a proportional splitter size without mutating trace state."""
        ratio = self._trace_area_ratio if ratio is None else ratio
        ratio = max(0.02, min(float(ratio), 0.75))
        splitter = self._trace_area_splitter()
        total = max(2, sum(splitter.sizes()), splitter.height())
        trace = max(1, round(total * ratio))
        splitter.setSizes([max(1, total - trace), trace])
        self._trace_area_ratio = ratio

    # ---- named scientific Views -------------------------------------------

    def _portable_mark_context(self, context: object) -> object:
        """The Mark context with this computer's key for the Data replaced by a placeholder,
        so another computer showing the same measurement (Network Workspace) can match it."""
        if isinstance(context, tuple) and context and self.experiment is not None \
                and context[0] in (self._current_data_key(), str(self.experiment.source_path)):
            return ("@data", *context[1:])
        return context

    def marks_payload(self, portable: bool = False) -> dict[str, dict[str, object]]:
        """Every pane's Marks and annotations (named views; Network Workspace when portable)."""
        marks: dict[str, dict[str, object]] = {}
        for pane_id, pane_state in self._pane_states.items():
            pane_marks: dict[str, object] = {}
            for mode, manager in pane_state.mark_managers.items():
                if manager.context_key is None:
                    continue
                context = self._portable_mark_context(manager.context_key) if portable else manager.context_key
                pane_marks[str(mode)] = {
                    "context": self._mark_context_signature(context),
                    "state": manager.persistence_state(),
                    "selected_id": manager.selected_id,
                    "half_peak": {
                        annotation.object_id: {
                            key: getattr(annotation.half_peak, key)
                            for key in ("extremum_x", "extremum_value", "baseline", "half_level",
                                        "left_crossing", "right_crossing", "width", "sample_index")
                        }
                        for annotation in manager.annotations()
                        if annotation.half_peak is not None
                    },
                }
            marks[str(pane_id)] = pane_marks
        return marks

    def apply_marks_payload(self, payload: object, portable: bool = False) -> None:
        """Show Marks sent by another computer (portable contexts); see marks_payload."""
        self._apply_named_view_marks(payload, portable=portable)

    def _named_view_payload(self) -> dict[str, object] | None:
        """Capture a Data-bound scientific view without duplicating other stores."""
        state = self.session_state()
        if state is None:
            return None
        marks = self.marks_payload()
        cuts = state.get("windows", {}).get("cuts", {}) if isinstance(state.get("windows"), dict) else {}
        analysis = {
            "region": self.analysis_region_combo.currentText(),
            "operation": self.analysis_operation_combo.currentText(),
            "window": self.analysis_window_spin.value(),
            "start_mark": self.analysis_start_mark_combo.currentData(),
            "end_mark": self.analysis_end_mark_combo.currentData(),
            "target": self._analysis_target_id,
        }
        return {
            "preset_schema": 2,
            "data_identity": self.experiment.data_identity,
            "display": state.get("display"),
            "trace_selection": state.get("trace_selection"),
            "pane": state.get("pane"),
            "splitters": {
                "main": self.main_splitter.sizes(),
                "right": self.right_splitter.sizes(),
                "plot_1d": self.plot_1d_splitter.sizes(),
                "plot_2d": self.plot_2d_splitter.sizes(),
                "trace_area_ratio": self._current_trace_area_ratio(),
            },
            "marks": marks,
            "show_mark_values": self.show_mark_values_checkbox.isChecked(),
            "analysis": analysis,
            "cuts": cuts,
            "overlay_name": self.saved_overlay_combo.currentText(),
        }

    @staticmethod
    def _mark_context_signature(context: object) -> str:
        import json
        return json.dumps(context, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)

    def _populate_view_preset_combo(self, selected: str | None = None) -> None:
        if not hasattr(self, "view_preset_combo"):
            return
        identity = self._current_data_key()
        names = self.named_view_store.list_names(identity) if identity else []
        previous = self.view_preset_combo.currentData() if selected is None else selected
        self.view_preset_combo.blockSignals(True)
        self.view_preset_combo.clear()
        self.view_preset_combo.addItem("Select Preset", userData=None)
        for name in names:
            self.view_preset_combo.addItem(name, userData=name)
        index = self.view_preset_combo.findData(previous)
        self.view_preset_combo.setCurrentIndex(index if index >= 0 else 0)
        self.view_preset_combo.blockSignals(False)
        self._update_view_preset_controls()

    def _update_view_preset_controls(self) -> None:
        selected = bool(getattr(self, "view_preset_combo", None)
                        and self.view_preset_combo.currentData())
        has_data = self.experiment is not None
        self.save_view_preset_button.setEnabled(has_data)
        self.update_view_preset_button.setEnabled(has_data and selected)
        self.delete_view_preset_button.setEnabled(has_data and selected)

    def _on_view_preset_selected(self, _index: int) -> None:
        self._update_view_preset_controls()
        name = self.view_preset_combo.currentData()
        if isinstance(name, str):
            self._load_named_view(name)

    def _save_view_preset(self) -> None:
        if self.experiment is None:
            return
        name, accepted = QInputDialog.getText(self, "Save View Preset", "Preset name:")
        if not accepted or not name.strip():
            return
        payload = self._named_view_payload()
        if payload is None:
            return
        identity = self.experiment.data_identity
        name = name.strip()
        try:
            self.named_view_store.save(identity, name, payload)
        except ValueError:
            if QMessageBox.question(
                self, "Replace View Preset?",
                f"A View Preset named '{name}' already exists for this Data. Replace it?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return
            try:
                self.named_view_store.save(identity, name, payload, overwrite=True)
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Save View Preset", str(error))
                return
        except OSError as error:
            QMessageBox.warning(self, "Save View Preset", str(error))
            return
        self._populate_view_preset_combo(name)
        self.statusBar().showMessage(f"Saved View Preset: {name}", 3500)

    def _update_view_preset(self) -> None:
        identity = self._current_data_key()
        name = self.view_preset_combo.currentData()
        payload = self._named_view_payload()
        if not identity or not isinstance(name, str) or payload is None:
            return
        try:
            self.named_view_store.save(identity, name, payload, overwrite=True)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Update View Preset", str(error))
            return
        self.statusBar().showMessage(f"Updated View Preset: {name}", 3500)

    def _delete_view_preset(self) -> None:
        identity = self._current_data_key()
        name = self.view_preset_combo.currentData()
        if not identity or not isinstance(name, str):
            return
        if QMessageBox.question(
            self, "Delete View Preset?", f"Delete '{name}' for this Data?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.named_view_store.delete(identity, name)
        self._populate_view_preset_combo()

    def _save_named_view(self) -> None:
        self._save_view_preset()

    def _load_named_view(self, name: str) -> bool:
        if self.experiment is None:
            return False
        payload = self.named_view_store.get(self.experiment.data_identity, name)
        if payload is None:
            return False
        payload_identity = payload.get("data_identity")
        if payload_identity is not None and payload_identity != self.experiment.data_identity:
            return False
        # ``restore_session_state`` already validates each axis/channel/pane
        # independently and intentionally ignores absent geometry/windows.
        restored = self.restore_session_state(payload)
        if restored:
            self._flush_display_state()
            self._populate_view_preset_combo(name)
            self.statusBar().showMessage(f"Loaded View Preset: {name}", 3000)
        return restored

    def _open_named_view_manager(self) -> None:
        if self.experiment is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Manage View Presets")
        dialog.setModal(False)
        layout = QVBoxLayout(dialog)
        view_list = QListWidget(dialog)
        view_list.addItems(self.named_view_store.list_names(self.experiment.data_identity))
        layout.addWidget(view_list)
        actions = QHBoxLayout()
        load_button = QPushButton("Load", dialog)
        rename_button = QPushButton("Rename", dialog)
        delete_button = QPushButton("Delete", dialog)
        close_button = QPushButton("Close", dialog)
        for button in (load_button, rename_button, delete_button, close_button):
            actions.addWidget(button)
        layout.addLayout(actions)

        def selected_name() -> str | None:
            item = view_list.currentItem()
            return item.text() if item is not None else None

        def load_selected() -> None:
            name = selected_name()
            if name and self._load_named_view(name):
                dialog.accept()

        def rename_selected() -> None:
            old_name = selected_name()
            if not old_name:
                return
            new_name, accepted = QInputDialog.getText(dialog, "Rename View", "View name:", text=old_name)
            if accepted and self.named_view_store.rename(self.experiment.data_identity, old_name, new_name):
                view_list.currentItem().setText(new_name.strip())
                self._populate_view_preset_combo(new_name.strip())

        def delete_selected() -> None:
            name = selected_name()
            if name and self.named_view_store.delete(self.experiment.data_identity, name):
                view_list.takeItem(view_list.currentRow())
                self._populate_view_preset_combo()

        load_button.clicked.connect(load_selected)
        view_list.itemDoubleClicked.connect(lambda _item: load_selected())
        rename_button.clicked.connect(rename_selected)
        delete_button.clicked.connect(delete_selected)
        close_button.clicked.connect(dialog.close)
        dialog.resize(360, 260)
        dialog.show()
        # Keep a Python reference for a genuinely modeless manager.
        self._named_view_dialog = dialog

    def _on_toggle_channel_browser(self, hidden: bool) -> None:
        sizes = self.main_splitter.sizes()
        total = max(sum(sizes), self.main_splitter.width(), self._surface_controls_width + 500)
        if hidden:
            if sizes[0] > 0:
                self._channel_browser_last_width = sizes[0]
            self.main_splitter.setSizes([0, total])
            self.controls_panel.hide()
            self.toggle_channels_button.setText(self.localizer.text("viewer.show_controls"))
        else:
            width = min(self._channel_browser_last_width, max(total - 200, 0))
            self.controls_panel.show()
            self.main_splitter.setSizes([width, max(200, total - width)])
            self.toggle_channels_button.setText(self.localizer.text("viewer.hide_controls"))

    def _set_surface_layout_mode(self, enabled: bool) -> None:
        """Keep the 3D control column fixed-width; other modes keep the splitter."""
        if enabled:
            if self.main_splitter.sizes()[0] > 0:
                self._surface_controls_width = self.main_splitter.sizes()[0]
            self._surface_controls_width = max(250, min(310, self._surface_controls_width))
            self.controls_panel.setMinimumWidth(self._surface_controls_width)
            self.controls_panel.setMaximumWidth(self._surface_controls_width)
            self.main_splitter.setHandleWidth(0)
            handle = self.main_splitter.handle(1)
            if handle is not None:
                handle.setEnabled(False)
            if not self.controls_panel.isHidden():
                total = max(sum(self.main_splitter.sizes()), self.main_splitter.width(),
                            self._surface_controls_width + 500)
                self.main_splitter.setSizes([
                    self._surface_controls_width,
                    max(200, total - self._surface_controls_width),
                ])
        else:
            self.controls_panel.setMinimumWidth(260)
            self.controls_panel.setMaximumWidth(320)
            self.main_splitter.setHandleWidth(5)
            handle = self.main_splitter.handle(1)
            if handle is not None:
                handle.setEnabled(True)
            if not self.controls_panel.isHidden():
                total = max(sum(self.main_splitter.sizes()), self.main_splitter.width(), 500)
                width = min(max(self._channel_browser_last_width, 180), max(total - 200, 180))
                self.main_splitter.setSizes([width, total - width])

    def _on_maximize_plot_toggled(self, maximized: bool) -> None:
        """Collapse secondary panels and restore their exact prior layout."""
        if not self.multi_pane_splitter.isHidden():
            if maximized:
                self._capture_active_pane_state()
                self._multi_pane_maximized = True
                self._plot_layout_state = {
                    "pane_layout": self.pane_layout_combo.currentText(),
                    "main_sizes": self.main_splitter.sizes(),
                    "right_sizes": self.right_splitter.sizes(),
                    "channels_hidden": self.controls_panel.isHidden(),
                    "toggle_hidden": self.toggle_channels_button.isHidden(),
                    "trace_hidden": self.trace_management_splitter.isHidden(),
                    "pane_sizes": self._pane_splitter_sizes(),
                }
                self.controls_panel.hide()
                self.toggle_channels_button.hide()
                self.trace_management_splitter.hide()
                self._clear_pane_layout()
                for pane_id, frame in self._pane_frames.items():
                    frame.setVisible(pane_id == self._active_pane_id)
                self.pane_grid.addWidget(self._pane_frames[self._active_pane_id])
                self.main_splitter.setSizes([0, max(sum(self.main_splitter.sizes()), 1)])
                make_icon_only(self.maximize_plot_button, "maximize", "Restore Layout")
                return
            state = self._plot_layout_state
            if not self._multi_pane_maximized or state is None:
                return
            self._multi_pane_maximized = False
            self.controls_panel.setHidden(state["channels_hidden"])
            self.toggle_channels_button.setHidden(state["toggle_hidden"])
            self.trace_management_splitter.setHidden(state["trace_hidden"])
            self.main_splitter.setSizes(state["main_sizes"])
            self.right_splitter.setSizes(state["right_sizes"])
            self._layout_panes(state["pane_layout"])
            self._restore_pane_splitter_sizes(state["pane_sizes"])
            make_icon_only(self.maximize_plot_button, "maximize", "Maximize Plot")
            self._plot_layout_state = None
            self._render_multi_panes()
            return
        secondary_widgets = (self.trace_management_splitter,)
        if maximized:
            self._plot_layout_state = {
                "main_sizes": self.main_splitter.sizes(),
                "right_sizes": self.right_splitter.sizes(),
                "plot_1d_sizes": self.plot_1d_splitter.sizes(),
                "plot_2d_sizes": self.plot_2d_splitter.sizes(),
                "hidden": {widget: widget.isHidden() for widget in secondary_widgets},
                "channels_hidden": self.controls_panel.isHidden(),
                "toggle_hidden": self.toggle_channels_button.isHidden(),
            }
            self.controls_panel.hide()
            self.toggle_channels_button.hide()
            for widget in secondary_widgets:
                widget.hide()
            self.main_splitter.setSizes([0, max(sum(self.main_splitter.sizes()), 1)])
            make_icon_only(self.maximize_plot_button, "maximize", "Restore Layout")
            return

        state = self._plot_layout_state
        if state is None:
            return
        self.controls_panel.setHidden(state["channels_hidden"])
        self.toggle_channels_button.setHidden(state["toggle_hidden"])
        for widget, was_hidden in state["hidden"].items():
            widget.setHidden(was_hidden)
        self.main_splitter.setSizes(state["main_sizes"])
        self.right_splitter.setSizes(state["right_sizes"])
        self.plot_1d_splitter.setSizes(state["plot_1d_sizes"])
        self.plot_2d_splitter.setSizes(state["plot_2d_sizes"])
        make_icon_only(self.maximize_plot_button, "maximize", "Maximize Plot")
        self._plot_layout_state = None

    def _fit_plot_controls_stack(self, index: int) -> None:
        # A QStackedWidget reserves its tallest page; size it to the visible
        # page so 1D/2D settings are not followed by the 3D page's empty height.
        for page_index in range(self.plot_controls_stack.count()):
            page = self.plot_controls_stack.widget(page_index)
            policy = page.sizePolicy()
            policy.setVerticalPolicy(
                QSizePolicy.Policy.Preferred if page_index == index else QSizePolicy.Policy.Ignored
            )
            page.setSizePolicy(policy)
        self.plot_controls_stack.updateGeometry()

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: 600; padding-top: 5px;")
        return label

    def _build_controls_panel(self) -> QWidget:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        self.controls_scroll_area = QScrollArea(panel)
        self.controls_scroll_area.setWidgetResizable(True)
        self.controls_scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.controls_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        layout.addWidget(self._section_label("PLOT SETTINGS"))
        self.plot_controls_stack = QStackedWidget()
        self.plot_controls_stack.addWidget(self._build_1d_controls_panel())
        self.plot_controls_stack.addWidget(self._build_2d_controls_panel())
        self._nd_controls_page = self._build_nd_controls_panel()
        # The 3D pages live outside the Viewer (3D window); register them so
        # language changes reach them even before that window is first opened.
        self.localizer.bind(self._nd_controls_page)
        self.localizer.bind(self._nd_plot_page)
        self.plot_controls_stack.currentChanged.connect(self._fit_plot_controls_stack)
        self._fit_plot_controls_stack(self.plot_controls_stack.currentIndex())
        layout.addWidget(self.plot_controls_stack)
        layout.addWidget(self._section_label("VIEW PRESET"))
        self.view_preset_combo = QComboBox()
        self.view_preset_combo.setObjectName("viewPresetSelector")
        self.view_preset_combo.setMinimumWidth(0)
        self.view_preset_combo.addItem("Select View Preset", userData=None)
        layout.addWidget(self.view_preset_combo)
        preset_buttons = QHBoxLayout()
        self.save_view_preset_button = QToolButton()
        self.save_view_preset_button.setIcon(
            icon("save", fallback=self.style().standardIcon(QStyle.SP_DialogSaveButton))
        )
        self.save_view_preset_button.setToolTip("Save View Preset")
        self.save_view_preset_button.setAccessibleName("Save View Preset")
        self.update_view_preset_button = QToolButton()
        self.update_view_preset_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.update_view_preset_button.setToolTip("Update selected View Preset")
        self.update_view_preset_button.setAccessibleName("Update View Preset")
        self.delete_view_preset_button = QToolButton()
        self.delete_view_preset_button.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self.delete_view_preset_button.setToolTip("Delete selected View Preset")
        self.delete_view_preset_button.setAccessibleName("Delete View Preset")
        for button in (self.save_view_preset_button, self.update_view_preset_button,
                       self.delete_view_preset_button):
            button.setFixedSize(28, 28)
            button.setAutoRaise(True)
            preset_buttons.addWidget(button)
        preset_buttons.setContentsMargins(0, 0, 0, 0)
        preset_buttons.setSpacing(2)
        preset_buttons.addStretch(1)
        layout.addLayout(preset_buttons)
        self.view_preset_combo.currentIndexChanged.connect(self._on_view_preset_selected)
        self.save_view_preset_button.clicked.connect(self._save_view_preset)
        self.update_view_preset_button.clicked.connect(self._update_view_preset)
        self.delete_view_preset_button.clicked.connect(self._delete_view_preset)
        self._update_view_preset_controls()

        layout.addWidget(self._section_label("FORMULA"))
        self.formula_controls = QWidget()
        formula_layout = QVBoxLayout(self.formula_controls)
        formula_layout.setContentsMargins(0, 0, 0, 0)
        formula_layout.setSpacing(2)
        self.x_formula_edit = QLineEdit()
        self.x_formula_edit.setObjectName("xFormulaInput")
        self.x_formula_edit.setPlaceholderText("blank = x")
        self.y_formula_edit = QLineEdit()
        self.y_formula_edit.setObjectName("yFormulaInput")
        self.y_formula_edit.setPlaceholderText("blank = y")
        from app.gui.math_render import FormulaPreviewLabel

        self.x_formula_preview = FormulaPreviewLabel("x′ = x")
        self.y_formula_preview = FormulaPreviewLabel("y′ = y")
        for preview in (self.x_formula_preview, self.y_formula_preview):
            preview.setWordWrap(True)
            preview.setMinimumHeight(24)
            preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
            preview.setObjectName("formulaPreview")
        for label, editor, preview in (("x", self.x_formula_edit, self.x_formula_preview),
                                       ("y", self.y_formula_edit, self.y_formula_preview)):
            formula_layout.addWidget(QLabel(f"{label} = F(x, y)" if label == "x" else f"{label} = G(x, y)"))
            formula_layout.addWidget(editor)
            formula_layout.addWidget(preview)
        self.formula_status_label = QLabel("Blank formula keeps that axis unchanged.")
        self.formula_status_label.setWordWrap(True)
        formula_layout.addWidget(self.formula_status_label)
        formula_buttons = QHBoxLayout()
        self.reset_formula_button = QPushButton("Reset")
        self.apply_formula_button = QPushButton("Apply")
        formula_buttons.addWidget(self.reset_formula_button)
        formula_buttons.addWidget(self.apply_formula_button)
        formula_layout.addLayout(formula_buttons)
        self._formula_preview_timer = QTimer(self)
        self._formula_preview_timer.setSingleShot(True)
        self._formula_preview_timer.setInterval(180)
        self._formula_preview_timer.timeout.connect(self._refresh_formula_previews)
        self.x_formula_edit.textChanged.connect(self._on_formula_text_changed)
        self.y_formula_edit.textChanged.connect(self._on_formula_text_changed)
        self.apply_formula_button.clicked.connect(self._apply_formula_inputs)
        self.apply_formula_button.clicked.connect(self._note_data_operation)
        self.reset_formula_button.clicked.connect(self._reset_formula_inputs)
        layout.addWidget(self.formula_controls)

        layout.addWidget(self._section_label("MARKS"))
        layout.addWidget(self.marks_widget)
        self.controls_scroll_area.setWidget(content)
        panel_layout.addWidget(self.controls_scroll_area)
        return panel

    def _on_formula_text_changed(self, *_args) -> None:
        self._formula_preview_timer.start()

    def _refresh_formula_previews(self) -> None:
        for axis, editor, preview in (
            ("x", self.x_formula_edit, self.x_formula_preview),
            ("y", self.y_formula_edit, self.y_formula_preview),
        ):
            source = editor.text().strip()
            try:
                tree = parse_formula(source)
                expression = formula_to_display(tree)
                latex = _latex_bare_root(tree) if tree is not None else axis
            except FormulaError as error:
                editor.setStyleSheet("QLineEdit { border: 1px solid %s; }" % STATUS["error"])
                preview.set_formula(f"{axis} preview unavailable")
                preview.setToolTip(str(error))
                continue
            editor.setStyleSheet("")
            preview.setToolTip(source or "Identity transformation")
            # Typeset like hand-written math; the Unicode text stays as text() / fallback.
            preview.set_formula(f"{axis}′ = {expression}", rf"{axis}' = {latex}",
                                lhs=f"{axis}'", terms=formula_terms(tree))
        self.formula_status_label.setText(
            "Preview only; Apply updates this 1D pane. Blank means identity."
        )
        self._refresh_formula_availability()

    def _refresh_formula_availability(self) -> None:
        available = self.experiment is not None and self.mode_combo.currentIndex() == 0
        self.formula_controls.setEnabled(available)
        if not available:
            self.formula_status_label.setText("Formula is available for 1D plots only.")

    def _apply_formula_inputs(self) -> bool:
        if self.experiment is None or self.mode_combo.currentIndex() != 0:
            return False
        x_formula, y_formula = self.x_formula_edit.text().strip(), self.y_formula_edit.text().strip()
        try:
            parse_formula(x_formula)
            parse_formula(y_formula)
            x_candidate, y_candidate = self.x_combo.currentData(), self.y_combo.currentData()
            if x_candidate is None or y_candidate is None:
                raise FormulaError("Choose valid X and Y axes before applying a Formula.")
            state = self._pane_states.get(self._active_pane_id)
            spec = self._current_transform_spec()
            x_values, y_values, _, _ = self._plot_arrays_for_entry(
                x_candidate, y_candidate, self.log_entries.current_row(), spec,
                apply_formula=False,
            )
            apply_formulas(x_values, y_values, x_formula, y_formula)
        except (FormulaError, ValueError, TypeError, FloatingPointError) as error:
            self.formula_status_label.setText(str(error))
            self.formula_status_label.setStyleSheet("color: %s;" % STATUS["error"])
            return False
        if state is None:
            return False
        state.x_formula = x_formula
        state.y_formula = y_formula
        state.formula_enabled = bool(x_formula or y_formula)
        self.formula_status_label.setStyleSheet("color: %s;" % STATUS["ok"])
        self.formula_status_label.setText("Formula applied to the current displayed X/Y values.")
        self.update_plot(preserve_view=False)
        self._schedule_display_state_save()
        return True

    def _reset_formula_inputs(self) -> None:
        self.x_formula_edit.clear()
        self.y_formula_edit.clear()
        state = self._pane_states.get(self._active_pane_id)
        if state is not None:
            state.x_formula = ""
            state.y_formula = ""
            state.formula_enabled = False
        self.formula_status_label.setStyleSheet("")
        self.formula_status_label.setText("Formula reset; both axes use identity values.")
        if self.experiment is not None and self.mode_combo.currentIndex() == 0:
            self.update_plot(preserve_view=False)
            self._schedule_display_state_save()

    @staticmethod
    def _form_row(form: QFormLayout, label: str, widget: QWidget) -> None:
        form.addRow(label, widget)

    def _build_1d_controls_panel(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for combo in (self.x_combo, self.y_combo, self.transform_combo, self.axis_preset_combo):
            combo.setMinimumWidth(145)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._form_row(form, "X Axis", self.x_combo)
        self._form_row(form, "Y Axis", self.y_combo)
        self._form_row(form, "Transform", self.transform_combo)
        self._form_row(form, "Axis Preset", self.axis_preset_combo)
        form.addRow(self.save_transform_button)
        preset_management = QWidget()
        preset_management_layout = QHBoxLayout(preset_management)
        preset_management_layout.setContentsMargins(0, 0, 0, 0)
        preset_management_layout.setSpacing(2)
        for button in self.axis_preset_management_buttons:
            preset_management_layout.addWidget(button)
        form.addRow(preset_management)
        modifier_row = QWidget()
        modifier_layout = QHBoxLayout(modifier_row)
        modifier_layout.setContentsMargins(0, 0, 0, 0)
        modifier_layout.addWidget(self.db_checkbox)
        modifier_layout.addWidget(self.unwrap_checkbox)
        form.addRow(modifier_row)
        form.addRow(self.show_data_points_checkbox)
        point_size_row = QWidget()
        point_size_layout = QHBoxLayout(point_size_row)
        point_size_layout.setContentsMargins(0, 0, 0, 0)
        point_size_layout.setSpacing(0)
        point_size_layout.addWidget(self.point_size_mode_combo)
        point_size_layout.addWidget(self.manual_point_size_spin)
        form.addRow("Point Size", point_size_row)
        return page

    def _build_2d_controls_panel(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for combo in (self.z_combo_2d, self.x_combo_2d, self.y_combo_2d,
                      self.transform_combo_2d, self.colormap_combo):
            combo.setMinimumWidth(145)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for label, widget in (("Z", self.z_combo_2d), ("X", self.x_combo_2d),
                              ("Y", self.y_combo_2d), ("Transform", self.transform_combo_2d),
                              ("Colormap", self.colormap_combo)):
            form.addRow(label, widget)
        form.addRow(self.auto_range_checkbox)
        form.addRow("Minimum", self.zmin_spin)
        form.addRow("Maximum", self.zmax_spin)
        form.addRow(self.auto_range_button)
        return page

    def _build_nd_controls_panel(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._three_d_control_rows = {}

        def add_control_row(form, key: str, title: str, widget: QWidget) -> None:
            label = QLabel(title)
            form.addRow(label, widget)
            self._three_d_control_rows[key] = (label, widget)

        geometry_form = QFormLayout()
        geometry_form.setContentsMargins(0, 0, 0, 0)
        add_control_row(geometry_form, "geometry", "Geometry Type", self.geometry_combo)
        layout.addLayout(geometry_form)

        axes = QFormLayout()
        axes.setContentsMargins(0, 0, 0, 0)
        add_control_row(axes, "x_axis", self.localizer.text("viewer.axis_x"), self.x_combo_nd)
        add_control_row(axes, "y_axis", self.localizer.text("viewer.axis_y"), self.y_combo_nd)
        add_control_row(axes, "height_axis", self.localizer.text("viewer.height_z"), self.z_combo_nd)
        add_control_row(axes, "height_transform", self.localizer.text("viewer.transform"), self.transform_combo_nd)
        add_control_row(axes, "unwrap_axis", "Unwrap Along", self.phase_unwrap_axis_combo)
        layout.addLayout(axes)

        layout.addWidget(self._section_label(self.localizer.text("viewer.surface")))
        surface = QFormLayout()
        surface.setContentsMargins(0, 0, 0, 0)
        surface.addRow(self.localizer.text("viewer.rendering"), self.surface_rendering_combo)
        z_scale_row = QWidget()
        z_scale_layout = QHBoxLayout(z_scale_row)
        z_scale_layout.setContentsMargins(0, 0, 0, 0)
        z_scale_layout.addWidget(self.surface_z_scale_slider, 1)
        z_scale_layout.addWidget(self.surface_z_scale_label)
        z_scale_layout.addWidget(self.surface_z_auto_checkbox)
        surface.addRow(self.localizer.text("viewer.z_scale"), z_scale_row)
        surface.addRow(self.surface_z_reset_button)
        surface.addRow(self.localizer.text("viewer.projection"), self.surface_projection_combo)
        opacity_row = QWidget()
        opacity_layout = QHBoxLayout(opacity_row)
        opacity_layout.setContentsMargins(0, 0, 0, 0)
        opacity_layout.addWidget(self.surface_opacity_slider, 1)
        opacity_layout.addWidget(self.surface_opacity_label)
        add_control_row(surface, "opacity", "Opacity", opacity_row)
        add_control_row(surface, "projection", "", self.surface_projection_checkbox)
        add_control_row(surface, "reference", "Reference Plane", self.surface_reference_combo)
        add_control_row(surface, "reference_value", "Plane Value", self.surface_reference_value)
        dual_opacity_row = QWidget()
        dual_opacity_layout = QHBoxLayout(dual_opacity_row)
        dual_opacity_layout.setContentsMargins(0, 0, 0, 0)
        dual_opacity_layout.addWidget(self.surface_b_opacity_slider, 1)
        dual_opacity_layout.addWidget(self.surface_b_opacity_label)
        add_control_row(surface, "surface_b", "Surface B Mapping", self.surface_b_transform_combo)
        add_control_row(surface, "opacity_b", "Surface B Opacity", dual_opacity_row)
        layout.addLayout(surface)

        layout.addWidget(self._section_label(self.localizer.text("viewer.color")))
        color = QFormLayout()
        color.setContentsMargins(0, 0, 0, 0)
        self._surface_color_source_label = QLabel(self.localizer.text("viewer.color_source"))
        self._surface_color_transform_label = QLabel(self.localizer.text("viewer.transform"))
        color.addRow(self._surface_color_source_label, self.surface_color_source_combo)
        color.addRow(self._surface_color_transform_label, self.surface_color_transform_combo)
        color.addRow(self.localizer.text("viewer.colormap"), self.colormap_combo_nd)
        add_control_row(color, "top_colorbar", "", self.surface_top_colorbar_checkbox)
        color.addRow(self.auto_range_checkbox_nd)
        color.addRow(self.localizer.text("viewer.minimum"), self.zmin_spin_nd)
        color.addRow(self.localizer.text("viewer.maximum"), self.zmax_spin_nd)
        color.addRow(self.auto_range_button_nd)
        layout.addLayout(color)

        point_form = QFormLayout()
        point_form.setContentsMargins(0, 0, 0, 0)
        add_control_row(point_form, "point_x", "Point X", self.point_x_mapping_combo)
        add_control_row(point_form, "point_y", "Point Y", self.point_y_mapping_combo)
        add_control_row(point_form, "point_z", "Point Z", self.point_z_mapping_combo)
        add_control_row(point_form, "point_color", "Point Color", self.point_color_mapping_combo)
        add_control_row(point_form, "point_size", "Point Size", self.point_size_spin)
        layout.addLayout(point_form)

        layout.addWidget(self._section_label(self.localizer.text("viewer.camera")))
        camera_grid = QGridLayout()
        camera_grid.setContentsMargins(0, 0, 0, 0)
        for index, preset in enumerate(("Top", "Front", "Side", "Isometric")):
            button = QPushButton(self.localizer.text(f"viewer.camera_{preset.lower()}"))
            button.clicked.connect(lambda _checked=False, name=preset: self._set_surface_camera_preset(name))
            camera_grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(camera_grid)
        camera_actions = QHBoxLayout()
        self.surface_reset_view_button = QPushButton(self.localizer.text("viewer.reset_view"))
        self.surface_view_all_button = QPushButton(self.localizer.text("viewer.view_all"))
        self.surface_reset_view_button.clicked.connect(self._reset_surface_view)
        self.surface_view_all_button.clicked.connect(self._view_all_surface)
        camera_actions.addWidget(self.surface_reset_view_button)
        camera_actions.addWidget(self.surface_view_all_button)
        layout.addLayout(camera_actions)

        self.surface_advanced_button = QToolButton()
        self.surface_advanced_button.setText(self.localizer.text("viewer.advanced"))
        self.surface_advanced_button.setCheckable(True)
        self.surface_advanced_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.surface_advanced_button.setArrowType(Qt.ArrowType.RightArrow)
        self.surface_advanced_content = QWidget()
        advanced_layout = QVBoxLayout(self.surface_advanced_content)
        advanced_layout.setContentsMargins(4, 2, 2, 2)
        self.surface_performance_label = QLabel()
        self.surface_performance_label.setWordWrap(True)
        advanced_layout.addWidget(self.surface_performance_label)
        self.surface_slice_label = QLabel(self.localizer.text("viewer.slice_dimensions"))
        advanced_layout.addWidget(self.surface_slice_label)
        advanced_layout.addWidget(self.slice_explorer)
        self.surface_advanced_content.hide()
        self.surface_advanced_button.toggled.connect(self._toggle_surface_advanced)
        layout.addWidget(self.surface_advanced_button)
        layout.addWidget(self.surface_advanced_content)
        layout.addStretch(1)
        self._update_geometry_controls()
        return page


    def _build_1d_page(self) -> QWidget:
        """v0.9B: X Axis and Y Axis are now both freely, independently
        selectable from the CURRENT file's dynamically-discovered
        AxisCandidate list (spec §4) — no more fixed
        Frequency/Point + channel-name split from v0.9A. Transform
        stays a genuinely separate system (spec §5): it only matters
        when the selected Y is a complex/vector channel, and is driven
        by TransformStore (defaults + user-saved custom transforms).
        Trace selection is now primarily the Log Entries table below
        the plot, with the Sweep spinbox kept as a secondary quick-jump
        (spec §9/§10) — both, plus keyboard Up/Down, funnel through
        LogEntriesWidget.select_row(), the single source of truth."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("X Axis:"))
        self.x_combo = QComboBox()
        self.x_combo.setMinimumWidth(0)
        self.x_combo.setMinimumContentsLength(9)
        self.x_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        controls.addWidget(self.x_combo)

        controls.addWidget(QLabel("Y Axis:"))
        self.y_combo = QComboBox()
        self.y_combo.setMinimumWidth(0)
        self.y_combo.setMinimumContentsLength(9)
        self.y_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        controls.addWidget(self.y_combo)

        controls.addWidget(QLabel("Transform:"))
        self.transform_combo = QComboBox()
        controls.addWidget(self.transform_combo)

        self.db_checkbox = QCheckBox("dB")
        self.db_checkbox.setEnabled(False)
        controls.addWidget(self.db_checkbox)

        self.unwrap_checkbox = QCheckBox("Unwrap Phase")
        self.unwrap_checkbox.setEnabled(False)
        controls.addWidget(self.unwrap_checkbox)

        self.show_data_points_checkbox = QCheckBox("Show Data Points")
        self.point_size_mode_combo = QComboBox()
        self.point_size_mode_combo.addItems(["Auto", "Manual"])
        self.point_size_mode_combo.setMinimumWidth(0)
        self.point_size_mode_combo.setMaximumWidth(65)
        self.manual_point_size_spin = QDoubleSpinBox()
        self.manual_point_size_spin.setMinimumWidth(0)
        self.manual_point_size_spin.setMaximumWidth(62)
        self.manual_point_size_spin.setToolTip("Manual marker diameter in pixels")
        self.manual_point_size_spin.setRange(1.0, 12.0)
        self.manual_point_size_spin.setSingleStep(0.5)
        self.manual_point_size_spin.setValue(4.0)
        self.manual_point_size_spin.setSuffix("")
        self.manual_point_size_spin.setEnabled(False)

        self.axis_preset_combo = QComboBox()
        self.axis_preset_combo.setMinimumWidth(0)

        self.save_transform_button = QPushButton("Save Axis Preset...")
        self.save_transform_button.setObjectName("saveAxisPresetButton")
        self.save_transform_button.setEnabled(False)
        self.update_axis_preset_button = QToolButton()
        self.update_axis_preset_button.setObjectName("updateAxisPresetButton")
        self.update_axis_preset_button.setText("Update")
        self.update_axis_preset_button.setToolTip("Update the selected Axis Preset")
        self.rename_axis_preset_button = QToolButton()
        self.rename_axis_preset_button.setObjectName("renameAxisPresetButton")
        self.rename_axis_preset_button.setText("Rename")
        self.rename_axis_preset_button.setToolTip("Rename the selected Axis Preset")
        self.delete_axis_preset_button = QToolButton()
        self.delete_axis_preset_button.setObjectName("deleteAxisPresetButton")
        self.delete_axis_preset_button.setText("Delete")
        self.delete_axis_preset_button.setToolTip("Delete the selected Axis Preset")
        self.axis_preset_management_buttons = (
            self.update_axis_preset_button,
            self.rename_axis_preset_button,
            self.delete_axis_preset_button,
        )
        for button in self.axis_preset_management_buttons:
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setMinimumWidth(0)
            button.setEnabled(False)

        controls.addStretch(1)
        # Controls are placed in the shared left settings column.

        trace_row = QHBoxLayout()
        self.trace_display_label = QLabel("Trace \u2014")
        trace_font = self.trace_display_label.font()
        trace_font.setPointSize(trace_font.pointSize() + 3)
        trace_font.setBold(True)
        self.trace_display_label.setFont(trace_font)
        trace_row.addWidget(self.trace_display_label)

        trace_row.addWidget(QLabel("  (click a Log Entry, \u2191/\u2193, or Sweep \u2192)"))
        trace_row.addSpacing(16)

        trace_row.addWidget(QLabel("Sweep (jump to entry):"))
        self.entry_spin = QSpinBox()
        self.entry_spin.setMinimum(0)
        self.entry_spin.setMaximum(0)
        trace_row.addWidget(self.entry_spin)
        self.entry_label = QLabel("")
        trace_row.addWidget(self.entry_label)
        trace_row.addStretch(1)
        layout.addLayout(trace_row)

        # Plot stays the dominant element (v0.8's layout-priority rule);
        # Log Entries is a resizable, modest-by-default panel below it,
        # not competing for the majority of screen space.
        self.plot_widget = Plot1DWidget()
        self.log_entries = LogEntriesWidget()
        self.trace_selection = self.log_entries.selection_state
        self.trace_color_combo = QComboBox()
        self.trace_color_combo.addItems(COLOR_MODES)
        self.trace_color_combo.setCurrentText(SEQUENTIAL)
        self.trace_color_combo.setToolTip("Color mapping for selected 1D traces")
        self.overlay_status_label = QLabel("Single Trace")
        self.overlay_status_label.setStyleSheet("color: %s;" % STATUS["muted"])
        self.overlay_status_label.setMinimumWidth(180)

        self.trace_management_splitter = QSplitter(Qt.Horizontal)
        self.trace_management_splitter.setChildrenCollapsible(True)
        self.trace_management_splitter.addWidget(self.log_entries)
        self.multi_trace_panel = self._build_multi_trace_panel()
        self.trace_management_splitter.addWidget(self.multi_trace_panel)
        self.trace_management_splitter.setSizes([700, 300])
        self.trace_management_splitter.setStretchFactor(0, 3)
        self.trace_management_splitter.setStretchFactor(1, 1)
        self.multi_trace_panel.hide()

        self.plot_1d_splitter = QSplitter(Qt.Vertical)
        plot_splitter = self.plot_1d_splitter
        plot_splitter.setChildrenCollapsible(True)
        plot_splitter.setHandleWidth(5)
        plot_splitter.addWidget(self.plot_widget)
        plot_splitter.addWidget(self.trace_management_splitter)
        self.plot_widget.setMinimumHeight(1)
        self.trace_management_splitter.setMinimumHeight(1)
        plot_splitter.setSizes([780, 220])
        plot_splitter.setStretchFactor(0, 1)
        plot_splitter.setStretchFactor(1, 0)
        layout.addWidget(plot_splitter)

        # signal wiring
        self.x_combo.currentIndexChanged.connect(self._on_axis_changed)
        self.y_combo.currentIndexChanged.connect(self._on_axis_changed)
        self.transform_combo.currentIndexChanged.connect(self._on_transform_changed)
        self.transform_combo.activated.connect(self._note_data_operation)   # user choice only
        self.db_checkbox.toggled.connect(self.update_plot)
        self.unwrap_checkbox.toggled.connect(self.update_plot)
        self.show_data_points_checkbox.toggled.connect(self._on_data_point_settings_changed)
        self.point_size_mode_combo.currentTextChanged.connect(self._on_data_point_settings_changed)
        self.manual_point_size_spin.valueChanged.connect(self._on_data_point_settings_changed)
        self.axis_preset_combo.currentIndexChanged.connect(self._on_axis_preset_changed)
        self.axis_preset_combo.currentIndexChanged.connect(self._update_axis_preset_controls)
        self.save_transform_button.clicked.connect(self._on_save_transform_clicked)
        self.update_axis_preset_button.clicked.connect(self._update_axis_preset)
        self.rename_axis_preset_button.clicked.connect(self._rename_axis_preset)
        self.delete_axis_preset_button.clicked.connect(self._delete_axis_preset)
        self.trace_color_combo.currentTextChanged.connect(self._on_trace_color_changed)
        self.entry_spin.valueChanged.connect(self._on_sweep_spin_changed)
        self.log_entries.entry_selected.connect(self._on_log_entry_selected)
        self.log_entries.selection_changed.connect(self._on_trace_selection_changed)

        return page

    def _build_multi_trace_panel(self) -> QWidget:
        # The outer scroll area is intentionally the splitter child.  The
        # trace workspace may become arbitrarily short, while its controls
        # remain laid out in a scrollable viewport instead of painting over
        # one another or forcing the splitter back to a preset height.
        scroll = QScrollArea()
        scroll.setObjectName("multiTraceScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(1)
        panel = QWidget()
        panel.setObjectName("multiTraceContent")
        panel.setMinimumWidth(270)
        panel.setMinimumHeight(260)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._section_label("TRACES"))
        layout.addWidget(self.overlay_status_label)

        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Trace Colors"))
        color_row.addWidget(self.trace_color_combo, 1)
        layout.addLayout(color_row)

        self.multi_trace_tree = QTreeWidget()
        self.multi_trace_tree.setColumnCount(4)
        self.multi_trace_tree.setHeaderLabels(["Show", "Active", "Ref", "Trace"])
        self.multi_trace_tree.setRootIsDecorated(False)
        self.multi_trace_tree.setAlternatingRowColors(True)
        self.multi_trace_tree.setMinimumHeight(96)
        self.multi_trace_tree.itemClicked.connect(self._on_multi_trace_item_clicked)
        layout.addWidget(self.multi_trace_tree, 1)

        saved_row = QHBoxLayout()
        self.saved_overlay_combo = QComboBox()
        self.saved_overlay_combo.setMinimumWidth(120)
        saved_row.addWidget(self.saved_overlay_combo, 1)
        self.load_overlay_button = QPushButton("Load")
        self.load_overlay_button.clicked.connect(self._load_selected_overlay)
        saved_row.addWidget(self.load_overlay_button)
        layout.addLayout(saved_row)

        actions = QHBoxLayout()
        self.clear_reference_button = QPushButton("Clear Ref")
        self.remove_trace_button = QPushButton("Remove")
        self.save_overlay_button = QPushButton("Save...")
        self.rename_overlay_button = QPushButton("Rename...")
        self.delete_overlay_button = QPushButton("Delete")
        self.clear_reference_button.clicked.connect(self._clear_reference_trace)
        self.remove_trace_button.clicked.connect(self._remove_trace_from_manager)
        self.save_overlay_button.clicked.connect(self._save_overlay_dialog)
        self.rename_overlay_button.clicked.connect(self._rename_overlay_dialog)
        self.delete_overlay_button.clicked.connect(self._delete_selected_overlay)
        for button, width in (
            (self.clear_reference_button, 72),
            (self.remove_trace_button, 60),
            (self.save_overlay_button, 52),
            (self.rename_overlay_button, 68),
            (self.delete_overlay_button, 54),
        ):
            button.setMaximumWidth(width)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.multi_trace_content = panel
        scroll.setWidget(panel)
        return scroll

    def _build_2d_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Z:"))
        self.z_combo_2d = QComboBox()
        self.z_combo_2d.setMinimumWidth(140)
        row1.addWidget(self.z_combo_2d)

        row1.addWidget(QLabel("X:"))
        self.x_combo_2d = QComboBox()
        self.x_combo_2d.setMinimumWidth(140)
        row1.addWidget(self.x_combo_2d)

        row1.addWidget(QLabel("Y:"))
        self.y_combo_2d = QComboBox()
        self.y_combo_2d.setMinimumWidth(140)
        row1.addWidget(self.y_combo_2d)

        row1.addWidget(QLabel("Transform:"))
        self.transform_combo_2d = QComboBox()
        for key in VALID_TRANSFORMS:
            self.transform_combo_2d.addItem(TRANSFORM_LABELS.get(key, key), userData=key)
        self.transform_combo_2d.setCurrentIndex(list(VALID_TRANSFORMS).index("magnitude_db"))
        row1.addWidget(self.transform_combo_2d)
        row1.addStretch(1)
        # Controls are placed in the shared left settings column.

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Colormap:"))
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(COLORMAPS)
        row2.addWidget(self.colormap_combo)

        row2.addWidget(QLabel("Color range:"))
        self.auto_range_checkbox = QCheckBox("Auto")
        self.auto_range_checkbox.setChecked(True)
        row2.addWidget(self.auto_range_checkbox)

        row2.addWidget(QLabel("Min:"))
        self.zmin_spin = QDoubleSpinBox()
        self.zmin_spin.setRange(-1e9, 1e9)
        self.zmin_spin.setDecimals(4)
        self.zmin_spin.setEnabled(False)
        row2.addWidget(self.zmin_spin)

        row2.addWidget(QLabel("Max:"))
        self.zmax_spin = QDoubleSpinBox()
        self.zmax_spin.setRange(-1e9, 1e9)
        self.zmax_spin.setDecimals(4)
        self.zmax_spin.setEnabled(False)
        row2.addWidget(self.zmax_spin)

        self.auto_range_button = QPushButton("Auto Range")
        row2.addWidget(self.auto_range_button)
        row2.addStretch(1)
        # Color controls are placed in the shared left settings column.

        self.plot_2d_widget = Plot2DWidget()
        self.plot_2d_splitter = QSplitter(Qt.Vertical)
        self.plot_2d_splitter.addWidget(self.plot_2d_widget)
        self.plot_2d_splitter.setSizes([740])
        self.plot_2d_splitter.setStretchFactor(0, 1)
        layout.addWidget(self.plot_2d_splitter)

        # signal wiring
        self.z_combo_2d.currentIndexChanged.connect(self._on_z_2d_changed)
        self.x_combo_2d.currentIndexChanged.connect(self._on_x_2d_changed)
        self.y_combo_2d.currentIndexChanged.connect(self._rebuild_2d_plot)
        self.transform_combo_2d.currentIndexChanged.connect(self._rebuild_2d_plot)
        self.transform_combo_2d.activated.connect(self._note_data_operation)
        self.colormap_combo.currentTextChanged.connect(self._on_colormap_changed)
        self.auto_range_checkbox.toggled.connect(self._on_auto_range_toggled)
        self.zmin_spin.valueChanged.connect(self._on_range_spin_changed)
        self.zmax_spin.valueChanged.connect(self._on_range_spin_changed)
        self.auto_range_button.clicked.connect(self._on_auto_range_button_clicked)
        self.plot_2d_widget.hover_moved.connect(
            lambda x, y, z: self._on_cut_source_hover(self.plot_2d_widget, x, y, z)
        )
        self.plot_2d_widget.color_range_changed.connect(self._on_colorbar_range_changed)

        return page

    def _build_nd_page(self) -> QWidget:
        """3D Surface page backed by the existing general N-D slice model."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.z_combo_nd = QComboBox()
        self.x_combo_nd = QComboBox()
        self.y_combo_nd = QComboBox()
        for combo in (self.z_combo_nd, self.x_combo_nd, self.y_combo_nd):
            combo.setMinimumWidth(0)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(10)
        self.transform_combo_nd = QComboBox()
        for key in VALID_TRANSFORMS:
            self.transform_combo_nd.addItem(TRANSFORM_LABELS.get(key, key), userData=key)
        self.transform_combo_nd.addItem("Unwrapped Phase (deg)", userData="phase_unwrapped_deg")
        self.transform_combo_nd.addItem("Unwrapped Phase (rad)", userData="phase_unwrapped_rad")
        self.transform_combo_nd.setCurrentIndex(list(VALID_TRANSFORMS).index("magnitude_db"))
        self.geometry_combo = QComboBox()
        for geometry in GeometryType:
            self.geometry_combo.addItem(geometry.value, userData=geometry.value)
        self.geometry_combo.setObjectName("threeDGeometryType")
        self.surface_rendering_combo = QComboBox()
        for label_key, policy in (
            ("viewer.policy_auto", "Auto"),
            ("viewer.policy_full", "Full Resolution"),
            ("viewer.policy_adaptive", "Adaptive LOD"),
            ("viewer.policy_performance", "Performance"),
        ):
            self.surface_rendering_combo.addItem(self.localizer.text(label_key), userData=policy)
        self.surface_z_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.surface_z_scale_slider.setRange(1, 100)
        self.surface_z_scale_slider.setValue(10)
        self.surface_z_scale_slider.setEnabled(False)
        self.surface_z_scale_slider.setToolTip(self.localizer.text("viewer.z_scale_range"))
        self.surface_z_scale_label = QLabel("Auto")
        self.surface_z_scale_label.setMinimumWidth(42)
        self.surface_z_auto_checkbox = QCheckBox(self.localizer.text("viewer.auto"))
        self.surface_z_auto_checkbox.setChecked(True)
        self.surface_z_reset_button = QPushButton(self.localizer.text("viewer.reset_z_scale"))
        self.surface_projection_combo = QComboBox()
        self.surface_projection_combo.addItem(
            self.localizer.text("viewer.perspective"), userData="perspective"
        )
        self.surface_projection_combo.addItem(
            self.localizer.text("viewer.orthographic"), userData="orthographic"
        )
        self.surface_color_source_combo = QComboBox()
        self.surface_color_source_combo.addItem(
            self.localizer.text("viewer.same_as_height"), userData=None
        )
        self.surface_color_transform_combo = QComboBox()
        for key in VALID_TRANSFORMS:
            self.surface_color_transform_combo.addItem(TRANSFORM_LABELS.get(key, key), userData=key)
        self.surface_color_transform_combo.addItem("Unwrapped Phase (deg)", userData="phase_unwrapped_deg")
        self.surface_color_transform_combo.addItem("Unwrapped Phase (rad)", userData="phase_unwrapped_rad")
        self.surface_color_transform_combo.setCurrentIndex(list(VALID_TRANSFORMS).index("magnitude_db"))
        self.phase_unwrap_axis_combo = QComboBox()
        self.phase_unwrap_axis_combo.addItem("Along X", userData=1)
        self.phase_unwrap_axis_combo.addItem("Along Y", userData=0)
        self.surface_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.surface_opacity_slider.setRange(0, 100)
        self.surface_opacity_slider.setValue(100)
        self.surface_opacity_slider.setEnabled(True)
        self.surface_opacity_slider.setToolTip(
            "Qt Surface transparency may remain opaque in both the Viewer and exported scene."
        )
        self.surface_opacity_label = QLabel("100%")
        self.surface_b_transform_combo = QComboBox()
        for key in ("real", "imag", "magnitude", "phase_deg", "phase_rad",
                    "phase_unwrapped_deg", "phase_unwrapped_rad"):
            self.surface_b_transform_combo.addItem(TRANSFORM_LABELS_3D[key], userData=key)
        self.surface_b_transform_combo.setCurrentIndex(self.surface_b_transform_combo.findData("imag"))
        self.surface_b_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.surface_b_opacity_slider.setRange(0, 100)
        self.surface_b_opacity_slider.setValue(55)
        self.surface_b_opacity_slider.setEnabled(True)
        self.surface_b_opacity_slider.setToolTip(
            "Qt Surface transparency may remain opaque in both the Viewer and exported scene."
        )
        self.surface_b_opacity_label = QLabel("55%")
        self.surface_projection_checkbox = QCheckBox("Bottom Projection")
        self.surface_reference_combo = QComboBox()
        for label, mode in (("Off", "off"), ("Minimum", "minimum"),
                            ("Custom", "custom"), ("Zero Plane", "zero")):
            self.surface_reference_combo.addItem(label, userData=mode)
        self.surface_reference_value = QDoubleSpinBox()
        self.surface_reference_value.setRange(-1e12, 1e12)
        self.surface_reference_value.setDecimals(6)
        self.surface_reference_value.setEnabled(False)
        self.surface_top_colorbar_checkbox = QCheckBox("Top Interactive Colorbar")
        self.point_x_mapping_combo = QComboBox()
        self.point_y_mapping_combo = QComboBox()
        self.point_z_mapping_combo = QComboBox()
        self.point_color_mapping_combo = QComboBox()
        self.point_size_spin = QDoubleSpinBox()
        self.point_size_spin.setRange(0.001, 0.2)
        self.point_size_spin.setSingleStep(0.005)
        self.point_size_spin.setDecimals(3)
        self.point_size_spin.setValue(0.018)
        self.colormap_combo_nd = QComboBox()
        self.colormap_combo_nd.addItems(COLORMAPS)
        self.auto_range_checkbox_nd = QCheckBox(self.localizer.text("viewer.auto"))
        self.auto_range_checkbox_nd.setChecked(True)
        self.zmin_spin_nd = QDoubleSpinBox()
        self.zmin_spin_nd.setRange(-1e9, 1e9)
        self.zmin_spin_nd.setDecimals(4)
        self.zmin_spin_nd.setEnabled(False)
        self.zmax_spin_nd = QDoubleSpinBox()
        self.zmax_spin_nd.setRange(-1e9, 1e9)
        self.zmax_spin_nd.setDecimals(4)
        self.zmax_spin_nd.setEnabled(False)
        self.auto_range_button_nd = QPushButton(self.localizer.text("viewer.auto_range"))

        # The 1D and heatmap widgets remain available as data/overlay helpers;
        # Plot Mode exposes only this Surface page for the third mode.
        self.nd_plot_stack = QStackedWidget()
        self.plot_widget_nd = Plot1DWidget()
        self.plot_2d_widget_nd = Plot2DWidget()
        self.nd_surface_host = QWidget()
        self.nd_surface_layout = QVBoxLayout(self.nd_surface_host)
        self.nd_surface_layout.setContentsMargins(0, 0, 0, 0)
        self.nd_surface_placeholder = QLabel(self.localizer.text("viewer.surface_3d"))
        self.nd_surface_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.nd_surface_layout.addWidget(self.nd_surface_placeholder)
        self.nd_surface_renderer = None
        self._nd_height_grid = None
        self.nd_plot_stack.addWidget(self.plot_widget_nd)
        self.nd_plot_stack.addWidget(self.plot_2d_widget_nd)
        self.nd_plot_stack.addWidget(self.nd_surface_host)
        self.nd_plot_stack.setCurrentIndex(2)
        layout.addWidget(self.nd_plot_stack, 1)
        self.slice_explorer = SliceExplorerWidget()
        self._nd_dims: list = []
        self._confirmed_full_resolution_shapes: set[tuple[int, int]] = set()
        self._surface_controls_width = 280
        self._surface_z_scale_timer = QTimer(self)
        self._surface_z_scale_timer.setSingleShot(True)
        self._surface_z_scale_timer.setInterval(100)
        self._surface_z_scale_timer.timeout.connect(self._apply_surface_z_scale)

        # signal wiring
        self.z_combo_nd.currentIndexChanged.connect(self._on_z_nd_changed)
        self.x_combo_nd.currentIndexChanged.connect(self._on_x_nd_changed)
        self.y_combo_nd.currentIndexChanged.connect(self._on_xy_nd_changed)
        self.transform_combo_nd.currentIndexChanged.connect(self._rebuild_nd_plot)
        self.transform_combo_nd.activated.connect(self._note_data_operation)
        self.geometry_combo.currentIndexChanged.connect(self._on_3d_geometry_changed)
        self.phase_unwrap_axis_combo.currentIndexChanged.connect(self._rebuild_nd_plot)
        self.surface_opacity_slider.valueChanged.connect(self._on_surface_opacity_changed)
        self.surface_b_opacity_slider.valueChanged.connect(self._on_surface_b_opacity_changed)
        self.surface_b_transform_combo.currentIndexChanged.connect(self._rebuild_nd_plot)
        self.surface_projection_checkbox.toggled.connect(self._on_surface_projection_toggled)
        self.surface_reference_combo.currentIndexChanged.connect(self._on_surface_reference_changed)
        self.surface_reference_value.valueChanged.connect(self._on_surface_reference_changed)
        self.surface_top_colorbar_checkbox.toggled.connect(self._on_top_colorbar_toggled)
        for combo in (self.point_x_mapping_combo, self.point_y_mapping_combo,
                      self.point_z_mapping_combo, self.point_color_mapping_combo):
            combo.currentIndexChanged.connect(self._rebuild_nd_plot)
        self.point_size_spin.valueChanged.connect(self._rebuild_nd_plot)
        self.colormap_combo_nd.currentTextChanged.connect(self._on_colormap_nd_changed)
        self.auto_range_checkbox_nd.toggled.connect(self._on_auto_range_nd_toggled)
        self.zmin_spin_nd.valueChanged.connect(self._on_range_spin_nd_changed)
        self.zmax_spin_nd.valueChanged.connect(self._on_range_spin_nd_changed)
        self.auto_range_button_nd.clicked.connect(self._on_auto_range_nd_button_clicked)
        self.surface_color_source_combo.currentIndexChanged.connect(self._rebuild_nd_color_source)
        self.surface_color_transform_combo.currentIndexChanged.connect(self._rebuild_nd_color_source)
        self.surface_rendering_combo.currentIndexChanged.connect(self._apply_surface_rendering_policy)
        self.surface_z_scale_slider.valueChanged.connect(self._on_surface_z_scale_changed)
        self.surface_z_auto_checkbox.toggled.connect(self._on_surface_z_auto_toggled)
        self.surface_z_reset_button.clicked.connect(self._reset_surface_z_scale)
        self.surface_projection_combo.currentIndexChanged.connect(self._apply_surface_projection)
        self.slice_explorer.slice_changed.connect(self._rebuild_nd_plot)
        self.plot_2d_widget_nd.hover_moved.connect(
            lambda x, y, z: self._on_cut_source_hover(self.plot_2d_widget_nd, x, y, z)
        )

        return page

    def _on_mode_changed(self, index: int) -> None:
        self._cancel_mark_placement()
        if (index != 2 and getattr(self, "nd_surface_renderer", None) is not None
                and self.nd_surface_renderer.is_preparing):
            self.nd_surface_renderer.clear()
        if index == 2:
            with QSignalBlocker(self.pane_layout_combo):
                self.pane_layout_combo.setCurrentIndex(0)
            self._leave_multi_pane()
            with QSignalBlocker(self.mode_combo):
                self.mode_combo.setCurrentIndex(2)
            self.plot_interaction_controls.set_multi_pane_available(False)
        self.pane_controls_host.setVisible(index != 2)
        if not self.multi_pane_splitter.isHidden() and not self._multi_pane_loading:
            self._set_surface_layout_mode(False)
            self.plot_controls_stack.setCurrentIndex(index)
            self._refresh_formula_availability()
            self._capture_active_pane_state()
            self._render_multi_panes()
            return
        self._set_surface_layout_mode(index == 2)
        self.mode_stack.setCurrentIndex(index)
        if index == 2:
            self._journal_operation("3D Surface rendering requested")
        if hasattr(self, "plot_controls_stack"):
            self.plot_controls_stack.setCurrentIndex(index)
        if index == 0:
            self.update_plot()  # refresh table to reflect 1D Plot page's current selection
        elif index == 1:
            self._clear_data_table()
            self._rebuild_2d_plot()
        elif index == 2:
            self.nd_plot_stack.setCurrentIndex(2)
            self._rebuild_nd_plot()
            self._apply_surface_projection()
        self._refresh_formula_availability()
        self._refresh_mark_ui()
        self._refresh_cut_windows()

    def _build_marks_widget(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.marks_info_label = QLabel("No marks")
        layout.addWidget(self.marks_info_label)

        self.show_mark_values_checkbox = QCheckBox("Show values on plot")
        self.show_mark_values_checkbox.setChecked(True)
        self.show_mark_values_checkbox.toggled.connect(self._refresh_mark_ui)
        layout.addWidget(self.show_mark_values_checkbox)

        self.marks_table = QTableWidget()
        self.marks_table.setColumnCount(1)
        self.marks_table.setHorizontalHeaderLabels(["Object"])
        self.marks_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.marks_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.marks_table.setSelectionMode(QTableWidget.SingleSelection)
        self.marks_table.setAlternatingRowColors(True)
        self.marks_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.marks_table.itemSelectionChanged.connect(self._on_mark_table_selection_changed)
        self.marks_table.itemChanged.connect(self._on_mark_visibility_changed)
        layout.addWidget(self.marks_table)

        self.selected_mark_details = QLabel("Select an object to inspect its values.")
        self.selected_mark_details.setWordWrap(True)
        self.selected_mark_details.setStyleSheet("padding: 3px 1px;")
        layout.addWidget(self.selected_mark_details)

        self.numeric_editor = QWidget()
        editor = QFormLayout(self.numeric_editor)
        editor.setContentsMargins(0, 4, 0, 0)
        self.numeric_x_edit = QLineEdit()
        self.numeric_y_edit = QLineEdit()
        self.numeric_x2_edit = QLineEdit()
        self.numeric_x_label = QLabel("X")
        self.numeric_y_label = QLabel("Y")
        self.numeric_x2_label = QLabel("End")
        editor.addRow(self.numeric_x_label, self.numeric_x_edit)
        editor.addRow(self.numeric_y_label, self.numeric_y_edit)
        editor.addRow(self.numeric_x2_label, self.numeric_x2_edit)
        self.numeric_apply_button = QPushButton("Apply Position")
        self.numeric_apply_button.clicked.connect(self._apply_numeric_position)
        editor.addRow(self.numeric_apply_button)
        self.numeric_error_label = QLabel("")
        self.numeric_error_label.setStyleSheet("color: %s;" % STATUS["error"])
        self.numeric_error_label.setWordWrap(True)
        editor.addRow(self.numeric_error_label)
        layout.addWidget(self.numeric_editor)
        self.numeric_editor.hide()

        layout.addWidget(self._section_label("LOCAL ANALYSIS"))
        self.analysis_target_label = QLabel("Target: select a Point Mark or Range")
        self.analysis_target_label.setWordWrap(True)
        layout.addWidget(self.analysis_target_label)
        analysis_form = QFormLayout()
        analysis_form.setContentsMargins(0, 0, 0, 0)
        self.analysis_region_combo = QComboBox()
        self.analysis_region_combo.addItems(REGION_MODES)
        analysis_form.addRow("Region", self.analysis_region_combo)
        self.analysis_operation_combo = QComboBox()
        self.analysis_operation_combo.addItems(OPERATIONS)
        analysis_form.addRow("Operation", self.analysis_operation_combo)
        self.analysis_window_spin = QSpinBox()
        self.analysis_window_spin.setRange(1, 100000)
        self.analysis_window_spin.setValue(20)
        self.analysis_window_spin.setSuffix(" samples")
        self.analysis_window_label = QLabel("Window")
        analysis_form.addRow(self.analysis_window_label, self.analysis_window_spin)
        self.analysis_between_widget = QWidget()
        between_layout = QHBoxLayout(self.analysis_between_widget)
        between_layout.setContentsMargins(0, 0, 0, 0)
        between_layout.setSpacing(4)
        self.analysis_start_mark_combo = QComboBox()
        self.analysis_end_mark_combo = QComboBox()
        between_layout.addWidget(self.analysis_start_mark_combo)
        between_layout.addWidget(QLabel("to"))
        between_layout.addWidget(self.analysis_end_mark_combo)
        self.analysis_between_label = QLabel("Marks")
        analysis_form.addRow(self.analysis_between_label, self.analysis_between_widget)
        layout.addLayout(analysis_form)
        preview_buttons = QHBoxLayout()
        self.analysis_preview_button = QPushButton("Preview Region")
        self.analysis_preview_button.clicked.connect(self._preview_analysis_region)
        self.analysis_clear_preview_button = QPushButton("Clear Preview")
        self.analysis_clear_preview_button.clicked.connect(self._clear_analysis_preview)
        preview_buttons.addWidget(self.analysis_preview_button)
        preview_buttons.addWidget(self.analysis_clear_preview_button)
        layout.addLayout(preview_buttons)
        self.analysis_find_button = QPushButton("Find")
        self.analysis_find_button.clicked.connect(self._run_local_analysis)
        layout.addWidget(self.analysis_find_button)
        self.analysis_result_label = QLabel("Select a target, then Find.")
        self.analysis_result_label.setWordWrap(True)
        self.analysis_result_label.setMinimumHeight(54)
        layout.addWidget(self.analysis_result_label)
        self.analysis_region_combo.currentTextChanged.connect(
            self._on_analysis_configuration_changed
        )
        self.analysis_operation_combo.currentTextChanged.connect(
            self._on_analysis_configuration_changed
        )
        self.analysis_window_spin.valueChanged.connect(
            self._on_analysis_configuration_changed
        )
        self.analysis_start_mark_combo.currentIndexChanged.connect(
            self._on_analysis_configuration_changed
        )
        self.analysis_end_mark_combo.currentIndexChanged.connect(
            self._on_analysis_configuration_changed
        )
        return container

    def _initialize_mark_overlays(self) -> None:
        self._mark_overlays = {
            0: MarkOverlay(
                self.plot_widget.plot_widget.getPlotItem(),
                self.plot_widget.plot_widget,
                self,
            ),
            1: MarkOverlay(
                self.plot_2d_widget.plot_item,
                self.plot_2d_widget.graphics_widget,
                self,
            ),
        }
        for mode, overlay in self._mark_overlays.items():
            overlay.place_requested.connect(
                lambda x, y, plot_mode=mode: self._place_mark(plot_mode, x, y)
            )
            overlay.move_requested.connect(
                lambda number, x, y, plot_mode=mode:
                self._move_mark(plot_mode, number, x, y)
            )
            overlay.move_preview_requested.connect(
                lambda number, x, y, plot_mode=mode:
                self._live_move_mark(plot_mode, number, x, y)
            )
            overlay.annotation_moved.connect(
                lambda object_id, x, y, x2, plot_mode=mode:
                self._move_annotation(plot_mode, object_id, x, y, x2)
            )
            overlay.annotation_preview.connect(
                lambda object_id, x, y, x2, plot_mode=mode:
                self._live_move_annotation(plot_mode, object_id, x, y, x2)
            )
        self._refresh_mark_ui()

    # ---- Multi-Pane Viewer -------------------------------------------------

    def _initialize_multi_pane(self) -> None:
        """Seed Pane 1 from the accepted single-view state.

        Pane layouts are intentionally session-only.  They all render through
        ``self.cached`` and never own an Experiment or an HDF5 reader.
        """
        state = PaneState(1)
        state.mark_managers = self._mark_managers
        self._pane_states = {1: state}

    @staticmethod
    def _pane_count(layout_name: str) -> int:
        return {
            ONE_PANE: 1, TWO_SIDE: 2, TWO_STACKED: 2,
            THREE_PANES: 3, FOUR_PANES: 4,
        }.get(layout_name, 1)

    def _capture_active_pane_state(self) -> None:
        if self._multi_pane_loading or self._active_pane_id not in self._pane_states:
            return
        state = self._pane_states[self._active_pane_id]
        state.plot_mode = min(self.mode_combo.currentIndex(), 1)
        state.x_axis = self._axis_ref_dict(self.x_combo.currentData())
        state.y_axis = self._axis_ref_dict(self.y_combo.currentData())
        state.transform_name = self.transform_combo.currentText() or "Magnitude"
        state.db = self.db_checkbox.isChecked()
        state.unwrap = self.unwrap_checkbox.isChecked()
        state.show_data_points = self.show_data_points_checkbox.isChecked()
        state.point_size_mode = self.point_size_mode_combo.currentText() or "Auto"
        state.manual_point_size = self.manual_point_size_spin.value()
        state.trace_index = self.log_entries.current_row()
        state.z_name = self.z_combo_2d.currentText()
        state.grid_x_name = self.x_combo_2d.currentText()
        state.grid_y_name = self.y_combo_2d.currentText()
        state.grid_transform = self.transform_combo_2d.currentData() or "raw"
        state.colormap = self.colormap_combo.currentText() or DEFAULT_COLORMAP
        state.auto_color = self.auto_range_checkbox.isChecked()
        state.color_min = self.zmin_spin.value()
        state.color_max = self.zmax_spin.value()
        if not self.multi_pane_splitter.isHidden():
            frame = self._pane_frames.get(self._active_pane_id)
            view_box = (frame.plot_2d.view_box if state.plot_mode == 1 else
                        frame.plot_1d.plot_widget.getViewBox()) if frame else None
        elif state.plot_mode == 1:
            view_box = self.plot_2d_widget.view_box
        else:
            view_box = self.plot_widget.plot_widget.getViewBox()
        if view_box is not None:
            try:
                ranges = view_box.viewRange()
                state.x_range = tuple(float(value) for value in ranges[0])
                state.y_range = tuple(float(value) for value in ranges[1])
            except (TypeError, ValueError, IndexError):
                pass

    def _default_pane_state(self, pane_id: int) -> PaneState:
        base = self._pane_states[1]
        state = PaneState(
            pane_id, plot_mode=base.plot_mode, x_axis=base.x_axis, y_axis=base.y_axis,
            transform_name=base.transform_name, db=base.db, unwrap=base.unwrap,
            show_data_points=base.show_data_points,
            point_size_mode=base.point_size_mode,
            manual_point_size=base.manual_point_size,
            trace_index=base.trace_index, z_name=base.z_name,
            grid_x_name=base.grid_x_name, grid_y_name=base.grid_y_name,
            grid_transform=base.grid_transform, colormap=base.colormap,
            auto_color=base.auto_color, color_min=base.color_min,
            color_max=base.color_max, x_range=base.x_range, y_range=base.y_range,
            x_formula=base.x_formula, y_formula=base.y_formula,
            formula_enabled=base.formula_enabled,
        )
        if pane_id == 2 and self.transform_combo.findText("Phase") >= 0:
            state.transform_name, state.db, state.unwrap = "Phase", False, False
        elif pane_id == 3:
            iq = self._built_in_iq_preset()
            if iq is not None:
                state.x_axis = {
                    "name": iq.x_axis.name, "source": iq.x_axis.source,
                    "base_channel": iq.x_axis.base_channel,
                    "transform_key": iq.x_axis.transform_key,
                }
                state.y_axis = {
                    "name": iq.y_axis.name, "source": iq.y_axis.source,
                    "base_channel": iq.y_axis.base_channel,
                    "transform_key": iq.y_axis.transform_key,
                }
                state.transform_name, state.db, state.unwrap = "Magnitude", False, False
        elif pane_id == 4 and state.z_name and state.grid_x_name and state.grid_y_name:
            state.plot_mode = 1
        return state

    def _create_pane_frame(self, pane_id: int) -> PaneFrame:
        frame = PaneFrame(pane_id, self.pane_grid_host)
        self._register_export_surface(
            frame.plot_1d.plot_widget, frame.plot_1d.plot_widget.getPlotItem(), pane_id
        )
        self._register_export_surface(frame.plot_2d.graphics_widget, frame.plot_2d.plot_item, pane_id)
        frame.activated.connect(self._activate_pane)
        frame.x_range_changed.connect(self._on_pane_x_range_changed)
        frame.hover_moved.connect(
            lambda _pane_id, x, y, z, source=frame.plot_2d:
            self._on_cut_source_hover(source, x, y, z)
        )
        frame.plot_2d.color_range_changed.connect(
            lambda low, high, p=pane_id: self._on_pane_colorbar_range_changed(p, low, high)
        )
        for mode, overlay in frame.mark_overlays.items():
            overlay.place_requested.connect(
                lambda x, y, p=pane_id, m=mode: self._pane_place_mark(p, m, x, y)
            )
            overlay.move_requested.connect(
                lambda number, x, y, p=pane_id, m=mode:
                self._pane_move_mark(p, m, number, x, y, False)
            )
            overlay.move_preview_requested.connect(
                lambda number, x, y, p=pane_id, m=mode:
                self._pane_move_mark(p, m, number, x, y, True)
            )
            overlay.annotation_moved.connect(
                lambda object_id, x, y, x2, p=pane_id, m=mode:
                self._pane_move_annotation(p, m, object_id, x, y, x2, False)
            )
            overlay.annotation_preview.connect(
                lambda object_id, x, y, x2, p=pane_id, m=mode:
                self._pane_move_annotation(p, m, object_id, x, y, x2, True)
            )
        return frame

    def _on_pane_colorbar_range_changed(self, pane_id: int, low: float, high: float) -> None:
        """Persist a manual ColorBarItem range in that pane's local state."""
        if self._multi_pane_loading:
            return
        state = self._pane_states.get(pane_id)
        if state is None:
            return
        state.auto_color = False
        state.color_min = float(low)
        state.color_max = float(high)
        if pane_id == self._active_pane_id and not self.multi_pane_splitter.isHidden():
            with QSignalBlocker(self.auto_range_checkbox), QSignalBlocker(self.zmin_spin), QSignalBlocker(self.zmax_spin):
                self.auto_range_checkbox.setChecked(False)
                self.zmin_spin.setValue(low)
                self.zmax_spin.setValue(high)
            self.zmin_spin.setEnabled(True)
            self.zmax_spin.setEnabled(True)
        if not self._restoring_session:
            self.workspace_state_changed.emit()

    def _pane_place_mark(self, pane_id: int, mode: int, x: float, y: float) -> None:
        self._activate_pane(pane_id)
        self._place_mark(mode, x, y)

    def _pane_move_mark(self, pane_id: int, mode: int, number: int,
                        x: float, y: float, preview: bool) -> None:
        self._activate_pane(pane_id)
        (self._live_move_mark if preview else self._move_mark)(mode, number, x, y)

    def _pane_move_annotation(self, pane_id: int, mode: int, object_id: str,
                              x: float, y: float, x2: float | None,
                              preview: bool) -> None:
        self._activate_pane(pane_id)
        (self._live_move_annotation if preview else self._move_annotation)(
            mode, object_id, x, y, x2
        )

    def _on_pane_layout_changed(self, layout_name: str) -> None:
        if not hasattr(self, "mode_stack"):
            return
        count = self._pane_count(layout_name)
        self.plot_interaction_controls.set_multi_pane_available(count > 1)
        if count == 1:
            self._leave_multi_pane()
            self._update_export_action_labels()
            self._schedule_view_all_after_layout(layout_name)
            return
        self._enter_multi_pane(count)
        self._layout_panes(layout_name)
        self._render_multi_panes()
        # Reparenting a trace panel changes the splitter's available height
        # only after Qt has laid out the new pane tree.  Reapply the saved
        # proportional choice on that next layout pass, not as a fixed preset.
        QTimer.singleShot(0, lambda ratio=self._trace_area_ratio: self._restore_trace_area_ratio(ratio))
        self._update_export_action_labels()
        self._schedule_view_all_after_layout(layout_name)

    def _schedule_view_all_after_layout(self, layout_name: str) -> None:
        if self._restoring_session:
            return
        QTimer.singleShot(
            0, lambda name=layout_name: QTimer.singleShot(
                0, lambda: self._view_all_after_layout(name)
            )
        )

    def _view_all_after_layout(self, layout_name: str) -> None:
        if (self._restoring_session or self.experiment is None
                or self.pane_layout_combo.currentText() != layout_name):
            return
        if self.multi_pane_splitter.isHidden():
            mode = self.mode_combo.currentIndex()
            if mode == 1 and self.plot_2d_widget._grid is not None:
                view_box = self.plot_2d_widget.view_box
            elif mode == 0 and self.plot_widget._trace_data:
                view_box = self.plot_widget.plot_widget.getViewBox()
            else:
                return
            view_box.autoRange(padding=0.02)
            self.workspace_state_changed.emit()
            return

        count = self._pane_count(layout_name)
        was_syncing = self._syncing_pane_view
        self._syncing_pane_view = True
        try:
            for pane_id in range(1, count + 1):
                state = self._pane_states.get(pane_id)
                frame = self._pane_frames.get(pane_id)
                if state is None or frame is None:
                    continue
                if state.plot_mode == 1 and frame.plot_2d._grid is not None:
                    view_box = frame.plot_2d.view_box
                elif state.plot_mode == 0 and frame.plot_1d._trace_data:
                    view_box = frame.plot_1d.plot_widget.getViewBox()
                else:
                    continue
                view_box.autoRange(padding=0.02)
                ranges = view_box.viewRange()
                state.x_range = tuple(float(value) for value in ranges[0])
                state.y_range = tuple(float(value) for value in ranges[1])
        finally:
            self._syncing_pane_view = was_syncing
        if self.sync_x_checkbox.isChecked():
            source = self._pane_states.get(self._active_pane_id)
            if source is not None and source.plot_mode == 0 and source.x_range is not None:
                for pane_id in range(1, count + 1):
                    other = self._pane_states.get(pane_id)
                    frame = self._pane_frames.get(pane_id)
                    if (other is None or frame is None or pane_id == self._active_pane_id
                            or other.plot_mode != 0 or other.x_axis != source.x_axis):
                        continue
                    other.x_range = source.x_range
                    frame.plot_1d.plot_widget.setXRange(*source.x_range, padding=0)
        self._capture_active_pane_state()
        self.workspace_state_changed.emit()

    def _enter_multi_pane(self, count: int) -> None:
        if self.multi_pane_splitter.isHidden():
            self._capture_active_pane_state()
            self._remember_trace_area_ratio()
            self._single_mark_managers = self._mark_managers
            self._single_mark_overlays = self._mark_overlays
            self.multi_pane_splitter.addWidget(self.trace_management_splitter)
            self._restore_trace_area_ratio(self._trace_area_ratio)
            self.mode_stack.hide()
            self.multi_pane_splitter.show()
        for pane_id in range(1, count + 1):
            if pane_id not in self._pane_states:
                self._pane_states[pane_id] = self._default_pane_state(pane_id)
            if pane_id not in self._pane_frames:
                self._pane_frames[pane_id] = self._create_pane_frame(pane_id)
            self._pane_frames[pane_id].show()
        for pane_id, frame in self._pane_frames.items():
            if pane_id > count:
                frame.hide()
        if self._active_pane_id > count:
            self._active_pane_id = 1
        self._activate_pane(self._active_pane_id, capture=False)

    def _leave_multi_pane(self) -> None:
        if self.multi_pane_splitter.isHidden():
            return
        self._capture_active_pane_state()
        self._remember_trace_area_ratio()
        self.plot_1d_splitter.addWidget(self.trace_management_splitter)
        self._restore_trace_area_ratio(self._trace_area_ratio)
        self.multi_pane_splitter.hide()
        self.mode_stack.show()
        state = self._pane_states[1]
        self._mark_managers = state.mark_managers
        self._mark_overlays = self._single_mark_overlays
        self._active_pane_id = 1
        self.active_pane_label.setText("Active Pane: 1")     # was left at the last active pane
        self._load_pane_controls(state)
        self.update_plot()

    def _layout_panes(self, layout_name: str) -> None:
        self._clear_pane_layout()
        count = self._pane_count(layout_name)
        frames = [self._pane_frames[pane_id] for pane_id in range(1, count + 1)]
        for frame in frames:
            frame.show()
        if len(frames) == 1:
            self.pane_grid.addWidget(frames[0])
            return
        root = self._new_pane_splitter(Qt.Horizontal if layout_name == TWO_SIDE else Qt.Vertical)
        if layout_name == TWO_SIDE or layout_name == TWO_STACKED:
            root.addWidget(frames[0])
            root.addWidget(frames[1])
        elif layout_name == THREE_PANES:
            root.addWidget(frames[0])
            lower = self._new_pane_splitter(Qt.Horizontal)
            lower.addWidget(frames[1])
            lower.addWidget(frames[2])
            root.addWidget(lower)
        else:
            top_row = self._new_pane_splitter(Qt.Horizontal)
            bottom_row = self._new_pane_splitter(Qt.Horizontal)
            top_row.addWidget(frames[0])
            top_row.addWidget(frames[1])
            bottom_row.addWidget(frames[2])
            bottom_row.addWidget(frames[3])
            root.addWidget(top_row)
            root.addWidget(bottom_row)
        self._pane_layout_root = root
        self.pane_grid.addWidget(root)
        self._reset_pane_geometry()

    def _new_pane_splitter(self, orientation: Qt.Orientation) -> QSplitter:
        splitter = QSplitter(orientation)
        splitter.setChildrenCollapsible(True)
        splitter.setHandleWidth(5)
        self._pane_splitters.append(splitter)
        return splitter

    def _clear_pane_layout(self) -> None:
        """Detach pane frames before replacing splitter topology, never state."""
        for frame in self._pane_frames.values():
            if frame.parent() is not self.pane_grid_host:
                frame.setParent(self.pane_grid_host)
        while self.pane_grid.count():
            item = self.pane_grid.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._pane_layout_root:
                widget.setParent(None)
        if self._pane_layout_root is not None:
            self._pane_layout_root.deleteLater()
        self._pane_layout_root = None
        self._pane_splitters = []

    def _pane_splitter_sizes(self) -> list[list[int]]:
        return [splitter.sizes() for splitter in self._pane_splitters]

    def _restore_pane_splitter_sizes(self, sizes: list[list[int]]) -> None:
        for splitter, values in zip(self._pane_splitters, sizes):
            if len(values) == splitter.count() and sum(values) > 0:
                splitter.setSizes(values)

    def _reset_pane_geometry(self) -> None:
        """Reset only visual splitter proportions; plot and Mark state stay intact."""
        if self.multi_pane_splitter.isHidden():
            self.plot_2d_splitter.setSizes([560, 160])
            return
        for splitter in self._pane_splitters:
            splitter.setSizes([500] * splitter.count())

    def _activate_pane(self, pane_id: int, *, capture: bool = True) -> None:
        if pane_id not in self._pane_states:
            return
        if capture:
            self._capture_active_pane_state()
        self._active_pane_id = pane_id
        state = self._pane_states[pane_id]
        self._mark_managers = state.mark_managers
        frame = self._pane_frames.get(pane_id)
        if frame is not None:
            self._mark_overlays = frame.mark_overlays
        for key, item in self._pane_frames.items():
            item.set_active(key == pane_id)
        self.active_pane_label.setText(f"Active Pane: {pane_id}")
        self._load_pane_controls(state)
        self._refresh_mark_ui()
        self._refresh_cut_windows()

    def _load_pane_controls(self, state: PaneState) -> None:
        self._multi_pane_loading = True
        controlled = (
            self.mode_combo, self.x_combo, self.y_combo, self.transform_combo,
            self.db_checkbox, self.unwrap_checkbox, self.z_combo_2d,
            self.x_combo_2d, self.y_combo_2d, self.transform_combo_2d,
            self.colormap_combo, self.auto_range_checkbox,
            self.zmin_spin, self.zmax_spin, self.show_data_points_checkbox,
            self.point_size_mode_combo, self.manual_point_size_spin,
            self.x_formula_edit, self.y_formula_edit,
        )
        blockers = [QSignalBlocker(widget) for widget in controlled]
        try:
            self.mode_combo.setCurrentIndex(state.plot_mode)
            for combo, value in ((self.x_combo, state.x_axis), (self.y_combo, state.y_axis)):
                if value:
                    index = self._find_axis_ref_index(combo, AxisRef(**value))
                    if index >= 0:
                        combo.setCurrentIndex(index)
            index = self.transform_combo.findText(state.transform_name)
            if index >= 0:
                self.transform_combo.setCurrentIndex(index)
            self.db_checkbox.setChecked(state.db and self.db_checkbox.isEnabled())
            self.unwrap_checkbox.setChecked(state.unwrap and self.unwrap_checkbox.isEnabled())
            self.show_data_points_checkbox.setChecked(state.show_data_points)
            self.point_size_mode_combo.setCurrentText(state.point_size_mode)
            self.manual_point_size_spin.setValue(state.manual_point_size)
            for combo, text in ((self.z_combo_2d, state.z_name),
                                (self.x_combo_2d, state.grid_x_name),
                                (self.y_combo_2d, state.grid_y_name),
                                (self.colormap_combo, state.colormap)):
                if text and combo.findText(text) >= 0:
                    combo.setCurrentText(text)
            index = self.transform_combo_2d.findData(state.grid_transform)
            if index >= 0:
                self.transform_combo_2d.setCurrentIndex(index)
            self.auto_range_checkbox.setChecked(state.auto_color)
            self.zmin_spin.setValue(state.color_min)
            self.zmax_spin.setValue(state.color_max)
            self.x_formula_edit.setText(state.x_formula)
            self.y_formula_edit.setText(state.y_formula)
            self.plot_controls_stack.setCurrentIndex(state.plot_mode)
        finally:
            del blockers
            self._multi_pane_loading = False
        self._sync_modifier_checkboxes()
        self._configure_axis_transform_controls(sync_derived_y=False)
        self.db_checkbox.blockSignals(True)
        self.unwrap_checkbox.blockSignals(True)
        self.db_checkbox.setChecked(state.db and self.db_checkbox.isEnabled())
        self.unwrap_checkbox.setChecked(state.unwrap and self.unwrap_checkbox.isEnabled())
        self.db_checkbox.blockSignals(False)
        self.unwrap_checkbox.blockSignals(False)
        self.manual_point_size_spin.setEnabled(self.point_size_mode_combo.currentText() == "Manual")
        self._refresh_formula_previews()
        self._refresh_formula_availability()

    def _on_data_point_settings_changed(self, *_args) -> None:
        manual = self.point_size_mode_combo.currentText() == "Manual"
        self.manual_point_size_spin.setEnabled(manual)
        if self._multi_pane_loading:
            return
        if not self.multi_pane_splitter.isHidden():
            self._capture_active_pane_state()
            frame = self._pane_frames.get(self._active_pane_id)
            if frame is not None:
                state = self._pane_states[self._active_pane_id]
                frame.plot_1d.set_data_points(
                    state.show_data_points, state.point_size_mode, state.manual_point_size,
                )
            return
        self.plot_widget.set_data_points(
            self.show_data_points_checkbox.isChecked(),
            self.point_size_mode_combo.currentText(), self.manual_point_size_spin.value(),
        )

    @staticmethod
    def _sync_range_half_peak_visuals(manager: MarkManager, overlay: MarkOverlay) -> None:
        for annotation in manager.annotations():
            if annotation.annotation_type == RANGE and annotation.half_peak is None:
                overlay.clear_analysis_result(annotation.object_id)

    def _candidate_from_ref(self, value: dict | None) -> AxisCandidate | None:
        if not value:
            return None
        reference = AxisRef(**value)
        return next((item for item in self._axis_candidates
                     if self._axis_ref_matches(item, reference)), None)

    def _pane_transform_spec(self, state: PaneState) -> TransformSpec:
        base = next((item for item in self.transform_store.list_all()
                     if item.name == state.transform_name), None)
        if base is None:
            base = TransformSpec(name="Magnitude", base="magnitude")
        return TransformSpec(
            name=base.name, base=base.base,
            db=state.db if base.base == "magnitude" else False,
            unwrap=state.unwrap if base.base in ("phase_deg", "phase_rad") else False,
        )

    def _pane_axis_data(self, candidate: AxisCandidate, entry: int,
                        state: PaneState) -> np.ndarray:
        if candidate.domain == "entries":
            return np.asarray(self.cached.get_data(candidate.base_channel, transform="raw")).reshape(-1)
        if candidate.source == "trace_axis":
            return np.asarray(self.experiment.vector_traces[candidate.base_channel].x_values).reshape(-1)
        transform = candidate.transform_key or "raw" if candidate.source == "derived" else "raw"
        if candidate.source == "derived" and candidate.transform_key == "magnitude" and state.db:
            transform = "magnitude_db"
        values = self._entry_from_full_data(
            self.cached.get_data(candidate.base_channel, transform=transform), entry
        )
        if (candidate.source == "derived" and candidate.transform_key in ("phase_deg", "phase_rad")
                and state.unwrap):
            values = unwrap_phase(values, unit="deg" if candidate.transform_key == "phase_deg" else "rad")
        return np.asarray(values)

    def _pane_plot_arrays(self, x_cand: AxisCandidate, y_cand: AxisCandidate,
                          entry: int, state: PaneState, *, apply_formula: bool = True):
        x_data = self._pane_axis_data(x_cand, entry, state)
        spec = self._pane_transform_spec(state)
        if (self._is_transformable_y_candidate(y_cand)
                and not self._is_iq_axis_pair(x_cand, y_cand)):
            raw = self._entry_from_full_data(
                self.cached.get_data(y_cand.base_channel, transform="raw"), entry
            )
            y_data = spec.apply(raw)
            transform = spec.resolve_transform_key()
        else:
            y_data = self._pane_axis_data(y_cand, entry, state)
            transform = (
                "magnitude_db" if y_cand.source == "derived"
                and y_cand.transform_key == "magnitude" and state.db
                else (y_cand.transform_key or "raw")
            )
        if apply_formula and state.plot_mode == 0 and state.formula_enabled:
            x_data, y_data = apply_formulas(x_data, y_data, state.x_formula, state.y_formula)
        return x_data, y_data, transform

    def _render_multi_panes(self) -> None:
        if self.multi_pane_splitter.isHidden() or self.experiment is None:
            return
        self._capture_active_pane_state()
        count = self._pane_count(self.pane_layout_combo.currentText())
        was_syncing = self._syncing_pane_view
        self._syncing_pane_view = True
        try:
            for pane_id in range(1, count + 1):
                self._render_pane(pane_id)
        finally:
            self._syncing_pane_view = was_syncing
        self._refresh_mark_ui()
        self._refresh_cut_windows()

    def _render_pane(self, pane_id: int) -> None:
        state, frame = self._pane_states[pane_id], self._pane_frames[pane_id]
        frame.set_mode(state.plot_mode)
        if state.plot_mode == 1:
            if not (state.z_name and state.grid_x_name and state.grid_y_name):
                frame.clear()
                frame.set_title("2D · unavailable")
                return
            try:
                grid = self.cached.get_2d_data(
                    state.grid_x_name, state.grid_y_name, state.z_name,
                    transform=state.grid_transform,
                )
            except Exception as error:
                frame.set_title(f"2D · {error}")
                return
            frame.plot_2d.plot(
                grid, colormap=state.colormap,
                z_min=None if state.auto_color else state.color_min,
                z_max=None if state.auto_color else state.color_max,
            )
            if state.x_range is not None or state.y_range is not None:
                frame.plot_2d.view_box.setRange(
                    xRange=state.x_range, yRange=state.y_range, padding=0,
                )
            context = (self._current_data_key(), state.z_name,
                       state.grid_x_name, state.grid_y_name)
            manager = state.mark_managers[1]
            if manager.context_key != context:
                self._persist_mark_manager(manager, pane_id)
            context_changed = manager.set_2d_context(
                context,
                grid.x_values, grid.y_values, grid.z_values,
                x_name=grid.x_name, y_name=grid.y_name, value_name=grid.z_name,
                x_unit=grid.x_unit, y_unit=grid.y_unit,
                value_unit=self._transformed_unit(grid.transform, grid.z_unit),
            )
            if context_changed:
                self._restore_mark_manager(manager, pane_id)
            frame.mark_overlays[1].render(
                manager.marks(), manager.annotations(), manager.selected_id,
                self.show_mark_values_checkbox.isChecked(),
            )
            frame.set_title(f"2D · {state.z_name}")
            return

        x_cand, y_cand = self._candidate_from_ref(state.x_axis), self._candidate_from_ref(state.y_axis)
        if x_cand is None or y_cand is None or not self.mgr.axis_domains_compatible(x_cand, y_cand):
            frame.clear()
            frame.set_title("1D · unavailable axes")
            return
        active = (self.log_entries.current_row() if self.sync_trace_checkbox.isChecked()
                  else state.trace_index)
        state.trace_index = active
        selected = self.trace_selection.visible_selection()
        if x_cand.domain != "points" or y_cand.domain != "points":
            selected = (active,)
        traces = {}
        for trace in selected:
            try:
                x_data, y_data, _ = self._pane_plot_arrays(x_cand, y_cand, trace, state)
                traces[trace] = (x_data, y_data)
            except Exception:
                continue
        frame.plot_1d.plot_traces(
            traces, active_trace=active,
            x_label=(f"{x_cand.name} [Formula]" if state.formula_enabled and state.x_formula
                     else f"{x_cand.name} [{x_cand.unit or '-'}]"),
            y_label=(f"{y_cand.name} — Formula" if state.formula_enabled
                     else f"{y_cand.name}"), title=f"{y_cand.name} vs {x_cand.name}",
            trace_labels={trace: f"Trace {trace + 1}" for trace in traces},
            color_mode=self.trace_color_combo.currentText() or SEQUENTIAL,
            auto_range=state.x_range is None,
        )
        frame.plot_1d.set_data_points(
            state.show_data_points, state.point_size_mode, state.manual_point_size,
        )
        try:
            x_data, y_data, transform = self._pane_plot_arrays(x_cand, y_cand, active, state)
            context = (self._current_data_key(), self._axis_mark_identity(x_cand),
                       self._axis_mark_identity(y_cand), active)
            if state.formula_enabled:
                context += ("formula", state.x_formula, state.y_formula)
            manager = state.mark_managers[0]
            if manager.context_key != context:
                self._persist_mark_manager(manager, pane_id)
            context_changed = manager.set_1d_context(
                context,
                x_data, y_data, x_name=x_cand.name, y_name=y_cand.name,
                x_unit=None if state.formula_enabled and state.x_formula else x_cand.unit,
                y_unit=(None if state.formula_enabled and state.y_formula
                        else self._transformed_unit(transform, y_cand.unit)),
            )
            if context_changed:
                self._restore_mark_manager(manager, pane_id)
            self._sync_range_half_peak_visuals(manager, frame.mark_overlays[0])
            frame.mark_overlays[0].render(
                manager.marks(), manager.annotations(), manager.selected_id,
                self.show_mark_values_checkbox.isChecked(),
            )
        except Exception:
            pass
        if state.x_range is not None or state.y_range is not None:
            frame.plot_1d.plot_widget.getViewBox().setRange(
                xRange=state.x_range, yRange=state.y_range, padding=0,
            )
        frame.set_title(f"1D · {state.transform_name} · Trace {active + 1}")

    def _on_sync_trace_toggled(self, checked: bool) -> None:
        if checked:
            active = self.log_entries.current_row()
            for state in self._pane_states.values():
                state.trace_index = active
        self._render_multi_panes()

    def _on_pane_x_range_changed(self, pane_id: int, low: float, high: float) -> None:
        if self._syncing_pane_view or pane_id not in self._pane_states:
            return
        self._pane_states[pane_id].x_range = (low, high)
        if not self.sync_x_checkbox.isChecked():
            return
        self._syncing_pane_view = True
        try:
            count = self._pane_count(self.pane_layout_combo.currentText())
            source = self._pane_states[pane_id]
            for other_id in range(1, count + 1):
                other = self._pane_states[other_id]
                if other_id == pane_id or source.plot_mode != 0 or other.plot_mode != 0:
                    continue
                if source.x_axis != other.x_axis:
                    continue
                other.x_range = (low, high)
                self._pane_frames[other_id].plot_1d.plot_widget.setXRange(low, high, padding=0)
        finally:
            self._syncing_pane_view = False

    def _current_mark_mode(self) -> int | None:
        index = self.mode_combo.currentIndex()
        return index if index in self._mark_managers else None

    def _current_mark_manager(self) -> MarkManager | None:
        mode = self._current_mark_mode()
        return self._mark_managers.get(mode) if mode is not None else None

    def _current_mark_tool(self) -> str | None:
        return self._pending_mark_tool

    def _on_mark_tool_changed(self) -> None:
        tool = self.mark_tool_combo.currentData()
        if tool is None:
            self._cancel_mark_placement(reset_selector=False)
            return
        self._cancel_mark_placement(reset_selector=False)
        self._pending_mark_tool = tool
        manager = self._current_mark_manager()
        self.add_mark_button.setEnabled(bool(manager and manager.can_place))
        self.add_mark_button.setChecked(True)

    def _on_add_mark_toggled(self, checked: bool) -> None:
        mode = self._current_mark_mode()
        manager = self._current_mark_manager()
        tool = self._current_mark_tool()
        if checked and (mode is None or manager is None or not manager.can_place):
            self._cancel_mark_placement()
            return
        if checked and tool == POINT_MARK and len(manager.marks()) >= MAX_MARKS:
            self.statusBar().showMessage(f"Maximum of {MAX_MARKS} Marks reached.", 4000)
            self._cancel_mark_placement()
            return
        for plot_mode, overlay in self._mark_overlays.items():
            overlay.set_placement_mode(bool(checked and plot_mode == mode))
        if checked:
            prompt = "Click Start..." if tool == RANGE else "Click Plot..."
            self.add_mark_button.setText(prompt)
        else:
            self.add_mark_button.setText("Add")

    def _cancel_mark_placement(self, *, reset_selector: bool = True) -> None:
        self._range_starts.clear()
        for overlay in self._mark_overlays.values():
            overlay.set_placement_mode(False)
        self.add_mark_button.blockSignals(True)
        self.add_mark_button.setChecked(False)
        self.add_mark_button.blockSignals(False)
        self.add_mark_button.setText("Add")
        self._pending_mark_tool = None
        if reset_selector and self.mark_tool_combo.currentIndex() != 0:
            self.mark_tool_combo.blockSignals(True)
            self.mark_tool_combo.setCurrentIndex(0)
            self.mark_tool_combo.blockSignals(False)

    def _place_mark(self, mode: int, x: float, y: float) -> None:
        manager = self._mark_managers[mode]
        tool = self._current_mark_tool()
        if tool is None:
            self._cancel_mark_placement()
            return
        created = None
        if tool == RANGE and mode not in self._range_starts:
            self._range_starts[mode] = x
            self._mark_overlays[mode].show_range_preview(x)
            self.add_mark_button.setText("Click End...")
            return
        if tool == POINT_MARK:
            created = manager.add_nearest(x, y)
        elif tool == RANGE:
            created = manager.add_range(self._range_starts[mode], x)
        elif tool == HORIZONTAL_LINE:
            created = manager.add_horizontal_line(y)
        elif tool == VERTICAL_LINE:
            created = manager.add_vertical_line(x)
        elif tool == CROSSHAIR:
            created = manager.add_crosshair(x, y)
        self._cancel_mark_placement()
        if created is None:
            message = (f"Maximum of {MAX_MARKS} Marks reached."
                       if tool == POINT_MARK else "Could not create this tool.")
            self.statusBar().showMessage(message, 4000)
            return
        self._persist_mark_manager(manager)
        self._refresh_mark_ui()
        self._note_data_operation()

    def _note_data_operation(self) -> None:
        """A real operation on the open measurement (counts toward unlocking Personal colours)."""
        if self.experiment is None:
            return
        from app._guard import gate

        gate.note_operation(self.experiment.source_path)

    def _move_mark(self, mode: int, number: int, x: float, y: float) -> None:
        if self._mark_managers[mode].move_nearest(number, x, y) is not None:
            self._clear_analysis_preview()
            self._persist_mark_manager(self._mark_managers[mode])
            self._refresh_mark_ui()

    def _live_move_mark(self, mode: int, number: int, x: float, y: float) -> None:
        manager = self._mark_managers[mode]
        moved = manager.move_nearest(number, x, y)
        if moved is not None:
            self._mark_overlays[mode].update_object(
                moved, self.show_mark_values_checkbox.isChecked()
            )
            if mode == self._current_mark_mode():
                self._refresh_mark_readout_only()

    def _move_annotation(
        self, mode: int, object_id: str, x: float, y: float, x2: float,
    ) -> None:
        manager = self._mark_managers[mode]
        moved = manager.move_annotation(
            object_id,
            x=None if np.isnan(x) else x,
            y=None if np.isnan(y) else y,
            x2=None if np.isnan(x2) else x2,
        )
        if moved is not None:
            self._sync_range_half_peak_visuals(manager, self._mark_overlays[mode])
            self._clear_analysis_preview()
            self._sync_annotation_cut_position(mode, moved)
            self._persist_mark_manager(manager)
            self._refresh_mark_ui()

    def _live_move_annotation(
        self, mode: int, object_id: str, x: float, y: float, x2: float,
    ) -> None:
        manager = self._mark_managers[mode]
        moved = manager.move_annotation(
            object_id,
            x=None if np.isnan(x) else x,
            y=None if np.isnan(y) else y,
            x2=None if np.isnan(x2) else x2,
        )
        if moved is not None:
            self._sync_range_half_peak_visuals(manager, self._mark_overlays[mode])
            self._mark_overlays[mode].update_object(
                moved, self.show_mark_values_checkbox.isChecked()
            )
            self._sync_annotation_cut_position(mode, moved)
            if mode == self._current_mark_mode():
                self._refresh_mark_readout_only()

    def _delete_selected_mark(self) -> None:
        manager = self._current_mark_manager()
        if manager is not None and manager.selected_id is not None:
            deleted_id = manager.selected_id
            deleted = manager.object(deleted_id)
            manager.delete_object(deleted_id)
            if getattr(deleted, "annotation_type", None) == RANGE:
                mode = self._current_mark_mode()
                if mode is not None:
                    self._mark_overlays[mode].clear_analysis_result(deleted_id)
            if self._analysis_target_id == deleted_id:
                self._analysis_target_id = None
                self.analysis_result_label.setText("Analysis target was deleted.")
            self._persist_mark_manager(manager)
        self._clear_analysis_preview()
        self._refresh_mark_ui()

    def _clear_marks(self) -> None:
        manager = self._current_mark_manager()
        if manager is not None:
            manager.clear()
            self._persist_mark_manager(manager)
        self._analysis_target_id = None
        self.analysis_result_label.setText("Select a target, then Find.")
        self._clear_analysis_preview()
        self._cancel_mark_placement()
        self._refresh_mark_ui()

    def _clear_all_marks(self) -> None:
        self._range_starts.clear()
        self._analysis_target_id = None
        self._analysis_preview_signature = None
        for manager in self._mark_managers.values():
            manager.clear()
            manager.context_key = None
            manager.mode = None
        for overlay in self._mark_overlays.values():
            overlay.clear()
        self._refresh_mark_ui()

    def _on_mark_table_selection_changed(self) -> None:
        manager = self._current_mark_manager()
        if manager is None:
            return
        row = self.marks_table.currentRow()
        id_item = self.marks_table.item(row, 0) if row >= 0 else None
        object_id = id_item.data(Qt.UserRole) if id_item is not None else None
        manager.select_object(object_id)
        selected = manager.object(object_id) if object_id else None
        if selected is not None and (
            hasattr(selected, "mark_id") or selected.annotation_type == RANGE
        ):
            self._analysis_target_id = selected.object_id
            if hasattr(selected, "mark_id"):
                if self.analysis_region_combo.currentText() == SELECTED_RANGE:
                    self.analysis_region_combo.setCurrentText(AUTO_NEARBY)
            else:
                self.analysis_region_combo.setCurrentText(SELECTED_RANGE)
            self.analysis_result_label.setText("Ready. Press Find to analyze this local region.")
        self._clear_analysis_preview()
        mode = self._current_mark_mode()
        if mode is not None:
            self._mark_overlays[mode].render(
                manager.marks(), manager.annotations(), manager.selected_id,
                self.show_mark_values_checkbox.isChecked(),
            )
        self.delete_mark_button.setEnabled(manager.selected_id is not None)
        self._populate_numeric_editor()
        self._refresh_analysis_target()

    def _on_mark_visibility_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        object_id = item.data(Qt.UserRole)
        manager = self._current_mark_manager()
        if manager is not None and object_id:
            manager.set_visible(object_id, item.checkState() == Qt.Checked)
            self._persist_mark_manager(manager)
            mode = self._current_mark_mode()
            if mode is not None:
                self._mark_overlays[mode].render(
                    manager.marks(), manager.annotations(), manager.selected_id,
                    self.show_mark_values_checkbox.isChecked(),
                )

    @staticmethod
    def _parse_optional_number(field: QLineEdit) -> float | None:
        text = field.text().strip()
        if not text:
            return None
        value = float(text)
        if not np.isfinite(value):
            raise ValueError("Values must be finite numbers")
        return value

    def _apply_numeric_position(self) -> None:
        manager = self._current_mark_manager()
        obj = manager.object(manager.selected_id) if manager and manager.selected_id else None
        if obj is None:
            return
        try:
            x = self._parse_optional_number(self.numeric_x_edit)
            y = self._parse_optional_number(self.numeric_y_edit)
            x2 = self._parse_optional_number(self.numeric_x2_edit)
            if x is None and y is None and x2 is None:
                self.numeric_error_label.setText("Enter at least one coordinate.")
                return
            if hasattr(obj, "mark_id"):
                updated = manager.position_mark(obj.number, x=x, y=y)
            else:
                updated = manager.position_annotation(obj.object_id, x=x, y=y, x2=x2)
            if updated is not None:
                obj = updated
        except (ValueError, OverflowError):
            self.numeric_error_label.setText("Invalid number or out of valid range.")
            return
        self.numeric_error_label.clear()
        mode = self._current_mark_mode()
        if mode is not None:
            self._sync_range_half_peak_visuals(manager, self._mark_overlays[mode])
            self._sync_annotation_cut_position(mode, obj)
        self._persist_mark_manager(manager)
        self._refresh_mark_ui()

    def _populate_numeric_editor(self) -> None:
        manager = self._current_mark_manager()
        obj = manager.object(manager.selected_id) if manager and manager.selected_id else None
        self.numeric_editor.setVisible(obj is not None)
        self.numeric_apply_button.setEnabled(obj is not None)
        if obj is None:
            for label, field in (
                (self.numeric_x_label, self.numeric_x_edit),
                (self.numeric_y_label, self.numeric_y_edit),
                (self.numeric_x2_label, self.numeric_x2_edit),
            ):
                label.hide()
                field.hide()
                field.clear()
            self.numeric_error_label.clear()
            return
        fields = (
            (self.numeric_x_label, self.numeric_x_edit),
            (self.numeric_y_label, self.numeric_y_edit),
            (self.numeric_x2_label, self.numeric_x2_edit),
        )
        for label, field in fields:
            label.show()
            field.show()
            field.setEnabled(True)
            field.clear()
        self.numeric_error_label.clear()
        if hasattr(obj, "mark_id"):
            self.numeric_x_label.setText(f"X [{obj.x_unit or '-'}]")
            self.numeric_x_edit.setPlaceholderText(f"Current: {obj.x:.8g}")
            self.numeric_y_label.setText(f"Y [{obj.y_unit or '-'}]")
            self.numeric_y_edit.setPlaceholderText(f"Current: {obj.y:.8g}")
            self.numeric_y_label.setVisible(obj.mode == "2d")
            self.numeric_y_edit.setVisible(obj.mode == "2d")
            self.numeric_x2_label.hide()
            self.numeric_x2_edit.hide()
        elif obj.annotation_type == RANGE:
            self.numeric_x_label.setText(f"Start [{obj.x_unit or '-'}]")
            self.numeric_x_edit.setPlaceholderText(f"Current: {obj.x:.8g}")
            self.numeric_x2_label.setText(f"End [{obj.x_unit or '-'}]")
            self.numeric_x2_edit.setPlaceholderText(f"Current: {obj.x2:.8g}")
            self.numeric_y_label.hide()
            self.numeric_y_edit.hide()
        elif obj.annotation_type == HORIZONTAL_LINE:
            self.numeric_y_label.setText(f"Y [{obj.y_unit or '-'}]")
            self.numeric_y_edit.setPlaceholderText(f"Current: {obj.y:.8g}")
            self.numeric_x_label.hide(); self.numeric_x_edit.hide()
            self.numeric_x2_label.hide(); self.numeric_x2_edit.hide()
        elif obj.annotation_type == VERTICAL_LINE:
            self.numeric_x_label.setText(f"X [{obj.x_unit or '-'}]")
            self.numeric_x_edit.setPlaceholderText(f"Current: {obj.x:.8g}")
            self.numeric_y_label.hide(); self.numeric_y_edit.hide()
            self.numeric_x2_label.hide(); self.numeric_x2_edit.hide()
        else:
            self.numeric_x_label.setText(f"X [{obj.x_unit or '-'}]")
            self.numeric_y_label.setText(f"Y [{obj.y_unit or '-'}]")
            self.numeric_x_edit.setPlaceholderText(f"Current: {obj.x:.8g}")
            self.numeric_y_edit.setPlaceholderText(f"Current: {obj.y:.8g}")
            self.numeric_x2_label.hide(); self.numeric_x2_edit.hide()

    def _refresh_mark_readout_only(self) -> None:
        manager = self._current_mark_manager()
        selected = manager.object(manager.selected_id) if manager and manager.selected_id else None
        if selected is None:
            return
        x_text, y_text, value_text = self._annotation_table_values(selected)
        self.selected_mark_details.setText(
            f"{selected.display_name}\nX / Start: {x_text}\n"
            f"Y / End: {y_text}\nValue / Delta: {value_text}"
        )
        if hasattr(selected, "mark_id") or selected.annotation_type in (RANGE, VERTICAL_LINE, CROSSHAIR):
            if getattr(selected, "x", None) is not None:
                self.numeric_x_edit.setPlaceholderText(f"Current: {selected.x:.8g}")
        if getattr(selected, "y", None) is not None:
            self.numeric_y_edit.setPlaceholderText(f"Current: {selected.y:.8g}")
        if getattr(selected, "x2", None) is not None:
            self.numeric_x2_edit.setPlaceholderText(f"Current: {selected.x2:.8g}")

    def _populate_analysis_mark_choices(self, manager: MarkManager | None) -> None:
        combos = (self.analysis_start_mark_combo, self.analysis_end_mark_combo)
        previous = [combo.currentData() for combo in combos]
        marks = manager.marks() if manager and manager.mode == "1d" else []
        for combo, old in zip(combos, previous):
            combo.blockSignals(True)
            combo.clear()
            for mark in marks:
                combo.addItem(mark.display_name, mark.object_id)
            found = combo.findData(old)
            combo.setCurrentIndex(found if found >= 0 else min(combo.count() - 1, 0))
            combo.blockSignals(False)
        if len(marks) > 1 and previous[1] is None:
            self.analysis_end_mark_combo.blockSignals(True)
            self.analysis_end_mark_combo.setCurrentIndex(1)
            self.analysis_end_mark_combo.blockSignals(False)

    def _analysis_target(self):
        manager = self._current_mark_manager()
        return manager.object(self._analysis_target_id) if manager and self._analysis_target_id else None

    def _analysis_ready(self) -> tuple[bool, str]:
        manager = self._current_mark_manager()
        if manager is None or manager.mode != "1d":
            return False, "Target: Select a 1D Point Mark or Range"
        if self._is_iq_plot():
            return False, "Target: Local Analysis is unavailable for IQ trajectories"
        mode = self.analysis_region_combo.currentText()
        operation = self.analysis_operation_combo.currentText()
        if operation == HALF_PEAK and not self._half_peak_supported():
            return False, "Target: Half-Peak requires Magnitude or Magnitude + dB"
        if mode == AROUND_MARK and operation == HALF_PEAK:
            return False, "Target: Half-Peak requires Auto, Range, Between Marks, or Current View"
        target = self._analysis_target()
        if mode in (AUTO_NEARBY, AROUND_MARK):
            if target is None:
                visible_marks = [mark for mark in manager.marks() if mark.visible]
                if len(visible_marks) == 1:
                    target = visible_marks[0]
                    self._analysis_target_id = target.object_id
            valid = target is not None and hasattr(target, "mark_id") and target.visible
            return valid, (f"Target: {target.display_name}" if valid
                           else "Target: select a visible Point Mark")
        if mode == SELECTED_RANGE:
            if target is None:
                visible_ranges = [
                    item for item in manager.annotations()
                    if item.annotation_type == RANGE and item.visible
                ]
                if len(visible_ranges) == 1:
                    target = visible_ranges[0]
                    self._analysis_target_id = target.object_id
            valid = target is not None and getattr(target, "annotation_type", None) == RANGE \
                and target.visible
            return valid, (f"Target: {target.display_name}" if valid
                           else "Target: select a visible Range")
        if mode == BETWEEN_MARKS:
            start_id = self.analysis_start_mark_combo.currentData()
            end_id = self.analysis_end_mark_combo.currentData()
            start = manager.object(start_id) if start_id else None
            end = manager.object(end_id) if end_id else None
            valid = bool(start and end and start_id != end_id and start.visible and end.visible)
            return valid, (f"Target: {start.display_name} to {end.display_name}" if valid
                           else "Target: choose two different visible Marks")
        return True, "Target: current visible X range"

    def _refresh_analysis_target(self) -> None:
        manager = self._current_mark_manager()
        self._populate_analysis_mark_choices(manager)
        mode = self.analysis_region_combo.currentText()
        self.analysis_window_spin.setVisible(mode == AROUND_MARK)
        self.analysis_window_label.setVisible(mode == AROUND_MARK)
        self.analysis_between_widget.setVisible(mode == BETWEEN_MARKS)
        self.analysis_between_label.setVisible(mode == BETWEEN_MARKS)
        ready, target_text = self._analysis_ready()
        self.analysis_target_label.setText(target_text)
        self.analysis_find_button.setEnabled(ready)
        self.analysis_preview_button.setEnabled(ready)
        self.analysis_clear_preview_button.setEnabled(self._analysis_preview_signature is not None)

    def _on_analysis_configuration_changed(self, *_args) -> None:
        self._clear_analysis_preview()
        self._refresh_analysis_target()

    def _clear_analysis_preview(self) -> None:
        self._analysis_preview_signature = None
        for overlay in self._mark_overlays.values():
            overlay.clear_analysis_region()
        if hasattr(self, "analysis_clear_preview_button"):
            self.analysis_clear_preview_button.setEnabled(False)

    def _resolve_analysis_region(self):
        manager = self._current_mark_manager()
        ready, message = self._analysis_ready()
        if not ready or manager is None:
            raise AnalysisError(message.removeprefix("Target: "))
        operation = self.analysis_operation_combo.currentText()
        mode = self.analysis_region_combo.currentText()
        x, y, source_indices = manager.analysis_trace()
        analyzer = LocalAnalyzer(x, y, source_indices)
        target = self._analysis_target()
        destination = None

        if mode == AUTO_NEARBY:
            center = manager.trace_position_for_mark(target.number)
            auto = analyzer.auto_region(center, operation)
            positions = auto.positions
            bounds = auto.x_bounds(x)
            destination = target
            detail = (target.object_id, auto.start_position, auto.end_position,
                      auto.feature_position, auto.max_radius)
        elif mode == AROUND_MARK:
            if operation == HALF_PEAK:
                raise AnalysisError("Half-Peak requires Auto, Range, Between Marks, or Current View.")
            center = manager.trace_position_for_mark(target.number)
            positions = analyzer.point_window_positions(center, self.analysis_window_spin.value())
            destination = target
            bounds = (float(np.min(x[positions])), float(np.max(x[positions]))) if positions.size else (0, 0)
            detail = (target.object_id, center, self.analysis_window_spin.value())
        elif mode == SELECTED_RANGE:
            positions = analyzer.range_positions(target.x, target.x2)
            bounds = tuple(sorted((float(target.x), float(target.x2))))
            detail = (target.object_id, *bounds)
        elif mode == BETWEEN_MARKS:
            start = manager.object(self.analysis_start_mark_combo.currentData())
            end = manager.object(self.analysis_end_mark_combo.currentData())
            bounds = tuple(sorted((float(start.x), float(end.x))))
            positions = analyzer.range_positions(*bounds)
            detail = (start.object_id, end.object_id, *bounds)
        else:
            view = self.plot_widget.plot_widget.getPlotItem().getViewBox().viewRange()[0]
            bounds = tuple(sorted((float(view[0]), float(view[1]))))
            positions = analyzer.range_positions(*bounds)
            selected = manager.object(manager.selected_id) if manager.selected_id else None
            if selected is not None and hasattr(selected, "mark_id") and selected.visible:
                destination = selected
            detail = bounds
        if positions.size == 0:
            raise AnalysisError("No valid data in the selected region.")
        signature = (manager.context_key, mode, operation, detail)
        return analyzer, positions, destination, bounds, signature

    def _preview_analysis_region(self) -> None:
        try:
            _analyzer, _positions, _destination, bounds, signature = self._resolve_analysis_region()
        except AnalysisError as error:
            self.analysis_result_label.setText(str(error))
            return
        self._mark_overlays[0].show_analysis_region(*bounds)
        self._analysis_preview_signature = signature
        self.analysis_clear_preview_button.setEnabled(True)
        self.analysis_result_label.setText(
            f"Preview: {self._format_mark_value(bounds[0], None)} to "
            f"{self._format_mark_value(bounds[1], None)}"
        )

    def _half_peak_supported(self) -> bool:
        y_candidate: AxisCandidate | None = self.y_combo.currentData()
        if y_candidate is None:
            return False
        if y_candidate.source == "derived":
            return y_candidate.transform_key == "magnitude"
        return bool(y_candidate.is_complex and self._current_transform_spec().base == "magnitude")

    def _is_iq_plot(self) -> bool:
        x_candidate: AxisCandidate | None = self.x_combo.currentData()
        y_candidate: AxisCandidate | None = self.y_combo.currentData()
        return self._is_iq_axis_pair(x_candidate, y_candidate)

    @staticmethod
    def _is_iq_axis_pair(
        x_candidate: AxisCandidate | None, y_candidate: AxisCandidate | None,
    ) -> bool:
        return bool(
            x_candidate and y_candidate
            and x_candidate.source == y_candidate.source == "derived"
            and x_candidate.base_channel == y_candidate.base_channel
            and {x_candidate.transform_key, y_candidate.transform_key} == {"real", "imag"}
        )

    def _run_local_analysis(self) -> None:
        manager = self._current_mark_manager()
        operation = self.analysis_operation_combo.currentText()
        mode = self.analysis_region_combo.currentText()
        if operation == HALF_PEAK and not self._half_peak_supported():
            self.analysis_result_label.setText("Half-Peak requires a Magnitude or Magnitude + dB trace.")
            return
        try:
            analyzer, positions, destination, bounds, signature = self._resolve_analysis_region()
            if mode == AUTO_NEARBY and operation == HALF_PEAK \
                    and self._analysis_preview_signature != signature:
                self._mark_overlays[0].show_analysis_region(*bounds)
                self._analysis_preview_signature = signature
                self.analysis_clear_preview_button.setEnabled(True)
                self.analysis_result_label.setText("Review the Auto Region preview, then press Find again.")
                return
            result = analyzer.analyze(operation, positions)
        except AnalysisError as error:
            self.analysis_result_label.setText(str(error))
            return
        if destination is not None:
            result_mark = manager.move_to_trace_position(destination.number, result.position)
        else:
            result_mark = manager.add_at_trace_position(result.position)
            if result_mark is None:
                self.analysis_result_label.setText("No free Point Mark (M1-M10 are occupied).")
                return
        self._clear_analysis_preview()
        self._persist_mark_manager(manager)
        self._refresh_mark_ui()
        lines = [
            f"{operation}: {result_mark.display_name}",
            f"X {self._format_mark_value(result.x, result_mark.x_unit)}",
            f"Y {self._format_mark_value(result.y, result_mark.y_unit)}",
            f"Index {result.sample_index}",
        ]
        if operation in (PEAK, TROUGH):
            lines.append(f"Prominence {result.prominence:.6g}")
        if operation == HALF_PEAK:
            lines.extend((
                f"Extremum {self._format_mark_value(result.x, result_mark.x_unit)}",
                f"Extremum Value {self._format_mark_value(result.y, result_mark.y_unit)}",
                f"Baseline {self._format_mark_value(result.baseline, result_mark.y_unit)}",
                f"Half Level {self._format_mark_value(result.half_level, result_mark.y_unit)}",
                f"Left {self._format_mark_value(result.left_half_x, result_mark.x_unit)}",
                f"Right {self._format_mark_value(result.right_half_x, result_mark.x_unit)}",
                f"Half-Level Width {self._format_mark_value(result.half_level_width, result_mark.x_unit)}",
            ))
            target = self._analysis_target()
            range_id = (target.object_id if mode == SELECTED_RANGE
                        and getattr(target, "annotation_type", None) == RANGE else None)
            if range_id is not None:
                manager.set_range_half_peak(range_id, HalfPeakResult(
                    extremum_x=result.x, extremum_value=result.y,
                    baseline=result.baseline, half_level=result.half_level,
                    left_crossing=result.left_half_x, right_crossing=result.right_half_x,
                    width=result.half_level_width, sample_index=result.sample_index,
                ))
            self._mark_overlays[0].show_half_peak_result(
                result.left_half_x, result.right_half_x, result.half_level, range_id=range_id,
            )
        self.analysis_result_label.setText("\n".join(lines))
    @staticmethod
    def _format_mark_value(value: float, unit: str | None) -> str:
        suffix = f" {unit}" if unit else ""
        return f"{value:.8g}{suffix}"

    def _refresh_mark_ui(self) -> None:
        for mode, manager in self._mark_managers.items():
            overlay = self._mark_overlays.get(mode)
            if overlay is not None:
                overlay.render(
                    manager.marks(), manager.annotations(), manager.selected_id,
                    self.show_mark_values_checkbox.isChecked(),
                )

        manager = self._current_mark_manager()
        supported = manager is not None
        objects = manager.objects() if manager is not None else []
        point_count = len(manager.marks()) if manager is not None else 0
        self.add_mark_button.setEnabled(bool(
            supported and manager.can_place and self._pending_mark_tool is not None
        ))
        self.delete_mark_button.setEnabled(
            bool(supported and manager.selected_id is not None)
        )
        self.clear_marks_button.setEnabled(bool(objects))

        self.marks_table.blockSignals(True)
        self.marks_table.clearSelection()
        self.marks_table.setRowCount(len(objects))
        if not supported:
            self.marks_info_label.setText("Marks are available in 1D and 2D Plot modes")
        else:
            self.marks_info_label.setText(
                f"{len(objects)} objects ({point_count} / {MAX_MARKS} Point Marks)"
            )
        for row, obj in enumerate(objects):
            id_item = QTableWidgetItem(obj.display_name)
            id_item.setData(Qt.UserRole, obj.object_id)
            id_item.setFlags(id_item.flags() | Qt.ItemIsUserCheckable)
            id_item.setCheckState(Qt.Checked if obj.visible else Qt.Unchecked)
            self.marks_table.setItem(row, 0, id_item)
            if obj.object_id == manager.selected_id:
                self.marks_table.selectRow(row)
        self.marks_table.blockSignals(False)
        selected = manager.object(manager.selected_id) if manager and manager.selected_id else None
        if selected is None:
            self.selected_mark_details.setText("Select an object to inspect its values.")
        else:
            x_text, y_text, value_text = self._annotation_table_values(selected)
            self.selected_mark_details.setText(
                f"{selected.display_name}\nX / Start: {x_text}\n"
                f"Y / End: {y_text}\nValue / Delta: {value_text}"
            )
        self._populate_numeric_editor()
        self._refresh_analysis_target()

    def _annotation_table_values(self, obj) -> tuple[str, str, str]:
        if hasattr(obj, "mark_id"):
            value = (self._format_mark_value(obj.value, obj.value_unit)
                     if obj.mode == "2d" else "-")
            return (
                self._format_mark_value(obj.x, obj.x_unit),
                self._format_mark_value(obj.y, obj.y_unit),
                value,
            )
        if obj.annotation_type == RANGE:
            return (
                self._format_mark_value(obj.x, obj.x_unit),
                self._format_mark_value(obj.x2, obj.x_unit),
                self._format_mark_value(obj.width, obj.x_unit),
            )
        if obj.annotation_type == HORIZONTAL_LINE:
            return "-", self._format_mark_value(obj.y, obj.y_unit), "-"
        if obj.annotation_type == VERTICAL_LINE:
            return self._format_mark_value(obj.x, obj.x_unit), "-", "-"
        value = (self._format_mark_value(obj.value, obj.value_unit)
                 if obj.value is not None else "-")
        return (
            self._format_mark_value(obj.x, obj.x_unit),
            self._format_mark_value(obj.y, obj.y_unit),
            value,
        )

    def _build_data_table_widget(self) -> QWidget:
        """Phase 9: a simple Index | X | Y table reflecting whatever
        1D-shaped data is currently on screen (1D Plot mode, or the
        N-D Slice Explorer's 1D sub-mode). Deliberately does NOT try
        to show a full 2D heatmap as a table - an 855x501 real sample
        would be ~430,000 rows, impractical for a Qt table and not
        what a 'data table view' is for; 2D data gets a proper matrix
        CSV export instead (Phase 10)."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.data_table_info_label = QLabel("(no data)")
        layout.addWidget(self.data_table_info_label)

        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Index", "X", "Y"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        layout.addWidget(table)
        self.data_table = table

        return container

    def _update_data_table(self, x_values, y_values, *, x_name: str, x_unit: str | None,
                            y_name: str, y_unit: str | None, transform: str) -> None:
        """Populates the modeless Data Table window from any 1D-shaped (x, y)
        pair. Called from the 1D Plot page and the N-D page's 1D
        sub-mode - never from 2D Heatmap mode (see
        _build_data_table_widget's docstring)."""
        table_data = build_1d_table(
            x_values, y_values, x_name=x_name, x_unit=x_unit,
            y_name=y_name, y_unit=y_unit, transform=transform,
        )
        self.data_table.setRowCount(len(table_data.rows))
        x_header = f"{table_data.x_name} [{table_data.x_unit or '-'}]"
        y_header = f"{table_data.y_name} [{table_data.y_unit or '-'}]"
        self.data_table.setHorizontalHeaderLabels(["Index", x_header, y_header])

        for row in table_data.rows:
            self.data_table.setItem(row.index, 0, QTableWidgetItem(str(row.index)))
            self.data_table.setItem(row.index, 1, QTableWidgetItem(f"{row.x:.6g}"))
            y_str = f"{row.y:.6g}" if isinstance(row.y, (int, float)) else str(row.y)
            self.data_table.setItem(row.index, 2, QTableWidgetItem(y_str))

        note = ""
        if table_data.truncated:
            note = f"  (showing first {len(table_data.rows)} of {table_data.total_rows} rows)"
        self.data_table_info_label.setText(
            f"{y_header} vs {x_header} \u2014 {len(table_data.rows)} rows{note}"
        )

    def _clear_data_table(self, message: str = "(2D data - switch to 1D Plot mode, "
                                                 "or use a Line Cut, to view a table)") -> None:
        self.data_table.setRowCount(0)
        self.data_table_info_label.setText(message)

    # ---- file loading -------------------------------------------------------

    def open_file_dialog(self, *_args, parent=None) -> str | None:
        path, _ = QFileDialog.getOpenFileName(
            parent or self, "Open Labber HDF5 log", "", "Labber HDF5 (*.hdf5 *.h5);;All files (*)"
        )
        if path:
            self.open_file(path)
        return path or None

    def open_file(self, path: str) -> None:
        previous_identity = self.experiment.data_identity if self.experiment is not None else None
        self._flush_display_state()
        self.metadata_dialog.flush_comment()
        if self._comment_context is not None:
            database_id, relative_path = self._comment_context
            try:
                if Path(database_id, relative_path).resolve() != Path(path).resolve():
                    self._comment_context = None
            except OSError:
                self._comment_context = None
        try:
            experiment = load_experiment(path)
        except (HDF5ReadError, UnsupportedLabberFormat) as e:
            QMessageBox.critical(
                self,
                "Cannot open file",
                f"This file could not be parsed as a Labber measurement log:\n\n{e}\n\n"
                f"(Raw HDF5 Explorer Mode fallback is not implemented yet in this phase.)",
            )
            return
        except Exception as e:  # defensive: never let an unexpected error crash the app silently
            QMessageBox.critical(self, "Unexpected error", f"Failed to open file:\n\n{e}")
            return
        remembered = self.viewer_display_state_store.get(path)     # read before showing records a new one
        self.show_experiment(experiment, previous_identity)
        self._apply_opening_view(remembered)

    def _apply_opening_view(self, state: dict | None) -> None:
        """How a file opens: the way it was left last time (data worked on before), else 2D
        when the measurement has a sweep (1D when it holds a single trace). A session being
        restored, or a named view, is applied afterwards and wins."""
        import os

        if self.experiment is None or self._restoring_session:
            return
        if isinstance(state, dict) and state.get("mode") in ("1d", "2d"):
            try:
                self._restore_display_state(state)
                return
            except Exception:
                pass                                   # an unusable old record: open as new
        if os.environ.get("LABLOGVIEWER_FIRST_OPEN_2D", "1") == "0":
            return                                     # the older behaviour (1D), used by earlier tests
        if self.mode_combo.currentIndex() != 0 or not self._two_d_possible():
            return
        self.mode_combo.setCurrentIndex(1)

    def _two_d_possible(self) -> bool:
        """A 2D heatmap can be built for the current Z / X / Y choice (no warning when not)."""
        try:
            if int(getattr(self.experiment, "total_dimensions", 1) or 1) < 2 or self.cached is None:
                return False
            z_name, x_name, y_name = (self.z_combo_2d.currentText(), self.x_combo_2d.currentText(),
                                      self.y_combo_2d.currentText())
            if not (z_name and x_name and y_name) or x_name == y_name:
                return False
            self.cached.get_2d_data(x_name, y_name, z_name, transform=self.transform_combo_2d.currentData() or "raw")
            return True
        except Exception:
            return False

    def show_experiment(self, experiment, previous_identity: str | None = None, *, live: bool = False) -> None:
        """Display an already-built Experiment (a local file or a Network Workspace mirror).
        ``live``: a reload of a file that is still growing (auto refresh)."""
        path = str(experiment.source_path)
        from app.core import fingerprint

        if not live:                          # a growing file is fingerprinted once it is opened normally
            fingerprint.remember(path, experiment.log_name, experiment.creation_time)   # for moved-file re-linking
        if previous_identity is None and self.experiment is not None:
            previous_identity = self.experiment.data_identity
        # Preserve the old context before replacing it; HDF5 remains read-only.
        self._persist_all_marks()
        # close any previously open experiment before replacing it
        if self.experiment is not None:
            if self._node_antinode_window is not None:
                self._node_antinode_window.close()
                self._node_antinode_window.deleteLater()
                self._node_antinode_window = None
            for window in tuple(self._yig_fitting_windows.values()):
                window.close()
            self._yig_fitting_windows.clear()
            self.experiment.close()

        self.experiment = experiment
        self.node_antinode_action.setEnabled(True)
        self.yig_fitting_action.setEnabled(True)
        self.surface_3d_action.setEnabled(True)
        if self._three_d_window is not None:
            self._three_d_window.update_title()
        host = getattr(self, "_three_d_process", None)
        if host is not None and host.is_running():
            host.send({"cmd": "open", "path": str(experiment.source_path), "state": None})
        if previous_identity is not None and previous_identity != experiment.data_identity:
            for pane_state in self._pane_states.values():
                pane_state.x_formula = ""
                pane_state.y_formula = ""
                pane_state.formula_enabled = False
            with QSignalBlocker(self.x_formula_edit), QSignalBlocker(self.y_formula_edit):
                self.x_formula_edit.clear()
                self.y_formula_edit.clear()
            self._refresh_formula_previews()
        self.cached = CachedExperiment(experiment, LRUDataCache())
        self.mgr = ChannelManager(experiment)
        self._overlay_plot_signature = None
        self.plot_widget.clear()
        self._clear_all_marks()
        for state in self._pane_states.values():
            for manager in state.mark_managers.values():
                manager.clear()
                manager.context_key = None
                manager.mode = None
        for frame in self._pane_frames.values():
            frame.clear()

        self._clear_cut_windows()

        # Populate every control so mode switching remains immediate, but do
        # not eagerly construct all three scientific representations.  The
        # old path loaded 1D, 2D, and N-D arrays before the first paint even
        # though only one mode can be visible.
        self._loading_file = True
        try:
            self._populate_summary()
            self._populate_axis_combos()
            self._populate_view_preset_combo(selected="")
            self._populate_saved_overlay_combo()
            self._populate_z_combo_2d()
            self._populate_z_combo_nd()
        finally:
            self._loading_file = False
        self._on_mode_changed(self.mode_combo.currentIndex())

        self.statusBar().showMessage(f"Loaded: {Path(path).name}  "
                                      f"({experiment.format_variant})")
        self._report_partial_acquisition(self.plot_2d_widget._grid)
        self._active_pane_id = 1
        self._capture_active_pane_state()
        self._pane_states[1].x_range = None
        self._pane_states[1].y_range = None
        for pane_id in tuple(self._pane_states):
            if pane_id != 1:
                self._pane_states[pane_id] = self._default_pane_state(pane_id)
        if not self.multi_pane_splitter.isHidden():
            self._activate_pane(1, capture=False)
            self._render_multi_panes()
        self._refresh_cut_windows()
        self._schedule_display_state_save()
        if not self._restoring_session:
            self.workspace_state_changed.emit()

    # ---- 1D combo population --------------------------------------------------

    def _populate_axis_combos(self) -> None:
        """Build semantic X/Y choices from the current experiment model."""
        self._axis_candidates = self.mgr.list_axis_candidates()
        x_candidates = self.mgr.list_x_axis_candidates()
        y_candidates = self.mgr.list_y_axis_candidates()

        self.x_combo.blockSignals(True)
        self.x_combo.clear()
        self._add_axis_groups(self.x_combo, x_candidates)
        self.x_combo.blockSignals(False)

        self.y_combo.blockSignals(True)
        self.y_combo.clear()
        self._add_axis_groups(self.y_combo, y_candidates)
        self.y_combo.blockSignals(False)

        # sensible default: a vector/complex channel for Y (the most
        # common "what do I want to look at" case) paired with its own
        # trace axis for X, falling back to the first/last candidates
        # if no vector channel is present.
        default_y = next((c for c in self._axis_candidates if c.source == "vector_channel"), None)
        default_x = None
        if default_y is not None:
            default_x = next(
                (c for c in self._axis_candidates
                 if c.source == "trace_axis" and c.base_channel == default_y.base_channel),
                None,
            )
        if default_x is None and self._axis_candidates:
            default_x = self._axis_candidates[0]
        if default_y is None and self._axis_candidates:
            default_y = self._axis_candidates[-1]

        if default_x is not None:
            self.x_combo.blockSignals(True)
            self.x_combo.setCurrentIndex(self.x_combo.findText(default_x.name))
            self.x_combo.blockSignals(False)
        if default_y is not None:
            self.y_combo.blockSignals(True)
            self.y_combo.setCurrentText(default_y.name)
            self.y_combo.blockSignals(False)

        self._populate_transform_combo()
        self._populate_axis_preset_combo()
        self._populate_log_entries()
        self._on_axis_changed()

    @staticmethod
    def _add_axis_group(combo: QComboBox, title: str,
                        candidates: list[AxisCandidate]) -> None:
        if not candidates:
            return
        combo.addItem(title, userData=None)
        header = combo.model().item(combo.count() - 1)
        header.setEnabled(False)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        for candidate in candidates:
            combo.addItem(candidate.name, userData=candidate)

    @classmethod
    def _add_axis_groups(cls, combo: QComboBox,
                         candidates: list[AxisCandidate]) -> None:
        physical = [candidate for candidate in candidates
                    if candidate.source in ("step", "trace_axis")]
        measurements = [candidate for candidate in candidates
                        if candidate.source in ("log_scalar", "vector_channel")]
        cls._add_axis_group(combo, "Physical", physical)
        cls._add_axis_group(combo, "Measurements", measurements)

        derived_channels: dict[str, list[AxisCandidate]] = {}
        for candidate in candidates:
            if candidate.source == "derived":
                derived_channels.setdefault(candidate.base_channel, []).append(candidate)
        for channel_name, derived in derived_channels.items():
            cls._add_axis_group(combo, f"Derived — {channel_name}", derived)

    def _populate_transform_combo(self) -> None:
        self.transform_combo.blockSignals(True)
        self.transform_combo.clear()
        for spec in self.transform_store.list_all():
            self.transform_combo.addItem(spec.name, userData=spec)
        # default to Magnitude (dB) - matches the sensible default used
        # by earlier versions, rather than leaving it at index 0 ("Real")
        idx = self.transform_combo.findText("Magnitude")
        if idx >= 0:
            self.transform_combo.setCurrentIndex(idx)
        self.transform_combo.blockSignals(False)
        self._on_transform_changed()
        if idx >= 0:
            self.db_checkbox.setChecked(True)  # overrides the plain-Magnitude
            # default's db=False - dB is the more useful starting view

    def _populate_log_entries(self) -> None:
        self.log_entries.populate(self.mgr)
        n = self.log_entries.total_entries()
        self.entry_spin.blockSignals(True)
        self.entry_spin.setMaximum(max(n - 1, 0))
        self.entry_spin.setEnabled(n > 1)
        self.entry_spin.setValue(self.log_entries.current_row())
        self.entry_spin.blockSignals(False)
        self._update_trace_display()
        self._refresh_multi_trace_panel()
        self._update_export_action_labels()

    def _on_axis_changed(self) -> None:
        """Fires when X Axis or Y Axis changes. The Transform system
        (spec §5) only applies when Y is a complex/vector channel —
        this is where that coupling is decided, kept minimal and
        one-directional so Axis and Transform stay conceptually
        separate."""
        if self.experiment is None:
            return
        x_cand: AxisCandidate | None = self.x_combo.currentData()
        y_cand: AxisCandidate | None = self.y_combo.currentData()
        self._configure_axis_transform_controls(sync_derived_y=True)
        compatible = bool(
            x_cand and y_cand and self.mgr.axis_domains_compatible(x_cand, y_cand)
        )

        self.save_transform_button.setText("Save Axis Preset...")
        self.save_transform_button.setEnabled(compatible)

        if not self._loading_file:
            self.update_plot()
        self._update_axis_preset_controls()

    def _configure_axis_transform_controls(self, *, sync_derived_y: bool) -> None:
        """Enable only operations meaningful for the selected 1D axes.

        A raw complex Y axis is transformed by the Transform selector; derived
        axes remain transformable but follow the selected representation. The
        IQ real/imaginary pair is the exception: both axes are already occupied
        by the two components of one complex trace.
        """
        x_cand: AxisCandidate | None = self.x_combo.currentData()
        y_cand: AxisCandidate | None = self.y_combo.currentData()
        transformable_y = (
            self._is_transformable_y_candidate(y_cand)
            and not self._is_iq_axis_pair(x_cand, y_cand)
        )
        derived_mode = bool(
            x_cand and y_cand
            and (x_cand.source == "derived" or y_cand.source == "derived")
        )
        if sync_derived_y and y_cand is not None and y_cand.source == "derived":
            self._align_transform_to_derived_axis(y_cand)
            self._sync_modifier_checkboxes()
        elif sync_derived_y and transformable_y and not derived_mode:
            self._sync_modifier_checkboxes()

        spec: TransformSpec | None = self.transform_combo.currentData()
        keys = {
            candidate.transform_key
            for candidate in (x_cand, y_cand)
            if candidate is not None and candidate.source == "derived"
        }
        db_applicable = "magnitude" in keys or bool(
            transformable_y and spec is not None and spec.base == "magnitude"
        )
        unwrap_applicable = bool(keys & {"phase_deg", "phase_rad"}) or bool(
            transformable_y and spec is not None and spec.base in ("phase_deg", "phase_rad")
        )

        self.transform_combo.setEnabled(transformable_y)
        self.db_checkbox.setEnabled(db_applicable)
        self.unwrap_checkbox.setEnabled(unwrap_applicable)
        if not db_applicable:
            self.db_checkbox.setChecked(False)
        if not unwrap_applicable:
            self.unwrap_checkbox.setChecked(False)

    def _is_transformable_y_candidate(self, candidate: AxisCandidate | None) -> bool:
        if candidate is None or candidate.domain != "points":
            return False
        if candidate.source not in ("vector_channel", "derived") or self.experiment is None:
            return False
        channel = self.experiment.channels.get(candidate.base_channel)
        return bool(channel and channel.is_complex)

    def _align_transform_to_derived_axis(self, candidate: AxisCandidate) -> None:
        """Keep an explicit derived Y choice and Transform control in sync."""
        key = candidate.transform_key
        spec = next((item for item in self.transform_store.list_all()
                     if item.base == key and not item.db and not item.unwrap), None)
        if spec is None:
            return
        index = self.transform_combo.findText(spec.name)
        if index >= 0 and index != self.transform_combo.currentIndex():
            self.transform_combo.blockSignals(True)
            self.transform_combo.setCurrentIndex(index)
            self.transform_combo.blockSignals(False)

    def _align_derived_axis_to_transform(self) -> None:
        candidate: AxisCandidate | None = self.y_combo.currentData()
        spec: TransformSpec | None = self.transform_combo.currentData()
        if candidate is None or candidate.source != "derived" or spec is None:
            return
        key = spec.resolve_transform_key()
        if key == "magnitude_db":
            key = "magnitude"
        match = next((item for item in self._axis_candidates
                      if item.source == "derived"
                      and item.base_channel == candidate.base_channel
                      and item.transform_key == key), None)
        if match is None and spec.base == "phase_rad":
            match = next((item for item in self._axis_candidates
                          if item.source == "vector_channel"
                          and item.base_channel == candidate.base_channel), None)
        if match is None:
            return
        index = self._find_axis_ref_index(self.y_combo, AxisRef.from_candidate(match))
        if index >= 0 and index != self.y_combo.currentIndex():
            self.y_combo.blockSignals(True)
            self.y_combo.setCurrentIndex(index)
            self.y_combo.blockSignals(False)

    def _sync_modifier_checkboxes(self) -> None:
        """Keeps dB/Unwrap enabled-state in sync with the currently
        selected TransformSpec's base — dB only ever applies to a
        Magnitude base, Unwrap only ever to a Phase base (spec §7:
        explicit, no double-application)."""
        spec: TransformSpec | None = self.transform_combo.currentData()
        if spec is None:
            self.db_checkbox.setEnabled(False)
            self.unwrap_checkbox.setEnabled(False)
            return
        is_magnitude = spec.base == "magnitude"
        is_phase = spec.base in ("phase_deg", "phase_rad")
        self.db_checkbox.setEnabled(is_magnitude)
        self.unwrap_checkbox.setEnabled(is_phase)
        self.db_checkbox.blockSignals(True)
        self.db_checkbox.setChecked(spec.db if is_magnitude else False)
        self.db_checkbox.blockSignals(False)
        self.unwrap_checkbox.blockSignals(True)
        self.unwrap_checkbox.setChecked(spec.unwrap if is_phase else False)
        self.unwrap_checkbox.blockSignals(False)

    def _on_transform_changed(self) -> None:
        self._align_derived_axis_to_transform()
        self._sync_modifier_checkboxes()
        if self.experiment is not None:
            self._configure_axis_transform_controls(sync_derived_y=False)
        if not self._loading_file:
            self.update_plot()

    def _current_transform_spec(self) -> TransformSpec:
        """The EFFECTIVE transform for plotting right now: the
        selected base TransformSpec, with dB/Unwrap overridden by the
        checkboxes' live state (which may differ from what a saved
        preset originally had, until the user explicitly re-saves)."""
        base_spec: TransformSpec | None = self.transform_combo.currentData()
        if base_spec is None:
            return TransformSpec(name="Magnitude", base="magnitude")
        return TransformSpec(
            name=base_spec.name,
            base=base_spec.base,
            db=self.db_checkbox.isChecked() if base_spec.base == "magnitude" else False,
            unwrap=self.unwrap_checkbox.isChecked() if base_spec.base in ("phase_deg", "phase_rad") else False,
        )

    def _on_save_transform_clicked(self) -> None:
        x_cand: AxisCandidate | None = self.x_combo.currentData()
        y_cand: AxisCandidate | None = self.y_combo.currentData()
        if x_cand and y_cand:
            self._save_axis_preset(x_cand, y_cand)

    def _current_data_key(self) -> str | None:
        if self.experiment is None:
            return None
        return self.experiment.data_identity

    def _legacy_mark_context(self, context: tuple) -> tuple | None:
        """Return the pre-v0.12H raw-path context when it differs from identity."""
        if self.experiment is None or not context:
            return None
        raw_source = str(self.experiment.source_path)
        if context[0] == raw_source:
            return None
        return (raw_source, *context[1:])

    def _mark_pane_id(self, manager: MarkManager) -> int:
        for pane_id, state in self._pane_states.items():
            if manager in state.mark_managers.values():
                return pane_id
        return 1

    def _persist_mark_manager(self, manager: MarkManager, pane_id: int | None = None) -> None:
        data_key = self._current_data_key()
        if data_key is None or manager.context_key is None or manager.mode is None:
            return
        self.mark_store.save(data_key, self._mark_pane_id(manager) if pane_id is None else pane_id,
                             manager.context_key, manager.persistence_state())

    def _restore_mark_manager(self, manager: MarkManager, pane_id: int) -> None:
        data_key = self._current_data_key()
        if data_key is None or manager.context_key is None:
            return
        state = self.mark_store.get(data_key, pane_id, manager.context_key)
        legacy_context = None
        if state is None:
            legacy_context = self._legacy_mark_context(manager.context_key)
            if legacy_context is not None:
                state = self.mark_store.get(data_key, pane_id, legacy_context)
        restored = manager.restore_persistence_state(state)
        if restored and legacy_context is not None:
            # Keep legacy records readable while future restores use the stable key.
            self.mark_store.save(data_key, pane_id, manager.context_key, manager.persistence_state())

    def _persist_all_marks(self) -> None:
        seen: set[int] = set()
        for pane_id, state in self._pane_states.items():
            for manager in state.mark_managers.values():
                if id(manager) not in seen:
                    self._persist_mark_manager(manager, pane_id)
                    seen.add(id(manager))
        for manager in self._mark_managers.values():
            if id(manager) not in seen:
                self._persist_mark_manager(manager)
                seen.add(id(manager))

    def _built_in_iq_preset(self) -> AxisPreset | None:
        imaginary = next(
            (c for c in self._axis_candidates
             if c.source == "derived" and c.transform_key == "imag"), None
        )
        if imaginary is None:
            return None
        real = next(
            (c for c in self._axis_candidates
             if c.source == "derived" and c.transform_key == "real"
             and c.base_channel == imaginary.base_channel
             and c.domain == imaginary.domain), None
        )
        if real is None:
            return None
        return AxisPreset(
            "IQ Transform", AxisRef.from_candidate(imaginary), AxisRef.from_candidate(real)
        )

    def _populate_axis_preset_combo(self) -> None:
        current = self.axis_preset_combo.currentText()
        self.axis_preset_combo.blockSignals(True)
        self.axis_preset_combo.clear()
        self.axis_preset_combo.addItem("Select...", userData=None)
        iq_preset = self._built_in_iq_preset()
        if iq_preset is not None:
            self.axis_preset_combo.addItem(iq_preset.name, userData=iq_preset)
        for preset in self.axis_preset_store.list_all(self._current_data_key()):
            self.axis_preset_combo.addItem(preset.name, userData=preset)
        index = self.axis_preset_combo.findText(current)
        self.axis_preset_combo.setCurrentIndex(index if index >= 0 else 0)
        self.axis_preset_combo.blockSignals(False)
        self._update_axis_preset_controls()

    def _save_axis_preset(self, x_cand: AxisCandidate, y_cand: AxisCandidate) -> None:
        name, ok = QInputDialog.getText(self, "Save Axis Preset", "Name for this axis preset:")
        if not ok or not name.strip():
            return
        name = name.strip()
        data_key = self._current_data_key()
        if data_key is None:
            return
        if name == "IQ Transform" or self.axis_preset_store.contains(name, data_key):
            QMessageBox.warning(self, "Preset name in use", "Choose a different Axis Preset name.")
            return
        preset = self._axis_preset_from_current(name, x_cand, y_cand)
        try:
            self.axis_preset_store.save(preset, data_key)
        except Exception as error:
            QMessageBox.warning(self, "Cannot Save Axis Preset", str(error))
            return
        self._populate_axis_preset_combo()
        index = self.axis_preset_combo.findText(preset.name)
        if index >= 0:
            self.axis_preset_combo.setCurrentIndex(index)

    def _axis_preset_from_current(self, name: str, x_cand: AxisCandidate,
                                  y_cand: AxisCandidate) -> AxisPreset:
        keys = {x_cand.transform_key, y_cand.transform_key}
        spec = self._current_transform_spec()
        transformable = self._is_transformable_y_candidate(y_cand)
        return AxisPreset(
            name=name,
            x_axis=AxisRef.from_candidate(x_cand),
            y_axis=AxisRef.from_candidate(y_cand),
            transform_name=spec.name if transformable else None,
            db=self.db_checkbox.isChecked()
            if ("magnitude" in keys or (transformable and spec.base == "magnitude")) else False,
            unwrap=self.unwrap_checkbox.isChecked()
            if (keys & {"phase_deg", "phase_rad"}
                or (transformable and spec.base in {"phase_deg", "phase_rad"})) else False,
        )

    def _selected_custom_axis_preset(self) -> AxisPreset | None:
        preset = self.axis_preset_combo.currentData()
        data_key = self._current_data_key()
        if (data_key is None or not isinstance(preset, AxisPreset)
                or preset.name == "IQ Transform"):
            return None
        return self.axis_preset_store.get(preset.name, data_key)

    def _update_axis_preset_controls(self, *_args) -> None:
        enabled = self._selected_custom_axis_preset() is not None
        for button in getattr(self, "axis_preset_management_buttons", ()):
            button.setEnabled(enabled)

    def _update_axis_preset(self) -> None:
        preset = self._selected_custom_axis_preset()
        x_cand, y_cand = self.x_combo.currentData(), self.y_combo.currentData()
        data_key = self._current_data_key()
        if preset is None or data_key is None or x_cand is None or y_cand is None:
            return
        updated = self._axis_preset_from_current(preset.name, x_cand, y_cand)
        try:
            saved = self.axis_preset_store.update(preset.name, updated, data_key)
        except Exception as error:
            QMessageBox.warning(self, "Cannot Update Axis Preset", str(error))
            return
        if saved:
            self._populate_axis_preset_combo()
            self.axis_preset_combo.setCurrentText(preset.name)
            self.statusBar().showMessage(f"Axis Preset '{preset.name}' updated.", 4000)

    def _rename_axis_preset(self) -> None:
        preset = self._selected_custom_axis_preset()
        data_key = self._current_data_key()
        if preset is None or data_key is None:
            return
        new_name, accepted = QInputDialog.getText(
            self, "Rename Axis Preset", "New name:",
            QLineEdit.EchoMode.Normal, preset.name,
        )
        new_name = new_name.strip()
        if not accepted or not new_name:
            return
        if new_name == "IQ Transform":
            QMessageBox.warning(
                self, "Cannot Rename Axis Preset",
                "'IQ Transform' is reserved for the built-in preset.",
            )
            return
        try:
            renamed = self.axis_preset_store.rename(preset.name, new_name, data_key)
        except Exception as error:
            QMessageBox.warning(self, "Cannot Rename Axis Preset", str(error))
            return
        if renamed:
            self._populate_axis_preset_combo()
            self.axis_preset_combo.setCurrentText(new_name)

    def _delete_axis_preset(self) -> None:
        preset = self._selected_custom_axis_preset()
        data_key = self._current_data_key()
        if preset is None or data_key is None:
            return
        answer = QMessageBox.question(
            self, "Delete Axis Preset", f"Delete '{preset.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            deleted = self.axis_preset_store.delete(preset.name, data_key)
        except Exception as error:
            QMessageBox.warning(self, "Cannot Delete Axis Preset", str(error))
            return
        if deleted:
            self._populate_axis_preset_combo()
            self.axis_preset_combo.setCurrentIndex(0)
            self.statusBar().showMessage(f"Axis Preset '{preset.name}' deleted.", 4000)

    @staticmethod
    def _axis_ref_matches(candidate: AxisCandidate, reference: AxisRef) -> bool:
        return (
            candidate.name == reference.name
            and candidate.source == reference.source
            and candidate.base_channel == reference.base_channel
            and candidate.transform_key == reference.transform_key
        )

    def _find_axis_ref_index(self, combo: QComboBox, reference: AxisRef) -> int:
        for index in range(combo.count()):
            candidate = combo.itemData(index)
            if candidate is not None and self._axis_ref_matches(candidate, reference):
                return index
        return -1

    def _on_axis_preset_changed(self) -> None:
        preset: AxisPreset | None = self.axis_preset_combo.currentData()
        if self.experiment is None:
            return
        if preset is None:
            self._restore_normal_transform_state()
            return
        if preset.name == "IQ Transform" and not self._is_iq_plot():
            self._normal_transform_state = {
                "x": self._axis_ref_dict(self.x_combo.currentData()),
                "y": self._axis_ref_dict(self.y_combo.currentData()),
                "transform": self.transform_combo.currentText(),
                "db": self.db_checkbox.isChecked(),
                "unwrap": self.unwrap_checkbox.isChecked(),
            }
        x_index = self._find_axis_ref_index(self.x_combo, preset.x_axis)
        y_index = self._find_axis_ref_index(self.y_combo, preset.y_axis)
        if x_index < 0 or y_index < 0:
            QMessageBox.warning(
                self, "Axis preset unavailable",
                f"'{preset.name}' uses a channel or axis that is not present in this file.",
            )
            return
        self.x_combo.blockSignals(True)
        self.y_combo.blockSignals(True)
        self.x_combo.setCurrentIndex(x_index)
        self.y_combo.setCurrentIndex(y_index)
        self.x_combo.blockSignals(False)
        self.y_combo.blockSignals(False)
        self._on_axis_changed()
        if preset.transform_name:
            transform_index = self.transform_combo.findText(preset.transform_name)
            if transform_index >= 0:
                self.transform_combo.setCurrentIndex(transform_index)
                self._on_transform_changed()
        self.db_checkbox.setChecked(preset.db and self.db_checkbox.isEnabled())
        self.unwrap_checkbox.setChecked(preset.unwrap and self.unwrap_checkbox.isEnabled())
        self.update_plot()

    def _restore_normal_transform_state(self) -> None:
        state = self._normal_transform_state
        if state is None and not self._is_iq_plot():
            return
        if state is not None:
            x_ref = AxisRef(**state["x"]) if state.get("x") else None
            y_ref = AxisRef(**state["y"]) if state.get("y") else None
            x_index = self._find_axis_ref_index(self.x_combo, x_ref) if x_ref else -1
            y_index = self._find_axis_ref_index(self.y_combo, y_ref) if y_ref else -1
        else:
            y_candidate = next(
                (candidate for candidate in self._axis_candidates
                 if candidate.source == "vector_channel"), None
            )
            x_candidate = next(
                (candidate for candidate in self._axis_candidates
                 if y_candidate and candidate.source == "trace_axis"
                 and candidate.base_channel == y_candidate.base_channel), None
            )
            x_index = self.x_combo.findText(x_candidate.name) if x_candidate else -1
            y_index = self.y_combo.findText(y_candidate.name) if y_candidate else -1
        if x_index < 0 or y_index < 0:
            return
        self.x_combo.blockSignals(True)
        self.y_combo.blockSignals(True)
        self.x_combo.setCurrentIndex(x_index)
        self.y_combo.setCurrentIndex(y_index)
        self.x_combo.blockSignals(False)
        self.y_combo.blockSignals(False)
        self._on_axis_changed()
        transform_name = state.get("transform", "Magnitude") if state else "Magnitude"
        transform_index = self.transform_combo.findText(transform_name)
        if transform_index >= 0:
            self.transform_combo.setCurrentIndex(transform_index)
            self._on_transform_changed()
        self.db_checkbox.setChecked(bool(state.get("db", True)) if state else True)
        self.unwrap_checkbox.setChecked(bool(state.get("unwrap", False)) if state else False)
        self._normal_transform_state = None
        self.update_plot()

    # ---- Trace / Log Entries / Sweep (single source of truth) ----------------

    def _on_log_entry_selected(self, row: int) -> None:
        self.entry_spin.blockSignals(True)
        self.entry_spin.setValue(row)
        self.entry_spin.blockSignals(False)
        self._update_trace_display()

    def _on_trace_selection_changed(self) -> None:
        self._update_trace_display()
        self._refresh_multi_trace_panel()
        self._preserve_overlay_view_once = True
        if not self.multi_pane_splitter.isHidden():
            if self.sync_trace_checkbox.isChecked():
                active = self.log_entries.current_row()
                for state in self._pane_states.values():
                    state.trace_index = active
            else:
                self._pane_states[self._active_pane_id].trace_index = self.log_entries.current_row()
            self._render_multi_panes()
        else:
            self.update_plot()

    def _on_trace_color_changed(self, _mode: str) -> None:
        self._preserve_overlay_view_once = True
        if not self.multi_pane_splitter.isHidden():
            self._render_multi_panes()
        else:
            self.update_plot()

    def _on_sweep_spin_changed(self, value: int) -> None:
        """Sweep is a quick-jump INTO the same single source of truth
        (LogEntriesWidget's selected row) — not a second, independent
        trace-index variable (spec §9's explicit requirement)."""
        self._select_sweep(value, QApplication.keyboardModifiers())

    def _select_sweep(self, value: int, modifiers=Qt.NoModifier) -> None:
        self.log_entries.select_row(value, modifiers)

    def _update_trace_display(self) -> None:
        total = self.log_entries.total_entries()
        if total <= 0:
            self.trace_display_label.setText("Trace \u2014")
            return
        current = self.log_entries.current_row() + 1
        count = self.trace_selection.selected_count
        suffix = f" \u00b7 {count} selected" if count > 1 else ""
        self.trace_display_label.setText(f"Trace {current} / {total}{suffix}")
        if count > 1:
            self.overlay_status_label.setText(
                f"Overlay active · {count} traces\nActive: Trace {current}"
            )
        else:
            self.overlay_status_label.setText("Single Trace")

    def _refresh_multi_trace_panel(self) -> None:
        if not hasattr(self, "multi_trace_tree"):
            return
        selected = self.trace_selection.ordered_selection()
        visible = self.trace_selection.visible_selection()
        listed = tuple(range(self.log_entries.row_count())) if self._trace_manager_requested else selected
        self.multi_trace_panel.setVisible(self._trace_manager_requested or len(selected) > 1)
        self._updating_multi_trace_panel = True
        existing = tuple(
            self.multi_trace_tree.topLevelItem(row).data(3, Qt.UserRole)
            for row in range(self.multi_trace_tree.topLevelItemCount())
        )
        # ResizeToContents re-measures every row on each item change, which made
        # large Trace Managers quadratic. Suspend it for the bulk update; the
        # modes are restored below, which measures the columns once.
        header = self.multi_trace_tree.header()
        for column in (0, 1, 2):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
        if existing != listed:
            self.multi_trace_tree.clear()
            self._multi_trace_row_states = {}
            items = []
            for trace in listed:
                item = QTreeWidgetItem(["", "", "", f"#{trace + 1}"])
                item.setData(3, Qt.UserRole, trace)
                for column in (0, 1, 2):
                    item.setTextAlignment(column, Qt.AlignCenter)
                items.append(item)
            self.multi_trace_tree.addTopLevelItems(items)
        row_states = getattr(self, "_multi_trace_row_states", {})
        self._multi_trace_row_states = row_states
        for row, trace in enumerate(listed):
            trace_visible = self.trace_selection.is_visible(trace)
            state = (
                trace_visible,
                trace == self.trace_selection.active_trace,
                trace == self.trace_selection.reference_trace,
            )
            if row_states.get(trace) == state:
                continue
            row_states[trace] = state
            item = self.multi_trace_tree.topLevelItem(row)
            item.setIcon(0, icon("show" if trace_visible else "hide"))
            item.setToolTip(0, "Visible · click to hide" if trace_visible else "Hidden · click to show")
            item.setText(1, "●" if state[1] else "○")
            item.setText(2, "★" if state[2] else "☆")
        self._updating_multi_trace_panel = False
        self.overlay_status_label.setText(
            f"{len(selected)} selected · {len(visible)} visible"
            + (f"\nActive: Trace {self.trace_selection.active_trace + 1}"
               if self.trace_selection.active_trace is not None else "")
        )
        self.clear_reference_button.setEnabled(self.trace_selection.reference_trace is not None)
        current = self.multi_trace_tree.currentItem()
        current_trace = current.data(3, Qt.UserRole) if current is not None else None
        self.remove_trace_button.setEnabled(
            self.trace_selection.selected_count > 1 and self.trace_selection.is_selected(current_trace)
        )
        self.multi_trace_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.multi_trace_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.multi_trace_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.multi_trace_tree.header().setSectionResizeMode(3, QHeaderView.Stretch)

    def _toggle_trace_visibility(self, item: QTreeWidgetItem) -> None:
        trace = item.data(3, Qt.UserRole)
        visible = not self.trace_selection.is_visible(trace)
        if not self.trace_selection.is_selected(trace):
            self.log_entries.select_row(trace, Qt.ControlModifier)
            self.trace_selection.set_visible(trace, visible)
            self._refresh_multi_trace_panel()
            return
        self.trace_selection.set_visible(trace, visible)
        self._refresh_multi_trace_panel()
        self._preserve_overlay_view_once = True
        self.update_plot()

    def _on_multi_trace_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        trace = item.data(3, Qt.UserRole)
        if column == 0:
            self._toggle_trace_visibility(item)
        elif column == 1:
            if not self.trace_selection.is_selected(trace):
                self.log_entries.select_row(trace, Qt.ControlModifier)
            self.log_entries.set_active(trace)
        elif column == 2:
            if not self.trace_selection.is_selected(trace):
                self.log_entries.select_row(trace, Qt.ControlModifier)
            self.trace_selection.set_reference(
                None if self.trace_selection.reference_trace == trace else trace
            )
            self._refresh_multi_trace_panel()
        elif column == 3 and not self.trace_selection.is_selected(trace):
            self.log_entries.select_row(trace, Qt.ControlModifier)

    def _clear_reference_trace(self) -> None:
        self.trace_selection.set_reference(None)
        self._refresh_multi_trace_panel()

    def _remove_trace_from_manager(self) -> None:
        item = self.multi_trace_tree.currentItem()
        trace = item.data(3, Qt.UserRole) if item is not None else None
        if self.trace_selection.selected_count > 1 and self.trace_selection.is_selected(trace):
            self.log_entries.select_row(trace, Qt.ControlModifier)

    @staticmethod
    def _axis_ref_dict(candidate: AxisCandidate | None) -> dict | None:
        if candidate is None:
            return None
        reference = AxisRef.from_candidate(candidate)
        return {
            "name": reference.name,
            "source": reference.source,
            "base_channel": reference.base_channel,
            "transform_key": reference.transform_key,
        }

    def _capture_overlay(self, name: str) -> SavedOverlay:
        return SavedOverlay(
            name=name,
            selected_traces=list(self.trace_selection.ordered_selection()),
            visible_traces=list(self.trace_selection.visible_selection()),
            active_trace=self.trace_selection.active_trace or 0,
            reference_trace=self.trace_selection.reference_trace,
            color_mode=self.trace_color_combo.currentText() or SEQUENTIAL,
            x_axis=self._axis_ref_dict(self.x_combo.currentData()),
            y_axis=self._axis_ref_dict(self.y_combo.currentData()),
            transform_name=self.transform_combo.currentText() or None,
            db=self.db_checkbox.isChecked(),
            unwrap=self.unwrap_checkbox.isChecked(),
            plot_mode=self.mode_combo.currentIndex(),
            two_d={
                "z": self.z_combo_2d.currentText(),
                "x": self.x_combo_2d.currentText(),
                "y": self.y_combo_2d.currentText(),
                "transform": self.transform_combo_2d.currentData() or "raw",
                "colormap": self.colormap_combo.currentText(),
                "auto_range": self.auto_range_checkbox.isChecked(),
                "minimum": self.zmin_spin.value(),
                "maximum": self.zmax_spin.value(),
            },
        )

    def _populate_saved_overlay_combo(self, selected_name: str | None = None) -> None:
        if not hasattr(self, "saved_overlay_combo"):
            return
        data_key = self._current_data_key()
        overlays = self.overlay_store.list_all(data_key) if data_key else []
        current = selected_name or self.saved_overlay_combo.currentText()
        self.saved_overlay_combo.blockSignals(True)
        self.saved_overlay_combo.clear()
        self.saved_overlay_combo.addItems([overlay.name for overlay in overlays])
        index = self.saved_overlay_combo.findText(current)
        if index >= 0:
            self.saved_overlay_combo.setCurrentIndex(index)
        self.saved_overlay_combo.blockSignals(False)
        available = bool(overlays)
        self.load_overlay_button.setEnabled(available)
        self.rename_overlay_button.setEnabled(available)
        self.delete_overlay_button.setEnabled(available)

    def _save_overlay(self, name: str) -> bool:
        data_key = self._current_data_key()
        if not data_key or not name.strip():
            return False
        try:
            self.overlay_store.save(data_key, self._capture_overlay(name.strip()))
        except ValueError as error:
            self.statusBar().showMessage(str(error), 5000)
            return False
        self._populate_saved_overlay_combo(name.strip())
        return True

    def _save_overlay_dialog(self) -> None:
        name, accepted = QInputDialog.getText(self, "Save Overlay", "Overlay name:")
        if accepted:
            self._save_overlay(name)

    def _load_selected_overlay(self) -> None:
        data_key = self._current_data_key()
        overlay = self.overlay_store.get(data_key, self.saved_overlay_combo.currentText()) \
            if data_key else None
        if overlay is not None:
            self._apply_overlay(overlay)

    def _apply_overlay(self, overlay: SavedOverlay) -> bool:
        if self.experiment is None:
            return False
        x_ref = AxisRef(**overlay.x_axis) if overlay.x_axis else None
        y_ref = AxisRef(**overlay.y_axis) if overlay.y_axis else None
        x_index = self._find_axis_ref_index(self.x_combo, x_ref) if x_ref else -1
        y_index = self._find_axis_ref_index(self.y_combo, y_ref) if y_ref else -1
        if x_index < 0 or y_index < 0:
            self.statusBar().showMessage("Saved Overlay axes are unavailable for this Data.", 5000)
            return False
        self.x_combo.blockSignals(True)
        self.y_combo.blockSignals(True)
        self.x_combo.setCurrentIndex(x_index)
        self.y_combo.setCurrentIndex(y_index)
        self.x_combo.blockSignals(False)
        self.y_combo.blockSignals(False)
        transform_index = self.transform_combo.findText(overlay.transform_name or "")
        if transform_index >= 0:
            self.transform_combo.setCurrentIndex(transform_index)
        self._on_axis_changed()
        self.db_checkbox.setChecked(overlay.db and self.db_checkbox.isEnabled())
        self.unwrap_checkbox.setChecked(overlay.unwrap and self.unwrap_checkbox.isEnabled())
        self.trace_color_combo.setCurrentText(overlay.color_mode)

        state = overlay.two_d
        for combo, key in ((self.z_combo_2d, "z"), (self.x_combo_2d, "x"),
                           (self.y_combo_2d, "y"), (self.colormap_combo, "colormap")):
            text = state.get(key)
            if text and combo.findText(text) >= 0:
                combo.blockSignals(True)
                combo.setCurrentText(text)
                combo.blockSignals(False)
        transform_2d = self.transform_combo_2d.findData(state.get("transform"))
        if transform_2d >= 0:
            self.transform_combo_2d.blockSignals(True)
            self.transform_combo_2d.setCurrentIndex(transform_2d)
            self.transform_combo_2d.blockSignals(False)
        auto = bool(state.get("auto_range", True))
        self.auto_range_checkbox.blockSignals(True)
        self.auto_range_checkbox.setChecked(auto)
        self.auto_range_checkbox.blockSignals(False)
        self.zmin_spin.setEnabled(not auto)
        self.zmax_spin.setEnabled(not auto)
        if not auto:
            self.zmin_spin.blockSignals(True)
            self.zmax_spin.blockSignals(True)
            self.zmin_spin.setValue(float(state.get("minimum", self.zmin_spin.value())))
            self.zmax_spin.setValue(float(state.get("maximum", self.zmax_spin.value())))
            self.zmin_spin.blockSignals(False)
            self.zmax_spin.blockSignals(False)
        restored = self.log_entries.restore_selection(
            overlay.selected_traces,
            active=overlay.active_trace,
            visible=overlay.visible_traces,
            reference=overlay.reference_trace,
        )
        if restored:
            self.mode_combo.setCurrentIndex(max(0, min(overlay.plot_mode, 2)))
            if overlay.plot_mode == 1:
                self._rebuild_2d_plot()
            self._refresh_multi_trace_panel()
        return restored

    def _rename_overlay_dialog(self) -> None:
        old_name = self.saved_overlay_combo.currentText()
        if not old_name:
            return
        new_name, accepted = QInputDialog.getText(
            self, "Rename Overlay", "New name:", text=old_name
        )
        data_key = self._current_data_key()
        if accepted and data_key and self.overlay_store.rename(data_key, old_name, new_name.strip()):
            self._populate_saved_overlay_combo(new_name.strip())

    def _delete_selected_overlay(self) -> None:
        data_key = self._current_data_key()
        name = self.saved_overlay_combo.currentText()
        if data_key and name and self.overlay_store.delete(data_key, name):
            self._populate_saved_overlay_combo()

    def _navigate_trace(self, delta: int, *, extend: bool = False) -> None:
        """Sequential trace navigation (keyboard Up/Down) - delegates
        to LogEntriesWidget.select_row(), which clamps to the valid
        range internally, so boundary handling comes for free."""
        if self.log_entries.row_count() <= 1:
            return
        self.log_entries.move_active(delta, extend=extend)

    # ---- 1D plotting --------------------------------------------------------------

    def _axis_data_with_modifiers(self, candidate: AxisCandidate, entry_idx: int):
        """Fetch an axis and apply only modifiers meaningful to derived data."""
        if candidate.domain == "entries":
            return np.asarray(
                self.cached.get_data(candidate.base_channel, transform="raw")
            ).reshape(-1)
        if candidate.source == "trace_axis":
            return np.asarray(
                self.experiment.vector_traces[candidate.base_channel].x_values
            ).reshape(-1)
        transform = "raw"
        if candidate.source == "derived":
            transform = candidate.transform_key or "raw"
            if candidate.transform_key == "magnitude" and self.db_checkbox.isChecked():
                transform = "magnitude_db"
        # A normal 1D trace only needs one sweep entry.  Asking the existing
        # reader for that slice avoids materialising every trace merely to
        # discard all but the active one during Viewer first paint.
        values = self.cached.get_data(
            candidate.base_channel, transform=transform, entry_slice=entry_idx
        )
        values = np.asarray(values).reshape(-1)
        if candidate.transform_key in ("phase_deg", "phase_rad") and self.unwrap_checkbox.isChecked():
            unit = "deg" if candidate.transform_key == "phase_deg" else "rad"
            return unwrap_phase(values, unit=unit)
        return values

    @staticmethod
    def _entry_from_full_data(values, entry_idx: int) -> np.ndarray:
        array = np.asarray(values)
        if array.ndim <= 1:
            return array.reshape(-1)
        return np.take(array, int(entry_idx), axis=-1).reshape(-1)

    def _plot_arrays_for_entry(
        self, x_cand: AxisCandidate, y_cand: AxisCandidate,
        entry_idx: int, spec: TransformSpec,
        *, apply_formula: bool = True,
    ) -> tuple[np.ndarray, np.ndarray, str, str | None]:
        """Resolve one trace through the same cached axis/transform path."""
        x_data = self._axis_data_with_modifiers(x_cand, entry_idx)
        transform_label = "raw"
        y_label_suffix = None
        if (self._is_transformable_y_candidate(y_cand)
                and not self._is_iq_axis_pair(x_cand, y_cand)):
            complex_data = np.asarray(
                self.cached.get_data(y_cand.base_channel, transform="raw", entry_slice=entry_idx)
            ).reshape(-1)
            y_data = spec.apply(complex_data)
            transform_label = spec.resolve_transform_key()
            y_label_suffix = spec.name + (" (unwrapped)" if spec.unwrap else "")
        else:
            y_data = self._axis_data_with_modifiers(y_cand, entry_idx)
            if (y_cand.source == "derived" and y_cand.transform_key == "magnitude"
                    and self.db_checkbox.isChecked()):
                y_label_suffix = "Magnitude (dB)"
                transform_label = "magnitude_db"
            elif (y_cand.source == "derived"
                  and y_cand.transform_key in ("phase_deg", "phase_rad")
                  and self.unwrap_checkbox.isChecked()):
                y_label_suffix = "Phase (unwrapped)"
                transform_label = y_cand.transform_key
        x_data, y_data = np.asarray(x_data), np.asarray(y_data)
        state = self._pane_states.get(self._active_pane_id)
        if (apply_formula and state is not None and state.plot_mode == 0
                and state.formula_enabled):
            x_data, y_data = apply_formulas(
                x_data, y_data, state.x_formula, state.y_formula,
            )
            y_label_suffix = "Formula"
        return np.asarray(x_data), np.asarray(y_data), transform_label, y_label_suffix

    @staticmethod
    def _axis_mark_identity(candidate: AxisCandidate) -> tuple:
        return (
            candidate.name,
            candidate.domain,
            candidate.source,
            candidate.base_channel,
            candidate.transform_key,
        )

    def _axis_display_unit(self, candidate: AxisCandidate) -> str | None:
        if candidate.source != "derived":
            return candidate.unit
        if candidate.transform_key == "magnitude" and self.db_checkbox.isChecked():
            return "dB"
        if candidate.transform_key == "phase_deg":
            return "deg"
        if candidate.transform_key == "phase_rad":
            return "rad"
        return candidate.unit

    def _axis_coordinate_semantic(self, candidate: AxisCandidate) -> tuple:
        modifier = None
        if candidate.source == "derived":
            if candidate.transform_key == "magnitude" and self.db_checkbox.isChecked():
                modifier = "db"
            elif (candidate.transform_key in ("phase_deg", "phase_rad")
                  and self.unwrap_checkbox.isChecked()):
                modifier = "unwrap"
        return self._axis_mark_identity(candidate), modifier

    @staticmethod
    def _transformed_unit(transform: str, source_unit: str | None) -> str | None:
        if transform == "magnitude_db":
            return "dB"
        if transform == "phase_deg":
            return "deg"
        if transform == "phase_rad":
            return "rad"
        return source_unit

    def update_plot(self, _signal_value=None, *, preserve_view: bool = False) -> None:
        """v0.9B: X and Y are both arbitrary AxisCandidates. Domain
        compatibility (spec §13) is checked before touching any data —
        an incompatible pairing shows a clear message and leaves the
        previous plot untouched, never crashes."""
        if self._loading_file or self.experiment is None or self.cached is None or self.mgr is None:
            return
        if not self.multi_pane_splitter.isHidden() and not self._multi_pane_loading:
            self._capture_active_pane_state()
            self._render_multi_panes()
            return
        preserve_view = preserve_view or self._preserve_overlay_view_once
        self._preserve_overlay_view_once = False
        x_name = self.x_combo.currentText()
        y_name = self.y_combo.currentText()
        if not x_name or not y_name:
            return
        x_cand: AxisCandidate | None = self.x_combo.currentData()
        y_cand: AxisCandidate | None = self.y_combo.currentData()
        if x_cand is None or y_cand is None:
            return

        if not self.mgr.axis_domains_compatible(x_cand, y_cand):
            QMessageBox.warning(
                self, "Incompatible axes",
                f"'{x_name}' and '{y_name}' cannot be plotted against each other — "
                f"they vary over different things (e.g. one is a per-sweep-entry "
                f"value, the other varies within a single trace). Pick two axes "
                f"from the same domain.",
            )
            return  # keep the existing plot untouched

        spec = self._current_transform_spec()
        entry_idx = self.log_entries.current_row()

        try:
            x_data, y_data, transform_label, y_label_suffix = self._plot_arrays_for_entry(
                x_cand, y_cand, entry_idx, spec
            )
            formula_state = self._pane_states.get(self._active_pane_id)
            formula_on = bool(formula_state and formula_state.formula_enabled)

            self._update_entry_label(entry_idx)

            title_suffix = ""
            if y_cand.domain == "points":
                title_suffix = f"  (Trace {entry_idx + 1}/{self.log_entries.total_entries()})"

            y_label = (f"{y_name} \u2014 Formula" if formula_on else
                       (f"{y_name} \u2014 {y_label_suffix}" if y_label_suffix
                        else f"{y_name} [{y_cand.unit or '-'}]"))

            selected = self.trace_selection.visible_selection()
            if x_cand.domain != "points" or y_cand.domain != "points":
                selected = (entry_idx,)
            traces: dict[int, tuple[np.ndarray, np.ndarray]] = {}
            skipped = []
            for trace in selected:
                try:
                    tx, ty, _, _ = self._plot_arrays_for_entry(x_cand, y_cand, trace, spec)
                    if tx.size != ty.size or not np.any(np.isfinite(tx) & np.isfinite(ty)):
                        raise ValueError("no compatible finite samples")
                    traces[trace] = (tx, ty)
                except Exception as error:
                    skipped.append((trace, str(error)))

            previous_count = len(self.plot_widget._trace_data)
            transition_to_overlay = previous_count <= 1 and len(traces) > 1
            valid = self.plot_widget.plot_traces(
                traces,
                active_trace=entry_idx,
                x_label=(f"{x_name} [Formula]" if formula_on and formula_state.x_formula
                         else f"{x_name} [{x_cand.unit or '-'}]"),
                y_label=y_label,
                title=f"{y_name} vs {x_name}{title_suffix}",
                trace_labels={trace: f"Trace {trace + 1}" for trace in traces},
                color_mode=self.trace_color_combo.currentText() or SEQUENTIAL,
                auto_range=(not preserve_view or transition_to_overlay),
            )
            self.plot_widget.set_data_points(
                self.show_data_points_checkbox.isChecked(),
                self.point_size_mode_combo.currentText(), self.manual_point_size_spin.value(),
            )
            self._overlay_plot_signature = (
                self._current_data_key(), self._axis_mark_identity(x_cand),
                self._axis_mark_identity(y_cand), transform_label, bool(spec.unwrap),
            )
            if formula_on:
                self._overlay_plot_signature += (formula_state.x_formula, formula_state.y_formula)
            if skipped:
                self.statusBar().showMessage(
                    f"Rendered {len(valid)} traces; skipped {len(skipped)} incompatible trace(s)."
                )
            context = (
                self._current_data_key(),
                self._axis_mark_identity(x_cand),
                self._axis_mark_identity(y_cand),
                entry_idx if x_cand.domain == "points" else None,
            )
            if formula_on:
                context += ("formula", formula_state.x_formula, formula_state.y_formula)
            y_unit = (self._transformed_unit(transform_label, y_cand.unit)
                      if y_cand.is_complex else self._axis_display_unit(y_cand))
            if formula_on and formula_state.y_formula:
                y_unit = None
            manager = self._mark_managers[0]
            if manager.context_key != context:
                self._persist_mark_manager(manager, 1)
            context_changed = manager.set_1d_context(
                context,
                x_data,
                y_data,
                x_name=x_name,
                y_name=y_name,
                x_unit=self._axis_display_unit(x_cand),
                y_unit=y_unit,
                x_semantic=self._axis_coordinate_semantic(x_cand),
                y_semantic=(
                    self._axis_mark_identity(y_cand), transform_label, bool(spec.unwrap)
                ) if y_cand.is_complex else self._axis_coordinate_semantic(y_cand),
            )
            if context_changed:
                self._analysis_target_id = None
                self._restore_mark_manager(manager, 1)
            self._sync_range_half_peak_visuals(self._mark_managers[0], self._mark_overlays[0])
            self._clear_analysis_preview()
            self._refresh_mark_ui()
            if self.mode_combo.currentIndex() == 0:
                self._update_data_table(
                    x_data, y_data, x_name=x_name, x_unit=x_cand.unit,
                    y_name=y_name, y_unit=(None if y_label_suffix or formula_on else y_cand.unit),
                    transform=transform_label,
                )
        except ChannelNotFound as e:
            QMessageBox.warning(self, "Channel not found", str(e))
        except Exception as e:
            QMessageBox.warning(self, "Plot error", f"Could not plot this selection:\n\n{e}")

    def _update_entry_label(self, entry_idx: int) -> None:
        """Shows the active step axis value at the selected sweep
        entry, e.g. 'Average Current = 162.615 mA', so the user knows
        what point they're looking at without needing to cross-reference
        the Log Entries table."""
        if not self.experiment.step_axes:
            self.entry_label.setText(f"(entry {entry_idx})")
            return
        parts = []
        for axis in self.experiment.step_axes:
            if entry_idx < len(axis.values):
                val = axis.values[entry_idx]
                unit = axis.channel.unit or ""
                parts.append(f"{axis.channel.name} = {val:.6g} {unit}".strip())
        self.entry_label.setText("  |  ".join(parts))

    # ---- 2D combo population (Phase 6) ------------------------------------------

    def _populate_z_combo_2d(self) -> None:
        self.z_combo_2d.blockSignals(True)
        self.z_combo_2d.clear()
        for name in self.mgr.list_2d_z_candidates():
            self.z_combo_2d.addItem(name)
        self.z_combo_2d.blockSignals(False)
        self._on_z_2d_changed()

    def _on_z_2d_changed(self) -> None:
        if self.experiment is None:
            return
        z_name = self.z_combo_2d.currentText()
        if not z_name:
            self.x_combo_2d.clear()
            self.y_combo_2d.clear()
            return

        x_candidates, y_candidates = self.mgr.get_2d_axis_candidates(z_name)

        self.x_combo_2d.blockSignals(True)
        self.x_combo_2d.clear()
        self.x_combo_2d.addItems(x_candidates)
        self.x_combo_2d.setEnabled(bool(x_candidates))
        self.x_combo_2d.blockSignals(False)

        self._2d_y_candidates = y_candidates
        self._populate_2d_y_candidates()

        is_vector = z_name in self.experiment.vector_traces
        self.transform_combo_2d.setEnabled(is_vector)

        self._rebuild_2d_plot()

    def _populate_2d_y_candidates(self) -> None:
        """Keep X and Y distinct while preserving a valid prior Y choice."""
        x_name = self.x_combo_2d.currentText()
        previous_y = self.y_combo_2d.currentText()
        available = [name for name in getattr(self, "_2d_y_candidates", []) if name != x_name]

        self.y_combo_2d.blockSignals(True)
        self.y_combo_2d.clear()
        self.y_combo_2d.addItems(available)
        if previous_y in available:
            self.y_combo_2d.setCurrentText(previous_y)
        self.y_combo_2d.setEnabled(bool(available))
        self.y_combo_2d.blockSignals(False)

    def _on_x_2d_changed(self) -> None:
        self._populate_2d_y_candidates()
        self._rebuild_2d_plot()

    # ---- 2D plotting (Phase 6) -----------------------------------------------------

    def _rebuild_2d_plot(self) -> None:
        """Fetches (possibly cached) 2D grid data and does a full
        re-plot, including current colormap/range settings. Called
        whenever the Z/X/Y/Transform selection changes - NOT called
        for colormap-only or range-only changes, which use the
        lighter-weight Plot2DWidget.set_colormap()/set_color_range()
        paths instead so they never re-fetch data."""
        if self._loading_file or self.experiment is None or self.cached is None:
            return
        if not self.multi_pane_splitter.isHidden() and not self._multi_pane_loading:
            self._capture_active_pane_state()
            self._render_multi_panes()
            return
        z_name = self.z_combo_2d.currentText()
        x_name = self.x_combo_2d.currentText()
        y_name = self.y_combo_2d.currentText()
        if not (z_name and x_name and y_name):
            return

        transform = self.transform_combo_2d.currentData() or "raw"

        try:
            grid = self.cached.get_2d_data(x_name, y_name, z_name, transform=transform)
        except Data2DError as e:
            # spec §12: do not crash, show a clear message, keep the
            # existing plot exactly as it was.
            QMessageBox.warning(self, "Cannot build 2D surface", str(e))
            return
        except ChannelNotFound as e:
            QMessageBox.warning(self, "Channel not found", str(e))
            return
        except Exception as e:
            QMessageBox.warning(self, "2D plot error", f"Could not build this 2D surface:\n\n{e}")
            return

        colormap = self.colormap_combo.currentText() or DEFAULT_COLORMAP
        if self.auto_range_checkbox.isChecked():
            self.plot_2d_widget.plot(grid, colormap=colormap, z_min=None, z_max=None)
            self._sync_range_spins_from_widget()
        else:
            z_min, z_max = self.zmin_spin.value(), self.zmax_spin.value()
            self.plot_2d_widget.plot(grid, colormap=colormap, z_min=z_min, z_max=z_max)

        self._report_partial_acquisition(grid)

        self._refresh_cut_windows()
        context = (self._current_data_key(), z_name, x_name, y_name)
        manager = self._mark_managers[1]
        if manager.context_key != context:
            self._persist_mark_manager(manager, 1)
        context_changed = manager.set_2d_context(
            context,
            grid.x_values,
            grid.y_values,
            grid.z_values,
            x_name=grid.x_name,
            y_name=grid.y_name,
            value_name=grid.z_name,
            x_unit=grid.x_unit,
            y_unit=grid.y_unit,
            value_unit=self._transformed_unit(grid.transform, grid.z_unit),
        )
        if context_changed:
            self._analysis_target_id = None
            self._restore_mark_manager(manager, 1)
        self._clear_analysis_preview()
        self._refresh_mark_ui()

    def _on_colormap_changed(self, name: str) -> None:
        if not name:
            return
        self.plot_2d_widget.set_colormap(name)

    def _on_auto_range_toggled(self, checked: bool) -> None:
        self.zmin_spin.setEnabled(not checked)
        self.zmax_spin.setEnabled(not checked)
        if checked:
            self.plot_2d_widget.auto_range_color()
            self._sync_range_spins_from_widget()
        else:
            self.plot_2d_widget.set_color_range(self.zmin_spin.value(), self.zmax_spin.value())

    def _on_range_spin_changed(self) -> None:
        if self.auto_range_checkbox.isChecked():
            return  # spins are disabled/ignored while Auto is on
        self.plot_2d_widget.set_color_range(self.zmin_spin.value(), self.zmax_spin.value())

    def _on_auto_range_button_clicked(self) -> None:
        self.auto_range_checkbox.blockSignals(True)
        self.auto_range_checkbox.setChecked(True)
        self.auto_range_checkbox.blockSignals(False)
        self.zmin_spin.setEnabled(False)
        self.zmax_spin.setEnabled(False)
        self.plot_2d_widget.auto_range_color()
        self._sync_range_spins_from_widget()

    def _on_colorbar_range_changed(self, low: float, high: float) -> None:
        """Make an interactive colorbar adjustment a real 2D display choice."""
        if self._multi_pane_loading:
            return
        with QSignalBlocker(self.auto_range_checkbox), QSignalBlocker(self.zmin_spin), QSignalBlocker(self.zmax_spin):
            self.auto_range_checkbox.setChecked(False)
            self.zmin_spin.setValue(low)
            self.zmax_spin.setValue(high)
        self.zmin_spin.setEnabled(True)
        self.zmax_spin.setEnabled(True)
        self._schedule_display_state_save()

    def _sync_range_spins_from_widget(self) -> None:
        """Reflects the widget's current (possibly auto-computed)
        z_min/z_max back into the spinboxes for display, without
        triggering another plot rebuild."""
        if self.plot_2d_widget._z_min is None:
            return
        self.zmin_spin.blockSignals(True)
        self.zmax_spin.blockSignals(True)
        self.zmin_spin.setValue(self.plot_2d_widget._z_min)
        self.zmax_spin.setValue(self.plot_2d_widget._z_max)
        self.zmin_spin.blockSignals(False)
        self.zmax_spin.blockSignals(False)

    # ---- 3D Surface / N-D data slicing ---------------------------------------

    def _populate_z_combo_nd(self) -> None:
        self.z_combo_nd.blockSignals(True)
        self.z_combo_nd.clear()
        for name in self.mgr.list_2d_z_candidates():
            self.z_combo_nd.addItem(name)
        self.z_combo_nd.blockSignals(False)
        selected_color = self.surface_color_source_combo.currentData()
        self.surface_color_source_combo.blockSignals(True)
        self.surface_color_source_combo.clear()
        self.surface_color_source_combo.addItem(
            self.localizer.text("viewer.same_as_height"), userData=None
        )
        for name in self.mgr.list_2d_z_candidates():
            self.surface_color_source_combo.addItem(name, userData=name)
        if selected_color is not None:
            index = self.surface_color_source_combo.findData(selected_color)
            if index >= 0:
                self.surface_color_source_combo.setCurrentIndex(index)
        self.surface_color_source_combo.blockSignals(False)
        self._on_z_nd_changed()

    def _on_z_nd_changed(self) -> None:
        if self.experiment is None:
            return
        z_name = self.z_combo_nd.currentText()
        if not z_name:
            self._nd_dims = []
            self.x_combo_nd.clear()
            self.y_combo_nd.clear()
            self.slice_explorer.clear()
            self._nd_height_grid = None
            self._clear_surface(self.localizer.text("viewer.surface_select_data"))
            return

        dims = self.mgr.list_dimensions(z_name)
        self._nd_dims = dims

        self.x_combo_nd.blockSignals(True)
        self.x_combo_nd.clear()
        for d in dims:
            self.x_combo_nd.addItem(d.name)
        self.x_combo_nd.blockSignals(False)

        self._on_x_nd_changed()  # repopulates Y (excluding X) and rebuilds everything downstream

    def _on_x_nd_changed(self) -> None:
        """Repopulates the Y dropdown to exclude whatever is now
        selected as X (so the same dimension can never be picked for
        both), preserving the previous Y choice if it's still valid -
        this is what makes 'swap X and Y' (spec §4) behave naturally
        instead of forcing the user to re-pick Y every time."""
        if self.experiment is None:
            return
        x_name = self.x_combo_nd.currentText()
        prev_y_data = self.y_combo_nd.currentData() if self.y_combo_nd.count() else None

        self.y_combo_nd.blockSignals(True)
        self.y_combo_nd.clear()
        for d in self._nd_dims:
            if d.name != x_name:
                self.y_combo_nd.addItem(d.name, d.name)
        if self.y_combo_nd.count() == 0:
            self.y_combo_nd.addItem(self.localizer.text("viewer.no_second_dimension"), None)

        restored = False
        if prev_y_data is not None:
            idx = self.y_combo_nd.findData(prev_y_data)
            if idx >= 0:
                self.y_combo_nd.setCurrentIndex(idx)
                restored = True
        if not restored:
            self.y_combo_nd.setCurrentIndex(0)
        self.y_combo_nd.blockSignals(False)

        self._on_xy_nd_changed()

    def _on_xy_nd_changed(self) -> None:
        if self.experiment is None:
            return
        x_name = self.x_combo_nd.currentText()
        y_name = self.y_combo_nd.currentData()

        varying = {x_name} | ({y_name} if y_name else set())
        remaining = [d for d in self._nd_dims if d.name not in varying]
        preserve = self.slice_explorer.current_fixed()
        self.slice_explorer.set_dimensions(remaining, preserve_indices=preserve)
        has_slice_dimensions = bool(remaining)
        self.surface_slice_label.setVisible(has_slice_dimensions)
        self.slice_explorer.setVisible(has_slice_dimensions)
        self.phase_unwrap_axis_combo.setItemText(
            0, f"Along X ({x_name})" if x_name else "Along X"
        )
        self.phase_unwrap_axis_combo.setItemText(
            1, f"Along Y ({y_name})" if y_name else "Along Y"
        )
        self._populate_point_mappings()

        z_name = self.z_combo_nd.currentText()
        is_vector = bool(z_name) and z_name in self.experiment.vector_traces
        self.transform_combo_nd.setEnabled(is_vector)
        self.surface_color_transform_combo.setEnabled(
            self.surface_color_source_combo.currentData() is not None
        )
        self._rebuild_nd_plot()

    def _ensure_nd_surface_renderer(self):
        from app.visualization3d.graphs_renderer import SURFACE_GEOMETRIES, GraphsSurfaceRenderer, graphs_available
        from app.visualization3d.renderer import SurfaceRenderer
        from app.visualization3d.transparent_renderer import TransparentSurfaceRenderer

        # Surface-family geometries use Qt Graphs 3D (GPU via RHI, true
        # transparency); Trajectory/Scatter keep the Data Visualization point
        # graph. Without Qt Graphs (e.g. offscreen), the previous renderers apply.
        geometry = self.geometry_combo.currentData() or "Surface"
        if graphs_available() and geometry in SURFACE_GEOMETRIES:
            kind, renderer_type = "graphs", GraphsSurfaceRenderer
        elif geometry == "Transparent Surface":
            kind, renderer_type = "transparent", TransparentSurfaceRenderer
        else:
            kind, renderer_type = "native", SurfaceRenderer
        active_kind = getattr(self, "_nd_surface_renderer_kind", None)
        if self.nd_surface_renderer is not None and active_kind == kind:
            return self.nd_surface_renderer

        camera_state = None
        if self.nd_surface_renderer is not None:
            if {active_kind, kind} <= {"native", "transparent"}:
                camera_state = self.nd_surface_renderer.camera_state()
            self.nd_surface_layout.removeWidget(self.nd_surface_renderer)
            self.nd_surface_renderer.hide()

        cache_name = f"_{kind}_nd_surface_renderer"
        renderer = getattr(self, cache_name, None)
        if renderer is None:
            renderer = renderer_type(self.nd_surface_host, self.localizer)
            renderer.surface_ready.connect(
                lambda grid, owner=renderer: self._surface_renderer_ready(owner, grid)
            )
            renderer.surface_failed.connect(
                lambda reason, owner=renderer: self._surface_renderer_failed(owner, reason)
            )
            renderer.color_range_changed.connect(self._on_3d_colorbar_range_changed)
            renderer.point_render_stage.connect(self._journal_operation)
            renderer.export_context_requested.connect(self._show_3d_export_menu)
            renderer.share_drag_requested.connect(self._start_3d_share_drag)
            renderer._share_mode = self._three_d_share_mode
            copy_shortcut = QShortcut(self.copy_plot_action.shortcut(), renderer)
            copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            copy_shortcut.activated.connect(self.copy_plot_action.trigger)
            self._plot_shortcuts.append(copy_shortcut)
            self.localizer.language_changed.connect(renderer.retranslate)
            setattr(self, cache_name, renderer)

        self.nd_surface_layout.removeWidget(self.nd_surface_placeholder)
        self.nd_surface_placeholder.hide()
        self.nd_surface_layout.addWidget(renderer)
        renderer.show()
        self.nd_surface_renderer = renderer
        self._nd_surface_renderer_kind = kind
        if camera_state is not None:
            renderer.apply_camera_state(camera_state)
        return renderer

    def _populate_point_mappings(self) -> None:
        dimensions = []
        x_name, y_name = self.x_combo_nd.currentText(), self.y_combo_nd.currentData()
        if x_name:
            dimensions.append((x_name, x_name))
        if y_name:
            dimensions.append((str(y_name), str(y_name)))
        derived = [
            ("Point / Index", "point_index"),
            ("Real", "real"), ("Imaginary", "imag"), ("Magnitude", "magnitude"),
            ("Magnitude (dB)", "magnitude_db"), ("Phase (deg)", "phase_deg"),
            ("Phase (rad)", "phase_rad"), ("Unwrapped Phase (deg)", "phase_unwrapped_deg"),
            ("Unwrapped Phase (rad)", "phase_unwrapped_rad"),
        ]
        options = dimensions + derived
        defaults = {
            "Trajectory": ("real", "imag", x_name or "frequency", "magnitude_db"),
            "Scatter": (x_name or "frequency", str(y_name or "sweep"),
                        self.transform_combo_nd.currentData() or "magnitude_db", "magnitude_db"),
        }
        geometry = self.geometry_combo.currentData()
        default_values = defaults.get(geometry, defaults["Scatter"])
        for combo, default in zip((self.point_x_mapping_combo, self.point_y_mapping_combo,
                                   self.point_z_mapping_combo, self.point_color_mapping_combo),
                                  default_values):
            with QSignalBlocker(combo):
                combo.clear()
                for label, key in options:
                    combo.addItem(label, userData=key)
                index = combo.findData(default)
                combo.setCurrentIndex(index if index >= 0 else 0)

    def _update_geometry_controls(self) -> None:
        geometry = self.geometry_combo.currentData() or "Surface"
        structured = geometry in {"Surface", "Transparent Surface", "Dual Surface", "Waterfall"}
        point_mode = geometry in {"Trajectory", "Scatter"}
        visibility = {
            "x_axis": structured, "y_axis": structured, "height_axis": structured,
            "height_transform": structured,
            "unwrap_axis": structured and (
                "phase" in str(self.transform_combo_nd.currentData())
                or "phase" in str(self.surface_color_transform_combo.currentData())
            ),
            "opacity": geometry in {"Surface", "Transparent Surface", "Dual Surface"},
            "projection": True,
            "reference": True,
            "reference_value": self.surface_reference_combo.currentData() == "custom",
            "surface_b": geometry == "Dual Surface",
            "opacity_b": geometry == "Dual Surface",
            "point_x": point_mode, "point_y": point_mode, "point_z": point_mode,
            "point_color": point_mode, "point_size": point_mode,
            "top_colorbar": geometry != "Transparent Surface",
        }
        for key, (label, widget) in self._three_d_control_rows.items():
            shown = visibility.get(key, True)
            label.setVisible(shown)
            widget.setVisible(shown)
        if "opacity" in self._three_d_control_rows:
            self._three_d_control_rows["opacity"][0].setText(
                self.localizer.text("viewer.transparency") if geometry == "Transparent Surface"
                else self.localizer.text("viewer.surface_a_opacity") if geometry == "Dual Surface"
                else self.localizer.text("viewer.opacity")
            )
        self._surface_color_source_label.setVisible(not point_mode)
        self.surface_color_source_combo.setVisible(not point_mode)
        self._surface_color_transform_label.setVisible(not point_mode)
        self.surface_color_transform_combo.setVisible(not point_mode)
        self.point_size_spin.setEnabled(point_mode)
        self.surface_reference_value.setEnabled(self.surface_reference_combo.currentData() == "custom")

    def _on_3d_geometry_changed(self, *_args) -> None:
        geometry = self.geometry_combo.currentData() or "Surface"
        if geometry == "Transparent Surface":
            with QSignalBlocker(self.surface_opacity_slider):
                self.surface_opacity_slider.setValue(35)
            self.surface_opacity_label.setText("35%")
        elif self.sender() is self.geometry_combo:
            with QSignalBlocker(self.surface_opacity_slider):
                self.surface_opacity_slider.setValue(100)
            self.surface_opacity_label.setText("100%")
        previous = getattr(self, "_geometry_before_dual", None)
        if geometry == "Dual Surface" and previous is None:
            # Dual defaults to Real vs Imaginary; remember the user's height
            # transform so leaving Dual restores it.
            self._geometry_before_dual = self.transform_combo_nd.currentData()
            with QSignalBlocker(self.transform_combo_nd):
                index = self.transform_combo_nd.findData("real")
                if index >= 0:
                    self.transform_combo_nd.setCurrentIndex(index)
            self.surface_b_transform_combo.setCurrentIndex(
                self.surface_b_transform_combo.findData("imag")
            )
        elif geometry != "Dual Surface" and previous is not None:
            self._geometry_before_dual = None
            with QSignalBlocker(self.transform_combo_nd):
                index = self.transform_combo_nd.findData(previous)
                if index >= 0:
                    self.transform_combo_nd.setCurrentIndex(index)
        self._populate_point_mappings()
        self._update_geometry_controls()
        self._rebuild_nd_plot()

    def _on_surface_opacity_changed(self, value: int) -> None:
        self.surface_opacity_label.setText(f"{int(value)}%")
        if self.nd_surface_renderer is not None:
            opacity = 1.0 - int(value) / 100.0 if (
                self.geometry_combo.currentData() == "Transparent Surface"
            ) else int(value) / 100.0
            self.nd_surface_renderer.set_opacity(opacity)

    def _on_surface_b_opacity_changed(self, value: int) -> None:
        self.surface_b_opacity_label.setText(f"{int(value)}%")
        if self.nd_surface_renderer is not None:
            self.nd_surface_renderer.set_secondary_opacity(int(value) / 100.0)

    def _on_surface_projection_toggled(self, checked: bool) -> None:
        if self.nd_surface_renderer is not None:
            self.nd_surface_renderer.set_bottom_projection(checked)

    def _on_surface_reference_changed(self, *_args) -> None:
        mode = self.surface_reference_combo.currentData() or "off"
        self.surface_reference_value.setEnabled(mode == "custom")
        self._update_geometry_controls()
        if self.nd_surface_renderer is not None:
            self.nd_surface_renderer.set_reference_plane(mode, self.surface_reference_value.value())

    def _on_top_colorbar_toggled(self, checked: bool) -> None:
        if self.nd_surface_renderer is not None:
            self.nd_surface_renderer.set_horizontal_colorbar_visible(checked)

    def _on_3d_colorbar_range_changed(self, low: float, high: float) -> None:
        with QSignalBlocker(self.auto_range_checkbox_nd):
            self.auto_range_checkbox_nd.setChecked(False)
        self.zmin_spin_nd.setEnabled(True)
        self.zmax_spin_nd.setEnabled(True)
        with QSignalBlocker(self.zmin_spin_nd), QSignalBlocker(self.zmax_spin_nd):
            self.zmin_spin_nd.setValue(float(low))
            self.zmax_spin_nd.setValue(float(high))
        # Route through the normal range handler so the Heatmap, renderer,
        # projection, and colorbar all consume the same range state.
        self._on_range_spin_nd_changed()

    def _clear_surface(self, message: str) -> None:
        if self.nd_surface_renderer is not None:
            self.nd_surface_renderer.clear()
        self.nd_surface_placeholder.setText(message)
        self.nd_surface_placeholder.show()
        self.nd_plot_stack.setCurrentIndex(2)

    def _surface_async_ready(self, _grid) -> None:
        if (not self._three_d_active()
                or self.nd_surface_renderer._source_height_grid is not self._nd_height_grid):
            self.nd_surface_renderer.clear()
            return
        self._finish_surface_render()

    def _surface_renderer_ready(self, renderer, grid) -> None:
        if renderer is self.nd_surface_renderer:
            self._surface_async_ready(grid)

    def _surface_renderer_failed(self, renderer, reason: str) -> None:
        if renderer is self.nd_surface_renderer:
            self._surface_async_failed(reason)

    def _surface_async_failed(self, reason: str) -> None:
        if self._three_d_active():
            self._clear_surface(self.localizer.text("viewer.surface_error", reason=reason))

    def _finish_surface_render(self) -> None:
        renderer = self.nd_surface_renderer
        if renderer is None or renderer.surface_grid is None:
            return
        height_grid = self._nd_height_grid
        self.nd_surface_placeholder.hide()
        self.surface_performance_label.setText(self.localizer.text(
            "viewer.surface_performance_details",
            source_rows=height_grid.z_values.shape[0],
            source_columns=height_grid.z_values.shape[1],
            render_rows=renderer.surface_grid.shape[0],
            render_columns=renderer.surface_grid.shape[1],
            policy=renderer.rendering_decision.effective.value,
            vertices=renderer.surface_grid.vertex_count,
        ))
        actual_range = renderer.color_range
        if actual_range is not None:
            with QSignalBlocker(self.zmin_spin_nd), QSignalBlocker(self.zmax_spin_nd):
                self.zmin_spin_nd.setValue(actual_range[0])
                self.zmax_spin_nd.setValue(actual_range[1])
        projection_index = self.surface_projection_combo.currentIndex()
        renderer.set_projection(self.surface_projection_combo.itemData(projection_index))
        if not self.surface_z_auto_checkbox.isChecked():
            renderer.set_z_scale(self.surface_z_scale_slider.value() / 10.0)
        self._apply_pending_3d_camera()

    def _apply_pending_3d_camera(self) -> None:
        raw = getattr(self, "_pending_3d_camera_state", None)
        if raw is None or self.nd_surface_renderer is None:
            return
        from app.visualization3d.state import CameraState3D
        self.nd_surface_renderer.apply_camera_state(CameraState3D.from_dict(raw))
        self._pending_3d_camera_state = None

    def _toggle_surface_advanced(self, expanded: bool) -> None:
        self.surface_advanced_content.setVisible(expanded)
        self.surface_advanced_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )

    def _surface_color_grid(self, height_grid):
        channel_name = self.surface_color_source_combo.currentData()
        if channel_name is None:
            return height_grid
        return self._get_3d_grid(
            channel_name, self.surface_color_transform_combo.currentData() or "raw"
        )

    def _get_3d_grid(self, channel_name: str, transform: str):
        unwrap_transform = str(transform).startswith("phase_unwrapped_")
        fetch_transform = {
            "phase_unwrapped_deg": "phase_deg",
            "phase_unwrapped_rad": "phase_rad",
        }.get(transform, transform)
        grid = self.cached.get_nd_slice(
            channel_name, x_dim=self.x_combo_nd.currentText(),
            y_dim=self.y_combo_nd.currentData(), fixed=self.slice_explorer.current_fixed(),
            transform=fetch_transform,
        )
        return transform_grid_3d(
            grid, transform, unwrap_axis=int(self.phase_unwrap_axis_combo.currentData() or 0)
        ) if unwrap_transform else grid

    def _surface_color_limits(self, values: np.ndarray | None = None):
        if values is None:
            if (self.geometry_combo.currentData() in {"Trajectory", "Scatter"}
                    and self.nd_surface_renderer is not None
                    and self.nd_surface_renderer._point_cloud is not None):
                values = self.nd_surface_renderer._point_cloud.color_values
            if self.nd_surface_renderer is not None and self.nd_surface_renderer.surface_grid is not None:
                if values is None:
                    values = self.nd_surface_renderer.surface_grid.color_values
            elif values is None and self._nd_height_grid is not None:
                values = np.asarray(self._nd_height_grid.z_values)
        if values is None:
            return None
        from app.gui.plot_2d_widget import robust_color_limits
        return robust_color_limits(values)

    def _update_surface_color_display(self, *, limits=None) -> None:
        if (self.nd_surface_renderer is not None and self.nd_surface_renderer.is_preparing
                and self._nd_height_grid is not None):
            self._render_current_surface(self._nd_height_grid)
            return
        if self.nd_surface_renderer is None:
            return
        if limits is None and not self.auto_range_checkbox_nd.isChecked():
            limits = (self.zmin_spin_nd.value(), self.zmax_spin_nd.value())
        if self.geometry_combo.currentData() in {"Trajectory", "Scatter"}:
            self.nd_surface_renderer.set_point_color_settings(
                colormap=self.colormap_combo_nd.currentText() or DEFAULT_COLORMAP,
                color_range=limits,
            )
            return
        if self.nd_surface_renderer.surface_grid is None:
            return
        self.nd_surface_renderer.set_color_settings(
            colormap=self.colormap_combo_nd.currentText() or DEFAULT_COLORMAP,
            color_range=limits,
        )
        actual = self.nd_surface_renderer.color_range
        if actual is not None:
            with QSignalBlocker(self.zmin_spin_nd), QSignalBlocker(self.zmax_spin_nd):
                self.zmin_spin_nd.setValue(actual[0])
                self.zmax_spin_nd.setValue(actual[1])

    def _render_current_surface(self, height_grid) -> None:
        if not self._three_d_active():
            return
        geometry = self.geometry_combo.currentData() or "Surface"
        if geometry in {"Surface", "Transparent Surface", "Dual Surface", "Waterfall"} and self.y_combo_nd.currentData() is None:
            self._clear_surface(self.localizer.text("viewer.surface_needs_xy"))
            return
        requested_policy = self.surface_rendering_combo.currentData() or "Auto"
        if not self._confirm_large_surface_request(height_grid, requested_policy):
            with QSignalBlocker(self.surface_rendering_combo):
                auto_index = self.surface_rendering_combo.findData("Auto")
                if auto_index >= 0:
                    self.surface_rendering_combo.setCurrentIndex(auto_index)
            requested_policy = "Auto"
        self.nd_plot_stack.setCurrentIndex(2)
        self.nd_surface_placeholder.setText(self.localizer.text("viewer.surface_loading"))
        self._journal_operation(
            f"3D {geometry} rendering started",
            rows=int(height_grid.z_values.shape[0]),
            columns=int(height_grid.z_values.shape[1]),
            policy=requested_policy,
        )
        try:
            color_grid = self._surface_color_grid(height_grid)
            if self.auto_range_checkbox_nd.isChecked():
                from app.gui.plot_2d_widget import robust_color_limits
                limits = robust_color_limits(color_grid.z_values)
            else:
                limits = (self.zmin_spin_nd.value(), self.zmax_spin_nd.value())
            if geometry in {"Trajectory", "Scatter"}:
                self._journal_operation(f"3D {geometry} preparation started")
                raw_grid = self._get_3d_grid(self.z_combo_nd.currentText(), "raw")
                point_keys = [combo.currentData() for combo in (
                    self.point_x_mapping_combo, self.point_y_mapping_combo,
                    self.point_z_mapping_combo, self.point_color_mapping_combo,
                )]

                def mapped(key):
                    if key in {"phase_unwrapped_deg", "phase_unwrapped_rad"}:
                        return transform_grid_3d(
                            raw_grid, key,
                            unwrap_axis=int(self.phase_unwrap_axis_combo.currentData() or 0),
                        ).z_values
                    return mapping_values(raw_grid, key)

                point_arrays = [mapped(key) for key in point_keys]
                point_count = int(np.size(point_arrays[2]))
                if any(key is None for key in point_keys):
                    raise ValueError("Select valid X, Y, Z and Color mappings before rendering.")
                if self.auto_range_checkbox_nd.isChecked():
                    from app.gui.plot_2d_widget import robust_color_limits
                    limits = robust_color_limits(point_arrays[3])
                if geometry == "Trajectory":
                    trace_index = self.log_entries.current_row()
                    cloud = prepare_trajectory_trace(
                        *point_arrays, trace_index=trace_index, max_points=20_000,
                    )
                else:
                    cloud = prepare_point_cloud(*point_arrays, max_points=20_000)
                if (cloud.coordinates.ndim != 2 or cloud.coordinates.shape[1] != 3
                        or len(cloud.coordinates) < (2 if geometry == "Trajectory" else 1)
                        or not np.isfinite(cloud.coordinates).all()
                        or not np.isfinite(cloud.color_values).all()):
                    raise ValueError("The selected 3D mapping has no valid finite point geometry.")
                self._journal_operation(
                    f"3D {geometry} data validated", points=len(cloud.coordinates)
                )

                def mapping_label(key):
                    if key == raw_grid.x_name or key in ("x", "frequency"):
                        return raw_grid.x_name, raw_grid.x_unit
                    if key == raw_grid.y_name or key in ("y", "sweep"):
                        return raw_grid.y_name, raw_grid.y_unit
                    if key == "real":
                        return f"Re({raw_grid.z_name})", raw_grid.z_unit
                    if key in ("imag", "imaginary"):
                        return f"Im({raw_grid.z_name})", raw_grid.z_unit
                    if key == "magnitude":
                        return f"|{raw_grid.z_name}|", raw_grid.z_unit
                    if key == "magnitude_db":
                        return f"|{raw_grid.z_name}|", "dB"
                    if str(key).startswith("phase"):
                        return f"Phase({raw_grid.z_name})", "deg" if "rad" not in str(key) else "rad"
                    return str(key), None

                axis_labels = tuple(mapping_label(key) for key in point_keys[:3])
                renderer = self._ensure_nd_surface_renderer()
                self._journal_operation(
                    f"3D {geometry} renderer update started", points=len(cloud.coordinates)
                )
                renderer.set_point_cloud(
                    cloud, geometry=geometry, axis_labels=axis_labels,
                    color_label=mapping_label(point_keys[3])[0],
                    color_range=limits,
                    colormap=self.colormap_combo_nd.currentText() or DEFAULT_COLORMAP,
                    point_size=self.point_size_spin.value(),
                )
                renderer.set_horizontal_colorbar_visible(self.surface_top_colorbar_checkbox.isChecked())
                renderer.set_bottom_projection(self.surface_projection_checkbox.isChecked())
                renderer.set_reference_plane(
                    self.surface_reference_combo.currentData() or "off",
                    self.surface_reference_value.value(),
                )
                with QSignalBlocker(self.zmin_spin_nd), QSignalBlocker(self.zmax_spin_nd):
                    self.zmin_spin_nd.setValue(float(limits[0]))
                    self.zmax_spin_nd.setValue(float(limits[1]))
                self.zmin_spin_nd.setEnabled(not self.auto_range_checkbox_nd.isChecked())
                self.zmax_spin_nd.setEnabled(not self.auto_range_checkbox_nd.isChecked())
                self.nd_surface_placeholder.hide()
                self.surface_performance_label.setText(
                    f"{geometry}: {len(cloud.coordinates):,} rendered samples / {point_count:,} source points. "
                    "Point rendering is capped at 20,000 samples."
                )
                self._apply_pending_3d_camera()
                return

            renderer = self._ensure_nd_surface_renderer()
            renderer.set_geometry_type(geometry)
            shared_height_limits = None
            secondary_grid = None
            use_background_prepare = False
            if geometry == "Dual Surface":
                renderer.clear_secondary_grid()
                selected_channel = self.z_combo_nd.currentText()
                secondary_transform = self.surface_b_transform_combo.currentData() or "imag"
                secondary_grid = self._get_3d_grid(selected_channel, secondary_transform)
                primary_values = np.asarray(height_grid.z_values, dtype=np.float64)
                secondary_values = np.asarray(secondary_grid.z_values, dtype=np.float64)
                finite = np.concatenate((primary_values[np.isfinite(primary_values)],
                                         secondary_values[np.isfinite(secondary_values)]))
                if finite.size == 0:
                    raise ValueError("Dual Surface mappings contain no finite values.")
                shared_height_limits = (float(np.min(finite)), float(np.max(finite)))
                if shared_height_limits[0] == shared_height_limits[1]:
                    shared_height_limits = (shared_height_limits[0] - 0.5,
                                            shared_height_limits[1] + 0.5)

            if geometry == "Waterfall":
                sample_count = int(height_grid.z_values.size)
                if requested_policy == "Performance":
                    budget = 50_000
                elif requested_policy == "Full Resolution":
                    budget = min(2_000_000, max(8, sample_count * 2))
                elif requested_policy == "Adaptive LOD" or sample_count > 1_000_000:
                    budget = 100_000
                else:
                    budget = min(1_000_000, max(8, sample_count * 2))
                waterfall = prepare_waterfall_grid(height_grid, color_grid, max_vertices=budget)
                renderer.set_waterfall_grid(
                    waterfall, colormap=self.colormap_combo_nd.currentText() or DEFAULT_COLORMAP,
                    color_range=limits, rendering_policy="Auto",
                )
                self.surface_performance_label.setText(
                    f"Waterfall: {height_grid.z_values.shape[0]:,} traces; "
                    f"{waterfall.grid.z_values.size:,} display vertices; "
                    f"frequency features retained by extrema/curvature selection."
                )
            else:
                use_background_prepare = (
                    geometry in {"Surface", "Transparent Surface"}
                    and height_grid.z_values.size > 1_000_000
                    and requested_policy != "Full Resolution"
                )
                set_grid = renderer.set_grid_async if use_background_prepare else renderer.set_grid
                set_grid(
                    height_grid, color_grid=color_grid,
                    colormap=self.colormap_combo_nd.currentText() or DEFAULT_COLORMAP,
                    color_range=limits, rendering_policy=requested_policy,
                    **({} if use_background_prepare else {
                        "shared_height_limits": shared_height_limits,
                    }),
                )
                if secondary_grid is not None:
                    renderer.set_secondary_grid(
                        secondary_grid, opacity=self.surface_b_opacity_slider.value() / 100.0
                    )

            renderer.set_opacity(self.surface_opacity_slider.value() / 100.0)
            renderer.set_bottom_projection(self.surface_projection_checkbox.isChecked())
            renderer.set_reference_plane(
                self.surface_reference_combo.currentData() or "off",
                self.surface_reference_value.value(),
            )
            renderer.set_horizontal_colorbar_visible(self.surface_top_colorbar_checkbox.isChecked())
            if use_background_prepare:
                return
            self._finish_surface_render()
        except Exception as error:
            self._journal_operation(f"3D {geometry} rendering failed", error=str(error))
            self._clear_surface(self.localizer.text("viewer.surface_error", reason=str(error)))
            self.statusBar().showMessage(
                self.localizer.text("viewer.surface_error", reason=str(error)), 6000
            )

    def _rebuild_nd_color_source(self, *_args) -> None:
        self._update_geometry_controls()
        self.surface_color_transform_combo.setEnabled(
            self.surface_color_source_combo.currentData() is not None
        )
        if not self._three_d_active() or self._nd_height_grid is None:
            return
        geometry = self.geometry_combo.currentData() or "Surface"
        if geometry in {"Trajectory", "Scatter"}:
            return
        if geometry == "Waterfall":
            self._render_current_surface(self._nd_height_grid)
            return
        if self.nd_surface_renderer is not None and self.nd_surface_renderer.is_preparing:
            self._render_current_surface(self._nd_height_grid)
            return
        try:
            color_grid = self._surface_color_grid(self._nd_height_grid)
            if self.nd_surface_renderer is None:
                self._render_current_surface(self._nd_height_grid)
                return
            self.nd_surface_renderer.set_color_grid(color_grid)
            self._update_surface_color_display()
            self.surface_performance_label.setText(self.localizer.text(
                "viewer.surface_performance_details",
                source_rows=self._nd_height_grid.z_values.shape[0],
                source_columns=self._nd_height_grid.z_values.shape[1],
                render_rows=self.nd_surface_renderer.surface_grid.shape[0],
                render_columns=self.nd_surface_renderer.surface_grid.shape[1],
                policy=self.nd_surface_renderer.rendering_decision.effective.value,
                vertices=self.nd_surface_renderer.surface_grid.vertex_count,
            ))
        except Exception as error:
            self._clear_surface(self.localizer.text("viewer.surface_error", reason=str(error)))

    def _rebuild_nd_plot(self) -> None:
        if self._loading_file or self.experiment is None or self.cached is None:
            return
        z_name = self.z_combo_nd.currentText()
        x_name = self.x_combo_nd.currentText()
        y_name = self.y_combo_nd.currentData()
        if not (z_name and x_name and y_name):
            if self._three_d_active():
                self._clear_surface(self.localizer.text("viewer.surface_needs_xy"))
            return

        self._update_geometry_controls()
        transform = self.transform_combo_nd.currentData() or "raw"
        fixed = self.slice_explorer.current_fixed()

        try:
            result = self._get_3d_grid(z_name, transform)
        except SliceError as e:
            # spec §12: show a clear message, keep the existing plot untouched
            QMessageBox.warning(self, "Cannot build slice", str(e))
            return
        except ChannelNotFound as e:
            QMessageBox.warning(self, "Channel not found", str(e))
            return
        except Exception as e:
            QMessageBox.warning(self, "Slice error", f"Could not build this slice:\n\n{e}")
            return

        self._nd_height_grid = result
        colormap = self.colormap_combo_nd.currentText() or DEFAULT_COLORMAP
        plot_result = result
        if self._three_d_active():
            geometry = self.geometry_combo.currentData() or "Surface"
            if geometry in {"Surface", "Transparent Surface", "Dual Surface", "Waterfall"}:
                plot_result = self._surface_color_grid(result)
            elif geometry in {"Trajectory", "Scatter"}:
                raw_grid = self._get_3d_grid(z_name, "raw")
                color_key = self.point_color_mapping_combo.currentData() or "magnitude_db"
                if color_key in {"phase_unwrapped_deg", "phase_unwrapped_rad"}:
                    color_values = transform_grid_3d(
                        raw_grid, color_key,
                        unwrap_axis=int(self.phase_unwrap_axis_combo.currentData() or 0),
                    ).z_values
                else:
                    color_values = mapping_values(raw_grid, color_key)
                plot_result = replace(result, z_values=color_values, transform=str(color_key))
        if not (self._three_d_active() and result.z_values.size > 1_000_000):
            if self.auto_range_checkbox_nd.isChecked():
                self.plot_2d_widget_nd.plot(plot_result, colormap=colormap, z_min=None, z_max=None)
                self._sync_range_spins_nd_from_widget()
            else:
                self.plot_2d_widget_nd.plot(
                    plot_result, colormap=colormap, z_min=self.zmin_spin_nd.value(),
                    z_max=self.zmax_spin_nd.value(),
                )
        self._refresh_cut_windows()
        if self._three_d_active():
            self._clear_data_table()
            self._render_current_surface(result)

    def _on_colormap_nd_changed(self, name: str) -> None:
        if not name:
            return
        self.plot_2d_widget_nd.set_colormap(name)
        if self._three_d_active():
            self._update_surface_color_display()

    def _on_auto_range_nd_toggled(self, checked: bool) -> None:
        self.zmin_spin_nd.setEnabled(not checked)
        self.zmax_spin_nd.setEnabled(not checked)
        if self.geometry_combo.currentData() in {"Trajectory", "Scatter"}:
            limits = self._surface_color_limits()
            if limits is not None:
                with QSignalBlocker(self.zmin_spin_nd), QSignalBlocker(self.zmax_spin_nd):
                    self.zmin_spin_nd.setValue(limits[0])
                    self.zmax_spin_nd.setValue(limits[1])
                self._update_surface_color_display(limits=limits)
            return
        if checked:
            self.plot_2d_widget_nd.auto_range_color()
            limits = self._surface_color_limits()
            if limits is not None:
                with QSignalBlocker(self.zmin_spin_nd), QSignalBlocker(self.zmax_spin_nd):
                    self.zmin_spin_nd.setValue(limits[0])
                    self.zmax_spin_nd.setValue(limits[1])
            self._update_surface_color_display(limits=limits)
        else:
            self.plot_2d_widget_nd.set_color_range(self.zmin_spin_nd.value(), self.zmax_spin_nd.value())
            self._update_surface_color_display(
                limits=(self.zmin_spin_nd.value(), self.zmax_spin_nd.value())
            )

    def _on_range_spin_nd_changed(self) -> None:
        if self.auto_range_checkbox_nd.isChecked():
            return
        self.plot_2d_widget_nd.set_color_range(self.zmin_spin_nd.value(), self.zmax_spin_nd.value())
        self._update_surface_color_display(
            limits=(self.zmin_spin_nd.value(), self.zmax_spin_nd.value())
        )

    def _on_auto_range_nd_button_clicked(self) -> None:
        self.auto_range_checkbox_nd.blockSignals(True)
        self.auto_range_checkbox_nd.setChecked(True)
        self.auto_range_checkbox_nd.blockSignals(False)
        self.zmin_spin_nd.setEnabled(False)
        self.zmax_spin_nd.setEnabled(False)
        if self.geometry_combo.currentData() in {"Trajectory", "Scatter"}:
            limits = self._surface_color_limits()
            if limits is not None:
                with QSignalBlocker(self.zmin_spin_nd), QSignalBlocker(self.zmax_spin_nd):
                    self.zmin_spin_nd.setValue(limits[0])
                    self.zmax_spin_nd.setValue(limits[1])
                self._update_surface_color_display(limits=limits)
            return
        self.plot_2d_widget_nd.auto_range_color()
        limits = self._surface_color_limits()
        if limits is not None:
            with QSignalBlocker(self.zmin_spin_nd), QSignalBlocker(self.zmax_spin_nd):
                self.zmin_spin_nd.setValue(limits[0])
                self.zmax_spin_nd.setValue(limits[1])
        self._update_surface_color_display(limits=limits)

    def _apply_surface_rendering_policy(self, *_args) -> None:
        if self.nd_surface_renderer is None:
            return
        if self.geometry_combo.currentData() in {
            "Transparent Surface", "Dual Surface", "Waterfall", "Trajectory", "Scatter"
        }:
            if self._nd_height_grid is not None:
                self._render_current_surface(self._nd_height_grid)
            return
        if self.nd_surface_renderer.is_preparing and self._nd_height_grid is not None:
            self._render_current_surface(self._nd_height_grid)
            return
        requested = self.surface_rendering_combo.currentData() or "Auto"
        previous = self.nd_surface_renderer.rendering_decision
        previous_policy = previous.requested.value if previous is not None else "Auto"
        if not self._confirm_large_surface_request(self._nd_height_grid, requested):
            with QSignalBlocker(self.surface_rendering_combo):
                index = self.surface_rendering_combo.findData(previous_policy)
                self.surface_rendering_combo.setCurrentIndex(index if index >= 0 else 0)
            return
        try:
            grid = self.nd_surface_renderer.set_rendering_policy(
                requested
            )
            if grid is not None:
                decision = self.nd_surface_renderer.rendering_decision
                if decision.warning:
                    self.statusBar().showMessage(self.localizer.text("viewer.surface_large_warning"), 7000)
                self.surface_performance_label.setText(self.localizer.text(
                    "viewer.surface_performance_details",
                    source_rows=grid.source_shape[0], source_columns=grid.source_shape[1],
                    render_rows=grid.shape[0], render_columns=grid.shape[1],
                    policy=decision.effective.value, vertices=grid.vertex_count,
                ))
        except Exception as error:
            self.statusBar().showMessage(self.localizer.text("viewer.surface_error", reason=str(error)), 6000)

    def _confirm_large_surface_request(self, grid, requested: str) -> bool:
        if grid is None or requested != "Full Resolution":
            return True
        shape = (int(grid.z_values.shape[0]), int(grid.z_values.shape[1]))
        if shape in self._confirmed_full_resolution_shapes:
            return True
        from app.visualization3d.policy import resolve_rendering_policy
        decision = resolve_rendering_policy(*shape, requested)
        if not decision.warning:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Full Resolution Safety")
        box.setText("Full Resolution may require significant memory and GPU resources.")
        box.setInformativeText(
            f"The requested surface contains {decision.source_points:,} points. "
            "Continue with the full mesh?"
        )
        box.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None or box.buttonRole(clicked) != QMessageBox.ButtonRole.AcceptRole:
            return False
        self._confirmed_full_resolution_shapes.add(shape)
        return True

    def _on_surface_z_scale_changed(self, value: int) -> None:
        scale = value / 10.0
        self.surface_z_scale_label.setText(f"{scale:.1f}×")
        if not self.surface_z_auto_checkbox.isChecked():
            self._surface_z_scale_timer.start()

    def _on_surface_z_auto_toggled(self, automatic: bool) -> None:
        self.surface_z_scale_slider.setEnabled(not automatic)
        self.surface_z_scale_label.setText(
            self.localizer.text("viewer.auto") if automatic
            else f"{self.surface_z_scale_slider.value() / 10.0:.1f}×"
        )
        if self.nd_surface_renderer is not None:
            self.nd_surface_renderer.set_z_scale(1.0 if automatic else self.surface_z_scale_slider.value() / 10.0)

    def _apply_surface_z_scale(self) -> None:
        if self.nd_surface_renderer is not None and not self.surface_z_auto_checkbox.isChecked():
            self.nd_surface_renderer.set_z_scale(self.surface_z_scale_slider.value() / 10.0)

    def _reset_surface_z_scale(self) -> None:
        self.surface_z_scale_slider.setValue(10)
        self.surface_z_auto_checkbox.setChecked(True)

    def _apply_surface_projection(self, *_args) -> None:
        if self.nd_surface_renderer is not None:
            self.nd_surface_renderer.set_projection(
                self.surface_projection_combo.currentData() or "perspective"
            )

    def _set_surface_camera_preset(self, preset: str) -> None:
        if self.nd_surface_renderer is not None:
            self.nd_surface_renderer.set_camera_preset(preset)

    def _reset_surface_view(self) -> None:
        if self.nd_surface_renderer is not None:
            self.nd_surface_renderer.reset_view()

    def _view_all_surface(self) -> None:
        if self.nd_surface_renderer is not None:
            self.nd_surface_renderer.view_all()

    def _sync_range_spins_nd_from_widget(self) -> None:
        if self.plot_2d_widget_nd._z_min is None:
            return
        self.zmin_spin_nd.blockSignals(True)
        self.zmax_spin_nd.blockSignals(True)
        self.zmin_spin_nd.setValue(self.plot_2d_widget_nd._z_min)
        self.zmax_spin_nd.setValue(self.plot_2d_widget_nd._z_max)
        self.zmin_spin_nd.blockSignals(False)
        self.zmax_spin_nd.blockSignals(False)

    # ---- summary panel (Phase 9: now backed by ChannelManager.get_metadata_summary) --

    def _populate_summary(self) -> None:
        """Metadata tab content. Uses ONLY ChannelManager.get_metadata_summary()
        — a structured API over already-parsed data — never touches
        Experiment/HDF5 internals directly from the GUI (spec's
        'GUI 不可以直接 import h5py' / 'all info via core API')."""
        mgr = self.mgr
        log_channels = mgr.list_log_channels()
        primary_channel = log_channels[0].name if log_channels else None
        summary = mgr.get_metadata_summary(primary_channel)

        lines = [
            f"File name       : {summary['file_name']}",
            f"Experiment name : {summary['experiment_name']}",
            f"Format variant  : {summary['format_variant']}",
            f"Project         : {summary['project']}",
            f"User            : {summary['user']}",
            f"Tags            : {summary['tags']}",
            f"Original Labber Comment (read-only): {summary['comment']!r}",
            f"Labber version  : {summary['version']}",
            "",
            f"Step channels   : {summary['n_step_channels']}",
            f"Log channels    : {summary['n_log_channels']}",
        ]

        if summary["channel"]:
            ch = summary["channel"]
            lines += [
                "",
                f"Primary channel : {ch['name']}",
                f"  unit          : {ch['unit'] or '-'}",
                f"  instrument    : {ch['instrument'] or '-'}",
                f"  vector/complex: {ch['is_vector']} / {ch['is_complex']}",
                f"  data shape    : {ch['shape']}",
                "",
                "Dimensions:",
            ]
            for d in summary["dimensions"]:
                uniform = "uniform" if d["is_uniform"] else "non-uniform"
                step_str = f", step={d['step']:.6g}" if d["step"] is not None else ""
                lines.append(
                    f"  - {d['name']} [{d['unit'] or '-'}]: size={d['size']}, "
                    f"min={d['min']:.6g}, max={d['max']:.6g} ({uniform}{step_str})"
                )

        lines.append("")
        lines.append("Step Channels")
        lines.append("-------------")
        active_names = set(mgr.list_active_sweep_axes())
        for channel in mgr.list_step_channels():
            kind = "active sweep" if channel.name in active_names else "fixed"
            lines.append(
                f"  {channel.name} | {channel.unit or '-'} | "
                f"{channel.instrument or '-'} | {kind}"
            )
        lines.append("")
        lines.append("Log Channels")
        lines.append("------------")
        for channel in mgr.list_log_channels():
            kind = ChannelBrowser._log_kind_label(channel)
            lines.append(
                f"  {channel.name} | {channel.unit or '-'} | "
                f"{channel.instrument or '-'} | {kind}"
            )
        lines.append("")
        lines.append(f"Source file: {summary['file_path']}")

        database_id, relative_path = self._comment_context or (None, None)
        self.metadata_dialog.set_metadata(
            "\n".join(lines), self.experiment.source_path if self.experiment else None,
            database_id=database_id, relative_path=relative_path,
        )

    # ---- keyboard trace navigation (v0.9A) -------------------------------------

    def eventFilter(self, obj, event) -> bool:
        """Application-wide Up/Down key interception for sequential
        Trace navigation on the 1D Plot page. Only acts when THIS
        window is the active one (so independent Viewer windows never
        steal each other's key events) and the 1D Plot mode is
        selected (trace navigation is specific to that page).

        Wrapped defensively: during interpreter/widget teardown (e.g.
        a test that constructs a MainWindow without calling close()),
        the underlying C++ objects this filter references can already
        be destroyed while the QApplication-level filter is still
        registered, which previously caused PySide6's error-recovery
        path to redeliver the event and re-enter this method in a
        long (though finite) cascade. Any exception here is treated as
        "not our event" rather than being allowed to propagate back
        into Qt's event dispatch."""
        try:
            if (
                event.type() == QEvent.KeyPress
                and self.isActiveWindow()
                and self.mode_combo.currentIndex() == 0
                and self.log_entries.row_count() > 1
            ):
                key = event.key()
                modifiers = event.modifiers()
                if modifiers & (Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier):
                    return False
                if key == Qt.Key_Up:
                    self._navigate_trace(-1, extend=bool(modifiers & Qt.ShiftModifier))
                    return True
                if key == Qt.Key_Down:
                    self._navigate_trace(1, extend=bool(modifiers & Qt.ShiftModifier))
                    return True
        except Exception:
            return False
        return False

    # ---- lifecycle --------------------------------------------------------

    def closeEvent(self, event) -> None:
        if getattr(self, "_network_mirror", False) and not getattr(self, "_network_closing", False):
            # Closing a Client mirror means leaving the session. Accept the close
            # (so quitting the app is never blocked) and disconnect right after.
            from PySide6.QtCore import QTimer
            from app.network.workspace import workspace

            self._network_closing = True
            QTimer.singleShot(0, workspace().disconnect)
        if self._three_d_window is not None:
            self._three_d_window.close()
        if getattr(self, "_three_d_process", None) is not None:
            self._three_d_process.shutdown()
        for job in tuple(self._animation_jobs):
            timer = job.get("timer")
            if timer is not None:
                timer.stop()
            worker = job["worker"]
            worker.cancel()
            worker.finish()
            job["session"].close()
            job["progress"].close()
        self._animation_jobs.clear()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self._node_antinode_window is not None:
            self._node_antinode_window.close()
            self._node_antinode_window = None
        for window in tuple(self._yig_fitting_windows.values()):
            window.close()
        self._yig_fitting_windows.clear()
        self.metadata_dialog.flush_comment()
        for window in self._cut_windows.values():
            window.close()
        self._flush_display_state()
        self._persist_all_marks()
        if not self._restoring_session:
            self.workspace_state_changed.emit()
        if self.experiment is not None:
            self.experiment.close()
        super().closeEvent(event)
