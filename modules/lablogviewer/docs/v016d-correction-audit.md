# v0.16D Correction Audit

Status: **PARTIAL BUT DELIVERABLE**. This is the current v0.16D working tree,
not a rollback or a new version. The supplied publication image is a visual
transparency target, not evidence that the current native renderer meets it.

## Baseline and regression

- Baseline before this correction: 721 passed, 2 skipped, 31 warnings,
  251.55 s, exit 0 after delayed Qt teardown. No cleanup recursion was
  observed in this run.
- Final complete suite: 736 passed, 2 skipped, 31 warnings, 242.54 s,
  exit 0 after delayed Qt teardown. The warnings are pyqtgraph's existing
  NumPy 2.5 array-shape deprecation warnings in the Multi-Pane tests.
- Focused 3D, multi-pane, tag and recovery tests passed. No scientific HDF5
  files were edited; all 120 source/reference HDF5/H5 files in the baseline
  SHA-256 manifest verified byte-identical after the final suite.

## Trajectory crash

The user-observed termination followed `3D Trajectory rendering started`
without a captured Python exception. The native crash could not be reproduced
offscreen, so its exact native root cause is **unproven**. Code review found a
real resource and semantics hazard: every sweep row was flattened into one
apparent trajectory, up to 100,000 native scatter items were created across
up to 32 Qt series, and malformed/complex mappings could reach graph setup.

The corrected path prepares only the active trace for Trajectory, validates
real finite X/Y/Z/color arrays and at least two points before native graph
creation, retains original `z[y, x]` source indices, and caps point modes at
20,000 display samples / 16 color buckets. The renderer independently rejects
invalid or oversized clouds before calling `set_geometry_type`; removed Qt
series are scheduled for deletion. The operation journal now distinguishes
preparation, validated data, renderer update and completion/failure. This is
defensive correction, **not proof of native crash elimination**. User native
Surface -> Trajectory -> Surface testing remains required.

## Transparency

The v0.16D CPU pipeline correctly changes RGBA alpha without altering
scientific RGB, Z values or color range. Tests cover 100%, 75%, 50% and 25%.
The defect is downstream: the existing Qt Data Visualization `Q3DSurface`
path accepts alpha-bearing colors but does not provide a reliable blended,
depth-sorted translucent surface. Inspection of its OpenGL surface renderer
shows alpha blending enabled for labels/background, not the surface pass.
The newer Qt Graphs API exposes an explicit transparency technique, but that
is a different backend, not an option on the current `Q3DSurface` instance.
See [Qt Data Visualization Q3DSurface](https://doc.qt.io/qt-6/q3dsurface-qtdatavis.html),
[Qt Graphs Surface3D transparency](https://doc.qt.io/qt-6/qml-qtgraphs-surface3d.html),
and the [Qt surface renderer source](https://codebrowser.dev/qt6/qtdatavis3d/src/datavisualization/engine/surface3drenderer.cpp.html).

To avoid a misleading control, Surface A/B opacity sliders are disabled with
a backend-limitation tooltip. Existing saved opacity values remain readable,
but **true interactive translucency is not implemented**. No fake RGB fading
was introduced. Surface + Bottom Projection, Surface + Reference Plane and
Dual Surface at partial opacity are not visually validated. A future renderer
with explicit blending/depth ordering is required to meet the reference image.

## Single-pane and persistence

All 3D geometry modes force one viewport and hide the Pane Layout, Active
Pane, Sync Trace, Sync X and Reset Layout bar. A 1D/2D Multi-Pane layout is
closed on entering 3D; 1D/2D layout controls remain available after leaving
3D. A restored 3D session also overrides stale PaneState mode/layout so it
cannot reopen a 2D Multi-Pane view. Offscreen interaction tests cover these
transitions; a native resize pass remains required.

TagStore's locale-dependent `read_text()` caused the reported CP950 failure
on a valid UTF-8 `tags.json`. Current tags, legacy tag imports, OverlayStore
and AxisPresetStore now read explicit UTF-8, matching the existing UTF-8
atomic writer. Other app-owned JSON/text persistence was audited: the shared
external state loader and fitting/settings text paths already specified their
encoding. Unicode tests cover `Mirror`, `測試資料`, `テスト` and
`RSMEP_最佳資料`, including a simulated non-UTF-8 locale. Valid UTF-8 no
longer creates a false `.bak`; genuinely corrupt JSON still does. Existing
backup files are not deleted or migrated.

## v0.16D feature review

`PASS` means code/test review only unless native validation is explicitly
listed. A CPU/offscreen pass never implies verified native OpenGL pixels.

| Feature | Status | Evidence / remaining work |
| --- | --- | --- |
| Surface, BWR, existing LOD/cache/camera | PASS | v0.16C core retained; focused and full regression pass |
| Surface opacity, A/B translucency | KNOWN LIMITATION | Backend lacks reliable surface blending; controls disabled |
| Bottom Projection | USER NATIVE VALIDATION REQUIRED | Mapping/series wired; layer visibility not pixel-verified |
| Reference/Zero Plane | USER NATIVE VALIDATION REQUIRED | Physical height mapping wired; depth appearance unverified |
| Top colorbar, shared draggable range | PASS | Qt signal and linked 2D/3D state tests pass |
| Height/Color source decoupling | PASS | Separate source/transform mapping and color tests pass |
| Real, Imaginary, Phase surfaces | PASS | Transform/mapping tests pass; native pixels unverified |
| Unwrapped Phase, axis and unit choices | PASS | Finite-run/axis/orientation tests pass |
| Dual Surface | KNOWN LIMITATION | Shared physical height works; partial-alpha overlap does not |
| Waterfall | USER NATIVE VALIDATION REQUIRED | Feature-aware row/column tests pass; native line view unverified |
| Trajectory / IQ | FIXED | Selected trace, pre-native validation, bounded resources; crash re-test needed |
| Scatter | FIXED | Pre-native validation and bounded Qt series; native graph unverified |
| Geometry classification | PASS | Separate geometry/transform selection tested |

Compared with v0.16C, the `lod.py`, `cache.py`, `policy.py`, camera interaction
and Safe Recovery foundations remain intact. The v0.16D modifications are
geometry/mapping extensions plus this correction. Left-drag Rotate,
middle-drag pure Pan, wheel Zoom and scientific picking remain covered by
existing regression tests, but were not natively operated in this session.
Safe Recovery still suppresses dangerous Viewer restoration after abnormal
exit; no recovery code was replaced.

## Native/platform status and delivery

Native macOS GUI/OpenGL was unavailable from this task's computer surface;
therefore no macOS pixel-level transparency or point-graph crash claim is
made. Windows native execution was also unavailable. Qt mouse and object
handling remains platform-neutral; the CP950 failure is covered by explicit
UTF-8 reads and locale-independent tests. User native validation is required
for Trajectory switching, first-frame 3D color, pane resize and every
transparent-overlay scenario. The 437-trace fitting reference limitation is
unrelated and unchanged.

Working project: `/Users/liuyushu/Desktop/lab browser/LabLogViewer_v0.16D`.
The earlier `LabLogViewer_v0.16D.zip` and v0.16C archives are immutable.
Corrected snapshot: `之前預存檔/LabLogViewer_v0.16D_20260924T1548.zip`.
No `.venv`, caches, temporary benchmark data or scientific HDF5 are included.
