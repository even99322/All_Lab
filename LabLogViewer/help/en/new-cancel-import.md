# Cancel opening a database
> Stop a long folder scan at once; what was already read stays. New in 2.0.

## Shortest procedure
1. **Start a scan.** Open a folder in the Browser as usual (see *Open and browse a database*). ![](db_open.png)
2. **Cancel.** Press **Cancel** on the scanning dialog, or **Stop** in the status bar while metadata is still being read. ![](db_scan.png)

## What the result means
- Files already read are listed normally; the rest can be read later by scanning again. Nothing on disk is changed.

## Options and parameters
- **Settings → Appearance → Advanced → CPU threads**: how many worker threads a scan may use (default 75 % of logical CPUs).

## If it does not work
- **One file is red afterwards**: only that file could not be read (for example an input/output error on a network drive); the scan of the rest continues. Scan again later.
