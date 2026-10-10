# v0.15C Analysis Migration Audit

## Source and ownership

The fitting reference inspected for this release is the newest local
`development_reference/fit_gui` tree. Its Python application is under
`fitapp/`; `config/`, crash logs, output reports, generated result images,
and bytecode caches are user/runtime artifacts and are not copied into
the release. An unchanged source snapshot is retained under
`references/fitting/fit_gui/`. Production code lives under
`app/analysis/yig_fitting/` and does not import the reference snapshot.

The reference was written for PyQt6. The production copy uses PySide6 and
LabLogViewer's existing Viewer-owned `Experiment`. `core/data_io.py`
materializes a detached in-memory fitting snapshot through
`Experiment.get_data(..., transform="raw")`; the GUI and fitting package
do not import h5py or open source files independently. One fitting window
is associated with the launching Viewer and canonical Data identity.
Per-Data fitting session files are kept under Qt's user AppData location.
The source Viewer is closed before its Experiment is closed, and the
analysis worker is stopped/waited during window shutdown.

## Feature migration table

Status terms: **Integrated** means the referenced capability is present
in the production port. **Tested** means an executable validation ran in
this environment. Tests cover a synthetic reference YIG model and a
synthetic shared/per-slice global fit; they do not constitute a fit against
the archived real-data report.

| Reference capability | Production location | Status / evidence |
|---|---|---|
| Current Viewer data ownership; frequency traces, arbitrary complex S-parameters, sweep coordinates, dB map | `core/data_io.py`, `workers/data_worker.py`, `gui/yig_fitting_window.py` | Integrated; tested against real `0828 RSMEP_1.hdf5` and synthetic orientation/partial-data cases. |
| S-parameter, sweep-axis, scale, slice, frequency-window and preview zoom controls | `ui/main_window.py`, `controller.py` | Integrated; UI opens and combo interaction tested offscreen; full native manual interaction remains required. |
| Formula file load/reload, function selection, generated-model selection | `controller.py`, `core/formula.py` | Integrated; formula loading and bundled model discovery exercised in UI construction. |
| Dynamic parameter table, parameter memory, bounds/fixed values, numeric expressions | `ui/param_table.py`, `controller.py`, `core/formula.py` | Integrated; numerical fit use is runtime-blocked. |
| Formula builder, ideal model, Fano phase, environment amplitude/phase/delay, conjugate, magnitude companion, SI unit option, generated Python preview/save/load | `ui/formula_builder.py`, `core/codegen.py` | Integrated; execution/UI visual verification is runtime-blocked by the missing SciPy-dependent preview paths and lack of visible native GUI automation. |
| Formula library: add current/from file, optional file copy, notes, LaTeX, load, edit and delete | `core/library.py`, `ui/library_dialog.py`, `controller.py` | Integrated; persistence logic is copied into per-Data AppData scope; end-to-end GUI operations not fully exercised. |
| Single fit for complex-direct, split real/imag, and magnitude model outputs | `core/fitting.py`, `workers/fit_engine.py` | Integrated; a generated complex YIG-node trace was fitted and its known resonance recovered. Split-real/imag and magnitude-specific cases were not separately numerically exercised. |
| Weighting by `|S| + epsilon`, robust loss/method/tolerance and fit limits | `core/fitting.py`, `controller.py`, `ui/main_window.py` | Integrated; the tested fit used linear loss and no weighting. Other options remain unverified in this pass. |
| Automatic parameter guesses, user guess hooks, preview curve, fit statistics and result report | `core/formula.py`, `controller.py`, `core/report.py` | Integrated; bundled formula loading and UI preparation pass, but no archived real-data report was regenerated. |
| Hampel spike cleaning; magnitude or magnitude/Re/Im checks; manual frequency exclusion; drag-to-exclude; removed-point overlay | `core/cleaning.py`, `controller.py`, `ui/plots.py` | Integrated. A NumPy fallback covers median/dilation if SciPy is unavailable; it was exercised before installing SciPy, but exact output equivalence was not compared. |
| Single-fit progress/cancel, apply result as new initial values, copy report, CSV export, automatic CSV/PNG output, output folder selection/open | `controller.py`, `core/report.py`, `workers/fit_engine.py` | Integrated; asynchronous GUI completion, cancel, CSV/PNG export were not manually exercised. |
| Continuous/batch fit with trace interval, step, moving frequency windows, second window, initial-value modes, frequency bounds, rolling bounds, stop-on-failure and phase-linked option | `batch_controller.py`, `core/batch.py`, `ui/batch_panel.py` | Integrated; a 3-trace synthetic batch recovered centers in source order. GUI-worker lifecycle and phase-linked batch were not exercised. |
| Batch live progress, results table, per-slice selection/jump, all-parameter/single-parameter plots, error bars, quality filters/metrics, CSV import/export and autosave | `batch_controller.py`, `core/batch.py`, `ui/batch_panel.py`, `ui/roll_table.py` | Integrated; numerical core run passes; GUI result display/import/export and autosave not manually exercised. |
| Phase line and physical `κ_eff = κ · sin²(φ)` relation; estimate from batch fits; detect node candidates; infer line from candidates; show nodes on preview | `core/phase.py`, `phase_controller.py`, `ui/phase_panel.py` | Integrated from the reference implementation; SciPy-assisted peak detection and real fitted-result workflow were not validated. |
| Phase-linked sequential fitting, hard/soft phase link, skip weak node-adjacent slices, shared/fixed/slice parameter roles, optional global `T`/`φ_ref` fitting, global progress/cancel | `core/phase.py`, `phase_controller.py`, `ui/phase_panel.py` | Integrated; one synthetic shared/per-slice global fit passed. Hard/soft links, `T`/`φ_ref` fitting and linked batch are not yet numerically verified. |
| Apply global shared values to parameter table, transfer results to batch page, export global results | `phase_controller.py`, `ui/phase_panel.py` | Integrated; result-driven GUI workflow not manually exercised. |
| Data, fit, batch and phase plots; formula preview images; parameter/result tables | `ui/plots.py`, `ui/latex_view.py`, `ui/param_table.py`, `ui/batch_panel.py`, `ui/phase_panel.py` | Integrated. Window construction/import passed; full visual/native interaction not verified. |
| Per-Data session restore, formula/parameter state, batch and phase state, output options, geometry/tab restore | `session.py`, `core/settings.py`, `core/paths.py` | Integrated with canonical-identity-scoped external JSON. Restart round-trip not independently GUI-tested in this pass. |
| Standalone reference file picker | `controller.py` | Intentionally excluded from the embedded production window: data remains owned by the launching Viewer, preventing a second reader/data source and cross-Viewer ambiguity. |
| Reference source config, crash logs, generated reports/images, output data and Python bytecode | Not copied to runtime | Excluded as per-Data/user artifacts, generated output, or transient files; reference source itself remains separately snapshotted. |

## Scientific/data boundaries

`Experiment.get_data()` returns vector values as `(frequency_points,
sweep_entries)`. The adapter keeps this orientation. Full step-axis
coordinates are flattened in the Experiment's declared axis order. A
partial one-dimensional sweep maps only its acquired leading coordinates.
An incomplete multi-dimensional sweep is ambiguous and therefore does
not receive guessed physical coordinates. Channels with different point
grids or entry counts are omitted from the fitting snapshot rather than
being aligned, averaged, or reshaped.

No HDF5 writer is present in this integration. Fitting and node analysis
consume read-only snapshots; source measurement data are not modified.

## Environment validation limits

SciPy 1.18.1 was installed into the existing `.venv` after the first
offscreen/fallback checks; the environment was not recreated. Single and
global numerical core tests then passed using synthetic data. The real
0831 reference report was inspected, and its source file was parsed through
the production adapter, but that 437-slice global result was not rerun.
Visible native macOS GUI automation and Windows-native execution were
unavailable; offscreen Qt interaction/import tests are not claimed as
either. Full numerical option coverage, batch fit, cancellation/export
interactions, and the reference-report parity check remain local
validation items.
