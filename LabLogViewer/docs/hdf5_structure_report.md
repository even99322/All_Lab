# Labber HDF5 Structure Report (Phase 1)

Generated from two **real** files you provided:

| File | Size | Role |
|---|---|---|
| `0828_5_0197_5_0297GHz_BG.hdf5` | ~94 KB | Single-point VNA trace (no active sweep) |
| `0828_RSMEP_1.hdf5` | ~84 MB | 1D swept measurement (855-point current sweep), each point a full VNA trace |

Full machine-readable dumps are in `sample_reports/*.inspect.json`
(produced by `tools/inspect_hdf5.py`, no hand-editing).

## 1. Top-level layout (both files)

```
/
├── Channels                 dataset, compound  — master channel registry
├── Data/
│   ├── Data                 dataset (1, n_scalar_channels, n_entries)
│   ├── Channel names        dataset, compound (name, info)
│   └── Time stamp           dataset (n_entries,)
│   [group attrs: Completed, Step dimensions, Step index,
│                 Fixed step index, Fixed step values,
│                 Entries, last trace]
├── Instrument config/
│   └── <instrument display name>/   (attrs = instrument settings;
│        may also contain scalar sub-datasets for numeric settings)
├── Instruments               dataset, compound — instrument driver info
├── Log list                  dataset, compound (channel_name,)
├── Settings                  group — measurement/optimizer settings
├── Step config/<channel>/    group per configured step channel
│   ├── Optimizer             group (attrs)
│   ├── Relation parameters   dataset
│   └── Step items            dataset (range/start/stop/step/n_pts/...)
├── Step list                 dataset, compound — ordered step channel defs
├── Tags/                     group (attrs: Project, Tags, User)
├── Traces/                   group — VECTOR / trace-valued channels
│   ├── Time stamp            dataset
│   ├── <trace channel name>          dataset (N, 2, n_entries) if complex
│   ├── <trace channel name>_N        dataset — points per trace
│   └── <trace channel name>_t0dt     dataset (n_entries_or_1, 2) — [t0, dt]
└── Views/                    group — saved Log Browser plot presets
    [root attrs: log_name, creation_time, comment, version, ...]
```

This matches Labber's own "measurement log" HDF5 layout — **not** a
generic/arbitrary HDF5 file. Both sample files share this skeleton;
the *contents* differ substantially (see below), which is exactly why
the parser must not hardcode field values.

## 2. The most important finding: two ways log-channel data is stored

Labber stores a channel's data in one of two different places
depending on whether the channel is **scalar** or **vector/trace**-valued:

### 2a. Scalar channels → `/Data/Data`

Shape `(1, n_channels, n_entries)`. `Data/Channel names` gives the
channel order. Every *step* channel that isn't swept as the vector
axis ends up here as one row, evaluated at each of the `n_entries`
sweep points.

In `0828_RSMEP_1.hdf5`, `Data/Data` has shape `(1, 11, 855)` — 11
scalar channels (Step index API, Average Current, S21-Enabled,
Output enabled, Output power, IF bandwidth, Average, # of averages,
Start/Stop frequency, # of points), each with 855 values across the
current sweep. `Average Current` is the channel that actually varies
(162.594 → 163.021 mA); the rest are constant per-point instrument
settings that Labber logs alongside the sweep for provenance.

In `0828_5_0197_5_0297GHz_BG.hdf5`, `Data/Data` has shape `(1, 0, 1)`
— **empty**, because every step channel is "fixed" (see
`Data.attrs['Fixed step index']` / `Fixed step values`, and
`Step dimensions == [1,1,...,1]`): this is a single-point measurement,
not a sweep.

### 2b. Vector/trace channels (e.g. VNA S21) → `/Traces/<name>`

The `Log list` dataset names the *measured* channel — here
`VNA - S21` — but its actual data is **not** in `Data/Data` at all.
Instead:

```
Traces/VNA - S21          shape (n_points, 2, n_entries), dtype float64
    attrs: complex=True, "x, name"="Frequency", "x, unit"="Hz"
Traces/VNA - S21_N        shape (1,)   -> n_points per trace (501)
Traces/VNA - S21_t0dt     shape (n_entries_or_1, 2) -> [t0, dt] per trace
```

* Axis 1 of the main dataset (`shape[1] == 2`) is `[real, imag]` —
  confirmed by `attrs['complex'] = True`.
* Axis 2 (`n_entries`) is the sweep index — 1 for the single-point
  file, 855 for the swept file.
* The trace's own x-axis (here: frequency) is **not stored as an
  explicit array**. It is reconstructed from `t0dt`:

  ```
  x = t0 + dt * arange(N)
  ```

  Verified numerically against both files:
  `t0dt = [5.0197e9, 2.0e4]`, `N = 501` →
  `x` runs from 5.0197 GHz to 5.0297 GHz in 501 points — exactly
  matching `Instrument config/.../Start frequency` and
  `Stop frequency` in both files. This is the same t0/dt convention
  Labber uses for time-domain digitizer traces, generalized here to a
  frequency sweep.

**Consequence for the parser:** a "log channel" can resolve to either
a column in `Data/Data` (scalar) or a dataset in `Traces/` (vector,
possibly complex, with its own reconstructed x-axis). The unified
data model (Phase 3) must represent both uniformly.

## 3. Sweep dimensionality is explicit, not inferred from shape alone

`Data.attrs['Step dimensions']` is an array with one entry per step
channel in `Step list`, in order. A value of `1` means that channel is
not actively swept for this log; a value `> 1` means it is, and gives
the number of points.

* Small file: `Step dimensions = [1,1,1,1,1,1,1,1,1,1]` → 0 active
  dimensions → single point.
* Big file: `Step dimensions = [1, 855, 1,1,1,1,1,1,1,1,1]` → one
  active dimension (`Average Current`, 855 points) → this is a 1D
  sweep over current, where *each* point is itself a full VNA
  frequency trace. From the Log Browser's point of view this is
  naturally a **2D plot**: X = Frequency (from the trace), Y =
  Average Current (the step channel), Z = S21.

This means "sweep dimensionality" for plotting purposes is
`(active step dimensions) + (trace's own axis, if the log channel is
a vector)` — not just `len(active step dimensions)`. The parser must
report both numbers separately: `n_step_dimensions` and
`has_vector_axis`.

## 4. Channel metadata (names, units, owning instrument)

The `/Channels` dataset is the authoritative registry: `name`,
`instrument`, `quantity`, `unitPhys` (physical/display unit),
`unitInstr` (instrument-native unit), gain/offset/amp, limits. This is
the correct source for **display name + unit** for every channel,
scalar or vector — e.g. `Average Current` → `mA`, `Output power` →
`dBm`, `IF bandwidth` → `Hz`. `VNA - S21` appears here too (unit
blank, since it's complex/dimensionless S-parameter).

`/Step list` gives the *configured* step channels in sweep order
(including ones that ended up fixed), with per-channel sweep mode,
final value, wait time. `/Step config/<channel>/Step items` gives the
actual start/stop/step/n_pts for channels that do sweep.

`/Instrument config/<instrument display name>` holds the instrument's
settings as **group attributes** (small file: R&S VNA, one
instrument) or as a mix of attributes *and* scalar HDF5 datasets (big
file: `Instrument config/VNA/Start frequency` etc. are 0-d datasets
with a `unit` attribute) — **both patterns must be supported**, this
is not consistent even between these two files.

`/Tags` group attrs give `Project`, `Tags`, `User` — directly usable
for the Project Browser (§7/§9 of the spec).

`/Views/<preset name>` groups store the Log Browser's own saved plot
configurations (axis choices, colormap, dB toggle, cursors, log
scale, etc.) per view name, plus `Views.attrs['selected_view']`. This
is a bonus: LabLogViewer can optionally **import Labber's saved view
settings** as a starting point for its own plot config (nice-to-have,
not required for Phase 1–6).

## 5. Format variant classification (what `inspect_hdf5.py` reports)

`inspect_hdf5.py` classifies every file into one of:

* `scalar_log_channel` — log channel data lives entirely in
  `Data/Data`, no `Traces` group with non-metadata datasets.
* `trace_log_channel` — log channel is vector/trace-valued, data in
  `Traces/<name>`; scalar step channels still tracked in `Data/Data`
  (both sample files are this variant).
* `vector_only` — `Traces` present but no scalar matrix at all.
* `unrecognized` — neither found; file will fall back to Raw HDF5
  Explorer Mode (§22/§23 of the spec).

This classification, not a hardcoded per-file branch, is what the
parser adapter architecture (`docs/parser_architecture.md`) is built
around.

## 6. Things intentionally NOT assumed

* Number of step channels, their names, or their order — read from
  `Step list` / `Channels` at runtime.
* Whether `Instrument config` sub-groups store settings as attrs vs.
  scalar datasets — both are handled.
* Whether a log channel is complex — always checked via the `complex`
  attribute on the `Traces/<name>` dataset, not inferred from name.
* Number of active sweep dimensions — read from `Step dimensions`,
  not from `len(Data/Data.shape)`.
* File size / n_entries — the big file (84 MB, 855×501 complex
  points) and the small file (94 KB, 1 point) are handled by the same
  code path with no special-casing.

## 7. Open questions for later phases (not blocking Phase 1)

* We have only seen the "modern" Labber trace-based layout in both
  samples. Older Labber versions are documented to sometimes store
  vector log data as literal N-D arrays directly under `Data/Data`
  with no separate `Traces` group. The adapter architecture accounts
  for this (`LabberParserV1` vs `LabberParserV2` in
  `docs/parser_architecture.md`) but there is no sample file yet to
  validate against — flagging this so a differently-shaped file
  doesn't silently mis-parse. `AutoDetector` falls back to Raw HDF5
  Explorer Mode rather than guessing.
* 3D+ sweeps (two or more active `Step dimensions` entries) haven't
  been seen in a real sample yet; the slicing logic in
  `app/analysis/slicing.py` is written generically against
  `Step dimensions` so it *should* generalize, but needs a real 3D
  file to confirm before Phase 7 sign-off.
