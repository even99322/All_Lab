# Formula
> Rescale X or Y of a 1D plot with an expression (Hz → GHz, linear → dB, divide by a value ...). Only the display changes; the file never does.

## Shortest procedure
1. **Find the formula panel.** **FORMULA** on the Viewer's left side (1D mode only): `x = F(x, y)` above, `y = G(x, y)` below. ![](formula_panel.png)
2. **Type expressions.** For example X `x/1e9`, Y `20*log10(|y|)`. The preview below shows the ordinary mathematical form (fractions, roots, absolute values) as you type. Blank means that axis is unchanged. ![](formula_preview.png)
3. **Apply.** Click **Apply**. The status reads "Formula applied to the current displayed X/Y values." and the plot updates. ![](formula_applied.png)
4. **Undo.** **Reset** returns both axes to their original values.

## What the result means
- `x` and `y` are the *displayed* values — after **Transform** (Magnitude, Phase ...) and **dB**. With dB ticked, `20*log10(|y|)` takes dB twice.
- The formula affects the current (active) pane's display; Marks, local analysis and "Displayed Data" exports use the rescaled values; "Raw Data" exports are unaffected.
- The HDF5 file is never modified; the formula is remembered with the Viewer's display state and returns next time.

## What you can write
- Variables `x`, `y`; constants `pi`, `e` (also `np.pi`).
- `+ - * /`, powers `^` or `**`, absolute value `|y|` or `abs(y)`.
- Functions: `sqrt`, `log10`, `ln`, `log` (natural), `exp`, `sin`, `cos`, `tan`, `abs`.
- LaTeX such as `\frac{y}{x}`, `\sqrt{x}`, `\left|y\right|` also works.
- For safety only mathematics is allowed; any other Python call, attribute or import is rejected.

## Examples
| Goal | Type |
|---|---|
| Frequency Hz → GHz | X: `x/1e9` |
| Linear amplitude → dB | Y: `20*log10(|y|)` (with dB unticked) |
| Normalise to a reference | Y: `y/0.35` |
| Power | Y: `y^2` |
| Phase rad → degrees | Y: `y*180/pi` |

## If it does not work
- **No formula panel**: formulas are for 1D plots; set **Plot Mode** back to 1D.
- **A red error**: the expression is malformed or uses a name that is not allowed; the message names it.
- **Gaps or broken lines**: e.g. `log10` or `sqrt` of negative values, or division by zero, give non-finite points that are not drawn.
- **"Saved Formula was invalid; identity was restored."**: a formula saved earlier is no longer valid and was cleared.
