"""
app/core/channel_manager.py — Phase 3 / v0.9B

A thin, GUI-friendly query API over an Experiment. This is what the
future Channel Browser (spec §9), axis-selection dropdowns (§10/§11),
and Slice Explorer (§12) will actually call — so those widgets never
need to know about StepAxis/VectorTraceInfo internals directly, and so
this logic is unit-testable independent of any Qt code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.data_model import ChannelInfo, Experiment


@dataclass
class ChannelSummary:
    """Flat, display-ready view of one channel, for populating the
    Step Channels / Log Channels / Calculated Channels tree (spec §9)."""
    name: str
    category: str            # "step" | "log" | "calculated"
    unit: str | None
    instrument: str | None
    is_vector: bool
    is_complex: bool
    n_points: int | None     # points along the channel's own axis, if any


@dataclass
class AxisCandidate:
    """v0.9B: one legitimate, dynamically-discovered choice for the
    Advanced Viewer's X Axis / Y Axis dropdown. Nothing is ever
    hardcoded to a specific channel name — the candidate list is built
    purely from what the CURRENT file's Experiment actually contains
    (spec §4's core requirement).

    `domain` is what makes X/Y compatibility checking generic rather
    than a pile of special cases:
      - "entries": one value per sweep entry (any step channel, fixed
        or actively swept, and any SCALAR log channel). Two
        "entries"-domain candidates are always directly comparable —
        e.g. Average Current vs Output Power — with no trace/entry
        selection needed.
      - "points": one value per point WITHIN a single trace (a vector
        log channel's own axis, e.g. "Frequency", or the vector
        channel itself, e.g. "VNA - S21"). Two "points"-domain
        candidates are only directly comparable when they come from
        the SAME vector channel (same point count) and a current
        trace/entry index has been chosen — see
        ChannelManager.axis_domains_compatible().
    """
    name: str
    domain: str                # "entries" | "points"
    unit: str | None
    source: str                  # "step" | "log_scalar" | "trace_axis" | "vector_channel"
    base_channel: str            # underlying ChannelInfo.name to fetch data from
    is_relation_based: bool = False
    is_complex: bool = False     # True only for source="vector_channel" -
                                   # signals the Transform system applies
    transform_key: str | None = None  # derived coordinate transform, if any


class ChannelManager:
    def __init__(self, experiment: Experiment):
        self.experiment = experiment

    # ---- listing ----------------------------------------------------------

    def list_step_channels(self) -> list[ChannelSummary]:
        return [
            self._summarize(c, "step")
            for c in self.experiment.channels.values()
            if c.is_step
        ]

    def list_log_channels(self) -> list[ChannelSummary]:
        return [
            self._summarize(c, "log")
            for c in self.experiment.channels.values()
            if c.is_log
        ]

    def list_all_channels(self) -> list[ChannelSummary]:
        out = []
        for c in self.experiment.channels.values():
            category = "log" if c.is_log else ("step" if c.is_step else "other")
            out.append(self._summarize(c, category))
        return out

    def list_active_sweep_axes(self) -> list[str]:
        """Channel names that are actually swept (Step dimensions > 1)
        in this particular log — feeds axis-selection dropdowns."""
        return [axis.channel.name for axis in self.experiment.step_axes]

    def list_plottable_x_candidates(self) -> list[str]:
        """Any active step axis, plus any vector trace's own axis
        (e.g. 'VNA - S21' contributes 'Frequency' conceptually, but
        since the trace axis isn't itself a named channel, we surface
        the vector channel name and let the plot layer know via
        get_vector_trace_axis() that selecting it as X means 'use its
        own reconstructed axis', per spec §10/§11)."""
        candidates = list(self.list_active_sweep_axes())
        for name, vt in self.experiment.vector_traces.items():
            label = vt.x_name or name
            if label not in candidates:
                candidates.append(label)
        return candidates

    def list_plottable_y_candidates(self) -> list[str]:
        return list(self.experiment.log_channel_names)

    # ---- 2D surface API (Phase 6) --------------------------------------------

    def list_2d_z_candidates(self) -> list[str]:
        """Any log channel is a valid Z choice - same set as the 1D Y
        candidates, kept as a separate method so the GUI's naming
        doesn't imply 1D/2D share state."""
        return list(self.experiment.log_channel_names)

    def get_2d_axis_candidates(self, z_channel: str) -> tuple[list[str], list[str]]:
        """Return the physical dimensions that can form a plain 2D plot.

        Both X and Y receive the same dimension set so either physical
        dimension can be placed on either screen axis. Channels with
        fewer or more than two dimensions belong in the 1D or N-D view,
        respectively, because this view has no controls for fixing extra
        dimensions.
        """
        if not z_channel:
            return [], []
        dimensions = self.list_dimensions(z_channel)
        if len(dimensions) != 2:
            return [], []
        names = [dimension.name for dimension in dimensions]
        return list(names), list(names)

    def get_2d_data(self, x_channel: str, y_channel: str, z_channel: str, transform: str = "raw"):
        return self.experiment.get_2d_data(x_channel, y_channel, z_channel, transform=transform)

    # ---- N-D Slice Explorer API (Phase 7) -------------------------------------

    def list_dimensions(self, z_channel: str):
        """Returns the ordered list of Dimension objects for a
        channel - the GUI's Slice Explorer populates its X/Y dropdowns
        and per-dimension slice controls entirely from this, never
        from a hardcoded dimension name."""
        return self.experiment.list_dimensions(z_channel)

    def get_nd_slice(self, z_channel: str, x_dim: str, y_dim: str | None = None,
                      fixed: dict | None = None, transform: str = "raw"):
        return self.experiment.get_nd_slice(z_channel, x_dim, y_dim=y_dim, fixed=fixed, transform=transform)

    # ---- lookups ---------------------------------------------------------

    def get_channel(self, name: str) -> ChannelInfo:
        return self.experiment.get_channel(name)

    def is_complex(self, name: str) -> bool:
        vt = self.experiment.vector_traces.get(name)
        return bool(vt and vt.complex)

    def get_vector_trace_axis(self, channel_name: str):
        """Returns the VectorTraceInfo for a vector channel (its own
        reconstructed x-axis, points, etc.), or None if the channel is
        scalar."""
        return self.experiment.vector_traces.get(channel_name)

    # ---- dimensionality summary (spec §11) --------------------------------

    def sweep_dimension_report(self) -> dict:
        return {
            "n_active_step_dims": self.experiment.n_active_step_dims,
            "has_vector_log_channel": self.experiment.has_vector_log_channel,
            "total_dimensions": self.experiment.total_dimensions,
            "detail": self.experiment.sweep_dimension_summary(),
        }

    # ---- Metadata API (Phase 9) -----------------------------------------------

    def get_metadata_summary(self, z_channel: str | None = None) -> dict:
        """A single, structured, GUI-ready snapshot of everything the
        Metadata panel needs to display (spec's Phase 9 list): file
        name, experiment name, comment/project/user/tags, and - if a
        channel is given - that channel's unit/shape plus every
        dimension's name/unit/size/min/max/step/uniformity.

        Pure formatting over data the Experiment already parsed at
        open time — no additional HDF5 access happens here. This is
        also the single place the GUI's Metadata panel reads from, so
        it never touches Experiment/ChannelInfo internals directly.
        """
        e = self.experiment
        summary: dict = {
            "file_name": Path(e.source_path).name,
            "file_path": e.source_path,
            "experiment_name": e.log_name,
            "format_variant": e.format_variant,
            "comment": e.comment,
            "project": e.project,
            "user": e.user,
            "tags": list(e.tags),
            "version": e.version,
            "creation_time": e.creation_time,
            "n_step_channels": len(self.list_step_channels()),
            "n_log_channels": len(self.list_log_channels()),
        }

        if z_channel:
            try:
                channel = self.get_channel(z_channel)
                dims = self.list_dimensions(z_channel)
                summary["channel"] = {
                    "name": channel.name,
                    "unit": channel.unit,
                    "instrument": channel.instrument,
                    "is_vector": channel.is_vector,
                    "is_complex": channel.is_complex,
                    "shape": tuple(d.size for d in dims),
                }
                summary["dimensions"] = [
                    {
                        "name": d.name,
                        "unit": d.unit,
                        "size": d.size,
                        "min": d.min,
                        "max": d.max,
                        "step": d.step,
                        "is_uniform": d.is_uniform,
                    }
                    for d in dims
                ]
            except Exception:
                summary["channel"] = None
                summary["dimensions"] = []
        else:
            summary["channel"] = None
            summary["dimensions"] = []

        return summary

    # ---- Dynamic Axis Discovery (v0.9B) ----------------------------------------

    def list_axis_candidates(self) -> list[AxisCandidate]:
        """Builds the full set of legitimate X/Y axis choices for the
        CURRENT file, purely from its actual parsed structure (spec
        §4: 'must dynamically determine what can legitimately function
        as an Axis'). Nothing here is hardcoded to any specific
        channel name — a file with different step/log channels simply
        produces a different candidate list.

        Deliberately does NOT expose every raw HDF5 dataset: only
        step channels (fixed or actively swept), scalar log channels,
        and vector log channels (as both their own trace axis AND the
        complex channel itself, for Transform-driven representations)
        become candidates — metadata, instrument config, and other
        non-axis information are excluded (spec §2's explicit warning
        against blindly exposing everything)."""
        candidates: list[AxisCandidate] = []
        e = self.experiment
        scalar_info = e.metadata_tree.get("scalar_data_matrix")
        available_scalar_names = set(scalar_info["channel_names"]) if scalar_info else set()

        for ch in e.channels.values():
            if ch.is_vector:
                continue  # vector channels are handled specially below
            if ch.name not in available_scalar_names:
                # This channel has no recorded values in Data/Data at
                # all - happens for files where every step channel is
                # fixed (e.g. a single-point measurement with no
                # active sweep, where Labber doesn't bother recording
                # per-entry values). Excluding it here means the axis
                # dropdown never offers a candidate that would fail to
                # resolve (spec's "Missing channel handling"
                # requirement) instead of raising when selected.
                continue
            if ch.is_step or ch.is_log:
                candidates.append(AxisCandidate(
                    name=ch.name,
                    domain="entries",
                    unit=ch.unit,
                    source="step" if ch.is_step else "log_scalar",
                    base_channel=ch.name,
                    is_relation_based=ch.is_relation_based,
                ))

        for name, vt in e.vector_traces.items():
            ch = e.channels.get(name)
            candidates.append(AxisCandidate(
                name=vt.x_name or f"{name} (index)",
                domain="points",
                unit=vt.x_unit,
                source="trace_axis",
                base_channel=name,
            ))
            candidates.append(AxisCandidate(
                name=name,
                domain="points",
                unit=ch.unit if ch else None,
                source="vector_channel",
                base_channel=name,
                is_complex=bool(ch.is_complex) if ch else True,
            ))
            if ch is None or ch.is_complex:
                for label, transform_key, unit in (
                    ("Real", "real", ch.unit if ch else None),
                    ("Imaginary", "imag", ch.unit if ch else None),
                    ("Magnitude", "magnitude", ch.unit if ch else None),
                    ("Phase", "phase_deg", "deg"),
                ):
                    candidates.append(AxisCandidate(
                        name=label,
                        domain="points",
                        unit=unit,
                        source="derived",
                        base_channel=name,
                        transform_key=transform_key,
                    ))

        return candidates

    def list_x_axis_candidates(self) -> list[AxisCandidate]:
        """Return candidates that are meaningful without an implicit transform.

        Raw complex measurements remain valid Y quantities but are not exposed
        as X. Their real-valued derived coordinates are explicit candidates,
        alongside physical dimensions and real scalar channels.
        """
        return [candidate for candidate in self.list_axis_candidates() if not candidate.is_complex]

    def list_y_axis_candidates(self) -> list[AxisCandidate]:
        """Return all meaningful dependent quantities and physical dimensions."""
        return self.list_axis_candidates()

    def axis_domains_compatible(self, x: AxisCandidate, y: AxisCandidate) -> bool:
        """Whether an (X, Y) pair can be plotted directly against each
        other (spec §13: invalid combinations must be caught, not
        crash). Two 'entries'-domain candidates are always compatible.
        Two 'points'-domain candidates are only compatible when they
        share the same underlying vector channel's point count — a
        cross-channel points-domain pairing isn't supported here."""
        if x.domain != y.domain:
            return False
        if x.domain == "points":
            return (
                x.base_channel == y.base_channel
                and self._points_length(x) == self._points_length(y)
                and self._points_length(x) > 0
            )
        return True

    def _points_length(self, candidate: AxisCandidate) -> int:
        vt = self.experiment.vector_traces.get(candidate.base_channel)
        return vt.n_points if vt else 0

    def get_axis_data(self, candidate: AxisCandidate, entry_index: int | None = None,
                       transform: str = "raw") -> np.ndarray:
        """Resolves an AxisCandidate into concrete data. `transform`
        only matters for source='vector_channel' (Real/Imag/Magnitude/
        Phase/etc — see app.plotting.complex_transform); it's ignored
        for 'entries'-domain and 'trace_axis' candidates, which are
        always real-valued as recorded.

        For a 'points'-domain, source='vector_channel' candidate,
        `entry_index` selects WHICH trace/Log Entry to read (defaults
        to 0) — this is the concrete link between axis resolution and
        the current Trace/Log Entry selection (spec §5's explicit
        Axis/Trace separation: the axis defines WHAT is plotted, the
        Trace selection defines WHICH entry, and they compose here
        without being coupled in the data model itself)."""
        if candidate.domain == "entries":
            return self.experiment.get_data(candidate.base_channel, transform="raw")

        if candidate.source == "trace_axis":
            vt = self.experiment.vector_traces[candidate.base_channel]
            return vt.x_values

        if candidate.source == "derived":
            idx = entry_index if entry_index is not None else 0
            data = self.experiment.get_data(
                candidate.base_channel,
                transform=candidate.transform_key or "raw",
                entry_slice=slice(idx, idx + 1),
            )
            return np.asarray(data).reshape(-1)

        # source == "vector_channel"
        idx = entry_index if entry_index is not None else 0
        data = self.experiment.get_data(
            candidate.base_channel, transform=transform, entry_slice=slice(idx, idx + 1)
        )
        return np.asarray(data).reshape(-1)

    # ---- internal -----------------------------------------------------------

    def _summarize(self, c: ChannelInfo, category: str) -> ChannelSummary:
        return ChannelSummary(
            name=c.name,
            category=category,
            unit=c.unit,
            instrument=c.instrument,
            is_vector=c.is_vector,
            is_complex=c.is_complex,
            n_points=c.n_points,
        )
