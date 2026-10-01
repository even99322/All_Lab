"""
app/core/labber_parser.py — Phase 3

Adapter architecture for turning a raw HDF5Reader into a unified
Experiment (data_model.py). See docs/parser_architecture.md for the
design rationale and docs/hdf5_structure_report.md for the real-file
findings this is built from.

Detection is purely STRUCTURAL (which groups/datasets exist), never
based on the *value* of a specific field like log_name — this is
required so the parser generalizes across different Labber files
(spec §28) instead of overfitting to one sample.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from app.core.data_identity import display_name_for_source
from app.core.hdf5_reader import HDF5Reader
from app.core.data_model import (
    ChannelInfo,
    Experiment,
    StepAxis,
    UnsupportedLabberFormat,
    VectorTraceInfo,
    extract_scalar_channel_row,
)


# --------------------------------------------------------------------------
# Base contract
# --------------------------------------------------------------------------

class BaseLabberParser(ABC):
    """Every parser version must implement both methods. `can_parse`
    must be side-effect-free and cheap (structural checks only) so
    AutoDetector can try several parsers quickly."""

    name: str = "base"

    @classmethod
    @abstractmethod
    def can_parse(cls, reader: HDF5Reader) -> bool:
        ...

    @abstractmethod
    def parse(self, reader: HDF5Reader) -> Experiment:
        ...


# --------------------------------------------------------------------------
# LabberParserV2 — the layout found in both real sample files
# --------------------------------------------------------------------------

class LabberParserV2(BaseLabberParser):
    """Handles the modern Labber log layout:
      - /Channels                 master channel registry
      - /Step list, /Log list     step/log channel name lists
      - /Data/Data (+ Channel names, group attrs incl. Step dimensions)
                                    scalar channel value matrix
      - /Traces/<name> (+ _N, _t0dt)
                                    vector/complex trace channels
      - /Instrument config/<...>   instrument settings (attrs and/or
                                    scalar sub-datasets — both handled)
      - /Tags                      Project / Tags / User

    Both format variants observed so far (scalar_log_channel and
    trace_log_channel — see hdf5_structure_report.md §5) are handled
    by this single parser; they differ only in whether the Traces
    branch is populated, which this parser checks for rather than
    assumes.
    """

    name = "labber_v2"

    REQUIRED_PATHS = ("/Channels", "/Data", "/Log list", "/Step list")

    @classmethod
    def can_parse(cls, reader: HDF5Reader) -> bool:
        return all(reader.kind_of(p) is not None for p in cls.REQUIRED_PATHS)

    # ---- top-level parse ------------------------------------------------

    def parse(self, reader: HDF5Reader) -> Experiment:
        if not self.can_parse(reader):
            raise UnsupportedLabberFormat(
                f"{self.name}: required paths not all present: {self.REQUIRED_PATHS}"
            )

        root_attrs = reader.get_attrs("/")
        tags_attrs = reader.get_attrs("/Tags") if reader.kind_of("/Tags") else {}

        channels_master = self._parse_channels_master(reader)
        step_names = self._parse_name_list(reader, "/Step list", "channel_name")
        log_names = self._parse_name_list(reader, "/Log list", "channel_name")
        step_relations = self._parse_step_relations(reader)

        vector_traces, vector_channel_names = self._parse_traces(reader, channels_master)

        scalar_info = self._parse_scalar_matrix(reader)
        sweep_meta = self._parse_sweep_metadata(reader)

        channels = self._build_channel_infos(
            channels_master=channels_master,
            step_names=step_names,
            log_names=log_names,
            vector_channel_names=vector_channel_names,
            scalar_info=scalar_info,
            vector_traces=vector_traces,
            step_relations=step_relations,
        )

        step_axes = self._build_step_axes(
            channels=channels,
            step_names=step_names,
            sweep_meta=sweep_meta,
            scalar_info=scalar_info,
        )

        instrument_config = self._parse_instrument_config(reader)

        format_variant = self._classify(scalar_info, vector_traces)

        metadata_tree: dict[str, Any] = {
            "root_attrs": root_attrs,
            "scalar_data_matrix": scalar_info,
            "sweep": sweep_meta,
            "step_list_raw": step_names,
            "log_list_raw": log_names,
            "channels_master": channels_master,
        }

        source_path = str(reader.path)
        metadata_log_name = str(root_attrs.get("log_name", "") or "")
        return Experiment(
            log_name=display_name_for_source(source_path, metadata_log_name),
            source_path=source_path,
            creation_time=root_attrs.get("creation_time"),
            comment=str(root_attrs.get("comment", "") or ""),
            project=(tags_attrs.get("Project") or [None])[0] if isinstance(tags_attrs.get("Project"), list) else tags_attrs.get("Project"),
            tags=list(tags_attrs.get("Tags") or []),
            user=(tags_attrs.get("User") or [None])[0] if isinstance(tags_attrs.get("User"), list) else tags_attrs.get("User"),
            version=str(root_attrs.get("version", "")) if root_attrs.get("version") is not None else None,
            channels=channels,
            step_axes=step_axes,
            log_channel_names=log_names,
            vector_traces=vector_traces,
            instrument_config=instrument_config,
            metadata_tree=metadata_tree,
            format_variant=format_variant,
            _reader=reader,
        )

    # ---- sub-parsers ------------------------------------------------------

    @staticmethod
    def _decode(v: Any) -> Any:
        if isinstance(v, bytes):
            return v.decode("utf-8", errors="replace")
        return v

    def _parse_channels_master(self, reader: HDF5Reader) -> list[dict]:
        rows = reader.read("/Channels")
        out = []
        for row in rows:
            entry = {}
            for field in row.dtype.names:
                entry[field] = self._decode(row[field])
            out.append(entry)
        return out

    def _parse_name_list(self, reader: HDF5Reader, path: str, field: str) -> list[str]:
        try:
            rows = reader.read(path)
        except Exception:
            return []
        names = []
        for row in rows:
            val = row[field] if field in row.dtype.names else row[0]
            names.append(self._decode(val))
        return names

    def _parse_step_relations(self, reader: HDF5Reader) -> dict[str, tuple[bool, str | None]]:
        """v0.9B: reads Step list's 'use_relations'/'equation' fields
        (confirmed present in real Labber files via direct inspection
        — see docs/hdf5_structure_report.md) to identify step channels
        whose swept values are DERIVED from a formula referencing other
        channels, rather than directly configured — the concrete
        mechanism behind Labber's "Function"/relation-based dimensions
        (e.g. a Flux channel computed from a Current channel via a
        conversion factor). Returns {channel_name: (use_relations, equation)}.
        Structural only — never evaluates the equation; Labber already
        evaluated it and stored the results in Data/Data, which this
        parser reads exactly like any other step channel's values."""
        result: dict[str, tuple[bool, str | None]] = {}
        if reader.kind_of("/Step list") != "dataset":
            return result
        try:
            rows = reader.read("/Step list")
        except Exception:
            return result
        field_names = rows.dtype.names or ()
        if "channel_name" not in field_names:
            return result
        for row in rows:
            name = self._decode(row["channel_name"])
            use_relations = bool(row["use_relations"]) if "use_relations" in field_names else False
            equation = self._decode(row["equation"]) if "equation" in field_names else None
            if not use_relations or equation == "":
                # Labber stores a placeholder equation (e.g. "x") even
                # when use_relations is False - only meaningful when
                # the relation is actually active.
                equation = None
            result[name] = (use_relations, equation)
        return result

    def _parse_traces(self, reader: HDF5Reader, channels_master: list[dict]):
        vector_traces: dict[str, VectorTraceInfo] = {}
        vector_channel_names: set[str] = set()

        if reader.kind_of("/Traces") != "group":
            return vector_traces, vector_channel_names

        keys = reader.get_group_keys("/Traces")
        base_names = {
            k for k in keys
            if not (k.endswith("_N") or k.endswith("_t0dt") or k == "Time stamp")
        }

        for name in base_names:
            ds_path = f"/Traces/{name}"
            if reader.kind_of(ds_path) != "dataset":
                continue
            ds = reader.get_dataset(ds_path)
            attrs = reader.get_attrs(ds_path)
            is_complex = bool(attrs.get("complex", False))
            x_name = attrs.get("x, name")
            x_unit = attrs.get("x, unit")

            n_key = f"/Traces/{name}_N"
            t0dt_key = f"/Traces/{name}_t0dt"

            n_points = None
            if reader.kind_of(n_key) == "dataset":
                n_arr = reader.read(n_key)
                n_points = int(np.asarray(n_arr).flatten()[0])
            if n_points is None:
                # fall back to the dataset's own first axis length
                n_points = ds.shape[0]

            x_values = np.arange(n_points, dtype=float)  # fallback: bare index
            if reader.kind_of(t0dt_key) == "dataset":
                t0dt = np.asarray(reader.read(t0dt_key))
                if t0dt.ndim == 2 and t0dt.shape[0] >= 1:
                    t0, dt = t0dt[0, 0], t0dt[0, 1]
                elif t0dt.ndim == 1 and t0dt.size == 2:
                    t0, dt = t0dt[0], t0dt[1]
                else:
                    t0, dt = 0.0, 1.0
                x_values = t0 + dt * np.arange(n_points, dtype=float)

            n_entries = ds.shape[-1] if len(ds.shape) >= 1 else 1

            channel = ChannelInfo(
                name=name,
                instrument=self._instrument_for(name, channels_master),
                unit=self._unit_for(name, channels_master),
                is_log=True,
                is_vector=True,
                is_complex=is_complex,
                shape=tuple(ds.shape),
                n_points=n_points,
            )

            vector_traces[name] = VectorTraceInfo(
                channel=channel,
                x_name=x_name,
                x_unit=x_unit,
                x_values=x_values,
                n_points=n_points,
                n_entries=n_entries,
                trace_path=ds_path,
                complex=is_complex,
            )
            vector_channel_names.add(name)

        return vector_traces, vector_channel_names

    def _parse_scalar_matrix(self, reader: HDF5Reader) -> dict | None:
        if reader.kind_of("/Data") != "group":
            return None
        if reader.kind_of("/Data/Data") != "dataset":
            return None
        ds = reader.get_dataset("/Data/Data")
        names = []
        if reader.kind_of("/Data/Channel names") == "dataset":
            rows = reader.read("/Data/Channel names")
            for row in rows:
                val = row["name"] if "name" in row.dtype.names else row[0]
                names.append(self._decode(val))
        return {
            "path": "/Data/Data",
            "shape": tuple(ds.shape),
            "channel_names": names,
        }

    def _parse_sweep_metadata(self, reader: HDF5Reader) -> dict:
        attrs = reader.get_attrs("/Data") if reader.kind_of("/Data") else {}
        step_dims = attrs.get("Step dimensions")
        return {
            "step_dimensions": step_dims,
            "step_index": attrs.get("Step index"),
            "fixed_step_index": attrs.get("Fixed step index"),
            "fixed_step_values": attrs.get("Fixed step values"),
            "completed": attrs.get("Completed"),
            "entries_last_trace": attrs.get("Entries, last trace"),
        }

    def _instrument_for(self, name: str, channels_master: list[dict]) -> str | None:
        for row in channels_master:
            if row.get("name") == name:
                return row.get("instrument")
        return None

    def _unit_for(self, name: str, channels_master: list[dict]) -> str | None:
        for row in channels_master:
            if row.get("name") == name:
                unit = row.get("unitPhys") or row.get("unitInstr")
                return unit if unit else None
        return None

    def _build_channel_infos(
        self,
        *,
        channels_master: list[dict],
        step_names: list[str],
        log_names: list[str],
        vector_channel_names: set[str],
        scalar_info: dict | None,
        vector_traces: dict[str, VectorTraceInfo],
        step_relations: dict[str, tuple[bool, str | None]] | None = None,
    ) -> dict[str, ChannelInfo]:
        step_relations = step_relations or {}
        channels: dict[str, ChannelInfo] = {}

        # start from the master registry - the authoritative name/unit/instrument source
        for row in channels_master:
            name = row.get("name")
            if not name:
                continue
            if name in vector_channel_names:
                # already built with full trace metadata in _parse_traces
                channels[name] = vector_traces[name].channel
                continue
            use_relations, equation = step_relations.get(name, (False, None))
            channels[name] = ChannelInfo(
                name=name,
                instrument=row.get("instrument"),
                unit=(row.get("unitPhys") or row.get("unitInstr") or None),
                is_step=name in step_names,
                is_log=name in log_names,
                is_vector=False,
                is_complex=False,
                shape=(),
                n_points=None,
                is_relation_based=use_relations,
                equation=equation,
            )

        # cover any step/log channel referenced by name but missing from
        # /Channels (defensive - seen possible in theory, not in either
        # sample, but do not assume it can't happen)
        for name in set(step_names) | set(log_names):
            if name not in channels:
                use_relations, equation = step_relations.get(name, (False, None))
                channels[name] = ChannelInfo(
                    name=name,
                    is_step=name in step_names,
                    is_log=name in log_names,
                    is_vector=name in vector_channel_names,
                    is_relation_based=use_relations,
                    equation=equation,
                )

        # make sure is_step/is_log flags are set correctly even for
        # channels that came from the vector-trace branch
        for name in vector_channel_names:
            ch = channels[name]
            ch.is_step = name in step_names or ch.is_step
            ch.is_log = True

        return channels

    def _build_step_axes(
        self,
        *,
        channels: dict[str, ChannelInfo],
        step_names: list[str],
        sweep_meta: dict,
        scalar_info: dict | None,
    ) -> list[StepAxis]:
        """Builds StepAxis entries ONLY for channels whose
        'Step dimensions' entry is > 1 (i.e. actually swept in this
        particular log) - per hdf5_structure_report.md §3. The values
        for an active axis are read from the scalar Data/Data matrix,
        which records the per-entry value even for the swept channel."""
        step_dims = sweep_meta.get("step_dimensions")
        axes: list[StepAxis] = []

        if not step_dims or not scalar_info:
            return axes

        scalar_names = scalar_info["channel_names"]

        for dim_index, name in enumerate(step_names):
            if dim_index >= len(step_dims):
                continue
            n_pts = step_dims[dim_index]
            if not n_pts or n_pts <= 1:
                continue  # fixed, not an active sweep axis
            if name not in scalar_names:
                continue  # can't recover its values; skip defensively
            channel = channels.get(name)
            if channel is None:
                continue
            row_idx = scalar_names.index(name)
            # values are read lazily by data_model normally, but for a
            # StepAxis we need them at parse time for axis labeling/
            # slicing UI, and this matrix is small (n_channels x n_entries
            # of float64) so eager read here is safe and matches both
            # sample files (max size seen: 11 x 855 float64 ~ 75KB).
            reader_data = None  # filled in below via closure workaround
            axes.append(
                StepAxis(channel=channel, values=np.array([]), dim_index=dim_index)
            )
            axes[-1]._pending_row_idx = row_idx  # temp marker, resolved by caller

        return axes

    def _classify(self, scalar_info: dict | None, vector_traces: dict) -> str:
        has_scalar = scalar_info is not None and len(scalar_info.get("channel_names", [])) > 0
        has_vector = len(vector_traces) > 0
        if has_vector and has_scalar:
            return "trace_log_channel"
        if has_scalar and not has_vector:
            return "scalar_log_channel"
        if has_vector and not has_scalar:
            return "vector_only"
        return "unrecognized"

    def _parse_instrument_config(self, reader: HDF5Reader) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if reader.kind_of("/Instrument config") != "group":
            return out
        for inst_name in reader.get_group_keys("/Instrument config"):
            inst_path = f"/Instrument config/{inst_name}"
            cfg = dict(reader.get_attrs(inst_path))
            if reader.kind_of(inst_path) == "group":
                for sub_name in reader.get_group_keys(inst_path):
                    sub_path = f"{inst_path}/{sub_name}"
                    if reader.kind_of(sub_path) == "dataset":
                        ds = reader.get_dataset(sub_path)
                        if ds.shape == ():
                            cfg[sub_name] = reader.get_scalar(sub_path)
            out[inst_name] = cfg
        return out


# --------------------------------------------------------------------------
# LabberParserV1 — placeholder for an older layout (spec §28)
# --------------------------------------------------------------------------

class LabberParserV1(BaseLabberParser):
    """Placeholder for an older Labber layout where vector log data is
    reportedly stored as a literal N-D array directly under Data/Data
    with no separate Traces group (per Labber's own format history).

    NOT YET VALIDATED against a real file - see the open question in
    docs/hdf5_structure_report.md §7. Deliberately excluded from
    AutoDetector.PARSERS until a real sample confirms its `can_parse`
    heuristic, so it cannot silently steal and mis-parse a V2 file.
    Kept here (rather than omitted) so the extension point is visible
    and ready to fill in once a sample surfaces.
    """

    name = "labber_v1_unvalidated"

    @classmethod
    def can_parse(cls, reader: HDF5Reader) -> bool:
        # Heuristic sketch only - Channels/Step list/Log list present,
        # but NO Traces group at all, which would indicate any vector
        # data (if present) lives directly in Data/Data instead.
        has_core = all(
            reader.kind_of(p) is not None
            for p in ("/Channels", "/Data", "/Log list", "/Step list")
        )
        no_traces = reader.kind_of("/Traces") is None
        return has_core and no_traces

    def parse(self, reader: HDF5Reader) -> Experiment:
        raise UnsupportedLabberFormat(
            "LabberParserV1 is a placeholder pending a real sample file "
            "to validate against (see hdf5_structure_report.md §7). "
            "Falling back to Raw HDF5 Explorer Mode is the correct "
            "behavior here, not a guessed parse."
        )


# --------------------------------------------------------------------------
# AutoDetector
# --------------------------------------------------------------------------

class AutoDetector:
    """Tries each registered parser's can_parse() in priority order.
    First match wins. LabberParserV1 is intentionally NOT registered
    yet (see class docstring above) - only re-enable it once validated
    against a real file, to avoid it wrongly claiming a V2-shaped file
    that happens to lack a populated Traces group (e.g. a purely
    scalar log with no vector channels at all - which V2 already
    handles correctly as format_variant='scalar_log_channel')."""

    PARSERS: list[type[BaseLabberParser]] = [LabberParserV2]

    @classmethod
    def detect_and_parse(cls, reader: HDF5Reader) -> Experiment:
        for parser_cls in cls.PARSERS:
            if parser_cls.can_parse(reader):
                experiment = parser_cls().parse(reader)
                cls._resolve_pending_step_axes(experiment, reader)
                return experiment
        raise UnsupportedLabberFormat(
            f"No registered parser recognized the structure of {reader.path}. "
            f"Falling back to Raw HDF5 Explorer Mode."
        )

    @staticmethod
    def _resolve_pending_step_axes(experiment: Experiment, reader: HDF5Reader) -> None:
        """LabberParserV2._build_step_axes stashes a row index rather
        than eagerly reading Data/Data itself (kept out of the parser
        method to avoid a second reader round-trip there); this
        resolves it once, right after parse, using the same reader.

        IMPORTANT (fixed for Phase 7's N-D support): the raw recorded
        row for a step channel has length n_entries — the FLATTENED
        product of ALL active step axes, not just this one. For the
        single-active-axis case (both real sample files) n_entries
        happens to equal this axis's own point count, so the naive
        'axis.values = row' used before Phase 7 worked by coincidence.
        For 2+ active axes it does not: this reshapes the recorded row
        into the full active-dimensions shape (same Step-list-order
        convention used by Experiment.get_full_nd_array) and extracts
        this axis's own distinct coordinate values, holding every
        OTHER active axis fixed at index 0 — correct because each step
        channel's recorded value only depends on its own loop index,
        replicated across the other axes' indices, by construction of
        how Labber (and this project's synthetic test fixtures) record
        a nested sweep."""
        scalar_info = experiment.metadata_tree.get("scalar_data_matrix")
        if not scalar_info:
            return
        sweep_meta = experiment.metadata_tree.get("sweep", {})
        step_dims = sweep_meta.get("step_dimensions")
        active_axes = experiment.step_axes
        active_sizes = tuple(
            int(step_dims[a.dim_index]) if step_dims is not None else len(active_axes)
            for a in active_axes
        )

        full = None
        n_channels = len(scalar_info.get("channel_names", []))
        for position, axis in enumerate(active_axes):
            row_idx = getattr(axis, "_pending_row_idx", None)
            if row_idx is None:
                continue
            if full is None:
                full = reader.read(scalar_info["path"])  # small, safe
            row = extract_scalar_channel_row(full, row_idx, n_channels=n_channels)
            if row.size == int(np.prod(active_sizes)) and active_sizes:
                reshaped = row.reshape(active_sizes)
                idx: list[Any] = [0] * len(active_sizes)
                idx[position] = slice(None)
                axis.values = reshaped[tuple(idx)]
            else:
                # defensive fallback: sizes don't factor as expected -
                # keep the old (possibly-wrong-for-N-D) behavior rather
                # than raising here, since this is parse-time and a
                # later SliceError from get_full_nd_array is a clearer
                # place for the user to see the real problem.
                axis.values = row
            delattr(axis, "_pending_row_idx")


def load_experiment(path: str) -> Experiment:
    """Convenience one-liner: open + detect + parse. Reader stays open
    for the Experiment's lifetime (see data_model.Experiment.close())."""
    reader = HDF5Reader(path)
    return AutoDetector.detect_and_parse(reader)
