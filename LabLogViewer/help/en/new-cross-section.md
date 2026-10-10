# 2D X/Y Cross-section
> Read a 2D map the way Labber does: two draggable lines on the heat map, a cut along X on top, a cut along Y on the left, and an intensity histogram in the top-left corner.

## Shortest procedure
1. **Show a 2D map.** Open a file, set **Plot Mode** to **2D Heatmap** (one pane).
2. **Turn the cross-section on.** **View → Show → Show 2D X/Y Cross-section**. The Viewer's own 2D plot is rebuilt in place: no extra window opens. Choose the same menu entry again to switch it off. ![](xsec_window.png)
3. **Move the lines.** Drag the dashed vertical (red) or horizontal (blue) line. The lines move only while you drag them, not when the mouse moves; the cut plots update live while you drag. The line above the plot shows X, Y and the value at the crossing.
4. **Read the cuts.** The top plot is value versus X at the horizontal line's Y. The plot on the **left** is value versus Y at the vertical line's X; its vertical axis is the heat map's Y axis (same range, always aligned) and its value axis is mirrored, so the curve grows towards the left and the colour bar on the right stays free.
5. **Adjust contrast.** In the top-left corner, the **Intensity** histogram shows how many pixels have each value; the blue band is the range that is coloured. Drag the band or either edge to set the contrast finely (the colour bar is too coarse for this), or drag the colour bar handles on the right. The histogram's horizontal axis automatically zooms to the current colour range plus a margin, so even a very narrow range stays easy to grab. To move a range that sits at the edge of the view, **hold the middle mouse button and drag** (or drag empty space) to pan, even beyond the data; the mouse wheel zooms, and a **double-click** refits the view to the colour range. Exact **Minimum** / **Maximum** values and **Auto Range** are in the left tool panel. **Auto Range** in the tool panel fits the contrast to the data robustly. The heat map and its colour bar follow immediately.

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
