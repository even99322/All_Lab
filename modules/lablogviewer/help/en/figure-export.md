# PowerPoint and image export
> In the exported PowerPoint every part is a separate, editable shape using PowerPoint's own 3-D effects; PNG, SVG and PDF are also available.

## Shortest procedure
1. **Export PowerPoint.** Figure Builder → **Export PowerPoint...** and choose a file name. ![](figexp_pptx.png)
2. **Edit in PowerPoint.** Every layer, YIG and coil is a group; double-click a group to select a single shape and change its colour, move it, or adjust **Format Shape → Effects → 3-D Format / 3-D Rotation**.
3. **Export an image.** **Export Image...**: PNG (3000 px wide), SVG or PDF (vector). ![](figexp_image.png)

## What the result means
- **Board layers**: flat freeform shapes (screw holes and vias cut out) with PowerPoint **3-D Format** (depth = layer thickness, material, lighting) and **3-D Rotation** (your view). All layers share one tilt and are placed by the same projection, so together they form one 3-D device.
- **YIG**: a circle with round top and bottom bevels — a real 3-D sphere; the spin arrows are separate white shapes.
- **Coil**: each turn is a donut with round bevels (a 3-D tube ring), plus the field arrow.
- **Sine wave**: an ordinary dashed freeform line, stretchable in any direction.
- The slide contains no pictures.

## If it does not work
- **Shading differs from the editor**: PowerPoint uses its own lighting and materials; adjust them in 3-D Format.
- **Moving one layer breaks the assembly**: layers are aligned by one projection; select the whole group or everything to move it.
- **No 3-D in older or non-Microsoft presentation apps**: 3-D Format is a PowerPoint feature; other apps may show flat shapes.
