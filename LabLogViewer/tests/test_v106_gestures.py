import pyqtgraph as pg
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QNativeGestureEvent, QPointingDevice
from PySide6.QtWidgets import QApplication

from app.gui.plot_gestures import TrackpadGestures


def _gesture(kind, value, widget):
    p = QPointF(widget.width() / 2, widget.height() / 2)
    return QNativeGestureEvent(kind, QPointingDevice.primaryPointingDevice(), 2, p, p, widget.mapToGlobal(p), value, QPointF())


def test_pinch_zoom_and_smart_zoom(qapp=None):
    app = QApplication.instance() or QApplication([])
    view = pg.GraphicsLayoutWidget()
    plot = view.addPlot()
    plot.plot([0, 1, 2], [0, 1, 4])
    view.resize(400, 300)
    view.show()
    box = plot.getViewBox()
    box.setRange(xRange=(0, 1), yRange=(0, 1), padding=0)
    filt = TrackpadGestures()
    viewport = view.viewport()
    before = box.viewRange()[0][1] - box.viewRange()[0][0]
    assert filt.eventFilter(viewport, _gesture(Qt.NativeGestureType.ZoomNativeGesture, 0.5, viewport))
    assert box.viewRange()[0][1] - box.viewRange()[0][0] < before
    assert filt.eventFilter(viewport, _gesture(Qt.NativeGestureType.SmartZoomNativeGesture, 0.0, viewport))
    assert box.autoRangeEnabled()[0]
    view.close()
