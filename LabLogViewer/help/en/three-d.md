# 3D Surface
> Draw 2D data as a rotatable 3D surface, waterfall or point cloud, and click to read scientific values.

## Shortest procedure
1. **Open 3D.** With a swept measurement in the Viewer (single pane), toolbar **Analysis** → **3D Surface...**. 3D runs in its own process, so if it hangs or crashes the Viewer is not affected. ![](three_open.png)
2. **Choose the data.** Top left: **Geometry** = **Surface**, **X** (e.g. Frequency), **Y** (e.g. Average Current), **Height (Z)** (e.g. VNA - S21), **Transform** (e.g. Magnitude (dB)). ![](three_data.png)
3. **Rotate and read values.** Left-drag rotates, middle-drag pans, the wheel zooms; a left click reads the X, Y, Z scientific values at that point. **CAMERA**: **Top / Front / Side / Isometric / Reset View / View All**. ![](three_view.png)
4. **Adjust the look.** **SURFACE**: **Rendering**, **Z Scale**, **Projection** (perspective / orthographic), **Opacity**, **Bottom projection**, **Reference Plane**; **COLOR**: **Color Source**, **Colormap**, range. ![](three_style.png)

## What the geometries are for
| Geometry | Use it for |
|---|---|
| **Surface** | continuous 2D data, the overall shape |
| **Transparent Surface** | seeing parts hidden behind; a depth-sorted translucent surface |
| **Dual Surface** | comparing two channels or transforms (Surface A / B, each with its own mapping and opacity) |
| **Waterfall** | each trace as a line, spread along Y; how each trace's shape evolves |
| **Trajectory** | points joined in acquisition order; the sweep path |
| **Scatter** | every measured point as a dot; for data that is not a regular grid |

## What the result means
- **3D changes how data is drawn, never the data.** Clicked values come from the original data (after your transform).
- **Display sampling**: large grids are drawn with fewer vertices (the status says "Display sampling: R × C → r × c vertices. Scientific data is unchanged."). Fine features may be hard to see when reduced; choose **Rendering: Full Resolution** when needed.
- **Z Scale** stretches the displayed height only; the axis labels keep the true values.
- **Reference Plane** (minimum, zero, custom) helps judge heights; a plane outside the height range is reported.
- Non-finite Z values show as gaps; the status gives their count.

## Options and parameters
- **Rendering**: **Auto** (by grid size), **Performance**, **Adaptive LOD** (simpler while moving, detailed when still), **Full Resolution** (warns first about memory and GPU for large grids).
- **Color Source**: the height by default, or another channel / transform (e.g. height Magnitude, colour Phase).
- **Top Interactive Colorbar**, **Auto** range: as in 2D heatmaps.
- The vertex budget is in **Settings → 3D → 3D Performance** (Balanced, High Quality, Resource Saving); it affects the display only.
- For export see *3D export*.

## If it does not work
- **"3D Surface is available in a single Viewer pane."**: set **Pane Layout** back to 1 Pane.
- **"Choose distinct X and Y dimensions"**: X and Y must differ.
- **Slow or jerky**: choose **Performance** or **Adaptive LOD**, or set **Settings → 3D Performance** to Resource Saving.
- **The window closes with "3D process ended unexpectedly"**: usually a graphics-driver problem; the Viewer is unaffected, open it again. See *3D problems*.
- **A translucent surface stays opaque**: on some graphics cards Qt surfaces ignore opacity; use the **Transparent Surface** geometry.
