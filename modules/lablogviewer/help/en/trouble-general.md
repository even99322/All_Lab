# General problems
> What to do first when LabLogViewer hangs, closes unexpectedly or shows a message you do not understand, and how to ask for help.

## Shortest procedure
1. **Hangs longer than 8 s.** If any window stops responding for 8 s, a report of what was running is written to the data folder's `logs/unresponsive-…-time.txt`; when it recovers, the status bar gives the path. Nothing is killed or changed. ![](trouble_general.png)
2. **Restart after an unexpected exit.** The next start offers **Safe Recovery Mode**: it restores the database and Browser but skips the last open Viewers and processing, so the same problem is not triggered again.
3. **Ask for help.** **Help → Export for AI...** creates one text with the whole guide (optionally with the current window state, without data or personal paths) to give an AI assistant with your question; or send the `logs/` report and **Settings → Debug → Copy Debug Information** to the developer.

## Common situations
| Situation | Check first |
|---|---|
| Large files open slowly | the first read takes time; view one trace in 1D first |
| A measurement will not open | is it a grey row in the Browser (not Labber or damaged)? |
| "Recovered from corruption" at start | a record file was damaged, backed up and started afresh; the backup is in the same folder |
| Stars or marks missing | does **Settings → General → Data Folder** point at your folder? |
| 3D or the Figure Builder closed | they run in their own processes; other windows are fine, open them again |

## If it does not work
- **Many report files**: old ones can be deleted without affecting records.
- **Safe Recovery Mode every time**: the app was not closed normally last time; check it is not being force-quit.
