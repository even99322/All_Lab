# Network problems
> Cannot find a Host, cannot join, or the mirror does not follow — check these in order.

## Shortest procedure
1. **Same network.** Both computers must be on the same subnet (usually the same Wi-Fi or switch); other subnets are refused. ![](trouble_network.png)
2. **Type the address if none is found.** Some networks block discovery; in the Client tab use **Or address:** with the Host's IP (shown in the Host computer's network settings).
3. **Windows firewall.** On a Windows Host, the first time you host choose **Private networks** and Allow; if you cancelled, allow LabLogViewer in the Windows firewall settings.
4. **Check the quality.** With "Too poor" (latency > 200 ms or < 1 MB/s) you cannot join; use a cable or move closer to the access point.

## What the messages mean
| Message | Meaning |
|---|---|
| Wrong join code | check the 6 digits; 5 wrong codes within a minute pause that computer for a minute |
| Host full | 5 Clients already |
| Incompatible version | the two LabLogViewer versions speak different formats; update both to the same version |
| Reconnecting | a short network drop; recovers automatically |
| Host is in 3D | 3D is not shared; wait until the Host returns to the Viewer |

## If it does not work
- **A company / school network never connects**: some networks isolate devices from each other (client isolation); use another network.
- **Sending a measurement fails**: the other side must have clicked **Receive** and use the same port (see *Quick Preview, comments and receiving*).
