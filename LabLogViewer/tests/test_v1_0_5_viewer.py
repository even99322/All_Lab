"""v1.0.5: colormap picker (ramps, reversed, custom), Full Range, inline X / Y cuts, Contrast popup, popup i18n."""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.data_model import Grid2DData


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QSettings("CCU QEL", "LabLogViewer").remove("view/custom_colormaps")
    yield


def _grid():
    x = np.linspace(0.0, 9.0, 10)
    y = np.linspace(100.0, 104.0, 5)
    z = y[:, None] * 10 + x[None, :]
    z[2, 3] = 5000.0                                   # one extreme pixel: Auto ignores it, Full keeps it
    return Grid2DData(x_name="X", x_unit="Hz", x_values=x, y_name="Y", y_unit="A", y_values=y,
                      z_name="S21", z_unit=None, z_values=z, transform="magnitude", fixed_dims={})


def test_colormap_names_resolve():
    from app.gui.plot_2d_widget import (
        COLORMAPS, custom_colormap_name, custom_colors, get_colormap, is_known_colormap,
    )

    for name in COLORMAPS:
        assert get_colormap(name).map(np.array([0.0, 1.0]), mode="byte").shape[0] == 2
    low, high = get_colormap("viridis").map(np.array([0.0, 1.0]), mode="byte")[:, :3]
    rlow, rhigh = get_colormap("viridis_r").map(np.array([0.0, 1.0]), mode="byte")[:, :3]
    assert np.array_equal(low, rhigh) and np.array_equal(high, rlow)
    name = custom_colormap_name(["#0000FF", "#ffffff", "#ff0000"])
    assert name == "Custom: #0000ff · #ffffff · #ff0000" and custom_colors(name) == ["#0000ff", "#ffffff", "#ff0000"]
    ends = get_colormap(name).map(np.array([0.0, 0.5, 1.0]), mode="byte")[:, :3].tolist()
    assert ends == [[0, 0, 255], [255, 255, 255], [255, 0, 0]]
    assert is_known_colormap("magma_r") and is_known_colormap(name) and not is_known_colormap("nope")
    assert not is_known_colormap("Custom: #12") and custom_colors("Custom: #123456") is None


def test_preview_accepts_new_maps():
    from app.core.quick_preview import _preview_colormap_ok

    assert _preview_colormap_ok("turbo") and _preview_colormap_ok("viridis_r")
    assert _preview_colormap_ok("Custom: #000000 · #ffffff") and not _preview_colormap_ok("rainbow")


def test_colormap_combo_previews_reverse_and_custom(qapp, settings, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from app.gui import colormap_combo as cc
    from app.gui.plot_2d_widget import COLORMAPS

    combo = cc.ColormapCombo()
    assert [combo.itemText(i) for i in range(len(COLORMAPS))] == COLORMAPS
    assert all(not combo.itemIcon(i).isNull() for i in range(len(COLORMAPS)))
    seen = []
    combo.currentTextChanged.connect(seen.append)
    combo.setCurrentText("viridis")
    combo.run_special(cc.REVERSE)
    assert combo.currentText() == "viridis_r" and seen[-1] == "viridis_r"
    assert cc.REVERSE not in seen and cc.CUSTOM not in seen          # the action entries are never "current"
    # a saved view with a custom map restores (findText adds it)
    custom = "Custom: #112233 · #ffffff · #aa0000"
    assert combo.findText(custom) >= 0
    combo.setCurrentText(custom)
    assert combo.currentText() == custom
    # Custom Colors...: the dialog's map is selected and remembered for the next combo
    monkeypatch.setattr(cc.CustomColormapDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(cc.CustomColormapDialog, "name", lambda self: "Custom: #000000 · #00ff00")
    combo.run_special(cc.CUSTOM)
    assert combo.currentText() == "Custom: #000000 · #00ff00"
    other = cc.ColormapCombo()
    assert other.findText("Custom: #000000 · #00ff00") < other._special_start()
    combo.close()
    other.close()


def test_custom_dialog_builds_the_name(qapp, settings):
    from app.gui.colormap_combo import CustomColormapDialog

    dialog = CustomColormapDialog("bwr")
    assert dialog.name() == "Custom: #0000ff · #ffffff · #ff0000"
    dialog.use_middle.setChecked(False)
    assert dialog.name() == "Custom: #0000ff · #ff0000"
    dialog._reverse()
    assert dialog.name() == "Custom: #ff0000 · #0000ff"
    assert not dialog.preview.pixmap().isNull()
    dialog.close()


def test_full_range_and_inline_parts(qapp):
    from app.gui.plot_2d_widget import Plot2DWidget

    widget = Plot2DWidget()
    widget.resize(900, 700)
    widget.show()
    widget.plot(_grid())
    assert widget._z_max < 5000.0 or widget._z_max == 5000.0
    assert widget.full_range_color() == (float(_grid().z_values.min()), 5000.0)
    widget.set_cross_section_enabled(True)
    cross = widget._cross
    ci = widget.graphics_widget.ci
    assert cross.hist_plot not in ci.items and cross.contrast_button.isVisible()
    cross.set_parts(x=True, y=False)
    assert cross.x_cut_plot in ci.items and cross.y_cut_plot not in ci.items
    cross.set_parts(x=False, y=True)
    assert cross.y_cut_plot in ci.items and cross.x_cut_plot not in ci.items
    cross.show_contrast_popup()
    assert cross.contrast_popup.isVisible() and cross.contrast() == (widget._z_min, widget._z_max)
    cross.contrast_popup.hide()
    cross.set_parts(x=False, y=False)
    assert not widget.cross_section_enabled and not cross.contrast_button.isVisible()
    widget.close()


def test_popup_items_are_translated(qapp):
    from PySide6.QtCore import QModelIndex
    from PySide6.QtWidgets import QComboBox, QStyleOptionViewItem

    from app.localization import manager

    combo = QComboBox()
    combo.addItems(["Histogram", "Peak"])
    manager._install_popup_translation(combo)
    delegate = combo.view().itemDelegate()
    option = QStyleOptionViewItem()
    old = manager._ACTIVE
    fake = type("M", (), {"language": "zh_TW", "_translated": staticmethod(lambda t: {"Histogram": "直方圖"}.get(t, t))})
    manager._ACTIVE = fake
    try:
        delegate.initStyleOption(option, combo.model().index(0, 0, QModelIndex()))
        assert option.text == "直方圖" and combo.itemText(0) == "Histogram"
    finally:
        manager._ACTIVE = old
    combo.close()
