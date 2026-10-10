# Measurement Interface

`MeasurementInterface` is the reserved integration point for a future
measurement application. It currently reports itself unavailable and performs
no hardware or external-process action.

Implement future Measurement behavior in this directory. Override
`is_available()` and `launch(context)` in `interface.py`; return an
`InterfaceResult` with `launched()`, `unavailable()`, or `failed(message)`.
The Database Browser presents localized messages for unavailable and failed
results. Exceptions are caught by the Browser boundary and are not shown as a
traceback.

`InterfaceContext` may provide the resolved database path/name/identity, the
selected folder and log path/name, and a sweep summary when the Browser already
has that information. Log ID, channel, dimension names, full metadata, and
instrument metadata are currently `None`; do not guess them. The context is
non-destructive and contains no open HDF5 handle.

Do not put instrument control in `BrowserWindow`. Normally only
`app/interfaces/measurement/` should need editing. Keep tests local to the
interface and add a Browser-level test only when its launch contract changes.
The GUI must not import `h5py`; use an established Core/Data Model API if a
future integration genuinely needs Labber data.
