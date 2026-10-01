# The windows and how they connect
> LabLogViewer has several windows; where each opens and what it is for.

## Shortest procedure
1. **Database Browser (first window).** Opens at start: find, preview, star, tag, comment, De-background, Scientific Figure Builder. The **Network Workspace** bar is at the top. ![](windows_browser.png)
2. **Viewer (second window).** Double-click a measurement in the Browser: 1D / 2D plots, panes, formula, marks, export. Each measurement can have its own Viewer. ![](windows_viewer.png)
3. **3D Surface.** Viewer **Analysis → 3D Surface...**; runs in its own process. ![](windows_3d.png)
4. **YIG Mirror Analysis.** Viewer **Analysis → YIG Mirror Analysis...**: fitting, Continuous Fit, Phase / Node. ![](windows_yig.png)
5. **Others.** **Settings**, **Help** (F1) and **Network Workspace** open from the top menu of any window; **Scientific Figure Builder** from the Browser's **Processing** menu (own process).

## What the result means
- 3D and the Figure Builder run in their own processes: if one hangs or closes unexpectedly, the other windows keep working and a message appears.
- If any window stops responding for 8 s, a diagnostic report is written automatically (see *General problems*).
- The next start restores the last database and open Viewers.

## If it does not work
- **Analysis menu is grey**: the Viewer has no data yet.
- **3D or YIG will not open**: the data has a single trace, or (for YIG) no complex S parameter.
