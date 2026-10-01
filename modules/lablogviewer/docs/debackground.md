# De-background Processing (v0.15B)

## Architecture

The Browser collects file/channel choices and reports compatibility. The
processing dialog dispatches inspection and generation to `QThread` workers.
`app.core.debackground` is the testable processing service; it reads each
source through `HDF5Reader -> AutoDetector -> Experiment`. The isolated
`app.core.hdf5_processing_writer` copies the Target to a same-directory
temporary file and can update only the selected, pre-existing complex trace
dataset. The finished copy is parsed again before atomic publication. GUI
modules do not import `h5py` or perform scientific array operations.

The output defaults to `<target-stem>_debg.hdf5`. Target and Background paths
are rejected as output destinations. Existing output is refused by the core
service unless the GUI has received explicit replacement approval. Cancel or
failure removes the temporary file. The Browser refreshes only when output is
inside its currently open database root, and Open in Viewer uses the ordinary
Browser Viewer path.

## Notebook and legacy-file investigation

The original notebook is preserved unchanged at
`references/debackground/debackground.ipynb`; the original remains under
`development_reference/debackground/`. Its relevant call is:

```python
ccukit.labberreader.VNAxANY(target_path).debackground(
    background_path, create_file=True
)
```

The notebook also refers to `ccuhdf.VNAxANY.add_debg_file(..., mode='/')`.
The `ccukit`/`ccuhdf` package implementation was not installed or available
locally, so the exact legacy internal algorithm could not be inspected. The
behavior below is established by reading the real Target, Background, and
legacy output files and reproducing the legacy result sample-for-sample.

For the available reference trio:

- Target: `development_reference/debackground/0828 RSMEP_1.hdf5`
- Existing result: `development_reference/debackground/0828 RSMEP_1_debg.hdf5`
- Background: `Data/PRL RSMEP best data/2026/08/Data_0828/0828 5.0197~5.0297GHz BG.hdf5`

The Target's `/Traces/VNA - S21` is a complex Labber trace represented as
real/imag components, HDF5 shape `(501, 2, 855)`, dtype float64. The Background
trace has shape `(501, 2, 1)`. Both have a 501-point Frequency grid from
5.0197 GHz to 5.0297 GHz, with identical stored grid values. The Target's
sweep dimension is Average Current with 855 acquired entries.

## Scientific operation and compatibility

For each acquired Target sweep entry `p`, the core is:

```text
S_debg(f, p) = S_target(f, p) / S_background(f)
```

Division is performed on complex samples. No magnitude or dB subtraction,
interpolation, resampling, extrapolation, cropping, padding, or averaging is
performed. Target Frequency is found from the parsed trace axis semantics and
Labber `_t0dt` calibration; the parser's axis is checked against calibration
for all entries. The Background must be one complex Frequency trace with one
entry and no active sweep dimensions. Other Background dimensionalities are
rejected rather than reduced by an invented averaging rule.

Point counts and units must match. Grids must be finite, strictly monotonic,
have the same direction, and match point-by-point in original order. The
absolute tolerance is:

```text
min(8 * float64_epsilon * max(1, max(abs(target_grid)), max(abs(bg_grid))),
    minimum_nonzero_grid_spacing * 1e-8)
```

The denominator must be finite and every `abs(S_background)` must exceed
`sqrt(float64_epsilon) * max(1, max(abs(S_background)))`. Zero and near-zero
denominators are not regularized with epsilon; the UI reports the issue and
does not enable Generate.

The known legacy output revealed an additional storage behavior: after complex
division, the real and imaginary components are each quantized to float16,
then written into the Target's original float64 dataset. Applying that
component conversion reproduces every sample of the supplied legacy output
exactly. This precision conversion is therefore retained for compatibility;
ratios outside the finite float16 component range are rejected. In the
reference case, before quantization, versus the legacy result, complex
differences were max `0.0003446285685799601`, mean `0.0001171463420854955`, and
RMSE `0.00014190745558077304`. Real, imaginary, magnitude, and phase maximum
absolute differences were respectively `0.00024413898236752551`,
`0.00024413945690415773`, `0.00034417441827261364`, and
`0.00043480884374957895` radians. The compatibility-quantized result matched
the legacy complex samples exactly.

## HDF5 preservation

The output begins as a byte copy of the Target. Only the existing selected
complex trace's real/imag value dataset is changed; dimensions, channel
metadata, axes, attributes, views, and unrelated measurement datasets are
left as copied. The reference Target, legacy output, and Background all had
the same 79-node tree. The new result retained the Target's complete tree,
dataset shapes and dtypes, root attributes, trace attributes, and
`/Views/Current view` attributes. Only `/Traces/VNA - S21` differed from the
Target. The new parser reopened the result as a complex 501 x 855 trace.

## Known limits

- The reference package source (`ccukit`/`ccuhdf`) is unavailable here. The
  ratio operation and float16 component conversion are strongly established
  by exact real-data reproduction, but undocumented behaviors for other
  package versions cannot be asserted.
- Only real Labber categories present in the local development data can be
  claimed as tested. Background must currently be a single, unambiguous
  complex Frequency trace; arbitrary multi-dimensional Background reduction
  is unsupported.
- Frequency calibration must describe a common grid across all acquired
  Target entries. Sweeps with changing Frequency grids are rejected.
- Windows-native GUI behavior still requires validation on Windows, although
  paths, temporary files, workers, Qt controls, and publication use
  cross-platform Python/Qt primitives.
