# Models, Model Builder and Library
> Know what the bundled models assume and their units; write your own with the Model Builder and keep it in the Model Library.

## Shortest procedure (a new model with the builder)
1. **Open the builder.** YIG Analysis → **FIT MODEL** → **Model Builder...**. ![](model_builder.png)
2. **Write the ideal model.** **1. Ideal Model**: **Function Name**, **Frequency Variable** (default `w`, in Hz), and the expression after **S_ideal =** in Python style or LaTeX, e.g. `1 - \frac{\kappa_e}{i(w-w_0)+\kappa/2}`. The rendered equation updates above.
3. **Set the parameters.** **2. Parameters** lists the parameters of the expression: give units (GHz, MHz, rad, ns ...); initial values and bounds may be left blank and are estimated from the data.
4. **Add optional terms.** **3. Optional Model Terms**: **Fano phase** (S = B + (S_ideal − B)·e^{iθ_F}), **Environment** (A·exp{i[φ₀ − 2π(w − w_c)τ]} with a **Delay Reference w_c**), **Conjugate output** (reversed IQ rotation), **Also generate a |S| model**.
5. **Preview, save and load.** **Preview with Current Data** draws the auto-guessed curve on the current data; **Save and Load** writes a `.py` and loads it in the main window. Tick **Add to Model Library when saving** (section 4) to store it in the library with a title and notes.

## What the bundled models assume
**`S11_single` (one YIG, reflection; in formulas_example.py, loaded by default when the analysis window opens)**
- ideal reflection: r = 1 − κ_m·e^{iθ_F} / (Γ_m/2 − i(ω − ω_m − Δ_m))
- κ_m = κ_b·sin²φ (effective coupling); Γ_m = (κ_m + α_r)/2; Δ_m = −(α_r/4)·sin 2φ
- environment: S = A·e^{i[φ_0 − 2π(ω − ω_m)τ]}·r, then complex conjugate (S11 convention)
- units: `w_m` GHz; `alpha_r`, `kappa_b` MHz; `phi`, `phi_0`, `theta_fano` rad; `t` ns; `A` dimensionless
- all frequencies are ordinary frequencies (Hz), not angular.

**`S11_node` (file formula_yig_node.py, for Phase / Node)**
- as `S11_single`, but the shift Δ_m = −(γ_0/4)·sin 2φ uses its own parameter `gamma_0` (MHz) instead of α_r.
- suggested roles: `kappa_b`, `alpha_r`, `gamma_0`, `t`, `theta_fano` shared; `w_m`, `A`, `phi_0` per slice; `phi` from the phase line.

**`S21_coupled` / `S21_single_mode` (file formula_s21_coupled.py, transmission)**
- S_ideal = 1 + e^{iθ_F}·κ / [i(ω_p − ω_w) − (κ + α) + g² / (i(ω_p − ω_d) − ξ)], ω_w the waveguide mode, ω_d the YIG, g the coupling; `single_mode` is g = 0.
- units: `w_w`, `w_d` GHz; `kappa`, `alpha`, `g`, `xi` MHz; `t` ns.
- versions ending in `_abs` fit |S| only; φ_0 and τ vanish in the absolute value and are absent.

## Choosing a model
- With phase (complex S) use a complex model; with |S| only use an `_abs` version, but phase-related parameters become harder to determine.
- Physical Nodes / Antinodes and phase linking require a model that declares κ_m = κ_b·sin²φ (`S11_single`, `S11_node`).
- More terms (Fano, a second mode) fit better but raise parameter correlations and errors; once residuals look like noise, stop adding terms.

## Model Library
**Model Library...** lists every model; search titles, functions and notes, and see the equation and notes of the selection. **Add Current Model** adds the loaded model; **Add from File...** adds a `.py`; **Open in Model Builder** reopens builder-made models; **Edit in Library...** changes title and notes; deleting can also delete the model file. **Copy model files into the library when adding** copies files into the library folder, so moving the original does not matter.

## Rules for your own `.py` model
- Every function whose first argument is the frequency `w` (Hz) appears in **Model Function**; names starting with `_` and `guess_*` functions are ignored.
- Return `np.hstack([real, imag])` (length 2N, complex fit), a complex array (length N, split automatically) or a real array (length N, fits |S|).
- Optional `guess_<function>(freq, s)` returns `{parameter: (initial, lower, upper)}` for **Auto Guess**; otherwise a built-in estimate is used.
- Optional `UNITS = {parameter: unit}` or `UNITS_<function>`; otherwise units are inferred from names (`_GHz`, `_MHz` suffixes, `theta` / `phi` → rad, `t` → ns). Frequency units decide how Continuous Fit moves its window.

## Safety check
A `.py` model is ordinary Python. Before loading, every file is checked statically (never run): files with dangerous code such as `subprocess`, `os`, `socket`, `eval`, `exec`, `__import__` are **refused**. Other unknown files ask once whether to trust them; the answer is remembered by the file's SHA-256, so an edited file asks again. Bundled and builder-made models are trusted automatically.

## If it does not work
- **"No usable model functions were found"**: the first argument is not the frequency, or there are fewer than two parameters.
- **"This file was not created by the Model Builder"**: only builder-made files open in **Open in Model Builder**.
- **"Model file not found … using the bundled model of the same name"**: the model file was moved; the bundled model with that name is used instead — check it is the one you want.
- **Refused when loading**: the file contains blocked code; remove everything that is not mathematics.
- **Odd units in results**: check the **Unit** column; with **Convert units to Hz / s / rad** ticked in the builder, write expressions in SI units.
