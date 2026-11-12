# Lab Control Hub 更新紀錄

1.0.36 起 Hub 跟 Lab Control 分開發佈（GitHub 發佈 `hub-v<版本>`）；1.0.35 以前的紀錄在 [CHANGELOG.md](CHANGELOG.md)。
每一版的說明在 [docs/releases/hub/](docs/releases/hub/)。

## 1.0.37 — 2026-11-13

NAS 流量分流（區網 / VPN / 外網直接；控制代理自動建立只計數的 labcontrol-netacct 容器）；每台電腦的流量分成跟 NAS 之間、其中經 VPN、其餘（多半外網）。詳見 [docs/releases/hub/v1.0.37.md](docs/releases/hub/v1.0.37.md)。

## 1.0.36 — 2026-11-13

Hub 改成自己發佈（`hub-v*`），控制台「從 GitHub 取得新版」多看 Hub 的發佈；Lab Control 的發佈不再附 Hub。詳見 [docs/releases/hub/v1.0.36.md](docs/releases/hub/v1.0.36.md)。
