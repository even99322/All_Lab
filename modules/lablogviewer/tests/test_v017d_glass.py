"""Focused tests for decorative glass without scientific or action changes."""

from __future__ import annotations

import json

import numpy as np
import pytest


@pytest.fixture(scope="module")
def qapp():
    import os
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _two_tone_backdrop(width=260, height=100):
    pixels = np.full((height, width, 4), 255, np.uint8)
    pixels[:, :width // 2, :3] = (15, 80, 180)
    pixels[:, width // 2:, :3] = (210, 60, 30)
    pixels[::6, :, :3] = 0          # fine stripes make frost measurable
    return pixels


def test_renderer_refracts_capsule_and_preserves_dimensions(qapp):
    from PySide6.QtCore import QRect
    from app.gui.glass import GlassMaterial, GlassRenderer, _pixels

    backdrop = _two_tone_backdrop()
    rect = QRect(40, 30, 180, 40)
    output = GlassRenderer().refract(backdrop, rect, GlassMaterial(frost=0.0), 1.0)
    assert (output.width(), output.height()) == (180, 40)
    result = _pixels(output)
    source = backdrop[30:70, 40:220]
    # The flat interior transmits the backdrop; the rim refracts and reflects it.
    assert not np.array_equal(result[..., :3], source[..., :3])
    assert not np.array_equal(result[20, 20, :3], result[20, 160, :3])


def test_frost_blurs_without_rebuilding_the_kernel(qapp):
    from PySide6.QtCore import QRect
    from app.gui.glass import GlassMaterial, GlassRenderer, _pixels

    renderer = GlassRenderer()
    backdrop = _two_tone_backdrop()
    rect = QRect(40, 30, 180, 40)
    clear = _pixels(renderer.refract(backdrop, rect, GlassMaterial(frost=0.0), 1.0))
    kernels = len(renderer._kernels)
    frosted = _pixels(renderer.refract(backdrop, rect, GlassMaterial(frost=1.0), 1.0))
    assert len(renderer._kernels) == kernels
    interior = (slice(12, 28), slice(40, 140))
    assert frosted[interior][..., :3].std() < clear[interior][..., :3].std()


def test_bevel_is_clamped_to_capsule_height(qapp):
    from app.gui.glass import GlassMaterial

    kernel = GlassMaterial(thickness=1.0).build_kernel(300, 40, 20.0, 2.0)
    assert kernel.h == 40 and kernel.w == 300


def test_settings_store_validates_and_persists_glass_dials(tmp_path):
    from app.settings.store import SettingsStore

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"general": {"glass_thickness": 7, "glass_frost": "x"}}), encoding="utf-8")
    store = SettingsStore(path)
    assert store.glass_material() == (0.5, 0.25)
    store.set_glass_material(0.8, 0.1)
    assert SettingsStore(path).glass_material() == (0.8, 0.1)
    with pytest.raises(ValueError):
        store.set_glass_material(1.5, 0.1)


def test_browser_glass_capsules_dials_and_fallback(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow
    from app.settings.store import SettingsStore
    from app.theme import ThemeManager

    previous_palette = qapp.palette()
    store = SettingsStore(tmp_path / "settings.json")
    manager = ThemeManager(qapp, store, qapp)
    browser = BrowserWindow(
        star_store=StarStore(tmp_path / "stars.json"), theme_manager=manager,
    )
    try:
        surface = browser.browser_glass_toolbar
        # v0.19A: the Network Workspace bar is the very first row, the glass toolbar next.
        assert browser.centralWidget().layout().itemAt(0).widget() is browser.network_bar
        assert browser.centralWidget().layout().itemAt(1).widget() is surface
        assert len(browser._toolbar_segments) == 3
        database_row, data_row, view_row = browser._toolbar_segments
        assert database_row.itemAt(0).widget() is browser.open_button
        assert data_row.itemAt(0).widget() is browser.star_button
        assert view_row.itemAt(view_row.count() - 1).widget() is browser.annotation_button
        assert view_row.itemAt(view_row.count() - 2).widget() is browser.debackground_button
        assert not browser.open_button.icon().isNull()
        browser.resize(1400, 800)
        browser.show()
        qapp.processEvents()

        # Open/Reload | Star..Back | dials | Appearance/De-background.
        rects = surface.capsule_rects()
        assert len(rects) == 4
        assert all(a.right() < b.left() for a, b in zip(rects, rects[1:]))
        surface._refresh_glass()
        assert len(surface._glass_images) == 4

        thickness = browser.glass_sliders["thickness"]
        frost = browser.glass_sliders["frost"]
        assert thickness.isEnabled() and frost.isEnabled()
        seen = []
        manager.glass_material_changed.connect(lambda t, f, final: seen.append((t, f, final)))
        thickness.setValue(80)
        assert manager.glass_material == (0.8, 0.25)
        assert store.glass_material() == (0.5, 0.25)     # not persisted mid-drag
        assert seen[-1] == (0.8, 0.25, False)
        QTest.mouseClick(frost, Qt.MouseButton.LeftButton, pos=frost.rect().center())
        assert seen[-1][2] is True
        assert store.glass_material() == manager.glass_material
        assert 40 <= frost.value() <= 60

        surface.set_optical_glass_enabled(False)
        qapp.processEvents()
        assert surface._glass_images == {}
        surface._refresh_glass()
        assert surface._glass_images == {}
        assert surface.size().width() > 0
        surface.set_optical_glass_enabled(True)
        manager.set_mode("dark")
        qapp.processEvents()
        surface._refresh_glass()
        assert len(surface._glass_images) == 4
    finally:
        browser.close()
        manager.detach()
        manager.deleteLater()
        qapp.setPalette(previous_palette)
        qapp.processEvents()


def test_runtime_glass_has_only_pyside6_binding():
    from pathlib import Path
    import app.gui.glass as glass

    source = Path(glass.__file__).read_text(encoding="utf-8")
    assert "PyQt6" not in source
    assert "PySide6" in source


def test_optical_failure_keeps_viewer_toolbar_actions(qapp, tmp_path):
    from app.gui.main_window import MainWindow
    from app.settings.store import SettingsStore
    from app.theme import ThemeManager

    previous_palette = qapp.palette()
    manager = ThemeManager(qapp, SettingsStore(tmp_path / "settings.json"), qapp)
    viewer = MainWindow(theme_manager=manager)
    try:
        toolbar = viewer.application_toolbar
        viewer.show()
        qapp.processEvents()
        rects = toolbar.capsule_rects()
        assert len(rects) >= 2        # separators split the glass
        assert all(a.right() < b.left() for a, b in zip(rects, rects[1:]))
        actions_before = tuple(toolbar.actions())

        def fail(*_args, **_kwargs):
            raise RuntimeError("simulated unavailable optical backend")

        toolbar._glass_renderer.refract = fail
        toolbar._refresh_glass()
        assert toolbar._glass_images == {}
        assert tuple(toolbar.actions()) == actions_before
        assert viewer.open_action in toolbar.actions()
        assert viewer.reload_action in toolbar.actions()
        assert not viewer.open_action.icon().isNull()
        assert not viewer.reload_action.icon().isNull()
    finally:
        viewer.close()
        manager.detach()
        manager.deleteLater()
        qapp.setPalette(previous_palette)
        qapp.processEvents()


def test_icon_validity_is_cached_for_large_item_views(qapp, monkeypatch):
    """Qt asks isNull() on every paint; it must not re-parse SVG each time."""
    import app.icons as icons

    icons.icon("show")
    parsed = []
    original = icons.QSvgRenderer
    monkeypatch.setattr(icons, "QSvgRenderer", lambda *a: parsed.append(a) or original(*a))
    shown = icons.icon("show")
    assert shown is icons.icon("show")
    for _ in range(2000):
        assert not shown.isNull()
    assert parsed == []


def test_buttons_use_glass_and_capsule_hover_is_round(qapp):
    from app.theme import DARK, LIGHT, TOOLBAR_ICON_SIZE, _stylesheet

    for colors in (LIGHT, DARK):
        sheet = _stylesheet(colors)
        assert "QPushButton, QToolButton {" in sheet
        assert "qlineargradient" in sheet and "border-radius: 10px" in sheet
        # Buttons inside glass capsules hover as a circle, not a rectangle.
        assert f"border-radius: {TOOLBAR_ICON_SIZE // 2 + 4}px" in sheet
        assert "QWidget#browserGlassToolbar QToolButton:hover" in sheet


def test_light_glass_has_visible_bevel_on_uniform_backdrop(qapp):
    from PySide6.QtCore import QRect
    from app.gui.glass import LIGHT_STYLE, GlassMaterial, GlassRenderer, _pixels

    backdrop = np.full((100, 260, 4), 255, np.uint8)
    backdrop[..., :3] = (247, 244, 238)          # Light theme window color
    rect = QRect(40, 30, 180, 40)
    plain = _pixels(GlassRenderer().refract(backdrop, rect, GlassMaterial(frost=0.0), 1.0))
    shaded = _pixels(GlassRenderer().refract(backdrop, rect, GlassMaterial(frost=0.0), 1.0,
                                             edge_shade=LIGHT_STYLE.edge_shade))
    near_rim, centre = shaded[20, 8, :3].astype(int), shaded[20, 90, :3].astype(int)
    assert np.array_equal(plain[20, 90, :3], shaded[20, 90, :3])
    assert near_rim.sum() < centre.sum()


def test_tree_branch_arrows_are_theme_colored_files(qapp):
    """Light-theme tree arrows were invisible with the app stylesheet."""
    import re
    from pathlib import Path
    from app.palette import ARROW
    from app.theme import DARK, LIGHT, _stylesheet

    for colors, stroke in ((LIGHT, ARROW["light"]), (DARK, ARROW["dark"])):
        urls = re.findall(r'url\("([^"]+)"\)', _stylesheet(colors))
        branch = [url for url in urls if "branch_" in url]
        hex_name = stroke.lstrip("#").lower()
        assert {Path(url).name for url in branch} == {f"branch_closed_{hex_name}.svg", f"branch_open_{hex_name}.svg"}
        for url in branch:
            assert stroke in Path(url).read_text(encoding="utf-8")
