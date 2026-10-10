# YIG Mirror Analysis

This package is the handoff-facing, GUI-independent entry point for LabLogViewer's
YIG Mirror analysis. The implementation is being consolidated incrementally;
the established, tested engine remains under `app/analysis/yig_fitting/` and is
called through `public_api.py`. The Viewer GUI must pass in an in-memory
`Experiment` or immutable arrays. This package does not open HDF5 files.

## Architecture

```text
HDF5 Reader -> Labber Parser -> Experiment Data Model
                                  |
                                  v
                          public_api.py
              fitting / batch / phase / global fit
                physical + coarse location results
                                  |
                                  v
                              GUI plots
```

The current production adapter is `adapt_experiment(experiment)`. It uses the
Data Model's raw complex traces and canonical `data_identity`; incomplete or
ambiguous sweep coordinates are not fabricated. The fitting implementation
continues to use the production modules in `yig_fitting/core/` until a tested
replacement is justified.

## Public API

`app.analysis.yig_mirror` exposes:

- `adapt_experiment(experiment)` - return the fitter's in-memory complex data.
- `fit_single_trace(...)` - execute the existing single-trace fitter.
- `fit_batch(...)` - execute independent trace fits with per-trace failures.
- `fit_global(...)` - execute slice/shared/fixed/phase-linked optimization.
- `estimate_kappa_phase_line(...)` - estimate the phase line from effective
  coupling or fitted phase observations.
- `analyze_physical_positions(...)` - calculate model-validated Node and
  Antinode frequencies.
- `coarse_detection_points(...)` - convert the retained legacy detector output
  to explicitly labeled coarse candidates.

Numerical arrays use Hz for frequency, ns for propagation delay `T`, radians
for phase, and the model's declared rate unit for linewidth/coupling. The
physical-position API names `kappa_b_hz` explicitly and rejects a model
relation other than the verified `kappa_b * sin(phi)**2` relation.

## Input and result conventions

The adapter represents each complex S trace as `[frequency, sweep_entry]`.
Existing fitting core functions accept a frequency vector plus one complex
trace. A global-fit slice contains its own frequency vector, complex measured
S, parameter vector, and optional bounds. No adapter writes to source HDF5.

`results.model.AnalysisPoint` is the shared location result. It stores type,
method, frequency, optional trace/sweep identity, phase, coupling, QC status,
and origin. Unknown quantities are `None`; the model does not invent a
confidence score. Coarse records always use `Coarse Node Candidate` or
`Coarse Antinode Candidate`; physical records use `Physical Node` or
`Physical Antinode`.

## Fitting model and units

The bundled `S11_single` model uses:

```text
kappa_m = kappa_b * sin(phi)^2
Gamma   = (kappa_m + alpha_r) / 2
Delta_m = -(alpha_r / 4) * sin(2*phi)
S       = A * exp(i*(phi_0 - 2*pi*(f-f_m)*t)) * S_ideal
```

The `S11_node` model uses the same coupling relation and substitutes its
independent `gamma_0` for `alpha_r` in `Delta_m`. It returns split real/imaginary
values to the existing fitter. Source conventions currently convert GHz to Hz,
MHz to Hz, and ns to seconds before evaluating the complex model. The model's
linewidth denominator convention is preserved as coded; any factor-of-two
physics review is separate from this module/API cleanup.

For the bundled single-resonance models, parameter meanings are:

| Parameter | Meaning | Unit / convention |
|---|---|---|
| `w_m` | Resonance frequency | GHz in the bundled functions; fit input is Hz |
| `alpha_r` | Dissipative rate used by the model | MHz |
| `kappa_b` | Maximum coupling scale in `kappa_m` | MHz |
| `gamma_0` | Independent dispersive rate in `S11_node` | MHz |
| `phi` | YIG position phase | radians |
| `A` | Complex-background magnitude scale | dimensionless |
| `phi_0` | Environment phase offset | radians |
| `t` | Environment/cable delay | ns, converted to seconds before `2*pi*f*t` |
| `theta_fano` | Fano phase in the reflection numerator | radians |
| `T_ns` | Phase-line delay `x/v_g` | ns; `2*pi*T_ns*1e-9*frequency_Hz` is dimensionless |
| `f_ref` | Phase-line reference frequency | Hz internally; the GUI displays GHz |

The code defines `Gamma = (kappa_m + alpha_r) / 2` and then uses `Gamma / 2`
in the ideal reflection denominator. This documentation records that exact
implementation rather than silently changing its linewidth convention.

For a verified `kappa_b*sin(phi)^2` model:

```text
phi(f_m) = phi_ref + 2*pi*T_ns*1e-9*(f_m - f_ref)   [radians]
Node:     phi = n*pi       -> kappa_m = 0
Antinode: phi = (n+1/2)*pi -> kappa_m = kappa_b
```

`T_ns` and frequency units make the phase increment dimensionless. The sign
and modulo-period ambiguities of phase fitting remain explicit in fit results.
The empirical Coarse Detector is not this physical model: it detects extrema
of a smoothed transmission metric after a linear resonance trajectory/window.

## Workflow and QC

Single Fit operates on one selected complex trace and records parameter
uncertainty and fit statistics. Continuous Fit applies the same fit per trace
and isolates individual failures. κ and phase estimators consume successful
trace results; Global Fit supports per-slice, shared, fixed, and linked roles.
For linked phase, the engine uses the phase-line constraint implemented in
`core/phase.py`.

Current QC includes finite-sample validation, preprocessing masks, optional
robust loss, per-trace fit status, R² thresholds, and estimator inlier masks.
The status vocabulary is `Good`, `Warning`, `Rejected`, `Failed`, and `Manual`.
Do not attach a status or uncertainty that the calculation did not produce.

## Editing guide

### Files a lab member should edit

- `models/formulas_example.py` - existing example reflection models.
- `models/formula_yig_node.py` - existing YIG Node physical model.
- `core/phase.py` - κ/phase estimation and linked phase equations.
- `node_antinode/physical.py` - verified physical extrema mapping.
- `node_antinode/coarse.py` - conversion of empirical detections to results.
- `results/model.py` - stable analysis result/status vocabulary.
- `public_api.py` - supported public entry points and integration boundary.

### GUI/infrastructure files normally not requiring edits

The HDF5 Reader/Parser, `app/core/data_model.py`, multiprocessing fit engine,
Qt controllers, Viewer actions, external-state stores, `app/gui/plot_export.py`,
and `app/gui/drag_share.py` are application infrastructure. Change those only
when a scientific or user-workflow requirement needs it, and add focused
cross-layer tests.

## Reference traceability

| Feature | Reference source | Production implementation |
|---|---|---|
| Single Fit | `references/fitting/fit_gui/fitapp/core/fitting.py::run_fit` | `yig_fitting/core/fitting.py::run_fit` |
| Continuous Fit | `.../core/batch.py::run_batch` | `yig_fitting/core/batch.py::run_batch` |
| κ fitting | `.../core/phase.py::fit_kappa_line` | `yig_fitting/core/phase.py::fit_kappa_line` |
| Phase fitting | `.../core/phase.py::fit_phase_line` | `yig_fitting/core/phase.py::fit_phase_line` |
| Physical Node | `.../core/phase.py::node_frequencies` | `yig_fitting/core/phase.py` and `yig_mirror/node_antinode/physical.py` |
| Physical Antinode | No explicit latest reference function; relation verified in `formula_yig_node.py` | `yig_fitting/core/phase.py::antinode_frequencies`, `yig_mirror/node_antinode/physical.py` |
| Coarse Node/Antinode | Older `references/node_antinode/` implementation | `app/core/node_antinode.py`, adapted by `yig_mirror/node_antinode/coarse.py` |
| Global / linked fit | `.../core/phase.py::run_global_fit` | `yig_fitting/core/phase.py::run_global_fit` |
| QC | `.../core/cleaning.py`, `fitting.py`, `batch.py` | matching `yig_fitting/core/` modules |
| Result export | `.../core/report.py` | `yig_fitting/core/report.py` and controller export actions |

The copied fit_gui source tree remains under `references/` for provenance and
must never be imported by production runtime. The 437-slice report's custom
`Z:/.../test.py` source is unavailable locally, so that exact run cannot yet
be reproduced or claimed as numerical parity.

## Analysis GUI and plot handoff

The unified entry point is `Viewer -> Analysis -> YIG Mirror Analysis`. The
Analysis code uses the same `PaneRenderSurface`, high-quality composite
renderer, and `DragShareController` used by LabLogViewer plotting. Existing
Matplotlib Analysis canvases rerender their Figure at export DPI; they do not
upscale a screen grab. `FitPlotWidget` owns six independent panes with Grid and
Focus layouts. Data Preview owns independent map/slice panes, and Phase / Node
owns independent κ, phase-line, 2D source-map, and signal-depth panes. Their
splitter state is stored in the Data-identity-bound Analysis session, separately
from fit results.

Every individual pane has View All, Copy, Save, and Pointer / Drag-to-Share
controls. Multi-pane workspaces also provide Copy/Save Active and All panes;
All panes captures their current splitter/focus arrangement. Single-pane SVG
uses Matplotlib's vector export. A multi-pane SVG is an SVG document containing
high-resolution raster-rendered Matplotlib pane images, and is labeled that way
in the Save dialog; it must not be represented as fully vector output.

The Phase / Node workspace composes current Data Model arrays, successful
Continuous Fit records, the estimated phase line, and structured location
results. Its 2D map preserves the frequency-by-sweep orientation from
`[frequency, sweep]` complex S data and labels physical locations separately
from sweep-dependent coarse candidates. The existing signal-depth view remains
available as a fourth plot.

## Model and state provenance

Bundled fit results carry `MODEL_ID` and `MODEL_VERSION`. Single-fit and batch
CSV/report output records those fields. The Analysis external session is keyed
by the existing canonical Data identity and includes GUI splitter/focus state;
it does not copy measurement arrays or write results into HDF5. Location CSV
exports include Data identity, model ID/version, method, type, sweep value,
frequency, phase, coupling, status, and origin where those values exist.

## Tests

From the project environment, run the focused tests with:

```text
python -m pytest tests/test_v015c_yig_fitting.py tests/test_v015d_yig_public_api.py
```

The synthetic estimator tests validate equations and units, but they do not
replace real-data reference parity. GUI behavior is tested separately through
Qt tests and must still receive native macOS and Windows interaction review.
