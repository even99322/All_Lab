# Fit one trace
> Fit one S-parameter trace with a YIG resonance model to get the resonance frequency, linewidth and coupling, and decide whether the result can be trusted.

## Shortest procedure
1. **Open YIG Analysis from the Viewer.** Open a measurement with a complex S parameter in the Viewer, click the toolbar's **Analysis** button → **YIG Mirror Analysis...**. The window works on a read-only snapshot; the file is never changed. ![](yig_open.png)
2. **Choose the data and the frequency range.** In **DATA** on the left, choose the **S Parameter**; for 2D data also choose the **Sweep Axis** and pick a slice with the **Slice** slider or by clicking the 2D map. Drag on the 1D trace in **Data Preview** to select the range around the resonance (or type **Frequency Start / Stop**). ![](yig_range.png)
3. **Choose a model.** **FIT MODEL** → **Model Library...** and pick a model (`S11_single` for one YIG in reflection). **Model Function** lists the functions in that file. ![](yig_model.png)
4. **Make initial values and preview them.** **SINGLE FIT** → **Auto Guess** fills **Initial / Lower / Upper** in the parameter table; **Preview Initial Fit** shows whether the starting curve is roughly on the data. ![](yig_guess.png)
5. **Fit and read the result.** Click **Start Fit**. **Fit Results** then shows six plots (Magnitude, Phase, IQ Plane, Real, Imaginary, Residual) and the text report; the table gains **Fitted** and **Error**. ![](yig_fit.png)

## What the result means
- **Fitted / Error**: fitted value and 1σ standard error (from the covariance matrix, scaled by the residual). Parameters ticked **Fixed** are not fitted and show *(fixed)* in the report.
- **R² (dB)**: computed on |S| in dB inside the fit range. Sensitive to dip depth and width, blind to phase.
- **R² (complex)**: computed on complex S, so it checks amplitude and phase together; stricter than R² (dB) for complex models.
- **χ²_red**: weighted residual sum of squares per degree of freedom. Only compare it between fits of the same data with the same weighting.
- **Residual plot**: a good fit leaves noise-like residuals. A systematic S-shape or spike at the resonance means the model is missing a term (Fano phase, a second mode ...).
- **IQ Plane**: the data should lie on the fitted circle; a circle travelled the other way round needs the complex conjugate (the bundled S11 models already use that convention).

### Parameters (bundled `S11_single`)
| Parameter | Unit | Meaning |
|---|---|---|
| `w_m` | GHz | YIG resonance frequency |
| `alpha_r` | MHz | intrinsic loss |
| `kappa_b` | MHz | maximum coupling (at an antinode) |
| `phi` | rad | position phase of the YIG on the line; effective coupling κ_m = κ_b·sin²φ |
| `A` | — | background amplitude |
| `phi_0` | rad | background phase |
| `t` | ns | cable delay |
| `theta_fano` | rad | Fano phase (asymmetry of the resonance) |

**Important:** one trace only determines κ_eff = κ_b·sin²φ; κ_b and φ cannot be separated (large κ_b with small φ gives the same curve as small κ_b with large φ). Use **Continuous Fit** and the **Phase / Node** tab to separate them.

## Options and parameters
- **PREPROCESSING**: **Hampel outlier filter** removes spikes using a rolling median (**Window Half-Width** in points, **Threshold** in local-noise σ, **Dilation** extra points removed on each side; **Components** checks |S| only or |S|, Re and Im). **Manual Exclusion** takes frequency ranges in GHz, or tick **Drag on trace to add exclusion range** and drag on the plot. Excluded points are marked and not fitted.
- **FIT SETTINGS**:
  - **Resonance weighting σ = |S| + ε** (on by default): the dip, where |S| is small, weighs more, so it is fitted more accurately; a larger ε approaches equal weights.
  - **method**: `trf` (default, supports bounds) or `dogbox`.
  - **loss**: `linear` is ordinary least squares; `soft_l1`, `huber`, `cauchy`, `arctan` reduce the influence of outliers.
  - **maxfev**: maximum evaluations; **ftol / xtol**: convergence tolerance.
- **Parameter table**: accepts expressions such as `pi/2`, `np.deg2rad(30)`; the **Unit** column can be edited (frequency units decide how Continuous Fit moves its window).
- **Fitted → Initial** uses this result as the next starting point (useful when stepping through slices).
- **Save Settings / Load Settings** store model, table, fit settings and range as JSON.

## Output and data safety
- **Copy Fit Report** copies the report; **Export Result CSV** writes parameters, errors and metadata plus a PNG of the six plots with the same name.
- **OUTPUT → Folder** blank means `fit_results/` beside the source data; **Save single-fit CSV and plot automatically** saves after every fit.
- Fit settings and sessions are kept in the data folder's `fitting/`; the HDF5 measurement is only ever read.

## If it does not work
- **"The number of data points is smaller than the number of free parameters"**: the range is too narrow or too many points are excluded; widen the range or fix some parameters.
- **The fit ends quickly but the curve is clearly wrong**: the starting values are too far away. Use **Auto Guess** and **Preview Initial Fit**; the initial value and bounds of `w_m` matter most.
- **A parameter sits on a bound**: Fitted equal to Lower or Upper cannot be trusted; widen the bounds and fit again.
- **Error is nan or huge**: parameters are strongly correlated (e.g. `kappa_b` and `phi` both free) or the data barely depends on that parameter. Fix one of them.
- **High R² (dB) but low R² (complex)**: amplitude right, phase wrong; check `t` (delay), `phi_0` and the IQ direction.
- **"Fit Failed"**: usually the model output length does not match the data or the computation overflowed; check the model function and units.
