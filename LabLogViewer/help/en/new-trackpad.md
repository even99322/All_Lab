# Mac trackpad gestures
> Everything you do with a mouse in the plots can also be done on a MacBook trackpad.

## Shortest procedure
1. **Pan.** In a 1D or 2D plot, slide two fingers: the plot follows your fingers. ![](shortcuts.png)
2. **Zoom.** Pinch (fingers together or apart): zoom around the point under the fingers.
3. **Fit everything.** Double-tap with two fingers (smart zoom): the plot auto-ranges to show all data.
4. **Context menu.** Press with two fingers (the trackpad's secondary click): the same menu as the right mouse button (copy, copy all panes, ...).
5. **Draw.** With annotation or the pen on, a single finger press-and-drag draws.
6. **3D.** A single finger drag rotates, two-finger scroll rotates (Shift + scroll pans), pinch zooms, two-finger twist rolls the scene, two-finger double-tap resets the view.

## Gesture table
| Gesture | 1D / 2D plot | 3D view |
|---|---|---|
| Two-finger slide | pan | rotate (with Shift: pan) |
| Pinch | zoom | zoom |
| Two-finger double-tap | auto range | reset view |
| Two-finger twist | not used | rotate |
| Two-finger press | context menu | context menu |
| One-finger drag | pointer / annotation as the tool dictates | rotate |

## What to know
- Force click and three-finger gestures are system-level on macOS and Qt does not deliver them, so they are not used.
- Mouse operations are unchanged.
- Gestures work only on a trackpad (or Magic Mouse); an ordinary wheel zooms as before.

## If it does not work
- **Nothing happens**: make sure the system trackpad settings have "Zoom in or out" and "Smart zoom" enabled (System Settings → Trackpad).
- **Pan feels reversed**: it follows macOS "Natural scrolling".
- **Pinch also scrolls the page**: click the plot first so it has focus.
