# 1D plots, traces and transforms
> Choose X / Y channels, step between traces, show complex data as Magnitude, Phase or dB, and compare several traces at once.

## Shortest procedure
1. **Open a measurement.** Double-click it in the Browser, or use **File → Open** (⌘/Ctrl+O) in the Viewer. **Plot Mode** starts at **1D Plot**. ![](v1d_open.png)
2. **Choose axes and transform.** **PLOT SETTINGS** on the left: **X Axis** (usually Frequency), **Y Axis** (e.g. VNA - S21), **Transform** (Magnitude, Phase, Real, Imaginary ...). Tick **dB** for 20·log10|S|; for Phase, **Unwrap Phase** removes ±π jumps. ![](v1d_axes.png)
3. **Step through traces.** The Log Entries table under the plot has one row per trace (one sweep point); click a row, press ↑ / ↓, or type a number in **Sweep (jump to entry)**. The title shows **Trace current / total**. ![](v1d_traces.png)
4. **Overlay several traces.** Shift-click selects a block, ⌘/Ctrl-click adds or removes one. Selected traces are drawn together; **Trace Colors** chooses Sequential (graded), Distinct or Single Color; the status shows "N selected · M visible". ![](v1d_overlay.png)

## What the result means
- **Magnitude** is linear |S|; with **dB** it is 20·log10|S|. Both are display choices; the data is unchanged.
- **Phase** is shown in degrees or radians; **Unwrap Phase** joins 2π jumps between neighbouring points, which shifts absolute values by multiples of 2π — keep that in mind when comparing phases of different traces.
- Each trace is one row of the Log Entries table; its columns (e.g. Average Current) are that trace's sweep values.
- If only part of the sweep was acquired, the status says "Partial acquisition: N / M sweeps shown".

## Options and parameters
- **Axis Preset**: save the current X / Y / transform under a name (**Save Axis Preset...**) and apply it from the list; **Update**, **Rename**, **Delete**. Presets are remembered per Data.
- **View Preset**: remembers the whole Viewer state (mode, axes, ranges ...) via **Save View Preset...** and the list.
- **Show Data Points**, **Point Size** (Auto / Manual): draw every measured point as well as the line.
- Custom transforms: use or manage them from the **Transform** list; they only affect the display.
- To rescale X or Y (e.g. x/1e9, 20·log10|y|) see *Formula*.

## If it does not work
- **The Y list lacks a channel**: it may not be a vector trace; for log channels (one value per sweep) use 2D or put it on the X axis.
- **"cannot be plotted against each other"**: X and Y vary over different things (one per sweep entry, the other within a trace). Pick two axes from the same domain.
- **Phase looks like a saw tooth**: tick **Unwrap Phase**, or the frequency points are too sparse.
- **dB shows -inf or gaps**: |S| has zeros or non-finite values; those points are left empty.
- **Too many overlaid traces**: select fewer, or switch **Trace Colors** to Distinct.
