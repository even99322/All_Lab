# Continuous Fit
> Fit every slice of a 2D sweep (field or current sweep) automatically, with the fit window following the resonance, to get each parameter as a function of the sweep.

## Shortest procedure
1. **Fit one slice well first.** Follow *Fit one trace* on a slice with a clear signal, then click **Fitted → Initial**. The width of that slice's frequency range becomes the window width for every slice. ![](cont_seed.png)
2. **Set the range and the tracking parameter.** In **CONTINUOUS FIT SETTINGS**: **Start / End Slice** (the **Current** buttons insert the current slice) and **Stride**; **Tracking Parameter 1** = the resonance-frequency parameter (e.g. `w_m`). The text below shows the window width and slice count. ![](cont_settings.png)
3. **Start.** Click **Start Continuous Fit**. The progress bar counts slices; fitting runs in a background process so the window stays usable, and **Cancel** stops at any time. ![](cont_run.png)
4. **Read the results.** Open the **Continuous Fit** tab: the table has one row per slice (success, R², parameters); **All Parameters** plots everything, **Single Parameter** plots one parameter against the sweep (**Y: / X:**). Select a row and click **Preview Selected Slice** to see that slice's fit. ![](cont_results.png)

## What the result means
- Each slice is an independent single fit; R² and χ²_red mean the same as in *Fit one trace*.
- Rows with **ok = no** are not used to move the window; the **msg** column says why (R² below threshold, too few points, fit failure).
- A sudden jump in a parameter curve usually means the window jumped to a neighbouring mode or to noise, not physics; check that slice with **Preview Selected Slice**.
- Near a Node the signal fades (κ_eff → 0), so `kappa` and `phi` errors grow or fits fail — an expected physical result. Use the phase-linked fit in *Phase / Node* to cross Nodes.

## How the window moves (important)
- **Window 1**: centre = Tracking Parameter 1 from the previous *successful* slice + offset, where offset = centre of your range − the tracking parameter's initial value; width = your current frequency-range width.
- **Tracking Parameter 2 (optional)**: a second mode with its own **Window 2 Width / Offset**; the fit uses the union of both windows. **= Window 1** copies the width.
- **Bound frequency parameters to fit window** (on by default): resonance-frequency parameters are bounded to the window.
- Tracking parameters must have a frequency unit (Hz / kHz / MHz / GHz). Only frequency parameters whose value lies inside the measured band are treated as *positions* and moved; linewidths (also MHz) are not.

## Options and parameters
- **Initial Guess Source**:
  - **Previous Fit** (default): start from the previous slice's result — the most stable.
  - **Table Initial Guess (shift with window)**: start every slice from the table, only shifting frequency parameters to the new window.
  - **Auto Guess per Slice**: guess again for each slice — for very different slices, but more prone to discontinuities.
- **R² Threshold** (default 0.9): slices with R² (dB) below it count as failed and do not update tracking.
- **Stop on failure**: stop at the first failure.
- **Rolling table** (Track, Parameter, Unit, ± Range, Extrapolate): for ticked parameters the initial value is the previous slice's value (or a linear extrapolation from the previous two with **Extrapolate**) and the bounds are initial ± Range. Useful for smoothly changing non-frequency parameters such as `phi`. **Keep rolling bounds within parameter bounds** clips them to the table bounds.
- Preprocessing (Hampel, manual exclusions) is applied to every slice.

## Output
- **Export CSV**: one row per slice with window range, points, ok, R², R² (complex), χ²_red, nfev, msg, and every parameter with its error; the first line is metadata (model, units, settings). **Load CSV** reloads earlier results.
- **OUTPUT → Export Continuous Fit results automatically** saves on completion (with a parameter plot PNG).
- While running, results are continuously backed up to `<file>_<model>_batch_autosave.csv` in the output folder, so an unexpected exit does not lose them.
- **Selected Fit → Initial** puts the selected slice's result back into the table.

## If it does not work
- **Many failures from the start**: the seed slice was not fitted well or the window is too narrow. Go back to step 1 and fit a clear slice, then **Fitted → Initial**.
- **Tracking runs away**: a window that is too wide catches a neighbouring mode; too narrow loses a fast-moving resonance. Adjust the range width, use **Stride** 1, or start from the other end (Start > End is allowed).
- **"Tracking parameter … has non-frequency unit"**: fix the tracking parameter's **Unit** in the table.
- **"Too few points in the fit window"**: the window left the measured band, or too many points are excluded.
- **Repeated failures near Nodes**: the signal physically vanishes there; use **Skip slices near Nodes** or the phase-linked fit in *Phase / Node*.
