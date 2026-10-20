"""v1.0.9: Labber-style 2D cuts (X top, Y right, read-out box, contrast panel left), Labber auto range,
bottom-right update notification."""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _grid():
    from app.core.data_model import Grid2DData

    x = np.linspace(0.0, 9.0, 10)
    y = np.linspace(100.0, 104.0, 5)
    z = 1000.0 + x[None, :] + 10.0 * y[:, None]
    return Grid2DData(x_values=x, y_values=y, z_values=z, x_name="Frequency", x_unit="GHz",
                      y_name="Current", y_unit="mA", z_name="S21", z_unit="dB", transform="raw")


def test_labber_layout_and_readout(qapp):
    from PySide6.QtCore import Qt

    from app.gui.plot_2d_widget import Plot2DWidget

    w = Plot2DWidget()
    w.resize(1100, 750)
    w.show()
    w.plot(_grid())
    w.set_cross_section_enabled(True)
    c = w._cross
    qapp.processEvents()
    ci = w.graphics_widget.ci
    pos = {id(item): cell for item, cell in ((it, ci.items[it]) for it in ci.items)}
    assert pos[id(c.x_cut_plot)][0] == (0, 0) and pos[id(w.plot_item)][0] == (1, 0)
    assert pos[id(c.y_cut_plot)][0] == (1, 1)                              # Y cut on the RIGHT
    assert not c.y_cut_plot.getViewBox().xInverted()
    assert c.panel.isVisible() and c.panel.parent() is w.left_panel_host     # contrast at the LEFT
    c.set_line_position(7.0, 103.0)
    text = c.readout.textItem.toPlainText()
    assert text.splitlines() == ["z: 2037 dB", "y: 103 mA", "x: 7 GHz"]
    assert c.readout.pos().x() == 7.0 and c.readout.pos().y() == 103.0
    assert c.v_line.pen.color().name() == "#ffffff" and c.v_line.pen.style() == Qt.PenStyle.SolidLine   # solid white
    c.full_range()
    assert c.contrast() == (2000.0, 2049.0)
    w.set_cross_section_enabled(False)
    assert not w.left_panel_host.isVisible() and not c.readout.isVisible()
    w.close()


def test_auto_range_drops_outliers_like_labber():
    from app.gui.plot_2d_widget import robust_color_limits

    rng = np.random.default_rng(3)
    z = rng.normal(-25.0, 10.0, 100_000)
    z[rng.choice(z.size, 300, replace=False)] = rng.choice([-6e4, 6e4], 300)   # d/dx noise spikes
    low, high = robust_color_limits(z)
    assert -60 < low < -40 and -10 < high < 10                              # not squeezed by ±6e4


def test_update_toast_bottom_right(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QGuiApplication

    from app.core import notifications
    from app.gui import update_toast

    monkeypatch.setattr(notifications, "store_path", lambda: tmp_path / "n.json")
    opened = []
    monkeypatch.setattr(update_toast.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString())))
    loc = type("L", (), {"language": "zh_TW", "text": staticmethod(lambda k: {
        "toast.update_title": "讀檔程式 {version} 可以更新", "toast.update_body": "目前是 {current}。",
        "toast.more": "⋯還有 {n} 項", "toast.later": "稍後", "toast.whats_new": "看更新內容",
        "toast.download": "下載", "update.now": "立即更新"}.get(k, k))})()
    bridge = update_toast._Bridge(loc)
    try:
        details = {"version": "9.9.9", "items": [{"category": "new", "zh": f"項目 {i}"} for i in range(6)],
                   "install": {"url": "http://hub/llv.exe", "platform": "linux"}}
        record = notifications.add("update", {"version": "9.9.9", "n": 6}, details=details, key="update:9.9.9")
        qapp.processEvents()
        toast = bridge.toast
        assert toast is not None and toast.isVisible() and toast.version == "9.9.9"
        area = (toast.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        assert toast.geometry().right() > area.center().x() and toast.geometry().bottom() > area.center().y()
        assert update_toast.pending_update()["id"] == record["id"]
        toast.update_button.click()                       # not an installed build → opens the download
        assert opened == ["http://hub/llv.exe"]
        assert bridge.show_for(record) is None            # once per version
    finally:
        notifications.remove_listener(bridge._listener)
