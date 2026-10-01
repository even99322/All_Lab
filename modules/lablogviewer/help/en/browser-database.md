# Open and browse a database
> Choose a folder of measurements; LabLogViewer scans every HDF5 in the background so you can sort, browse and open them.

## Shortest procedure
1. **Open a folder.** **Open Database** (folder icon, top left) or **File → Open Database...** (⌘/Ctrl+O), and choose a folder with `.hdf5` / `.h5` files (sub-folders are included). ![](db_open.png)
2. **Let it scan.** The status bar first says "Database usable — enriching metadata for N files..." — you can already work — and then "Database metadata ready — N logs". ![](db_scan.png)
3. **Find a measurement.** Click a sub-folder in **Folders**; **Data in Folder** lists star, Log name, Date created, Sweep dimension and Tags. Click a column header to sort. ![](db_list.png)
4. **Open it.** A single click shows it in **Quick Preview** on the right; a **double click** opens it in the Viewer. ![](db_open_viewer.png)

## What the result means
- **Sweep dimension**: "1D · 501" is one trace of 501 points; "2D · 855 × 501" is 855 traces of 501 points.
- **Date created**: Labber's creation time, or the file's modification time when missing.
- Grey rows are HDF5 files that could not be read as Labber logs; hover to see why.
- Scan results are cached, so reopening a folder is fast.
- **Auto refresh** (default every 2 s, **Settings › General**): new, changed and removed measurements appear by themselves; the selection and scroll position stay. Only sizes and times are compared, so it stays light also on shared storage; new folders appear within 30 s. A file Labber is still writing is listed by name and read once it can be. **Reload** (⌘/Ctrl+R) still scans everything at once.
- **Opening data**: the first time a measurement opens in the Viewer it shows as a **2D** heatmap when it has a sweep (1D for a single trace); data you worked on before opens the way you left it.

## Options and parameters
- **Right-click menu**: Star / Unstar, Edit Tags..., Rename File..., Open.
- **New folder**: right-click a folder in **Folders** (or empty space for the top level) → **New Folder...**, or **File → New Folder...** (⌘/Ctrl+Shift+N) for the selected folder. Names cannot contain / \ : * ? " < > | or start with a dot. Empty folders are shown too. If the name is taken you can **Keep Both** ("name (2)") or cancel; an existing folder is never replaced.
- **Rename File...** changes the file name only (the extension is kept); the file's stars, tags, comments, marks and views follow the new name. Close this Data's Viewer first. If another file already has that name you can **Keep Both** (the file gets "name (2)") or cancel; another measurement file is never replaced.
- Next time you start, the last database and selection are restored.

## If it does not work
- **Empty list**: the folder has no `.hdf5` / `.h5`, or scanning is still running; check the status bar.
- **"Could not scan this folder"**: no read permission, or a cloud folder whose files are not downloaded; make sure the files are local.
- **A grey row**: not a Labber file, or damaged (e.g. an interrupted acquisition).
- **"Database Busy"**: renaming waits until scanning finishes.
- **A large network folder is slow**: the first scan reads every file's metadata; later openings use the cache.
