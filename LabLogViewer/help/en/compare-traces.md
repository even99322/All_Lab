# Compare traces and files
> Overlay, stack, subtract or divide up to six 1D traces: I, Q, phase and magnitude of one file, or trace 3 of file A against trace 10 of file B.

## Shortest procedure
1. **Open the window.** Menu **Analysis → Compare Traces...** (it works with or without a file open in the Viewer; the open file is offered automatically). Press **Open file...** to add more HDF5 files; they are only read, never changed. ![](compare_open.png)
2. **Describe each trace.** Every **Trace** box has: **File**, **Channel**, **Quantity** (for a complex channel: real, imag, magnitude, magnitude_db, phase_deg, phase_rad), **X axis** (which dimension runs along the horizontal axis) and, for every other sweep dimension, an **… index** box that picks the position (for example *Current index 12* = the 13th current value, counting from 0). Use **Add trace** / **Remove last trace** for up to six traces. ![](compare_traces.png)
3. **Read the overlay.** With **View = Overlay** and **X handling = Native X values** each trace is drawn at its own measured X. The legend names file, channel, quantity and position. If two Y units occur (for example dB and deg) the second goes on a right-hand axis; more than two units is refused with a message. ![](compare_overlay.png)
4. **Compare only the shape, or the difference.** Choose **View = Difference (first − second)** or **Ratio (first ÷ second)** (they use traces 1 and 2), and set **X handling** and **Y handling** (below). ![](compare_options.png)
5. **Check the result.** A difference of a trace with itself is a flat line at 0; the dashed line marks 0. The note under the options lists anything you should know (different units, different X ranges, X ignored). ![](compare_difference.png)

## What the result means
- **Overlay / Stacked** show measured data; nothing is changed. **Stacked (own X axes)** gives every trace its own plot and X axis; **Link X axes in stacked view** makes zooming X in one move all (useful when X differs but you want to scan together).
- **Difference** and **Ratio** are calculated after both traces are put on one X grid by linear interpolation, so they are only as good as that interpolation: when the traces are measured on very different grids, treat small features as unreliable. A ratio where the second trace is 0 is left empty.
- With **Ignore X values (shape only)** both traces are stretched to positions 0 … 1; the X axis then means *relative position*, not frequency or current. Use it to see whether two sweeps have the same shape even when their ranges differ. It says nothing about where the features lie in real units.
- **Scale to 0 … 1**, **Zero mean, unit spread**, **Divide by largest |Y|** remove the absolute size so different quantities (or different gain) can be compared by shape. Difference and ratio are then of the scaled traces, in no physical unit.

## Options and parameters
- **X handling**: *Native X values* (each trace at its own X; Overlay and Stacked only), *Shared X range* (all interpolated onto the first trace's X points inside the range every trace covers), *Ignore X values (shape only)*, *Fixed step* (a regular grid with the **Step** you type, in X units, over the shared range; refused if the step is larger than the range or would make more than 2 million points).
- **Y handling**: as measured, or one of the three scalings above.
- Traces with different X or Y **units** are still drawn; a note warns you, because subtracting dB from degrees has no physical meaning.
- The window is independent: closing it frees the files it opened itself; it never writes to any file.

## If it does not work
- **"The two traces have no X range in common"**: the sweeps do not overlap; use **Ignore X values** or **Native X values** with Overlay.
- **"Choose a file and a channel for every trace"**: open a file first (**Open file...**), then pick a channel in each box.
- **The Y axis says "Scaled Y"**: a Y handling is on; set it back to *Y as measured* for real units.
- **"More than two different Y units"**: pick a scaling in **Y handling**, or compare fewer quantities.
- **Flat line or empty plot**: the index you chose may sit in a part of the sweep that was not measured (the file was stopped early); try another index.
