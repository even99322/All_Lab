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
- Stars and Tags live in your data folder (`state/stars.json`, `state/tags.json`), not in the HDF5, and follow renamed files.

## Options and parameters
- **Tags → Manage Tags...**: add, rename or delete Tags and set their category (Project, Level, Board Design, Data Analysis, Other). Deleting a Tag removes it from all data.
- Default Tags: Project (LRCPAEP, BIC, CM, RSMEP), Level (LA, LR), Board Design (Mirror), Data Analysis (Flux, BG, De-background), Other (Good Data, Best Data, Debug, singleYIG, doubleYIG).
- **Recent** in the retrieve dialog keeps the last 5 queries (within 14 days); click to reuse.
- To search a whole large folder see *Search Tags in the largest folder*.

## If it does not work
- **A message about only one Project / Level**: a single-choice category already has another Tag; remove it first.
- **No results**: AND is too strict; try OR, or check the Tags are really assigned.
- **No automatic Tags**: the name lacks BG / Flux-dep / _debg, or the file is more than 4 folders below the database.
