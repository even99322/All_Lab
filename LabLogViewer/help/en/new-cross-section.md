# 2D X/Y Cross-section
> Read a 2D map the way Labber does: two draggable lines on the heat map, a cut along X on top, a cut along Y on the right, and the contrast controls (intensity histogram) on the left.

## Shortest procedure
1. **Show a 2D map.** Open a file, set **Plot Mode** to **2D Heatmap** (one pane).
2. **Turn the cross-section on.** Click the **X / Y Cut** button in the toolbar (right of the tool buttons, grid icon): it opens the X and the Y cut together; click again to close them. **View → Show → Show X Cut / Show Y Cut** opens just one. The Viewer's own 2D plot is rebuilt in place: no extra window opens. ![](xsec_window.png)
3. **Move the lines.** Drag the white vertical or horizontal line. The lines move only while you drag them, not when the mouse moves; the cut plots update live while you drag. The line above the plot shows X, Y and the value at the crossing.
4. **Read the cuts.** The top plot is value versus X at the horizontal line's Y. The plot on the **right** is value versus Y at the vertical line's X; its vertical axis is the heat map's Y axis (same range, always aligned) and the values grow to the right, as in Labber. The box at the crossing of the two white lines shows z, y and x.
5. **Adjust the contrast.** The **Contrast** panel left of the map shows a vertical intensity histogram (how many pixels have each value); the blue band is the coloured range — drag the band or either edge, or type **Max** / **Min**. **Auto range** leaves out the outlying 1 % at each end (like Labber), **Full range** uses minimum to maximum. Wheel zooms the histogram, **double-click** fits it to the colour range again. The heat map and the colour bar follow at once.

## What the result means
- The cuts are exactly the displayed map (after rotation, Operation, Smooth, equations and Third dimension settings), so a **d/dx** map gives derivative cuts.
- The cross-section follows the Viewer: change the channel, transform or settings and it refreshes while keeping your line positions. Turning it on or off never changes the data or the saved colour range.
- The colour range you set here is the same one the Viewer uses (and saves); the colour bar handles and the Max/Min boxes always agree.
- It uses the Viewer's dark or light theme.

## Several panes
With more than one pane the panes are too small for this layout, so **Show 2D X/Y Cross-section** opens it in a separate window instead (same plots, follows the active pane, **Always on top** option).

## If it does not work
- **Status bar says to open a 2D map first**: the Viewer is in 1D mode or no 2D data is loaded.
- **Lines jump to the middle**: a new file or a different map was loaded, so the crosshair restarted at the centre.
- **The histogram is too zoomed in or out**: double-click it to refit to the colour range.
- **Everything one colour**: drag the blue band in the histogram to cover the whole data range, or the data truly has little variation.
- The older **Show X Cut** / **Show Y Cut** windows are still available.
