# General problems
> What to do first when LabLogViewer hangs, closes unexpectedly or shows a message you do not understand, and how to ask for help.

## Shortest procedure
1. **Hangs longer than 8 s.** If any window stops responding for 8 s, a report of what was running is written to the data folder's `logs/unresponsive-…-time.txt`; when it recovers, the status bar gives the path. Nothing is killed or changed. ![](trouble_general.png)
2. **Restart after an unexpected exit.** The next start offers **Safe Recovery Mode**: it restores the database and Browser but skips the last open Viewers and processing, so the same problem is not triggered again.
3. **Read the real reason.** After an unexpected exit, **Recovery Report** now shows the Python or native stack of the crash (kept in `native_crash.txt` beside the session file). If it says the run "most likely ended from outside", the program was force-quit, ran out of memory, or the computer slept or lost power: no crash happened inside LabLogViewer. A message titled **Unexpected error** has a **Show Details...** button with the full traceback; copy it when you ask for help.
4. **Ask for help.** **Help → Export for AI...** creates one text with the whole guide (optionally with the current window state, without data or personal paths) to give an AI assistant with your question; or send the `logs/` report and **Settings → Debug → Copy Debug Information** to the developer.

## Common situations
| Situation | Check first |
|---|---|
| Large files open slowly | the first read takes time; view one trace in 1D first |
| A measurement will not open | is it a grey row in the Browser (not Labber or damaged)? |
| "Recovered from corruption" at start | a record file was damaged, backed up and started afresh; the backup is in the same folder |
| A `.hdf5.lock` file stays in a data folder | a program was closed while writing; it is treated as abandoned after about 2 minutes and removed by the next write, or delete it by hand when nobody is writing |
| Status bar says changes are waiting to be saved into data files | the file was in use (another computer, Labber, offline drive); Tags and stars stay on this computer and are retried about every 60 seconds and after a restart |
| Stars or marks missing | does **Settings → General → Data Folder** point at your folder? |
| **File temporarily busy** (for example *unable to lock file, errno = 35*) | A network drive (NAS) did not answer in time. The file is not damaged. LabLogViewer opens files read-only without file locks and retries a few times; wait a moment and open it again |
| One file is red after scanning a folder | only that file could not be read (input/output error on the drive); the rest of the folder was scanned; rescan later |
| 3D or the Figure Builder closed | they run in their own processes; other windows are fine, open them again |

## If it does not work
- **Many report files**: old ones can be deleted without affecting records.
- **Safe Recovery Mode every time**: the app was not closed normally last time; check it is not being force-quit.
