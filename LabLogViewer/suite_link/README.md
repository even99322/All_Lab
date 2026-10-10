# suite_link

Shared, Qt-free helper that lets separately packaged lab programs (Browser, Measurement,
Lab LaTeX, Time Domain) jump to each other. Copy this folder unchanged into each program.

- `links.py`: build/parse `labbrowser://open?v=1&...`. Only `open` and `fill` exist; nothing can
  start a measurement or run a command. Unknown schemes, actions, versions and control
  characters are refused.
- `registration.py`: one interface (`Registrar`) to tell the operating system which program owns
  a scheme: Windows (HKCU registry), Linux (.desktop + xdg-mime), macOS (Info.plist entry from
  `plist_entry()`, added when the app is built).
- Local machine only. No network access.

Not wired into the Browser yet: nothing calls `register()` at start-up.
