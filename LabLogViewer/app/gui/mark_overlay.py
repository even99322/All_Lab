"""Interactive pyqtgraph overlays for Point Marks and reference tools."""

from __future__ import annotations

import math
import time

import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPainterPath

from app.palette import MARK
from app.core.mark_model import (
    CROSSHAIR, HORIZONTAL_LINE, HORIZONTAL_RANGE, RANGE, RECT_RANGE, VERTICAL_LINE,
)


BRIGHT_GREEN = MARK["point_2d"]


def downward_pointer_path() -> QPainterPath:
    """A fixed-pixel triangle whose tip sits 7-8 px above its data origin."""
    path = QPainterPath()
    path.moveTo(-0.42, -1.42)
    path.lineTo(0.42, -1.42)
    path.lineTo(0.0, -0.55)
    path.closeSubpath()
    return path


class _FilledROI(pg.ROI):
    """ROI that paints a translucent fill; its edges are drawn by full-length lines."""

    fill_brush = None

    def paint(self, painter, option, widget=None):
        if self.fill_brush is not None:
            painter.setPen(pg.mkPen(None))
            painter.setBrush(self.fill_brush)
            painter.drawRect(self.boundingRect().adjusted(0, 0, 0, 0))
        super().paint(painter, option, widget)


class MarkOverlay(QObject):
    place_requested = Signal(float, float)
    move_requested = Signal(int, float, float)
    move_preview_requested = Signal(int, float, float)
    annotation_moved = Signal(str, float, float, float, float)
    annotation_preview = Signal(str, float, float, float, float)

    def __init__(self, plot_item: pg.PlotItem, graphics_widget, parent=None):
        super().__init__(parent)
        self.plot_item = plot_item
        self.graphics_widget = graphics_widget
        self._targets: dict[int, pg.TargetItem] = {}
        self._sample_targets: dict[int, pg.ScatterPlotItem] = {}
        self._annotation_items: dict[str, object] = {}
        self._corner_labels: dict[str, object] = {}
        self._rect_lines: dict[str, list] = {}
        self._value_labels: dict[str, object] = {}
        self._items: list = []
        self._range_preview: pg.InfiniteLine | None = None
        self._analysis_preview_items: list = []
        self._analysis_result_items: list = []
        self._range_analysis_result_items: dict[str, list] = {}
        self.placement_mode = False
        self._updating = False
        self._last_preview: dict[str, float] = {}
        self.graphics_widget.scene().sigMouseClicked.connect(self._on_scene_clicked)

    def set_placement_mode(self, enabled: bool) -> None:
        self.placement_mode = enabled
        self.graphics_widget.setCursor(Qt.CrossCursor if enabled else Qt.ArrowCursor)
        if not enabled:
            self.clear_range_preview()

    def show_range_preview(self, x: float, *, horizontal: bool = False) -> None:
        self.clear_range_preview()
        self._range_preview = pg.InfiniteLine(
            pos=x, angle=0 if horizontal else 90, movable=False,
            pen=pg.mkPen(MARK["range"], width=2, style=Qt.DashLine),
        )
        self._range_preview.setZValue(90)
        self.plot_item.addItem(self._range_preview)

    def clear_range_preview(self) -> None:
        if self._range_preview is not None:
            try:
                self.plot_item.removeItem(self._range_preview)
            except Exception:
                pass
            self._range_preview = None

    def show_analysis_region(self, start: float, end: float) -> None:
        """Show a temporary, non-object search-region preview."""
        self.clear_analysis_region()
        low, high = sorted((float(start), float(end)))
        pen = pg.mkPen(MARK["preview_region_edge"], width=1.5, style=Qt.DashLine)
        region = pg.LinearRegionItem(
            values=(low, high), orientation="vertical", movable=False,
            brush=pg.mkBrush(*MARK["preview_region_fill"]), pen=pen,
        )
        region.setZValue(18)
        self.plot_item.addItem(region)
        view = self.plot_item.getViewBox().viewRange()
        label = pg.TextItem(
            "Preview", color=MARK["range"], anchor=(0.5, 0.0),
            fill=pg.mkBrush(*MARK["preview_label_fill"]), border=pen,
        )
        label.setPos((low + high) / 2.0, view[1][1])
        label.setZValue(19)
        self.plot_item.addItem(label)
        self._analysis_preview_items = [region, label]

    def clear_analysis_region(self) -> None:
        for item in self._analysis_preview_items:
            try:
                self.plot_item.removeItem(item)
            except Exception:
                pass
        self._analysis_preview_items = []

    def show_half_peak_result(
        self, left: float, right: float, level: float, *, range_id: str | None = None,
    ) -> None:
        """Render Half-Peak geometry, optionally owned by a Range annotation."""
        if range_id is None:
            for item in self._analysis_result_items:
                try:
                    self.plot_item.removeItem(item)
                except Exception:
                    pass
            self._analysis_result_items = []
        else:
            self.clear_analysis_result(range_id)
        pen = pg.mkPen(MARK["half_level"], width=2, style=Qt.DashLine)
        segment = pg.PlotDataItem([left, right], [level, level], pen=pen)
        crossings = pg.ScatterPlotItem(
            [left, right], [level, level], symbol="t", size=10,
            pen=pg.mkPen(MARK["half_level_edge"], width=1.5), brush=pg.mkBrush(MARK["half_level_fill"]),
        )
        label = pg.TextItem(
            "Half-Level", color=MARK["half_level_edge"], anchor=(0.5, 1.15),
            fill=pg.mkBrush(*MARK["half_level_label_fill"]), border=pg.mkPen(MARK["half_level_border"]),
        )
        label.setPos((left + right) / 2.0, level)
        for item in (segment, crossings, label):
            item.setZValue(82)
            self.plot_item.addItem(item)
        items = [segment, crossings, label]
        if range_id is None:
            self._analysis_result_items = items
        else:
            self._range_analysis_result_items[range_id] = items

    def clear_analysis_result(self, range_id: str | None = None) -> None:
        if range_id is None:
            groups = [self._analysis_result_items, *self._range_analysis_result_items.values()]
            self._analysis_result_items = []
            self._range_analysis_result_items = {}
        else:
            groups = [self._range_analysis_result_items.pop(range_id, [])]
        for item in (entry for group in groups for entry in group):
            try:
                self.plot_item.removeItem(item)
            except Exception:
                pass

    def render(self, marks, annotations, selected_id: str | None,
               show_values: bool = True) -> None:
        self._remove_rendered_items()
        self._targets = {}
        self._sample_targets = {}
        self._annotation_items = {}
        self._value_labels = {}
        self._corner_labels = {}
        self._rect_lines = {}
        for mark in marks:
            if mark.visible:
                self._render_point(mark, mark.object_id == selected_id, show_values)
        for annotation in annotations:
            if annotation.visible:
                self._render_annotation(annotation, annotation.object_id == selected_id, show_values)

    def clear(self) -> None:
        self.render([], [], None)
        self.set_placement_mode(False)
        self.clear_analysis_region()
        self.clear_analysis_result()

    def _remove_rendered_items(self) -> None:
        for item in self._items:
            try:
                self.plot_item.removeItem(item)
            except Exception:
                pass
        self._items = []

    def _add_item(self, item, z: float) -> None:
        item.setZValue(z)
        self.plot_item.addItem(item)
        self._items.append(item)

    def _preview_allowed(self, key: str) -> bool:
        if self._updating:
            return False
        now = time.monotonic()
        if now - self._last_preview.get(key, 0.0) < 1 / 60:
            return False
        self._last_preview[key] = now
        return True

    @staticmethod
    def _value(value, unit) -> str:
        if value is None:
            return "-"
        suffix = f" {unit}" if unit else ""
        return f"{value:.5g}{suffix}"

    def _point_label(self, mark, show_values: bool) -> str:
        if not show_values:
            return mark.mark_id
        x_line = f"{mark.x_name}: {self._value(mark.x, mark.x_unit)}"
        y_line = f"{mark.y_name}: {self._value(mark.y, mark.y_unit)}"
        if mark.mode == "2d":
            return (f"{mark.mark_id}\n{x_line}\n{y_line}\n"
                    f"{mark.value_name}: {self._value(mark.value, mark.value_unit)}")
        return f"{mark.mark_id}\n{x_line}\n{y_line}"

    def _render_point(self, mark, selected: bool, show_values: bool) -> None:
        if mark.mode == "1d":
            color = MARK["selected"] if selected else MARK["point"]
            sample_target = pg.ScatterPlotItem(
                [mark.x], [mark.value], symbol="o", size=11 if selected else 8,
                pen=pg.mkPen(MARK["point_selected_edge"] if selected else MARK["point_edge"], width=2.4 if selected else 1.6),
                brush=pg.mkBrush(*MARK["transparent"]),
            )
            self._add_item(sample_target, 96)
            self._sample_targets[mark.number] = sample_target
            vertical_offset = 27 + ((mark.number - 1) % 3) * 24
            target = pg.TargetItem(
                pos=(mark.x, mark.value), size=14,
                symbol=downward_pointer_path(),
                pen=pg.mkPen(MARK["outline"], width=1),
                hoverPen=pg.mkPen(MARK["outline"], width=2),
                brush=pg.mkBrush(color), hoverBrush=pg.mkBrush(MARK["point_selected_edge"]),
                movable=True, label=self._point_label(mark, show_values),
                labelOpts={"offset": (0, vertical_offset), "color": color, "anchor": (0.5, 1.0)},
            )
        else:
            x_range = self.plot_item.getViewBox().viewRange()[0]
            on_right = mark.x > sum(x_range) / 2
            target = pg.TargetItem(
                pos=(mark.x, mark.y), size=15 if selected else 12,
                symbol="crosshair",
                pen=pg.mkPen(MARK["point_selected_ring"] if selected else MARK["outline_alt"], width=2.4),
                hoverPen=pg.mkPen(MARK["outline_alt"], width=3),
                brush=pg.mkBrush(BRIGHT_GREEN), hoverBrush=pg.mkBrush(BRIGHT_GREEN),
                movable=True, label=self._point_label(mark, show_values),
                labelOpts={
                    "offset": (-10 if on_right else 10, 10), "color": BRIGHT_GREEN,
                    "anchor": (1.0 if on_right else 0.0, 0.0),
                    "fill": pg.mkBrush(*MARK["point_shadow"]),
                    "border": pg.mkPen(BRIGHT_GREEN, width=0.8),
                },
            )
        target.sigPositionChangeFinished.connect(
            lambda *_args, number=mark.number, item=target:
            self.move_requested.emit(number, float(item.pos().x()), float(item.pos().y()))
        )
        target.sigPositionChanged.connect(
            lambda item=target, number=mark.number: self._emit_point_preview(number, item)
        )
        self._add_item(target, 100)
        self._targets[mark.number] = target

    def _render_annotation(self, annotation, selected: bool, show_values: bool) -> None:
        object_id = annotation.object_id
        if annotation.annotation_type == RANGE:
            pen = pg.mkPen(MARK["selected"] if selected else MARK["range"], width=2 if selected else 1.3)
            region = pg.LinearRegionItem(
                values=(annotation.x, annotation.x2), orientation="vertical",
                movable=True, brush=pg.mkBrush(*MARK["range_fill"]), pen=pen,
                hoverBrush=pg.mkBrush(*MARK["range_hover"]), hoverPen=pg.mkPen(MARK["outline"], width=2),
            )
            region.sigRegionChangeFinished.connect(
                lambda item=region, key=object_id: self._emit_range_move(key, item)
            )
            region.sigRegionChanged.connect(
                lambda item=region, key=object_id: self._emit_range_preview(key, item)
            )
            self._add_item(region, 25)
            self._annotation_items[object_id] = region
            if show_values:
                view = self.plot_item.getViewBox().viewRange()
                y_top = view[1][1] - 0.04 * (view[1][1] - view[1][0])
                x_pos = min(max((annotation.x + annotation.x2) / 2, view[0][0]), view[0][1])
                label = pg.TextItem(
                    f"{self._value(annotation.x, annotation.x_unit)} - "
                    f"{self._value(annotation.x2, annotation.x_unit)}\n"
                    f"Delta {self._value(annotation.width, annotation.x_unit)}",
                    color=MARK["range_label"], anchor=(0.5, 0.0),
                    fill=pg.mkBrush(*MARK["label_fill"]), border=pg.mkPen(MARK["range_label_border"]),
                )
                label.setPos(x_pos, y_top)
                self._add_item(label, 70)
                self._value_labels[object_id] = label
            return

        if annotation.annotation_type == HORIZONTAL_RANGE:
            pen = pg.mkPen(MARK["selected"] if selected else MARK["range"], width=2 if selected else 1.3)
            region = pg.LinearRegionItem(
                values=(annotation.y, annotation.y2), orientation="horizontal",
                movable=True, brush=pg.mkBrush(*MARK["range_fill"]), pen=pen,
                hoverBrush=pg.mkBrush(*MARK["range_hover"]), hoverPen=pg.mkPen(MARK["outline"], width=2),
            )
            region.sigRegionChangeFinished.connect(
                lambda item=region, key=object_id: self._emit_hrange(key, item, True)
            )
            region.sigRegionChanged.connect(
                lambda item=region, key=object_id: self._emit_hrange(key, item, False)
            )
            self._add_item(region, 25)
            self._annotation_items[object_id] = region
            if show_values:
                view = self.plot_item.getViewBox().viewRange()
                x_pos = view[0][0] + 0.02 * (view[0][1] - view[0][0])
                y_pos = min(max((annotation.y + annotation.y2) / 2, view[1][0]), view[1][1])
                label = pg.TextItem(
                    self._hrange_text(annotation), color=MARK["range_label"], anchor=(0.0, 0.5),
                    fill=pg.mkBrush(*MARK["label_fill"]), border=pg.mkPen(MARK["range_label_border"]),
                )
                label.setPos(x_pos, y_pos)
                self._add_item(label, 70)
                self._value_labels[object_id] = label
            return

        if annotation.annotation_type == RECT_RANGE:
            edge = pg.mkPen(MARK["selected"] if selected else "#000000", width=2)
            hover = pg.mkPen(MARK["outline"], width=2)
            roi = _FilledROI(
                (annotation.x, annotation.y), (annotation.width, annotation.height),
                pen=pg.mkPen(None), hoverPen=hover, handlePen=edge, movable=True, rotatable=False,
                resizable=False, removable=False,
            )
            roi.fill_brush = pg.mkBrush(150, 165, 235, 70)
            # Every side and corner can be dragged.
            for pos, center in (((0, 0), (1, 1)), ((1, 0), (0, 1)), ((0, 1), (1, 0)), ((1, 1), (0, 0)),
                                ((0.5, 0), (0.5, 1)), ((0.5, 1), (0.5, 0)),
                                ((0, 0.5), (1, 0.5)), ((1, 0.5), (0, 0.5))):
                roi.addScaleHandle(pos, center)
            roi.setAcceptedMouseButtons(Qt.LeftButton)
            lines = []
            for value, angle in ((annotation.x, 90), (annotation.x2, 90), (annotation.y, 0), (annotation.y2, 0)):
                line = pg.InfiniteLine(pos=value, angle=angle, movable=False, pen=edge)
                line.setAcceptedMouseButtons(Qt.NoButton)
                self._add_item(line, 24)
                lines.append(line)
            self._rect_lines[object_id] = lines
            roi.sigRegionChanged.connect(lambda item=roi, key=object_id: self._sync_rect_lines(key, item))
            roi.sigRegionChangeFinished.connect(
                lambda item=roi, key=object_id: self._emit_rect(key, item, True)
            )
            roi.sigRegionChanged.connect(
                lambda item=roi, key=object_id: self._emit_rect(key, item, False)
            )
            self._add_item(roi, 26)
            self._annotation_items[object_id] = roi
            if show_values:
                top = pg.TextItem(
                    self._rect_text(annotation), color=MARK["range_label"], anchor=(1.0, 1.0),
                    fill=pg.mkBrush(*MARK["label_fill"]),
                )
                top.setPos(annotation.x2, annotation.y2)
                self._add_item(top, 70)
                self._value_labels[object_id] = top
                corner = pg.TextItem(
                    self._rect_corner_text(annotation), color=MARK["range_label"], anchor=(0.0, 0.0),
                    fill=pg.mkBrush(*MARK["label_fill"]),
                )
                corner.setPos(annotation.x, annotation.y)
                self._add_item(corner, 70)
                self._corner_labels[object_id] = corner
            return

        if annotation.annotation_type == HORIZONTAL_LINE:
            line = pg.InfiniteLine(
                pos=annotation.y, angle=0, movable=True,
                pen=pg.mkPen(MARK["selected"] if selected else MARK["hline"], width=2 if selected else 1.5),
                hoverPen=pg.mkPen(MARK["outline"], width=3),
                label=(f"{annotation.display_name}  {self._value(annotation.y, annotation.y_unit)}"
                       if show_values else annotation.display_name),
                labelOpts={"position": 0.94, "color": MARK["hline"], "movable": True},
            )
            line.sigPositionChangeFinished.connect(
                lambda item=line, key=object_id:
                self.annotation_moved.emit(key, math.nan, float(item.value()), math.nan, math.nan)
            )
            line.sigPositionChanged.connect(
                lambda item=line, key=object_id:
                self._emit_annotation_preview(key, math.nan, float(item.value()), math.nan, math.nan)
            )
            self._add_item(line, 45)
            self._annotation_items[object_id] = line
            return

        if annotation.annotation_type == VERTICAL_LINE:
            line = pg.InfiniteLine(
                pos=annotation.x, angle=90, movable=True,
                pen=pg.mkPen(MARK["selected"] if selected else MARK["vline"], width=2 if selected else 1.5),
                hoverPen=pg.mkPen(MARK["outline"], width=3),
                label=(f"{annotation.display_name}  {self._value(annotation.x, annotation.x_unit)}"
                       if show_values else annotation.display_name),
                labelOpts={"position": 0.94, "color": MARK["vline"], "movable": True},
            )
            line.sigPositionChangeFinished.connect(
                lambda item=line, key=object_id:
                self.annotation_moved.emit(key, float(item.value()), math.nan, math.nan, math.nan)
            )
            line.sigPositionChanged.connect(
                lambda item=line, key=object_id:
                self._emit_annotation_preview(key, float(item.value()), math.nan, math.nan, math.nan)
            )
            self._add_item(line, 45)
            self._annotation_items[object_id] = line
            return

        if annotation.annotation_type == CROSSHAIR:
            pen = pg.mkPen(MARK["selected"] if selected else MARK["outline"], width=2 if selected else 1.3)
            x_range = self.plot_item.getViewBox().viewRange()[0]
            on_right = annotation.x > sum(x_range) / 2
            vertical = pg.InfiniteLine(pos=annotation.x, angle=90, movable=False, pen=pen)
            horizontal = pg.InfiniteLine(pos=annotation.y, angle=0, movable=False, pen=pen)
            handle = pg.TargetItem(
                pos=(annotation.x, annotation.y), size=14 if selected else 11,
                symbol="crosshair", pen=pg.mkPen(MARK["outline"], width=2),
                brush=pg.mkBrush(MARK["crosshair_fill"]), movable=True,
                label=(
                    f"{annotation.display_name}\nX {self._value(annotation.x, annotation.x_unit)}\n"
                    f"Y {self._value(annotation.y, annotation.y_unit)}"
                    + (f"\n{self._value(annotation.value, annotation.value_unit)}"
                       if show_values and annotation.value is not None else "")
                    if show_values else annotation.display_name
                ),
                labelOpts={
                    "offset": (-10 if on_right else 10, -10), "color": MARK["outline"],
                    "anchor": (1.0 if on_right else 0.0, 1.0),
                },
            )
            handle.sigPositionChanged.connect(
                lambda item=handle, v=vertical, h=horizontal, key=object_id:
                self._crosshair_preview(key, item, v, h)
            )
            handle.sigPositionChangeFinished.connect(
                lambda *_args, item=handle, key=object_id:
                self.annotation_moved.emit(
                    key, float(item.pos().x()), float(item.pos().y()), math.nan, math.nan
                )
            )
            for item in (vertical, horizontal, handle):
                self._add_item(item, 48 if item is not handle else 55)
            self._annotation_items[object_id] = (vertical, horizontal, handle)

    def _emit_point_preview(self, number: int, item: pg.TargetItem) -> None:
        if self._preview_allowed(f"point:{number}"):
            self.move_preview_requested.emit(number, float(item.pos().x()), float(item.pos().y()))

    def _hrange_text(self, annotation) -> str:
        return (f"{self._value(annotation.y, annotation.y_unit)} - "
                f"{self._value(annotation.y2, annotation.y_unit)}\n"
                f"Delta {self._value(annotation.height, annotation.y_unit)}")

    _SI_UNITS = {"Hz", "V", "A", "s", "W", "T", "Ω", "H", "F", "m"}
    _SI_STEPS = ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "µ"),
                 (1e-9, "n"), (1e-12, "p"))

    @classmethod
    def _g(cls, value, unit) -> str:
        """Seven significant digits; SI units get a prefix (5.033002 GHz, 1.807 MHz)."""
        if unit in cls._SI_UNITS and value and math.isfinite(value):
            for factor, prefix in cls._SI_STEPS:
                if abs(value) >= factor:
                    return f"{value / factor:.7g} {prefix}{unit}"
            return f"{value / 1e-12:.7g} p{unit}"
        return f"{value:.7g}{' ' + unit if unit else ''}"

    def _rect_text(self, annotation) -> str:
        g = self._g
        return (f"y: {g(annotation.y2, annotation.y_unit)}, <y>: {g((annotation.y + annotation.y2) / 2, annotation.y_unit)}, "
                f"dy: {g(annotation.height, annotation.y_unit)}\n"
                f"x: {g(annotation.x2, annotation.x_unit)}, <x>: {g((annotation.x + annotation.x2) / 2, annotation.x_unit)}, "
                f"dx: {g(annotation.width, annotation.x_unit)}")

    def _rect_corner_text(self, annotation) -> str:
        return (f"y: {self._g(annotation.y, annotation.y_unit)}\n"
                f"x: {self._g(annotation.x, annotation.x_unit)}")

    def _sync_rect_lines(self, object_id: str, roi) -> None:
        lines = self._rect_lines.get(object_id)
        if not lines:
            return
        pos, size = roi.pos(), roi.size()
        for line, value in zip(lines, (pos.x(), pos.x() + size.x(), pos.y(), pos.y() + size.y())):
            line.setValue(float(value))

    def _emit_hrange(self, object_id: str, region, finished: bool) -> None:
        if not finished and not self._preview_allowed(object_id):
            return
        low, high = region.getRegion()
        signal = self.annotation_moved if finished else self.annotation_preview
        signal.emit(object_id, math.nan, float(low), math.nan, float(high))

    def _emit_rect(self, object_id: str, roi, finished: bool) -> None:
        if not finished and not self._preview_allowed(object_id):
            return
        pos, size = roi.pos(), roi.size()
        x1, y1 = float(pos.x()), float(pos.y())
        x2, y2 = x1 + float(size.x()), y1 + float(size.y())
        signal = self.annotation_moved if finished else self.annotation_preview
        signal.emit(object_id, x1, y1, x2, y2)

    def _emit_range_preview(self, object_id: str, region: pg.LinearRegionItem) -> None:
        if not self._preview_allowed(object_id):
            return
        start, end = region.getRegion()
        self.annotation_preview.emit(object_id, float(start), math.nan, float(end), math.nan)

    def _emit_annotation_preview(self, object_id: str, x: float, y: float, x2: float,
                                 y2: float = math.nan) -> None:
        if self._preview_allowed(object_id):
            self.annotation_preview.emit(object_id, x, y, x2, y2)

    def _crosshair_preview(self, object_id, handle, vertical, horizontal) -> None:
        x, y = float(handle.pos().x()), float(handle.pos().y())
        vertical.setValue(x)
        horizontal.setValue(y)
        self._emit_annotation_preview(object_id, x, y, math.nan)

    def update_object(self, obj, show_values: bool) -> None:
        """Update one existing overlay in place during a live drag."""
        self._updating = True
        try:
            if hasattr(obj, "mark_id"):
                target = self._targets.get(obj.number)
                if target is None:
                    return
                target.setPos(obj.x, obj.value if obj.mode == "1d" else obj.y)
                sample_target = self._sample_targets.get(obj.number)
                if sample_target is not None and obj.mode == "1d":
                    sample_target.setData([obj.x], [obj.value])
                if show_values:
                    target.label().setFormat(self._point_label(obj, True))
                return
            item = self._annotation_items.get(obj.object_id)
            if item is None:
                return
            if obj.annotation_type == RANGE:
                item.setRegion((obj.x, obj.x2))
                label = self._value_labels.get(obj.object_id)
                if show_values and label is not None:
                    label.setText(
                        f"{self._value(obj.x, obj.x_unit)} - {self._value(obj.x2, obj.x_unit)}\n"
                        f"Delta {self._value(obj.width, obj.x_unit)}"
                    )
                    view = self.plot_item.getViewBox().viewRange()
                    label.setPos(
                        min(max((obj.x + obj.x2) / 2, view[0][0]), view[0][1]),
                        view[1][1] - 0.04 * (view[1][1] - view[1][0]),
                    )
            elif obj.annotation_type == HORIZONTAL_RANGE:
                item.setRegion((obj.y, obj.y2))
                label = self._value_labels.get(obj.object_id)
                if show_values and label is not None:
                    label.setText(self._hrange_text(obj))
                    view = self.plot_item.getViewBox().viewRange()
                    label.setPos(view[0][0] + 0.02 * (view[0][1] - view[0][0]),
                                 min(max((obj.y + obj.y2) / 2, view[1][0]), view[1][1]))
            elif obj.annotation_type == RECT_RANGE:
                item.setPos((obj.x, obj.y), finish=False)
                item.setSize((obj.width, obj.height), finish=False)
                self._sync_rect_lines(obj.object_id, item)
                label = self._value_labels.get(obj.object_id)
                if show_values and label is not None:
                    label.setText(self._rect_text(obj))
                    label.setPos(obj.x2, obj.y2)
                corner = self._corner_labels.get(obj.object_id)
                if show_values and corner is not None:
                    corner.setText(self._rect_corner_text(obj))
                    corner.setPos(obj.x, obj.y)
            elif obj.annotation_type == HORIZONTAL_LINE:
                item.setValue(obj.y)
                if show_values and item.label is not None:
                    item.label.setText(f"{obj.display_name}  {self._value(obj.y, obj.y_unit)}")
            elif obj.annotation_type == VERTICAL_LINE:
                item.setValue(obj.x)
                if show_values and item.label is not None:
                    item.label.setText(f"{obj.display_name}  {self._value(obj.x, obj.x_unit)}")
            else:
                vertical, horizontal, handle = item
                vertical.setValue(obj.x)
                horizontal.setValue(obj.y)
                handle.setPos(obj.x, obj.y)
                if show_values:
                    text = (f"{obj.display_name}\nX {self._value(obj.x, obj.x_unit)}\n"
                            f"Y {self._value(obj.y, obj.y_unit)}")
                    if obj.value is not None:
                        text += f"\n{self._value(obj.value, obj.value_unit)}"
                    handle.label().setFormat(text)
        finally:
            self._updating = False

    def _emit_range_move(self, object_id: str, region: pg.LinearRegionItem) -> None:
        start, end = region.getRegion()
        self.annotation_moved.emit(object_id, float(start), math.nan, float(end), math.nan)

    def _on_scene_clicked(self, event) -> None:
        if not self.placement_mode or event.button() != Qt.LeftButton:
            return
        scene_pos = event.scenePos()
        if not self.plot_item.sceneBoundingRect().contains(scene_pos):
            return
        view_pos = self.plot_item.getViewBox().mapSceneToView(scene_pos)
        event.accept()
        self.place_requested.emit(float(view_pos.x()), float(view_pos.y()))
