# Data safety
> What LabLogViewer does and does not do to your measurement files, and how your records are protected.

## Shortest procedure
1. **Measured values are only read.** Opening, previewing, transforms, formulas, marks, fits, 3D and export never change any measured value in an HDF5 file. ![](data_safety.png)
2. **Only five things touch files**:
   **Comments**: written into Labber's own `comment` text field at the file's root (the one Labber shows); kept externally if the file has no such field.
   **Tags**: the Tag names are added to the file's own `Tags` list (Labber's); Tags already there are kept, and if the file is busy nothing is written and you are told.
   **Stars**: written to the file's root `star` attribute (Labber's); a file that is already starred is never switched off by the program.
   **Rename File**: changes the file name only.
   Tags, stars, comments and renames first create a short-lived `.lock` file beside the data file so two computers cannot write one file at once; busy files are retried later (a rename just asks you to try again later).
   **De-background, permanent copies**: create **new** files; the originals are untouched.
3. **Your records are separate.** Stars, marks, fits ... live in the *Data folder*.

## How records are protected
- Every record is written to a temporary file and then swapped in, never half-written.
- A damaged record file is backed up before starting afresh, and you are told at start.
- Records written by a newer version are opened read-only by an older one, never overwritten.
- Renaming moves every related record together; if any step fails, everything is rolled back.
- After an unexpected exit, the next start offers **Safe Recovery Mode**.

## Saving next to other files (local or NAS)
Whenever something is saved where a file of the same name already exists — an exported image, CSV, NPZ, animation, Figure, fit result, AI guide, a De-background output — you are asked first: **Overwrite**, **Keep Both** (the new one gets "name (2)", "name (3)"...) or **Cancel**. This replaces the system's own "Replace?" question and is the same on a shared NAS folder. Renaming a measurement file and creating a folder only offer **Keep Both** or cancel, because replacing would remove someone's data.

## If it does not work
- **A "recovered from corruption" notice**: the backup is in the same folder; it can be sent to the developer.
- **Worried a file was changed**: unless you edited a comment or Tags, or renamed it, its modification time does not change.
