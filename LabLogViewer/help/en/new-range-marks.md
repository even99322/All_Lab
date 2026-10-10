# Horizontal and rectangle range marks
> Mark a span of Y values, or a box of X and Y, and see which quantity each axis is. New in 2.0.

## Shortest procedure
1. **Choose the tool.** **Mark Tool → Horizontal Range** or **Rectangle Range**. ![](marks_tool.png)
2. **Click twice on the plot.** Horizontal Range: two clicks give two Y values. Rectangle Range: one corner, then the opposite corner. Drag any edge or corner afterwards, or type **X Start / X End / Y Start / Y End** in the details. ![](marks_ranges.png)

## What the result means
- Labels and the details panel show the quantity and unit of each axis (for example Phase in deg), so a mark is never ambiguous.
- Changing the Y quantity (for example Magnitude to Phase) removes marks that sit at a Y value (Horizontal Line, Crosshair, Horizontal and Rectangle Range), because their numbers no longer mean the same thing.

## Options and parameters
- The full list of tools and the details panel are described in *Marks*.

## If it does not work
- **The second click does nothing**: the status line shows which click is expected; press **Add** again to restart.
