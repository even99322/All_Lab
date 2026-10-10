# Custom icons (SVG)
> Replace any toolbar icon with your own SVG; never locked.

## Shortest procedure
1. **Open it.** **Settings → Personal → Icons** lists every replaceable icon. ![](personal_icons.png)
2. **Choose an icon**, e.g. `zoom`.
3. **Replace.** **Replace with SVG...** and choose your `.svg`. With **Follow theme colour** ticked the icon recolours with the light / dark theme.
4. **Restore.** **Restore default** or **Restore all icons**.

## Limits (safety)
- Plain SVG only, up to 256 KB: no scripts, external links, embedded images or video, or event attributes.
- Your file is copied to the data folder's `state/personal_icons/`; the original icon is kept.

## If it does not work
- **"This SVG cannot be used"**: the message gives the reason (e.g. `<script>`, `<image>` or an external link); export plain SVG from your drawing program.
- **A black, invisible icon**: tick **Follow theme colour**, or the file uses a fixed dark colour.
