# Copy, save and export
> Copy a plot or save it as PNG / SVG, export numbers as CSV / NPZ, or turn a whole sweep into a GIF / MP4 animation.

## Shortest procedure
1. **Copy a plot.** Press **⌘C** (Windows: Ctrl+C) on the plot, or right-click → **Copy Plot**, and paste into slides or documents. With several panes, **Copy All Panes** is Control+Shift+C on a Mac and Ctrl+Shift+C on Windows. ![](export_menu.png)
2. **Save an image.** The Viewer toolbar's **Export** button (share icon) → **Save Plot As...**, PNG or SVG (vector). With several panes there are also **Save Active Pane As...** and **Save All Panes As...**.
3. **Export numbers.** **Export** → **Export Data...**: choose **Scope**, **Representation**, **Range** and **Format**, a destination, then **Export**. ![](export_data.png)
4. **Export an animation.** **Export** → **Export Animation...**: MP4 or GIF, which traces (all / selected / visible), speed and resolution, then a file name. ![](export_animation.png)

## What the export options mean
| Option | Meaning |
|---|---|
| **Scope**: Active Trace / Selected Traces / Visible Traces / Full Data | the current trace, the selected traces, the traces on screen, or everything |
| **Representation**: Raw Data | original values (complex kept as real and imaginary), unaffected by transform, dB or formula |
| **Representation**: Current Transform | values after the current transform (Magnitude, Phase, dB ...) |
| **Representation**: Displayed Data | exactly what is displayed (including the formula) |
| **Range**: Full Range / Visible X Range | all X, or only the visible X range |
| **Format**: CSV / NPZ | CSV is a long table (one point per row, Excel-friendly); NPZ keeps array structure for Python (`numpy.load`) |

- 2D data always exports the full valid grid.
- Files carry metadata (source file, channel, transform, range) so you can trace how the numbers were made.

## Animations
- **MP4**: 15–200 traces per second; size: current, 720p or 1080p.
- **GIF**: every frame is 10 ms (the smallest reliable GIF delay), so the length = number of traces × 10 ms.
- At least two traces are needed; the export runs in the background and can be cancelled.

## If it does not work
- **Exported numbers differ from the screen**: check **Representation** — Raw Data has no dB or formula.
- **The CSV is slow in Excel**: use NPZ for large data, or export the Visible X Range only.
- **"Animation export needs at least two traces."**: single-trace data cannot be animated.
- **"MP4 encoding is unavailable in this installation."**: no video encoder; use GIF.
- **⌘C does nothing**: click the plot first so it has focus.
