"""Focused regression for the v0.16D correction pass."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.axis_preset_store import AxisPreset, AxisPresetStore, AxisRef
from app.core.overlay_store import OverlayStore, SavedOverlay
from app.core.tag_store import TagStore
from app.visualization3d.renderer import build_surface_gradient, map_surface_colors
from app.visualization3d.mapping import PointCloudData


def test_unicode_tags_round_trip_without_locale_or_false_backup(tmp_path, monkeypatch):
    path = tmp_path / "tags.json"
    labels = ("Mirror", "測試資料", "テスト", "RSMEP_最佳資料")
    store = TagStore(path, legacy_paths=[])
    assert "Mirror" in store.list_tags()
    for label in labels[1:]:
        assert store.create_tag(label)

    original_read = Path.read_text

    def require_utf8(self, *args, **kwargs):
        if self == path and kwargs.get("encoding") not in ("utf-8", "utf-8-sig"):   # explicit UTF-8, never the locale
            raise UnicodeDecodeError("cp950", b"\xe3", 0, 1, "simulated locale failure")
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", require_utf8)
    reloaded = TagStore(path, legacy_paths=[])
    assert all(label in reloaded.list_tags() for label in labels)
    assert not path.with_suffix(".json.bak").exists()


def test_legacy_tag_and_other_json_stores_read_unicode_utf8(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"available_tags": ["測試資料", "テスト"]},
                                 ensure_ascii=False), encoding="utf-8")
    axis_path = tmp_path / "axis.json"
    overlay_path = tmp_path / "overlays.json"
    axis = AxisPreset("RSMEP_最佳資料", AxisRef("x", "step", "x"),
                      AxisRef("y", "log_scalar", "y"))
    AxisPresetStore(axis_path).save(axis, "data-a")
    OverlayStore(overlay_path).save("data-a", SavedOverlay("測試資料", [], [], 0))

    original_read = Path.read_text

    def require_utf8(self, *args, **kwargs):
        if self in {legacy, axis_path, overlay_path}:
            assert kwargs.get("encoding") in ("utf-8", "utf-8-sig")      # explicit UTF-8, never the locale
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", require_utf8)
    migrated = TagStore(tmp_path / "tags.json", legacy_paths=[legacy])
    assert "測試資料" in migrated.list_tags()
    assert "テスト" in migrated.list_tags()
    assert AxisPresetStore(axis_path).get("RSMEP_最佳資料", "data-a") == axis
    assert OverlayStore(overlay_path).get("data-a", "測試資料").name == "測試資料"


def test_actual_corrupt_tag_file_still_creates_backup(tmp_path):
    path = tmp_path / "tags.json"
    path.write_text("{bad json", encoding="utf-8")
    with pytest.raises(Exception):
        TagStore(path, legacy_paths=[])
    assert path.with_suffix(".json.bak").read_bytes() == b"{bad json"


@pytest.mark.parametrize("opacity", [1.0, 0.75, 0.5, 0.25])
def test_surface_opacity_changes_only_alpha(opacity):
    values = np.array([[-2.0, 0.0, 2.0]])
    opaque = map_surface_colors(values, "LabLog BWR", (-2.0, 2.0))
    rendered = map_surface_colors(values, "LabLog BWR", (-2.0, 2.0), opacity=opacity)
    assert np.array_equal(rendered[..., :3], opaque[..., :3])
    assert np.all(np.abs(rendered[..., 3].astype(int) - round(255 * opacity)) <= 1)
    gradient = build_surface_gradient("LabLog BWR", (-2.0, 2.0), (-2.0, 2.0), opacity=opacity)
    colors = [gradient.stops()[index][1] for index in (0, len(gradient.stops()) // 2, -1)]
    assert all(abs(color.alpha() - round(255 * opacity)) <= 1 for color in colors)
    assert len({(color.red(), color.green(), color.blue()) for color in colors}) == 3


def test_3d_window_leaves_viewer_multi_pane_layout_untouched(tmp_path):
    """v0.18B: 3D is a separate window, so the Viewer keeps its pane layout."""
    from PySide6.QtWidgets import QApplication
    from app.gui.main_window import MainWindow
    from app.gui.multi_pane import TWO_SIDE
    from tests.real_data import BIG_FILE

    if not BIG_FILE.exists():
        pytest.skip("Real Viewer fixture unavailable")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "axis2.json"),
                        overlay_store=OverlayStore(tmp_path / "overlay2.json"))
    window.show()
    window.open_file(str(BIG_FILE))
    window.pane_layout_combo.setCurrentText(TWO_SIDE)
    app.processEvents()
    assert not window.multi_pane_splitter.isHidden()
    three_d = window.open_3d_window()
    app.processEvents()
    assert three_d.isVisible()
    assert window.surface_opacity_slider.isEnabled()
    assert window.surface_b_opacity_slider.isEnabled()
    assert "exported scene" in window.surface_opacity_slider.toolTip().lower()
    assert window.pane_layout_combo.currentText() == TWO_SIDE
    assert not window.multi_pane_splitter.isHidden()
    assert not window.pane_controls_host.isHidden()
    three_d.close()
    window.close()
    app.processEvents()


@pytest.mark.parametrize("coordinates", [
    np.array([[0.0, 1.0, 2.0]]),
    np.array([[0.0, 1.0, np.inf], [1.0, 2.0, 3.0]]),
    np.zeros((20_001, 3)),
    np.array([[0 + 1j, 1, 2], [1, 2, 3]]),
])
def test_invalid_trajectory_is_rejected_before_native_graph_creation(coordinates):
    from app.visualization3d.renderer import SurfaceRenderer

    cloud = PointCloudData(coordinates, np.zeros(len(coordinates)),
                           np.arange(len(coordinates)), (len(coordinates),))

    class NoNativeGraph:
        def set_geometry_type(self, _geometry):
            raise AssertionError("Invalid data reached the native Qt graph")

    with pytest.raises(ValueError):
        SurfaceRenderer.set_point_cloud(
            NoNativeGraph(), cloud, geometry="Trajectory",
            axis_labels=(("X", None), ("Y", None), ("Z", None)),
        )


def test_point_update_reuses_native_series_and_does_not_claim_immediate_completion():
    import inspect
    from app.visualization3d.renderer import SurfaceRenderer
    from app.gui.main_window import MainWindow

    update = inspect.getsource(SurfaceRenderer.set_point_cloud)
    viewer_update = inspect.getsource(MainWindow._render_current_surface)
    assert "removeSeries" not in update
    assert "deleteLater" not in update
    # v0.18A: keep our own proxies alive; series.dataProxy() returned a
    # mistyped wrapper once the Python-owned proxy was collected.
    assert "self._scatter_proxies[bucket].resetArray" in update
    assert "render update submitted" in update
    assert "rendering completed" not in viewer_update


def test_3d_session_restore_reopens_3d_window_and_keeps_viewer_layout(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from app.core.viewer_display_state_store import ViewerDisplayStateStore
    from app.gui.main_window import MainWindow
    from app.gui.multi_pane import ONE_PANE, TWO_SIDE
    from tests.real_data import BIG_FILE

    if not BIG_FILE.exists():
        pytest.skip("Real Viewer fixture unavailable")
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_rebuild_nd_plot", lambda self: None)
    windows = []
    try:
        for index in range(2):
            window = MainWindow(viewer_display_state_store=ViewerDisplayStateStore(
                tmp_path / f"viewer-{index}.json"
            ))
            window.open_file(str(BIG_FILE))
            windows.append(window)
        first, restored = windows
        first.open_3d_window()
        state = first.session_state()
        assert state is not None
        state["pane"]["layout"] = TWO_SIDE
        assert restored.restore_session_state(state)
        app.processEvents()
        # v0.18B: a saved 3D view reopens the 3D window; the Viewer keeps the
        # restored pane layout because 3D no longer shares its plot area.
        assert restored._three_d_active()
        assert restored.pane_layout_combo.currentText() == TWO_SIDE
    finally:
        for window in windows:
            window.close()
        app.processEvents()
