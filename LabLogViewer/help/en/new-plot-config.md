# Plot settings: rotation, operations, smoothing, third dimension
> The Viewer's left panel can now clean up and re-express data before it is drawn: rotate complex data, subtract a mean, take a derivative, smooth, and (in 2D) work along the third axis. Only the display changes; the HDF5 file never does.

## Shortest procedure
1. **Open the panel.** Load a file in the Viewer. Under **PLOT SETTINGS** scroll to **Fixed Rotation** (1D and 2D) and, in 2D mode, **Third dimension**. ![](plotcfg_2d.png)
2. **Pick an Operation.** In **Fixed Rotation** open **Operation** and choose e.g. **y-<y>** (subtract each trace's mean) or **d/dx** (derivative along X). The plot redraws at once and the colour-bar or axis label shows the new quantity (for example "d/dx"). ![](plotcfg_1d.png)
3. **Smooth.** Type a number in **Smooth** (3 means a running average over 3 points; 1 is off). Noise drops, sharp features widen.
4. **2D only: work along the other axis.** In **Third dimension** choose a **Channel** (any sweep channel; it becomes the heat map's Y axis), then its own **Operation** and **Smooth**. Choosing **d/dx** here gives a derivative map along Y drawn with a blue/red diverging colour scale. ![](plotcfg_third.png)
5. **Go back.** Set **Operation** to **None** and **Smooth** to 1; everything returns to the raw display.

## What each control does
| Control | Meaning |
|---|---|
| **Rotation/x** + **Calculate** | complex data only: rotates the I/Q plane by an angle (degrees) before Magnitude/Re/Im/Phase are taken; **Calculate** fits the angle that puts the signal on the real axis |
| **Fixed Rotation** + **Calculate** | the same, but one angle for the whole data set instead of per trace |
| **Auto-rotate** | rotates every trace so its main axis of variation lies on the real axis |
| **Plot in dB** | shows Magnitude as 20·log10 (same as the Transform dB option; greyed out unless the plotted quantity is Magnitude) |
| **Unwrap angle** | removes 360° jumps of a Phase map along X |
| **Operation** | **None**; **y-<y>** subtract the mean; **Normalize** divide by the largest absolute value; **d/dx** numeric derivative; **FFT** magnitude of the Fourier transform along X; **Histogram** distribution of y values (set **Bins**); **Histogram-2D** (2D plots) distribution of the map values, and for complex data the real-versus-imaginary (I/Q) histogram opens in its own window |
| **Smooth** | running average over N points |
| **Traces / Show individual** | 1D: draw every selected trace, or only the active one |
| **Third dimension › Channel** | which sweep channel is the Y axis |
| **Show trace list**, **Ignore x-data** | draw by point number instead of X values |

## What the result means
- Processing order is fixed: rotation → component (Magnitude, Re, Im, Phase) → dB / unwrap → Operation → Smooth → third-dimension Operation and Smooth → equations. Equations therefore act on the already processed values.
- **d/dx** is per physical unit (value change divided by X change), not per point. With dB data the unit shows as "mdB"-style derived units.
- **FFT** and **Histogram** change the X axis meaning: it becomes frequency-of-X or value bins; the axis label follows.
- Marks, Compare Traces and "Displayed Data" exports use the processed values; "Raw Data" exports do not.

## If it does not work
- **"Rotation needs complex (I/Q) data; this channel is real."**: pick a channel stored as complex, or use Transform instead.
- **"Cannot calculate rotation"**: the data are constant or contain only NaN.
- **Histogram-2D (I/Q) says it needs complex data**: same reason; real channels only have a value histogram.
- **d/dx looks very noisy**: a derivative amplifies noise; raise **Smooth** first.
- **Third dimension has no effect in 1D**: it only exists in 2D mode.
