# 2D heatmaps and line cuts
> Draw a whole sweep as a heatmap, set the colour map and colour range, and look at one profile with X / Y line cuts.

## Shortest procedure
1. **Switch to 2D.** With a swept measurement open, set **Plot Mode** in the toolbar to **2D Plot**. ![](v2d_mode.png)
2. **Choose Z, X, Y and the transform.** **PLOT SETTINGS** on the left: **Z** the channel to colour (e.g. VNA - S21), **X** usually Frequency, **Y** the sweep parameter (e.g. Average Current); **Transform** Magnitude, Magnitude (dB), Phase ... ![](v2d_axes.png)
3. **Adjust the colours.** Same panel: **Colormap** (default LabLog BWR; also CoolWarm, bwr, viridis, plasma, inferno, magma, gray); tick **Auto** for an automatic range, or untick and type **Minimum / Maximum**, or drag the end handles on the colour bar. ![](v2d_color.png)
4. **Look at a profile.** The crosshair reads X / Y / Z under the mouse; the toolbar **Show** button → **Show X Cut** or **Show Y Cut** opens a profile window, and with **Follow main plot** ticked it follows the crosshair. ![](v2d_cut.png)

## What the result means
- Each row is one trace (one Y value); the colour is that point's Z value after the transform; the colour bar gives values and units.
- **X Cut**: Y fixed, Z against X (that trace's 1D plot). **Y Cut**: X fixed (e.g. one frequency), Z against the sweep parameter.
- The colour range only changes the colour mapping, not the data. A narrow range brings out weak signals, but values outside it are clipped to the end colours.
- Non-finite values (NaN, Inf) are left blank.

## Options and parameters
- **Auto Range** recomputes the colour range from the current data.
- Cut windows: **Always on top** keeps them above other windows; the cut position is saved in View Presets.
- Marks (Point Mark, Range, Crosshair ...) and local analysis also work in 2D; see *Marks and local analysis*.
- For 3D surfaces or waterfalls use **Analysis → 3D Surface...** (see *3D Surface*).
- Exporting 2D numbers writes the full valid grid (see *Copy, save and export*).

## If it does not work
- **No 2D option or an empty plot**: the measurement has a single trace (no sweep); use 1D.
- **"Cannot build 2D surface"**: X or Y is not a regular grid, or X and Y are the same dimension; choose other axes.
- **Almost one colour everywhere**: an extreme value stretches the range; untick **Auto** and set the range, or use dB.
- **The cut does not follow**: tick **Follow main plot** and move the mouse over the heatmap.
