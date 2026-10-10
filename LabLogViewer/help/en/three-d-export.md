# 3D export
> Copy a 3D plot, save it as a publication-style PNG / PDF / SVG, or capture the interactive view on screen.

## Shortest procedure
1. **Choose the export style.** **Settings → 3D → 3D Export Style**: **Publication** or **Screen capture**. The 3D window's toolbar shows the current style ("Export: Publication style"). ![](three_export_style.png)
2. **Set up the view.** Rotate to the angle you want and set colours and ranges — export uses the current view and settings.
3. **Copy or save.** 3D toolbar **Copy 3D Plot** (paste anywhere) or **Save 3D Plot...**. Publication style saves PNG, PDF or SVG; screen capture saves PNG or SVG. ![](three_export_save.png)
4. **Drag-to-Share.** The toolbar also has **Drag-to-Share** to drag the plot into other apps.

## The two styles
| | Publication | Screen capture |
|---|---|---|
| How | redraws a journal-style figure at the current view (white background, fine grid, standard fonts) | captures what the window shows |
| Formats | PNG (high resolution), PDF, SVG | PNG, SVG |
| Resolution | independent of the window | the window size |
| Transparency | true translucent blending | depends on the graphics card |
| Data | up to about 20,000 vertices (large grids are sampled) | as on screen |

## What the result means
- Both styles show the same data; publication style samples down for size and clarity, so check very small features are still visible.
- In publication PDF / SVG, dense data is embedded as an image (to keep files small); axes and text stay vector.

## If it does not work
- **"No 3D scene is available for export."**: 3D is still preparing or has no data; wait until it appears.
- **Saving fails**: check the folder is writable; the extension follows the chosen format.
- **Publication style looks different from the screen**: it uses its own colours and fonts; use Screen capture for an exact copy.
