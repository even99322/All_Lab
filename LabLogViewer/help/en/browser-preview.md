# Quick Preview and comments
> Select a measurement to see it on the right; keep a comment.

## Shortest procedure
1. **Preview.** The first readable row is selected automatically when a folder opens, so the preview is never blank on a readable folder; click another row in **Data in Folder**; **Quick Preview** on the right draws it — a curve for 1D, a heatmap for 2D — with the Log name and channel above; move the mouse to read X / Y / Z. ![](preview_show.png)
2. **Comment.** Type in **Comment** at the bottom right; it saves automatically when you stop typing, and the status bar says where. ![](preview_comment.png)

## What the result means
- Quick Preview draws a thinned copy of very large 2D maps (at most about 1600 points per axis) so it appears fast, and keeps the six most recently viewed previews so going back is instant. The Viewer always shows the full data.
- When the program starts again, the Data you had selected last time begins loading in the background while the folder is still being scanned, so its preview is usually ready (or nearly) when the scan ends, and the file is read only once instead of several times.
- Quick Preview is a small view; double-click to open the Viewer for transforms, marks and export.
- **Where comments go**: first into the HDF5 file's own Labber `comment` field (the same one Labber shows); the status says "Saved native Labber Comment.". If the file has no such field or the text exceeds its fixed length, the comment goes to your data folder (`state/comments.json`) and the status says "Saved external Comment". This is the only place LabLogViewer writes into a measurement file, and only that text field — never measured values.
- If an older LabLogViewer external note and a different Labber comment both exist, both are kept, never merged.

## If it does not work
- **The preview stays empty**: a folder with no readable file shows nothing; large 2D files take a moment the first time; grey rows cannot be previewed.
- **"Wait for this Data's Quick Preview to finish, then try again."**: renaming waits while the preview is reading.
- **Comment saved externally**: normal when the file has no Labber comment field; it is kept and follows renames.
