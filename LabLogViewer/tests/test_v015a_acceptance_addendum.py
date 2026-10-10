from __future__ import annotations

import os
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.axis_preset_store import AxisPreset, AxisPresetStore, AxisRef
from tests.real_data import BIG_FILE, FLUX_FILE, SMALL_FILE


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_axis_preset_update_rename_delete_are_data_scoped_and_restart_safe(tmp_path):
    path = tmp_path / "axis-presets.json"
    store = AxisPresetStore(path)
    axis_x = AxisRef("Frequency", "trace_axis", "S21")
    axis_y = AxisRef("VNA - S21", "vector_channel", "S21")
    store.save(AxisPreset("Magnitude", axis_x, axis_y, "Magnitude", db=True), "data-a")
    store.save(AxisPreset("Phase", axis_x, axis_y, "Phase"), "data-b")

    updated = AxisPreset("Magnitude", axis_x, axis_y, "Phase", unwrap=True)
    assert store.update("Magnitude", updated, "data-a")
    assert store.get("Magnitude", "data-a").transform_name == "Phase"
    assert store.get("Magnitude", "data-b") is None
    assert store.rename("Magnitude", "Unwrapped Phase", "data-a")

    restarted = AxisPresetStore(path)
    assert restarted.get("Unwrapped Phase", "data-a").unwrap
    assert restarted.get("Phase", "data-b") is not None
    assert restarted.delete("Unwrapped Phase", "data-a")
    assert restarted.get("Phase", "data-b") is not None


def test_axis_preset_failed_writes_restore_the_previous_memory_state(tmp_path, monkeypatch):
    store = AxisPresetStore(tmp_path / "axis-presets.json")
    axis_x = AxisRef("Frequency", "trace_axis", "S21")
    axis_y = AxisRef("VNA - S21", "vector_channel", "S21")
    original = AxisPreset("Original", axis_x, axis_y, "Magnitude")
    store.save(original, "data-a")

    def fail_write():
        raise OSError("simulated read-only state directory")

    monkeypatch.setattr(store, "_save", fail_write)
    with pytest.raises(OSError):
        store.update("Original", AxisPreset("Original", axis_x, axis_y, "Phase"), "data-a")
    assert store.get("Original", "data-a") == original
    with pytest.raises(OSError):
        store.rename("Original", "Renamed", "data-a")
    assert store.get("Original", "data-a") == original
    with pytest.raises(OSError):
        store.delete("Original", "data-a")
    assert store.get("Original", "data-a") == original


def _choose_combo_item(qapp, combo, index):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    assert combo.isEnabled()
    combo.showPopup()
    qapp.processEvents()
    model_index = combo.model().index(index, combo.modelColumn())
    rect = combo.view().visualRect(model_index)
    assert rect.isValid() and not rect.isEmpty()
    QTest.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    qapp.processEvents()
    assert combo.currentIndex() == index


@pytest.mark.skipif(not SMALL_FILE.exists(), reason="Real Labber sample file is unavailable.")
def test_axis_transform_and_axis_preset_controls_are_interactive(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QInputDialog
    from app.gui.main_window import MainWindow

    store = AxisPresetStore(tmp_path / "axis.json")
    window = MainWindow(axis_preset_store=store)
    window.resize(1280, 820)
    window.open_file(str(SMALL_FILE))
    window.show()
    qapp.processEvents()
    try:
        vector_index = next(
            index for index in range(window.y_combo.count())
            if (candidate := window.y_combo.itemData(index)) is not None
            and candidate.source == "vector_channel" and candidate.is_complex
        )
        _choose_combo_item(qapp, window.y_combo, vector_index)
        assert window.transform_combo.isEnabled()
        phase_index = window.transform_combo.findText("Phase")
        assert phase_index >= 0
        _choose_combo_item(qapp, window.transform_combo, phase_index)
        assert window.transform_combo.currentText() == "Phase"

        monkeypatch.setattr(
            QInputDialog, "getText",
            staticmethod(lambda *args, **kwargs: ("Acceptance axis", True)),
        )
        window.save_transform_button.click()
        assert store.get("Acceptance axis", window.experiment.data_identity) is not None
        assert window.update_axis_preset_button.isEnabled()
        assert window.rename_axis_preset_button.isEnabled()
        assert window.delete_axis_preset_button.isEnabled()

        real_index = window.transform_combo.findText("Real")
        _choose_combo_item(qapp, window.transform_combo, real_index)
        window.update_axis_preset_button.click()
        saved = store.get("Acceptance axis", window.experiment.data_identity)
        assert saved.transform_name == "Real"
        _choose_combo_item(qapp, window.transform_combo,
                           window.transform_combo.findText("Phase"))
        window.update_axis_preset_button.click()
        assert store.get("Acceptance axis", window.experiment.data_identity).transform_name == "Phase"
        _choose_combo_item(qapp, window.axis_preset_combo, 0)
        _choose_combo_item(qapp, window.axis_preset_combo,
                           window.axis_preset_combo.findText("Acceptance axis"))
        assert window.transform_combo.currentText() == "Phase"

        monkeypatch.setattr(
            QInputDialog, "getText",
            staticmethod(lambda *args, **kwargs: ("Renamed axis", True)),
        )
        window.rename_axis_preset_button.click()
        assert store.get("Renamed axis", window.experiment.data_identity) is not None

        monkeypatch.setattr(
            QInputDialog, "getText",
            staticmethod(lambda *args, **kwargs: ("IQ Transform", True)),
        )
        warnings = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            staticmethod(lambda *args, **kwargs: warnings.append(args[2])),
        )
        window.rename_axis_preset_button.click()
        assert store.get("Renamed axis", window.experiment.data_identity) is not None
        assert "reserved" in warnings[-1]

        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
        )
        window.delete_axis_preset_button.click()
        assert store.get("Renamed axis", window.experiment.data_identity) is None

        phase_candidate_index = next(
            index for index in range(window.y_combo.count())
            if (candidate := window.y_combo.itemData(index)) is not None
            and candidate.source == "derived" and candidate.transform_key == "phase_deg"
        )
        _choose_combo_item(qapp, window.y_combo, phase_candidate_index)
        assert window.transform_combo.isEnabled()
        assert window.transform_combo.currentText() == "Phase"
        assert window.unwrap_checkbox.isEnabled()

        magnitude_index = window.transform_combo.findText("Magnitude")
        _choose_combo_item(qapp, window.transform_combo, magnitude_index)
        selected_y = window.y_combo.currentData()
        assert selected_y.source == "derived" and selected_y.transform_key == "magnitude"
        x, y, transform, _ = window._plot_arrays_for_entry(
            window.x_combo.currentData(), selected_y, window.log_entries.current_row(),
            window._current_transform_spec(), apply_formula=False,
        )
        raw = window.cached.get_data(
            selected_y.base_channel, transform="raw", entry_slice=window.log_entries.current_row()
        ).reshape(-1)
        assert np.allclose(y, window._current_transform_spec().apply(raw), equal_nan=True)
        assert transform == "magnitude"
        assert x.size == y.size
    finally:
        window.close()


def test_gif_validation_rejects_wrong_container_and_frame_count(tmp_path):
    from PIL import Image
    from app.core.animation_export import AnimationExportError, validate_gif_output

    png_named_gif = tmp_path / "bad.gif"
    Image.new("RGBA", (32, 32), "red").save(png_named_gif, format="PNG")
    with pytest.raises(AnimationExportError, match="signature"):
        validate_gif_output(png_named_gif)

    animated = tmp_path / "two.gif"
    frames = [Image.new("RGB", (32, 32), color) for color in ("red", "green")]
    frames[0].save(animated, save_all=True, append_images=frames[1:], duration=10, loop=0)
    with pytest.raises(AnimationExportError, match="expected 3"):
        validate_gif_output(animated, expected_frames=3)


def test_pane_frame_uses_compact_2d_layout_without_changing_1d_spacing(qapp):
    from app.gui.multi_pane import PaneFrame

    frame = PaneFrame(1)
    one_d = (frame._frame_layout.contentsMargins().left(), frame._frame_layout.spacing(),
             frame.header.maximumHeight())
    frame.set_mode(1)
    two_d = (frame._frame_layout.contentsMargins().left(), frame._frame_layout.spacing(),
             frame.header.maximumHeight())
    frame.set_mode(0)
    restored = (frame._frame_layout.contentsMargins().left(), frame._frame_layout.spacing(),
                frame.header.maximumHeight())
    assert two_d[0] < one_d[0] and two_d[1] < one_d[1]
    assert restored == one_d
    assert frame.plot_2d.graphics_widget.ci.layout.getContentsMargins() == (0.0, 0.0, 0.0, 0.0)
    frame.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real Labber sample file is unavailable.")
def test_gif_frame_is_redrawn_at_1080p_export_density(qapp):
    from app.core.animation_export import GIF, AnimationOptions, target_frame_size
    from app.gui.animation_renderer import AnimationRenderSession
    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.resize(960, 640)
    window.open_file(str(BIG_FILE))
    window.show()
    qapp.processEvents()
    try:
        trace_ids, panes, logical_size = window._animation_snapshot(AnimationOptions(GIF))
        target = target_frame_size(logical_size.width(), logical_size.height(), "1080p")
        assert target[1] == 1080 and target[0] >= 1080
        renderer = AnimationRenderSession(panes, logical_size)
        try:
            image = renderer.render(trace_ids[0], target_size=target)
            assert (image.width(), image.height()) == target
            assert image.sizeInBytes() >= target[0] * target[1] * 4
            plot_item = renderer.widgets[0].plot_widget.getPlotItem()
            assert plot_item.titleLabel.geometry().height() > 0
        finally:
            renderer.close()
    finally:
        window.close()


@pytest.mark.skipif(not FLUX_FILE.exists(), reason="Real Flux sample file is unavailable.")
def test_layout_rebuild_auto_ranges_each_2d_pane_but_active_pane_switch_does_not(qapp, tmp_path):
    from PySide6.QtTest import QTest
    from app.core.axis_preset_store import AxisPresetStore
    from app.gui.main_window import MainWindow
    from app.gui.multi_pane import FOUR_PANES, ONE_PANE, THREE_PANES, TWO_SIDE

    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "axis.json"))
    window.resize(1400, 900)
    window.open_file(str(FLUX_FILE))
    window.mode_combo.setCurrentIndex(1)
    window.show()
    qapp.processEvents()
    try:
        grid = window.plot_2d_widget._grid
        assert grid is not None
        for layout in (TWO_SIDE, THREE_PANES, FOUR_PANES, TWO_SIDE, ONE_PANE):
            _choose_combo_item(
                qapp, window.pane_layout_combo,
                window.pane_layout_combo.findText(layout),
            )
            QTest.qWait(80)
            qapp.processEvents()
            count = window._pane_count(layout)
            if count > 1:
                for pane_id in range(1, count + 1):
                    frame = window._pane_frames[pane_id]
                    pane_grid = frame.plot_2d._grid
                    assert pane_grid is not None
                    ranges = frame.plot_2d.view_box.viewRange()
                    x_values = np.asarray(pane_grid.x_values)
                    y_values = np.asarray(pane_grid.y_values)
                    assert ranges[0][0] <= x_values.min() and ranges[0][1] >= x_values.max()
                    assert ranges[1][0] <= y_values.min() and ranges[1][1] >= y_values.max()

        _choose_combo_item(
            qapp, window.pane_layout_combo,
            window.pane_layout_combo.findText(FOUR_PANES),
        )
        QTest.qWait(100)
        frame = window._pane_frames[1]
        grid = frame.plot_2d._grid
        x_center = float(np.asarray(grid.x_values).mean())
        y_center = float(np.asarray(grid.y_values).mean())
        frame.plot_2d.view_box.setRange(
            xRange=(x_center - 0.05, x_center + 0.05),
            yRange=(y_center - 0.05, y_center + 0.05), padding=0,
        )
        before = frame.plot_2d.view_box.viewRange()
        window._activate_pane(2)
        qapp.processEvents()
        after = frame.plot_2d.view_box.viewRange()
        assert np.allclose(before[0], after[0])
        assert np.allclose(before[1], after[1])
    finally:
        window.close()
