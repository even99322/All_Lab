"""Apply the user's "CPU threads" setting to the shared Qt worker pool."""

from __future__ import annotations

from PySide6.QtCore import QThreadPool

from app.core.hdf5_reader import set_decode_threads


def three_d_threads(count: int) -> int:
    """The separate 3D process gets half of the setting (at least 1), so both together stay near it."""
    return max(1, int(count) // 2)


def apply_cpu_threads(count: int) -> None:
    QThreadPool.globalInstance().setMaxThreadCount(max(1, int(count)))
    set_decode_threads(count)
