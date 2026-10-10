"""Session-only active/selected/anchor state for ordered sweep traces."""

from __future__ import annotations

from collections.abc import Iterable


class TraceSelectionState:
    """Owns trace selection independently of any Qt selection model."""

    def __init__(self, traces: Iterable[int] = (), initial: int | None = None):
        self._traces: tuple[int, ...] = ()
        self._positions: dict[int, int] = {}
        self._selected: set[int] = set()
        self._visible: dict[int, bool] = {}
        self.active_trace: int | None = None
        self.anchor_trace: int | None = None
        self.reference_trace: int | None = None
        self.reset(traces, initial)

    def reset(self, traces: Iterable[int], initial: int | None = None) -> None:
        ordered = tuple(dict.fromkeys(int(trace) for trace in traces))
        self._traces = ordered
        self._positions = {trace: position for position, trace in enumerate(ordered)}
        self._selected.clear()
        self._visible.clear()
        self.active_trace = None
        self.anchor_trace = None
        self.reference_trace = None
        if ordered:
            target = int(initial) if initial is not None and int(initial) in self._positions else ordered[0]
            self.single_select(target)

    def contains(self, trace: int) -> bool:
        if trace is None:
            return False
        return int(trace) in self._positions

    def is_selected(self, trace: int) -> bool:
        return trace is not None and int(trace) in self._selected

    def single_select(self, trace: int) -> bool:
        trace = int(trace)
        if not self.contains(trace):
            return False
        self.active_trace = trace
        self.anchor_trace = trace
        self._selected = {trace}
        self._visible = {trace: True}
        self.reference_trace = None
        return True

    def range_select(self, trace: int) -> bool:
        trace = int(trace)
        if not self.contains(trace):
            return False
        anchor = self.anchor_trace if self.contains(self.anchor_trace) else self.active_trace
        if not self.contains(anchor):
            anchor = trace
        first, last = sorted((self._positions[anchor], self._positions[trace]))
        selected = set(self._traces[first:last + 1])
        self._visible = {item: self._visible.get(item, True) for item in selected}
        self._selected = selected
        if self.reference_trace not in selected:
            self.reference_trace = None
        self.active_trace = trace
        self.anchor_trace = anchor
        return True

    def toggle_select(self, trace: int) -> bool:
        trace = int(trace)
        if not self.contains(trace):
            return False
        self.anchor_trace = trace
        if trace in self._selected:
            if len(self._selected) > 1:
                self._selected.remove(trace)
                self._visible.pop(trace, None)
                if self.reference_trace == trace:
                    self.reference_trace = None
                if self.active_trace == trace:
                    self.active_trace = min(
                        self._selected,
                        key=lambda item: abs(self._positions[item] - self._positions[trace]),
                    )
                if len(self._selected) == 1:
                    only = next(iter(self._selected))
                    self._visible[only] = True
        else:
            self._selected.add(trace)
            self._visible[trace] = True
            self.active_trace = trace
        return True

    def set_active(self, trace: int) -> bool:
        """Change only Active, preserving selection, visibility and Reference."""
        trace = int(trace)
        if trace not in self._selected:
            return False
        self.active_trace = trace
        return True

    def set_visible(self, trace: int, visible: bool) -> bool:
        trace = int(trace)
        if trace not in self._selected:
            return False
        self._visible[trace] = bool(visible)
        return True

    def is_visible(self, trace: int) -> bool:
        return trace in self._selected and self._visible.get(int(trace), True)

    def visible_selection(self) -> tuple[int, ...]:
        return tuple(trace for trace in self.ordered_selection() if self.is_visible(trace))

    def set_reference(self, trace: int | None) -> bool:
        if trace is None:
            self.reference_trace = None
            return True
        trace = int(trace)
        if trace not in self._selected:
            return False
        self.reference_trace = trace
        return True

    def restore(
        self, selected: Iterable[int], *, active: int | None = None,
        visible: Iterable[int] | None = None, reference: int | None = None,
    ) -> bool:
        selected_set = {int(trace) for trace in selected if self.contains(int(trace))}
        if not selected_set:
            return False
        ordered = tuple(trace for trace in self._traces if trace in selected_set)
        visible_set = set(ordered if visible is None else (int(trace) for trace in visible))
        self._selected = set(ordered)
        self._visible = {trace: trace in visible_set for trace in ordered}
        self.active_trace = int(active) if active in self._selected else ordered[0]
        self.anchor_trace = self.active_trace
        self.reference_trace = int(reference) if reference in self._selected else None
        return True

    def move_active(self, delta: int, *, extend: bool = False) -> bool:
        if not self._traces:
            return False
        active = self.active_trace if self.contains(self.active_trace) else self._traces[0]
        position = max(0, min(self._positions[active] + int(delta), len(self._traces) - 1))
        target = self._traces[position]
        return self.range_select(target) if extend else self.single_select(target)

    def ordered_selection(self) -> tuple[int, ...]:
        return tuple(trace for trace in self._traces if trace in self._selected)

    @property
    def selected_count(self) -> int:
        return len(self._selected)
