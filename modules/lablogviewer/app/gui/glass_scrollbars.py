"""Liquid-glass scroll bars (vertical and horizontal), application wide.

Scroll bars float over the content (transparent track); the handle is the same
optical glass as the toolbar capsules (app/gui/glass.py): it refracts the
content that is really underneath it. The refraction is computed outside
painting -- after a scroll, at most every 30 ms -- and cached, so painting a
scroll bar only draws a stored image and scrolling stays smooth.

Vertical handles reuse the horizontal capsule kernel: the backdrop is turned a
quarter turn, refracted, and drawn with the painter turned back.
LABLOGVIEWER_DISABLE_GLASS=1 falls back to a plain frosted handle.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QProxyStyle, QScrollBar, QStyle

from app.gui import glass

EXTENT = 12            # scroll bar thickness (logical px)
THIN = 7               # handle thickness at rest
WIDE = 10              # handle thickness while hovered / dragged
MIN_HANDLE = 28
REFRESH_MS = 30


def _area_of(bar: QScrollBar) -> QAbstractScrollArea | None:
    parent = bar.parentWidget()
    while parent is not None and not isinstance(parent, QAbstractScrollArea):
        parent = parent.parentWidget()
    return parent


class _BarGlass(QObject):
    """Keeps one scroll bar's refracted handle image up to date."""

    def __init__(self, bar: QScrollBar):
        super().__init__(bar)
        self.bar = bar
        self.image: QImage | None = None
        self.image_rect = QRect()
        self.renderer = glass.GlassRenderer()
        self._grabbing = False
        self.timer = QTimer(self, singleShot=True, interval=REFRESH_MS, timeout=self.refresh)
        bar.valueChanged.connect(self.schedule)
        bar.rangeChanged.connect(self.schedule)
        bar.installEventFilter(self)
        area = _area_of(bar)
        self.viewport = area.viewport() if area is not None else None
        if self.viewport is not None:
            self.viewport.installEventFilter(self)

    def schedule(self, *_args) -> None:
        if not self.timer.isActive():
            self.timer.start()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API spelling
        kind = event.type()
        if watched is self.bar and kind in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Enter,
                                            QEvent.Type.Leave, QEvent.Type.MouseButtonRelease):
            self.schedule()
        elif watched is self.viewport and kind == QEvent.Type.Paint and not self._grabbing:
            self.schedule()                                   # content under the glass changed
        return False

    def handle_rect(self) -> QRect:
        return handle_rect(self.bar)

    def refresh(self) -> None:
        bar = self.bar
        if not bar.isVisible() or self.viewport is None or not glass.optical_glass_available():
            self.image = None
            return
        rect = self.handle_rect()
        if rect.width() < 4 or rect.height() < 4:
            self.image = None
            return
        dpr = bar.devicePixelRatioF()
        material = glass.current_glass_material()
        pad = max(2, int(material.pad_px(1.0)))
        target = QRect(self.viewport.mapFromGlobal(bar.mapToGlobal(rect.topLeft())), rect.size()).adjusted(-pad, -pad, pad, pad)
        self._grabbing = True
        try:
            backdrop = self.viewport.grab(target.intersected(self.viewport.rect())).toImage()
        finally:
            self._grabbing = False
        if backdrop.isNull():
            self.image = None
            return
        # place the grab into the full padded frame (edges clamp to the nearest pixel)
        frame = QImage(int(target.width() * dpr), int(target.height() * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(bar.palette().color(bar.backgroundRole()))
        frame.setDevicePixelRatio(dpr)
        painter = QPainter(frame)
        offset = target.intersected(self.viewport.rect()).topLeft() - target.topLeft()
        painter.drawImage(QPoint(offset.x(), offset.y()), backdrop)
        painter.end()
        pixels = glass._pixels(frame)
        vertical = bar.orientation() == Qt.Orientation.Vertical
        if vertical:
            # painter.rotate(90) turns a horizontal image clockwise, so feed it the backdrop turned anticlockwise
            pixels = np.ascontiguousarray(np.rot90(pixels, 1))
        h, w = pixels.shape[:2]
        device_pad = int(round(pad * dpr))
        inner = QRect(device_pad, device_pad, w - 2 * device_pad, h - 2 * device_pad)
        try:
            dark = QColor(bar.palette().color(bar.backgroundRole())).lightness() < 128
            style = glass.DARK_STYLE if dark else glass.LIGHT_STYLE
            refracted = self.renderer.refract(pixels, inner, _scaled(material, pad), dpr, edge_shade=style.edge_shade)
        except (ValueError, IndexError):
            self.image = None
            return
        self.image = refracted
        self.image_rect = rect
        bar.update()


def _scaled(material, pad: int):
    """Scroll handles are thinner than toolbar capsules: a proportionally smaller pad and bevel."""
    from dataclasses import replace

    return replace(material, pad=float(pad), bevel=min(material.bevel, 5.0), strength=material.strength * 0.5)


def handle_rect(bar: QScrollBar) -> QRect:
    from PySide6.QtWidgets import QStyleOptionSlider

    option = QStyleOptionSlider()
    bar.initStyleOption(option)
    slider = bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, option,
                                        QStyle.SubControl.SC_ScrollBarSlider, bar)
    wide = bar.underMouse() or bar.isSliderDown()
    thickness = WIDE if wide else THIN
    if bar.orientation() == Qt.Orientation.Vertical:
        x = bar.width() - thickness - 2
        return QRect(x, slider.top() + 2, thickness, max(4, slider.height() - 4))
    y = bar.height() - thickness - 2
    return QRect(slider.left() + 2, y, max(4, slider.width() - 4), thickness)


def _in_combo_popup(widget) -> bool:
    from PySide6.QtWidgets import QComboBox

    node = widget
    while node is not None:
        if isinstance(node, QComboBox) or node.inherits("QComboBoxPrivateContainer"):
            return True
        node = node.parentWidget()
    return False


class GlassScrollStyle(QProxyStyle):
    """Overlay scroll bars with a glass handle; everything else is the normal style."""

    def __init__(self, base=None):
        super().__init__(base)
        self._helpers: dict[int, _BarGlass] = {}

    def styleHint(self, hint, option=None, widget=None, data=None):  # noqa: N802 - Qt API spelling
        if hint == QStyle.StyleHint.SH_ScrollBar_Transient:
            return 1                                              # float over the content
        if hint == QStyle.StyleHint.SH_ScrollBar_LeftClickAbsolutePosition:
            return 0
        return super().styleHint(hint, option, widget, data)

    def pixelMetric(self, metric, option=None, widget=None):  # noqa: N802 - Qt API spelling
        if metric == QStyle.PixelMetric.PM_ScrollBarExtent:
            return EXTENT
        if metric == QStyle.PixelMetric.PM_ScrollBarSliderMin:
            return MIN_HANDLE
        if metric == QStyle.PixelMetric.PM_ScrollView_ScrollBarOverlap:
            return EXTENT                                         # the bar lies over the content
        return super().pixelMetric(metric, option, widget)

    def subControlRect(self, control, option, sub, widget=None):  # noqa: N802 - Qt API spelling
        if control == QStyle.ComplexControl.CC_ScrollBar and isinstance(widget, QScrollBar):
            rect = option.rect
            if sub in (QStyle.SubControl.SC_ScrollBarAddLine, QStyle.SubControl.SC_ScrollBarSubLine):
                return QRect()                                    # no arrow buttons
            span = rect.height() if option.orientation == Qt.Orientation.Vertical else rect.width()
            total = option.maximum - option.minimum + option.pageStep
            length = span if total <= 0 else max(MIN_HANDLE, int(span * option.pageStep / max(total, 1)))
            length = min(length, span)
            room = span - length
            value_range = option.maximum - option.minimum
            start = 0 if value_range <= 0 else int(room * (option.sliderPosition - option.minimum) / value_range)
            if option.upsideDown:
                start = room - start
            if option.orientation == Qt.Orientation.Vertical:
                slider = QRect(rect.x(), rect.y() + start, rect.width(), length)
                if sub == QStyle.SubControl.SC_ScrollBarSubPage:
                    return QRect(rect.x(), rect.y(), rect.width(), start)
                if sub == QStyle.SubControl.SC_ScrollBarAddPage:
                    return QRect(rect.x(), slider.bottom() + 1, rect.width(), rect.bottom() - slider.bottom())
            else:
                slider = QRect(rect.x() + start, rect.y(), length, rect.height())
                if sub == QStyle.SubControl.SC_ScrollBarSubPage:
                    return QRect(rect.x(), rect.y(), start, rect.height())
                if sub == QStyle.SubControl.SC_ScrollBarAddPage:
                    return QRect(slider.right() + 1, rect.y(), rect.right() - slider.right(), rect.height())
            if sub == QStyle.SubControl.SC_ScrollBarSlider:
                return slider
            if sub == QStyle.SubControl.SC_ScrollBarGroove:
                return rect
        return super().subControlRect(control, option, sub, widget)

    def hitTestComplexControl(self, control, option, pos, widget=None):  # noqa: N802 - Qt API spelling
        if control == QStyle.ComplexControl.CC_ScrollBar and isinstance(widget, QScrollBar):
            for sub in (QStyle.SubControl.SC_ScrollBarSlider, QStyle.SubControl.SC_ScrollBarSubPage,
                        QStyle.SubControl.SC_ScrollBarAddPage):
                if self.subControlRect(control, option, sub, widget).contains(pos):
                    return sub
            return QStyle.SubControl.SC_None
        return super().hitTestComplexControl(control, option, pos, widget)

    def drawControl(self, element, option, painter, widget=None):  # noqa: N802 - Qt API spelling
        # Drop-down lists: items keep their English text (code reads it) and are translated here.
        if element in (QStyle.ControlElement.CE_MenuItem, QStyle.ControlElement.CE_ItemViewItem) \
                and _in_combo_popup(widget) and getattr(option, "text", ""):
            from app.localization.manager import translate_for_display

            translated = translate_for_display(option.text)
            if translated != option.text:
                option.text = translated
        super().drawControl(element, option, painter, widget)

    def drawComplexControl(self, control, option, painter, widget=None):  # noqa: N802 - Qt API spelling
        if control != QStyle.ComplexControl.CC_ScrollBar or not isinstance(widget, QScrollBar):
            super().drawComplexControl(control, option, painter, widget)
            return
        helper = self._helpers.get(id(widget))
        if helper is None:
            helper = self._helpers[id(widget)] = _BarGlass(widget)
            widget.destroyed.connect(lambda _o=None, key=id(widget): self._helpers.pop(key, None))
            helper.schedule()
        if option.maximum <= option.minimum:
            return
        rect = handle_rect(widget)
        dark = QColor(widget.palette().color(widget.backgroundRole())).lightness() < 128
        style = glass.DARK_STYLE if dark else glass.LIGHT_STYLE
        image = helper.image if helper.image_rect == rect else None
        if helper.image_rect != rect:
            helper.schedule()
        painter.save()
        vertical = widget.orientation() == Qt.Orientation.Vertical
        target = QRectF(rect)
        if vertical:
            # paint_glass draws horizontal capsules: rotate the painter for vertical handles
            painter.translate(target.center())
            painter.rotate(90)
            painter.translate(-target.center())
            target = QRectF(target.center().x() - target.height() / 2, target.center().y() - target.width() / 2,
                            target.height(), target.width())
        glass.paint_glass(painter, target, image, style)
        painter.restore()


_installed = False


def install_glass_scrollbars(app: QApplication) -> None:
    global _installed
    if _installed:
        return
    from PySide6.QtWidgets import QStyleFactory

    base = QStyleFactory.create(app.style().name()) if app.style() is not None else None
    app.setStyle(GlassScrollStyle(base))
    _installed = True
