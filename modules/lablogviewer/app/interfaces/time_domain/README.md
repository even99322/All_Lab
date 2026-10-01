# Time Domain Interface

This directory is the reserved integration point for a future Time Domain
application. No FFT, conversion, signal-processing, simulation, or instrument
functionality is implemented by the v0.17A placeholder.

Implement future behavior in `interface.py` by overriding
`is_available()` and `launch(context)`. Return `InterfaceResult.launched()`,
`InterfaceResult.unavailable()`, or `InterfaceResult.failed(message)`; the
Browser owns localized user-facing unavailable/error reporting.

The optional `InterfaceContext` fields are described in
`app/interfaces/README.md`. Unavailable metadata remains `None`; no source file
is opened by this integration hook. Normally only
`app/interfaces/time_domain/` needs editing. Keep time-domain science out of
`BrowserWindow`, do not modify Labber HDF5, and do not add GUI-level `h5py`
access. Add unit tests for scientific work here and focused launch-contract
tests when the public interface changes.
