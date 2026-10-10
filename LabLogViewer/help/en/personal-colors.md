# Personal colours
> Your own colours for the light / dark theme, scientific plots and annotation pens. Unlocks after 10 data operations.

## Shortest procedure
1. **Open it.** **Settings → Personal → Colours**. The lock status is shown at the top. ![](personal_colors.png)
2. **Choose an item.** Five groups on the left: Light theme, Dark theme, Scientific plot — white, Scientific plot — dark, Annotation pens; click an item (e.g. "Accent").
3. **Choose a colour.** On the **colour wheel** (ring = hue, square = saturation and brightness) or type a **Colour code** such as `#1A2B3C`. It applies to all windows at once; changed items get a `*`.
4. **Reset.** **Reset colour / Reset group / Reset all colours**.

## Unlocking
- Colours are read-only until **10 data operations**; the top says how many remain.
- One operation = real work on a **different** measurement file: placing a Mark, choosing a transform, applying a formula, a Zoom rectangle, or a finished YIG fit. Only opening does not count; repeats on the same file count once; following someone in Network Workspace does not count.
- A licence from the developer can unlock them earlier (see *Licences*). **Icons are never locked.**
- The 10th operation unlocks immediately, without restarting.

## What the result means
- Colours are stored in the data folder's `state/personal.json`.
- A red warning appears when text contrast is too low (< 3:1) to read comfortably.

## If it does not work
- **The wheel is grey**: still locked, or a group heading is selected instead of an item.
- **No visible change**: some colours only appear in one theme (dark-theme items need the dark theme).
