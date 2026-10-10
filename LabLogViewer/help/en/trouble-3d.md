# 3D problems
> When the 3D window is slow, blank or closes unexpectedly.

## Shortest procedure
1. **Slow or jerky.** Set **Rendering** to **Performance** or **Adaptive LOD** in the 3D window, or **Settings → 3D → 3D Performance** to **Resource Saving**. ![](trouble_3d.png)
2. **Blank view.** X and Y must be different dimensions and **Height (Z)** must have a channel; the status says what is missing.
3. **Unexpected close.** 3D runs in its own process; when it closes you see "3D process ended unexpectedly" and the Viewer is unaffected — open **Analysis → 3D Surface...** again. Graphics drivers are the usual cause; update them or use **Performance**.
4. **Full Resolution warning.** Choosing **Full Resolution** for a large grid warns about memory and GPU first; Cancel keeps a lighter setting.

## What the result means
- These settings only change how finely data is drawn; values never change, and clicked readouts always come from the original data.
- When a sweep is very long (for example 10001 frequency points), the 3D window first draws a small test surface to find how many samples per axis your graphics card draws correctly, then draws your data with at most that many samples, keeping the peaks, so the deepest and shallowest colours stay exactly under the surface. The status bar still shows the original grid size.

## If it does not work
- **Translucency has no effect**: on some graphics cards Qt surfaces stay opaque; use the **Transparent Surface** geometry.
- **Trajectory / Scatter slow or white in dark mode**: these two stop measuring frame rate after the first frame (so the GPU idles when you are not rotating) and follow **Settings → Appearance → Scientific Plot Appearance** (dark or white background, labels, grid, reference plane).
- **3D needs a single pane**: set the Viewer's **Pane Layout** back to 1 Pane.
- **It keeps closing**: send the `logs/` report to the developer (see *General problems*).
