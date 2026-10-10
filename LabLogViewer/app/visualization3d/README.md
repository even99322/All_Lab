# 3D visualization (v0.18A hybrid engine)

- `graphs_renderer.py` — Qt Graphs 3D renderer for Surface, Transparent
  Surface, Dual Surface and Waterfall. `graphs_available()` selects it when
  `PySide6.QtGraphsWidgets` exists and the Qt platform is not offscreen/minimal.
- `display_grid.py` — evenly spaced, peak-preserving display grids required by
  `QSurfaceDataProxy.resetArrayNp`; display cells record their source indices.
- `renderer.py` — Qt Data Visualization renderer, now used for Trajectory and
  Scatter (and as the fallback for everything).

Verified PySide6 6.11 constraints (macOS, native):

- `resetArrayNp` does not own its numpy buffer; `reset_surface()` keeps the
  last two buffers per series referenced (otherwise a native crash).
- Python `QValue3DAxisFormatter` copies returned by `createNewInstance()` must
  stay referenced; formatters are updated in place and `markDirty(True)`.
- `Q3DSurfaceWidgetItem` must be Qt-owned (parent = renderer) and created
  before its `QQuickWidget`, or interpreter shutdown crashes.
- `QCustom3DItem` (built-in, `.obj` or balsam `.mesh`) does not render, so
  reference planes are 2 x 2 surface series. NaN heights do not create gaps;
  they are drawn at the floor with alpha 0.
- Default transparency blending produced false colors; Approximate OIT gives
  correct colors (Qt may log an "invalid blend modes" notice).
- Data Visualization `MeshPoint` draws nothing on macOS; point geometries use
  low-poly spheres. Keep Python references to `QScatterDataProxy` objects.

# Scientific 3D Mapping System (v0.17B)

## Data and Renderer Boundaries

This package only creates display representations. It never reads or writes
HDF5. The Reader/Parser/Data Model owns scientific arrays; the Viewer requests
an existing `Grid2DData` slice and maps it to geometry. Structured Surface
data retains the `z[y, x]` invariant. Display transforms and LOD indices do not
replace the source arrays.

```text
Experiment / CachedExperiment
  -> Viewer selects dimensions, channel, transform, color mapping
  -> mapping.py resolves geometry-specific display arrays
  -> data.py / lod.py validates and prepares indexed structured grids
  -> renderer.py applies the persistent Qt Data Visualization series
  -> camera-only events reuse prepared data and existing renderer resources
  -> renderer.py renders the active Qt scene for Copy/Save/Drag
```

`mapping.py` is the geometry adapter boundary. The current UI supplies regular
2D experiment slices; `prepare_point_cloud` also accepts matching arbitrary
XYZ arrays as a pure preparation API, but no HDF5/unstructured point-source
picker is wired into the Viewer yet.

## Geometry and Mappings

- **Surface** uses the existing `Q3DSurface` series and LOD path. X/Y are the
  selected experiment dimensions; Height is the selected channel transform.
- **Dual Surface** lays two transforms of the same selected channel and X/Y
  coordinates in a common physical-height normalization. Each gets its own
  opacity. Surface B currently uses a fixed contrast color rather than an
  independent scientific color transform.
- **Waterfall** chooses trace and frequency samples with endpoint/curvature
  anchors, and inserts NaN separator rows before the Qt wireframe path. These
  rows prevent a wire from connecting adjacent traces. It reuses the persistent
  Surface renderer and bounded feature-aware display budget; it is not a new
  native line-series renderer.
- **Trajectory** and **Scatter** use a lazily-created native `Q3DScatter`.
  Mappings can use the selected X/Y dimensions, zero-based C-order Point / Index,
  Real, Imaginary, Magnitude, Magnitude (dB), Phase, or Unwrapped Phase. The
  current Trajectory representation is a colored point sequence for the active
  trace, not a connected parametric polyline. Point colors are represented
  by up to 16 uniform-color series, so they are an explicitly quantized display
  approximation to the continuous colorbar. Both point modes are limited to
  20,000 prepared display samples per native update. The selected Trajectory
  trace must have at least two finite mapped points; original row-major source
  indices are retained for readout.

Wrapped Phase keeps its discontinuities. Unwrapped Phase runs `numpy.unwrap`
  independently over finite contiguous segments along the chosen grid axis:
  X/Frequency is axis 1 and Y/Sweep is axis 0. Degree and radian forms preserve
  their selected unit. The flattened point index uses row-major `z[y, x]`
  ordering and remains mapped to the original grid index.

## Surface Color, Overlays, and State

The 2D plot, 3D ramp, surface shader/texture, bottom projection, and top
interactive bar sample the same pyqtgraph LabLog BWR map. With Height as Color
Source, the Qt range gradient uses the scientific limits; a separate Color
Source uses an RGBA texture. Dragging either top range handle updates the shared
Minimum/Maximum controls, 2D Heatmap, Surface, and projection. The range update
changes color only; it does not rebuild the height grid.

Opacity calculations change the RGBA alpha channel only. RGB values and
scientific ranges are invariant. **The native QSurface3DSeries remains opaque
on tested macOS Qt Data Visualization, even with an RGBA texture.** Surface
and Dual Surface opacity controls therefore do not promise visual alpha in
the Viewer or export. The Reference Plane uses `reference_plane.obj` as a
`QCustom3DItem` with an RGBA texture (alpha 90/255) in the *same* graph. Native
macOS pixel testing confirmed that this item blends over the scientific
surface while the old semi-transparent `QSurface3DSeries` rendered opaque dark.
The plane position and width/depth use the same normalized axis mappings as
the surface; a minimum plane gets a small display-only depth offset from the
floor projection to avoid z-fighting. Its reported scientific value is not
changed. Include `reference_plane.obj` as a data asset when bundling with
PyInstaller.

Copy, Save, and Drag call `render_plot_image()` on the active Qt graph.
`QAbstract3DGraph.renderToImage` renders the current geometry, camera,
projection mode, overlays, axes, and colors at 3x the viewport resolution;
the existing QWidget colorbar is painted alongside at the same scale. The
graph's viewport aspect ratio is unchanged. PNG is a native rendered image;
SVG embeds that image and is **not vector 3D geometry**. `publication.py` is
retained for historical standalone tests but is no longer called by Viewer
export. Export now faithfully shares native Surface opacity limitations.

## Transparent Surface Geometry

`Transparent Surface` is an explicit alternative Geometry for one structured
surface. Its renderer was migrated from
`development_reference/transparent/git_transform/app/visualization3d/transparent_renderer.py`;
runtime imports only `app/visualization3d/transparent_renderer.py` and does not
depend on the development reference tree.

The renderer preserves `z[y, x]`, builds two indexed triangles per grid cell,
excludes faces touching non-finite height/color samples, and colors each face
from the mean of its three scientific color values. Faces are depth-sorted and
alpha is applied per face. This is painter-style sorting, not order-independent
transparency, so intersecting/overlapping facets can show sorting artifacts.
Transparency blends screen colors; use the scientific colorbar/readout for
exact value comparison.

The display-only budgets follow the reference: Auto/Adaptive up to 6,000
vertices, Performance up to 2,000, a 1,400-vertex interaction view, and Full
Resolution up to 12,000 vertices. Larger Full Resolution requests are rejected
with an explanation; native opaque Surface remains the high-density path. The
source Data Model and HDF5 are unchanged. Picking chooses the nearest displayed
sample, not an interpolated point on a triangle.

Native Qt graphs and the Matplotlib transparent geometry use an XYZ box ratio
of (1, 1, 0.72), matching the reference's relative shape. Native scenes share
a light gray scientific theme; Transparent Surface uses a light LabLog palette
instead of the reference's graphite background. It owns its figure colorbar;
the native interactive top colorbar control is hidden for this Geometry.

Transparent Surface shares Viewer camera state/presets, Z Scale, color
source/range, and high-resolution Copy/Save/Drag rendering. Dual Surface,
Waterfall, Trajectory, and Scatter stay on their native Qt renderers. Camera
interaction changes view state only and does not reread or transform data.
The project reference `development_reference/3d/plot.ipynb` uses Plotly
`go.Surface` plus a floor Surface with `opacity=0.7`; it does **not** set top
Surface opacity. The paper image's exact top-surface production method is not
established by that notebook. General alpha on the native Qt Surface remains
backend-limited; the separate Transparent Surface Geometry uses the Matplotlib
face-blending path documented below.
The projection sits at the current display-height floor and uses the same map.
Minimum/Custom/Zero reference planes are separate geometry and report the
unscaled physical value and unit.

Viewer display state is external to measurement HDF5 and now includes geometry,
axis/channel mappings, transforms, phase direction, color source/range,
colormap, opacity, overlays, point mappings, rendering policy, point size,
and camera pose/target.
Save View and normal per-Data display restore reuse that same state structure.
All 3D geometries are single-pane only. Entering 3D closes a 1D/2D Multi-Pane
layout and hides the Pane Layout/sync controls. Returning to 1D/2D restores
those controls and their normal layout choices. Multiple objects in one 3D
scene (Dual Surface, projection, reference plane) are not multiple panes.

## Limits and Validation

The Surface path retains the persistent `Q3DSurface`, `QSurfaceDataProxy`,
scientific BWR binding, normalized reversible axes, feature-aware indexed LOD,
96 MiB bounded cache, generation-token stale-result rejection, and background
NumPy preparation from v0.16C. Camera events do not reread HDF5, rerun the
transform, or regenerate the structured LOD pyramid. Waterfall uses its own
feature-aware row/column selection before passing data through that renderer.
Trajectory/Scatter currently use a fixed 20,000-point display budget and do
not yet have an interaction/idle LOD ladder.

The v0.16D correction pass validates real finite XYZ/color arrays before
creating a native point graph. Trajectory uses the active trace rather than
flattening all sweep rows into one apparent path. Subsequent user evidence
showed an abnormal exit *after* the previous "rendering completed" journal
label. That label only meant `set_point_cloud` returned, not that Qt displayed
a frame. The graph now reuses up to 16 persistent native series/proxies rather
than removing and deleting them during each update, avoiding an identified
queued-render lifetime risk. The journal distinguishes geometry update,
scene attachment, update submission, event-loop reach, first measured FPS,
and scene-alive timeout. The exact native crash cause remains unproven without
a native crash stack and repeated macOS reproduction; user validation is
required. A Trajectory is still a point sequence, not a connected line in the
native graph; export preserves that point-sequence representation rather than
inventing a connected line.

### CPU preparation comparison

One same-machine pass compared the v0.16C archive source and this working
tree using the same 429,357-sample real measurement and a 9,000,900-sample
synthetic grid. These are CPU-side preparation timings, not GPU frame-rate or
native render measurements; filesystem/cache warm-up and single-run noise
apply. The synthetic case contains a narrow resonance-like dip and a broad
feature. Its LOD stage already existed in v0.16C, so it is not an acceleration
introduced by the new geometry modes.

| Workload / stage | v0.16C (ms) | v0.16D (ms) | Observation |
| --- | ---: | ---: | --- |
| 429k parse | 16.57 | 16.80 | Comparable |
| 429k raw slice | 35.82 | 31.79 | 1.13x faster |
| 429k magnitude-dB slice | 33.43 | 38.46 | 1.15x slower |
| 429k full mesh mapping | 24.55 | 20.09 | 1.22x faster |
| 429k BWR mapping | 30.35 | 26.79 | 1.13x faster |
| 429k 100k LOD preparation | 29.92 | 22.51 | 1.33x faster |
| 9M Surface LOD to about 100k vertices | 390.67 | 427.32 | 1.09x slower |
| 9M retained-grid mesh mapping | 3.56 | 1.38 | CPU-only; small result |

Peak process RSS for the full one-pass script was 160.8 MiB in v0.16C and
152.1 MiB in v0.16D on the 429k section, and 389.3 MiB versus 382.6 MiB after
the 9M synthetic section. This is process high-water RSS, not GPU memory and
not an isolated per-renderer allocation. The mixed results do not demonstrate
a general 2x speedup; GPU FPS, interaction latency, and overlay transparency
cost remain unmeasured because this session has no native rendering context.

The automated Qt widget, scientific mapping, state round-trip, and CPU LOD
tests run offscreen. The v0.17refixed Reference Plane and Qt export path also
received native macOS OpenGL pixel tests and a real 855x501 HDF5 Viewer/export
run. The user's two requested Viewer/export comparison images were not attached
to this task; **user native visual acceptance is still required** for that
exact scene. Native Windows rendering validation has not been performed.

The 3D implementation is spread across `mapping.py` (geometry adapters),
`data.py` (structured validation/scene mapping), `lod.py` (feature-aware
indices), `policy.py` (render budgets), `cache.py` (bounded prepared levels),
and `renderer.py` (Qt graph and camera). GUI control/persistence wiring is in
`app/gui/main_window.py`.

Run focused tests with:

```bash
python -m pytest -q tests/test_v016d_geometry_mapping.py tests/test_v016c_surface_lod.py tests/test_v016b_surface_policy.py tests/test_v016b_acceptance_patch.py
```

This package renders an existing `Grid2DData`; it never reads or writes HDF5.
The Reader/Parser/Data Model owns original scientific arrays. The Viewer asks
`CachedExperiment.get_nd_slice(...)` for the chosen X/Y, height channel, and
transform. The display path must preserve `z_values[y, x]`:

```text
Grid2DData (complete transformed scientific array)
  -> data.prepare_surface_grid (validation and selected original indices)
  -> data.prepare_surface_mesh (normalized Qt coordinates, display Z Scale)
  -> QSurfaceDataItem rows / QSurfaceDataProxy / Q3DSurface
```

The original array remains the source for analysis and export. LOD is an index
map into it, not a scientific resampling or HDF5 migration. Height and Color
Source use the same selected indices. The selected original row/column indices
are stored in `SurfaceGrid`; Qt picking reports the **exact original sample at
the selected rendered vertex**, not an interpolated region or Z-scaled value.
Picking between vertices is Qt's nearest selectable rendered vertex, so fine
readout of an omitted sample requires a finer LOD/full representation.

## Color and filled-surface diagnosis

The 2D plot, 3D color ramp, and 3D shader/texture all use the same pyqtgraph
LabLog BWR color map. With Height as Color Source, a `ColorStyleRangeGradient`
maps Qt's normalized height coordinate back through the scientific color
range. An independent Color Source uses an RGBA texture. Z Scale does not
alter scientific values or color limits. Camera events never change the
underlying value-to-color mapping.

Qt's `QSurface3DSeries` defaults to `DrawSurfaceAndWireframe`. The previous
renderer did not override it. At 857 x 501, roughly 857,000 dark mesh edges
can overwhelm filled faces at normal zoom, even when the colorbar and shader
are correct. The renderer now explicitly uses `DrawSurface` only. Unit-level
isolation confirms the default and the filled-only setting across 50 x 50,
100 x 100, 287 x 251, and 857 x 501 configurations. This is a strong
code-level root-cause candidate, **not a native pixel proof**: this development
session cannot create a working macOS OpenGL context. The 857 x 501 first
frame and camera/color invariance still require user native visual acceptance.

## Policy, LOD, and cache

`policy.py` preserves Auto, Full Resolution, Adaptive LOD, and Performance.
Auto keeps up to 1M source points full. Larger Auto grids initially show a
50k representation and refine to a 100k feature-preserving representation in
the background. Interaction uses a viewport/zoom/FPS-informed budget up to
50k; after a 180 ms idle debounce it returns to the idle representation.
Explicit Performance uses 50k. Explicit Full Resolution above 2M vertices
is refused with a clear safety error because constructing millions of Qt
objects in one GUI event would risk a freeze or crash. This is a known backend
limit, not a reduction of the scientific source.

`lod.py` keeps boundary indices and global extrema where the vertex budget
permits, then chooses an axis index in each interval from maximum local
curvature across the opposite dimension. This retains narrow peaks/dips more
reliably than uniform stride. Because a Qt rectilinear grid needs shared
row/column indices, not every narrow local feature at every sweep point can
be guaranteed at finite budgets. The original arrays remain intact.

`cache.py` is a 96 MiB LRU cache for the current source's prepared levels.
Keys include the source array identities, channel/transform/axis state, and
LOD budget; they deliberately exclude camera orientation. New source or
Color Source invalidates the cache. Worker results have generation tokens;
stale results are discarded. NumPy LOD selection runs in a Qt thread-pool
job; only the GUI thread creates or resets Qt graph objects. The first large
LOD is also prepared in the worker. Camera moves use existing representations
and do not reread HDF5, reparse, or rerun scientific transforms.

## Geometry, interaction, and diagnostics

Qt's X and Z form the horizontal plane; Qt Y is vertical. The mapping is:

```text
Qt X = normalized scientific X
Qt Y = normalized scientific Z * display-only Z Scale
Qt Z = normalized scientific Y
```

The native graph owns a persistent proxy/series. Left drag rotates after an
8-pixel threshold; a short left click picks a scientific sample; middle drag
is consumed as pan only; wheel zoom is native. During pan, orientation stays
fixed. Right drag is not a camera binding. Multiple held buttons do not
combine operations.

Diagnostics expose source/rendered grid, active LOD budget, cache bytes,
NumPy preparation time, Qt item-build/reset times, and Qt-reported FPS/frame
time when available. FPS and GPU upload timing are **not** inferred from CPU
preparation. The OpenGL probe prevents native graph construction when no
context exists; tests can still validate CPU LOD, color mapping, and Qt series
configuration. On this machine the probe exits 139 without a context, so
first-frame pixels, GPU upload, and native interaction FPS are unmeasured.

## Files and tests

- `data.py`: validation, selected source indices, normalized scene geometry.
- `lod.py`: feature-preserving rectilinear index selection.
- `cache.py`: bounded prepared-level cache.
- `policy.py`: idle and viewport-aware interaction budgets.
- `renderer.py`: Qt series, background jobs, color binding, camera interaction.
- `availability.py`, `diagnostics.py`, `state.py`: probe, metrics, camera state.
- `tests/test_v016c_surface_lod.py`: color configuration, LOD, cache, staleness,
  interaction transition, and source-integrity tests.

Run the focused tests with `python -m pytest -q tests/test_v016c_surface_lod.py`.
Then run the complete suite. A native macOS visual check remains necessary
before claiming the black-surface blocker is fully resolved.
