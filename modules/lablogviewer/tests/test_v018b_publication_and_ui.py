"""v0.18B: publication-style 3D export, YIG export alignment, spin-box arrows."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest


@pytest.fixture(scope="module")
def qapp():
    import os
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_si_axis_scales_units_and_keeps_non_si_units():
    from app.visualization3d.publication_style import si_axis

    frequency = si_axis("Frequency", "Hz", [5.0197e9, 5.0297e9])
    assert frequency.label == "Frequency (GHz)" and frequency.scale == 1e9
    current = si_axis("Average Current", "A", [0.1626, 0.1630])
    assert current.label == "Average Current (mA)" and current.scale == 1e-3
    assert si_axis("S21", "dB", [-40, -10]).label == "S21 (dB)"
    assert si_axis("Index", None, [0, 10]).scale == 1.0


def _stub_renderer(geometry="Surface"):
    x = np.linspace(5.0197e9, 5.0297e9, 60)
    y = np.linspace(0.1626, 0.1630, 40)
    z = -10 - 30 * np.exp(-((x[None, :] - 5.0247e9 - (y[:, None] - 0.1628) * 4e10) / 1.5e6) ** 2)
    grid = SimpleNamespace(x_values=x, y_values=y, z_values=z, x_name="Frequency", x_unit="Hz",
                           y_name="Average Current", y_unit="A", z_name="VNA - S21", z_unit="dB",
                           transform="magnitude_db")
    state = SimpleNamespace(x_rotation=35.0, y_rotation=25.0)
    return SimpleNamespace(
        _geometry_type=geometry, _source_height=grid, _source_color=grid, _secondary_source=None,
        _opacity=0.8, _colormap_name="LabLog BWR", _color_range=None, _projection_enabled=True,
        _projection_opacity=1.0, _reference_mode="custom", _reference_value=-20.0, _z_scale=1.0,
        _waterfall={"height": grid, "color": grid} if geometry == "Waterfall" else None,
        _point_cloud=None, camera_state=lambda: state,
        graph=SimpleNamespace(isOrthoProjection=lambda: False),
    )


@pytest.mark.parametrize("geometry", ["Surface", "Waterfall"])
def test_publication_export_renders_png_and_pdf(qapp, geometry):
    from app.visualization3d.publication_style import render_renderer_publication

    renderer = _stub_renderer(geometry)
    png = render_renderer_publication(renderer, image_format="png", dpi=80)
    assert png.startswith(b"\x89PNG") and len(png) > 10_000
    pdf = render_renderer_publication(renderer, image_format="pdf", dpi=80)
    assert pdf.startswith(b"%PDF")
    # Export never alters the scientific source values.
    assert renderer._source_height.z_values.min() == pytest.approx(-40.0, abs=0.5)


def test_publication_export_requires_a_scene(qapp):
    from app.visualization3d.publication_style import render_renderer_publication

    empty = SimpleNamespace(camera_state=lambda: None)
    with pytest.raises(ValueError):
        render_renderer_publication(empty)


def test_yig_single_pane_export_is_placed_at_the_origin(qapp):
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QWidget
    from app.analysis.yig_fitting.ui.plots import _normalize_surfaces
    from app.gui.plot_export import PaneRenderSurface

    widget = QWidget()
    right_pane = PaneRenderSurface(widget, QRect(520, 310, 500, 300))
    surfaces, size = _normalize_surfaces([right_pane], 1020, 610)
    assert surfaces[0].target == QRect(0, 0, 500, 300)
    assert (size.width(), size.height()) == (500, 300)


def test_spin_box_step_arrows_are_themed_images(qapp):
    import re
    from pathlib import Path
    from app.palette import ARROW
    from app.theme import DARK, LIGHT, _stylesheet

    for colors, variant in ((LIGHT, "light"), (DARK, "dark")):
        urls = re.findall(r'url\("([^"]+)"\)', _stylesheet(colors))
        spin = {Path(url).name for url in urls if "spin_" in url}
        hex_name = ARROW[variant].lstrip("#").lower()
        assert spin == {f"spin_up_{hex_name}.svg", f"spin_down_{hex_name}.svg"}
        assert all(Path(url).is_file() for url in urls if "spin_" in url)


def test_three_d_export_style_setting_defaults_to_publication(tmp_path):
    from app.settings.store import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    assert store.three_d_export_style() == "publication"
    store.set_three_d_export_style("screen")
    assert SettingsStore(tmp_path / "settings.json").three_d_export_style() == "screen"
    with pytest.raises(ValueError):
        store.set_three_d_export_style("poster")


def test_yig_residual_line_follows_plot_appearance(qapp):
    """v0.18D: the neutral Residual line was near-black on dark plots."""
    import numpy as np
    from app.analysis.yig_fitting.ui.analysis_pane import FitPlotWidget
    from app.theme import DARK_PLOT, FOREGROUND_LINE_GID, WHITE_PLOT

    widget = FitPlotWidget()
    for pane in widget.panes:
        pane.apply_scientific_plot_appearance(DARK_PLOT)
    f = np.linspace(5.0197e9, 5.0297e9, 101)
    s = 0.5 * np.exp(1j * np.linspace(0, 3, 101))
    widget.plot(f, s, s * 0.9, np.abs(s) * 0.9)
    residual = [line for line in widget.panes[5].axis.get_lines() if line.get_gid() == FOREGROUND_LINE_GID]
    assert residual and residual[0].get_color() == DARK_PLOT.text
    with widget.panes[5].temporary_scientific_plot_appearance(WHITE_PLOT):
        assert residual[0].get_color() == WHITE_PLOT.text
    assert residual[0].get_color() == DARK_PLOT.text
    widget.close()
