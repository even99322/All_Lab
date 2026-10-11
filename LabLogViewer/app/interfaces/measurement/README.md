# Measurement Interface (v2.1.3)

Interfaces → Measurement opens **Lab Control**, the lab's measurement program.

- With a Data entry selected, Lab Control applies that file's measurement settings (it asks first; undo works).
- Lab Control already open on this computer: the request goes over a local connection
  (`QLocalServer` named `qel-labcontrol-<user>`, Lab Control 0.0.28+), and its window comes to the front.
- Not open: it is started with `--apply <file>` — the program recorded in `~/.qel/labcontrol/app.json`,
  else `%LOCALAPPDATA%\Programs\Lab Control\LabControl.exe` / `/Applications/Lab Control.app`.
  `LABCONTROL_EXE` overrides.

Dragging a Data entry from the Browser list now also carries the file (file URL), so it can be dropped
into Lab Control (same "apply settings"), a file manager or a mail.
