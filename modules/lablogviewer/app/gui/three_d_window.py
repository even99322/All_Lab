"""Independent 3D Surface analysis window opened from the Viewer's Analysis menu.

The window follows its Viewer's current Data (like YIG Mirror Analysis). It
hosts the Viewer's existing 3D control panel and 3D plot page, so every 3D
setting, the renderer and the per-Data view state keep one implementation.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QLabel, QMainWindow, QScrollArea, QSplitter, QToolButton, QWidget

from app.gui.glass import GlassToolBar
from app.gui.plot_interaction import PlotInteractionControls
from app.icons import make_icon_only
from app.theme import TOOLBAR_ICON_SIZE


class ThreeDAnalysisWindow(QMainWindow):
    closed = Signal()

    CONTROLS_WIDTH = 300

    def __init__(self, viewer, controls: QWidget, plot_page: QWidget):
        super().__init__(None)
        self.viewer = viewer
        self.setObjectName("threeDAnalysisWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(1320, 860)

        toolbar = GlassToolBar("3D", self)
        toolbar.setObjectName("threeDToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(TOOLBAR_ICON_SIZE, TOOLBAR_ICON_SIZE))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        self.open_button = make_icon_only(QToolButton(), "open", "Open HDF5...", TOOLBAR_ICON_SIZE, flat=True)
        self.open_button.clicked.connect(self.open_hdf5)
        toolbar.addWidget(self.open_button)
        toolbar.addSeparator()
        self.copy_button = make_icon_only(QToolButton(), "copy", "Copy 3D Plot", TOOLBAR_ICON_SIZE, flat=True)
        self.copy_button.clicked.connect(viewer.copy_3d_plot)
        self.save_button = make_icon_only(QToolButton(), "save", "Save 3D Plot...", TOOLBAR_ICON_SIZE, flat=True)
        self.save_button.clicked.connect(viewer.save_3d_plot_dialog)
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.save_button)
        toolbar.addSeparator()
        # Pointer / Drag to Share, as in the Viewer (one plot: no pane scope).
        self.interaction_controls = PlotInteractionControls(toolbar)
        self.interaction_controls.scope_label.hide()
        self.interaction_controls.scope_combo.hide()
        self.interaction_controls.set_share_mode(viewer._three_d_share_mode)
        self.interaction_controls.mode_changed.connect(self.set_share_mode)
        toolbar.addWidget(self.interaction_controls)
        toolbar.addSeparator()
        self.export_style_label = QLabel()
        self.export_style_label.setObjectName("threeDExportStyle")
        toolbar.addWidget(self.export_style_label)
        from app.gui.annotation import AnnotationSession, lock_widgets, make_annotation_button, toolbar_stretch
        toolbar.addWidget(toolbar_stretch())
        self.annotation_button = make_annotation_button(TOOLBAR_ICON_SIZE)
        toolbar.addWidget(self.annotation_button)
        self.refresh_export_style()

        self.controls_scroll = QScrollArea()
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.controls_scroll.setWidget(controls)
        self.controls_scroll.setMinimumWidth(240)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.controls_scroll)
        self.splitter.addWidget(plot_page)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([self.CONTROLS_WIDTH, 1000])
        self.setCentralWidget(self.splitter)
        # Point geometries use the Data Visualization renderer, whose native
        # OpenGL window sits above Qt widgets: annotate it with a floating sheet.
        self.annotation = AnnotationSession(
            self, self.annotation_button,
            regions=lambda: [([plot_page], type(getattr(viewer, "nd_surface_renderer", None)).__name__
                              == "SurfaceRenderer")],
            lock=lock_widgets([viewer.geometry_combo]),
            localizer=viewer.localizer,
        )
        from app.settings.dialog import install_settings_menu
        self.settings_menu = install_settings_menu(self, opener=getattr(viewer, "settings_opener", None),
                                                   network_opener=getattr(viewer, "network_opener", None))
        from app.gui.network_panel import install_network_bar

        install_network_bar(self, opener=getattr(viewer, "network_opener", None))
        QShortcut(QKeySequence.StandardKey.Open, self, self.open_hdf5)
        QShortcut(QKeySequence.StandardKey.Copy, self, viewer.copy_3d_plot)
        QShortcut(QKeySequence.StandardKey.Save, self, viewer.save_3d_plot_dialog)
        self.update_title()

    def set_share_mode(self, enabled: bool) -> None:
        self.interaction_controls.set_share_mode(enabled)
        self.viewer.set_3d_share_mode(enabled)

    def open_hdf5(self) -> None:
        """Open another single HDF5 file; this window then shows its 3D surface."""
        before = getattr(self.viewer, "experiment", None)
        if self.viewer.open_file_dialog(parent=self) is None:
            return
        experiment = getattr(self.viewer, "experiment", None)
        if experiment is None or experiment is before:
            return                                   # load failed; the Viewer already reported why
        self.viewer.open_3d_window()

    def update_title(self) -> None:
        experiment = getattr(self.viewer, "experiment", None)
        name = getattr(experiment, "display_name", None) or getattr(experiment, "log_name", None) or ""
        self.setWindowTitle(f"3D Surface — {name}" if name else "3D Surface")

    def refresh_export_style(self) -> None:
        style = self.viewer.three_d_export_style()
        key = "viewer.export_style_publication" if style == "publication" else "viewer.export_style_screen"
        self.export_style_label.setText(self.viewer.localizer.text(key))

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        # The export style is a Browser setting; reflect changes on activation.
        if event.type() == event.Type.ActivationChange and self.isActiveWindow():
            self.refresh_export_style()
        super().changeEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        self.annotation.set_active(False)
        self.closed.emit()
        super().closeEvent(event)
