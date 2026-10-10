# App icon and Windows shortcuts
> Choose one of seven icons or your own picture; on Windows the shortcuts follow it. New in 2.0.

## Shortest procedure
1. **Pick the icon.** **Settings → Appearance → App icon**: click one of the seven icons (number 5 is the default). The Dock or taskbar icon and every window icon change at once. To use your own picture, press **Import picture...** and choose an image (PNG, JPG, BMP... at least 32 x 32 pixels); it appears as the **My picture** button next to the icons and can be selected again later. Non-square pictures are padded to a square, not stretched. Below, **3D window icon** gives the 3D Surface window its own icon: choose one of three, or press **Import picture...** in that group to use your own (it becomes the **My picture** button in the same row, works like the app icon one). Or tick **Use the same icon as the app** to make it follow the app icon. ![](settings_appearance.png)
2. **Windows only.** The Desktop and Start-menu shortcuts made by the installer are rewritten to the chosen icon (Explorer may need a moment or a refresh to redraw it). If you pinned a shortcut yourself, press **Recreate Desktop shortcut** for a new one. ![](settings_appearance.png)

## What the result means
- A Windows shortcut stores its own icon, so only shortcuts LabLogViewer made can be updated; yours are never changed.
- The installer also asks which icon (1-7) to use when installing.

## Options and parameters
- No licence is needed for any icon, including your own picture.
- The imported pictures are kept as `custom_app_icon.png` (app) and `custom_3d_icon.png` (3D window) in the `state` folder next to your settings; the original file is never changed or needed afterwards.

## If it does not work
- **"This file cannot be used as an icon"**: choose a normal picture file at least 32 x 32 pixels.
- **The old icon stays on Windows**: refresh the Desktop (F5) or sign out and in; Windows caches icons.
