# De-background
> Divide the target measurement's complex S parameter point by point by one background trace (e.g. the line measured without a sample), producing a new `_debg.hdf5` file.

## Shortest procedure
1. **Open it.** Select the target in the Browser and click **De-background** in the toolbar (or **Processing → De-background...**). **Target Data** is filled with the selection. ![](debg_open.png)
2. **Choose the background.** **Background Data** → **Select...** and pick a background measurement (a single trace). ![](debg_background.png)
3. **Check compatibility.** Choose the **S Parameter**; the seven **COMPATIBILITY** checks turn to OK one by one: S Parameter, Target, Background shape, Frequency points, Frequency grid, Frequency range, Denominator. ![](debg_checks.png)
4. **Generate.** **Output** defaults to "Targetname_debg.hdf5" beside the target; click **Generate**. The database refreshes and the new file opens in a Viewer; its name contains _debg, so it gets the **De-background** Tag automatically. ![](debg_done.png)

## Data requirements
- Target: one complex S parameter, frequency × sweep (1D or 2D).
- Background: the same S-parameter name, **one** complex frequency trace.
- Same number of frequency points, same unit, same ordering, every frequency within a tiny numerical tolerance (the check shows the largest Δ and the tolerance), same start and end.
- No NaN, Inf or zero in the background; its smallest |BG| must exceed a numerical floor (about peak × 1.5×10⁻⁸), otherwise the **Denominator** check fails and nothing can be generated.

## What the result means
- For every sweep: S_debg(f) = S_target(f) / S_background(f), a complex division — the background's amplitude and phase are both removed. In dB, |S| becomes target dB − background dB.
- **Precision (important):** to match Labber's legacy `_debg` files exactly, the real and imaginary parts are quantised to float16 precision and stored in the original float64 layout — about **3 significant digits** (relative error about 10⁻³). For fine fits or high precision, analyse the original target and background instead of relying on the last digits of a `_debg` file.
- The output is a full copy of the target with only the chosen S parameter replaced; other channels and metadata are unchanged and Labber can open it.
- The target and background files are never modified; the output cannot replace either.

## Options and parameters
- **Output → Choose...**: another location or name (must be `.h5` / `.hdf5`).
- **If the output name is taken** you are asked: **Overwrite** (replace it), **Keep Both** (the new file is saved as "name (2).hdf5"; this is the default), **Choose Another…** or **Cancel**. The target and background can never be the output.
- **Output on the NAS / a shared folder** works: such shares cannot make the "hard link" normally used to publish the finished file, so the program checks the name once more and renames the temporary file instead.
- Processing runs in the background and **Cancel Processing** stops it without leaving a partial file (a temporary file is renamed only when complete).
- The window is non-modal; the Browser stays usable (but renaming waits).

## If it does not work
- **Generate is grey**: see which **COMPATIBILITY** row is not OK; the text explains why.
- **Frequency grid mismatch**: the two measurements used different frequency settings (points, range or unit); measure the background again with the same settings.
- **Background shape mismatch**: the background is a 2D sweep; choose a single-trace background.
- **Near-zero denominator**: the background amplitude almost vanishes at some frequency (e.g. a failed background measurement) and cannot be used.
- **"The ratio exceeds the legacy-compatible component storage range"**: a divided value exceeds float16's range (about 6.5×10⁴), usually because the background amplitude is too small.
- **The new file is not found afterwards**: it was written outside the current database; check the output path.
- **"Could not safely publish without replacing an existing file: [Errno 45] Operation not supported"** (versions before 1.0.3, output on the NAS): update, or write the output to a local folder and copy it over.
