# Measurement transfer (v0.17F)

The Database Browser transfers one interpreted Labber measurement at a time.
The sender parses with `load_experiment()` and reads `Experiment.get_data()`,
`list_dimensions()` and `step_axes`. The network layer never opens HDF5 or
reinterprets Labber Empty, Function or Scalar fields. The original HDF5 is
opened read-only and is never modified.

## Use

In the receiving Browser, choose a TCP port and press **Receive**. On the
sending Browser, enter the receiver's IP address and the same port. Drag a
measurement row from **Data in Folder** onto **Drop a measurement here to
send** in Quick Preview. A normal click still selects the row; double-click
still opens the existing Viewer. The drag starts only after Qt's normal drag
distance. The drop target selects the configured receiver; a drag token, not a
local filesystem path, is put in the Qt drag MIME payload.

The receiver listens on all IPv4 interfaces only while **Receive** is on.
Received `.llvmeasure` archives are stored outside the source database under
`~/.lablogviewer/received/`. `load_measurement(path)` reconstructs channel
metadata, step axes, channel dimensions and raw scientific arrays without the
sender's HDF5. It exposes `get_data()`, `list_dimensions()` and
`get_full_nd_array()` for later integration. This pass does not add a Viewer
adapter or session synchronization for received measurements.

## Format and safety

Version 1 is a ZIP container containing UTF-8 `manifest.json` and numeric
`.npy` arrays (`allow_pickle=False`). Channels are enumerated from the
existing data model, not from hard-coded S-parameter names. Real and complex
arrays retain dtype and shape. The manifest contains logical dimension names,
units, sizes, axis arrays, channel flags/relations and measurement identity,
but no absolute source path. Derived magnitude/phase arrays are not duplicated.

TCP uses one JSON line with protocol version, byte length and SHA-256,
followed by exactly that many archive bytes and an OK/error response. The
receiver validates the checksum and archive before an atomic move into its
storage directory. It rejects oversized, malformed, duplicated-member and
pickle/object-array payloads. Sending is on a Qt worker thread; receiving is
on a Python background thread. Qt widgets are updated through signals.

This is for trusted laboratory networks: there is no authentication, TLS,
discovery or remote Viewer control. A firewall may require an inbound rule.
The current 2 GB archive cap is a resource boundary, not a scientific limit.
Network endpoint settings are not persisted. The receiver handles one
connection at a time.

## Flux/Empty case

`0908 X2 Flux-dep.hdf5` is a real regression fixture. Its parser-interpreted
S21 has a 10001-point Frequency trace axis and a 399-point current sweep,
giving `(10001, 399)` complex values. `DC supply - 4 - Current` is recorded as
a relation-based channel with equation `x`; that metadata is transferred.
The protocol uses this interpreted model. It neither treats an HDF5 Empty
field as missing scientific data nor invents a separate Flux axis that the
current parser does not expose.
