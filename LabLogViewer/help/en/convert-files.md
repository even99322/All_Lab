# Convert .nc ⇄ Labber .hdf5
> Turn Lab Control .nc files into .hdf5 files Labber can open, or Labber .hdf5 files into .nc — many at once.

## Shortest procedure
1. **Pick the files.** In the Browser select one or more Data (Ctrl/⌘ or Shift to select several), right-click → **Convert to Labber .hdf5...** or **Convert to Lab Control .nc...**. Or use **File → Convert**; in a Viewer, **File → Convert** starts with the open file.
2. **Choose where to save.** By default next to each original file, or in a folder you choose. The name stays, only the extension changes; when that name is taken _2, _3 … is added — **no file is ever overwritten**.
3. **Click Convert.** Each file is listed with ✔ where it was saved, ✘ why it failed, or ⚠ what to keep in mind. Files saved inside the open database appear in the list automatically. ![](convert_dialog.png)

## To Labber .hdf5
- **Writer**:
  - **Automatic** (default): uses the **Labber API** (the same route Lab Control uses for Labber export) when a Labber Python environment is found, otherwise **Built-in**.
  - **Labber API**: always Labber's own code. Set **Labber Python** (default `C:\LocalEnvs\Labber環境_venv\Scripts\python.exe`, the same as Lab Control).
  - **Built-in**: no Labber needed; writes the layout the Labber API writes (Channels, Step list, Log list, Data, Traces, Tags, Instrument config).
- **Axis order**: Labber's first Step-list channel is the inner (fastest) loop. The converter puts the .nc's inner axis first, so every cell of a 2D / 3D sweep lands in the right place in Labber.
- **Traces** (e.g. S21 vs Frequency) become Labber traces (complex stays complex); the frequency axis is stored as start + step. When it is not evenly spaced you get a ⚠: Labber shows an evenly spaced axis, LabLogViewer still reads the exact frequencies.
- The instrument settings snapshot goes to **Instrument config**.

## To .nc
- Lab Control's NetCDF layout (labcontrol-nc/1): one coordinate per swept axis, the trace axis as its own coordinate, complex values stay complex. Opens in Lab Control, LabLogViewer and Python's `xarray.open_dataset(file, engine="h5netcdf")`.
- Labber's instrument settings and fixed step channels go to the file's `labcontrol` attribute (snapshot); LabLogViewer's Metadata shows them.
- An unfinished 1D measurement converts the recorded points (⚠); an unfinished multi-axis one cannot be placed point by point and is reported as failed.

## If it does not work
- **Labber Python not found**: "Labber API" was chosen but the path is wrong or that Python cannot `import Labber`. Choose Automatic or Built-in, or pick the right python.exe under **Labber Python**.
- **Cannot open**: the file is not a format LabLogViewer reads, or it is still being written.
