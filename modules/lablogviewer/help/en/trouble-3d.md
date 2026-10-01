# 3D problems
> When the 3D window is slow, blank or closes unexpectedly.

## Shortest procedure
1. **Slow or jerky.** Set **Rendering** to **Performance** or **Adaptive LOD** in the 3D window, or **Settings → 3D → 3D Performance** to **Resource Saving**. ![](trouble_3d.png)
2. **Blank view.** X and Y must be different dimensions and **Height (Z)** must have a channel; the status says what is missing.
3. **Unexpected close.** 3D runs in its own process; when it closes you see "3D process ended unexpectedly" and the Viewer is unaffected — open **Analysis → 3D Surface...** again. Graphics drivers are the usual cause; update them or use **Performance**.
4. **Full Resolution warning.** Choosing **Full Resolution** for a large grid warns about memory and GPU first; Cancel keeps a lighter setting.

## What the result means
- These settings only change how finely data is drawn; values never change, and clicked readouts always come from the original data.

## If it does not work
- **Translucency has no effect**: on some graphics cards Qt surfaces stay opaque; use the **Transparent Surface** geometry.
- **3D needs a single pane**: set the Viewer's **Pane Layout** back to 1 Pane.
- **It keeps closing**: send the `logs/` report to the developer (see *General problems*).
