# Marks and local analysis
> Put marks on a plot to read exact values, and find peaks, dips or half-peak widths near them automatically.

## Shortest procedure
1. **Choose a mark tool.** Toolbar **Mark Tool** → **Point Mark** (or Range, Horizontal Range, Rectangle Range, Horizontal Line, Vertical Line, Crosshair). ![](marks_tool.png)
2. **Place it.** Click **Add** (the button reads "Click Plot...") and click on the curve. A Point Mark snaps to the nearest data point and is named M1, M2 ...; a **Range** or **Horizontal Range** needs two clicks (start, end) and a **Rectangle Range** two clicks too (one corner, then the opposite corner); the status line tells which click is next. ![](marks_place.png)
3. **Read values.** **MARKS** on the left lists every mark with X and Y; **Show values on plot** writes them on the plot. Drag a mark to move it. ![](marks_values.png)
4. **Local analysis.** **LOCAL ANALYSIS**: choose a **Region** (e.g. Around Selected Mark) and an **Operation** (Peak / Trough / Half-Peak ...), select a target mark, then **Find**. A Point Mark is placed at the result and the values are listed. ![](marks_analysis.png)
5. **Mark a span instead of a point.** Choose **Horizontal Range** (two clicks = two Y values) or **Rectangle Range** (two clicks = opposite corners), then drag any edge or corner to adjust. ![](marks_ranges.png)

## What the result means
- **Local Maximum / Local Minimum**: the largest / smallest sample in the region (that sample, no interpolation).
- **Peak / Trough**: the local peak / dip with the largest *prominence* in the region; **Prominence** is how far it rises above (or falls below) the higher of its two bases.
- **Half-Peak**: **Baseline** = median of the outer 10 % of points on each side; the feature farthest from the baseline is used; **Half Level** = midpoint between baseline and extremum; **Left / Right** = where the curve crosses the half level (linear interpolation); **Half-Level Width** = Right − Left.
- **Mind the units:** the half level is computed in the *displayed* Y units. With dB it is half of the dB depth, which is not the FWHM of linear amplitude or power; untick **dB** first when you need a linear width. Half-Peak accepts Magnitude or Magnitude + dB only.
- Values come from what is displayed (after transforms and formulas); the file is unchanged.

## Options and parameters
**Mark tools**
- **Multi-Pane**: tick **Sync Point Marks across panes** (MARKS panel) to show the same Point Marks on the same measured data point in every pane, for example the same frequency in a Magnitude pane and a Phase pane. See the Multi-Pane page.
- **Point Mark**: up to 10 (M1–M10), snapped to data points; the status shows "N objects (x / 10 Point Marks)".
- **Range**: a vertical band between two X values, usable as an analysis region.
- **Horizontal Range**: a horizontal band between two Y values (read a Y span, e.g. a level window). Drag either edge or the whole band.
- **Rectangle Range**: a box with an X span and a Y span, drawn as four black edge lines across the plot with a light-blue fill. Drag any side or corner to resize it, or drag the inside to move it. Two labels show the numbers: the top-right corner reads the upper Y and right X plus the mid values (`<y>`, `<x>`) and the sizes (`dy`, `dx`); the bottom-left corner reads the lower Y and left X.
- **Horizontal / Vertical Line / Crosshair**: read a Y or X value; the crosshair reads both.
- **Delete** removes the selected mark; **Clear Tools** removes all.

**Analysis regions**
- **Auto Nearby Feature**: around the selected Point Mark, up to 5 % of the trace on each side (at least 20, at most 500 points), the nearest clear feature. The first **Find** previews the region; press **Find** again to accept.
- **Around Selected Mark**: a fixed window (**Window** points) around the mark.
- **Selected Range**: the selected Range mark.
- **Between Two Marks**: between two marks.
- **Current Visible X Range**: what is visible on screen.
- **Preview Region / Clear Preview**: show the region before analysing.

## Which axes a mark belongs to
Every mark is stored in the quantities shown on the axes when you placed it. The on-plot label names the quantity and unit (for example "VNA - S21 — Phase: 165.99 deg"), and the details under **MARKS** have an **Axes:** line such as "X = Frequency [Hz]; Y = VNA - S21 — Phase [deg]". Edit exact numbers in the numeric editor (**X Start / X End**, **Y Start / Y End** for the new ranges).
- Changing the **Y quantity** (e.g. Magnitude → Phase) removes the marks that sit at a Y value: Horizontal Line, Crosshair, Horizontal Range and Rectangle Range, because their Y numbers would be meaningless in the new quantity. Point Marks and vertical Ranges are kept and re-read at the same samples.
- Changing the **X quantity** likewise removes marks at an X value (Range, Vertical Line, Crosshair, Rectangle Range).

## Saving
Marks are remembered per Data and return the next time you open the file (kept in the data folder's `state/marks.json`).

## If it does not work
- **"No free Point Mark (M1-M10 are occupied)"**: delete unused Point Marks.
- **"No nearby feature found"**: nothing prominent nearby; use Selected Range spanning both sides of the feature.
- **"Ambiguous half-level feature"**: two equally deep features; narrow the region to one.
- **"No valid left/right half-level crossing"**: the region is too narrow and the curve does not return past the half level on one side; widen it.
- **Mark tools unavailable**: marks work in 1D and 2D modes only.
