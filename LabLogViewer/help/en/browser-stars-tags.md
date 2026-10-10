# Stars and Tags
> Star important measurements, classify them with Tags (project, level, board design, analysis type ...), and find them again with Retrieve by Tags.

## Shortest procedure
1. **Star.** Click the star in the first column, or select a row and click the toolbar **Star** button; click again to unstar. ![](tags_star.png)
2. **Tag.** Select a row, click **Tag...** in the toolbar (or **Edit Tags...** bottom left, or right-click **Edit Tags...**), tick Tags, OK. **Tags of Selected Data** bottom left shows them. ![](tags_assign.png)
3. **Find by Tag.** Toolbar **Retrieve by Tags** (funnel) → tick Tags, choose **AND** (all) or **OR** (any) → **Retrieve**. The list becomes "Query Results" with a **Source folder** column. ![](tags_query.png)
4. **Back to the folder.** Toolbar **Back to Folder** (back arrow) ends the query.

## What the result means
- **Automatic Tags**: when a database opens, **BG** (name contains BG), **Flux** (Flux-dep) and **De-background** (_debg) are added from file names; Tags you added by hand in one `Data_xxxx` folder become the default for the other files there. An automatic Tag you removed is never added back. Automatic Tags only reach files at most 4 folders below the opened database.
- **Single-choice categories**: one "Project" and one "Level" per measurement; Flux and BG cannot both be set.
- **Stars are written into the HDF5 file too** (the root attribute `star`, the same place Labber uses), so everyone sees them. A file that is already starred is never turned off by the program; a star is removed only when you click to unstar. Files without a `star` attribute get one only when you star them. Your computer keeps a copy (`state/stars.json`); when a file is opened, the star in the file is used. The old `stars.json` is not copied into files.
- **Busy files and waiting changes.** While one computer writes a file it creates a short-lived `<name>.hdf5.lock` next to it; other computers wait and try again, and a lock older than about 2 minutes is treated as abandoned. If a Tag or star cannot be written (file in use, drive offline), it stays on your computer, is retried about every 60 seconds and after a restart, and the status bar shows how many changes are waiting. If two computers change the same item, the last one wins.
- **Renaming** takes the same lock: if the file is in use by another computer you are told right away to try later (rename is not queued).
- **Tags are also written into the HDF5 file** (its `Tags` list, the same place Labber uses), so everyone who opens the same database on the NAS sees them, like a rename. Only the Tag names are stored; categories stay on your computer. Tags that Labber or someone else wrote in the file are kept, never deleted or overwritten. When a file is opened, the Tags in the file are used first, and your local copy (`state/tags.json`) fills in for files that have none. Only the Tag list is added; measured values are never touched.
- Renaming or deleting a Tag in **Manage Tags...** updates the files too.

## Options and parameters
- **Tags → Manage Tags...**: add, rename or delete Tags and set their category (Project, Level, Board Design, Data Analysis, Other). Deleting a Tag removes it from all data.
- Default Tags: Project (LRCPAEP, BIC, CM, RSMEP), Level (LA, LR), Board Design (Mirror), Data Analysis (Flux, BG, De-background), Other (Good Data, Best Data, Debug, singleYIG, doubleYIG).
- **Recent** in the retrieve dialog keeps the last 5 queries (within 14 days); click to reuse.
- To search a whole large folder see *Search Tags in the largest folder*.

## If it does not work
- **A message that Tags could not be saved inside the HDF5 file**: the file is busy (a measurement is running, someone else is editing it, or the drive is read-only or offline). Nothing is damaged; the change is kept on your computer and written again automatically later (about once a minute while the database is open, and after the next open). Close other windows showing that file if it is open here.
- **A message about only one Project / Level**: a single-choice category already has another Tag; remove it first.
- **No results**: AND is too strict; try OR, or check the Tags are really assigned.
- **No automatic Tags**: the name lacks BG / Flux-dep / _debg, or the file is more than 4 folders below the database.
