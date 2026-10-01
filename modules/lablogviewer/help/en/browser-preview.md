# Quick Preview, comments and receiving
> Select a measurement to see it on the right; keep a comment; or send a measurement to LabLogViewer on another computer.

## Shortest procedure
1. **Preview.** Click a row in **Data in Folder**; **Quick Preview** on the right draws it — a curve for 1D, a heatmap for 2D — with the Log name and channel above; move the mouse to read X / Y / Z. ![](preview_show.png)
2. **Comment.** Type in **Comment** at the bottom right; it saves automatically when you stop typing, and the status bar says where. ![](preview_comment.png)
3. **(Optional) receive.** On the receiving computer click **Receive**; it waits on the **Receiver TCP port** (default 53117). ![](preview_receive.png)
4. **(Optional) send.** On the sending computer set **Receiver** to the other computer's IP and the same port, then drag a row from the list onto **Drop a measurement here to send**.

## What the result means
- Quick Preview is a small view; double-click to open the Viewer for transforms, marks and export.
- **Where comments go**: first into the HDF5 file's own Labber `comment` field (the same one Labber shows); the status says "Saved native Labber Comment.". If the file has no such field or the text exceeds its fixed length, the comment goes to your data folder (`state/comments.json`) and the status says "Saved external Comment". This is the only place LabLogViewer writes into a measurement file, and only that text field — never measured values.
- If an older LabLogViewer external note and a different Labber comment both exist, both are kept, never merged.
- **Received measurements** are stored in the data folder's `state/received/`; the status shows "Received: name". What is sent is the interpreted measurement content, not an executable file.

## If it does not work
- **The preview stays empty**: large 2D files take a moment the first time; grey rows cannot be previewed.
- **"Wait for this Data's Quick Preview to finish, then try again."**: renaming waits while the preview is reading.
- **Sending fails**: both computers must be on the same network, the other must have clicked **Receive**, IP and port must match; a firewall may block the port.
- **"Invalid receiver host"**: the IP or host name is empty or has spaces.
- **Comment saved externally**: normal when the file has no Labber comment field; it is kept and follows renames.
