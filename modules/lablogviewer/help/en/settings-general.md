# General, language and 3D settings
> The Settings tabs: language, data folders, appearance, personal, 3D, debug and about.

## Shortest procedure
1. **Open Settings.** Top menu **Settings** in any window (opens directly, no sub-menu). ![](settings_general.png)
2. **General.** **Language** (English / Traditional Chinese, applied at once), **Data Folder** (see *Data folder*), **Largest data folder (Tag search)** (see *Search Tags in the largest folder*), **Auto refresh** (Off, every 1, 2 or 5 s; see *Open and browse a database*).
3. **Appearance.** **Theme**: Light, Dark, Follow System; **Advanced**: **Scientific Plot Appearance** (white or dark plots), **Export Plot Background**, **Toolbar Glass** thickness and frost; **App icon**: click one of the four icons (no licence needed). ![](settings_appearance.png)
4. **3D.** **3D Performance** (Balanced, High Quality, Resource Saving) and **3D Export Style** (Publication, Screen capture). ![](settings_3d.png)

## What the result means
- **Scientific Plot Appearance** changes only the plot area's colours (white suits projectors and print); **Export Plot Background** decides the background of copied and saved images and may differ from the screen.
- **App icon** changes the Dock / taskbar icon and every window's icon at once (the 3D and Figure Builder windows follow). The icon of the installed program file itself is fixed when the program is packaged.
- **3D Performance** is a vertex budget for display; data is never changed.
- Settings live in the data folder's `state/settings.json`; the separate 3D and Figure Builder processes follow changes automatically.

## Other tabs
- **Personal**: your own colours and icons; see *Personal colours* and *Custom icons*.
- **Debug**: version, system, Python / Qt versions; **Copy Debug Information** for the developer.
- **About**: version, **Licences...** (see *Licences*) and **What's New...**, the notes of this version; they also appear once on the first start after an update. After an update your records are kept; if a new version has to adapt them, a backup is made first in `update_backups` in the data folder.

## If it does not work
- **Some text stays English**: channel names, parameter symbols and scientific terms (Magnitude, S21, Hz ...) are kept on purpose.
- **A circle spreads when changing theme**: that is the animation; everything keeps working. See *Theme ripple, button spring, glass scroll bars*.
