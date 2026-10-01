"""Reference-derived fitting workspace bound to one source Viewer Data."""

from __future__ import annotations

from PySide6.QtCore import Qt

from app.analysis.yig_fitting.controller import FitController
from app.analysis.yig_fitting.core.paths import session_path
from app.analysis.yig_fitting.core.settings import SessionStore
from app.analysis.yig_fitting.ui.main_window import MainWindow as FittingMainWindow


class YigMirrorFittingWindow(FittingMainWindow):
    def __init__(self, experiment, source_viewer):
        super().__init__(source_viewer)
        self.setWindowFlag(Qt.Window, True)
        self.source_identity = experiment.data_identity
        self.source_path = str(experiment.source_path)
        self.source_log_name = str(experiment.display_name)
        self.source_experiment = experiment
        self.setWindowTitle(f"YIG Mirror Analysis - {self.source_log_name}")
        self._install_annotation(source_viewer)

        store = SessionStore(session_path(self.source_identity))
        self.fit_controller = FitController(self, store=store, source_path=self.source_path)
        self.operation_journal = getattr(source_viewer, "operation_journal", None)
        for button, label in (
            (self.btn_fit, "Single Trace Fit started"),
            (self.btn_batch, "Continuous Fit started"),
            (self.phase_panel.btn_link_batch, "Phase-linked Continuous Fit started"),
            (self.phase_panel.btn_global, "Global Linked Fit started"),
            (self.phase_panel.btn_coarse_run, "Coarse Node/Antinode processing started"),
            (self.phase_panel.btn_estimate, "Phase and physical-position estimation started"),
            (self.phase_panel.btn_detect, "Dip candidate extraction started"),
            (self.phase_panel.btn_from_nodes, "Phase estimation from candidates started"),
        ):
            button.clicked.connect(
                lambda _checked=False, operation=label: self._journal_operation(operation)
            )
        self.data_worker = self.fit_controller.load_experiment(experiment)

    def _journal_operation(self, label: str) -> None:
        if callable(self.operation_journal):
            self.operation_journal(label, data=self.source_log_name)

    def _install_annotation(self, source_viewer) -> None:
        """Pen / laser overlay on the analysis plots (display only, never saved)."""
        from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget
        from app.gui.annotation import AnnotationSession, lock_widgets, make_annotation_button

        # A larger round button in the tab row's top-right corner. The tab row
        # grows to hold it, so it never covers a page's own first row (the
        # Continuous Fit page uses its full width for buttons).
        self.annotation_button = make_annotation_button(24)
        self.annotation_button.setObjectName("annotationToggleLarge")
        self.annotation_button.setFixedSize(36, 36)
        # Qt caps a corner widget at the tab-row height, so the row is made taller.
        self.tabs.tabBar().setObjectName("yigAnalysisTabs")
        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 1, 6, 1)
        corner_layout.addWidget(self.annotation_button)
        self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        layout_buttons = [
            button for button in self.findChildren(QPushButton)
            if button.text().replace("&", "") in {"Grid View", "Focus Pane", "Reset Layout"}
        ]
        self.annotation = AnnotationSession(
            self, self.annotation_button, regions=lambda: [([self.tabs], False)],
            lock=lock_widgets([self.tabs.tabBar(), *layout_buttons], [self.tabs]),
            localizer=getattr(source_viewer, "localizer", None),
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        annotation = getattr(self, "annotation", None)
        if annotation is not None:
            annotation.set_active(False)
        super().closeEvent(event)
