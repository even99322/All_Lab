"""v0.20 Figure Builder: sine envelope + 3-D sine, 8-handle scaling of YIG/coil, live editors."""

from __future__ import annotations

import json
import os
import zipfile

import pytest
from PySide6.QtCore import QPointF
from shapely.geometry import box

from app.figure_builder.scene import (
    Layer, Scene, Wave, set_wave_peak, set_wave_preset, wave_amplitude_at, wave_peak_amplitudes,
    wave_peak_positions, wave_point_3d,
)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _scene() -> Scene:
    scene = Scene(mode="MS", layers=[Layer("ground", "Ground", box(0, 0, 10, 6)),
                                      Layer("guide", "Guide", box(1, 2, 9, 3))])
    scene.update_levels()
    return scene


def test_peaks_follow_cycles_and_phase():
    wave = Wave(1, 0, 0, 1, 1, cycles=3.0, phase=0.0)
    positions = wave_peak_positions(wave)
    assert len(positions) == 3 and positions[0] == pytest.approx(1 / 12)
    wave.phase = 90.0                         # a peak now sits at the very start
    assert wave_peak_positions(wave)[0] == pytest.approx(0.0, abs=1e-9)


def test_flat_is_default_and_old_projects_load():
    wave = Wave(1, 0, 0, 1, 1)
    assert wave.envelope_preset == "flat" and wave_peak_amplitudes(wave) == [1.0] * len(wave_peak_positions(wave))
    scene = _scene()
    scene.add_wave()
    data = scene.to_dict()
    for row in data["waves"]:
        for key in ("envelope", "envelope_preset", "mode", "p0", "p1", "amplitude"):
            row.pop(key)
    loaded = Scene.from_dict(json.loads(json.dumps(data)))
    assert loaded.waves[0].mode == "screen" and loaded.waves[0].envelope_preset == "flat"


def test_presets_and_dragged_peak():
    wave = Wave(1, 0, 0, 1, 1, cycles=5.0)
    set_wave_preset(wave, "decay")
    amps = wave_peak_amplitudes(wave)
    assert all(a > b for a, b in zip(amps, amps[1:]))
    set_wave_preset(wave, "standing")
    amps = wave_peak_amplitudes(wave)
    assert amps[2] == max(amps) and amps[0] < 0.5
    set_wave_peak(wave, 1, 0.25)
    assert wave.envelope_preset == "custom" and wave_peak_amplitudes(wave)[1] == pytest.approx(0.25)
    assert wave_peak_amplitudes(wave)[2] == pytest.approx(amps[2])
    wave.cycles = 3.0                         # fewer peaks: the custom shape is resampled, not lost
    assert len(wave_peak_amplitudes(wave)) == 3
    assert wave_amplitude_at(wave, 0.0) == pytest.approx(wave_peak_amplitudes(wave)[0])


def test_3d_wave_starts_and_ends_where_asked_and_swings_up():
    scene = _scene()
    wave = scene.add_wave3d()
    wave.p0, wave.p1, wave.amplitude, wave.cycles = [0.0, 1.0, 0.5], [8.0, 4.0, 2.0], 1.5, 2.0
    assert wave_point_3d(wave, 0.0) == pytest.approx((0.0, 1.0, 0.5))
    assert wave_point_3d(wave, 1.0) == pytest.approx((8.0, 4.0, 2.0), abs=1e-9)
    t = wave_peak_positions(wave)[0]
    peak = wave_point_3d(wave, t)
    base = [a + (b - a) * t for a, b in zip(wave.p0, wave.p1)]
    assert peak[2] > base[2] and abs(peak[0] - base[0]) < abs(peak[2] - base[2])


def _drag_target(canvas, rect, handle, factor):
    """Pointer position that scales the item by `factor` when the padded `handle` is grabbed."""
    inner = rect.adjusted(3, 3, -3, -3)
    grab, a, b = canvas._handles(rect)[handle], canvas._handles(inner)[handle], canvas._handles(inner)[
        canvas._OPPOSITE[handle]]
    return QPointF(grab.x() + (a.x() - b.x()) * (factor - 1), grab.y() + (a.y() - b.y()) * (factor - 1))


@pytest.mark.parametrize("handle", ["n", "s", "e", "w", "nw", "ne", "se", "sw"])
@pytest.mark.parametrize("kind", ["yig", "coil"])
def test_item_scales_uniformly_about_the_opposite_side(qapp, handle, kind):
    from app.figure_builder.window import FigureBuilderWindow

    window = FigureBuilderWindow(None)
    window.resize(1000, 700)
    window.show()
    window.set_scene(_scene())
    item = window.add_yig() if kind == "yig" else window.add_coil()
    canvas = window.canvas
    canvas.repaint()
    qapp.processEvents()
    frame = canvas._frame
    rect = canvas.item_rect(item, frame)
    anchor = canvas._handles(rect)[canvas._OPPOSITE[handle]]
    origin = canvas._scale_origin(item, handle)
    radius, pitch, turns = item.radius, getattr(item, "pitch", None), getattr(item, "turns", None)
    canvas.scale_item(item, handle, canvas._handles(rect)[handle], origin)
    assert item.radius == pytest.approx(radius)                        # grabbing a handle changes nothing
    canvas.scale_item(item, handle, _drag_target(canvas, rect, handle, 1.6), origin)
    assert item.radius == pytest.approx(radius * 1.6, rel=1e-6)
    if kind == "coil":
        assert item.pitch == pytest.approx(pitch * 1.6) and item.turns == turns
    new_anchor = canvas._handles(canvas.item_rect(item, frame))[canvas._OPPOSITE[handle]]
    assert (new_anchor.x(), new_anchor.y()) == pytest.approx((anchor.x(), anchor.y()), abs=1.0)
    window.close()


def test_editors_update_live_with_one_undo_step(qapp):
    from app.figure_builder.window import FigureBuilderWindow

    window = FigureBuilderWindow(None)
    window.set_scene(_scene())
    yig = window.add_yig()
    base = len(window._undo)
    spin = window.editors["yig"][1]["radius"][1]
    for value in (0.30, 0.31, 0.32, 0.33):
        spin.setValue(value)
        assert yig.radius == pytest.approx(value)
    assert len(window._undo) == base + 1
    window.undo()
    assert window.scene.yigs[0].radius != pytest.approx(0.33)
    thickness = window.layer_table.cellWidget(0, 3)
    before = len(window._undo)
    for value in (0.1, 0.12, 0.14):
        thickness.setValue(value)
    assert window.scene.layers[-1].thickness == pytest.approx(0.14)
    assert len(window._undo) == before + 1
    window.close()


def test_3d_wave_editor_and_pptx_use_the_same_points(qapp, tmp_path):
    from PySide6.QtCore import QRectF
    from app.figure_builder import render
    from app.figure_builder.pptx_export import export_pptx, slide_shape_summary
    from app.figure_builder.scene import Projection
    from app.figure_builder.window import FigureBuilderWindow

    window = FigureBuilderWindow(None)
    window.set_scene(_scene())
    wave = window.add_wave()
    assert wave.mode == "3d"
    window._set_item("p1z", 3.0)
    assert wave.p1[2] == pytest.approx(3.0)
    window._set_item("envelope_preset", "gaussian")
    assert wave.envelope_preset == "gaussian"
    scene = window.scene
    proj = Projection(scene.camera)
    frame = render.make_frame(scene, QRectF(0, 0, 800, 500), proj)
    pts = render.wave_points(wave, frame, 100, proj)
    assert (pts[0].x(), pts[0].y()) == pytest.approx(
        (frame.point(*proj.project(*wave.p0)).x(), frame.point(*proj.project(*wave.p0)).y()))
    path = export_pptx(scene, tmp_path / "w.pptx")
    assert f"Sine wave {wave.id}" in [n for n, _ in slide_shape_summary(path)]
    window.close()
