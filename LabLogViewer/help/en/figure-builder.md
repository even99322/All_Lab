# Scientific Figure Builder
> Turn an AutoCAD circuit drawing (DXF) into a 3-D device figure: board, dielectric, copper traces, screw holes and vias that go through, plus YIG spheres, coils and sine waves.

## Shortest procedure
1. **Open it.** Database Browser → **Processing** → **Scientific Figure Builder...**. It runs in its own process; a hang or close does not affect other windows. ![](fig_open.png)
2. **Load the DXF.** **Open DXF...** and choose a file saved as "AutoCAD 2013 DXF". Layers are recognised and the 3-D board appears; warnings show under the **Layers** tab. ![](fig_loaded.png)
3. **Add YIG and coils.** **Add YIG**, **Add Coil**, and drag them into place (a YIG sits on the surface below; coils stay under the board). ![](fig_objects.png)
4. **Add a sine wave.** **Add Sine Wave** draws a 3-D wave along the board's long side, just above the board. Drag the line to move it across the board, drag the two square end handles to place its **start** and **end** in 3-D, and drag the round dot on any peak up or down to change that peak's height. ![](fig_wave.png)
5. **Set the view and export.** Drag empty space to turn the view (or **Elevation** and **Rotation** in the **View** tab), then **Export PowerPoint...** or **Export Image...** (see *PowerPoint and image export*). ![](fig_view.png)

## DXF rules
- Save as **AutoCAD 2013 DXF** (other versions warn and may differ).
- **Microstrip (MS)**: `b` board (bottom ground and dielectric), `g` guide (top trace), `h` screw holes (through every layer).
- **Coplanar waveguide (CPW)**: `db` down board (bottom ground and dielectric), `ub` up board (top ground), `g` guide, `h` screw holes, `v` plated vias (purple copper joining the grounds).
- Any `db`, `ub` or `v` layer means CPW, otherwise MS. Layer names are case-insensitive; other layers are ignored.
- Outlines may be loose lines: ends closer than the tolerance (about 0.002 mm or 2×10⁻⁵ of the drawing size) are joined; nested outlines use the even-odd rule (a ring stays a ring).

## What the result means
- Default thickness: copper **0.032 mm**, dielectric **0.813 mm**, editable per layer in **Layers**; proportions are true.
- **Placement**: "On layer below" stacks upwards, "Same level" shares the level below (e.g. CPW guide and top ground), "Through all" (vias). **Move Up / Move Down** change the order.
- Screw holes are cut from every layer; vias are cut from the others and shown purple.
- The view is an orthographic projection — the same calculation as the PowerPoint export, so positions match.

## Options and parameters
- **Selected** tab:
  - Select a YIG or coil and drag any of its 8 handles (4 edges, 4 corners): the object scales uniformly (a sphere stays a sphere, a coil keeps its shape) and the side opposite the handle stays fixed.
  - YIG: radius, height above the surface, number of spin arrows, sphere colour, spin colour.
  - Coil: radius, wire thickness, turns, turn spacing, gap under the board, colour, field arrow and its colour.
  - Sine wave: cycles, phase, **Peak shape**, line width (pt), dashed, colour. A 3-D wave also has **Height (mm)** (its swing, perpendicular to the start-end line, in the vertical plane) and **Start / End X, Y, Z (mm)** in board coordinates (z = 0 is the underside of the lowest layer; a new wave sits at the top of the stack).
  - **Peak shape**: *Flat* (equal peaks, the default), *Exponential decay*, *Gaussian*, *Standing wave*, or *Custom* (appears once you drag a peak dot; the other peaks keep their height). Changing the cycles or phase re-spaces the peaks; a custom shape is stretched to the new number of peaks.
  - Every number box updates the figure while you type or scroll; one continuous adjustment is one undo step.
- **Layers** tab: show, colour, thickness, placement, move up / down.
- **View** tab: elevation (90° = straight down), rotation, background colour.
- **Undo / Redo** (⌘/Ctrl+Z, ⌘/Ctrl+Shift+Z, up to 60 steps); the Delete key removes the selected object.
- **Save Figure...** writes `.llvfig` (geometry, objects, view); **Open Figure...** continues editing.

## If it does not work
- **"No known layers"**: layer names are not b / g / h or db / ub / g / h / v.
- **A layer reports "no closed outline"**: lines do not meet (a gap larger than the tolerance); join the ends in AutoCAD.
- **A coil is invisible**: it is under the board and hidden; drag it near the front edge or lower the elevation.
- **Text labels**: figures contain no text; add labels in PowerPoint or a drawing program.
- **"The Figure Builder closed unexpectedly"**: other windows are fine; open it again from **Processing**.
