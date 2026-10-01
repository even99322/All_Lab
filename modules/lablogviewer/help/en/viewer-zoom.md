# Zoom, trackpad and Drag-to-Share
> Zoom into a region with a rectangle, zoom and pan with the trackpad, or drag a plot straight into PowerPoint, Word or Finder.

## Shortest procedure
1. **Zoom.** The third of the Viewer's interaction buttons, **Zoom** (magnifier). Hold the left button and drag a rectangle; release to zoom to it. You can zoom several levels. ![](zoom_draw.png)
2. **Step back.** **Right-click** the plot or press **Esc** to go back one level, down to the view before zooming. ![](zoom_back.png)
3. **Back to normal.** The first button, **Pointer / Normal**: left-drag pans, the wheel zooms, the context menu has **View All**.
4. **Drag-to-Share.** Click **Drag-to-Share** (the middle button), then drag the plot from the Viewer into PowerPoint, Keynote, Word or Finder; releasing inserts a PNG. With several panes, **Drag Target** chooses **Active Pane** or **All Panes**. ![](zoom_share.png)

## Mac trackpad
- **Pinch**: zooms 1D / 2D plots around the fingers.
- **Two-finger scroll**: pans (a mouse wheel still zooms).
- 3D windows: two-finger scroll rotates, Shift + scroll pans, pinch zooms.

## What the result means
- Zooming and panning only change what you see; to export only the visible part, choose **Visible X Range** when exporting.
- A shared image is the plot as shown (marks included; pen and laser annotations are never included); temporary files live in the data folder's `state/drag-share/` and old ones are removed automatically.

## If it does not work
- **Nothing happens when dragging**: make sure **Drag-to-Share** is on and start the drag inside the plot.
- **The target app does not accept it**: some apps accept files but not images; drop on Finder / the desktop, then insert the file.
- **Lost after zooming**: right-click or Esc a few times, or use **View All** in pointer mode.
- **Pinch does nothing**: native pinch gestures exist on macOS; Windows trackpads arrive as wheel events.
