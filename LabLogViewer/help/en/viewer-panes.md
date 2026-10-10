# Several plots side by side
> Open 2–4 panes in one Viewer to compare different traces, different transforms, or 1D and 2D of the same measurement.

## Shortest procedure
1. **Choose a layout.** Second toolbar row, **Pane Layout**: **2 Panes · Side by Side**, **2 Panes · Stacked**, **3 Panes** or **4 Panes · 2 × 2**. ![](panes_layout.png)
2. **Pick the active pane.** Click a pane to make it active (outlined; **Active Pane: N** is shown). Every setting on the left (mode, axes, transform, trace) applies to it only. ![](panes_active.png)
3. **Set each pane.** For example pane 1 Magnitude (dB), pane 2 Phase; or pane 1 1D and pane 2 2D. ![](panes_compare.png)
4. **Decide what to synchronise.** **Sync Trace** (on by default): changing trace changes all 1D panes; **Sync X Range**: zooming or panning X moves all panes.
5. **Mark the same point everywhere.** In the **MARKS** panel on the left tick **Sync Point Marks across panes**, then place a Point Mark in any pane. The same measured point appears in every other pane, so you can read, say, the Magnitude and the Phase at one frequency side by side. ![](panes_sync_marks.png)

## What the result means
- All panes show the same measurement — different views of one dataset; they never change each other's data.
- Marks belong to their own pane; local analysis runs in the active pane. With **Sync Point Marks across panes** ticked, Point Marks are copied to the other panes and follow every placement, drag, hide and delete in the active pane (Range and other mark tools are not copied).
- The copy follows the same data point (sample), not the same X number, so it stays correct when panes show different quantities of one trace; a pane in 2D uses the nearest grid cell. Panes in 1D and panes in 2D only mirror with panes of the same kind.

## Options and parameters
- **Reset Layout** makes the panes equal in size again.
- Copy and export come in *active pane* and *all panes* versions (see *Copy, save and export*); Drag-to-Share also has **Drag Target: Active Pane / All Panes**.
- Returning to **1 Pane** shows **pane 1**'s settings (the other panes keep theirs for this session and return when you switch back).

## If it does not work
- **The Point Mark does not appear in another pane**: **Sync Point Marks across panes** is off, that pane is hidden, or it shows a different kind of plot (1D against 2D).
- **A setting seems to do nothing**: you changed the active pane; click the pane you want first.
- **3D Surface is unavailable**: it works in a single pane; switch back to **1 Pane**.
- **Two panes show different traces**: **Sync Trace** is off, or one pane is 2D (which shows every trace).
