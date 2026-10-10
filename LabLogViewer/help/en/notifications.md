# Notifications (bell and pet bubble)
> A bell in the Browser window collects updates, finished measurements, login streaks and pet level-ups. New in 2.0.

## Shortest procedure
1. **Look at the bell.** In the Browser window, top right of the toolbar, the bell shows a red number when there are unread notices. Rest the mouse on it to read the count. ![](notice_bell.png)
2. **Open the list.** Click the bell: the newest notice is on top. An update notice lists its first three changes; **... more** opens the full update preview. Use **Mark all read** or **Clear** at the top. ![](notice_list.png)
3. **Read an update.** The preview groups the changes as New features, Improvements and Fixes. **Skip this version** hides this version and does not announce it again; **Close** keeps the notice in the list. ![](notice_update.png)
4. **See it on the pet.** With a desktop pet shown, a new notice also appears as a translucent bubble above its head for about 6 seconds. Several notices wait their turn; click the bubble to open the list. ![](notice_pet.png)
5. **Choose what you receive.** In the list press the gear button: **Mute all**, or switch off a kind (update, measurement, streak, level-up).

## What the result means
- **Update**: a newer version was announced. About 2.5 seconds after start the program reads the project's GitHub update file once in the background (only `latest.json`, 6 second timeout, silently skipped when offline); it can also read a folder on this computer. Apart from this one GitHub address the program connects to no web address, and network-share paths are always refused. The notice installs or restarts nothing; when the desktop pet is shown it announces the notice in a bubble too.
- **Measurement finished**: a measurement program reported that a run ended.
- **Login streak**: you opened LabLogViewer on 3, 7, 14, 30, 60, 100, 200, 365 ... days in a row (needs an adopted pet).
- **Level-up**: the desktop pet reached a new level.

## Options and parameters
- The list keeps the latest 50 notices and remembers them after you close the program.
- A notice is shown in the language in use when you read it.
- Without a pet on screen, notices only go to the bell.

## If it does not work
- **No bell number**: all notices may be read, or **Mute all** or that kind is switched off in the gear menu.
- **No update notice**: you are offline or the GitHub update file cannot be read, the version is not newer, or you chose **Skip this version**.
- **No streak notice**: the streak is counted by the desktop pet, so adopt one in Settings → Pet.
