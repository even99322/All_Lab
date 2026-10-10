# Desktop pet
> A small companion that lives next to LabLogViewer. It is optional, never locks a scientific function, and closes with the program.

## Shortest path
1. **Open.** **Settings → Pet**. ![](settings_pet.png)
2. **Adopt.** In **Pets**, press **Adopt this pet** on the Cute Cat, Shiba or Capybara and confirm. The pet appears at the bottom right of the main screen.
3. **Play.** Drag it anywhere, even onto another monitor. Click it for a reaction and to bring LabLogViewer to the front (a click on a sleeping pet only wakes it; dragging never jumps). **Move the mouse over it**: a dark bar appears under the pet with the **panel** button (▦) and your quick actions. Right-click it for **Sleep / Wake up**, **Pet settings…** and **Hide pet**. ![](pet_panel.png)
4. **Hide or show.** Untick or tick **Show the pet** (or right-click the pet → **Hide pet**).  **Keep the pet above all programs** (ticked by default) keeps the pet on screen above every other program, also when you click another program or the desktop is empty. Untick it and ordinary windows can cover the pet. Programs playing full-screen video or slides are never covered by the pet.

## Panel, quick actions and levels
- **Panel** (▦ on the hover bar): the pet's level and your **quick actions**. The **EXP bar** is in **Settings → Pet**, not next to the pet.
- **Quick actions** are shortcuts to existing windows: YIG Analysis, Export data, 3D Surface, Node / Antinode, Compare traces, Formula, De-background, Show the Browser. Click a slot to run it; use its arrow to change or clear it. Viewer actions run on the Viewer you used last; if no measurement is open the pet says so.
- Slots by level: Lv.1 = 2, Lv.10 = 3, Lv.20 = 4, Lv.30 = 5, Lv.40 = 6, Lv.50 = 8. The first four also appear on the hover bar.
- **EXP comes from normal research.** Base unit = opening a measurement for basic data analysis (10). Opening the program once a day gives less (3). **Operator-type analysis** — the first real operation on a file such as transforms, formula, marks or zoom — gives 1.5× (15). **External analysis** such as a finished YIG fit, and a de-background result, give 2.0× (20). Exports count like basic analysis (10, once per file per day).
- **Streak bonus**: every 10 consecutive days with LabLogViewer opened adds +2 % to all EXP (10 days +2 %, 20 days +4 % …). Missing a day restarts the streak. Settings → Pet shows the current streak and bonus.
- The same event never pays twice (re-opening the same file, repeating the same export the same day, re-doing the same fit the same day); clicking or dragging the pet gives no EXP; a poor result still counts.
- About 20 measurements a day reach **Lv.50 in about half a year** (prototype; the final numbers come with the measurement software). Every level-up plays a celebration; Lv.10 / 20 / 30 / 40 / 50 also change the pet's look.
- **Scientific functions are never locked by the level.**

## Backpack, recent research, notifications, dock and dashboard
The panel has five tabs; locked tabs show 🔒 and the level that opens them.
- **Backpack** (Lv.1: 5 items … Lv.50: 50): **Add current measurement**; from **Lv.10** drag files, pictures or text onto the pet. Measurement files are kept as a **reference only** (never copied); pictures and text are stored in `state/pet_backpack/`. **Open**, **Copy**, **Copy all**, **Export selected…** (never overwrites: a same-name file gets “(2)”), **Remove**, **Clear** (does not delete measurement files).
- **Recent** (Lv.20): measurements, fits, exports and de-background results you worked on; double-click to open; **Pin / unpin** keeps an item at the top.
- **Alerts** (Lv.30): history of finished fits, exports, de-background, level-ups and errors; **Mark all read**, **Clear history**. Essential alerts still pop up on the pet at every level.
- **Dock** (Lv.40): the Quick tab shows backpack, unread alerts and recent items at a glance; the slot arrow menu can move actions left or right.
- **Dashboard** (Lv.50): today / this month / total counts of measurements, operator analysis, external analysis, de-background and exports, research days and total EXP; save and load **presets** of quick actions (e.g. “Morning Measurement”).

## Appearance and growth potions
- **Appearance** (Settings → Pet): after levelling up you can switch back to any look already unlocked (Lv.1, 10, 20 …). Clips of higher levels are not shown with a lower look.
- The hover bar never gets wider than the pet: extra quick actions stay in the panel.
- **Growth potions** come from the developer as a licence file for this computer (Settings → About → Licences…). A potion can add EXP, set the pet to a level (up to Lv.50) or give one **re-selection** chance (adopt another regular pet before Lv.50). Each potion is used **once**; installing it again does nothing.

## The adoption rule
- You can adopt **one** pet. A **new** pet becomes available only when your current pet reaches **Lv.50**.
- Pets you adopted before can be brought back at any time (their level is kept).
- Until your pet reaches Lv.50 the other regular pets show **Raise your current pet to Lv.50 first**.

## What the pet does
- Idles, blinks, glances at the mouse cursor now and then (never while asleep), naps after about 3 minutes without mouse movement and wakes when you move the mouse.
- Essential warnings reach the pet at every level and even while it sleeps: an unexpected error makes it jump with a red **!**.
- If you click another program the pet stays where it is; clicking the pet brings the Viewer you used last (or the Browser) to the front, even from another program or when LabLogViewer is minimised.
- The pet is a separate small window owned by LabLogViewer: closing LabLogViewer closes the pet. It does not read or change measurement files; its choice is saved in `state/pet.json` in the data folder.

## If it does not work, check
- **The pet disappears when I use another program**: **Settings → Pet → Keep the pet above all programs** is unticked, or the other program is in full-screen mode (the pet then waits behind it).
- **No pet appears**: **Show the pet** is unticked, or no pet is adopted yet.
- **Adopt this pet is grey**: the reason is written under the pet (usually *Raise your current pet to Lv.50 first*).
