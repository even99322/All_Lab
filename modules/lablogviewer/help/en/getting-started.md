# Five-minute start
> From a folder of measurement files to a curve on screen, a mark on it, and the plot in your slides.

## Shortest procedure
1. **Open a database.** In the first window (Database Browser), click the folder icon **Open Database** at the top left and choose a folder with Labber `.hdf5` files. ![](start_open.png)
2. **Preview.** Click a measurement in the middle list; **Quick Preview** on the right draws it at once. ![](start_preview.png)
3. **Open the Viewer.** Double-click it to open the Viewer (second window); on the left choose the **Y Axis** and **Transform**, e.g. Magnitude with dB. ![](start_viewer.png)
4. **Read a point.** Toolbar **Mark Tool** → **Point Mark** → **Add** → click the curve; its X and Y are listed on the left. ![](start_mark.png)
5. **Into your slides.** Press ⌘C (Windows: Ctrl+C) on the plot and paste into your slides, or use **Drag-to-Share** to drag it in. ![](start_copy.png)

## Next
- 2D sweeps: set **Plot Mode** to 2D; see *2D heatmaps and line cuts*.
- Finding data: see *Stars and Tags*.
- Fitting resonances: see *Fit one trace*.
- Press **F1** in any window to open its help page.

## Reading this Help
- **Folders** (the default look): every topic is a folder; click one and its guides come out as a stack of cards. Scroll (or press ↓ / Space) and each card stops at the top while the next one slides over it; the covered cards shrink a little, so the pile and the bar on the right show how far you are. Click a picture to see it full size; **Esc** returns to the folders.
- The **tabs** under the search field list the topics; click one to see its guides and jump straight to any of them. The search field searches every guide in both languages.
- Buttons on the right: **All topics**; **Show as a list / as folders** (the same guides as a list of pages with folding sections — the guide you are reading stays open); **Copy all guides for an AI assistant** (one click puts every guide plus a short description of what is open now — version, system, open windows; never data, paths or user names — on the clipboard); **Export for AI** (preview and save it as a file).

## Your data is safe
LabLogViewer only reads measured values. Everything you do (stars, tags, marks, fits ...) is stored in a separate folder; the one exception is comments, which go into Labber's own comment field (see *Data safety*).

## If it does not work
- **Empty list**: no `.hdf5` / `.h5` in the folder, or still scanning; see the status bar.
- **Double-click does nothing**: grey rows cannot be read.
- **⌘C does nothing**: click the plot first.
