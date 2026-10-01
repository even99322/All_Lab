"""Scientific Figure Builder window (runs in its own process; see process.py)."""

from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QMainWindow, QMessageBox, QPushButton, QSlider, QSpinBox, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from app.figure_builder import render
from app.figure_builder.scene import Coil, Projection, Scene, Wave, Yig

HANDLE = 7
UNDO_LIMIT = 60


def _text(localizer, key: str) -> str:
    return localizer.text(key) if localizer is not None else key


def _segment_distance(a: QPointF, b: QPointF, p: QPointF) -> float:
    ax, ay, bx, by, px, py = a.x(), a.y(), b.x(), b.y(), p.x(), p.y()
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class ColorButton(QPushButton):
    colorChosen = Signal(str)

    def __init__(self, color: str = "#000000", parent=None):
        super().__init__(parent)
        self.setFixedWidth(64)
        self.clicked.connect(self._choose)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        self._color = QColor(color).name().upper()
        self.setText(self._color)
        text = "#000000" if QColor(self._color).lightness() > 140 else "#FFFFFF"
        self.setStyleSheet(f"background:{self._color}; color:{text}; border-radius:4px; padding:2px;")

    def color(self) -> str:
        return self._color

    def _choose(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self)
        if chosen.isValid():
            self.set_color(chosen.name())
            self.colorChosen.emit(self._color)


# -- canvas ---------------------------------------------------------------------------------------
class FigureCanvas(QWidget):
    """Draws the scene; drag YIG / coils on the board, move and freely resize sine waves,
    drag empty space to turn the view."""

    changed = Signal()              # scene edited (commit point for undo)
    selection_changed = Signal(object)
    camera_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("figureCanvas")
        self.setMouseTracking(True)
        self.setMinimumSize(420, 320)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scene: Scene | None = None
        self.selected = None
        self.background = QColor("#FFFFFF")
        self._frame = None
        self._drag = None

    def set_scene(self, scene: Scene | None) -> None:
        self.scene = scene
        self.select(None)
        self.update()

    def select(self, item) -> None:
        self.selected = item
        self.selection_changed.emit(item)
        self.update()

    # -- painting ---------------------------------------------------------------------------
    def paintEvent(self, _event):  # noqa: N802 - Qt API spelling
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.background)
        if self.scene is None:
            painter.setPen(self.palette().color(self.palette().ColorRole.PlaceholderText))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.property("emptyText") or "")
            return
        rect = QRectF(self.rect()).adjusted(12, 12, -12, -12)
        # during a drag the frame stays fixed so the figure does not jump
        frame = render.draw_scene(painter, self.scene, rect, None)
        if self._drag is None or self._frame is None:
            self._frame = frame
        self._draw_selection(painter, frame)

    def _draw_selection(self, painter: QPainter, frame) -> None:
        if self.selected is None or self.scene is None:
            return
        pen = QPen(self.palette().color(self.palette().ColorRole.Highlight), 1.4, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect = self.item_rect(self.selected, frame)
        if rect is None:
            return
        painter.drawRect(rect)
        if isinstance(self.selected, Wave):
            painter.setPen(QPen(self.palette().color(self.palette().ColorRole.Highlight), 1))
            painter.setBrush(self.palette().color(self.palette().ColorRole.Base))
            for point in self._handles(rect).values():
                painter.drawRect(QRectF(point.x() - HANDLE / 2, point.y() - HANDLE / 2, HANDLE, HANDLE))

    def item_rect(self, item, frame) -> QRectF | None:
        proj = Projection(self.scene.camera)
        if isinstance(item, Yig):
            center, radius, _ground = render.yig_screen(self.scene, item, proj, frame)
            return QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius).adjusted(-3, -3, 3, 3)
        if isinstance(item, Coil):
            heights = render.coil_turn_heights(self.scene, item)
            top = frame.point(*proj.project(item.x, item.y, heights[0]))
            bottom = frame.point(*proj.project(item.x, item.y, heights[-1]))
            rx = item.radius * frame.scale
            ry = rx * proj.sp + item.wire * frame.scale
            return QRectF(top.x() - rx, top.y() - ry, 2 * rx, bottom.y() - top.y() + 2 * ry).adjusted(-3, -3, 3, 3)
        if isinstance(item, Wave):
            a = frame.box_point(item.x, item.y)
            b = frame.box_point(item.x + item.w, item.y + item.h)
            return QRectF(a, b).normalized()
        return None

    @staticmethod
    def _handles(rect: QRectF) -> dict[str, QPointF]:
        cx, cy = rect.center().x(), rect.center().y()
        return {"nw": rect.topLeft(), "n": QPointF(cx, rect.top()), "ne": rect.topRight(),
                "e": QPointF(rect.right(), cy), "se": rect.bottomRight(), "s": QPointF(cx, rect.bottom()),
                "sw": rect.bottomLeft(), "w": QPointF(rect.left(), cy)}

    # -- hit testing ------------------------------------------------------------------------
    def item_at(self, point: QPointF):
        if self.scene is None or self._frame is None:
            return None
        frame = self._frame
        for wave in reversed(self.scene.waves):
            # a wave is picked on its line (anywhere in its box only when nothing else is there),
            # so it never hides the YIG or coils under its box
            line = render.wave_points(wave, frame, 120)
            if any(math.dist((p.x(), p.y()), (point.x(), point.y())) <= 6 for p in line) or \
                    any(_segment_distance(a, b, point) <= 6 for a, b in zip(line, line[1:])):
                return wave
        proj = Projection(self.scene.camera)
        for yig in sorted(self.scene.yigs, key=lambda y: -proj.depth(y.x, y.y, 0)):
            center, radius, _ = render.yig_screen(self.scene, yig, proj, frame)
            if math.dist((center.x(), center.y()), (point.x(), point.y())) <= radius + 3:
                return yig
        for coil in self.scene.coils:
            if self.item_rect(coil, frame).contains(point):
                return coil
        if isinstance(self.selected, Wave) and self.item_rect(self.selected, frame).contains(point):
            return self.selected
        return None

    def _handle_at(self, point: QPointF) -> str | None:
        if not isinstance(self.selected, Wave) or self._frame is None:
            return None
        for name, handle in self._handles(self.item_rect(self.selected, self._frame)).items():
            if abs(handle.x() - point.x()) <= HANDLE and abs(handle.y() - point.y()) <= HANDLE:
                return name
        return None

    # -- mouse ------------------------------------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802 - Qt API spelling
        if self.scene is None or event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        handle = self._handle_at(point)
        if handle is not None:
            self._drag = ("resize", self.selected, handle, point, (self.selected.x, self.selected.y,
                                                                  self.selected.w, self.selected.h))
            return
        item = self.item_at(point)
        self.select(item)
        if item is None:
            camera = self.scene.camera
            self._drag = ("orbit", None, None, point, (camera.elevation, camera.azimuth))
        elif isinstance(item, Wave):
            self._drag = ("move_wave", item, None, point, (item.x, item.y))
        else:
            self._drag = ("move", item, None, point, (item.x, item.y))

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt API spelling
        point = event.position()
        if self._drag is None:
            handle = self._handle_at(point)
            cursors = {"n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
                       "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
                       "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
                       "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor}
            if handle:
                self.setCursor(cursors[handle])
            elif self.item_at(point) is not None:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        kind, item, handle, start, original = self._drag
        frame = self._frame
        if kind == "orbit":
            camera = self.scene.camera
            camera.azimuth = (original[1] + (point.x() - start.x()) * 0.4) % 360
            camera.elevation = max(5.0, min(90.0, original[0] + (point.y() - start.y()) * 0.3))
            self._frame = None
            self.camera_changed.emit()
        elif kind == "move":
            self.move_item_to(item, point)
        elif kind == "move_wave":
            du, dv = frame.box_uv(point.x(), point.y())
            su, sv = frame.box_uv(start.x(), start.y())
            item.x, item.y = original[0] + du - su, original[1] + dv - sv
        elif kind == "resize":
            self.resize_wave(item, handle, point, original)
        self.update()

    def move_item_to(self, item, point: QPointF) -> None:
        frame = self._frame
        proj = Projection(self.scene.camera)
        X, Y = frame.figure(point.x(), point.y())
        if isinstance(item, Yig):
            # the sphere centre follows the pointer; solve on the plane of the surface below it
            z = self.scene.surface_height(item.x, item.y) + item.lift + item.radius
            item.x, item.y = proj.unproject(X, Y, z)
            z2 = self.scene.surface_height(item.x, item.y) + item.lift + item.radius
            if abs(z2 - z) > 1e-9:
                item.x, item.y = proj.unproject(X, Y, z2)
        elif isinstance(item, Coil):
            z = render.coil_turn_heights(self.scene, item)[0]
            item.x, item.y = proj.unproject(X, Y, z)

    def resize_wave(self, wave: Wave, handle: str, point: QPointF, original) -> None:
        """Free resize: width and height change independently (no locked aspect ratio)."""
        x, y, w, h = original
        u, v = self._frame.box_uv(point.x(), point.y())
        left, top, right, bottom = x, y, x + w, y + h
        if "w" in handle:
            left = min(u, right - 0.01)
        if "e" in handle:
            right = max(u, left + 0.01)
        if "n" in handle:
            top = min(v, bottom - 0.01)
        if "s" in handle:
            bottom = max(v, top + 0.01)
        wave.x, wave.y, wave.w, wave.h = left, top, right - left, bottom - top

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API spelling
        if self._drag is not None:
            kind = self._drag[0]
            moved = self._drag[3] != event.position()
            self._drag = None
            self._frame = None
            self.update()
            if moved or kind == "orbit":
                self.changed.emit()
                self.selection_changed.emit(self.selected)

    def keyPressEvent(self, event):  # noqa: N802 - Qt API spelling
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected is not None:
            self.scene.remove(self.selected)
            self.select(None)
            self.changed.emit()
            return
        super().keyPressEvent(event)


# -- window ---------------------------------------------------------------------------------------
class FigureBuilderWindow(QMainWindow):
    def __init__(self, localizer=None, parent=None):
        super().__init__(parent)
        self.localizer = localizer
        self.setObjectName("figureBuilderWindow")
        self.resize(1320, 820)
        self.scene: Scene | None = None
        self.project_path: Path | None = None
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._syncing = False
        self.canvas = FigureCanvas()
        self.canvas.changed.connect(self._commit)
        self.canvas.selection_changed.connect(self._show_selection)
        self.canvas.camera_changed.connect(self._sync_camera_controls)
        splitter = QSplitter()
        splitter.addWidget(self.canvas)
        self.side = QTabWidget()
        self.side.setMinimumWidth(360)
        self.side.addTab(self._layers_tab(), "")
        self.side.addTab(self._selection_tab(), "")
        self.side.addTab(self._view_tab(), "")
        splitter.addWidget(self.side)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([940, 380])
        self.setCentralWidget(splitter)
        self._build_toolbar()
        self.retranslate()
        if localizer is not None and hasattr(localizer, "language_changed"):
            localizer.language_changed.connect(lambda _l: self.retranslate())
        self._update_actions()

    def t(self, key: str) -> str:
        return _text(self.localizer, key)

    # -- toolbar ----------------------------------------------------------------------------
    def _build_toolbar(self) -> None:
        bar = QToolBar()
        bar.setObjectName("figureToolbar")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.tool_actions = {}
        for key, slot, shortcut in (
            ("open_dxf", self.choose_dxf, QKeySequence.StandardKey.Open),
            ("open_project", self.choose_project, None),
            ("save_project", self.save_project, QKeySequence.StandardKey.Save),
            (None, None, None),
            ("add_yig", self.add_yig, None), ("add_coil", self.add_coil, None), ("add_wave", self.add_wave, None),
            ("delete", self.delete_selected, None),
            (None, None, None),
            ("undo", self.undo, QKeySequence.StandardKey.Undo), ("redo", self.redo, QKeySequence.StandardKey.Redo),
            (None, None, None),
            ("export_pptx", self.choose_export_pptx, None), ("export_image", self.choose_export_image, None),
        ):
            if key is None:
                bar.addSeparator()
                continue
            action = QAction(self)
            action.triggered.connect(slot)
            if shortcut is not None:
                action.setShortcut(shortcut)
            bar.addAction(action)
            self.tool_actions[key] = action

    def retranslate(self) -> None:
        self.setWindowTitle(self.t("fig.title"))
        for key, action in self.tool_actions.items():
            action.setText(self.t(f"fig.{key}"))
        self.side.setTabText(0, self.t("fig.layers"))
        self.side.setTabText(1, self.t("fig.selection"))
        self.side.setTabText(2, self.t("fig.view"))
        self.canvas.setProperty("emptyText", self.t("fig.empty"))
        self.layer_table.setHorizontalHeaderLabels([self.t("fig.col_show"), self.t("fig.col_layer"),
                                                    self.t("fig.col_color"), self.t("fig.col_thickness"),
                                                    self.t("fig.col_place")])
        self.move_up.setText(self.t("fig.layer_up"))
        self.move_down.setText(self.t("fig.layer_down"))
        self.elevation_label.setText(self.t("fig.elevation"))
        self.azimuth_label.setText(self.t("fig.azimuth"))
        self.background_label.setText(self.t("fig.background"))
        self.no_selection.setText(self.t("fig.no_selection"))
        self.canvas.update()

    def _update_actions(self) -> None:
        loaded = self.scene is not None
        for key in ("save_project", "add_yig", "add_coil", "add_wave", "export_pptx", "export_image"):
            self.tool_actions[key].setEnabled(loaded)
        self.tool_actions["delete"].setEnabled(self.canvas.selected is not None)
        self.tool_actions["undo"].setEnabled(len(self._undo) > 1)
        self.tool_actions["redo"].setEnabled(bool(self._redo))

    # -- layers tab -------------------------------------------------------------------------
    def _layers_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.mode_label = QLabel()
        layout.addWidget(self.mode_label)
        self.layer_table = QTableWidget(0, 5)
        self.layer_table.setObjectName("figureLayers")
        self.layer_table.verticalHeader().hide()
        self.layer_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.layer_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.layer_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.layer_table, 1)
        row = QHBoxLayout()
        self.move_up = QPushButton()
        self.move_down = QPushButton()
        self.move_up.clicked.connect(lambda: self._move_layer(1))
        self.move_down.clicked.connect(lambda: self._move_layer(-1))
        row.addWidget(self.move_up)
        row.addWidget(self.move_down)
        row.addStretch(1)
        layout.addLayout(row)
        self.warnings = QLabel()
        self.warnings.setWordWrap(True)
        self.warnings.setStyleSheet("color:#C62828;")
        layout.addWidget(self.warnings)
        return page

    def _fill_layers(self) -> None:
        self._syncing = True
        table = self.layer_table
        table.setRowCount(0)
        if self.scene is None:
            self._syncing = False
            return
        self.mode_label.setText(self.t("fig.mode_" + self.scene.mode.lower()))
        # top of the stack first (as in drawing programs)
        for layer in reversed(self.scene.layers):
            row = table.rowCount()
            table.insertRow(row)
            show = QCheckBox()
            show.setChecked(layer.visible)
            show.toggled.connect(lambda on, l=layer: self._set_layer(l, "visible", on))
            table.setCellWidget(row, 0, show)
            item = QTableWidgetItem(layer.name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 1, item)
            color = ColorButton(layer.color)
            color.colorChosen.connect(lambda c, l=layer: self._set_layer(l, "color", c))
            table.setCellWidget(row, 2, color)
            thickness = QDoubleSpinBox()
            thickness.setDecimals(3)
            thickness.setRange(0.001, 50.0)
            thickness.setSingleStep(0.01)
            thickness.setSuffix(" mm")
            thickness.setValue(layer.thickness)
            thickness.setEnabled(layer.placement != "through")
            thickness.editingFinished.connect(lambda l=layer, w=thickness: self._set_layer(l, "thickness", w.value()))
            table.setCellWidget(row, 3, thickness)
            place = QComboBox()
            for value in ("stack", "same", "through"):
                place.addItem(self.t(f"fig.place_{value}"), value)
            place.setCurrentIndex(("stack", "same", "through").index(layer.placement))
            place.currentIndexChanged.connect(lambda _i, l=layer, w=place: self._set_layer(l, "placement", w.currentData()))
            table.setCellWidget(row, 4, place)
        table.resizeColumnsToContents()
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._syncing = False

    def _set_layer(self, layer, attribute: str, value) -> None:
        if self._syncing or getattr(layer, attribute) == value:
            return
        setattr(layer, attribute, value)
        self.scene.update_levels()
        self.canvas.update()
        self._commit()
        if attribute == "placement":
            self._fill_layers()

    def _move_layer(self, direction: int) -> None:
        """Move the selected layer up (+1) or down (-1) in the stack."""
        if self.scene is None:
            return
        row = self.layer_table.currentRow()
        if row < 0:
            return
        index = len(self.scene.layers) - 1 - row
        target = index + direction
        if not 0 <= target < len(self.scene.layers):
            return
        layers = self.scene.layers
        layers[index], layers[target] = layers[target], layers[index]
        self.scene.update_levels()
        self._fill_layers()
        self.layer_table.selectRow(len(layers) - 1 - target)
        self.canvas.update()
        self._commit()

    # -- selection tab ----------------------------------------------------------------------
    def _selection_tab(self) -> QWidget:
        self.selection_stack = QStackedWidget()
        self.no_selection = QLabel()
        self.no_selection.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_selection.setWordWrap(True)
        self.selection_stack.addWidget(self.no_selection)
        self.editors = {}
        for kind, fields in (
            ("yig", [("radius", "num"), ("lift", "num"), ("spins", "int"), ("color", "color"),
                     ("spin_color", "color")]),
            ("coil", [("radius", "num"), ("wire", "num"), ("turns", "int"), ("pitch", "num"), ("gap", "num"),
                      ("color", "color"), ("arrow", "bool"), ("arrow_color", "color")]),
            ("wave", [("cycles", "num"), ("phase", "num"), ("width", "num"), ("dashed", "bool"),
                      ("color", "color")]),
        ):
            page = QWidget()
            form = QFormLayout(page)
            widgets = {}
            for name, kind_of in fields:
                if kind_of == "num":
                    widget = QDoubleSpinBox()
                    widget.setDecimals(3)
                    widget.setRange(-360.0 if name == "phase" else 0.0, 360.0 if name == "phase" else 1000.0)
                    widget.setSingleStep(0.05 if name not in ("phase", "width", "cycles") else
                                         {"phase": 15.0, "width": 0.5, "cycles": 0.25}[name])
                    widget.editingFinished.connect(lambda n=name, w=widget: self._set_item(n, w.value()))
                elif kind_of == "int":
                    widget = QSpinBox()
                    widget.setRange(1, 61 if name == "spins" else 60)
                    widget.editingFinished.connect(lambda n=name, w=widget: self._set_item(n, w.value()))
                elif kind_of == "bool":
                    widget = QCheckBox()
                    widget.toggled.connect(lambda on, n=name: self._set_item(n, on))
                else:
                    widget = ColorButton()
                    widget.colorChosen.connect(lambda c, n=name: self._set_item(n, c))
                label = QLabel()
                label.setObjectName(f"label_{kind}_{name}")
                form.addRow(label, widget)
                widgets[name] = (label, widget)
            self.editors[kind] = (page, widgets)
            self.selection_stack.addWidget(page)
        return self.selection_stack

    def _show_selection(self, item) -> None:
        self._update_actions()
        kind = {Yig: "yig", Coil: "coil", Wave: "wave"}.get(type(item))
        if kind is None:
            self.selection_stack.setCurrentIndex(0)
            return
        page, widgets = self.editors[kind]
        self._syncing = True
        for name, (label, widget) in widgets.items():
            label.setText(self.t(f"fig.{kind}_{name}"))
            value = getattr(item, name)
            if isinstance(widget, ColorButton):
                widget.set_color(value)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            else:
                widget.setValue(value)
        self._syncing = False
        self.selection_stack.setCurrentWidget(page)
        self.side.setCurrentIndex(1)

    def _set_item(self, name: str, value) -> None:
        item = self.canvas.selected
        if self._syncing or item is None or getattr(item, name) == value:
            return
        setattr(item, name, value)
        self.canvas.update()
        self._commit()

    # -- view tab ---------------------------------------------------------------------------
    def _view_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.elevation = QSlider(Qt.Orientation.Horizontal)
        self.elevation.setRange(5, 90)
        self.azimuth = QSlider(Qt.Orientation.Horizontal)
        self.azimuth.setRange(0, 359)
        self.elevation.valueChanged.connect(lambda v: self._set_camera("elevation", v))
        self.azimuth.valueChanged.connect(lambda v: self._set_camera("azimuth", v))
        self.elevation.sliderReleased.connect(self._commit)
        self.azimuth.sliderReleased.connect(self._commit)
        self.elevation_label = QLabel()
        self.azimuth_label = QLabel()
        form.addRow(self.elevation_label, self.elevation)
        form.addRow(self.azimuth_label, self.azimuth)
        self.background_label = QLabel()
        self.background_button = ColorButton("#FFFFFF")
        self.background_button.colorChosen.connect(self._set_background)
        form.addRow(self.background_label, self.background_button)
        return page

    def _set_camera(self, name: str, value: float) -> None:
        if self._syncing or self.scene is None:
            return
        setattr(self.scene.camera, name, float(value))
        self.canvas.update()

    def _sync_camera_controls(self) -> None:
        if self.scene is None:
            return
        self._syncing = True
        self.elevation.setValue(int(round(self.scene.camera.elevation)))
        self.azimuth.setValue(int(round(self.scene.camera.azimuth)) % 360)
        self._syncing = False

    def _set_background(self, color: str) -> None:
        self.canvas.background = QColor(color)
        self.canvas.update()

    # -- loading ----------------------------------------------------------------------------
    def choose_dxf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.t("fig.open_dxf"), "", "AutoCAD DXF (*.dxf)")
        if path:
            self.load_dxf(path)

    def load_dxf(self, path: str) -> bool:
        from app.figure_builder.dxf_import import DxfImportError, import_dxf

        try:
            drawing = import_dxf(path)
        except DxfImportError as error:
            QMessageBox.warning(self, self.t("fig.title"), str(error))
            return False
        self.set_scene(Scene.from_drawing(drawing, source=str(path)))
        self.warnings.setText("\n".join(drawing.warnings))
        self.project_path = None
        return True

    def set_scene(self, scene: Scene) -> None:
        self.scene = scene
        self.canvas.set_scene(scene)
        self._undo = [scene.to_dict()]
        self._redo = []
        self._fill_layers()
        self._sync_camera_controls()
        self.warnings.clear()
        self._update_actions()

    def choose_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.t("fig.open_project"), "", "Figure (*.llvfig)")
        if path:
            self.open_project(path)

    def open_project(self, path: str) -> bool:
        try:
            scene = Scene.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError) as error:
            QMessageBox.warning(self, self.t("fig.title"), str(error))
            return False
        self.set_scene(scene)
        self.project_path = Path(path)
        return True

    def save_project(self, path: str | None = None) -> Path | None:
        if self.scene is None:
            return None
        if not path:
            suggestion = str(self.project_path or Path(self.scene.source or "figure").with_suffix(".llvfig"))
            path, _ = QFileDialog.getSaveFileName(self, self.t("fig.save_project"), suggestion, "Figure (*.llvfig)")
            if not path:
                return None
        self.project_path = Path(path)
        self.project_path.write_text(json.dumps(self.scene.to_dict(), ensure_ascii=False), encoding="utf-8")
        return self.project_path

    # -- editing ----------------------------------------------------------------------------
    def add_yig(self) -> Yig | None:
        if self.scene is None:
            return None
        item = self.scene.add_yig()
        self.canvas.select(item)
        self._commit()
        return item

    def add_coil(self) -> Coil | None:
        if self.scene is None:
            return None
        item = self.scene.add_coil()
        self.canvas.select(item)
        self._commit()
        return item

    def add_wave(self) -> Wave | None:
        if self.scene is None:
            return None
        item = self.scene.add_wave()
        self.canvas.select(item)
        self._commit()
        return item

    def delete_selected(self) -> None:
        if self.scene is not None and self.canvas.selected is not None:
            self.scene.remove(self.canvas.selected)
            self.canvas.select(None)
            self._commit()

    def _commit(self) -> None:
        if self.scene is None:
            return
        state = self.scene.to_dict()
        if self._undo and self._undo[-1] == state:
            self._update_actions()
            return
        self._undo.append(state)
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()
        self._update_actions()

    def _restore(self, state: dict) -> None:
        selected = self.canvas.selected
        selected_id = getattr(selected, "id", None)
        self.scene = Scene.from_dict(state)
        self.canvas.scene = self.scene
        match = next((i for i in self.scene.yigs + self.scene.coils + self.scene.waves if i.id == selected_id), None)
        self.canvas.select(match)
        self._fill_layers()
        self._sync_camera_controls()
        self._update_actions()

    def undo(self) -> None:
        if len(self._undo) > 1:
            self._redo.append(self._undo.pop())
            self._restore(self._undo[-1])

    def redo(self) -> None:
        if self._redo:
            state = self._redo.pop()
            self._undo.append(state)
            self._restore(state)

    # -- export -----------------------------------------------------------------------------
    def choose_export_pptx(self) -> None:
        if self.scene is None:
            return
        suggestion = str(Path(self.scene.source or "figure").with_suffix(".pptx"))
        path, _ = QFileDialog.getSaveFileName(self, self.t("fig.export_pptx"), suggestion, "PowerPoint (*.pptx)")
        if path:
            self.export_pptx(path)

    def export_pptx(self, path: str) -> Path:
        from app.figure_builder.pptx_export import export_pptx

        return export_pptx(self.scene, path)

    def choose_export_image(self) -> None:
        if self.scene is None:
            return
        suggestion = str(Path(self.scene.source or "figure").with_suffix(".png"))
        path, chosen = QFileDialog.getSaveFileName(self, self.t("fig.export_image"), suggestion,
                                                   "PNG (*.png);;SVG (*.svg);;PDF (*.pdf)")
        if path:
            self.export_image(path)

    def export_image(self, path: str, width: int = 3000) -> Path:
        """PNG (width px, transparent where the background is not painted), SVG or PDF (vector)."""
        path = Path(path)
        suffix = path.suffix.lower()
        bounds = QRectF(0, 0, width, width * 0.62)
        background = self.canvas.background
        if suffix == ".svg":
            from PySide6.QtSvg import QSvgGenerator
            from PySide6.QtCore import QSize

            generator = QSvgGenerator()
            generator.setFileName(str(path))
            generator.setSize(QSize(int(bounds.width()), int(bounds.height())))
            generator.setViewBox(bounds)
            generator.setTitle("LabLogViewer figure")
            painter = QPainter(generator)
            render.draw_scene(painter, self.scene, bounds, background, scale_px_per_pt=width / 1000)
            painter.end()
        elif suffix == ".pdf":
            from PySide6.QtCore import QMarginsF, QSizeF
            from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter

            writer = QPdfWriter(str(path))
            writer.setPageLayout(QPageLayout(QPageSize(QSizeF(254, 157.5), QPageSize.Unit.Millimeter),
                                             QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0)))
            writer.setResolution(300)
            painter = QPainter(writer)
            page = QRectF(painter.viewport())
            render.draw_scene(painter, self.scene, page, background, scale_px_per_pt=page.width() / 1000)
            painter.end()
        else:
            from PySide6.QtGui import QImage

            image = QImage(int(bounds.width()), int(bounds.height()), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(background)
            painter = QPainter(image)
            render.draw_scene(painter, self.scene, bounds, None, scale_px_per_pt=width / 1000)
            painter.end()
            image.save(str(path))
        return path
