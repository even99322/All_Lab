"""In-memory local extrema analysis for displayed 1D traces."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


LOCAL_MAXIMUM = "Local Maximum"
LOCAL_MINIMUM = "Local Minimum"
PEAK = "Peak"
TROUGH = "Trough"
HALF_PEAK = "Half-Peak"
OPERATIONS = (LOCAL_MAXIMUM, LOCAL_MINIMUM, PEAK, TROUGH, HALF_PEAK)

AUTO_NEARBY = "Auto Nearby Feature"
AROUND_MARK = "Around Selected Mark"
SELECTED_RANGE = "Selected Range"
BETWEEN_MARKS = "Between Two Marks"
VISIBLE_RANGE = "Current Visible X Range"
REGION_MODES = (
    AUTO_NEARBY, AROUND_MARK, SELECTED_RANGE, BETWEEN_MARKS, VISIBLE_RANGE,
)


class AnalysisError(ValueError):
    """A concise, user-presentable local-analysis failure."""


@dataclass(frozen=True)
class AnalysisResult:
    operation: str
    position: int
    sample_index: int
    x: float
    y: float
    prominence: float | None = None
    baseline: float | None = None
    half_level: float | None = None
    left_half_x: float | None = None
    right_half_x: float | None = None

    @property
    def half_level_width(self) -> float | None:
        if self.left_half_x is None or self.right_half_x is None:
            return None
        return self.right_half_x - self.left_half_x


@dataclass(frozen=True)
class AutoRegion:
    """A bounded index interval around one detected local feature."""

    positions: np.ndarray
    start_position: int
    end_position: int
    feature_position: int
    max_radius: int

    def x_bounds(self, x_values) -> tuple[float, float]:
        x = np.asarray(x_values, dtype=float)
        values = x[self.positions]
        return float(np.min(values)), float(np.max(values))


class LocalAnalyzer:
    """Analyzes only explicitly supplied positions in an existing trace."""

    def __init__(self, x, y, source_indices=None):
        self.x = np.asarray(x, dtype=float).reshape(-1)
        self.y = np.asarray(y, dtype=float).reshape(-1)
        if self.x.size != self.y.size:
            raise ValueError("X and Y must have equal length")
        self.source_indices = (
            np.arange(self.x.size, dtype=int) if source_indices is None
            else np.asarray(source_indices, dtype=int).reshape(-1)
        )
        if self.source_indices.size != self.x.size:
            raise ValueError("source_indices must match X and Y")

    def range_positions(self, start: float, end: float) -> np.ndarray:
        low, high = sorted((float(start), float(end)))
        return np.flatnonzero(
            np.isfinite(self.x) & np.isfinite(self.y)
            & (self.x >= low) & (self.x <= high)
        )

    def point_window_positions(self, center_position: int, half_window: int = 20) -> np.ndarray:
        if not 0 <= center_position < self.x.size:
            return np.array([], dtype=int)
        half_window = max(int(half_window), 1)
        start = max(center_position - half_window, 0)
        stop = min(center_position + half_window + 1, self.x.size)
        positions = np.arange(start, stop, dtype=int)
        return positions[np.isfinite(self.x[positions]) & np.isfinite(self.y[positions])]

    def auto_region(
        self, center_position: int, operation: str, max_radius: int | None = None,
    ) -> AutoRegion:
        """Find one nearby feature without walking arbitrarily across the trace.

        Search is capped at five percent of the trace or 500 samples per side
        (with a 20-sample minimum on short traces). Proximity is the primary
        ranking term; prominence only breaks otherwise nearby choices.
        """
        if not 0 <= center_position < self.x.size:
            raise AnalysisError("Select a valid Point Mark for Auto Nearby Feature.")
        if max_radius is None:
            max_radius = min(500, max(20, int(np.ceil(self.x.size * 0.05))))
        max_radius = max(3, min(int(max_radius), max(self.x.size - 1, 3)))
        start = max(0, center_position - max_radius)
        end = min(self.x.size - 1, center_position + max_radius)
        bounded = np.arange(start, end + 1, dtype=int)
        bounded = bounded[np.isfinite(self.x[bounded]) & np.isfinite(self.y[bounded])]
        if bounded.size < 3:
            raise AnalysisError("No nearby feature found.")

        if operation in (LOCAL_MAXIMUM, PEAK):
            candidate_groups = [(True, self._rank_candidates(bounded, maxima=True))]
        elif operation in (LOCAL_MINIMUM, TROUGH):
            candidate_groups = [(False, self._rank_candidates(bounded, maxima=False))]
        elif operation == HALF_PEAK:
            candidate_groups = [
                (True, self._rank_candidates(bounded, maxima=True)),
                (False, self._rank_candidates(bounded, maxima=False)),
            ]
        else:
            raise AnalysisError("Unsupported nearby analysis operation.")

        candidates = [
            (position, prominence, maxima)
            for maxima, group in candidate_groups for position, prominence in group
        ]
        finite_values = self.y[bounded]
        local_step = float(np.median(np.abs(np.diff(finite_values)))) if bounded.size > 1 else 0.0
        scale = max(float(np.max(np.abs(finite_values))), 1.0)
        minimum_prominence = max(local_step * 2.0, np.finfo(float).eps * scale * 64.0)
        candidates = [item for item in candidates if item[1] >= minimum_prominence]
        if not candidates:
            raise AnalysisError("No nearby feature found.")

        strongest = max(item[1] for item in candidates)
        def score(item):
            position, prominence, _ = item
            proximity = abs(position - center_position) / max_radius
            strength_penalty = 1.0 - prominence / strongest if strongest > 0 else 1.0
            return proximity + 0.2 * strength_penalty, abs(position - center_position), -prominence, position

        feature, _prominence, maxima = min(candidates, key=score)
        opposite = self._rank_candidates(bounded, maxima=not maxima)
        left_opposite = [position for position, _ in opposite if position < feature]
        right_opposite = [position for position, _ in opposite if position > feature]
        fallback = max(3, min(max_radius // 2, 100))
        left = max(left_opposite) if left_opposite else max(start, feature - fallback)
        right = min(right_opposite) if right_opposite else min(end, feature + fallback)
        if left >= feature or right <= feature:
            raise AnalysisError("No nearby feature found.")
        positions = np.arange(left, right + 1, dtype=int)
        positions = positions[np.isfinite(self.x[positions]) & np.isfinite(self.y[positions])]
        if positions.size < 3:
            raise AnalysisError("No nearby feature found.")
        return AutoRegion(
            positions=positions, start_position=left, end_position=right,
            feature_position=int(feature), max_radius=max_radius,
        )

    def analyze(self, operation: str, positions) -> AnalysisResult:
        positions = np.asarray(positions, dtype=int).reshape(-1)
        positions = positions[(positions >= 0) & (positions < self.x.size)]
        positions = positions[np.isfinite(self.x[positions]) & np.isfinite(self.y[positions])]
        if positions.size == 0:
            raise AnalysisError("No valid data in the selected region.")
        if operation == LOCAL_MAXIMUM:
            return self._result(operation, positions[int(np.argmax(self.y[positions]))])
        if operation == LOCAL_MINIMUM:
            return self._result(operation, positions[int(np.argmin(self.y[positions]))])
        if operation in (PEAK, TROUGH):
            ranked = self._rank_candidates(positions, maxima=operation == PEAK)
            if not ranked:
                raise AnalysisError(f"No local {operation.lower()} found.")
            position, prominence = ranked[0]
            return self._result(operation, position, prominence=prominence)
        if operation == HALF_PEAK:
            return self._half_peak(positions)
        raise AnalysisError("Unsupported analysis operation.")

    def _result(self, operation: str, position: int, **values) -> AnalysisResult:
        return AnalysisResult(
            operation=operation, position=int(position),
            sample_index=int(self.source_indices[position]),
            x=float(self.x[position]), y=float(self.y[position]), **values,
        )

    def _rank_candidates(self, positions: np.ndarray, *, maxima: bool) -> list[tuple[int, float]]:
        candidates: list[tuple[int, float]] = []
        for segment in np.split(positions, np.flatnonzero(np.diff(positions) != 1) + 1):
            if segment.size < 3:
                continue
            values = self.y[segment] if maxima else -self.y[segment]
            i = 1
            while i < values.size - 1:
                plateau_end = i
                while plateau_end + 1 < values.size and values[plateau_end + 1] == values[i]:
                    plateau_end += 1
                if values[i] > values[i - 1] and plateau_end + 1 < values.size \
                        and values[plateau_end] > values[plateau_end + 1]:
                    local_i = (i + plateau_end) // 2
                    left_base = float(np.min(values[:local_i + 1]))
                    right_base = float(np.min(values[local_i:]))
                    prominence = float(values[local_i] - max(left_base, right_base))
                    if prominence > 0:
                        candidates.append((int(segment[local_i]), prominence))
                i = max(plateau_end + 1, i + 1)
        return sorted(candidates, key=lambda item: (-item[1], item[0]))

    def _half_peak(self, positions: np.ndarray) -> AnalysisResult:
        if positions.size < 5:
            raise AnalysisError("No valid half-level crossing: region is too small.")
        edge_count = max(1, int(np.ceil(positions.size * 0.1)))
        edge_positions = np.concatenate((positions[:edge_count], positions[-edge_count:]))
        baseline = float(np.median(self.y[edge_positions]))
        peak_candidates = self._rank_candidates(positions, maxima=True)
        trough_candidates = self._rank_candidates(positions, maxima=False)
        features = [
            (position, prominence, abs(float(self.y[position]) - baseline))
            for position, prominence in (*peak_candidates, *trough_candidates)
        ]
        if not features:
            raise AnalysisError("No valid half-level feature found.")
        features.sort(key=lambda item: (-item[2], -item[1], item[0]))
        scale = max(abs(baseline), features[0][2], 1.0)
        if len(features) > 1 and np.isclose(
            features[0][2], features[1][2], rtol=1e-6,
            atol=np.finfo(float).eps * scale * 64,
        ):
            raise AnalysisError("Ambiguous half-level feature in selected Range.")
        position, prominence, _ = features[0]
        half_level = baseline + (float(self.y[position]) - baseline) / 2.0
        left = self._find_crossing_by_x(positions, position, half_level, side=-1)
        right = self._find_crossing_by_x(positions, position, half_level, side=1)
        if left is None and right is None:
            raise AnalysisError("No valid half-level crossing.")
        if left is None:
            raise AnalysisError("No valid left half-level crossing.")
        if right is None:
            raise AnalysisError("No valid right half-level crossing.")
        return self._result(
            HALF_PEAK, position, prominence=prominence, baseline=baseline,
            half_level=half_level, left_half_x=left, right_half_x=right,
        )

    def _find_crossing_by_x(
        self, positions: np.ndarray, position: int, level: float, *, side: int,
    ) -> float | None:
        """Find the nearest enclosing crossing on one physical-X side.

        Analysis candidates retain source sample identity, while this crossing
        walk is ordered by X so descending traces use the same left/right
        semantics as ascending traces. Stable sorting preserves duplicate-X
        sample order.
        """
        ordered = positions[np.argsort(self.x[positions], kind="stable")]
        matches = np.flatnonzero(ordered == int(position))
        if matches.size == 0:
            return None
        center = int(matches[0])
        direction = -1 if side < 0 else 1
        index = center
        while 0 <= index + direction < ordered.size:
            first = int(ordered[index])
            second = int(ordered[index + direction])
            y1, y2 = float(self.y[first]), float(self.y[second])
            d1, d2 = y1 - level, y2 - level
            if d1 == 0:
                return float(self.x[first])
            if d2 == 0:
                return float(self.x[second])
            if d1 * d2 < 0:
                x1, x2 = float(self.x[first]), float(self.x[second])
                if y2 == y1:
                    return None
                crossing = x1 + (level - y1) * (x2 - x1) / (y2 - y1)
                return float(crossing) if np.isfinite(crossing) else None
            index += direction
        return None
