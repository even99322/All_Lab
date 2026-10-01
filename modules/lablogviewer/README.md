# LabLogViewer

A lightweight native viewer for Labber HDF5/H5 measurement files.

**Current version: v1.0.3.** v0.9D was cancelled and does not exist. The
planned v0.9F and v0.9G work was completed as one consolidated batch.

## v1.0.3

Folder LabLogViewer_v1.0.3, copied from v1.01. From this version on the program uses the same
three-part numbers as the lab's Lab APP release tags (Lab APP published v1.01 as v1.0.2);
two-part 1.x names are compared as 1.0.x.

- De-background output can be written to NAS/SMB shares (no hard links there: the name is
  checked, then the temporary file is renamed).
- Saving where a file of the same name exists asks Overwrite / Keep Both / Cancel everywhere
  (Rename File and New Folder: Keep Both / Cancel).
- Re-link merges records instead of skipping them when the new path already has records,
  asks only for real conflicts, and has a quick Match by Path mode; Data Transfer uses the
  same merge rules.
- What's New lists every version since the previous one.

## v1.01

First release numbered 1.x (folder LabLogViewer_v1.01, copied from v0.19E).
- Auto refresh of the opened database (default every 2 s, Settings > General): only
  sizes and times are compared, only new or changed files are read; selection and
  scroll stay; a Viewer follows a running measurement and keeps its view.
- New Folder from the Browser (right-click in Folders, or File > New Folder...);
  empty folders are shown.
- Data opened for the first time starts as a 2D heatmap (when it has a sweep); data
  worked on before opens the way it was left.
- First start after an update: records upgraded with a backup when needed, What's
  New shown once (also Settings > About > What's New...).
- Data Transfer inside LabLogViewer (licence feature "migrate"), replacing the separate
  Migrator plugin.
- Licences: several issuers' keys can be trusted (a successor's key is added; earlier
  licences keep working).
- Faster: a Viewer is built ahead of time (opening data about 1.5 s -> 0.35 s),
  Matplotlib loaded only when needed, the packaged app keeps Matplotlib's font cache
  (it was rebuilt on every start), lighter theme event handling.

## v0.19E

- Plugins now live together in `../LabLogViewer_Plugins/` (DevTools, and the new
  **Migrator**: moves history from an old data folder into a new one, matching
  measurement files by content, with backup, report and undo). `build_guard.py`
  refuses a build copy that contains plugins, `_sandbox` or a developer key file.
- Fix: with a substitute HOME (tests, sandboxes) the first-run import of older
  data no longer reads the real user's `~/Library/Application Support`.
- Help has two looks, switched with the buttons on the right. **Folders** (default):
  one animated folder per topic (a port of rare-ui's folder component); opening it
  lays the guides out as sticky stacking cards, pictures filling most of each step
  card; glass tabs under the top search list every topic's guides. **List**: the
  Tips-style pages below, with pictures more than twice as large. A one-click
  "Copy all guides for an AI assistant" button sits on the same rail.
- Help rebuilt in the style of Apple's Tips: grouped cards with icons, article pages
  that open with the shortest working procedure (one annotated picture per step),
  then folding sections for options, what the result means and what to check.
- All 37 articles rewritten in English and Traditional Chinese and checked against
  the code; YIG, Continuous Fit and Phase / Node have beginner and reference parts.
- Every picture is a real operated state with the control outlined; one English set
  shared by both languages; thumbnails zoom onto the outlined control.
- Fixes found while writing Help: Continuous Fit row selection error, cut-off
  Continuous Fit buttons, stale "Active Pane" label, inaccurate data-folder note.

## v0.19D

- Scientific Figure Builder (Browser > Processing): AutoCAD 2013 DXF (MS: b/g/h,
  CPW: db/ub/g/h/v) -> 3-D device figure with screw holes cut through every
  layer and purple copper vias; drag YIG spheres and coils; freely resizable
  sine waves; layer colours / thickness / order; undo; runs in its own process.
  Export: PowerPoint with every part a separate shape using PowerPoint's own
  3-D (depth, bevel, lighting, rotation), PNG / SVG / PDF.
- Retrieve by Tags can search the largest data folder (Settings > General);
  automatic Tags only reach files at most 4 folders below the opened database.
- Theme ripple from the pressed button (colours fight when the look stays the
  same), button press spring (0.96 + inner shadow, overshoot on release),
  liquid-glass scroll bars floating over the content.
- Every Help page has an English screenshot.

## v0.19C

- Personal colours unlock after 10 data operations on different measurement
  files (Marks, transforms, formula, Zoom, YIG fit; opening alone does not
  count). Icons are not locked.
- Licences (`.llvkey`, Settings > About > Licences...): signed by the
  developer, bound to one computer's machine code, valid about two months.
  Features: `personal_colors` (unlock early) and `relink`.
- Hidden Re-link Moved Data tool (licence feature `relink`): finds moved files
  by content fingerprint and moves their records, with backup and undo.
- `app/_guard/` holds all lock logic; `build_guard.py` compiles it (Cython)
  into a build copy and embeds the public key. The developer tool is the
  separate folder `LabLogViewer_Plugins/DevTools` (never shipped).

## v0.19B

- Network Workspace traffic is encrypted (X25519 + AES-GCM; needs `cryptography`).
- Settings > Personal: your own theme / plot / pen colours (colour wheel or
  colour code) and your own SVG icons.
- Viewer Zoom tool (drag a rectangle; right-click / Esc steps back) and Mac
  trackpad gestures (pinch zoom, two-finger pan; 3D rotate).
- Help window (top menu Help, F1) in English and Chinese, and Export for AI.
- Cloud-synced folders are refused for history records unless you accept the risk.
- Content fingerprints of opened files (for re-linking records after moves).

## v0.19A

- Network Workspace (Browser, very top): one computer hosts, up to 5 on the
  same network follow it by join code. Clients see the Host's Viewer and YIG
  Analysis live (view only; 3D is not shared) and the Host's pen / laser.
  Data comes from a shared-folder copy when the Client has one, otherwise
  from the Host (memory only); only the Host can send a permanent copy.
- Network quality (RTT, throughput) is measured every 2 s; below 200 ms /
  1 MB/s hosting or joining is refused. Reconnect, resume, checksums.
- The Host sends the parser's data model, not files or screenshots:
  Empty / Function / Scalar, every Sij and complex data are preserved.
- Each computer has a user name (asked on first use).

## v0.18D

- YIG Analysis: the Residual line follows the plot appearance (light text
  color on Dark plots), including in exports.
- The Annotation button is at the top right in every window: the far right
  of the Viewer, 3D and Browser toolbars, and a larger round button in the
  YIG Analysis tab-bar corner (right of Data Preview / Grid View).
- All colors live in `app/palette.py` (12 sections: app theme, plot
  background, liquid glass, arrows, status text, stars, annotation, Mark
  tools, panes/traces, 3D view, 3D publication export, analysis markers).
  Other modules import names from it and contain no color literals. Tree and
  spin-box arrows are generated from `palette.ARROW` at runtime.
- Icons moved to the version folder's `icons/` directory (next to `app/`).
- Everything kept outside HDF5 lives in one data folder, `LabLogViewerData`
  (macOS `~/Documents`, Windows `C:\Users\<user>`), movable from
  Settings > General > Data Folder. Data from earlier versions is copied in
  on first launch; the originals are kept.
- 3D window: Open HDF5, Pointer / Drag to Share. Settings gains a 3D section
  and a Settings menu in every window.
- One safe formula engine for the Viewer and YIG (LaTeX, Viewer and Python
  spellings; no eval/exec); untrusted .py models ask before they run and
  dangerous ones are blocked.
- Hand-written formula previews (fractions, matrices, cases, aligned) that
  size to the equation.
- 3D Surface runs in its own process (a crash closes only the 3D window);
  an unresponsiveness watchdog writes diagnostic reports.
- Windows: UTF-8 (+BOM) records, locked-file retries, long HDF5 paths.
  Requires PySide6 6.8+.

## v0.18C

- Screen Annotation in the Database Browser (Quick Preview), Viewer (1D/2D and
  multi-pane), YIG Mirror Analysis and the 3D Surface window: an Annotation
  icon opens a liquid-glass toolbar at the bottom of the window with Pen
  (red / blue / yellow / green, adjustable width), Laser Pointer (red, fading
  trail 0–1.5 s) and Clear Screen.
- Annotation is a transparent sheet fixed to the screen. It is display only:
  never written to HDF5, never saved, not part of exports, and it does not
  change data, transforms, formulas or analysis results. Closing Annotation
  removes the drawing.
- Right-button drag draws; the left button and wheel keep panning, rotating
  and zooming. Plot right-click menus pause while Annotation is on. Layout
  changes (Plot Mode, multi-pane layout and splitters, Maximize, YIG tabs and
  pane layout, 3D geometry) are locked until it closes.
- Strokes are smoothed and outlined with a contrasting halo so they remain
  visible on any colormap. Clear Screen removes the ink at once and animates a
  glass "suction" of the sheet into the Annotation button.
- Trajectory / Scatter (native OpenGL point graph) use a floating transparent
  window that follows the plot.

## v0.18B

- 3D Surface moved out of the Viewer into its own window: Analysis → 3D
  Surface... (like YIG Mirror Analysis). It follows the Viewer's current Data
  and hosts all 3D settings; Plot Mode is now 1D / 2D only and the Viewer's
  pane layout, X/Y Cut and plot export are independent of the 3D window.
- Publication-style 3D export (default): a Matplotlib journal figure with
  serif type, light grey panes, the exact scientific colormap, a color-mapped
  floor projection, an upper-left horizontal colorbar and SI-scaled axes
  (GHz, mA). PNG / PDF / SVG; dense fills are embedded at 300 dpi so vector
  files stay small. Settings → Appearance → Advanced → 3D Export Style
  switches to Screen capture.
- The interactive 3D view uses the same look (serif labels, grey walls, soft
  lighting, SI-scaled axis labels).
- 3D controls are translated in Chinese; scientific terms (Real, Imaginary,
  Magnitude, Phase, IQ, dB, geometry names) stay in English.
- Spin-box step arrows are visible in Light and Dark themes; combo popups
  widen to their longest entry.
- YIG Mirror Analysis: exporting, copying or dragging a single pane no longer
  places the plot in a corner of a larger empty image (Fit Results, Data
  Preview and Phase panes).

## v0.18A

- 3D uses a hybrid engine. Surface, Transparent Surface, Dual Surface and
  Waterfall render with Qt Graphs 3D (GPU via Qt RHI: Metal on macOS,
  Direct3D on Windows). Trajectory and Scatter stay on Qt Data Visualization.
  Without Qt Graphs (for example the offscreen test platform) the previous
  renderers are used.
- Grids upload in one call from an evenly spaced, peak-preserving display
  grid: narrow resonance dips keep their full depth, and every display cell
  maps to one real source sample for readouts. An 855 x 501 grid uploads in
  about 6 ms instead of about 1.4 s.
- True transparency: opacity changes only alpha, so the scientific colormap
  is never altered. Transparent Surface is now full-resolution native.
- Reference Plane and Bottom Projection are available for every geometry
  (Surface, Transparent, Dual, Waterfall, Trajectory, Scatter).
- 3D Performance profile (Settings → General): Balanced (default; 1M display
  vertices, 150k while dragging), High Quality (4.2M / 300k) and Resource
  Saving (250k / 50k). Large grids are prepared in a background thread.
- Waterfall draws up to 64 colormapped trace ribbons; Dual Surface keeps the
  user's height transform after leaving Dual; Trajectory and Scatter render
  again (previously an exception) with visible sphere samples.

## v0.17G

- Push buttons and stand-alone tool buttons use a liquid-glass material:
  specular top rim, split-body gradient, translucent edge and accent-tinted
  hover/pressed/checked states, in both Light and Dark themes.
- Buttons inside the toolbar glass capsules hover and check as round lenses
  instead of rectangles.
- Light-theme toolbar glass is more legible: optical bevel shading, a cool
  tint, a deeper soft shadow and a lower inner shade separate it from the
  beige window background.
- Settings → Appearance... opens the Settings dialog's Appearance page, which
  now holds Theme, Advanced (Scientific Plot Appearance, Export Plot
  Background) and the Toolbar Glass dials. The Browser's round theme button
  and toolbar glass dials remain and stay in sync.

## v0.17F

- Database Browser measurement transfer: drag a log row onto the Quick Preview
  transfer target to send it to the configured LabLogViewer receiver.
- Sends parsed measurement identity, channels, logical axes and raw real/complex
  arrays. The receiver stores a validated archive outside the source database.
- See `docs/measurement_transfer.md` for protocol, Flux/Empty semantics,
  network setup and current limitations.

## v0.17D

- Extends v0.17C's icon-based, three-segment Browser toolbar and icon-based
  Viewer toolbar with a cached component-level Glass material. The existing
  icons, actions, tooltips, and column alignment remain in place.
- In-app background sampling, subtle edge refraction, low-resolution frosting,
  tint and a restrained rim are painted behind ordinary sharp Qt controls.
  Scientific plots, tables, fitting controls and the 3D viewport are unchanged.
- `LABLOGVIEWER_DISABLE_GLASS=1` uses a theme-aware fallback material; Browser
  Safe Recovery also disables optical processing.
- Glass is drawn as capsules around grouped controls using a PySide6 port of
  the MIT-licensed PyGlass optics (copyright 2026 neomosh8): bevel refraction
  with dispersion, Fresnel rim reflection, frost and shadow. No PyQt6 runtime
  dependency. See `docs/third_party_pyglass.txt`.
- The Browser toolbar has Thickness and Frost dials; values apply to every
  glass surface live and are saved in `settings.json` on release.
- Fixes a severe slowdown inherited from v0.17C: icon validity was re-parsed
  from SVG on every Qt paint/size check, which made the Trace Manager
  quadratic in the number of traces (tens of seconds for 855 traces).

## v0.17C

- Adds theme-aware toolbar icons from packaged assets in
  `app/resources/icons/`, resolved by semantic name through `app/icons.py`.
  Icon lines use the Light (`#1D1D1F`) or Dark (`#E6E6E6`) text color and
  switch with the application theme. Icon controls are icon-only; their
  former label is the hover tooltip and accessible name, and each keeps its
  original action.
- Database Browser and Viewer Open/Reload share the same icon artwork only;
  their actions and handlers remain separate.
- Star is shown as a yellow-filled star when a Data entry is starred and as a
  theme-colored outline when it is not, in both the Star button and the Data
  list Star column. Clicking the star in the Star column toggles Star/Unstar
  through the same store as the Star button.
- Checked mode controls (Pointer / Drag-to-Share, Maximize Plot, and other
  checkable buttons) show the Selected surface with an Accent border.
- Top toolbar icons are 32 px. The Database Browser top row is split into
  three segments that follow the workspace columns, so Star/Tag sit above
  Data in Folder.
- The Browser Appearance dropdown is now one round button that cycles
  Light (sun) → Dark (moon) → Follow System on each press.
- Browser top-row controls are all flat icon buttons, including Retrieve by
  Tags (funnel), Back to Folder (return arrow), and a new De-background icon.
  Menu buttons no longer draw a drop-down arrow; they show the pressed
  surface while their menu is open.
- YIG Analysis workspaces use one Copy and one Save icon button, each with
  This Pane / All Panes entries, an icon View All, 20 px plot toolbars,
  themed pane borders and tabs, and a left panel sized so it is not clipped.
- Trace Manager Show is a clickable eye icon (no checkbox). Mark Tool icons,
  the formula preview, and the Plot Settings panel follow the active theme;
  1D/2D Plot Settings no longer reserve the 3D page's height.
- Windows follow-ups from the static audit: Rename falls back to a
  no-overwrite rename when hard links are unsupported, and Follow System
  reads the Windows app theme when Qt reports an unknown color scheme.

## v0.17B

- Adds one persisted Light / Dark / Follow System appearance preference shared
  by the Database Browser toolbar, Settings menu, and Settings → Appearance.
- Keeps application Theme separate from Scientific Plot Appearance and Export
  Plot Background. Plot and export choices live under Settings → Appearance →
  Advanced, both default to white, and exports render plot chrome in their own
  selected presentation without changing scientific values or colormaps.
- Applies a restrained charcoal/cyan hierarchy to application panels and
  controls. Application theme changes do not reload scientific data or
  determine the scientific plot background.
- Keeps Analysis Matplotlib workspaces styled after clear/replot operations;
  Analysis copy, save, and drag rendering temporarily apply the selected export
  appearance, then restore the on-screen plot style.
- Fixes the 1D/2D Viewer control-row expansion that created a large blank gap
  after theme repolishing. The control row now keeps its natural height and the
  left settings panel has a practical minimum width. The 3D viewport layout is
  unchanged.
- Follow System stores the selected `system` mode and tracks Qt's operating
  system color-scheme notification when available.
- The Database Browser's three-column workspace and splitter layout are
  unchanged. No Liquid Glass styling is included.

## v0.17refixed (previous working baseline)

- Adds `Transparent Surface`, a separate Geometry mode using depth-sorted,
  per-triangle Matplotlib alpha blending from the preserved transparent
  reference. Native Qt Surface remains available for dense opaque work.
- All native 3D geometries share a scientific XYZ axis-box style and a
  (1, 1, 0.72) display aspect. Transparent Surface uses that same aspect with
  LabLog's light background rather than the reference's graphite background.
- Native and transparent 3D Copy, Save, and Drag render their current scene at
  high resolution. SVG embeds the rendered raster frame; it is not vector
  geometry.
- Transparent rendering uses 6,000 display samples in Auto/Adaptive and a
  12,000-sample Full Resolution limit. Painter-style sorting can artifact on
  overlapping facets; source arrays remain unchanged. See
  `app/visualization3d/README.md`.

## v0.17A

- Adds a Database Browser `Interfaces` menu with Measurement and Time Domain
  integration placeholders plus an external Online Paper Library launcher.
- Defines a small context/result contract under `app/interfaces/`; unavailable
  placeholders are localized, and the paper-library URL is opened through the
  operating system's default browser only after an explicit user action.
- Documents the future interface entry points. The deferred v0.16D 3D limits
  remain unresolved: Surface transparency, two empty geometry modes, and the
  opaque Reference Plane.

## v0.16D

- Extends the existing Qt Data Visualization Surface renderer with Surface,
  Dual Surface, Waterfall, Trajectory, and Scatter geometry selections. The
  original Surface series, feature-aware LOD, cache, and camera handlers remain
  the rendering foundation.
- Adds independent height/color mappings, wrapped and direction-selectable
  unwrapped phase, bottom projection, reference planes,
  and a draggable horizontal color-range bar linked to the existing numeric
  range controls and 2D Heatmap.
- Adds XYZ scientific point mapping, point-index mapping, scientific readout,
  and per-Data persistence for the new 3D view configuration.
- Correction pass: all 3D geometries are single-pane; entering 3D closes a
  1D/2D Multi-Pane layout. Trajectory uses the selected trace and validates
  mapped points before native rendering. LabLogViewer-owned JSON stores read
  UTF-8 explicitly, including tags on CP950 Windows systems.
- Known limits: native pixel/crash validation is unavailable in this
  environment. The Qt Data Visualization Surface backend does not provide
  reliable on-screen alpha blending, so opacity controls are disabled rather
  than presenting a false scientific transparency. Trajectory remains colored
  samples rather than a connected polyline; point colors are quantized into
  16 bins. See `app/visualization3d/README.md`.

## v0.16C

- Sets the 3D series to a filled scientific surface rather than Qt's default
  filled-plus-wireframe mode, which overlays roughly 857,000 dark mesh edges
  on the 857 x 501 reference grid. Native pixel validation remains required.
- Adds feature-preserving, source-indexed 3D LOD, viewport-aware interaction
  budgets, idle refinement, a bounded cache, background NumPy preparation,
  and stale-result rejection. Original HDF5 and scientific arrays are untouched.
- Records measured preparation, Qt item-build, proxy-reset, and available FPS
  diagnostics. See `app/visualization3d/README.md` for limits and handoff.

## v0.16B

- Promotes 3D Surface to a first-class third Plot Mode and removes the old
  visualization-mode selector from the user workflow.
- Adds dynamic X/Y/Height selection, independent Color Source and transform,
  scientific colormaps, colorbar metadata, and independent color limits.
- Adds display-only Z Scale, Auto/Full Resolution/Adaptive LOD/Performance
  policies, camera presets, and Perspective/Orthographic projection.
- Keeps scientific grids in `z[y, x]` orientation. Auto uses full resolution
  through one million points and the preliminary reduced mesh above that;
  explicit policies remain available.
- Adds English and Traditional Chinese labels for the new Surface controls.
- Offscreen test environment has no usable OpenGL context, so native 3D
  rendering and camera appearance still require local GUI acceptance.

## v0.16A

- Adds a bilingual Settings → Debug page with privacy-conscious runtime
  information and a safe Copy Debug Information action.
- Keeps Settings as a regular top-level native-menu candidate on macOS by
  disabling Cocoa text-role relocation; it follows Processing in the Browser
  menu model.
- Adds an interactive 3D Surface mode to the existing N-D visualization, using
  the selected dimensions, slice, and existing transform output.
- Preserves `z[y, x]`, maps physical coordinates through reversible axis
  formatters to avoid 32-bit GPU-coordinate precision loss, and introduced an
  initial 100,000-vertex display cap without changing scientific data.
- Uses the already-installed PySide6 Qt Data Visualization module. The scene
  is created only when 3D Surface is selected; unsupported OpenGL contexts
  leave the existing heatmap available.
- Native 3D interaction, OpenGL rendering, and the macOS menu-bar appearance
  still require local GUI acceptance; this workspace exposed no native app
  surface during implementation.

## v0.15E

- Adds external General/Language settings with runtime English and Traditional
  Chinese switching, a centralized translation catalog, and a policy that
  keeps scientific terms in English.
- Consolidates Viewer and Analysis Pointer / Drag-to-Share controls. Analysis
  plot workspaces can target the active pane or the current all-pane layout.
- Stabilizes Fit Results Grid/Focus layout transitions, provides Reset Layout,
  and stores Analysis splitter preferences as validated proportions.
- Migrates Tags to Project, Level, Board Design, Data Analysis, and Other
  categories with cardinality checks, explicit conflict handling, and
  category-aware retrieval.
- Keeps the Browser De-background action at the right end of its toolbar and
  removes pyqtgraph's floating 2D quick-control buttons without disabling
  ordinary plot navigation or context menus.

The available native desktop surface was unavailable during this release's
automated session; see the delivery report for local GUI checks still required.

## v0.15D

YIG Mirror Analysis integration: fixes the κ/phase estimator SciPy import,
adds a GUI-independent Analysis API and structured physical/coarse location
results, and consolidates the Node/Antinode workflow into the Analysis window.
Analysis includes resizable Data Preview plots, six independent Fit Results
panes with Grid/Focus layouts, shareable high-resolution analysis plots, and a
dedicated Phase / Node workspace. See
[`app/analysis/yig_mirror/README.md`](app/analysis/yig_mirror/README.md) for the
scientific model conventions and handoff map. Real-data fit execution was
checked on the available 0828 RSMEP sample, but its exploratory subset included
low-quality fits; the unavailable 437-slice reference run was not claimed as
numerical parity.

- The Viewer Analysis menu opens an independent Node / Antinode Finder using
  the existing Experiment Data Model. It reproduces the supplied Node
  reference's dip filtering, linear resonance tracking, dynamic frequency
  window, smoothing, and SciPy peak detection; Antinode candidates are the
  new minima-of-average-transmission extension. Candidate markers use measured
  resonance positions, and the result table reports sweep/frequency axes and
  units.
- The Finder supports Node, Antinode, and Both modes, manual/median dip-depth
  thresholds, adjustable window/smoothing/peak controls, and background
  analysis cancellation. It accepts only complex S-parameter data with one
  unambiguous active sweep axis and does not write analysis results to HDF5.
- The Viewer Analysis menu also opens the reference-derived YIG Mirror Fitting
  workspace as an independent window tied to that Viewer's canonical Data
  identity. It includes single-trace, continuous/multi-trace, phase-linked and
  global workflows; formula building/library, parameter cleaning, reports,
  plots and CSV outputs remain external to the experimental HDF5 file.
- Fitting uses a detached complex-data snapshot from the existing Experiment
  API. SciPy is required for numerical single, batch and global fits; when it
  is unavailable, the analysis workspace remains viewable while fit actions
  are disabled with an explanatory status.
- The Database Browser's Data right-click menu can rename `.hdf5`/`.h5` files
  in place. It preserves file bytes and migrates applicable external
  Data-bound state; renaming is blocked while that Data is open in a Viewer.
- The inspected reference Python sources are preserved under
  `references/node_antinode/`. No reference HDF5 files or ground-truth
  candidate datasets are bundled.

## v0.15B

- The Browser's Processing menu and toolbar now open a modeless
  De-background workflow. Target and Background are selected through the
  existing database hierarchy; compatible complex VNA S-parameters are
  listed only after both files are inspected.
- Processing requires matching, ordered Frequency grids and a single
  Background Frequency trace. It calculates `Target / Background` in the
  complex domain, broadcasts only across Target sweep entries, and creates a
  new Labber file beside the Target without modifying either source.
- Output is copied from the Target, patches only the selected existing
  complex trace, is reopened through the normal parser for verification, and
  is atomically published. Existing output requires an explicit replacement
  confirmation. A successful file can be added to the Browser and opened by
  the ordinary Viewer path.
- The supplied legacy RSMEP result was exactly reproduced after discovering
  its component-wise float16 value quantization. The HDF5 trace dataset's
  original dtype, shape, attributes, and all unrelated Target content are
  retained. See `docs/debackground.md` for the reference analysis and limits.

## v0.15A

- View Presets save complete meaningful Viewer configurations outside the
  measurement files and remain bound to the canonical identity of their Data.
- The Viewer sidebar groups plot settings, View Presets, independent X/Y
  formulas, and the existing Marks controls in a scrollable layout.
- 1D formulas use a restricted mathematical parser. Blank X or Y expressions
  preserve that axis; formulas are applied after the existing transform,
  dB, and unwrap processing without modifying source measurements.
- GIF animation exports include every trace in the chosen scope, in order,
  at the encoder's 10 ms frame interval. MP4 retains its existing trace-rate
  options and full-trace behavior.

## v0.14D

- Copy, PNG save, and drag-to-share now record each scientific plot as a Qt paint-command display list and replay it at export density. Small on-screen panes therefore no longer become soft raster captures when composed into a high-resolution All-Panes figure; SVG export remains a native vector path where supported.
- Export Animation adds bounded, immutable GIF Quick Scan and H.264 MP4 analysis exports. MP4 supports All/Selected/Visible scope, fixed scientific axes, optional trace/sweep overlays, 15/30/60/100/150/200 traces per second, and Current/720p/1080p output while encoding runs off the GUI thread.
- Animation supports current 1D traces and vector-log 2D/Flux grid panes together in All-Panes output. Flux frames reveal acquired sweep rows in order, using a fixed color range and the measured X/Y orientation; grids whose entry-to-row mapping is ambiguous are rejected.

## v0.14C

- The lower Trace Manager now remains a continuously resizable workspace: at small heights its intact controls live in a scrollable viewport rather than overlapping rows or forcing a minimum splitter size.
- Plot export commands are consolidated in the Viewer toolbar's Export menu and the native File menu. The plot context menu keeps pyqtgraph navigation controls while removing only its ambiguous generic `Export...` item.
- Plot surfaces support lazy native PNG drag-to-share. A normal Multi-Pane drag composes all panes, while Option/Alt drag locks the pointer pane without changing the Active Pane; the same canonical renderer powers Copy, Save, and the drag payload.

## v0.14B

- The Plot / Log Entries workspace is now a continuously resizable splitter. Its useful proportional height survives Multi-Pane transitions and session restoration without changing trace selection, visibility, Active/Reference state, colors, or overlays.
- Export Data provides pane-aware CSV and NPZ scientific output for Active, Selected, Visible, or Full Data scopes. Exports identify Raw, Current Transform, or Displayed representations; preserve complex values, axis meaning, trace/sweep provenance, partial-data validity, and the canonical Flux `z[y, x]` orientation.

## v0.14A

- Plot copy and image export now use the already-rendered scientific plot surfaces: active-pane or all-pane composites can be copied to the clipboard or saved as PNG/SVG without reopening HDF5 data. Context menus choose the pointer pane; keyboard copy chooses the Active Pane and leaves ordinary text/table copy native.
- Composite exports preserve the current Multi-Pane splitter arrangement, including a saved underlying workspace layout while an individual pane is maximized. Plot-only output excludes Viewer controls and chrome while retaining axes, legends, colorbars, current transforms, and Marks.

## v0.13D

- The selected Data's Comment is now a compact, collapsible Browser panel below
  Quick Preview. It is capped at 15% of that workspace and preserves existing
  external notes by canonical Data identity.
- Confirmed Labber root `comment` attributes can be read and written through a
  deliberately narrow core service; unknown layouts fall back to external
  notes and measurement datasets are never touched.
- Quick Preview now restores a Viewer-selected 1D vector trace from a 2D
  measurement, while the Browser scan uses a validated metadata index and fast
  listing followed by background enrichment.
- Viewer commands are grouped under Show, Cut always-on-top retains visibility,
  and named scientific Views can be saved, loaded, renamed, and deleted.

## v0.13C

- Metadata is now the single information-and-notes workspace. It retains the
  read-only Original Labber Comment in the metadata summary and adds a compact,
  vertically resizable LabLogViewer Comment editor backed by external atomic
  state. Existing v0.13B Browser comments are adopted lazily using canonical
  Data identity; no HDF5 metadata is modified.
- LabLogViewer automatically records the useful workspace context: the last
  database and Browser selection, open Viewer windows, display/trace state,
  multi-pane controls and splitters, modeless Metadata/Data Table windows, and
  Viewer-owned X/Y Cut windows. Startup restores each valid part independently;
  a missing file, database, malformed session, or off-screen geometry falls
  back safely without affecting external Stars, Tags, Marks, presets, overlays,
  comments, or per-Data Quick Preview display state.

## v0.13B

- Browser comments are now independent, modeless native text windows owned by
  stable database-plus-data identity. They retain standard Qt clipboard,
  undo/redo, and context-menu behavior while remaining external, debounced,
  atomic state.
- Browser Quick Preview can restore a Data-specific, validated subset of the
  last Viewer display: channel/trace, transform, axes, and compatible 2D
  colormap and color limits. Invalid or corrupt state falls back to the normal
  lightweight preview without restoring Marks, panes, geometry, or sessions.
- Redundant embedded Line Cut controls were removed. Shared View-menu and
  toolbar actions remain the single X/Y Cut command path, and each Cut window
  now identifies its current source Data (plus pane where Multi-Pane needs it).

## v0.13A

- External application state now shares defensive JSON infrastructure while
  remaining in separate domain files: schema-aware loading, atomic replacement,
  future-schema protection, and malformed-file backup keep Stars, Tags, axis
  presets, transforms, overlays, and Marks external to Labber files.
- The Browser adds a debounced, Unicode-safe Database Comment field. Comments
  are stored by database identity plus relative data path, so similarly named
  logs cannot share a comment and no experimental HDF5 metadata is changed.
- Viewer File and View menus now use the same QAction commands as the toolbar,
  including Open, Reload, Traces, Metadata, Data Table, and X/Y Cut actions.
  Each Viewer owns its commands, keeping multiple Viewer windows independent.
- Safely mappable interrupted one-step vector acquisitions now expose their
  measured leading extent instead of failing on nominal-versus-acquired sweep
  counts. Ambiguous multi-step mismatches remain explicitly unavailable; no
  missing experimental samples are synthesized.

## v0.12H

- Final v0.12 integration hardens Mark Persistence context ownership: every
  context now starts with resolved Data Identity, while prior raw-path context
  records remain readable and are migrated lazily after a successful restore.
  The same file therefore retains its Marks when opened through an equivalent
  path spelling, without discarding older external state.
- Independent X/Y Cut windows now expose a clear `Follow main plot` control.
  A crosshair move updates each following window in one cached-curve pass,
  rather than performing a separate grid and position redraw. Disabling Follow
  holds the current scientific cut while the main crosshair continues moving.
- v0.12H adds integration coverage for legacy Mark-context recovery, repeated
  X/Y Cut movement without cache reads, Cut-window reopen behavior, and final
  multi-feature regression validation on the available read-only Labber data.

## v0.12G

- Browser and Viewer display names now preserve the complete operator-facing
  filename stem. Labber's embedded `log_name` attribute remains available in
  metadata, but it can no longer hide meaningful suffixes such as `_debg`,
  `_p_debg`, or `BG` after a file has been renamed.
- Display names are explicitly separate from persistent Data Identity. Viewer
  presets, overlays, and Marks retain their resolved source-path keys; Browser
  Stars and Tags retain their database-root plus relative-path keys. Identical
  visible names therefore cannot share user state accidentally.
- Compatible 2D and N-D heatmaps can now open independent, modeless X Cut and
  Y Cut windows simultaneously from the Viewer toolbar or View menu. They
  follow the active Viewer/Pane crosshair and numeric Mark Crosshair position,
  use the current transform and axis units, offer an Always on top toggle, and
  extract from the already-loaded grid without another HDF5 read.
- Cut windows belong to their source Viewer. In Multi-Pane layouts they follow
  the Active Pane when it is a compatible 2D heatmap and report an unavailable
  state rather than showing stale data for incompatible plots.

## v0.12F

- Range numeric positioning is now available only when a real Range is
  selected. Object-specific fields are enabled only for valid Point Mark,
  Range, H-Line, V-Line, and Crosshair coordinates, so typing a range never
  silently creates one or changes another tool type.
- A permanent Viewer toolbar provides Open, Reload, Traces, Metadata, and Data
  Table actions. Metadata and detailed Data Table content are reusable,
  modeless windows, returning the former lower-panel space to plotting and Log
  Entries.
- Traces now reveals the existing Multi-Trace manager even for one trace. It
  manipulates the same selected/visible/Active/Reference `TraceSelectionState`
  and adds or removes traces without creating a second selection model.
- Multi-pane layouts now use nested Qt splitters: side-by-side/stacked two
  panes, a full-width top pane over two lower panes, and a true 2x2 layout.
  Panes may be made very small, resize freely, retain proportions through
  Maximize/Restore, and can reset only their geometry.
- Point Marks, Ranges, H-Lines, V-Lines, and Crosshairs persist externally in
  `~/.lablogviewer/marks.json`, scoped by Data file, pane, and plot context.
  Point Marks retain source sample identity; 2D objects restore against valid
  grid cells; visibility is retained; malformed/future data is handled safely.
  Half-Peak results and all HDF5 content remain deliberately non-persistent.

## v0.12E

- Half-Peak results now belong to the Range that produced them. Each Range
  retains its own observed extremum, baseline, half level, crossings, and
  Half-Level Width; moving, numerically repositioning, or deleting that Range
  clears only its own result and plot geometry.
- Compatible 1D plots gain `Show Data Points` with per-pane Auto and Manual
  screen-pixel sizing. Markers are always sourced from real displayed samples,
  adapt to the visible X range, follow each trace color, and never trigger an
  additional HDF5 read while zooming, panning, or changing point settings.
- Every 1D Point Mark now has an exact-sample target ring at its measured
  coordinate. The existing downward pointer stays visibly separated above the
  trace; selected targets are stronger, and target rings remain visible when
  ordinary data points are hidden.
- Mark Tool now begins at the neutral `Choose Mark` action and returns there
  after each completed placement. Point Mark remains reliably selectable after
  Range, H-Line, V-Line, and Crosshair placement with native mouse input.
- Local Analysis enablement follows the selected target, region, operation, and
  displayed data semantics. A sole Point Mark supports nearby/window analysis,
  a sole Range supports all range operations, and only Between Two Marks
  requires two Point Marks.
- Half-Peak uses the median of the outer 10% of the selected range as its local
  baseline, finds the dominant real-sample feature, and searches outward in
  physical X for the nearest enclosing crossings. Results report baseline,
  half level, left/right positions, and `Half-Level Width`, with temporary
  crossing geometry on the plot. It is not presented as a fitted linewidth.
- The Viewer adds optional session-only layouts for one to four stable panes:
  one pane remains the default, with side-by-side/stacked two-pane, fixed
  three-pane, and 2x2 four-pane layouts. One subtle highlight identifies the
  Active Pane, and the existing Plot Settings control only that pane.
- Each pane keeps independent 1D/2D axes, transform, dB/unwrap, IQ-derived axis,
  colormap/range, view, and pane-local Mark state. Trace synchronization is on
  by default; compatible X-range synchronization is optional and off by
  default. Multi-Trace visibility/Active/Reference state and v0.12C Saved
  Overlays remain in their original domains.
- Multi-pane rendering reuses the parsed `Experiment` and shared LRU data
  cache. Panes contain lightweight plot surfaces only and never read HDF5
  directly. Maximize Plot expands the Active Pane and restores the prior pane
  layout without discarding other pane state.

## v0.12C

- The lower Data Table now gains a compact Multi-Trace manager whenever two
  or more traces are selected. Selected, visible, Active, and Reference states
  are independent; changing Active no longer collapses the overlay selection,
  and hiding a trace does not deselect it.
- The manager provides explicit Active and single-Reference controls, per-trace
  visibility, the existing Sequential/Distinct/Single Color modes, and selected
  versus visible counts. Ctrl/Command and Shift Sweep changes use the same
  selection model as the Data Table.
- Saved Overlays persist externally per Data file in
  `~/.lablogviewer/overlays.json`. Up to five named overlays can restore trace
  composition, visibility, Active/Reference state, deterministic color mode,
  axes, transform modifiers, plot mode, and 2D colormap/range state. Marks,
  window geometry, session state, and Quick Preview state are deliberately not
  owned by Saved Overlays.
- Point Mark can be reselected reliably after every other Mark Tool, one valid
  visible Point Mark enables the nearby/around Local Analysis modes, and leaving
  IQ Transform restores the prior normal axes and transform controls.

## v0.12B

- Every selected trace is rendered on the same 1D plot using its own recorded
  X coordinates and the current shared axis/transform pipeline. Curves are
  retained by trace identity, so selection and active-trace changes reuse
  existing PyQtGraph items instead of rebuilding the overlay.
- Sequential is the default trace-color mode, mapping sweep order from a
  readable cyan-blue to navy. Distinct and Single Color modes are available in
  the compact Plot Settings area; all mappings are deterministic.
- The active trace keeps its assigned color while gaining restrained line-width,
  opacity, and z-order emphasis. Marks, numeric positioning, and Local Analysis
  continue to use only that active trace.
- Inactive opacity adapts to selection size. Small overlays receive individual
  legend entries; large overlays use one compact count/range/active summary.
  Display-only clipping and peak-preserving downsampling keep dense plots usable
  without changing analysis arrays or experimental data.

## v0.12A

- Each Viewer now owns a session-only `TraceSelectionState` with one active
  trace, a sweep-ordered selected set, and an independent Shift-selection
  anchor. A new Data/file resets all three to the initial valid trace, and
  separate Viewer windows never share selection state.
- The existing Log Entries table supports native-style single-click reset,
  Shift contiguous ranges, and Ctrl/Command discontinuous toggles. Removing
  the final selected row is prevented. If an active row is toggled out while
  other rows remain, the nearest retained selected trace becomes Active.
- The active row has a compact row-header marker and the Trace summary shows a
  selected count only for multi-selection. Sweep jumps and unmodified arrows
  reset to one selection; Shift+Up/Down extends from the stable anchor.
- Selection identity and modifier semantics remain the foundation used by the
  v0.12B overlay. Metadata, Marks, transforms, and Local Analysis still use the
  one active trace.

## v0.11E

- Local Analysis now supports Auto Nearby Feature, Around Selected Mark,
  Selected Range, Between Two Marks, and Current Visible X Range through one
  common in-memory region pipeline. Preview Region is a temporary dashed gray
  band and never becomes a saved Range or moves a Mark.
- Auto Nearby Feature searches no farther than 5% of the trace or 500 samples
  per side, with a 20-sample minimum on short traces. It identifies real local
  turning points without smoothing, ranks proximity before a smaller
  prominence contribution, and reports no nearby feature instead of searching
  the whole trace or guessing.
- Clicking a Point Mark or Range makes its Local Analysis role explicit.
  Region-dependent controls and Find/Preview availability now follow the
  selected objects, including hidden-target and M1-M10 capacity safeguards.
- Auto Half-Peak requires the user to review the proposed region before Find;
  its baseline, half-level, crossing, and displayed-domain semantics remain
  unchanged from v0.11D.

## v0.11D

- Point Marks, Ranges, lines, and Crosshairs now update their cached scientific
  coordinates, lower-left readout, and existing plot labels continuously while
  dragging. Preview signals are limited to about 60 Hz; release performs the
  authoritative sample/grid snap without rereading HDF5 or rebuilding plots.
- Compact Local Analysis uses a selected Range or a selected Point Mark with a
  default local window of +/-20 valid plotted samples. Local Maximum/Minimum
  use exact subset extrema, while Peak/Trough rank actual local candidates by
  prominence with deterministic sample-order ties and no implicit smoothing.
- Half-Peak is Range-only and requires Magnitude or Magnitude+dB. It estimates
  the local baseline from the median of the outer 10% boundary samples, uses
  the midpoint to the dominant extremum in the currently displayed domain,
  and reports interpolated left/right crossings as a Half-Level Width. It is
  explicitly not a fitted linewidth or FWHM measurement.

## v0.11C

- The second Viewer now uses a compact, resizable left Plot Settings and Mark
  Information column, a wider primary plot, and Metadata/Data Table bottom
  tabs. Step and Log Channel details moved into Metadata without data loss.
- Point Marks, Ranges, lines, and Crosshairs have independent visibility,
  compact optional plot values, selectable details, and direct numeric
  positioning with partial-field updates and real sample/grid snapping.
- Plot presets now include axes, measurement, transform, dB, and unwrap state,
  persist externally per source Data identity, and do not leak across files.
  Legacy global presets remain preserved and unassigned when identity is
  ambiguous. A built-in IQ Transform is available for compatible complex data.

## v0.11B

- One compact, painter-icon Mark Tool selector provides Point Mark, X Range,
  Horizontal Line, Vertical Line, and Crosshair creation in the second Viewer.
- 1D Point Marks use a fixed-pixel downward triangle above the real sample so
  fine traces remain visible. 2D Point Marks use consistent bright green with
  a dark edge and an additional selected-state cue on BWR heatmaps.
- Ranges use two-click creation, draggable boundaries, whole-band movement,
  normalized Start/End coordinates, and light neutral shading. Lines and
  Crosshairs are draggable data-coordinate reference objects.
- The Marks table handles mixed object selection, shared deletion and clearing,
  units, Range width, and nearest-cell 2D Crosshair values. Reference tools are
  invalidated conservatively when their coordinate semantics change.

## v0.11A

- The second Viewer supports up to ten session-only measurement Marks on 1D
  traces and 2D heatmaps. Add Mark places one Mark per activation and snaps to
  the nearest real sample or grid cell.
- Compact M1-M10 plot labels pair with a detailed Marks table. Marks can be
  selected, dragged, deleted individually, or cleared; the lowest available ID
  is reused after deletion.
- Marks retain underlying sample identity while Real, Imaginary, Magnitude,
  Phase, dB, and phase-unwrapping displays change. Incompatible file, trace, or
  axis contexts clear safely instead of showing unrelated coordinates.
- Marks remain in data coordinates through zoom, pan, resize, splitters, Hide
  Channels, and Maximize Plot. They are not persisted and never write HDF5.

## v0.10C

- `Retrieve by Tags...` searches the complete loaded Browser root with
  conventional multi-Tag AND/OR semantics and no measurement-array reads.
- Query results reuse normal Browser entries and retain Quick Preview, Tags,
  Star, context actions, and second-Viewer opening. A source-folder column and
  result count make cross-session and duplicate-name results unambiguous.
- Recent query definitions are stored in `~/.lablogviewer/tags.json`, limited
  to five entries and fourteen days. They are deduplicated independent of Tag
  order and remain consistent across Tag rename/delete operations.

## v0.10B

- The lower-left Browser panel now follows the selected Data entry and offers
  the same Tag editor used by the toolbar and context menu.
- Tag management supports create, rename, and deliberate deletion while
  updating all external assignments atomically.
- Session defaults use the nearest structural `Data_####` folder identity.
  Explicit `Flux-dep` names receive an initial Flux default. Per-Tag user
  suppression and manual assignments persist in `~/.lablogviewer/tags.json`.
- v0.10A Tag JSON migrates to schema 2 without changing HDF5 files or replacing
  existing explicit assignments.

## v0.10A

- Adds a persistent, external Tag library and multiple-Tag assignments without
  writing to Labber HDF5 files.
- Provides built-in Tags, Unicode custom Tag creation, safe legacy metadata
  migration, and atomic persistence in `~/.lablogviewer/tags.json`.
- Adds a scrollable Tag Assignment dialog, Browser toolbar/context actions,
  available Tags in the lower-left area, and a compact Tags data column.
- Data identity uses the scanned database's canonical root plus each file's
  relative path. Renaming a file or moving its database root changes that
  identity; v0.10A intentionally does not guess or content-match moved data.

## v0.9G

- The Browser now separates folder navigation, data-in-folder listing, and a
  large asynchronous Quick Preview for 1D traces and 2D heatmaps.
- Data rows expose creation time, sweep dimensionality, and persistent
  starred/unstarred state. Toolbar and context-menu Star actions share the
  same external store.
- Derived-axis plot configurations are saved independently from data
  transforms in `~/.lablogviewer/axis_presets.json`; presets such as IQ
  restore both axes after an application restart.
- Existing Viewer splitters, Maximize Plot, transforms, line cuts, metadata,
  and generic complex measurement support remain unchanged.

## v0.9E

This release improves axis semantics while preserving the validated v0.9C
Flux parser and visualization behavior.

- 1D X and Y choices are generated from the current experiment data model.
  Physical dimensions and real scalar quantities can be X axes; raw complex
  measurement channels remain Y quantities controlled by the Transform UI.
- Every actual complex measurement also contributes grouped derived-axis
  choices: Real, Imaginary, Magnitude, and Phase. Compatible pairs such as
  Real vs Imaginary and Magnitude vs Phase are plotted directly from the same
  selected trace without pretending they are sweep dimensions.
- Normal 2D X and Y choices now come from the selected Z channel's actual
  `Dimension` objects. Exactly two-dimensional datasets support either
  dimension on either screen axis, including axis swapping.
- One-dimensional channels remain in the 1D view. Channels with more than two
  dimensions remain in the N-D Slice Explorer, where extra dimensions can be
  fixed explicitly.
- Real, Imaginary, Magnitude, and Phase transforms remain separate from axis
  selection. Magnitude retains the dB toggle and Phase retains user-controlled
  unwrapping.
- Saved custom transforms remain external to experimental data at
  `~/.lablogviewer/transforms.json` and survive application restart.
- The default 2D colormap is the custom `LabLog BWR` reference LUT: low values
  are deep red, the center is white, and high values are deep blue. Image and
  colorbar use the same LUT.
- Automatic color limits use the finite 1st and 99th percentiles, preserving
  physically meaningful units while preventing isolated extrema from
  flattening the useful visual range. Manual Min/Max still bypass Auto.
- The 2D heatmap and Line Cut panel now share a draggable vertical splitter.
  The outer channel and metadata boundaries remain draggable, and Maximize
  Plot collapses secondary panels and restores their previous layout.

Derived quantities remain distinct from physical dimensions in the data model.
Each derived `AxisCandidate` records its source complex channel and transform
key, while the existing `TransformSpec` system continues to control raw
measurement Y data, dB, phase unwrap, and saved presets.

## Real-data regression

Tests resolve read-only fixtures from the workspace `Data/` directory rather
than obsolete upload paths. The v0.9E acceptance coverage includes:

- `0828 X1 Flux-dep_debg.hdf5`: S21, Frequency x DC supply current,
  681 x 10001 heatmap.
- `0828 RSMEP_1.hdf5`: S21, Frequency x Average Current,
  855 x 501 heatmap with swapped-axis verification.
- `0826 LRCPAEP @4.782GHz.hdf5`: real S31, Frequency x DC supply current,
  581 x 1001 heatmap.
- `0828 5.0197~5.0297GHz BG.hdf5`: one-dimensional S21 trace,
  501 points.

No executable application code branches on an Sij name. Only channels present
in the current experiment are exposed.

## Architecture

- `app/core/labber_parser.py` parses Labber structures into an `Experiment`.
- `app/core/data_model.py` owns channel data, physical dimensions, 1D/2D/N-D
  reconstruction, and the generalized scalar-matrix axis-order handling found
  during v0.9C Flux validation.
- `app/core/channel_manager.py` supplies semantic GUI-facing channel and axis
  candidates.
- `app/core/transform_store.py` owns built-in and saved transform definitions.
- `app/gui/main_window.py` connects independent Axis, Transform, trace, and
  visualization controls.
- `app/gui/plot_2d_widget.py` renders heatmaps and owns colormap/range display
  behavior without reading HDF5.

## Run

```bash
pip install -r requirements.txt
python main.py
python main.py /path/to/file.hdf5
python main.py /path/to/folder
```

Run the complete suite with the project's environment:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ -q
```

The delivery ZIP intentionally excludes `.venv`; dependencies are declared in
`requirements.txt`.
