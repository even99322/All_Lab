# General, language and 3D settings
> The Settings tabs: language, data folders, appearance, personal, 3D, debug and about.

## Shortest procedure
1. **Open Settings.** Top menu **Settings** in any window (opens directly, no sub-menu). ![](settings_general.png)
2. **General.** **Language** (English / Traditional Chinese, applied at once), **Data Folder** (see *Data folder*), **Largest data folder (Tag search)** (see *Search Tags in the largest folder*), **Auto refresh** (Off, every 1, 2 or 5 s; see *Open and browse a database*).
3. **Appearance.** **Theme**: Light, Dark, Follow System; **Advanced**: **Scientific Plot Appearance** (white or dark plots), **Export Plot Background**, **CPU threads** (how many worker threads background work may use; default is 75 % of this computer's logical CPUs, at least 1; try a lower number if the computer must stay responsive for other programs), **Toolbar Glass** thickness and frost; **Performance Monitor...** (opens a live window, see below); **App icon**: click one of the seven icons or import your own picture, and choose the 3D window icon (also one of three or your own picture; no licence needed); on Windows also **Recreate Desktop shortcut**. ![](settings_appearance.png)
4. **3D.** **3D Performance** (Balanced, High Quality, Resource Saving) and **3D Export Style** (Publication, Screen capture). ![](settings_3d.png)

## What the result means
- **Scientific Plot Appearance** changes only the plot area's colours (white suits projectors and print); **Export Plot Background** decides the background of copied and saved images and may differ from the screen.
- **Performance Monitor...** (Advanced) shows three live curves: *UI lag* (how late the interface answers; below about 50 ms feels smooth), *CPU* (percent of one core used by LabLogViewer) and *Memory* (the program's resident memory; a steady climb while you only open and close data means something is not being released). Below them, *Where the UI thread spends its time* lists the functions the interface was busy in, and *Recent stalls* lists every freeze of 0.15 s or more with where it was stuck. **Pause** stops the curves, **Reset** clears them, **Copy Report** / **Save Report...** write a text report (saved in the data folder's `logs/`) that you can send to the developer when something feels slow. It only observes; it changes nothing.
- On **Windows** the Desktop and Start-menu shortcuts made by the installer follow the icon you pick (Explorer may need a moment or a refresh to redraw it). A shortcut you pinned yourself keeps its own icon: press **Recreate Desktop shortcut** to get a new one with the current icon. The installer also lets you choose the icon (1-7) while installing. On macOS the Dock icon changes immediately.
- **App icon** changes the Dock / taskbar icon and every window's icon at once (the 3D and Figure Builder windows follow). The icon of the installed program file itself is fixed when the program is packaged.
- **CPU threads** takes effect at once and is remembered. It limits parallel background work such as 3D preparation. The main program may use this many threads; the 3D window runs as its own program and uses half of it (at least 1), so the two together do not take the whole computer; it never changes data or results. The largest value is the number of logical CPUs of this computer.
- **3D Performance** is a vertex budget for display; data is never changed.
- Settings live in the data folder's `state/settings.json`; the separate 3D and Figure Builder processes follow changes automatically.

## Other tabs
- **Personal**: your own colours and icons; see *Personal colours* and *Custom icons*.
- **Debug**: version, system, Python / Qt versions; **Copy Debug Information** for the developer.
- **About**: version, **Licences...** (see *Licences*) and **What's New...**, the notes of this version; they also appear once on the first start after an update. After an update your records are kept; if a new version has to adapt them, a backup is made first in `update_backups` in the data folder.

## If it does not work
- **Some text stays English**: channel names, parameter symbols and scientific terms (Magnitude, S21, Hz ...) are kept on purpose.
- **A circle spreads when changing theme**: that is the animation; everything keeps working. See *Theme ripple, button spring, glass scroll bars*.
