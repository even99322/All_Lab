# Node / Antinode Reference Provenance

The two adjacent Python files are unchanged copies of the supplied experimental
reference implementation. Their SHA-256 digests are:

- `labber_core.py`: `d3b2a724a0307fd2472cb3380057810ae811470fc42c0aa317023ca2018a492b`
- `labber_viewer_app.py`: `fe4e922d718068f28ec93940b738ab735d5399c439d1e08dba79b0afff77128d`

The reference Node workflow is in `labber_viewer_app.py`, method
`run_node_detection` (around lines 799–906 in the copied source). For each
sweep trace it finds the minimum of `abs(S)`, defines dip depth as the
trace maximum minus that minimum, and fits a first-order frequency trajectory
to traces whose dip depth exceeds either a user threshold or the median raw
dip depth. It then averages `abs(S)` in an inclusive, trajectory-centered
frequency window for every sweep value. Smoothing uses a normalized box kernel
with NumPy `convolve(..., mode="same")`, followed by copying the un-smoothed
values into the first and last `window_length // 2` entries. SciPy
`find_peaks` applies the user distance and prominence to this smoothed curve.
Reported Node frequencies are measured minima within the dynamic window, not
the fitted trajectory values.

The supplied reference has no Antinode detector and includes no dedicated
Node / Antinode HDF5 reference result. v0.15C therefore defines Antinodes as
local minima of the same smoothed average-transmission curve by applying the
same distance/prominence parameters to its negation. The production adapter
uses LabLogViewer's `Experiment.get_2d_data` API and accepts only a complex
trace with exactly one active sweep axis and a named frequency axis. It does
not depend on this directory at runtime.

The original reference Python files are kept under the workspace's
`development_reference/node antinode/`; no source HDF5 files are included here.
