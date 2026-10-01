"""Session-only point Marks and coordinate annotations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Hashable

import numpy as np


MAX_MARKS = 10
POINT_MARK = "point"
RANGE = "range"
HORIZONTAL_LINE = "horizontal_line"
VERTICAL_LINE = "vertical_line"
CROSSHAIR = "crosshair"

TOOL_LABELS = {
    POINT_MARK: "Point Mark",
    RANGE: "Range",
    HORIZONTAL_LINE: "Horizontal Line",
    VERTICAL_LINE: "Vertical Line",
    CROSSHAIR: "Crosshair",
}
MARK_TOOLS = tuple(TOOL_LABELS)


@dataclass
class Mark:
    number: int
    mode: str
    sample_index: int | None
    x_index: int
    y_index: int | None
    x: float
    y: float
    value: float
    x_name: str
    y_name: str
    value_name: str
    x_unit: str | None
    y_unit: str | None
    value_unit: str | None
    visible: bool = True

    @property
    def mark_id(self) -> str:
        return f"M{self.number}"

    @property
    def object_id(self) -> str:
        return f"{POINT_MARK}:{self.number}"

    @property
    def display_name(self) -> str:
        return self.mark_id


@dataclass
class HalfPeakResult:
    """A calculated Half-Peak result owned by one Range annotation."""
    extremum_x: float
    extremum_value: float
    baseline: float
    half_level: float
    left_crossing: float
    right_crossing: float
    width: float
    sample_index: int


@dataclass
class Annotation:
    annotation_type: str
    number: int
    x: float | None = None
    y: float | None = None
    x2: float | None = None
    value: float | None = None
    x_name: str = "X"
    y_name: str = "Y"
    value_name: str = "Value"
    x_unit: str | None = None
    y_unit: str | None = None
    value_unit: str | None = None
    visible: bool = True
    half_peak: HalfPeakResult | None = None

    @property
    def object_id(self) -> str:
        return f"{self.annotation_type}:{self.number}"

    @property
    def display_name(self) -> str:
        return f"{TOOL_LABELS[self.annotation_type]} {self.number}"

    @property
    def width(self) -> float | None:
        if self.annotation_type != RANGE or self.x is None or self.x2 is None:
            return None
        return self.x2 - self.x


class MarkManager:
    """Owns the annotation state for one 1D or 2D plot context."""

    def __init__(self):
        self._marks: dict[int, Mark] = {}
        self._annotations: dict[str, Annotation] = {}
        self.selected_number: int | None = None
        self.selected_id: str | None = None
        self.context_key: Hashable | None = None
        self.mode: str | None = None
        self._x_semantic: Hashable | None = None
        self._y_semantic: Hashable | None = None
        self._x = np.array([], dtype=float)
        self._y = np.array([], dtype=float)
        self._source_indices = np.array([], dtype=int)
        self._z: np.ndarray | None = None
        self._names = ("X", "Y", "Value")
        self._units: tuple[str | None, str | None, str | None] = (None, None, None)

    @property
    def can_place(self) -> bool:
        if self.mode == "1d":
            return self._x.size > 0 and self._y.size == self._x.size
        if self.mode == "2d":
            return bool(self._x.size and self._y.size and self._z is not None)
        return False

    @property
    def names(self) -> tuple[str, str, str]:
        return self._names

    def marks(self) -> list[Mark]:
        return [self._marks[number] for number in sorted(self._marks)]

    def annotations(self) -> list[Annotation]:
        order = {tool: index for index, tool in enumerate(MARK_TOOLS)}
        return sorted(
            self._annotations.values(),
            key=lambda item: (order[item.annotation_type], item.number),
        )

    def objects(self) -> list[Mark | Annotation]:
        return [*self.marks(), *self.annotations()]

    def analysis_trace(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Cached displayed 1D arrays and original sample identities."""
        if self.mode != "1d":
            return (np.array([], dtype=float),) * 3
        return self._x, self._y, self._source_indices

    def trace_position_for_mark(self, number: int) -> int | None:
        mark = self._marks.get(number)
        if mark is None or mark.sample_index is None:
            return None
        found = np.flatnonzero(self._source_indices == mark.sample_index)
        return int(found[0]) if found.size else None

    def move_to_trace_position(self, number: int, position: int) -> Mark | None:
        old = self._marks.get(number)
        if old is None or self.mode != "1d" or not 0 <= position < self._x.size:
            return None
        mark = replace(self._mark_at_1d_position(number, position), visible=old.visible)
        self._marks[number] = mark
        self.select_object(mark.object_id)
        return mark

    def add_at_trace_position(self, position: int) -> Mark | None:
        number = self._lowest_free(self._marks)
        if number is None or self.mode != "1d" or not 0 <= position < self._x.size:
            return None
        mark = self._mark_at_1d_position(number, position)
        self._marks[number] = mark
        self.select_object(mark.object_id)
        return mark

    def object(self, object_id: str) -> Mark | Annotation | None:
        return next((item for item in self.objects() if item.object_id == object_id), None)

    def set_visible(self, object_id: str, visible: bool) -> bool:
        item = self.object(object_id)
        if item is None:
            return False
        updated = replace(item, visible=bool(visible))
        if isinstance(item, Mark):
            self._marks[item.number] = updated
        else:
            self._annotations[object_id] = updated
        return True

    def set_1d_context(
        self, context_key: Hashable, x_values, y_values, *,
        x_name: str, y_name: str, x_unit: str | None = None,
        y_unit: str | None = None, x_semantic: Hashable | None = None,
        y_semantic: Hashable | None = None,
    ) -> bool:
        x = np.asarray(x_values, dtype=float).reshape(-1)
        y = np.asarray(y_values, dtype=float).reshape(-1)
        if x.size != y.size:
            raise ValueError("1D Mark data requires equal-length X and Y arrays.")
        finite = np.isfinite(x) & np.isfinite(y)
        source_indices = np.flatnonzero(finite)
        x, y = x[finite], y[finite]
        changed = self.mode != "1d" or self.context_key != context_key
        if changed:
            self.clear()
        else:
            if self._x_semantic != x_semantic:
                self._remove_types({RANGE, VERTICAL_LINE, CROSSHAIR})
            if self._y_semantic != y_semantic:
                self._remove_types({HORIZONTAL_LINE, CROSSHAIR})
        self.mode = "1d"
        self.context_key = context_key
        self._x_semantic, self._y_semantic = x_semantic, y_semantic
        self._x, self._y, self._z = x, y, None
        self._source_indices = source_indices
        self._names = (x_name, y_name, y_name)
        self._units = (x_unit, y_unit, y_unit)
        self._refresh_values()
        return changed

    def set_2d_context(
        self, context_key: Hashable, x_values, y_values, z_values, *,
        x_name: str, y_name: str, value_name: str,
        x_unit: str | None = None, y_unit: str | None = None,
        value_unit: str | None = None,
    ) -> bool:
        x = np.asarray(x_values, dtype=float).reshape(-1)
        y = np.asarray(y_values, dtype=float).reshape(-1)
        z = np.asarray(z_values, dtype=float)
        if z.shape != (y.size, x.size):
            raise ValueError("2D Mark data must have shape (len(Y), len(X)).")
        changed = self.mode != "2d" or self.context_key != context_key
        if changed:
            self.clear()
        self.mode = "2d"
        self.context_key = context_key
        self._x_semantic = x_name
        self._y_semantic = y_name
        self._x, self._y, self._z = x, y, z
        self._source_indices = np.arange(x.size, dtype=int)
        self._names = (x_name, y_name, value_name)
        self._units = (x_unit, y_unit, value_unit)
        self._refresh_values()
        return changed

    def add_nearest(self, x: float, y: float) -> Mark | None:
        number = self._lowest_free(self._marks)
        if number is None or not self.can_place:
            return None
        mark = self._make_mark(number, x, y)
        self._marks[number] = mark
        self.select_object(mark.object_id)
        return mark

    def move_nearest(self, number: int, x: float, y: float) -> Mark | None:
        if number not in self._marks or not self.can_place:
            return None
        old = self._marks[number]
        mark = replace(self._make_mark(number, x, y), visible=old.visible)
        self._marks[number] = mark
        self.select_object(mark.object_id)
        return mark

    def position_mark(
        self, number: int, *, x: float | None = None, y: float | None = None,
    ) -> Mark | None:
        """Position a Point Mark from numeric data coordinates.

        1D numeric positioning snaps by X alone. 2D accepts either axis
        independently and snaps each supplied coordinate to the real grid.
        Values outside the actual coordinate range are rejected.
        """
        old = self._marks.get(number)
        if old is None or not self.can_place or (x is None and y is None):
            return None
        if x is not None and not self._in_range(float(x), self._x):
            raise ValueError("X is out of valid range")
        if self.mode == "2d" and y is not None and not self._in_range(float(y), self._y):
            raise ValueError("Y is out of valid range")
        if self.mode == "1d":
            target_x = old.x if x is None else float(x)
            ix = int(np.argmin(np.abs(self._x - target_x)))
            mark = replace(self._mark_at_1d_position(number, ix), visible=old.visible)
        else:
            target_x = old.x if x is None else float(x)
            target_y = old.y if y is None else float(y)
            mark = replace(self._make_mark(number, target_x, target_y), visible=old.visible)
        self._marks[number] = mark
        self.select_object(mark.object_id)
        return mark

    def add_range(self, start: float, end: float) -> Annotation | None:
        if not self.can_place:
            return None
        start, end = sorted((float(start), float(end)))
        if start == end:
            return None
        return self._add_annotation(RANGE, x=start, x2=end)

    def add_horizontal_line(self, y: float) -> Annotation | None:
        return self._add_annotation(HORIZONTAL_LINE, y=float(y)) if self.can_place else None

    def add_vertical_line(self, x: float) -> Annotation | None:
        return self._add_annotation(VERTICAL_LINE, x=float(x)) if self.can_place else None

    def add_crosshair(self, x: float, y: float) -> Annotation | None:
        if not self.can_place:
            return None
        return self._add_annotation(CROSSHAIR, x=float(x), y=float(y))

    def move_annotation(
        self, object_id: str, *, x: float | None = None,
        y: float | None = None, x2: float | None = None,
    ) -> Annotation | None:
        annotation = self._annotations.get(object_id)
        if annotation is None:
            return None
        if annotation.annotation_type == RANGE:
            start = annotation.x if x is None else float(x)
            end = annotation.x2 if x2 is None else float(x2)
            start, end = sorted((start, end))
            changed = start != annotation.x or end != annotation.x2
            annotation = replace(
                annotation, x=start, x2=end,
                half_peak=None if changed else annotation.half_peak,
            )
        elif annotation.annotation_type == HORIZONTAL_LINE and y is not None:
            annotation = replace(annotation, y=float(y))
        elif annotation.annotation_type == VERTICAL_LINE and x is not None:
            annotation = replace(annotation, x=float(x))
        elif annotation.annotation_type == CROSSHAIR and x is not None and y is not None:
            if self.mode == "2d":
                x = float(self._x[int(np.argmin(np.abs(self._x - float(x))))])
                y = float(self._y[int(np.argmin(np.abs(self._y - float(y))))])
            annotation = replace(annotation, x=float(x), y=float(y))
        annotation = self._with_current_metadata(annotation)
        self._annotations[object_id] = annotation
        self.select_object(object_id)
        return annotation

    def position_annotation(
        self, object_id: str, *, x: float | None = None,
        y: float | None = None, x2: float | None = None,
    ) -> Annotation | None:
        """Apply partial numeric coordinates with finite/range validation."""
        annotation = self._annotations.get(object_id)
        if annotation is None or (x is None and y is None and x2 is None):
            return None
        for label, value, axis in (("X", x, self._x), ("End", x2, self._x), ("Y", y, self._y)):
            if value is not None and not self._in_range(float(value), axis):
                raise ValueError(f"{label} is out of valid range")
        if annotation.annotation_type == CROSSHAIR:
            x = annotation.x if x is None else x
            y = annotation.y if y is None else y
            if self.mode == "2d":
                x = float(self._x[int(np.argmin(np.abs(self._x - x)))])
                y = float(self._y[int(np.argmin(np.abs(self._y - y)))])
        if annotation.annotation_type == RANGE:
            start = annotation.x if x is None else float(x)
            end = annotation.x2 if x2 is None else float(x2)
            if start == end:
                raise ValueError("Range Start and End must differ")
        return self.move_annotation(object_id, x=x, y=y, x2=x2)

    def set_range_half_peak(self, object_id: str, result: HalfPeakResult) -> bool:
        annotation = self._annotations.get(object_id)
        if annotation is None or annotation.annotation_type != RANGE:
            return False
        self._annotations[object_id] = replace(annotation, half_peak=result)
        return True

    def range_half_peak(self, object_id: str) -> HalfPeakResult | None:
        annotation = self._annotations.get(object_id)
        return annotation.half_peak if annotation and annotation.annotation_type == RANGE else None

    def clear_range_half_peak(self, object_id: str) -> bool:
        annotation = self._annotations.get(object_id)
        if annotation is None or annotation.annotation_type != RANGE or annotation.half_peak is None:
            return False
        self._annotations[object_id] = replace(annotation, half_peak=None)
        return True

    @staticmethod
    def _in_range(value: float, values: np.ndarray) -> bool:
        return bool(np.isfinite(value) and values.size and np.nanmin(values) <= value <= np.nanmax(values))

    def select(self, number: int | None) -> None:
        self.select_object(f"{POINT_MARK}:{number}" if number in self._marks else None)

    def select_object(self, object_id: str | None) -> None:
        valid = {obj.object_id for obj in self.objects()}
        self.selected_id = object_id if object_id in valid else None
        if self.selected_id and self.selected_id.startswith(f"{POINT_MARK}:"):
            self.selected_number = int(self.selected_id.split(":", 1)[1])
        else:
            self.selected_number = None

    def delete(self, number: int) -> bool:
        return self.delete_object(f"{POINT_MARK}:{number}")

    def delete_object(self, object_id: str) -> bool:
        if object_id.startswith(f"{POINT_MARK}:"):
            number = int(object_id.split(":", 1)[1])
            removed = self._marks.pop(number, None) is not None
        else:
            removed = self._annotations.pop(object_id, None) is not None
        if removed and self.selected_id == object_id:
            self.selected_id = None
            self.selected_number = None
        return removed

    def clear(self) -> None:
        self._marks.clear()
        self._annotations.clear()
        self.selected_number = None
        self.selected_id = None

    def persistence_state(self) -> dict:
        """Return only stable, source-coordinate Mark state.

        Labels, calculated values, and Half-Peak output are deliberately
        omitted: they are derived from the current displayed arrays.
        """
        return {
            "mode": self.mode,
            "marks": [
                {
                    "number": mark.number,
                    "sample_index": mark.sample_index,
                    "x_index": mark.x_index,
                    "y_index": mark.y_index,
                    "x": mark.x,
                    "y": mark.y,
                    "visible": mark.visible,
                }
                for mark in self.marks()
            ],
            "annotations": [
                {
                    "type": item.annotation_type,
                    "number": item.number,
                    "x": item.x,
                    "y": item.y,
                    "x2": item.x2,
                    "visible": item.visible,
                }
                for item in self.annotations()
            ],
        }

    def restore_persistence_state(self, state: dict | None) -> int:
        """Restore valid entries only after the current data context is ready."""
        if not isinstance(state, dict) or state.get("mode") != self.mode or not self.can_place:
            return 0
        restored = 0
        self.clear()
        for raw in state.get("marks", []):
            try:
                number = int(raw["number"])
                if not 1 <= number <= MAX_MARKS or number in self._marks:
                    continue
                if self.mode == "1d":
                    source_index = int(raw["sample_index"])
                    locations = np.flatnonzero(self._source_indices == source_index)
                    if not locations.size:
                        continue
                    mark = self._mark_at_1d_position(number, int(locations[0]))
                else:
                    ix, iy = int(raw["x_index"]), int(raw["y_index"])
                    if not (0 <= ix < self._x.size and 0 <= iy < self._y.size):
                        continue
                    mark = self._make_mark(number, float(self._x[ix]), float(self._y[iy]))
                self._marks[number] = replace(mark, visible=bool(raw.get("visible", True)))
                restored += 1
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        for raw in state.get("annotations", []):
            try:
                annotation_type = str(raw["type"])
                number = int(raw["number"])
                if annotation_type not in MARK_TOOLS or annotation_type == POINT_MARK:
                    continue
                if not 1 <= number <= MAX_MARKS:
                    continue
                object_id = f"{annotation_type}:{number}"
                if object_id in self._annotations:
                    continue
                x = raw.get("x")
                y = raw.get("y")
                x2 = raw.get("x2")
                if annotation_type == RANGE:
                    if x is None or x2 is None or not self._in_range(float(x), self._x) or not self._in_range(float(x2), self._x):
                        continue
                    x, x2 = sorted((float(x), float(x2)))
                    if x == x2:
                        continue
                elif annotation_type == HORIZONTAL_LINE:
                    if y is None or not self._in_range(float(y), self._y):
                        continue
                    y = float(y)
                elif annotation_type == VERTICAL_LINE:
                    if x is None or not self._in_range(float(x), self._x):
                        continue
                    x = float(x)
                elif annotation_type == CROSSHAIR:
                    if (x is None or y is None or not self._in_range(float(x), self._x)
                            or not self._in_range(float(y), self._y)):
                        continue
                    x, y = float(x), float(y)
                    if self.mode == "2d":
                        x = float(self._x[int(np.argmin(np.abs(self._x - x)))])
                        y = float(self._y[int(np.argmin(np.abs(self._y - y)))])
                annotation = Annotation(annotation_type, number, x=x, y=y, x2=x2,
                                        visible=bool(raw.get("visible", True)))
                self._annotations[object_id] = self._with_current_metadata(annotation)
                restored += 1
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        self.select_object(None)
        return restored

    @staticmethod
    def _lowest_free(items: dict, maximum: int = MAX_MARKS) -> int | None:
        return next((number for number in range(1, maximum + 1) if number not in items), None)

    def _add_annotation(self, annotation_type: str, **coordinates) -> Annotation | None:
        used = {
            item.number: item for item in self._annotations.values()
            if item.annotation_type == annotation_type
        }
        number = self._lowest_free(used)
        if number is None:
            return None
        annotation = self._with_current_metadata(
            Annotation(annotation_type=annotation_type, number=number, **coordinates)
        )
        self._annotations[annotation.object_id] = annotation
        self.select_object(annotation.object_id)
        return annotation

    def _with_current_metadata(self, annotation: Annotation) -> Annotation:
        x_name, y_name, value_name = self._names
        x_unit, y_unit, value_unit = self._units
        value = annotation.value
        if (annotation.annotation_type == CROSSHAIR and self.mode == "2d"
                and annotation.x is not None and annotation.y is not None):
            ix = int(np.argmin(np.abs(self._x - annotation.x)))
            iy = int(np.argmin(np.abs(self._y - annotation.y)))
            value = float(self._z[iy, ix])
        return replace(
            annotation, value=value, x_name=x_name, y_name=y_name,
            value_name=value_name, x_unit=x_unit, y_unit=y_unit,
            value_unit=value_unit,
        )

    def _remove_types(self, annotation_types: set[str]) -> None:
        removed = {
            key for key, item in self._annotations.items()
            if item.annotation_type in annotation_types
        }
        for key in removed:
            del self._annotations[key]
        if self.selected_id in removed:
            self.selected_id = None
            self.selected_number = None

    def _make_mark(self, number: int, x: float, y: float) -> Mark:
        if self.mode == "1d":
            ix = self._nearest_1d_index(x, y)
            return self._mark_at_1d_position(number, ix)
        ix = int(np.argmin(np.abs(self._x - x)))
        iy = int(np.argmin(np.abs(self._y - y)))
        return Mark(
            number, "2d", None, ix, iy,
            float(self._x[ix]), float(self._y[iy]), float(self._z[iy, ix]),
            *self._names, *self._units,
        )

    def _mark_at_1d_position(self, number: int, position: int) -> Mark:
        source_index = int(self._source_indices[position])
        return Mark(
            number, "1d", source_index, source_index, None,
            float(self._x[position]), float(self._y[position]), float(self._y[position]),
            *self._names, *self._units,
        )

    def _nearest_1d_index(self, x: float, y: float) -> int:
        x_span = float(np.ptp(self._x)) or 1.0
        y_span = float(np.ptp(self._y)) or 1.0
        distance = np.square((self._x - x) / x_span) + np.square((self._y - y) / y_span)
        return int(np.argmin(distance))

    def _refresh_values(self) -> None:
        refreshed: dict[int, Mark] = {}
        for number, mark in self._marks.items():
            if self.mode == "1d" and mark.sample_index is not None:
                positions = np.flatnonzero(self._source_indices == mark.sample_index)
                if not positions.size:
                    continue
                position = int(positions[0])
                refreshed[number] = Mark(
                    number, "1d", mark.sample_index, mark.sample_index, None,
                    float(self._x[position]), float(self._y[position]), float(self._y[position]),
                    *self._names, *self._units, visible=mark.visible,
                )
            elif (self.mode == "2d" and mark.y_index is not None
                  and mark.x_index < self._x.size and mark.y_index < self._y.size):
                refreshed[number] = Mark(
                    number, "2d", None, mark.x_index, mark.y_index,
                    float(self._x[mark.x_index]), float(self._y[mark.y_index]),
                    float(self._z[mark.y_index, mark.x_index]),
                    *self._names, *self._units, visible=mark.visible,
                )
        self._marks = refreshed
        self._annotations = {
            key: self._with_current_metadata(
                replace(item, half_peak=None) if item.annotation_type == RANGE else item
            )
            for key, item in self._annotations.items()
        }
        if self.selected_id not in {obj.object_id for obj in self.objects()}:
            self.selected_id = None
            self.selected_number = None
