# LabLogViewer Interfaces

## Purpose and Entry Point

The Database Browser's top-level `Interfaces` menu is the single launch point
for future external or specialized workflows. It appears after `Processing`
and immediately before `Settings`. It is intentionally not copied into Viewer,
Analysis, or fitting windows.

The v0.17A menu contains `Measurement`, `Time Domain`, and `Online Paper
Library`. Measurement and Time Domain are safe placeholders. Online Paper
Library is only an external default-browser launcher, not a Python interface or
embedded web view.

## Directory Map

```text
app/interfaces/
|-- README.md
|-- __init__.py
|-- base.py
|-- online_paper_library.py
|-- measurement/
|   |-- README.md
|   |-- __init__.py
|   `-- interface.py
`-- time_domain/
    |-- README.md
    |-- __init__.py
    `-- interface.py
```

## Contract

`BaseInterface` defines two small methods:

- `is_available() -> bool`: whether a real implementation can currently run.
- `launch(context: InterfaceContext) -> InterfaceResult`: request a launch and
  return a result instead of displaying Qt dialogs from the scientific or
  integration module.

`InterfaceResult` uses `InterfaceStatus.LAUNCHED`, `UNAVAILABLE`, or `ERROR`.
Use `InterfaceResult.launched()`, `.unavailable()`, or `.failed(message)`.
The Browser translates unavailable messages and handles error presentation.
An implementation may raise unexpectedly; the Browser boundary catches it,
logs it, and displays a safe localized message without a traceback.

## Context Payload

`InterfaceContext` is an immutable, best-effort snapshot:

| Field | v0.17A value source |
| --- | --- |
| `database_path` | Resolved Browser database root, otherwise `None` |
| `database_name` | Root folder name, otherwise `None` |
| `database_identity` | Existing scan identity or resolved root identity, otherwise `None` |
| `selected_folder` | Selected log's parent, or current Browser folder |
| `selected_log_name` | Existing Browser `LogEntry.log_name`, otherwise `None` |
| `selected_log_id` | `None`; not currently exposed by the Browser model |
| `selected_log_path` | Selected entry's existing path, otherwise `None` |
| `selected_channel` | `None`; no channel selection exists in the Browser |
| `selected_dimensions` | `None`; dimension names are not loaded into Browser state |
| `sweep_information` | Existing sweep summary if metadata is complete, else `None` |
| `metadata` | `None`; no extra metadata is loaded for this hook |
| `instrument_metadata` | `None`; no instrument metadata is loaded for this hook |

Never infer missing IDs, channels, dimensions, metadata, instrument addresses,
or network addresses. This context does not own HDF5 handles and causes no
scientific-data reread.

## Launch Flow

`BrowserWindow` owns the menu actions and calls
`_launch_measurement_interface()` or `_launch_time_domain_interface()`. Each
method builds one `InterfaceContext` and passes it to that interface's
`launch()`. If unavailable, the Browser displays the localized informational
message. The placeholders report unavailable and do not control hardware,
start a process, alter Session Restore, or persist Viewer state.

Online Paper Library is isolated in `online_paper_library.py`. Its sole URL
constant is `ONLINE_PAPER_LIBRARY_URL`. An explicit menu activation passes a
`QUrl` to `QDesktopServices.openUrl`, allowing the operating system to select
the user's default browser. Tests inject/mock the opener; they must never make
a request or launch a real browser.

## Localization

The existing localization catalogs provide English and Traditional Chinese
labels. `Interfaces` / `Measurement` / `Online Paper Library` map to `介面` /
`量測` / `線上論文庫`. `Time Domain` intentionally remains English in both
languages. Placeholder messages use the same catalogs and update with the
application language. Tag values remain user data and are not changed by this
feature.

## Future Implementation Guidance

For a real Measurement or Time Domain implementation, normally edit only the
corresponding `app/interfaces/<name>/` files and their focused tests. Keep
integration details and scientific work out of `BrowserWindow`. A genuine
extension of the contract may require a small change to `base.py`, the relevant
Browser adapter, localization entries, and contract tests; avoid broader Core
changes unless a documented data API is insufficient.

The GUI must not import `h5py` or access HDF5 datasets. Reuse the existing
Reader, Parser, or Data Model API for legitimate data needs. Do not modify
scientific HDF5 through interface launch hooks.

## Testing

Run focused interface tests with `pytest -q tests/test_v017a_interfaces.py`.
Test returned status/context values without hardware. For Online Paper Library,
inject an opener and assert the exact `QUrl`; do not use the network or the
system browser. Test menu labels/order in both languages and switch back to
English. Then run the complete project regression suite.

## Files Future Developers Normally Edit

- Measurement: `measurement/interface.py`, its README, and focused tests.
- Time Domain: `time_domain/interface.py`, its README, and focused tests.
- Shared result/context contract: `base.py`, only if required.
- Browser adapter and localization catalogs: only for a genuine contract/menu
  change.

The Reader, Parser, Viewer, 3D renderer, Analysis, Tags, Session/Recovery, and
Browser layout should normally not need modification to implement either
future interface.
