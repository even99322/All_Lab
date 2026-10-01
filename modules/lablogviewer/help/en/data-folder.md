# Data folder (history records)
> Everything you do in LabLogViewer (stars, tags, marks, fits, personalisation ...) is kept in one folder called LabLogViewerData, which you can move wherever you like.

## Shortest procedure
1. **See where it is.** **Settings → General → Data Folder** shows the path; **Show Folder** opens it in Finder / Explorer. ![](data_folder.png)
2. **Move it.** **Change Location...** and choose a place (LabLogViewerData is created there).
3. **Restart.** You are asked whether to quit now; **at the next start** all records are moved at once and the old place is emptied. Until then the page says "Will move at next launch to …".
4. **Back to the default.** **Use Default** moves it back.

## Default location
- macOS: `~/Documents/LabLogViewerData` (or `~/LabLogViewerData` if Documents is synced by iCloud).
- Windows: `C:\Users\<user>\LabLogViewerData`.

## What is inside
| Place | Contents |
|---|---|
| `state/` | stars, tags, external comments, marks, transforms, overlays, views, sessions, database cache, settings, personalisation, Drag-to-Share temporaries, received measurements |
| `fitting/` | YIG sessions, model library, parameter settings |
| `logs/` | diagnostic reports when the app stopped responding |
| `licenses/` | installed licences |
| `lablogviewer_data.json`, `README.txt` | folder marker and description |

## What the result means
- This folder *is* your history. To change computers, copy the whole folder and point **Change Location...** at it.
- Moves happen at the next start, before any record is opened, so nothing written while closing is lost.
- When upgrading from an older version, the old data is **copied** in and the originals are kept as a backup.

## Moving history to a new LabLogViewer or computer
**Settings › About › Licences... › Data Transfer...** (shown when your licence includes *Data transfer*). It copies stars, Tags, comments, Marks, views, 3D views, YIG sessions, the formula library, settings and the host user name from an old data folder into this program's data folder, and makes the records follow measurement files that were moved or renamed.
1. Choose the **Old data folder** and **Where the measurement files are now** (the database open in the Browser is filled in; stars and Tags are kept per opened database, so use the same top folder).
2. **Compare** (nothing is written). *Same content*: the file's SHA-256 matches, it will move. *Same name and size*: not checked by content — tick it only if you are sure. *Not found*: the record stays under its old path.
3. Wrong or missing match: select the row and choose **Choose the new file by hand…**. *Same content* or *same layout* (same channels and sweep sizes) lets every record move. If the files differ too much, you must tick a disclaimer, and only stars, Tags and comments can move — Marks, views, 3D views and YIG sessions depend on the data's channels and axes and stay with the old path.
4. **Migrate**. The Viewer windows close, the data folder is backed up (`migration_backups`, with a report), the records are transferred and LabLogViewer restarts. **Undo last migration** restores the backup.
- When both folders have a record for the same file, this folder's record is kept unless you choose the old one; settings only fill what is missing.
- The old folder and the measurement files are only read.

## Cloud folders
- Keeping it in iCloud, OneDrive, Dropbox or Google Drive is not recommended: several computers writing at once can conflict or lose records.
- Choosing a cloud folder shows a warning; you must tick that lost history records are your own responsibility to continue.

## If it does not work
- **"… already contains LabLogViewer data"**: you may use the data there (your current folder is left unchanged).
- **The move fails**: no write permission or no space; the old place stays in use and you are told.
- **Records missing**: check that **Data Folder** points at your original folder.
