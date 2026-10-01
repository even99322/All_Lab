# Phase / Node
> From Continuous Fit results, find the YIG's phase line, the physical Nodes (coupling vanishes) and Antinodes (strongest coupling), and separate κ_b from φ with phase-linked fits.

## Shortest procedure
1. **Finish a Continuous Fit first.** Use a model that declares κ_m = κ_b·sin²φ (bundled `S11_single` or `S11_node`) and complete *Continuous Fit* with at least five successful slices. This tab only uses Continuous Fit results. ![](phase_start.png)
2. **Check the parameter mapping.** **Phase / Node** tab → **PHYSICAL MAPPING**: **Phase Parameter φ** = `phi`, **Resonance Frequency Parameter** = `w_m`, **Coupling Parameter κ_b** = `kappa_b`; keep **Phase Period P** at 1 π and **κ_eff = coupling parameter × sin²(phase parameter)** ticked. They are guessed from the parameter names and are usually right. ![](phase_mapping.png)
3. **Estimate the phase line.** **PHASE LINE** → **Estimator Source** = **κ_eff = κ_b sin²φ (recommended)** → **Estimate from Continuous Fit**. T, φ_ref, f_ref and κ_b are filled in; the text below shows the fit quality and Node spacing. ![](phase_estimate.png)
4. **Look at Nodes / Antinodes.** The four plots update: points on **κm vs fm** should follow the curve; **2D Experimental Data** shows physical Nodes (φ = nP) and Antinodes (φ = (n + ½)P); the **location table** lists each frequency. ![](phase_nodes.png)
5. **(Advanced) phase-linked or global fit.** To truly separate κ_b and φ, use **PHASE-LINKED CONTINUOUS FIT** or **GLOBAL LINKED FIT** (see *Advanced reference*). ![](phase_global.png)

## What problem this solves
A single fit only gives the effective coupling κ_eff = κ_b·sin²φ: κ_b (maximum coupling) and φ (the phase of the YIG's position) are mixed. But φ changes linearly with the resonance frequency: φ(f_m) = φ_ref + 2π·T·(f_m − f_ref), with T = x / v_g (YIG position over group velocity, in ns). As the sweep moves the resonance, κ_eff rises and falls as sin²φ; the whole curve κ_eff(f_m) gives κ_b, T and φ_ref together, and so which frequencies are Nodes (zero coupling, where the 2D map's signal breaks off) and which are Antinodes.

**Data needed:** a Continuous Fit of one 2D sweep with one model; the resonance should ideally cover at least one Node spacing (Δf_node = 1 / (2T)).

## Fields and units
| Field | Unit | Meaning and starting value |
|---|---|---|
| Phase Period P | π | period of κ and sin²φ. π for the physical model — do not change unless your model differs |
| T = x/v_g | ns | slope of phase against frequency. Node spacing = P / (2π·T) = 1 / (2T) for P = π |
| φ_ref | rad | phase at f_ref (folded by P) |
| f_ref | GHz | reference frequency, by default the middle of the fitted band |
| κ_b (estimated) | as the coupling parameter (usually MHz) | maximum coupling from the κ_eff curve |
| T Search Maximum | ns | upper limit of the T search (default 20 ns). Too small misses the true T; too large is slower and may pick a too-short period |

T, φ_ref and f_ref can be typed in; Node positions and plots update immediately.

## Estimator Source: three ways
- **κ_eff = κ_b sin²φ (recommended)**: fits κ_b·sin²(phase line) to each slice's κ_eff. The most robust; outlier slices (4σ) are dropped. Needs ≥ 5 successful slices.
- **Fitted φ (mod π)**: uses each slice's fitted φ. A single φ is only known mod π and may be the mirror −φ, so the line is found by folding and a grid search; the use of the mirror solution is reported.
- **Dip candidate frequencies**: without reliable fits, **Extract Dip Candidates** finds shallow-dip frequencies from the signal depth (**Candidate Threshold** default 0.35), or type frequencies yourself; then **Estimate from Candidates** derives T from their spacing. At least two are needed.

**Note:** sin² is symmetric under φ → −φ, so κ_eff cannot tell the sign of T; T > 0 is reported by convention.

## Physical versus Coarse — can they be mixed?
- **Physical**: φ = nP (Node) and φ = (n + ½)P (Antinode) from the phase line. Only computed when the model declares κ_m = κ_b·sin²φ; these positions have physical meaning and can be reported.
- **Coarse**: **Coarse Detector (empirical fallback)** is purely empirical: find the dip per slice → fit a line to the dip trajectory → average and smooth the transmission along it → peaks are Node candidates, valleys Antinode candidates. Settings: **Candidate type**, **Trajectory half-width** (default 0.25 GHz), **Smoothing points**, **Minimum sweep distance**, **Prominence**, **Manual dip-depth threshold**.
- **Do not mix them.** Coarse results are *candidates*, not physical extrema; the table's **Method** column shows the source. Use coarse candidates to find rough positions when fits are poor, or as input to **Dip candidate frequencies** — but final physical positions should come from the phase line.

## What the plots and table show
- **κm vs fm**: each slice's κ_eff (points) and κ_b·sin²φ from the phase line (curve). Points should follow the curve; rejected outliers are marked.
- **Phase Line**: each slice's φ folded by P against f_m, with the line. Nodes sit at 0 / P after folding.
- **2D Experimental Data**: the raw map with Nodes / Antinodes. Nodes should sit where the signal fades or breaks — the most direct check.
- **Signal Depth**: dip depth per slice against frequency; near Nodes it approaches 0.
- **Location table**: Type (Node / Antinode), Method (physical / coarse), Sweep, Frequency (GHz), Phase (rad), κm (Hz), Status.

## Can the result be trusted?
- The estimate's **R²** (κ_eff fit) is close to 1 and most slices are used (used N / M).
- Points on κm vs fm show no systematic deviation; Nodes on the 2D map sit where the signal vanishes.
- T's error (±) is small relative to T, and at least one Node spacing fits inside the band.
- If the band is short and κ_eff shows no rise and fall, T is essentially undetermined — do not trust it.
- After a global fit: reasonable χ²_red, good minimum and median per-slice R², small errors on T and φ_ref.

## Advanced reference
### PHASE-LINKED CONTINUOUS FIT
- **Link Mode**: **Hard link** — φ is not fitted, it comes from the phase line; **Soft link** — φ is fitted within the line's prediction ± **Soft-Link Range** (default 0.3 rad).
- **Skip slices near Nodes (weak signal)** (on by default): slices whose predicted |φ − nP| is below the threshold (default 0.12 rad) are not fitted; the window is extrapolated across the Node. The text below shows the skipped frequency width.
- **Fix shared parameters**: fix κ_b, α_r ... at the global-fit or κ_b-estimate values. When φ is linked to f_m, freeing κ_b and γ_0 per slice can make f_m unidentifiable — fixing is recommended.
- **Start Phase-Linked Continuous Fit** replaces the results in the Continuous Fit tab (use **Export CSV** first to keep the old ones).

### GLOBAL LINKED FIT
All slices are fitted together. Give each parameter a role in the table: **Per Slice** (e.g. `w_m`, `A`, `phi_0`), **Shared** (e.g. `kappa_b`, `alpha_r`, `t`), **Fixed**, **Phase Line** (φ only, from T and φ_ref). **Reset Roles** restores the suggestion.
- **Fit T / Fit φref**: also fit T and φ_ref (both on by default).
- **Slice Stride**, **Use successful slices only** (on by default), **max nfev** (default 200; each evaluation covers all slices).
- The report lists shared parameters ± errors, the phase line, per-slice parameters and R², and physical Node frequencies. **Shared Values → Parameter Initials** writes shared values to the table; **Send Results to Continuous Fit** shows per-slice results there; **Export Global Fit CSV** saves them.

## Output and data safety
- **Export Location Results CSV...** saves the location table; the global fit has its own CSV; the report text can be copied from the text box.
- Results are only written to files or folders you choose; the HDF5 measurement is never modified.

## If it does not work
- **"At least five successful fitted traces are required"**: too few successful Continuous Fit slices; improve that first.
- **"Select the coupling parameter κ_b"**: no coupling parameter in PHYSICAL MAPPING, or the model has none.
- **No physical Nodes / Antinodes shown**: the model does not declare κ_m = κ_b·sin²φ (e.g. your own model) or T = 0. Use a bundled model or add `PHYSICAL_COUPLING_RELATION = "kappa_b * sin(phi)**2"` to your model file.
- **Nodes do not match the 2D map**: T may be a multiple or fraction of the true period. Adjust **T Search Maximum** (try smaller first) or cross-check with **Dip candidate frequencies**.
- **"The T search range is too large"**: the band is very wide or the maximum too large; lower **T Search Maximum**.
- **Global fit: "There are no free parameters" or "Fewer than two usable slices"**: check the roles (all Fixed?) and **Use successful slices only**.
- **Soft link: "requires the phase parameter to be free"**: `phi` is ticked Fixed in the parameter table.
