"""Privacy-conscious runtime diagnostics and the Settings Debug page."""

from __future__ import annotations

import importlib.util
import platform
import sys

import PySide6
from PySide6.QtCore import qVersion
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from app import __version__
from app.localization import LocalizationManager
from app.visualization3d.diagnostics import renderer_diagnostics
from app.visualization3d.availability import probe_opengl_context


def collect_debug_values(localizer: LocalizationManager | None = None) -> dict[str, str]:
    """Return only stable, non-secret runtime facts (never paths or env vars)."""
    if localizer is None:
        from app.localization import get_localization_manager
        localizer = get_localization_manager()

    def state(key: str) -> str:
        return localizer.text(f"debug.state.{key}")

    scene = renderer_diagnostics()
    values = {
        "debug.value.version": f"LabLogViewer v{__version__}",
        "debug.value.developer": "戦わずに恋をする",
        "debug.value.co_developer": "張譯文",
        "debug.value.python": platform.python_version(),
        "debug.value.pyside": str(PySide6.__version__),
        "debug.value.qt": qVersion(),
        "debug.value.os": platform.system() or "Unknown",
        "debug.value.platform": platform.machine() or "Unknown",
        "debug.value.renderer": "Qt Data Visualization / OpenGL" if scene else state("not_active"),
        "debug.value.gpu": state("not_queried"),
        "debug.value.3d": state("scene_active") if scene else state("unavailable"),
        "debug.value.mesh": state("not_active"),
        "debug.value.fps": state("not_active"),
        "debug.value.frame_time": state("not_active"),
    }
    if not scene and importlib.util.find_spec("PySide6.QtDataVisualization"):
        try:
            values["debug.value.3d"] = (
                state("context_available") if probe_opengl_context()
                else state("context_unavailable")
            )
        except Exception:
            values["debug.value.3d"] = state("undetermined")
    if scene:
        dimensions = scene.get("mesh_dimensions")
        source_dimensions = scene.get("source_dimensions")
        values["debug.value.mesh"] = (
            f"{dimensions[0]} × {dimensions[1]} ({scene.get('vertex_count', 0):,} vertices)"
            if dimensions else state("no_mesh")
        )
        if source_dimensions and source_dimensions != dimensions:
            values["debug.value.mesh"] += (
                f"; source {source_dimensions[0]} × {source_dimensions[1]}"
            )
        fps = scene.get("fps")
        frame_time = scene.get("frame_time_ms")
        values["debug.value.fps"] = f"{fps:.1f}" if isinstance(fps, (int, float)) else state("not_yet_available")
        values["debug.value.frame_time"] = (
            f"{frame_time:.2f} ms" if isinstance(frame_time, (int, float)) else state("not_yet_available")
        )
    return values


def format_debug_information(localizer: LocalizationManager) -> str:
    labels = (
        "debug.application_version", "debug.developer", "debug.co_developer",
        "debug.python_version", "debug.pyside_version",
        "debug.qt_version", "debug.operating_system", "debug.platform_architecture",
        "debug.renderer_backend", "debug.graphics_device", "debug.three_d_status",
        "debug.mesh_dimensions", "debug.approximate_fps", "debug.approximate_frame_time",
    )
    values = collect_debug_values(localizer)
    return "\n".join(
        f"{localizer.text(label)}: {values[value_key]}"
        for label, value_key in zip(labels, values, strict=True)
    )


class DebugPage(QWidget):
    def __init__(self, localizer: LocalizationManager, parent=None):
        super().__init__(parent)
        self.localizer = localizer
        self.output = QPlainTextEdit(self)
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.copy_button = QPushButton()
        self.refresh_button = QPushButton()
        self.copy_button.clicked.connect(self.copy_information)
        self.refresh_button.clicked.connect(self.refresh)
        buttons = QHBoxLayout()
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self.output, 1)
        self.refresh()
        self.retranslate()
        self.localizer.language_changed.connect(self._on_language_changed)

    def _on_language_changed(self, _language: str) -> None:
        self.retranslate()

    def retranslate(self) -> None:
        self.copy_button.setText(self.localizer.text("debug.copy_information"))
        self.refresh_button.setText(self.localizer.text("debug.refresh"))
        self.refresh()

    def refresh(self) -> None:
        self.output.setPlainText(format_debug_information(self.localizer))

    def copy_information(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.output.toPlainText())
