"""繪圖元件：2D 預覽只在換資料時重畫，切片/範圍變動只更新線條（避免卡頓）"""
import warnings
import numpy as np
import matplotlib
from contextlib import contextmanager
from io import BytesIO
from matplotlib.figure import Figure
from matplotlib.widgets import SpanSelector
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QMenu, QScrollArea, QToolButton,
    QVBoxLayout, QWidget, QSplitter,
)
from matplotlib.ticker import MaxNLocator
from pathlib import Path

from app.palette import ANALYSIS
from app.gui.drag_share import DragShareController, DragShareManager
from app.gui.plot_export import (
    PaneRenderSurface, render_composite_image, render_widget_image, write_png, write_svg,
)
from app.gui.plot_interaction import PlotInteractionControls, copy_save_buttons
from app.icons import icon, make_icon_only
from app.theme import ANALYSIS_ICON_SIZE, WHITE_PLOT, current_theme_colors, get_theme_manager

_DRAG_SHARE_MANAGER = None


def _drag_manager():
    global _DRAG_SHARE_MANAGER
    if _DRAG_SHARE_MANAGER is None:
        _DRAG_SHARE_MANAGER = DragShareManager()
    return _DRAG_SHARE_MANAGER


def _export_plot_colors():
    manager = get_theme_manager()
    return manager.export_plot_colors if manager is not None else WHITE_PLOT

matplotlib.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "PingFang TC",
                                          "Noto Sans CJK TC", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
warnings.filterwarnings("ignore", message="Tight layout not applied")
warnings.filterwarnings("ignore", message="Glyph .* missing from font")



def _normalize_surfaces(surfaces, right, bottom):
    """Place the exported panes at the image origin.

    Pane rectangles are measured inside the whole pane splitter; exporting a
    pane that is not the top-left one otherwise left an empty band and the
    plot appeared pushed to one side.
    """
    if not surfaces:
        return surfaces, QSize(1, 1)
    left = min(surface.target.left() for surface in surfaces)
    top = min(surface.target.top() for surface in surfaces)
    moved = [PaneRenderSurface(surface.widget, surface.target.translated(-left, -top),
                               surface.image_renderer, surface.appearance_context)
             for surface in surfaces]
    return moved, QSize(max(1, right - left), max(1, bottom - top))

class _CanvasWidget(QWidget):
    def __init__(self, figsize, parent=None, scroll=False):
        super().__init__(parent)
        self.setObjectName("analysisCanvasPane")
        self.fig = Figure(figsize=figsize)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self._plot_colors = WHITE_PLOT
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.toolbar)
        self.scroll = None
        if scroll:  # 空間不足時可捲動，不會把圖擠扁
            self.scroll = QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setWidget(self.canvas)
            lay.addWidget(self.scroll)
        else:
            lay.addWidget(self.canvas)
        self._share_mode = False
        self._plot_owner = None
        self._pane_id = None
        view_all = QAction(icon("view_all"), "View All", self)
        view_all.triggered.connect(self.view_all)
        copy = QAction(icon("copy"), "Copy Plot", self)
        save = QAction(icon("save"), "Save Plot...", self)
        self.toolbar.setIconSize(QSize(ANALYSIS_ICON_SIZE, ANALYSIS_ICON_SIZE))
        copy.triggered.connect(self.copy_plot)
        save.triggered.connect(self.save_plot_dialog)
        self.toolbar.addAction(view_all)
        self.toolbar.addAction(copy)
        self.toolbar.addAction(save)
        for action in self.toolbar.actions():
            if "Save the figure" in action.toolTip():
                action.setVisible(False)
        self.canvas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.canvas.customContextMenuRequested.connect(self._show_context_menu)
        self._copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.canvas)
        self._copy_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._copy_shortcut.activated.connect(self.copy_plot)
        self._save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self.canvas)
        self._save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._save_shortcut.activated.connect(self.save_plot_dialog)
        self._drag_controller = DragShareController(
            self.canvas, None, _drag_manager(), self._render_drag_payload,
            lambda: self._share_mode and self.isVisible(), lambda: False, self,
        )
        self.apply_scientific_plot_appearance(self._plot_colors, draw=False)

    def apply_scientific_plot_appearance(self, colors, *, draw=True):
        """Style plot chrome without changing data artists or colormap semantics."""
        self._plot_colors = colors
        self.fig.set_facecolor(colors.background)
        for axis in self.fig.get_axes():
            axis.set_facecolor(colors.panel)
            axis.tick_params(axis="both", colors=colors.text)
            axis.xaxis.label.set_color(colors.text)
            axis.yaxis.label.set_color(colors.text)
            axis.title.set_color(colors.text)
            for spine in axis.spines.values():
                spine.set_color(colors.border)
            for label in (*axis.get_xticklabels(), *axis.get_yticklabels()):
                label.set_color(colors.text)
            for line in (*axis.get_xgridlines(), *axis.get_ygridlines()):
                if line.get_visible():
                    line.set_color(colors.border)
                    line.set_alpha(0.45)
            legend = axis.get_legend()
            if legend is not None:
                legend.get_frame().set_facecolor(colors.panel)
                legend.get_frame().set_edgecolor(colors.border)
                for label in legend.get_texts():
                    label.set_color(colors.text)
        if draw:
            self.canvas.draw_idle()

    @contextmanager
    def temporary_scientific_plot_appearance(self, colors):
        previous = self._plot_colors
        self.apply_scientific_plot_appearance(colors, draw=False)
        try:
            yield
        finally:
            self.apply_scientific_plot_appearance(previous)

    def set_active(self, active):
        self._active = bool(active)
        colors = current_theme_colors()
        self.setStyleSheet(
            f"QWidget#analysisCanvasPane {{ border: 2px solid {colors.accent}; }}"
            if active else
            f"QWidget#analysisCanvasPane {{ border: 1px solid {colors.border}; }}"
        )

    def apply_lablog_theme(self, _colors):
        self.set_active(getattr(self, "_active", False))

    def bind_plot_owner(self, owner, pane_id):
        self._plot_owner = owner
        self._pane_id = int(pane_id)
        self._drag_controller.pane_id = self._pane_id
        self._drag_controller._render = owner.render_drag_payload
        self._drag_controller._is_multi_pane = owner.is_drag_all

    def render_image(self, scale=2):
        colors = _export_plot_colors()
        with self.temporary_scientific_plot_appearance(colors):
            return self._render_figure(scale)

    def view_all(self):
        for axis in self.fig.axes:
            axis.relim(visible_only=True)
            axis.autoscale(enable=True, axis="both", tight=False)
        self.canvas.draw_idle()

    def set_share_mode(self, enabled):
        self._share_mode = bool(enabled)

    def copy_plot(self):
        QGuiApplication.clipboard().setImage(self._render_composite())

    def _render_figure(self, scale):
        buffer = BytesIO()
        self.fig.savefig(buffer, format="png", dpi=self.fig.get_dpi() * scale)
        image = QImage.fromData(buffer.getvalue(), "PNG")
        return image if not image.isNull() else render_widget_image(self.canvas, scale=scale)

    def _render_composite(self):
        size = self.canvas.size()
        colors = _export_plot_colors()
        surface = PaneRenderSurface(
            self.canvas, QRect(0, 0, size.width(), size.height()), self._render_figure,
            lambda: self.temporary_scientific_plot_appearance(colors),
        )
        return render_composite_image([surface], size, background=colors.background)

    def save_plot_dialog(self):
        path, selected = QFileDialog.getSaveFileName(
            self, "Save Analysis Plot", "analysis_plot.png",
            "PNG image (*.png);;SVG image (*.svg)",
        )
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".svg" if "SVG" in selected else ".png")
        if target.suffix.lower() == ".svg":
            try:
                colors = _export_plot_colors()
                with self.temporary_scientific_plot_appearance(colors):
                    self.fig.savefig(target, format="svg", facecolor=colors.background)
                ok = target.exists() and target.stat().st_size > 0
            except (OSError, ValueError):
                ok = False
        else:
            ok = write_png(self._render_composite(), target)
        if not ok:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Save Analysis Plot", "The plot could not be saved.")

    def _render_drag_payload(self, _pane_id, _all_panes):
        if self._plot_owner is not None:
            return self._plot_owner.render_drag_payload(
                self._pane_id, self._plot_owner.is_drag_all()
            )
        return self._render_composite(), "YIG_Analysis_Plot"

    def _show_context_menu(self, point):
        menu = QMenu(self.canvas)
        menu.addAction("View All", self.view_all)
        menu.addSeparator()
        menu.addAction("Copy Plot", self.copy_plot)
        menu.addAction("Save Plot...", self.save_plot_dialog)
        menu.exec(self.canvas.mapToGlobal(point))


class DataPlotWidget(QWidget):
    sliceClicked = Signal(float)          # 點擊 2D 圖的 y 值
    rangeSelected = Signal(float, float)  # 切片圖拖曳的頻率範圍 (GHz)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.map_pane = _CanvasWidget((8, 4), self)
        self.slice_pane = _CanvasWidget((8, 4), self)
        self.active_pane = 0
        self.share_mode = False
        self.drag_manager = DragShareManager()
        self.map_pane.bind_plot_owner(self, 0)
        self.slice_pane.bind_plot_owner(self, 1)
        self.map_pane.canvas.mpl_connect("button_press_event", lambda _event: self.set_active_pane(0))
        self.slice_pane.canvas.mpl_connect("button_press_event", lambda _event: self.set_active_pane(1))
        self.view_all_button = make_icon_only(QToolButton(self), "view_all", "View All",
                                              ANALYSIS_ICON_SIZE, flat=True)
        self.copy_button, self.save_button = copy_save_buttons(
            self, copy_active=self.copy_active, copy_all=self.copy_all,
            save_active=self.save_active_dialog, save_all=self.save_all_dialog,
        )
        self.interaction_controls = PlotInteractionControls(self, ANALYSIS_ICON_SIZE)
        self.pointer_button = self.interaction_controls.btn_pointer
        self.share_button = self.interaction_controls.btn_share
        self.cmb_drag_scope = self.interaction_controls.scope_combo
        self.interaction_controls.mode_changed.connect(self.set_share_mode)
        actions = QHBoxLayout()
        for widget in (self.view_all_button, self.copy_button, self.save_button):
            actions.addWidget(widget)
        actions.addWidget(self.interaction_controls)
        actions.addStretch(1)
        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.splitter.addWidget(self.map_pane)
        self.splitter.addWidget(self.slice_pane)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([1, 1])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(actions)
        layout.addWidget(self.splitter)
        self.view_all_button.clicked.connect(self.view_all)
        self.f = None
        self.yv = None
        self.db = None
        self.yname = ""
        self.idx = 0
        self.frange = (0.0, 0.0)
        self.frange2 = None        # 第二視窗 (GHz)，None = 不使用
        self.ax_map = None
        self.ax_sl = None
        self.span = None
        self.zoom = True           # 繪圖範圍跟隨遮罩
        self.track = None          # 連續擬合軌跡 (y, f_ghz, ok)
        self.track_art = []
        self.removed = None        # 目前切片被去除的點（布林陣列，長度 = 頻率點數）
        self.exclude_ranges = []   # 手動排除頻段 (GHz)
        self.exclude_art = []
        self.nodes = []            # 節點頻率 (GHz)
        self.node_art = []
        self.analysis_points = []
        self.analysis_art = []
        self.map_pane.canvas.mpl_connect("button_press_event", self._on_click)

    def set_active_pane(self, pane_id):
        self.active_pane = int(pane_id) if int(pane_id) in (0, 1) else 0
        self.map_pane.set_active(self.active_pane == 0)
        self.slice_pane.set_active(self.active_pane == 1)
        self.interaction_controls.set_multi_pane_available(True)

    def view_all(self):
        self.map_pane.view_all()
        self.slice_pane.view_all()

    def set_share_mode(self, enabled):
        self.share_mode = bool(enabled)
        self.interaction_controls.set_share_mode(self.share_mode)
        self.map_pane._share_mode = self.share_mode
        self.slice_pane._share_mode = self.share_mode

    def is_drag_all(self):
        return self.cmb_drag_scope.currentData() == "all"

    def _render_surfaces(self, pane_id=None, all_panes=False):
        panes = (self.map_pane, self.slice_pane) if all_panes else (
            self.map_pane if pane_id is None else (self.map_pane, self.slice_pane)[int(pane_id)],
        )
        origin = self.splitter.mapTo(self, self.splitter.rect().topLeft())
        surfaces = []
        colors = _export_plot_colors()
        right = bottom = 1
        for pane in panes:
            canvas = pane.canvas
            point = canvas.mapTo(self, canvas.rect().topLeft())
            rect = QRect(point.x() - origin.x(), point.y() - origin.y(),
                         canvas.width(), canvas.height())
            surfaces.append(PaneRenderSurface(
                canvas, rect, pane.render_image,
                lambda pane=pane, colors=colors:
                pane.temporary_scientific_plot_appearance(colors),
            ))
            right, bottom = max(right, rect.right() + 1), max(bottom, rect.bottom() + 1)
        return _normalize_surfaces(surfaces, right, bottom)

    def render_drag_payload(self, pane_id, all_panes):
        surfaces, size = self._render_surfaces(pane_id, all_panes)
        if not surfaces:
            return None
        colors = _export_plot_colors()
        return render_composite_image(
            surfaces, size, background=colors.background
        ), "YIG_Data_Preview"

    def copy_active(self):
        pane = (self.map_pane, self.slice_pane)[self.active_pane]
        QGuiApplication.clipboard().setImage(pane._render_composite())

    def copy_all(self):
        surfaces, size = self._render_surfaces(all_panes=True)
        colors = _export_plot_colors()
        QGuiApplication.clipboard().setImage(
            render_composite_image(surfaces, size, background=colors.background)
        )

    def save_active_dialog(self):
        path, selected = QFileDialog.getSaveFileName(
            self, "Save Active Analysis Pane", "YIG_Active_Pane.png",
            "PNG image (*.png);;SVG image (*.svg)",
        )
        if path:
            self.save_active_to(path, selected)

    def save_all_dialog(self):
        path, selected = QFileDialog.getSaveFileName(
            self, "Save All Analysis Panes", "YIG_Data_Preview.png",
            "PNG image (*.png);;SVG document with embedded raster panes (*.svg)",
        )
        if path:
            self.save_all_to(path, selected)

    def save_active_to(self, path, selected_filter=""):
        pane = (self.map_pane, self.slice_pane)[self.active_pane]
        target = Path(path)
        if target.suffix.lower() == ".svg" or "SVG" in selected_filter:
            if target.suffix.lower() != ".svg":
                target = target.with_suffix(".svg")
            try:
                colors = _export_plot_colors()
                with pane.temporary_scientific_plot_appearance(colors):
                    pane.fig.savefig(target, format="svg", facecolor=colors.background)
                return target.exists() and target.stat().st_size > 0
            except (OSError, ValueError):
                return False
        return write_png(pane._render_composite(), target.with_suffix(".png"))

    def save_all_to(self, path, selected_filter=""):
        surfaces, size = self._render_surfaces(all_panes=True)
        target = Path(path)
        if target.suffix.lower() == ".svg" or "SVG" in selected_filter:
            if target.suffix.lower() != ".svg":
                target = target.with_suffix(".svg")
            return write_svg(
                surfaces, size, target, background=_export_plot_colors().background
            )
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        return write_png(render_composite_image(
            surfaces, size, background=_export_plot_colors().background
        ), target)

    def set_map(self, f_ghz, yv, db, yname, title):
        """重建整張圖（只在換檔案 / 換 S 參數 / 換軸時呼叫）"""
        if self.span is not None:
            self.span.set_active(False)
        self.f, self.yv, self.db, self.yname = f_ghz, yv, db, yname
        self.idx = min(self.idx, db.shape[1] - 1)
        map_fig = self.map_pane.fig
        slice_fig = self.slice_pane.fig
        map_fig.clear()
        slice_fig.clear()
        self.ax_map = self.hline = self.v1 = self.v2 = None
        self.v3 = self.v4 = None
        self.track_art = []
        self.node_art = []
        self.analysis_art = []

        if db.shape[1] > 1:
            self.ax_map = map_fig.add_subplot(111)
            self.ax_sl = slice_fig.add_subplot(111)
            order = np.argsort(yv)
            img = db[:, order].T
            finite = img[np.isfinite(img)]
            vmin, vmax = (np.percentile(finite, [1, 99.5]) if finite.size else (None, None))
            y0, y1 = float(yv[order[0]]), float(yv[order[-1]])
            if y0 == y1:
                y1 = y0 + 1
            self.ax_map.imshow(img, aspect="auto", origin="lower", cmap="viridis",
                               extent=[f_ghz[0], f_ghz[-1], y0, y1],
                               vmin=vmin, vmax=vmax, interpolation="nearest")
            self.hline = self.ax_map.axhline(yv[self.idx], color="r", lw=1)
            self.v1 = self.ax_map.axvline(f_ghz[0], color="w", ls="--", lw=1)
            self.v2 = self.ax_map.axvline(f_ghz[-1], color="w", ls="--", lw=1)
            self.v3 = self.ax_map.axvline(f_ghz[0], color="orange", ls="--", lw=1, visible=False)
            self.v4 = self.ax_map.axvline(f_ghz[-1], color="orange", ls="--", lw=1, visible=False)
            self.ax_map.set_xlabel("Frequency (GHz)")
            self.ax_map.set_ylabel(yname)
            self.ax_map.set_title(f"|{title}| (dB)")
        else:
            self.ax_map = map_fig.add_subplot(1, 1, 1)
            self.ax_map.text(0.5, 0.5, "A 2D source map is not available for this Data.",
                             ha="center", va="center", transform=self.ax_map.transAxes)
            self.ax_map.set_axis_off()
            self.ax_sl = slice_fig.add_subplot(1, 1, 1)

        self.l_full, = self.ax_sl.plot(f_ghz, db[:, self.idx], color="0.6", lw=1, label="full")
        self.l_sel, = self.ax_sl.plot([], [], color="C0", lw=1.5, label="fit range")
        self.l_bad, = self.ax_sl.plot([], [], "x", color="red", ms=6, mew=1.2, label="removed")
        self.exclude_art = []
        self.span2 = self.ax_sl.axvspan(f_ghz[0], f_ghz[0], color="orange", alpha=0.12,
                                        visible=False, label="window 2")
        self.ax_sl.set_xlabel("Frequency (GHz)")
        self.ax_sl.set_ylabel("Magnitude (dB)")
        self.ax_sl.grid(alpha=0.3)
        self.ax_sl.legend(loc="best")
        map_fig.tight_layout()
        slice_fig.tight_layout()
        self.span = SpanSelector(self.ax_sl, self._on_span, "horizontal", useblit=True,
                                 props=dict(alpha=0.2, facecolor="tab:orange"))
        self._draw_track()
        self._draw_excludes()
        self._draw_nodes()
        self.map_pane.apply_scientific_plot_appearance(self.map_pane._plot_colors, draw=False)
        self.slice_pane.apply_scientific_plot_appearance(self.slice_pane._plot_colors, draw=False)
        self._update()

    def set_slice(self, idx):
        if self.db is None:
            return
        self.idx = int(np.clip(idx, 0, self.db.shape[1] - 1))
        self._update()

    def set_range(self, f1, f2):
        self.frange = tuple(sorted((f1, f2)))
        if self.db is not None:
            self._update()

    def set_range2(self, rng):
        """第二視窗 (f1, f2) GHz；None 表示不使用"""
        self.frange2 = None if rng is None else tuple(sorted(rng))
        if self.db is not None:
            self._update()

    def set_removed(self, mask):
        """標記目前切片被去除的點（None = 無）"""
        self.removed = None if mask is None else np.asarray(mask, bool)
        if self.db is not None:
            self._update()

    def set_exclude_ranges(self, ranges):
        self.exclude_ranges = list(ranges or [])
        if self.db is not None:
            self._draw_excludes()
            self._update()

    def set_nodes(self, f_ghz):
        """在 2D 圖與切片圖上標示節點頻率（φ = nπ）；傳空 list 清除"""
        self.nodes = list(f_ghz or [])
        if self.db is not None:
            self._draw_nodes()
            self.map_pane.canvas.draw_idle()
            self.slice_pane.canvas.draw_idle()

    def set_analysis_points(self, points):
        """Overlay physical lines and coarse candidates with distinct styling."""
        from app.analysis.yig_mirror.results.model import COARSE_TYPES

        for artist in self.analysis_art:
            try:
                artist.remove()
            except (ValueError, AttributeError):
                pass
        self.analysis_art = []
        self.analysis_points = list(points or [])
        if self.ax_sl is None:
            return
        for point in self.analysis_points:
            frequency_ghz = point.frequency_hz / 1e9
            if point.type == "Physical Node":
                color, style = ANALYSIS["physical_node"], ":"
            elif point.type == "Physical Antinode":
                color, style = ANALYSIS["physical_antinode"], "--"
            else:
                continue
            if self.ax_map is not None:
                self.analysis_art.append(self.ax_map.axvline(
                    frequency_ghz, color=color, linestyle=style, linewidth=1.2,
                ))
            self.analysis_art.append(self.ax_sl.axvline(
                frequency_ghz, color=color, linestyle=style, linewidth=1.0,
            ))
        coarse = [point for point in self.analysis_points
                  if point.type in COARSE_TYPES and point.sweep_value is not None]
        if self.ax_map is not None and coarse:
            self.analysis_art.append(self.ax_map.scatter(
                [point.frequency_hz / 1e9 for point in coarse],
                [point.sweep_value for point in coarse],
                c=[ANALYSIS["coarse_node_marker"] if point.type == "Coarse Node Candidate" else ANALYSIS["coarse_antinode_marker"]
                   for point in coarse],
                marker="x", s=30, linewidths=1.2, zorder=5,
            ))
        self.map_pane.canvas.draw_idle()
        self.slice_pane.canvas.draw_idle()

    def _draw_nodes(self):
        for a in self.node_art:
            try:
                a.remove()
            except ValueError:
                pass
        self.node_art = []
        for f in self.nodes:
            if self.ax_map is not None:
                self.node_art.append(self.ax_map.axvline(f, color=ANALYSIS["node_line"], ls=":", lw=1.2))
            self.node_art.append(self.ax_sl.axvline(f, color=ANALYSIS["node_line"], ls=":", lw=1.0))

    def _draw_excludes(self):
        for a in self.exclude_art:
            try:
                a.remove()
            except ValueError:
                pass
        self.exclude_art = []
        for a, b in self.exclude_ranges:
            self.exclude_art.append(self.ax_sl.axvspan(a, b, color="red", alpha=0.10, lw=0))
            if self.ax_map is not None:
                self.exclude_art.append(self.ax_map.axvspan(a, b, color="red", alpha=0.18, lw=0))

    def set_zoom(self, on):
        self.zoom = bool(on)
        if self.db is not None:
            self._update()

    def layout_state(self):
        sizes = self.splitter.sizes()
        total = sum(sizes) or 1
        return {"map_ratio": sizes[0] / total}

    def apply_layout_state(self, state):
        ratio = min(0.95, max(0.05, float((state or {}).get("map_ratio", 0.5))))
        self.splitter.setSizes([round(ratio * 1000), round((1 - ratio) * 1000)])

    def set_track(self, y=None, f_ghz=None, ok=None, f2_ghz=None):
        """在 2D 圖上疊加連續擬合的頻率軌跡（f2_ghz 為第二追蹤參數）；傳 None 清除"""
        self.track = None if y is None else (np.asarray(y), np.asarray(f_ghz), np.asarray(ok, bool),
                                             None if f2_ghz is None else np.asarray(f2_ghz))
        if self.db is not None:
            self._draw_track()
            self.map_pane.canvas.draw_idle()

    def _draw_track(self):
        for a in self.track_art:
            try:
                a.remove()
            except ValueError:
                pass
        self.track_art = []
        if self.ax_map is None or self.track is None:
            return
        y, f, ok, f2 = self.track
        xl, yl = self.ax_map.get_xlim(), self.ax_map.get_ylim()
        for ff, col in ((f, ANALYSIS["window_primary"]), (f2, ANALYSIS["window_secondary"])):
            if ff is None:
                continue
            if ok.any():
                self.track_art.append(self.ax_map.plot(ff[ok], y[ok], "o", ms=3, mfc="none",
                                                       mec=col, mew=0.8)[0])
            if (~ok).any():
                self.track_art.append(self.ax_map.plot(ff[~ok], y[~ok], "x", ms=4,
                                                       color=ANALYSIS["rejected"], mew=0.8)[0])
        self.ax_map.set_xlim(xl)
        self.ax_map.set_ylim(yl)

    def _update(self):
        f, y = self.f, self.db[:, self.idx]
        f1, f2 = self.frange
        m = (f >= f1) & (f <= f2)
        r2 = self.frange2
        if r2 is not None:
            m |= (f >= r2[0]) & (f <= r2[1])
        self.l_full.set_ydata(y)
        bad = self.removed if (self.removed is not None and self.removed.shape == y.shape) else None
        if bad is not None:
            self.l_bad.set_data(f[bad], y[bad])
            good_y = np.where(bad, np.nan, y)
        else:
            self.l_bad.set_data([], [])
            good_y = y
        # 聯集中不連續的部分與被去除的點用 NaN 斷開，避免畫出跨越空白的連線
        ys = np.where(m, good_y, np.nan)
        self.l_sel.set_data(f, ys)
        on2 = r2 is not None
        if on2:
            xy = self.span2.get_xy() if hasattr(self.span2, "get_xy") else None
            try:
                self.span2.set_x(r2[0])
                self.span2.set_width(r2[1] - r2[0])
            except AttributeError:
                if xy is not None:
                    xy[:, 0] = [r2[0], r2[0], r2[1], r2[1], r2[0]][:len(xy)]
                    self.span2.set_xy(xy)
        self.span2.set_visible(on2)
        if self.hline is not None:
            yy = self.yv[self.idx]
            self.hline.set_ydata([yy, yy])
            self.v1.set_xdata([f1, f1])
            self.v2.set_xdata([f2, f2])
            self.v3.set_visible(on2)
            self.v4.set_visible(on2)
            if on2:
                self.v3.set_xdata([r2[0], r2[0]])
                self.v4.set_xdata([r2[1], r2[1]])
        if self.zoom and m.sum() >= 2:
            lo, hi = (f1, f2) if not on2 else (min(f1, r2[0]), max(f2, r2[1]))
            pad = (hi - lo) * 0.03
            xr = (lo - pad, hi + pad) if on2 else (lo, hi)
            yy = good_y[(f >= xr[0]) & (f <= xr[1])]
        else:
            xr = (f[0], f[-1])
            yy = good_y   # y 軸範圍不含被去除的尖峰
        self.ax_sl.set_xlim(*xr)
        if self.ax_map is not None:
            self.ax_map.set_xlim(*xr)
        fin = yy[np.isfinite(yy)]
        if fin.size:
            pad = max(0.05 * np.ptp(fin), 0.1)
            self.ax_sl.set_ylim(fin.min() - pad, fin.max() + pad)
        self.ax_sl.set_title(f"Slice #{self.idx}   {self.yname} = {self.yv[self.idx]:.6g}")
        self.slice_pane.canvas.draw_idle()
        self.map_pane.canvas.draw_idle()

    def _on_span(self, xmin, xmax):
        if xmax > xmin:
            self.rangeSelected.emit(xmin, xmax)

    def _on_click(self, ev):
        if self.ax_map is None or ev.inaxes is not self.ax_map or ev.ydata is None:
            return
        if getattr(self.map_pane.toolbar, "mode", ""):
            return
        self.sliceClicked.emit(float(ev.ydata))


class LegacyFitPlotWidget(_CanvasWidget):
    def __init__(self, parent=None, min_height=None):
        super().__init__((12, 7), parent, scroll=min_height is not None)
        if min_height:
            self.canvas.setMinimumHeight(min_height)

    def plot(self, f, s, c, mag, label="Fitted", title=None, removed=None):
        """removed: (f_removed, s_removed) 被去除的雜訊點，以灰色 × 標示（不影響座標範圍）"""
        fig = self.fig
        fig.clear()
        axs = fig.subplots(2, 3)
        fg = f / 1e9
        xl = "Frequency (GHz)"
        with np.errstate(divide="ignore"):
            axs[0, 0].plot(fg, 20 * np.log10(np.abs(s)), "b", label="Measured")
            axs[0, 0].plot(fg, 20 * np.log10(mag), "r--", label=label)
            if removed is not None and len(removed[0]):
                yl = axs[0, 0].get_ylim()
                rf, rs = removed
                ydb = np.clip(20 * np.log10(np.abs(rs)), *yl)
                axs[0, 0].plot(np.asarray(rf) / 1e9, ydb, "x", color="0.5", ms=5,
                               label=f"removed ({len(rf)})")
                axs[0, 0].set_ylim(yl)
        axs[0, 1].plot(fg, np.unwrap(np.angle(s)), "b", label="Measured")
        axs[0, 2].plot(s.real, s.imag, "b", label="Measured")
        axs[1, 0].plot(fg, s.real, "b", label="Measured")
        axs[1, 1].plot(fg, s.imag, "b", label="Measured")
        if c is not None:
            axs[0, 1].plot(fg, np.unwrap(np.angle(c)), "r--", label=label)
            axs[0, 2].plot(c.real, c.imag, "r--", label=label)
            axs[1, 0].plot(fg, c.real, "r--", label=label)
            axs[1, 1].plot(fg, c.imag, "r--", label=label)
            axs[1, 2].plot(fg, np.abs(s - c), "k", lw=1)
            axs[1, 2].set(xlabel=xl, ylabel="|S − fit|", title="Residual")
        else:
            axs[1, 2].plot(fg, np.abs(s) - mag, "k", lw=1)
            axs[1, 2].set(xlabel=xl, ylabel="|S| − fit", title="Residual")
        axs[1, 2].grid(alpha=0.3)
        axs[0, 0].set(xlabel=xl, ylabel="Magnitude (dB)", title="Magnitude")
        axs[0, 1].set(xlabel=xl, ylabel="Phase (rad)", title="Phase")
        axs[0, 2].set(xlabel="Real", ylabel="Imag", title="IQ Plane")
        axs[0, 2].set_aspect("equal", adjustable="datalim")
        axs[1, 0].set(xlabel=xl, ylabel="Real", title="Real Part")
        axs[1, 1].set(xlabel=xl, ylabel="Imag", title="Imaginary Part")
        for a in axs.flat:
            a.xaxis.set_major_locator(MaxNLocator(4))
            a.tick_params(labelsize=8)
            a.title.set_fontsize(10)
            a.xaxis.label.set_fontsize(9)
            a.yaxis.label.set_fontsize(9)
        for a in (axs[0, 0], axs[0, 1], axs[0, 2], axs[1, 0], axs[1, 1]):
            a.grid(alpha=0.3)
            a.legend(fontsize=7)
        if title:
            fig.suptitle(title, fontsize=10)
            fig.tight_layout(rect=(0, 0, 1, 0.93))
        else:
            fig.tight_layout()
        self.apply_scientific_plot_appearance(self._plot_colors, draw=False)
        self.canvas.draw_idle()


class ParamPlotWidget(_CanvasWidget):
    """連續擬合：參數隨掃描軸 / 追蹤頻率變化；點擊資料點可選取該切片"""
    pointClicked = Signal(int)   # 回傳 row 編號

    def __init__(self, parent=None):
        super().__init__((6, 4), parent)
        self.ax = self.fig.add_subplot(1, 1, 1)
        self._x = self._y = self._rows = None
        self.canvas.mpl_connect("button_press_event", self._on_click)

    def plot(self, x, y, err, ok, rows, xlabel, ylabel, sel_row=None, title=""):
        ax = self.ax
        ax.clear()
        x, y = np.asarray(x, float), np.asarray(y, float)
        err = None if err is None else np.asarray(err, float)
        ok = np.asarray(ok, bool)
        self._x, self._y, self._rows = x, y, list(rows)
        if ok.any():
            e = None if err is None else np.where(np.isfinite(err[ok]), err[ok], 0)
            ax.errorbar(x[ok], y[ok], yerr=e, fmt="o-", ms=3, lw=1, color="C0",
                        ecolor="C0", elinewidth=0.8, capsize=0, label="OK")
        bad = ~ok & np.isfinite(y)
        if bad.any():
            ax.plot(x[bad], y[bad], "x", color="0.6", ms=5, label="Failed / low R²")
        if sel_row is not None and sel_row in self._rows:
            i = self._rows.index(sel_row)
            if np.isfinite(y[i]):
                ax.plot([x[i]], [y[i]], "o", ms=10, mfc="none", mec="r", mew=1.5)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(alpha=0.3)
        if ok.any() or bad.any():
            ax.legend(fontsize=8, loc="best")
        self.fig.tight_layout()
        self.apply_scientific_plot_appearance(self._plot_colors, draw=False)
        self.canvas.draw_idle()

    def clear(self):
        self.ax.clear()
        self._x = self._y = self._rows = None
        self.apply_scientific_plot_appearance(self._plot_colors, draw=False)
        self.canvas.draw_idle()

    def _on_click(self, ev):
        if ev.inaxes is not self.ax or self._x is None or getattr(self.toolbar, "mode", ""):
            return
        good = np.isfinite(self._x) & np.isfinite(self._y)
        if not good.any():
            return
        pts = self.ax.transData.transform(np.column_stack([self._x[good], self._y[good]]))
        d = np.hypot(pts[:, 0] - ev.x, pts[:, 1] - ev.y)
        i = int(np.argmin(d))
        if d[i] < 15:
            self.pointClicked.emit(int(np.array(self._rows)[good][i]))


class AllParamsPlotWidget(_CanvasWidget):
    """連續擬合：所有參數 vs X 的小圖陣列；點任一資料點可選取該切片"""
    pointClicked = Signal(int)

    ROW_PX = 190   # 每列小圖最小高度；超過可視範圍時可捲動

    def __init__(self, parent=None):
        super().__init__((10, 7), parent, scroll=True)
        self._axes = []      # [(ax, x, y, rows, sel_artist)]
        self.canvas.mpl_connect("button_press_event", self._on_click)

    def plot(self, x, series, ok, rows, xlabel, sel_row=None, title=""):
        """
        series: list of dict(y, err, label, fixed)
        """
        fig = self.fig
        fig.clear()
        self._axes = []
        n = len(series)
        if n == 0:
            self.apply_scientific_plot_appearance(self._plot_colors, draw=False)
            self.canvas.draw_idle()
            return
        ncol = 3 if n <= 9 else 4
        nrow = int(np.ceil(n / ncol))
        x = np.asarray(x, float)
        ok = np.asarray(ok, bool)
        rows = list(rows)
        self.canvas.setMinimumHeight(nrow * self.ROW_PX + 40)
        axs = fig.subplots(nrow, ncol, sharex=True, squeeze=False)
        for k, ser in enumerate(series):
            ax = axs[k // ncol][k % ncol]
            y = np.asarray(ser["y"], float)
            err = None if ser.get("err") is None else np.asarray(ser["err"], float)
            if ok.any():
                e = None if err is None else np.where(np.isfinite(err[ok]), err[ok], 0)
                ax.errorbar(x[ok], y[ok], yerr=e, fmt="o-", ms=2.5, lw=0.9, color="C0",
                            ecolor="C0", elinewidth=0.6, capsize=0)
            bad = ~ok & np.isfinite(y)
            if bad.any():
                ax.plot(x[bad], y[bad], "x", color="0.6", ms=4)
            fin = y[ok & np.isfinite(y)]
            if fin.size:  # 誤差棒過大時不讓 y 軸被撐爆
                lo, hi = fin.min(), fin.max()
                pad = (hi - lo) * 0.1 or abs(hi) * 0.05 or 1.0
                ax.set_ylim(lo - pad, hi + pad)
            sel_art, = ax.plot([], [], "o", ms=8, mfc="none", mec="r", mew=1.4)
            ax.set_title(ser["label"] + (" (fixed)" if ser.get("fixed") else ""), fontsize=9)
            ax.tick_params(labelsize=8)
            ax.xaxis.set_major_locator(MaxNLocator(5))
            ax.yaxis.set_major_locator(MaxNLocator(5))
            ax.grid(alpha=0.3)
            ax.yaxis.get_major_formatter().set_useOffset(False)
            self._axes.append((ax, x, y, rows, sel_art))
        for k in range(n, nrow * ncol):
            axs[k // ncol][k % ncol].set_visible(False)
        for c in range(ncol):
            # 每欄最下面一個可見的圖標 X 軸
            for r in range(nrow - 1, -1, -1):
                if r * ncol + c < n:
                    axs[r][c].set_xlabel(xlabel, fontsize=9)
                    axs[r][c].tick_params(labelbottom=True)
                    break
        if title:
            fig.suptitle(title, fontsize=10)
            fig.tight_layout(rect=(0, 0, 1, 0.96))
        else:
            fig.tight_layout()
        self.set_selected(sel_row, draw=False)
        self.apply_scientific_plot_appearance(self._plot_colors, draw=False)
        self.canvas.draw_idle()

    def set_selected(self, sel_row, draw=True):
        for ax, x, y, rows, art in self._axes:
            if sel_row is not None and sel_row in rows:
                i = rows.index(sel_row)
                art.set_data([x[i]], [y[i]])
            else:
                art.set_data([], [])
        if draw:
            self.canvas.draw_idle()

    def clear(self):
        self.fig.clear()
        self._axes = []
        self.apply_scientific_plot_appearance(self._plot_colors, draw=False)
        self.canvas.draw_idle()

    def _on_click(self, ev):
        if getattr(self.map_pane.toolbar, "mode", ""):
            return
        for ax, x, y, rows, _ in self._axes:
            if ev.inaxes is not ax:
                continue
            good = np.isfinite(x) & np.isfinite(y)
            if not good.any():
                return
            pts = ax.transData.transform(np.column_stack([x[good], y[good]]))
            d = np.hypot(pts[:, 0] - ev.x, pts[:, 1] - ev.y)
            i = int(np.argmin(d))
            if d[i] < 15:
                self.pointClicked.emit(int(np.array(rows)[good][i]))
            return


class LegacyPhasePlotWidget(_CanvasWidget):
    """相位 / 節點：κ_eff、φ（折疊）、訊號強度 vs 共振頻率"""

    def __init__(self, parent=None):
        super().__init__((8, 8), parent, scroll=True)
        self.canvas.setMinimumHeight(620)

    def plot(self, kap=None, phi=None, depth=None, line=None, nodes=(), period=np.pi,
             frange=None, glob=None, title=""):
        """
        kap   : (f_hz, κ, inlier 或 None, 單位)
        phi   : (f_hz, φ)
        depth : (f_hz, d, detected_nodes_hz)
        line  : dict(T_ns, phi_ref, f_ref, kappa_b 或 None)
        glob  : (f_hz, φ) 全域擬合每片的相位
        """
        from ..core.phase import phase_line
        fig = self.fig
        fig.clear()
        axs = fig.subplots(3, 1, sharex=True)
        if frange is None:
            xs = [a[0] for a in (kap, phi, depth) if a is not None and len(a[0])]
            frange = (min(np.min(x) for x in xs), max(np.max(x) for x in xs)) if xs else (0, 1)
        fg = np.linspace(frange[0], frange[1], 2000)
        G = 1e9

        ax = axs[0]
        if kap is not None and len(kap[0]):
            f, k, inl, unit = kap
            inl = np.ones(len(f), bool) if inl is None else np.asarray(inl, bool)
            ax.plot(f[inl] / G, k[inl], "o", ms=3, color="C0", label="κ_eff（逐片）")
            if (~inl).any():
                ax.plot(f[~inl] / G, k[~inl], "x", ms=4, color="0.6", label="離群")
            ax.set_ylabel(f"κ_eff [{unit}]" if unit else "κ_eff")
        if line and line.get("kappa_b") is not None and np.isfinite(line["kappa_b"]):
            ph = phase_line(fg, line["T_ns"], line["phi_ref"], line["f_ref"])
            ax.plot(fg / G, line["kappa_b"] * np.sin(np.pi / period * ph) ** 2, "-", color="C3",
                    lw=1.2, label="κ_b sin²φ(f)")
        ax.set_title("耦合強度  κ_eff = κ_b sin²φ")

        ax = axs[1]
        P = period
        if phi is not None and len(phi[0]):
            ax.plot(phi[0] / G, np.mod(phi[1], P), "o", ms=3, color="C0", label="φ（逐片，mod P）")
        if glob is not None and len(glob[0]):
            ax.plot(glob[0] / G, np.mod(glob[1], P), "s", ms=4, mfc="none", color="C2",
                    label="φ（全域擬合）")
        if line:
            ph = np.mod(phase_line(fg, line["T_ns"], line["phi_ref"], line["f_ref"]), P)
            ph[np.abs(np.diff(ph, prepend=ph[0])) > P / 2] = np.nan
            ax.plot(fg / G, ph, "-", color="C3", lw=1.2, label="相位直線")
        ax.set_ylim(-0.05 * P, 1.05 * P)
        ax.set_ylabel("φ mod P [rad]")
        ax.set_title("相位（週期 P 折疊；節點 = 0 / P）")

        ax = axs[2]
        if depth is not None and len(depth[0]):
            f, d, det = depth
            o = np.argsort(f)
            ax.plot(np.asarray(f)[o] / G, np.asarray(d)[o], ".-", ms=3, lw=0.8, color="C0",
                    label="訊號強度")
            for x in det or []:
                ax.axvline(x / G, color="C1", ls="--", lw=1)
        ax.set_ylabel("max||S|−中位數| / 中位數")
        ax.set_xlabel("共振頻率 (GHz)")
        ax.set_title("訊號強度（虛線 = 自動偵測的節點）")

        for ax in axs:
            for n, f in nodes:
                ax.axvline(f / G, color=ANALYSIS["node_line"], ls=":", lw=1.2)
            ax.grid(alpha=0.3)
            h, _ = ax.get_legend_handles_labels()
            if h:
                ax.legend(loc="best", fontsize=8)
            ax.yaxis.set_major_locator(MaxNLocator(5))
        if nodes:
            ymax = axs[0].get_ylim()[1]
            for n, f in nodes:
                axs[0].text(f / G, ymax, f"n={n}", ha="center", va="bottom", fontsize=8,
                            color=ANALYSIS["node_label"])
        if title:
            fig.suptitle(title, fontsize=10)
        fig.tight_layout(rect=(0, 0, 1, 0.97 if title else 1))
        self.apply_scientific_plot_appearance(self._plot_colors, draw=False)
        self.canvas.draw_idle()


class PhasePlotWidget(QWidget):
    """Four independent, resizable plots for the phase/node workflow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.panes = [_CanvasWidget((6, 3), self) for _ in range(4)]
        self.titles = ("κm vs fm", "Phase Line", "2D Experimental Data", "Signal Depth")
        self.active_pane = 0
        self.share_mode = False
        self.drag_manager = DragShareManager()
        for index, pane in enumerate(self.panes):
            pane.bind_plot_owner(self, index)
            pane.canvas.mpl_connect("button_press_event", lambda _event, i=index: self.set_active_pane(i))

        self.view_all_button = make_icon_only(QToolButton(self), "view_all", "View All",
                                              ANALYSIS_ICON_SIZE, flat=True)
        self.copy_button, self.save_button = copy_save_buttons(
            self, copy_active=self.copy_active, copy_all=self.copy_all,
            save_active=self.save_active_dialog, save_all=self.save_all_dialog,
        )
        self.interaction_controls = PlotInteractionControls(self, ANALYSIS_ICON_SIZE)
        self.pointer_button = self.interaction_controls.btn_pointer
        self.share_button = self.interaction_controls.btn_share
        self.cmb_drag_scope = self.interaction_controls.scope_combo
        self.interaction_controls.mode_changed.connect(self.set_share_mode)
        toolbar = QHBoxLayout()
        for widget in (self.view_all_button, self.copy_button, self.save_button):
            toolbar.addWidget(widget)
        toolbar.addWidget(self.interaction_controls)
        toolbar.addStretch(1)

        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.addWidget(self.panes[0])
        self.left_splitter.addWidget(self.panes[1])
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.addWidget(self.panes[2])
        self.right_splitter.addWidget(self.panes[3])
        self.pane_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.pane_splitter.addWidget(self.left_splitter)
        self.pane_splitter.addWidget(self.right_splitter)
        self.pane_splitter.setSizes([1, 1])
        self.left_splitter.setSizes([1, 1])
        self.right_splitter.setSizes([1, 1])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(toolbar)
        layout.addWidget(self.pane_splitter, 1)

        self.view_all_button.clicked.connect(self.view_all)
        self._axes = [None] * 4
        self.plot()
        self.set_active_pane(0)
        self.interaction_controls.set_multi_pane_available(True)

    @property
    def fig(self):
        return self.panes[self.active_pane].fig

    @property
    def canvas(self):
        return self.panes[self.active_pane].canvas

    def clear_with_message(self, message):
        for pane in self.panes:
            pane.fig.clear()
            axis = pane.fig.add_subplot(111)
            axis.text(0.5, 0.5, str(message), ha="center", va="center",
                      transform=axis.transAxes, wrap=True)
            axis.set_axis_off()
            pane.apply_scientific_plot_appearance(pane._plot_colors, draw=False)
            pane.canvas.draw_idle()

    def set_active_pane(self, pane_id):
        if pane_id not in range(len(self.panes)):
            return
        self.active_pane = int(pane_id)
        for index, pane in enumerate(self.panes):
            pane.set_active(index == self.active_pane)

    def set_share_mode(self, enabled):
        self.share_mode = bool(enabled)
        self.interaction_controls.set_share_mode(self.share_mode)
        for pane in self.panes:
            pane._share_mode = self.share_mode

    def is_drag_all(self):
        return self.cmb_drag_scope.currentData() == "all"

    def layout_state(self):
        def ratio(splitter, default=0.5):
            sizes = splitter.sizes()
            total = sum(sizes)
            return sizes[0] / total if len(sizes) == 2 and total else default

        return {"outer_ratio": ratio(self.pane_splitter),
                "left_ratio": ratio(self.left_splitter),
                "right_ratio": ratio(self.right_splitter)}

    def apply_layout_state(self, state):
        from PySide6.QtCore import QByteArray
        values = state if isinstance(state, dict) else {}
        for splitter, ratio_key, legacy_key in (
                (self.pane_splitter, "outer_ratio", "outer"),
                (self.left_splitter, "left_ratio", "left"),
                (self.right_splitter, "right_ratio", "right")):
            ratio = values.get(ratio_key)
            if isinstance(ratio, (int, float)) and np.isfinite(ratio):
                ratio = min(0.85, max(0.15, float(ratio)))
                splitter.setSizes([round(1000 * ratio), round(1000 * (1 - ratio))])
            else:
                encoded = values.get(legacy_key)
                if isinstance(encoded, str) and encoded:
                    splitter.restoreState(QByteArray.fromHex(encoded.encode()))

    def _render_surfaces(self, pane_id=None, all_panes=False):
        panes = self.panes if all_panes else [
            self.panes[self.active_pane if pane_id is None else int(pane_id)]
        ]
        origin = self.pane_splitter.mapTo(self, self.pane_splitter.rect().topLeft())
        surfaces = []
        right = bottom = 1
        for pane in panes:
            canvas = pane.canvas
            point = canvas.mapTo(self, canvas.rect().topLeft())
            rect = QRect(point.x() - origin.x(), point.y() - origin.y(),
                         canvas.width(), canvas.height())
            colors = _export_plot_colors()
            surfaces.append(PaneRenderSurface(
                canvas, rect, pane.render_image,
                lambda pane=pane, colors=colors:
                pane.temporary_scientific_plot_appearance(colors),
            ))
            right, bottom = max(right, rect.right() + 1), max(bottom, rect.bottom() + 1)
        return _normalize_surfaces(surfaces, right, bottom)

    def render_drag_payload(self, pane_id, all_panes):
        surfaces, size = self._render_surfaces(pane_id, all_panes)
        if not surfaces:
            return None
        colors = _export_plot_colors()
        return render_composite_image(
            surfaces, size, background=colors.background
        ), "YIG_Phase_Node"

    def copy_active(self):
        QGuiApplication.clipboard().setImage(self.panes[self.active_pane]._render_composite())

    def copy_all(self):
        surfaces, size = self._render_surfaces(all_panes=True)
        colors = _export_plot_colors()
        QGuiApplication.clipboard().setImage(
            render_composite_image(surfaces, size, background=colors.background)
        )

    def save_active_dialog(self):
        path, selected = QFileDialog.getSaveFileName(
            self, "Save Active Analysis Pane", "YIG_Phase_Pane.png",
            "PNG image (*.png);;SVG image (*.svg)",
        )
        if path:
            self.save_active_to(path, selected)

    def save_all_dialog(self):
        path, selected = QFileDialog.getSaveFileName(
            self, "Save All Analysis Panes", "YIG_Phase_Node.png",
            "PNG image (*.png);;SVG document with embedded raster panes (*.svg)",
        )
        if path:
            self.save_all_to(path, selected)

    def save_active_to(self, path, selected_filter=""):
        pane = self.panes[self.active_pane]
        target = Path(path)
        if target.suffix.lower() == ".svg" or "SVG" in selected_filter:
            if target.suffix.lower() != ".svg":
                target = target.with_suffix(".svg")
            try:
                colors = _export_plot_colors()
                with pane.temporary_scientific_plot_appearance(colors):
                    pane.fig.savefig(target, format="svg", facecolor=colors.background)
                return target.exists() and target.stat().st_size > 0
            except (OSError, ValueError):
                return False
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        return write_png(pane._render_composite(), target)

    def save_all_to(self, path, selected_filter=""):
        surfaces, size = self._render_surfaces(all_panes=True)
        target = Path(path)
        if target.suffix.lower() == ".svg" or "SVG" in selected_filter:
            if target.suffix.lower() != ".svg":
                target = target.with_suffix(".svg")
            return write_svg(
                surfaces, size, target, background=_export_plot_colors().background
            )
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        return write_png(render_composite_image(
            surfaces, size, background=_export_plot_colors().background
        ), target)

    def view_all(self):
        for pane in self.panes:
            pane.view_all()

    def plot(self, kap=None, phi=None, depth=None, line=None, nodes=(), period=np.pi,
             frange=None, glob=None, title="", source_map=None, points=(), trajectory=None):
        from ..core.phase import phase_line

        if frange is None:
            xs = [item[0] for item in (kap, phi, depth) if item is not None and len(item[0])]
            if source_map is not None:
                xs.append(np.asarray(source_map["frequency_hz"]))
            frange = (min(np.nanmin(x) for x in xs), max(np.nanmax(x) for x in xs)) if xs else (0, 1)
        fg = np.linspace(frange[0], frange[1], 2000)
        G = 1e9
        for pane in self.panes:
            pane.fig.clear()
        axs = [pane.fig.add_subplot(111) for pane in self.panes]
        self._axes = axs

        ax = axs[0]
        if kap is not None and len(kap[0]):
            f, k, inliers, unit = kap
            inliers = np.ones(len(f), bool) if inliers is None else np.asarray(inliers, bool)
            ax.plot(f[inliers] / G, k[inliers], "o", ms=3, color="C0", label="Per-trace κeff")
            if (~inliers).any():
                ax.plot(f[~inliers] / G, k[~inliers], "x", ms=4, color="0.6", label="Rejected")
            ax.set_ylabel(f"κeff [{unit}]" if unit else "κeff")
        if line and line.get("kappa_b") is not None and np.isfinite(line["kappa_b"]):
            phase_values = phase_line(fg, line["T_ns"], line["phi_ref"], line["f_ref"])
            ax.plot(fg / G, line["kappa_b"] * np.sin(np.pi / period * phase_values) ** 2,
                    color="C3", lw=1.2, label="κb sin²φ(fm)")
        ax.set_title("κm vs fm")
        ax.set_xlabel("Resonance frequency (GHz)")

        ax = axs[1]
        if phi is not None and len(phi[0]):
            ax.plot(phi[0] / G, np.mod(phi[1], period), "o", ms=3, color="C0", label="Per-trace φ")
        if glob is not None and len(glob[0]):
            ax.plot(glob[0] / G, np.mod(glob[1], period), "s", ms=4, mfc="none", color="C2",
                    label="Global linked fit")
        if line:
            phase_values = np.mod(phase_line(fg, line["T_ns"], line["phi_ref"], line["f_ref"]), period)
            phase_values[np.abs(np.diff(phase_values, prepend=phase_values[0])) > period / 2] = np.nan
            ax.plot(fg / G, phase_values, color="C3", lw=1.2, label="Phase model")
        for index, (_n, frequency) in enumerate(nodes):
            ax.axvline(frequency / G, color=ANALYSIS["physical_node"], ls=":", lw=1.0,
                       label="Physical Node" if index == 0 else None)
        ax.set_ylim(-0.05 * period, 1.05 * period)
        ax.set_ylabel("Phase mod P (rad)")
        ax.set_xlabel("Resonance frequency (GHz)")
        ax.set_title("Phase Line")

        ax = axs[2]
        if source_map is None:
            ax.text(0.5, 0.5, "2D source data unavailable", ha="center", va="center",
                    transform=ax.transAxes)
        else:
            frequency = np.asarray(source_map["frequency_hz"], float)
            sweep = np.asarray(source_map["sweep_values"], float)
            values = np.asarray(source_map["complex_values"], complex)
            if values.shape != (frequency.size, sweep.size):
                ax.text(0.5, 0.5, "2D source dimensions are incompatible", ha="center", va="center",
                        transform=ax.transAxes)
            else:
                order = np.argsort(sweep)
                with np.errstate(divide="ignore", invalid="ignore"):
                    magnitude_db = 20 * np.log10(np.abs(values[:, order]).T)
                mesh = ax.pcolormesh(frequency / G, sweep[order], magnitude_db,
                                     shading="auto", cmap="viridis")
                ax.figure.colorbar(mesh, ax=ax, label="|S| (dB)")
                ax.set_xlabel("Frequency (GHz)")
                ax.set_ylabel(source_map.get("sweep_name", "Sweep"))
                if trajectory is not None:
                    tx, tf, valid = trajectory
                    tx, tf, valid = np.asarray(tx), np.asarray(tf), np.asarray(valid, bool)
                    ax.plot(tf[valid] / G, tx[valid], "w.-", ms=3, lw=1.0, label="Fitted resonance")
                for type_name, color, style in (
                    ("Physical Node", ANALYSIS["physical_node"], ":"),
                    ("Physical Antinode", ANALYSIS["physical_antinode"], "--"),
                ):
                    group = [point for point in points if point.type == type_name]
                    for index, point in enumerate(group):
                        ax.axvline(point.frequency_hz / G, color=color, ls=style, lw=1.1,
                                   label=type_name if index == 0 else None)
                for type_name, color, marker in (
                    ("Coarse Node Candidate", ANALYSIS["coarse_node"], "v"),
                    ("Coarse Antinode Candidate", ANALYSIS["coarse_antinode"], "^"),
                ):
                    group = [point for point in points if point.type == type_name
                             and point.sweep_value is not None]
                    if group:
                        ax.scatter([point.frequency_hz / G for point in group],
                                   [point.sweep_value for point in group],
                                   marker=marker, color=color, s=18, label=type_name)
                handles, _labels = ax.get_legend_handles_labels()
                if handles:
                    ax.legend(loc="best", fontsize=7)
        ax.set_title("2D Experimental Data")

        ax = axs[3]
        if depth is not None and len(depth[0]):
            f, signal, detected = depth
            order = np.argsort(f)
            ax.plot(np.asarray(f)[order] / G, np.asarray(signal)[order], ".-", ms=3, lw=0.8,
                    color="C0", label="Signal depth")
            for index, frequency in enumerate(detected or []):
                ax.axvline(frequency / G, color="C1", ls="--", lw=1,
                           label="Coarse candidate" if index == 0 else None)
        ax.set_ylabel("Normalized signal depth")
        ax.set_xlabel("Resonance frequency (GHz)")
        ax.set_title("Signal Depth")

        for axis, pane in zip(axs, self.panes):
            axis.grid(alpha=0.3)
            axis.yaxis.set_major_locator(MaxNLocator(5))
            handles, _labels = axis.get_legend_handles_labels()
            if handles:
                axis.legend(loc="best", fontsize=7)
            if title:
                axis.set_title(f"{axis.get_title()} — {title}", fontsize=9)
            axis.tick_params(labelsize=7)
            pane.apply_scientific_plot_appearance(pane._plot_colors, draw=False)
            pane.canvas.draw_idle()
