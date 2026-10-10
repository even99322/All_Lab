# Quality, security and limits
> What the quality levels mean, what is encrypted, and the limits.

## Shortest procedure
1. **Watch the quality.** The top bar and the Network Workspace panel update latency and speed every 2 s. ![](net_quality.png)
2. **Read the level** from the table below; with "Too poor" you cannot join or stay in sync.
3. **Change network if needed**: use a cable or the same Wi-Fi access point.

## Quality levels
| Level | Round-trip latency | Speed |
|---|---|---|
| Excellent | ≤ 20 ms | ≥ 20 MB/s |
| Good | ≤ 80 ms | ≥ 5 MB/s |
| Fair | ≤ 200 ms | ≥ 1 MB/s |
| Too poor | beyond these | — |
Both sides see the same quality. "Fair" works, but large 2D data transfers slowly.

## Security
- All traffic uses X25519 key exchange and AES-GCM encryption; tampered data is rejected.
- The join code itself never crosses the network (it only proves both sides know the same code).
- 5 wrong join codes within a minute from one computer lock it out for a minute.
- Only computers on the same subnet are accepted.

## Limits
- One Host serves at most 5 Clients.
- 3D is not shared.
- Clients are always view only.

## If it does not work
- See *Network problems*.
