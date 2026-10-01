"""Draw a Scene with QPainter (editor view and PNG / SVG / PDF export).

Layers are extruded outlines drawn bottom-up; inside a layer the visible side
walls are drawn far-to-near, then the top face (even-odd, so holes show what
is below). Coils are drawn before the board (they sit under it), YIG spheres
after it, sine waves last.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient,
)
from shapely.geometry.polygon import orient

from app.figure_builder.dxf_import import polygons
from app.figure_builder.scene import Projection, Scene

MARGIN = 0.06


@dataclass
class Frame:
    """Figure plane (X right, Y up, mm) <-> device rectangle."""
    scale: float
    ox: float
    oy: float
    rect: QRectF
    minx: float
    maxy: float
    width: float                   # figure extent in mm
    height: float

    def point(self, X: float, Y: float) -> QPointF:
        return QPointF(self.ox + (X - self.minx) * self.scale, self.oy + (self.maxy - Y) * self.scale)

    def figure(self, px: float, py: float) -> tuple[float, float]:
        return (px - self.ox) / self.scale + self.minx, self.maxy - (py - self.oy) / self.scale

    # waves live in the figure box, 0..1 across and 0..1 down
    def box_point(self, u: float, v: float) -> QPointF:
        return QPointF(self.ox + u * self.width * self.scale, self.oy + v * self.height * self.scale)

    def box_uv(self, px: float, py: float) -> tuple[float, float]:
        return (px - self.ox) / (self.width * self.scale), (py - self.oy) / (self.height * self.scale)


def scene_bounds(scene: Scene, proj: Projection) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for layer in scene.layers:
        if not layer.visible:
            continue
        for polygon in polygons(layer.geometry):
            for x, y in polygon.exterior.coords:
                for z in (layer.z0, layer.z1):
                    X, Y = proj.project(x, y, z)
                    xs.append(X)
                    ys.append(Y)
    for coil in scene.coils:
        low = scene.bottom() - coil.gap - coil.turns * coil.pitch - coil.wire
        for angle in range(0, 360, 30):
            x = coil.x + coil.radius * math.cos(math.radians(angle))
            y = coil.y + coil.radius * math.sin(math.radians(angle))
            for z in (low, scene.bottom()):
                X, Y = proj.project(x, y, z)
                xs.append(X)
                ys.append(Y)
    for yig in scene.yigs:
        z = scene.surface_height(yig.x, yig.y) + yig.lift + yig.radius
        X, Y = proj.project(yig.x, yig.y, z)
        xs += [X - yig.radius, X + yig.radius]
        ys += [Y - yig.radius, Y + yig.radius]
    if not xs:
        return 0.0, 0.0, 10.0, 10.0
    return min(xs), min(ys), max(xs), max(ys)


def make_frame(scene: Scene, rect: QRectF, proj: Projection | None = None) -> Frame:
    proj = proj or Projection(scene.camera)
    minx, miny, maxx, maxy = scene_bounds(scene, proj)
    width, height = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)
    pad_w, pad_h = width * MARGIN, height * MARGIN + width * MARGIN * 0.5
    minx, maxx, miny, maxy = minx - pad_w, maxx + pad_w, miny - pad_h, maxy + pad_h
    width, height = maxx - minx, maxy - miny
    scale = min(rect.width() / width, rect.height() / height)
    ox = rect.x() + (rect.width() - width * scale) / 2
    oy = rect.y() + (rect.height() - height * scale) / 2
    return Frame(scale, ox, oy, rect, minx, maxy, width, height)


def _shade(color: str, factor: float) -> QColor:
    c = QColor(color)
    if factor >= 1.0:
        return c.lighter(int(100 * factor))
    return c.darker(int(100 / max(factor, 0.05)))


def layer_paths(scene: Scene, layer, proj: Projection, frame: Frame):
    """(walls [(depth, QPolygonF, shade)], top face QPainterPath) of one layer."""
    walls = []
    top = QPainterPath()
    top.setFillRule(Qt.FillRule.OddEvenFill)
    for polygon in polygons(scene.solid(layer)):
        polygon = orient(polygon, 1.0)                        # exterior CCW, holes CW: material on the left
        for ring in [polygon.exterior, *polygon.interiors]:
            coords = list(ring.coords)
            path_points = [frame.point(*proj.project(x, y, layer.z1)) for x, y in coords]
            top.addPolygon(QPolygonF(path_points))
            top.closeSubpath()
            if layer.z1 - layer.z0 <= 0:
                continue
            for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
                nx, ny = y1 - y0, -(x1 - x0)                   # outward normal (material on the left)
                rnx, rny = proj.rotate(nx, ny)
                if rny >= 0:                                    # faces away from the viewer
                    continue
                length = math.hypot(rnx, rny) or 1.0
                quad = QPolygonF([frame.point(*proj.project(x0, y0, layer.z1)),
                                  frame.point(*proj.project(x1, y1, layer.z1)),
                                  frame.point(*proj.project(x1, y1, layer.z0)),
                                  frame.point(*proj.project(x0, y0, layer.z0))])
                depth = proj.depth((x0 + x1) / 2, (y0 + y1) / 2, (layer.z0 + layer.z1) / 2)
                walls.append((depth, quad, 0.55 + 0.25 * abs(rnx) / length))
    return walls, top


def draw_layer(painter: QPainter, scene: Scene, layer, proj: Projection, frame: Frame) -> None:
    walls, top = layer_paths(scene, layer, proj, frame)
    for _depth, quad, shade in sorted(walls, key=lambda w: w[0]):
        color = _shade(layer.color, shade)
        painter.setPen(QPen(color, 0.9))                  # covers antialiasing seams between wall pieces
        painter.setBrush(color)
        painter.drawPolygon(quad)
    bounds = top.boundingRect()
    gradient = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
    gradient.setColorAt(0.0, _shade(layer.color, 1.08))
    gradient.setColorAt(1.0, _shade(layer.color, 0.92))
    painter.setBrush(QBrush(gradient))
    painter.setPen(QPen(_shade(layer.color, 0.8), 0.6))
    painter.drawPath(top)


def spin_positions(count: int) -> list[tuple[float, float]]:
    """Spin glyph centres in a unit disc (hexagonal packing, like the reference figure)."""
    if count <= 1:
        return [(0.0, 0.0)][:count]
    points = [(0.0, 0.0)]
    ring = 1
    while len(points) < count:
        n = 6 * ring
        for k in range(n):
            a = 2 * math.pi * k / n + math.pi / 6
            points.append((0.46 * ring * math.cos(a), 0.46 * ring * math.sin(a)))
        ring += 1
    return points[:count]


def spin_glyph(center: QPointF, size: float) -> tuple[QPainterPath, QPainterPath]:
    """A spin: an up arrow with a small precession ring (outline path, filled head path)."""
    line = QPainterPath()
    line.moveTo(center.x(), center.y() + size * 0.5)
    line.lineTo(center.x(), center.y() - size * 0.3)
    line.addEllipse(QPointF(center.x(), center.y() + size * 0.22), size * 0.28, size * 0.1)
    head = QPainterPath()
    head.moveTo(center.x(), center.y() - size * 0.55)
    head.lineTo(center.x() - size * 0.2, center.y() - size * 0.22)
    head.lineTo(center.x() + size * 0.2, center.y() - size * 0.22)
    head.closeSubpath()
    return line, head


def yig_screen(scene: Scene, yig, proj: Projection, frame: Frame) -> tuple[QPointF, float, QPointF]:
    surface = scene.surface_height(yig.x, yig.y)
    center = frame.point(*proj.project(yig.x, yig.y, surface + yig.lift + yig.radius))
    ground = frame.point(*proj.project(yig.x, yig.y, surface))
    return center, yig.radius * frame.scale, ground


def draw_yig(painter: QPainter, scene: Scene, yig, proj: Projection, frame: Frame) -> None:
    center, radius, ground = yig_screen(scene, yig, proj, frame)
    shadow = QRadialGradient(ground, radius * 1.2)
    shadow.setColorAt(0.0, QColor(0, 0, 0, 90))
    shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(shadow))
    painter.drawEllipse(ground, radius * 1.2, radius * 1.2 * proj.sp)
    body = QRadialGradient(QPointF(center.x() - radius * 0.35, center.y() - radius * 0.4), radius * 1.4)
    base = QColor(yig.color)
    body.setColorAt(0.0, base.lighter(260))
    body.setColorAt(0.35, base.lighter(130))
    body.setColorAt(1.0, base.darker(220))
    painter.setBrush(QBrush(body))
    painter.drawEllipse(center, radius, radius)
    size = radius * 0.3
    pen = QPen(QColor(yig.spin_color), max(0.6, radius * 0.045))
    for u, v in spin_positions(yig.spins):
        glyph_center = QPointF(center.x() + u * radius, center.y() + v * radius)
        line, head = spin_glyph(glyph_center, size)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(line)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(yig.spin_color))
        painter.drawPath(head)


def coil_turn_heights(scene: Scene, coil) -> list[float]:
    top = scene.bottom() - coil.gap - coil.wire / 2
    return [top - i * coil.pitch for i in range(max(1, coil.turns))]


def draw_coil(painter: QPainter, scene: Scene, coil, proj: Projection, frame: Frame) -> None:
    rx = coil.radius * frame.scale
    ry = coil.radius * frame.scale * proj.sp
    wire = max(1.0, coil.wire * frame.scale)
    heights = coil_turn_heights(scene, coil)
    base = QColor(coil.color)
    for z in reversed(heights):                                     # bottom turn first
        center = frame.point(*proj.project(coil.x, coil.y, z))
        box = QRectF(center.x() - rx, center.y() - ry, 2 * rx, 2 * ry)
        gradient = QLinearGradient(box.topLeft(), box.topRight())
        gradient.setColorAt(0.0, base.darker(170))
        gradient.setColorAt(0.3, base.lighter(150))
        gradient.setColorAt(0.55, base)
        gradient.setColorAt(1.0, base.darker(200))
        painter.setPen(QPen(QBrush(base.darker(160)), wire, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(box.translated(0, -wire * 0.08), 0, 180 * 16)             # far half
        painter.setPen(QPen(QBrush(gradient), wire, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.drawArc(box, 180 * 16, 180 * 16)                               # near half
    if coil.arrow:
        bottom = frame.point(*proj.project(coil.x, coil.y, heights[-1]))
        top = frame.point(*proj.project(coil.x, coil.y, scene.bottom() - coil.gap * 0.2))
        width = coil.radius * frame.scale * 0.55
        path = arrow_path(bottom, top, width)
        painter.setPen(QPen(QColor(coil.arrow_color).darker(150), 0.8))
        painter.setBrush(QColor(coil.arrow_color))
        painter.drawPath(path)


def arrow_path(tail: QPointF, tip: QPointF, width: float) -> QPainterPath:
    length = tail.y() - tip.y()
    head = min(length * 0.45, width * 1.1)
    shaft = width * 0.42
    cx = tip.x()
    path = QPainterPath()
    path.moveTo(cx, tip.y())
    path.lineTo(cx + width / 2, tip.y() + head)
    path.lineTo(cx + shaft / 2, tip.y() + head)
    path.lineTo(cx + shaft / 2, tail.y())
    path.lineTo(cx - shaft / 2, tail.y())
    path.lineTo(cx - shaft / 2, tip.y() + head)
    path.lineTo(cx - width / 2, tip.y() + head)
    path.closeSubpath()
    return path


def wave_points(wave, frame: Frame, samples: int = 240) -> list[QPointF]:
    points = []
    for i in range(samples + 1):
        t = i / samples
        u = wave.x + wave.w * t
        v = wave.y + wave.h / 2 - wave.h / 2 * math.sin(2 * math.pi * wave.cycles * t + math.radians(wave.phase))
        points.append(frame.box_point(u, v))
    return points


def wave_pen(wave, scale_px_per_pt: float = 1.0) -> QPen:
    pen = QPen(QColor(wave.color), wave.width * scale_px_per_pt)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    if wave.dashed:
        pen.setDashPattern([2.2, 1.2])
    return pen


def draw_wave(painter: QPainter, wave, frame: Frame, scale_px_per_pt: float = 1.0) -> None:
    painter.setPen(wave_pen(wave, scale_px_per_pt))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPolyline(QPolygonF(wave_points(wave, frame)))


def draw_scene(painter: QPainter, scene: Scene, rect: QRectF, background: QColor | None = None,
               scale_px_per_pt: float = 1.0) -> Frame:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if background is not None:
        painter.fillRect(rect, background)
    scene.update_levels()
    proj = Projection(scene.camera)
    frame = make_frame(scene, rect, proj)
    for coil in scene.coils:
        draw_coil(painter, scene, coil, proj, frame)
    order = sorted((l for l in scene.layers if l.visible), key=lambda l: (l.z1, l.placement == "through"))
    for layer in order:
        draw_layer(painter, scene, layer, proj, frame)
    for yig in sorted(scene.yigs, key=lambda y: proj.depth(y.x, y.y, 0)):
        draw_yig(painter, scene, yig, proj, frame)
    for wave in scene.waves:
        draw_wave(painter, wave, frame, scale_px_per_pt)
    return frame
