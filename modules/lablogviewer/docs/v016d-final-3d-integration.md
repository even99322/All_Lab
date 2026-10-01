# v0.16D final 3D correction and integration

## Scope and evidence

This pass continues the current v0.16D working tree. The user reproduced an
abnormal macOS exit after the former journal label `3D Trajectory rendering
completed` (17:39:18 and 17:39:43 local). That label was emitted immediately
after submitting a Qt point update, before the native GPU frame. It was not
evidence of a stable frame, and the native crash root cause is still unproven
without a crash stack and repeated native reproduction.

The current `Q3DScatter` point update removed all 16 color-bucket series,
called `deleteLater()` on each, then immediately attached replacements. A
queued renderer can still hold the retired native objects. The update now
retains each series and its data proxy for the lifetime of the graph; changes
use `resetArray()` and visibility. Existing physical axis formatters are also
reused where possible. This removes a concrete object-lifetime hazard, but
does not establish that it was the sole cause of the reported crash.

The journal now distinguishes geometry request, attachment, update submission,
event-loop reach, a *measured FPS signal* if emitted, a 1.5-second scene-alive
checkpoint, and explicit clear/cleanup. A timeout is not presented as proof
of a rendered frame. The old misleading `rendering completed` label is gone.

## Reference and publication renderer

`development_reference/3d/plot.ipynb` uses Plotly `go.Surface` for the main
surface and a second, flattened Surface with `surfacecolor=S21` and
`opacity=0.7` for the bottom projection. The notebook does not explicitly set
main-surface opacity. It is reference/provenance, not a runtime dependency;
the exact method used to produce the attached paper figure is not fully
recoverable from that notebook alone.

`app/visualization3d/publication.py` is a separate Matplotlib Agg renderer.
The Viewer takes a copy-owned, read-only snapshot of selected scientific data,
the color map/range, geometry, overlays, opacity, axes/units, projection, and
camera. Structured grids use `prepare_surface_grid()` with a 20,000-vertex
feature-preserving export budget. The original scientific arrays remain
untouched. Surface and projection polygons use real RGBA blending; the LabLog
BWR lookup is sampled from the same 2D `get_colormap()` source. Z Scale changes
only display geometry. The Snapshot can render PNG or SVG. The SVG colorbar
remains vector instead of an embedded raster image; Copy, Save, and
Drag-to-Share route to this same renderer instead of capturing the native
viewport. PNG output is 2376 x 1716 pixels at 220 dpi.

The interactive `Q3DSurface` backend still does not reliably blend surface
alpha. The opacity controls explicitly say that true alpha applies to
Copy/Save/Drag output; they do not promise native viewport translucency.
Interactive alpha needs a blending-capable replacement backend with correct
depth ordering, not RGB fading. Matplotlib's 3D painter can also have
occlusion-order artifacts on overlapping transparent surfaces; Dual Surface
requires user visual review.

## State and safety

Data-bound Viewer display state and named Save View now include the 3D camera
pose/target/zoom. Restore applies it after rendering. Existing Safe Recovery
still suppresses Viewer/operation restoration after an abnormal previous exit;
this pass does not bypass it. 3D remains single-pane only.

The publication renderer is lazy-loaded. Matplotlib is already in
`requirements.txt`. It renders synchronously, bounded to 20,000 selected
vertices; the measured real-data PNG took 3.72 seconds on this host. Copy and
Drag can briefly block the GUI during publication rendering. Asynchronous
export/cancellation is a known remaining UX improvement, not a claim of
non-blocking export.

Qt's camera rotations and pan target are mapped into Matplotlib azimuth,
elevation and physical axis limits. This represents the current pose/zoom and
scientific state, but is not a pixel-identical camera reconstruction between
two different 3D projection engines. Trajectory remains a native colored
point sequence; its publication representation is a connected XYZ line.
Scatter publication currently uses the native-prepared 20,000 point budget.

## Validation

- Baseline full suite: 736 passed, 2 skipped, 31 warnings, 320.10 s, exit 0.
- An intermediate full run exposed one compatibility regression in the mouse
  test's minimal fake renderer. It was corrected. That run had 744 passed,
  2 skipped, 1 failed, 31 warnings; cleanup did not exit promptly and was
  interrupted (exit 130). It is not the final regression result.
- Focused publication, mouse, corrections, and export/session tests passed.
- Final complete suite after all source edits: 750 passed, 2 skipped,
  31 warnings, 281.32 s, exit 0. Normal Qt shutdown completed without
  cleanup recursion. The warnings are existing pyqtgraph/NumPy deprecations.
- Real read-only RSMEP fixture: `Data/PRL RSMEP best data/2026/08/Data_0828/0828 RSMEP_1.hdf5`.
  Source grid 855 x 501, publication grid 184 x 108, snapshot preparation
  0.134 s, PNG through full renderer 3.722 s, 806,729 bytes. The output was
  visually inspected: RGBA Surface, colored floor projection, and BWR colorbar
  are present. These are one-run wall times, not GPU frame-rate metrics.
- Native interactive Qt validation was not possible in this session: the
  offscreen Qt plugin reports `OpenGL: False`, and the computer-control tool
  exposes no native app surface. The reported crash is **not** claimed fixed.
- Windows-compatible Qt mouse APIs, `pathlib`, and Matplotlib Agg are used;
  native Windows testing was not available.
- Baseline HDF5/H5 manifest contains 120 files; byte-integrity comparison
  passed 120/120 after implementation. The 3D reference notebook and HTML
  were read, not edited.

## User native validation required

1. Open the RSMEP Viewer, switch Surface -> Trajectory -> Surface -> Scatter
   -> Trajectory several times, change trace and color map, close, and relaunch.
   Check for abnormal exits and capture the macOS crash report if one recurs.
2. Right-click a 3D graph and try Copy and Save PNG/SVG. In Drag-to-Share mode,
   drag a 3D graph into a native destination. Verify the files show the current
   angle, color range, projection and alpha rather than a viewport screenshot.
3. Compare 100/75/50/25 percent opacity with Bottom Projection, Reference
   Plane and Dual Surface in publication output. Interactive transparency is
   explicitly not established with the current Qt backend.
4. Save View, change camera/geometry, reload it, then cleanly restart. Confirm
   3D state returns. After any abnormal exit, confirm Safe Recovery does not
   auto-open the risky 3D Viewer.
