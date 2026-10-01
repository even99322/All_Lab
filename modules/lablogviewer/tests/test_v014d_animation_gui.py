"""Viewer integration for immutable, 1D scientific animation snapshots."""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.real_data import BIG_FILE, FLUX_FILE

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False


pytestmark = pytest.mark.skipif(not PYSIDE_AVAILABLE or not BIG_FILE.exists(), reason="Qt or real data unavailable")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_animation_action_and_snapshot_use_original_trace_ids_without_live_viewer_mutation(qapp):
    from app.core.animation_export import MP4, SELECTED_TRACES, AnimationOptions
    from app.gui.main_window import MainWindow
    from app.gui.animation_renderer import AnimationRenderSession

    viewer = MainWindow()
    try:
        viewer.resize(1000, 700)
        viewer.show()
        viewer.open_file(str(BIG_FILE))
        qapp.processEvents()
        assert viewer.export_animation_action.isEnabled()
        viewer.log_entries.restore_selection((0, 20, 300), active=20, visible=(0, 20, 300))
        ids, panes, size = viewer._animation_snapshot(AnimationOptions(MP4, trace_scope=SELECTED_TRACES))
        assert ids == (0, 20, 300)
        original = panes[0].frames[20].copy()
        viewer.log_entries.select_row(300)
        qapp.processEvents()
        assert (panes[0].frames[20] == original).all()
        renderer = AnimationRenderSession(panes, size)
        try:
            image = renderer.render(ids[0], target_size=(1280, 720))
            assert (image.width(), image.height()) == (1280, 720)
        finally:
            renderer.close()
    finally:
        viewer.close()


@pytest.mark.skipif(not FLUX_FILE.exists(), reason="Real Flux fixture unavailable")
def test_animation_snapshot_keeps_flux_grid_orientation_and_fixed_global_color_range(qapp):
    from app.core.animation_export import GIF, AnimationOptions
    from app.gui.animation_renderer import AnimationRenderSession
    from app.gui.main_window import MainWindow

    viewer = MainWindow()
    try:
        viewer.resize(1000, 700)
        viewer.show()
        viewer.open_file(str(FLUX_FILE))
        viewer.mode_combo.setCurrentIndex(1)
        qapp.processEvents()
        trace_ids, panes, size = viewer._animation_snapshot(AnimationOptions(GIF))
        assert len(trace_ids) > 1
        assert len(panes) == 1 and panes[0].kind == "2d"
        assert panes[0].grid is not None
        assert panes[0].reveal_trace_ids == trace_ids
        assert panes[0].grid.z_values.shape == viewer.plot_2d_widget._grid.z_values.shape
        assert panes[0].z_range is not None
        renderer = AnimationRenderSession(panes, size)
        try:
            image = renderer.render(trace_ids[0], target_size=(1280, 720))
            assert (image.width(), image.height()) == (1280, 720)
            first_grid = renderer.widgets[0]._grid
            assert np.array_equal(first_grid.z_values[trace_ids[0]], panes[0].grid.z_values[trace_ids[0]])
            assert np.isnan(first_grid.z_values[trace_ids[1]]).all()
            renderer.render(trace_ids[1], target_size=(1280, 720))
            second_grid = renderer.widgets[0]._grid
            assert np.array_equal(second_grid.z_values[trace_ids[1]], panes[0].grid.z_values[trace_ids[1]])
        finally:
            renderer.close()
    finally:
        viewer.close()


def test_background_mp4_worker_keeps_the_container_suffix_on_its_atomic_temp_file(qapp, tmp_path):
    from PySide6.QtGui import QImage
    from app.gui.animation_renderer import AnimationEncodeWorker

    target = tmp_path / "analysis.mp4"
    worker = AnimationEncodeWorker(target, "MP4", fps=30, duration=1 / 30)
    assert worker.temporary.name == "analysis.part.mp4"
    image = QImage(64, 64, QImage.Format.Format_ARGB32)
    image.fill(0xFF224466)
    worker.start()
    assert worker.submit(image)
    worker.finish()
    assert worker.finished.wait(15)
    assert worker.error is None
    assert target.read_bytes()[4:8] == b"ftyp"


def test_background_gif_worker_publishes_valid_animated_gif_with_every_source_frame(qapp, tmp_path):
    from PIL import Image
    from PySide6.QtGui import QImage
    from app.gui.animation_renderer import AnimationEncodeWorker

    target = tmp_path / "scan.gif"
    worker = AnimationEncodeWorker(target, "GIF", fps=100, duration=0.01, expected_frames=3)
    worker.start()
    for color in (0xFFFF0000, 0xFF00FF00, 0xFF0000FF):
        image = QImage(64, 64, QImage.Format.Format_ARGB32)
        image.fill(color)
        assert worker.submit(image)
    worker.finish()
    assert worker.finished.wait(15)
    assert worker.error is None
    assert target.read_bytes()[:6] in (b"GIF87a", b"GIF89a")
    assert worker.validated_frame_count == 3
    with Image.open(target) as gif:
        assert gif.format == "GIF"
        assert gif.is_animated
        assert gif.n_frames == 3
        colors = []
        for index in range(3):
            gif.seek(index)
            assert gif.info.get("duration") == 10
            colors.append(gif.convert("RGB").getpixel((32, 32)))
        assert colors[0][0] > 220
        assert colors[1][1] > 180
        assert colors[2][2] > 220
    assert worker.frames_written == 3


def test_closing_source_viewer_cancels_animation_and_cleans_temp_file(qapp, tmp_path):
    from app.core.animation_export import GIF, AnimationOptions
    from app.gui.main_window import MainWindow

    viewer = MainWindow()
    viewer.resize(1000, 700)
    viewer.show()
    viewer.open_file(str(BIG_FILE))
    qapp.processEvents()
    options = AnimationOptions(GIF)
    ids, panes, size = viewer._animation_snapshot(options)
    output = tmp_path / "interrupted.gif"
    viewer._start_animation_export(output, options, ids, panes, size)
    worker = viewer._animation_jobs[0]["worker"]
    viewer.close()
    assert not viewer._animation_jobs
    assert worker.finished.wait(15)
    assert not worker.temporary.exists()
    assert not output.exists()
