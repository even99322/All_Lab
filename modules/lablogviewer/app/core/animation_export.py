"""Scientific animation planning and streaming encoders.

This module is deliberately GUI- and HDF5-free.  The Viewer supplies an
immutable sequence of already-rendered frames; this module validates trace
scope/ranges and encodes those frames without reaching back into live widgets
or experimental files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np


GIF = "GIF"
MP4 = "MP4"
ALL_TRACES = "All Traces"
SELECTED_TRACES = "Selected Traces"
VISIBLE_TRACES = "Visible Traces"
TRACE_SCOPES = (ALL_TRACES, SELECTED_TRACES, VISIBLE_TRACES)
MP4_SPEEDS = (15, 30, 60, 100, 150, 200)
MP4_RESOLUTIONS = ("Current Size", "720p", "1080p")


class AnimationExportError(ValueError):
    """A requested animation cannot be represented safely."""


@dataclass(frozen=True)
class AnimationOptions:
    format: str
    trace_scope: str = ALL_TRACES
    traces_per_second: int = 60
    resolution: str = "1080p"
    show_trace_number: bool = True
    show_sweep_parameter: bool = False

    def validated(self) -> "AnimationOptions":
        if self.format not in (GIF, MP4):
            raise AnimationExportError("Choose GIF or MP4.")
        if self.trace_scope not in TRACE_SCOPES:
            raise AnimationExportError("Choose a valid trace scope.")
        if self.format == MP4 and self.traces_per_second not in MP4_SPEEDS:
            raise AnimationExportError("Choose one of the supported MP4 playback speeds.")
        if self.resolution not in MP4_RESOLUTIONS:
            raise AnimationExportError("Choose a supported animation resolution.")
        return self


def scope_trace_ids(scope: str, total: int, selected: Sequence[int], visible: Sequence[int]) -> tuple[int, ...]:
    """Resolve stable original trace IDs in ascending acquisition order."""
    if total < 1:
        return ()
    candidates = range(total) if scope == ALL_TRACES else (selected if scope == SELECTED_TRACES else visible)
    return tuple(sorted({int(value) for value in candidates if 0 <= int(value) < total}))


def finite_global_range(values: Iterable[np.ndarray]) -> tuple[float, float]:
    """Return a scientific global range, ignoring invalid samples only."""
    low = np.inf
    high = -np.inf
    for item in values:
        array = np.asarray(item, dtype=float).reshape(-1)
        finite = array[np.isfinite(array)]
        if finite.size:
            low = min(low, float(finite.min()))
            high = max(high, float(finite.max()))
    if not np.isfinite(low) or not np.isfinite(high):
        raise AnimationExportError("No finite displayed samples are available for animation.")
    if low == high:
        margin = max(abs(low), 1.0) * 5e-10
        low, high = low - margin, high + margin
    return low, high


GIF_FRAME_DURATION_SECONDS = 0.01


def gif_frame_ids(trace_ids: Sequence[int], *, maximum_frames: int | None = None) -> tuple[int, ...]:
    """Return every in-scope trace in its original forward order.

    ``maximum_frames`` remains accepted for compatibility with callers from
    older releases, but it deliberately has no sampling effect.
    """
    del maximum_frames
    return tuple(int(trace) for trace in trace_ids)


def gif_duration_seconds(frame_count: int) -> float:
    """Total duration at the reliable 10 ms GIF centisecond tick."""
    if frame_count < 1:
        raise AnimationExportError("Animation needs at least one frame.")
    return frame_count * GIF_FRAME_DURATION_SECONDS


def target_frame_size(current_width: int, current_height: int, resolution: str) -> tuple[int, int]:
    """Fit a target video canvas while retaining the scientific aspect ratio."""
    width, height = max(1, int(current_width)), max(1, int(current_height))
    if resolution == "Current Size":
        return width, height
    target_height = 720 if resolution == "720p" else 1080
    target_width = max(2, int(round(width * target_height / height / 2.0)) * 2)
    return target_width, target_height


def encoder_available(format_name: str) -> bool:
    if format_name == GIF:
        try:
            import imageio_ffmpeg  # noqa: F401
            from PIL import Image  # noqa: F401
            return True
        except ImportError:
            return False
    if format_name == MP4:
        try:
            import imageio_ffmpeg  # noqa: F401
            import imageio.v3  # noqa: F401
            return True
        except ImportError:
            return False
    return False


def validate_gif_output(
    path: str | Path, *, expected_frames: int | None = None,
    expected_duration_ms: int | None = None,
) -> int:
    """Independently verify the container and frame count before publishing."""
    target = Path(path)
    try:
        with target.open("rb") as stream:
            signature = stream.read(6)
    except OSError as error:
        raise AnimationExportError(f"Cannot read encoded GIF: {error}") from error
    if signature not in (b"GIF87a", b"GIF89a"):
        raise AnimationExportError("GIF encoder produced a file without a GIF signature.")

    try:
        from PIL import Image

        with Image.open(target) as image:
            if image.format != "GIF":
                raise AnimationExportError("The animation output is not actually a GIF file.")
            frame_count = int(image.n_frames)
            animated = bool(getattr(image, "is_animated", frame_count > 1))
            durations = []
            if expected_duration_ms is not None:
                for index in range(frame_count):
                    image.seek(index)
                    durations.append(int(image.info.get("duration", 0)))
    except AnimationExportError:
        raise
    except Exception as error:
        raise AnimationExportError(f"Cannot validate encoded GIF frames: {error}") from error

    if frame_count < 1:
        raise AnimationExportError("GIF output contains no frames.")
    if expected_frames is not None and frame_count != int(expected_frames):
        raise AnimationExportError(
            f"GIF contains {frame_count} frames; expected {int(expected_frames)}."
        )
    if frame_count > 1 and not animated:
        raise AnimationExportError("GIF output has multiple frames but is not marked animated.")
    if expected_duration_ms is not None and any(
        duration != int(expected_duration_ms) for duration in durations
    ):
        raise AnimationExportError(
            f"GIF frame timing is not the expected {int(expected_duration_ms)} ms tick."
        )
    return frame_count


def encode_gif(frames: Iterable[np.ndarray], destination: str | Path, *, duration_seconds: float,
               expected_frames: int | None = None) -> None:
    """Stream every frame to GIF at a centisecond-accurate frame rate."""
    import imageio_ffmpeg
    del duration_seconds  # Duration is determined by the fixed 10 ms GIF tick.

    iterator = iter(frames)
    try:
        first = np.asarray(next(iterator), dtype=np.uint8)
    except StopIteration as error:
        raise AnimationExportError("Animation needs at least one frame.") from error
    if first.ndim != 3 or first.shape[2] not in (3, 4):
        raise AnimationExportError("GIF frames must be RGB or RGBA images.")
    height, width = first.shape[:2]
    writer = imageio_ffmpeg.write_frames(
        Path(destination), (width, height), pix_fmt_in="rgba" if first.shape[2] == 4 else "rgb24",
        pix_fmt_out="pal8", fps=100, codec="gif", macro_block_size=1,
        output_params=["-loop", "0"],
    )
    try:
        writer.send(None)
        writer.send(np.ascontiguousarray(first))
        written = 1
        for frame in iterator:
            image = np.asarray(frame, dtype=np.uint8)
            if image.shape != first.shape:
                raise AnimationExportError("GIF frames must all have the same dimensions.")
            writer.send(np.ascontiguousarray(image))
            written += 1
    finally:
        writer.close()
    validate_gif_output(
        destination,
        expected_frames=written if expected_frames is None else expected_frames,
        expected_duration_ms=10,
    )


def encode_mp4(frames: Iterable[np.ndarray], destination: str | Path, *, fps: int) -> None:
    """Stream H.264-compatible MP4 frames via imageio-ffmpeg."""
    if fps not in MP4_SPEEDS:
        raise AnimationExportError("Unsupported MP4 playback speed.")
    import imageio.v2 as imageio

    target = Path(destination)
    writer = imageio.get_writer(
        target, format="FFMPEG", mode="I", fps=fps,
        codec="libx264", macro_block_size=2, pixelformat="yuv420p",
    )
    wrote = False
    try:
        for frame in frames:
            writer.append_data(np.asarray(frame, dtype=np.uint8)[..., :3])
            wrote = True
    finally:
        writer.close()
    if not wrote:
        target.unlink(missing_ok=True)
        raise AnimationExportError("Animation needs at least one frame.")
