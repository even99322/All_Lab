# Search Tags in the largest folder
> When all measurements are gathered in one very large folder (tens of thousands to millions of files), search all of it at once instead of only the open database.

## Shortest procedure
1. **Set the largest folder.** **Settings → General → Largest data folder (Tag search) → Browse...** and choose that folder. ![](largest_setting.png)
2. **Open Retrieve by Tags.** Browser toolbar **Retrieve by Tags**, tick Tags, choose AND / OR.
3. **Choose the scope.** **Search in** → **Largest data folder** (greyed until set) → **Retrieve**. ![](largest_query.png)
4. **Read the results.** Every match is listed; **Source folder** is its place relative to the largest folder. Select, preview, double-click, star or tag them as usual. ![](largest_results.png)

## What the result means
- The search reads LabLogViewer's **Tag records**, not the disk, so it stays fast even for millions of files.
- So **only files with Tag records are found**: their database must have been opened at least once (and the files tagged, by hand or automatically). Folders never opened do not appear.
- Files that were moved or deleted are skipped.
- Each result keeps its own database, so stars, Tags, comments and Rename go to the right place.

## Reach of automatic Tags
When a database opens, automatic Tags (BG, Flux, De-background) only reach files at most 4 folders below it. If you open the whole large folder as a database, deeper files are not tagged automatically — tag them by hand, or open their own sub-folder.

## If it does not work
- **Largest folder is greyed**: not set in Settings, or the folder no longer exists.
- **A file is missing**: its database has never been opened, or it lacks these Tags; open that folder so Tags are created.
- **Too many results**: use AND or more Tags.
