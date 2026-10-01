# Host a session
> Let up to 5 computers on the same network follow your Viewer and YIG Analysis live, read-only — for group meetings or discussions.

## Shortest procedure
1. **Open Network Workspace.** Top menu **Network Workspace** in any window, or click the network bar at the top. The first time, name this computer (any language; **Rename...** later). ![](net_host_open.png)
2. **Start hosting.** **Host** tab: type a **Workspace / Session** name (e.g. "Tuesday group meeting") → **Start Hosting**. A 6-digit **Join code** appears. ![](net_host_start.png)
3. **Give people the join code.** They follow *Join a Host*. **Connected Clients** lists each one with its connection quality. ![](net_host_clients.png)
4. **Work normally.** The Viewer you use (trace, axes, Marks — points, ranges, lines, crosshairs and their half-peak results, also when you move or clear them — and pen annotations) and its YIG Analysis window are mirrored to every Client. **Stop Hosting** ends the session.

## What the result means
- Clients see a mirror of your screen; they can look but not change anything, and never change your data or settings.
- Measurement data is sent as needed and kept only in the Client's memory, cleared when they leave — unless you send a permanent copy.
- 3D windows are not shared; Clients see a notice while you are in 3D.
- The top bar always shows "Hosting · N / 5 Clients" and the quality.

## Options and parameters
- **Send a permanent copy...**: choose a Client and send the current HDF5 file; it is saved in their data folder's `state/received/` (you confirm first; a checksum verifies it). Only the Host can send copies.
- A new join code is generated every time you start hosting.

## If it does not work
- **"Hosting cannot start"**: usually port 47811 is in use or the network is unavailable; the message says why.
- **Windows, first time**: Windows asks whether LabLogViewer may accept connections — choose **Private networks** and Allow, otherwise nobody can connect.
- **"Open a measurement in a Viewer to share it."**: open a Viewer first.
- **Clients cannot connect**: see *Network problems*.
