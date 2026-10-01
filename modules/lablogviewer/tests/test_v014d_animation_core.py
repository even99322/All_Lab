"""v0.14D scientific animation planning and encoder coverage."""

from __future__ import annotations

import numpy as np
import pytest

from app.core.animation_export import (
    ALL_TRACES, GIF, MP4, MP4_SPEEDS, SELECTED_TRACES, VISIBLE_TRACES,
    AnimationExportError, AnimationOptions, finite_global_range, gif_duration_seconds,
    gif_frame_ids, scope_trace_ids, target_frame_size,
)


def test_scope_preserves_original_non_contiguous_trace_identity_and_order():
    assert scope_trace_ids(ALL_TRACES, 8, (5, 1), (7,)) == tuple(range(8))
    assert scope_trace_ids(SELECTED_TRACES, 1001, (700, 20, 300, 0), ()) == (0, 20, 300, 700)
    assert scope_trace_ids(VISIBLE_TRACES, 10, (), (9, 3, 3, -1, 11)) == (3, 9)


def test_global_range_is_transform_domain_finite_and_not_per_frame():
    low, high = finite_global_range((np.array([np.nan, -100.0, np.inf]), np.array([0.0, -np.inf])))
    assert (low, high) == (-100.0, 0.0)
    with pytest.raises(AnimationExportError):
        finite_global_range((np.array([np.nan, np.inf]),))


@pytest.mark.parametrize("count", (100, 1001, 10000))
def test_gif_keeps_every_trace_and_duration_grows_at_centisecond_tick(count):
    source = tuple(range(count))
    frames = gif_frame_ids(source, maximum_frames=100)
    assert frames == source
    assert len(frames) == count
    assert gif_duration_seconds(count) == pytest.approx(count / 100)


def test_gif_keeps_non_contiguous_scope_order_without_sampling():
    scope = (0, 8, 237, 800, 1000)
    assert gif_frame_ids(scope) == scope


def test_mp4_options_are_fixed_choices_and_target_resolution_is_real_canvas_size():
    assert AnimationOptions(MP4, traces_per_second=60, resolution="1080p").validated()
    with pytest.raises(AnimationExportError):
        AnimationOptions(MP4, traces_per_second=77).validated()
    assert target_frame_size(1000, 500, "720p") == (1440, 720)
    assert target_frame_size(1000, 500, "1080p") == (2160, 1080)
    assert target_frame_size(1000, 500, "Current Size") == (1000, 500)
    assert set(MP4_SPEEDS) == {15, 30, 60, 100, 150, 200}
    assert AnimationOptions(GIF, resolution="1080p").validated().format == GIF


def test_gif_and_mp4_encoders_write_valid_streamed_files(tmp_path):
    from app.core.animation_export import encode_gif, encode_mp4, encoder_available
    from PIL import Image

    frames = [np.full((32, 48, 4), fill_value, dtype=np.uint8) for fill_value in (20, 180)]
    for frame in frames:
        frame[..., 3] = 255
    gif = tmp_path / "scan.gif"
    encode_gif((frames[0], frames[0], frames[1]), gif, duration_seconds=0.1)
    assert gif.read_bytes()[:6] in (b"GIF87a", b"GIF89a")
    with Image.open(gif) as encoded:
        assert encoded.n_frames == 3
        assert [encoded.seek(index) or encoded.info.get("duration") for index in range(3)] == [10, 10, 10]
    assert encoder_available(GIF)
    if encoder_available(MP4):
        mp4 = tmp_path / "analysis.mp4"
        encode_mp4(frames, mp4, fps=30)
        assert mp4.read_bytes()[4:8] == b"ftyp"


def test_high_quality_scale_repaints_small_widgets_at_export_density():
    from PySide6.QtCore import QSize
    from app.gui.plot_export import high_quality_scale

    assert high_quality_scale(QSize(400, 200)) >= 5
    assert high_quality_scale(QSize(1400, 800)) >= 2
