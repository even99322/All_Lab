# "File temporarily busy" on a network drive
> What the message means, why the file is not damaged, and what to do. New in 2.0.

## Shortest procedure
1. **Read the message.** Opening a file on a network drive (NAS) can answer *unable to lock file, errno = 35*. LabLogViewer now reads files without file locks and retries several times; if the drive still does not answer it shows **File temporarily busy**. ![](new_busy_dialog.png)
2. **Wait and open again.** Wait a few seconds and open the file again. If it keeps happening, copy the file to this computer and open the copy. ![](trouble_general.png)

## What the result means
- The file is not damaged and nothing was changed; the drive was busy.
- A real damaged file gives a different message (*Not a readable HDF5 file*).

## Options and parameters
- After an unexpected exit the **Recovery Report** shows the real crash stack, or says the program was ended from outside (force-quit, out of memory, sleep, power loss). Messages titled **Unexpected error** have **Show Details...** with the full traceback.

## If it does not work
- **Same message every time for one file**: the drive may be disconnected; check it in the file manager.
