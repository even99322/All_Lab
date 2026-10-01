# Parser Architecture Proposal (based on Phase 1 findings)

This is the design I'll implement in Phase 2/3. Posting it before writing
the real parser so you can veto/adjust the shape of things while it's
still cheap to change.

## Layering (per your spec, §4)

```
HDF5 Reader        app/core/hdf5_reader.py
      ↓
Format Detector     app/core/labber_parser.py  (BaseLabberParser + AutoDetector)
      ↓
Data Parser          app/core/labber_parser.py  (LabberParserV2, ...)
      ↓
Internal Data Model  app/core/data_model.py     (Experiment, Channel, ...)
      ↓
Plot/Analysis Layer   app/plotting/*, app/analysis/*
      ↓
GUI                   app/gui/*
```

The GUI and plotting layers only ever see `app/core/data_model.py`
objects — never an `h5py.File` or raw numpy array straight off disk.

## `hdf5_reader.py` — thin, dumb I/O layer

Responsibilities: open/close the file (context manager), give lazy
access to any dataset by path, read attributes, and nothing else. No
Labber-specific knowledge. This is what powers Raw HDF5 Explorer Mode
(§22/§23) directly, and is the only module that ever calls into
`h5py`.

```python
class HDF5Reader:
    def __init__(self, path: str): ...
    def __enter__(self) -> "HDF5Reader": ...
    def tree(self) -> list[NodeInfo]: ...       # reuses inspect_hdf5 logic
    def get_dataset(self, path: str) -> h5py.Dataset: ...   # lazy, not loaded
    def read(self, path: str, slice_=None) -> np.ndarray: ... # explicit load
    def get_attrs(self, path: str) -> dict: ...
```

## `labber_parser.py` — adapter architecture

```python
class BaseLabberParser(ABC):
    """Contract every parser version must satisfy."""
    @classmethod
    @abstractmethod
    def can_parse(cls, reader: HDF5Reader) -> bool: ...
    @abstractmethod
    def parse(self, reader: HDF5Reader) -> Experiment: ...

class LabberParserV2(BaseLabberParser):
    """Handles the layout found in both sample files:
       - Data/Data for scalar channels
       - Traces/<name> (+ _N, _t0dt) for vector/complex log channels
       - Step dimensions / Step list / Channels / Instrument config
       Format variants scalar_log_channel / trace_log_channel /
       vector_only are all handled inside this one parser — they're
       the same underlying layout, just with one branch empty.
    """
    @classmethod
    def can_parse(cls, reader): 
        # True if /Channels, /Data, /Log list, /Step list all exist
        ...
    def parse(self, reader): ...

class LabberParserV1(BaseLabberParser):
    """Placeholder for older Labber files that (per Labber's own docs)
       may store vector log data as a literal N-D array directly under
       Data/Data with no separate Traces group, instead of the
       t0dt-reconstructed trace format. Not yet validated against a
       real file (see open question in the structure report) — implemented
       defensively and exercised by a synthetic test fixture until a
       real sample surfaces. Ships disabled from AutoDetector's list until
       a real file confirms it, to avoid silently mis-parsing a V2 file."""
    @classmethod
    def can_parse(cls, reader): ...
    def parse(self, reader): ...

class AutoDetector:
    """Tries each registered parser's can_parse() in priority order.
       First match wins. If none match: raises UnsupportedLabberFormat,
       which the GUI layer (§22) catches to fall back to Raw HDF5
       Explorer Mode rather than crashing."""
    PARSERS = [LabberParserV2]  # LabberParserV1 added once validated

    @staticmethod
    def detect_and_parse(reader: HDF5Reader) -> Experiment: ...
```

Nothing here reads a *value* and branches on it (e.g. "if log_name ==
X"). Detection is purely structural (`can_parse` checks which
groups/datasets exist), matching your §28 requirement.

## `data_model.py` — the unified internal representation

Directly reflects what both files actually contain, generalized:

```python
@dataclass
class ChannelInfo:
    name: str
    instrument: str | None
    unit: str | None
    is_step: bool
    is_log: bool
    is_vector: bool          # True for trace/complex channels like VNA-S21
    shape: tuple[int, ...]   # logical shape as exposed to the plot layer

@dataclass
class StepAxis:
    channel: ChannelInfo
    values: np.ndarray       # 1D array, length = this axis's n_points
    active: bool             # False if Step dimensions entry == 1

@dataclass
class VectorTrace:
    channel: ChannelInfo
    x_values: np.ndarray     # reconstructed from t0dt, or read directly
    x_unit: str | None
    complex: bool
    # actual y-data NOT eagerly loaded here — see LazyChannelData below

@dataclass
class Experiment:
    log_name: str
    creation_time: float
    comment: str
    tags: list[str]
    project: str
    user: str
    channels: dict[str, ChannelInfo]
    step_axes: list[StepAxis]        # only the ACTIVE ones, in sweep order
    log_channels: list[ChannelInfo]
    vector_traces: dict[str, VectorTrace]   # keyed by channel name
    instrument_config: dict[str, dict]
    metadata_tree: dict              # everything else, for the Metadata Viewer
    source_path: str
    _reader: HDF5Reader              # kept open for lazy data access

    def get_data(self, channel_name: str, transform: str = "raw") -> np.ndarray:
        """Lazily loads and, for vector channels, applies
        Real/Imag/Magnitude/Magnitude dB/Phase transforms
        (app/plotting/complex_transform.py). This is the ONLY
        data-loading entry point the plot layer uses."""
        ...
```

Key design choices driven directly by Phase 1 findings:

* `Experiment` never eagerly loads `Traces/VNA - S21` (84 MB dataset
  in the big file) into memory. `get_data()` slices lazily via
  `HDF5Reader.read(path, slice_=...)`, which is required by your
  Performance Requirements (§21) anyway.
* `is_vector` on `ChannelInfo` is exactly how the GUI decides whether
  a channel needs the Complex Data transform controls (§10) or not —
  derived once at parse time from the `complex` attribute, not
  re-checked ad hoc.
* `StepAxis.active` mirrors `Step dimensions > 1` per channel — this
  is what feeds the Sweep Dimension Detection (§11) and Slice Explorer
  (§12): `n_dims = len(active step_axes) + (1 if any vector_trace
  else 0)`.

## Why this satisfies your compatibility principle (§28)

* Nothing in `LabberParserV2` is keyed off a specific channel name,
  instrument name, or log name from either sample file — all lookups
  go through `Step list` / `Log list` / `Channels` / group presence
  checks.
* The two sample files already exercise meaningfully different code
  paths (empty vs. populated `Data/Data`, 1 vs. 855 trace entries, one
  vs. eleven scalar channels, single- vs. multi-instrument config) —
  both pass through the same parser with no special-casing, which is
  the real test of "not hardcoded to one file."
* A file that doesn't match V2's `can_parse()` check falls through to
  `UnsupportedLabberFormat` → Raw HDF5 Explorer Mode, rather than
  crashing or silently producing wrong data.

## What I need from you to proceed

1. Confirm this layering matches what you want, or tell me what to
   change.
2. Per your own Phase 4 requirement, once this is built I'll run it
   against both real files via a CLI test script and show you the
   parsed `Experiment` summary (channel list, sweep dims, complex
   flags) for both — before touching any GUI code.
3. If/when you have a Labber file with 2+ active sweep dimensions or
   from an older Labber version, send it — it'll directly validate the
   open questions in the structure report instead of me guessing.
