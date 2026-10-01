"""Interactive 3D visualization foundation.

The renderer is imported lazily by the Viewer only when its Surface view is
constructed, keeping ordinary Browser and 1D/2D startup independent of 3D.
"""

from .data import (
    MAX_SURFACE_VERTICES, NormalizedAxis, SurfaceGrid, SurfaceMeshData,
    normalize_axis_for_scene, prepare_surface_grid, prepare_surface_mesh,
)
from .state import CameraState3D

__all__ = [
    "MAX_SURFACE_VERTICES", "CameraState3D", "NormalizedAxis", "SurfaceGrid",
    "SurfaceMeshData", "normalize_axis_for_scene", "prepare_surface_grid",
    "prepare_surface_mesh",
]
