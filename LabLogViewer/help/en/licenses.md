# Licences
> A licence file (.llvkey) from the developer unlocks features early on one computer; send your machine code to ask for one.

## Shortest procedure
1. **Open Licences.** **Settings → About → Licences...** (the same button is on **Settings → Personal → Colours**). ![](licenses.png)
2. **Copy the machine code.** **Copy** next to **Machine code** (`LLV-XXXX-XXXX-XXXX-XXXX`) and send it to the developer.
3. **Install.** When you receive the `.llvkey`, **Install Licence...** and choose it. The list shows holder, features, valid-until date and days left; features work immediately.
4. **Renew.** Licences usually last two months; after expiry the list says so — ask for a new one.

## What the result means
- The **machine code** is a hash of this computer's hardware identifier with no personal data; a licence only works on the computer it was made for.
- Features: **Personal colours** (without 10 operations) and **Re-link moved data** (a tester tool, below).
- Licences are stored in the data folder's `licenses/`.
- A licence is refused if the computer's date is before its issue date; setting the clock back never revives an expired licence.

## Data Transfer
When your licence includes *Data transfer*, this window also shows **Data Transfer...**: history from an old data folder is moved into this program's one (see *Data folder*).

## Re-link moved data (tester tool)
With this feature the Licences window shows **Re-link Moved Data...**: after measurement files were moved, it moves their stars, tags, comments, marks, views, 3D views and YIG sessions to the new paths. The measurement files are only read.
1. Close all Viewers. Open the new location in the Browser first (**Open Database**), so the stars and tags are put under the database you look at.
2. Choose the folder the files are in now (**Folder with the moved files**), then pick one way:
   - **Only the folder moved, or the NAS now mounts under another name** (e.g. `/Volumes/ccuqel` → `/Volumes/ccuqel-1`): pick the old part of the path in **Old folder** (the list offers the folders the missing files share) → **Match by Path**. Only names and sizes are checked, nothing is read, so it is quick even on the NAS. Rows show "Same place, same size".
   - **Files were renamed or reorganised**: **Find Files**. Each file is found by content (slow on a NAS: every candidate is read). "Same content" rows are ticked; "Same name and size (check)" had no content fingerprint — tick only if sure.
3. **Re-link Ticked Rows**. A backup is made each time; **Undo Last Re-link** restores it.

**When the new path already has records.** Opening the new location already made some records by itself (automatic tags such as BG/Flux, the first view, a content fingerprint); these give way to your old records without a question. Your own records are combined: tags are joined, comments are both kept, named views/overlays/axis presets with the same name are kept as "name (2)", stars need nothing. Only when both paths hold *your* records that cannot be combined (Marks on both sides, Flux on one and BG on the other, a YIG session on both) you are asked: **Overwrite with Old Records** or **Keep Current Records** (**Show Details** lists them). Undo works for both.

## If it does not work
- **"This licence is for another computer."**: the machine code differs (new computer or reinstalled system); ask again.
- **"The licence signature is invalid"**: the file was changed or not issued by the developer.
- **"This copy of LabLogViewer cannot check licences"**: this build has no developer key; get an official build.
- **"This computer's date is earlier than the licence's issue date"**: check the system clock.
