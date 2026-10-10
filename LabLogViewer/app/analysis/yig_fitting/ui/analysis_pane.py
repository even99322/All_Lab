"""Independent, shareable scientific plot panes for YIG analysis."""

from __future__ import annotations

from contextlib import contextmanager
import re
from io import BytesIO
from math import isfinite
from pathlib import Path

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QRect, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from matplotlib.ticker import MaxNLocator

from app.palette import ANALYSIS
from app.gui.drag_share import DragShareController, DragShareManager
from app.gui.plot_export import (
    PaneRenderSurface,
    render_composite_image,
    render_widget_image,
    write_png,
    write_svg,
)
from app.gui.plot_interaction import PlotInteractionControls, copy_save_buttons
from app.icons import make_icon_only
from app.theme import ANALYSIS_ICON_SIZE, FOREGROUND_LINE_GID, WHITE_PLOT, get_theme_manager


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "YIG_analysis"


class AnalysisPlotPane(QWidget):
    """One independent Matplotlib scientific pane using LabLogViewer exports."""

    activated = Signal(int)
    doubleClicked = Signal(int)

    def __init__(self, pane_id: int, title: str, parent=None):
        super().__init__(parent)
        self.pane_id = int(pane_id)
        self._active = False
        self._host: FitPlotWidget | None = None
        self.figure = Figure(figsize=(5, 3), constrained_layout=True)
        self.axis = self.figure.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self._plot_colors = WHITE_PLOT
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setIconSize(QSize(ANALYSIS_ICON_SIZE, ANALYSIS_ICON_SIZE))
        self.title_label = QLabel(title)
        self.title_label.setObjectName("analysisPaneTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._compact = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)
        layout.addWidget(self.title_label)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        self.setMinimumSize(220, 150)
        self.set_active(False)
        self.canvas.mpl_connect("button_press_event", self._on_mouse)
        self.canvas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.canvas.customContextMenuRequested.connect(self._context_menu)
        self._copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.canvas)
        self._copy_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._copy_shortcut.activated.connect(self.copy_active)
        self._save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self.canvas)
        self._save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._save_shortcut.activated.connect(self.save_active_dialog)
        self._drag_controller: DragShareController | None = None
        self.apply_scientific_plot_appearance(WHITE_PLOT)

    def bind_workspace(self, host: "FitPlotWidget") -> None:
        self._host = host
        if self._drag_controller is None:
            self._drag_controller = DragShareController(
                self.canvas,
                self.pane_id,
                host.drag_manager,
                host.render_drag_payload,
                lambda: host.share_mode and self.isVisible(),
                host.is_drag_all,
                self,
            )

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        active_border = getattr(self, "_theme_accent", ANALYSIS["pane_active_border"])
        inactive_border = getattr(self, "_theme_border", ANALYSIS["pane_inactive_border"])
        self.setStyleSheet(
            "QWidget#analysisPaneTitle { font-weight: 600; }"
            f"AnalysisPlotPane {{ border: 2px solid {active_border}; }}"
            if active else
            "QWidget#analysisPaneTitle { font-weight: 600; }"
            f"AnalysisPlotPane {{ border: 1px solid {inactive_border}; }}"
        )

    def apply_lablog_theme(self, colors) -> None:
        self._theme_accent = colors.accent
        self._theme_border = colors.border
        self.set_active(self._active)

    def apply_scientific_plot_appearance(self, colors) -> None:
        self._plot_colors = colors
        self.figure.set_facecolor(colors.background)
        for axis in self.figure.axes:
            axis.set_facecolor(colors.panel)
            axis.tick_params(axis="both", colors=colors.text)
            axis.xaxis.label.set_color(colors.text)
            axis.yaxis.label.set_color(colors.text)
            axis.title.set_color(colors.text)
            for spine in axis.spines.values():
                spine.set_color(colors.border)
            for label in (*axis.get_xticklabels(), *axis.get_yticklabels()):
                label.set_color(colors.text)
            axis.grid(True, color=colors.border, alpha=0.45)
            for line in axis.get_lines():
                if line.get_gid() == FOREGROUND_LINE_GID:
                    line.set_color(colors.text)   # e.g. Residual on dark plots
            legend = axis.get_legend()
            if legend is not None:
                legend.get_frame().set_facecolor(colors.panel)
                legend.get_frame().set_edgecolor(colors.border)
                for label in legend.get_texts():
                    label.set_color(colors.text)
        self.canvas.draw_idle()

    @contextmanager
    def temporary_scientific_plot_appearance(self, colors):
        previous = self._plot_colors
        self.apply_scientific_plot_appearance(colors)
        try:
            yield
        finally:
            self.apply_scientific_plot_appearance(previous)

    def set_compact(self, compact: bool) -> None:
        self._compact = bool(compact)
        self.title_label.setVisible(not self._compact)
        self.toolbar.setVisible(not self._compact)
        self.setMinimumSize(54, 78) if self._compact else self.setMinimumSize(220, 150)
        for axis in self.figure.axes:
            axis.tick_params(labelsize=5 if self._compact else 8)
            axis.xaxis.label.set_size(6 if self._compact else 9)
            axis.yaxis.label.set_size(6 if self._compact else 9)
            axis.title.set_fontsize(7 if self._compact else 10)
        self.canvas.draw_idle()

    def view_all(self) -> None:
        self.axis.relim(visible_only=True)
        self.axis.autoscale(enable=True, axis="both", tight=False)
        self.canvas.draw_idle()

    def clear_plot(self, message: str | None = None) -> None:
        self.figure.clear()
        self.axis = self.figure.add_subplot(111)
        if message:
            self.axis.text(0.5, 0.5, message, ha="center", va="center",
                           transform=self.axis.transAxes, wrap=True)
            self.axis.set_axis_off()
        self.apply_scientific_plot_appearance(self._plot_colors)
        self.canvas.draw_idle()

    def _on_mouse(self, event) -> None:
        if event.button == 1:
            self.activated.emit(self.pane_id)
            if event.dblclick and self._host is not None:
                self.doubleClicked.emit(self.pane_id)

    def _context_menu(self, point) -> None:
        if self._host is None:
            return
        self.activated.emit(self.pane_id)
        menu = QMenu(self)
        menu.addAction("View All", self.view_all)
        menu.addSeparator()
        menu.addAction("Copy This Pane", self.copy_active)
        menu.addAction("Copy All Panes", self._host.copy_all)
        menu.addAction("Save This Pane...", self.save_active_dialog)
        menu.addAction("Save All Panes...", self._host.save_all_dialog)
        menu.exec(self.canvas.mapToGlobal(point))

    def copy_active(self) -> None:
        if self._host is not None:
            self._host.copy_pane(self.pane_id)
        else:
            QGuiApplication.clipboard().setImage(self.render_image(2))

    def render_image(self, scale: int):
        from PySide6.QtGui import QImage

        buffer = BytesIO()
        self.figure.savefig(buffer, format="png", dpi=self.figure.get_dpi() * scale)
        image = QImage.fromData(buffer.getvalue(), "PNG")
        return image if not image.isNull() else render_widget_image(self.canvas)

    def save_active_dialog(self) -> None:
        path, selected = QFileDialog.getSaveFileName(
            self, "Save Analysis Plot", f"{_safe_name(self.title_label.text())}.png",
            "PNG image (*.png);;SVG image (*.svg)",
        )
        if path and self._host:
            self._host.save_active_to(path, selected)


class FitPlotWidget(QWidget):
    """Six independent plot panes with grid/focus and current-layout export."""

    TITLES = ("Magnitude", "Phase", "IQ Plane", "Real", "Imaginary", "Residual")

    def __init__(self, parent=None, min_height=None):
        super().__init__(parent)
        self.panes = [AnalysisPlotPane(i, title, self) for i, title in enumerate(self.TITLES)]
        self.active_pane = 0
        self.mode = "grid"
        self._last_focus_ratio = 0.76
        self.share_mode = False
        self._last_plot_args = None
        self._pending_axis_views = None
        self.drag_manager = DragShareManager()
        self._build()
        for pane in self.panes:
            pane.bind_workspace(self)
            pane.activated.connect(self.set_active_pane)
            pane.doubleClicked.connect(self.toggle_focus)
        self.set_active_pane(0)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.btn_grid = QPushButton("Grid View")
        self.btn_focus = QPushButton("Focus Pane")
        self.btn_view_all = make_icon_only(QToolButton(self), "view_all", "View All",
                                           ANALYSIS_ICON_SIZE, flat=True)
        self.btn_copy, self.btn_save = copy_save_buttons(
            self, copy_active=lambda: self.copy_pane(self.active_pane), copy_all=self.copy_all,
            save_active=self.save_active_dialog, save_all=self.save_all_dialog,
        )
        self.btn_reset_layout = QPushButton("Reset Layout")
        self.interaction_controls = PlotInteractionControls(self, ANALYSIS_ICON_SIZE)
        self.btn_pointer = self.interaction_controls.btn_pointer
        self.btn_share = self.interaction_controls.btn_share
        self.cmb_drag_scope = self.interaction_controls.scope_combo
        self.interaction_controls.mode_changed.connect(self.set_share_mode)
        for widget in (self.btn_grid, self.btn_focus, self.btn_reset_layout,
                       self.btn_view_all, self.btn_copy, self.btn_save):
            bar.addWidget(widget)
        bar.addSpacing(8)
        bar.addWidget(self.interaction_controls)
        bar.addStretch(1)
        root.addLayout(bar)

        self.plot_area = QWidget(self)
        self.plot_area_layout = QVBoxLayout(self.plot_area)
        self.plot_area_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_container = QWidget(self.plot_area)
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(2, 2, 2, 2)
        self.grid_layout.setSpacing(4)
        self.grid_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.grid_container.setMinimumSize(0, 0)
        for index, pane in enumerate(self.panes):
            self.grid_layout.addWidget(pane, index // 3, index % 3)
        self.plot_area_layout.addWidget(self.grid_container)
        self.focus_splitter = QSplitter(Qt.Orientation.Vertical, self.plot_area)
        self.focus_main = QWidget()
        self.focus_main_layout = QVBoxLayout(self.focus_main)
        self.focus_main_layout.setContentsMargins(0, 0, 0, 0)
        self.focus_strip = QWidget()
        self.focus_strip_layout = QHBoxLayout(self.focus_strip)
        self.focus_strip_layout.setContentsMargins(0, 0, 0, 0)
        self.focus_strip_layout.setSpacing(2)
        self.focus_strip.setMinimumWidth(5 * 54 + 8)
        self.focus_strip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.focus_splitter.addWidget(self.focus_main)
        self.focus_splitter.addWidget(self.focus_strip)
        self.focus_splitter.setSizes([700, 220])
        self.focus_splitter.setChildrenCollapsible(False)
        self.focus_splitter.hide()
        root.addWidget(self.plot_area, 1)

        self.btn_grid.clicked.connect(lambda: self.set_mode("grid"))
        self.btn_focus.clicked.connect(lambda: self.set_mode("focus"))
        self.btn_view_all.clicked.connect(self.view_all)
        self.btn_reset_layout.clicked.connect(self.reset_layout)
        self._set_grid_stretches()
        self.setMinimumWidth(5 * 54 + 20)

    @property
    def fig(self):
        """Compatibility facade for existing callers that clear the result plot."""
        return self

    @property
    def canvas(self):
        return self.panes[self.active_pane].canvas

    def clear(self):
        for pane in self.panes:
            pane.clear_plot()

    def clear_with_message(self, message: str):
        for pane in self.panes:
            pane.clear_plot(message)

    def text(self, *_args, **_kwargs):
        # Legacy Figure.text call sites now use clear_with_message explicitly.
        return None

    def savefig(self, path, dpi=None):
        del dpi
        return self.save_all_to(str(path), "PNG image (*.png)")

    def set_mode(self, mode: str):
        mode = "focus" if mode == "focus" else "grid"
        if mode == self.mode:
            return
        if self.mode == "focus":
            self._last_focus_ratio = self._focus_ratio()
        self.mode = mode
        if mode == "grid":
            self._detach_focus_widgets()
            self.plot_area_layout.removeWidget(self.focus_splitter)
            self.focus_splitter.hide()
            self._attach_grid_widgets()
            self._set_grid_stretches()
            self.plot_area_layout.addWidget(self.grid_container)
            self.grid_container.show()
        else:
            self._detach_grid_widgets()
            self.plot_area_layout.removeWidget(self.grid_container)
            self.grid_container.hide()
            self._attach_focus_widgets()
            self.plot_area_layout.addWidget(self.focus_splitter)
            self.focus_splitter.show()
            self._schedule_focus_sizes(self._last_focus_ratio)
        self.btn_grid.setEnabled(mode != "grid")
        self.btn_focus.setEnabled(mode != "focus")
        for pane in self.panes:
            pane.canvas.draw_idle()

    def _set_grid_stretches(self):
        for column in range(3):
            self.grid_layout.setColumnStretch(column, 1)
        for row in range(2):
            self.grid_layout.setRowStretch(row, 1)

    def reset_layout(self):
        """Restore the canonical 3x2 pane geometry without changing plot ranges."""
        self._last_focus_ratio = 0.76
        self.set_active_pane(0)
        self.set_mode("grid")
        self._detach_grid_widgets()
        self._attach_grid_widgets()
        self._set_grid_stretches()
        self.focus_splitter.setSizes([760, 240])
        self.grid_container.updateGeometry()
        for pane in self.panes:
            pane.set_compact(False)
            pane.canvas.draw_idle()

    def _ensure_grid_container(self):
        return

    def _attach_grid_widgets(self):
        self._ensure_grid_container()
        for index, pane in enumerate(self.panes):
            pane.set_compact(False)
            self.grid_layout.addWidget(pane, index // 3, index % 3)

    def _detach_grid_widgets(self):
        for pane in self.panes:
            self.grid_layout.removeWidget(pane)

    def _detach_focus_widgets(self):
        for pane in self.panes:
            self.focus_main_layout.removeWidget(pane)
            self.focus_strip_layout.removeWidget(pane)

    def _attach_focus_widgets(self):
        self._detach_focus_widgets()
        self.focus_strip.setMinimumWidth(5 * 54 + 8)
        self.panes[self.active_pane].set_compact(False)
        self.focus_main_layout.addWidget(self.panes[self.active_pane])
        for index, pane in enumerate(self.panes):
            if index != self.active_pane:
                pane.set_compact(True)
                self.focus_strip_layout.addWidget(pane, 1)

    def _focus_ratio(self):
        sizes = self.focus_splitter.sizes()
        total = sum(sizes)
        return sizes[0] / total if len(sizes) == 2 and total else self._last_focus_ratio

    def _schedule_focus_sizes(self, ratio=None):
        if ratio is not None:
            self._last_focus_ratio = min(0.95, max(0.25, float(ratio)))

        def apply():
            height = max(100, self.focus_splitter.height())
            upper = round(self._last_focus_ratio * height)
            self.focus_splitter.setSizes([upper, max(1, height - upper)])

        QTimer.singleShot(0, apply)

    def toggle_focus(self, pane_id):
        if self.mode == "focus" and pane_id == self.active_pane:
            self.set_mode("grid")
        else:
            self.set_active_pane(pane_id)
            self.set_mode("focus")

    def set_active_pane(self, pane_id: int):
        pane_id = int(pane_id)
        if not 0 <= pane_id < len(self.panes):
            return
        if pane_id == self.active_pane:
            return
        focus_ratio = self._focus_ratio() if self.mode == "focus" else self._last_focus_ratio
        self.active_pane = pane_id
        for index, pane in enumerate(self.panes):
            pane.set_active(index == pane_id)
        if self.mode == "focus":
            self._detach_focus_widgets()
            self._attach_focus_widgets()
            self._schedule_focus_sizes(focus_ratio)

    def set_share_mode(self, enabled: bool):
        self.share_mode = bool(enabled)
        self.interaction_controls.set_share_mode(self.share_mode)

    def is_drag_all(self):
        return self.cmb_drag_scope.currentData() == "all"

    def view_all(self):
        for pane in self.panes:
            pane.view_all()

    def state(self):
        focus_ratio = self._focus_ratio() if self.mode == "focus" else self._last_focus_ratio
        return {"mode": self.mode, "active_pane": self.active_pane,
                "focus_ratio": focus_ratio,
                "axis_views": [
                    {"x": list(pane.axis.get_xlim()), "y": list(pane.axis.get_ylim())}
                    for pane in self.panes
                ]}

    def apply_state(self, state):
        if not isinstance(state, dict):
            state = {}
        self.set_active_pane(state.get("active_pane", 0))
        self.set_mode(state.get("mode", "grid"))
        try:
            ratio = float(state.get("focus_ratio", 0.76))
        except (TypeError, ValueError):
            ratio = 0.76
        if not isfinite(ratio):
            ratio = 0.76
        ratio = min(0.88, max(0.25, ratio))
        self._schedule_focus_sizes(ratio)
        self._pending_axis_views = state.get("axis_views")

    def _apply_pending_axis_views(self):
        views, self._pending_axis_views = self._pending_axis_views, None
        if not isinstance(views, list):
            return
        for pane, view in zip(self.panes, views):
            try:
                xlim, ylim = view["x"], view["y"]
                if len(xlim) == 2 and len(ylim) == 2 and all(
                        isfinite(float(value)) for value in (*xlim, *ylim)):
                    if xlim[0] != xlim[1] and ylim[0] != ylim[1]:
                        pane.axis.set_xlim(*map(float, xlim))
                        pane.axis.set_ylim(*map(float, ylim))
                        pane.canvas.draw_idle()
            except (KeyError, TypeError, ValueError):
                continue

    def plot(self, f, s, c, mag, label="Fitted", title=None, removed=None):
        """Update the six scientifically independent views from one fit snapshot."""
        import numpy as np
        self._last_plot_args = (f, s, c, mag, label, title, removed)
        fg = np.asarray(f, dtype=float) / 1e9
        measured = np.asarray(s, dtype=complex)
        model = None if c is None else np.asarray(c, dtype=complex)
        magnitude = np.asarray(mag, dtype=float)

        axes = [pane.axis for pane in self.panes]
        for axis in axes:
            axis.clear()
            axis.grid(alpha=0.3)

        with np.errstate(divide="ignore", invalid="ignore"):
            axes[0].plot(fg, 20 * np.log10(np.abs(measured)), color="C0", label="Measured")
            axes[0].plot(fg, 20 * np.log10(magnitude), "--", color="C3", label=label)
        if removed is not None and len(removed[0]):
            axes[0].plot(np.asarray(removed[0]) / 1e9,
                         20 * np.log10(np.abs(removed[1])), "x", color="0.5",
                         label=f"Excluded ({len(removed[0])})")
        axes[0].set(xlabel="Frequency (GHz)", ylabel="Magnitude (dB)")
        axes[0].legend(fontsize=7)

        axes[1].plot(fg, np.unwrap(np.angle(measured)), color="C0", label="Measured")
        if model is not None:
            axes[1].plot(fg, np.unwrap(np.angle(model)), "--", color="C3", label=label)
        axes[1].set(xlabel="Frequency (GHz)", ylabel="Phase (rad)")
        axes[1].legend(fontsize=7)

        axes[2].plot(measured.real, measured.imag, color="C0", label="Measured")
        if model is not None:
            axes[2].plot(model.real, model.imag, "--", color="C3", label=label)
        axes[2].set(xlabel="Real", ylabel="Imaginary")
        axes[2].set_aspect("equal", adjustable="datalim")
        axes[2].legend(fontsize=7)

        axes[3].plot(fg, measured.real, color="C0", label="Measured")
        if model is not None:
            axes[3].plot(fg, model.real, "--", color="C3", label=label)
        axes[3].set(xlabel="Frequency (GHz)", ylabel="Real")
        axes[3].legend(fontsize=7)

        axes[4].plot(fg, measured.imag, color="C0", label="Measured")
        if model is not None:
            axes[4].plot(fg, model.imag, "--", color="C3", label=label)
        axes[4].set(xlabel="Frequency (GHz)", ylabel="Imaginary")
        axes[4].legend(fontsize=7)

        if model is not None:
            residual = np.abs(measured - model)
            residual_label = "|S - Fit|"
        else:
            residual = np.abs(measured) - magnitude
            residual_label = "|S| - Fit Magnitude"
        axes[5].plot(fg, residual, color=self.panes[5]._plot_colors.text, linewidth=1,
                     gid=FOREGROUND_LINE_GID)
        axes[5].set(xlabel="Frequency (GHz)", ylabel=residual_label)

        for index, (pane, axis) in enumerate(zip(self.panes, axes)):
            axis.xaxis.set_major_locator(MaxNLocator(4))
            axis.tick_params(labelsize=8)
            axis.title.set_fontsize(10)
            pane_title = f"{self.TITLES[index]} — {title}" if title else self.TITLES[index]
            pane.title_label.setText(pane_title)
            axis.set_title(pane_title, fontsize=10)
            pane.apply_scientific_plot_appearance(pane._plot_colors)
            pane.canvas.draw_idle()
        self.view_all()
        self._apply_pending_axis_views()

    def _visible_canvases(self):
        return [pane.canvas for pane in self.panes if pane.isVisible()]

    @staticmethod
    def _export_plot_colors():
        manager = get_theme_manager()
        return manager.export_plot_colors if manager is not None else WHITE_PLOT

    def _surfaces(self, pane_id=None, all_panes=False):
        if all_panes:
            canvases = self._visible_canvases()
        else:
            canvases = [self.panes[self.active_pane if pane_id is None else pane_id].canvas]
        if not canvases:
            return [], QSize(1, 1)
        origin = self.plot_area.mapTo(self, self.plot_area.rect().topLeft())
        surfaces = []
        export_colors = self._export_plot_colors()
        right = bottom = 1
        for canvas in canvases:
            point = canvas.mapTo(self, canvas.rect().topLeft())
            rect = QRect(point.x() - origin.x(), point.y() - origin.y(),
                         canvas.width(), canvas.height())
            pane = next(candidate for candidate in self.panes if candidate.canvas is canvas)
            surfaces.append(PaneRenderSurface(
                canvas, rect, pane.render_image,
                lambda pane=pane, colors=export_colors:
                pane.temporary_scientific_plot_appearance(colors),
            ))
            right, bottom = max(right, rect.right() + 1), max(bottom, rect.bottom() + 1)
        # Rectangles are measured in the whole six-pane area; move the selected
        # panes to the image origin so a single exported pane is not pushed
        # into a corner of an otherwise empty image.
        left = min(surface.target.left() for surface in surfaces)
        top = min(surface.target.top() for surface in surfaces)
        surfaces = [PaneRenderSurface(surface.widget, surface.target.translated(-left, -top),
                                      surface.image_renderer, surface.appearance_context)
                    for surface in surfaces]
        return surfaces, QSize(max(1, right - left), max(1, bottom - top))

    def render_all_image(self):
        surfaces, size = self._surfaces(all_panes=True)
        colors = self._export_plot_colors()
        return render_composite_image(surfaces, size, background=colors.background) if surfaces else None

    def render_drag_payload(self, pane_id, all_panes):
        surfaces, size = self._surfaces(pane_id, all_panes)
        if not surfaces:
            return None
        colors = self._export_plot_colors()
        image = render_composite_image(surfaces, size, background=colors.background)
        label = "All_Panes" if all_panes else self.TITLES[self.active_pane if pane_id is None else pane_id]
        return image, f"YIG_{_safe_name(label)}"

    def copy_all(self):
        image = self.render_all_image()
        if image is not None:
            QGuiApplication.clipboard().setImage(image)

    def copy_pane(self, pane_id):
        pane = self.panes[int(pane_id)]
        colors = self._export_plot_colors()
        surface = PaneRenderSurface(
            pane.canvas,
            QRect(0, 0, pane.canvas.width(), pane.canvas.height()),
            pane.render_image,
            lambda: pane.temporary_scientific_plot_appearance(colors),
        )
        QGuiApplication.clipboard().setImage(
            render_composite_image(
                [surface], pane.canvas.size(), background=colors.background
            )
        )

    def save_active_to(self, path, selected_filter=""):
        pane = self.panes[self.active_pane]
        if str(path).lower().endswith(".svg") or "SVG" in selected_filter:
            target = Path(path)
            if target.suffix.lower() != ".svg":
                target = target.with_suffix(".svg")
            colors = self._export_plot_colors()
            surface = PaneRenderSurface(
                pane.canvas, QRect(0, 0, pane.canvas.width(), pane.canvas.height()),
                pane.render_image,
                lambda: pane.temporary_scientific_plot_appearance(colors),
            )
            return write_svg([surface], pane.canvas.size(), target, background=colors.background)
        colors = self._export_plot_colors()
        image = render_composite_image(
            [PaneRenderSurface(pane.canvas,
                               QRect(0, 0, pane.canvas.width(), pane.canvas.height()),
                               pane.render_image,
                               lambda: pane.temporary_scientific_plot_appearance(colors))],
            pane.canvas.size(), background=colors.background,
        )
        target = Path(path)
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        return write_png(image, target)

    def save_active_dialog(self):
        self.panes[self.active_pane].save_active_dialog()

    def save_all_dialog(self):
        path, selected = QFileDialog.getSaveFileName(
            self, "Save All Analysis Panes", "YIG_All_Panes.png",
            "PNG image (*.png);;SVG document with embedded raster panes (*.svg)",
        )
        if path:
            self.save_all_to(path, selected)

    def save_all_to(self, path, selected_filter=""):
        surfaces, size = self._surfaces(all_panes=True)
        if not surfaces:
            return False
        target = Path(path)
        if target.suffix.lower() == ".svg" or "SVG" in selected_filter:
            if target.suffix.lower() != ".svg":
                target = target.with_suffix(".svg")
            return write_svg(surfaces, size, target,
                             background=self._export_plot_colors().background)
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        return write_png(render_composite_image(
            surfaces, size, background=self._export_plot_colors().background
        ), target)


__all__ = ["AnalysisPlotPane", "FitPlotWidget"]
