"""Small memory-bounded cache for current-source 3D display levels."""

from __future__ import annotations

from collections import OrderedDict
from typing import Hashable

from app.visualization3d.data import SurfaceGrid


def surface_grid_bytes(grid: SurfaceGrid) -> int:
    arrays = (
        grid.x_values, grid.y_values, grid.z_values, grid.color_values,
        grid.source_row_indices, grid.source_column_indices,
    )
    return sum(int(array.nbytes) for array in {id(a): a for a in arrays}.values())


class BoundedSurfaceCache:
    def __init__(self, max_bytes: int = 96 * 1024 * 1024):
        self.max_bytes = int(max_bytes)
        if self.max_bytes <= 0:
            raise ValueError("Cache limit must be positive.")
        self._entries: OrderedDict[Hashable, tuple[SurfaceGrid, int]] = OrderedDict()
        self.current_bytes = 0

    def get(self, key: Hashable) -> SurfaceGrid | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        return entry[0]

    def put(self, key: Hashable, grid: SurfaceGrid) -> bool:
        size = surface_grid_bytes(grid)
        old = self._entries.pop(key, None)
        if old is not None:
            self.current_bytes -= old[1]
        if size > self.max_bytes:
            return False
        while self._entries and self.current_bytes + size > self.max_bytes:
            _, (_, removed) = self._entries.popitem(last=False)
            self.current_bytes -= removed
        self._entries[key] = (grid, size)
        self.current_bytes += size
        return True

    def clear(self) -> None:
        self._entries.clear()
        self.current_bytes = 0

    def __len__(self) -> int:
        return len(self._entries)
