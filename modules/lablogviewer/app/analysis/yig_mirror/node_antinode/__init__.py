"""Physical model-derived and empirical coarse location detectors."""

from .physical import analyze_physical_positions
from .coarse import coarse_detection_points, run_coarse_detector

__all__ = ["analyze_physical_positions", "coarse_detection_points", "run_coarse_detector"]
