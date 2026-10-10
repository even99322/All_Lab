# Join a Host
> Follow another LabLogViewer on the same network, read-only.

## Shortest procedure
1. **Network Workspace → Client tab.** Hosts on your network are listed automatically; type a host or session name in the search box to filter. ![](net_client_list.png)
2. **If none is found, type the address.** **Or address:** the Host's IP (e.g. `192.168.1.20`, or with port `192.168.1.20:47811`).
3. **Join.** Select the Host, type the **Join code**, click **Connect**. ![](net_client_join.png)
4. **Follow.** A mirror of the Host's Viewer (and YIG Analysis) opens, titled "… following … (view only)". **Leave** ends it. ![](net_client_mirror.png)

## What the result means
- While connected your LabLogViewer is **view only**: mirror windows follow the Host and cannot be operated.
- Data from the Host stays in memory and is cleared when you leave; the status shows the source ("data: from Host (memory only)" or "data: this computer").
- If the Host sends a permanent copy, you get a notice and the file is saved in your data folder's `state/received/`.

## Options and parameters
- **Shared data folders...**: if this computer can read the same files (e.g. shared storage), they are read locally instead of downloaded — faster.
- A dropped connection reconnects automatically ("Reconnecting").

## If it does not work
- **Empty list**: the computers are not on the same network (subnet), or the network blocks discovery; type the IP instead.
- **Wrong join code**: 5 wrong codes within a minute from one computer are refused for a minute; wait and retry.
- **Cannot join because quality is too poor**: latency above 200 ms or speed below 1 MB/s; see *Quality, security and limits*.
- **Host full**: a Host serves at most 5 Clients.
